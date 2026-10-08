import asyncio
import hashlib
import contextlib
import inspect
import json
import logging
import types
from dataclasses import replace
from io import BytesIO

import app.routes as routes
import pytest
from app import repo
from app.agents import identity_scope, identity_source
from app.workers import detail_page_job as dpj
from conftest import auth_headers, fake_worker_app, make_settings, patch_route_db, worker_job


MODEL_ID = "11111111-1111-1111-1111-111111111111"
LICENSE_ID = "22222222-2222-2222-2222-222222222222"
CATEGORY = "상의"


def _solid_png(rgb):
    out = BytesIO()
    dpj.Image.new("RGB", (120, 180), rgb).save(out, format="PNG")
    return out.getvalue()


def _center_band_png(outside, center):
    image = dpj.Image.new("RGB", (120, 180), outside)
    image.paste(center, (0, 72, 120, 108))
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def test_signature_title_color_uses_center_band_luminance_once_for_cut_metadata():
    signature = {"id": "sig", "hookTitleOverlay": True}
    assert dpj._image_metrics(_solid_png((245, 245, 245)), signature) == (
        120, 180, {"hookTitleColor": "#111111"},
    )
    assert dpj._image_metrics(_solid_png((12, 12, 12)), signature) == (
        120, 180, {"hookTitleColor": "#ffffff"},
    )
    assert dpj._image_metrics(_solid_png((245, 245, 245)), {"id": "ordinary"}) == (120, 180, {})
    assert dpj._image_metrics(b"not-an-image", signature) == (None, None, {})
    assert dpj._image_metrics(_center_band_png((12, 12, 12), (245, 245, 245)), signature)[2] == {
        "hookTitleColor": "#111111",
    }
    assert dpj._image_metrics(_center_band_png((245, 245, 245), (12, 12, 12)), signature)[2] == {
        "hookTitleColor": "#ffffff",
    }


@pytest.mark.parametrize("reason", ["detail_qc_failed", "detail_qc_unknown"])
def test_detail_rejection_refunds_entire_page_without_publishing(monkeypatch, reason):
    captured = {"writes": [], "deleted": [], "events": []}

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
            raise dpj.DetailShotRejected(reason, {"passed": False, "attempts": 3})
        return dpj.InlineImage("image/png", b"PASSED-IMAGE"), {"passed": True}

    def assemble(storyboard, cuts, *args, **kwargs):
        raise AssertionError("incomplete page must not be assembled")

    async def finalize(conn, **kwargs):
        raise AssertionError("incomplete page must not be published")

    async def fail(conn, **kwargs):
        captured["failure"] = kwargs
        return True

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
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_failure", fail)
    monkeypatch.setattr(dpj, "_emit", emit)
    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"))
    app.state.r2.put_bytes = lambda key, data, *args, **kwargs: captured["writes"].append(data)
    app.state.r2.delete = lambda key: captured["deleted"].append(key)
    app.state.r2.head = lambda key: None
    asyncio.run(dpj.run_detail_page_job(app, _job(reserved=2, per_cut=1)))

    assert captured["failure"]["code"] == "incomplete_cuts"
    assert captured["failure"]["reserved"] == 2
    assert captured["writes"] == [b"PASSED-IMAGE"]
    assert len(captured["deleted"]) == 1, "환불된 페이지의 임시 미리보기 파일도 지운다"
    done = [payload for event, payload in captured["events"] if payload.get("status") == "cut_done"]
    assert [payload["blockId"] for payload in done] == ["accepted"]
    failed = [payload for event, payload in captured["events"] if payload.get("status") == "cut_failed"]
    assert any(payload["blockId"] == "rejected" and payload.get("reason") == reason for payload in failed)


def test_best_effort_detail_fills_page_and_uses_five_cut_minimum(monkeypatch):
    captured = {}

    async def project(*args):
        return {"copywriting": False}

    async def storyboard(*args):
        return [{"id": block_id, "source": "ai", "sectionRole": "product", "contentRole": "detail",
                 "cutType": "product", "shot": "detail", "direction": direction, "colorId": "base"}
                for block_id, direction in [("best-effort", "front"), ("passed", "back")]]

    async def product(*args):
        return {"colors": [{"id": "base", "isBase": True, "images": [
            {"slot": "Front", "id": "front"}, {"slot": "Back", "id": "back"}]}]}

    async def asset(conn, user_id, asset_id):
        return {"mime_type": "image/png", "r2_key": f"k/{asset_id}"}

    async def detail(app, job, spec, product, analysis):
        if spec["id"] == "best-effort":
            return dpj.InlineImage("image/png", b"GENERATED-BEST"), {
                "passed": False, "decision": "FAIL", "publishedBestEffort": True,
                "selectedAttempt": 2, "judgments": [{"decision": "FAIL"}] * 2,
            }
        return dpj.InlineImage("image/png", b"GENERATED-PASS"), {"passed": True, "decision": "PASS"}

    async def finalize(conn, **kwargs):
        captured.update(kwargs)
        return {"editor_blocks": kwargs["editor_blocks"], "available": 95}

    async def failure(conn, **kwargs):
        raise AssertionError("two generated cuts must not take the refund path")

    async def emit(*args, **kwargs):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", project)
    monkeypatch.setattr(dpj.repo, "get_storyboard", storyboard)
    monkeypatch.setattr(dpj.repo, "get_product", product)
    monkeypatch.setattr(dpj.repo, "get_analysis", lambda *args: asyncio.sleep(0, result={}))
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", asset)
    monkeypatch.setattr(dpj.detail_shot_runtime, "generate", detail)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", finalize)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_failure", failure)
    monkeypatch.setattr(dpj, "_emit", emit)
    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"))
    job = _job(reserved=5, per_cut=1)
    job["metadata"].update(aiCount=2, minCuts=5)

    asyncio.run(dpj.run_detail_page_job(app, job))

    assert captured["charge"] == 5
    assert len(captured["cut_assets"]) == 2
    assert {cut["blockId"] for cut in captured["metadata"]["cutQc"]} == {"best-effort", "passed"}
    assert any(cut.get("publishedBestEffort") is True for cut in captured["metadata"]["cutQc"])


