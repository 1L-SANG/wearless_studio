"""Garment segmentation and transparent cutout production. Owned by the SAM2 service.

This is the extraction of the logic that until now lived only in `scripts/source_sam2_spike.py`
and its siblings. It lives here, outside `app/`, on purpose:

  * the main backend image has no torch and must never grow one (Dockerfile runs
    `uv sync --frozen --no-dev`, and torch/transformers sit in the optional `[embeddings]`
    extra) — so the code that needs torch must not be importable from a module the API
    imports at startup;
  * the dependency direction has to be `sam_service -> (nothing in app)` and
    `scripts/hybrid_* -> sam_service`, never `app -> scripts`.

torch and transformers are imported lazily, inside the functions that need them, so this
module can be imported — and most of it tested — in an environment that has neither.

Nothing here redraws pixels. The cutout is the ORIGINAL photograph with an alpha channel;
every RGB value in the kept region is the value the camera recorded.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

import cv2
import numpy as np

#: Pinned. The cutout cache key includes this, so changing it invalidates every stored cutout
#: rather than silently mixing two models' output.
MODEL_ID = "facebook/sam2.1-hiera-tiny"
MODEL_VERSION = f"{MODEL_ID}@grid8"

#: Version of everything AROUND the model: 8x8 point grid, IoU-0.80 dedupe, encode-once
#: embedding reuse, solidity-preferring candidate selection, primary-mask-distance debris
#: cleanup, feathered RGBA encoding.
#:
#: It is part of the cutout cache identity, so bumping it regenerates every canonical cutout
#: naturally instead of leaving stale objects that were produced by different rules. Model
#: version alone cannot express this — the model has not changed while the selection logic has,
#: twice.
#:
#: v2: encode-once + solidity selection + mask-distance pruning (was: per-prompt encoding,
#:     largest-area selection, bbox-distance pruning).
ALGORITHM_VERSION = "sam2-grid8-v2"

#: Point-grid prompting, unchanged from the validated spike: 8x8 = 64 prompts, enough to catch
#: sleeves and torso separately while staying practical on CPU.
GRID = 8
DEDUPE_IOU = 0.80
MIN_IOU_SCORE = 0.7

#: Longest edge used for INFERENCE. 0 = full resolution.
#:
#: OFF for now, deliberately. Segmenting at 1280 and upscaling the mask made the selector pick
#: a DIFFERENT candidate than full resolution did: the resulting cutout kept only 0.02% of the
#: source's pixels in its opaque region and its mean brightness moved 166 -> 187, i.e. it
#: isolated something other than the garment. Full resolution is the configuration that was
#: visually verified end to end, so that is what ships until the selector is re-validated at
#: reduced scale. The cost is real (~94s per view on MPS) and is why this must be a background
#: job rather than anything inline with a request.
SEGMENT_MAX_PX = 0

#: Longest edge of the stored cutout. It is evidence for an image model, not a print asset —
#: 2048 keeps stitch and rib legible while staying a few MB instead of twenty.
CUTOUT_MAX_PX = 2048

#: Feather width for the alpha edge. Two pixels — enough to kill the jaggies a hard binary mask
#: leaves, small enough that no background colour bleeds into the garment.
FEATHER_PX = 2


class SegmentationUnavailable(RuntimeError):
    """torch/transformers missing, or the model failed to load.

    Callers degrade: a garment with no cutout is a garment that keeps the RAW-only path.
    """


@dataclass(frozen=True)
class Cutout:
    """One produced cutout plus everything needed to cache and audit it."""

    view: str
    png: bytes
    width: int
    height: int
    source_sha256: str
    model_version: str
    area_frac: float

    def as_metadata(self) -> dict:
        return {"view": self.view, "sourceSha256": self.source_sha256,
                "modelVersion": self.model_version, "width": self.width,
                "height": self.height, "areaFrac": self.area_frac}


def source_fingerprint(data: bytes) -> str:
    """Cache identity of a source image. Content, not asset id — a replaced upload reusing an
    id must not serve the previous garment's cutout."""
    return hashlib.sha256(data).hexdigest()


def cache_key(source_sha256: str, view: str, model_version: str = MODEL_VERSION,
              algorithm_version: str = ALGORITHM_VERSION) -> str:
    return f"{model_version}|{algorithm_version}|{view}|{source_sha256}"


