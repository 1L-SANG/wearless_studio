"""Validation for optional editor image effects; legacy elements stay unchanged."""
import math


def validate_editor_image_blur(blocks):
    for block in blocks:
        if not isinstance(block, dict):
            continue
        elements = block.get("elements")
        if not isinstance(elements, list):
            continue
        for element in elements:
            if not isinstance(element, dict) or "blur" not in element:
                continue
            value = element["blur"]
            if (element.get("type") != "image" or isinstance(value, bool)
                    or not isinstance(value, (int, float)) or not 0 <= value <= 100
                    or not math.isfinite(value)):
                raise ValueError("이미지 흐림은 0부터 100 사이의 숫자여야 해요.")
