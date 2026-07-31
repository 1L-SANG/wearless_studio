import asyncio
import hashlib
import json
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.agents import cut_generator
from app.agents import space_set_assets as sets


def _asset(variant, filename, *, release_id="test-release", sha256="a" * 64):
    return {
        "key": (
            "seed/genexamples/space-sets/v1/releases/"
            f"{release_id}/{variant}/{filename}"
        ),
        "sha256": sha256,
        "width": 1024,
        "height": 1365,
        "mime": "image/png",
    }


def _png_bytes(size=(4, 3)):
    output = BytesIO()
    Image.new("RGB", size, (240, 240, 240)).save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def space_registry(tmp_path, monkeypatch):
    registry = {
        "schemaVersion": 1,
        "releaseId": "test-release",
        "baseUrl": "https://images.example.test",
        "placeTypes": ["cafe-shop-interior"],
        "sets": [
            {
                "setId": "women_top_cafe_01",
                "setType": "styling",
                "placeType": "cafe-shop-interior",
                "gender": "women",
                "applicableClothingTypes": ["top"],
                "spaceVariation": "subtle",
                "platePolicy": "required",
                "representativePlate": _asset(
                    "plate", "women_top_cafe_01.png"
                ),
                "members": [
                    {
                        "exampleId": "ss_cafe_01_01",
                        "order": 1,
                        "cutType": "styling",
                        "shot": "full",
                        "direction": "front",
                        "all": _asset("all", "ss_cafe_01_01.png"),
                        "pose": _asset("pose", "ss_cafe_01_01.png"),
                    },
                    {
                        "exampleId": "ss_cafe_01_02",
                        "order": 2,
                        "cutType": "styling",
                        "shot": "medium",
                        "direction": "side",
                        "all": _asset("all", "ss_cafe_01_02.png"),
                        "pose": _asset("pose", "ss_cafe_01_02.png"),
                    },
                ],
            }
        ],
    }
    path = tmp_path / "space_set_assets.json"
    path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(sets, "_DEFAULT_SPACE_SET_ASSETS", str(path))
    sets.load_space_set_registry.cache_clear()
    yield registry
    sets.load_space_set_registry.cache_clear()


def _blocks():
    group_id = "ssg1__women_top_cafe_01__instance-123"
    return [
        {
            "id": "b1",
            "spaceGroupId": group_id,
            "spaceVariation": "subtle",
            "spaceSetMemberOrder": 1,
            "exampleId": "ss_cafe_01_01",
            "cutType": "styling",
            "shot": "full",
            "direction": "front",
        },
        {
            "id": "b2",
            "spaceGroupId": group_id,
            "spaceVariation": "subtle",
            "spaceSetMemberOrder": 2,
            "exampleId": "ss_cafe_01_02",
            "cutType": "styling",
            "shot": "medium",
            "direction": "side",
        },
    ]


def test_parse_group_id_accepts_only_the_published_namespace():
    assert sets.parse_space_set_group_id(None) is None
    for invalid in (
        "sg-legacy",
        "sgset__women_top_cafe_01__instance-123",
    ):
        with pytest.raises(sets.SpaceSetBindingError) as caught:
            sets.parse_space_set_group_id(invalid)
        assert caught.value.code == "invalid_space_set_group_id"
    assert sets.parse_space_set_group_id(
        "ssg1__women_top_cafe_01__instance-123"
    ) == ("women_top_cafe_01", "instance-123")
    with pytest.raises(sets.SpaceSetBindingError) as caught:
        sets.parse_space_set_group_id("ssg1__missing-instance")
    assert caught.value.code == "invalid_space_set_group_id"
    with pytest.raises(sets.SpaceSetBindingError):
        sets.parse_space_set_group_id(f"ssg1__{'a' * 201}__instance")
    with pytest.raises(sets.SpaceSetBindingError):
        sets.parse_space_set_group_id("ssg1__set__bad__instance")


def test_registry_rejects_men_dress_sets(space_registry):
    invalid = json.loads(json.dumps(space_registry))
    invalid["sets"][0]["gender"] = "men"
    invalid["sets"][0]["applicableClothingTypes"] = ["dress"]

    with pytest.raises(ValueError, match="space_set_registry_applicability_invalid"):
        sets.validate_space_set_registry_document(invalid)


def test_exact_space_set_binding_and_key_resolution(space_registry):
    blocks = _blocks()
    bindings = sets.bind_storyboard_space_sets(
        blocks, clothing_type="top", gender="women"
    )

    assert (
        bindings[id(blocks[0])]["poseReference"]["exampleId"]
        == "ss_cafe_01_01"
    )
    assert bindings[id(blocks[1])]["poseReference"]["source"] == "space-set"
    base_url, registry = sets.load_space_set_registry()
    assert (
        sets.resolve_asset_url(
            registry["women_top_cafe_01"]["representativePlate"], base_url
        )
        == (
            "https://images.example.test/seed/genexamples/space-sets/v1/"
            "releases/test-release/plate/women_top_cafe_01.png"
        )
    )


