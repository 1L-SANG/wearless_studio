"""Linear-light garment tone shared by the seller preview and final render."""
from __future__ import annotations

import numpy as np

TONE_VERSION = "garment-color-v1"
SLIDERS_KEY = "garmentColorSliders"
ZERO_SLIDERS = {"chroma": 0, "lightness": 0, "warmth": 0}
_LUMA = np.array([0.2126, 0.7152, 0.0722])


def srgb8_to_linear(rgb):
    srgb = np.asarray(rgb, dtype=np.float64) / 255
    return np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)


def tone_linear(rgb, *, chroma=0, lightness=0, warmth=0):
    rgb = np.asarray(rgb, dtype=np.float64)
    # Parameters may be broadcast over a grid of candidate colors.
    t = np.asarray(warmth) / 100 * 0.10
    gr, gb = 1 + t, 1 - t
    normalizer = 1 / (_LUMA[0] * gr + _LUMA[1] + _LUMA[2] * gb)
    balanced = rgb * np.stack((gr, np.ones_like(t), gb), axis=-1) * normalizer[..., None]
    y = np.sum(balanced * _LUMA, axis=-1, keepdims=True)
    delta = (1 + np.asarray(chroma)[..., None] / 100 * 0.40) * (balanced - y)

    limit = np.ones_like(delta)
    np.divide(1 - y, delta, out=limit, where=delta > 0)
    np.divide(-y, delta, out=limit, where=delta < 0)
    scale = np.clip(np.min(limit, axis=-1, keepdims=True), 0, 1)
    gain = np.exp2(np.asarray(lightness) / 100 * 0.50)
    return (y + delta * scale) * gain[..., None]



def normalize_sliders(value, color_ids) -> dict | None:
    if (not isinstance(value, dict) or type(value.get("version")) is not int
            or value["version"] != 1 or not isinstance(value.get("colors"), dict)):
        return None
    colors = {}
    for color_id, sliders in value["colors"].items():
        if color_id not in color_ids:
            continue
        if not isinstance(sliders, dict):
            return None
        axes = {axis: sliders.get(axis) for axis in ZERO_SLIDERS}
        if any(type(number) is not int or not -100 <= number <= 100 for number in axes.values()):
            return None
        if any(axes.values()):
            colors[color_id] = axes
    return {"version": 1, "colors": colors}
