"""라우트 HTTP 레벨 회귀 테스트.

검증 실패는 get_conn(DB) 이전에 발생하므로 풀 없이 TestClient로 검증 가능.
explicit-null PATCH가 500이 아니라 422 봉투로 떨어지는지 — 에러 핸들러 직렬화 버그 가드.
"""

from dataclasses import replace

import pytest

import app.routes as routes
from app.agents import identity_scope

from conftest import patch_route_db


MODEL_ID = "11111111-1111-1111-1111-111111111111"
LICENSE_ID = "22222222-2222-2222-2222-222222222222"
CATEGORY = "일반 의류"


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


@pytest.mark.parametrize("plan, expected", [
    ("basic", "free"), ("plus", "free"), ("unknown", "free"),
    ("free", "free"), ("starter", "starter"), ("seller", "seller"), ("pro", "pro"),
])
def test_account_reads_retired_plans_as_free(client, make_token, monkeypatch, plan, expected):
    async def fake_get_account(conn, user_id):
        return {"name": "한지수", "avatar": "", "credits": 24, "plan": plan}

    monkeypatch.setattr(routes.repo, "get_account", fake_get_account)
    patch_route_db(monkeypatch, routes)

    response = client.get("/v1/me/account", headers=_auth(make_token))

    assert response.status_code == 200, response.text
    assert response.json()["plan"] == expected


@pytest.mark.parametrize("body", [
    {"mode": "new", "cutType": "mirror"},
    {"mode": "new", "cut_type": "mirror"},
    {"mode": "new", "cutType": "mirror", "contentRole": "coordination"},
    {"mode": "vary", "source": {"cutType": "mirror"}},
    {"mode": "vary", "source": {"cut_type": "mirror"}},
])
def test_editor_rejects_retired_cut_type_before_job_or_credit_reservation(client, make_token, body):
    response = client.post(
        "/v1/projects/p1/editor:generate-image", headers=_auth(make_token), json=body,
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "unknown_cut_type"


@pytest.mark.parametrize("hints", [{}, {"contentRole": "coordination"}, {"sectionRole": "styling"}])
def test_storyboard_rejects_retired_cut_type_before_storage(client, make_token, hints):
    response = client.put(
        "/v1/projects/p1/storyboard", headers=_auth(make_token),
        json=[{"id": "retired", "source": "ai", "cutType": "mirror", **hints}],
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "unknown_cut_type"


@pytest.mark.parametrize("enabled", [False, True])
def test_account_exposes_server_topup_bonus_flag(client, make_token, monkeypatch, enabled):
    client.app.state.settings = replace(client.app.state.settings, topup_bonus_enabled=enabled)

    async def fake_get_account(conn, user_id):
        return {"name": "한지수", "avatar": "", "credits": 24, "plan": "pro",
                "topup_bonus_enabled": not enabled}

    monkeypatch.setattr(routes.repo, "get_account", fake_get_account)
    patch_route_db(monkeypatch, routes)

    response = client.get("/v1/me/account", headers=_auth(make_token))

    assert response.status_code == 200, response.text
    assert response.json().get("topupBonusEnabled") is enabled


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

    async def fake_save_analysis(conn, project_id, analysis, **options):
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

    async def fake_save_analysis(conn, project_id, analysis, **options):
        seen["analysis"] = analysis
        return {"project_id": project_id, "payload": analysis}

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "get_product", fake_get_product)
    monkeypatch.setattr(routes.repo, "save_analysis", fake_save_analysis)
    patch_route_db(monkeypatch, routes)

    res = client.patch(
        "/v1/projects/p1/analysis",
        headers=_auth(make_token),
        json={"brandUseCategory": "  일반 의류  "},
    )

    assert res.status_code == 200, res.text
    assert seen["analysis"]["brandUseCategory"] == "일반 의류"
    assert res.json()["brandUseCategory"] == "일반 의류"


def test_save_analysis_rejects_unknown_brand_use_category_before_persistence(
    client, make_token, monkeypatch
):
    called = False

    async def fake_save_analysis(conn, project_id, analysis, **options):
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

    async def fake_save_analysis(conn, project_id, analysis, **options):
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

    async def fake_save_analysis(conn, project_id, analysis, **options):
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

    async def fake_save_analysis(conn, project_id, analysis, **options):
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
    }