@pytest.mark.parametrize("scope", ["all", "pose"])
def test_resolve_set_member_as_standalone_generation_example(
    space_registry, scope
):
    block = {
        "exampleId": "ss_cafe_01_01",
        "cutType": "styling",
        "shot": "full",
        "direction": "front",
    }

    reference = sets.resolve_published_example_reference(
        block, clothing_type="top", gender="women", scope=scope
    )

    assert reference["exampleId"] == "ss_cafe_01_01"
    assert reference["scope"] == scope
    assert f"/{scope}/" in reference["asset"]["key"]


def test_standalone_all_keeps_the_current_block_direction(space_registry):
    reference = sets.resolve_published_example_reference(
        {
            "exampleId": "ss_cafe_01_01",
            "cutType": "styling",
            "shot": "full",
            "direction": "back",
        },
        clothing_type="top",
        gender="women",
        scope="all",
    )

    assert reference["exampleId"] == "ss_cafe_01_01"
    assert reference["scope"] == "all"


def test_standalone_pose_uses_the_current_block_framing(space_registry):
    reference = sets.resolve_published_example_reference(
        {
            "exampleId": "ss_cafe_01_01",
            "cutType": "styling",
            "shot": "medium",
            "direction": "front",
        },
        clothing_type="top",
        gender="women",
        scope="pose",
    )

    assert reference["exampleId"] == "ss_cafe_01_01"
    assert reference["scope"] == "pose"


@pytest.mark.parametrize(
    ("changes", "clothing_type", "gender", "scope", "error"),
    [
        ({}, "top", "women", "bg", "space_set_example_scope_invalid"),
        (
            {"exampleId": "ss_missing"},
            "top",
            "women",
            "all",
            "space_set_example_unavailable",
        ),
        (
            {"direction": "back"},
            "top",
            "women",
            "pose",
            "space_set_example_incompatible",
        ),
        ({}, "bottom", "women", "all", "space_set_example_incompatible"),
        ({}, "top", "men", "all", "space_set_example_incompatible"),
    ],
)
def test_standalone_set_member_reference_fails_closed(
    space_registry, changes, clothing_type, gender, scope, error
):
    block = {
        "exampleId": "ss_cafe_01_01",
        "cutType": "styling",
        "shot": "full",
        "direction": "front",
        **changes,
    }
    with pytest.raises(sets.SpaceSetBindingError) as caught:
        sets.resolve_published_example_reference(
            block, clothing_type=clothing_type, gender=gender, scope=scope
        )
    assert caught.value.code == error


@pytest.mark.parametrize(
    ("mutate", "clothing_type", "gender", "expected"),
    [
        (
            lambda blocks: blocks[1].update(exampleId="ss_missing_pose"),
            "top",
            "women",
            "space_set_pose_unavailable",
        ),
        (
            lambda blocks: blocks[1].update(shot="full"),
            "top",
            "women",
            "space_set_pose_incompatible",
        ),
        (lambda _blocks: None, "bottom", "women", "space_set_not_applicable"),
        (lambda _blocks: None, "top", "men", "space_set_gender_mismatch"),
    ],
)
def test_space_set_binding_fails_closed(
    space_registry, mutate, clothing_type, gender, expected
):
    blocks = _blocks()
    mutate(blocks)
    with pytest.raises(sets.SpaceSetBindingError) as caught:
        sets.bind_storyboard_space_sets(
            blocks, clothing_type=clothing_type, gender=gender
        )
    assert caught.value.code == expected


