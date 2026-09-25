"""신분증 사진의 주민등록번호 자리가 실제로 덮였는지 검사.

가이드 촬영이면 카드가 프레임을 채우므로 번호 자리는 규격 비율로 계산된다.
덮였다면 그 영역은 **단색**이다 — 표준편차가 거의 0 이고 평균이 어둡다.

이 검사가 v1 스펙 §7.2 의 한계("서버는 마스킹을 검증할 수 없다")를 정상 경로에서
없앤다. 다만 임계는 캘리브 전이므로 shadow 로 먼저 켠다(설계 §7).

⚠️ 이 파일의 `CARD_ASPECT`·`RRN_REGION`·`guide_rect_in_frame`·`rrn_rect_in_frame` 은
프런트 `src/features/model/idCardGeometry.js` 의 `CARD_ASPECT`·`RRN_REGION`·
`guideRectInFrame`·`rrnRectInFrame` 을 그대로 이식한 것이어야 한다. 프런트는 카드가
프레임을 채우는 가이드 박스를 먼저 잡고(`guideRectInFrame`) 그 안에서 번호 자리를
계산한다(`rrnRectInFrame`) — 이 두 단계를 그대로 밟지 않고 RRN_REGION 비율을 프레임
전체에 바로 적용하면(1차 구현의 버그) 어긋난 사각형을 보게 된다. 세로 사진에서는
겹침이 0%까지 벌어진다(가이드 상자가 프레임을 꽉 채우지 않는 세로 프레임에서 특히
심하다) — 이 기능이 존재하는 바로 그 촬영 방향이라 치명적이다. 테스트가 두 파일의
"계산된 사각형"을 대조한다(스칼라 상수 대조만으로는 이 종류의 공식 불일치를 못 잡는다).

이 모듈은 순수 함수만 둔다 — FastAPI·DB·R2 어느 것도 모른다. bytes 를 받아
판정을 돌려줄 뿐이고, 호출부(facemarket_enrollment.py)가 라우팅·로깅·거부를 맡는다.
"""
import math

import cv2
import numpy as np

# ISO/IEC 7810 ID-1 카드 규격(85.6 × 53.98mm) — 프런트 idCardGeometry.js 의
# CARD_ASPECT 와 동일해야 한다.
CARD_ASPECT = 85.6 / 53.98

# 카드 기준 주민등록번호 영역(0~1 비율) — 프런트 idCardGeometry.js 의 RRN_REGION 과
# 같은 값이어야 한다(테스트가 대조한다). 이 비율은 guide_rect_in_frame() 이 잡은
# 가이드 박스 "안에서" 적용된다 — 프레임 전체에 바로 적용하면 안 된다.
RRN_REGION = {"xr": 0.06, "yr": 0.58, "wr": 0.62, "hr": 0.14}

# 단색으로 덮였다면 표준편차가 거의 0 이다. 임계는 캘리브 전 출발값이다.
MAX_STDDEV = 12.0
MAX_MEAN = 90.0     # 어두운 색으로 덮는다(#111)


def _js_round(x: float) -> int:
    """JS `Math.round` 와 같은 반올림(절반은 올림) — 이 함수의 입력은 전부 양수다.

    Python 내장 `round()` 는 은행가 반올림(0.5 를 가장 가까운 짝수로) 이라, 절반
    경계에서 프런트와 결과가 갈릴 수 있다(리뷰 finding). `math.floor(x + 0.5)` 는
    양수에서 JS 와 정확히 같은 결과를 낸다.
    """
    return math.floor(x + 0.5)


def guide_rect_in_frame(frame_w: float, frame_h: float, fill: float = 0.86) -> dict:
    """프레임 안에서 카드 비율을 유지하는 가장 큰 사각형을 잡고, fill 만큼 줄인다.

    프런트 `idCardGeometry.guideRectInFrame` 과 한 줄씩 동일해야 한다.
    """
    by_width = frame_w / CARD_ASPECT <= frame_h
    w = (frame_w if by_width else frame_h * CARD_ASPECT) * fill
    h = w / CARD_ASPECT
    return {
        "x": _js_round((frame_w - w) / 2),
        "y": _js_round((frame_h - h) / 2),
        "w": _js_round(w),
        "h": _js_round(h),
    }


def rrn_rect_in_frame(frame_w: float, frame_h: float, fill: float = 0.86) -> dict:
    """가이드 박스 안에서 주민등록번호 자리의 프레임 픽셀 좌표.

    프런트 `idCardGeometry.rrnRectInFrame` 과 한 줄씩 동일해야 한다 — guide_rect_in_frame
    으로 먼저 가이드 박스를 잡고, RRN_REGION 비율을 그 박스 "안에서" 적용한다.
    """
    g = guide_rect_in_frame(frame_w, frame_h, fill)
    return {
        "x": _js_round(g["x"] + RRN_REGION["xr"] * g["w"]),
        "y": _js_round(g["y"] + RRN_REGION["yr"] * g["h"]),
        "w": _js_round(RRN_REGION["wr"] * g["w"]),
        "h": _js_round(RRN_REGION["hr"] * g["h"]),
    }


def mask_is_applied(image_bytes: bytes, *, region: dict | None = None) -> tuple[bool, dict]:
    """지정된 영역의 덮어쓰기만 검사한다. 번호 위치의 의미 판정은 하지 않는다."""
    if region is not None:
        if (not isinstance(region, dict) or set(region) != {"xr", "yr", "wr", "hr"}
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in region.values())
                or region["xr"] < 0 or region["yr"] < 0
                or region["wr"] < .02 or region["hr"] < .02
                or region["xr"] + region["wr"] > 1.000001
                or region["yr"] + region["hr"] > 1.000001):
            return False, {"reason": "invalid_region"}
    try:
        image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    except Exception:
        image = None
    if image is None:
        return False, {"reason": "decode_failed"}

    h, w = image.shape[:2]
    if region is None:
        rect = rrn_rect_in_frame(w, h)
    else:
        x, y = math.floor(region["xr"] * w), math.floor(region["yr"] * h)
        rect = {"x": x, "y": y,
                "w": math.ceil((region["xr"] + region["wr"]) * w) - x,
                "h": math.ceil((region["yr"] + region["hr"]) * h) - y}
    x0 = max(0, rect["x"]); y0 = max(0, rect["y"])
    x1 = min(w, x0 + rect["w"]); y1 = min(h, y0 + rect["h"])
    if x1 <= x0 or y1 <= y0:
        return False, {"reason": "region_empty"}

    # JPEG 경계의 번짐은 제외하고 실제 불투명하게 덮인 내부 픽셀을 확인한다.
    inset = 1 if region is not None and min(x1 - x0, y1 - y0) > 4 else 0
    region = image[y0 + inset:y1 - inset, x0 + inset:x1 - inset]
    mean = float(region.mean())
    stddev = float(region.std())
    applied = stddev <= MAX_STDDEV and mean <= MAX_MEAN
    return applied, {"mean": round(mean, 2), "stddev": round(stddev, 2)}
