"""Server-owned representative-color render plan and final color application.

Stored recipes always describe the desired final color. Only the render copy uses
representative-color evidence; the color photograph has authority over color alone.
"""
from __future__ import annotations

import hashlib
from io import BytesIO

from PIL import Image
from dataclasses import dataclass

from . import garment_edit
from .gemini_image import InlineImage


class GarmentColorError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ColorPlan:
    mode: str
    target_color_id: str | None
    base_color_id: str | None
    render_color_id: str | None
    target_asset_ids: tuple[str, ...] = ()


def plan_for(product: dict, block: dict) -> ColorPlan:
    colors = product.get("colors") or []
    base = next((color for color in colors if color.get("isBase")), colors[0] if colors else {})
    base_id = str(base["id"]) if base.get("id") is not None else None
    selected = block.get("colorId")
    target = next((color for color in colors if str(color.get("id")) == str(selected)), None) if selected is not None else base
    if selected is not None and target is None:
        raise GarmentColorError("color_unknown")
    target_id = str(target["id"]) if target and target.get("id") is not None else None
    images = [image for image in (target or {}).get("images") or [] if isinstance(image, dict) and image.get("id")]
    ids = tuple(str(image["id"]) for image in sorted(images, key=lambda image: image.get("slot") != "Front"))
    # Legacy requests without any color still retain the old no-garment guard.
    if selected is not None and not ids:
        raise GarmentColorError("color_reference_unavailable")
    worn = block.get("cutType", "styling") in garment_edit.WORN_CUT_TYPES
    mode = "base_then_color" if worn and target_id != base_id else "direct"
    if mode == "base_then_color":
        ids = tuple(str(image["id"]) for image in images if image.get("slot") == "Front")
        if not ids:
            raise GarmentColorError("color_reference_unavailable")
    return ColorPlan(mode, target_id, base_id, base_id if mode == "base_then_color" else target_id, ids)


def render_spec(block: dict, plan: ColorPlan) -> dict:
    return {**block, "colorId": plan.render_color_id} if plan.mode == "base_then_color" else dict(block)


def execution(plan: ColorPlan, target_asset: dict | None = None, target_image: InlineImage | None = None,
              *, master_asset_id=None, master_sha256=None) -> dict:
    return {
        "version": 1, "mode": plan.mode, "targetColorId": plan.target_color_id,
        "baseColorId": plan.base_color_id, "renderColorId": plan.render_color_id,
        "targetEvidence": ([{"assetId": str(target_asset["id"]),
                             "sha256": hashlib.sha256(target_image.data).hexdigest()}]
                           if target_asset is not None and target_image is not None else []),
        "masterAssetId": master_asset_id, "masterSha256": master_sha256,
        "colorCheck": {"status": "not_run"},
    }


async def apply(settings, provider, master: InlineImage, target: InlineImage, plan: ColorPlan,
                *, cut_type: str, clothing_type: str, front_closure: dict, **face_kwargs) -> tuple[bytes, str]:
    """Exactly one edit after the caller's full render. Never substitute the master on failure."""
    if target is None or not target.data:
        raise GarmentColorError("color_reference_unavailable")
    try:
        canvas = await garment_edit.run_cpu_bound(_canvas_request, master.data)
        image, mime = await garment_edit.generate(settings, provider, master, target, cut_type,
            mode="color", clothing_type=clothing_type, closure_state=None,
            front_closure=front_closure, output_size_override=canvas, **face_kwargs)
        return await garment_edit.run_cpu_bound(_restore_canvas, master.data, image, mime, canvas)
    except garment_edit.face_identity.FacePassUnavailable:
        raise
    except Exception as exc:
        raise GarmentColorError("color_application_failed") from exc


def _canvas_request(master: bytes) -> str | None:
    try:
        with Image.open(BytesIO(master)) as image:
            width, height = image.size
    except (OSError, ValueError):
        return None
    if min(width, height) < 64 or max(width, height) > 3840 or not 1 / 3 <= width / height <= 3:
        return None
    # GPT's 16px grid may require a few pixels of proportional resampling.
    # Keep the source's actual resolution instead of scaling to a different tier.
    grid_w, grid_h = width // 16 * 16, height // 16 * 16
    if abs((grid_w / grid_h) / (width / height) - 1) > .01:
        raise GarmentColorError("color_application_failed")
    return f"{grid_w}x{grid_h}"


def _restore_canvas(master: bytes, output: bytes, mime: str, requested: str | None) -> tuple[bytes, str]:
    try:
        with Image.open(BytesIO(master)) as original:
            size = original.size
    except (OSError, ValueError):
        return output, mime
    with Image.open(BytesIO(output)) as edited:
        if edited.size == size:
            return output, mime
        requested_size = tuple(map(int, requested.split("x"))) if requested else None
        if edited.size != requested_size:
            raise GarmentColorError("color_application_failed")
        resized = edited.resize(size, Image.Resampling.LANCZOS)
        result = BytesIO()
        resized.save(result, format="PNG")
        return result.getvalue(), "image/png"