def test_space_set_binding_allows_pose_swap_add_and_drag_out(
    space_registry, monkeypatch
):
    blocks = _blocks()
    # Original tuple order is not immutable: swap each block to another compatible
    # published pose recipe, then leave only a singleton in the production run.
    blocks[0].update(
        exampleId="ss_cafe_01_02", shot="medium", direction="side"
    )
    blocks[1].update(
        exampleId="ss_cafe_01_01", shot="full", direction="front"
    )
    swapped = sets.bind_storyboard_space_sets(
        blocks, clothing_type="top", gender="women"
    )
    assert swapped[id(blocks[0])]["poseReference"]["exampleId"] == "ss_cafe_01_02"
    assert swapped[id(blocks[1])]["poseReference"]["exampleId"] == "ss_cafe_01_01"

    flat_example_id = "ex_horizon_women_top_full_01"
    monkeypatch.setattr(
        cut_generator,
        "load_example_asset_registry",
        lambda: (
            "https://images.example.test",
            {
                flat_example_id: {
                    "pose": "pose/flat.png",
                    "cutType": "horizon",
                    "shot": "full",
                    "direction": "front",
                    "gender": "women",
                    "applicableClothingTypes": ["top"],
                }
            },
        ),
    )
    added = {
            "id": "b3",
            "spaceGroupId": blocks[0]["spaceGroupId"],
            "spaceVariation": "subtle",
            "exampleId": flat_example_id,
        "cutType": "horizon",
        "shot": "full",
        "direction": "front",
    }
    blocks.append(added)
    with_added = sets.bind_storyboard_space_sets(
        blocks, clothing_type="top", gender="women"
    )
    assert with_added[id(added)]["poseReference"]["source"] == "flat"

    blocks[1].pop("spaceGroupId")
    blocks[2].pop("spaceGroupId")
    singleton = sets.bind_storyboard_space_sets(
        blocks, clothing_type="top", gender="women"
    )
    assert set(singleton) == {id(blocks[0])}
    dragged_pose = sets.resolve_published_pose_reference(
        blocks[1], clothing_type="top", gender="women"
    )
    assert dragged_pose["source"] == "space-set"
    assert dragged_pose["exampleId"] == "ss_cafe_01_01"


def test_space_set_binding_rejects_non_contiguous_run(space_registry):
    blocks = _blocks()
    blocks.insert(
        1,
        {
            "id": "unrelated",
            "source": "ai",
            "cutType": "product",
            "shot": "detail",
        },
    )

    with pytest.raises(sets.SpaceSetBindingError) as caught:
        sets.bind_storyboard_space_sets(
            blocks, clothing_type="top", gender="women"
        )

    assert caught.value.code == "space_set_members_not_contiguous"


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda registry: registry.update(schemaVersion=2),
            "space_set_registry_schema_invalid",
        ),
        (
            lambda registry: registry["sets"][0].update(
                setType="horizon-rotation"
            ),
            "space_set_registry_member_recipe_invalid",
        ),
        (
            lambda registry: registry["sets"][0].update(
                spaceVariation="varied"
            ),
            "space_set_registry_space_variation_invalid",
        ),
        (
            lambda registry: registry["sets"][0].update(
                applicableClothingTypes=["top", "outer"]
            ),
            "space_set_registry_applicability_invalid",
        ),
    ],
)
def test_registry_contract_mismatches_fail_closed(
    space_registry, mutate, expected
):
    mutate(space_registry)
    Path(sets._DEFAULT_SPACE_SET_ASSETS).write_text(
        json.dumps(space_registry), encoding="utf-8"
    )
    sets.load_space_set_registry.cache_clear()

    with pytest.raises(ValueError, match=expected):
        sets.load_space_set_registry()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda registry: registry["sets"][0]["representativePlate"].update(
            url="https://evil.example/plate.png"
        ),
        lambda registry: registry["sets"][0]["representativePlate"].update(
            key="https://evil.example/plate.png"
        ),
        lambda registry: registry["sets"][0]["representativePlate"].update(
            key=(
                "seed/genexamples/space-sets/v1/releases/"
                "test-release/plate/%2e%2e/secret.png"
            )
        ),
        lambda registry: registry["sets"][0]["representativePlate"].update(
            key=(
                "seed/genexamples/space-sets/v1/releases/"
                "bad__release/plate/x.png"
            )
        ),
        lambda registry: registry["sets"][0]["representativePlate"].update(
            key=(
                "seed/genexamples/space-sets/v1/releases/"
                "test-release/pose/not-a-plate.png"
            )
        ),
        lambda registry: registry["sets"][0].update(setId="set__ambiguous"),
        lambda registry: registry["sets"][0]["members"][0].update(
            exampleId="ss_bad__example"
        ),
    ],
)
def test_registry_rejects_ssrf_paths_and_ambiguous_ids(
    space_registry, mutate
):
    mutate(space_registry)
    Path(sets._DEFAULT_SPACE_SET_ASSETS).write_text(
        json.dumps(space_registry), encoding="utf-8"
    )
    sets.load_space_set_registry.cache_clear()

    with pytest.raises(ValueError):
        sets.load_space_set_registry()


