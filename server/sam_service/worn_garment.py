"""Worn-garment segmentation for the Tone Editor. A SEPARATE capability from canonical cutout.

Two things live in this service now, and they must not be confused:

  RAW product photograph  -> `segmentation.py`   -> canonical cutout   (sam2-grid8-v2)
  GENERATED mannequin cut -> this module         -> editor mask        (editor-worn-garment-sam2-v1)

`segmentation.py` is imported for the pieces that are genuinely shared (the loaded model, mask
geometry helpers) and is NEVER modified: the canonical path's grid, selector, versions and
cutouts have to stay byte-identical, so the only safe way to add a second capability is to keep
it entirely outside that file.

The architecture is the one the validation settled on, and the order matters:

    generated cut -> SAM2 produces MANY candidates on its own (no hints at all)
                  -> Base-Diff + product category SCORE the candidates that already exist
                  -> one M2M refinement pass
                  -> editor mask

Base-Diff never prompts SAM, never supplies a box, never intersects the result. Every earlier
attempt did one of those and every one failed: hinting the prompt destroyed the very candidate
we needed, and intersecting truncated low-contrast hems. Measured on the 16-case corpus, letting
SAM answer first and scoring afterwards took candidate recall to 16/16.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

import cv2
import numpy as np

from sam_service.segmentation import (MODEL_VERSION, SegmentationUnavailable,
                                      candidate_stats, fill_holes, plausible)

#: Version of everything around the model for the EDITOR mask: 16x16 grid, IoU-0.90 dedupe,
#: Base-Diff candidate scoring, single M2M refinement. Part of the cache identity, so bumping it
#: regenerates masks instead of leaving objects produced under different rules.
#:
#: Deliberately unrelated to `sam2-grid8-v2`. A canonical cutout and an editor mask answer
#: different questions about different images and must never share a cache slot.
#:
#: v2 (2026-08-17): the coordinating garment worn in the same cut is scored against and vetoed,
#: so an editor mask can only ever be the garment being sold. Cache identity moves with it — a
#: mask produced under v1 rules was never asked that question.
ALGORITHM_VERSION = "editor-worn-garment-sam2-v2"
#: Bumped alone when only the ranking changes — candidates would be identical, the choice not.
SELECTOR_VERSION = "basediff-rank-v2"

#: Validated candidate generation. 16x16 is 4x the canonical grid: this image is a dressed
#: mannequin rather than a product on white, and the extra prompts are what surfaced the
#: cardigan-without-inner-shirt and the spaghetti-strap candidates. One encode regardless.
GRID = 16
#: Looser than canonical (0.80). Two candidates that differ only at the neckline are two
#: different answers here, and collapsing them costs recall.
DEDUPE_IOU = 0.90
#: Same floor the canonical path uses for a prompt's own confidence.
MIN_IOU_SCORE = 0.7
#: One refinement pass. Measured: removes fragment noise (burgundy cardigan went from 263
#: components to 1) and leaves already-clean masks alone (IoU 0.99 vs the input).
M2M = True

#: Category -> plausible vertical extent, and the band the garment cannot occupy. Broad priors
#: from the product's own analysis metadata; never inferred from the image, never per-sample.
CATEGORY_ZONE = {"top": (0.05, 0.70), "outer": (0.05, 0.70),
                 "bottom": (0.35, 1.00), "dress": (0.05, 0.98)}
CATEGORY_FORBIDDEN = {"top": (0.78, 1.00), "outer": (0.78, 1.00),
                      "bottom": (0.00, 0.30), "dress": ()}
HEAD_BAND = (0.00, 0.11)

#: Base-Diff ROI per category — the band the "what changed" question is asked in.
DIFF_ROI = {"top": (0.08, 0.62), "outer": (0.08, 0.62),
            "bottom": (0.38, 1.00), "dress": (0.08, 0.95)}

# ── The matching garment (코디 의류) is worn in the same cut, and is NOT for sale ─────────
#
# A mannequin cut dresses the base in the product AND in a coordinating garment (mannequin_job
# `matching TOP/BOTTOM garment — also dress the mannequin in this`). Only the product is sold, so
# only the product may be recoloured: the seller adjusting the tone of a coordinating item would
# publish a colour their buyer cannot get.
#
# Base-Diff cannot tell the two apart on its own — both are "something that appeared since the
# bare base", so a candidate covering both garments collects MORE evidence than the product
# alone. The matching garment's side is therefore passed in from the product metadata, exactly
# like the category, and used the same way: to score candidates, never to prompt SAM.

#: Where ONLY the matching garment can be, keyed by which side that garment is worn on.
#: 0.60 is the band top this pipeline already validated for the coordinating bottom
#: (`app/services/qc.py` PANTS_BAND_TOP — below the hip, waist transition left out so a top's hem
#: does not land inside it). 0.35 mirrors it upwards: `CATEGORY_ZONE["bottom"]` starts at 0.35, so
#: a product bottom cannot reach above it.
MATCHING_CORE = {"bottom": (0.60, 1.00), "top": (0.00, 0.35)}

#: Product category -> matching sides this geometry can actually separate. A dress is absent on
#: purpose: it reaches the calf, so no band belongs to the matching garment alone and the v1
#: scoring stands unchanged.
MATCHING_SEPARABLE = {"top": ("bottom",), "outer": ("bottom",), "bottom": ("top",)}

#: A chosen mask with more than this share of its mass in the matching band is the matching
#: garment (or both garments at once), not the product. Refused rather than served: losing the
#: Tone Editor on that cut costs the seller nothing, recolouring the wrong garment does.
MATCH_ZONE_MAX = 0.25

MASK_PREFIX = "derived/editor-garment-mask"


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


def source_fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mask_key(source_sha256: str, model_version: str = MODEL_VERSION,
             algorithm_version: str = ALGORITHM_VERSION) -> str:
    """Deterministic object key; a hit means this exact mask already exists.

    Sits under its own prefix rather than beside canonical cutouts — same bucket, different
    question, and the layout should make a mix-up impossible to do by accident.
    """
    return (f"{MASK_PREFIX}/{algorithm_version}/{model_version.replace('/', '_')}"
            f"/{source_sha256}.png")


def category_of(clothing_type: str | None) -> str:
    ct = str(clothing_type or "").lower()
    return ct if ct in CATEGORY_ZONE else "top"


def matching_core_band(clothing_type: str | None, matching_side: str | None) -> tuple:
    """The band that belongs to the coordinating garment alone, or () when it cannot be told.

    Empty for every case v1 could not reason about — no matching garment, a dress, or a matching
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


