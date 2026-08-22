import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.workers import detail_page_job as dpj
from app.workers import editor_image_job as eij
from conftest import FakeR2, fake_worker_app, make_settings, worker_job


REAL_MODEL_ID = "11111111-1111-1111-1111-111111111111"
REAL_LICENSE_ID = "22222222-2222-2222-2222-222222222222"
REAL_ENROLLMENT_ID = "33333333-3333-3333-3333-333333333333"
REAL_CATEGORY = "일반 여성 의류"


class _TrackingR2(FakeR2):
    def __init__(self, *, fail_delete=False):
        self.reads = []
        self.puts = []
        self.caches = []
        self.deletes = []
        self.objects = set()
        self.fail_delete = fail_delete

    def get_bytes(self, key):
        self.reads.append(key)
        return key.encode()

    def put_bytes(self, key, data, mime, cache=None):
        self.puts.append(key)
        self.caches.append(cache)
        self.objects.add(key)

    def delete(self, key):
        self.deletes.append(key)
        if self.fail_delete:
            raise RuntimeError("delete failed")
        self.objects.discard(key)

    def head(self, key):
        return {"size": 1, "mime": "image/png"} if key in self.objects else None


def _patch_detail_terminal(monkeypatch, captured):
    async def fake_gen_cuts(app, job, prepared, product, analysis):
        captured["prepared"] = prepared
        results = [
            {"blockId": item[0]["id"], "imageUrl": f"/{item[0]['id']}"}
            for item in prepared
            if item[1]
        ]
        assets = [{"key": f"out/{item['blockId']}"} for item in results]
        return results, assets, 0, [], [], None, []

    def fake_assemble(*_args, **_kwargs):
        return []

    async def fake_finalize(conn, **kwargs):
        captured["finalize"] = kwargs
        return {"editor_blocks": [], "available": 99}

    async def fake_emit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(dpj, "_gen_cuts", fake_gen_cuts)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)


def _prepared_by_id(captured):
    return {item[0]["id"]: item for item in captured["prepared"]}


def test_example_repeat_indexes_are_section_local_and_ignore_client_runtime_value(
    monkeypatch,
):
    monkeypatch.setattr(
        dpj.cut_generator,
        "load_example_asset_registry",
        lambda: (None, {}),
    )
    # 2026-08-14 오너 규칙: 변주 지수는 색상 단위 — 같은 색 반복(복제)은 0으로 유지되고
    # 다른 색상으로 반복할 때만 1, 2, …로 오른다.
    blocks = [
        {"id": "a1", "source": "ai", "sectionId": "section-a", "sectionRole": "studio",
         "cutType": "horizon", "shot": "full", "direction": "front", "pose": "auto",
         "exampleId": "same", "refScope": "all", "_exampleRepeatIndex": 99},
        {"id": "a2-same-color", "source": "ai", "sectionId": "section-a",
         "sectionRole": "studio", "cutType": "horizon", "shot": "medium",
         "direction": "front", "pose": "auto", "exampleId": "same", "refScope": "all"},
        {"id": "a3-ivory", "source": "ai", "sectionId": "section-a", "sectionRole": "studio",
         "cutType": "horizon", "shot": "full", "direction": "front", "pose": "auto",
         "exampleId": "same", "colorId": "ivory", "refScope": "all"},
        {"id": "a4-sky", "source": "ai", "sectionId": "section-a", "sectionRole": "studio",
         "cutType": "horizon", "shot": "full", "direction": "front", "pose": "auto",
         "exampleId": "same", "colorId": "sky", "refScope": "all"},
        {"id": "a5-ivory-again", "source": "ai", "sectionId": "section-a",
         "sectionRole": "studio", "cutType": "horizon", "shot": "full",
         "direction": "front", "pose": "auto", "exampleId": "same", "colorId": "ivory",
         "refScope": "all"},
        {"id": "other-example", "source": "ai", "sectionId": "section-a",
         "sectionRole": "studio", "cutType": "horizon", "shot": "full",
         "direction": "front", "pose": "auto", "exampleId": "other", "refScope": "all"},
        {"id": "other-section", "source": "ai", "sectionId": "section-b",
         "sectionRole": "studio", "cutType": "horizon", "shot": "full",
         "direction": "front", "pose": "auto", "exampleId": "same", "refScope": "all"},
        {"id": "explicit", "source": "ai", "sectionId": "section-a", "sectionRole": "studio",
         "cutType": "horizon", "shot": "full", "direction": "front", "pose": "walking",
         "exampleId": "same", "refScope": "all"},
        {"id": "pose-scope", "source": "ai", "sectionId": "section-a", "sectionRole": "studio",
         "cutType": "horizon", "shot": "full", "direction": "front", "pose": "auto",
         "exampleId": "same", "refScope": "pose"},
        {"id": "space-set", "source": "ai", "sectionId": "section-a", "sectionRole": "studio",
         "cutType": "horizon", "shot": "full", "direction": "front", "pose": "auto",
         "exampleId": "same", "spaceGroupId": "set", "refScope": "all"},
    ]

    assert dpj._example_repeat_indexes(blocks, "top") == [
        0, 0, 1, 2, 1, 0, 0, None, None, None,
    ]


def test_duplicate_source_indexes_fold_identical_specs_only(monkeypatch):
    monkeypatch.setattr(
        dpj.cut_generator,
        "load_example_asset_registry",
        lambda: (None, {}),
    )
    base = {"source": "ai", "sectionId": "section-a", "sectionRole": "studio",
            "cutType": "horizon", "shot": "full", "direction": "front", "pose": "auto",
            "exampleId": "same", "refScope": "all"}
    blocks = [
        {**base, "id": "original"},
        # 같은 설정 복제(색상까지 동일) → 원본 0번을 가리킨다: 1장만 생성해 복사.
        {**base, "id": "copy"},
        # 색상이 다르면 복제가 아니다 — 별도 생성(변주 대상).
        {**base, "id": "ivory", "colorId": "ivory"},
        # 샷이 다르면 다른 컷이다.
        {**base, "id": "medium", "shot": "medium"},
        # 공간 세트 멤버는 접지 않는다.
        {**base, "id": "in-set", "spaceGroupId": "set"},
    ]

    assert dpj._duplicate_source_indexes(blocks, "top") == [
        None, 0, None, None, None,
    ]


