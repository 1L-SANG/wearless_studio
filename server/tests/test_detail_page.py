import asyncio
import hashlib
import contextlib
import inspect
import logging
import types
from dataclasses import replace

import app.routes as routes
import pytest
from app import repo
from app.agents import identity_source
from app.workers import detail_page_job as dpj
from conftest import auth_headers, fake_worker_app, make_settings, patch_route_db, worker_job


MODEL_ID = "11111111-1111-1111-1111-111111111111"
LICENSE_ID = "22222222-2222-2222-2222-222222222222"
CATEGORY = "상의"


@pytest.mark.parametrize("reason", ["detail_qc_failed", "detail_qc_unknown"])
def test_detail_rejection_keeps_other_cut_without_asset_preview_or_charge(monkeypatch, reason):
    captured = {"writes": [], "events": []}

    async def project(*args):
        return {"copywriting": False}

    async def storyboard(*args):
        return [{"id": block_id, "source": "ai", "sectionRole": "product", "contentRole": "detail",
                 "cutType": "product", "shot": "detail", "direction": direction, "colorId": "base"}
                for block_id, direction in [("rejected", "front"), ("accepted", "back")]]

    async def product(*args):
        return {"colors": [{"id": "base", "isBase": True, "images": [
            {"slot": "Front", "id": "front"}, {"slot": "Back", "id": "back"}]}]}

    async def analysis(*args):
        return {}

    async def asset(conn, user_id, asset_id):
        return {"mime_type": "image/png", "r2_key": f"k/{asset_id}"}

    async def detail(app, job, spec, product, analysis):
        if spec["id"] == "rejected":
            raise dpj.DetailShotRejected(reason, {"passed": False, "attempts": 2})
        return dpj.InlineImage("image/png", b"PASSED-IMAGE"), {"passed": True}

    def assemble(storyboard, cuts, *args, **kwargs):
        captured["cuts"] = cuts
        return []

    async def finalize(conn, **kwargs):
        captured["finalize"] = kwargs
        return {"editor_blocks": [], "available": 99}

    async def emit(pool, job_id, event, payload):
        captured["events"].append((event, payload))

    monkeypatch.setattr(dpj.repo, "get_project", project)
    monkeypatch.setattr(dpj.repo, "get_storyboard", storyboard)
    monkeypatch.setattr(dpj.repo, "get_product", product)
    monkeypatch.setattr(dpj.repo, "get_analysis", analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", asset)
    monkeypatch.setattr(dpj.detail_shot_runtime, "generate", detail)
    monkeypatch.setattr(dpj.page_assembler, "assemble", assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", finalize)
    monkeypatch.setattr(dpj, "_emit", emit)
    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"))
    app.state.r2.put_bytes = lambda key, data, *args, **kwargs: captured["writes"].append(data)
    asyncio.run(dpj.run_detail_page_job(app, _job(reserved=2, per_cut=1)))

    assert [cut["blockId"] for cut in captured["cuts"]] == ["accepted"]
    assert captured["writes"] == [b"PASSED-IMAGE"]
    assert len(captured["finalize"]["cut_assets"]) == 1
    assert captured["finalize"]["charge"] == 1
    assert captured["finalize"]["reserved"] == 2
    done = [payload for event, payload in captured["events"] if payload.get("status") == "cut_done"]
    assert [payload["blockId"] for payload in done] == ["accepted"]
    failed = [payload for event, payload in captured["events"] if payload.get("status") == "cut_failed"]
    assert any(payload["blockId"] == "rejected" and payload.get("reason") == reason for payload in failed)


# ---------- 라우트 ----------

def test_detail_404(client, make_token, monkeypatch):
    async def fake_gp(conn, uid, pid):
        return None
    monkeypatch.setattr(routes.repo, "get_project", fake_gp)
    patch_route_db(monkeypatch, routes)
    res = client.post("/v1/projects/nope/detail-page:generate", headers=auth_headers(make_token))
    assert res.status_code == 404


def test_detail_creates_job_and_reserves(client, make_token, monkeypatch):
    seen = {}

    class Scaler:
        def prewarm_soon(self):
            seen["prewarmed"] = seen.get("prewarmed", 0) + 1

    client.app.state.detail_worker_autoscaler = Scaler()

    async def fake_gp(conn, uid, pid):
        return {"id": pid}

    async def fake_eb(conn, pid):
        return []  # 미완료

    async def fake_sb(conn, pid):
        return [{"id": "b1", "source": "ai"}, {"id": "b2", "source": "ai"}, {"id": "m", "source": "mine"}]

    async def fake_create_job(conn, **kw):
        seen.update(kw)
        return {"id": "job-dp-1"}, True

    async def fake_reserve(conn, uid, amount):
        seen["reserved"] = amount
        return 100

    async def fake_product(conn, pid):
        # 크레딧 견적의 복제 접기(_duplicate_source_indexes)가 clothing_type을 읽는다.
        return {"clothing_type": "top"}

    async def fake_analysis(conn, pid):
        return {}

    monkeypatch.setattr(routes.repo, "get_project", fake_gp)
    monkeypatch.setattr(routes.repo, "get_editor_blocks", fake_eb)
    monkeypatch.setattr(routes.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(routes.repo, "get_product", fake_product)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    patch_route_db(monkeypatch, routes)
    res = client.post("/v1/projects/p1/detail-page:generate", headers=auth_headers(make_token))
    assert res.status_code == 202, res.text
    assert res.json()["jobId"] == "job-dp-1"
    assert seen["kind"] == "detail_page"
    assert seen["credits_reserved"] == 38  # ai 블록 2개 × storyboardPerCut(19)
    assert seen["reserved"] == 38
    # 예약 시점 단가 스냅샷 — 워커 정산의 단일 기준(정산 불변식)
    assert seen["metadata"]["perCutCost"] == 19
    assert seen["metadata"]["creditCostVersion"] == "v6"
    assert seen["metadata"]["aiCount"] == 2
    assert seen["prewarmed"] == 1


def test_detail_rejects_saved_bg_example_before_job_or_credit(
    client, make_token, monkeypatch,
):
    calls = {"create_job": 0, "reserve": 0}

    async def fake_gp(conn, uid, pid):
        return {"id": pid}

    async def fake_eb(conn, pid):
        return []

    async def fake_sb(conn, pid):
        return [{
            "id": "b1",
            "source": "ai",
            "exampleId": "ex-bg-1",
            "refScope": "bg",
        }]

    async def fake_create_job(conn, **kw):
        calls["create_job"] += 1
        return {"id": "job-dp-bg"}, True

    async def fake_reserve(conn, uid, amount):
        calls["reserve"] += 1
        return 100

    async def fake_analysis(conn, pid):
        return {}

    monkeypatch.setattr(routes.repo, "get_project", fake_gp)
    monkeypatch.setattr(routes.repo, "get_editor_blocks", fake_eb)
    monkeypatch.setattr(routes.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    patch_route_db(monkeypatch, routes)

    res = client.post(
        "/v1/projects/p1/detail-page:generate",
        headers=auth_headers(make_token),
    )

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "genexample_bg_disabled"
    assert calls == {"create_job": 0, "reserve": 0}


def test_detail_completed_recall(client, make_token, monkeypatch):
    async def fake_gp(conn, uid, pid):
        return {"id": pid}

    async def fake_eb(conn, pid):
        return [{"id": "b0", "kind": "benefit", "contentRole": "hero"}]  # 이미 완료

    async def fake_acct(conn, uid):
        return {"credits": 42}

    async def fake_analysis(conn, pid):
        return {}

    monkeypatch.setattr(routes.repo, "get_project", fake_gp)
    monkeypatch.setattr(routes.repo, "get_editor_blocks", fake_eb)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(routes.repo, "get_account", fake_acct)
    patch_route_db(monkeypatch, routes)
    res = client.post("/v1/projects/p1/detail-page:generate", headers=auth_headers(make_token))
    assert res.status_code == 200
    body = res.json()
    assert body["data"][0]["id"] == "b0" and body["credits"] == 42


class _RouteConn:
    def __init__(self, events):
        self.events = events

    async def commit(self):
        self.events.append("commit")


def _patch_counted_route_conn(monkeypatch, events):
    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _RouteConn(events)

    async def fake_lock(_conn):
        events.append("cutover_lock")

    async def fake_closed(_conn):
        events.append("cutover_open")
        return False

    monkeypatch.setattr(routes, "get_conn", fake_conn)
    monkeypatch.setattr(routes.repo, "lock_facemarket_writer_boundary", fake_lock)
    monkeypatch.setattr(routes.repo, "facemarket_writer_boundary_closed", fake_closed)


def test_detail_product_only_storyboard_strips_real_model_before_facemarket_gate(
    client, make_token, monkeypatch
):
    events = []
    seen = {}
    client.app.state.settings = replace(
        client.app.state.settings,
        facemarket_enabled=True,
    )

    async def fake_project(conn, user_id, project_id):
        return {"id": project_id, "facemarket_license_id": "stale"}

    async def fake_analysis(conn, project_id):
        return {"selectedModelId": MODEL_ID, "brandUseCategory": CATEGORY}

    async def fake_storyboard(conn, project_id):
        return [{"id": "p1", "source": "ai", "cutType": "product", "shot": "ghost"}]

    async def forbidden_resolve(*_args, **_kwargs):
        raise AssertionError("product-only detail generation must not enter FaceMarket gate")

    async def fake_editor(conn, project_id):
        events.append("cache")
        return []

    async def fake_product(conn, project_id):
        return {"clothing_type": "top"}

    async def fake_create(conn, **kwargs):
        seen.update(kwargs)
        events.append("job")
        return {"id": "job-product"}, True

    async def fake_reserve(conn, user_id, amount):
        events.append("reserve")
        return 10

    monkeypatch.setattr(routes.repo, "get_project", fake_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(routes.repo, "get_storyboard", fake_storyboard)
    monkeypatch.setattr(routes.facemarket, "resolve_project_license", forbidden_resolve)
    monkeypatch.setattr(routes.repo, "get_editor_blocks", fake_editor)
    monkeypatch.setattr(routes.repo, "get_product", fake_product)
    monkeypatch.setattr(routes.repo, "create_job", fake_create)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    _patch_counted_route_conn(monkeypatch, events)

    response = client.post(
        "/v1/projects/p1/detail-page:generate",
        headers=auth_headers(make_token),
    )

    # 실제 모델은 hooking·styling·studio 섹션 컷만 만든다(2026-09-14 studio, 2026-09-25 styling,
    # 2026-09-26 hooking 추가). 제품 컷만 있는 콘티는 만들 수 있는 컷이 0 이라 잡도 예약도
    # 없이 끝난다("전부 걸러져 0컷이면 400" — 사용자 지시).
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "real_model_studio_only"
    assert seen == {} and events == ["cache"]


def _post_real_detail_generate(client, make_token, monkeypatch, storyboard):
    """실제 모델(MODEL_ID)을 고른 프로젝트로 상세페이지 생성을 요청한다 — 라이선스 확인은 통과.

    돌려주는 seen: create_job 에 넘어간 인자 + reserved(reserve_credits 금액). 잡을 만들지
    않았으면 비어 있다.
    """
    seen = {}
    client.app.state.settings = replace(
        client.app.state.settings,
        facemarket_enabled=True,
    )

    async def fake_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_analysis(conn, project_id):
        return {"selectedModelId": MODEL_ID, "brandUseCategory": CATEGORY}

    async def fake_storyboard(conn, project_id):
        return storyboard

    async def fake_resolve(conn, project, analysis):
        return {"id": LICENSE_ID, "model_id": MODEL_ID}

    def fake_verify(*args, **kwargs):
        return None

    async def fake_lock(conn, project_id, license_id):
        return None

    async def fake_editor(conn, project_id):
        return []

    async def fake_product(conn, project_id):
        return {"clothing_type": "top"}

    async def fake_create(conn, **kwargs):
        seen.update(kwargs)
        return {"id": "job-real"}, True

    async def fake_reserve(conn, user_id, amount):
        seen["reserved"] = amount
        return 10

    monkeypatch.setattr(routes.repo, "get_project", fake_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(routes.repo, "get_storyboard", fake_storyboard)
    monkeypatch.setattr(routes.facemarket, "resolve_project_license", fake_resolve)
    monkeypatch.setattr(routes.facemarket, "verify_license_local", fake_verify)
    monkeypatch.setattr(routes.facemarket, "set_project_license", fake_lock)
    monkeypatch.setattr(routes.repo, "get_editor_blocks", fake_editor)
    monkeypatch.setattr(routes.repo, "get_product", fake_product)
    monkeypatch.setattr(routes.repo, "create_job", fake_create)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    _patch_counted_route_conn(monkeypatch, [])

    response = client.post(
        "/v1/projects/p1/detail-page:generate",
        headers=auth_headers(make_token),
    )
    return response, seen


def test_detail_real_styling_needs_no_virtual_stand_in(
    client, make_token, monkeypatch
):
    """스타일링 컷은 **실제 모델 그대로** 만든다 — 가상 대역 없음(2026-09-25 사용자 결정).

    이력: 2026-09-11 에 "가상 대역 없이 실제 모델 그대로 간다" 로 바뀌었고(400
    styling_model_required 폐기), 2026-09-14 에 studio 섹션만 남겼다가, 2026-09-25 에 styling
    섹션을 다시 열었다(호리존만으로는 상세페이지가 모자라다). 2026-09-26 hooking 도 열려 남은
    막힌 섹션은 product 뿐이다 — 그 컷은 예약에서 빠진다(만들지도 않을 컷에 크레딧을 잡지 않는다).
    """
    response, seen = _post_real_detail_generate(client, make_token, monkeypatch, [
        {"id": "s1", "source": "ai", "cutType": "styling"},
        # 아직 막힌 섹션(product) — 예약에서 빠져야 한다
        {"id": "p1", "source": "ai", "sectionRole": "product", "contentRole": "productOverview",
         "cutType": "product", "direction": "front", "shot": "ghost"},
    ])

    assert response.status_code == 202, response.text
    # 스타일링 컷이 실제 모델 하나로 간다 — 가상 대역(stylingModelId) 없이 라이선스가 붙는다.
    assert seen["payload"] == {
        "mode": "generate",
        "modelId": MODEL_ID,
        "brandUseCategory": CATEGORY,
        "_facemarket": {"modelId": MODEL_ID, "licenseId": LICENSE_ID},
    }
    # 제품 컷은 예약에서 빠진다 — 예약 = 실제로 만들 스타일링 1컷.
    per_cut = client.app.state.settings.credit_cost_storyboard_per_cut
    assert seen["metadata"]["aiCount"] == 1
    assert seen["credits_reserved"] == per_cut
    assert seen["reserved"] == per_cut


def test_detail_real_hooking_cuts_are_reserved_like_styling(
    client, make_token, monkeypatch
):
    """첫 장면(hooking)은 2026-09-26 사용자 결정으로 열렸다 — 실제 모델 그대로, 예약에 든다.

    예전(2026-09-14~09-25)에는 hooking 만 있는 콘티가 400 real_model_studio_only 였다.
    """
    response, seen = _post_real_detail_generate(client, make_token, monkeypatch, [
        {"id": "k1", "source": "ai", "sectionRole": "hooking", "contentRole": "hero",
         "cutType": "styling", "direction": "front", "shot": "full"},
        {"id": "k2", "source": "ai", "sectionRole": "hooking", "contentRole": "benefit",
         "cutType": "horizon", "direction": "front", "shot": "medium"},
    ])

    assert response.status_code == 202, response.text
    assert seen["payload"] == {
        "mode": "generate",
        "modelId": MODEL_ID,
        "brandUseCategory": CATEGORY,
        "_facemarket": {"modelId": MODEL_ID, "licenseId": LICENSE_ID},
    }
    per_cut = client.app.state.settings.credit_cost_storyboard_per_cut
    assert seen["metadata"]["aiCount"] == 2
    assert seen["credits_reserved"] == seen["reserved"] == 2 * per_cut


def test_detail_real_blocked_sections_only_are_refused_with_the_studio_only_code(
    client, make_token, monkeypatch
):
    """hooking 까지 열린 뒤(2026-09-26) 막힌 섹션은 product 뿐이다 — 제품 컷(전체·디테일)만
    있는 콘티는 만들 컷이 0 이다 — 잡·예약 없이 400."""
    response, seen = _post_real_detail_generate(client, make_token, monkeypatch, [
        {"id": "p1", "source": "ai", "sectionRole": "product", "contentRole": "productOverview",
         "cutType": "product", "direction": "front", "shot": "ghost"},
        {"id": "p2", "source": "ai", "sectionRole": "product", "contentRole": "detail",
         "cutType": "product", "direction": "front", "shot": "detail"},
    ])

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "real_model_studio_only"
    assert seen == {}


def test_detail_mixed_real_selection_queues_one_model_for_every_cut(
    client, make_token, monkeypatch
):
    seen = {}
    client.app.state.settings = replace(
        client.app.state.settings,
        facemarket_enabled=True,
    )

    async def fake_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_analysis(conn, project_id):
        return {
            "selectedModelId": MODEL_ID,
            "stylingModelId": "mA",
            "brandUseCategory": CATEGORY,
        }

    async def fake_storyboard(conn, project_id):
        return [
            {"id": "h1", "source": "ai", "cutType": "horizon"},
            {"id": "s1", "source": "ai", "cutType": "styling"},
        ]

    async def fake_resolve(conn, project, analysis):
        return {"id": LICENSE_ID, "model_id": MODEL_ID}

    def fake_verify(*args, **kwargs):
        return None

    async def fake_editor(conn, project_id):
        return []

    async def fake_lock(conn, project_id, license_id):
        return None

    async def fake_product(conn, project_id):
        return {"clothing_type": "top"}

    async def fake_create(conn, **kwargs):
        seen.update(kwargs)
        return {"id": "job-mixed"}, True

    async def fake_reserve(conn, user_id, amount):
        return 10

    monkeypatch.setattr(routes.repo, "get_project", fake_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(routes.repo, "get_storyboard", fake_storyboard)
    monkeypatch.setattr(routes.facemarket, "resolve_project_license", fake_resolve)
    monkeypatch.setattr(routes.facemarket, "verify_license_local", fake_verify)
    monkeypatch.setattr(routes.facemarket, "set_project_license", fake_lock)
    monkeypatch.setattr(routes.repo, "get_editor_blocks", fake_editor)
    monkeypatch.setattr(routes.repo, "get_product", fake_product)
    monkeypatch.setattr(routes.repo, "create_job", fake_create)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    _patch_counted_route_conn(monkeypatch, [])

    response = client.post(
        "/v1/projects/p1/detail-page:generate",
        headers=auth_headers(make_token),
    )

    assert response.status_code == 202, response.text
    # 저장된 옛 stylingModelId 는 더 이상 읽지 않는다 — 모든 컷이 선택 모델 하나로 간다.
    assert seen["payload"] == {
        "mode": "generate",
        "modelId": MODEL_ID,
        "brandUseCategory": CATEGORY,
        "_facemarket": {"modelId": MODEL_ID, "licenseId": LICENSE_ID},
    }


def test_detail_current_selection_updates_lock_and_queues_snapshot_atomically(
    client, make_token, monkeypatch
):
    events = []
    seen = {}
    client.app.state.settings = replace(
        client.app.state.settings,
        facemarket_enabled=True,
    )

    async def fake_project(conn, user_id, project_id):
        return {"id": project_id, "facemarket_license_id": "revoked-old"}

    async def fake_analysis(conn, project_id):
        return {"selected_model_id": MODEL_ID, "brandUseCategory": CATEGORY}

    async def fake_resolve(conn, project, analysis):
        assert project["facemarket_license_id"] == "revoked-old"
        assert analysis["selected_model_id"] == MODEL_ID
        return {"id": LICENSE_ID, "model_id": MODEL_ID}

    def fake_verify(app, row, **kwargs):
        assert kwargs == {"model_id": MODEL_ID, "brand_use_category": CATEGORY}
        events.append("verified")

    async def fake_lock(conn, project_id, license_id):
        events.append(("lock", license_id))

    async def fake_editor(conn, project_id):
        events.append("cache")
        return []

    async def fake_storyboard(conn, project_id):
        return [{"id": "b1", "source": "ai", "cutType": "horizon"}]

    async def fake_product(conn, project_id):
        return {"clothing_type": "top"}

    async def fake_create(conn, **kwargs):
        seen.update(kwargs)
        events.append("job")
        return {"id": "job-1"}, True

    async def fake_reserve(conn, user_id, amount):
        events.append("reserve")
        return 10

    monkeypatch.setattr(routes.repo, "get_project", fake_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(routes.facemarket, "resolve_project_license", fake_resolve)
    monkeypatch.setattr(routes.facemarket, "verify_license_local", fake_verify)
    monkeypatch.setattr(routes.facemarket, "set_project_license", fake_lock)
    monkeypatch.setattr(routes.repo, "get_editor_blocks", fake_editor)
    monkeypatch.setattr(routes.repo, "get_storyboard", fake_storyboard)
    monkeypatch.setattr(routes.repo, "get_product", fake_product)
    monkeypatch.setattr(routes.repo, "create_job", fake_create)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    _patch_counted_route_conn(monkeypatch, events)

    response = client.post(
        "/v1/projects/p1/detail-page:generate",
        headers=auth_headers(make_token),
    )

    assert response.status_code == 202, response.text
    assert seen["payload"] == {
        "mode": "generate",
        "modelId": MODEL_ID,
        "brandUseCategory": CATEGORY,
        "_facemarket": {"modelId": MODEL_ID, "licenseId": LICENSE_ID},
    }
    assert events == [
        "verified",
        "cache",
        "cutover_lock",
        "cutover_open",
        ("lock", LICENSE_ID),
        "job",
        "reserve",
        "commit",
    ]


def test_detail_denial_precedes_cache_job_and_credit(
    client, make_token, monkeypatch
):
    client.app.state.settings = replace(
        client.app.state.settings,
        facemarket_enabled=True,
    )
    calls = {"cache": 0, "job": 0, "reserve": 0}

    async def fake_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_analysis(conn, project_id):
        return {"selectedModelId": MODEL_ID, "brandUseCategory": CATEGORY}

    async def fake_storyboard(conn, project_id):
        return [{"id": "b1", "source": "ai", "cutType": "horizon"}]

    async def fake_resolve(conn, project, analysis):
        return {"id": LICENSE_ID, "model_id": MODEL_ID}

    def deny(*args, **kwargs):
        raise routes.HTTPException(
            status_code=409,
            detail={"code": "license_revoked", "message": "blocked"},
        )

    async def counted(name, result=None):
        calls[name] += 1
        return result

    monkeypatch.setattr(routes.repo, "get_project", fake_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(routes.repo, "get_storyboard", fake_storyboard)
    monkeypatch.setattr(routes.facemarket, "resolve_project_license", fake_resolve)
    monkeypatch.setattr(routes.facemarket, "verify_license_local", deny)
    monkeypatch.setattr(routes.repo, "get_editor_blocks",
                        lambda *args: counted("cache", []))
    monkeypatch.setattr(routes.repo, "create_job",
                        lambda *args, **kwargs: counted("job"))
    monkeypatch.setattr(routes.repo, "reserve_credits",
                        lambda *args: counted("reserve"))
    patch_route_db(monkeypatch, routes)

    response = client.post(
        "/v1/projects/p1/detail-page:generate",
        headers=auth_headers(make_token),
    )

    assert response.status_code == 409
    assert calls == {"cache": 0, "job": 0, "reserve": 0}


def test_detail_reservation_failure_does_not_commit_new_lock(
    client, make_token, monkeypatch
):
    events = []
    client.app.state.settings = replace(
        client.app.state.settings,
        facemarket_enabled=True,
    )

    async def fake_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_analysis(conn, project_id):
        return {"selectedModelId": MODEL_ID, "brandUseCategory": CATEGORY}

    async def fake_storyboard(conn, project_id):
        return [{"id": "b1", "source": "ai", "cutType": "horizon"}]

    async def fake_resolve(conn, project, analysis):
        return {"id": LICENSE_ID, "model_id": MODEL_ID}

    def fake_verify(*args, **kwargs):
        return None

    async def fake_lock(conn, project_id, license_id):
        events.append("lock")

    async def fake_editor(conn, project_id):
        return []

    async def fake_storyboard(conn, project_id):
        return [{"id": "b1", "source": "ai", "cutType": "horizon"}]

    async def fake_product(conn, project_id):
        return {"clothing_type": "top"}

    async def fake_create(conn, **kwargs):
        return {"id": "job-1"}, True

    async def fake_reserve(conn, user_id, amount):
        return None

    monkeypatch.setattr(routes.repo, "get_project", fake_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(routes.repo, "get_storyboard", fake_storyboard)
    monkeypatch.setattr(routes.facemarket, "resolve_project_license", fake_resolve)
    monkeypatch.setattr(routes.facemarket, "verify_license_local", fake_verify)
    monkeypatch.setattr(routes.facemarket, "set_project_license", fake_lock)
    monkeypatch.setattr(routes.repo, "get_editor_blocks", fake_editor)
    monkeypatch.setattr(routes.repo, "get_storyboard", fake_storyboard)
    monkeypatch.setattr(routes.repo, "get_product", fake_product)
    monkeypatch.setattr(routes.repo, "create_job", fake_create)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    _patch_counted_route_conn(monkeypatch, events)

    response = client.post(
        "/v1/projects/p1/detail-page:generate",
        headers=auth_headers(make_token),
    )

    assert response.status_code == 402
    assert events == ["cutover_lock", "cutover_open", "lock"]


def test_detail_cached_success_commits_verified_lock_immediately_before_return(
    client, make_token, monkeypatch
):
    events = []
    client.app.state.settings = replace(
        client.app.state.settings,
        facemarket_enabled=True,
    )

    async def fake_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_analysis(conn, project_id):
        return {"selectedModelId": MODEL_ID, "brandUseCategory": CATEGORY}

    async def fake_storyboard(conn, project_id):
        return [{"id": "b1", "source": "ai", "cutType": "horizon"}]

    async def fake_resolve(conn, project, analysis):
        return {"id": LICENSE_ID, "model_id": MODEL_ID}

    def fake_verify(*args, **kwargs):
        events.append("verified")

    async def fake_lock(conn, project_id, license_id):
        events.append("lock")

    async def fake_editor(conn, project_id):
        events.append("cache")
        return [{"id": "done"}]

    async def fake_account(conn, user_id):
        events.append("account")
        return {"credits": 5}

    monkeypatch.setattr(routes.repo, "get_project", fake_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(routes.repo, "get_storyboard", fake_storyboard)
    monkeypatch.setattr(routes.facemarket, "resolve_project_license", fake_resolve)
    monkeypatch.setattr(routes.facemarket, "verify_license_local", fake_verify)
    monkeypatch.setattr(routes.facemarket, "set_project_license", fake_lock)
    monkeypatch.setattr(routes.repo, "get_editor_blocks", fake_editor)
    monkeypatch.setattr(routes.repo, "get_account", fake_account)
    _patch_counted_route_conn(monkeypatch, events)

    response = client.post(
        "/v1/projects/p1/detail-page:generate",
        headers=auth_headers(make_token),
    )

    assert response.status_code == 200
    assert events == ["verified", "cache", "account"]


# ---------- 워커 (부분 성공 정산) ----------

class _Conn:
    async def commit(self):  # main 워커가 명시 commit 하므로 스텁에도 필요
        return None


class _FakePool:
    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn()
        return _cm()


class _FakeR2:
    def get_bytes(self, key):
        return b"\x89PNG-bytes"

    def put_bytes(self, key, data, mime, cache=None):
        return None

    def public_url(self, key):
        # 실제 R2.public_url 미러 — cut_done previewUrl 근거(editor_wait_dev_spec §2-1)
        return f"https://r2.test/{key}"

    def preview_url(self, key, expires=3600):
        return f"https://r2.test/{key}"


class _FakeGemini:
    pass


class _RecordingGemini:
    def __init__(self):
        self.calls = 0

    async def generate_content_image(self, model, prompt, images, image_size, *, aspect_ratio=None):
        self.calls += 1
        return types.SimpleNamespace(image=b"IMG", mime="image/png")


def _app(settings):
    st = types.SimpleNamespace(settings=settings, pool=_FakePool(), r2=_FakeR2(), gemini=_FakeGemini())
    return types.SimpleNamespace(state=st)


def _job(reserved=2, per_cut=1):
    # 라우트 예약 규칙 미러: reserved = ai 블록 수 × per_cut, metadata.perCutCost = 예약 시점
    # 단가 스냅샷(워커 정산의 단일 기준 — 실행 시점 설정·콘티 변동과 무관).
    return {"id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "u1:tok",
            "credits_reserved": reserved, "metadata": {"perCutCost": per_cut}}


def _settings(**overrides):
    from conftest import make_settings
    return make_settings(gemini_api_key="x", r2_bucket="b", **overrides)


def test_run_detail_page_job_rejects_bg_example_when_pilot_disabled(monkeypatch):
    captured = {}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return [{
            "id": "bg",
            "source": "ai",
            "exampleId": "ex-bg-1",
            "refScope": "bg",
        }]

    async def fake_failure(conn, **kw):
        captured.update(kw)
        return {"ok": True}

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_failure", fake_failure)

    asyncio.run(dpj.run_detail_page_job(_app(_settings()), _job(reserved=1)))

    assert captured["metadata"] == {"error": "genexample_bg_disabled"}
    assert captured["code"] == "genexample_bg_disabled"
    assert captured["reserved"] == 1


def test_run_detail_page_job_cancellation_finalizes_refund_then_reraises(monkeypatch):
    captured = {}

    async def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError

    async def fake_failure(conn, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(dpj.repo, "get_project", cancelled)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_failure", fake_failure)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(dpj.run_detail_page_job(_app(_settings()), _job(reserved=5)))

    assert captured["reserved"] == 5
    assert captured["code"] == "worker_shutdown"
    assert captured["metadata"] == {"error": "worker_shutdown"}


def test_detail_openai_references_are_normalized_once_per_job(monkeypatch):
    calls = []
    shared = dpj.InlineImage("image/jpeg", b"shared")
    other = dpj.InlineImage("image/webp", b"other")
    exact = dpj.InlineImage("image/jpeg", b"confirmed-exact")

    async def fake_normalize(images, cache=None):
        calls.append(list(images))
        return [dpj.InlineImage("image/png", b"png:" + image.data) for image in images]

    monkeypatch.setattr(dpj, "normalize_openai_images", fake_normalize)
    generic = ({"source": "ai", "cutType": "horizon"}, [shared, shared, other], "", False, [])
    confirmed = ({"source": "ai", "cutType": "styling"}, [exact], "", False, [], None,
                 False, None, object())

    normalized = asyncio.run(dpj._normalize_detail_openai_refs(
        [generic, generic, confirmed], "gpt-image-2"
    ))

    assert calls == [[shared, other]]
    assert [image.data for image in normalized[0][1]] == [b"png:shared", b"png:shared", b"png:other"]
    assert normalized[1][1] == normalized[0][1]
    assert normalized[2][1] == [exact]  # confirmed packet keeps exact MIME/bytes
    # 판정 입력은 정규화 전 원본이다. PNG 는 같은 사진의 ~4.7배라(JPEG 4.3MB → PNG 20MB)
    # 컷마다 도는 독립 QC 가 그 배수만큼 부풀지 않게 원본을 인덱스 10 에 남겨 둔다.
    assert normalized[0][10] == [shared, shared, other]
    # confirmed 컷은 정규화를 건너뛰므로 원본 자리를 만들지 않는다 → images 가 곧 원본.
    assert len(normalized[2]) <= 10


def test_detail_refs_keep_provider_and_qc_inputs_at_stable_indexes(monkeypatch):
    """정규화한 컷은 원본이 인덱스 10, 안 한 컷은 그 자리가 비어 images 로 폴백한다.

    _one_impl 의 `qc_images = item[10] if len(item) > 10 else images` 가 두 경우 모두
    원본 바이트를 고르는 근거다.
    """
    original = dpj.InlineImage("image/jpeg", b"jpeg-original")
    item = ({"source": "ai", "cutType": "horizon"}, [original], "1. X", False,
            [original], None, False, None, None, False)

    async def fake_normalize(images, cache=None):
        return [dpj.InlineImage("image/png", b"png:" + image.data) for image in images]

    monkeypatch.setattr(dpj, "normalize_openai_images", fake_normalize)
    gpt = asyncio.run(dpj._normalize_detail_openai_refs([item], "gpt-image-2"))[0]
    assert [i.data for i in gpt[1]] == [b"png:jpeg-original"]   # 프로바이더가 받는 입력
    assert gpt[10] == [original]                                # 판정기가 받는 입력

    # Gemini 라우트는 JPEG 를 그대로 받으므로 정규화 자체를 하지 않는다 → 폴백 경로.
    gemini = asyncio.run(dpj._normalize_detail_openai_refs([item], "gemini-3-pro-image"))[0]
    assert len(gemini) == 10 and gemini[1] == [original]


def test_run_detail_page_job_reports_space_set_binding_error_without_generation(
    monkeypatch,
):
    captured = {}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return [
            {
                "id": "set-1",
                "source": "ai",
                "sectionRole": "fit",
                "contentRole": "coordination",
                "cutType": "styling",
                "direction": "front",
                "shot": "full",
                "spaceGroupId": "ssg1__missing-set__instance-1",
                "spaceSetMemberOrder": 1,
                "exampleId": "missing-member",
            }
        ]

    async def fake_prod(conn, pid):
        return {"clothingType": "top"}

    async def fake_analysis(conn, pid):
        return {"targetGenders": ["women"]}

    def fake_bind(blocks, *, clothing_type, gender):
        raise dpj.space_set_assets.SpaceSetBindingError(
            "unknown_space_set",
            "저장된 공간 세트를 찾을 수 없어요. 세트를 다시 선택해 주세요.",
        )

    async def fake_failure(conn, **kw):
        captured.update(kw)
        return {"ok": True}

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(
        dpj.space_set_assets, "bind_storyboard_space_sets", fake_bind
    )
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_failure", fake_failure)

    asyncio.run(dpj.run_detail_page_job(_app(_settings()), _job(reserved=1)))

    assert captured["code"] == "unknown_space_set"
    assert captured["metadata"] == {"error": "unknown_space_set"}
    assert captured["message"] == (
        "저장된 공간 세트를 찾을 수 없어요. 세트를 다시 선택해 주세요."
    )


def test_production_space_set_scene_qc_outage_fails_cut_closed(monkeypatch):
    events = []

    async def fake_generate(*_args, **_kwargs):
        return b"generated", "image/png"

    async def unavailable_scene_qc(*_args, **_kwargs):
        raise dpj.VisionError("qc unavailable")

    async def fake_emit(_pool, _job_id, event_type, payload):
        events.append((event_type, payload))

    monkeypatch.setattr(dpj.cut_generator, "generate", fake_generate)
    monkeypatch.setattr(dpj.image_qc, "scene_verdict", unavailable_scene_qc)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    product_image = dpj.InlineImage("image/png", b"product")
    plate = dpj.InlineImage("image/png", b"plate")
    result = asyncio.run(
        dpj._gen_cuts(
            _app(_settings()),
            _job(reserved=1),
            [
                (
                    {"id": "set-cut", "refScope": "pose"},
                    [product_image, plate],
                    "1. PRODUCT\n2. SPACE SET PLATE",
                    False,
                    [product_image],
                    plate,
                    True,
                )
            ],
            {"clothingType": "top"},
            {},
        )
    )

    assert result[:3] == ([], [], 0)
    # 새 이벤트 계약(editor_wait_dev_spec §2-1): 생성 시작(cut_start)과 컷 단위 progress 가
    # 실패 컷에도 둘러싼다 — 대기 화면이 "그리다 실패"를 정직하게 그리는 근거.
    assert events == [
        ("step", {"blockId": "set-cut", "status": "cut_start"}),
        ("step", {"blockId": "set-cut", "status": "cut_failed"}),
        ("progress", {"progress": 80, "phase": "cut", "done": 1, "total": 1}),
    ]


def test_gen_cuts_detail_requires_loaded_detail_manifest(monkeypatch):
    """상품 메타데이터가 아니라 워커가 실제 첨부한 자산으로 게이트한다.

    2026-08-07 개편: 같은 방향 원본이 있으면 구조 확대 모드로 생성하므로,
    게이트가 막는 경우는 '컷 방향 근거(디테일도 원본도)가 전무'할 때다 —
    뒷면 디테일 컷에 앞면 자산만 로드된 상황으로 검증한다."""
    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj, "_emit", fake_emit)
    app = _app(_settings())
    app.state.gemini = _RecordingGemini()
    spec = {"id": "detail-1", "cutType": "product", "shot": "detail", "direction": "back"}
    images = [dpj.InlineImage("image/png", b"front")]
    manifest = dpj.cut_generator.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False, mood_count=0)

    (cut_results, cut_assets, face_cuts, garment_qcs,
     cut_qcs, page_qc, warnings) = asyncio.run(dpj._gen_cuts(
        app, _job(reserved=1), [(spec, images, manifest, False, images)],
        {"name": "니트", "clothingType": "top"}, {},
    ))

    assert app.state.gemini.calls == 0
    assert cut_results == [] and cut_assets == [] and face_cuts == 0
    assert garment_qcs == [] and cut_qcs == [] and page_qc is None and warnings == []


def test_gen_cuts_detail_publishes_only_dedicated_checked_output(monkeypatch):
    async def fake_emit(pool, job_id, et, payload):
        return None

    async def fake_detail(app, job, block, product, analysis):
        assert block["id"] == "detail-1"
        return dpj.InlineImage("image/png", b"QC-PASSED"), {"passed": True}

    monkeypatch.setattr(dpj.detail_shot_runtime, "generate", fake_detail)
    monkeypatch.setattr(dpj, "_emit", fake_emit)
    app = _app(_settings())
    app.state.gemini = _RecordingGemini()
    spec = {"id": "detail-1", "cutType": "product", "shot": "detail"}
    images = [
        dpj.InlineImage("image/png", b"front"),
        dpj.InlineImage("image/png", b"detail"),
    ]
    manifest = dpj.cut_generator.build_manifest(
        [{"slot": "Front"}, {"slot": "Detail"}],
        has_mannequin=False, has_match=False, mood_count=0,
    )

    (cut_results, cut_assets, face_cuts, garment_qcs,
     cut_qcs, page_qc, warnings) = asyncio.run(dpj._gen_cuts(
        app, _job(reserved=1), [(spec, images, manifest, False, images)],
        {"name": "니트", "clothingType": "top"}, {},
    ))

    assert app.state.gemini.calls == 0
    assert len(cut_results) == len(cut_assets) == 1
    assert face_cuts == 0
    assert garment_qcs == [] and page_qc is None and warnings == []
    assert len(cut_qcs) == 1 and cut_qcs[0]["passed"] is True


def test_run_detail_page_job_partial_success(monkeypatch):
    captured = {}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return [{"id": "b1", "source": "ai", "cutType": "styling"},
                {"id": "b2", "source": "ai", "cutType": "product"}]

    async def fake_prod(conn, pid):
        return {"colors": [{"isBase": True, "images": [
            {"slot": "Front", "id": "a1"}, {"slot": "Detail", "id": "a2"},
        ]}]}

    async def fake_analysis(conn, pid):
        return {"suggestedName": "미니멀 코튼 셔츠"}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": "k/a1"}

    call = {"n": 0}

    async def fake_gen(settings, gemini, cut_spec, product, images, *, analysis=None, manifest=None, **_kw):
        call["n"] += 1
        if call["n"] == 1:
            return b"IMG", "image/png"     # b1 성공
        raise RuntimeError("gen fail")      # b2 실패 → 빈 슬롯, 미차감

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        captured["cut_results"] = cut_results
        return [{"id": "b0", "kind": "benefit", "contentRole": "hero", "elements": []}]

    async def fake_finalize(conn, **kw):
        captured.update(kw)
        return {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"))
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=38)))

    assert captured["charge"] == 19              # 성공 컷 1개 × per_cut(19), 실패 컷 미차감
    assert len(captured["cut_assets"]) == 1
    assert len(captured["cut_results"]) == 1     # b1만
    assert captured["product_name"] == "미니멀 코튼 셔츠"  # copywriting OFF도 무호출 작명


def test_run_detail_page_job_retries_transient_cut_failures(monkeypatch):
    """컷 생성은 일시 실패에 한 번 더 시도한다 — 빈 슬롯은 셀러에겐 그냥 못 만든 페이지다.
    단 ValueError(입력 계약 위반)는 다시 불러도 같은 답이라 재시도하지 않는다."""
    captured = {}
    attempts = {"transient": 0, "deterministic": 0}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return [{"id": "b1", "source": "ai", "cutType": "styling"},
                {"id": "b2", "source": "ai", "cutType": "product"}]

    async def fake_prod(conn, pid):
        return {"colors": [{"isBase": True, "images": [
            {"slot": "Front", "id": "a1"}, {"slot": "Detail", "id": "a2"},
        ]}]}

    async def fake_analysis(conn, pid):
        return {"suggestedName": "미니멀 코튼 셔츠"}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": "k/a1"}

    async def fake_gen(settings, gemini, cut_spec, product, images, **_kw):
        if cut_spec.get("id") == "b1":          # 첫 시도 실패 → 재시도에서 성공
            attempts["transient"] += 1
            if attempts["transient"] == 1:
                raise RuntimeError("provider hiccup")
            return b"IMG", "image/png"
        attempts["deterministic"] += 1          # 결정적 실패 → 재시도 없음
        raise ValueError("unknown cutType")

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        captured["cut_results"] = cut_results
        return [{"id": "b0", "kind": "benefit", "contentRole": "hero", "elements": []}]

    async def fake_finalize(conn, **kw):
        captured.update(kw)
        return {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_emit(pool, job_id, et, payload):
        captured.setdefault("steps", []).append(payload.get("status"))

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"))
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=38)))

    assert attempts["transient"] == 2, "일시 실패는 한 번 더 시도한다"
    assert attempts["deterministic"] == 1, "ValueError 는 재시도하지 않는다"
    assert len(captured["cut_results"]) == 1      # 재시도로 살아난 b1만
    assert captured["charge"] == 19                # 실패 컷은 여전히 미차감
    assert captured["steps"].count("cut_failed") == 1


def test_run_detail_page_job_attaches_matching_garment_to_horizon(monkeypatch):
    captured = {"matching_item_ids": [], "loaded_asset_ids": []}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return [{
            "id": "fit-with-match", "source": "ai", "sectionRole": "fit",
            "contentRole": "fit", "cutType": "horizon", "shot": "medium",
            "colorId": "col1", "matchIds": ["match-1", "match-2"],
        }]

    async def fake_prod(conn, pid):
        return {"clothingType": "top", "colors": [{
            "id": "col1", "isBase": True,
            "images": [{"slot": "Front", "id": "product-1"}],
        }]}

    async def fake_analysis(conn, pid):
        return {"fitProfile": {
            "category": "top", "gender": "women",
            "axes": {"fit": "regular", "length": None}, "matchCut": "wide",
        }}

    async def fake_matching_asset(conn, matching_item_id, user_id, project_id):
        assert (user_id, project_id) == ("u1", "p1")
        captured["matching_item_ids"].append(matching_item_id)
        return f"{matching_item_id}-asset"

    async def fake_asset(conn, uid, aid):
        captured["loaded_asset_ids"].append(aid)
        return {"mime_type": "image/png", "r2_key": f"k/{aid}"}

    async def fake_gen(settings, gemini, cut_spec, product, images, *, analysis=None, manifest=None, **_kw):
        captured["cut_spec"] = cut_spec
        captured["image_data"] = [image.data for image in images]
        captured["manifest"] = manifest
        captured["prompt"] = dpj.cut_generator.build_prompt(
            cut_spec, product, analysis=analysis, manifest=manifest)
        return b"IMG", "image/png"

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        return []

    async def fake_finalize(conn, **kw):
        captured.update(kw)
        return {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_emit(pool, job_id, et, payload):
        return None

    class KeyR2:
        def get_bytes(self, key):
            return key.encode()

        def put_bytes(self, key, data, mime, cache=None):
            return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_matching_item_asset", fake_matching_asset)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"), r2=KeyR2())
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    assert captured["matching_item_ids"] == ["match-1", "match-2"]
    assert captured["loaded_asset_ids"] == [
        "product-1", "match-1-asset", "match-2-asset",
    ]
    assert captured["cut_spec"]["cutType"] == "horizon"
    assert captured["image_data"] == [
        b"k/product-1", b"k/match-1-asset", b"k/match-2-asset",
    ]
    assert captured["manifest"].count(
        "MATCHING — the user-selected coordinating garment"
    ) == 2
    assert "- matching bottom" in captured["prompt"]


def test_run_detail_page_job_separates_detail_manifests_by_direction(monkeypatch):
    """앞·뒤 디테일 블록(같은 색)은 각자 방향의 디테일만 첨부한다 — 캐시 키에 방향 포함 검증."""
    captured = {"manifests": {}}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return [
            {"id": "front-detail", "source": "ai", "sectionRole": "product",
             "contentRole": "detail", "cutType": "product", "shot": "detail",
             "direction": "front", "colorId": "base"},
            {"id": "back-detail", "source": "ai", "sectionRole": "product",
             "contentRole": "detail", "cutType": "product", "shot": "detail",
             "direction": "back", "colorId": "base"},
        ]

    async def fake_prod(conn, pid):
        return {"colors": [
            {"id": "base", "name": "레드", "swatchId": "red", "isBase": True, "images": [
                {"slot": "Front", "id": "base-front"},
                {"slot": "Back", "id": "base-back"},
                {"slot": "Detail", "id": "base-detail"},
                {"slot": "BackDetail", "id": "base-backdetail"},
            ]},
        ]}

    async def fake_analysis(conn, pid):
        return {}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": f"k/{aid}"}

    async def fake_gen(settings, gemini, sources, *, target, direction, progress, **kwargs):
        captured["manifests"][direction] = [image.data for image in sources]
        return dpj.InlineImage("image/png", b"IMG"), {"passed": True}


    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        return []

    async def fake_finalize(conn, **kw):
        return {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.detail_shot_runtime, "generate_verified", fake_gen)
    monkeypatch.setattr(dpj.detail_shot_runtime, "ProgressStore", lambda *_args: object())
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"))
    app.state.r2.get_bytes = lambda key: key.encode()
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=2)))

    front_sources = captured["manifests"]["front"]
    back_sources = captured["manifests"]["back"]
    assert b"k/base-detail" in front_sources
    assert b"k/base-backdetail" not in front_sources
    assert b"k/base-backdetail" in back_sources
    assert b"k/base-detail" not in back_sources


def test_run_detail_page_job_fails_cut_when_any_matching_asset_is_missing(monkeypatch):
    captured = {
        "matching_item_ids": [],
        "loaded_asset_ids": [],
        "generate_calls": 0,
    }

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return [{
            "id": "fit-with-missing-match",
            "source": "ai",
            "sectionRole": "fit",
            "contentRole": "fit",
            "cutType": "horizon",
            "shot": "medium",
            "colorId": "col1",
            "matchIds": ["match-1", "match-2"],
        }]

    async def fake_prod(conn, pid):
        return {"clothingType": "top", "colors": [{
            "id": "col1",
            "isBase": True,
            "images": [{"slot": "Front", "id": "product-1"}],
        }]}

    async def fake_analysis(conn, pid):
        return {}

    async def fake_matching_asset(conn, matching_item_id, user_id, project_id):
        assert (user_id, project_id) == ("u1", "p1")
        captured["matching_item_ids"].append(matching_item_id)
        return f"{matching_item_id}-asset"

    async def fake_asset(conn, uid, aid):
        captured["loaded_asset_ids"].append(aid)
        if aid == "match-2-asset":
            return None
        return {"mime_type": "image/png", "r2_key": f"k/{aid}"}

    async def forbidden_generate(*args, **kwargs):
        captured["generate_calls"] += 1
        raise AssertionError("a partially resolved matching outfit must not be generated")

    async def fake_failure(conn, **kwargs):
        captured["failure"] = kwargs
        return {"available": 99}

    async def forbidden_success(conn, **kwargs):
        raise AssertionError("all-cuts-failed path must not finalize success")

    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_matching_item_asset", fake_matching_asset)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "generate", forbidden_generate)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_failure", fake_failure)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", forbidden_success)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"))
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    assert captured["matching_item_ids"] == ["match-1", "match-2"]
    assert captured["loaded_asset_ids"] == [
        "product-1", "match-1-asset", "match-2-asset",
    ]
    assert captured["generate_calls"] == 0
    assert captured["failure"]["code"] == "all_cuts_failed"
    assert captured["failure"]["metadata"] == {
        "error": "all_cuts_failed",
        "requestedCuts": 1,
    }


