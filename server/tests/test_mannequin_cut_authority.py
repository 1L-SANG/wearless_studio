"""서버가 이미 못 쓴다고 판정한 컷은 정본으로 소비될 수 없다.

이 게이트 이전에는 화면(`reviewState.js`)만 컷을 막았고 서버는:
  · `selected_mannequin_id` 에 임의 문자열을 그대로 기록했고(존재·소유·QC 검증 0),
  · 승인은 소유권만 봤고("실패는 저장되지 않는다"는 전제 위에 서 있었다),
  · detail page 워커는 선택 포인터를 QC 없이 소비했고,
  · 편집 부모·시리즈 기준도 QC 를 읽지 않았다.

이 스위트는 그 다섯 구멍을 서버 쪽에서 닫혔는지만 본다. **job 완료·크레딧 계약은 여기서
다루지 않는다** — TEXTURE_TRUTH_UNCERTAIN 이 여전히 done + charge 로 끝나는 것은 별개의
알려진 결함이다.
"""

import pytest
from fastapi import HTTPException

from app import repo, routes
from app.services import mannequin_cut_authority as authority
from app.services.mannequin_cut_authority import evaluate_mannequin_cut_authority


# ── fixtures — 실제 파이프라인이 쓰는 shape 그대로 ──────────────────────────
def uncertain_qc():
    """cc1c075 이후 texture truth 불확정 컷 — 현재 production 모드(enforce)."""
    return {
        "outcome": "needs_review",
        "hybridComposite": {
            "mode": "enforce",
            "applied": False,
            "wouldApply": False,
            "failClosed": False,
            "needsReview": True,
            "textureTruth": "TEXTURE_TRUTH_UNCERTAIN",
            "failureReason": "guided_period_unvalidated_harmonic",
        },
        "structuredQC": {
            "overallDecision": "review",
            "checks": [
                {"check": "garment_structure", "status": "pass", "score": 0.95},
                {"check": "pattern_fidelity", "status": "fail", "score": 0.0,
                 "criticalErrors": ["guided_period_unvalidated_harmonic"]},
            ],
        },
    }


def valid_qc():
    """정상 합성 컷 — Branch 1/2 가 투영까지 마친 결과."""
    return {
        "outcome": "auto_pass",
        "hybridComposite": {
            "mode": "enforce",
            "applied": True,
            "wouldApply": True,
            "failClosed": False,
            "needsReview": False,
            "deterministicPassed": True,
        },
        "structuredQC": {
            "overallDecision": "pass",
            "checks": [
                {"check": "garment_structure", "status": "pass", "score": 0.95},
                {"check": "pattern_fidelity", "status": "pass", "score": 1.0},
            ],
        },
    }


def shadow_qc():
    """shadow 관측 — 합성을 적용하지 않는 것이 정상 동작이다."""
    return {
        "outcome": "auto_pass",
        "hybridComposite": {
            "mode": "shadow",
            "applied": False,
            "wouldApply": True,
            "failClosed": False,
            "needsReview": True,
        },
    }


# ── 1. predicate ───────────────────────────────────────────────────────────
def test_texture_uncertain_cut_is_not_authorized():
    verdict = evaluate_mannequin_cut_authority(uncertain_qc())
    assert verdict.allowed is False
    assert verdict.reason == authority.REASON_HYBRID_NOT_APPLIED


def test_valid_cut_stays_authorized():
    assert evaluate_mannequin_cut_authority(valid_qc()).allowed is True


def test_shadow_observation_never_blocks_on_applied_false():
    """shadow 는 관측 전용 — 여기서 막으면 shadow 를 켜는 순간 전부 사용 불가가 된다."""
    assert evaluate_mannequin_cut_authority(shadow_qc()).allowed is True


def test_needs_review_alone_does_not_remove_authority():
    """needsReview 는 '사람이 보라'이지 '제품으로 못 쓴다'가 아니다."""
    qc = valid_qc()
    qc["outcome"] = "needs_review"
    qc["hybridComposite"]["needsReview"] = True
    assert evaluate_mannequin_cut_authority(qc).allowed is True


def test_regenerate_outcome_is_not_authorized():
    qc = valid_qc()
    qc["outcome"] = "regenerate"
    v = evaluate_mannequin_cut_authority(qc)
    assert v.allowed is False and v.reason == authority.REASON_OUTCOME_REGENERATE


