"""불확정 경로의 결정론 폴백 — 승격된 픽셀만 나가고, 실패는 전부 typed 다."""

import numpy as np

from app.services.hybrid_composite import direct_transfer_fallback as fb


def _garment(w=500, h=700, colour=(70, 110, 190)):
    """단순한 상의 실루엣 + 원단."""
    img = np.full((h, w, 3), 240, np.uint8)
    body = np.zeros((h, w), np.uint8)
    quad = np.int32([[int(w * 0.22), int(h * 0.12)], [int(w * 0.78), int(h * 0.12)],
                     [int(w * 0.84), int(h * 0.88)], [int(w * 0.16), int(h * 0.88)]])
    import cv2
    cv2.fillPoly(body, [quad], 255)
    stripes = np.arange(w)[None, :] // 9 % 2
    fabric = np.where(stripes[..., None] == 0, np.uint8(colour),
                      np.uint8([max(0, c - 60) for c in colour]))
    img[body > 0] = np.broadcast_to(fabric, (h, w, 3))[body > 0]
    return img


def _landmarks():
    return {"shoulder_l": [0.22, 0.12], "shoulder_r": [0.78, 0.12],
            "hem_r": [0.84, 0.88], "hem_l": [0.16, 0.88]}


def test_a_render_that_cannot_be_promoted_returns_no_pixels():
    """승격 못 하면 **픽셀을 주지 않는다** — 보존과 권한은 다르다.

    조건부 단언(`if not out.applied:`)은 시험이 아니다. 승격 여부와 무관하게 성립해야
    하는 불변식으로 적는다: 픽셀이 있다 == 승격됐다.
    """
    out = fb.attempt_direct_fallback(
        carrier_bgr=np.full((700, 500, 3), 150, np.uint8),
        carrier_landmarks=_landmarks(),
        source_bgr=_garment(),
        source_landmarks=_landmarks())
    assert isinstance(out, fb.DirectFallback)
    assert (out.image_bgr is not None) == out.applied
    assert bool(out.reasons) != out.applied


def test_a_raising_panel_map_is_a_typed_refusal(monkeypatch):
    """이 경로의 계약은 '후보 실패이지 잡 실패가 아니다' — 예외가 새면 계약이 깨진다."""
    def boom(*a, **k):
        raise RuntimeError("panel-map exploded")

    monkeypatch.setattr(fb, "build_panel_map", boom)
    out = fb.attempt_direct_fallback(
        carrier_bgr=np.full((700, 500, 3), 150, np.uint8),
        carrier_landmarks=_landmarks(),
        source_bgr=_garment(),
        source_landmarks=_landmarks())
    assert out.applied is False
    assert out.image_bgr is None
    assert fb.REASON_CARRIER_PANEL_MAP in out.reasons
    assert "panel-map exploded" in str(out.detail)


def test_a_raising_transfer_is_a_typed_refusal(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("transfer exploded")

    monkeypatch.setattr(fb, "run_gated_direct_transfer", boom)
    out = fb.attempt_direct_fallback(
        # carrier 도 실루엣이 있어야 panel map 이 서고, 그래야 전송까지 **도달**한다.
        carrier_bgr=_garment(colour=(150, 150, 150)),
        carrier_landmarks=_landmarks(),
        source_bgr=_garment(),
        source_landmarks=_landmarks())
    assert out.applied is False
    assert out.image_bgr is None
    assert fb.REASON_TRANSFER_RAISED in out.reasons


def test_an_unusable_carrier_is_a_typed_result_not_an_exception():
    """폴백이 안 되는 것은 후보 실패이지 잡 실패가 아니다."""
    out = fb.attempt_direct_fallback(
        carrier_bgr=np.zeros((10, 10, 3), np.uint8),
        carrier_landmarks={},
        source_bgr=_garment(),
        source_landmarks=_landmarks())
    assert out.applied is False
    assert out.image_bgr is None
    assert out.reasons


def test_the_fallback_never_calls_a_provider_or_touches_storage():
    """구조로 고정한다 — 이 경로에 provider·DB·R2 가 끼어들 자리가 없다."""
    import inspect
    src = inspect.getsource(fb)
    for banned in ("genai", "requests", "httpx", "boto3", "psycopg", "await ",
                   "async def", "upload", "r2_"):
        assert banned not in src, banned