def test_run_detail_page_job_uses_other_color_detail_and_keeps_normal_color_strict(monkeypatch):
    captured = {"loaded_asset_ids": [], "generated_block_ids": [], "prompts": {}}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return [
            {
                "id": "cross-color-detail", "source": "ai", "sectionRole": "product",
                "contentRole": "detail", "cutType": "product", "shot": "detail",
                "colorId": "green",
            },
            {
                "id": "valid-fit", "source": "ai", "sectionRole": "fit",
                "contentRole": "fit", "cutType": "horizon", "shot": "full",
                "colorId": 0,
            },
            {
                "id": "same-color-detail", "source": "ai", "sectionRole": "product",
                "contentRole": "detail", "cutType": "product", "shot": "detail",
                "colorId": "base",
                "_detailColorTransfer": {"targetName": "잘못 저장된 색상"},
            },
        ]

    async def fake_prod(conn, pid):
        return {"colors": [
            {"id": "base", "name": "레드", "swatchId": "red", "isBase": True, "images": [
                {"slot": "Front", "id": "base-front"},
                {"slot": "Detail", "id": "base-detail"},
            ]},
            {"id": "green", "name": "그린", "swatchId": "green", "images": [
                {"slot": "Front", "id": "green-front"},
            ]},
            {"id": "0", "images": [{"slot": "Front", "id": "zero-front"}]},
        ]}

    async def fake_analysis(conn, pid):
        return {}

    async def fake_asset(conn, uid, aid):
        captured["loaded_asset_ids"].append(aid)
        return {"mime_type": "image/png", "r2_key": f"k/{aid}"}

    async def fake_gen(settings, gemini, cut_spec, product, images, *, analysis=None, manifest=None, **_kw):
        captured["generated_block_ids"].append(cut_spec["id"])
        captured["prompts"][cut_spec["id"]] = dpj.cut_generator.build_prompt(
            cut_spec, product, analysis=analysis, manifest=manifest)
        captured.setdefault("manifests", {})[cut_spec["id"]] = manifest
        return b"IMG", "image/png"

    async def fake_detail(settings, gemini, sources, *, target, direction, progress, **kwargs):
        block_id = "cross-color-detail" if target else "same-color-detail"
        captured["generated_block_ids"].append(block_id)
        captured.setdefault("targets", {})[block_id] = target
        captured.setdefault("sources", {})[block_id] = [image.data for image in sources]
        return dpj.InlineImage("image/png", b"DETAIL"), {"passed": True}

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        captured["cut_results"] = cut_results
        return []

    async def fake_finalize(conn, **kw):
        captured.update(kw)
        return {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.detail_shot_runtime, "generate_verified", fake_detail)
    monkeypatch.setattr(dpj.detail_shot_runtime, "ProgressStore", lambda *_args: object())
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"))
    app.state.r2.get_bytes = lambda key: key.encode()
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=3)))

    assert set(captured["loaded_asset_ids"]) == {
        "zero-front", "green-front", "base-detail", "base-front",
    }
    assert captured["generated_block_ids"] == [
        "valid-fit", "cross-color-detail", "same-color-detail",
    ]
    assert [result["blockId"] for result in captured["cut_results"]] == [
        "valid-fit", "cross-color-detail", "same-color-detail",
    ]
    transfer = captured["targets"]["cross-color-detail"]
    assert transfer["colorTransfer"] is True
    assert transfer["sourceSha256"] == hashlib.sha256(b"k/base-detail").hexdigest()
    assert transfer["colorSourceSha256"] == hashlib.sha256(b"k/green-front").hexdigest()
    assert captured["targets"]["same-color-detail"] is None
    assert captured["sources"]["cross-color-detail"] == [b"k/green-front", b"k/base-detail"]
    assert captured["sources"]["same-color-detail"] == [b"k/base-front", b"k/base-detail"]
    assert captured["manifests"]["valid-fit"] == "1. PRODUCT — front view of the garment"
    assert captured["charge"] == 3


