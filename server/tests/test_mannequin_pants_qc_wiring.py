"""매칭 하의(코디 바지) 정체성 QC — 워커 배선.

증명 대상:
  1. 바지 판정이 **기존 AG-P2 1콜 안**에서 나온다(match_image 첨부, AI 콜 증가 0).
  2. enforce에서 기존 예산을 소진해도 중요한 문제가 남으면 원본 기반 최종 1회만 추가한다.
     최종 검수까지 실패한 후보는 저장하지 않는다(2026-09-10 사용자 승인).
  3. 깨끗한 final_reject 가 있으면 드롭 대신 그걸로 구제한다.
  4. shadow 는 아무 것도 막지 않고 계측만 한다.
  5. 편집(bust)·untuck 후 바지영역이 회귀하면 그 편집을 버리고 편집 전으로 롤백한다.
"""

import asyncio
import types

import pytest

from app.services.qc import QcResult
from app.workers import mannequin_job
from conftest import make_settings
from test_mannequin_quality import assessment

_PASS = QcResult("pass", [], {})


def _p2(*, product=90, matching_fidelity=88, matching_critical=None, critical=None):
    return {
        "verdict": "pass", "mismatches": [], "correctionPrompt": None,
        "product_fidelity": product, "physical_naturalness": product,
        "image_quality": product, "series_consistency": None,
        "critical_errors": critical or [],
        "matching_fidelity": matching_fidelity,
        "matching_critical_errors": matching_critical or [],
    }


class _Gemini:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.generation_calls = []

    async def generate_content_image(self, model, prompt, images, size, aspect_ratio=None):
        self.generation_calls.append(prompt)
        out = self.outputs.pop(0) if self.outputs else b"extra"
        return types.SimpleNamespace(image=out, mime="image/png")


class _R2:
    def __init__(self):
        self.puts = []

    def put_bytes(self, key, data, mime, cache=None):
        self.puts.append((key, data, mime))


async def _no_series(**kwargs):
    return None


def _run(monkeypatch, *, p2_by_attempt, outputs, pants_qc="enforce", image_qc="enforce",
         with_match=True, max_attempts=2, bust="off", untuck="off",
         compare=None, base_fidelity_axes=None, mannequin_verdicts=None, gemini=None, r2=None):
    emits = []
    verdict_calls = {"n": 0, "match_seen": []}
    p2s = list(p2_by_attempt)
    mverdicts = list(mannequin_verdicts or [_PASS] * (max_attempts + 2))

    monkeypatch.setattr(mannequin_job.qc, "evaluate_mannequin_qc", lambda _d: mverdicts.pop(0))
    monkeypatch.setattr(mannequin_job.qc, "evaluate_canvas_alpha_qc",
                        lambda _d: QcResult("pass"))
    monkeypatch.setattr(mannequin_job, "_apply_series_qc", _no_series)
    if compare is not None:
        monkeypatch.setattr(mannequin_job.qc, "compare_pants_region", compare)
    if base_fidelity_axes is not None:
        monkeypatch.setattr(mannequin_job, "base_fidelity_retry_axes", base_fidelity_axes)

    async def fake_verdict(s, prods, gen, *, scored=False, fit_profile=None, match_image=None):
        verdict_calls["n"] += 1
        verdict_calls["match_seen"].append(match_image is not None)
        return p2s.pop(0) if p2s else p2s_last()

    def p2s_last():
        return p2_by_attempt[-1]

    monkeypatch.setattr(mannequin_job.image_qc, "verdict", fake_verdict)

    async def fake_emit(pool, job_id, event_type, payload):
        emits.append(dict(payload))

    monkeypatch.setattr(mannequin_job, "_emit", fake_emit)

    gemini, r2 = gemini or _Gemini(outputs), r2 or _R2()
    settings = make_settings(
        r2_bucket="bucket", image_qc=image_qc, mannequin_pants_qc=pants_qc,
        mannequin_axis_qc="off", mannequin_max_attempts=max_attempts,
        mannequin_bust_pass=bust, mannequin_fabric_pass="off", mannequin_untuck_pass=untuck)
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=settings, pool=object(), r2=r2, gemini=gemini))
    job = {"id": "j1", "user_id": "u1", "project_id": "p1", "payload": {}}

    result = asyncio.run(mannequin_job._run_candidate(
        app=app, job=job, candidate="A", base_fit="regular", base_gender="women",
        base_img=mannequin_job.InlineImage("image/png", b"base"),
        prod_imgs=[mannequin_job.InlineImage("image/png", b"prod")],
        match_img=(mannequin_job.InlineImage("image/png", b"bottom") if with_match else None),
        product_count=1, template="${baseGender} ${clothingType} ${imageManifest}",
        product={}, analysis={}, clothing_type="top"))
    return result, gemini, r2, emits, verdict_calls


