"""값싼 사전필터 QC (AI 아님, Pillow만). spike에서 본 실패 모드(유령·크롭)를 공짜로 거른다.

의미 판정(같은 옷인가·로고)은 비전 QC 에이전트(AG-P2)의 몫 — 여기선 결정적 픽셀 검사만.
임계값은 추정치라 초기엔 shadow 모드(판정 로그만, 게이팅 X)로 캘리브레이션 후 켠다(워커 결정).
"""

from dataclasses import dataclass, field
from io import BytesIO

from PIL import Image, ImageChops, ImageFilter, ImageStat

# --- 임계값 (2026-07-04 캘리브레이션: scripts/qc_calibrate.py — 베이스 마네킹·모델 프리셋 + 합성 실패모드) ---
FG_THRESHOLD = 28  # 배경과의 거리 > 이 값 = 전경
STRONG_THRESHOLD = 80  # 이만큼 진하면 '확실한 전경'(유령은 거의 없음)
MIN_SIDE = 640
ASPECT_MIN, ASPECT_MAX = 0.62, 0.85  # 세로 비율
BBOX_TOP_MAX = 0.16  # 전경 상단이 이보다 위
BBOX_BOTTOM_MIN = 0.86  # 전경 하단이 이보다 아래
BBOX_HEIGHT_MIN = 0.72  # 전경 높이 비율
LOWER_BODY_MIN_RATIO = 0.012  # 하단 12% 영역의 전경 비율 (일반 레짐)
LOWER_BODY_MIN_RATIO_LOW = 0.0001  # 〃 저대비 레짐 — 흰 발/흰 바닥 정상(≈0.0002~0.0004)과 소실(=0) 사이
# 유령 판정 2-레짐 (실측: 흰옷 정상 fg≈0.010/strong≈0.0002 · 흰 유령 fg≈0.0001 · 유색 유령 fg≈0.06/strong≈0.000):
#  - 저대비 레짐(fg < NORMAL_CONTRAST_FG_RATIO — 화이트·아이보리 의류/호리존): strong 검사를 건너뛰고
#    FG_SOLID_MIN_RATIO 미달만 유령으로 본다. 흰옷 정상을 차단하지 않기 위함(모노톤 스와치 실존).
#  - 일반 레짐(fg ≥ NORMAL_CONTRAST_FG_RATIO): strong < STRONG_FG_MIN_RATIO 면 유령(유색 유령 검출 유지).
FG_SOLID_MIN_RATIO = 0.004  # 전경 최소 질량 — 흰 유령(≈0.0001)과 흰옷 정상(≈0.010) 사이
NORMAL_CONTRAST_FG_RATIO = 0.03  # 레짐 경계 — 흰옷 정상(≈0.013)과 유색 유령(≈0.06) 사이
STRONG_FG_MIN_RATIO = 0.05  # 확실한 전경 비율 (유령이면 낮음) — 일반 레짐에서만 적용


