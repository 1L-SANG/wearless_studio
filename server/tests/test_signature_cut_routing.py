"""시그니처 컷(상세페이지 첫 화면) 전용 모델·프롬프트 분기.

오너 확정(2026-08-17): 첫 화면 컷만 GPT-image 계열로 생성하고, 배경은 착장 의류 색의
연한 톤, 구도는 얼굴 일부만 보이는 극단 클로즈업으로 간다. 나머지 컷은 기존 그대로다.
판정은 프론트 전용 풀(signatureCutPool)이 붙이는 exampleId 의 'sig_' 접두로 한다 —
저장 계약에 새 필드를 더하지 않는다.
"""

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agents import cut_generator, horizon_background
from app.agents.model_routing import resolve_model
from app.workers import detail_page_job


def _settings(**overrides):
    base = dict(
        model_image_light="gemini-3-flash-image",
        model_image_high="gemini-3-pro-image",
        model_image_signature="gpt-image-2",
        model_text="gemini-3.7-flash",
        mannequin_image_size="2K",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_signature_example_id_is_detected():
    assert cut_generator.is_signature_cut({"exampleId": "sig_women_03"}) is True
    assert cut_generator.is_signature_cut({"example_id": "sig_men_01"}) is True
    assert cut_generator.is_signature_cut({"exampleId": "ex_styling_women_top_full_01"}) is False
    assert cut_generator.is_signature_cut({"exampleId": None}) is False
    assert cut_generator.is_signature_cut({}) is False


def test_signature_tier_resolves_to_gpt_image_and_others_stay():
    settings = _settings()
    assert resolve_model(settings, "image_signature") == "gpt-image-2"
    assert resolve_model(settings, "image_high") == "gemini-3-pro-image"
    assert resolve_model(settings, "image_light") == "gemini-3-flash-image"


def test_signature_tier_is_overridable_by_env_injected_setting():
    assert resolve_model(_settings(model_image_signature="gpt-image-3"), "image_signature") == "gpt-image-3"


def test_partial_settings_fall_back_to_image_high():
    """테스트·부분 설정 객체가 새 키를 안 가질 수 있다 — 기존 관례대로 안전 조회."""
    partial = SimpleNamespace(
        model_image_light="a", model_image_high="b", model_text="c", mannequin_image_size="2K",
    )
    assert resolve_model(partial, "image_signature") == "b"


def test_signature_direction_covers_composition_and_background():
    direction = cut_generator.SIGNATURE_DIRECTION
    lowered = direction.lower()
    # 구도: 극단 클로즈업 + 얼굴 일부만
    assert "extreme close" in lowered
    assert "part of the face" in lowered
    # 배경: 의류 색 계열의 밝고 낮은 채도, 디테일 없음
    assert "same hue family as the garment" in lowered
    assert "desaturated" in lowered
    assert "detail-free" in lowered


def test_signature_falls_back_when_openai_key_is_missing(monkeypatch):
    """프로덕션에 OPENAI_API_KEY 가 아직 없다 — 첫 화면이 빈칸이 되는 대신 기존 모델로 떨어진다."""
    from app.agents import cut_generator as cg

    with_key = _settings(openai_api_key="sk-test")
    without_key = _settings(openai_api_key=None)
    spec = {"exampleId": "sig_women_01"}

    def chosen(settings):
        model = resolve_model(settings, "image_high")
        if cg.is_signature_cut(spec):
            sig = resolve_model(settings, "image_signature")
            if not sig.startswith("gpt-image") or getattr(settings, "openai_api_key", None):
                model = sig
        return model

    assert chosen(with_key) == "gpt-image-2"
    assert chosen(without_key) == "gemini-3-pro-image"
    # gpt 계열이 아닌 모델로 바꿔 두면 키와 무관하게 그대로 쓴다.
    assert chosen(_settings(model_image_signature="gemini-3-pro-image", openai_api_key=None)) == "gemini-3-pro-image"


def test_signature_gpt_model_accepts_fallback_only_key():
    settings = _settings(openai_api_key=None, openai_api_key_fallback="owner-key")

    assert cut_generator._resolve_generation_model(
        settings, {"exampleId": "sig_women_01"}
    ) == "gpt-image-2"


def test_signature_stage1_and_local_stage2_use_the_same_gpt_tier():
    calls = []

    class FakeGemini:
        async def generate_content_image(
            self, model, prompt, images, image_size, aspect_ratio,
        ):
            calls.append(model)
            return SimpleNamespace(image=f"OUT-{len(calls)}".encode(), mime="image/png")

    settings = _settings(
        openai_api_key="sk-test",
        detail_cut_image_size="4K",
        mannequin_aspect_ratio="2:3",
    )
    spec = {
        "cutType": "styling",
        "shot": "medium",
        "direction": "front",
        "exampleId": "sig_women_01",
    }
    product = {"name": "니트", "clothingType": "top", "colors": []}
    stage1 = asyncio.run(
        cut_generator.generate(settings, FakeGemini(), spec, product, [])
    )
    asyncio.run(cut_generator.repair(
        settings,
        FakeGemini(),
        spec,
        product,
        cut_generator.InlineImage("image/png", stage1[0]),
        qc_corrections=("Restore only the failed lighting integration.",),
    ))

    assert calls == ["gpt-image-2", "gpt-image-2"]


def test_openai_input_is_normalized_to_png():
    """OpenAI images/edits 는 MPO(확장자만 .jpeg 인 아이폰 다중사진)를 거부한다.

    실측(2026-08-17): 같은 사진이 Gemini 성공 / OpenAI 400 invalid_image_file.
    셀러 사진은 대부분 휴대폰 촬영본이라, 보내기 직전 표준 RGB PNG 로 통일한다.
    """
    from io import BytesIO

    from PIL import Image

    from app.agents.gemini_image import InlineImage, _as_openai_png

    # 표준 PNG 는 재인코딩하지 않고 그대로 통과시킨다.
    buf = BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, "PNG")
    png = buf.getvalue()
    assert _as_openai_png(InlineImage("image/png", png)) is png

    # JPEG·팔레트 등은 PNG 로 변환된다.
    buf = BytesIO()
    Image.new("RGB", (8, 8), (200, 100, 50)).save(buf, "JPEG")
    converted = _as_openai_png(InlineImage("image/jpeg", buf.getvalue()))
    assert Image.open(BytesIO(converted)).format == "PNG"
    assert Image.open(BytesIO(converted)).mode == "RGB"

    # 깨진 바이트는 변환하지 못해도 원본을 그대로 보내 기존 동작을 유지한다(폴백).
    broken = b"not-an-image"
    assert _as_openai_png(InlineImage("image/jpeg", broken)) == broken