#: R2 prefix for produced cutouts. Sits under `derived/` rather than beside the seller's
#: uploads: these are machine output, and the layout should say so at a glance.
CUTOUT_PREFIX = "derived/canonical-cutout"


def cutout_key(source_sha256: str, view: str, model_version: str = MODEL_VERSION,
               algorithm_version: str = ALGORITHM_VERSION) -> str:
    """Deterministic object key. Same inputs -> same key -> the object is already there.

    Every element of the cache identity is in the path, so a changed source photograph, a
    different view, a new model or new selection rules each land on a different object and old
    ones are never silently reused.
    """
    return (f"{CUTOUT_PREFIX}/{algorithm_version}/{model_version.replace('/', '_')}"
            f"/{view}/{source_sha256}.png")


# ── mask geometry (no torch) ─────────────────────────────────────────────────

def _iou(a: np.ndarray, b: np.ndarray) -> float:
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    return float(inter / union) if union else 0.0


def dedupe(masks: list[np.ndarray]) -> list[np.ndarray]:
    kept: list[np.ndarray] = []
    for m in sorted(masks, key=lambda x: -x.sum()):
        if all(_iou(m, k) < DEDUPE_IOU for k in kept):
            kept.append(m)
    return kept


def fill_holes(mask: np.ndarray) -> np.ndarray:
    """Close interior holes without swallowing background.

    Flood filling from (0, 0) alone is wrong: any background region not connected to that one
    corner counts as interior and inflates the mask. Padding by a 1px border that touches every
    edge and filling from there fixes it — this was a measured bug, not a hypothetical
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


#: A secondary blob must be at least this fraction of the primary garment's area to be kept.
#:
#: Measured on the real grey-knit Front, whose mask had 570 components: the primary was 97.63%
#: of the mask and the LARGEST of the other 569 was 0.153% of it, with a median of 17 pixels.
#: A genuinely separate garment part — a tie, a belt, a hanging strap — is percent-scale
#: against the body it belongs to, not tenth-of-a-percent. 1% sits two orders of magnitude
#: above the observed debris and well below any real part, so it separates the two populations
#: without being tuned to either.
SECONDARY_MIN_AREA_FRAC = 0.01

#: A secondary component is "attached" when its MEDIAN pixel lies within this fraction of the
#: frame diagonal of the primary mask.
#:
#: Median, not minimum: on the real app-uploaded Front one backdrop patch grazed the garment at
#: 4px while its bulk sat ~197px away, so the closest pixel says nothing useful. Median, not a
#: pixel-count fraction: a fraction cutoff is sensitive to component shape and dropped an 80px
#: flap lying 30px below the hem.
#:
#: The threshold separates two measured populations rather than sitting on either:
#:   backdrop patches (real image, 1536x2048)   median 7.44% and 7.69% of the diagonal
#:   legitimate adjacent parts (flap, strap)    median <= 2.7%
#: 0.04 sits between them with roughly 2x margin on both sides.
MAX_MEDIAN_DIST_FRAC = 0.04


def prune_disconnected_debris(mask: np.ndarray) -> tuple[np.ndarray, dict]:
    """Drop speckle that is not plausibly part of the garment. Conservative by construction.

    Keeps the primary component always, plus any secondary component that is BOTH big enough
    relative to the primary AND genuinely adjacent to the primary MASK — measured pixel to
    pixel with a distance transform, not bounding box to bounding box.

    Bounding boxes were the previous rule and they are structurally wrong here: a garment fills
    most of its frame, so its box swallows the whole image and every backdrop scrap "touches"
    it. Distance to the actual mask has no such degenerate case.

    Adjacency is judged on the component's BULK, not its closest pixel. A backdrop patch can
    graze the garment at a single point (one measured at 4px) while lying 200px away on
    average; a real strap or tie runs alongside the garment for most of its length.

    What this deliberately does not do: it never erodes, never dilates, never fills, never
    touches the primary component's outline, and never reads RGB. A cuff, a hem or a neckline
    is part of the primary component and cannot be affected by a rule that only ever deletes
    whole *other* components.
    """
    if not mask.any():
        return mask, {"components": 0, "kept": 0, "removedFrac": 0.0}
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    if n <= 2:                                   # background + one component
        return mask, {"components": max(0, n - 1), "kept": max(0, n - 1), "removedFrac": 0.0}

    areas = stats[1:, cv2.CC_STAT_AREA]
    primary = int(np.argmax(areas)) + 1
    p_area = int(stats[primary, cv2.CC_STAT_AREA])

    h, w = mask.shape
    max_median = MAX_MEDIAN_DIST_FRAC * float((h * h + w * w) ** 0.5)
    # Distance from every pixel to the nearest PRIMARY pixel. One O(HW) pass serves every
    # component, so this costs the same whether there are 3 blobs or 600.
    distance = cv2.distanceTransform(1 - (labels == primary).astype(np.uint8), cv2.DIST_L2, 5)

    keep = [primary]
    for c in range(1, n):
        if c == primary:
            continue
        if int(stats[c, cv2.CC_STAT_AREA]) < SECONDARY_MIN_AREA_FRAC * p_area:
            continue
        if float(np.median(distance[labels == c])) > max_median:
            continue
        keep.append(c)

    cleaned = np.isin(labels, keep)
    total = int(mask.sum())
    return cleaned, {
        "components": int(n - 1), "kept": len(keep),
        "primaryFrac": round(p_area / total, 6),
        "removedFrac": round((total - int(cleaned.sum())) / total, 6),
    }


#: Minimum solidity — primary component area divided by its bounding-box area — for a
#: candidate to be *preferred*. Not a rejection: see the fallback in `select_garment_mask`.
#:
#: Measured across every deduped candidate of both real fixtures (22 candidates, two images):
#:   garment-like candidates        0.662 - 0.686
#:   contaminated / inverse masks   0.493 - 0.596
#: 0.63 is the midpoint of that gap. The signal is geometric rather than positional: swallowing
#: backdrop above a shoulder stretches the bounding box without filling it, so solidity drops,
#: whereas the garment itself fills its box at a consistent ratio. Nothing here encodes where
#: the contamination sits, which garment it is, or how big the frame is.
SOLIDITY_MIN = 0.63


def _primary_solidity(mask: np.ndarray) -> float:
    """Largest connected component's area over its own bounding box. 0 for an empty mask."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    if n < 2:
        return 0.0
    primary = int(np.argmax(stats[1:, cv2.CC_STAT_AREA])) + 1
    area = int(stats[primary, cv2.CC_STAT_AREA])
    box = int(stats[primary, cv2.CC_STAT_WIDTH]) * int(stats[primary, cv2.CC_STAT_HEIGHT])
    return area / box if box else 0.0


