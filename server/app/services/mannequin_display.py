"""Deterministic seller-display enlargement for native 1K mannequin PNGs."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image


ALGORITHM = "pillow-lanczos-2x-v1"
METADATA_KEY = "sellerDisplay"
NATIVE_SIZE = (1024, 1536)
DISPLAY_SIZE = (2048, 3072)
DISPLAY_MIME = "image/png"


@dataclass(frozen=True)
class Derivative:
    key: str
    data: bytes
    metadata: dict


def key_for(native_key: str) -> str:
    """Return the sole allowed display key for a canonical asset key."""
    return f"{native_key}.seller-display-2x.png"


def build(native: bytes, mime: str, native_key: str) -> Derivative | None:
    """Create a 2x LANCZOS PNG only for an exact native Sunburst 1K canvas."""
    if mime != DISPLAY_MIME:
        return None
    try:
        with Image.open(BytesIO(native)) as source:
            source.load()
            if source.size != NATIVE_SIZE:
                return None
            enlarged = source.resize(DISPLAY_SIZE, Image.Resampling.LANCZOS)
            out = BytesIO()
            enlarged.save(out, format="PNG")
    except Exception:
        return None
    data = out.getvalue()
    key = key_for(native_key)
    return Derivative(
        key=key,
        data=data,
        metadata={
            "algorithm": ALGORITHM,
            "r2Key": key,
            "mimeType": DISPLAY_MIME,
            "sourceWidth": NATIVE_SIZE[0],
            "sourceHeight": NATIVE_SIZE[1],
            "width": DISPLAY_SIZE[0],
            "height": DISPLAY_SIZE[1],
            "byteSize": len(data),
        },
    )


def resolve_key(asset: dict) -> str | None:
    """Resolve a stored display key only when it is bound to this exact native asset."""
    metadata = asset.get("metadata")
    display = metadata.get(METADATA_KEY) if isinstance(metadata, dict) else None
    native_key = asset.get("r2_key")
    if not isinstance(display, dict) or not isinstance(native_key, str) or not native_key:
        return None
    expected = {
        "algorithm": ALGORITHM,
        "r2Key": key_for(native_key),
        "mimeType": DISPLAY_MIME,
        "sourceWidth": NATIVE_SIZE[0],
        "sourceHeight": NATIVE_SIZE[1],
        "width": DISPLAY_SIZE[0],
        "height": DISPLAY_SIZE[1],
    }
    if any(display.get(field) != value for field, value in expected.items()):
        return None
    if (asset.get("width"), asset.get("height")) != NATIVE_SIZE:
        return None
    return expected["r2Key"]
