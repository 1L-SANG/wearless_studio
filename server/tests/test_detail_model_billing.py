"""WO4: provider와 외부 DB 없이 상세페이지 요금과 잠금 계약을 검증한다."""
import asyncio
import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app import plan_pricing, repo, routes
from app.agents import content_roles
from app.workers import detail_page_job as dpj
from conftest import auth_headers, patch_route_db
from storyboard_v4_cases import v4_blocks
from test_detail_incomplete_cuts_partial import STORYBOARD, _IntentDB, _R2, _run_job
from test_page_templates_v4 import style
from test_plan_pricing import _wire_sql_regenerate

_GET_DETAIL_PAGE_LOCKED_TEMPLATE = repo.get_detail_page_locked_template


def _wire(monkeypatch, *, plan="free", model="mF", mannequin=False, charged=False,
          ai=True, front=True, balance=200, existing=None, cached=False):
    patch_route_db(monkeypatch, routes)
    events = []
    analysis = {"selectedModelId": model, "pageStyle": style()}
    board = v4_blocks([{"id": "b1", "source": "ai" if ai else "mine",
                        "cutType": "horizon", "shot": "full"}])
    monkeypatch.setattr(repo, "get_project", AsyncMock(return_value={"id": "p1"}))
    monkeypatch.setattr(repo, "get_analysis", AsyncMock(side_effect=lambda *_: deepcopy(analysis)))
    monkeypatch.setattr(repo, "get_storyboard", AsyncMock(return_value=board))
    monkeypatch.setattr(repo, "get_editor_blocks", AsyncMock(return_value=[{"id": "done"}] if cached else []))
    monkeypatch.setattr(repo, "get_account", AsyncMock(return_value={"credits": balance}))
    monkeypatch.setattr(repo, "get_product", AsyncMock(return_value={"clothingType": "top", "colors": [
        {"isBase": True, "images": [{"slot": "Front", "id": "a1"}] if front else []}]}))
    async def lock(*_):
        events.append("lock")
    async def pricing(*_):
        events.append("pricing")
        return {"plan": plan, "mannequin_stage_used": mannequin, "already_charged": charged}
    async def create(*_, **kwargs):
        events.append("create")
        return {"id": "j1", **kwargs}, True
    async def reserve(*args):
        events.append("reserve")
        return balance - args[-1] if balance >= args[-1] else None
    monkeypatch.setattr(repo, "lock_page_style_project", AsyncMock(side_effect=lock))
    monkeypatch.setattr(repo, "get_detail_page_pricing_state", AsyncMock(side_effect=pricing), raising=False)
    monkeypatch.setattr(repo, "find_detail_page_request_job", AsyncMock(return_value=existing), raising=False)
    monkeypatch.setattr(repo, "create_job", AsyncMock(side_effect=create))
    monkeypatch.setattr(repo, "reserve_credits", AsyncMock(side_effect=reserve))
    monkeypatch.setattr(repo, "get_mannequin_pricing_state", AsyncMock(return_value={
        "plan": plan, "selected_model_id": model, "done_count": 0, "extension_fee_already_paid": False}))
    return analysis, board, events


