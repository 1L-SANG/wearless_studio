"""Color-only mannequin-anchor editing core.

This module deliberately does not decide whether two product colorways are safe to
transform.  A caller must supply one of the explicit, audited eligibility tokens
below.  The module owns only validation, prompt assembly, the single image edit
call, and deterministic cache identity helpers; persistence remains a worker
concern.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Sequence

from ..config import Settings
from .gemini_image import GeminiImageClient, InlineImage
from .model_routing import resolve_model
from .prompts import _sanitize


_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_PROMPT_FILE = os.path.join(_SERVER_DIR, "prompts", "mannequin_colorway_v1.txt")

PROMPT_VERSION = "mannequin_colorway_v1"

# These are assertions made upstream by an authorized workflow, not conclusions
# this module may infer from pixels, product names, or category metadata.
COLOR_ONLY_ELIGIBILITY_ALLOWLIST = frozenset({
    "seller_confirmed_same_sku_color_only",
    "catalog_verified_same_sku_color_only",
})

_HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}\Z")
_CACHE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp"})
_IMAGE_SIZES = frozenset({"1K", "2K", "4K"})
_ASPECT_RATIOS = frozenset({
    "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9",
})
_CANONICAL_COLOR_LABELS = frozenset({
    "white", "gray", "grey", "charcoal", "black", "ivory", "cream", "oatmeal",
    "beige", "tan", "camel", "brown", "red", "burgundy", "wine", "orange",
    "yellow", "mustard", "green", "khaki", "olive", "mint", "blue", "sky blue",
    "denim blue", "navy", "purple", "lavender", "pink", "rose", "silver", "gold",
    "화이트", "흰색", "그레이", "회색", "차콜", "블랙", "검정", "아이보리", "크림",
    "오트밀", "베이지", "탄", "카멜", "브라운", "갈색", "레드", "빨강", "버건디",
    "와인", "오렌지", "주황", "옐로우", "노랑", "머스타드", "그린", "초록",
    "카키", "올리브", "민트", "블루", "파랑", "스카이블루", "데님블루", "네이비",
    "퍼플", "보라", "라벤더", "핑크", "로즈", "실버", "골드",
})


class MannequinColorwayError(ValueError):
    """Raised before an image call when the color-only contract is not proven."""


def require_color_only_eligibility(value: str) -> str:
    """Return an allowlisted eligibility token or fail closed.

    ``True`` and other truthy values are intentionally rejected.  The caller must
    identify the upstream proof used to declare a change-only-color operation.
    """

    if not isinstance(value, str) or value not in COLOR_ONLY_ELIGIBILITY_ALLOWLIST:
        raise MannequinColorwayError("color_only_eligibility_required")
    return value


def _clean_target_color(
    target_color_name: str | None,
    target_color_hex: str | None,
    *,
    has_product_references: bool,
) -> tuple[str | None, str | None, str]:
    name = _sanitize(target_color_name or "")[:80] or None
    # Product color labels can be seller-controlled text.  Only the stable catalog
    # vocabulary is allowed into the instruction; custom names must rely on a
    # validated hex value or attached target-color photos and omit this field.
    if name and name.casefold() not in _CANONICAL_COLOR_LABELS:
        raise MannequinColorwayError("invalid_target_color_name")
    raw_hex = str(target_color_hex or "").strip()
    if raw_hex and not _HEX_COLOR_RE.fullmatch(raw_hex):
        raise MannequinColorwayError("invalid_target_color_hex")
    color_hex = raw_hex.lower() or None
    if not name and not color_hex and not has_product_references:
        raise MannequinColorwayError("target_color_evidence_required")

    label_parts = []
    if name:
        label_parts.append(name)
    if color_hex:
        label_parts.append(color_hex)
    if label_parts:
        label = " ".join(label_parts)
        if has_product_references:
            label += " as physically shown by the attached target-color product photos"
    else:
        label = "the exact target colorway physically shown by the attached product photos"
    return name, color_hex, label


def _validate_image(image: InlineImage, *, field: str) -> None:
    if not isinstance(image, InlineImage):
        raise MannequinColorwayError(f"invalid_{field}")
    if not isinstance(image.mime, str) or not image.mime.startswith("image/"):
        raise MannequinColorwayError(f"invalid_{field}_mime")
    if not isinstance(image.data, bytes) or not image.data:
        raise MannequinColorwayError(f"invalid_{field}_bytes")


def build_image_manifest(product_reference_count: int) -> str:
    if isinstance(product_reference_count, bool) or not isinstance(product_reference_count, int) \
            or product_reference_count < 0:
        raise MannequinColorwayError("invalid_product_reference_count")
    lines = [
        "1. CURRENT MANNEQUIN CUT — the exact base photograph to edit; preserve its canvas",
    ]
    for index in range(product_reference_count):
        lines.append(
            f"{index + 2}. TARGET-COLOR PRODUCT PHOTO — color evidence only for the MAIN PRODUCT"
        )
    return "\n".join(lines)


def render_prompt(
    *,
    eligibility: str,
    target_color_name: str | None = None,
    target_color_hex: str | None = None,
    product_reference_count: int = 0,
) -> str:
    """Render a fail-closed, change-only-color edit prompt."""

    require_color_only_eligibility(eligibility)
    manifest = build_image_manifest(product_reference_count)
    _name, _hex, target_label = _clean_target_color(
        target_color_name,
        target_color_hex,
        has_product_references=product_reference_count > 0,
    )
    with open(_PROMPT_FILE, encoding="utf-8") as handle:
        template = handle.read()
    prompt = (
        template
        .replace("${targetColor}", target_label)
        .replace("${imageManifest}", manifest)
    )
    leftovers = re.findall(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", prompt)
    if leftovers:
        raise MannequinColorwayError("unresolved_prompt_token")
    return prompt


def _cache_text(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise MannequinColorwayError(f"invalid_{field}")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 2048:
        raise MannequinColorwayError(f"invalid_{field}")
    return cleaned


def _cache_choice(value: object, *, field: str, allowed: frozenset[str]) -> str:
    cleaned = _cache_text(value, field=field)
    if cleaned not in allowed:
        raise MannequinColorwayError(f"invalid_{field}")
    return cleaned


def cache_fingerprint(
    *,
    project_id: str,
    mannequin_key: str,
    target_color_id: str,
    target_color_name: str | None = None,
    target_color_hex: str | None = None,
    product_ref_keys: Iterable[str] = (),
    resolved_model_id: str,
    image_size: str,
    aspect_ratio: str,
    prompt_version: str = PROMPT_VERSION,
) -> str:
    """Return a stable SHA-256 identity for one derived colorway anchor.

    Product references are a set of color evidence, so duplicate keys and caller
    ordering do not create needless cache misses.  Every value is length-safe and
    JSON encoded instead of delimiter-concatenated to avoid ambiguous identities.
    """

    if isinstance(product_ref_keys, (str, bytes)):
        raise MannequinColorwayError("invalid_product_ref_keys")
    try:
        refs = sorted({
            _cache_text(value, field="product_ref_key") for value in product_ref_keys
        })
    except TypeError as exc:
        raise MannequinColorwayError("invalid_product_ref_keys") from exc
    color_name, color_hex, _label = _clean_target_color(
        target_color_name,
        target_color_hex,
        has_product_references=bool(refs),
    )
    payload = {
        "aspectRatio": _cache_choice(
            aspect_ratio, field="aspect_ratio", allowed=_ASPECT_RATIOS
        ),
        "imageSize": _cache_choice(
            image_size, field="image_size", allowed=_IMAGE_SIZES
        ),
        "mannequinKey": _cache_text(mannequin_key, field="mannequin_key"),
        "productRefKeys": refs,
        "projectId": _cache_text(project_id, field="project_id"),
        "promptVersion": _cache_text(prompt_version, field="prompt_version"),
        "resolvedModelId": _cache_text(
            resolved_model_id, field="resolved_model_id"
        ),
        "targetColorId": _cache_text(target_color_id, field="target_color_id"),
        "targetColorName": color_name,
        "targetColorHex": color_hex,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def cache_key(
    *,
    project_id: str,
    mannequin_key: str,
    target_color_id: str,
    target_color_name: str | None = None,
    target_color_hex: str | None = None,
    product_ref_keys: Iterable[str] = (),
    resolved_model_id: str,
    image_size: str,
    aspect_ratio: str,
    prompt_version: str = PROMPT_VERSION,
    extension: str = "png",
) -> str:
    """Return an R2-compatible key only; this function never reads or writes R2."""

    ext = str(extension or "").lower().lstrip(".")
    if ext not in _CACHE_EXTENSIONS:
        raise MannequinColorwayError("invalid_cache_extension")
    project_hash = hashlib.sha256(
        _cache_text(project_id, field="project_id").encode("utf-8")
    ).hexdigest()[:16]
    digest = cache_fingerprint(
        project_id=project_id,
        mannequin_key=mannequin_key,
        target_color_id=target_color_id,
        target_color_name=target_color_name,
        target_color_hex=target_color_hex,
        product_ref_keys=product_ref_keys,
        resolved_model_id=resolved_model_id,
        image_size=image_size,
        aspect_ratio=aspect_ratio,
        prompt_version=prompt_version,
    )
    cleaned_version = _cache_text(prompt_version, field="prompt_version")
    safe_version = re.sub(r"[^A-Za-z0-9._-]", "-", cleaned_version)[:80]
    return f"derived/mannequin-colorway/{safe_version}/{project_hash}/{digest}.{ext}"


async def generate(
    settings: Settings,
    gemini: GeminiImageClient,
    current_mannequin: InlineImage,
    *,
    eligibility: str,
    target_color_name: str | None = None,
    target_color_hex: str | None = None,
    target_product_images: Sequence[InlineImage] = (),
) -> tuple[bytes, str]:
    """Edit one mannequin image, changing only the main product's colorway.

    There is exactly one image-generation call.  The current mannequin is always
    image 1/edit target; optional seller photos follow as color-only evidence.
    """

    require_color_only_eligibility(eligibility)
    _validate_image(current_mannequin, field="current_mannequin")
    if isinstance(target_product_images, (str, bytes)) or not isinstance(
        target_product_images, Sequence
    ):
        raise MannequinColorwayError("invalid_target_product_images")
    refs = list(target_product_images)
    for image in refs:
        _validate_image(image, field="target_product_image")
    prompt = render_prompt(
        eligibility=eligibility,
        target_color_name=target_color_name,
        target_color_hex=target_color_hex,
        product_reference_count=len(refs),
    )
    result = await gemini.generate_content_image(
        # 색상 앵커는 새 컷을 창작하는 단계가 아니라 한정된 편집 1회다. 사용자 확정대로
        # Flash tier를 써서 프로젝트·색상당 비용을 낮추고, 이 결과를 이후 Pro 컷들이 재사용한다.
        resolve_model(settings, "image_light"),
        prompt,
        [current_mannequin, *refs],
        settings.mannequin_image_size,
        aspect_ratio=settings.mannequin_aspect_ratio,
    )
    return result.image, result.mime