def test_run_detail_page_job_attaches_resolved_examples_with_scoped_manifest(monkeypatch):
    captured = {}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return [
            {"id": "all", "source": "ai", "cutType": "styling",
             "exampleId": "ex_styling_top_full_1", "refScope": "all",
             "pose": "walk"},
            {"id": "pose", "source": "ai", "cutType": "horizon",
             "exampleId": "ex_horizon_top_full_1", "refScope": "pose"},
            {"id": "mismatch", "source": "ai", "cutType": "styling",
             "exampleId": "ex_wrong_clothing", "refScope": "all",
             "pose": "walk"},
            {"id": "unpublished", "source": "ai", "cutType": "horizon",
             "exampleId": "ex_without_bg", "refScope": "bg"},
            {"id": "direction-mismatch", "source": "ai", "cutType": "styling",
             "direction": "front", "exampleId": "ex_back_pose", "refScope": "pose"},
            {"id": "named", "source": "ai", "cutType": "styling", "pose": "walk",
             "exampleId": "ex_styling_top_full_1", "refScope": "pose"},
        ]

    async def fake_prod(conn, pid):
        return {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}

    async def fake_analysis(conn, pid):
        return {}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": "k/a1"}

    async def fake_example(settings, example_id, scope="all", clothing_type=None):
        # scope 전달 검증(2026-07-12 누끼 variant): pose 블록은 pose로, 그 외 all
        assert clothing_type == "top"
        return dpj.InlineImage("image/jpeg", f"EXAMPLE:{example_id}:{scope}".encode())

    def fake_example_status(example_id, clothing_type, scope="all"):
        assert clothing_type == "top"
        if example_id == "ex_wrong_clothing":
            return "not_applicable"
        if example_id == "ex_without_bg":
            return "variant_unpublished"
        return "available"

    def fake_pose_compatible(example_id, normalized):
        return example_id != "ex_back_pose"

    async def fake_gen(settings, gemini, cut_spec, product, images, *, analysis=None, manifest=None):
        captured[cut_spec["id"]] = {
            "cut_spec": cut_spec,
            "images": images,
            "manifest": manifest,
            "prompt": dpj.cut_generator.build_prompt(
                cut_spec, product, analysis=analysis, manifest=manifest),
        }
        return b"IMG", "image/png"

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting):
        return []

    async def fake_finalize(conn, **kw):
        captured["finalize"] = kw
        return {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "example_asset_status", fake_example_status)
    monkeypatch.setattr(dpj.cut_generator, "pose_direction_compatible", fake_pose_compatible)
    monkeypatch.setattr(dpj.cut_generator, "load_example_image", fake_example)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    asyncio.run(dpj.run_detail_page_job(
        _app(_settings(genexample_bg_enabled=True)), _job(reserved=5)))

    for block_id, scope in (("all", "all"), ("pose", "pose")):
        item = captured[block_id]
        assert len(item["images"]) == 2  # PRODUCT 다음에 resolved EXAMPLE 실제 첨부
        assert item["images"][-1].data.startswith(b"EXAMPLE:")
        assert item["images"][-1].data.endswith(f":{scope}".encode())  # scope별 자산(누끼 variant) 선택 검증
    assert "EXAMPLE REFERENCE (scope: all)" in captured["all"]["manifest"]
    assert "POSE CONTROL" in captured["pose"]["manifest"]
    assert "EXAMPLE REFERENCE as art direction" in captured["all"]["prompt"]
    assert "Do not transfer any background, lighting, color grade" in captured["pose"]["prompt"]
    assert "EXAMPLE REFERENCE as art direction" not in captured["pose"]["prompt"]
    assert len(captured["named"]["images"]) == 1
    assert "EXAMPLE REFERENCE" not in captured["named"]["manifest"]
    assert "REFERENCE SCOPE" not in captured["named"]["prompt"]
    for block_id in ("mismatch", "unpublished"):
        assert len(captured[block_id]["images"]) == 1
        assert captured[block_id]["cut_spec"]["exampleId"] is None
        assert "EXAMPLE REFERENCE" not in captured[block_id]["manifest"]
        assert "Composition nuance" not in captured[block_id]["prompt"]
    assert captured["finalize"]["metadata"]["warnings"] == [
        {
            "code": "example_not_applicable", "blockId": "mismatch",
            "exampleId": "ex_wrong_clothing", "clothingType": "top", "refScope": "all",
        },
        {
            "code": "pose_direction_incompatible", "blockId": "direction-mismatch",
            "exampleId": "ex_back_pose", "direction": "front",
        },
        {
            "code": "example_variant_unpublished", "blockId": "unpublished",
            "exampleId": "ex_without_bg", "clothingType": "top", "refScope": "bg",
        },
    ]
    assert "direction-mismatch" not in captured  # preflight에서 빈 슬롯, 생성 호출 0회


