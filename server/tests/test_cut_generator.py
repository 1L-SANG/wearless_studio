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


def test_build_prompt_manifest_fallback_no_images():
    p = cg.build_prompt({"cutType": "product"}, {"name": "니트", "colors": []})
    assert "${imageManifest}" not in p
    assert "product photos" in p.lower()


def test_build_prompt_product_detail_requires_detail_slot_across_all_colors():
    product = {"name": "니트", "colors": [
        {"id": "base", "isBase": True, "images": [{"slot": "Front", "id": "a1"}]},
        {"id": "other", "images": [{"slot": "Back", "id": "a2"}]},
    ]}
    with pytest.raises(ValueError, match="detail_reference_required"):
        cg.build_prompt({"cutType": "product", "shot": "detail"}, product)


def test_build_prompt_product_detail_uses_detail_slot():
    product = {"name": "니트", "colors": [{"isBase": True, "images": [
        {"slot": "Front", "id": "a1"}, {"slot": "Detail", "id": "a2"},
    ]}]}
    prompt = cg.build_prompt({"cutType": "product", "shot": "detail"}, product)
    assert "detail close-up of the garment" in prompt
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
    assert "PRODUCT — detail close-up" in prompt
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
    assert "PRODUCT — detail close-up" in prompt
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
    assert "worn on a mannequin" in p and "MATCH" in p and "MOOD" in p


def test_pose_medium_prompt_generates_full_frame_before_deterministic_crop():
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

    assert "full body from head to feet" in pose_prompt
    assert "medium framing:" not in pose_prompt
    assert "medium framing:" in all_prompt


def test_generate_applies_medium_crop_only_to_pose_scope(monkeypatch):
    calls = []

    class FakeGemini:
        async def generate_content_image(self, model, prompt, images, image_size, aspect_ratio):
            return SimpleNamespace(image=b"FULL", mime="image/png")

    async def fake_crop(settings, image, mime):
        calls.append((image, mime))
        return b"CROPPED", mime

    monkeypatch.setattr(cg.pose_crop, "crop_pose_medium", fake_crop)
    settings = make_settings(gemini_api_key="x")
    product = {"name": "니트", "colors": []}
    pose_manifest = cg.build_manifest(
        [], has_mannequin=False, has_match=False, mood_count=0, example_scope="pose"
    )

    pose_result = asyncio.run(cg.generate(
        settings, FakeGemini(),
        {"cutType": "styling", "direction": "front", "shot": "medium", "refScope": "pose"},
        product, [], manifest=pose_manifest,
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
    assert all_result == (b"FULL", "image/png")
    assert bg_result == (b"FULL", "image/png")
    assert pose_full_result == (b"FULL", "image/png")
    assert calls == [(b"FULL", "image/png")]


def test_build_manifest_places_exact_model_labels_after_mannequin():
    manifest = cg.build_manifest(
        [{"slot": "Front"}], has_mannequin=True, has_match=True, mood_count=1,
        has_model_face=True, has_model_sheet=True)
    assert manifest.splitlines() == [
        "1. PRODUCT — the garment worn on a mannequin (verified colors, fit and length — follow this)",
        "2. MODEL — frontal close-up of the model (identity ground truth; do NOT copy this image's pose, framing, or clothing)",
        "3. MODEL SHEET — a 2x2 grid of four studio portraits of the SAME single person (identity reference only). Do NOT copy the grid layout, framing, poses, or clothing; the output must be one single normal photograph, never a grid",
        "4. PRODUCT — front view of the garment",
        "5. MATCHING — the user-selected coordinating garment worn in the same outfit",
        "6. MOOD — reference for lighting/color/ambience ONLY (never copy its garment, person or framing)",
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
    assert "mannequin" in lines[0] and lines[0].startswith("1.")
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
    assert "MODEL IDENTITY" in p                       # [[FACE_REF]] 정체성 지시
    assert "recognizably that same individual" in p
    assert "the real person in the MODEL FACE reference" in p   # [[FACE:licensed]]
    assert "keep the face unobtrusive" not in p        # FACE:same 오버라이드됨
    assert "${" not in p and "[[" not in p             # 토큰·마커 유출 없음
    # 옷 근거가 여전히 최우선 — 얼굴이 옷 지시를 밀어내지 않는다
    assert "GARMENT FIDELITY" in p and "the references win" in p


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


def test_face_ref_token_always_substituted_on_every_path():
    # ${faceRefLine} 미치환은 render 의 leftover 가드 → ValueError → _gen_cuts 가 삼켜
    # **전 컷 빈 슬롯 + 전액 미차감**으로 조용히 죽는다. 모든 컷 조합에서 치환을 확인.
    for cut in cg.CUT_TYPES:
        for has_face in (False, True):
            p = cg.build_prompt({"cutType": cut}, PRODUCT_TOP, has_face=has_face)
            assert "${faceRefLine}" not in p and "${" not in p
