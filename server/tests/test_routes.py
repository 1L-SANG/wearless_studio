"""라우트 HTTP 레벨 회귀 테스트.

검증 실패는 get_conn(DB) 이전에 발생하므로 풀 없이 TestClient로 검증 가능.
explicit-null PATCH가 500이 아니라 422 봉투로 떨어지는지 — 에러 핸들러 직렬화 버그 가드.
"""

from dataclasses import replace

import pytest

import app.routes as routes

from conftest import patch_route_db


MODEL_ID = "11111111-1111-1111-1111-111111111111"
LICENSE_ID = "22222222-2222-2222-2222-222222222222"
CATEGORY = "일반 여성 의류"


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def test_editor_source_capability_parser_accepts_api_urls_and_rejects_malformed_hosts():
    asset_id = "33333333-3333-3333-3333-333333333333"

    assert routes._asset_id_from_file_capability(
        f"https://api.wearless.kr/v1/assets/{asset_id}/file?e=2"
    ) == asset_id
    assert routes._asset_id_from_file_capability("http://[") is None


def test_patch_explicit_null_compose_mode_is_422_not_500(client, make_token):
    res = client.patch(
        "/v1/projects/any-id", headers=_auth(make_token), json={"composeMode": None}
    )
    assert res.status_code == 422
    body = res.json()
    assert body["error"]["code"] == "validation_error"


def test_patch_explicit_null_copywriting_is_422(client, make_token):
    res = client.patch(
        "/v1/projects/any-id", headers=_auth(make_token), json={"copywriting": None}
    )
    assert res.status_code == 422


def test_patch_retired_simple_mode_is_422(client, make_token):
    res = client.patch(
        "/v1/projects/any-id", headers=_auth(make_token), json={"composeMode": "simple"}
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "validation_error"


def test_openapi_only_advertises_current_compose_modes(client):
    schemas = client.app.openapi()["components"]["schemas"]
    assert schemas["Project"]["properties"]["composeMode"]["enum"] == ["basic", "extended"]
    assert schemas["ProjectPatch"]["properties"]["composeMode"]["anyOf"][0]["enum"] == [
        "basic",
        "extended",
    ]


def test_save_analysis_forces_dress_to_women(client, make_token, monkeypatch):
    seen = {}

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_get_product(conn, project_id):
        return {"clothingType": "dress"}

    async def fake_save_analysis(conn, project_id, analysis):
        seen["analysis"] = analysis
        return {"project_id": project_id, "payload": analysis}

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "get_product", fake_get_product)
    monkeypatch.setattr(routes.repo, "save_analysis", fake_save_analysis)
    patch_route_db(monkeypatch, routes)

    res = client.patch(
        "/v1/projects/p1/analysis",
        headers=_auth(make_token),
        json={"targetGenders": ["men"], "fit": "regular"},
    )

    assert res.status_code == 200, res.text
    assert seen["analysis"]["targetGenders"] == ["women"]
    assert res.json()["targetGenders"] == ["women"]


def test_save_analysis_normalizes_brand_use_category(client, make_token, monkeypatch):
    seen = {}

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_get_product(conn, project_id):
        return {"clothingType": "top"}

    async def fake_save_analysis(conn, project_id, analysis):
        seen["analysis"] = analysis
        return {"project_id": project_id, "payload": analysis}

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "get_product", fake_get_product)
    monkeypatch.setattr(routes.repo, "save_analysis", fake_save_analysis)
    patch_route_db(monkeypatch, routes)

    res = client.patch(
        "/v1/projects/p1/analysis",
        headers=_auth(make_token),
        json={"brandUseCategory": "  일반 여성 의류  "},
    )

    assert res.status_code == 200, res.text
    assert seen["analysis"]["brandUseCategory"] == "일반 여성 의류"
    assert res.json()["brandUseCategory"] == "일반 여성 의류"


def test_save_analysis_rejects_unknown_brand_use_category_before_persistence(
    client, make_token, monkeypatch
):
    called = False

    async def fake_save_analysis(conn, project_id, analysis):
        nonlocal called
        called = True

    monkeypatch.setattr(routes.repo, "save_analysis", fake_save_analysis)
    patch_route_db(monkeypatch, routes)

    res = client.patch(
        "/v1/projects/p1/analysis",
        headers=_auth(make_token),
        json={"brandUseCategory": "의류"},
    )

    assert res.status_code == 400, res.text
    assert res.json()["error"]["code"] == "invalid_brand_use_category"
    assert called is False