def test_measured_pattern_fidelity_failure_is_not_authorized():
    qc = valid_qc()
    qc["structuredQC"]["checks"][1] = {"check": "pattern_fidelity", "status": "fail",
                                       "score": 0.2}
    v = evaluate_mannequin_cut_authority(qc)
    assert v.allowed is False and v.reason == authority.REASON_PATTERN_FIDELITY_FAILED


@pytest.mark.parametrize("legacy", [
    None,
    {},
    {"outcome": "auto_pass"},
    {"outcome": "needs_review", "product_fidelity": 88},
    {"hybridComposite": {"mode": "enforce", "wouldApply": True}},   # applied 키 부재
    {"structuredQC": {"overallDecision": "pass"}},                  # checks 부재
    "not-a-dict",
])
def test_legacy_cuts_keep_their_existing_authority(legacy):
    """판정 신호 부재는 나쁨이 아니다 — 배제하면 과거 프로젝트가 통째로 막힌다."""
    assert evaluate_mannequin_cut_authority(legacy).allowed is True


def test_cut_row_helper_rejects_missing_rows():
    assert authority.cut_is_consumable(None) is False
    assert authority.cut_is_consumable({"qc_scores": valid_qc()}) is True
    assert authority.cut_is_consumable({"qc_scores": uncertain_qc()}) is False
    assert authority.cut_is_consumable({}) is True          # legacy row, qc_scores 없음


# ── 2. route guard — 선택/승인 ─────────────────────────────────────────────
def test_route_guard_passes_valid_cut():
    routes._require_consumable_cut({"qc_scores": valid_qc()})     # raises 없음


def test_route_guard_rejects_unauthorized_cut_without_leaking_diagnostics():
    qc = uncertain_qc()
    qc["hybridComposite"].update({
        "guidedObservedPeriodPx": 45.0, "carrierSha256": "deadbeef",
        "frontScan": {"n_colors": 2}, "failureDetail": "guided period 45.00px …"})
    with pytest.raises(HTTPException) as exc:
        routes._require_consumable_cut({"qc_scores": qc})
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert detail["code"] == "cut_not_usable"
    body = repr(detail)
    for leak in ("guidedObserved", "TEXTURE_TRUTH", "carrierSha256", "frontScan",
                 "guided_period_unvalidated_harmonic", "45.0"):
        assert leak not in body, leak


# ── 3. 저장소 소비 지점 ────────────────────────────────────────────────────
class _Cur:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return None

    async def fetchall(self):
        return list(self._rows)

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _Cur(self._rows)


def test_series_reference_cuts_drop_unauthorized_rows():
    rows = [
        {"candidate": "A", "version": 2, "qc_scores": valid_qc(),
         "r2_bucket": "b", "r2_key": "a.png"},
        {"candidate": "B", "version": 3, "qc_scores": uncertain_qc(),
         "r2_bucket": "b", "r2_key": "b.png"},
        {"candidate": "C", "version": 1, "qc_scores": None,
         "r2_bucket": "b", "r2_key": "c.png"},          # legacy — 유지
    ]
    import asyncio
    kept = asyncio.run(repo.list_series_reference_cuts(_Conn(rows), "p1", limit=8))
    assert [r["candidate"] for r in kept] == ["A", "C"]


# ── 4. HTTP 경계 — 선택/승인 ───────────────────────────────────────────────
def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def _route_db(monkeypatch, *, cut, project=None, patched=None):
    from conftest import patch_route_db
    patch_route_db(monkeypatch, routes)

    async def get_project(conn, user_id, project_id):
        return project if project is not None else {"id": project_id}

    async def get_cut(conn, user_id, project_id, cut_id):
        return cut

    async def patch_project(conn, user_id, project_id, fields):
        if patched is not None:
            patched.append(dict(fields))
        return {"id": project_id, "status": "draft", "title": "p",
                "compose_mode": "basic", "copywriting": False,
                "selected_mannequin_id": fields.get("selected_mannequin_id"),
                "adjust_count": 0,
                "created_at": "2026-08-08T00:00:00Z",
                "updated_at": "2026-08-08T00:00:00Z"}

    monkeypatch.setattr(repo, "get_project", get_project)
    monkeypatch.setattr(repo, "get_mannequin_cut_for_approval", get_cut)
    monkeypatch.setattr(repo, "patch_project", patch_project)