@pytest.mark.parametrize("plan,model,mannequin,charged,ai,fee", [
    ("free", "mF", False, False, True, 19),
    ("starter", "mF", False, False, True, 19),
    ("seller", "mF", False, False, True, 10),
    ("pro", "mF", False, False, True, 0),
    ("free", "mA", False, False, True, 0),
    ("free", "mB", False, False, True, 0),
    ("free", "real-model-uuid", False, False, True, 0),
    ("free", "mF", True, False, True, 0),
    ("free", "mF", False, True, True, 0),
    ("free", "mF", False, False, False, 0),
])
def test_detail_reservation_and_legacy_metadata(client, make_token, monkeypatch,
                                               plan, model, mannequin, charged, ai, fee):
    _, board, events = _wire(monkeypatch, plan=plan, model=model, mannequin=mannequin,
                             charged=charged, ai=ai)
    response = client.post("/v1/projects/p1/detail-page:generate", headers=auth_headers(make_token))
    assert response.status_code == 202, response.text
    created = repo.create_job.call_args.kwargs
    cut_cost = 95 if ai else 0
    assert created["credits_reserved"] == cut_cost + fee
    expected = {"creditCostVersion": "v6", "perCutCost": 19, "aiCount": int(ai),
                "minCuts": 5, "storyboardDigest": dpj._storyboard_digest(board)}
    if fee:
        expected.update(extensionModelFee=fee, plan=plan, selectedModelId=model)
        assert created["payload"]["modelId"] == model
    assert created["metadata"] == expected
    assert events.index("lock") < events.index("create")
    if ai:
        assert repo.reserve_credits.call_args.args[-1] == cut_cost + fee
        assert events.index("lock") < events.index("pricing") < events.index("create") < events.index("reserve")
    else:
        repo.reserve_credits.assert_not_awaited()


