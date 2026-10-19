"""fal SAM 3 후보 생성과 톤 마스크 판정 규칙.

종류별 구역·상품색 유사도·코디 보호·상위 후보 복귀 규칙은 오프라인 픽셀 테스트로 검증한다.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import math
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

from . import fal_api, fal_cutout
from .segmentation_types import SamUnavailable, WornGarmentResult

log = logging.getLogger(__name__)
MODEL_VERSION = fal_api.SAM_MODEL
ALGORITHM_VERSION = "editor-worn-garment-fal-sam3-v2"
# 종류별 단어 하나와 공통 단어 하나, 캐시 미스당 정확히 두 요청. 각 $0.005.
CATEGORY_PROMPTS = {"top": "top", "outer": "outerwear", "bottom": "pants", "dress": "dress"}
SKIRT_TERMS = ("skirt", "스커트", "치마")
COMMON_PROMPT = "clothing"
PROMPT_CALLS = 2
MAX_MASKS = 3


class SegmentationUnavailable(ValueError):
    """판정에 쓸 이미지를 해독하지 못했다."""



SELECTOR_VERSION = "basediff-rank-v3"


DEDUPE_IOU = 0.90


CATEGORY_ZONE = {"top": (0.05, 0.70), "outer": (0.05, 0.70),
                 "bottom": (0.35, 1.00), "dress": (0.05, 0.98)}


CATEGORY_FORBIDDEN = {"top": (0.78, 1.00), "outer": (0.78, 1.00),
                      "bottom": (0.00, 0.30), "dress": ()}


HEAD_BAND = (0.00, 0.11)


DIFF_ROI = {"top": (0.08, 0.62), "outer": (0.08, 0.62),
            "bottom": (0.38, 1.00), "dress": (0.08, 0.95)}


MATCHING_CORE = {"bottom": (0.60, 1.00), "top": (0.00, 0.35)}


MATCHING_SEPARABLE = {"top": ("bottom",), "outer": ("bottom",), "bottom": ("top",)}


MATCH_ZONE_MAX = 0.25


VETO_FALLBACK_RANKS = 3


FALLBACK_OUTSIDE_MAX = 0.25


PRODUCT_CHROMA_SIGMA = 6.0


PRODUCT_LIGHTNESS_SIGMA = 45.0


PRODUCT_ALPHA_MIN = 127


PRODUCT_WEIGHT = 2.0


MASK_PREFIX = "derived/editor-garment-mask"


def fill_holes(mask: np.ndarray) -> np.ndarray:
    """Close interior holes without swallowing background.

    Flood filling from (0, 0) alone is wrong: any background region not connected to that one
    corner counts as interior and inflates the mask. Padding by a 1px border that touches every
    edge and filling from there fixes it , this was a measured bug, not a hypothetical
    (one garment's mask went 0.675 -> 0.821 area fraction before the fix).
    """
    h, w = mask.shape
    padded = np.zeros((h + 2, w + 2), np.uint8)
    padded[1:-1, 1:-1] = mask.astype(np.uint8)
    ff = padded.copy()
    cv2.floodFill(ff, np.zeros((h + 4, w + 4), np.uint8), (0, 0), 1)
    holes = (ff[1:-1, 1:-1] == 0)
    return np.logical_or(mask.astype(bool), holes)


def candidate_stats(mask: np.ndarray) -> dict:
    h, w = mask.shape
    area = int(mask.sum())
    if area == 0:
        return {"areaFrac": 0.0}
    ys, xs = np.nonzero(mask)
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    border = int(mask[0].sum() + mask[-1].sum() + mask[:, 0].sum() + mask[:, -1].sum())
    n_lab, _ = cv2.connectedComponents(mask.astype(np.uint8))
    return {
        "areaFrac": round(area / (h * w), 4),
        "bbox": [x0, y0, x1 - x0 + 1, y1 - y0 + 1],
        "bboxFillFrac": round(area / max(1, (x1 - x0 + 1) * (y1 - y0 + 1)), 3),
        "borderTouchFrac": round(border / (2 * (h + w)), 4),
        "components": int(n_lab - 1),
        "aspectHW": round((y1 - y0 + 1) / max(1, (x1 - x0 + 1)), 3),
    }


def plausible(st: dict) -> tuple[bool, str]:
    """Conservative rejection of obviously-wrong candidates.

    Deliberately not tuned to garments: these thresholds were set from the spike and are meant
    to drop frame-fills and slivers, not to encode what a sweater looks like.
    """
    a = st.get("areaFrac", 0.0)
    if a < 0.02:
        return False, "too_small"
    if a > 0.75:
        return False, "near_full_frame"
    if st.get("borderTouchFrac", 0) > 0.55:
        return False, "border_dominated"
    aspect = st.get("aspectHW", 1)
    if aspect > 6 or aspect < 0.12:
        return False, "impossible_aspect"
    return True, ""


class NoGarmentCandidate(SegmentationUnavailable):
    """SAM produced nothing that could be the product garment. Recoverable: the cut is fine,
    only the Tone Editor is unavailable for it."""


@dataclass(frozen=True)
class WornGarmentMask:
    png: bytes
    width: int
    height: int
    area_frac: float
    candidates: int
    plausible_candidates: int
    selected_score: float
    evidence: float
    m2m: bool
    source_sha256: str
    #: Share of the served mask that sits in the matching garment's band, and which side that
    #: garment was on. Both None/0 when the cut wears the product alone. Reported so a drift
    #: toward the coordinating garment is visible in the job ledger instead of only on screen.
    match_share: float = 0.0
    matching_side: str | None = None
    #: Which rank the served mask came from (0 = SAM's top pick) and how many masks the veto
    #: refused on the way there. Both 0 on the ordinary path; non-zero is the ledger's record
    #: that the coordinating-garment guard actually had to work on this cut.
    selected_rank: int = 0
    vetoed_attempts: int = 0
    #: How much the served mask looked like the seller's own uploaded garment. 0.0 means the
    #: cutout was not available for this cut, not that the mask looked wrong.
    product_match: float = 0.0


def source_fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mask_key(source_sha256: str, model_version: str = MODEL_VERSION,
             algorithm_version: str = ALGORITHM_VERSION,
             product_key: str | None = None) -> str:
    """Deterministic object key; a hit means this exact mask already exists.

    Sits under its own prefix rather than beside canonical cutouts , same bucket, different
    question, and the layout should make a mix-up impossible to do by accident.

    The product reference is part of the identity. The mask job runs right after generation and
    the seller's cutout may still be preprocessing, so the same cut is legitimately asked twice:
    once without a reference and once with. Without this the first answer would be cached and
    the reference would never get a chance to change it.
    """
    ref = hashlib.sha256(product_key.encode("utf-8")).hexdigest()[:12] if product_key else "noref"
    return (f"{MASK_PREFIX}/{algorithm_version}/{model_version.replace('/', '_')}"
            f"/{ref}/{source_sha256}.png")


def category_of(clothing_type: str | None) -> str:
    ct = str(clothing_type or "").lower()
    return ct if ct in CATEGORY_ZONE else "top"


def matching_core_band(clothing_type: str | None, matching_side: str | None) -> tuple:
    """The band that belongs to the coordinating garment alone, or () when it cannot be told.

    Empty for every case v1 could not reason about , no matching garment, a dress, or a matching
    item on the product's own side (a mislabelled custom upload). Empty means "score exactly as
    v1 did", which is what keeps this change confined to cuts that actually wear two garments.
    """
    side = str(matching_side or "").strip().lower()
    if side not in MATCHING_SEPARABLE.get(category_of(clothing_type), ()):
        return ()
    return MATCHING_CORE[side]


def diff_roi(category: str, match_band: tuple) -> tuple:
    """Where to ask "what changed", with the matching garment's band taken out.

    Evidence is the heaviest scoring axis, so leaving the coordinating garment inside the ROI is
    what would let a candidate covering both garments outrank the product alone.
    """
    y0, y1 = DIFF_ROI[category]
    if not match_band:
        return (y0, y1)
    b0, b1 = match_band
    if b0 <= y0:                      # matching garment above the product (product = bottom)
        y0 = max(y0, b1)
    else:                             # matching garment below it (product = top/outer)
        y1 = min(y1, b0)
    return (y0, y1) if y1 > y0 else DIFF_ROI[category]


def diff_map(base_bgr: np.ndarray, gen_bgr: np.ndarray) -> np.ndarray:
    """Perceptual difference against the bare base mannequin, 0..1.

    Chroma is weighted above lightness so a global exposure wobble between two renders does not
    read as "something was added here".
    """
    b = cv2.GaussianBlur(base_bgr, (0, 0), 2.0)
    g = cv2.GaussianBlur(gen_bgr, (0, 0), 2.0)
    lb = cv2.cvtColor(b, cv2.COLOR_BGR2LAB).astype(np.float32)
    lg = cv2.cvtColor(g, cv2.COLOR_BGR2LAB).astype(np.float32)
    d = (0.5 * np.abs(lg[..., 0] - lb[..., 0])
         + np.abs(lg[..., 1] - lb[..., 1])
         + np.abs(lg[..., 2] - lb[..., 2]))
    hi = float(np.percentile(d, 99.0)) or 1.0
    return np.clip(d / hi, 0.0, 1.0)


def evidence_mask(d: np.ndarray, roi: tuple) -> np.ndarray:
    """High-confidence "clothing appeared here" region. Scoring input, never a boundary."""
    h, _w = d.shape
    m = np.zeros(d.shape, np.uint8)
    y0, y1 = int(h * roi[0]), int(h * roi[1])
    band = d[y0:y1]
    if band.size == 0:
        return m
    m[y0:y1] = (band >= max(0.25, float(np.percentile(band, 85)))).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    return cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((21, 21), np.uint8))


def figure_silhouette(gen_bgr: np.ndarray) -> np.ndarray:
    """The dressed figure, for measuring how far a candidate spills into the backdrop.

    Found by flooding inward from the frame border across smooth pixels rather than by a
    lightness threshold: a white garment on a white mannequin against a pale horizon has almost
    no lightness contrast, but it does have an unbroken outline.
    """
    lightness = cv2.GaussianBlur(cv2.cvtColor(gen_bgr, cv2.COLOR_BGR2LAB)[..., 0],
                                 (0, 0), 1.2).astype(np.float32)
    gx = cv2.Sobel(lightness, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(lightness, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    edge = cv2.dilate((mag >= max(2.0, float(np.percentile(mag, 92)))).astype(np.uint8),
                      np.ones((3, 3), np.uint8))
    h, w = edge.shape
    filled = edge.copy()
    pad = np.zeros((h + 2, w + 2), np.uint8)
    pad[1:-1, 1:-1] = edge
    for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if filled[y, x] == 0:
            cv2.floodFill(filled, pad.copy(), (x, y), 2)
    fg = cv2.morphologyEx((filled != 2).astype(np.uint8), cv2.MORPH_CLOSE,
                          np.ones((11, 11), np.uint8))
    cnts, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if cnts:                                        # one figure per cut
        fg = np.zeros_like(fg)
        cv2.drawContours(fg, [max(cnts, key=cv2.contourArea)], -1, 1, cv2.FILLED)
    return fg


def _lab_median(bgr: np.ndarray, keep: np.ndarray) -> np.ndarray | None:
    """Median Lab of the kept pixels. None when there is nothing to measure.

    Median, not mean: a candidate mask that caught a sliver of background or a logo print should
    still report the colour of the cloth that fills it.
    """
    keep = keep.astype(bool)
    if not keep.any():
        return None
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    return np.median(lab[keep].reshape(-1, 3).astype(np.float32), axis=0)


def product_signature(cutout_png: bytes) -> np.ndarray | None:
    """Colour fingerprint of the garment the seller actually uploaded. None when unusable.

    The input is the canonical cutout: the original photograph with an alpha channel, so the
    garment pixels are simply the opaque ones , no re-segmentation, no guessing at background.

    None is fail-open. A missing, empty or corrupt cutout must leave scoring exactly as it was
    rather than nudge it with a signature nobody can vouch for.
    """
    if not cutout_png:
        return None
    img = cv2.imdecode(np.frombuffer(cutout_png, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None or img.ndim != 3 or img.shape[2] not in (3, 4):
        return None
    if img.shape[2] == 4:
        keep = img[..., 3] > PRODUCT_ALPHA_MIN
        bgr = img[..., :3]
    else:                                   # no alpha: every pixel is garment
        keep, bgr = np.ones(img.shape[:2], bool), img
    return _lab_median(bgr, keep)


def product_affinity(gen_bgr: np.ndarray, mask: np.ndarray,
                     signature: np.ndarray | None) -> float:
    """0..1 , does this region look like the garment being sold.

    Distance between the two median colours, not histogram overlap. Overlap was tried first and
    is actively wrong here: a region with a WIDE colour spread covers more of a narrow
    signature than the true garment does, so washed denim outscored the shirt it was worn with
    (2026-08-18 real pixels: denim 0.358, shirt 0.042).

    Chroma and lightness are separate kernels because they disagree between a photograph and a
    render by very different amounts , see the sigma constants.
    """
    if signature is None:
        return 0.0
    here = _lab_median(gen_bgr, mask)
    if here is None:
        return 0.0
    chroma = float(np.hypot(here[1] - signature[1], here[2] - signature[2]))
    lightness = float(abs(here[0] - signature[0]))
    score = (np.exp(-(chroma ** 2) / (2 * PRODUCT_CHROMA_SIGMA ** 2))
             * np.exp(-(lightness ** 2) / (2 * PRODUCT_LIGHTNESS_SIGMA ** 2)))
    return round(float(score), 4)


def _band_frac(mask: np.ndarray, band: tuple) -> float:
    if not band:
        return 0.0
    h = mask.shape[0]
    return float(mask[int(h * band[0]):int(h * band[1])].sum() / max(1, mask.sum()))


def score_candidate(mask: np.ndarray, evidence: np.ndarray, figure: np.ndarray,
                    category: str, match_band: tuple = (),
                    product: tuple | None = None) -> dict:
    """Rank one already-generated candidate. Generic axes only , no per-sample rules.

    Weights express the ordering of the axes, not a fit to any image: carrying the evidence
    matters most, being made mostly of evidence next, and contamination subtracts.

    `match_band` empty (no coordinating garment, or a pair this geometry cannot separate) leaves
    `matchZone` at 0 and the score identical to v1's.

    `product` is `(generated_bgr, signature)` when the seller's own garment cutout is ready.
    Absent leaves `productMatch` at 0 and the score identical to v3's , the axis only ever adds
    what it can prove.
    """
    m = mask.astype(bool)
    ev_bool = evidence.astype(bool)
    inter = float((m & ev_bool).sum())
    parts = {
        "evidence": round(inter / max(1, ev_bool.sum()), 3),
        "density": round(inter / max(1, m.sum()), 3),
        "zone": round(_band_frac(m, CATEGORY_ZONE[category]), 3),
        "forbidden": round(_band_frac(m, CATEGORY_FORBIDDEN[category]), 3),
        "head": round(_band_frac(m, HEAD_BAND), 3),
        # Mass sitting where only the coordinating garment can be. Weighted like `forbidden`:
        # both answer "this cannot be the garment we are selling".
        "matchZone": round(_band_frac(m, match_band), 3),
        # Looks like the garment the seller uploaded. Scoring only , the cutout never prompts
        # SAM, never becomes a box, and is never intersected with the mask (2026-08-18).
        "productMatch": (product_affinity(product[0], m, product[1]) if product else 0.0),
        "outsideFigure": round(float((m & ~figure.astype(bool)).sum() / max(1, m.sum())), 3),
        "areaFrac": round(float(m.mean()), 4),
    }
    ok, why = plausible(candidate_stats(m.astype(np.uint8)))
    if not ok:
        return {**parts, "score": None, "rejected": why}
    parts["score"] = round(
        1.5 * parts["evidence"] + 1.0 * parts["density"] + 0.5 * parts["zone"]
        + PRODUCT_WEIGHT * parts["productMatch"]
        - 2.0 * parts["forbidden"] - 2.0 * parts["head"] - 2.0 * parts["matchZone"]
        - 1.5 * parts["outsideFigure"], 4)
    return parts


def dedupe(masks: list[np.ndarray], iou: float = DEDUPE_IOU) -> list[np.ndarray]:
    kept: list[np.ndarray] = []
    for m in sorted(masks, key=lambda x: -x.sum()):
        if all(_iou(m, k) < iou for k in kept):
            kept.append(m)
    return kept


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / union) if union else 0.0


def tidy(mask: np.ndarray) -> np.ndarray:
    """Minimal cleanup. SAM's boundary is the boundary; this only drops specks and pinholes."""
    m = fill_holes(mask).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m)
    if n > 1:
        biggest = max(range(1, n), key=lambda i: stats[i, cv2.CC_STAT_AREA])
        floor = stats[biggest, cv2.CC_STAT_AREA] * 0.02
        keep = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= floor]
        m = np.isin(lab, keep).astype(np.uint8)
    return m.astype(bool)