def _status(emits, status):
    return [e for e in emits if e.get("status") == status]


# ── 0. 순수 헬퍼 ────────────────────────────────────────────────────────────

def test_pants_gate_fires_only_on_hard_errors_under_enforce():
    enf = make_settings(mannequin_pants_qc="enforce")
    # 하드 게이트(색·종류·통·구조)만 발화. 낮은 matching_fidelity 점수만으론 안 막는다(요구 3).
    assert mannequin_job.pants_gate(enf, _p2(matching_fidelity=5, matching_critical=[])) is False
    assert mannequin_job.pants_gate(enf, _p2(matching_critical=["matching bottom type changed"]))
    # shadow/off 는 절대 게이트하지 않는다.
    for mode in ("shadow", "off"):
        s = make_settings(mannequin_pants_qc=mode)
        assert mannequin_job.pants_gate(s, _p2(matching_critical=["x"])) is False
        assert mannequin_job._pants_shippable(s, _p2(matching_critical=["x"])) is True
    # enforce 에서 하드 게이트 걸린 후보는 구제 불가.
    assert mannequin_job._pants_shippable(enf, _p2(matching_critical=["x"])) is False


def test_pants_ref_and_region_mode_gate_on_flag_and_match():
    img = mannequin_job.InlineImage("image/png", b"m")
    off, sh, enf = (make_settings(mannequin_pants_qc=m) for m in ("off", "shadow", "enforce"))
    assert mannequin_job._pants_qc_ref(off, img) is None          # 플래그 off → 참조 없음
    assert mannequin_job._pants_qc_ref(sh, img) is img            # shadow/enforce → 참조 첨부
    assert mannequin_job._pants_qc_ref(enf, None) is None         # 매칭 없음 → 참조 없음
    assert mannequin_job._pants_region_mode(enf, None) == "off"   # 매칭 없으면 픽셀 비교도 off
    assert mannequin_job._pants_region_mode(sh, img) == "shadow"


# ── 1. 같은 1콜 안 + AI 콜 증가 0 ────────────────────────────────────────────

def test_pants_clean_ships_and_verdict_gets_match_image(monkeypatch):
    """바지 깨끗 + enforce → attempt 1 출고. verdict 는 match_image 를 받았다(같은 1콜에 얹힘)."""
    result, gemini, r2, emits, vc = _run(
        monkeypatch, p2_by_attempt=[_p2(matching_critical=[])], outputs=[b"gen-1"])
    assert result is not None
    assert r2.puts[0][1] == b"gen-1"
    assert len(gemini.generation_calls) == 1
    # 핵심: 판정 콜 수 == 생성 attempt 수. 바지 때문에 새 vision 콜이 생기지 않았다.
    assert vc["n"] == 1
    assert vc["match_seen"] == [True], "매칭 하의가 있으면 verdict 에 match_image 가 실린다"


def test_pants_ref_omitted_when_flag_off(monkeypatch):
    """플래그 off 면 매칭 하의가 있어도 verdict 에 match_image 를 넣지 않는다(요청 불변)."""
    result, gemini, r2, emits, vc = _run(
        monkeypatch, p2_by_attempt=[_p2()], outputs=[b"gen-1"], pants_qc="off")
    assert result is not None
    assert vc["match_seen"] == [False]


# ── 2. 예산 내 재롤과 최종 1회 구제 ────────────────────────────────────────

def test_pants_critical_gets_one_final_repair_then_stops(monkeypatch):
    """마지막 1회에도 바지 하드 오류가 남으면 저장과 자동 재시도 없이 실패로 종결한다."""
    crit = ["matching bottom colour changed"]
    gemini, r2 = _Gemini([b'gen-1', b'gen-2', b'final']), _R2()
    with pytest.raises(mannequin_job.MannequinQualityError, match='final_product_rejected'):
        _run(monkeypatch, gemini=gemini, r2=r2, outputs=[],
            p2_by_attempt=[{**_p2(matching_critical=crit), **assessment()}] * 3)
    assert len(gemini.generation_calls) == 3
    assert 'matching bottom colour changed' in gemini.generation_calls[-1]
    assert r2.puts == []


