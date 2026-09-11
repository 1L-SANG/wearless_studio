"""컷 범위가 실제 생성 경로에서 지켜지는가 — 예약·건너뜀·크레딧까지.

실제 모델에 가상 전용 컷을 억지로 만들지 않는다(2026-09-11 사용자 결정). 콘티보드가 1차로
거르지만, 저장된 콘티에서 모델만 바꾸면 범위 밖 블록이 남는다 — 서버가 마지막 방어선이다.
"""
import asyncio
from dataclasses import replace

import app.routes as routes
import pytest
from app.agents import identity_source
from app.workers import detail_page_job as dpj
from conftest import (
    FakeR2, auth_headers, fake_worker_app, make_settings, patch_route_db, worker_job,
)

REAL = "11111111-1111-1111-1111-111111111111"
LICENSE_ID = "22222222-2222-2222-2222-222222222222"
CATEGORY = "상의"

#: 확정 GPT 프로필을 요구하는 조합 = 가상 전용
VIRTUAL_ONLY = {"id": "v1", "source": "ai", "cutType": "styling", "direction": "front",
                "shot": "full", "refScope": "all", "pose": "auto",
                "exampleId": "ex_styling_men_top_full_snapshot_03"}
BOTH_BLOCK = {"id": "b1", "source": "ai", "cutType": "horizon", "shot": "full",
              "direction": "front"}