# ---- 실존 모델(얼굴 패스) 첫 화면 — 2026-09-26 오너 결정 ----------------------------

def _sig_spec():
    return {"id": "b0", "source": "ai", "cutType": "horizon", "shot": "full",
            "direction": "front", "exampleId": "sig_men_01", "colorId": "c1"}


def _product():
    return {"clothing_type": "top", "colors": [{"id": "c1", "isBase": True, "images": []}]}


def test_face_pass_signature_direction_keeps_the_face_detectable():
    """얼굴 패스가 받을 수 있는 얼굴만 주문한다 — 옆얼굴·뒤통수·턱만은 금지, 두 눈·코·입은 보인다."""
    lowered = cut_generator.SIGNATURE_DIRECTION_FACE_PASS.lower()
    assert "extreme close" in lowered
    assert "both eyes, the nose and the mouth are clearly visible" in lowered
    assert "never a side profile" in lowered
    assert "never the back of the head" in lowered
    assert "never only the lower face" in lowered
    # 배경 규칙은 가상 모델 첫 화면과 같다
    assert "same hue family as the garment" in lowered
    assert "detail-free" in lowered


def _restore_prior_authority_sentence(prompt):
    prompt = prompt.replace("complete example or shared series scene", "all-scope example or bg plate")
    current = ("Storyboard color/shot/face/model/matching/closure override the example; "
               "the selected example owns direction and pose.")
    prior = ("Storyboard color/direction/shot/face/model/matching/closure and any explicit pose "
             "override the example; never recover an overridden attribute from its pixels.")
    assert prompt.count(current) == 1
    return prompt.replace(current, prior)


