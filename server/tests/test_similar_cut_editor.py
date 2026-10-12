import asyncio
from contextlib import asynccontextmanager

import pytest

from app import routes
from app.workers import editor_image_job as eij
from conftest import FakeConn, auth_headers, fake_worker_app, make_settings, patch_route_db, worker_job


ROOT = "11111111-1111-4111-8111-111111111111"
CHILD = "22222222-2222-4222-8222-222222222222"
PLAN = {"actionKey": "hands-low", "actionSummary": "손을 내리기", "instructions": "Lower the hands.",
        "preserve": ["garment"], "avoid": ["relocation"]}


def asset(asset_id, **metadata):
    return {"id": asset_id, "source": "ai", "r2_key": asset_id, "mime_type": "image/png",
            "metadata": {"cut_type": "styling", **metadata}}


@pytest.mark.parametrize("case", ["foreign", "missing_root", "product", "detail", "forged", "child"])
def test_route_similar_validates_before_reserving(client, make_token, monkeypatch, case):
    seen = {}
    assets = {ROOT: asset(ROOT), CHILD: asset(CHILD, similar_cut={"version": 1, "rootAssetId": ROOT})}
    selected = CHILD if case in {"missing_root", "child"} else ROOT
    if case == "foreign":
        assets.clear()
    if case == "missing_root":
        del assets[ROOT]
    if case == "product":
        assets[ROOT]["metadata"]["cut_type"] = "product"
    if case == "detail":
        assets[ROOT]["metadata"]["detail_recipe"] = {"version": 1}

    async def project(*args):
        return {"id": "p1"}

    async def lookup(conn, uid, pid, aid):
        assert pid == "p1"
        return assets.get(aid)

    async def provenance(conn, uid, aid):
        seen["provenance"] = aid
        return None

    async def create(conn, **kwargs):
        seen["job"] = kwargs
        return {"id": "j1"}, True

    async def reserve(*args):
        seen["charged"] = True
        return 99

    patch_route_db(monkeypatch, routes)
    monkeypatch.setattr(routes.repo, "get_project", project)
    monkeypatch.setattr(routes.repo, "get_project_asset_for_user", lookup)
    monkeypatch.setattr(routes.repo, "get_asset_facemarket_provenance", provenance)
    monkeypatch.setattr(routes.repo, "create_job", create)
    monkeypatch.setattr(routes.repo, "reserve_credits", reserve)
    response = client.post("/v1/projects/p1/editor:generate-image", headers=auth_headers(make_token), json={
        "mode": "vary", "variationMode": "similar", "source": {"src": f"/v1/assets/{selected}/file", "cutType": "styling"},
        "changes": [], "_similarCut": {"rootAssetId": "forged", "plan": PLAN},
    })
    if case in {"foreign", "missing_root", "product", "detail"}:
        assert response.status_code == 400
        assert "job" not in seen and "charged" not in seen
    else:
        assert response.status_code == 202, response.text
        payload = seen["job"]["payload"]
        assert payload["_similarCut"] == {"version": 1, "rootAssetId": ROOT, "sourceAssetId": selected}
        assert payload["source"]["src"] == f"/v1/assets/{ROOT}/file"
        assert seen["provenance"] == ROOT


