"""입력측 업로드 QC — 손상·저해상 상품사진을 소스 단계에서 거른다 (FR-D4).

output QC(services/qc.py)가 '생성된 이미지'의 유령·크롭을 잡는다면, 이 모듈은
'셀러가 올린 원본'이 생성에 쓸 만한지 검사한다 — garbage-in 방지. 결정적 Pillow만.

⚠️ blur(흐림) 검사는 넣지 않는다: 이 앱의 상품사진은 평무지 배경이 많아 엣지 분산
기반 blur 판정이 정상 사진을 흐림으로 오탐(→정상 업로드 차단)한다. blur는 실제
정상/흐림 상품사진 표본으로 캘리브레이션한 뒤에만 추가할 것. 지금은 decode 유효성 +
최소 해상도만 — 오탐 0인 견고한 검사만 담는다.

임계는 관대하게(초소형 썸네일만 차단). flag INPUT_QC=shadow로 로그 보고 조정 후 enforce.
"""

from dataclasses import dataclass, field
from io import BytesIO

from PIL import Image

MIN_SIDE = 400  # 최소 변(px) — 관대(제품 상세용 사진은 통상 훨씬 큼, 초소형만 차단)


@dataclass
class InputQcResult:
    verdict: str  # 'pass' | 'reject'
    reasons: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def evaluate_input_qc(image_bytes: bytes) -> InputQcResult:
    """업로드 원본 바이트 → pass/reject. decode 실패·초소형만 reject(오탐 회피)."""
    try:
        img = Image.open(BytesIO(image_bytes))
        img.load()  # 전체 디코드 강제 — 헤더만 읽는 .size가 놓치는 truncated JPEG까지 검출
        w, h = img.size
    except Exception:
        return InputQcResult("reject", ["decode_failed"])

    metrics = {"width": w, "height": h}
    reasons: list[str] = []
    if min(w, h) < MIN_SIDE:
        reasons.append("too_small")
    return InputQcResult("pass" if not reasons else "reject", reasons, metrics)


def input_qc_message(result: InputQcResult) -> str:
    """reject 사유 → 셀러용 한국어 안내(enforce 400 메시지)."""
    msgs = {
        "decode_failed": "이미지 파일이 손상됐거나 열 수 없어요. 다른 사진으로 다시 올려주세요.",
        "too_small": f"사진이 너무 작아요. 가로·세로 최소 {MIN_SIDE}px 이상 사진을 올려주세요.",
    }
    seen = [msgs[r] for r in result.reasons if r in msgs]
    return " ".join(seen) or "업로드한 사진을 사용할 수 없어요. 다른 사진으로 다시 시도해 주세요."