def test_signature_prompt_uses_the_face_pass_direction_only_for_face_pass_cuts():
    real = cut_generator.build_prompt(_sig_spec(), _product(), face_pass=True)
    virtual = cut_generator.build_prompt(_sig_spec(), _product(), face_pass=False)
    assert cut_generator.SIGNATURE_DIRECTION_FACE_PASS in real
    assert cut_generator.SIGNATURE_DIRECTION not in real
    assert cut_generator.SIGNATURE_DIRECTION in virtual
    assert cut_generator.SIGNATURE_DIRECTION_FACE_PASS not in virtual
    assert hashlib.sha256(_restore_prior_authority_sentence(real).encode()).hexdigest() == (
        "99b066a1ff444813ccd0bb905c185954f03aaf8f37105756a4c7b85db9d7384e"
    )
    assert hashlib.sha256(_restore_prior_authority_sentence(virtual).encode()).hexdigest() == (
        "8dcacc558f0ec32eded8e0c2ec01a70f630917b869af1b4c9816334cafab4229"
    )


def test_signature_prompt_without_measurement_keeps_the_existing_background_sentence():
    prompt = cut_generator.build_prompt(_sig_spec(), _product(), face_pass=False)

    assert "BACKGROUND: same hue family as the garment's dominant color" in prompt
    assert "Do not follow the attached reference photo's background color" not in prompt


def test_non_signature_cut_gets_no_signature_direction_even_with_face_pass():
    spec = {**_sig_spec(), "exampleId": "ex_horizon_men_top_full_front_02"}
    prompt = cut_generator.build_prompt(spec, _product(), face_pass=True)
    assert "SIGNATURE OPENING CUT" not in prompt


def _bottom_sig_spec(**overrides):
    return {
        "id": "bottom-signature",
        "source": "ai",
        "cutType": "horizon",
        "shot": "medium",
        "direction": "front",
        "faceExposure": "same",
        "refScope": "all",
        "exampleId": "sig_women_bottom_pants_01",
        "colorId": "c1",
        **overrides,
    }


def _bottom_product():
    return {
        "clothing_type": "bottom",
        "colors": [{"id": "c1", "isBase": True, "images": []}],
    }


def _dress_sig_spec(**overrides):
    return {
        "id": "dress-signature",
        "source": "ai",
        "cutType": "horizon",
        "shot": "medium",
        "direction": "front",
        "faceExposure": "same",
        "refScope": "all",
        "exampleId": "sig_women_dress_01",
        "colorId": "c1",
        **overrides,
    }


def _dress_product():
    return {
        "clothing_type": "dress",
        "colors": [{"id": "c1", "isBase": True, "images": []}],
    }


@pytest.mark.parametrize(
    ("spec", "product", "face_pass"),
    [
        (_sig_spec(), _product(), False),
        (_sig_spec(), _product(), True),
        (_bottom_sig_spec(), _bottom_product(), False),
        (_dress_sig_spec(), _dress_product(), False),
    ],
)
def test_measured_signature_prompt_uses_calculated_hex_and_ignores_reference_background(
    spec, product, face_pass,
):
    palette = horizon_background.signature_palette_for_observed("#2a5db0")
    runtime = horizon_background.apply_signature_runtime(spec, palette)
    prompt = cut_generator.build_prompt(runtime, product, face_pass=face_pass)

    assert palette["backgroundHex"] in prompt
    assert "hue-family suggestion, not an exact final brightness" in prompt
    assert "Do not follow the attached reference photo's background color" in prompt
    assert "BACKGROUND: same hue family as the garment's dominant color" not in prompt


