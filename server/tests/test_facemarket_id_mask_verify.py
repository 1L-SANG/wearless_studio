"""업로드된 신분증 사진의 주민등록번호 자리가 실제로 덮였는지 검사.

카드가 가이드를 채우도록 찍히므로 그 자리는 규격으로 계산된다. v1 스펙 §7.2 가
인정했던 한계("서버는 마스킹을 검증할 수 없다")를 정상 경로에서 없애는 검사다.

임계는 캘리브 전이므로 shadow 로 먼저 켠다 — v1 에서 SFace 임계를 캘리브 없이
넣었다가 오탈락으로 고생한 선례가 있다.

Fix round 1(리뷰 finding): 1차 구현은 RRN_REGION 비율을 프레임 전체에 바로
적용했다. 프런트는 그 전에 guideRectInFrame 으로 가이드 박스를 먼저 잡고 그
"안에서" RRN_REGION 을 적용한다(rrnRectInFrame) — 두 사각형은 다르고, 세로
프레임에서는 겹침이 0%까지 벌어진다(가장 중요한 촬영 방향인데도). 스칼라 상수만
대조하는 테스트는 이 공식 불일치를 못 잡으므로, `test_rrn_rect_matches_frontend_table`
이 계산된 사각형 자체를 프런트 실행 결과와 대조한다.

최종리뷰 I4: 사각형을 결정하는 상수는 RRN_REGION 하나가 아니라 RRN_REGION·
CARD_ASPECT·fill 기본값 셋이다. `test_region_matches_frontend` 는 RRN_REGION 만
대조해서, JS 쪽에서 CARD_ASPECT 나 fill 을 재조정하면 JS 골든 테스트는 새 값으로
맞춰 통과하는데 Python 은 하드코딩된 표(`test_rrn_rect_matches_frontend_table`)를
계속 통과해 서버가 조용히 다른 사각형을 보게 된다 — shadow 에선 로그의 판정 분포가
드리프트하는 것 말고는 아무 증상도 없다(enforce 를 올릴 근거가 되는 바로 그 데이터가
오염된다). `test_card_aspect_and_fill_match_frontend` 가 이 둘도 같이 잠근다.
"""
import re

import cv2
import numpy as np
import pytest

from app.facemarket_id_mask_verify import CARD_ASPECT, RRN_REGION, mask_is_applied, rrn_rect_in_frame


def _card(masked: bool) -> bytes:
    """가이드를 채운 카드 한 장. masked=True 면 주민번호 자리를 단색으로 덮는다.

    실제 서버 판정과 같은 공식(rrn_rect_in_frame, 가이드 박스 안에서 RRN_REGION 적용)
    으로 마스크를 칠한다 — RRN_REGION 을 프레임 전체에 바로 적용하면(1차 구현의 버그)
    서버가 보는 자리와 다른 곳을 칠하게 된다.
    """
    img = np.full((540, 856, 3), 200, np.uint8)          # 밝은 카드
    img[::7, :] = 120                                     # 텍스트처럼 보이는 줄무늬
    if masked:
        r = rrn_rect_in_frame(856, 540)
        img[r["y"]:r["y"] + r["h"], r["x"]:r["x"] + r["w"]] = 17  # 단색으로 덮음
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
    클라가 덮은 자리와 서버가 보는 자리가 달라져 정상 사진이 거부된다.

    주의: 이 테스트는 스칼라 상수만 문자열로 대조한다 — 공식(가이드 박스를 먼저
    잡고 그 안에서 비율을 적용하는 중첩 계산) 자체가 어긋나는 건 못 잡는다.
    공식 대조는 아래 test_rrn_rect_matches_frontend_table 이 한다. RRN_REGION 이
    아닌 나머지 두 상수(CARD_ASPECT·fill 기본값)는 아래 test_card_aspect_and_fill_
    match_frontend 가 대조한다."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[2] / "src/features/model/idCardGeometry.js").read_text()
    for key, value in RRN_REGION.items():
        assert f"{key}: {value}" in js, f"{key} 가 프런트와 다르다"


