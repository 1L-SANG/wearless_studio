"""거부된 컷 관측 잡 — 배선·비동기성·안전 강등.

이 잡의 존재 이유는 코퍼스 수집이므로, 여기서 잠그는 것은 판정 품질이 아니라 **부작용이
없다는 것**이다: 재생성을 막지 않고, 기다리게 하지 않고, 실패해도 조용히 끝난다.
"""
import ast
import asyncio
import hashlib
import pathlib
import types

import pytest

import app.routes as routes
from app.agents import mannequin_base_fidelity_qc as bf
from app.workers import base_fidelity_observe_job as obs
from conftest import auth_headers, make_settings, patch_route_db

SERVER = pathlib.Path(__file__).resolve().parents[1]
ROUTES_SRC = (SERVER / "app/routes.py").read_text(encoding="utf-8")
WORKER_SRC = (SERVER / "app/workers/base_fidelity_observe_job.py").read_text(encoding="utf-8")
PROMPT_PATH = SERVER / "prompts/mannequin_base_fidelity_qc_v1.txt"

#: 축별 섹션 해시. 전체 파일 해시로는 "wearGeometry 만 바꿨다"를 증명할 수 없다 —
#: 한 축을 보정할 때 다른 축이 딸려 바뀌지 않았음을 이 두 값이 잠근다.
#: poseFrameMatch 는 검증을 통과한 뒤로 **한 바이트도** 바뀌지 않았다(2026-08-12 v2 보정 포함).
POSE_SECTION_SHA256 = "f5bb6d65c448185d52626f20dc9e6b775850f9a9b2c8161b3b7653b19e2db62d"
#: wearGeometry v2 — 구조 무결성 검사로 재정의. 고칠 때 QC_VERSION 도 함께 올린다.
WEAR_SECTION_SHA256 = "2bbb1c72ff54675a751c21728a0bdb6423f543af8fdafdbedf27d1313b551cd9"
_SEP = "=" * 64


def _section(name: str) -> str:
    """프롬프트에서 축 섹션 하나를 잘라낸다(다음 구분선 직전까지)."""
    nl = chr(10)
    src = PROMPT_PATH.read_text(encoding="utf-8")
    start = src.index(_SEP + nl + name)
    # 제목 아래 닫는 구분선을 건너뛴 뒤에 **다음** 섹션의 구분선을 찾는다.
    body = src.index(_SEP + nl, start + len(_SEP) + 1) + len(_SEP) + 1
    nxt = src.find(_SEP + nl, body)
    return src[start:len(src) if nxt == -1 else nxt]


def _code_only(text: str) -> str:
    return "\n".join(line.split("#")[0] for line in text.splitlines())


def _func_source(src: str, name: str) -> str:
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"{name} not found")


# ── 워커 하니스 ──────────────────────────────────────────────────────────────

class _R2:
    def __init__(self, keys=None):
        self.keys = keys if keys is not None else {}
        self.reads = []

    def get_bytes(self, key):
        self.reads.append(key)
        if key not in self.keys:
            raise FileNotFoundError(key)
        return self.keys[key]

    def put_bytes(self, *a, **k):  # 호출되면 테스트가 잡는다
        raise AssertionError("관측 잡은 R2 에 쓰지 않는다")


def _run_worker(monkeypatch, *, settings=None, payload=None, cut=None, base=None,
                verdict=None, r2_keys=None, analysis=None, product=None):
    finished, events = [], []

    async def fake_finalize(conn, *, job_id, lease_token, status, result):
        finished.append((status, result))
        return True

    async def fake_emit(pool, job_id, event_type, payload_):
        events.append((event_type, payload_))

    async def fake_cut(conn, uid, pid, client_id):
        return cut

    async def fake_asset(conn, uid, asset_id):
        return base

    async def fake_product(conn, pid):
        return product if product is not None else {"clothing_type": "top", "name": "셔츠"}

    async def fake_analysis(conn, pid):
        return analysis if analysis is not None else {"fit": "over", "subCategory": "shirt",
                                                     "targetGenders": ["women"]}

    monkeypatch.setattr(obs.repo, "finalize_uncharged_job", fake_finalize)
    monkeypatch.setattr(obs.repo, "get_mannequin_cut_asset", fake_cut)
    monkeypatch.setattr(obs.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(obs.repo, "get_product", fake_product)
    monkeypatch.setattr(obs.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(obs, "_emit", fake_emit)
    if verdict is not None:
        monkeypatch.setattr(obs.bf, "verdict", verdict)

    s = settings or make_settings(
        mannequin_base_fidelity_observe_regenerations="on",
        base_mannequin_women_asset_id="base-w", base_mannequin_men_asset_id="base-m")
    r2 = _R2(r2_keys if r2_keys is not None else {"base/w.png": b"BASE", "cuts/a-3.png": b"GEN"})
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        pool=_FakePool(), r2=r2, settings=s))
    job = {"id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "u1:tok",
           "payload": payload if payload is not None else {"rejectedCutId": "A-3"}}
    monkeypatch.setattr(obs, "load_settings", lambda: s)
    asyncio.run(obs.run_base_fidelity_observe_job(app, job))
    return finished, events, r2


