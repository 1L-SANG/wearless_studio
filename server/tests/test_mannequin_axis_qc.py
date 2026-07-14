"""P1 축 QC 워커 오케스트레이션(workers/mannequin_job._apply_axis_qc + _run_candidate 통합).

계약: off=완전 미개입 / shadow=판정·이벤트만 / enforce=코드 가드 승인 시에만 편집 1회.
모든 인프라 실패는 fail-open(원본 유지), 이벤트에는 해시만(원문 미포함), 예산은 생성+편집 공유.
"""
import asyncio
import contextlib
import hashlib
import types

from app.workers import mannequin_job
from app.agents import mannequin_fit_qc
from conftest import make_settings

PROFILE = {"category": "top", "gender": "women", "source": "seller",
           "axes": {"fit": "slim", "length": "long"}, "version": 1}

_PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000d49444154789c626001000000ffff030000060005"
    "57bfabd40000000049454e44ae426082")
_EDITED = _PNG_1PX + b"EDITED"  # 바이트 구분용 (Pillow 파싱은 채택 뒤 dims 계산에만 쓰임)


class _Conn:
    async def commit(self):
        return None


class _FakePool:
    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn()
        return _cm()


class _FakeGemini:
    """1번째 호출=신규 생성, 이후=편집. 호출 인자를 기록한다."""

    def __init__(self, edit_error=False):
        self.calls = []
        self.edit_error = edit_error

    async def generate_content_image(self, model, prompt, images, size, aspect_ratio=None):
        self.calls.append({"prompt": prompt, "images": list(images), "size": size,
                           "aspect_ratio": aspect_ratio})
        if len(self.calls) == 1:
            return types.SimpleNamespace(image=_PNG_1PX, mime="image/png")
        if self.edit_error:
            from app.agents.gemini_image import GeminiError
            raise GeminiError("edit down")
        return types.SimpleNamespace(image=_EDITED, mime="image/png")


class _R2:
    def __init__(self):
        self.puts = []

    def put_bytes(self, key, data, mime):
        self.puts.append((key, data, mime))


def _verdict(fit_ok=True, len_ok=True, identity=True, visible=True):
    return {"identityPass": identity, "mismatches": [],
            "axisPass": [
                {"axis": "fit", "target": "slim", "pass": fit_ok, "visible": visible,
                 "observedLandmark": "seen"},
                {"axis": "length", "target": "long", "pass": len_ok, "visible": True,
                 "observedLandmark": "seen"}]}


