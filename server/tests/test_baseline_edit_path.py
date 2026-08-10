"""Phase 3 P0-C — baseline 편집 워커 경로.

계약:
  · 편집 입력은 **active baseline** 이다. selected_mannequin_id 가 다른 컷이어도 baseline 을 쓴다.
  · baseline 이미지를 못 읽으면 fresh 생성으로 넘어가지 않고 **실패**한다.
  · baseline 이 없거나 그 사이 교체됐으면 실패한다(옛 정본을 편집하지 않는다).
  · 입력 순서: baseline 이 0번, 상품 참조가 그 뒤. Generation Run 스냅샷도 같은 순서다.
  · 계보: parent_output_id = baseline.output_id, baseline_id = baseline.id,
    generation_run_id = **이번 호출**(baseline 의 run 이 아니다).
  · enforce 에서 reject 는 저장 없이 실패 종결. shadow 는 기록만 하고 기존 계약을 유지한다.
"""

import asyncio
import contextlib
import hashlib
import types

import numpy as np
import pytest

from app import repo
from app.agents.gemini_image import InlineImage
from app.services import edit_intent_qc as eqc
from app.workers import mannequin_job as mj
from conftest import make_settings


def _png(v=235, size=(600, 400)):
    import cv2
    img = np.full((size[0], size[1], 3), v, np.uint8)
    img[80:480, 120:280] = (120, 120, 120)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


BASELINE_PNG = _png()
EDITED_PNG = _png()


class _Conn:
    async def commit(self):
        return None


class _Pool:
    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn()

        return _cm()


BASELINE = {"id": "base-1", "baseline_cut_id": "cut-1", "output_id": "out-base",
            "generation_run_id": "run-base", "cut_client_id": "A-3",
            "locked_invariants": {"garmentCategory": {"status": "recorded",
                                                      "value": "top"}}}
SESSION = {"id": "sess-1", "baseline_id": "base-1", "allowed_scope": None,
           "locked_invariants": {}, "status": "queued"}


def _run(monkeypatch, *, baseline=BASELINE, session=SESSION, parent_is_baseline=True,
         r2_fail=False, qc_decision=None, enforce=True, edited=None, cut_row=True,
         edit_type="GARMENT_LENGTH_ONLY", adjustments=None):
    """편집 잡 1회 실행 → 관찰 dict."""
    seen = {"gemini": [], "runs": [], "sessions": [], "saved": [], "failed": [],
            "success": []}

    class _Gemini:
        async def generate_content_image(self, model, prompt, images, size,
                                         temperature=None, aspect_ratio=None):
            seen["gemini"].append({"prompt": prompt, "images": list(images),
                                   "model": model})
            return types.SimpleNamespace(image=edited or EDITED_PNG, mime="image/png",
                                         latency_ms=1, usage=None)

    class _R2:
        def get_bytes(self, key):
            if r2_fail:
                raise RuntimeError("r2 down")
            return BASELINE_PNG

        def put_bytes(self, key, data, mime, cache=None):
            seen["saved"].append(key)

        def delete(self, key):
            return None

    async def get_product(conn, project_id):
        return {"name": "티", "clothing_type": "top", "fit": "regular",
                "colors": [{"isBase": True, "images": [{"id": "a1", "slot": "Front"}]}]}

    async def get_analysis(conn, project_id):
        return {}

    async def get_active_baseline(conn, project_id):
        return baseline

    async def get_edit_session(conn, session_id):
        return session

    async def update_edit_session(conn, **kw):
        seen["sessions"].append(kw)

    async def get_edit_parent(conn, user_id, project_id):
        if not parent_is_baseline:
            return {"id": "B-9", "asset_id": "other", "r2_key": "k", "mime_type": "image/png"}
        return {"id": "A-3", "asset_id": "asset-base", "r2_key": "k",
                "mime_type": "image/png"}

    async def get_cut_for_approval(conn, user_id, project_id, cut_id):
        if not cut_row:
            return None
        return {"mannequin_cut_id": "cut-1", "id": cut_id, "asset_id": "asset-base",
                "qc_scores": None, "product_id": "p", "clothing_type": "top",
                "generation_metadata": {}}

    async def get_asset(conn, user_id, asset_id):
        return {"id": asset_id, "mime_type": "image/png", "r2_key": "k"}

    async def insert_run(conn, **kw):
        seen["runs"].append(kw)

    async def noop(conn, **kw):
        return None

    async def finalize_success(conn, **kw):
        seen["success"].append(kw)
        return {"cuts": kw["candidates"], "available": 3}

    async def finalize_failure(conn, **kw):
        seen["failed"].append(kw)
        return True

    for name, fn in (("get_product", get_product), ("get_analysis", get_analysis),
                     ("get_active_baseline", get_active_baseline),
                     ("get_edit_session", get_edit_session),
                     ("update_edit_session", update_edit_session),
                     ("get_mannequin_edit_parent", get_edit_parent),
                     ("get_mannequin_cut_for_approval", get_cut_for_approval),
                     ("get_asset_for_user", get_asset),
                     ("insert_generation_run", insert_run),
                     ("update_generation_run", noop),
                     ("update_generation_run_prompt_key", noop),
                     ("finalize_mannequin_success", finalize_success),
                     ("finalize_mannequin_failure", finalize_failure)):
        monkeypatch.setattr(repo, name, fn)
    monkeypatch.setattr(mj, "_emit", lambda *a, **k: _noop())
    if qc_decision is not None:
        def fake_eval(**kw):
            return {"decision": qc_decision, "requestedChangeSatisfied":
                    qc_decision == "pass", "requestedChangeMeasurements": {},
                    "unexpectedChanges": [], "lockedInvariantViolations":
                    ["background"] if qc_decision == "reject" else [],
                    "regenerationInstructions": ["fix"] if qc_decision == "reject" else [],
                    "checks": {}, "metrics": {}}
        monkeypatch.setattr(eqc, "evaluate", fake_eval)

    settings = make_settings(
        r2_bucket="bucket", generation_run_log="shadow",
        mannequin_edit_intent_qc=("enforce" if enforce else "shadow"))
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=settings, pool=_Pool(), r2=_R2(), gemini=_Gemini()))
    job = {"id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "t",
           "credits_reserved": 2,
           "payload": {"mode": "edit", "editType": edit_type,
                       "adjustments": adjustments or {"garmentLengthStep": -1},
                       "editSessionId": "sess-1", "baselineId": "base-1"}}
    asyncio.run(mj.run_mannequin_job(app, job))
    return seen