@pytest.mark.parametrize("value", [False, 0, [], {}])
def test_save_analysis_rejects_non_string_brand_use_category_before_persistence(
    client, make_token, monkeypatch, value
):
    called = False

    async def fake_save_analysis(conn, project_id, analysis):
        nonlocal called
        called = True

    monkeypatch.setattr(routes.repo, "save_analysis", fake_save_analysis)
    patch_route_db(monkeypatch, routes)

    res = client.patch(
        "/v1/projects/p1/analysis",
        headers=_auth(make_token),
        json={"brandUseCategory": value},
    )

    assert res.status_code == 400, res.text
    assert res.json()["error"]["code"] == "invalid_brand_use_category"
    assert called is False


def test_save_analysis_normalizes_empty_brand_use_category_to_none(
    client, make_token, monkeypatch
):
    seen = []

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_get_product(conn, project_id):
        return {"clothingType": "top"}

    async def fake_save_analysis(conn, project_id, analysis):
        seen.append(analysis)
        return {"project_id": project_id, "payload": analysis}

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "get_product", fake_get_product)
    monkeypatch.setattr(routes.repo, "save_analysis", fake_save_analysis)
    patch_route_db(monkeypatch, routes)

    for value in (None, "   "):
        res = client.patch(
            "/v1/projects/p1/analysis",
            headers=_auth(make_token),
            json={"brandUseCategory": value},
        )
        assert res.status_code == 200, res.text
        assert res.json()["brandUseCategory"] is None

    assert [analysis["brandUseCategory"] for analysis in seen] == [None, None]


def test_analysis_routes_normalize_retired_tight_fit(client, make_token, monkeypatch):
    seen = {}

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_get_product(conn, project_id):
        return {"clothingType": "top"}

    async def fake_get_analysis(conn, project_id):
        return {
            "fit": "tight",
            "fitProfile": {
                "category": "top", "gender": "women", "source": "auto",
                "axes": {"fit": "tight", "length": "basic"},
            },
        }

    async def fake_save_analysis(conn, project_id, analysis):
        seen["analysis"] = analysis
        return {"project_id": project_id, "payload": analysis}

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "get_product", fake_get_product)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_get_analysis)
    monkeypatch.setattr(routes.repo, "save_analysis", fake_save_analysis)
    patch_route_db(monkeypatch, routes)

    loaded = client.get("/v1/projects/p1/analysis", headers=_auth(make_token))
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["fit"] == "slim"
    assert loaded.json()["fitProfile"]["axes"]["fit"] == "slim"

    saved = client.patch(
        "/v1/projects/p1/analysis",
        headers=_auth(make_token),
        json={
            "fit": "tight",
            "fitProfile": {
                "category": "top", "gender": "women", "source": "seller",
                "axes": {"fit": "tight"},
            },
        },
    )
    assert saved.status_code == 200, saved.text
    assert seen["analysis"]["fit"] == "slim"
    assert seen["analysis"]["fitProfile"]["axes"]["fit"] == "slim"


def test_save_product_atomically_repairs_dress_analysis_gender(
    client, make_token, monkeypatch
):
    seen = {}

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_save_product(conn, project_id, user_id, fields):
        seen["product"] = fields
        return {
            "id": "product-1",
            "project_id": project_id,
            "name": "원피스",
            "clothing_type": "dress",
            "colors": [],
            "measurements": [],
            "measurements_unknown": False,
            "upload_complete": True,
        }

    async def fake_get_analysis(conn, project_id):
        return {"targetGenders": ["men"], "fit": "regular"}

    async def fake_save_analysis(conn, project_id, analysis):
        seen["analysis"] = analysis
        return {"project_id": project_id, "payload": analysis}

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "save_product", fake_save_product)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_get_analysis)
    monkeypatch.setattr(routes.repo, "save_analysis", fake_save_analysis)
    patch_route_db(monkeypatch, routes)

    res = client.patch(
        "/v1/projects/p1/product",
        headers=_auth(make_token),
        json={"clothingType": "dress"},
    )

    assert res.status_code == 200, res.text
    assert seen["product"]["clothing_type"] == "dress"
    assert seen["analysis"] == {
        "targetGenders": ["women"],
        "fit": "regular",
    }