@dataclass
class QcResult:
    verdict: str  # 'pass' | 'retry'
    reasons: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def evaluate_canvas_alpha_qc(generated_bytes: bytes) -> QcResult:
    """출력 캔버스에 실제 투명 픽셀이 있는지만 검사한다.

    시스템의 마네킹·스튜디오 이미지는 파일 캔버스 전체가 불투명이어야 한다.
    시스루·메시·레이스 원단의 비침은 배경과 섬유가 섞여 보이는 RGB 색과
    빛으로 이미 합성된 모습이어야 하며, 의류 픽셀 자체의 alpha를 낮추어서는 안 된다.

    alpha 채널이 있어도 모든 픽셀이 255면 실제 투명도는 없으므로 통과한다.
    """
    try:
        img = Image.open(BytesIO(generated_bytes))
        img.load()
    except Exception:
        return QcResult("retry", ["decode_failed"])

    w, h = img.size
    has_alpha_source = "A" in img.getbands() or "transparency" in img.info
    metrics = {
        "width": w,
        "height": h,
        "hasAlphaSource": has_alpha_source,
        "alphaMin": 255,
        "transparentPixelCount": 0,
        "transparentPixelRatio": 0.0,
    }
    if not has_alpha_source:
        return QcResult("pass", metrics=metrics)

    alpha = img.convert("RGBA").getchannel("A")
    histogram = alpha.histogram()
    transparent_count = sum(histogram[:255])
    alpha_min = next((value for value, count in enumerate(histogram) if count), 255)
    metrics.update({
        "alphaMin": alpha_min,
        "transparentPixelCount": transparent_count,
        "transparentPixelRatio": round(transparent_count / (w * h), 6),
    })
    reasons = ["transparent_canvas"] if transparent_count else []
    return QcResult("retry" if reasons else "pass", reasons, metrics)


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
    alpha_result = evaluate_canvas_alpha_qc(generated_bytes)
    if "transparent_canvas" in alpha_result.reasons:
        return alpha_result
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
    if not bbox or fg_count < total * FG_SOLID_MIN_RATIO:
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

    # 하단 12% 전경 존재(발/다리) — 크롭·유령 양쪽 탐지. 임계는 레짐 연동(흰 발·흰 바닥 대응)
    normal_contrast = fg_count >= total * NORMAL_CONTRAST_FG_RATIO
    lower_min = LOWER_BODY_MIN_RATIO if normal_contrast else LOWER_BODY_MIN_RATIO_LOW
    lower = fg.crop((0, int(h * 0.88), w, h))
    if lower.histogram()[-1] < total * lower_min:
        reasons.append("missing_lower_body")

    # 유령(일반 레짐만): 전경 질량은 있는데 확실한 전경이 없으면 옅게 번진 것.
    # 저대비 레짐(흰옷·호리존)은 strong이 원래 0에 수렴 — 위 FG_SOLID_MIN_RATIO 체크만으로 판정.
    if normal_contrast and strong_count < total * STRONG_FG_MIN_RATIO:
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
        "transparent_canvas": (
            "Return a fully opaque image canvas (alpha 255 everywhere). "
            "Show sheer fabric through RGB color and light blending only, never transparent pixels."
        ),
    }
    seen = [hints[r] for r in result.reasons if r in hints]
    if not seen:
        return ""
    return "CORRECTION (highest priority — the previous attempt failed): " + " ".join(seen)


# --- 매칭 하의(코디 바지) 보존 — 편집 전후 로컬 픽셀 비교 (AI 아님, API 비용 0) ---
# bust·untuck 같은 편집은 상의/가슴만 손봐야 한다. 그런데 전체를 다시 렌더하므로 하체
# 바지가 조용히 다른 색·폭·구조로 드리프트할 수 있다. 편집 전후 같은 컷을 픽셀로 대보고
# 바지 영역이 크게 바뀌면 그 편집을 버리게 한다. 원본 상품과 대조가 아니라 **편집 전후**
# 대조라 정체성 판정(AG-P2)과 무관하고, 결정론적이라 vision 콜이 필요 없다.
#
# 국소화는 **고정 세로 밴드**다 — 랜드마크는 이 파이프라인에서 오탐 상수(missing_lower_body).
# 허리 전이대(밴드 상단)를 살짝 비워 두어 상의 밑단·untuck 변화가 바지 판정으로 새지 않게 한다.
PANTS_BAND_TOP = 0.60      # 프레임 높이의 이 지점부터(엉덩이 아래) — 허리 경계 소폭 허용
PANTS_BAND_BOTTOM = 0.97   # 밑단까지. 발/바닥(맨 아래 3%)은 제외
PANTS_FG_MIN_RATIO = 0.02  # 밴드 전경 최소 질량 — 미만이면 색 판정을 건너뛴다(노이즈 방지)
PANTS_COLOR_DELTA_MAX = 40    # 전경 평균색 최대 채널 이동(0-255) — 색 계열 변화
PANTS_WIDTH_DELTA_MAX = 0.18  # 바지 폭(밴드 너비 대비 분율) 변화 — 와이드↔슬림
# 엣지맵 평균 차이(0-1) — 포켓·허리단·버튼 구조 변화. 실측 스케일(합성): 주름 노이즈≈0.002,
# 굵은 구조 추가≈0.05. 노이즈의 ~20배로 두어 gross 드리프트만 잡고 미세 텍스처는 흘린다.
# 절대 임계는 실사진 캘리브레이션 대상 — shadow 로 edgeDelta 를 모아 조정한다. 포켓 단위
# 미세 변화는 AG-P2 재판정(matching_critical_errors)이 별도로 잡으므로 여기선 보수적으로 둔다.
PANTS_EDGE_DELTA_MAX = 0.04


def _pants_band_mask(band: Image.Image, bg: tuple[int, int, int]) -> tuple[Image.Image, int]:
    """밴드에서 배경과 다른 전경 마스크('L', 0/255)와 전경 픽셀 수를 만든다."""
    diff = ImageChops.difference(band, Image.new("RGB", band.size, bg)).convert("L")
    mask = diff.point(lambda v: 255 if v > FG_THRESHOLD else 0)
    return mask, mask.histogram()[255]