def test_card_aspect_and_fill_match_frontend():
    """CARD_ASPECT·guideRectInFrame 의 fill 기본값도 RRN_REGION 과 같은 이유로
    프런트와 일치해야 한다(최종리뷰 I4).

    셋(RRN_REGION·CARD_ASPECT·fill) 중 이 사각형을 결정하는데, 기존 대조는
    RRN_REGION 만 봤다. JS 쪽에서 CARD_ASPECT 나 fill 을 재조정하면 — 예를 들어
    카드 규격을 바꾸거나 여백을 다시 캘리브하면 — JS 의 골든 테스트(mask 좌표 골든값)는
    그 새 값으로 다시 맞춰져 통과하는데, 이 파일의 다른 테스트(test_rrn_rect_matches_
    frontend_table)는 Python 쪽 하드코딩된 표를 계속 통과한다. 서버는 그렇게 조용히
    다른 사각형을 검사하게 된다 — shadow 에선 로그의 판정 분포가 드리프트하는 것 말고는
    아무 증상도 없다(enforce 를 올릴 근거가 되는 바로 그 데이터가 오염된다). R12 가 잡은
    것과 같은 결함 유형(스칼라 상수 대조만으로는 공식/상수 불일치를 못 잡는다)이
    RRN_REGION 이 아닌 다른 두 상수를 통해 다시 들어오는 길을 막는다.

    실제로 Node 로 프런트 함수를 실행해 대조하는 편이 더 강하지만, 백엔드 CI 잡에
    node 의존을 새로 얹지 않기 위해 기존 테스트들과 같은 문자열 대조 방식을 쓴다 —
    공백은 허용하되(정규식) 값 자체는 엄격히 비교한다.
    """
    import inspect
    from pathlib import Path

    from app.facemarket_id_mask_verify import guide_rect_in_frame

    js = (Path(__file__).resolve().parents[2] / "src/features/model/idCardGeometry.js").read_text()

    card_aspect_match = re.search(r"CARD_ASPECT\s*=\s*([0-9.]+)\s*/\s*([0-9.]+)", js)
    assert card_aspect_match, "프런트에서 `CARD_ASPECT = a / b` 형태를 찾을 수 없다"
    js_card_aspect = float(card_aspect_match.group(1)) / float(card_aspect_match.group(2))
    assert js_card_aspect == pytest.approx(CARD_ASPECT), (
        "CARD_ASPECT 가 프런트와 다르다 — 서버가 계산하는 가이드 박스가 달라진다"
    )

    fill_match = re.search(r"guideRectInFrame\([^)]*\bfill\s*=\s*([0-9.]+)", js)
    assert fill_match, "프런트 guideRectInFrame() 의 fill 기본값을 찾을 수 없다"
    js_fill = float(fill_match.group(1))
    # 0.86 을 여기 다시 하드코딩하지 않는다 — 그러면 Python 쪽만 고치고 이 숫자는
    # 안 고치는 실수가 이 테스트를 무력화한다. 실제 함수 시그니처의 기본값을 읽는다.
    py_fill = inspect.signature(guide_rect_in_frame).parameters["fill"].default
    assert js_fill == py_fill, "fill 기본값이 프런트와 다르다 — 서버가 계산하는 가이드 박스 크기가 달라진다"


def test_rrn_rect_matches_frontend_table():
    """계산된 사각형 자체를 프런트 rrnRectInFrame() 실행 결과와 대조한다(리뷰 finding).

    프런트는 RRN_REGION 비율을 프레임 전체가 아니라 guideRectInFrame() 이 잡은 가이드
    박스 "안에서" 적용한다(rrnRectInFrame). 1차 구현은 이 중첩을 빼먹고 RRN_REGION 을
    프레임 전체에 바로 적용해, 세로 프레임에서 겹침이 0%까지 벌어졌다(가장 중요한
    촬영 방향인데도). 스칼라 상수 대조(위 test_region_matches_frontend)는 이런 공식
    불일치를 못 잡으므로, 여기서는 실제 Node 로 프런트 함수를 실행한 결과값을 그대로
    박아 대조한다(리뷰어가 직접 계산·검증한 값).
    """
    cases = {
        (1920, 1080): {"x": 312, "y": 615, "w": 913, "h": 130},
        (1080, 1920): {"x": 132, "y": 1007, "w": 576, "h": 82},
        (720, 1280): {"x": 87, "y": 671, "w": 384, "h": 55},
        (1440, 1080): {"x": 175, "y": 603, "w": 768, "h": 109},
    }
    for (frame_w, frame_h), expected in cases.items():
        assert rrn_rect_in_frame(frame_w, frame_h) == expected, (frame_w, frame_h)