def test_save_storyboard_persists_canonical_blocks(client, make_token, monkeypatch):
    seen = {}

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_save_storyboard(conn, user_id, project_id, blocks):
        seen["blocks"] = blocks
        return blocks

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "save_storyboard", fake_save_storyboard)
    patch_route_db(monkeypatch, routes)

    res = client.put(
        "/v1/projects/p1/storyboard",
        headers=_auth(make_token),
        json=[
            {
                "id": "b1",
                "source": "ai",
                "contentRole": "detail",
                "cutType": "horizon",
                "shot": "full",
            },
                {
                    "id": "b2",
                    "source": "mine",
                    "sectionRole": "fit",
                    "cutType": "styling",
                    "ownImages": ["asset-1"],
            },
            {
                "id": "b3",
                "source": "ai",
                "contentRole": "hero",
                "cutType": "product",
                "shot": "detail",
            },
        ],
    )

    assert res.status_code == 200, res.text
    assert [block["id"] for block in seen["blocks"]] == ["b3", "b2", "b1"]
    hero, mine, detail = seen["blocks"]
    assert (hero["sectionRole"], hero["cutType"], hero["shot"]) == (
        "hooking", "styling", "full",
    )
    assert (detail["contentRole"], detail["sectionRole"], detail["cutType"], detail["shot"]) == (
        "detail", "product", "product", "detail",
    )
    assert mine["contentRole"] == "custom"
    assert mine["cutType"] is None
    assert mine["ownImages"] == ["asset-1"]


def test_save_storyboard_rejects_invalid_example_selection_origin_before_db(client, make_token):
    res = client.put(
        "/v1/projects/p1/storyboard",
        headers=_auth(make_token),
        json=[{
            "id": "b1", "source": "ai", "sectionRole": "fit", "contentRole": "fit",
            "cutType": "horizon", "direction": "front", "shot": "full",
            "exampleId": "example-id", "exampleSelectionOrigin": "system",
        }],
    )

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "invalid_example_selection_origin"


def test_save_storyboard_rejects_pre_release_space_group_ids(
    client, make_token, monkeypatch
):
    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_product(conn, project_id):
        return {"clothingType": "top"}

    async def fake_analysis(conn, project_id):
        return {"targetGenders": ["women"]}

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "get_product", fake_product)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    patch_route_db(monkeypatch, routes)

    res = client.put(
        "/v1/projects/p1/storyboard",
        headers=_auth(make_token),
        json=[{
            "id": "b1",
            "source": "ai",
            "sectionRole": "fit",
            "contentRole": "coordination",
            "cutType": "styling",
            "direction": "front",
            "shot": "full",
            "spaceGroupId": "legacy-space",
        }],
    )

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "invalid_space_set_group_id"


def test_save_storyboard_rejects_bg_example_when_pilot_disabled(client, make_token):
    res = client.put(
        "/v1/projects/p1/storyboard",
        headers=_auth(make_token),
        json=[{
            "id": "b1",
            "source": "ai",
            "contentRole": "hero",
            "exampleId": "ex-bg-1",
            "refScope": "bg",
        }],
    )

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "genexample_bg_disabled"


def test_save_storyboard_validates_production_set_without_flat_registry(
    client, make_token, monkeypatch
):
    seen = {}

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_product(conn, project_id):
        return {"clothingType": "top"}

    async def fake_analysis(conn, project_id):
        return {"targetGenders": ["women"]}

    async def fake_save(conn, user_id, project_id, blocks):
        seen["saved"] = blocks
        return blocks

    def fake_validate(blocks, *, clothing_type, gender):
        seen["validated"] = (blocks, clothing_type, gender)
        return None

    def fake_resolve_example(block, *, clothing_type, gender, scope):
        seen["standaloneExample"] = (
            block["exampleId"],
            clothing_type,
            gender,
            scope,
        )
        return {"source": "space-set", "exampleId": block["exampleId"]}

    def flat_registry_must_not_load():
        raise AssertionError("production sets must not fall back to flat registry")

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "get_product", fake_product)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(routes.repo, "save_storyboard", fake_save)
    monkeypatch.setattr(
        routes.space_set_assets, "validate_storyboard_space_sets", fake_validate
    )
    monkeypatch.setattr(
        routes.space_set_assets,
        "resolve_published_example_reference",
        fake_resolve_example,
    )
    monkeypatch.setattr(
        routes.cut_generator, "load_example_asset_registry", flat_registry_must_not_load
    )
    patch_route_db(monkeypatch, routes)

    res = client.put(
        "/v1/projects/p1/storyboard",
        headers=_auth(make_token),
        json=[
            {
                "id": "b1",
                "source": "ai",
                "sectionRole": "fit",
                "contentRole": "coordination",
                "cutType": "styling",
                "direction": "front",
                "shot": "full",
                "spaceGroupId": "ssg1__set-cafe-01__instance-01",
                "spaceSetMemberOrder": 1,
                "exampleId": "ss_cafe_01",
                "refScope": "bg",
            },
            {
                "id": "dragged-out",
                "source": "ai",
                "sectionRole": "fit",
                "contentRole": "coordination",
                "cutType": "styling",
                "direction": "front",
                "shot": "full",
                "exampleId": "ss_cafe_01",
                "refScope": "all",
            },
        ],
    )

    assert res.status_code == 200, res.text
    assert seen["validated"][1:] == ("top", "women")
    assert seen["saved"][0]["exampleId"] == "ss_cafe_01"
    assert seen["standaloneExample"] == (
        "ss_cafe_01",
        "top",
        "women",
        "all",
    )