# ── 라우트: 예약에서 빠진다(만들지도 않을 컷으로 402 가 나면 안 된다) ──
@pytest.mark.parametrize("model_id,expected_cuts", [(REAL, 1), ("mB", 2)])
def test_reserve_counts_only_cuts_this_model_can_make(
    client, make_token, monkeypatch, model_id, expected_cuts,
):
    seen = {}
    client.app.state.settings = replace(client.app.state.settings, facemarket_enabled=True)

    async def fake_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_analysis(conn, project_id):
        return {"selectedModelId": model_id, "brandUseCategory": CATEGORY}

    async def fake_storyboard(conn, project_id):
        return [dict(BOTH_BLOCK), dict(VIRTUAL_ONLY)]

    async def fake_product(conn, project_id):
        return {"clothing_type": "top"}

    async def fake_editor(conn, project_id):
        return []

    async def fake_resolve(conn, project, analysis):
        return {"id": LICENSE_ID, "model_id": REAL}

    async def fake_verify(*args, **kwargs):
        return None

    async def fake_lock(conn, project_id, license_id):
        return None

    async def fake_create(conn, **kwargs):
        seen.update(kwargs)
        return {"id": "job-scope"}, True

    async def fake_reserve(conn, user_id, amount):
        seen["reserved"] = amount
        return 99

    async def fake_boundary_lock(conn):
        return None

    async def fake_boundary_open(conn):
        return False

    monkeypatch.setattr(routes.repo, "get_project", fake_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(routes.repo, "get_storyboard", fake_storyboard)
    monkeypatch.setattr(routes.repo, "get_product", fake_product)
    monkeypatch.setattr(routes.repo, "get_editor_blocks", fake_editor)
    monkeypatch.setattr(routes.facemarket, "resolve_project_license", fake_resolve)
    monkeypatch.setattr(routes.facemarket, "verify_license", fake_verify)
    monkeypatch.setattr(routes.facemarket, "set_project_license", fake_lock)
    monkeypatch.setattr(routes.repo, "create_job", fake_create)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    monkeypatch.setattr(routes.repo, "lock_facemarket_writer_boundary", fake_boundary_lock)
    monkeypatch.setattr(routes.repo, "facemarket_writer_boundary_closed", fake_boundary_open)
    patch_route_db(monkeypatch, routes)

    res = client.post("/v1/projects/p1/detail-page:generate", headers=auth_headers(make_token))
    assert res.status_code == 202, res.text
    per_cut = client.app.state.settings.credit_cost_storyboard_per_cut
    assert seen["metadata"]["aiCount"] == expected_cuts
    assert seen["credits_reserved"] == expected_cuts * per_cut


# ── 에디터 새 컷: 범위 밖이면 잡도 크레딧도 없다 ──
def test_editor_new_cut_outside_the_scope_is_refused_before_any_charge(
    client, make_token, monkeypatch,
):
    calls = {"create": 0, "reserve": 0}
    client.app.state.settings = replace(client.app.state.settings, facemarket_enabled=True)

    async def fake_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_analysis(conn, project_id):
        return {"brandUseCategory": CATEGORY}

    async def forbidden_create(*args, **kwargs):
        calls["create"] += 1

    async def forbidden_reserve(*args, **kwargs):
        calls["reserve"] += 1

    async def forbidden_resolve(*args, **kwargs):
        raise AssertionError("범위 밖이면 라이선스도 읽지 않는다")

    monkeypatch.setattr(routes.repo, "get_project", fake_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(routes.repo, "create_job", forbidden_create)
    monkeypatch.setattr(routes.repo, "reserve_credits", forbidden_reserve)
    monkeypatch.setattr(routes.facemarket, "resolve_model_license", forbidden_resolve)
    patch_route_db(monkeypatch, routes)

    res = client.post(
        "/v1/projects/p1/editor:generate-image", headers=auth_headers(make_token),
        json={"mode": "new", **{k: v for k, v in VIRTUAL_ONLY.items()
                                if k not in ("id", "source")}, "modelId": REAL},
    )
    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == "identity_scope_mismatch"
    assert calls == {"create": 0, "reserve": 0}


def test_editor_new_cut_inside_the_scope_is_not_touched(client, make_token, monkeypatch):
    """가상 모델의 같은 컷은 예전 그대로 통과한다(회귀 고정)."""
    seen = {}
    client.app.state.settings = replace(client.app.state.settings, facemarket_enabled=True)

    async def fake_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_analysis(conn, project_id):
        return {"brandUseCategory": CATEGORY}

    async def fake_create(conn, **kwargs):
        seen.update(kwargs)
        return {"id": "job-ok"}, True

    async def fake_reserve(conn, user_id, amount):
        return 9

    monkeypatch.setattr(routes.repo, "get_project", fake_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(routes.repo, "create_job", fake_create)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    patch_route_db(monkeypatch, routes)

    res = client.post(
        "/v1/projects/p1/editor:generate-image", headers=auth_headers(make_token),
        json={"mode": "new", **{k: v for k, v in VIRTUAL_ONLY.items()
                                if k not in ("id", "source")}, "modelId": "mB"},
    )
    assert res.status_code == 202, res.text
    assert seen["payload"]["modelId"] == "mB"


# ── 워커: 범위 밖 블록은 생성 전에 건너뛰고, 그 컷은 과금되지 않는다 ──
def test_worker_skips_out_of_scope_blocks_without_charging(monkeypatch):
    captured = {"cuts": [], "events": []}

    async def fake_project(conn, uid, pid):
        return {"id": pid, "copywriting": False}

    async def fake_storyboard(conn, pid):
        return [dict(BOTH_BLOCK), dict(VIRTUAL_ONLY)]

    async def fake_product(conn, pid):
        return {"clothing_type": "top", "colors": [{"id": "col1", "isBase": True}]}

    async def fake_analysis(conn, pid):
        return {"selectedModelId": REAL, "brandUseCategory": CATEGORY}

    async def fake_generate(settings, gemini, cut_spec, product, images, **kwargs):
        captured["cuts"].append(cut_spec.get("id"))
        return b"IMG", "image/png"

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **kwargs):
        return []

    async def fake_finalize(conn, **kwargs):
        captured["finalize"] = kwargs
        return {"editor_blocks": [], "available": 99}

    async def fake_emit(*args, **kwargs):
        captured["events"].append((args[-2], args[-1]))

    async def fake_license(conn, model_id, *, license_id=None, **kwargs):
        return {"id": LICENSE_ID, "model_id": REAL, "model_status": "verified",
                "status": "active", "assets_status": "ready", "unit_price": None,
                "current_enrollment_id": "e1", "match_policy_version": "v1",
                "model_name": "김*늘", "gender": "men", "height_bucket": None,
                "body_type": None, "license_valid_until": None, "vc_id": None,
                "allowed_use": [CATEGORY], "forbidden_use": [],
                "enrollment_status": "passed", "license_enrollment_id": "e1",
                "has_face_front": True, "has_grid_sedcard": True,
                "assets_current_evidence": True}

    async def fake_real_refs(conn, model_id, **kwargs):
        return [{"key": "real/face", "mime": "image/png", "bucket": "face"},
                {"key": "real/grid", "mime": "image/png", "bucket": "face"}]

    monkeypatch.setattr(dpj.repo, "get_project", fake_project)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_storyboard)
    monkeypatch.setattr(dpj.repo, "get_product", fake_product)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.facemarket, "resolve_model_license", fake_license)
    monkeypatch.setattr(dpj.facemarket, "verify_license", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(dpj.facemarket, "verify_license_local", lambda *a, **k: None)
    monkeypatch.setattr(identity_source, "resolve_real_model_assets", fake_real_refs)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_generate)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj.repo, "lock_facemarket_writer_boundary", lambda conn: asyncio.sleep(0))
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b", facemarket_enabled=True))
    app.state.r2_face = FakeR2()
    asyncio.run(dpj.run_detail_page_job(app, worker_job({
        "mode": "generate", "modelId": REAL, "brandUseCategory": CATEGORY,
        "_facemarket": {"modelId": REAL, "licenseId": LICENSE_ID},
    }, credits_reserved=1)))

    # 가상 전용 블록은 생성기까지 가지 않는다
    assert "v1" not in captured["cuts"]
    skipped = [payload for kind, payload in captured["events"]
               if isinstance(payload, dict) and payload.get("status") == "cut_skipped"]
    assert any(p.get("reason") == "identity_scope_mismatch" and p.get("blockId") == "v1"
               for p in skipped), (skipped, captured["events"], captured.get("cuts"))
    # 건너뛴 컷은 자산이 없다 = 성공 컷 수 기준 정산에서 빠진다(크레딧 0).
    assets = (captured.get("finalize") or {}).get("cut_assets") or []
    assert all(a.get("block_id") != "v1" for a in assets)