class _FakeConn:
    async def commit(self):
        return None

    async def rollback(self):
        return None


class _FakePool:
    def connection(self):
        import contextlib as _c

        @_c.asynccontextmanager
        async def _cm():
            yield _FakeConn()

        return _cm()


OK_CUT = {"id": "asset-1", "r2_key": "cuts/a-3.png", "mime_type": "image/png"}
OK_BASE = {"id": "base-w", "r2_key": "base/w.png", "mime_type": "image/png"}


def _verdict(pose="pass", wear="pass"):
    async def _v(settings, base_image, generated_image, *, product=None, analysis=None):
        _v.seen = {"base": base_image, "gen": generated_image,
                   "product": product, "analysis": analysis}
        return bf.validate({"poseFrameMatch": {"decision": pose, "reason": "p"},
                            "wearGeometry": {"decision": wear, "reason": "w"}})
    return _v


# ── 1·2. 어떤 경로가 관측을 거는가 ───────────────────────────────────────────

def test_normal_generation_does_not_enqueue_observation():
    """최초 생성(:generate)은 거부가 아니다 — 관측을 걸면 안 된다."""
    gen = _func_source(ROUTES_SRC, "generate_mannequins")
    assert "base_fidelity_observe" not in gen
    assert "_enqueue_base_fidelity_observation" not in gen


def test_regeneration_enqueues_observation():
    regen = _code_only(_func_source(ROUTES_SRC, "regenerate_mannequins"))
    assert "_enqueue_base_fidelity_observation(" in regen
    helper = _code_only(_func_source(ROUTES_SRC, "_enqueue_base_fidelity_observation"))
    assert 'kind="base_fidelity_observe"' in helper


def test_only_the_regenerate_route_triggers_observation():
    """다른 라우트(에디터 변형 등)에는 붙지 않는다 — 거부 의미가 아니다."""
    calls = [n for n in ROUTES_SRC.splitlines()
             if "_enqueue_base_fidelity_observation(" in n and "async def" not in n]
    assert len(calls) == 1, calls


def test_observation_is_only_enqueued_for_a_freshly_created_job():
    regen = _code_only(_func_source(ROUTES_SRC, "regenerate_mannequins"))
    idx = regen.index("_enqueue_base_fidelity_observation(")
    assert "if created:" in regen[:idx].rsplit("await conn.commit()", 1)[-1]


# ── 3. 재생성이 판정을 기다리지 않는다 ──────────────────────────────────────

def test_regeneration_never_awaits_the_provider():
    """요청 경로에서 판정기를 부르면 6~17초가 사용자 지연에 붙는다."""
    regen = _code_only(_func_source(ROUTES_SRC, "regenerate_mannequins"))
    helper = _code_only(_func_source(ROUTES_SRC, "_enqueue_base_fidelity_observation"))
    for banned in ("bf.verdict", "mannequin_base_fidelity_qc.verdict",
                   "analyze_with_fallback", "get_bytes"):
        assert banned not in regen and banned not in helper
    # 헬퍼가 하는 일은 잡 하나 만들고 커밋하는 것뿐이어야 한다.
    assert "create_job" in helper


def test_enqueue_failure_cannot_break_regeneration():
    helper = _code_only(_func_source(ROUTES_SRC, "_enqueue_base_fidelity_observation"))
    assert "except Exception" in helper
    assert "raise" not in helper
    assert "rollback" in helper


# ── 4·5·6. 무엇을 보는가 ────────────────────────────────────────────────────

def test_the_rejected_cut_identity_is_captured_at_request_time():
    """신원은 요청 처리 중에 잡는다 — 워커가 나중에 '최신 컷'을 찾으면 새 컷을 보게 된다."""
    helper = _code_only(_func_source(ROUTES_SRC, "_enqueue_base_fidelity_observation"))
    assert "get_mannequin_edit_parent" in helper
    assert "rejectedCutId" in helper