def test_generate_editor_image_rejects_bg_example_before_credit_reservation(
    client, make_token,
):
    res = client.post(
        "/v1/projects/p1/editor:generate-image",
        headers=_auth(make_token),
        json={
            "mode": "new",
            "contentRole": "hero",
            "exampleId": "ex-bg-1",
            "refScope": "bg",
        },
    )

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "genexample_bg_disabled"


def test_generate_editor_image_rejects_space_groups_before_credit_reservation(
    client, make_token,
):
    res = client.post(
        "/v1/projects/p1/editor:generate-image",
        headers=_auth(make_token),
        json={
            "mode": "new",
            "cutType": "styling",
            "spaceGroupId": "ssg1__set-cafe-01__instance-01",
        },
    )

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "space_set_editor_unsupported"


@pytest.mark.parametrize("model_key", ["modelId", "model_id"])
def test_editor_new_owns_category_model_and_license_snapshot(
    client, make_token, monkeypatch, model_key
):
    seen = {}
    fence = []
    client.app.state.settings = replace(
        client.app.state.settings,
        facemarket_enabled=True,
    )

    async def fake_project(conn, user_id, project_id):
        return {"id": project_id, "facemarket_license_id": "stale"}

    async def fake_analysis(conn, project_id):
        return {"brandUseCategory": CATEGORY}

    async def fake_resolve(conn, model_id, **kwargs):
        assert model_id == MODEL_ID and kwargs == {}
        return {"id": LICENSE_ID, "model_id": MODEL_ID}

    async def fake_verify(app, row, **kwargs):
        assert kwargs == {"model_id": MODEL_ID, "brand_use_category": CATEGORY}

    async def fake_create(conn, **kwargs):
        seen.update(kwargs)
        return {"id": "job-1"}, True

    async def fake_reserve(conn, user_id, amount):
        return 9

    async def fake_lock(conn):
        fence.append("lock")

    async def fake_closed(conn):
        fence.append("closed")
        return False

    monkeypatch.setattr(routes.repo, "get_project", fake_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(routes.facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(routes.facemarket, "verify_license", fake_verify)
    monkeypatch.setattr(routes.repo, "create_job", fake_create)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    monkeypatch.setattr(routes.repo, "lock_facemarket_writer_boundary", fake_lock)
    monkeypatch.setattr(routes.repo, "facemarket_writer_boundary_closed", fake_closed)
    patch_route_db(monkeypatch, routes)

    response = client.post(
        "/v1/projects/p1/editor:generate-image",
        headers=_auth(make_token),
        json={
            "mode": "new",
            "cutType": "styling",
            model_key: MODEL_ID,
            "brandUseCategory": "정치·종교",
            "_facemarket": {"modelId": "attacker", "licenseId": "attacker"},
        },
    )

    assert response.status_code == 202, response.text
    assert seen["payload"]["modelId"] == MODEL_ID
    assert "model_id" not in seen["payload"]
    assert seen["payload"]["brandUseCategory"] == CATEGORY
    assert seen["payload"]["_facemarket"] == {
        "modelId": MODEL_ID,
        "licenseId": LICENSE_ID,
    }
    assert fence == ["lock", "closed"]


def test_editor_vary_inherits_trusted_source_license_snapshot(
    client, make_token, monkeypatch
):
    seen = {}
    fence = []
    source_asset_id = "33333333-3333-3333-3333-333333333333"
    client.app.state.settings = replace(
        client.app.state.settings,
        facemarket_enabled=True,
    )

    async def fake_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_analysis(conn, project_id):
        return {"brandUseCategory": CATEGORY}

    async def fake_provenance(conn, user_id, asset_id):
        assert asset_id == source_asset_id
        return {
            "real_derived": True,
            "facemarket": {"modelId": MODEL_ID, "licenseId": LICENSE_ID},
        }

    async def fake_resolve(conn, model_id, **kwargs):
        assert model_id == MODEL_ID
        assert kwargs == {"license_id": LICENSE_ID}
        return {"id": LICENSE_ID, "model_id": MODEL_ID}

    async def fake_verify(app, row, **kwargs):
        assert kwargs == {"model_id": MODEL_ID, "brand_use_category": CATEGORY}

    async def fake_create(conn, **kwargs):
        seen.update(kwargs)
        return {"id": "job-vary-real"}, True

    async def fake_reserve(conn, user_id, amount):
        return 9

    async def fake_lock(conn):
        fence.append("lock")

    async def fake_closed(conn):
        fence.append("closed")
        return False

    monkeypatch.setattr(routes.repo, "get_project", fake_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(
        routes.repo,
        "get_asset_facemarket_provenance",
        fake_provenance,
        raising=False,
    )
    monkeypatch.setattr(routes.facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(routes.facemarket, "verify_license", fake_verify)
    monkeypatch.setattr(routes.repo, "create_job", fake_create)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    monkeypatch.setattr(routes.repo, "lock_facemarket_writer_boundary", fake_lock)
    monkeypatch.setattr(routes.repo, "facemarket_writer_boundary_closed", fake_closed)
    patch_route_db(monkeypatch, routes)

    response = client.post(
        "/v1/projects/p1/editor:generate-image",
        headers=_auth(make_token),
        json={
            "mode": "vary",
            "source": {
                "src": f"/v1/assets/{source_asset_id}/file?e=2",
                "cutType": "styling",
            },
            "_facemarket": {"modelId": "attacker", "licenseId": "attacker"},
        },
    )

    assert response.status_code == 202, response.text
    assert seen["payload"]["_facemarket"] == {
        "modelId": MODEL_ID,
        "licenseId": LICENSE_ID,
    }
    assert seen["payload"]["brandUseCategory"] == CATEGORY
    assert fence == ["lock", "closed"]


def test_editor_vary_real_marker_without_trusted_lineage_fails_before_charge(
    client, make_token, monkeypatch
):
    calls = {"create": 0, "reserve": 0}
    source_asset_id = "33333333-3333-3333-3333-333333333333"

    async def fake_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_provenance(conn, user_id, asset_id):
        return {"real_derived": True, "facemarket": None}

    async def fake_create(*args, **kwargs):
        calls["create"] += 1

    async def fake_reserve(*args, **kwargs):
        calls["reserve"] += 1

    monkeypatch.setattr(routes.repo, "get_project", fake_project)
    monkeypatch.setattr(
        routes.repo,
        "get_asset_facemarket_provenance",
        fake_provenance,
        raising=False,
    )
    monkeypatch.setattr(routes.repo, "create_job", fake_create)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    patch_route_db(monkeypatch, routes)

    response = client.post(
        "/v1/projects/p1/editor:generate-image",
        headers=_auth(make_token),
        json={
            "mode": "vary",
            "source": {"src": f"/v1/assets/{source_asset_id}/file"},
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "model_unavailable"
    assert calls == {"create": 0, "reserve": 0}


def test_editor_denial_precedes_job_and_credit(client, make_token, monkeypatch):
    client.app.state.settings = replace(
        client.app.state.settings,
        facemarket_enabled=True,
    )
    calls = {"create": 0, "reserve": 0}

    async def fake_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_analysis(conn, project_id):
        return {"brandUseCategory": CATEGORY}

    async def fake_resolve(conn, model_id, **kwargs):
        return {"id": LICENSE_ID, "model_id": MODEL_ID}

    async def deny(*args, **kwargs):
        raise routes.HTTPException(
            status_code=409,
            detail={"code": "model_assets_unavailable", "message": "blocked"},
        )

    async def fake_create(*args, **kwargs):
        calls["create"] += 1

    async def fake_reserve(*args, **kwargs):
        calls["reserve"] += 1

    monkeypatch.setattr(routes.repo, "get_project", fake_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(routes.facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(routes.facemarket, "verify_license", deny)
    monkeypatch.setattr(routes.repo, "create_job", fake_create)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    patch_route_db(monkeypatch, routes)

    response = client.post(
        "/v1/projects/p1/editor:generate-image",
        headers=_auth(make_token),
        json={"mode": "new", "cutType": "styling", "modelId": MODEL_ID},
    )

    assert response.status_code == 409
    assert calls == {"create": 0, "reserve": 0}


def test_editor_product_cut_strips_real_model_before_facemarket_gate(
    client, make_token, monkeypatch
):
    client.app.state.settings = replace(
        client.app.state.settings,
        facemarket_enabled=True,
    )
    seen = {}

    async def fake_project(conn, user_id, project_id):
        return {"id": project_id, "facemarket_license_id": "stale"}

    async def fake_analysis(conn, project_id):
        return {"brandUseCategory": CATEGORY}

    async def forbidden_resolve(*_args, **_kwargs):
        raise AssertionError("product-only editor cuts must not enter FaceMarket gate")

    async def fake_create(conn, **kwargs):
        seen.update(kwargs)
        return {"id": "job-product"}, True

    async def fake_reserve(conn, user_id, amount):
        return 9

    monkeypatch.setattr(routes.repo, "get_project", fake_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(routes.facemarket, "resolve_model_license", forbidden_resolve)
    monkeypatch.setattr(routes.repo, "create_job", fake_create)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    patch_route_db(monkeypatch, routes)

    response = client.post(
        "/v1/projects/p1/editor:generate-image",
        headers=_auth(make_token),
        json={
            "mode": "new",
            "cutType": "product",
            "shot": "ghost",
            "modelId": MODEL_ID,
            "brandUseCategory": "정치·종교",
            "_facemarket": {"modelId": "attacker", "licenseId": "attacker"},
        },
    )

    assert response.status_code == 202, response.text
    assert seen["payload"] == {
        "mode": "new",
        "cutType": "product",
        "shot": "ghost",
    }


def test_editor_virtual_and_vary_strip_client_snapshot(
    client, make_token, monkeypatch
):
    client.app.state.settings = replace(
        client.app.state.settings,
        facemarket_enabled=True,
    )
    payloads = []

    async def fake_project(conn, user_id, project_id):
        return {"id": project_id, "facemarket_license_id": "stale"}

    async def fake_analysis(conn, project_id):
        return {"brandUseCategory": CATEGORY}

    async def fake_provenance(conn, user_id, asset_id):
        return {
            "real_derived": False,
            "facemarket": {"modelId": MODEL_ID, "licenseId": LICENSE_ID},
        }

    async def fake_create(conn, **kwargs):
        payloads.append(kwargs["payload"])
        return {"id": f"job-{len(payloads)}"}, True

    async def fake_reserve(conn, user_id, amount):
        return 9

    monkeypatch.setattr(routes.repo, "get_project", fake_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(
        routes.repo,
        "get_asset_facemarket_provenance",
        fake_provenance,
        raising=False,
    )
    monkeypatch.setattr(routes.repo, "create_job", fake_create)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    patch_route_db(monkeypatch, routes)

    common = {"_facemarket": {"modelId": "attacker", "licenseId": "attacker"}}
    virtual = client.post(
        "/v1/projects/p1/editor:generate-image",
        headers=_auth(make_token),
        json={"mode": "new", "cutType": "styling", "model_id": "mA", **common},
    )
    vary = client.post(
        "/v1/projects/p1/editor:generate-image",
        headers=_auth(make_token),
        json={
            "mode": "vary",
            "source": {
                "src": "/v1/assets/33333333-3333-3333-3333-333333333333/file"
            },
            **common,
        },
    )

    assert virtual.status_code == 202 and vary.status_code == 202
    assert payloads[0]["modelId"] == "mA" and "model_id" not in payloads[0]
    assert payloads[0]["brandUseCategory"] == CATEGORY
    assert all("_facemarket" not in payload for payload in payloads)


def test_patch_unknown_status_field_ignored_not_500(client, make_token):
    # status·adjustCount는 모델에 없어 무시 → 검증 통과 후 get_conn(풀 없음) → 503, 500 아님
    res = client.patch(
        "/v1/projects/any-id",
        headers=_auth(make_token),
        json={"status": "done", "adjustCount": 9},
    )
    assert res.status_code == 503  # db_unavailable (검증은 통과)
