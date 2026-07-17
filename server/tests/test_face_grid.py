"""grid_sedcard 합성 검증 — 정사각·해상도·PNG·3장 미만 처리."""

import io

import numpy as np
import pytest
from PIL import Image

from app.agents.face_grid import compose_sedcard


def _png(color: int) -> bytes:
    img = np.full((200, 300, 3), color, np.uint8)  # 비정사각 입력 → 크롭 확인
    out = io.BytesIO()
    Image.fromarray(img).save(out, format="PNG")
    return out.getvalue()


def test_compose_sedcard_square_png():
    out = compose_sedcard([_png(10), _png(120), _png(240)])
    im = Image.open(io.BytesIO(out))
    assert im.width == im.height
    assert im.width >= 1024
    assert im.format == "PNG"


def test_compose_sedcard_empty_raises():
    with pytest.raises(ValueError):
        compose_sedcard([])


def test_compose_sedcard_handles_fewer_than_three():
    out = compose_sedcard([_png(50)])  # 1장이어도 크래시 없이 그리드 생성
    im = Image.open(io.BytesIO(out))
    assert im.width == im.height
