"""시그니처 컷(상세페이지 첫 화면) 전용 모델·프롬프트 분기.

오너 확정(2026-08-17): 첫 화면 컷만 GPT-image 계열로 생성하고, 배경은 착장 의류 색의
연한 톤, 구도는 얼굴 일부만 보이는 극단 클로즈업으로 간다. 나머지 컷은 기존 그대로다.
판정은 프론트 전용 풀(signatureCutPool)이 붙이는 exampleId 의 'sig_' 접두로 한다 —
저장 계약에 새 필드를 더하지 않는다.
"""

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