def test_selecting_an_unauthorized_cut_is_rejected(client, make_token, monkeypatch):
    written: list = []
    _route_db(monkeypatch, cut={"id": "A-3", "qc_scores": uncertain_qc()},
              patched=written)
    res = client.patch("/v1/projects/p1", headers=_auth(make_token),
                       json={"selectedMannequinId": "A-3"})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "cut_not_usable"
    assert written == [], "거절된 선택이 DB 에 기록되면 안 된다"


def test_selecting_a_valid_cut_still_works(client, make_token, monkeypatch):
    written: list = []
    _route_db(monkeypatch, cut={"id": "A-3", "qc_scores": valid_qc()}, patched=written)
    res = client.patch("/v1/projects/p1", headers=_auth(make_token),
                       json={"selectedMannequinId": "A-3"})
    assert res.status_code == 200
    assert written == [{"selected_mannequin_id": "A-3"}]


def test_selecting_a_missing_or_foreign_cut_is_404(client, make_token, monkeypatch):
    """존재하지 않는 컷과 타 사용자 컷은 같은 응답 — 존재 여부를 노출하지 않는다."""
    written: list = []
    _route_db(monkeypatch, cut=None, patched=written)
    res = client.patch("/v1/projects/p1", headers=_auth(make_token),
                       json={"selectedMannequinId": "Z-99"})
    assert res.status_code == 404
    assert written == []


def test_other_patch_fields_never_touch_the_cut_gate(client, make_token, monkeypatch):
    written: list = []
    _route_db(monkeypatch, cut={"id": "A-3", "qc_scores": uncertain_qc()},
              patched=written)
    res = client.patch("/v1/projects/p1", headers=_auth(make_token),
                       json={"composeMode": "extended"})
    assert res.status_code == 200
    assert written == [{"compose_mode": "extended"}]


def test_approving_an_unauthorized_cut_is_rejected(client, make_token, monkeypatch):
    approved: list = []
    _route_db(monkeypatch, cut={"id": "A-3", "mannequin_cut_id": "mc1",
                                "asset_id": "as1", "qc_scores": uncertain_qc()})

    async def approve(conn, **kwargs):
        approved.append(kwargs)
        return {"baseline": {"id": "b1"}, "superseded_id": None, "idempotent": False}

    monkeypatch.setattr(repo, "approve_mannequin_baseline", approve)
    res = client.post("/v1/projects/p1/mannequins:approve",
                      headers=_auth(make_token), json={"cutId": "A-3"})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "cut_not_usable"
    assert approved == [], "거절된 컷이 baseline 이 되면 안 된다"


# ── 5. 워커 소비 지점 — 오래된 선택 포인터 방어 ────────────────────────────
def test_detail_page_worker_refuses_a_stale_unauthorized_selection():
    """선택 시점 검증만 믿지 않는다 — 소비 직전에 한 번 더 본다."""
    import inspect
    from app.workers import detail_page_job

    src = inspect.getsource(detail_page_job.run_detail_page_job)
    block = src.split("selected_mannequin_id", 1)[1].split("color_assets", 1)[0]
    assert "mannequin_cut_authority.cut_is_consumable" in block
    # 앵커를 버릴 뿐 잡을 죽이지 않는다 — 상품 사진 근거로 계속 간다.
    assert "raise" not in block


def test_edit_parent_query_carries_qc_and_filters_it():
    import inspect
    src = inspect.getsource(repo.get_mannequin_edit_parent)
    assert "mc.qc_scores" in src
    assert "mannequin_cut_authority.cut_is_consumable" in src
    # 우선순위 정렬은 유지하되 창을 넓혀 막힌 컷을 건너뛴다 — 한 행만 뽑고 나서
    # 거절하면 폴백이 사라진다.
    assert "limit %s" in src
    assert repo._EDIT_PARENT_SCAN_LIMIT > 1


# ── 6. 이 패치가 건드리지 않는 것 ──────────────────────────────────────────
def test_gate_does_not_touch_job_or_credit_semantics():
    """SERVER_CONSUMPTION_BYPASS_CLOSED — UNCERTAIN_DONE_AND_CHARGE_REMAINS."""
    import inspect
    src = inspect.getsource(authority)
    for forbidden in ("jobs", "credits", "status =", "finalize", "await "):
        assert forbidden not in src, forbidden