def test_bottom_signature_prompt_uses_lower_body_direction_without_upper_body_copy():
    prompt = cut_generator.build_prompt(
        _bottom_sig_spec(), _bottom_product(), face_pass=False,
    )
    lowered = prompt.lower()
    assert cut_generator.SIGNATURE_DIRECTION_BOTTOM in prompt
    assert "extreme close crop of the lower body" in lowered
    assert "just above the waistband" in lowered
    assert "mid-thigh" in lowered
    assert "short skirts or shorts" in lowered
    assert "waistband" in lowered
    assert "front closure" in lowered
    assert "pockets" in lowered
    assert "pleats" in lowered
    assert "face and hair must not appear" in lowered
    assert "upper body" not in lowered
    assert "waist through the feet" not in lowered
    assert "keep both shoes" not in lowered


def test_bottom_signature_suppresses_head_face_and_hair_rules_for_every_direction():
    for direction in ("front", "side", "back"):
        prompt = cut_generator.build_prompt(
            _bottom_sig_spec(direction=direction),
            _bottom_product(),
            hair_profile={"hairLength": "long"},
            face_shape_profile={"faceShape": "oval"},
            face_pass=True,
        )
        lowered = prompt.lower()
        assert cut_generator.SIGNATURE_DIRECTION_BOTTOM in prompt
        assert "face handling:" not in lowered
        assert "subject hair" not in lowered
        assert "subject face shape" not in lowered
        assert "back of the head" not in lowered
        assert "head is turned" not in lowered
        assert "side profile of the model" not in lowered


def test_bottom_signature_ignores_face_pass_and_never_requests_a_face():
    prompt = cut_generator.build_prompt(
        _bottom_sig_spec(), _bottom_product(), face_pass=True,
    )
    assert cut_generator.SIGNATURE_DIRECTION_BOTTOM in prompt
    assert cut_generator.SIGNATURE_DIRECTION_FACE_PASS not in prompt
    assert cut_generator.wants_face(_bottom_sig_spec(), "bottom") is False


def test_real_model_bottom_signature_does_not_enter_face_pass_even_for_legacy_full_shot(
    monkeypatch,
):
    class FakeGemini:
        async def generate_content_image(
            self, model, prompt, images, image_size, aspect_ratio,
        ):
            return SimpleNamespace(image=b"BOTTOM", mime="image/png")

    async def face_pass_must_not_run(*args, **kwargs):
        raise AssertionError("bottom signature must not request a face pass")

    monkeypatch.setattr(
        cut_generator.face_identity, "apply_face_pass", face_pass_must_not_run,
    )
    settings = _settings(
        openai_api_key="sk-test",
        detail_cut_image_size="4K",
        mannequin_aspect_ratio="2:3",
        face_identity_enabled=True,
    )
    spec = _bottom_sig_spec(
        shot="full", modelId="real-model", exampleId="sig_women_bottom_pants_02",
    )
    identity = cut_generator.face_identity.FaceIdentitySpec("model.safetensors")

    image, mime = asyncio.run(cut_generator.generate(
        settings,
        FakeGemini(),
        spec,
        _bottom_product(),
        [],
        face_identity_spec=identity,
    ))

    assert (image, mime) == (b"BOTTOM", "image/png")


def test_bottom_signature_pose_scope_is_not_expanded_and_cropped_like_a_generic_bottom():
    request = cut_generator._base_request(
        _settings(
            detail_cut_image_size="4K",
            mannequin_aspect_ratio="2:3",
            openai_api_key="sk-test",
        ),
        _bottom_sig_spec(refScope="pose"),
        _bottom_product(),
    )
    assert request.spec["shot"] == "medium"
    assert request.spec["refScope"] == "all"