def test_run_detail_page_job_attaches_set_plate_and_set_or_flat_pose(monkeypatch):
    captured = {"loads": [], "flatLoads": [], "cuts": {}}
    group_id = "ssg1__set-cafe-01__instance-01"
    storyboard = [
        {
            "id": "set-1",
            "source": "ai",
            "sectionRole": "fit",
            "contentRole": "coordination",
            "cutType": "styling",
            "direction": "front",
            "shot": "full",
            "spaceGroupId": group_id,
            "spaceSetMemberOrder": 1,
            "exampleId": "ss_cafe_01",
            "refScope": "all",
            "pose": "arms crossed",
            "spaceVariation": "fixed",
        },
        {
            "id": "set-2",
            "source": "ai",
            "sectionRole": "fit",
            "contentRole": "coordination",
            "cutType": "styling",
            "direction": "side",
            "shot": "medium",
            "spaceGroupId": group_id,
            "spaceSetMemberOrder": 2,
            "exampleId": "ex-flat-side-medium",
            "refScope": "all",
            "pose": "hands in pockets",
            "spaceVariation": "fixed",
        },
        {
            "id": "dragged-out",
            "source": "ai",
            "sectionRole": "fit",
            "contentRole": "coordination",
            "cutType": "styling",
            "direction": "front",
            "shot": "full",
            "exampleId": "ss_drag_pose",
            "refScope": "pose",
            "pose": "auto",
        },
        {
            "id": "standalone-all",
            "source": "ai",
            "sectionRole": "fit",
            "contentRole": "coordination",
            "cutType": "styling",
            "direction": "side",
            "shot": "full",
            "exampleId": "ss_all_example",
            "refScope": "all",
            "pose": "auto",
            # 서버 전용 필드를 저장 payload가 위조해도 resolver 판정을 덮지 못해야 한다.
            "_referenceDirectionCompatible": True,
        },
        {
            "id": "mine",
            "source": "mine",
            "sectionRole": "product",
        },
    ]
    set_entry = {
        "setId": "set-cafe-01",
        "spaceVariation": "subtle",
        "representativePlate": {"key": "plate"},
        "members": [],
    }
    members = [
        {
            "exampleId": "ss_cafe_01",
            "order": 1,
            "cutType": "styling",
            "direction": "front",
            "shot": "full",
            "pose": {"key": "pose-1"},
        },
        {
            "exampleId": "ss_cafe_02",
            "order": 2,
            "cutType": "styling",
            "direction": "side",
            "shot": "medium",
            "pose": {"key": "pose-2"},
        },
    ]
    set_entry["members"] = members

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return storyboard

    async def fake_prod(conn, pid):
        return {
            "clothingType": "top",
            "colors": [
                {
                    "isBase": True,
                    "images": [{"slot": "Front", "id": "product-front"}],
                }
            ],
        }

    async def fake_analysis(conn, pid):
        return {"targetGenders": ["women"]}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": f"k/{aid}"}

    def fake_bind(blocks, *, clothing_type, gender):
        assert clothing_type == "top" and gender == "women"
        captured["boundBlocks"] = [block["id"] for block in blocks]
        return {
            id(blocks[0]): {
                "groupId": group_id,
                "set": set_entry,
                "poseReference": {
                    "source": "space-set",
                    "exampleId": members[0]["exampleId"],
                    "asset": members[0]["pose"],
                },
            },
            id(blocks[1]): {
                "groupId": group_id,
                "set": set_entry,
                "poseReference": {
                    "source": "flat",
                    "exampleId": "ex-flat-side-medium",
                    "asset": None,
                },
            },
        }

    async def fake_set_image(settings, asset, *, role):
        captured["loads"].append((asset["key"], role))
        return dpj.InlineImage("image/png", asset["key"].encode())

    def fake_resolve_example(block, *, clothing_type, gender, scope):
        assert block["exampleId"] in ("ss_drag_pose", "ss_all_example")
        assert clothing_type == "top" and gender == "women"
        expected_scope = (
            "pose" if block["exampleId"] == "ss_drag_pose" else "all"
        )
        assert scope == expected_scope
        return {
            "source": "space-set",
            "exampleId": block["exampleId"],
            "scope": scope,
            "directionCompatible": scope != "all",
            "asset": {
                "key": "pose-drag" if scope == "pose" else "all-example"
            },
        }

    async def fake_flat_image(
        settings, example_id, scope="all", clothing_type=None
    ):
        captured["flatLoads"].append((example_id, scope, clothing_type))
        return dpj.InlineImage("image/png", b"flat-pose")

    async def fake_gen(
        settings,
        gemini,
        cut_spec,
        product,
        images,
        *,
        analysis=None,
        manifest=None,
        **_kwargs,
    ):
        captured["cuts"][cut_spec["id"]] = {
            "images": [image.data for image in images],
            "manifest": manifest,
            "cutSpec": dict(cut_spec),
        }
        return b"IMG", "image/png"

    async def fake_scene(_settings, plate, generated):
        assert plate.data == b"plate"
        return {"verdict": "pass", "mismatches": [], "correctionPrompt": None}

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        return []

    async def fake_finalize(conn, **kw):
        captured["finalize"] = kw
        return {"editor_blocks": [], "available": 99}

    async def fake_emit(pool, job_id, event_type, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(
        dpj.space_set_assets, "bind_storyboard_space_sets", fake_bind
    )
    monkeypatch.setattr(
        dpj.space_set_assets, "load_space_set_image", fake_set_image
    )
    monkeypatch.setattr(
        dpj.space_set_assets,
        "resolve_published_example_reference",
        fake_resolve_example,
    )
    monkeypatch.setattr(dpj.cut_generator, "load_example_image", fake_flat_image)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.image_qc, "scene_verdict", fake_scene)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    asyncio.run(dpj.run_detail_page_job(_app(_settings()), _job(reserved=4)))

    assert captured["loads"] == [
        ("plate", "대표 배경"),
        ("pose-1", "포즈"),
        ("pose-drag", "포즈"),
        ("all-example", "전체 예시"),
    ]
    assert captured["flatLoads"] == [
        ("ex-flat-side-medium", "pose", "top")
    ]
    assert captured["boundBlocks"] == [
        "set-1",
        "set-2",
        "dragged-out",
        "standalone-all",
        "mine",
    ]
    assert captured["cuts"]["set-1"]["images"][-2:] == [b"plate", b"pose-1"]
    assert captured["cuts"]["set-2"]["images"][-2:] == [b"plate", b"flat-pose"]
    assert captured["cuts"]["dragged-out"]["images"][-1:] == [b"pose-drag"]
    assert captured["cuts"]["standalone-all"]["images"][-1:] == [
        b"all-example"
    ]
    for block_id, item in captured["cuts"].items():
        assert "EXAMPLE REFERENCE (scope: bg)" not in item["manifest"]
        assert item["cutSpec"]["pose"] == "auto"
        expected_scope = "all" if block_id == "standalone-all" else "pose"
        assert item["cutSpec"]["refScope"] == expected_scope
        if expected_scope == "pose":
            assert "POSE CONTROL" in item["manifest"]
    assert "EXAMPLE REFERENCE (scope: all)" in (
        captured["cuts"]["standalone-all"]["manifest"]
    )
    assert "source ONLY of scene, lighting, capture tone" in (
        captured["cuts"]["standalone-all"]["manifest"]
    )
    assert captured["cuts"]["standalone-all"]["cutSpec"][
        "_referenceDirectionCompatible"
    ] is False
    for block_id in ("set-1", "set-2"):
        assert (
            captured["cuts"][block_id]["cutSpec"]["spaceVariation"]
            == "subtle"
        )
    assert "SPACE SET PLATE" in captured["cuts"]["set-1"]["manifest"]
    assert "SPACE SET PLATE" in captured["cuts"]["set-2"]["manifest"]
    assert "SPACE SET PLATE" not in captured["cuts"]["dragged-out"]["manifest"]
    assert captured["finalize"]["charge"] == 4