def test_identity_lookup_runs_after_commit_and_cannot_500_the_request():
    """회귀(2026-08-12): 커밋 전 트랜잭션에서 조회했더니 조회 실패가 재생성을 500 으로 만들었다."""
    regen = _code_only(_func_source(ROUTES_SRC, "regenerate_mannequins"))
    assert "get_mannequin_edit_parent" not in regen
    idx = regen.index("_enqueue_base_fidelity_observation(")
    assert "await conn.commit()" in regen[:idx]
    helper = _code_only(_func_source(ROUTES_SRC, "_enqueue_base_fidelity_observation"))
    assert helper.index("get_mannequin_edit_parent") > helper.index("try:")


def test_worker_inspects_the_payload_cut_not_the_latest(monkeypatch):
    """페이로드의 거부된 컷을 본다 — '지금 최신 컷'을 다시 찾지 않는다."""
    finished, _, _ = _run_worker(monkeypatch, payload={"rejectedCutId": "B-7"},
                                 cut=OK_CUT, base=OK_BASE, verdict=_verdict())
    rec = finished[0][1]
    assert rec["rejectedCutId"] == "B-7" and rec["candidate"] == "B"
    # 최신 컷을 다시 조회하는 경로가 워커에 있으면 안 된다.
    assert "get_mannequin_edit_parent" not in WORKER_SRC
    assert "list_mannequin_cuts" not in WORKER_SRC


def test_base_reference_comes_from_generation_time_metadata(monkeypatch):
    v = _verdict()
    finished, _, r2 = _run_worker(
        monkeypatch, payload={"rejectedCutId": "A-3", "cutMetadata": {"profileGender": "men"}},
        cut=OK_CUT, base={"id": "base-m", "r2_key": "base/w.png", "mime_type": "image/png"},
        verdict=v)
    rec = finished[0][1]
    assert rec["baseReference"] == {"gender": "men", "assetId": "base-m",
                                    "source": "cut_metadata"}


def test_base_reference_is_rederived_when_metadata_is_absent(monkeypatch):
    finished, _, _ = _run_worker(monkeypatch, cut=OK_CUT, base=OK_BASE, verdict=_verdict(),
                                 analysis={"targetGenders": ["women"]})
    assert finished[0][1]["baseReference"]["source"] == "rederived"
    assert finished[0][1]["baseReference"]["gender"] == "women"


def test_image_order_is_base_then_generated(monkeypatch):
    v = _verdict()
    _run_worker(monkeypatch, cut=OK_CUT, base=OK_BASE, verdict=v)
    assert v.seen["base"].data == b"BASE"
    assert v.seen["gen"].data == b"GEN"


def test_fit_and_ease_metadata_is_forwarded(monkeypatch):
    v = _verdict()
    finished, _, _ = _run_worker(
        monkeypatch, cut=OK_CUT, base=OK_BASE, verdict=v,
        analysis={"fit": "over", "subCategory": "knit", "targetGenders": ["women"]},
        product={"clothing_type": "top", "name": "박시 니트"})
    assert v.seen["analysis"]["fit"] == "over"
    assert v.seen["product"]["name"] == "박시 니트"
    assert finished[0][1]["product"] == {"category": "top", "subCategory": "knit", "fit": "over"}


# ── 7·8·12. 결과 기록 ───────────────────────────────────────────────────────

def test_pass_observation_is_recorded(monkeypatch):
    """PASS 도 반드시 남긴다 — 거부됐는데 pass 인 표본이 캘리브레이션의 핵심이다."""
    finished, events, _ = _run_worker(monkeypatch, cut=OK_CUT, base=OK_BASE,
                                      verdict=_verdict(pose="pass", wear="pass"))
    status, rec = finished[0]
    assert status == "done" and rec["observationStatus"] == "completed"
    assert rec["baseFidelity"]["wearGeometry"]["decision"] == "pass"
    assert rec["userRejected"] is True and rec["manualDefect"] is None
    assert events[0][1]["status"] == "base_fidelity_observe"


def test_retry_observation_is_recorded(monkeypatch):
    finished, events, _ = _run_worker(monkeypatch, cut=OK_CUT, base=OK_BASE,
                                      verdict=_verdict(pose="retry", wear="pass"))
    rec = finished[0][1]
    assert rec["baseFidelity"]["poseFrameMatch"]["decision"] == "retry"
    assert rec["baseFidelity"]["overall"]["decision"] == "retry"
    assert events[0][1]["poseFrameMatch"] == "retry"


def test_user_rejection_is_never_auto_labelled_as_a_defect(monkeypatch):
    """거부 사유는 포즈·색·핏·취향 등 다양하다. 라벨은 사람만 붙인다."""
    finished, _, _ = _run_worker(monkeypatch, cut=OK_CUT, base=OK_BASE, verdict=_verdict())
    assert finished[0][1]["manualDefect"] is None
    assert "WEAR_GEOMETRY" not in WORKER_SRC