def test_dress_signature_prompt_uses_dress_crop_without_head_or_upper_body_rules():
    for direction in ("front", "side", "back"):
        prompt = cut_generator.build_prompt(
            _dress_sig_spec(direction=direction),
            _dress_product(),
            hair_profile={"hairLength": "long"},
            face_shape_profile={"faceShape": "oval"},
            face_pass=True,
        )
        lowered = prompt.lower()
        assert cut_generator.SIGNATURE_DIRECTION_DRESS in prompt
        assert "extreme close crop from just below the collarbone" in lowered
        assert "mid-thigh or the knee" in lowered
        assert "waistline" in lowered
        assert "closure" in lowered
        assert "skirt drape" in lowered
        assert "silhouette" in lowered
        assert "face and hair must not appear" in lowered
        assert "upper body" not in lowered
        assert "head to the hip" not in lowered
        assert "face handling:" not in lowered
        assert "subject hair" not in lowered
        assert "subject face shape" not in lowered
        assert "back of the head" not in lowered
        assert "head is turned" not in lowered


def test_real_model_dress_signature_does_not_enter_face_pass(monkeypatch):
    class FakeGemini:
        async def generate_content_image(
            self, model, prompt, images, image_size, aspect_ratio,
        ):
            return SimpleNamespace(image=b"DRESS", mime="image/png")

    async def face_pass_must_not_run(*args, **kwargs):
        raise AssertionError("dress signature must not request a face pass")

    monkeypatch.setattr(
        cut_generator.face_identity, "apply_face_pass", face_pass_must_not_run,
    )
    settings = _settings(
        openai_api_key="sk-test",
        detail_cut_image_size="4K",
        mannequin_aspect_ratio="2:3",
        face_identity_enabled=True,
    )
    spec = _dress_sig_spec(shot="full", modelId="real-model")
    identity = cut_generator.face_identity.FaceIdentitySpec("model.safetensors")

    image, mime = asyncio.run(cut_generator.generate(
        settings,
        FakeGemini(),
        spec,
        _dress_product(),
        [],
        face_identity_spec=identity,
    ))

    assert (image, mime) == (b"DRESS", "image/png")


def test_dress_signature_keeps_medium_without_generic_post_crop():
    request = cut_generator._base_request(
        _settings(
            detail_cut_image_size="4K",
            mannequin_aspect_ratio="2:3",
            openai_api_key="sk-test",
        ),
        _dress_sig_spec(refScope="pose"),
        _dress_product(),
    )
    assert request.spec["shot"] == "medium"
    assert request.spec["refScope"] == "all"


def test_non_signature_prompt_changes_only_retired_authority_sentence():
    spec = {**_sig_spec(), "exampleId": "ex_horizon_men_top_full_front_02"}
    prompt = cut_generator.build_prompt(spec, _product(), face_pass=True)
    prompt = _restore_prior_authority_sentence(prompt)
    assert len(prompt.encode()) == 5964
    assert hashlib.sha256(prompt.encode()).hexdigest() == (
        "3e804c37cb983ee280dcf1a5c0aa705bba8a65e37371375654b786fe0e6a8851"
    )


def test_server_signature_pool_matches_clothing_gender_and_skirt_category():
    assert cut_generator.signature_cut_ids_for(
        gender="women", clothing_type="bottom", sub_category="slacks",
    ) == (
        "sig_women_bottom_pants_01",
        "sig_women_bottom_pants_02",
    )
    assert cut_generator.signature_cut_ids_for(
        gender="women", clothing_type="bottom", sub_category="midi_skirt",
    ) == (
        "sig_women_bottom_skirt_01",
        "sig_women_bottom_skirt_02",
    )
    assert cut_generator.signature_cut_ids_for(
        gender="women", clothing_type="bottom", custom_category="플리츠 치마",
    ) == (
        "sig_women_bottom_skirt_01",
        "sig_women_bottom_skirt_02",
    )
    assert cut_generator.signature_cut_ids_for(
        gender="women", clothing_type="bottom", sub_category="slacks",
        custom_category="플리츠 치마",
    ) == (
        "sig_women_bottom_skirt_01",
        "sig_women_bottom_skirt_02",
    )
    assert cut_generator.signature_cut_ids_for(
        gender="men", clothing_type="bottom", sub_category="mini_skirt",
    ) == (
        "sig_men_bottom_pants_01",
        "sig_men_bottom_pants_02",
    )
    assert cut_generator.signature_cut_ids_for(
        gender="women", clothing_type="top",
    ) == ("sig_women_01", "sig_women_02", "sig_women_03", "sig_women_04")
    assert cut_generator.signature_cut_ids_for(
        gender="women", clothing_type="dress",
    ) == ("sig_women_dress_01", "sig_women_dress_02")
    assert cut_generator.signature_cut_ids_for(
        gender="men", clothing_type="dress",
    ) == (
        "sig_men_01", "sig_men_02", "sig_men_03", "sig_men_04", "sig_men_05",
    )
    assert cut_generator.pick_signature_cut(
        project_id="dress-project", gender="women", clothing_type="dress",
    ) == "sig_women_dress_01"