def test_example_repeat_indexes_exclude_direction_incompatible_reference(monkeypatch):
    monkeypatch.setattr(
        dpj.cut_generator,
        "load_example_asset_registry",
        lambda: (None, {"front-example": {
            "all": "unused.png", "cutType": "horizon", "direction": "front",
        }}),
    )
    blocks = [
        {"id": "side", "source": "ai", "sectionId": "section-a", "sectionRole": "studio",
         "cutType": "horizon", "shot": "full", "direction": "side", "pose": "auto",
         "exampleId": "front-example", "refScope": "all"},
        {"id": "front", "source": "ai", "sectionId": "section-a", "sectionRole": "studio",
         "cutType": "horizon", "shot": "full", "direction": "front", "pose": "auto",
         "exampleId": "front-example", "refScope": "all"},
    ]

    assert dpj._example_repeat_indexes(blocks, "top") == [None, 0]


def test_detail_product_prunes_mannequin_model_matching_and_owned_mood(monkeypatch):
    captured = {}

    async def fake_project(conn, uid, pid):
        return {"copywriting": False, "selected_mannequin_id": "candidate-1"}

    async def fake_storyboard(conn, pid):
        return [{
            "id": "product",
            "source": "ai",
            "cutType": "product",
            "shot": "ghost",
            "matchIds": ["match-1"],
            "refAssetIds": ["mood"],
            "exampleId": "example-all",
            "refScope": "all",
        }]

    async def fake_product(conn, pid):
        return {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "product"}]}]}

    async def fake_analysis(conn, pid):
        return {"selectedModelId": "mA"}

    async def fake_mannequins(conn, uid, pid):
        return [{"candidate": "candidate", "version": "1", "asset_id": "mannequin"}]

    async def fake_asset(conn, uid, asset_id):
        return {"id": asset_id, "r2_key": f"k/{asset_id}", "mime_type": "image/png"}

    async def fail_matching(*_args, **_kwargs):
        raise AssertionError("product cut must not resolve matching input")

    def fail_model(*_args, **_kwargs):
        raise AssertionError("product cut must not resolve virtual model input")

    async def fake_example(settings, example_id, scope="all", clothing_type=None):
        return dpj.InlineImage("image/png", b"example:all")

    monkeypatch.setattr(dpj.repo, "get_project", fake_project)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_storyboard)
    monkeypatch.setattr(dpj.repo, "get_product", fake_product)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "list_mannequin_cuts", fake_mannequins)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.repo, "get_matching_item_asset", fail_matching)
    monkeypatch.setattr(dpj.cut_generator, "resolve_virtual_model_assets", fail_model)
    monkeypatch.setattr(dpj.cut_generator, "example_asset_status", lambda *_args: "available")
    monkeypatch.setattr(dpj.cut_generator, "load_example_image", fake_example)
    _patch_detail_terminal(monkeypatch, captured)

    r2 = _TrackingR2()
    app = fake_worker_app(
        make_settings(
            gemini_api_key="x",
            r2_bucket="b",
            detailpage_fallback_model_id="",
            facemarket_enabled=True,
        ),
        r2=r2,
    )
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    item = _prepared_by_id(captured)["product"]
    assert [image.data for image in item[1]] == [b"k/product", b"example:all"]
    assert item[2].splitlines() == [
        "1. PRODUCT — front view of the garment",
        "2. EXAMPLE REFERENCE (scope: all) — source of background, lighting, mood, "
        "framing and composition; never copy its garments, shoes, accessories, person, "
        "model identity or pose; the example has ZERO authority over facial identity or "
        "facial features, and ZERO authority over body morphology: height, head-to-body "
        "ratio, shoulder width and build, torso length and build, waist shape, pelvis and "
        "hip width, or limb proportions",
    ]
    assert r2.reads == ["k/product"]


def test_detail_non_base_color_prunes_base_color_mannequin(monkeypatch):
    captured = {}

    async def fake_project(conn, uid, pid):
        return {"copywriting": False, "selected_mannequin_id": "A-1"}

    async def fake_storyboard(conn, pid):
        common = {
            "source": "ai", "cutType": "styling", "shot": "full",
            "direction": "front", "faceExposure": "same",
        }
        return [
            {**common, "id": "base", "colorId": "base-color"},
            {**common, "id": "other", "colorId": "other-color"},
        ]

    async def fake_product(conn, pid):
        return {
            "clothingType": "top",
            "colors": [
                {"id": "base-color", "isBase": True,
                 "images": [{"slot": "Front", "id": "base-product"}]},
                {"id": "other-color", "isBase": False,
                 "images": [{"slot": "Front", "id": "other-product"}]},
            ],
        }

    async def fake_analysis(conn, pid):
        return {}

    async def fake_mannequins(conn, uid, pid):
        return [{
            "candidate": "A",
            "version": 1,
            "asset_id": "mannequin",
            "active_asset_id": "tone-mannequin",
        }]

    async def fake_asset(conn, uid, asset_id):
        return {"id": asset_id, "r2_key": f"k/{asset_id}", "mime_type": "image/png"}

    monkeypatch.setattr(dpj.repo, "get_project", fake_project)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_storyboard)
    monkeypatch.setattr(dpj.repo, "get_product", fake_product)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "list_mannequin_cuts", fake_mannequins)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    _patch_detail_terminal(monkeypatch, captured)

    app = fake_worker_app(
        make_settings(gemini_api_key="x", r2_bucket="b"),
        r2=_TrackingR2(),
    )
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=2)))

    prepared = _prepared_by_id(captured)
    assert [image.data for image in prepared["base"][1]] == [
        b"k/tone-mannequin", b"k/base-product",
    ]
    assert [image.data for image in prepared["other"][1]] == [b"k/other-product"]
    assert "mannequin" in prepared["base"][2].lower()
    assert "mannequin" not in prepared["other"][2].lower()