def test_standalone_space_set_example_is_bound_as_confirmed_service_example():
    source = inspect.getsource(dpj.run_detail_page_job)
    append = "imgs.append(_space_example_cache[cache_key])"
    bind = "service_example_image = _space_example_cache[cache_key]"
    assert append in source and bind in source
    assert source.index(bind, source.index(append)) > source.index(append)


def test_run_detail_page_job_uses_queued_model_without_mutating_storyboard(monkeypatch):
    captured = {}
    storyboard = [
        {"id": "product", "source": "ai", "cutType": "product", "shot": "ghost"},
        {"id": "person", "source": "ai", "cutType": "horizon", "shot": "full"},
    ]

    class TrackingR2:
        def get_bytes(self, key):
            return key.encode()

        def put_bytes(self, key, data, mime, cache=None):
            return None

        def delete(self, key):
            return None

        def public_url(self, key):
            return f"https://r2.test/{key}"

        def preview_url(self, key, expires=3600):
            return f"https://r2.test/{key}"

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False, "selected_mannequin_id": "A-1"}

    async def fake_sb(conn, pid):
        return storyboard

    async def fake_prod(conn, pid):
        return {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}

    async def fake_analysis(conn, pid):
        return {"selectedModelId": "mB"}

    async def fake_cuts(conn, uid, pid):
        return [{"candidate": "A", "version": 1, "asset_id": "man"}]

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": f"k/{aid}"}

    def fake_model_refs(spec, *, require_full_body=False):
        assert require_full_body is True
        if spec["cutType"] == "product":
            return None
        assert require_full_body is True
        return (
            {"key": "seed/models/mB/face_front.webp", "mime": "image/webp"},
            {"key": "seed/models/mB/body_front.png", "mime": "image/jpeg"},
        )

    async def fake_gen(settings, gemini, cut_spec, product, images, *, analysis=None, manifest=None):
        captured[cut_spec["id"]] = {
            "spec": cut_spec, "data": [image.data.decode() for image in images],
            "manifest": manifest,
        }
        return b"IMG", "image/png"

    def fake_assemble(saved_storyboard, cut_results, copy_results, product, copywriting):
        captured["assembled_storyboard"] = saved_storyboard
        return []

    async def fake_finalize(conn, **kw):
        return {"editor_blocks": [], "available": 99}

    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "list_mannequin_cuts", fake_cuts)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "resolve_virtual_model_assets", fake_model_refs)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    app = fake_worker_app(
        make_settings(
            gemini_api_key="x",
            r2_bucket="b",
            facemarket_enabled=True,
        ),
        r2=TrackingR2(),
    )
    asyncio.run(dpj.run_detail_page_job(app, worker_job({
        "mode": "generate",
        "modelId": "mB",
        "brandUseCategory": None,
    }, credits_reserved=2)))

    assert captured["person"]["spec"]["modelId"] == "mB"
    assert captured["person"]["data"] == [
        "k/man", "seed/models/mB/face_front.webp",
        "seed/models/mB/body_front.png", "k/a1",
    ]
    assert captured["person"]["manifest"].splitlines()[0].startswith(
        "1. MANNEQUIN — coarse worn-geometry prior"
    )
    assert captured["person"]["manifest"].splitlines()[1].startswith("2. MODEL FACE —")
    assert captured["person"]["manifest"].splitlines()[2].startswith("3. MODEL FULL BODY —")
    assert captured["person"]["manifest"].splitlines()[3] == "4. PRODUCT — front view of the garment"
    assert captured["product"]["data"] == ["k/a1"]
    assert "mannequin" not in captured["product"]["manifest"].lower()
    assert "MODEL" not in captured["product"]["manifest"]
    assert captured["assembled_storyboard"] is not storyboard
    assert [block["id"] for block in captured["assembled_storyboard"]] == ["person", "product"]
    assert [block["contentRole"] for block in captured["assembled_storyboard"]] == [
        "fit", "productOverview",
    ]
    assert all("modelId" not in block and "model_id" not in block for block in storyboard)


