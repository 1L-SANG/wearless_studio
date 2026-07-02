"""tier → 모델 매핑 (ai_agent_modules §1).

에이전트는 모델명을 직접 갖지 않고 tier만 선언한다. 교체 = env(config) 한 곳 수정.
"""

from ..config import Settings

UNKNOWN_TIER = "_unknown_"


def resolve_model(settings: Settings, tier: str) -> str:
    """tier('image_light'|'image_high'|'text')를 실제 모델 id로 해석."""
    mapping = {
        "image_light": settings.model_image_light,
        "image_high": settings.model_image_high,
        "text": settings.model_text,
    }
    model = mapping.get(tier)
    if not model:
        raise ValueError(f"알 수 없는 tier: {tier}")
    return model


def model_routing_snapshot(settings: Settings) -> dict:
    """관측 로그·디버그용 현재 라우팅 스냅샷 (ai_agent_modules §6)."""
    return {
        "image_light": settings.model_image_light,
        "image_high": settings.model_image_high,
        "text": settings.model_text,
        "imageSize": settings.mannequin_image_size,
    }
