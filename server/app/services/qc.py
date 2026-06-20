"""값싼 사전필터 QC (AI 아님, Pillow만). spike에서 본 실패 모드(유령·크롭)를 공짜로 거른다.

의미 판정(같은 옷인가·로고)은 비전 QC 에이전트(AG-P2)의 몫 — 여기선 결정적 픽셀 검사만.
임계값은 추정치라 초기엔 shadow 모드(판정 로그만, 게이팅 X)로 캘리브레이션 후 켠다(워커 결정).
"""

from dataclasses import dataclass, field
from io import BytesIO

from PIL import Image, ImageChops, ImageFilter, ImageStat

# --- 임계값 (실측 1K 정상/유령 표본으로 캘리브레이션 대상) ---
FG_THRESHOLD = 28  # 배경과의 거리 > 이 값 = 전경
STRONG_THRESHOLD = 80  # 이만큼 진하면 '확실한 전경'(유령은 거의 없음)
MIN_SIDE = 640
ASPECT_MIN, ASPECT_MAX = 0.62, 0.85  # 세로 비율
BBOX_TOP_MAX = 0.16  # 전경 상단이 이보다 위
BBOX_BOTTOM_MIN = 0.86  # 전경 하단이 이보다 아래
BBOX_HEIGHT_MIN = 0.72  # 전경 높이 비율
LOWER_BODY_MIN_RATIO = 0.012  # 하단 12% 영역의 전경 비율
STRONG_FG_MIN_RATIO = 0.05  # 확실한 전경 비율 (유령이면 낮음)


@dataclass
class QcResult:
    verdict: str  # 'pass' | 'retry'
    reasons: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def _bg_color(img: Image.Image) -> tuple[int, int, int]:
    w, h = img.size
    b = max(2, min(w, h) // 50)
    edges = [
        img.crop((0, 0, w, b)),
        img.crop((0, h - b, w, h)),
        img.crop((0, 0, b, h)),
        img.crop((w - b, 0, w, h)),
    ]
    rs, gs, bs = [], [], []
    for e in edges:
        m = ImageStat.Stat(e).mean
        rs.append(m[0]); gs.append(m[1]); bs.append(m[2])
    return (int(sum(rs) / 4), int(sum(gs) / 4), int(sum(bs) / 4))


def evaluate_mannequin_qc(generated_bytes: bytes) -> QcResult:
    try:
        img = Image.open(BytesIO(generated_bytes)).convert("RGB")
    except Exception:
        return QcResult("retry", ["decode_failed"])

    w, h = img.size
    metrics = {"width": w, "height": h, "aspect": round(w / h, 3)}
    reasons: list[str] = []

    if w < MIN_SIDE or h < MIN_SIDE:
        reasons.append("too_small")
    if not (ASPECT_MIN <= w / h <= ASPECT_MAX):
        reasons.append("bad_aspect_ratio")

    # 배경과의 거리 → 전경 마스크
    bg = Image.new("RGB", img.size, _bg_color(img))
    gray = ImageChops.difference(img, bg).convert("L")
    # 'L' point→0/255 이진 마스크. MinFilter로 stray 단일 픽셀 침식 → bbox 부풀림 완화
    fg = gray.point(lambda v: 255 if v > FG_THRESHOLD else 0).filter(ImageFilter.MinFilter(3))
    strong = gray.point(lambda v: 255 if v > STRONG_THRESHOLD else 0)

    total = w * h
    fg_count = fg.histogram()[-1]  # 255(전경) 픽셀 수
    strong_count = strong.histogram()[-1]
    metrics["strongFgRatio"] = round(strong_count / total, 4)

    bbox = fg.getbbox()  # (l, t, r, b) or None
    if not bbox or fg_count < total * 0.02:
        reasons.append("ghost_or_artifact")
        return QcResult("retry", reasons, metrics)

    l, t, r, b = bbox
    metrics |= {
        "bboxTop": round(t / h, 3),
        "bboxBottom": round(b / h, 3),
        "bboxHeight": round((b - t) / h, 3),
    }
    if t > h * BBOX_TOP_MAX or b < h * BBOX_BOTTOM_MIN or (b - t) < h * BBOX_HEIGHT_MIN:
        reasons.append("full_body_crop")

    # 하단 12% 전경 존재(발/다리) — 크롭·유령 양쪽 탐지
    lower = fg.crop((0, int(h * 0.88), w, h))
    if lower.histogram()[-1] < total * LOWER_BODY_MIN_RATIO:
        reasons.append("missing_lower_body")

    # 유령: 확실한 전경 비율이 낮으면 옅게 번진 것
    if strong_count < total * STRONG_FG_MIN_RATIO:
        reasons.append("ghost_or_artifact")

    return QcResult("pass" if not reasons else "retry", reasons, metrics)


def format_qc_feedback(result: QcResult) -> str:
    """QC 실패 이유 → 다음 시도에 얹을 최우선 교정 지시 (reflexion)."""
    hints = {
        "full_body_crop": "Show the FULL body from head to feet; do not crop the legs or zoom in.",
        "missing_lower_body": "The legs and feet must be fully visible at the bottom of the frame.",
        "ghost_or_artifact": "Render a SOLID, fully opaque mannequin — not translucent, faded, or ghosted.",
        "bad_aspect_ratio": "Output a portrait image matching the base photo's aspect ratio.",
        "too_small": "Output a high-resolution image.",
        "decode_failed": "Output a valid photographic image.",
    }
    seen = [hints[r] for r in result.reasons if r in hints]
    if not seen:
        return ""
    return "CORRECTION (highest priority — the previous attempt failed): " + " ".join(seen)