def _run_real_detail_job(monkeypatch, storyboard, *, credits_reserved, failing_ids=()):
    """실제 모델(REAL)을 고른 상세페이지 잡을 끝까지 돌린다 — 라이선스·얼굴·생성기는 전부 가짜.

    돌려주는 captured: cuts(생성기까지 간 컷 id → modelId·참조 키), events([(종류, payload)]),
    settlements, finalize(성공 종결일 때만). failing_ids 의 컷은 생성기에서 실패한다.
    """
    captured = {"cuts": {}, "settlements": [], "events": []}

    class TrackingR2:
        def get_bytes(self, key):
            return key.encode()

        def put_bytes(self, key, data, mime, cache=None):
            return None

        def delete(self, key):
            return None

        def public_url(self, key):
            return f"https://r2.test/{key}"

        def preview_url(self, key, expires=3600):
            return f"https://r2.test/{key}"

    async def fake_project(conn, user_id, project_id):
        return {"copywriting": False}

    async def fake_storyboard(conn, project_id):
        return storyboard

    async def fake_product(conn, project_id):
        return {
            "clothing_type": "top",
            "colors": [{"isBase": True, "images": [{"slot": "Front", "id": "product"}]}],
        }

    async def fake_analysis(conn, project_id):
        return {"targetGenders": ["women"]}

    async def fake_asset(conn, user_id, asset_id):
        return {"mime_type": "image/png", "r2_key": f"public/{asset_id}"}

    async def fake_license(conn, model_id, *, license_id=None, **kwargs):
        assert model_id == MODEL_ID
        assert license_id == LICENSE_ID
        return {
            "id": LICENSE_ID,
            "model_id": MODEL_ID,
            "model_name": "실모델",
            "status": "active",
            "model_status": "verified",
            "current_enrollment_id": "enrollment-1",
            "match_policy_version": "policy-1",
            "unit_price": 5000,
        }

    async def fake_verify(*args, **kwargs):
        return None

    async def fake_real_refs(conn, model_id, **kwargs):
        return [
            {"key": "real/face", "mime": "image/png", "bucket": "face"},
            {"key": "real/grid", "mime": "image/png", "bucket": "face"},
        ]

    def fake_virtual_refs(spec, *, require_full_body=False):
        raise AssertionError("실제 모델을 고른 잡은 가상 참조를 쓰지 않는다")

    async def fake_generate(settings, gemini, cut_spec, product, images, **kwargs):
        captured["cuts"][cut_spec["id"]] = {
            "modelId": cut_spec.get("modelId"),
            "images": [image.data.decode() for image in images],
        }
        if cut_spec["id"] in failing_ids:
            raise ValueError("real cut generation failed")
        return b"IMG", "image/png"

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **kwargs):
        return []

    async def fake_finalize(conn, **kwargs):
        captured["finalize"] = kwargs
        return {"editor_blocks": [], "available": 99}

    async def fake_settlement(*args, **kwargs):
        captured["settlements"].append(kwargs)

    async def fake_emit(*args, **kwargs):
        captured["events"].append((args[-2], args[-1]))

    monkeypatch.setattr(dpj.repo, "get_project", fake_project)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_storyboard)
    monkeypatch.setattr(dpj.repo, "get_product", fake_product)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.facemarket, "resolve_model_license", fake_license)
    monkeypatch.setattr(dpj.facemarket, "verify_license", fake_verify)
    monkeypatch.setattr(dpj.facemarket, "verify_license_local", lambda *args, **kwargs: None)
    monkeypatch.setattr(identity_source, "resolve_real_model_assets", fake_real_refs)
    monkeypatch.setattr(dpj.cut_generator, "resolve_virtual_model_assets", fake_virtual_refs)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_generate)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "lock_facemarket_writer_boundary", lambda conn: asyncio.sleep(0))
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj.facemarket, "record_license_settlement", fake_settlement)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    public_r2 = TrackingR2()
    face_r2 = TrackingR2()
    app = fake_worker_app(
        make_settings(gemini_api_key="x", r2_bucket="b", facemarket_enabled=True),
        r2=public_r2,
    )
    app.state.r2_face = face_r2
    app.state.fm_chain = object()

    asyncio.run(dpj.run_detail_page_job(app, worker_job({
        "mode": "generate",
        "modelId": MODEL_ID,
        "brandUseCategory": CATEGORY,
        "_facemarket": {"modelId": MODEL_ID, "licenseId": LICENSE_ID},
    }, credits_reserved=credits_reserved)))
    return captured


#: 아직 막힌 섹션(product)의 제품 컷 — 실제 모델로는 만들지 않는다(2026-09-26 기준 남은 유일한 섹션).
_REAL_BLOCKED_PRODUCT = {"id": "product", "source": "ai", "sectionRole": "product",
                         "contentRole": "productOverview", "cutType": "product",
                         "shot": "ghost", "direction": "front"}