def _run(monkeypatch, *, mode, verdicts, guard=False, max_attempts=2, gemini=None,
         profile=PROFILE, image_qc="off", p2=None):
    """_run_candidate 를 실제 경로로 실행. verdicts=판정 fake 가 순서대로 돌려줄 값(or 예외)."""
    emits = []

    async def fake_emit(pool, job_id, event_type, p):
        emits.append((event_type, dict(p)))

    seq = list(verdicts)

    async def fake_verdict(settings, prods, gen_img, fit_profile, match_image=None):
        v = seq.pop(0)
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(mannequin_job, "_emit", fake_emit)
    monkeypatch.setattr(mannequin_fit_qc, "verdict", fake_verdict)
    if guard:
        monkeypatch.setattr(mannequin_job, "_MANNEQUIN_AXIS_QC_ENFORCEMENT_READY", True)
    if p2 is not None:
        async def fake_p2(s, prods, gen):
            return p2
        monkeypatch.setattr(mannequin_job.image_qc, "verdict", fake_p2)

    g = gemini or _FakeGemini()
    r2 = _R2()
    settings = make_settings(r2_bucket="b", mannequin_axis_qc=mode,
                             mannequin_max_attempts=max_attempts, image_qc=image_qc)
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=settings, pool=_FakePool(), r2=r2, gemini=g))
    job = {"id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "u1:t"}
    result = asyncio.run(mannequin_job._run_candidate(
        app=app, job=job, candidate="A", base_fit="regular", base_gender="women",
        base_img=types.SimpleNamespace(mime="image/png", data=b"x"),
        prod_imgs=[types.SimpleNamespace(mime="image/png", data=b"p")], match_img=None,
        product_count=1, template="Dress ${baseGender} ${clothingType}.\n${imageManifest}",
        product={"name": "티"}, analysis={}, clothing_type="top", image_manifest="1. base",
        fit_profile=profile, adjusted_axes=("fit",), fit_profile_source="payload_snapshot"))
    return result, g, r2, emits


def _events(emits, status):
    return [p for e, p in emits if e == "step" and p.get("status") == status]


# ---------- 모드·가드 ----------

def test_axis_qc_defaults_off_and_effective_mode_resolution():
    assert make_settings().mannequin_axis_qc == "off"
    s_off = make_settings(mannequin_axis_qc="off")
    s_sh = make_settings(mannequin_axis_qc="shadow")
    s_en = make_settings(mannequin_axis_qc="enforce")
    assert mannequin_job._effective_axis_qc_mode(s_off) == "off"
    assert mannequin_job._effective_axis_qc_mode(s_sh) == "shadow"
    assert mannequin_job._effective_axis_qc_mode(s_en) == "shadow"  # 가드 False → 강등


def test_axis_qc_off_skips_judge_and_retry(monkeypatch):
    boom = RuntimeError("judge must not be called")
    result, g, r2, emits = _run(monkeypatch, mode="off", verdicts=[boom])
    assert result is not None and len(g.calls) == 1
    assert _events(emits, "axis_qc") == [] and _events(emits, "axis_retry") == []
    assert r2.puts[0][1] == _PNG_1PX


def test_axis_qc_skips_null_or_empty_profile(monkeypatch):
    result, g, _, emits = _run(monkeypatch, mode="shadow", verdicts=[], profile=None)
    assert result is not None and _events(emits, "axis_qc") == []
    result, g, _, emits = _run(monkeypatch, mode="shadow", verdicts=[],
                               profile={"category": "top", "gender": "women", "axes": {}})
    assert result is not None and _events(emits, "axis_qc") == []


# ---------- shadow ----------

def test_axis_qc_shadow_logs_failure_keeps_original_no_edit(monkeypatch):
    result, g, r2, emits = _run(monkeypatch, mode="shadow", verdicts=[_verdict(fit_ok=False)])
    assert result is not None
    assert len(g.calls) == 1  # 편집 미발화
    assert r2.puts[0][1] == _PNG_1PX
    qc = _events(emits, "axis_qc")[0]
    assert qc["outcome"] == "fail" and qc["effective_mode"] == "shadow"
    retry = _events(emits, "axis_retry")[0]
    assert retry["fired"] is False and retry["outcome"] == "shadow_observed"
    assert retry["failed_axes"] == [{"axis": "fit", "target": "slim"}]
    assert len(retry["edit_hash"]) == 64  # 지시는 해시만 — 발송 안 됨


def test_axis_qc_configured_enforce_is_hard_shadowed_until_ready(monkeypatch):
    result, g, _, emits = _run(monkeypatch, mode="enforce", verdicts=[_verdict(fit_ok=False)])
    assert result is not None and len(g.calls) == 1
    retry = _events(emits, "axis_retry")[0]
    assert retry["outcome"] == "enforce_guarded" and retry["fired"] is False
    assert retry["configured_mode"] == "enforce" and retry["effective_mode"] == "shadow"
    assert retry["enforcement_ready"] is False


# ---------- enforce (가드 해제) ----------

def test_axis_qc_enforce_pass_fires_no_edit(monkeypatch):
    result, g, _, emits = _run(monkeypatch, mode="enforce", guard=True, verdicts=[_verdict()])
    assert result is not None and len(g.calls) == 1
    assert _events(emits, "axis_retry")[0]["outcome"] == "not_needed"


def test_axis_qc_enforce_failure_edits_once_and_selects_edited(monkeypatch):
    result, g, r2, emits = _run(monkeypatch, mode="enforce", guard=True,
                                verdicts=[_verdict(fit_ok=False), _verdict()])
    assert result is not None
    assert len(g.calls) == 2  # 생성 1 + 편집 1
    edit_call = g.calls[1]
    assert [im.data for im in edit_call["images"]] == [_PNG_1PX]  # 실패 이미지 1장만
    assert edit_call["prompt"].endswith(mannequin_fit_qc.EDIT_TAIL)
    assert r2.puts[0][1] == _EDITED  # 채택본 = 편집본
    retry = _events(emits, "axis_retry")[0]
    assert retry["outcome"] == "edited_selected" and retry["fired"] is True
    assert retry["edit_attempt"] == 2
    assert retry["edited_image_hash"] == hashlib.sha256(_EDITED).hexdigest()
    subjects = [q["subject"] for q in _events(emits, "axis_qc")]
    assert subjects == ["generated", "edited"]


def test_axis_qc_keeps_original_when_edited_identity_fails(monkeypatch):
    result, g, r2, emits = _run(monkeypatch, mode="enforce", guard=True,
                                verdicts=[_verdict(fit_ok=False), _verdict(identity=False)])
    assert result is not None and len(g.calls) == 2
    assert r2.puts[0][1] == _PNG_1PX
    assert _events(emits, "axis_retry")[0]["outcome"] == "original_kept"


def test_axis_qc_keeps_original_when_passing_axis_regresses(monkeypatch):
    result, g, r2, emits = _run(monkeypatch, mode="enforce", guard=True,
                                verdicts=[_verdict(fit_ok=False),
                                          _verdict(fit_ok=True, len_ok=False)])
    assert r2.puts[0][1] == _PNG_1PX
    assert _events(emits, "axis_retry")[0]["outcome"] == "original_kept"


# ---------- fail-open ----------

def test_axis_qc_initial_judge_error_is_fail_open(monkeypatch):
    from app.agents.vision_llm import VisionError
    result, g, r2, emits = _run(monkeypatch, mode="enforce", guard=True,
                                verdicts=[VisionError("down")])
    assert result is not None and len(g.calls) == 1
    assert r2.puts[0][1] == _PNG_1PX
    assert _events(emits, "axis_qc")[0]["outcome"] == "error"
    assert _events(emits, "axis_retry")[0]["outcome"] == "original_judge_error"


def test_axis_qc_edit_error_keeps_original(monkeypatch):
    result, g, r2, emits = _run(monkeypatch, mode="enforce", guard=True,
                                verdicts=[_verdict(fit_ok=False)],
                                gemini=_FakeGemini(edit_error=True))
    assert result is not None and r2.puts[0][1] == _PNG_1PX
    assert _events(emits, "axis_retry")[0]["outcome"] == "edit_error"


def test_axis_qc_edit_judge_error_keeps_original(monkeypatch):
    from app.agents.vision_llm import VisionError
    result, g, r2, emits = _run(monkeypatch, mode="enforce", guard=True,
                                verdicts=[_verdict(fit_ok=False), VisionError("down")])
    assert result is not None and len(g.calls) == 2
    assert r2.puts[0][1] == _PNG_1PX
    assert _events(emits, "axis_retry")[0]["outcome"] == "edit_judge_error"


# ---------- 예산·선점·게이트 불변 ----------

def test_axis_qc_retry_respects_shared_max_attempt_budget(monkeypatch):
    result, g, r2, emits = _run(monkeypatch, mode="enforce", guard=True, max_attempts=1,
                                verdicts=[_verdict(fit_ok=False)])
    assert result is not None and len(g.calls) == 1  # 생성 1 = 예산 소진 → 편집 불가
    assert _events(emits, "axis_retry")[0]["outcome"] == "budget_exhausted"


def test_legacy_identity_rejection_preempts_axis_edit(monkeypatch):
    # AG-P2 enforce 가 reject → 그 attempt 는 축 QC 미실행 + 선점 이벤트, re-roll 이 우선.
    boom = RuntimeError("judge must not be called on rejected candidate")
    result, g, r2, emits = _run(
        monkeypatch, mode="enforce", guard=True, max_attempts=1,
        verdicts=[boom], image_qc="enforce",
        p2={"verdict": "retry", "mismatches": ["색 다름"], "correctionPrompt": "fix color"})
    assert result is None  # max_attempts=1 → 후보 드롭(기존 동작)
    assert _events(emits, "axis_qc") == []
    pre = _events(emits, "axis_retry")
    assert pre and pre[0]["outcome"] == "identity_gate_preempted" and pre[0]["fired"] is False


def test_axis_qc_never_unshadows_pillow_gate():
    s = make_settings(mannequin_axis_qc="enforce", mannequin_qc_enabled=True)
    pillow_reject, _ = mannequin_job.gate_decision(s, "fail", None)
    assert pillow_reject is False  # G9 하드 섀도 불변


# ---------- 이벤트 위생 ----------

def test_axis_qc_events_contain_hashes_not_raw_prompts_or_profiles(monkeypatch):
    _, _, _, emits = _run(monkeypatch, mode="enforce", guard=True,
                          verdicts=[_verdict(fit_ok=False), _verdict()])
    blob = str([p for _, p in emits if p.get("status") in ("axis_qc", "axis_retry")])
    assert "Re-tailor" not in blob and "Change NOTHING" not in blob  # 편집 지시 원문 없음
    assert "shoulder seam" not in blob  # 관측문구 원문 없음 (observed_landmark 는 판정기 서술)
    for p in _events(emits, "axis_qc") + _events(emits, "axis_retry"):
        assert len(p["profile_hash"]) == 64
        for k in ("image_hash", "original_image_hash", "edited_image_hash", "edit_hash"):
            if p.get(k):
                assert len(p[k]) == 64