def test_detail_prunes_mood_for_all_and_bg_but_keeps_pose_order(monkeypatch):
    captured = {}
    blocks = [
        {"id": scope, "source": "ai", "cutType": "styling", "shot": "full",
         "refAssetIds": ["mood"], "exampleId": f"example-{scope}", "refScope": scope,
         "pose": "walk" if scope == "all" else "auto"}
        for scope in ("all", "bg", "pose")
    ]

    async def fake_project(conn, uid, pid):
        return {"copywriting": False}

    async def fake_storyboard(conn, pid):
        return blocks

    async def fake_product(conn, pid):
        return {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "product"}]}]}

    async def fake_analysis(conn, pid):
        return {}

    async def fake_asset(conn, uid, asset_id):
        return {"id": asset_id, "r2_key": f"k/{asset_id}", "mime_type": "image/png"}

    async def fake_example(settings, example_id, scope="all", clothing_type=None):
        return dpj.InlineImage("image/png", f"example:{scope}".encode())

    monkeypatch.setattr(dpj.repo, "get_project", fake_project)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_storyboard)
    monkeypatch.setattr(dpj.repo, "get_product", fake_product)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "example_asset_status", lambda *_args: "available")
    monkeypatch.setattr(dpj.cut_generator, "pose_direction_compatible", lambda *_args: True)
    monkeypatch.setattr(dpj.cut_generator, "load_example_image", fake_example)
    _patch_detail_terminal(monkeypatch, captured)

    app = fake_worker_app(
        make_settings(gemini_api_key="x", r2_bucket="b", genexample_bg_enabled=True),
        r2=_TrackingR2(),
    )
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=3)))

    prepared = _prepared_by_id(captured)
    assert [image.data for image in prepared["all"][1]] == [b"k/product", b"example:all"]
    assert [image.data for image in prepared["bg"][1]] == [b"example:bg", b"k/product"]
    assert [image.data for image in prepared["pose"][1]] == [
        b"k/product", b"k/mood", b"example:pose",
    ]
    assert "MOOD —" not in prepared["all"][2]
    assert "MOOD —" not in prepared["bg"][2]
    assert prepared["pose"][2].count("MOOD —") == 1


def test_detail_space_binding_prunes_mood_with_or_without_plate(monkeypatch):
    captured = {}
    blocks = [
        {"id": "with-plate", "source": "ai", "cutType": "styling", "shot": "full",
         "refAssetIds": ["mood"], "exampleId": "pose-1", "spaceGroupId": "group-1"},
        {"id": "without-plate", "source": "ai", "cutType": "horizon", "shot": "full",
         "refAssetIds": ["mood"], "exampleId": "pose-2", "spaceGroupId": "group-2"},
    ]

    async def fake_project(conn, uid, pid):
        return {"copywriting": False}

    async def fake_storyboard(conn, pid):
        return blocks

    async def fake_product(conn, pid):
        return {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "product"}]}]}

    async def fake_analysis(conn, pid):
        return {"targetGenders": ["women"]}

    async def fake_asset(conn, uid, asset_id):
        return {"id": asset_id, "r2_key": f"k/{asset_id}", "mime_type": "image/png"}

    def fake_bind(storyboard, *, clothing_type, gender):
        out = {}
        for block in storyboard[:2]:
            has_plate = block["id"] == "with-plate"
            out[id(block)] = {
                "set": {
                    "setId": block["id"],
                    "spaceVariation": "subtle",
                    "representativePlate": {"key": "plate"} if has_plate else None,
                },
                "poseReference": {
                    "source": "space-set",
                    "exampleId": block["exampleId"],
                    "asset": {"key": block["exampleId"]},
                },
            }
        return out

    async def fake_set_image(settings, asset, *, role):
        return dpj.InlineImage("image/png", asset["key"].encode())

    monkeypatch.setattr(dpj.repo, "get_project", fake_project)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_storyboard)
    monkeypatch.setattr(dpj.repo, "get_product", fake_product)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.space_set_assets, "parse_space_set_group_id", lambda *_args: None)
    monkeypatch.setattr(dpj.space_set_assets, "bind_storyboard_space_sets", fake_bind)
    monkeypatch.setattr(dpj.space_set_assets, "load_space_set_image", fake_set_image)
    _patch_detail_terminal(monkeypatch, captured)

    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"), r2=_TrackingR2())
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=2)))

    prepared = _prepared_by_id(captured)
    assert [image.data for image in prepared["with-plate"][1]] == [
        b"k/product", b"plate", b"pose-1",
    ]
    assert [image.data for image in prepared["without-plate"][1]] == [
        b"k/product", b"pose-2",
    ]
    assert "MOOD —" not in prepared["with-plate"][2]
    assert "MOOD —" not in prepared["without-plate"][2]


def test_detail_selected_all_example_load_failure_fails_closed(monkeypatch):
    captured = {}

    async def fake_project(conn, uid, pid):
        return {"copywriting": False}

    async def fake_storyboard(conn, pid):
        return [{
            "id": "all-missing",
            "source": "ai",
            "cutType": "styling",
            "shot": "full",
            "refAssetIds": ["mood"],
            "exampleId": "example-all",
            "refScope": "all",
        }]

    async def fake_product(conn, pid):
        return {
            "clothingType": "top",
            "colors": [{
                "isBase": True,
                "images": [{"slot": "Front", "id": "product"}],
            }],
        }

    async def fake_analysis(conn, pid):
        return {}

    async def fake_asset(conn, uid, asset_id):
        return {"id": asset_id, "r2_key": f"k/{asset_id}", "mime_type": "image/png"}

    async def fake_example_none(settings, example_id, scope="all", clothing_type=None):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_project)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_storyboard)
    monkeypatch.setattr(dpj.repo, "get_product", fake_product)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "example_asset_status", lambda *_args: "available")
    monkeypatch.setattr(dpj.cut_generator, "load_example_image", fake_example_none)
    _patch_detail_terminal(monkeypatch, captured)

    app = fake_worker_app(
        make_settings(gemini_api_key="x", r2_bucket="b"),
        r2=_TrackingR2(),
    )
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    item = _prepared_by_id(captured)["all-missing"]
    assert item[1] == []
    assert "MOOD —" not in item[2]