def test_product_reject_with_pants_critical_gets_one_final_repair_without_crash(monkeypatch):
    """상품 QC 도 거절(p2_reject) + 바지 하드 게이트가 두 attempt 다 걸리는 경로.

    바지-critical 은 pre_reject 구제 풀에서 제외되므로, 예산 소진 시 pre_reject 가 None 이다.
    None 을 안전하게 다뤄 최종 구제로 넘긴다. pre_reject[1] 언팩 크래시도 없어야 한다.
    """
    crit = ["matching bottom colour changed"]
    gemini, r2 = _Gemini([b'gen-1', b'gen-2', b'final']), _R2()
    failed = {**assessment(), **_p2(critical=['logo altered'], matching_critical=crit)}
    with pytest.raises(mannequin_job.MannequinQualityError, match='final_product_rejected'):
        _run(monkeypatch, gemini=gemini, r2=r2, outputs=[], p2_by_attempt=[failed] * 3)
    assert len(gemini.generation_calls) == 3
    assert 'logo altered' in gemini.generation_calls[-1]
    assert r2.puts == []


def test_pants_critical_salvages_clean_final_reject_instead_of_dropping(monkeypatch):
    """드롭 직전, 깨끗한 final_reject 후보가 있으면 그걸로 구제한다(드롭보다 우선).

    attempt 1: base-fidelity 재시도(바지 깨끗) → final_reject 풀에 담기고 재롤.
    attempt 2: 상품 통과·바지 critical + 예산 소진 → 드롭 대신 attempt 1(깨끗)로 구제 출고.
    """
    axes = iter([["poseFrameMatch"], []])  # attempt1 만 base-fidelity 재시도

    def fake_bf(s, base_fidelity):
        try:
            return next(axes)
        except StopIteration:
            return []

    result, gemini, r2, emits, vc = _run(
        monkeypatch,
        p2_by_attempt=[_p2(matching_critical=[]),
                       _p2(matching_critical=["matching bottom type changed"])],
        outputs=[b"clean-1", b"bad-2"], base_fidelity_axes=fake_bf)
    assert result is not None, "깨끗한 후보가 있으면 드롭하지 않는다"
    assert r2.puts[0][1] == b"clean-1", "출고본은 바지 깨끗했던 attempt 1"
    salvaged = _status(emits, "qc_salvaged")
    assert any(e.get("reason") == "matching_identity_dropped" for e in salvaged)


# ── 3. shadow 는 막지 않는다 ─────────────────────────────────────────────────

def test_shadow_never_drops_and_still_observes(monkeypatch):
    """shadow: 바지 critical 이어도 출고(관측만). match_image 는 계측 위해 여전히 첨부."""
    result, gemini, r2, emits, vc = _run(
        monkeypatch, pants_qc="shadow",
        p2_by_attempt=[_p2(matching_critical=["matching bottom colour changed"])],
        outputs=[b"gen-1"])
    assert result is not None and r2.puts[0][1] == b"gen-1"
    assert len(gemini.generation_calls) == 1, "shadow 는 재롤을 만들지 않는다"
    assert vc["match_seen"] == [True]
    assert _status(emits, "candidate_dropped") == []


# ── 5. 편집·untuck 후 바지영역 회귀 → 롤백 ──────────────────────────────────

def test_bust_edit_reverts_when_pants_region_regresses(monkeypatch):
    """bust 편집이 바지영역을 회귀시키면 편집 전(생성본)으로 롤백. compare_pants_region 배선 검증."""
    def regressed(before, after, **kw):
        return QcResult("pants_regressed", ["matching_bottom_colour_shift"], {})

    result, gemini, r2, emits, vc = _run(
        monkeypatch, image_qc="off",   # 게이트 없이 편집까지 태운다
        p2_by_attempt=[_p2()], outputs=[b"gen-1", b"busted"],
        bust="on", compare=regressed)
    assert result is not None
    assert r2.puts[0][1] == b"gen-1", "회귀한 bust 결과가 아니라 편집 전 생성본이 출고된다"
    reverts = [e for e in _status(emits, "edit_reverted") if e.get("reason") == "pants_regressed"]
    assert reverts, "pants_regressed 사유로 되돌린 이벤트가 있어야 한다"