async def _noop():
    return None


# ── 입력 ─────────────────────────────────────────────────────────────────────

def test_baseline_image_is_the_first_provider_input(monkeypatch):
    seen = _run(monkeypatch, qc_decision="pass")
    assert seen["gemini"], "편집 호출이 나가지 않았다"
    first = seen["gemini"][0]["images"][0]
    assert first.data == BASELINE_PNG, "baseline 이 0번 입력이 아니다"


def test_run_snapshot_records_the_baseline_at_position_zero(monkeypatch):
    seen = _run(monkeypatch, qc_decision="pass")
    row = seen["runs"][0]
    snap = row["input_assets"]
    assert snap[0]["role"] == "parent_cut" and snap[0]["position"] == 0
    assert snap[0]["outputId"] == "out-base"
    assert snap[0]["sha256"] == hashlib.sha256(BASELINE_PNG).hexdigest()
    assert row["kind"] == "mannequin_baseline_edit"


def test_edit_uses_the_baseline_even_when_another_cut_is_selected(monkeypatch):
    """selected_mannequin_id 가 다른 컷이어도 편집 입력은 active baseline 이다."""
    seen = _run(monkeypatch, parent_is_baseline=False, qc_decision="pass")
    assert seen["gemini"][0]["images"][0].data == BASELINE_PNG


def test_prompt_states_it_is_a_limited_edit_and_lists_locks(monkeypatch):
    seen = _run(monkeypatch, qc_decision="pass")
    prompt = seen["gemini"][0]["prompt"]
    assert "LIMITED EDIT" in prompt and "not a regeneration" in prompt
    assert "MUST NOT CHANGE" in prompt and "GARMENT_LENGTH_ONLY" in prompt
    assert "IMAGE 1" in prompt


@pytest.mark.parametrize(("edit_type", "adjustments", "expected"), [
    ("GARMENT_LENGTH_ONLY", {"garmentLengthStep": -2}, "make the garment visibly shorter"),
    ("SLEEVE_LENGTH_ONLY", {"sleeveLengthStep": 1}, "make both sleeves visibly longer"),
    ("BODY_WIDTH_ONLY", {"bodyWidthStep": 2}, "make the garment body visibly roomier"),
    ("SHOULDER_WIDTH_ONLY", {"shoulderWidthStep": -1}, "make the garment shoulders visibly narrower"),
    ("TUCK_STATE_ONLY", {"tuckStateStep": 1}, "tuck the garment in"),
    ("MANNEQUIN_VOLUME_ONLY", {"mannequinVolumeStep": -1}, "make the mannequin volume visibly slimmer"),
])
def test_prompt_translates_steps_into_unambiguous_visual_directions(
        monkeypatch, edit_type, adjustments, expected):
    seen = _run(monkeypatch, qc_decision="pass", edit_type=edit_type,
                adjustments=adjustments)
    assert expected in seen["gemini"][0]["prompt"]