@pytest.mark.parametrize("real_cuts_fail,hooking_shot,hooking_open", [
    pytest.param(False, "medium", True, id="False"),
    pytest.param(True, "medium", True, id="True"),
    # 회귀(2026-09-25): hero(hooking)와 coordination(styling)의 기본 레시피가 같다
    # (styling/front/full). 건너뛴 컷이 복제 판정(_duplicate_source_indexes)의 원본이 되면
    # 뒤에 오는 같은 레시피의 허용된 스타일링 컷이 그 "복제"로 cut_failed 가 됐다 — 건너뛴
    # 컷은 원본에서 뺀다(_SkippedCut).
    # 2026-09-26 hooking 이 열려 이 조합은 기본 정책으로는 더 생기지 않는다. 남은 막힌 섹션
    # (product)은 제품 컷이라 착용 컷과 레시피가 겹치지 않아 대신 쓸 수 없다. 그래서 이
    # 케이스만 정책 상수를 09-25 값(styling·studio)으로 되돌려 hooking 을 다시 막고 지킨다.
    pytest.param(False, "full", False, id="hooking-same-recipe-as-styling"),
])
def test_run_detail_page_job_puts_the_real_face_in_every_worn_cut_and_settles(
    monkeypatch, real_cuts_fail, hooking_shot, hooking_open,
):
    """실제 모델은 **첫 장면·스타일링·스튜디오 섹션 컷**을 만들고 전부 그 얼굴로 간다.

    이력: 2026-09-11 에 "스튜디오·스타일링·미러 전부 실제 얼굴" 로 바뀌어 가상 대역(mA)이
    사라졌다. 2026-09-14 에 studio 섹션만 남겼다가, 2026-09-25 사용자 결정으로 styling 섹션
    (스타일링·미러)을, 2026-09-26 사용자 결정으로 hooking(첫 장면)을 다시 열었다. product 는
    여전히 아예 만들지 않는다(gpt-image 호출 0, 정산 0).
    """
    from app.agents import identity_scope

    if not hooking_open:
        monkeypatch.setattr(identity_scope, "REAL_ALLOWED_SECTION_ROLES", ("styling", "studio"))
    storyboard = [
        # 첫 장면이 페이지 맨 앞에 온다 — 막혔을 때 뒤의 같은 레시피 스타일링 컷의 "원본"이 될 자리.
        {"id": "hooking", "source": "ai", "sectionRole": "hooking", "contentRole": "hero",
         "cutType": "styling", "shot": hooking_shot, "direction": "front"},
        {"id": "h-front", "source": "ai", "cutType": "horizon", "shot": "full", "direction": "front"},
        {"id": "h-side", "source": "ai", "cutType": "horizon", "shot": "full", "direction": "side"},
        {"id": "styling", "source": "ai", "cutType": "styling", "shot": "full", "direction": "front"},
        {"id": "mirror", "source": "ai", "cutType": "mirror", "shot": "full"},
        dict(_REAL_BLOCKED_PRODUCT),
    ]
    real_face_ids = (
        (("hooking",) if hooking_open else ()) + ("h-front", "h-side", "styling", "mirror")
    )
    skipped_ids = {"product"} | (set() if hooking_open else {"hooking"})

    captured = _run_real_detail_job(
        monkeypatch, storyboard, credits_reserved=len(real_face_ids),
        failing_ids=real_face_ids if real_cuts_fail else (),
    )

    # 열린 섹션의 착용 컷(첫 장면·스튜디오·스타일링·미러)은 전부 실제 얼굴로 생성기에 간다.
    for block_id in real_face_ids:
        assert block_id in captured["cuts"], f"{block_id} 가 생성기에 가지 않았다(복제로 접혔나?)"
        assert captured["cuts"][block_id]["modelId"] == MODEL_ID, block_id
        assert "real/face" in captured["cuts"][block_id]["images"], block_id
    # 막힌 섹션은 생성기까지 가지 않는다 = gpt-image 호출 0.
    assert not skipped_ids & set(captured["cuts"])
    skipped = {payload["blockId"]: payload for event_type, payload in captured["events"]
               if event_type == "step" and payload.get("status") == "cut_skipped"}
    assert set(skipped) == skipped_ids
    assert all(p["reason"] == "real_model_studio_only" for p in skipped.values())
    done_events = {
        payload["blockId"]: payload
        for event_type, payload in captured["events"]
        if event_type == "step" and payload.get("status") == "cut_done"
    }
    if real_cuts_fail:
        assert not any(block_id in done_events for block_id in real_face_ids)
    else:
        for block_id in real_face_ids:
            assert "previewUrl" not in done_events[block_id], block_id
    if not hooking_open:
        # 회귀 고정: 건너뛴 첫 장면과 레시피가 같아도 스타일링 컷은 **직접** 실제 얼굴로 생성된다.
        assert captured["cuts"]["styling"]["modelId"] == MODEL_ID
        assert "styling" in done_events
    # 건너뛴 컷은 완료 이벤트도 자산도 없다 = 정산(성공 컷 수 기준)에서 빠진다.
    assert not skipped_ids & set(done_events)
    if real_cuts_fail:
        # 실제 얼굴 컷이 전부 실패하면 나온 컷이 0 이다 — 성공 종결도 정산도 없다.
        assert "finalize" not in captured
        assert captured["settlements"] == []
        return
    assets = captured["finalize"]["cut_assets"]
    assert len(assets) == len(real_face_ids)
    assert all(a.get("block_id") not in skipped_ids for a in assets)
    for asset in assets:
        assert asset["metadata"]["facemarket_real_derived"] is True
        assert asset["provenance"] == {"license_id": LICENSE_ID, "model_id": MODEL_ID}
    # 정산은 실제 얼굴 컷이 하나라도 나왔을 때 1회(단가는 라이선스 unit_price).
    assert len(captured["settlements"]) == 1
    assert captured["settlements"][0]["total"] == 5000
    assert captured["settlements"][0]["project_id"] == "p1"


def test_a_scope_skipped_cut_closes_its_slot_without_the_garment_truth_warning(
    monkeypatch, caplog,
):
    """범위 밖이라 건너뛴 자리는 cut_skipped(이유) → cut_failed(화면이 자리를 닫는다)로 끝난다.

    "no garment-truth references" 경고로 떨어지면 안 된다 — 옷 근거가 없을 때의 로그라 원인을
    잘못 가리킨다(2026-09-26: 막힌 첫 장면이 3번 연속 그 경고로 보였다).
    """
    caplog.set_level(logging.INFO, logger=dpj.log.name)
    captured = _run_real_detail_job(monkeypatch, [
        {"id": "styling", "source": "ai", "cutType": "styling", "shot": "full",
         "direction": "front"},
        dict(_REAL_BLOCKED_PRODUCT),
    ], credits_reserved=1)

    steps = [(payload.get("status"), payload.get("reason"))
             for event_type, payload in captured["events"]
             if event_type == "step" and payload.get("blockId") == "product"]
    assert steps == [("cut_skipped", "real_model_studio_only"), ("cut_failed", None)]
    assert "product" not in captured["cuts"]
    # 이 로거를 실제로 잡고 있다는 증거(건너뛴 사유 로그) — 아래 부재 확인이 헛돌지 않게.
    assert "AG-06 real_model_studio_only" in caplog.text
    assert "no garment-truth references" not in caplog.text
    # 옆의 허용된 컷은 그대로 실제 얼굴로 나온다.
    assert captured["cuts"]["styling"]["modelId"] == MODEL_ID
    assert len(captured["finalize"]["cut_assets"]) == 1


def test_run_detail_page_job_partial_charge_uses_reservation_time_price(monkeypatch):
    """정산 불변식 회귀: 부분 성공 단가는 실행 시점 설정이 아니라 예약액에서 역산한다.
    예약(단가 1 × 2블록 = 2) 후 배포로 단가가 5로 올라도, 1컷 성공 = 1 차감(5도 2도 아님)."""
    captured = {}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return [{"id": "b1", "source": "ai", "cutType": "styling"},
                {"id": "b2", "source": "ai", "cutType": "product"}]

    async def fake_prod(conn, pid):
        return {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}

    async def fake_analysis(conn, pid):
        return {}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": "k/a1"}

    call = {"n": 0}

    async def fake_gen(settings, gemini, cut_spec, product, images, *, analysis=None, manifest=None, **_kw):
        call["n"] += 1
        if call["n"] == 1:
            return b"IMG", "image/png"
        raise RuntimeError("gen fail")

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        return [{"id": "b0", "kind": "benefit", "contentRole": "hero", "elements": []}]

    async def fake_finalize(conn, **kw):
        captured.update(kw)
        return {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    from conftest import make_settings
    inflated = make_settings(gemini_api_key="x", r2_bucket="b", credit_cost_storyboard_per_cut=5)
    asyncio.run(dpj.run_detail_page_job(_app(inflated), _job(reserved=2)))

    assert captured["charge"] == 1  # 스냅샷 단가(perCutCost=1) × 성공 1컷 — 실행 시점 5는 무시


def test_run_detail_page_job_storyboard_growth_never_settles_zero(monkeypatch):
    """정산 불변식 회귀: 예약(2블록×1) 후 콘티 재저장으로 ai 블록이 3개로 늘어도
    정산은 스냅샷 단가 × 성공 컷(예약액 캡)이다 — 예약액÷현재 블록 수 역산이었다면
    블록 수가 예약액을 넘는 순간 단가 0 → 무과금 생성이 된다."""
    captured = {}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):  # 예약 시점(2블록)보다 커진 콘티
        return [{"id": "b1", "source": "ai", "cutType": "styling"},
                {"id": "b2", "source": "ai", "cutType": "product"},
                {"id": "b3", "source": "ai", "cutType": "horizon"}]

    async def fake_prod(conn, pid):
        return {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}

    async def fake_analysis(conn, pid):
        return {}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": "k/a1"}

    async def fake_gen(settings, gemini, cut_spec, product, images, *, analysis=None, manifest=None, **_kw):
        return b"IMG", "image/png"  # 3컷 전부 성공

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        return [{"id": "b0", "kind": "benefit", "contentRole": "hero", "elements": []}]

    async def fake_finalize(conn, **kw):
        captured.update(kw)
        return {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    asyncio.run(dpj.run_detail_page_job(_app(_settings()), _job(reserved=2)))

    assert captured["charge"] == 2  # min(3컷 × 스냅샷 1, 예약 2) — 0이 아니고 예약 초과도 아님


def test_run_detail_page_job_fails_when_all_ai_blocks_lack_garment_truth(monkeypatch):
    # 옷 근거(상품 사진/마네킹) 없이 무드 레퍼런스만 있는 블록은 생성하지 않는다 — 실패·환불.
    # (무드만 첨부하면 모델이 레퍼런스 속 옷을 베끼거나 지어낸다 — ADR-0004 정확성 최우선)
    captured = {}
    calls = {"n": 0}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return [{"id": "b1", "source": "ai", "cutType": "styling", "refAssetIds": ["ref1"]}]

    async def fake_prod(conn, pid):
        return {"colors": []}   # 상품 사진 없음 + 마네킹 미선택

    async def fake_analysis(conn, pid):
        return {}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": f"k/{aid}"}

    async def fake_gen(settings, gemini, cut_spec, product, images, *, analysis=None, manifest=None, **_kw):
        calls["n"] += 1
        return b"IMG", "image/png"

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        raise AssertionError("all-cuts-failed must stop before assembly")

    async def fake_finalize(conn, **kw):
        raise AssertionError("all-cuts-failed must not finalize success")

    async def fake_failure(conn, **kw):
        captured["failure"] = kw
        return {"status": "failed"}

    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_failure", fake_failure)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"))
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=2)))

    assert calls["n"] == 0                       # 생성 호출 자체가 없다
    assert captured["failure"]["code"] == "all_cuts_failed"
    assert captured["failure"]["metadata"] == {
        "error": "all_cuts_failed", "requestedCuts": 1,
    }