def _patch_editor_common(monkeypatch, captured):
    async def fake_product(conn, pid):
        return {"clothingType": "top", "colors": [{
            "isBase": True,
            "images": [{"slot": "Front", "id": "product"}],
        }]}

    async def fake_analysis(conn, pid):
        return {"targetGenders": ["women"]}

    async def fake_asset(conn, uid, asset_id):
        return {"id": asset_id, "r2_key": f"k/{asset_id}", "mime_type": "image/png"}

    async def fake_generate(
        settings, gemini, cut_spec, product, images, *, analysis=None, manifest=None,
        has_face=False,
    ):
        captured.setdefault("generations", []).append({
            "cutType": cut_spec["cutType"],
            "scope": cut_spec.get("refScope"),
            "images": [image.data for image in images],
            "manifest": manifest,
            "hasFace": has_face,
        })
        return b"OUTPUT", "image/png"

    async def fake_best_of(settings, product_images, initial, generate_candidate):
        return initial, None, []

    async def fake_scene(*_args, **_kwargs):
        return {"verdict": "pass", "mismatches": [], "correctionPrompt": None}

    async def fake_finalize(conn, **kwargs):
        captured["finalize"] = kwargs
        return {"id": "wardrobe"}

    async def fake_emit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(eij.repo, "get_product", fake_product)
    monkeypatch.setattr(eij.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(eij.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(eij.cut_generator, "generate", fake_generate)
    monkeypatch.setattr(eij.image_qc, "best_of", fake_best_of)
    monkeypatch.setattr(eij.image_qc, "scene_verdict", fake_scene)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_success", fake_finalize)
    monkeypatch.setattr(eij, "_emit", fake_emit)


def _patch_editor_selected_mannequin(monkeypatch, *, active_asset_id=None):
    async def fake_project(conn, uid, pid):
        return {"selected_mannequin_id": "A-1"}

    async def fake_mannequins(conn, uid, pid):
        return [{
            "candidate": "A",
            "version": 1,
            "asset_id": "mannequin",
            "active_asset_id": active_asset_id,
        }]

    monkeypatch.setattr(eij.repo, "get_project", fake_project)
    monkeypatch.setattr(eij.repo, "list_mannequin_cuts", fake_mannequins)


def test_editor_base_color_worn_cut_puts_selected_mannequin_first(monkeypatch):
    captured = {}
    _patch_editor_common(monkeypatch, captured)
    _patch_editor_selected_mannequin(monkeypatch, active_asset_id="tone-mannequin")

    async def fake_product(conn, pid):
        return {
            "clothingType": "top",
            "colors": [{
                "id": "base",
                "isBase": True,
                "images": [{"slot": "Front", "id": "product"}],
            }],
        }

    def fake_model_refs(spec, *, require_full_body=False):
        assert require_full_body is True
        return (
            {"key": "model-face", "mime": "image/png"},
            {"key": "model-body", "mime": "image/png"},
        )

    monkeypatch.setattr(eij.repo, "get_product", fake_product)
    monkeypatch.setattr(eij.cut_generator, "resolve_virtual_model_assets", fake_model_refs)

    app = fake_worker_app(
        make_settings(gemini_api_key="x", r2_bucket="b"), r2=_TrackingR2()
    )
    asyncio.run(eij.run_editor_image_job(app, worker_job({
        "mode": "new",
        "cutType": "styling",
        "shot": "full",
        "colorId": "base",
        "modelId": "mA",
    })))

    generated = captured["generations"][0]
    assert generated["images"] == [
        b"k/tone-mannequin", b"model-face", b"model-body", b"k/product",
    ]
    lines = generated["manifest"].splitlines()
    assert "MANNEQUIN — coarse worn-geometry prior" in lines[0]
    assert lines[1].startswith("2. MODEL FACE —")
    assert lines[2].startswith("3. MODEL FULL BODY —")
    assert lines[3] == "4. PRODUCT — front view of the garment"


def test_editor_excludes_selected_mannequin_from_product_and_non_base_color(monkeypatch):
    captured = {}
    _patch_editor_common(monkeypatch, captured)
    _patch_editor_selected_mannequin(monkeypatch)

    async def fake_product(conn, pid):
        return {
            "clothingType": "top",
            "colors": [
                {"id": "base", "isBase": True,
                 "images": [{"slot": "Front", "id": "base-product"}]},
                {"id": "other", "isBase": False,
                 "images": [{"slot": "Front", "id": "other-product"}]},
            ],
        }

    monkeypatch.setattr(eij.repo, "get_product", fake_product)
    app = fake_worker_app(
        make_settings(gemini_api_key="x", r2_bucket="b"), r2=_TrackingR2()
    )

    asyncio.run(eij.run_editor_image_job(app, worker_job({
        "mode": "new", "cutType": "product", "shot": "ghost", "colorId": "base",
    })))
    asyncio.run(eij.run_editor_image_job(app, worker_job({
        "mode": "new", "cutType": "styling", "shot": "full", "colorId": "other",
    })))

    product_cut, other_color_cut = captured["generations"]
    assert product_cut["images"] == [b"k/base-product"]
    assert other_color_cut["images"] == [b"k/other-product"]
    assert "mannequin" not in product_cut["manifest"].lower()
    assert "mannequin" not in other_color_cut["manifest"].lower()


def test_editor_selected_mannequin_r2_failure_does_not_fall_back(monkeypatch):
    captured = {}
    _patch_editor_common(monkeypatch, captured)
    _patch_editor_selected_mannequin(monkeypatch)

    class FailingMannequinR2(_TrackingR2):
        def get_bytes(self, key):
            if key == "k/mannequin":
                raise RuntimeError("selected mannequin unavailable")
            return super().get_bytes(key)

    async def fake_failure(conn, **kwargs):
        captured["failure"] = kwargs
        return True

    monkeypatch.setattr(eij.repo, "finalize_editor_image_failure", fake_failure)
    app = fake_worker_app(
        make_settings(gemini_api_key="x", r2_bucket="b"), r2=FailingMannequinR2()
    )
    asyncio.run(eij.run_editor_image_job(app, worker_job({
        "mode": "new", "cutType": "styling", "shot": "full",
    })))

    assert captured.get("generations") is None
    assert "selected mannequin unavailable" in captured["failure"]["metadata"]["error"]


def test_editor_prunes_mood_for_all_and_bg_but_keeps_pose_order(monkeypatch):
    captured = {}
    _patch_editor_common(monkeypatch, captured)

    async def fake_example(settings, example_id, scope="all", clothing_type=None):
        return eij.InlineImage("image/png", f"example:{scope}".encode())

    monkeypatch.setattr(eij.cut_generator, "example_asset_status", lambda *_args: "available")
    monkeypatch.setattr(eij.cut_generator, "pose_direction_compatible", lambda *_args: True)
    monkeypatch.setattr(eij.cut_generator, "load_example_image", fake_example)

    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"), r2=_TrackingR2())
    for scope in ("all", "bg", "pose"):
        asyncio.run(eij.run_editor_image_job(app, worker_job({
            "mode": "new",
            "cutType": "styling",
            "shot": "full",
            "refAssetIds": ["mood"],
            "exampleId": f"example-{scope}",
            "refScope": scope,
        })))

    all_cut, bg_cut, pose_cut = captured["generations"]
    assert all_cut["images"] == [b"k/product", b"example:all"]
    assert bg_cut["images"] == [b"example:bg", b"k/product"]
    assert pose_cut["images"] == [b"k/product", b"k/mood", b"example:pose"]
    assert "MOOD —" not in all_cut["manifest"]
    assert "MOOD —" not in bg_cut["manifest"]
    assert pose_cut["manifest"].count("MOOD —") == 1


def test_editor_real_product_prunes_identity_and_never_sets_settlement_flag(monkeypatch):
    captured = {"settlements": 0}
    _patch_editor_common(monkeypatch, captured)
    model_id = REAL_MODEL_ID

    async def fake_real_refs(conn, selected_model_id, **_kwargs):
        assert selected_model_id == model_id
        return [
            {"key": "face-front", "mime": "image/png", "bucket": "face"},
            {"key": "face-sheet", "mime": "image/png", "bucket": "face"},
        ]

    async def fake_license(conn, selected_model_id, *, license_id=None, **_kwargs):
        assert license_id == REAL_LICENSE_ID
        return {
            "id": license_id,
            "model_id": selected_model_id,
            "status": "active",
            "model_status": "verified",
            "current_enrollment_id": REAL_ENROLLMENT_ID,
            "match_policy_version": "policy-v1",
            "unit_price": 10,
        }

    async def fake_verify(app, row, **kwargs):
        return None

    async def fake_writer_boundary(_conn):
        return None

    async def fake_example(settings, example_id, scope="all", clothing_type=None):
        return eij.InlineImage("image/png", b"example:all")

    async def fake_settlement(*_args, **_kwargs):
        captured["settlements"] += 1

    monkeypatch.setattr(eij.identity_source, "resolve_real_model_assets", fake_real_refs)
    monkeypatch.setattr(eij.facemarket, "resolve_model_license", fake_license)
    monkeypatch.setattr(eij.facemarket, "verify_license", fake_verify)
    monkeypatch.setattr(eij.facemarket, "verify_license_local", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(eij.repo, "lock_facemarket_writer_boundary", fake_writer_boundary)
    monkeypatch.setattr(eij.facemarket, "record_license_settlement", fake_settlement)
    monkeypatch.setattr(eij.cut_generator, "example_asset_status", lambda *_args: "available")
    monkeypatch.setattr(eij.cut_generator, "load_example_image", fake_example)

    public_r2 = _TrackingR2()
    face_r2 = _TrackingR2()
    app = fake_worker_app(
        make_settings(gemini_api_key="x", r2_bucket="b", facemarket_enabled=True),
        r2=public_r2,
    )
    app.state.r2_face = face_r2
    app.state.fm_chain = object()
    asyncio.run(eij.run_editor_image_job(app, worker_job({
        "mode": "new",
        "cutType": "product",
        "shot": "ghost",
        "modelId": model_id,
        "brandUseCategory": REAL_CATEGORY,
        "_facemarket": {"modelId": model_id, "licenseId": REAL_LICENSE_ID},
        "refAssetIds": ["mood"],
        "exampleId": "example-all",
        "refScope": "all",
    })))

    generated = captured["generations"][0]
    assert generated["images"] == [b"k/product", b"example:all"]
    assert "MODEL" not in generated["manifest"]
    assert "MODEL FACE" not in generated["manifest"]
    assert "MOOD —" not in generated["manifest"]
    assert face_r2.reads == []
    assert captured["settlements"] == 0
    assert public_r2.caches == ["public, max-age=31536000, immutable"]
    assert captured["finalize"]["image"].get("metadata", {}).get(
        "facemarket_real_derived", False
    ) is False


def test_editor_real_visible_worn_cut_enables_identity_contract(monkeypatch):
    captured = {"settlements": 0}
    _patch_editor_common(monkeypatch, captured)
    model_id = REAL_MODEL_ID

    async def fake_real_refs(conn, selected_model_id, **_kwargs):
        return [
            {"key": "face-front", "mime": "image/png", "bucket": "face"},
            {"key": "face-sheet", "mime": "image/png", "bucket": "face"},
        ]

    async def fake_license(conn, selected_model_id, *, license_id=None, **_kwargs):
        assert license_id == REAL_LICENSE_ID
        return {
            "id": license_id,
            "model_id": selected_model_id,
            "status": "active",
            "model_status": "verified",
            "current_enrollment_id": REAL_ENROLLMENT_ID,
            "match_policy_version": "policy-v1",
            "unit_price": 10,
        }

    async def fake_verify(app, row, **kwargs):
        return None

    async def fake_writer_boundary(_conn):
        return None

    monkeypatch.setattr(eij.identity_source, "resolve_real_model_assets", fake_real_refs)
    monkeypatch.setattr(eij.facemarket, "resolve_model_license", fake_license)
    monkeypatch.setattr(eij.facemarket, "verify_license", fake_verify)
    monkeypatch.setattr(eij.facemarket, "verify_license_local", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(eij.repo, "lock_facemarket_writer_boundary", fake_writer_boundary)

    public_r2 = _TrackingR2()
    app = fake_worker_app(
        make_settings(gemini_api_key="x", r2_bucket="b", facemarket_enabled=True),
        r2=public_r2,
    )
    app.state.r2_face = _TrackingR2()
    asyncio.run(eij.run_editor_image_job(app, worker_job({
        "mode": "new",
        "cutType": "styling",
        "shot": "full",
        "direction": "front",
        "faceExposure": "show",
        "modelId": model_id,
        "brandUseCategory": REAL_CATEGORY,
        "_facemarket": {"modelId": model_id, "licenseId": REAL_LICENSE_ID},
    })))

    generated = captured["generations"][0]
    assert generated["images"][:2] == [b"face-front", b"face-sheet"]
    assert generated["hasFace"] is True
    assert "MODEL — frontal close-up" in generated["manifest"]
    assert "MODEL SHEET" in generated["manifest"]
    assert "MODEL FULL BODY" not in generated["manifest"]
    assert public_r2.caches == ["private, no-store"]
    assert captured["finalize"]["image"]["metadata"] == {
        "facemarket_real_derived": True,
    }


class _HolderResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def _run_editor_worker_vc_denial(monkeypatch, holder_call, *, license_overrides=None):
    captured = {}
    _patch_editor_common(monkeypatch, captured)
    model_id = REAL_MODEL_ID

    async def fake_real_refs(conn, selected_model_id, **_kwargs):
        return [
            {"key": "face-front", "mime": "image/png", "bucket": "face"},
            {"key": "face-sheet", "mime": "image/png", "bucket": "face"},
        ]

    async def fake_license(conn, selected_model_id, *, license_id=None):
        return {
            "id": REAL_LICENSE_ID,
            "model_id": selected_model_id,
            "status": "active",
            "license_valid_until": None,
            "unit_price": 10,
            "vc_id": "vc-1",
            "allowed_use": [REAL_CATEGORY],
            "forbidden_use": [],
            "model_status": "verified",
            "assets_status": "ready",
            "current_enrollment_id": REAL_ENROLLMENT_ID,
            "license_enrollment_id": REAL_ENROLLMENT_ID,
            "enrollment_status": "passed",
            "match_policy_version": "policy-v1",
            "has_face_front": True,
            "has_grid_sedcard": True,
            "assets_current_evidence": True,
            **(license_overrides or {}),
        }

    async def fake_failure(conn, **kwargs):
        captured["failure"] = kwargs

    async def forbidden_success(*_args, **_kwargs):
        raise AssertionError("VC denial must not finalize success")

    async def forbidden_settlement(*_args, **_kwargs):
        raise AssertionError("VC denial must not settle")

    monkeypatch.setattr(eij.identity_source, "resolve_real_model_assets", fake_real_refs)
    monkeypatch.setattr(eij.facemarket, "resolve_model_license", fake_license)
    monkeypatch.setattr(eij.facemarket.holder_client, "post", holder_call)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_failure", fake_failure)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_success", forbidden_success)
    monkeypatch.setattr(eij.facemarket, "record_license_settlement", forbidden_settlement)

    public_r2 = _TrackingR2()
    face_r2 = _TrackingR2()
    app = fake_worker_app(
        make_settings(
            gemini_api_key="x",
            r2_bucket="b",
            facemarket_enabled=True,
            fm_vc_required=True,
            opendid_holder_url="http://holder",
            opendid_holder_hmac_secret="shared-secret",
        ),
        r2=public_r2,
    )
    app.state.r2_face = face_r2

    asyncio.run(eij.run_editor_image_job(app, worker_job({
        "mode": "new",
        "cutType": "styling",
        "shot": "full",
        "direction": "front",
        "faceExposure": "show",
        "modelId": model_id,
        "brandUseCategory": REAL_CATEGORY,
        "_facemarket": {"modelId": model_id, "licenseId": REAL_LICENSE_ID},
    })))

    assert captured.get("generations") is None
    assert captured["failure"]["reserved"] == 1
    assert public_r2.reads == []
    assert face_r2.reads == []


def test_editor_holder_outage_fails_before_model_asset_reads(monkeypatch):
    async def holder_down(*_args, **_kwargs):
        raise httpx.ConnectError("holder down")

    _run_editor_worker_vc_denial(monkeypatch, holder_down)


def test_editor_revoked_or_expired_license_fails_before_model_asset_reads(monkeypatch):
    async def forbidden_holder(*_args, **_kwargs):
        raise AssertionError("local denial must not call Holder")

    for overrides in (
        {"status": "revoked"},
        {"license_valid_until": datetime.now(timezone.utc) - timedelta(seconds=1)},
    ):
        _run_editor_worker_vc_denial(
            monkeypatch,
            forbidden_holder,
            license_overrides=overrides,
        )


def test_editor_invalid_or_revoked_vc_fails_before_model_asset_reads(monkeypatch):
    for payload in (
        {"verified": False, "status": "invalid"},
        {"verified": True, "status": "revoked"},
    ):
        async def holder_result(*_args, _payload=payload, **_kwargs):
            return _HolderResponse(_payload)

        _run_editor_worker_vc_denial(monkeypatch, holder_result)


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("missing_snapshot", "model_unavailable"),
        ("missing_snapshot_flag_off", "model_unavailable"),
        ("mismatched_snapshot", "model_unavailable"),
        ("revoked", "license_revoked"),
        ("holder", "holder_unavailable"),
        ("stale_evidence", "model_enrollment_unavailable"),
        ("missing_refs", "model_assets_unavailable"),
        ("r2_failure", "model_assets_unavailable"),
    ],
)
def test_editor_real_denials_refund_without_generation_output_or_settlement(
    monkeypatch, case, expected_code
):
    captured = {"resolve": 0, "verify": 0, "assets": 0, "settlement": 0}
    _patch_editor_common(monkeypatch, captured)

    async def fake_resolve(conn, model_id, *, license_id=None, **_kwargs):
        captured["resolve"] += 1
        assert model_id == REAL_MODEL_ID and license_id == REAL_LICENSE_ID
        return {
            "id": REAL_LICENSE_ID,
            "model_id": REAL_MODEL_ID,
            "model_status": "verified",
            "status": "active",
            "current_enrollment_id": REAL_ENROLLMENT_ID,
            "match_policy_version": "policy-v1",
            "unit_price": 10,
        }

    async def fake_verify(app, row, **kwargs):
        captured["verify"] += 1
        assert kwargs == {
            "model_id": REAL_MODEL_ID,
            "brand_use_category": REAL_CATEGORY,
        }
        if case in {"revoked", "holder", "stale_evidence"}:
            raise eij.facemarket._err(
                expected_code,
                "blocked",
                status=503 if case == "holder" else 409,
            )

    async def fake_refs(conn, model_id, *, enrollment_id, evidence_version):
        captured["assets"] += 1
        assert enrollment_id == REAL_ENROLLMENT_ID
        assert evidence_version == "policy-v1"
        if case == "missing_refs":
            return None
        return [
            {"key": "face-front", "mime": "image/png", "bucket": "face"},
            {"key": "face-grid", "mime": "image/png", "bucket": "face"},
        ]

    async def fake_failure(conn, **kwargs):
        captured["failure"] = kwargs
        return {"status": "failed"}

    async def forbidden_success(*args, **kwargs):
        raise AssertionError("denial must not finalize success")

    async def fake_settlement(*args, **kwargs):
        captured["settlement"] += 1

    monkeypatch.setattr(eij.facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(eij.facemarket, "verify_license", fake_verify)
    monkeypatch.setattr(eij.identity_source, "resolve_real_model_assets", fake_refs)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_failure", fake_failure)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_success", forbidden_success)
    monkeypatch.setattr(eij.facemarket, "record_license_settlement", fake_settlement)

    public_r2 = _TrackingR2()
    face_r2 = _TrackingR2()
    if case == "r2_failure":
        def fail_read(key):
            face_r2.reads.append(key)
            raise RuntimeError("private object unavailable")
        face_r2.get_bytes = fail_read
    app = fake_worker_app(
        make_settings(
            gemini_api_key="x",
            r2_bucket="b",
            facemarket_enabled=case != "missing_snapshot_flag_off",
        ),
        r2=public_r2,
    )
    app.state.r2_face = face_r2
    app.state.fm_chain = object()
    payload = {
        "mode": "new",
        "cutType": "styling",
        "shot": "full",
        "modelId": REAL_MODEL_ID,
        "brandUseCategory": REAL_CATEGORY,
        "_facemarket": {
            "modelId": REAL_MODEL_ID,
            "licenseId": REAL_LICENSE_ID,
        },
    }
    if case in {"missing_snapshot", "missing_snapshot_flag_off"}:
        payload.pop("_facemarket")
    elif case == "mismatched_snapshot":
        payload["_facemarket"]["modelId"] = "44444444-4444-4444-4444-444444444444"

    asyncio.run(eij.run_editor_image_job(
        app,
        worker_job(payload, credits_reserved=7),
    ))

    assert captured.get("generations") is None
    assert captured["settlement"] == 0
    assert captured["failure"]["reserved"] == 7
    assert captured["failure"]["code"] == expected_code
    if case in {"revoked", "holder", "stale_evidence"}:
        assert face_r2.reads == [] and captured["assets"] == 0
    if case in {
        "missing_snapshot",
        "missing_snapshot_flag_off",
        "mismatched_snapshot",
    }:
        assert captured["resolve"] == 0


def test_editor_final_recheck_revoked_license_deletes_output_and_refunds(monkeypatch):
    captured = {"resolve": 0, "settlement": 0}
    _patch_editor_common(monkeypatch, captured)

    def gate_row(status):
        return {
            "id": REAL_LICENSE_ID,
            "model_id": REAL_MODEL_ID,
            "model_status": "verified",
            "status": status,
            "license_valid_until": datetime.now(timezone.utc) + timedelta(days=1),
            "unit_price": 10,
            "vc_id": "vc-1",
            "allowed_use": [REAL_CATEGORY],
            "forbidden_use": [],
            "assets_status": "ready",
            "current_enrollment_id": REAL_ENROLLMENT_ID,
            "license_enrollment_id": REAL_ENROLLMENT_ID,
            "enrollment_status": "passed",
            "match_policy_version": "policy-v1",
            "has_face_front": True,
            "has_grid_sedcard": True,
            "assets_current_evidence": True,
        }

    async def fake_resolve(conn, model_id, *, license_id=None, **kwargs):
        captured["resolve"] += 1
        assert model_id == REAL_MODEL_ID and license_id == REAL_LICENSE_ID
        return gate_row("active" if captured["resolve"] == 1 else "revoked")

    async def fake_verify(app, row, **kwargs):
        assert row["status"] == "active"

    async def fake_refs(conn, model_id, *, enrollment_id, evidence_version):
        return [
            {"key": "face-front", "mime": "image/png", "bucket": "face"},
            {"key": "face-grid", "mime": "image/png", "bucket": "face"},
        ]

    async def fake_failure(conn, **kwargs):
        captured["failure"] = kwargs
        return True

    async def fake_success(conn, **kwargs):
        captured["success"] = kwargs
        return {"id": "published"}

    async def fake_settlement(*_args, **_kwargs):
        captured["settlement"] += 1

    async def fake_lock(conn):
        captured["locked"] = True

    monkeypatch.setattr(eij.facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(eij.facemarket, "verify_license", fake_verify)
    monkeypatch.setattr(eij.identity_source, "resolve_real_model_assets", fake_refs)
    monkeypatch.setattr(eij.repo, "lock_facemarket_writer_boundary", fake_lock)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_failure", fake_failure)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_success", fake_success)
    monkeypatch.setattr(eij.facemarket, "record_license_settlement", fake_settlement)

    public_r2 = _TrackingR2()
    face_r2 = _TrackingR2()
    app = fake_worker_app(
        make_settings(gemini_api_key="x", r2_bucket="b", facemarket_enabled=True),
        r2=public_r2,
    )
    app.state.r2_face = face_r2
    app.state.fm_chain = object()

    asyncio.run(eij.run_editor_image_job(app, worker_job({
        "mode": "new",
        "cutType": "styling",
        "shot": "full",
        "modelId": REAL_MODEL_ID,
        "brandUseCategory": REAL_CATEGORY,
        "_facemarket": {"modelId": REAL_MODEL_ID, "licenseId": REAL_LICENSE_ID},
    }, credits_reserved=7)))

    assert captured["resolve"] == 2
    assert "success" not in captured
    assert captured["settlement"] == 0
    assert captured["failure"]["reserved"] == 7
    assert captured["failure"]["code"] == "license_revoked"
    assert public_r2.puts and public_r2.deletes == public_r2.puts


def test_editor_final_recheck_delete_failure_leaves_cleanup_intent(monkeypatch):
    events = []
    captured = {"resolve": 0, "settlement": 0}
    _patch_editor_common(monkeypatch, captured)

    def gate_row(status):
        return {
            "id": REAL_LICENSE_ID,
            "model_id": REAL_MODEL_ID,
            "model_status": "verified",
            "status": status,
            "license_valid_until": datetime.now(timezone.utc) + timedelta(days=1),
            "unit_price": 10,
            "vc_id": "vc-1",
            "allowed_use": [REAL_CATEGORY],
            "forbidden_use": [],
            "assets_status": "ready",
            "current_enrollment_id": REAL_ENROLLMENT_ID,
            "license_enrollment_id": REAL_ENROLLMENT_ID,
            "enrollment_status": "passed",
            "match_policy_version": "policy-v1",
            "has_face_front": True,
            "has_grid_sedcard": True,
            "assets_current_evidence": True,
        }

    async def fake_resolve(conn, model_id, *, license_id=None, **kwargs):
        captured["resolve"] += 1
        return gate_row("active" if captured["resolve"] == 1 else "revoked")

    async def fake_verify(app, row, **kwargs):
        assert row["status"] == "active"

    async def fake_refs(conn, model_id, *, enrollment_id, evidence_version):
        return [
            {"key": "face-front", "mime": "image/png", "bucket": "face"},
            {"key": "face-grid", "mime": "image/png", "bucket": "face"},
        ]

    async def fake_failure(conn, **kwargs):
        captured["failure"] = kwargs
        return True

    async def fake_success(conn, **kwargs):
        captured["success"] = kwargs
        return {"id": "published"}

    async def fake_settlement(*_args, **_kwargs):
        captured["settlement"] += 1

    async def fake_lock(conn):
        captured["locked"] = True

    async def fake_intent(conn, **kwargs):
        events.append("intent")
        return "intent-1"

    async def forbidden_clear(conn, intent_id):
        events.append(f"clear:{intent_id}")

    monkeypatch.setattr(eij.facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(eij.facemarket, "verify_license", fake_verify)
    monkeypatch.setattr(eij.identity_source, "resolve_real_model_assets", fake_refs)
    monkeypatch.setattr(eij.repo, "lock_facemarket_writer_boundary", fake_lock)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_failure", fake_failure)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_success", fake_success)
    monkeypatch.setattr(eij.facemarket, "record_license_settlement", fake_settlement)
    monkeypatch.setattr(
        eij.repo,
        "create_ai_output_cleanup_intent",
        fake_intent,
        raising=False,
    )
    monkeypatch.setattr(
        eij.repo,
        "clear_ai_output_cleanup_intent",
        forbidden_clear,
        raising=False,
    )

    public_r2 = _TrackingR2(fail_delete=True)
    original_put = public_r2.put_bytes

    def tracking_put(*args, **kwargs):
        events.append("put")
        return original_put(*args, **kwargs)

    public_r2.put_bytes = tracking_put
    face_r2 = _TrackingR2()
    app = fake_worker_app(
        make_settings(gemini_api_key="x", r2_bucket="b", facemarket_enabled=True),
        r2=public_r2,
    )
    app.state.r2_face = face_r2
    app.state.fm_chain = object()

    asyncio.run(eij.run_editor_image_job(app, worker_job({
        "mode": "new",
        "cutType": "styling",
        "shot": "full",
        "modelId": REAL_MODEL_ID,
        "brandUseCategory": REAL_CATEGORY,
        "_facemarket": {"modelId": REAL_MODEL_ID, "licenseId": REAL_LICENSE_ID},
    }, credits_reserved=7)))

    assert events[:2] == ["intent", "put"]
    assert public_r2.puts and public_r2.deletes == public_r2.puts
    assert not any(event.startswith("clear:") for event in events)
    assert "success" not in captured
    assert captured["settlement"] == 0
    assert captured["failure"]["code"] == "license_revoked"


def test_editor_cancel_after_put_clears_cleanup_intent_after_confirmed_delete(monkeypatch):
    events = []
    captured = {}
    _patch_editor_common(monkeypatch, captured)

    async def cancelled_success(conn, **kwargs):
        captured["success"] = kwargs
        return None

    async def fake_intent(conn, **kwargs):
        events.append("intent")
        return "intent-1"

    async def fake_clear(conn, intent_id):
        events.append(f"clear:{intent_id}")

    monkeypatch.setattr(eij.repo, "finalize_editor_image_success", cancelled_success)
    monkeypatch.setattr(
        eij.repo,
        "create_ai_output_cleanup_intent",
        fake_intent,
        raising=False,
    )
    monkeypatch.setattr(
        eij.repo,
        "clear_ai_output_cleanup_intent",
        fake_clear,
        raising=False,
    )

    public_r2 = _TrackingR2()
    original_put = public_r2.put_bytes

    def tracking_put(*args, **kwargs):
        events.append("put")
        return original_put(*args, **kwargs)

    public_r2.put_bytes = tracking_put
    app = fake_worker_app(
        make_settings(gemini_api_key="x", r2_bucket="b", facemarket_enabled=True),
        r2=public_r2,
    )

    asyncio.run(eij.run_editor_image_job(app, worker_job({
        "mode": "new",
        "cutType": "product",
        "shot": "ghost",
    })))

    assert events[:2] == ["intent", "put"]
    assert public_r2.puts and public_r2.deletes == public_r2.puts
    assert events[-1] == "clear:intent-1"


def test_editor_active_job_cleanup_claim_does_not_delete_published_output(monkeypatch):
    from app.workers.draft_asset_reclaimer import DraftAssetReclaimer

    events = []
    captured = {}
    _patch_editor_common(monkeypatch, captured)

    async def reclaim_drafts(conn):
        return []

    async def active_job_claim(conn):
        events.append("claim-active")
        return []

    async def fake_clear(conn, intent_id):
        events.append(f"clear:{intent_id}")

    async def publishing_success(conn, **kwargs):
        events.append("finalize")
        await eij.repo.clear_ai_output_cleanup_intent(
            conn, kwargs["image"]["cleanup_intent_id"]
        )
        return {"id": "wardrobe"}

    monkeypatch.setattr(eij.repo, "reclaim_stale_unreferenced_draft_assets", reclaim_drafts)
    monkeypatch.setattr(
        eij.repo,
        "claim_unpublished_ai_output_cleanup_intents",
        active_job_claim,
        raising=False,
    )
    monkeypatch.setattr(
        eij.repo,
        "clear_ai_output_cleanup_intent",
        fake_clear,
        raising=False,
    )
    monkeypatch.setattr(eij.repo, "finalize_editor_image_success", publishing_success)

    public_r2 = _TrackingR2()
    original_put = public_r2.put_bytes

    def tracking_put(*args, **kwargs):
        events.append("put")
        return original_put(*args, **kwargs)

    public_r2.put_bytes = tracking_put
    app = fake_worker_app(
        make_settings(gemini_api_key="x", r2_bucket="b", facemarket_enabled=True),
        r2=public_r2,
    )

    async def fake_intent(conn, **kwargs):
        events.append("intent")
        await DraftAssetReclaimer(app)._sweep_once()
        return "intent-1"

    monkeypatch.setattr(
        eij.repo,
        "create_ai_output_cleanup_intent",
        fake_intent,
        raising=False,
    )

    asyncio.run(eij.run_editor_image_job(app, worker_job({
        "mode": "new",
        "cutType": "product",
        "shot": "ghost",
    })))

    assert events[:3] == ["intent", "claim-active", "put"]
    assert "clear:intent-1" in events
    assert public_r2.puts
    assert public_r2.deletes == []