def select_garment_mask(masks: list[np.ndarray]) -> tuple[np.ndarray | None, dict]:
    """The garment is the largest plausible, hole-filled, centre-touching, SOLID candidate.

    A product photograph is framed around its product, so "largest plausible blob overlapping
    the centre" is a description of the framing rather than a guess about the garment. Anything
    that fails `plausible` never gets here.

    Largest alone is not enough. On the real app-uploaded fixture SAM offered both a clean
    garment mask and the same garment with the backdrop above one shoulder fused on through a
    bridge. The contaminated one was 13% larger *because* of the contamination, so pure area
    picked it, and no connected-component cleanup could undo it — the backdrop was inside the
    primary component.

    Solidity separates them: contamination stretches the bounding box without filling it. So
    solid candidates are PREFERRED, and area decides among them.

    The preference degrades rather than fails. If no candidate is solid — an open cardigan or a
    pair of trousers may genuinely fill its box poorly — every candidate stays in contention and
    the behaviour is exactly what it was before. This rule can only ever act when a solid
    alternative actually exists.
    """
    scored = []
    for m in dedupe(masks):
        filled = fill_holes(m)
        st = candidate_stats(filled)
        ok, why = plausible(st)
        if not ok:
            continue
        h, w = filled.shape
        if not filled[int(h * 0.45):int(h * 0.55), int(w * 0.45):int(w * 0.55)].any():
            continue                       # not the framed subject
        st["primarySolidity"] = round(_primary_solidity(filled), 4)
        scored.append((st["areaFrac"], filled, st))
    if not scored:
        return None, {"reason": "no_plausible_candidate", "candidates": len(masks)}

    solid = [s for s in scored if s[2]["primarySolidity"] >= SOLIDITY_MIN]
    pool, gate = (solid, "solid") if solid else (scored, "no_solid_candidate_fallback")
    pool.sort(key=lambda t: -t[0])
    _, mask, st = pool[0]
    # Debris removal happens HERE, before anything encodes or stores the mask: a cutout
    # persisted with backdrop speckle in it would cache the defect for every later generation.
    mask, prune = prune_disconnected_debris(mask)
    solidity = st.get("primarySolidity")
    st = candidate_stats(mask)
    return mask, {"reason": "selected", "candidates": len(masks), "gate": gate,
                  "considered": len(scored), "solidCandidates": len(solid),
                  "primarySolidity": solidity, **st, "prune": prune}


