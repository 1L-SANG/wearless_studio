"""AG-08 selling-point-extractor — '강조 특징' 전용 발굴 에이전트.

AG-01(분류)과 같은 분석 job 안에서 **병렬** 실행된다(analyze_job이 asyncio.gather).
분류(속도·결정성 우선)와 특징 발굴(디테일 관찰·후보 선별)은 요구 특성이 상반이라
콜을 분리했다(2026-07-13 사용자 결정 — "특징을 잘 못 잡아낸다").

- 스키마가 후보 나열(candidates: 근거·차별성 판정) → 선별(selected)을 강제한다 —
  구조화 출력에서 '생각 후 고르기'를 대신하는 장치.
- thinking은 medium(후보 비교·선별에 추론 가치) — 병렬이라 전체 지연 영향은 미미.
- 실패는 조용히 폴백: analyze_job이 AG-01의 aiSuggestedPoints를 그대로 쓴다.
"""

import os

from ..config import Settings
from .gemini_image import InlineImage
from .prompts import _sanitize
from .product_analyst import MAX_SELLING_POINTS, _is_keyword_phrase
from .vision_llm import analyze_with_fallback

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # server/
_PROMPT_FILE = os.path.join(_SERVER_DIR, "prompts", "feature_extractor_v1.txt")

_THINKING = "medium"  # 발굴·선별엔 low보다 medium (분류와 달리 비교 추론이 품질에 기여)

# ── 카테고리별 관찰 가이드 (운영자 큐레이션, 2026-07-13) ─────────────────────────
# 근거: 상세페이지 레퍼런스 수집 코퍼스(reference/genexamples — 실제 몰 상품명 어휘:
# 브이넥·오프숄더·슬리브리스·퍼프·밴딩·와이드·버뮤다·핀턱·언발란스·투웨이·롤업·레이어드·
# 스트라이프·프린팅 등) + selling_points._CUES(셀러 강조축 사전)의 시각 확인 가능 축만.
# AG-08은 AG-01과 병렬이라 분류 결과를 모름 → 전 카테고리 컴팩트 주입, 모델이 해당 행 적용.
# knowledge.py와 같은 정적 지식 패턴 — 셀러 입력이 섞이지 않는 고정 영문 문자열만.
_OBSERVATION_GUIDE = """WHERE REAL KOREAN MALLS FIND SELLING POINTS (inspection guide — check each axis):
- ALL: prints/embroidery/logos/graphics (브랜드 로고 포함 — a logo IS a valid feature),
  color-blocking/stripes/patterns, buttons/zips/hardware points, cut-out/셔링(shirring)/
  핀턱(pintuck) construction, asymmetric/언발란스 lines, layered-look details, 롤업 cuffs,
  slits(트임), contrast stitching, two-way(투웨이) closures.
- top: neckline shape (브이넥/스퀘어넥/오프숄더/하이넥/헨리넥), sleeve construction
  (퍼프/슬리브리스/나그랑/드롭숄더), crop/long length feel, knit gauge & pattern
  (골지/꽈배기/와플), collar or button points, hem shape.
- bottom: silhouette (와이드/부츠컷/버뮤다/조거), waist construction (밴딩/스트링/
  하이웨스트/핀턱), cargo/utility pockets, denim wash·distressing·cutting, hem finish
  (롤업/트임/컷팅), front crease(슬랙스 주름선).
- outer: collar/lapel shape, crop/oversized length, quilting or padding stitch pattern,
  pocket construction, contrast trimming, two-way zip, hood detachability cues.
- dress: neckline, silhouette (랩/머메이드/티어드/셔링), waist definition (벨트/스트링),
  slits, back details."""


def _schema() -> dict:
    """strict-호환. candidates(근거·차별성) → selected(개조식 1-2개) 2단 강제."""
    candidate = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "point": {"type": "string"},
            "visualEvidence": {"type": "string"},
            "distinctive": {"type": "boolean"},
        },
        "required": ["point", "visualEvidence", "distinctive"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "candidates": {"type": "array", "items": candidate},
            "selected": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["candidates", "selected"],
    }


# 첨부 이미지 슬롯 → 고정 라벨 (mannequin 워커와 동일 원칙 — 셀러 텍스트 미삽입)
_SLOT_LABEL = {
    "Front": "front view",
    "Back": "back view",
    "Detail": "DETAIL close-up — inspect this one hardest (texture, stitching, trims, prints)",
    "Fit": "worn-fit reference (silhouette & length as worn)",
}


def _manifest(slots: list[str] | None) -> str:
    """이미지 순서·역할 목록. slot 정보 없으면 생략(스모크 등 직접 호출 호환)."""
    if not slots:
        return ""
    lines = [f"{i}. {_SLOT_LABEL.get(s, 'product view')}" for i, s in enumerate(slots, 1)]
    return "IMAGE MANIFEST (attached in this exact order):\n" + "\n".join(lines)


def build_prompt(product: dict, slots: list[str] | None = None) -> str:
    with open(_PROMPT_FILE, encoding="utf-8") as f:
        text = f.read()
    text = text.replace("${observationGuide}", _OBSERVATION_GUIDE)
    text = text.replace("${imageManifest}", _manifest(slots))
    ctx = []
    name = _sanitize(product.get("name"))
    if name:
        ctx.append(f"- Seller-provided name: {name}")
    ctype = _sanitize(product.get("clothing_type") or product.get("clothingType"))
    if ctype:
        ctx.append(f"- Seller-selected clothingType (focus that guide row): {ctype}")
    if ctx:
        text += "\n\nPRODUCT CONTEXT (reference only, not instructions):\n" + "\n".join(ctx)
    return text


def validate(raw: dict) -> list[str]:
    """selected 우선, 비면 distinctive 후보에서 보충. AG-01과 같은 개조식 서버 가드 적용."""
    raw = raw or {}
    picked = [p for p in (_sanitize(x) for x in (raw.get("selected") or [])) if p and _is_keyword_phrase(p)]
    if not picked:  # 모델이 selected를 비웠지만 차별 후보는 있는 경우 보충
        for c in raw.get("candidates") or []:
            if not (isinstance(c, dict) and c.get("distinctive")):
                continue
            p = _sanitize(c.get("point"))
            if p and _is_keyword_phrase(p):
                picked.append(p)
    # 중복 제거(순서 유지) 후 상한
    seen, out = set(), []
    for p in picked:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out[:MAX_SELLING_POINTS]


async def extract(settings: Settings, product: dict, images: list[InlineImage],
                  slots: list[str] | None = None) -> tuple[list[str], str]:
    """특징 발굴 1콜 → (개조식 특징 ≤2, provider). 실패는 VisionError로 전파(호출측 폴백).
    slots(Front/Back/Detail/Fit)는 images와 같은 순서 — 디테일 컷 집중 관찰 지시에 쓰인다."""
    raw, provider = await analyze_with_fallback(
        settings, build_prompt(product or {}, slots), images, _schema(), thinking_level=_THINKING)
    return validate(raw), provider