@pytest.mark.parametrize("interruption", ["cancelled", "exception"])
def test_interrupted_page_revokes_preview_stored_before_cut_gather_returns(monkeypatch, interruption):
    captured = {"deleted": []}

    async def project(*args):
        return {"copywriting": False}

    async def storyboard(*args):
        return [{"id": "detail", "source": "ai", "sectionRole": "product",
                 "contentRole": "detail", "cutType": "product", "shot": "detail",
                 "direction": "front", "colorId": "base"}]

    async def product(*args):
        return {"colors": [{"id": "base", "isBase": True, "images": [{"slot": "Front", "id": "front"}]}]}

    async def asset(*args):
        return {"mime_type": "image/png", "r2_key": "k/front"}

    async def interrupted(*args, stored_outputs=None, **kwargs):
        assert stored_outputs is not None
        stored_outputs.append({"key": "k/completed-cut", "cleanup_intent_id": None})
        if interruption == "cancelled":
            raise asyncio.CancelledError
        raise RuntimeError("sibling failed before gather returned")

    async def fail(conn, **kwargs):
        captured["failure"] = kwargs
        return True

    monkeypatch.setattr(dpj.repo, "get_project", project)
    monkeypatch.setattr(dpj.repo, "get_storyboard", storyboard)
    monkeypatch.setattr(dpj.repo, "get_product", product)
    monkeypatch.setattr(dpj.repo, "get_analysis", lambda *args: asyncio.sleep(0, result={}))
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", asset)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_failure", fail)
    monkeypatch.setattr(dpj, "_gen_cuts", interrupted)
    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"))
    app.state.r2.delete = lambda key: captured["deleted"].append(key)
    app.state.r2.head = lambda key: None

    if interruption == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(dpj.run_detail_page_job(app, _job(reserved=1, per_cut=1)))
    else:
        asyncio.run(dpj.run_detail_page_job(app, _job(reserved=1, per_cut=1)))

    assert captured["failure"]["reserved"] == 1
    assert captured["deleted"] == ["k/completed-cut"]


@pytest.mark.parametrize("cancel_point", ["commit", "post_commit"])
def test_success_commit_uncertainty_never_deletes_published_cut(monkeypatch, cancel_point):
    captured = {"deleted": [], "finalized": False, "commit_fault_delivered": False}

    async def project(*args):
        return {"copywriting": False}

    async def storyboard(*args):
        return [{"id": "detail", "source": "ai", "sectionRole": "product",
                 "contentRole": "detail", "cutType": "product", "shot": "detail",
                 "direction": "front", "colorId": "base"}]

    async def product(*args):
        return {"colors": [{"id": "base", "isBase": True, "images": [{"slot": "Front", "id": "front"}]}]}

    async def generated(*args, stored_outputs=None, **kwargs):
        stored_outputs.append({"key": "k/published", "cleanup_intent_id": None})
        return ([{"blockId": "detail", "imageUrl": "/v1/assets/a1/file", "width": 880, "height": 1320}],
                [{"asset_id": "a1", "bucket": "b", "key": "k/published", "mime": "image/png"}],
                0, [], [], None, [])

    async def finalize(*args, **kwargs):
        captured["finalized"] = True
        return {"editor_blocks": []}

    async def fail(*args, **kwargs):
        captured["failure_attempted"] = True
        return False  # 이미 성공 커밋이면 실패 종결은 lease 펜스가 거부한다.

    async def record(*args, **kwargs):
        if cancel_point == "post_commit":
            raise asyncio.CancelledError

    class CommitConn:
        async def commit(self):
            if cancel_point == "commit" and captured["finalized"] and not captured["commit_fault_delivered"]:
                captured["commit_fault_delivered"] = True
                raise asyncio.CancelledError  # DB 커밋은 이뤄졌으나 응답이 끊긴 상황

    class CommitPool:
        @contextlib.asynccontextmanager
        async def connection(self):
            yield CommitConn()

    monkeypatch.setattr(dpj.repo, "get_project", project)
    monkeypatch.setattr(dpj.repo, "get_storyboard", storyboard)
    monkeypatch.setattr(dpj.repo, "get_product", product)
    monkeypatch.setattr(dpj.repo, "get_analysis", lambda *args: asyncio.sleep(0, result={}))
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", lambda *args: asyncio.sleep(0, result={
        "mime_type": "image/png", "r2_key": "k/front"}))
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", finalize)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_failure", fail)
    monkeypatch.setattr(dpj, "_gen_cuts", generated)
    monkeypatch.setattr(dpj.page_assembler, "assemble", lambda *args, **kwargs: [])
    monkeypatch.setattr(dpj.fm_fingerprint_store, "record_cut_fingerprints", record)
    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"))
    app.state.pool = CommitPool()
    app.state.r2.delete = lambda key: captured["deleted"].append(key)
    app.state.r2.head = lambda key: None

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(dpj.run_detail_page_job(app, _job(reserved=1, per_cut=1)))

    assert captured["finalized"] is True
    assert captured["deleted"] == [], "성공 커밋 중·직후 중단은 과금된 자산을 삭제하지 않는다"