# ── Base-Diff: candidate evidence ONLY ───────────────────────────────────────

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


def _band_frac(mask: np.ndarray, band: tuple) -> float:
    if not band:
        return 0.0
    h = mask.shape[0]
    return float(mask[int(h * band[0]):int(h * band[1])].sum() / max(1, mask.sum()))


def score_candidate(mask: np.ndarray, evidence: np.ndarray, figure: np.ndarray,
                    category: str, match_band: tuple = ()) -> dict:
    """Rank one already-generated candidate. Generic axes only — no per-sample rules.

    Weights express the ordering of the axes, not a fit to any image: carrying the evidence
    matters most, being made mostly of evidence next, and contamination subtracts.

    `match_band` empty (no coordinating garment, or a pair this geometry cannot separate) leaves
    `matchZone` at 0 and the score identical to v1's.
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
        "outsideFigure": round(float((m & ~figure.astype(bool)).sum() / max(1, m.sum())), 3),
        "areaFrac": round(float(m.mean()), 4),
    }
    ok, why = plausible(candidate_stats(m.astype(np.uint8)))
    if not ok:
        return {**parts, "score": None, "rejected": why}
    parts["score"] = round(
        1.5 * parts["evidence"] + 1.0 * parts["density"] + 0.5 * parts["zone"]
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


# ── SAM2: candidate generation and refinement ────────────────────────────────

def generate_candidates(segmenter, rgb: np.ndarray, grid: int = GRID) -> list[np.ndarray]:
    """Uniform point grid, one image encode, `multimask_output`. No hints of any kind.

    Same shape as the canonical path's prompting — the image is encoded once and every prompt
    batch reuses that embedding — but it lives here so the canonical grid stays at 8.
    """
    h, w = rgb.shape[:2]
    xs = np.linspace(w / (grid + 1), w - w / (grid + 1), grid)
    ys = np.linspace(h / (grid + 1), h - h / (grid + 1), grid)
    points = [[[float(x), float(y)]] for y in ys for x in xs]

    masks: list[np.ndarray] = []
    with segmenter._no_grad():
        image_inputs = segmenter.processor(images=rgb, return_tensors="pt").to(segmenter.device)
        original_sizes = image_inputs["original_sizes"]
        embeddings = segmenter.model.get_image_embeddings(image_inputs["pixel_values"])
        segmenter.encode_calls += 1
        for i in range(0, len(points), 16):
            chunk = points[i:i + 16]
            b = len(chunk)
            sizes = original_sizes.repeat(b, 1)
            inputs = segmenter.processor(
                input_points=[[p] for p in chunk], input_labels=[[[1]] for _ in chunk],
                original_sizes=sizes, return_tensors="pt").to(segmenter.device)
            # `expand` is a view: the batch never materialises 16 copies of the embedding.
            batched = [e.expand(b, *e.shape[1:]) for e in embeddings]
            out = segmenter.model(image_embeddings=batched,
                                  input_points=inputs["input_points"],
                                  input_labels=inputs["input_labels"],
                                  multimask_output=True)
            post = segmenter.processor.post_process_masks(out.pred_masks, sizes)
            for per_image, score in zip(post, out.iou_scores):
                m = per_image[0] if per_image.ndim == 4 else per_image
                m = m.cpu().numpy()
                s = score.detach().cpu().numpy().reshape(-1)
                for j in range(m.shape[0]):
                    if s[j] >= MIN_IOU_SCORE:
                        masks.append(m[j].astype(bool))
    return masks


def refine(segmenter, rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """One mask-to-mask pass: feed the chosen mask back as the prior, take the single output.

    Not another prompting mechanism — the only inputs are the candidate itself and its own
    centroid. Returns the input unchanged if the model cannot refine it, because a worse mask
    is worse than no refinement.
    """
    import torch

    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return mask
    cx, cy = float(xs.mean()), float(ys.mean())
    low = cv2.resize(mask.astype(np.float32), (256, 256), interpolation=cv2.INTER_AREA)
    # The decoder expects logits, not a 0/1 image; ±10 is a confident prior either way.
    low = torch.tensor((low * 20.0 - 10.0)[None, None], dtype=torch.float32)

    with segmenter._no_grad():
        image_inputs = segmenter.processor(images=rgb, return_tensors="pt").to(segmenter.device)
        sizes = image_inputs["original_sizes"]
        embeddings = segmenter.model.get_image_embeddings(image_inputs["pixel_values"])
        segmenter.encode_calls += 1
        inputs = segmenter.processor(input_points=[[[[cx, cy]]]], input_labels=[[[1]]],
                                     original_sizes=sizes, return_tensors="pt").to(segmenter.device)
        out = segmenter.model(image_embeddings=embeddings,
                              input_points=inputs["input_points"],
                              input_labels=inputs["input_labels"],
                              input_masks=low.to(segmenter.device),
                              multimask_output=False)
        post = segmenter.processor.post_process_masks(out.pred_masks, sizes)
        r = post[0]
        r = r[0] if r.ndim == 4 else r
        refined = r.cpu().numpy()[0].astype(bool)
    return refined if refined.any() else mask


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


def encode_mask_png(mask: np.ndarray) -> bytes:
    """8-bit single-channel PNG, 0 = untouched, 255 = garment. Lossless, never JPEG."""
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray((mask.astype(np.uint8) * 255), mode="L").save(buf, format="PNG",
                                                                  optimize=False)
    return buf.getvalue()


def produce(segmenter, generated: bytes, base: bytes, *, clothing_type: str | None,
            matching_side: str | None = None,
            grid: int = GRID, use_m2m: bool = M2M) -> WornGarmentMask:
    """Generated cut + bare base -> editor garment mask. Raises `SegmentationUnavailable`.

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
    rgb = cv2.cvtColor(gen, cv2.COLOR_BGR2RGB)
    raw = generate_candidates(segmenter, rgb, grid)
    candidates = dedupe(raw)

    d = diff_map(base_img, gen)
    evidence = evidence_mask(d, diff_roi(category, match_band))
    figure = figure_silhouette(gen)

    best, best_score, best_parts = None, None, None
    for m in candidates:
        filled = fill_holes(m)
        parts = score_candidate(filled, evidence, figure, category, match_band)
        if parts["score"] is None:
            continue
        if best_score is None or parts["score"] > best_score:
            best, best_score, best_parts = filled, parts["score"], parts
    if best is None:
        raise NoGarmentCandidate(
            f"no plausible garment candidate among {len(candidates)} (category={category})")

    mask = refine(segmenter, rgb, best) if use_m2m else best
    mask = tidy(mask)
    if mask.shape != (h, w):                        # post_process_masks returns source size
        mask = cv2.resize(mask.astype(np.uint8), (w, h),
                          interpolation=cv2.INTER_NEAREST).astype(bool)

    # Veto AFTER refinement: M2M can grow the mask, and it is the mask we would actually serve
    # that has to be the product. Scoring alone is a preference; this is the guarantee.
    match_share = round(_band_frac(mask, match_band), 4) if match_band else 0.0
    if match_band and match_share > MATCH_ZONE_MAX:
        raise NoGarmentCandidate(
            f"selected mask sits on the matching garment: {match_share:.2f} of its mass in "
            f"{match_band} (category={category}, matching={matching_side})")

    return WornGarmentMask(
        png=encode_mask_png(mask), width=w, height=h,
        area_frac=round(float(mask.mean()), 4), candidates=len(candidates),
        plausible_candidates=sum(
            1 for m in candidates
            if score_candidate(fill_holes(m), evidence, figure, category,
                               match_band)["score"] is not None),
        selected_score=float(best_score), evidence=float(best_parts["evidence"]),
        m2m=bool(use_m2m), match_share=match_share,
        matching_side=(str(matching_side).lower() if match_band else None),
        source_sha256=source_fingerprint(generated))