def to_cutout_png(bgr: np.ndarray, mask: np.ndarray, *,
                  max_px: int = CUTOUT_MAX_PX) -> bytes:
    """Original pixels + alpha. No redraw, no recolour, no resize.

    The RGB planes are copied through untouched — including under transparent pixels, so a
    viewer that ignores alpha still shows the true photograph rather than a black silhouette.
    """
    from PIL import Image

    alpha = (mask.astype(np.uint8) * 255)
    if FEATHER_PX > 0:
        k = 2 * FEATHER_PX + 1
        alpha = cv2.GaussianBlur(alpha, (k, k), 0)
        # Soften INWARD only. Letting blurred alpha survive outside the mask leaves a haze of
        # half-opaque background — on the grey knit that showed up as visible speckle from the
        # backdrop's texture, which is exactly the interference the cutout exists to remove.
        alpha[~mask] = 0
    rgba = np.dstack([cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), alpha])
    h, w = rgba.shape[:2]
    scale = min(1.0, max_px / max(h, w)) if max_px else 1.0
    if scale < 1.0:
        rgba = cv2.resize(rgba, (max(1, int(w * scale)), max(1, int(h * scale))),
                          interpolation=cv2.INTER_AREA)
    buf = io.BytesIO()
    # No `optimize=True`: on a 24-megapixel RGBA it costs minutes of CPU for a file that is
    # about to be handed to an image model, not served to a browser.
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG", compress_level=6)
    return buf.getvalue()


# ── SAM2 inference (needs torch) ─────────────────────────────────────────────

def _torch():
    try:
        import torch
    except ImportError as e:                    # pragma: no cover - environment dependent
        raise SegmentationUnavailable(
            "torch is not installed. The SAM2 service image provides it; the main backend "
            "deliberately does not."
        ) from e
    return torch


def _device() -> str:
    """Auto-detected, unless SAM_DEVICE pins it.

    The override exists for benchmarking: the deployment target is CPU-only Fargate, and a
    number measured on Apple MPS says nothing about it. It changes no default — unset behaves
    exactly as before.
    """
    import os
    forced = (os.getenv("SAM_DEVICE") or "").strip().lower()
    if forced in ("cpu", "mps", "cuda"):
        return forced
    torch = _torch()
    if torch.backends.mps.is_available():
        return "mps"
    return "cuda" if torch.cuda.is_available() else "cpu"