def outside_figure_frac(mask: np.ndarray, figure: np.ndarray) -> float:
    """Share of the mask that is not on the figure at all , i.e. plain background."""
    m = mask.astype(bool)
    total = float(m.sum())
    return float((m & ~figure.astype(bool)).sum() / total) if total else 1.0


def _settle(mask: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Cleanup + source sizing. `post_process_masks` returns the model's size, not the cut's."""
    m = tidy(mask)
    if m.shape != shape:
        h, w = shape
        m = cv2.resize(m.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)
    return m


def encode_mask_png(mask: np.ndarray) -> bytes:
    """8-bit single-channel PNG, 0 = untouched, 255 = garment. Lossless, never JPEG."""
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray((mask.astype(np.uint8) * 255), mode="L").save(buf, format="PNG",
                                                                  optimize=False)
    return buf.getvalue()


def produce(generated: bytes, base: bytes, *, candidates: list[np.ndarray],
            clothing_type: str | None,
            matching_side: str | None = None, product: bytes | None = None) -> WornGarmentMask:
    """Generated cut + bare base + candidates -> editor garment mask.

    `matching_side` is "top"/"bottom" when the cut also wears a coordinating garment on that side
    (product metadata, never inferred from the image). It only ever scores and vetoes; SAM is
    prompted identically with or without it.
    """
    gen = cv2.imdecode(np.frombuffer(generated, np.uint8), cv2.IMREAD_COLOR)
    if gen is None:
        raise SegmentationUnavailable("generated cut failed to decode")
    base_img = cv2.imdecode(np.frombuffer(base, np.uint8), cv2.IMREAD_COLOR)
    if base_img is None:
        raise SegmentationUnavailable("base mannequin failed to decode")
    h, w = gen.shape[:2]
    if base_img.shape[:2] != (h, w):
        base_img = cv2.resize(base_img, (w, h), interpolation=cv2.INTER_AREA)

    category = category_of(clothing_type)
    match_band = matching_core_band(clothing_type, matching_side)
    candidates = dedupe(candidates)

    d = diff_map(base_img, gen)
    evidence = evidence_mask(d, diff_roi(category, match_band))
    figure = figure_silhouette(gen)
    signature = product_signature(product) if product else None
    reference = (gen, signature) if signature is not None else None

    ranked = []
    for m in candidates:
        filled = fill_holes(m)
        parts = score_candidate(filled, evidence, figure, category, match_band, reference)
        if parts["score"] is not None:
            ranked.append((parts["score"], filled, parts))
    if not ranked:
        raise NoGarmentCandidate(
            f"no plausible garment candidate among {len(candidates)} (category={category})")
    ranked.sort(key=lambda r: -r[0])

    # 정제 없이 후보 자체를 정리한 뒤, 기존 상위 3개 거부와 배경 문턱을 적용한다.
    chosen, refused = None, []
    for rank, (score, candidate, parts) in enumerate(ranked[:VETO_FALLBACK_RANKS]):
        mask = _settle(candidate, (h, w))
        share = round(_band_frac(mask, match_band), 4) if match_band else 0.0
        if match_band and share > MATCH_ZONE_MAX:
            refused.append(share)
            continue
        # 1등 후보는 배경 문턱에서 뺀다. 운영 SAM2도 1등의 정제 마스크는 이 문턱을 거치지 않았다
        # (worn_garment.py FALLBACK_OUTSIDE_MAX 주석). figure는 추정치라, 흰 배경 위 연회색 티처럼
        # 배경과 비슷한 옷을 몸 밖으로 잘못 본다(2026-10-19 운영 재현). 문턱은 대체 후보에만 쓴다.
        if rank == 0 or outside_figure_frac(mask, figure) <= FALLBACK_OUTSIDE_MAX:
            chosen = (mask, share, score, parts, rank)
            break
    if chosen is None:
        raise NoGarmentCandidate(
            f"no fallback mask was both off the matching garment and garment-shaped: "
            f"matchZone refusals {[f'{s:.2f}' for s in refused]} vs {match_band}, "
            f"outsideFigure ceiling {FALLBACK_OUTSIDE_MAX} "
            f"(category={category}, matching={matching_side}, "
            f"ranks={min(len(ranked), VETO_FALLBACK_RANKS)}/{len(ranked)})")
    mask, match_share, score, parts, rank = chosen

    return WornGarmentMask(
        png=encode_mask_png(mask), width=w, height=h,
        area_frac=round(float(mask.mean()), 4), candidates=len(candidates),
        plausible_candidates=len(ranked),
        selected_score=float(score), evidence=float(parts["evidence"]),
        product_match=float(parts["productMatch"]),
        m2m=False, match_share=match_share, selected_rank=rank,
        vetoed_attempts=len(refused),
        matching_side=(str(matching_side).lower() if match_band else None),
        source_sha256=source_fingerprint(generated))


def prompts_for(clothing_type: str | None, sub_category: str | None) -> tuple[str, str]:
    category = category_of(clothing_type)
    prompt = CATEGORY_PROMPTS[category]
    if category == "bottom" and any(term in str(sub_category or "").lower() for term in SKIRT_TERMS):
        prompt = "skirt"
    return prompt, COMMON_PROMPT


async def generate_candidates(settings, generated: bytes, *, clothing_type: str | None,
                              sub_category: str | None = None) -> list[np.ndarray]:
    """종류 단어와 clothing으로 최대 6개 후보를 받는다. 차분과 상품은 채점에만 쓴다."""
    prepared = await asyncio.to_thread(fal_api.prepare_image, generated)
    # fal 입력은 2048px로 줄어들 수 있지만 판정과 출력은 원본 컷 크기를 유지한다.
    gen = cv2.imdecode(np.frombuffer(generated, np.uint8), cv2.IMREAD_COLOR)
    if gen is None:
        raise SegmentationUnavailable("generated cut failed to decode")
    source_rgb = cv2.cvtColor(gen, cv2.COLOR_BGR2RGB)
    candidates = []
    for prompt in prompts_for(clothing_type, sub_category):
        body = await fal_api.run_model(settings, MODEL_VERSION, {
            "image_url": prepared.url, "prompt": prompt, "return_multiple_masks": True,
            "max_masks": MAX_MASKS, "include_scores": True, "output_format": "png"})
        masks, scores = body.get("masks"), body.get("scores")
        if not isinstance(masks, list):
            raise SamUnavailable("fal SAM returned invalid masks")
        for index, item in enumerate(masks[:MAX_MASKS]):
            try:
                url = item["url"]
                score = scores[index] if isinstance(scores, list) else item["score"]
                if not isinstance(url, str) or not url or isinstance(score, bool):
                    raise ValueError
                if not math.isfinite(float(score)):
                    raise ValueError
            except (KeyError, IndexError, TypeError, ValueError):
                raise SamUnavailable("fal SAM returned invalid mask scores or URL") from None
            data = await fal_api.fetch_image(url)
            try:
                mask = await asyncio.to_thread(fal_cutout._mask_from_color, data, source_rgb)
            except (OSError, ValueError):
                raise SamUnavailable("fal SAM returned invalid mask image") from None
            # fal 점수는 응답 유효성만 확인한다. 선택은 기존 score_candidate 공식만 쓴다.
            candidates.append(mask)
    return candidates


@dataclass(frozen=True)
class FalWornGarmentResult(WornGarmentResult):
    # 실제로 읽어 채점에 사용한 레퍼런스만 기록한다. 읽기 실패는 다음 조회 때 업그레이드된다.
    product_key: str | None = None


def _result(data: bytes, source_hash: str, key: str, *, product_key: str | None,
            mask: WornGarmentMask | None = None) -> FalWornGarmentResult:
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
        area = float((np.asarray(image.convert("L")) > 127).mean())
    return FalWornGarmentResult(
        ready=True, mask_key=key, source_hash=source_hash, model_version=MODEL_VERSION,
        algorithm_version=ALGORITHM_VERSION, selector_version=SELECTOR_VERSION,
        checksum=source_fingerprint(data), width=width, height=height,
        area_frac=round(area, 4), byte_size=len(data), grid=0, m2m=False,
        product_key=product_key, cached=mask is None,
        matching_side=mask.matching_side if mask else None,
        match_share=mask.match_share if mask else None,
        selected_rank=mask.selected_rank if mask else None,
        vetoed_attempts=mask.vetoed_attempts if mask else None,
        product_match=mask.product_match if mask else None)


async def segment_worn_garment(settings, r2, *, source_key: str, base_key: str,
                               clothing_type: str | None, sub_category: str | None = None,
                               matching_side: str | None = None,
                               product_key: str | None = None) -> WornGarmentResult:
    """기존 SAM 결과 계약을 지키며 캐시 확인, fal 후보 생성, 판정, PNG 저장을 수행한다."""
    if not fal_api.configured(settings):
        raise SamUnavailable("fal is not configured (FAL_KEY)")
    try:
        generated = await asyncio.to_thread(r2.get_bytes, source_key)
        base = await asyncio.to_thread(r2.get_bytes, base_key)
    except Exception:
        return WornGarmentResult(ready=False, code="source_error")

    product = None
    if product_key:
        try:
            product = await asyncio.to_thread(r2.get_bytes, product_key)
            if await asyncio.to_thread(product_signature, product) is None:
                product = None
        except Exception:
            product = None
            log.warning("tone mask product reference unavailable")
    used_product_key = product_key if product else None
    source_hash = source_fingerprint(generated)
    key = mask_key(source_hash, product_key=used_product_key)
    try:
        if await asyncio.to_thread(r2.head, key):
            cached = await asyncio.to_thread(r2.get_bytes, key)
            return await asyncio.to_thread(_result, cached, source_hash, key,
                                           product_key=used_product_key)
    except Exception:
        return WornGarmentResult(ready=False, code="source_error")

    try:
        candidates = await generate_candidates(settings, generated, clothing_type=clothing_type,
                                               sub_category=sub_category)
        mask = await asyncio.to_thread(produce, generated, base, candidates=candidates,
            clothing_type=clothing_type, matching_side=matching_side, product=product)
    except SamUnavailable:
        raise
    except NoGarmentCandidate as exc:
        return WornGarmentResult(ready=False, code="no_garment_candidate", message=str(exc))
    except Exception:
        return WornGarmentResult(ready=False, code="segmentation_error")
    try:
        await asyncio.to_thread(r2.put_bytes, key, mask.png, "image/png")
    except Exception:
        return WornGarmentResult(ready=False, code="mask_store_failed")
    return _result(mask.png, source_hash, key, product_key=used_product_key, mask=mask)