def test_server_remaps_only_mismatched_signature_ids_deterministically():
    blocks = [
        {
            "id": "wrong",
            "source": "ai",
            "exampleId": "sig_women_01",
            "exampleSelectionOrigin": "user",
            "colorId": "seller-color",
            "matchIds": ["seller-match"],
        },
        {
            "id": "right",
            "source": "ai",
            "exampleId": "sig_women_bottom_skirt_01",
        },
        {"id": "ordinary", "source": "ai", "exampleId": "ex_regular"},
    ]
    kwargs = dict(
        project_id="repair-project",
        gender="women",
        clothing_type="bottom",
        sub_category="long_skirt",
    )
    repaired, remaps = cut_generator.repair_signature_cut_selections(blocks, **kwargs)
    repaired_again, remaps_again = cut_generator.repair_signature_cut_selections(blocks, **kwargs)

    assert repaired[0]["exampleId"] == "sig_women_bottom_skirt_01"
    assert repaired[0]["exampleId"] == repaired_again[0]["exampleId"]
    assert repaired[0]["exampleSelectionOrigin"] == "user"
    assert repaired[0]["colorId"] == "seller-color"
    assert repaired[0]["matchIds"] == ["seller-match"]
    assert repaired[1] is blocks[1]
    assert repaired[2] is blocks[2]
    assert remaps == remaps_again == [
        {
            "blockId": "wrong",
            "fromExampleId": "sig_women_01",
            "toExampleId": repaired[0]["exampleId"],
        }
    ]

    upper, upper_remaps = cut_generator.repair_signature_cut_selections(
        [{**blocks[0], "exampleId": "sig_women_bottom_pants_01"}],
        project_id="repair-project",
        gender="women",
        clothing_type="top",
    )
    assert upper[0]["exampleId"] in {
        "sig_women_01", "sig_women_02", "sig_women_03", "sig_women_04",
    }
    assert upper_remaps[0]["fromExampleId"] == "sig_women_bottom_pants_01"

    dress, dress_remaps = cut_generator.repair_signature_cut_selections(
        [{**blocks[0], "id": "dress", "exampleId": "sig_women_02"}],
        project_id="dress-repair-project",
        gender="women",
        clothing_type="dress",
    )
    assert dress[0]["exampleId"] in {"sig_women_dress_01", "sig_women_dress_02"}
    assert dress_remaps[0]["fromExampleId"] == "sig_women_02"

    stale_slot, stale_slot_remaps = cut_generator.repair_signature_cut_selections(
        [{
            **blocks[0],
            "id": "stale-slot",
            "hookSlotRole": "signature",
            "exampleId": "ex_horizon_women_dress_medium_profile_01",
        }],
        project_id="dress-repair-project",
        gender="women",
        clothing_type="dress",
    )
    assert stale_slot[0]["exampleId"] in {"sig_women_dress_01", "sig_women_dress_02"}
    assert stale_slot_remaps[0]["fromExampleId"] == "ex_horizon_women_dress_medium_profile_01"


