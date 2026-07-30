"""라우트 HTTP 레벨 회귀 테스트.

검증 실패는 get_conn(DB) 이전에 발생하므로 풀 없이 TestClient로 검증 가능.
explicit-null PATCH가 500이 아니라 422 봉투로 떨어지는지 — 에러 핸들러 직렬화 버그 가드.
"""

import app.routes as routes

from conftest import patch_route_db


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


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
        "benefit", "styling", "full",
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


def test_patch_unknown_status_field_ignored_not_500(client, make_token):
    # status·adjustCount는 모델에 없어 무시 → 검증 통과 후 get_conn(풀 없음) → 503, 500 아님
    res = client.patch(
        "/v1/projects/any-id",
        headers=_auth(make_token),
        json={"status": "done", "adjustCount": 9},
    )
    assert res.status_code == 503  # db_unavailable (검증은 통과)