def test_run_detail_page_job_copywriting_qc_failure_keeps_original(monkeypatch):
    # copywriting 경로(_gen_copy) 커버 + AG-03 검수 실패 시 원문 유지(except 커버 — NameError 회귀 방지)
    captured = {}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": True}

    async def fake_sb(conn, pid):
        return [{
            "id": "b1", "source": "ai", "sectionRole": "product", "contentRole": "detail",
            "cutType": "horizon",
        }]

    async def fake_prod(conn, pid):
        return {"colors": [{"isBase": True, "images": [
            {"slot": "Front", "id": "a1"}, {"slot": "Detail", "id": "d1"},
        ]}]}

    async def fake_analysis(conn, pid):
        return {"sellingPoints": ["촉감"], "materials": []}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": "k/a1"}

    async def fake_gen(settings, gemini, cut_spec, product, images, *, analysis=None, manifest=None, **_kw):
        captured["cut_spec"] = cut_spec
        return b"IMG", "image/png"

    async def fake_copy(settings, **kw):
        captured["copy_kwargs"] = kw
        return {
            "texts": [{"role": "body", "text": "원본 카피"}],
            "productName": "골지 데일리 니트",
        }

    async def fake_review(settings, items, confirmed):
        raise RuntimeError("qc down")  # 검수 실패 → 원문 유지 (except 커버)

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        captured["storyboard"] = storyboard
        captured["copy_results"] = copy_results
        captured["assembled_product_name"] = product.get("name")
        return [{"id": "b0", "elements": []}]

    async def fake_finalize(conn, **kw):
        captured.update(kw)
        return {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    async def checked_detail(app, job, spec, product, analysis):
        captured["cut_spec"] = spec
        return dpj.InlineImage("image/png", b"DETAIL-CHECKED"), {"passed": True}

    monkeypatch.setattr(dpj.detail_shot_runtime, "generate", checked_detail)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.copywriter, "generate", fake_copy)
    monkeypatch.setattr(dpj.copy_qc, "review", fake_review)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    asyncio.run(dpj.run_detail_page_job(_app(_settings()), _job(reserved=1)))  # ai 블록 1개 × 단가 1
    assert captured["charge"] == 1
    assert captured["copy_kwargs"]["content_role"] == "detail"
    assert captured["copy_kwargs"]["section_role"] == "product"
    assert "block_kind" not in captured["copy_kwargs"]
    assert captured["copy_kwargs"]["include_product_name"] is True
    assert captured["product_name"] == "골지 데일리 니트"
    assert captured["assembled_product_name"] == "골지 데일리 니트"
    assert captured["cut_spec"]["cutType"] == "product"
    assert captured["cut_spec"]["shot"] == "detail"
    assert captured["storyboard"][0]["sectionRole"] == "product"
    assert captured["storyboard"][0]["cutType"] == "product"
    assert captured["storyboard"][0]["shot"] == "detail"
    assert captured["copy_results"] == [{"blockId": "b1", "texts": [{"role": "body", "text": "원본 카피"}]}]


def test_copywriting_off_uses_analysis_name_without_another_llm_call():
    assert dpj._fallback_product_name(
        {"name": "새 상품", "clothingType": "top"},
        {"suggestedName": "미니멀 코튼 셔츠", "subCategory": "shirt"},
    ) == "미니멀 코튼 셔츠"
    assert dpj._fallback_product_name(
        {"name": "", "clothingType": "outer"}, {"suggestedName": None},
    ) == "데일리 아우터"


def test_detail_finalize_updates_product_name_and_project_title_in_same_transaction(monkeypatch):
    statements = []

    class Cursor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, sql, params=None):
            statements.append((" ".join(sql.split()), params))

        async def fetchone(self):
            return {"id": "j1"}

    class Conn:
        def cursor(self):
            return Cursor()

    async def fake_release(*args, **kwargs):
        return 9

    monkeypatch.setattr(repo, "release_credits", fake_release)
    result = asyncio.run(repo.finalize_detail_page_success(
        Conn(), job_id="j1", lease_token="lease", user_id="u1", project_id="p1",
        editor_blocks=[], cut_assets=[], reserved=0, charge=0, metadata={},
        product_name="골지 데일리 니트",
    ))

    sql = [statement for statement, _params in statements]
    assert any("update products set name = %s where project_id = %s" in s for s in sql)
    assert any("update projects set title = %s where id = %s and user_id = %s" in s for s in sql)
    assert result["available"] == 9


def test_detail_passthrough_ships_the_sellers_original_without_generating(monkeypatch):
    """미세 패턴 상품의 디테일 컷은 셀러 원본을 그대로 싣는다 — 이미지 모델을 부르지 않는다.

    2026-08-01 측정: 줄 하나가 파란 실 2가닥 + 베이지 1가닥인 원단은 4K 에서도 한 주기 14px →
    요소당 2.3px 이라 재현이 불가능하다. 원본이 있는데 다시 그리면 있던 정보를 버리는 셈이다.

    과금은 **생성한 컷 수**(cut_assets)로 매기므로 패스스루는 거기 들어가면 안 된다 —
    호출하지 않은 컷에 크레딧이 붙는다.
    """
    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj, "_emit", fake_emit)
    app = _app(_settings())
    app.state.gemini = _RecordingGemini()
    spec = {"id": "detail-1", "cutType": "product", "shot": "detail"}
    images = [dpj.InlineImage("image/png", b"front")]
    original = {"id": "asset-detail-1", "width": 3000, "height": 4000, "slot": "Detail"}

    (cut_results, cut_assets, face_cuts, garment_qcs,
     cut_qcs, page_qc, warnings) = asyncio.run(dpj._gen_cuts(
        app, _job(reserved=1),
        [(spec, images, "manifest", False, images, None, False, original)],
        {"name": "스트라이프 셔츠", "clothingType": "top"}, {},
    ))

    assert app.state.gemini.calls == 0, "패스스루는 이미지 모델을 부르지 않는다"
    assert cut_results == [{
        "blockId": "detail-1",
        "imageUrl": "/v1/assets/asset-detail-1/file",
        "width": 3000, "height": 4000,
    }]
    assert cut_assets == [], "새 asset 을 만들지 않는다 — 과금 단위에 들어가면 안 된다"
    assert face_cuts == 0 and garment_qcs == [] and cut_qcs == []
    assert page_qc is None and warnings == []


def test_run_detail_page_job_emits_copy_first_then_cut_events(monkeypatch):
    """에디터 대기 계약(editor_wait_dev_spec §2-1) — 이벤트 순서와 페이로드.

    ① copy_ready(검수 통과본)는 모든 컷 이벤트보다 앞: 셀러가 컷을 기다리는 동안
       문구를 다듬는 전제. ② cut_done 은 previewUrl(1h presigned)+width/height 를 싣는다
       (asset 행은 finalize 전이라 /file 은 404 — DB 무변경 원칙). ③ 컷 1개 종결마다
       progress(phase=cut, 20→80)가 나간다 — 체크포인트 정지 화면 방지."""
    events = []

    async def fake_gp(conn, uid, pid):
        return {"copywriting": True}

    async def fake_sb(conn, pid):
        return [
            {"id": "b1", "source": "ai", "sectionRole": "styling",
             "contentRole": "hero", "cutType": "styling"},
            {"id": "b2", "source": "ai", "sectionRole": "product",
             "contentRole": "detail", "cutType": "horizon"},
        ]

    async def fake_prod(conn, pid):
        return {"colors": [{"isBase": True, "images": [
            {"slot": "Front", "id": "a1"}, {"slot": "Detail", "id": "d1"},
        ]}]}

    async def fake_analysis(conn, pid):
        return {"sellingPoints": [], "materials": []}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": f"k/{aid}"}

    async def fake_gen(settings, gemini, cut_spec, product, images, **_kw):
        return b"IMGDATA", "image/png"

    async def fake_copy(settings, **kw):
        return [{"role": "headline" if kw.get("content_role") == "hero" else "body",
                 "text": "카피"}]

    async def fake_review(settings, items, confirmed):
        return []  # 전건 pass

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        return [{"id": "b0", "elements": []}]

    async def fake_finalize(conn, **kw):
        return {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_emit(pool, job_id, et, payload):
        events.append((et, payload))

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    async def checked_detail(app, job, spec, product, analysis):
        return dpj.InlineImage("image/png", b"DETAIL-CHECKED"), {"passed": True}

    monkeypatch.setattr(dpj.detail_shot_runtime, "generate", checked_detail)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.copywriter, "generate", fake_copy)
    monkeypatch.setattr(dpj.copy_qc, "review", fake_review)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    asyncio.run(dpj.run_detail_page_job(_app(_settings()), _job(reserved=2)))

    kinds = [(et, p.get("status") or p.get("phase")) for et, p in events]
    # ① 카피가 컷보다 앞 — copy_ready 전부가 첫 cut_* 이벤트보다 먼저다
    first_cut = next(i for i, k in enumerate(kinds) if str(k[1]).startswith("cut"))
    copy_idx = [i for i, k in enumerate(kinds) if k[1] == "copy_ready"]
    assert copy_idx and max(copy_idx) < first_cut
    assert ("progress", "copy") in kinds
    # ② cut_done 페이로드 — previewUrl(presigned 미러)+치수
    dones = [p for et, p in events if et == "step" and p.get("status") == "cut_done"]
    assert {d["blockId"] for d in dones} == {"b1", "b2"}
    for d in dones:
        assert d["previewUrl"].startswith("https://r2.test/")
        # 치수는 미상일 수 있다(assembler 가 2:3 폴백) — 키 존재만 계약
        assert "width" in d and "height" in d
    # ③ 컷 단위 progress — 2컷이면 50, 80 (20+60×n/2)
    cut_prog = [p["progress"] for et, p in events
                if et == "progress" and p.get("phase") == "cut"]
    assert cut_prog == [50, 80]
    # ④ cut_start 는 컷마다 1회
    starts = [p for et, p in events if et == "step" and p.get("status") == "cut_start"]
    assert {s["blockId"] for s in starts} == {"b1", "b2"}
    # ⑤ 조립 국면 전환 이벤트
    assert ("progress", "assemble") in kinds


def test_assembler_wires_source_block_id_and_copy_role():
    """editor_wait_dev_spec §2-3 — 대기 화면 컷 채움·셀러 카피 오버라이드의 매칭 키."""
    storyboard = [{"id": "sb1", "source": "ai", "sectionRole": "styling",
                   "contentRole": "hero", "cutType": "styling"}]
    cut_results = [{"blockId": "sb1", "imageUrl": "/v1/assets/x/file", "width": 880, "height": 1320}]
    copy_results = [{"blockId": "sb1", "texts": [{"role": "headline", "text": "헤드라인"}]}]
    blocks = dpj.page_assembler.assemble(storyboard, cut_results, copy_results, {}, True)
    hero = blocks[0]
    img = next(e for e in hero["elements"] if e["type"] == "image")
    txt = next(e for e in hero["elements"] if e["type"] == "text")
    assert img["sourceBlockId"] == "sb1"
    assert txt["sourceBlockId"] == "sb1" and txt["copyRole"] == "headline"


def test_job_events_poll_returns_json_with_after_cursor(client, make_token, monkeypatch):
    """?poll=1 — SSE 대신 1회 JSON 폴링(editor_wait_dev_spec §2-2).
    EventSource 는 Bearer 헤더를 못 실으므로 대기 화면은 이 분기로 이벤트를 받는다."""
    async def fake_get_job(conn, uid, jid):
        return {"id": jid, "status": "running"}

    seen = {}

    async def fake_list(conn, uid, jid, after):
        seen["after"] = after
        return [
            {"id": 4, "event_type": "step",
             "payload": {"blockId": "b1", "status": "cut_done",
                         "previewUrl": "https://r2.test/k", "width": 880, "height": 1320}},
            {"id": 5, "event_type": "progress",
             "payload": {"progress": 50, "phase": "cut", "done": 1, "total": 2}},
        ]

    monkeypatch.setattr(routes.repo, "get_job", fake_get_job)
    monkeypatch.setattr(routes.repo, "list_job_events", fake_list)
    connection_leases = 0

    @contextlib.asynccontextmanager
    async def counted_conn(_request):
        nonlocal connection_leases
        connection_leases += 1
        yield object()

    monkeypatch.setattr(routes, "get_conn", counted_conn)
    res = client.get("/v1/jobs/j1/events?poll=1&after=3", headers=auth_headers(make_token))
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/json")
    assert seen["after"] == 3
    body = res.json()
    assert [e["id"] for e in body["events"]] == [4, 5]
    assert body["events"][0]["type"] == "step"
    assert body["events"][0]["payload"]["previewUrl"] == "https://r2.test/k"
    assert connection_leases == 1, "poll 1회가 소유권·이벤트 조회용 DB lease를 중복 획득하면 안 된다"


def test_billable_failure_is_not_retried_by_the_worker(monkeypatch):
    """프로바이더가 이미 그렸을 수 있는 실패는 워커도 다시 보내지 않는다.

    아래층(gemini_image)이 "다시 안 보낸다"고 판단한 실패를 위층 컷 재시도가 그대로
    다시 보내면 같은 컷을 두 번 과금한다 — 그 방어를 코드로 고정한다(2026-08-17 검증).
    """
    import inspect
    from app.workers import detail_page_job

    source = inspect.getsource(detail_page_job)
    assert 'billable = bool(getattr(e, "billable", False))' in source
    assert "if billable or attempt >= max_attempts" in source


def test_gemini_error_carries_the_billable_flag_across_layers():
    """표식이 오류 객체에 실려야 위층이 볼 수 있다."""
    from app.agents.gemini_image import GeminiError

    assert GeminiError("x").billable is False
    assert GeminiError("x", billable=True).billable is True
    # 위층은 getattr 로 읽는다 — 다른 예외 타입이 와도 안전하게 False.
    assert getattr(ValueError("y"), "billable", False) is False