@pytest.mark.parametrize("cut_type,shot,direction", [
    ("mirror", "medium", None),
    ("product", "ghost", "front"),
])
def test_server_safety_net_clears_signature_reference_from_unsupported_recipe(
    cut_type, shot, direction,
):
    block = {
        "id": f"bad-{cut_type}",
        "source": "ai",
        "cutType": cut_type,
        "shot": shot,
        "direction": direction,
        "exampleId": "sig_women_bottom_pants_01",
        "exampleSelectionOrigin": "user",
        "refScope": "all",
        "colorId": "seller-color",
    }
    repaired, remaps = cut_generator.repair_signature_cut_selections(
        [block],
        project_id="invalid-recipe",
        gender="women",
        clothing_type="bottom",
    )

    assert repaired[0]["cutType"] == cut_type
    assert repaired[0]["shot"] == shot
    assert repaired[0]["direction"] == direction
    assert repaired[0]["colorId"] == "seller-color"
    assert repaired[0]["exampleId"] is None
    assert repaired[0]["exampleSelectionOrigin"] is None
    assert repaired[0]["refScope"] is None
    assert remaps == [{
        "blockId": f"bad-{cut_type}",
        "fromExampleId": "sig_women_bottom_pants_01",
        "toExampleId": None,
        "reason": "unsupported_signature_recipe",
    }]


@pytest.mark.parametrize("cut_type,shot,direction", [
    ("mirror", "medium", None),
    ("product", "ghost", "front"),
])
def test_direct_prompt_rejects_signature_id_on_unsupported_recipe(
    cut_type, shot, direction,
):
    reason = "unknown_cut_type" if cut_type == "mirror" else "unsupported_signature_recipe"
    with pytest.raises(ValueError, match=reason):
        cut_generator.build_prompt(
            _bottom_sig_spec(cutType=cut_type, shot=shot, direction=direction),
            _bottom_product(),
        )


def test_bottom_signature_stage2_repair_keeps_the_signature_framing_contract():
    prompt = cut_generator.build_qc_repair_prompt(
        _bottom_sig_spec(),
        _bottom_product(),
        ("Correct only the failed framing crop.",),
    )
    lowered = prompt.lower()
    assert cut_generator.SIGNATURE_DIRECTION_BOTTOM in prompt
    assert "just above the waistband" in lowered
    assert "mid-thigh" in lowered
    assert "face and hair must not appear" in lowered
    assert "waist through the feet" not in lowered


def test_dress_signature_stage2_repair_keeps_the_signature_framing_contract():
    prompt = cut_generator.build_qc_repair_prompt(
        _dress_sig_spec(),
        _dress_product(),
        ("Correct only the failed framing crop.",),
    )
    lowered = prompt.lower()
    assert cut_generator.SIGNATURE_DIRECTION_DRESS in prompt
    assert "just below the collarbone" in lowered
    assert "mid-thigh or the knee" in lowered
    assert "face and hair must not appear" in lowered
    assert "head to the hip" not in lowered


@pytest.mark.parametrize("measured", [True, False])
def test_upper_signature_stage2_repair_keeps_measured_or_default_background_contract(
    measured,
):
    spec = _sig_spec()
    if measured:
        palette = horizon_background.signature_palette_for_observed("#2a5db0")
        spec = horizon_background.apply_signature_runtime(spec, palette)
    prompt = cut_generator.build_qc_repair_prompt(
        spec,
        _product(),
        ("Correct only the failed lighting integration.",),
    )

    if measured:
        assert palette["backgroundHex"] in prompt
        assert "Do not follow the attached reference photo's background color" in prompt
        assert "BACKGROUND: same hue family as the garment's dominant color" not in prompt
    else:
        assert "BACKGROUND: same hue family as the garment's dominant color" in prompt
        assert "Do not follow the attached reference photo's background color" not in prompt