def test_save_storyboard_persists_canonical_blocks(client, make_token, monkeypatch):
    seen = {}

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_save_storyboard(conn, user_id, project_id, blocks, base_revision=None):
        seen["blocks"] = blocks
        return 1

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


def test_save_storyboard_accepts_bottom_signature_without_flat_catalog_lookup(
    client, make_token, monkeypatch,
):
    seen = {}

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_product(conn, project_id):
        return {"clothingType": "bottom"}

    async def fake_analysis(conn, project_id):
        return {"targetGenders": ["women"], "subCategory": "mini_skirt"}

    async def fake_save(conn, user_id, project_id, blocks, base_revision=None):
        seen["blocks"] = blocks
        return 1

    def flat_registry_must_not_load():
        raise AssertionError("sig_* must stay outside the ordinary example catalog")

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "get_product", fake_product)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(routes.repo, "save_storyboard", fake_save)
    monkeypatch.setattr(
        routes.cut_generator, "load_example_asset_registry", flat_registry_must_not_load,
    )
    patch_route_db(monkeypatch, routes)

    res = client.put(
        "/v1/projects/p1/storyboard",
        headers=_auth(make_token),
        json=[{
            "id": "opening",
            "source": "ai",
            "sectionRole": "hooking",
            "contentRole": "hero",
            "cutType": "horizon",
            "direction": "front",
            "shot": "medium",
            "hookSlotRole": "signature",
            "exampleId": "sig_women_bottom_skirt_02",
            "exampleSelectionOrigin": "user",
        }],
    )

    assert res.status_code == 200, res.text
    assert seen["blocks"][0]["exampleId"] == "sig_women_bottom_skirt_02"
    assert seen["blocks"][0]["hookSlotRole"] == "signature"


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

    async def fake_save(conn, user_id, project_id, blocks, base_revision=None):
        seen["saved"] = blocks
        return 1

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

    def fake_verify(app, row, **kwargs):
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
    monkeypatch.setattr(routes.facemarket, "verify_license_local", fake_verify)
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
            "cutType": "horizon",
            model_key: MODEL_ID,
            "brandUseCategory": "속옷",
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
            "cut_type": "horizon",
        }

    async def fake_resolve(conn, model_id, **kwargs):
        assert model_id == MODEL_ID
        assert kwargs == {"license_id": LICENSE_ID}
        return {"id": LICENSE_ID, "model_id": MODEL_ID}

    def fake_verify(app, row, **kwargs):
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

    async def fake_asset(conn, uid, pid, aid):
        return {"id": aid, "source": "ai", "metadata": {"cut_type": "horizon"}}

    monkeypatch.setattr(routes.repo, "get_project_asset_for_user", fake_asset)
    monkeypatch.setattr(routes.repo, "get_project", fake_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(
        routes.repo,
        "get_asset_facemarket_provenance",
        fake_provenance,
        raising=False,
    )
    monkeypatch.setattr(routes.facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(routes.facemarket, "verify_license_local", fake_verify)
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
            "variationMode": "similar",
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
    assert seen["payload"]["source"]["cutType"] == "horizon"
    assert fence == ["lock", "closed"]


@pytest.mark.parametrize(
    ("trusted_cut_type", "body_patch"),
    [
        ("product", {}),
        ("horizon", {"changes": [{"type": "bg", "value": "거리"}]}),
        ("horizon", {"changes": [{"type": "other", "value": "거리 배경으로 이동"}]}),
        ("horizon", {"changes": [{"type": "pose", "value": "파리 거리에서 걷기"}]}),
        ("horizon", {"refBgAssetId": "44444444-4444-4444-4444-444444444444"}),
        ("horizon", {}),
        ("styling", {}),
    ],
)
def test_editor_real_vary_accepts_similar_and_detail_but_rejects_changes(
    client, make_token, monkeypatch, trusted_cut_type, body_patch
):
    """두 생성 경로 모두 실제 모델 출처와 라이선스 확인을 유지한다."""
    seen = {}
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
        return {
            "real_derived": True,
            "facemarket": {"modelId": MODEL_ID, "licenseId": LICENSE_ID},
            "cut_type": trusted_cut_type,
        }

    async def fake_resolve(conn, model_id, **kwargs):
        assert model_id == MODEL_ID and kwargs == {"license_id": LICENSE_ID}
        return {"id": LICENSE_ID, "model_id": MODEL_ID}

    def fake_verify(app, row, **kwargs):
        seen["verified"] = kwargs

    async def fake_create(conn, **kwargs):
        seen.update(kwargs)
        return {"id": "job-vary-real"}, True

    async def fake_reserve(conn, user_id, amount):
        return 9

    async def fake_lock(conn):
        return None

    async def fake_closed(conn):
        return False

    async def fake_asset(conn, uid, pid, aid):
        metadata = {"cut_type": trusted_cut_type}
        if trusted_cut_type == "product":
            metadata["detail_recipe"] = {"version": 1}
        return {"id": aid, "source": "ai", "metadata": metadata}

    monkeypatch.setattr(routes.repo, "get_project_asset_for_user", fake_asset)
    monkeypatch.setattr(routes.repo, "get_project", fake_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(routes.repo, "get_asset_facemarket_provenance", fake_provenance)
    monkeypatch.setattr(routes.facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(routes.facemarket, "verify_license_local", fake_verify)
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
            **({} if trusted_cut_type == "product" else {"variationMode": "similar"}),
            "source": {
                "src": f"/v1/assets/{source_asset_id}/file",
                "cutType": "horizon",
            },
            **body_patch,
        },
    )

    if body_patch:
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_similar_cut"
        assert "payload" not in seen and "verified" not in seen
        return
    assert response.status_code == 202, response.text
    assert seen["payload"]["_facemarket"] == {
        "modelId": MODEL_ID,
        "licenseId": LICENSE_ID,
    }
    # 컷 종류는 클라이언트가 말한 "horizon" 이 아니라 원장이 말한 값이다.
    assert seen["payload"]["source"]["cutType"] == trusted_cut_type
    assert seen["verified"] == {"model_id": MODEL_ID, "brand_use_category": CATEGORY}


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

    async def fake_asset(conn, uid, pid, aid):
        return {"id": aid, "source": "ai", "metadata": {"cut_type": "horizon"}}

    monkeypatch.setattr(routes.repo, "get_project_asset_for_user", fake_asset)
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
            "variationMode": "similar",
            "source": {"src": f"/v1/assets/{source_asset_id}/file"},
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "model_unavailable"
    assert calls == {"create": 0, "reserve": 0}


def test_editor_new_real_studio_cut_is_accepted(
    client, make_token, monkeypatch
):
    """실제 모델로 **스튜디오 컷**은 새로 만들 수 있다.

    실제 모델이 만들 수 있는 섹션은 identity_scope.REAL_ALLOWED_SECTION_ROLES 다(2026-09-14 studio,
    2026-09-25 styling, 2026-09-26 hooking, 2026-09-28 product 추가 — 이제 막힌 섹션이 없다).
    스타일링·첫 장면·제품 섹션 컷은 아래 test_editor_new_real_styling_and_hooking_cuts_are_accepted,
    막는 장치는 정책을 좁힌 test_editor_new_real_product_section_cut_is_refused 가 잠근다.
    """
    client.app.state.settings = replace(
        client.app.state.settings,
        facemarket_enabled=True,
    )
    seen = {}

    async def fake_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_analysis(conn, project_id):
        return {"brandUseCategory": CATEGORY}

    async def fake_resolve(conn, model_id, **kwargs):
        assert model_id == MODEL_ID
        return {"id": LICENSE_ID, "model_id": MODEL_ID}

    def fake_verify(app, row, **kwargs):
        seen["verified"] = kwargs

    async def fake_create(conn, **kwargs):
        seen.update(kwargs)
        return {"id": "job-new-styling"}, True

    async def fake_reserve(conn, user_id, amount):
        return 9

    async def fake_lock(conn):
        return None

    async def fake_closed(conn):
        return False

    monkeypatch.setattr(routes.repo, "get_project", fake_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(routes.facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(routes.facemarket, "verify_license_local", fake_verify)
    monkeypatch.setattr(routes.repo, "create_job", fake_create)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    monkeypatch.setattr(routes.repo, "lock_facemarket_writer_boundary", fake_lock)
    monkeypatch.setattr(routes.repo, "facemarket_writer_boundary_closed", fake_closed)
    patch_route_db(monkeypatch, routes)

    response = client.post(
        "/v1/projects/p1/editor:generate-image",
        headers=_auth(make_token),
        json={"mode": "new", "cutType": "horizon", "modelId": MODEL_ID},
    )

    assert response.status_code == 202, response.text
    assert seen["payload"]["modelId"] == MODEL_ID
    assert seen["payload"]["cutType"] == "horizon"
    assert seen["payload"]["brandUseCategory"] == CATEGORY
    # 라이선스 확인은 컷 종류와 무관하게 그대로 붙는다.
    assert seen["verified"] == {"model_id": MODEL_ID, "brand_use_category": CATEGORY}
    assert seen["payload"]["_facemarket"] == {"modelId": MODEL_ID, "licenseId": LICENSE_ID}


@pytest.mark.parametrize("cut_type,extra", [
    pytest.param("styling", {}, id="styling"),
    # 2026-09-26: 첫 장면(hooking) 섹션도 열렸다 — hero 컷이 같은 길로 간다.
    pytest.param("styling", {"contentRole": "hero"}, id="hooking-hero"),
    # 2026-09-28: product 섹션도 열렸다 — 그 섹션에 착용 컷을 청하면 스타일링과 똑같이
    # 실제 얼굴·라이선스 확인으로 간다(제품컷 cutType=product 는 얼굴 없이 간다 — 아래
    # test_editor_product_cut_strips_real_model_before_facemarket_gate).
    pytest.param("styling", {"contentRole": "productOverview"}, id="product-section-worn"),
])
def test_editor_new_real_styling_and_hooking_cuts_are_accepted(
    client, make_token, monkeypatch, cut_type, extra,
):
    """실제 모델 + styling 섹션(스타일링)·hooking 섹션(첫 장면) = 스튜디오 컷과 똑같이
    실제 얼굴로 만든다.

    styling 은 2026-09-25, hooking 은 2026-09-26 사용자 결정 — 그 전에는 409
    real_model_studio_only 로 거부했다.
    """
    client.app.state.settings = replace(
        client.app.state.settings,
        facemarket_enabled=True,
    )
    seen = {}

    async def fake_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_analysis(conn, project_id):
        return {"brandUseCategory": CATEGORY}

    async def fake_resolve(conn, model_id, **kwargs):
        assert model_id == MODEL_ID
        return {"id": LICENSE_ID, "model_id": MODEL_ID}

    def fake_verify(app, row, **kwargs):
        seen["verified"] = kwargs

    async def fake_create(conn, **kwargs):
        seen.update(kwargs)
        return {"id": "job-new-styling"}, True

    async def fake_reserve(conn, user_id, amount):
        seen["reserved"] = amount
        return 9

    async def fake_lock(conn):
        return None

    async def fake_closed(conn):
        return False

    monkeypatch.setattr(routes.repo, "get_project", fake_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(routes.facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(routes.facemarket, "verify_license_local", fake_verify)
    monkeypatch.setattr(routes.repo, "create_job", fake_create)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    monkeypatch.setattr(routes.repo, "lock_facemarket_writer_boundary", fake_lock)
    monkeypatch.setattr(routes.repo, "facemarket_writer_boundary_closed", fake_closed)
    patch_route_db(monkeypatch, routes)

    response = client.post(
        "/v1/projects/p1/editor:generate-image",
        headers=_auth(make_token),
        json={"mode": "new", "cutType": cut_type, "modelId": MODEL_ID, **extra},
    )

    assert response.status_code == 202, response.text
    assert seen["payload"]["modelId"] == MODEL_ID
    assert seen["payload"]["cutType"] == cut_type
    assert seen["payload"]["brandUseCategory"] == CATEGORY
    assert seen["verified"] == {"model_id": MODEL_ID, "brand_use_category": CATEGORY}
    assert seen["payload"]["_facemarket"] == {"modelId": MODEL_ID, "licenseId": LICENSE_ID}
    assert seen["reserved"] == client.app.state.settings.credit_cost_editor_image


def test_editor_new_real_product_section_cut_is_refused(client, make_token, monkeypatch):
    """실제 모델 + 막힌 섹션 = 생성 전에 거부. 잡도 예약도 없다(과금 0).

    2026-09-28 product 까지 열려 기본 정책으로 막힌 섹션은 없다. 장치(에디터 라우트 가드)는
    남아 있으니 정책을 09-26 값(product 제외)으로 좁혀 지킨다. 제품컷(cutType=product)은
    에디터에서 실제 모델을 떼고 진행하므로(test_editor_product_cut_strips_real_model_before_facemarket_gate)
    거부 예시는 제품 섹션 역할(productOverview)에 착용 컷(styling)을 청한 경우다.
    """
    monkeypatch.setattr(identity_scope, "REAL_ALLOWED_SECTION_ROLES",
                        ("hooking", "styling", "studio"))
    client.app.state.settings = replace(client.app.state.settings, facemarket_enabled=True)
    calls = {"create": 0, "reserve": 0}

    async def fake_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_analysis(conn, project_id):
        return {"brandUseCategory": CATEGORY}

    async def forbidden_create(conn, **kwargs):
        calls["create"] += 1
        raise AssertionError("범위 밖이면 잡을 만들지 않는다")

    async def forbidden_reserve(conn, user_id, amount):
        calls["reserve"] += 1
        raise AssertionError("범위 밖이면 크레딧을 잡지 않는다")

    async def forbidden_resolve(*args, **kwargs):
        raise AssertionError("범위 밖이면 라이선스도 읽지 않는다")

    monkeypatch.setattr(routes.repo, "get_project", fake_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(routes.repo, "create_job", forbidden_create)
    monkeypatch.setattr(routes.repo, "reserve_credits", forbidden_reserve)
    monkeypatch.setattr(routes.facemarket, "resolve_model_license", forbidden_resolve)
    patch_route_db(monkeypatch, routes)

    response = client.post(
        "/v1/projects/p1/editor:generate-image",
        headers=_auth(make_token),
        json={"mode": "new", "contentRole": "productOverview", "cutType": "styling",
              "modelId": MODEL_ID},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "real_model_studio_only"
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
            "brandUseCategory": "속옷",
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

    async def fake_asset(conn, uid, pid, aid):
        return {"id": aid, "source": "ai", "metadata": {"cut_type": "horizon"}}

    monkeypatch.setattr(routes.repo, "get_project_asset_for_user", fake_asset)
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
            "variationMode": "similar",
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


@pytest.mark.parametrize("hints", [{}, {"contentRole": "coordination"}, {"sectionRole": "styling"}])
def test_detail_request_rejects_saved_retired_cut_before_job_or_credit(client, make_token, monkeypatch, hints):
    from fastapi.testclient import TestClient
    from unittest.mock import AsyncMock

    monkeypatch.setattr(routes.repo, "get_project", AsyncMock(return_value={"id": "p1"}))
    monkeypatch.setattr(routes.repo, "get_analysis", AsyncMock(return_value={}))
    monkeypatch.setattr(routes.repo, "get_product", AsyncMock(return_value={"clothingType": "top"}))
    monkeypatch.setattr(routes.repo, "get_editor_blocks", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes.repo, "get_storyboard", AsyncMock(return_value=[
        {"id": "retired", "source": "ai", "cutType": "mirror", **hints},
    ]))
    create = AsyncMock()
    reserve = AsyncMock()
    monkeypatch.setattr(routes.repo, "create_job", create)
    monkeypatch.setattr(routes.repo, "reserve_credits", reserve)
    patch_route_db(monkeypatch, routes)
    response = TestClient(client.app, raise_server_exceptions=False).post(
        "/v1/projects/p1/detail-page:generate", headers=_auth(make_token),
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "unknown_cut_type"
    create.assert_not_awaited()
    reserve.assert_not_awaited()


def test_garment_color_request_rejects_saved_retired_cut_without_observation(client, make_token, monkeypatch):
    from fastapi.testclient import TestClient
    from unittest.mock import AsyncMock

    from app.services import garment_color_measure

    monkeypatch.setattr(routes.repo, "get_project", AsyncMock(return_value={"id": "p1"}))
    monkeypatch.setattr(routes.repo, "get_analysis", AsyncMock(return_value={}))
    monkeypatch.setattr(routes.repo, "get_product", AsyncMock(return_value={"clothingType": "top"}))
    monkeypatch.setattr(routes.repo, "get_storyboard", AsyncMock(return_value=[
        {"id": "retired", "source": "ai", "cutType": "mirror"},
    ]))
    observe = AsyncMock(side_effect=AssertionError("must reject before paid observation"))
    monkeypatch.setattr(garment_color_measure.garment_color_observer, "observe", observe)
    from conftest import FakePool
    client.app.state.pool = FakePool()
    patch_route_db(monkeypatch, routes)
    response = TestClient(client.app, raise_server_exceptions=False).post(
        "/v1/projects/p1/analysis/garment-colors:measure", headers=_auth(make_token),
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "unknown_cut_type"
    observe.assert_not_awaited()
