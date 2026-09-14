"""업로드된 신분증 사진의 주민등록번호 자리가 실제로 덮였는지 검사.

카드가 가이드를 채우도록 찍히므로 그 자리는 규격으로 계산된다. v1 스펙 §7.2 가
인정했던 한계("서버는 마스킹을 검증할 수 없다")를 정상 경로에서 없애는 검사다.

임계는 캘리브 전이므로 shadow 로 먼저 켠다 — v1 에서 SFace 임계를 캘리브 없이
넣었다가 오탈락으로 고생한 선례가 있다.
"""
import cv2
import numpy as np
import pytest

from app.facemarket_id_mask_verify import RRN_REGION, mask_is_applied


def _card(masked: bool) -> bytes:
    """가이드를 채운 카드 한 장. masked=True 면 주민번호 자리를 단색으로 덮는다."""
    img = np.full((540, 856, 3), 200, np.uint8)          # 밝은 카드
    img[::7, :] = 120                                     # 텍스트처럼 보이는 줄무늬
    if masked:
        x = int(RRN_REGION["xr"] * 856); y = int(RRN_REGION["yr"] * 540)
        w = int(RRN_REGION["wr"] * 856); h = int(RRN_REGION["hr"] * 540)
        img[y:y + h, x:x + w] = 17                        # 단색으로 덮음
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def test_masked_card_passes():
    applied, metrics = mask_is_applied(_card(masked=True))
    assert applied is True, metrics


def test_unmasked_card_fails():
    applied, metrics = mask_is_applied(_card(masked=False))
    assert applied is False, metrics


def test_metrics_explain_the_verdict():
    _, metrics = mask_is_applied(_card(masked=True))
    assert "stddev" in metrics and "mean" in metrics, "판정 근거가 없으면 캘리브를 못 한다"


def test_unreadable_bytes_do_not_crash():
    applied, metrics = mask_is_applied(b"not an image")
    assert applied is False
    assert metrics.get("reason") == "decode_failed"


def test_region_matches_frontend():
    """프런트 idCardGeometry.RRN_REGION 과 같은 값이어야 한다 — 어긋나면
    클라가 덮은 자리와 서버가 보는 자리가 달라져 정상 사진이 거부된다."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[2] / "src/features/model/idCardGeometry.js").read_text()
    for key, value in RRN_REGION.items():
        assert f"{key}: {value}" in js, f"{key} 가 프런트와 다르다"