@pytest.mark.parametrize(
    ("spec", "clothing_type"),
    [
        (_dress_sig_spec(direction="side", sideStyle="profile", modelId="real"), "dress"),
        (_dress_sig_spec(direction="back", modelId="real"), "dress"),
        (_bottom_sig_spec(shot="full", direction="back", modelId="real"), "bottom"),
    ],
)
def test_headless_signature_never_enters_angle_swap_or_missing_photo_preflight(
    spec, clothing_type,
):
    normalized = cut_generator.normalize_spec(spec, clothing_type=clothing_type)
    angle_swap = SimpleNamespace()

    assert cut_generator._angle_swap_direction(
        normalized, clothing_type, angle_swap,
    ) is None
    assert cut_generator.angle_swap_missing_photo(
        spec, {"clothingType": clothing_type}, angle_swap,
    ) is None


def test_detail_page_worker_safety_net_remaps_and_logs(caplog):
    storyboard = [{
        "id": "opening",
        "source": "ai",
        "cutType": "horizon",
        "shot": "medium",
        "exampleId": "sig_women_01",
        "exampleSelectionOrigin": "user",
    }]
    with caplog.at_level("WARNING", logger="wearless.detail_page_job"):
        repaired = detail_page_job._repair_signature_storyboard(
            storyboard,
            product={"clothingType": "bottom"},
            analysis={"targetGenders": ["women"], "subCategory": "mini_skirt"},
            project_id="server-safety-project",
        )

    assert repaired[0]["exampleId"].startswith("sig_women_bottom_skirt_")
    assert repaired[0]["exampleSelectionOrigin"] == "user"
    assert "signature example remapped" in caplog.text
    assert "sig_women_01" in caplog.text
    assert repaired[0]["exampleId"] in caplog.text


def test_detail_page_worker_safety_net_remaps_dress_to_dress_pool(caplog):
    storyboard = [{
        "id": "opening-dress",
        "source": "ai",
        "cutType": "horizon",
        "shot": "medium",
        "exampleId": "sig_women_01",
        "exampleSelectionOrigin": "user",
    }]
    with caplog.at_level("WARNING", logger="wearless.detail_page_job"):
        repaired = detail_page_job._repair_signature_storyboard(
            storyboard,
            product={"clothingType": "dress"},
            analysis={"targetGenders": ["women"]},
            project_id="server-dress-project",
        )

    assert repaired[0]["exampleId"] in {"sig_women_dress_01", "sig_women_dress_02"}
    assert repaired[0]["exampleSelectionOrigin"] == "user"
    assert "signature example remapped" in caplog.text


def test_signature_registry_has_the_exact_frontend_pools_and_metadata():
    registry_path = Path(__file__).parents[1] / "app" / "data" / "example_assets.json"
    assets = json.loads(registry_path.read_text(encoding="utf-8"))["assets"]
    upper = {
        "sig_men_01", "sig_men_02", "sig_men_03", "sig_men_04", "sig_men_05",
        "sig_women_01", "sig_women_02", "sig_women_03", "sig_women_04",
    }
    bottom = {
        "sig_women_bottom_pants_01", "sig_women_bottom_pants_02",
        "sig_women_bottom_skirt_01",
        "sig_women_bottom_skirt_02", "sig_men_bottom_pants_01",
        "sig_men_bottom_pants_02",
    }
    dress = {"sig_women_dress_01", "sig_women_dress_02"}
    signature_ids = {example_id for example_id in assets if example_id.startswith("sig_")}
    assert signature_ids == upper | bottom | dress
    for example_id in upper | bottom | dress:
        entry = assets[example_id]
        assert entry["all"] == (
            f"https://images.wearless.kr/seed/genexamples/signature/v1/all/{example_id}.png"
        )
        assert entry["thumb"] == (
            f"https://images.wearless.kr/seed/genexamples/signature/v1/thumb/{example_id}.webp"
        )
        assert entry["cutType"] == "signature"
        assert entry["shot"] == "medium"
        assert entry["direction"] == "front"
        assert entry["confirmedGptEligibleV1"] is False
        expected_applicability = (
            ["top", "outer", "dress"] if example_id in upper
            else ["bottom"] if example_id in bottom
            else ["dress"]
        )
        assert entry["applicableClothingTypes"] == expected_applicability
        expected_gender = "men" if example_id.startswith("sig_men_") else "women"
        assert entry["gender"] == expected_gender
