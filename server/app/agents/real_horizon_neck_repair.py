"""Qwen 얼굴 교체가 끝난 REAL 호리존 최종본의 목선만 Sunburst로 보정한다."""

import asyncio
import hashlib
import logging
from io import BytesIO
from pathlib import Path

from PIL import Image

from .. import facemarket, image_usage
from . import face_identity
from .gemini_image import InlineImage

log = logging.getLogger("wearless.real_horizon_neck_repair")
MODEL = "gpt-image-2.5-sunburst"
PROMPT_VERSION = "real_horizon_neck_repair_v1"
_PROMPT = Path(__file__).resolve().parents[2] / "prompts" / (PROMPT_VERSION + ".txt")


def eligible(settings, spec, *, generation_model, real_identity_attached, outcome):
    """가상모델, 다른 컷, 얼굴 패스 미적용, 다른 생성 모델은 호출하지 않는다."""
    return (
        settings.real_horizon_neck_repair_enabled
        and real_identity_attached
        and spec.get("cutType") == "horizon"
        and facemarket.is_real_model_id(spec.get("modelId"))
        and generation_model.startswith("gpt-image")
        and outcome.get("face_pass") == "applied"
    )


def _validate(source, repaired, identity_spec, model_dir):
    """검사만 한다. 저장할 API 출력 바이트는 수정하지 않는다."""
    with Image.open(BytesIO(source.data)) as original, Image.open(BytesIO(repaired.data)) as output:
        original.load()
        output.load()
        dimensions = {
            "inputDimensions": list(original.size),
            "outputDimensions": list(output.size),
        }
        if output.format != "PNG":
            return "invalid_output_format", dimensions
        # auto 출력의 반올림 차이는 허용하되, 다른 비율로 바꾼 이미지는 쓰지 않는다.
        if abs((output.width / output.height) / (original.width / original.height) - 1) > 0.01:
            return "aspect_ratio_changed", dimensions
        references = getattr(identity_spec, "references", None)
        if not references:
            references = face_identity.reference_embeddings([source.data], model_dir)
        score = face_identity.identity_score(output, references, model_dir=model_dir)
        dimensions["identityScore"] = score
        if score is None:
            return "identity_unavailable", dimensions
        if score < face_identity.GATE_IDENTITY_MIN:
            return "identity_low", dimensions
        return None, dimensions


async def repair(settings, client, source: InlineImage, identity_spec):
    """한 번 호출한다. 실패 또는 동일인 검사 탈락이면 Qwen 원본을 보존한다."""
    metadata = {
        "model": MODEL,
        "promptVersion": PROMPT_VERSION,
        "quality": "high",
        "size": "auto",
        "attempted": True,
        "applied": False,
        "inputSha256": hashlib.sha256(source.data).hexdigest(),
    }
    try:
        with image_usage.job_scope(stage="real_horizon_neck_repair"):
            result = await client.edit_image(
                MODEL, _PROMPT.read_text(encoding="utf-8"), source,
                size="auto", quality="high",
            )
        repaired = InlineImage(result.mime, result.image)
        metadata["outputSha256"] = hashlib.sha256(repaired.data).hexdigest()
        reason, validation = await asyncio.to_thread(
            _validate, source, repaired, identity_spec,
            getattr(settings, "fm_face_qc_dir", None),
        )
        metadata.update(validation)
        if reason:
            metadata["reason"] = reason
            log.warning("REAL horizon neck repair rejected: %s", reason)
            return source, metadata
        metadata["applied"] = True
        return repaired, metadata
    except Exception as exc:
        metadata.update(reason="repair_unavailable", errorType=type(exc).__name__)
        log.warning("REAL horizon neck repair unavailable: %s", type(exc).__name__)
        return source, metadata