def test_asset_fetch_checks_hash_dimensions_and_never_follows_redirects(
    space_registry, monkeypatch
):
    body = _png_bytes()
    expected = _asset(
        "pose",
        "ss_secure_pose.png",
        sha256=hashlib.sha256(body).hexdigest(),
    )
    expected.update(width=4, height=3)
    calls = []
    response_status = {"value": 200}

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url):
            calls.append(url)
            status = response_status["value"]
            return httpx.Response(
                status,
                headers={
                    "content-type": "image/png",
                    **(
                        {"location": "https://evil.example/stolen.png"}
                        if status == 302
                        else {}
                    ),
                },
                content=body if status == 200 else b"",
                request=httpx.Request("GET", url),
            )

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(sets.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(sets.asyncio, "sleep", no_sleep)

    loaded = asyncio.run(
        sets.load_space_set_image(None, expected, role="포즈")
    )
    assert loaded.data == body
    assert all(url.startswith("https://images.example.test/") for url in calls)

    bad_hash = {**expected, "sha256": "0" * 64}
    with pytest.raises(sets.SpaceSetBindingError):
        asyncio.run(sets.load_space_set_image(None, bad_hash, role="포즈"))

    bad_dimensions = {**expected, "width": 5}
    with pytest.raises(sets.SpaceSetBindingError):
        asyncio.run(
            sets.load_space_set_image(None, bad_dimensions, role="포즈")
        )

    response_status["value"] = 302
    calls.clear()
    with pytest.raises(sets.SpaceSetBindingError):
        asyncio.run(sets.load_space_set_image(None, expected, role="포즈"))
    assert len(calls) == 3
    assert all("evil.example" not in url for url in calls)


def test_non_release_group_is_rejected_even_with_an_empty_registry(
    tmp_path, monkeypatch
):
    path = tmp_path / "space_set_assets.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "releaseId": None,
                "baseUrl": None,
                "placeTypes": ["horizon-studio"],
                "sets": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sets, "_DEFAULT_SPACE_SET_ASSETS", str(path))
    sets.load_space_set_registry.cache_clear()

    with pytest.raises(sets.SpaceSetBindingError) as caught:
        sets.bind_storyboard_space_sets(
            [
                {
                    "spaceGroupId": "legacy-group",
                    "exampleId": "flat-example",
                }
            ],
            clothing_type="top",
            gender="women",
        )
    assert caught.value.code == "invalid_space_set_group_id"


@pytest.mark.parametrize("variation", [None, "varied", "fixed"])
def test_group_member_variation_must_match_the_published_set(
    space_registry, variation
):
    blocks = _blocks()
    if variation is None:
        blocks[0].pop("spaceVariation", None)
    else:
        blocks[0]["spaceVariation"] = variation

    with pytest.raises(sets.SpaceSetBindingError) as caught:
        sets.bind_storyboard_space_sets(
            blocks, clothing_type="top", gender="women"
        )

    assert caught.value.code == "space_set_variation_mismatch"


def test_horizon_sequence_may_publish_without_representative_plate(
    tmp_path, monkeypatch
):
    registry = {
        "schemaVersion": 1,
        "releaseId": "horizon-release",
        "baseUrl": "https://images.example.test",
        "placeTypes": ["horizon-studio"],
        "sets": [
            {
                "setId": "horizon-sequence-01",
                "setType": "horizon-sequence",
                "placeType": "horizon-studio",
                "gender": "women",
                "applicableClothingTypes": ["top"],
                "spaceVariation": "fixed",
                "platePolicy": "not-required",
                "representativePlate": None,
                "members": [
                    {
                        "exampleId": "ss_horizon_01",
                        "order": 1,
                        "cutType": "horizon",
                        "shot": "full",
                        "direction": "front",
                        "all": _asset(
                            "all",
                            "ss_horizon_01.png",
                            release_id="preserved-old-release",
                        ),
                        "pose": _asset(
                            "pose",
                            "ss_horizon_01.png",
                            release_id="preserved-old-release",
                        ),
                    },
                    {
                        "exampleId": "ss_horizon_02",
                        "order": 2,
                        "cutType": "horizon",
                        "shot": "full",
                        "direction": "side",
                        "all": _asset(
                            "all",
                            "ss_horizon_02.png",
                            release_id="preserved-old-release",
                        ),
                        "pose": _asset(
                            "pose",
                            "ss_horizon_02.png",
                            release_id="preserved-old-release",
                        ),
                    },
                ],
            }
        ],
    }
    path = tmp_path / "space_set_assets.json"
    path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(sets, "_DEFAULT_SPACE_SET_ASSETS", str(path))
    sets.load_space_set_registry.cache_clear()

    _base_url, loaded = sets.load_space_set_registry()

    assert loaded["horizon-sequence-01"]["representativePlate"] is None
    assert loaded["horizon-sequence-01"]["platePolicy"] == "not-required"
    assert "/releases/preserved-old-release/" in (
        loaded["horizon-sequence-01"]["members"][0]["pose"]["key"]
    )