def test_untuck_reverts_when_pants_region_regresses(monkeypatch):
    """untuck post-pass 가 바지영역을 회귀시키면 pre-untuck 을 유지한다."""
    seen = {}

    def regressed(before, after, **kw):
        seen.update(kw)
        return QcResult("pants_regressed", ["matching_bottom_width_shift"], {})

    result, gemini, r2, emits, vc = _run(
        monkeypatch, image_qc="off",
        p2_by_attempt=[_p2()], outputs=[b"gen-1", b"untucked"],
        untuck="on", compare=regressed)
    assert result is not None
    assert r2.puts[0][1] == b"gen-1", "회귀한 untuck 결과 대신 pre-untuck 이 출고된다"
    assert any(e.get("untuck_outcome") == "reverted_pants" for e in emits)
    # untuck 은 상의 밑단을 허리 아래로 내린다 — 밴드 상단을 무릎 아래로 낮춰 비교해야
    # 정상 untuck 이 "바지 회귀"로 오판되지 않는다(2026-08-15 리뷰).
    from app.services import qc as qcmod
    assert seen.get("band_top") == qcmod.PANTS_BAND_TOP_UNTUCK


def test_shadow_region_observes_but_never_reverts(monkeypatch):
    """shadow: 바지영역이 회귀해도 편집을 되돌리지 않고 계측만 한다(다크 출고·캘리브 우선)."""
    def regressed(before, after, **kw):
        return QcResult("pants_regressed", ["matching_bottom_colour_shift"], {"colorDelta": 99})

    result, gemini, r2, emits, vc = _run(
        monkeypatch, pants_qc="shadow", image_qc="off",
        p2_by_attempt=[_p2()], outputs=[b"gen-1", b"busted"],
        bust="on", compare=regressed)
    assert result is not None
    assert r2.puts[0][1] == b"busted", "shadow 는 회귀해도 편집본을 그대로 출고한다"
    region = [e for e in _status(emits, "pants_region") if e.get("mode") == "shadow"]
    assert region and region[0]["pants_verdict"] == "pants_regressed", "관측은 남긴다"
    assert not any(e.get("reason") == "pants_regressed"
                   for e in _status(emits, "edit_reverted")), "shadow 는 롤백 이벤트 없음"


def test_region_compare_fails_open_on_unknown(monkeypatch):
    """compare 가 pants_unknown(디코드 실패 등)이면 enforce 여도 편집을 되돌리지 않는다(fail-open)."""
    def unknown(before, after, **kw):
        return QcResult("pants_unknown", ["decode_failed"], {})

    result, gemini, r2, emits, vc = _run(
        monkeypatch, pants_qc="enforce", image_qc="off",
        p2_by_attempt=[_p2()], outputs=[b"gen-1", b"busted"],
        bust="on", compare=unknown)
    assert result is not None
    assert r2.puts[0][1] == b"busted", "판정 불가에 멀쩡한 편집을 되돌리면 안 된다"
    assert not any(e.get("reason") == "pants_regressed"
                   for e in _status(emits, "edit_reverted"))


# ── 하의 상품일 때 매칭은 상의 (2026-08-15 리뷰: 판정·밴드가 이 분기를 안 봤음) ──

def test_bottom_hero_never_sends_matching_as_a_pants_reference():
    """주상품이 하의면 매칭 아이템은 상의다 — 프롬프트가 'matching bottom' 이라 단언하므로
    참조를 붙이면 안 되고(오판), 하체 밴드 비교도 주상품을 덮으므로 꺼야 한다."""
    enf = make_settings(mannequin_pants_qc="enforce")
    img = mannequin_job.InlineImage("image/png", b"m")
    # 상의 상품(매칭=하의) — 정상 동작
    assert mannequin_job._pants_qc_ref(enf, img, "top") is img
    assert mannequin_job._pants_region_mode(enf, img, "top") == "enforce"
    # 하의 상품(매칭=상의) — 판정·밴드 둘 다 꺼진다
    assert mannequin_job._pants_qc_ref(enf, img, "bottom") is None
    assert mannequin_job._pants_region_mode(enf, img, "bottom") == "off"
    # 대소문자·공백도 같은 규칙(_build_manifest 와 동일 관례)
    assert mannequin_job._pants_qc_ref(enf, img, " Bottom ") is None


def test_retry_feedback_carries_the_matching_defect():
    """바지 하드게이트로 건 재롤은 그 사유가 프롬프트에 실려야 의미가 있다 —
    merge_qc_scores 가 매칭 키를 버리므로 p2 에서 직접 읽어야 한다(리뷰 확정 결함)."""
    p2 = _p2(matching_critical=["matching bottom colour changed"])
    fb = mannequin_job._build_retry_feedback(
        mannequin_job.merge_qc_scores(p2, None), None, p2)
    assert "matching bottom colour changed" in fb
    assert "MATCHING GARMENT" in fb