def compare_pants_region(
    before_bytes: bytes, after_bytes: bytes, *,
    band_top: float = PANTS_BAND_TOP, band_bottom: float = PANTS_BAND_BOTTOM,
    color_thresh: int = PANTS_COLOR_DELTA_MAX, width_thresh: float = PANTS_WIDTH_DELTA_MAX,
    edge_thresh: float = PANTS_EDGE_DELTA_MAX,
) -> QcResult:
    """편집 전(before)·후(after) 바지 영역을 대조한다. → QcResult.

    verdict: 'pants_regressed'(색·폭·구조 중 하나라도 크게 변함) | 'pants_stable' |
    'pants_unknown'(디코드 실패 — **fail-open**, 호출측은 회귀 아님으로 취급).

    주름·미세 광택·작은 봉제선은 세 지표 어디에도 크게 걸리지 않아 통과한다(요구 3). 임계값은
    추정치라 shadow 로 메트릭을 모아 캘리브레이션한 뒤 enforce 한다(services/qc.py 관례).
    """
    try:
        before = Image.open(BytesIO(before_bytes)).convert("RGB")
        before.load()
        after = Image.open(BytesIO(after_bytes)).convert("RGB")
        after.load()
    except Exception:
        return QcResult("pants_unknown", ["decode_failed"])
    if after.size != before.size:
        after = after.resize(before.size, Image.LANCZOS)

    w, h = before.size
    top, bottom = int(h * band_top), int(h * band_bottom)
    if bottom <= top or w < 4:
        return QcResult("pants_unknown", ["band_degenerate"])
    box = (0, top, w, bottom)
    band_b, band_a = before.crop(box), after.crop(box)
    band_w, band_h = band_b.size
    band_area = band_w * band_h
    bg = _bg_color(before)

    mask_b, count_b = _pants_band_mask(band_b, bg)
    mask_a, count_a = _pants_band_mask(band_a, bg)

    # 색: 전경 평균색 채널 이동. 전경이 너무 적으면(짧은 하의·거의 배경) 판정 불가로 0.
    color_delta = 0.0
    if count_b >= band_area * PANTS_FG_MIN_RATIO and count_a >= band_area * PANTS_FG_MIN_RATIO:
        mean_b = ImageStat.Stat(band_b, mask_b).mean
        mean_a = ImageStat.Stat(band_a, mask_a).mean
        color_delta = max(abs(mean_b[i] - mean_a[i]) for i in range(3))

    # 폭: 전경 bbox 너비(밴드 너비 대비). 와이드↔슬림 실루엣 변화를 잡는다. getbbox 는 최외곽
    # 단일 픽셀에 좌우되므로, 재렌더 아티팩트 한 점이 폭을 부풀려 멀쩡한 편집을 되돌릴 수 있다.
    # evaluate_mannequin_qc 와 동일하게 MinFilter(3)로 stray 단일 픽셀을 침식하고 bbox 를 잰다.
    def _width_frac(mask):
        bbox = mask.filter(ImageFilter.MinFilter(3)).getbbox()
        return (bbox[2] - bbox[0]) / band_w if bbox else 0.0
    width_delta = abs(_width_frac(mask_b) - _width_frac(mask_a))

    # 구조: 엣지맵 평균 차이. 포켓·허리단·버튼·플라이 생성/소실을 잡는다. FIND_EDGES 의
    # 테두리 아티팩트는 1px 크롭으로 뺀다.
    def _edges(band):
        e = band.convert("L").filter(ImageFilter.FIND_EDGES)
        return e.crop((1, 1, band_w - 1, band_h - 1)) if band_w > 2 and band_h > 2 else e
    edge_delta = ImageStat.Stat(ImageChops.difference(_edges(band_b), _edges(band_a))).mean[0] / 255.0

    reasons = []
    if color_delta > color_thresh:
        reasons.append("matching_bottom_colour_shift")
    if width_delta > width_thresh:
        reasons.append("matching_bottom_width_shift")
    if edge_delta > edge_thresh:
        reasons.append("matching_bottom_structure_shift")
    metrics = {
        "colorDelta": round(color_delta, 2),
        "widthDelta": round(width_delta, 4),
        "edgeDelta": round(edge_delta, 4),
        "fgRatioBefore": round(count_b / band_area, 4),
        "fgRatioAfter": round(count_a / band_area, 4),
    }
    return QcResult("pants_regressed" if reasons else "pants_stable", reasons, metrics)
