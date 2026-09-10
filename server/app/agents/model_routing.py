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
        "image_mannequin": getattr(settings, "model_image_mannequin", None) or settings.model_image_high,
        # 시그니처 컷(첫 화면)만 별도 모델 — 구도·배경 문법이 달라 오너가 GPT-image 계열로 확정(2026-08-17).
        "image_signature": getattr(settings, "model_image_signature", None) or settings.model_image_high,
        "text": settings.model_text,  # AG-01 등 text tier (계약 §1 MODEL_ROUTING_TEXT)
    }
    model = mapping.get(tier)
    if not model:
        raise ValueError(f"알 수 없는 tier: {tier}")
    return model


def resolve_detail_cut_model(settings: Settings) -> str:
    """AG-06 콘티 전용 모델. 미설정 환경은 기존 image_high 경로를 그대로 쓴다."""
    return getattr(settings, "model_detail_cut", "") or resolve_model(settings, "image_high")


def resolve_editor_cut_model(settings: Settings) -> str:
    """에디터 컷(editor_image_job) 전용 모델. 미설정 환경은 기존 image_high 경로를 그대로 쓴다.

    image_high 를 직접 바꾸면 마네킹·매칭 플랫레이·AG-07 까지 함께 전환되므로,
    에디터 워커 안에서만 Settings 복사본의 image_high 를 이 값으로 치환한다(detail_cut 선례와 동일).
    """
    return getattr(settings, "model_editor_cut", "") or resolve_model(settings, "image_high")


def model_routing_snapshot(settings: Settings) -> dict:
    """관측 로그·디버그용 현재 라우팅 스냅샷 (ai_agent_modules §6)."""
    return {
        "image_light": settings.model_image_light,
        "image_high": settings.model_image_high,
        "image_mannequin": getattr(settings, "model_image_mannequin", None) or settings.model_image_high,
        "image_signature": getattr(settings, "model_image_signature", None) or settings.model_image_high,
        "detail_cut": resolve_detail_cut_model(settings),
        "editor_cut": resolve_editor_cut_model(settings),
        "text": settings.model_text,
        "imageSize": settings.mannequin_image_size,
        "detailImageSize": (
            getattr(settings, "detail_cut_image_size", None)
            or settings.mannequin_image_size
        ),
    }
