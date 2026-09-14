"""신분증 사진의 주민등록번호 자리가 실제로 덮였는지 검사.

가이드 촬영이면 카드가 프레임을 채우므로 번호 자리는 규격 비율로 계산된다.
덮였다면 그 영역은 **단색**이다 — 표준편차가 거의 0 이고 평균이 어둡다.

이 검사가 v1 스펙 §7.2 의 한계("서버는 마스킹을 검증할 수 없다")를 정상 경로에서
없앤다. 다만 임계는 캘리브 전이므로 shadow 로 먼저 켠다(설계 §7).

⚠️ RRN_REGION 은 프런트 `src/features/model/idCardGeometry.js` 의 같은 이름 상수와
값이 일치해야 한다. 어긋나면 클라가 덮은 자리와 서버가 보는 자리가 달라져 정상
사진이 거부된다 — 테스트가 두 값을 대조한다.

이 모듈은 순수 함수만 둔다 — FastAPI·DB·R2 어느 것도 모른다. bytes 를 받아
판정을 돌려줄 뿐이고, 호출부(facemarket_enrollment.py)가 라우팅·로깅·거부를 맡는다.
"""
import logging

import cv2
import numpy as np

logger = logging.getLogger("wearless.fm_id_mask")

RRN_REGION = {"xr": 0.06, "yr": 0.58, "wr": 0.62, "hr": 0.14}

# 단색으로 덮였다면 표준편차가 거의 0 이다. 임계는 캘리브 전 출발값이다.
MAX_STDDEV = 12.0
MAX_MEAN = 90.0     # 어두운 색으로 덮는다(#111)


def mask_is_applied(image_bytes: bytes) -> tuple[bool, dict]:
    try:
        image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    except Exception:
        image = None
    if image is None:
        return False, {"reason": "decode_failed"}

    h, w = image.shape[:2]
    x0 = int(RRN_REGION["xr"] * w); y0 = int(RRN_REGION["yr"] * h)
    x1 = min(w, x0 + int(RRN_REGION["wr"] * w))
    y1 = min(h, y0 + int(RRN_REGION["hr"] * h))
    if x1 <= x0 or y1 <= y0:
        return False, {"reason": "region_empty"}

    region = image[y0:y1, x0:x1]
    mean = float(region.mean())
    stddev = float(region.std())
    applied = stddev <= MAX_STDDEV and mean <= MAX_MEAN
    return applied, {"mean": round(mean, 2), "stddev": round(stddev, 2)}