@pytest.mark.parametrize("case", ["success", "planner_failure", "resume", "lease_lost", "conflict", "missing_root", "busy", "conflict_limit"])
def test_similar_worker_plans_and_reserves_before_generation(monkeypatch, case):
    seen = {"plans": [], "generate": [], "saved": [], "failure": [], "connections": 0, "committed": False}
    record = {"version": 1, "rootAssetId": ROOT, "sourceAssetId": CHILD, "plan": PLAN}
    history = [{**record, "jobId": "previous-running", "plan": {**PLAN, "actionKey": "looking-away"}}]

    async def lookup(conn, uid, pid, aid):
        if case == "missing_root" and aid == ROOT:
            return None
        return asset(aid, **({"similar_cut": record} if aid == CHILD else {}))

    async def empty(*args, **kwargs):
        return {}

    async def prior(*args):
        return record if case == "resume" else None

    async def history_rows(*args, **kwargs):
        return history.copy()

    async def plan(settings, image, **kwargs):
        assert seen["connections"] == 0, "Vision calls must not hold a database connection"
        seen["plans"].append(kwargs)
        assert image.data == ROOT.encode()
        if case == "planner_failure":
            raise eij.VisionError("unavailable")
        return PLAN

    async def reserve(conn, **kwargs):
        seen["saved"].append(kwargs)
        if case == "lease_lost":
            return {"status": "lease_lost"}
        if case == "busy" and len(seen["saved"]) < 3:
            return {"status": "busy"}
        if case == "conflict_limit":
            return {"status": "history_changed"}
        if case == "conflict" and len(seen["saved"]) == 1:
            history.append({**record, "jobId": "new-racer"})
            return {"status": "history_changed"}
        return {"status": "existing" if case == "resume" else "reserved", "similar_cut": record}

    async def generate(settings, gemini, image, changes, cut_type, **kwargs):
        assert seen["saved"], "Plan must be persisted and lease fenced before image generation"
        assert seen["committed"] and seen["connections"] == 0
        assert image.data == ROOT.encode()
        assert kwargs["similar_plan"] == PLAN
        seen["generate"].append(kwargs)
        return b"generated", "image/png"

    async def success(conn, **kwargs):
        seen["success"] = kwargs
        return {"id": "result"}

    async def failure(conn, **kwargs):
        seen["failure"].append(kwargs)

    # 원본 계획과 리스의 고립 시험이며 설정 조회 원장은 별도 시험에서 다룬다.
    async def no_recipes(*args):
        return {}

    async def no_wardrobe(*args):
        return []

    monkeypatch.setattr(eij.repo, "get_project_asset_for_user", lookup)
    monkeypatch.setattr(eij.asset_recipe, "resolve_asset_recipes", no_recipes)
    monkeypatch.setattr(eij.repo, "list_wardrobe_images", no_wardrobe)
    monkeypatch.setattr(eij.repo, "get_product", empty)
    monkeypatch.setattr(eij.repo, "get_analysis", empty)
    monkeypatch.setattr(eij.repo, "get_similar_cut_plan_for_job", prior)
    monkeypatch.setattr(eij.repo, "list_similar_cut_plans", history_rows)
    monkeypatch.setattr(eij.repo, "reserve_similar_cut_plan", reserve)
    monkeypatch.setattr(eij.similar_cut, "plan_next", plan)
    monkeypatch.setattr(eij.cut_variator, "generate", generate)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_success", success)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_failure", failure)
    monkeypatch.setattr(eij.repo, "create_ai_output_cleanup_intent", empty)
    monkeypatch.setattr(eij, "_emit", empty)
    app = fake_worker_app(make_settings())

    class TrackingConn(FakeConn):
        async def commit(self):
            if seen["saved"]:
                seen["committed"] = True

    class TrackingPool:
        @asynccontextmanager
        async def connection(self):
            seen["connections"] += 1
            try:
                yield TrackingConn()
            finally:
                seen["connections"] -= 1

    app.state.pool = TrackingPool()
    app.state.r2.get_bytes = lambda key: key.encode()
    asyncio.run(eij.run_editor_image_job(app, worker_job({
        "mode": "vary", "variationMode": "similar", "source": {"src": f"/v1/assets/{ROOT}/file", "cutType": "styling"},
        "_similarCut": {k: v for k, v in record.items() if k != "plan"}, "changes": [],
    })))
    if case in {"planner_failure", "missing_root", "lease_lost", "conflict_limit"}:
        assert not seen["generate"] and "success" not in seen
        if case != "lease_lost":
            assert seen["failure"][0]["reserved"] == 1
        if case == "conflict_limit":
            assert len(seen["plans"]) == 2
    else:
        assert len(seen["generate"]) == 1
        assert seen["success"]["image"]["metadata"]["similar_cut"] == record
        assert seen["success"]["metadata"]["similar_cut"] == record
        assert len(seen["plans"]) == (0 if case == "resume" else 2 if case == "conflict" else 1)
        if seen["plans"]:
            assert seen["plans"][0]["history"][0]["jobId"] == "previous-running"


@pytest.mark.parametrize("override", [
    {"changes": "invalid"}, {"changes": {}}, {"changes": None}, {"changes": [{}]},
    {"changes": [{"type": "pose", "value": 1}]},
    {"changes": [{"type": "bg", "value": "cafe"}]}, {"changes": [{"type": "shot", "value": "full"}]},
    {"changes": [{"type": "pose", "value": "x"}] * 4}, {"refBgAssetId": ROOT},
    {"refBg": {"src": "other-place"}}, {"source": "not-an-object"},
])
def test_route_invalid_similar_settings_do_not_create_job(client, make_token, monkeypatch, override):
    async def project(*args):
        return {"id": "p1"}

    async def forbidden(*args, **kwargs):
        pytest.fail("Invalid similar settings must not reserve credits or create a job")

    monkeypatch.setattr(routes.repo, "get_project", project)
    monkeypatch.setattr(routes.repo, "create_job", forbidden)
    monkeypatch.setattr(routes.repo, "reserve_credits", forbidden)
    patch_route_db(monkeypatch, routes)
    response = client.post("/v1/projects/p1/editor:generate-image", headers=auth_headers(make_token), json={
        "mode": "vary", "variationMode": "similar", "source": {"src": f"/v1/assets/{ROOT}/file"},
        **override,
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_similar_cut"
