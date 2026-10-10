"""AG-07, 최초 원본 사진과 저장된 비슷한 컷 계획을 받아 착용컷을 생성한다.

계획의 보존 조건과 동작 지시를 실행하고, 필요한 얼굴 패스를 적용한다.
에디터와 콘티의 비슷한 컷 워커가 이 실행기를 함께 사용한다.
"""

from ..config import Settings
from . import face_identity, similar_cut
from .gemini_image import GeminiImageClient, InlineImage, run_cpu_bound
from .model_routing import resolve_model

#: cut_generator._WORN_CUTS 와 같은 집합 — 사람이 담기는 컷에만 얼굴 패스를 건다.
_WORN_CUTS = ("styling", "horizon", "mirror")

async def generate(
    settings: Settings,
    gemini: GeminiImageClient,
    source_image: InlineImage,
    changes: list,
    cut_type: str | None,
    *,
    face_identity_spec: face_identity.FaceIdentitySpec | None = None,
    face_pass_outcome: dict | None = None,
    face_pass_url_provider=None,
    similar_plan: dict | None = None,
    similar_context: dict | None = None,
) -> tuple[bytes, str]:
    """계획에 따른 비슷한 컷 1장 생성. 실패 시 GeminiError를 그대로 전파한다.

    face_identity_spec 이 오면 AG-06 과 같은 얼굴 패스를 결과에 건다. 변형은 원본 컷을 다시
    그리는 것이라 얼굴이 흔들릴 수 있고, 그러면 한 상세페이지 안에서 같은 사람이 컷마다 달라진다.
    패스는 **착장 컷일 때만** — product/ghost 류에는 사람이 없다. 실패는 원본 폴백(예외 없음).
    """
    if similar_plan is None:
        raise ValueError("similar_plan_required")
    model = resolve_model(settings, "image_high")
    prompt = similar_cut.generation_prompt(
        similar_plan, context=similar_context or {}, cut_type=cut_type, changes=changes)
    images = [source_image]
    image_size = getattr(settings, "detail_cut_image_size", None) or settings.mannequin_image_size
    size_options = {}
    if model.startswith("gpt-image"):
        size_options["openai_size_override"] = await run_cpu_bound(
            similar_cut.openai_source_size, source_image, image_size)
    res = await gemini.generate_content_image(
        model, prompt, images,
        image_size,
        aspect_ratio=None,
        **size_options,
    )
    image, mime = res.image, res.mime
    if (face_identity_spec is not None
            and getattr(settings, "face_identity_enabled", False)
            and cut_type in _WORN_CUTS):
        image, mime = await face_identity.apply_face_pass(
            settings, image, mime, face_identity_spec, outcome=face_pass_outcome,
            url_provider=face_pass_url_provider)
    return image, mime