def test_record_carries_the_qc_version(monkeypatch):
    finished, _, _ = _run_worker(monkeypatch, cut=OK_CUT, base=OK_BASE, verdict=_verdict())
    assert finished[0][1]["qcVersion"] == bf.QC_VERSION


# ── 9·10·11. 안전 강등 ──────────────────────────────────────────────────────

def test_provider_failure_ends_in_the_job_only(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("provider down")

    finished, events, _ = _run_worker(monkeypatch, cut=OK_CUT, base=OK_BASE, verdict=boom)
    status, rec = finished[0]
    assert status == "error" and rec["observationStatus"] == "failed"
    assert rec["error"] == "RuntimeError"
    assert events[0][1]["status"] == "base_fidelity_observe_failed"


def test_missing_rejected_asset_skips(monkeypatch):
    finished, _, _ = _run_worker(monkeypatch, cut=None, base=OK_BASE, verdict=_verdict())
    status, rec = finished[0]
    assert status == "done" and rec["reason"] == obs.SKIP_CUT_GONE


def test_unreadable_rejected_asset_skips(monkeypatch):
    finished, _, _ = _run_worker(monkeypatch, cut=OK_CUT, base=OK_BASE, verdict=_verdict(),
                                 r2_keys={"base/w.png": b"BASE"})  # 컷 객체가 없다
    assert finished[0][1]["reason"] == obs.SKIP_CUT_GONE


def test_missing_base_skips(monkeypatch):
    finished, _, _ = _run_worker(monkeypatch, cut=OK_CUT, base=None, verdict=_verdict())
    assert finished[0][1]["reason"] == obs.SKIP_NO_BASE


def test_missing_cut_reference_skips(monkeypatch):
    finished, _, _ = _run_worker(monkeypatch, payload={}, cut=OK_CUT, base=OK_BASE,
                                 verdict=_verdict())
    assert finished[0][1]["reason"] == obs.SKIP_NO_CUT_REF


def test_disabled_flag_skips_without_calling_the_provider(monkeypatch):
    async def must_not_run(*a, **k):
        raise AssertionError("off 인데 판정기가 돌면 안 된다")

    finished, _, _ = _run_worker(
        monkeypatch, settings=make_settings(mannequin_base_fidelity_observe_regenerations="off"),
        cut=OK_CUT, base=OK_BASE, verdict=must_not_run)
    assert finished[0][1]["reason"] == obs.SKIP_DISABLED


def test_worker_never_retries_the_provider_itself():
    code = _code_only(WORKER_SRC)
    assert "for attempt" not in code and "while True" not in code


# ── 12·13·14. 하지 않는 것 ──────────────────────────────────────────────────

def test_worker_never_generates_images_or_touches_sam():
    code = _code_only(WORKER_SRC)
    for banned in ("generate_content_image", "GeminiImageClient", "state.gemini",
                   "sam_client", "segment_garment", "canonical_reference",
                   "sam_preprocess", "put_bytes", "_save_cut"):
        assert banned not in code, banned
    # gemini_image 는 InlineImage(순수 데이터클래스) 때문에만 등장해야 한다.
    assert code.count("gemini") == 1 and "from ..agents.gemini_image import InlineImage" in code


def test_worker_never_touches_gating_or_credits():
    code = _code_only(WORKER_SRC)
    for banned in ("gate_decision", "final_decision", "merge_qc_scores", "score_outcome",
                   "reserve_credits", "release_credits", "repo.charge", "charge_credits",
                   "mannequin_cuts"):
        assert banned not in code, banned
    assert "finalize_uncharged_job" in code  # 무과금 종결만 쓴다


def test_worker_does_not_mutate_the_original_result():
    code = _code_only(WORKER_SRC)
    for banned in ("update assets", "delete from", "save_analysis", "update mannequin"):
        assert banned not in code.lower()


def test_observation_job_is_uncharged_at_the_enqueue_site():
    helper = _code_only(_func_source(ROUTES_SRC, "_enqueue_base_fidelity_observation"))
    assert "credits_reserved=0" in helper


# ── 15. 멱등성 ──────────────────────────────────────────────────────────────

def test_idempotency_key_pins_cut_and_qc_version():
    helper = _code_only(_func_source(ROUTES_SRC, "_enqueue_base_fidelity_observation"))
    assert "idempotency_key=" in helper
    assert "cut_id" in helper and "QC_VERSION" in helper


def test_kind_is_declared_in_the_db_constraint_and_excluded_from_the_active_index():
    import re
    root = SERVER.parent / "supabase/migrations"
    files = sorted(p for p in root.glob("*.sql") if "jobs_kind_check" in p.read_text("utf-8"))
    body = files[-1].read_text(encoding="utf-8")
    kinds = set(re.findall(r"'([a-z_]+)'", re.search(
        r"kind in \((.*?)\)", body, re.S).group(1)))
    assert "base_fidelity_observe" in kinds
    # 활성 유니크 인덱스에서 제외 — 연속 거부 표본이 조용히 버려지면 안 된다.
    idx = sorted(p for p in root.glob("*.sql") if "jobs_active_unique_idx" in p.read_text("utf-8"))
    assert "base_fidelity_observe" in idx[-1].read_text(encoding="utf-8")


def test_worker_kind_is_registered():
    from app.workers.dispatcher import _KINDS, _WORKERS
    assert _WORKERS["base_fidelity_observe"] is obs.run_base_fidelity_observe_job
    assert "base_fidelity_observe" in _KINDS


# ── 16·17·18. 프로덕션 설정·프롬프트 불변 ───────────────────────────────────

def test_global_generation_flag_is_untouched(monkeypatch):
    from app.config import load_settings
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.delenv("MANNEQUIN_BASE_FIDELITY_QC", raising=False)
    monkeypatch.delenv("MANNEQUIN_BASE_FIDELITY_OBSERVE_REGENERATIONS", raising=False)
    s = load_settings()
    assert s.mannequin_base_fidelity_qc == "off"
    assert s.mannequin_base_fidelity_observe_regenerations == "off"


def test_observation_does_not_reuse_the_generation_path_flag():
    """전역 플래그를 켜서 관측을 구현하면 전 생성에 지연이 붙는다 — 분리 유지."""
    code = _code_only(WORKER_SRC)
    assert "mannequin_base_fidelity_observe_regenerations" in code
    assert "mannequin_base_fidelity_qc" not in code.replace(
        "mannequin_base_fidelity_qc as bf", "")


def test_manifest_declares_the_intended_rollout():
    """부분 롤아웃 계약: 포즈만 게이트, 기하는 관측, 거부컷 수집 on."""
    import yaml
    v = yaml.safe_load((SERVER.parent / "copilot/api/manifest.yml").read_text(encoding="utf-8"))
    assert v["variables"]["MANNEQUIN_BASE_FIDELITY_QC"] == "enforce"
    assert v["variables"]["MANNEQUIN_BASE_FIDELITY_OBSERVE_REGENERATIONS"] == "on"


def test_enforce_in_the_manifest_still_gates_pose_only():
    """매니페스트가 enforce 여도 wearGeometry 는 재시도를 요구하지 않는다."""
    from app.workers.mannequin_job import _BASE_FIDELITY_GATED_AXES, base_fidelity_retry_axes
    s = make_settings(mannequin_base_fidelity_qc="enforce")
    wear_only = bf.validate({"poseFrameMatch": {"decision": "pass", "reason": "p"},
                             "wearGeometry": {"decision": "retry", "reason": "w"}})
    assert base_fidelity_retry_axes(s, wear_only) == []
    assert _BASE_FIDELITY_GATED_AXES == ("poseFrameMatch",)


def test_pose_section_is_byte_identical_since_validation():
    """poseFrameMatch 는 검증된 상태 그대로여야 한다 — wearGeometry 보정에 딸려 바뀌면 안 된다."""
    got = hashlib.sha256(_section("AXIS 1 — poseFrameMatch").encode("utf-8")).hexdigest()
    assert got == POSE_SECTION_SHA256, (
        f"poseFrameMatch 섹션이 바뀌었다({got}). 이 축은 이번 범위가 아니다.")


def test_wear_section_matches_the_pinned_version():
    got = hashlib.sha256(_section("AXIS 2 — wearGeometry").encode("utf-8")).hexdigest()
    assert got == WEAR_SECTION_SHA256, (
        f"wearGeometry 섹션이 바뀌었다({got}). 의도한 변경이면 QC_VERSION 을 올리고 해시를 갱신하라.")


@pytest.mark.parametrize("axis", ["poseFrameMatch", "wearGeometry"])
def test_both_axes_still_declared(axis):
    assert axis in PROMPT_PATH.read_text(encoding="utf-8")
    assert axis in bf.AXES


def test_qc_version_tracks_the_prompt_semantics():
    """프롬프트 의미가 바뀌면 버전도 올라가야 한다 — 관측 코퍼스에서 판정기를 구분하려면."""
    assert bf.QC_VERSION == "base-fidelity-v2"