# ── 실패 계약: fresh fallback 금지 ───────────────────────────────────────────

def test_missing_baseline_fails_instead_of_generating_fresh(monkeypatch):
    seen = _run(monkeypatch, baseline=None)
    assert seen["failed"] and not seen["gemini"], "baseline 없이 생성이 나갔다"
    assert seen["failed"][0]["metadata"]["error"] == "no_approved_baseline"


def test_baseline_image_load_failure_fails_the_edit(monkeypatch):
    seen = _run(monkeypatch, r2_fail=True)
    assert seen["failed"] and not seen["gemini"]
    assert seen["failed"][0]["metadata"]["error"] == "baseline_asset_load_failed"


def test_superseded_baseline_is_not_edited(monkeypatch):
    """세션이 가리키는 baseline 이 더는 active 가 아니면 옛 정본을 편집하지 않는다."""
    seen = _run(monkeypatch, session={**SESSION, "baseline_id": "base-old"})
    assert seen["failed"] and not seen["gemini"]
    assert seen["failed"][0]["metadata"]["error"] == "baseline_superseded"


def test_missing_cut_row_fails_closed(monkeypatch):
    seen = _run(monkeypatch, parent_is_baseline=False, cut_row=False)
    assert seen["failed"] and not seen["gemini"]


# ── 계보 ─────────────────────────────────────────────────────────────────────

def test_output_lineage_points_at_the_baseline_and_a_new_run(monkeypatch):
    seen = _run(monkeypatch, qc_decision="pass")
    cand = seen["success"][0]["candidates"][0]
    lin = cand["generation_lineage"]
    assert lin["parent_output_id"] == "out-base"
    assert lin["baseline_id"] == "base-1"
    assert lin["generation_run_id"] == seen["runs"][0]["run_id"]
    assert lin["generation_run_id"] != "run-base", "baseline 의 호출을 재사용했다"


def test_edit_run_records_the_baseline_run_as_parent(monkeypatch):
    seen = _run(monkeypatch, qc_decision="pass")
    assert seen["runs"][0]["parent_generation_run_id"] == "run-base"


# ── 판정 적용 ────────────────────────────────────────────────────────────────

def test_enforce_rejects_without_saving_or_charging(monkeypatch):
    seen = _run(monkeypatch, qc_decision="reject", enforce=True)
    assert not seen["success"], "reject 인데 출고됐다"
    assert seen["failed"]
    assert seen["failed"][0]["metadata"]["error"] == "edit_intent_rejected"
    statuses = [x["status"] for x in seen["sessions"]]
    assert "reject" in statuses


def test_enforce_review_required_saves_as_needs_review(monkeypatch):
    seen = _run(monkeypatch, qc_decision="review_required", enforce=True)
    cand = seen["success"][0]["candidates"][0]
    assert cand["qc_scores"]["outcome"] == "needs_review"
    # 세션 종결은 finalize 와 **같은 tx** 다 — 별도 update 가 아니라 인자로 넘어간다
    assert seen["success"][0]["edit_session"]["status"] == "review_required"


def test_shadow_never_blocks_delivery(monkeypatch):
    """shadow 는 기록만 한다 — reject 판정이어도 기존 계약대로 저장된다."""
    seen = _run(monkeypatch, qc_decision="reject", enforce=False)
    assert seen["success"], "shadow 가 출고를 막았다"
    cand = seen["success"][0]["candidates"][0]
    assert cand["qc_scores"]["outcome"] == "needs_review"
    assert cand["qc_scores"]["editIntentQc"]["decision"] == "reject"


def test_pass_is_recorded_in_the_same_transaction_as_finalize(monkeypatch):
    """job=success 인데 session=running 인 불일치를 만들지 않는다."""
    seen = _run(monkeypatch, qc_decision="pass")
    es = seen["success"][0]["edit_session"]
    assert es["status"] == "pass" and es["id"] == "sess-1"
    assert es["qc_result"]["decision"] == "pass"
    assert "pass" not in [x["status"] for x in seen["sessions"]], \
        "종결이 별도 tx 로 새어 나갔다"