@pytest.mark.parametrize("mannequin,charged,override,fee", [
    (False, False, None, 19), (True, False, None, 0),
    (False, True, None, 0), (False, False, "mA", 0), (False, False, "mN", 19),
])
def test_detail_quote_uses_shared_policy(client, make_token, monkeypatch, mannequin, charged, override, fee):
    _wire(monkeypatch, mannequin=mannequin, charged=charged)
    params = {} if override is None else {"selectedModelId": override}
    response = client.get("/v1/projects/p1/credit-quote", params=params, headers=auth_headers(make_token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["detailPageGenerate"] == {"extensionModelFee": fee, "selectedModelId": override or "mF"}
    assert body["mannequinGenerate"] == {"base": 45, "extensionModelFee": 0 if override == "mA" else 19,
        "total": 45 if override == "mA" else 64, "selectedModelId": override or "mF", "extensionFeeAlreadyPaid": False}
    assert body["plan"] == "free" and body["storyboardPerCut"] == 19


@pytest.mark.parametrize("cached", [False, True])
def test_join_and_cache_skip_front_check_and_model_pricing(client, make_token, monkeypatch, cached):
    _wire(monkeypatch, front=False, balance=0, cached=cached,
          existing={"id": "old", "status": "running", "payload": {}})
    response = client.post("/v1/projects/p1/detail-page:generate", headers=auth_headers(make_token))
    assert response.status_code == (200 if cached else 202), response.text
    repo.create_job.assert_not_awaited()
    repo.reserve_credits.assert_not_awaited()
    repo.get_detail_page_pricing_state.assert_not_awaited()


def test_detail_rechecks_template_after_project_lock(client, make_token, monkeypatch):
    analysis, _, _ = _wire(monkeypatch)
    async def lock(*_):
        analysis["pageStyle"] = style(templateId="show")
    monkeypatch.setattr(repo, "lock_page_style_project", lock)
    response = client.post("/v1/projects/p1/detail-page:generate", headers=auth_headers(make_token))
    assert response.status_code == 400 and response.json()["error"]["code"] == "page_style_mismatch"
    repo.create_job.assert_not_awaited()
    repo.reserve_credits.assert_not_awaited()


def test_model_fee_uses_payload_model_even_if_analysis_changes_under_lock(client, make_token, monkeypatch):
    analysis, _, _ = _wire(monkeypatch, model="mA")
    async def lock(*_):
        analysis["selectedModelId"] = "mF"
    monkeypatch.setattr(repo, "lock_page_style_project", lock)
    response = client.post("/v1/projects/p1/detail-page:generate", headers=auth_headers(make_token))
    assert response.status_code == 202, response.text
    created = repo.create_job.call_args.kwargs
    assert created["payload"]["modelId"] == "mA"
    assert created["credits_reserved"] == 95
    assert "extensionModelFee" not in created["metadata"]


def test_lock_keeps_the_storyboard_used_for_model_payload_and_license_gate(client, make_token, monkeypatch):
    _wire(monkeypatch)
    original = v4_blocks([{"id": "b1", "source": "ai", "cutType": "product", "shot": "ghost"}])
    current = {"board": original}
    async def storyboard(*_):
        return deepcopy(current["board"])
    async def lock(*_):
        current["board"] = v4_blocks([{"id": "b1", "source": "ai", "cutType": "horizon", "shot": "full"}])
    monkeypatch.setattr(repo, "get_storyboard", AsyncMock(side_effect=storyboard))
    monkeypatch.setattr(repo, "lock_page_style_project", lock)
    response = client.post("/v1/projects/p1/detail-page:generate", headers=auth_headers(make_token))
    assert response.status_code == 202, response.text
    created = repo.create_job.call_args.kwargs
    assert "modelId" not in created["payload"]
    assert created["metadata"]["storyboardDigest"] == dpj._storyboard_digest(original)
    assert created["metadata"]["storyboardDigest"] != dpj._storyboard_digest(current["board"])


@pytest.mark.parametrize("mannequin", [False, True])
@pytest.mark.parametrize("field,value", [("templateId", "show"), ("fontStyle", "serif"),
                                        ("colorMode", "point"), ("updatedAt", "2026-11-13T10:00:00Z")])
def test_detail_job_locks_only_template(client, make_token, monkeypatch, mannequin, field, value):
    _wire(monkeypatch)
    monkeypatch.setattr(repo, "page_style_locked", AsyncMock(return_value=mannequin))
    monkeypatch.setattr(repo, "has_detail_page_job", AsyncMock(return_value=True), raising=False)
    monkeypatch.setattr(repo, "save_analysis", AsyncMock(return_value={
        "project_id": "p1", "payload": {"pageStyle": style(**{field: value})}}))
    response = client.patch("/v1/projects/p1/analysis?merge=1", headers=auth_headers(make_token),
                            json={"pageStyle": style(**{field: value})})
    if field == "templateId":
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "page_style_locked"
        assert response.json()["error"]["message"] == (
            "마네킹컷을 만든 뒤에는 템플릿을 바꿀 수 없어요." if mannequin else
            "상세페이지를 만들기 시작한 뒤에는 템플릿을 바꿀 수 없어요.")
        repo.save_analysis.assert_not_awaited()
    else:
        assert response.status_code == 200, response.text
        repo.save_analysis.assert_awaited_once()


def _show_storyboard():
    board = [{"id": "b1", "taxonomyVersion": 4, "pageSectionKey": "show.first",
              "pageSectionKind": "first", "pageDesignId": "P01-SHOW-01", "sectionId": "first",
              "sectionLayout": "stack", "source": "ai", "slotKey": "first.hero",
              "contentRole": "hero", "sectionRole": "hooking", "cutType": "horizon",
              "shot": "medium", "direction": "front"}]
    content_roles.validate_storyboard_v4(board)
    return board


def test_detail_start_pins_template_before_later_storyboard_save(client, make_token, monkeypatch):
    conn = _sql_detail(monkeypatch)
    board = _show_storyboard()
    monkeypatch.setattr(repo, "get_storyboard", AsyncMock(return_value=board))
    save_board = AsyncMock(return_value=1)
    monkeypatch.setattr(repo, "save_storyboard", save_board)
    headers = auth_headers(make_token)

    started = client.post("/v1/projects/p1/detail-page:generate", headers=headers)
    assert started.status_code == 202, started.text
    different_template = v4_blocks([{"id": "b1", "source": "ai", "cutType": "horizon", "shot": "full"}])
    rejected = client.put("/v1/projects/p1/storyboard", headers=headers, json=different_template)
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["error"]["code"] == "page_style_mismatch"
    save_board.assert_not_awaited()
    assert asyncio.run(_GET_DETAIL_PAGE_LOCKED_TEMPLATE(conn, "p1")) == "show"

    monkeypatch.setattr(repo, "save_analysis", AsyncMock(return_value={
        "project_id": "p1", "payload": {"pageStyle": style(templateId="show", fontStyle="serif")},
    }))
    resp = client.patch("/v1/projects/p1/analysis?merge=1", headers=headers,
                        json={"pageStyle": style(templateId="show", fontStyle="serif")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["pageStyle"]["fontStyle"] == "serif"
    refused = client.patch("/v1/projects/p1/analysis?merge=1", headers=headers,
                           json={"pageStyle": style(templateId="emo")})
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "page_style_locked"


@pytest.mark.parametrize("patch,status", [
    ({"fontStyle": "serif"}, 200), ({"colorMode": "point"}, 200),
    ({"templateId": "emo"}, 409),
])
def test_detail_lock_infers_missing_style_from_show_storyboard(client, make_token, monkeypatch, patch, status):
    analysis, _, _ = _wire(monkeypatch)
    analysis.pop("pageStyle")
    monkeypatch.setattr(repo, "get_storyboard", AsyncMock(return_value=_show_storyboard()))
    monkeypatch.setattr(repo, "page_style_locked", AsyncMock(return_value=False))
    monkeypatch.setattr(repo, "has_detail_page_job", AsyncMock(return_value=True))
    requested = style(templateId="show", **patch) if "templateId" not in patch else style(**patch)
    monkeypatch.setattr(repo, "save_analysis", AsyncMock(return_value={
        "project_id": "p1", "payload": {"pageStyle": requested}}))
    response = client.patch("/v1/projects/p1/analysis?merge=1", headers=auth_headers(make_token),
                            json={"pageStyle": requested})
    assert response.status_code == status, response.text
    if status == 409:
        assert response.json()["error"]["code"] == "page_style_locked"
        assert response.json()["error"]["message"] == "상세페이지를 만들기 시작한 뒤에는 템플릿을 바꿀 수 없어요."
        repo.save_analysis.assert_not_awaited()
    else:
        assert response.json()["pageStyle"] == requested
        repo.save_analysis.assert_awaited_once()


@pytest.mark.parametrize("board", [[], [{"taxonomyVersion": 3, "pageSectionKey": "show.first"}],
    [{"taxonomyVersion": 4, "pageSectionKey": "show.first"}, {"taxonomyVersion": 3, "pageSectionKey": "show.first"}]])
@pytest.mark.parametrize("template,status", [("emo", 200), ("show", 409)])
def test_detail_lock_missing_style_uses_default_for_empty_or_legacy_storyboard(
    client, make_token, monkeypatch, board, template, status,
):
    analysis, _, _ = _wire(monkeypatch)
    analysis.pop("pageStyle")
    monkeypatch.setattr(repo, "get_storyboard", AsyncMock(return_value=board))
    monkeypatch.setattr(repo, "page_style_locked", AsyncMock(return_value=False))
    monkeypatch.setattr(repo, "has_detail_page_job", AsyncMock(return_value=True))
    requested = style(templateId=template)
    monkeypatch.setattr(repo, "save_analysis", AsyncMock(return_value={
        "project_id": "p1", "payload": {"pageStyle": requested}}))
    response = client.patch("/v1/projects/p1/analysis?merge=1", headers=auth_headers(make_token),
                            json={"pageStyle": requested})
    assert response.status_code == status, response.text
    if status == 409:
        assert response.json()["error"]["code"] == "page_style_locked"
        assert response.json()["error"]["message"] == "상세페이지를 만들기 시작한 뒤에는 템플릿을 바꿀 수 없어요."
        repo.save_analysis.assert_not_awaited()
    else:
        assert response.json()["pageStyle"] == requested


@pytest.mark.parametrize("template,status", [("emo", 200), ("show", 409)])
def test_mannequin_lock_keeps_default_even_with_detail_job_and_show_storyboard(
    client, make_token, monkeypatch, template, status,
):
    analysis, _, _ = _wire(monkeypatch)
    analysis.pop("pageStyle")
    monkeypatch.setattr(repo, "get_storyboard", AsyncMock(return_value=_show_storyboard()))
    monkeypatch.setattr(repo, "page_style_locked", AsyncMock(return_value=True))
    monkeypatch.setattr(repo, "has_detail_page_job", AsyncMock(return_value=True))
    monkeypatch.setattr(repo, "save_analysis", AsyncMock(return_value={
        "project_id": "p1", "payload": {"pageStyle": style(templateId=template)}}))
    response = client.patch("/v1/projects/p1/analysis?merge=1", headers=auth_headers(make_token),
                            json={"pageStyle": style(templateId=template)})
    assert response.status_code == status, response.text
    if status == 409:
        assert response.json()["error"]["message"] == "마네킹컷을 만든 뒤에는 템플릿을 바꿀 수 없어요."
        repo.save_analysis.assert_not_awaited()
    repo.get_storyboard.assert_not_awaited()


def _sql_detail(monkeypatch, *, balance=200, front=True):
    conn = _wire_sql_regenerate(monkeypatch, plan="free", done_count=0, balance=balance)
    conn.db.execute("create table mannequin_cuts(project_id text)")
    conn.db.execute("insert into analyses values ('p1', ?)", (json.dumps({"selectedModelId": "mF"}),))
    conn.db.commit()
    monkeypatch.setattr(repo, "get_analysis", AsyncMock(return_value={"selectedModelId": "mF"}))
    monkeypatch.setattr(repo, "get_storyboard", AsyncMock(return_value=v4_blocks([
        {"id": "b1", "source": "ai", "cutType": "horizon", "shot": "full"}])))
    monkeypatch.setattr(repo, "get_editor_blocks", AsyncMock(return_value=[]))
    if not front:
        monkeypatch.setattr(repo, "get_product", AsyncMock(return_value={}))
    return conn


@pytest.mark.parametrize("front,balance,status,code", [
    (False, 200, 400, "missing_front_photo"), (True, 95, 402, "insufficient_credits"),
])
def test_rejected_detail_rolls_back_job_and_reservation(client, make_token, monkeypatch, front, balance, status, code):
    conn = _sql_detail(monkeypatch, balance=balance, front=front)
    response = client.post("/v1/projects/p1/detail-page:generate", headers=auth_headers(make_token))
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert conn.db.execute("select count(*) from jobs").fetchone()[0] == 0
    assert conn.db.execute("select reserved from credit_accounts").fetchone()[0] == 0


@pytest.mark.parametrize("kind,status,charged,expected", [
    ("detail_page", "done", 19, 0), ("detail_page", "done", 0, 19),
    ("detail_page", "failed", 19, 19), ("detail_page", "running", 19, 19),
    ("mannequin", "pending", 0, 0), ("mannequin", "running", 0, 0),
    ("mannequin_adjust", "failed", 0, 0),
])
def test_pricing_state_reads_only_done_actual_model_charge(monkeypatch, kind, status, charged, expected):
    conn = _sql_detail(monkeypatch)
    conn.db.execute("insert into jobs(user_id,project_id,kind,status,metadata) values ('user-1','p1',?,?,?)",
                    (kind, status, json.dumps({"extensionModelFee": 19, "extensionModelFeeCharged": charged})))
    state = asyncio.run(repo.get_detail_page_pricing_state(conn, "user-1", "p1"))
    assert plan_pricing.detail_page_model_fee(state["plan"], "mF",
        mannequin_stage_used=state["mannequin_stage_used"], already_charged=state["already_charged"]) == expected
    assert asyncio.run(repo.has_detail_page_job(conn, "p1")) == (kind == "detail_page")


def test_mannequin_cut_alone_waives_detail_fee(monkeypatch):
    conn = _sql_detail(monkeypatch)
    conn.db.execute("insert into mannequin_cuts values ('p1')")
    state = asyncio.run(repo.get_detail_page_pricing_state(conn, "user-1", "p1"))
    assert state["mannequin_stage_used"] is True


@pytest.mark.parametrize("status", ["pending", "running", "failed", "done"])
def test_quote_waives_fee_for_every_mannequin_job_state(client, make_token, monkeypatch, status):
    conn = _sql_detail(monkeypatch)
    conn.db.execute("insert into jobs(user_id,project_id,kind,status,metadata) "
                    "values ('user-1','p1','mannequin',?,'{}')", (status,))
    response = client.get("/v1/projects/p1/credit-quote", headers=auth_headers(make_token))
    assert response.status_code == 200, response.text
    assert response.json()["detailPageGenerate"] == {"extensionModelFee": 0, "selectedModelId": "mF"}


@pytest.mark.parametrize("charged", [0, 19])
def test_next_detail_request_charges_fee_only_until_first_paid_success(client, make_token, monkeypatch, charged):
    conn = _sql_detail(monkeypatch)
    conn.db.execute("insert into jobs(user_id,project_id,kind,status,metadata) "
                    "values ('user-1','p1','detail_page','done',?)",
                    (json.dumps({"extensionModelFee": 19, "extensionModelFeeCharged": charged}),))
    response = client.post("/v1/projects/p1/detail-page:generate", headers=auth_headers(make_token))
    assert response.status_code == 202, response.text
    row = conn.db.execute("select credits_reserved,metadata from jobs where status='pending'").fetchone()
    assert row[0] == (95 if charged else 114)
    assert ("extensionModelFee" in json.loads(row[1])) is (not charged)


def test_detail_quote_matches_generation_for_legacy_model_key(client, make_token, monkeypatch):
    conn = _sql_detail(monkeypatch)
    analysis = {"selected_model_id": "mF"}
    conn.db.execute("update analyses set payload = ?", (json.dumps(analysis),))
    monkeypatch.setattr(repo, "get_analysis", AsyncMock(return_value=analysis))
    quote = client.get("/v1/projects/p1/credit-quote", headers=auth_headers(make_token))
    assert quote.status_code == 200, quote.text
    assert quote.json()["mannequinGenerate"]["selectedModelId"] is None
    assert quote.json()["mannequinGenerate"]["extensionModelFee"] == 0
    assert quote.json()["detailPageGenerate"] == {"extensionModelFee": 19, "selectedModelId": "mF"}
    response = client.post("/v1/projects/p1/detail-page:generate", headers=auth_headers(make_token))
    assert response.status_code == 202, response.text
    assert conn.db.execute("select credits_reserved from jobs where status='pending'").fetchone()[0] == 114


def test_request_lookup_prefers_exact_key_and_scopes_active_job(monkeypatch):
    conn = _sql_detail(monkeypatch)
    for job_id, user, project, kind, status, key in [
        ("keyed", "user-1", "p1", "detail_page", "failed", "original"),
        ("active", "user-1", "p1", "detail_page", "running", "new"),
        ("foreign", "other", "p2", "detail_page", "running", "foreign"),
    ]:
        conn.db.execute("insert into jobs(id,user_id,project_id,kind,status,idempotency_key) values (?,?,?,?,?,?)",
                        (job_id, user, project, kind, status, key))
    assert asyncio.run(repo.find_detail_page_request_job(conn, "user-1", "p1", "original"))["id"] == "keyed"
    assert asyncio.run(repo.find_detail_page_request_job(conn, "user-1", "p1", None))["id"] == "active"
    assert asyncio.run(repo.find_detail_page_request_job(conn, "user-1", "p2", "foreign")) is None


@pytest.mark.parametrize("user,project,kind", [
    ("other", "p1", "detail_page"), ("user-1", "other", "detail_page"),
    ("user-1", "p1", "editor_image"),
])
def test_paid_success_in_other_scope_does_not_waive_model_fee(monkeypatch, user, project, kind):
    conn = _sql_detail(monkeypatch)
    conn.db.execute("insert into jobs(user_id,project_id,kind,status,metadata) values (?,?,?,'done',?)",
                    (user, project, kind, json.dumps({"extensionModelFeeCharged": 19})))
    state = asyncio.run(repo.get_detail_page_pricing_state(conn, "user-1", "p1"))
    assert not state["already_charged"]


@pytest.mark.parametrize("model_fee", [10, 19])
@pytest.mark.parametrize("failures,cut_charge", [(set(), 95), ({"b2"}, 76), ({"b1", "b2"}, 0)])
def test_worker_settles_reserved_model_fee_with_completed_cut_charge(monkeypatch, model_fee, failures, cut_charge):
    reserved = 95 + model_fee
    expected = cut_charge + model_fee if cut_charge else 0
    original = dpj.run_detail_page_job
    async def with_snapshot(app, job):
        job["metadata"].update(extensionModelFee=model_fee, aiCount=2, minCuts=5)
        await original(app, job)
    monkeypatch.setattr(dpj, "run_detail_page_job", with_snapshot)
    _IntentDB().install(monkeypatch)
    result = _run_job(monkeypatch, job_id="fee", r2=_R2(), fail_blocks=failures, calls=[], reserved=reserved)
    if not expected:
        assert "success" not in result
        assert result["failure"]["reserved"] == reserved
        assert result["failure"]["code"] == "all_cuts_failed"
    else:
        success = result["success"]
        assert success["reserved"] == reserved
        assert success["charge"] == expected <= success["reserved"]
        assert success["metadata"]["extensionModelFeeCharged"] == model_fee
        if failures:
            partial = success["metadata"]["partial"]
            assert (partial["quoteCredits"], partial["creditsCharged"], partial["creditsNotCharged"]) == (reserved, expected, 19)


def test_worker_keeps_reserved_seller_fee_after_plan_changes_to_pro(client, make_token, monkeypatch):
    _wire(monkeypatch, plan="seller")
    repo.get_storyboard.return_value = v4_blocks(deepcopy(STORYBOARD))
    response = client.post("/v1/projects/p1/detail-page:generate", headers=auth_headers(make_token))
    assert response.status_code == 202, response.text
    reservation = deepcopy(repo.create_job.call_args.kwargs)
    assert reservation["credits_reserved"] == 105
    assert reservation["metadata"]["extensionModelFee"] == 10
    assert reservation["metadata"]["plan"] == "seller"
    repo.get_detail_page_pricing_state.reset_mock()
    repo.get_detail_page_pricing_state.side_effect = None
    repo.get_detail_page_pricing_state.return_value = {
        "plan": "pro", "mannequin_stage_used": False, "already_charged": False}
    repo.get_account.return_value = {"plan": "pro", "credits": 200}
    assert plan_pricing.extension_model_fee("pro", "mF") == 0
    original = dpj.run_detail_page_job
    async def with_snapshot(app, job):
        job["metadata"] = deepcopy(reservation["metadata"])
        await original(app, job)
    monkeypatch.setattr(dpj, "run_detail_page_job", with_snapshot)
    _IntentDB().install(monkeypatch)
    result = _run_job(monkeypatch, job_id="plan-change", r2=_R2(), fail_blocks={"b2"}, calls=[],
                      reserved=reservation["credits_reserved"])
    assert result["success"]["charge"] == 86
    assert result["success"]["metadata"]["extensionModelFeeCharged"] == 10
    repo.get_detail_page_pricing_state.assert_not_awaited()


@pytest.mark.parametrize("per_cut,reserved,expected,charged", [(19, 57, 57, 19), (0, 19, 0, 0)])
def test_worker_caps_total_and_records_zero_model_charge(monkeypatch, per_cut, reserved, expected, charged):
    original = dpj.run_detail_page_job
    async def with_snapshot(app, job):
        job["metadata"].update(extensionModelFee=19, aiCount=2, minCuts=5, perCutCost=per_cut)
        await original(app, job)
    monkeypatch.setattr(dpj, "run_detail_page_job", with_snapshot)
    _IntentDB().install(monkeypatch)
    result = _run_job(monkeypatch, job_id="cap", r2=_R2(), fail_blocks=set(), calls=[], reserved=reserved)
    assert result["success"]["charge"] == expected <= reserved
    assert result["success"]["metadata"]["extensionModelFeeCharged"] == charged