# ---------- 라우트 ----------

def test_detail_minimum_cut_setting_cannot_drift_from_frontend(monkeypatch):
    from app.config import load_settings

    monkeypatch.delenv("CREDIT_MIN_STORYBOARD_CUTS", raising=False)
    assert load_settings().credit_min_storyboard_cuts == 5
    monkeypatch.setenv("CREDIT_MIN_STORYBOARD_CUTS", "6")
    assert load_settings().credit_min_storyboard_cuts == 5


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
    assert seen["credits_reserved"] == 95  # AI 2컷도 최소 5컷 기준
    assert seen["reserved"] == 95
    # 예약 시점 단가 스냅샷 — 워커 정산의 단일 기준(정산 불변식)
    assert seen["metadata"]["perCutCost"] == 19
    assert seen["metadata"]["creditCostVersion"] == "v6"
    assert seen["metadata"]["aiCount"] == 2
    assert seen["metadata"]["minCuts"] == 5
    assert seen["metadata"]["storyboardDigest"] == hashlib.sha256(json.dumps(
        [{"id": "b1", "source": "ai"}, {"id": "b2", "source": "ai"},
         {"id": "m", "source": "mine"}],
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode()).hexdigest()
    assert seen["prewarmed"] == 1


@pytest.mark.parametrize("ai_count,expected_cost", [(0, 0), (3, 95), (7, 133)])
def test_detail_reservation_uses_minimum_only_for_positive_ai_count(
    client, make_token, monkeypatch, ai_count, expected_cost,
):
    seen = {}

    async def fake_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_analysis(conn, project_id):
        return {}

    async def fake_editor(conn, project_id):
        return []

    async def fake_storyboard(conn, project_id):
        return [{"id": f"b{i}", "source": "ai", "cutType": "product",
                 "shot": "ghost", "direction": "front", "colorId": f"color-{i}"}
                for i in range(ai_count)] + [{"id": "mine", "source": "mine"}]

    async def fake_product(conn, project_id):
        return {"clothing_type": "top"}

    async def fake_create(conn, **kwargs):
        seen.update(kwargs)
        return {"id": "j1"}, True

    async def fake_reserve(conn, user_id, cost):
        seen["reserved"] = cost
        return 100

    monkeypatch.setattr(routes.repo, "get_project", fake_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(routes.repo, "get_editor_blocks", fake_editor)
    monkeypatch.setattr(routes.repo, "get_storyboard", fake_storyboard)
    monkeypatch.setattr(routes.repo, "get_product", fake_product)
    monkeypatch.setattr(routes.repo, "create_job", fake_create)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    patch_route_db(monkeypatch, routes)

    response = client.post("/v1/projects/p1/detail-page:generate", headers=auth_headers(make_token))
    assert response.status_code == 202, response.text
    assert seen["credits_reserved"] == expected_cost
    assert seen["metadata"]["aiCount"] == ai_count
    assert seen["metadata"]["minCuts"] == 5
    if ai_count == 0:
        assert "reserved" not in seen
    else:
        assert seen["reserved"] == expected_cost


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

    # 제품 컷은 2026-09-28 에 실제 모델에게도 열렸다 — 사람이 안 나오는 컷이라 모델 종류와
    # 상관이 없다. 제품 컷만 있는 콘티는 잡이 만들어지되, 얼굴을 쓰는 컷이 없으니 실제 모델
    # id·라이선스(_facemarket)가 페이로드에서 빠지고 FaceMarket 게이트에도 들어가지 않는다
    # (forbidden_resolve 가 불리면 AssertionError, 컷오버 잠금도 없음).
    assert response.status_code == 202, response.text
    assert seen["payload"] == {"mode": "generate"}
    assert events == ["cache", "job", "reserve", "commit"]


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
    섹션을 다시 열었다(호리존만으로는 상세페이지가 모자라다). 2026-09-26 hooking 도 열렸다.
    모델 범위 밖 컷은 별도 테스트에서 시작 전 거절한다.
    """
    response, seen = _post_real_detail_generate(client, make_token, monkeypatch, [
        {"id": "s1", "source": "ai", "cutType": "styling"},
    ])

    assert response.status_code == 202, response.text
    # 스타일링 컷이 실제 모델 하나로 간다 — 가상 대역(stylingModelId) 없이 라이선스가 붙는다.
    assert seen["payload"] == {
        "mode": "generate",
        "modelId": MODEL_ID,
        "brandUseCategory": CATEGORY,
        "_facemarket": {"modelId": MODEL_ID, "licenseId": LICENSE_ID},
    }
    # 예약 = 실제로 만들 스타일링 1컷의 최소 요금.
    per_cut = client.app.state.settings.credit_cost_storyboard_per_cut
    assert seen["metadata"]["aiCount"] == 1
    assert seen["credits_reserved"] == 5 * per_cut
    assert seen["reserved"] == 5 * per_cut


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
    assert seen["credits_reserved"] == seen["reserved"] == 5 * per_cut


def test_detail_real_blocked_sections_only_are_refused_with_the_studio_only_code(
    client, make_token, monkeypatch
):
    """막힌 섹션의 컷만 있는 콘티는 만들 컷이 0 이다 — 잡·예약 없이 400.

    2026-09-28 product 까지 열려 지금 막힌 섹션은 없다. 장치(라우트 가드)는 남아 있으니
    정책을 좁혀(product 를 뺀다) 거절 경로를 계속 지킨다.
    """
    monkeypatch.setattr(identity_scope, "REAL_ALLOWED_SECTION_ROLES",
                        ("hooking", "styling", "studio"))
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


def test_horizon_openai_refs_are_normalized_when_other_detail_cuts_use_gemini(monkeypatch):
    horizon_image = dpj.InlineImage("image/jpeg", b"horizon-jpeg")
    styling_image = dpj.InlineImage("image/jpeg", b"styling-jpeg")
    horizon = ({"source": "ai", "cutType": "horizon"}, [horizon_image], "", False, [])
    styling = ({"source": "ai", "cutType": "styling"}, [styling_image], "", False, [])

    async def fake_normalize(images, cache=None):
        return [dpj.InlineImage("image/png", b"png:" + image.data) for image in images]

    monkeypatch.setattr(dpj, "normalize_openai_images", fake_normalize)
    normalized = asyncio.run(dpj._normalize_detail_openai_refs(
        [horizon, styling], "gemini-3-pro-image", "gpt-image-2.5-sunburst"
    ))
    assert [image.data for image in normalized[0][1]] == [b"png:horizon-jpeg"]
    assert normalized[0][10] == [horizon_image]
    assert normalized[1][1] == [styling_image]
    assert len(normalized[1]) <= 10


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


@pytest.mark.parametrize("qc_report", [
    {"passed": True, "decision": "PASS"},
    {"passed": False, "decision": "FAIL", "publishedBestEffort": True,
     "selectedAttempt": 2, "judgments": [{"decision": "FAIL"}] * 3},
])
def test_gen_cuts_detail_publishes_selected_generated_output_once(monkeypatch, qc_report):
    async def fake_emit(pool, job_id, et, payload):
        return None

    async def fake_detail(app, job, block, product, analysis):
        assert block["id"] == "detail-1"
        return dpj.InlineImage("image/png", b"SELECTED-GENERATED"), qc_report

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
    assert cut_qcs == [{"blockId": "detail-1", **qc_report}]


def test_run_detail_page_job_partial_failure_refunds_without_publishing(monkeypatch):
    captured = {}
    from app.agents.gemini_image import GeminiError

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

    call = {"b2": 0}

    async def fake_gen(settings, gemini, cut_spec, product, images, *, analysis=None, manifest=None, **_kw):
        if cut_spec["id"] == "b1":
            return b"IMG", "image/png"
        call["b2"] += 1
        raise GeminiError("temporary rejection", transient=True)

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        raise AssertionError("incomplete page must not be assembled")

    async def fake_finalize(conn, **kw):
        raise AssertionError("incomplete page must not be published")

    async def fake_failure(conn, **kw):
        captured["failure"] = kw
        return True

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
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=38)))

    assert call["b2"] == 3
    assert captured["failure"]["reserved"] == 38
    assert captured["failure"]["code"] == "incomplete_cuts"
    assert captured["failure"]["metadata"] == {
        "error": "incomplete_cuts", "requestedCuts": 2,
        "generatedCuts": 1, "failedBlockIds": ["b2"],
    }
    assert "cut_results" not in captured


@pytest.mark.parametrize("requested,completed,legacy,expected_charge", [
    (3, 3, False, 95),
    (7, 7, False, 133),
    (3, 3, True, 57),
])
def test_detail_success_settles_from_requested_minimum_snapshot(
    monkeypatch, requested, completed, legacy, expected_charge,
):
    captured = {}

    async def fake_project(conn, user_id, project_id):
        return {"copywriting": False}

    async def fake_storyboard(conn, project_id):
        return [{"id": f"b{i}", "source": "ai", "cutType": "product",
                 "shot": "ghost", "direction": "front", "colorId": f"color-{i}"}
                for i in range(requested)]

    async def fake_product(conn, project_id):
        return {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}

    async def fake_analysis(conn, project_id):
        return {}

    async def fake_asset(conn, user_id, asset_id):
        return {"mime_type": "image/png", "r2_key": "k/a1"}

    async def fake_cuts(app, job, prepared, product, analysis, **kwargs):
        return ([{"blockId": f"b{i}"} for i in range(completed)],
                [{"metadata": {}} for _ in range(completed)], 0, [], [], None, [])

    def fake_assemble(*args, **kwargs):
        return []

    async def fake_finalize(conn, **kwargs):
        captured.update(kwargs)
        return {"editor_blocks": [], "available": 99}

    async def fake_emit(pool, job_id, event, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_project)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_storyboard)
    monkeypatch.setattr(dpj.repo, "get_product", fake_product)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj, "_gen_cuts", fake_cuts)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    reserved = (requested if legacy or requested >= 5 else 5) * 19
    job = _job(reserved=reserved, per_cut=19)
    job["metadata"]["aiCount"] = requested
    if not legacy:
        job["metadata"]["minCuts"] = 5
    asyncio.run(dpj.run_detail_page_job(_app(_settings()), job))

    assert len(captured["cut_assets"]) == completed
    assert captured["reserved"] == reserved
    assert captured["charge"] == expected_charge


def test_run_detail_page_job_retries_transient_cut_failures_twice(monkeypatch):
    """첫 생성이 두 번 실패해도 세 번째에 완성되면 전체 페이지를 내보낸다."""
    from app.agents.gemini_image import GeminiError
    captured = {}
    attempts = {"transient": 0}

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
        if cut_spec.get("id") == "b1":
            attempts["transient"] += 1
            if attempts["transient"] < 3:
                raise GeminiError("provider hiccup", transient=True)
            return b"IMG", "image/png"
        return b"IMG", "image/png"

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

    assert attempts["transient"] == 3
    assert len(captured["cut_results"]) == 2
    assert captured["charge"] == 38
    assert captured["steps"].count("cut_failed") == 0


def test_confirmed_profile_cut_also_gets_two_safe_retries(monkeypatch):
    from app.agents.gemini_image import GeminiError
    attempts = 0

    async def fake_generate(settings, gemini, cut_spec, product, images, **kwargs):
        nonlocal attempts
        attempts += 1
        assert kwargs["confirmed_prompt_input"] == "confirmed-input"
        if attempts < 3:
            raise GeminiError("temporary provider failure", transient=True)
        return b"IMG", "image/png"

    async def fake_emit(pool, job_id, event, payload):
        return None

    monkeypatch.setattr(dpj.cut_generator, "generate", fake_generate)
    monkeypatch.setattr(dpj, "_emit", fake_emit)
    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b",
                                        detail_cut_max_attempts=3))
    spec = {"id": "confirmed", "source": "ai", "cutType": "horizon", "shot": "full"}
    image = dpj.InlineImage("image/png", b"product")
    packet = types.SimpleNamespace(prompt_input="confirmed-input")
    prepared = [(spec, [image], "manifest", False, [image], None, False,
                 None, packet, False)]

    results, assets, *_ = asyncio.run(dpj._gen_cuts(
        app, _job(reserved=19), prepared, {"clothing_type": "top"}, {}))

    assert attempts == 3
    assert [result["blockId"] for result in results] == ["confirmed"]
    assert len(assets) == 1


@pytest.mark.parametrize("desquiggle_mode", ["off", "on"])
def test_run_detail_page_job_attaches_matching_garment_to_horizon(monkeypatch, desquiggle_mode):
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

    async def fake_matching_metadata(conn, matching_item_id, user_id, project_id):
        assert desquiggle_mode == "on", "off must not add metadata queries"
        return {"name": "스트라이프 팬츠"} if matching_item_id == "match-2" else None

    async def fake_asset(conn, uid, aid):
        captured["loaded_asset_ids"].append(aid)
        return {"mime_type": "image/png", "r2_key": f"k/{aid}"}

    async def fake_gen(settings, gemini, cut_spec, product, images, *, analysis=None, manifest=None, **_kw):
        captured["cut_spec"] = cut_spec
        captured["protection"] = _kw.get("garment_protection")
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
    monkeypatch.setattr(dpj.repo, "get_matching_item_metadata", fake_matching_metadata)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b",
                                       garment_desquiggle_mode=desquiggle_mode), r2=KeyR2())
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
    if desquiggle_mode == "on":
        assert len(captured["protection"].matching) == 2
        assert captured["protection"].matching[0].has_pattern is None
        assert captured["protection"].matching[1].has_pattern is True
    else:
        assert captured["protection"] is None


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
        raise AssertionError("incomplete page must not be published")

    async def fake_failure(conn, **kw):
        captured["failure"] = kw
        return True

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
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_failure", fake_failure)
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
    assert captured["failure"]["code"] == "incomplete_cuts"
    assert captured["failure"]["metadata"]["failedBlockIds"] == ["direction-mismatch"]
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

    async def fake_failure(conn, **kwargs):
        captured["failure"] = kwargs
        return True

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
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_failure", fake_failure)
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


#: 정책을 좁혀(product 를 뺀) 막은 제품 컷. 2026-09-28 product 가 열려 기본 정책으로는 막힌 섹션이
#: 없다 — 건너뛰기 장치를 지키는 테스트는 _block_product_for_real 로 정책을 좁혀 쓴다.
_REAL_BLOCKED_PRODUCT = {"id": "product", "source": "ai", "sectionRole": "product",
                         "contentRole": "productOverview", "cutType": "product",
                         "shot": "ghost", "direction": "front"}


def _block_product_for_real(monkeypatch):
    monkeypatch.setattr(identity_scope, "REAL_ALLOWED_SECTION_ROLES",
                        ("hooking", "styling", "studio"))


@pytest.mark.parametrize("real_cuts_fail,hooking_shot,hooking_open", [
    pytest.param(False, "medium", True, id="False"),
    pytest.param(True, "medium", True, id="True"),
    # 회귀(2026-09-25): hero(hooking)와 coordination(styling)의 기본 레시피가 같다
    # (styling/front/full). 건너뛴 컷이 복제 판정(_duplicate_source_indexes)의 원본이 되면
    # 뒤에 오는 같은 레시피의 허용된 스타일링 컷이 그 "복제"로 cut_failed 가 됐다 — 건너뛴
    # 컷은 원본에서 뺀다(_SkippedCut).
    # 2026-09-26 hooking 이 열려 이 조합은 기본 정책으로는 더 생기지 않는다. 이
    # 케이스만 정책 상수를 09-25 값(styling·studio)으로 되돌려 hooking 을 다시 막고 지킨다.
    pytest.param(False, "full", False, id="hooking-same-recipe-as-styling"),
])
def test_run_detail_page_job_puts_the_real_face_in_every_worn_cut_and_settles(
    monkeypatch, real_cuts_fail, hooking_shot, hooking_open,
):
    """실제 모델은 **첫 장면·스타일링·스튜디오 섹션 컷**을 만들고 전부 그 얼굴로 간다.

    이력: 2026-09-11 에 "스튜디오·스타일링·미러 전부 실제 얼굴" 로 바뀌어 가상 대역(mA)이
    사라졌다. 2026-09-14 에 studio 섹션만 남겼다가, 2026-09-25 사용자 결정으로 styling 섹션
    (스타일링·미러)을, 2026-09-26 사용자 결정으로 hooking(첫 장면)을 다시 열었다.
    이 테스트는 허용된 컷만 담고, 범위 밖 제품 컷의 실패는 아래에서 따로 확인한다.
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
    ]
    real_face_ids = (
        (("hooking",) if hooking_open else ()) + ("h-front", "h-side", "styling", "mirror")
    )
    skipped_ids = set() if hooking_open else {"hooking"}

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
    # 범위 밖 컷은 완료 이벤트가 없고 작업 전체를 실패시킨다.
    assert not skipped_ids & set(done_events)
    if real_cuts_fail or skipped_ids:
        # 전체 AI 컷 중 하나라도 미완성이면 출고·과금·모델 정산이 없다.
        assert "finalize" not in captured
        assert captured["settlements"] == []
        assert captured["failure"]["code"] == (
            "all_cuts_failed" if real_cuts_fail else "incomplete_cuts")
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
    _block_product_for_real(monkeypatch)
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
    assert "finalize" not in captured
    assert captured["failure"]["code"] == "incomplete_cuts"
    assert captured["failure"]["reserved"] == 1
    assert captured["settlements"] == []


def test_run_detail_page_job_full_charge_uses_reservation_time_price(monkeypatch):
    """예약 후 배포로 단가가 올라도 완성 페이지에는 예약 시점 단가를 쓴다."""
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

    async def fake_gen(settings, gemini, cut_spec, product, images, *, analysis=None, manifest=None, **_kw):
        return b"IMG", "image/png"

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

    assert captured["charge"] == 2  # 스냅샷 단가(perCutCost=1) × 완성 2컷


def test_run_detail_page_job_rejects_board_growth_before_generation(monkeypatch):
    """예약 뒤 콘티가 늘면 예약액으로 새 컷을 싸게 출고하지 않고 전액 환불한다."""
    captured = {}
    reserved_board = [{"id": "b1", "source": "ai", "cutType": "styling"},
                      {"id": "b2", "source": "ai", "cutType": "product"}]

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
        raise AssertionError("changed storyboard must fail before provider calls")

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **_kw):
        return [{"id": "b0", "kind": "benefit", "contentRole": "hero", "elements": []}]

    async def fake_finalize(conn, **kw):
        raise AssertionError("changed storyboard must not publish")

    async def fake_failure(conn, **kw):
        captured["failure"] = kw
        return True

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

    job = _job(reserved=2)
    job["metadata"]["storyboardDigest"] = hashlib.sha256(json.dumps(
        reserved_board, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode()).hexdigest()
    asyncio.run(dpj.run_detail_page_job(_app(_settings()), job))

    assert captured["failure"]["code"] == "storyboard_changed"
    assert captured["failure"]["reserved"] == 2


def test_run_detail_page_job_fails_when_all_ai_blocks_lack_garment_truth(monkeypatch):
    # 옷 근거(상품 사진/마네킹) 없이 무드 레퍼런스만 있는 블록은 생성하지 않는다 — 실패·환불.
    # (무드만 첨부하면 모델이 레퍼런스 속 옷을 베끼거나 지어낸다 — ADR-0004 정확성 최우선)
    captured = {}
    calls = {"n": 0}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return [{"id": f"b{i}", "source": "ai", "cutType": "styling", "refAssetIds": ["ref1"]}
                for i in range(3)]

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
    job = worker_job(credits_reserved=95)
    job["metadata"] = {"perCutCost": 19, "aiCount": 3, "minCuts": 5}
    asyncio.run(dpj.run_detail_page_job(app, job))

    assert calls["n"] == 0                       # 생성 호출 자체가 없다
    assert captured["failure"]["code"] == "all_cuts_failed"
    assert captured["failure"]["reserved"] == 95  # 실패 경로가 예약 전액을 반환
    assert captured["failure"]["metadata"] == {
        "error": "all_cuts_failed", "requestedCuts": 3,
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
        return {"sellingPoints": ["촉감"], "materials": [],
                "suggestedName": "골지 데일리 니트"}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": "k/a1"}

    async def fake_gen(settings, gemini, cut_spec, product, images, *, analysis=None, manifest=None, **_kw):
        captured["cut_spec"] = cut_spec
        return b"IMG", "image/png"

    async def fake_copy(settings, **kw):
        captured["copy_kwargs"] = kw
        return [{"blockId": "b1", "texts": [
            {"role": "headline", "text": "원본 카피"},
        ]}]

    async def fake_review(settings, items, confirmed):
        raise RuntimeError("qc down")  # 검수 실패 → 원문 유지 (except 커버)

    async def fake_feature(settings, points, product_row, analysis_row):
        captured["feature_points"] = points
        return []

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
    monkeypatch.setattr(dpj.feature_copy, "generate", fake_feature)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    asyncio.run(dpj.run_detail_page_job(_app(_settings()), _job(reserved=1)))  # ai 블록 1개 × 단가 1
    assert captured["charge"] == 1
    assert [block["id"] for block in captured["copy_kwargs"]["blocks"]] == ["b1"]
    assert set(captured["copy_kwargs"]) == {"blocks", "product", "analysis"}
    assert captured["product_name"] == "골지 데일리 니트"
    assert captured["assembled_product_name"] == "골지 데일리 니트"
    assert captured["cut_spec"]["cutType"] == "product"
    assert captured["cut_spec"]["shot"] == "detail"
    assert captured["feature_points"] == ["촉감"]
    assert captured["storyboard"][0]["sectionRole"] == "product"
    assert captured["storyboard"][0]["cutType"] == "product"
    assert captured["storyboard"][0]["shot"] == "detail"
    assert captured["copy_results"] == [{"blockId": "b1", "texts": [
        {"role": "headline", "text": "원본 카피"},
    ]}]


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


def test_fine_pattern_detail_generates_checked_new_cut_instead_of_original(monkeypatch):
    """촘촘한 무늬도 원본을 바로 출고하지 않고 새 디테일 후보를 검수한다."""
    calls = []

    async def fake_emit(pool, job_id, et, payload):
        return None

    async def fake_detail(app, job, block, product, analysis):
        calls.append(block["id"])
        return dpj.InlineImage("image/png", b"POLISHED-IMAGE"), {"passed": True}

    monkeypatch.setattr(dpj, "_emit", fake_emit)
    monkeypatch.setattr(dpj.detail_shot_runtime, "generate", fake_detail)
    app = _app(_settings())
    spec = {"id": "detail-1", "cutType": "product", "shot": "detail"}
    images = [dpj.InlineImage("image/png", b"front")]
    original = {"id": "asset-detail-1", "width": 3000, "height": 4000, "slot": "Detail"}

    stored_outputs = []
    (cut_results, cut_assets, face_cuts, garment_qcs,
     cut_qcs, page_qc, warnings) = asyncio.run(dpj._gen_cuts(
        app, _job(reserved=1),
        [(spec, images, "manifest", False, images, None, False, original)],
        {"name": "스트라이프 셔츠", "clothingType": "top"}, {},
        stored_outputs=stored_outputs,
    ))

    assert calls == ["detail-1"]
    assert len(cut_results) == len(cut_assets) == 1
    assert stored_outputs == [{"key": cut_assets[0]["key"],
                               "cleanup_intent_id": cut_assets[0]["cleanup_intent_id"]}]
    assert cut_results[0]["imageUrl"] != "/v1/assets/asset-detail-1/file"
    assert cut_qcs == [{"blockId": "detail-1", "passed": True}]
    assert face_cuts == 0 and garment_qcs == []
    assert page_qc is None and warnings == []


def test_striped_detail_page_never_publishes_uploaded_detail_photo(monkeypatch):
    captured = {"detail_calls": 0, "events": [], "uploaded": [], "feature_calls": []}

    async def project(conn, user_id, project_id):
        return {"copywriting": False}

    async def storyboard(conn, project_id):
        return [{"id": "stripe-detail", "source": "ai", "cutType": "product",
                 "shot": "detail", "direction": "front", "colorId": "base"}]

    async def product(conn, project_id):
        return {"name": "잔스트라이프 셔츠", "clothing_type": "top", "colors": [{
            "id": "base", "isBase": True,
            "images": [{"slot": "Front", "id": "front"},
                       {"slot": "Detail", "id": "source-detail"}],
        }]}

    async def analysis(conn, project_id):
        return {}

    async def asset(conn, user_id, asset_id):
        return {"id": asset_id, "mime_type": "image/png", "r2_key": f"input/{asset_id}"}

    async def detail(app, job, block, product_row, analysis_row, **kwargs):
        captured["detail_calls"] += 1
        return dpj.InlineImage("image/png", b"POLISHED-DETAIL"), {"passed": True}

    async def feature(settings, points, product_row, analysis_row):
        captured["feature_calls"].append((points, product_row.get("name")))
        return []

    def assemble(storyboard_rows, cut_results, *args, **kwargs):
        captured["cut_results"] = cut_results
        return []

    async def finalize(conn, **kwargs):
        captured["finalize"] = kwargs
        return {"editor_blocks": [], "available": 95}

    async def emit(pool, job_id, event, payload):
        captured["events"].append((event, payload))

    monkeypatch.setattr(dpj.repo, "get_project", project)
    monkeypatch.setattr(dpj.repo, "get_storyboard", storyboard)
    monkeypatch.setattr(dpj.repo, "get_product", product)
    monkeypatch.setattr(dpj.repo, "get_analysis", analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", asset)
    monkeypatch.setattr(dpj.detail_shot_runtime, "generate", detail)
    monkeypatch.setattr(dpj.feature_copy, "generate", feature)
    monkeypatch.setattr(dpj.page_assembler, "assemble", assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", finalize)
    monkeypatch.setattr(dpj, "_emit", emit)

    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"))
    app.state.r2.put_bytes = lambda key, data, *args, **kwargs: captured["uploaded"].append(data)
    job = worker_job(credits_reserved=95)
    job["metadata"] = {"perCutCost": 19, "aiCount": 1, "minCuts": 5}
    asyncio.run(dpj.run_detail_page_job(app, job))

    assert captured["detail_calls"] == 1
    assert captured["uploaded"] == [b"POLISHED-DETAIL"]
    assert captured["cut_results"][0]["imageUrl"] != "/v1/assets/source-detail/file"
    assert len(captured["finalize"]["cut_assets"]) == 1
    assert captured["finalize"]["charge"] == 95
    assert captured["feature_calls"] == [([], "잔스트라이프 셔츠")]
    assert all(payload.get("status") != "cut_passthrough" for _, payload in captured["events"])


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
        return [
            {"blockId": block["id"], "texts": [{"role": "headline", "text": "카피"}]}
            for block in kw["blocks"]
        ]

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


def test_every_non_real_stored_cut_done_carries_preview_url(monkeypatch):
    """대기 화면 점진 표시(2026-09-26) — 저장된 비-REAL 컷은 cut_done 마다 예외 없이
    previewUrl 을 싣는다(원본·복제 모두). 디테일도 새 생성 컷으로 표시한다.
    REAL 얼굴 컷의 주소 비공개는 test_detail_page_license_face 가 지킨다."""
    events = []

    async def fake_emit(pool, job_id, et, payload):
        events.append((et, payload))

    async def fake_gen(settings, gemini, cut_spec, product, images, **_kw):
        return b"IMGDATA", "image/png"

    async def checked_detail(app, job, block, product, analysis, **kwargs):
        return dpj.InlineImage("image/png", b"CHECKED-DETAIL"), {"passed": True}

    monkeypatch.setattr(dpj, "_emit", fake_emit)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.detail_shot_runtime, "generate", checked_detail)
    app = _app(_settings())
    images = [dpj.InlineImage("image/png", b"front")]
    worn = {"source": "ai", "sectionId": "section-a", "sectionRole": "studio",
            "cutType": "horizon", "shot": "full", "direction": "front",
            "pose": "auto", "refScope": "all"}
    detail = {"source": "ai", "cutType": "product", "shot": "detail",
              "direction": "front", "colorId": "base"}
    original = {"id": "asset-detail-1", "width": 3000, "height": 4000, "slot": "Detail"}

    def item(spec, passthrough=None):
        return (spec, images, "manifest", False, images, None, False, passthrough)

    prepared = [
        item({**worn, "id": "worn"}),
        item({**worn, "id": "worn-copy"}),
        item({**detail, "id": "detail"}, original),
        item({**detail, "id": "detail-copy"}, original),
    ]
    assert dpj._duplicate_source_indexes(
        [p[0] for p in prepared], "top") == [None, 0, None, 2], "복제 판정 전제"

    (cut_results, cut_assets, *_rest) = asyncio.run(dpj._gen_cuts(
        app, _job(reserved=2), prepared, {"name": "셔츠", "clothingType": "top"}, {}))

    assert [c["blockId"] for c in cut_results] == ["worn", "worn-copy", "detail", "detail-copy"]
    assert len(cut_assets) == 2, "착용 원본과 디테일 원본만 생성 자산으로 센다"
    dones = [p for et, p in events if et == "step" and p.get("status") == "cut_done"]
    assert {d["blockId"] for d in dones} == {"worn", "worn-copy", "detail", "detail-copy"}
    assert len(dones) == 4
    for d in dones:
        assert d["previewUrl"].startswith("https://r2.test/"), d["blockId"]
    assert not any(p.get("status") == "cut_passthrough" for _, p in events)
    # 이벤트는 작게 — 원본 키·정리 표식은 싣지 않는다.
    assert "cleanup" not in repr(events) and "r2_key" not in repr(events)


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
    """응답이 미확정이면 3회 허용 설정이어도 프로바이더를 다시 부르지 않는다."""
    from app.agents.gemini_image import GeminiError

    attempts = 0

    async def fake_generate(settings, gemini, cut_spec, product, images, **kwargs):
        nonlocal attempts
        attempts += 1
        raise GeminiError("ambiguous provider response", billable=True)

    async def fake_emit(pool, job_id, event, payload):
        return None

    monkeypatch.setattr(dpj.cut_generator, "generate", fake_generate)
    monkeypatch.setattr(dpj, "_emit", fake_emit)
    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b",
                                        detail_cut_max_attempts=3))
    spec = {"id": "b1", "source": "ai", "cutType": "horizon", "shot": "full"}
    image = dpj.InlineImage("image/png", b"product")
    packet = types.SimpleNamespace(prompt_input="confirmed-input")
    prepared = [(spec, [image], "manifest", False, [image], None, False,
                 None, packet, False)]

    results, assets, *_ = asyncio.run(dpj._gen_cuts(
        app, _job(reserved=19), prepared, {"clothing_type": "top"}, {}))

    assert attempts == 1
    assert results == [] and assets == []


def test_nontransient_rejection_is_not_retried_by_the_worker(monkeypatch):
    from app.agents.gemini_image import GeminiError

    attempts = 0

    async def fake_generate(settings, gemini, cut_spec, product, images, **kwargs):
        nonlocal attempts
        attempts += 1
        raise GeminiError("OpenAI 400: invalid_image_file")

    async def fake_emit(pool, job_id, event, payload):
        return None

    monkeypatch.setattr(dpj.cut_generator, "generate", fake_generate)
    monkeypatch.setattr(dpj, "_emit", fake_emit)
    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b",
                                        detail_cut_max_attempts=3))
    spec = {"id": "b1", "source": "ai", "cutType": "horizon", "shot": "full"}
    image = dpj.InlineImage("image/png", b"product")
    packet = types.SimpleNamespace(prompt_input="confirmed-input")
    prepared = [(spec, [image], "manifest", False, [image], None, False,
                 None, packet, False)]

    results, assets, *_ = asyncio.run(dpj._gen_cuts(
        app, _job(reserved=19), prepared, {"clothing_type": "top"}, {}))

    assert attempts == 1
    assert results == [] and assets == []


def test_gemini_error_carries_the_billable_flag_across_layers():
    """표식이 오류 객체에 실려야 위층이 볼 수 있다."""
    from app.agents.gemini_image import GeminiError

    assert GeminiError("x").billable is False
    assert GeminiError("x", billable=True).billable is True
    # 위층은 getattr 로 읽는다 — 다른 예외 타입이 와도 안전하게 False.
    assert getattr(ValueError("y"), "billable", False) is False