def test_edit_never_supersedes_the_baseline(monkeypatch):
    """어떤 편집 결과도 baseline 을 자동 교체하지 않는다."""
    called = []

    async def approve(conn, **kw):
        called.append(kw)

    monkeypatch.setattr(repo, "approve_mannequin_baseline", approve)
    _run(monkeypatch, qc_decision="pass")
    assert called == [], "편집이 baseline 을 자동 승인했다"


def test_retry_happens_at_most_once(monkeypatch):
    seen = _run(monkeypatch, qc_decision="reject", enforce=True)
    assert len(seen["gemini"]) <= 2, "재시도 상한을 넘겼다"


def test_flag_off_does_not_take_the_edit_path(monkeypatch):
    """off 면 편집 잡이 생기지 않지만, 이미 큐에 있어도 편집 경로로 가지 않는다."""
    calls = []

    async def guard(app, job, *, fail):
        calls.append(job)

    monkeypatch.setattr(mj, "_run_baseline_edit", guard)

    async def get_product(conn, pid):
        return {}

    monkeypatch.setattr(repo, "get_product", get_product)
    monkeypatch.setattr(mj, "_emit", lambda *a, **k: _noop())

    async def fail_ok(conn, **kw):
        return True

    monkeypatch.setattr(repo, "finalize_mannequin_failure", fail_ok)
    settings = make_settings(mannequin_edit_intent_qc="off", r2_bucket="b")
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=settings, pool=_Pool(), r2=None, gemini=None))
    job = {"id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "t",
           "credits_reserved": 0, "payload": {"mode": "edit"}}
    with contextlib.suppress(Exception):
        asyncio.run(mj.run_mannequin_job(app, job))
    assert calls == [], "플래그 off 인데 편집 경로를 탔다"


# ── 편집 결과의 권한 게이트 — 코덱스가 "가드를 지워도 25/25 통과" 를 보인 자리 ────
def _blocked_billing(monkeypatch):
    """청구 판정을 '소비 불가' 로 고정한다 — 게이트가 하는 일만 따로 본다."""
    from app.services.mannequin_cut_authority import BillableCharge

    monkeypatch.setattr(
        mj, "resolve_billable_charge",
        lambda _c, _r: BillableCharge(0, 0, "no_consumable_cut"))


def test_an_unauthorized_edit_result_is_not_shipped(monkeypatch):
    """편집이라고 해서 쓸 수 없는 컷을 출고하거나 과금해도 되는 것이 아니다.

    이 경로는 예전에 `charge=reserved` 를 **무조건** 확정했고 권한 판정을 아예 부르지
    않았다. 가드를 지우면 이 시험이 죽어야 한다.
    """
    _blocked_billing(monkeypatch)
    seen = _run(monkeypatch, enforce=True)
    assert not seen["success"], "권한 없는 편집 결과가 출고됐다"
    assert seen["failed"], "종결이 없다"
    meta = seen["failed"][0]["metadata"]
    assert meta["error"] == "no_authorized_output", meta
    assert meta["blockedCandidates"], "왜 막혔는지가 남아야 한다"


def test_an_unauthorized_edit_closes_its_session(monkeypatch):
    """잡은 끝났는데 세션이 `running` 이면 그 세션은 영원히 종결되지 않는다."""
    _blocked_billing(monkeypatch)
    seen = _run(monkeypatch, enforce=True)
    statuses = [x["status"] for x in seen["sessions"]]
    assert statuses, "세션 상태 전이가 하나도 없다"
    assert "running" != statuses[-1], statuses
    assert "failed" in statuses, statuses


def test_the_settlement_key_is_derived_from_the_job_and_is_stable(monkeypatch):
    """무작위 settle_key 는 실패 종결의 멱등성을 깨뜨린다 — 같은 잡이면 같은 키여야 한다.

    코덱스 실측: 안정 키를 임의 UUID 로 바꿔도 크레딧 시험 25개가 전부 통과했다.
    """
    _blocked_billing(monkeypatch)
    first = _run(monkeypatch, enforce=True)["failed"][0]["settle_key"]
    _blocked_billing(monkeypatch)
    second = _run(monkeypatch, enforce=True)["failed"][0]["settle_key"]
    assert first == second, (first, second)
    assert "j1" in first, first          # 잡에서 유도된다
