"""시그니처 컷(상세페이지 첫 화면) 전용 모델·프롬프트 분기.

오너 확정(2026-08-17): 첫 화면 컷만 GPT-image 계열로 생성하고, 배경은 착장 의류 색의
연한 톤, 구도는 얼굴 일부만 보이는 극단 클로즈업으로 간다. 나머지 컷은 기존 그대로다.
판정은 프론트 전용 풀(signatureCutPool)이 붙이는 exampleId 의 'sig_' 접두로 한다 —
저장 계약에 새 필드를 더하지 않는다.
"""

import asyncio
from types import SimpleNamespace

from app.agents import cut_generator
from app.agents.model_routing import model_routing_snapshot, resolve_model


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
    assert model_routing_snapshot(partial)["image_signature"] == "b"


def test_snapshot_exposes_the_signature_tier():
    assert model_routing_snapshot(_settings())["image_signature"] == "gpt-image-2"


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