class Sam2Segmenter:
    """Holds the loaded model. Construct once per process — loading is the expensive part."""

    def __init__(self, model_id: str = MODEL_ID) -> None:
        torch = _torch()
        try:
            from transformers import Sam2Model, Sam2Processor
        except ImportError as e:                # pragma: no cover - environment dependent
            raise SegmentationUnavailable("transformers is not installed") from e
        self.model_id = model_id
        self.device = _device()
        try:
            self.processor = Sam2Processor.from_pretrained(model_id)
            self.model = Sam2Model.from_pretrained(model_id).to(self.device).eval()
        except Exception as e:                  # noqa: BLE001 - any load failure is the same
            raise SegmentationUnavailable(f"SAM2 model load failed: {e!r}") from e
        self._no_grad = torch.no_grad
        #: Vision-encoder invocations since construction. Exactly one per view is the whole
        #: point of the embedding reuse, so it is counted rather than assumed.
        self.encode_calls = 0

    def candidates(self, rgb: np.ndarray) -> list[np.ndarray]:
        """Uniform point-grid prompting. Automatic — no operator input anywhere.

        The image is encoded ONCE and its embedding is reused for every prompt batch.

        The previous version passed `images=[rgb] * len(chunk)` to the processor, so each batch
        handed the model 16 identical copies of the photograph and the Hiera encoder ran once
        per copy: 64 encodes of one image for a 64-prompt grid, of which 63 were redundant. The
        prompts were the only thing that varied.

        Split accordingly, using the public API:
          image-specific  — pixel_values, original_sizes, image embeddings  (computed once)
          prompt-specific — input_points, input_labels                      (per batch)

        Prompt coordinates still go through the processor so they are rescaled into the model's
        1024x1024 frame exactly as before; `original_sizes` is all that step needs.
        """
        h, w = rgb.shape[:2]
        xs = np.linspace(w / (GRID + 1), w - w / (GRID + 1), GRID)
        ys = np.linspace(h / (GRID + 1), h - h / (GRID + 1), GRID)
        points = [[[float(x), float(y)]] for y in ys for x in xs]

        masks: list[np.ndarray] = []
        with self._no_grad():
            image_inputs = self.processor(images=rgb, return_tensors="pt").to(self.device)
            original_sizes = image_inputs["original_sizes"]
            embeddings = self.model.get_image_embeddings(image_inputs["pixel_values"])
            self.encode_calls += 1

            for i in range(0, len(points), 16):
                chunk = points[i:i + 16]
                b = len(chunk)
                sizes = original_sizes.repeat(b, 1)
                inputs = self.processor(
                    input_points=[[p] for p in chunk],
                    input_labels=[[[1]] for _ in chunk],
                    original_sizes=sizes,
                    return_tensors="pt").to(self.device)
                # `expand` is a view, not a copy — the batch never materialises 16 embeddings.
                batched = [e.expand(b, *e.shape[1:]) for e in embeddings]
                out = self.model(image_embeddings=batched,
                                 input_points=inputs["input_points"],
                                 input_labels=inputs["input_labels"],
                                 multimask_output=True)
                post = self.processor.post_process_masks(out.pred_masks, sizes)
                for per_image, score in zip(post, out.iou_scores):
                    m = per_image[0] if per_image.ndim == 4 else per_image
                    m = m.cpu().numpy()
                    s = score.detach().cpu().numpy().reshape(-1)
                    for j in range(m.shape[0]):
                        if s[j] >= MIN_IOU_SCORE:
                            masks.append(m[j].astype(bool))
        return masks

    def cutout(self, image_bytes: bytes, *, view: str,
               segment_max_px: int = SEGMENT_MAX_PX) -> Cutout:
        """One source photograph -> one transparent cutout. Raises SegmentationUnavailable.

        Inference runs on a downscaled copy and the resulting mask is scaled back up; the
        cutout's RGB is always the full-resolution original. 64 prompts over a 4284x5712 frame
        took 94s on MPS, which is minutes per garment for no benefit — mask boundaries at this
        scale are smooth enough that the downscale costs nothing visible.
        """
        bgr = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
        if bgr is None:
            raise SegmentationUnavailable("source image failed to decode")
        h, w = bgr.shape[:2]
        scale = min(1.0, segment_max_px / max(h, w)) if segment_max_px else 1.0
        small = (cv2.resize(bgr, (max(1, int(w * scale)), max(1, int(h * scale))),
                            interpolation=cv2.INTER_AREA) if scale < 1.0 else bgr)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        mask, info = select_garment_mask(self.candidates(rgb))
        if mask is None:
            raise SegmentationUnavailable(f"no garment mask found ({info.get('reason')})")
        if mask.shape != (h, w):
            mask = cv2.resize(mask.astype(np.uint8), (w, h),
                              interpolation=cv2.INTER_NEAREST).astype(bool)
        png = to_cutout_png(bgr, mask)
        from PIL import Image
        with Image.open(io.BytesIO(png)) as im:     # the stored size, not the source's
            out_w, out_h = im.size
        return Cutout(view=view, png=png, width=int(out_w), height=int(out_h),
                      source_sha256=source_fingerprint(image_bytes),
                      model_version=MODEL_VERSION,
                      area_frac=float(info.get("areaFrac") or 0.0))
