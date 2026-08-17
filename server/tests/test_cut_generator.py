"""AG-06 cut_generator — build_prompt 배관 테스트 (계약 이식 후, 2026-07-07).

스펙 정규화·섹션 렌더의 세부 계약은 test_cuts.py 가 담당한다. 여기는 워커가 쓰는
진입점(build_prompt/generate 경로)의 회귀만 지킨다: 매니페스트 토큰 유출 금지(architect
DEFECT 1), 빈 이미지 폴백 문구, 미상 cutType 은 조용한 styling 폴백이 아니라 ValueError,
mirror 가 정식 컷으로 렌더되는지.
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.agents import cut_generator as cg
from conftest import make_settings


def test_cut_types_constant_includes_mirror():
    assert cg.CUT_TYPES == ("styling", "horizon", "product", "mirror")


def test_build_prompt_substitutes_image_manifest():
    # ${imageManifest} 리터럴 토큰이 모델로 유출되면 안 됨 (architect DEFECT 1 회귀 방지)
    product = {"name": "니트", "colors": [{"isBase": True, "images": [
        {"slot": "Front", "id": "a1"}, {"slot": "Back", "id": "a2"}]}]}
    p = cg.build_prompt({"cutType": "styling", "direction": "front", "shot": "full"}, product)
    assert "${imageManifest}" not in p
    assert "front view of the garment" in p and "back view of the garment" in p
    assert "CUT PLAN AUTHORITY" in p
    assert "recipe=styling/lifestyle" in p


def test_build_prompt_manifest_fallback_no_images():
    p = cg.build_prompt({"cutType": "product"}, {"name": "니트", "colors": []})
    assert "${imageManifest}" not in p
    assert "product photos" in p.lower()


@pytest.mark.parametrize("clothing_type", ["top", "bottom"])
def test_medium_prompt_requires_a_separate_pose_and_camera_not_a_full_shot_crop(clothing_type):
    prompt = cg.build_prompt(
        {"cutType": "horizon", "direction": "front", "shot": "medium", "pose": "lean"},
        {"name": "컬러 상품", "clothingType": clothing_type, "colors": []},
    )

    assert "separately photographed exposure" in prompt
    assert "NEVER make it by digitally cropping, zooming, or reframing a full-body render" in prompt
    assert "use the distinct pose requested by the current CUT SPEC" in prompt
    assert "purpose-shot upper-garment photograph" in prompt
    assert "separate lower camera around hip-to-upper-thigh height" in prompt
    assert "must not share the full shot's exact body pose or perspective" in prompt


def test_build_prompt_product_detail_falls_back_to_original_zoom_mode():
    # 2026-08-07 개편: 디테일 사진이 없어도 같은 방향 원본이 있으면 구조 확대 모드로 생성한다.
    product = {"name": "니트", "colors": [
        {"id": "base", "isBase": True, "images": [{"slot": "Front", "id": "a1"}]},
        {"id": "other", "images": [{"slot": "Back", "id": "a2"}]},
    ]}
    prompt = cg.build_prompt({"cutType": "product", "shot": "detail"}, product)
    assert "structural element" in prompt            # SHOT:detail_zoom


def test_build_prompt_product_detail_requires_same_side_evidence():
    # 방향 근거(같은 쪽 디테일도 원본도)가 전무하면 그 컷만 실패한다 — 지어내지 않는다.
    product = {"name": "니트", "colors": [
        {"id": "base", "isBase": True, "images": [{"slot": "Front", "id": "a1"}]},
    ]}
    with pytest.raises(ValueError, match="detail_reference_required"):
        cg.build_prompt(
            {"cutType": "product", "shot": "detail", "direction": "back"}, product)


def test_build_prompt_product_detail_uses_detail_slot():
    product = {"name": "니트", "colors": [{"isBase": True, "images": [
        {"slot": "Front", "id": "a1"}, {"slot": "Detail", "id": "a2"},
    ]}]}
    prompt = cg.build_prompt({"cutType": "product", "shot": "detail"}, product)
    assert "front-side detail close-up of the garment" in prompt
    assert "tight product-only close-up" in prompt


def test_build_prompt_product_detail_uses_other_color_detail_with_color_transfer():
    product = {"name": "가디건", "colors": [
        {"id": "red", "name": "레드", "swatchId": "red", "isBase": True, "images": [
            {"slot": "Front", "id": "red-front"},
            {"slot": "Detail", "id": "red-detail"},
        ]},
        {"id": "green", "name": "그린", "swatchId": "green", "images": [
            {"slot": "Front", "id": "green-front"},
        ]},
    ]}

    images, transfer = cg.detail_reference_images(product, "green")
    prompt = cg.build_prompt(
        {"cutType": "product", "shot": "detail", "colorId": "green"}, product)

    assert images == [("Front", "green-front"), ("Detail", "red-detail")]
    assert transfer == {
        "targetName": "그린", "targetHex": "#3f7a4f", "referenceName": "레드",
    }
    assert "PRODUCT — front-side detail close-up" in prompt
    assert "DETAIL COLORWAY TRANSFER" in prompt
    assert "Target color: 그린 (#3f7a4f)" in prompt
    assert "fabric structure, shape, and material exactly" in prompt
    assert "change ONLY their color" in prompt
    assert "Do not invent any detail" in prompt


def test_build_prompt_product_detail_same_color_has_no_color_transfer():
    product = {"name": "가디건", "colors": [{
        "id": 7, "name": "그린", "swatchId": "green", "isBase": True,
        "images": [
            {"slot": "Front", "id": "green-front"},
            {"slot": "Detail", "id": "green-detail"},
        ],
    }]}

    images, transfer = cg.detail_reference_images(product, "7")
    prompt = cg.build_prompt(
        {"cutType": "product", "shot": "detail", "colorId": "7"}, product)

    assert images == [("Front", "green-front"), ("Detail", "green-detail")]
    assert transfer is None
    assert "PRODUCT — front-side detail close-up" in prompt
    assert "DETAIL COLORWAY TRANSFER" not in prompt


def test_product_detail_unknown_color_does_not_transfer_from_other_color():
    product = {"name": "가디건", "colors": [
        {"id": "red", "name": "레드", "isBase": True, "images": [
            {"slot": "Front", "id": "red-front"},
            {"slot": "Detail", "id": "red-detail"},
        ]},
    ]}

    with pytest.raises(ValueError, match="invalid_color"):
        cg.detail_reference_images(product, "missing")
    with pytest.raises(ValueError, match="invalid_color"):
        cg.build_prompt(
            {"cutType": "product", "shot": "detail", "colorId": "missing"},
            product,
        )


def test_detail_reference_images_prefers_base_then_first_detail_color():
    product = {"colors": [
        {"id": "first", "name": "블루", "images": [
            {"slot": "Detail", "id": "first-detail"},
        ]},
        {"id": "base", "name": "레드", "isBase": True, "images": [
            {"slot": "Detail", "id": "base-detail"},
        ]},
        {"id": "target", "name": "그린", "images": [
            {"slot": "Front", "id": "target-front"},
        ]},
    ]}

    images, _transfer = cg.detail_reference_images(product, "target")
    assert images == [("Front", "target-front"), ("Detail", "base-detail")]

    product["colors"][1]["images"] = []
    images, _transfer = cg.detail_reference_images(product, "target")
    assert images == [("Front", "target-front"), ("Detail", "first-detail")]


# ---------- detail_reference_images 방향 인지 (2026-08-07 스펙 §5 매트릭스) ----------


def test_detail_refs_back_direction_prefers_same_color_backdetail():
    p = {"colors": [{"id": "base", "isBase": True, "images": [
        {"slot": "Front", "id": "f1"}, {"slot": "Back", "id": "b1"},
        {"slot": "Detail", "id": "d1"}, {"slot": "BackDetail", "id": "bd1"},
    ]}]}
    images, transfer = cg.detail_reference_images(p, None, direction="back")
    assert ("BackDetail", "bd1") in images
    assert transfer is None


def test_detail_refs_back_direction_borrows_backdetail_from_other_color_only():
    # 목표색엔 BackDetail 없음 → 타색 BackDetail을 색전환으로 빌린다. 타색 Detail(앞면)은 금지.
    p = {"colors": [
        {"id": "base", "name": "베이스", "isBase": True, "images": [
            {"slot": "Front", "id": "bf"}, {"slot": "Detail", "id": "bd-front"},
            {"slot": "BackDetail", "id": "bd-back"}]},
        {"id": "red", "name": "레드", "images": [
            {"slot": "Front", "id": "rf"}, {"slot": "Back", "id": "rb"}]},
    ]}
    images, transfer = cg.detail_reference_images(p, "red", direction="back")
    assert ("BackDetail", "bd-back") in images
    assert ("Detail", "bd-front") not in images
    assert transfer is not None            # 타색 근거 → 색전환 메타 필수


def test_detail_refs_back_direction_falls_back_to_originals_when_no_backdetail():
    # 어느 색에도 BackDetail 없음 → 목표색 원본만 반환(구조 확대 모드는 렌더 단계 판정).
    # 반대 방향(Detail)은 근거로 빌리지 않는다 — 스펙 §5 금지열.
    p = {"colors": [{"id": "base", "isBase": True, "images": [
        {"slot": "Front", "id": "f1"}, {"slot": "Back", "id": "b1"},
        {"slot": "Detail", "id": "d1"}]}]}
    images, transfer = cg.detail_reference_images(p, None, direction="back")
    assert not any(s == "BackDetail" for s, _ in images)
    assert transfer is None
    # 목표색 이미지 목록은 그대로 유지된다 (전 슬롯 — 첨부 순서 계약 불변)
    assert ("Back", "b1") in images


def test_detail_refs_front_direction_never_borrows_backdetail():
    p = {"colors": [
        {"id": "base", "isBase": True, "images": [
            {"slot": "Front", "id": "f1"}, {"slot": "BackDetail", "id": "bd1"}]},
    ]}
    images, transfer = cg.detail_reference_images(p, None, direction="front")
    # 앞면 방향에 앞면 디테일 없음 + 빌릴 타색도 없음 → 원본 폴백, 색전환 없음
    assert transfer is None


def test_detail_refs_excludes_opposite_side_detail_even_same_color():
    # 같은 색에 앞·뒤 디테일이 다 있어도 컷 방향의 것만 첨부 — 지시 대상 모호성 제거
    p = {"colors": [{"id": "base", "isBase": True, "images": [
        {"slot": "Front", "id": "f1"}, {"slot": "Back", "id": "b1"},
        {"slot": "Detail", "id": "d1"}, {"slot": "BackDetail", "id": "bd1"},
    ]}]}
    front_images, _ = cg.detail_reference_images(p, None, direction="front")
    assert ("Detail", "d1") in front_images
    assert not any(s == "BackDetail" for s, _ in front_images)
    back_images, _ = cg.detail_reference_images(p, None, direction="back")
    assert ("BackDetail", "bd1") in back_images
    assert not any(s == "Detail" for s, _ in back_images)


def test_detail_refs_default_direction_is_front_backward_compat():
    # 기존 호출부(위치 인자 2개)와 동일 동작 — direction 미지정 = front
    p = {"colors": [{"id": "base", "isBase": True,
                     "images": [{"slot": "Detail", "id": "d1"}]}]}
    images, _ = cg.detail_reference_images(p, None)
    assert ("Detail", "d1") in images


def test_build_prompt_unknown_cut_type_raises():
    # 회귀 방지: 미상 cutType(예: 폐기 토큰 'daily')을 styling 으로 조용히 대체 렌더하지 않는다 —
    # 병렬 백엔드 머지에서 mirror 가 styling 으로 무음 폴백되던 사고의 재발 금지.
    with pytest.raises(ValueError):
        cg.build_prompt({"cutType": "daily"}, {"name": "니트"})


def test_build_prompt_mirror_is_first_class():
    p = cg.build_prompt({"cutType": "mirror", "shot": "medium"}, {"name": "골지 니트", "clothing_type": "top"})
    assert "MIRROR SELFIE" in p               # 거울샷 전용 섹션으로 렌더
    assert "${" not in p and "[[" not in p    # 토큰·섹션 마커 유출 없음
    assert "PRODUCT CONTEXT" in p and "골지 니트" in p


def test_build_prompt_respects_given_manifest():
    # 워커가 첨부 순서(마네킹→상품→매칭→무드)에 맞춰 만든 매니페스트를 그대로 쓴다
    product = {"name": "니트", "colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}
    manifest = cg.build_manifest([{"slot": "Front"}], has_mannequin=True, has_match=True, mood_count=1)
    p = cg.build_prompt({"cutType": "styling"}, product, manifest=manifest)
    assert "MANNEQUIN" in p and "MATCH" in p and "MOOD" in p


def test_pose_medium_prompt_keeps_requested_crop_authoritative():
    product = {"name": "니트", "colors": [{"isBase": True, "images": [
        {"slot": "Front", "id": "a1"},
    ]}]}
    pose_manifest = cg.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False,
        mood_count=0, example_scope="pose",
    )

    pose_prompt = cg.build_prompt(
        {"cutType": "styling", "direction": "front", "shot": "medium", "refScope": "pose"},
        product, manifest=pose_manifest,
    )
    all_prompt = cg.build_prompt(
        {"cutType": "styling", "direction": "front", "shot": "medium", "refScope": "all"},
        product,
    )

    assert "For a medium shot, preserve every pose landmark still visible" in pose_prompt
    assert "hidden lower-body landmarks do not control the crop" in pose_prompt
    assert "medium framing:" in pose_prompt
    assert "medium framing:" in all_prompt


def test_generate_applies_medium_crop_only_to_bottom_pose_scope(monkeypatch):
    calls = []

    class FakeGemini:
        async def generate_content_image(self, model, prompt, images, image_size, aspect_ratio):
            return SimpleNamespace(image=b"FULL", mime="image/png")

    async def fake_crop(settings, image, mime, clothing_type):
        calls.append((image, mime, clothing_type))
        return b"CROPPED", mime

    monkeypatch.setattr(cg.pose_crop, "crop_pose_medium", fake_crop)
    settings = make_settings(gemini_api_key="x")
    product = {"name": "바지", "clothingType": "bottom", "colors": []}
    pose_manifest = cg.build_manifest(
        [], has_mannequin=False, has_match=False, mood_count=0, example_scope="pose"
    )

    pose_result = asyncio.run(cg.generate(
        settings, FakeGemini(),
        {"cutType": "styling", "direction": "front", "shot": "medium", "refScope": "pose"},
        product, [], manifest=pose_manifest,
    ))
    top_pose_result = asyncio.run(cg.generate(
        settings, FakeGemini(),
        {"cutType": "styling", "direction": "front", "shot": "medium", "refScope": "pose"},
        {**product, "clothingType": "top"}, [], manifest=pose_manifest,
    ))
    all_result = asyncio.run(cg.generate(
        settings, FakeGemini(),
        {"cutType": "styling", "direction": "front", "shot": "medium", "refScope": "all"},
        product, [],
    ))
    bg_manifest = cg.build_manifest(
        [], has_mannequin=False, has_match=False, mood_count=0, example_scope="bg"
    )
    bg_result = asyncio.run(cg.generate(
        settings, FakeGemini(),
        {"cutType": "styling", "direction": "front", "shot": "medium", "refScope": "bg"},
        product, [], manifest=bg_manifest,
    ))
    pose_full_result = asyncio.run(cg.generate(
        settings, FakeGemini(),
        {"cutType": "styling", "direction": "front", "shot": "full", "refScope": "pose"},
        product, [], manifest=pose_manifest,
    ))

    assert pose_result == (b"CROPPED", "image/png")
    assert top_pose_result == (b"FULL", "image/png")
    assert all_result == (b"FULL", "image/png")
    assert bg_result == (b"FULL", "image/png")
    assert pose_full_result == (b"FULL", "image/png")
    assert calls == [(b"FULL", "image/png", "bottom")]


def test_all_scope_explicit_pose_and_direction_override_example():
    product = {"name": "니트", "clothingType": "top", "colors": [{"isBase": True, "images": [
        {"slot": "Front", "id": "a1"},
    ]}]}
    manifest = cg.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False,
        mood_count=0, example_scope="all",
    )
    prompt = cg.build_prompt(
        {
            "cutType": "styling", "direction": "back", "shot": "full",
            "pose": "one hand on hip", "refScope": "all", "exampleId": "ex-any",
        },
        product, manifest=manifest,
    )
    assert "USER POSE OVERRIDE" in prompt
    assert "explicit pose in the current CUT SPEC" in prompt
    assert "USER DIRECTION OVERRIDE" in prompt
    assert "direction (back)" in prompt
    assert "POSE FROM EXAMPLE" not in prompt


def test_repeated_all_example_second_use_adds_bounded_whole_body_micro_pose():
    manifest = cg.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False,
        mood_count=0, example_scope="all",
    )
    prompt = cg.build_prompt(
        {
            "cutType": "horizon", "direction": "front", "shot": "full",
            "pose": "auto", "refScope": "all", "exampleId": "same-example",
            "_exampleRepeatIndex": 1,
        },
        PRODUCT_TOP,
        manifest=manifest,
    )

    assert "REPEATED ALL-SCOPE EXAMPLE" in prompt
    assert "SECOND-USE MICRO-POSE" in prompt
    assert "hand and finger placement" in prompt
    assert "stance width, shoulder/pelvis offset, or weight" in prompt
    assert "WITHOUT reversing the original support side" in prompt
    assert "NEVER change the action, body-direction family" in prompt
    assert "${" not in prompt and "[[" not in prompt


@pytest.mark.parametrize(
    ("spec", "manifest_kwargs"),
    [
        ({
            "cutType": "horizon", "direction": "front", "shot": "full",
            "pose": "auto", "refScope": "all", "exampleId": "same-example",
            "_exampleRepeatIndex": 0,
        }, {"example_scope": "all"}),
        ({
            "cutType": "horizon", "direction": "front", "shot": "full",
            "pose": "walking", "refScope": "all", "exampleId": "same-example",
            "_exampleRepeatIndex": 1,
        }, {"example_scope": "all"}),
        ({
            "cutType": "horizon", "direction": "front", "shot": "full",
            "pose": "auto", "refScope": "pose", "exampleId": "same-example",
            "_exampleRepeatIndex": 1,
        }, {"example_scope": "pose"}),
        ({
            "cutType": "product", "direction": "front", "shot": "ghost",
            "refScope": "all", "exampleId": "same-example",
            "_exampleRepeatIndex": 1,
        }, {"example_scope": "all", "example_is_product": True}),
        ({
            "cutType": "horizon", "direction": "side", "shot": "full",
            "pose": "auto", "refScope": "all", "exampleId": "same-example",
            "_referenceDirectionCompatible": False,
            "_exampleRepeatIndex": 1,
        }, {"example_scope": "all", "reference_direction_compatible": False}),
    ],
)
def test_repeated_example_micro_pose_is_absent_outside_eligible_contract(
    spec, manifest_kwargs,
):
    manifest = cg.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False,
        mood_count=0, **manifest_kwargs,
    )
    prompt = cg.build_prompt(spec, PRODUCT_TOP, manifest=manifest)

    assert "REPEATED ALL-SCOPE EXAMPLE" not in prompt
    assert "SECOND-USE MICRO-POSE" not in prompt


def test_repeated_all_example_mirror_cut_uses_the_common_rule():
    manifest = cg.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False,
        mood_count=0, example_scope="all",
    )
    prompt = cg.build_prompt(
        {
            "cutType": "mirror", "shot": "full", "faceExposure": "hide",
            "refScope": "all", "exampleId": "mirror-example",
            "_exampleRepeatIndex": 2,
        },
        PRODUCT_TOP,
        manifest=manifest,
    )

    assert "REPEATED ALL-SCOPE EXAMPLE" in prompt
    assert "THIRD-USE MICRO-POSE" in prompt
    assert "SAME support side" in prompt


def test_all_scope_changed_direction_removes_example_pose_and_camera(monkeypatch):
    monkeypatch.setattr(
        cg,
        "load_example_asset_registry",
        lambda: (None, {"front-example": {
            "all": "unused.png", "cutType": "styling", "direction": "front",
        }}),
    )
    product = {"name": "니트", "clothingType": "top", "colors": [{
        "isBase": True, "images": [{"slot": "Front", "id": "a1"}],
    }]}
    manifest = cg.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False,
        mood_count=0, example_scope="all", reference_direction_compatible=False,
    )

    prompt = cg.build_prompt(
        {
            "cutType": "styling", "direction": "side", "shot": "full",
            "pose": "auto", "refScope": "all", "exampleId": "front-example",
        },
        product,
        manifest=manifest,
    )

    assert "DIRECTION-CHANGED STYLING/MIRROR SCENE TRANSFER" in prompt
    assert "STYLING/MIRROR ART-DIRECTION TRANSFER" not in prompt
    assert "Do NOT copy or preserve the example person's pose" in prompt
    assert "reframe the same pose" not in prompt
    assert "which example supplied the composition, camera" not in prompt
    assert "Face visibility is part of this scope's composition" not in prompt
    assert "POSE FROM EXAMPLE" not in prompt
    assert "reference: captureTone, light, scene" in prompt
    assert "reference: camera" not in prompt
    assert "source ONLY of scene, lighting, capture tone" in manifest
    assert "source of background, lighting, mood, pose" not in manifest


def test_all_scope_hidden_reference_hides_face_when_direction_matches(monkeypatch):
    monkeypatch.setattr(
        cg,
        "load_example_asset_registry",
        lambda: (None, {"hidden-front": {
            "all": "unused.png",
            "cutType": "styling",
            "direction": "front",
            "faceVisibility": "hidden",
        }}),
    )

    prompt = cg.build_prompt(
        {
            "cutType": "styling",
            "direction": "front",
            "shot": "full",
            "refScope": "all",
            "exampleId": "hidden-front",
        },
        PRODUCT_TOP,
    )

    assert "Face handling: match the reference's hidden-face composition" in prompt
    assert "no eyes, nose, or mouth may be visible" in prompt
    assert "Face handling: neutral and natural; keep the face unobtrusive" not in prompt


def test_all_scope_explicit_show_wins_over_hidden_reference(monkeypatch):
    monkeypatch.setattr(
        cg,
        "load_example_asset_registry",
        lambda: (None, {"hidden-front": {
            "all": "unused.png",
            "cutType": "styling",
            "direction": "front",
            "faceVisibility": "hidden",
        }}),
    )

    prompt = cg.build_prompt(
        {
            "cutType": "styling",
            "direction": "front",
            "shot": "full",
            "faceExposure": "show",
            "refScope": "all",
            "exampleId": "hidden-front",
        },
        PRODUCT_TOP,
    )

    assert "Face handling: the model's face may be visible" in prompt
    assert "Face handling: match the reference's hidden-face composition" not in prompt


def test_all_scope_hidden_reference_does_not_hide_face_when_direction_changes(monkeypatch):
    monkeypatch.setattr(
        cg,
        "load_example_asset_registry",
        lambda: (None, {"hidden-front": {
            "all": "unused.png",
            "cutType": "styling",
            "direction": "front",
            "faceVisibility": "hidden",
        }}),
    )

    manifest = cg.build_manifest(
        [{"slot": "Front"}],
        has_mannequin=False,
        has_match=False,
        mood_count=0,
        example_scope="all",
        reference_direction_compatible=False,
    )
    prompt = cg.build_prompt(
        {
            "cutType": "styling",
            "direction": "side",
            "shot": "full",
            "refScope": "all",
            "exampleId": "hidden-front",
        },
        PRODUCT_TOP,
        manifest=manifest,
    )

    assert "DIRECTION-CHANGED STYLING/MIRROR SCENE TRANSFER" in prompt
    assert "Face handling: neutral and natural; keep the face unobtrusive" in prompt
    assert "Face handling: match the reference's hidden-face composition" not in prompt


def test_horizon_changed_direction_uses_scene_only_reference_section():
    manifest = cg.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False,
        mood_count=0, example_scope="all", reference_direction_compatible=False,
    )
    prompt = cg.build_prompt(
        {
            "cutType": "horizon", "direction": "back", "shot": "full",
            "refScope": "all", "exampleId": "ss_front",
            "_referenceDirectionCompatible": False,
        },
        {"name": "셔츠", "clothingType": "top", "colors": [{
            "isBase": True, "images": [{"slot": "Front", "id": "a1"}],
        }]},
        manifest=manifest,
    )

    assert "DIRECTION-CHANGED HORIZON SCENE TRANSFER" in prompt
    assert "HORIZON STUDIO:" not in prompt
    assert "Follow the attached EXAMPLE REFERENCE's framing" not in prompt
    assert "reference: captureTone, light, scene" in prompt
    assert "reference: camera" not in prompt


def test_mirror_bg_includes_reflection_mechanics():
    manifest = cg.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False,
        mood_count=0, example_scope="bg",
    )
    prompt = cg.build_prompt(
        {"cutType": "mirror", "shot": "full", "refScope": "bg", "exampleId": "ex-bg"},
        {"name": "니트", "clothingType": "top", "colors": []},
        manifest=manifest,
    )
    assert "EDIT TASK — INSERT A MODEL" in prompt
    assert "MIRROR-CUT MECHANICS" in prompt
    assert "physically correct mirror reflection" in prompt


def test_space_set_medium_is_independent_camera_result_not_full_crop(monkeypatch):
    calls = []

    class FakeGemini:
        async def generate_content_image(self, model, prompt, images, image_size, aspect_ratio):
            assert "medium framing:" in prompt
            assert "PUBLISHED SPACE-SET LOCATION" in prompt
            return SimpleNamespace(image=b"MEDIUM", mime="image/png")

    async def fake_crop(settings, image, mime):
        calls.append((image, mime))
        return b"CROPPED", mime

    monkeypatch.setattr(cg.pose_crop, "crop_pose_medium", fake_crop)
    settings = make_settings(gemini_api_key="x")
    manifest = cg.build_manifest(
        [],
        has_mannequin=False,
        has_match=False,
        mood_count=0,
        example_scope="pose",
        has_space_set_plate=True,
    )
    result = asyncio.run(
        cg.generate(
            settings,
            FakeGemini(),
            {
                "cutType": "styling",
                "direction": "side",
                "shot": "medium",
                "refScope": "pose",
                "exampleId": "ss_member-02",
                "spaceGroupId": "ssg1__set-01__instance-01",
            },
            {"name": "니트", "clothingType": "top"},
            [],
            manifest=manifest,
        )
    )

    assert result == (b"MEDIUM", "image/png")
    assert calls == []
    assert manifest.splitlines()[-2].startswith("1. SPACE SET PLATE")
    assert manifest.splitlines()[-1].startswith("2. POSE CONTROL")
    assert "SPACE SET PLATE exclusively controls the location and background" in manifest


def test_horizon_sequence_without_plate_does_not_claim_one_shared_location():
    manifest = cg.build_manifest(
        [],
        has_mannequin=False,
        has_match=False,
        mood_count=0,
        example_scope="pose",
        has_space_set_plate=False,
    )
    prompt = cg.build_prompt(
        {
            "cutType": "horizon",
            "direction": "front",
            "shot": "full",
            "refScope": "pose",
            "exampleId": "ss_horizon-01",
            "spaceGroupId": "ssg1__horizon-sequence-01__instance-01",
            "_spaceSetContinuity": False,
        },
        {"name": "니트", "clothingType": "top"},
        manifest=manifest,
    )

    assert "POSE CONTROL" in manifest
    assert "SPACE SET PLATE" not in manifest
    assert "SPACE CONTINUITY" not in prompt
    assert "PUBLISHED SPACE-SET LOCATION" not in prompt


def test_build_manifest_places_exact_virtual_model_labels_after_mannequin():
    manifest = cg.build_manifest(
        [{"slot": "Front"}], has_mannequin=True, has_match=True, mood_count=1,
        has_model_face=True, has_model_full_body=True)
    assert manifest.splitlines() == [
        "1. MANNEQUIN — coarse worn-geometry prior only where seller PRODUCT pixels support it; ZERO authority to resolve uncertain color, material, construction, fit or length",
        "2. MODEL FACE — facial identity authority for the selected model ONLY: preserve facial identity and facial features; ZERO authority over height, head-to-body ratio, shoulders, torso, waist, pelvis, limb proportions, body shape, pose, framing or clothing",
        "3. MODEL FULL BODY — full-body proportion authority for the selected model ONLY: preserve height, head-to-body ratio, shoulder width and slope, torso length and build, waist, pelvis and hip width, and arm and leg proportions; ZERO authority over facial identity, facial features, hair, pose, framing or clothing",
        "4. PRODUCT — front view of the garment",
        "5. MATCHING — the user-selected coordinating garment worn in the same outfit",
        "6. MOOD — reference for lighting/color/ambience ONLY (never copy its garment, person or framing)",
    ]


def test_build_manifest_does_not_disguise_face_grid_as_full_body():
    manifest = cg.build_manifest(
        [], has_mannequin=False, has_match=False, mood_count=0,
        has_model_face=True, has_model_sheet=True,
    )

    assert "MODEL FULL BODY" not in manifest
    assert "MODEL — frontal close-up" in manifest
    assert "MODEL SHEET — a 2x2 grid of four studio portraits" in manifest
    assert manifest.count("ZERO authority over body shape or proportions") == 2


def test_virtual_model_full_body_resolution_is_atomic(monkeypatch):
    monkeypatch.setattr(cg, "load_virtual_model_registry", lambda: {
        "complete": {"views": {
            "face_front": {"key": "face", "mime": "image/png"},
            "grid_sedcard": {"key": "face-grid", "mime": "image/png"},
            "body_front": {"key": "body", "mime": "image/png"},
        }},
        "face-only": {"views": {
            "face_front": {"key": "face", "mime": "image/png"},
            "grid_sedcard": {"key": "face-grid", "mime": "image/png"},
        }},
        "body-only": {"views": {
            "body_front": {"key": "body", "mime": "image/png"},
        }},
    })

    assert cg.resolve_virtual_model_assets(
        {"cutType": "styling", "modelId": "complete"}, require_full_body=True,
    ) == (
        {"key": "face", "mime": "image/png", "bucket": "public"},
        {"key": "body", "mime": "image/png", "bucket": "public"},
    )
    assert cg.resolve_virtual_model_assets(
        {"cutType": "styling", "modelId": "face-only"}, require_full_body=True,
    ) is None
    assert cg.resolve_virtual_model_assets(
        {"cutType": "styling", "modelId": "body-only"}, require_full_body=True,
    ) is None


def test_confirmed_gpt_direction_sheet_resolution_is_exact_and_fail_closed(monkeypatch):
    monkeypatch.setattr(cg, "load_virtual_model_registry", lambda: {
        "complete": {"views": {
            "face_front": {"key": "wrong-face", "mime": "image/png"},
            "body_front": {"key": "wrong-body", "mime": "image/png"},
            "grid_face_direction": {
                "key": "face-directions", "mime": "image/png",
                "byteLength": 123, "sha256": "a" * 64,
            },
            "grid_fullbody": {
                "key": "body-directions", "mime": "image/jpeg",
                "byteLength": 456, "sha256": "b" * 64,
            },
        }},
        "legacy-only": {"views": {
            "face_front": {"key": "face", "mime": "image/png"},
            "body_front": {"key": "body", "mime": "image/png"},
        }},
    })

    assert cg.resolve_confirmed_gpt_direction_sheets(
        {"cutType": "styling", "modelId": "complete"}
    ) == (
        {
            "key": "face-directions", "mime": "image/png", "bucket": "public",
            "byteLength": 123, "sha256": "a" * 64,
        },
        {
            "key": "body-directions", "mime": "image/jpeg", "bucket": "public",
            "byteLength": 456, "sha256": "b" * 64,
        },
    )
    with pytest.raises(ValueError, match="missing_grid_face_direction"):
        cg.resolve_confirmed_gpt_direction_sheets(
            {"cutType": "styling", "modelId": "legacy-only"}
        )
    with pytest.raises(ValueError, match="require_worn_cut_model"):
        cg.resolve_confirmed_gpt_direction_sheets(
            {"cutType": "product", "modelId": "complete"}
        )


@pytest.mark.parametrize(
    "bad_field,bad_value",
    [
        ("byteLength", 0),
        ("byteLength", True),
        ("sha256", "A" * 64),
        ("sha256", "a" * 63),
    ],
)
def test_confirmed_gpt_direction_sheet_resolution_rejects_unsealed_assets(
    monkeypatch, bad_field, bad_value,
):
    valid = {
        "key": "directions", "mime": "image/png",
        "byteLength": 123, "sha256": "a" * 64,
    }
    bad = dict(valid)
    bad[bad_field] = bad_value
    monkeypatch.setattr(cg, "load_virtual_model_registry", lambda: {
        "model": {"views": {
            "grid_face_direction": bad,
            "grid_fullbody": valid,
        }},
    })

    with pytest.raises(ValueError, match="missing_grid_face_direction"):
        cg.resolve_confirmed_gpt_direction_sheets(
            {"cutType": "styling", "modelId": "model"}
        )


@pytest.mark.parametrize(
    "manifest_kwargs",
    [
        {"example_scope": "all"},
        {"example_scope": "all", "reference_direction_compatible": False},
        {"example_scope": "all", "example_is_product": True},
        {"example_scope": "pose"},
        {"example_scope": "pose", "has_space_set_plate": True},
        {"example_scope": "bg"},
    ],
)
def test_generation_examples_have_zero_face_and_body_authority(manifest_kwargs):
    manifest = cg.build_manifest(
        [{"slot": "Front"}],
        has_mannequin=False,
        has_match=False,
        mood_count=0,
        **manifest_kwargs,
    )

    assert "ZERO authority over facial identity or facial features" in manifest
    assert "ZERO authority over body morphology: height, head-to-body ratio" in manifest
    assert "shoulder width and build, torso length and build, waist shape" in manifest
    assert "pelvis and hip width, or limb proportions" in manifest


def test_build_manifest_emits_one_label_per_attached_matching_garment():
    manifest = cg.build_manifest(
        [{"slot": "Front"}],
        has_mannequin=False,
        has_match=True,
        matching_count=2,
        mood_count=0,
    )

    assert manifest.splitlines() == [
        "1. PRODUCT — front view of the garment",
        "2. MATCHING — the user-selected coordinating garment worn in the same outfit",
        "3. MATCHING — the user-selected coordinating garment worn in the same outfit",
    ]


def test_pose_manifest_keeps_product_matching_pose_relative_order():
    manifest = cg.build_manifest(
        [{"slot": "Front"}], has_mannequin=True, has_match=True, mood_count=1,
        example_scope="pose",
    )
    lines = manifest.splitlines()
    product_index = next(i for i, line in enumerate(lines) if "front view of the garment" in line)
    matching_index = next(i for i, line in enumerate(lines) if "MATCHING —" in line)
    pose_index = next(i for i, line in enumerate(lines) if "POSE CONTROL" in line)

    assert product_index < matching_index < pose_index
    assert pose_index == len(lines) - 1


def test_build_prompt_injects_fit_profile_and_drops_legacy_fit():
    # 확정 fitProfile(마네킹 단계 산출물)을 텍스트 제약으로 이중 전달 — 마네킹 참조와 원본
    # 사진 인상이 충돌할 때 순종률 확보(컷 파이프라인 계약). 프로필 있으면 레거시 '- Fit:' 생략.
    product = {"name": "니트", "clothing_type": "top",
               "colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}
    analysis = {"fit": "regular", "fitProfile": {
        "category": "top", "gender": "women",
        "axes": {"fit": "over", "length": None},
    }}
    p = cg.build_prompt({"cutType": "styling", "direction": "front", "shot": "full"},
                        product, analysis=analysis)
    assert "FIT PROFILE (declared target axes" in p
    assert "- fit: oversized volume" in p
    assert p.index("FIT PROFILE") < p.index("PRODUCT CONTEXT")
    assert "- Fit: regular" not in p


@pytest.mark.parametrize(
    ("matching_profile", "matching_line"),
    [
        ({"matchCut": "wide"}, "- matching bottom"),
        ({
            "version": 2,
            "matchingFit": {
                "clothingId": "skirt-1",
                "fitCategory": "skirt",
                "axes": {"silhouette": "a_line"},
            },
        }, "- matching skirt silhouette"),
    ],
)
def test_build_prompt_matching_fit_requires_bottom_on_screen(matching_profile, matching_line):
    # v1/v2 매칭 축은 별도 의류가 화면에 있을 때만(마네킹 참조 or MATCH 첨부) 렌더한다.
    product = {"name": "니트", "clothing_type": "top",
               "colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}
    analysis = {"fitProfile": {
        "category": "top", "gender": "women",
        "axes": {"fit": "regular", "length": None}, **matching_profile,
    }}
    spec = {"cutType": "styling", "direction": "front", "shot": "full"}

    with_mannequin = cg.build_manifest(
        [{"slot": "Front"}], has_mannequin=True, has_match=False, mood_count=0)
    p1 = cg.build_prompt(spec, product, analysis=analysis, manifest=with_mannequin)
    assert matching_line in p1

    with_match = cg.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=True, mood_count=0)
    p2 = cg.build_prompt(spec, product, analysis=analysis, manifest=with_match)
    assert matching_line in p2

    neither = cg.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False, mood_count=0)
    p3 = cg.build_prompt(spec, product, analysis=analysis, manifest=neither)
    assert matching_line not in p3
    assert "- fit:" in p3   # 나머지 축은 유지


# ── FaceMarket 라이선스 얼굴 주입 (FM-31) ────────────────────────────────────
PRODUCT_TOP = {"name": "니트", "clothing_type": "top",
               "colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}


def test_wants_face_only_for_cuts_that_actually_show_a_face():
    # 첨부 판정 = 얼굴이 실제로 프레임에 담기는 컷만. 라이선스료를 내고도 가려지는 컷에
    # 얼굴을 붙이면 토큰 낭비 + 지시 충돌(FACE:hide vs 얼굴 첨부)이 된다.
    assert cg.wants_face({"cutType": "styling", "shot": "full"}, "top") is True
    assert cg.wants_face({"cutType": "horizon", "shot": "medium"}, "top") is True
    assert cg.wants_face({"cutType": "styling", "shot": "full", "faceExposure": "show"}, "top") is True

    # product = 사람·신체 노출 금지([[CUT:product]]) → faceExposure=None
    assert cg.wants_face({"cutType": "product", "shot": "ghost"}, "top") is False
    # 거울샷 기본 = 폰이 얼굴을 가림(hide). 명시적 show 일 때만 첨부.
    assert cg.wants_face({"cutType": "mirror", "shot": "full"}, "top") is False
    assert cg.wants_face({"cutType": "mirror", "shot": "full", "faceExposure": "show"}, "top") is True
    # 셀러가 명시적으로 비식별을 골랐으면 존중
    assert cg.wants_face({"cutType": "styling", "shot": "full", "faceExposure": "hide"}, "top") is False
    # 뒷모습 = 얼굴이 프레임 밖
    assert cg.wants_face({"cutType": "styling", "shot": "full", "direction": "back"}, "top") is False
    # 중간샷은 상의 프레이밍에 머리가 있고, 하의 프레이밍에는 없다.
    assert cg.wants_face({"cutType": "styling", "shot": "medium"}, "bottom") is False
    assert cg.wants_face({"cutType": "styling", "shot": "medium"}, "top") is True


def test_wants_face_unknown_cut_type_is_false_not_raise():
    # 회귀 방지: 여기서 ValueError 가 새면 워커의 준비 루프가 통째로 죽어 **잡 전체**가
    # 실패한다. 현행 계약은 '미상 컷 = 그 컷만 빈 슬롯'.
    assert cg.wants_face({"cutType": "daily"}, "top") is False


def test_build_manifest_places_face_after_garment_truth_before_mood():
    # images 는 역할 메타가 없는 위치 리스트 — 워커 첨부 순서와 이 목록이 lockstep 이어야 한다.
    # 얼굴은 옷 근거(마네킹·상품·매칭) 뒤, 무드 앞.
    m = cg.build_manifest([{"slot": "Front"}], has_mannequin=True, has_match=True,
                          mood_count=1, has_face=True)
    lines = m.split("\n")
    assert len(lines) == 5
    assert "MANNEQUIN" in lines[0] and lines[0].startswith("1.")
    assert "front view of the garment" in lines[1]
    assert lines[2].startswith("3. MATCH")
    assert lines[3].startswith("4. MODEL FACE")
    assert lines[4].startswith("5. MOOD")


def test_face_label_does_not_trip_match_cut_guard():
    # _FACE_LABEL 이 마네킹/매칭 라벨의 부분문자열이 되면 matchCut 가드가 오발해
    # 화면에 없는 하의의 핏을 지시 → 모델이 하의를 지어낸다.
    assert cg._MANNEQUIN_LABEL not in cg._FACE_LABEL
    assert cg._MATCH_LABEL not in cg._FACE_LABEL

    analysis = {"fitProfile": {"category": "top", "gender": "women",
                               "axes": {"fit": "regular", "length": None}, "matchCut": "wide"}}
    face_only = cg.build_manifest([{"slot": "Front"}], has_mannequin=False, has_match=False,
                                  mood_count=0, has_face=True)
    p = cg.build_prompt({"cutType": "styling", "shot": "full"}, PRODUCT_TOP,
                        analysis=analysis, manifest=face_only, has_face=True)
    assert "- matching bottom" not in p


def test_build_prompt_with_face_injects_identity_and_overrides_face_line():
    # 얼굴을 첨부하면서 기본 FACE:same('keep the face unobtrusive')을 그대로 두면
    # 라이선스료를 내고 "얼굴을 가려라"를 지시받는 자기모순이 된다.
    manifest = cg.build_manifest([{"slot": "Front"}], has_mannequin=False, has_match=False,
                                 mood_count=0, has_face=True)
    p = cg.build_prompt({"cutType": "styling", "shot": "full"}, PRODUCT_TOP,
                        manifest=manifest, has_face=True)
    assert "MODEL FACE IDENTITY CONTINUITY" in p       # [[IDENTITY_REF]] 정체성 지시
    assert "LICENSED MODEL FACE" in p                  # [[FACE_REF]] 라이선스 지시
    assert "recognizably that same individual" in p
    assert "the real person in the MODEL FACE reference" in p   # [[FACE:licensed]]
    assert "keep the face unobtrusive" not in p        # FACE:same 오버라이드됨
    assert "${" not in p and "[[" not in p             # 토큰·마커 유출 없음
    # 옷 근거가 여전히 최우선 — 얼굴이 옷 지시를 밀어내지 않는다
    assert "GARMENT FIDELITY" in p and "the references win" in p


def test_virtual_model_manifest_injects_generic_identity_without_licensed_claims():
    manifest = cg.build_manifest(
        [{"slot": "Front"}],
        has_mannequin=False,
        has_match=False,
        mood_count=0,
        has_model_face=True,
        has_model_full_body=True,
    )

    p = cg.build_prompt(
        {"cutType": "styling", "shot": "full", "direction": "front"},
        PRODUCT_TOP,
        manifest=manifest,
        has_face=False,
    )

    assert "MODEL FACE IDENTITY CONTINUITY" in p
    assert "MODEL FULL-BODY CONTINUITY" in p
    assert "All attached face-identity references depict the SAME one model" in p
    assert "MODEL FACE and MODEL FULL BODY depict the SAME selected model" in p
    assert "Treat them as one atomic pair" in p
    assert "sole authority for the selected model's underlying build" in p
    assert "height, head-to-body ratio" in p
    assert "shoulder width and slope" in p
    assert "torso length and build" in p
    assert "waist, pelvis and hip width" in p
    assert "arm length and leg" in p
    assert "real, identity-verified person whose face is licensed" not in p
    assert "Face handling: the model is the real person" not in p


def test_product_cut_never_injects_identity_even_if_manifest_is_malformed():
    manifest = cg.build_manifest(
        [{"slot": "Front"}],
        has_mannequin=False,
        has_match=False,
        mood_count=0,
        has_model_face=True,
        has_model_sheet=True,
        has_face=True,
    )

    p = cg.build_prompt(
        {"cutType": "product", "shot": "ghost", "direction": "front"},
        PRODUCT_TOP,
        manifest=manifest,
        has_face=True,
    )

    assert "MODEL IDENTITY CONTINUITY" not in p
    assert "LICENSED MODEL FACE" not in p
    assert "Face handling: the model is the real person" not in p


def test_hidden_back_cut_keeps_generic_identity_without_revealing_face():
    manifest = cg.build_manifest(
        [{"slot": "Back"}],
        has_mannequin=False,
        has_match=False,
        mood_count=0,
        has_model_face=True,
        has_model_sheet=True,
    )

    p = cg.build_prompt(
        {
            "cutType": "styling",
            "shot": "full",
            "direction": "back",
            "faceExposure": "hide",
        },
        PRODUCT_TOP,
        manifest=manifest,
        has_face=False,
    )

    assert "MODEL FACE IDENTITY CONTINUITY" in p
    assert "MODEL FULL-BODY CONTINUITY" not in p
    assert "KEEP the face hidden" in p
    assert "Camera angle: from behind" in p
    assert "Face handling: frame or turn the head so the face is not identifiable" in p
    assert "LICENSED MODEL FACE" not in p
    assert "keep their face in frame, visible and recognizable" not in p


def test_build_prompt_face_ignored_on_cuts_that_hide_the_face():
    # 방어선 이중화: 호출자가 has_face=True 를 잘못 넘겨도 얼굴이 안 담기는 컷이면
    # 정체성 지시를 렌더하지 않는다(첨부 판정과 동일 규칙 _face_fits).
    for spec in ({"cutType": "product", "shot": "ghost"},
                 {"cutType": "mirror", "shot": "full"},
                 {"cutType": "styling", "shot": "full", "direction": "back"}):
        p = cg.build_prompt(spec, PRODUCT_TOP, has_face=True)
        assert "MODEL IDENTITY" not in p
        assert "MODEL FACE" not in p
        assert "${" not in p and "[[" not in p


def test_build_prompt_without_face_is_unchanged_from_legacy():
    # 라이선스 없는 기존 경로 무변경 — 얼굴 관련 문구가 한 글자도 새지 않는다.
    p = cg.build_prompt({"cutType": "styling", "shot": "full"}, PRODUCT_TOP)
    assert "MODEL FACE" not in p and "MODEL IDENTITY" not in p
    assert "licensed" not in p
    assert "Face handling: neutral and natural; keep the face unobtrusive." in p  # FACE:same 유지
    assert "${" not in p and "[[" not in p


def test_build_prompt_accepts_optional_server_directing_profile():
    profile = {
        "directionMode": "exact",
        "poseDynamics": "natural_asymmetry",
        "capture": "phone_snapshot",
        "light": "reference_integrated",
    }
    p = cg.build_prompt(
        {"cutType": "styling", "shot": "full", "direction": "front"},
        PRODUCT_TOP,
        directing_profile=profile,
    )

    assert "SERVER DIRECTING PROFILE" in p
    assert "ordinary phone snapshot" in p
    assert "believable human asymmetry" in p
    assert "seller PRODUCT/MANNEQUIN references are the sole truth" in p
    assert p.index("SERVER DIRECTING PROFILE") < p.index("PRODUCT CONTEXT")


def test_build_prompt_user_direction_and_named_pose_override_profile():
    p = cg.build_prompt(
        {
            "cutType": "styling",
            "shot": "full",
            "direction": "back",
            "pose": "one hand on hip",
            "_referenceDirectionCompatible": False,
        },
        PRODUCT_TOP,
        directing_profile={
            "directionMode": "exact",
            "poseDynamics": "reference_kinematics",
            "capture": "phone_snapshot",
        },
    )

    assert "Direction relationship: RETARGET to the requested back view" in p
    assert "reference's visible balance" not in p
    assert "Pose: one hand on hip" in p


def test_generate_forwards_directing_profile_to_rendered_prompt():
    class FakeGemini:
        async def generate_content_image(self, model, prompt, images, image_size, aspect_ratio):
            assert "SERVER DIRECTING PROFILE" in prompt
            assert "ordinary phone snapshot" in prompt
            return SimpleNamespace(image=b"PROFILE", mime="image/png")

    result = asyncio.run(
        cg.generate(
            make_settings(gemini_api_key="x"),
            FakeGemini(),
            {"cutType": "styling", "shot": "full", "direction": "front"},
            PRODUCT_TOP,
            [],
            directing_profile={"capture": "phone_snapshot"},
        )
    )
    assert result == (b"PROFILE", "image/png")


def test_stage1_and_local_stage2_share_caller_selected_model_and_detail_4k():
    calls = []

    class FakeGemini:
        async def generate_content_image(
            self, model, prompt, images, image_size, aspect_ratio,
        ):
            calls.append({
                "model": model,
                "prompt": prompt,
                "images": images,
                "imageSize": image_size,
                "aspectRatio": aspect_ratio,
            })
            return SimpleNamespace(image=f"OUT-{len(calls)}".encode(), mime="image/png")

    settings = make_settings(
        gemini_api_key="x",
        model_image_high="gpt-image-2-2026-04-21",
        mannequin_image_size="1K",
        detail_cut_image_size="4K",
        mannequin_aspect_ratio="2:3",
    )
    spec = {"cutType": "styling", "shot": "full", "direction": "front"}
    product = {"name": "니트", "clothingType": "top", "colors": []}
    stage1 = asyncio.run(cg.generate(settings, FakeGemini(), spec, product, []))
    stage2 = asyncio.run(cg.repair(
        settings,
        FakeGemini(),
        spec,
        product,
        cg.InlineImage("image/png", stage1[0]),
        qc_corrections=(
            "Restore the storyboard shot, direction, face handling, and effective pose; "
            "storyboard controls override example pixels.",
        ),
    ))

    assert stage1 == (b"OUT-1", "image/png")
    assert stage2 == (b"OUT-2", "image/png")
    assert [call["model"] for call in calls] == [
        "gpt-image-2-2026-04-21", "gpt-image-2-2026-04-21",
    ]
    assert [call["imageSize"] for call in calls] == ["4K", "4K"]
    assert [call["aspectRatio"] for call in calls] == ["2:3", "2:3"]
    assert calls[0]["images"] == []
    assert [image.data for image in calls[1]["images"]] == [b"OUT-1"]
    assert "AG-06 second-stage" in calls[1]["prompt"]
    assert "do not reconstruct a different photograph" in calls[1]["prompt"]


def test_default_cut_route_keeps_editor_on_shared_high_tier():
    calls = []

    class FakeGemini:
        async def generate_content_image(
            self, model, prompt, images, image_size, aspect_ratio,
        ):
            calls.append(model)
            return SimpleNamespace(image=b"OUT", mime="image/png")

    settings = make_settings(
        model_image_high="gemini-3-pro-image",
        model_detail_cut="",
    )
    asyncio.run(cg.generate(
        settings,
        FakeGemini(),
        {"cutType": "styling", "shot": "full", "direction": "front"},
        {"name": "니트", "clothingType": "top", "colors": []},
        [],
    ))

    assert calls == ["gemini-3-pro-image"]


def test_qc_repair_prompt_rejects_empty_or_oversized_corrections():
    spec = {"cutType": "styling", "shot": "full", "direction": "front"}
    product = {"clothingType": "top"}
    with pytest.raises(ValueError, match="invalid_qc_corrections"):
        cg.build_qc_repair_prompt(spec, product, ())
    with pytest.raises(ValueError, match="invalid_qc_corrections"):
        cg.build_qc_repair_prompt(spec, product, ("x" * 241,))


def test_face_ref_token_always_substituted_on_every_path():
    # ${faceRefLine} 미치환은 render 의 leftover 가드 → ValueError → _gen_cuts 가 삼켜
    # **전 컷 빈 슬롯 + 전액 미차감**으로 조용히 죽는다. 모든 컷 조합에서 치환을 확인.
    for cut in cg.CUT_TYPES:
        for has_face in (False, True):
            p = cg.build_prompt({"cutType": cut}, PRODUCT_TOP, has_face=has_face)
            assert "${faceRefLine}" not in p and "${" not in p
