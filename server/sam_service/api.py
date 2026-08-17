"""SAM2 garment segmentation service — HTTP surface.

Two endpoints and nothing else:

  GET  /health           unauthenticated liveness, plus whether the model is resident
  POST /segment-garment  authenticated; Front and/or Back R2 keys -> transparent cutouts

Views are independent by construction: one view failing never discards another that worked, so
a garment with a broken Back photo still yields a usable Front cutout.

Cutouts are returned BY REFERENCE: the service writes the PNG to a deterministic R2 key and
returns that key. It does not return image bytes, and it never touches the application
database — the main backend owns asset rows, this service owns R2 objects.

The key encodes source content hash + view + model version + algorithm version, so an existing
object means "already produced by exactly these rules". The service checks for it before
spending ~25s on inference, which is what makes job retries cheap instead of duplicative.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from sam_service import model as model_registry, worn_garment
from sam_service.config import SamSettings, load_settings
from sam_service.segmentation import (ALGORITHM_VERSION, MODEL_VERSION,
                                      SegmentationUnavailable, cache_key,
                                      cutout_key, source_fingerprint)
from sam_service.storage import R2Source, SourceRejected, SourceUnavailable

log = logging.getLogger("sam_service")
# uvicorn configures its own loggers and leaves root at WARNING, so an INFO line here would
# never reach the container log — and the container log is the only place the Fargate memory
# measurement can land.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log.setLevel(logging.INFO)

VIEWS = ("Front", "Back")

#: Wall clock a single view may take. Full-resolution inference measured ~94s on MPS and CPU
#: will be slower, so this is generous on purpose — it exists to stop a wedged request living
#: forever, not to enforce a latency target that has not been established.
VIEW_TIMEOUT_S = float(600)

#: At most ONE segmentation in flight per process. A single full-resolution view peaked at
#: ~15.7 GB locally, so two concurrent ones would double the task's memory ceiling and turn a
#: sizing question into an OOM kill. Views within a request are already sequential; this also
#: serialises across concurrent requests.
_INFERENCE_SLOT = asyncio.Semaphore(1)


class ViewRequest(BaseModel):
    key: str = Field(..., description="R2 object key of the source photograph")


class SegmentRequest(BaseModel):
    views: dict[str, ViewRequest] = Field(..., description="'Front' and/or 'Back'")


class WornGarmentRequest(BaseModel):
    """A GENERATED mannequin cut plus the bare base it was dressed from.

    Trusted R2 keys only, exactly as `/segment-garment` — the service resolves them with its own
    credentials and refuses anything outside this project's prefixes.
    """

    sourceKey: str = Field(..., description="R2 key of the generated mannequin cut")
    baseKey: str = Field(..., description="R2 key of the bare base mannequin it was dressed on")
    clothingType: str | None = Field(default=None, description="top|outer|bottom|dress")
    subCategory: str | None = Field(default=None)
    #: Side the coordinating (not-for-sale) garment is worn on in this cut: top|bottom, or absent
    #: when the cut wears the product alone. Product metadata — never inferred from the image.
    matchingSide: str | None = Field(default=None, description="top|bottom")


def get_settings() -> SamSettings:
    return load_settings()


async def require_internal_token(
    authorization: str | None = Header(default=None),
    settings: SamSettings = Depends(get_settings),
) -> None:
    """Bearer shared secret, compared in constant time.

    An unset secret is a 503, not an open door: a misconfigured deployment must fail closed
    rather than quietly serve segmentation to anyone who can reach the task.
    """
    if not settings.auth_configured:
        raise HTTPException(status_code=503, detail={
            "code": "auth_not_configured",
            "message": "SAM_INTERNAL_TOKEN is not set; refusing to serve unauthenticated."})
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"code": "missing_token"})
    presented = authorization.split(" ", 1)[1].strip()
    if not hmac.compare_digest(presented, settings.internal_token or ""):
        raise HTTPException(status_code=403, detail={"code": "invalid_token"})


def _source_reader(settings: SamSettings) -> R2Source:
    return R2Source(settings)


def _process_memory_kb() -> dict:
    """Current and peak RSS of this process, from /proc/self/status.

    Benchmark instrumentation only — it goes to the log, never to the response. The Fargate
    cluster has Container Insights disabled and enabling it would alter infrastructure the
    production API shares, so process-local numbers are how task memory gets measured at all.
    Returns empty off Linux (macOS has no /proc); callers must treat the keys as optional.
    """
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            wanted = {"VmRSS:": "vmRssKb", "VmHWM:": "vmHwmKb"}
            out = {}
            for line in fh:
                key = line.split(None, 1)[0]
                if key in wanted:
                    out[wanted[key]] = int(line.split()[1])
            return out
    except OSError:
        return {}


def create_app(*, source_factory=_source_reader) -> FastAPI:
    """`source_factory` is the one seam tests use, so they never touch boto3 or R2."""
    app = FastAPI(title="wearless SAM2 segmentation", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "modelLoaded": model_registry.is_loaded(),
                "modelVersion": MODEL_VERSION, "loadError": model_registry.load_failure()}

    @app.post("/segment-garment", dependencies=[Depends(require_internal_token)])
    async def segment_garment(req: SegmentRequest,
                              settings: SamSettings = Depends(get_settings)) -> dict:
        requested = {v: r for v, r in req.views.items() if v in VIEWS}
        if not requested:
            raise HTTPException(status_code=422, detail={
                "code": "no_views", "message": f"supply at least one of {list(VIEWS)}"})

        source = source_factory(settings)
        results: dict[str, dict] = {}
        for view in VIEWS:                       # deterministic order; Front first
            if view not in requested:
                continue
            results[view] = await _segment_one(view, requested[view].key, source, settings)

        ok = [v for v, r in results.items() if r["status"] == "ready"]
        status = "ready" if len(ok) == len(results) else ("partial" if ok else "failed")
        return {"status": status, "modelVersion": MODEL_VERSION, "views": results,
                "algorithmVersion": ALGORITHM_VERSION}

    @app.post("/segment-worn-garment", dependencies=[Depends(require_internal_token)])
    async def segment_worn_garment(req: WornGarmentRequest,
                                   settings: SamSettings = Depends(get_settings)) -> dict:
        """Editor garment mask for one generated mannequin cut.

        Deliberately NOT part of `/segment-garment`: that endpoint means "cut this product
        photograph out of its background" and this one means "find the sold garment on a dressed
        mannequin". Same model, different algorithm, different cache namespace, and overloading
        one route would make the two indistinguishable in logs, caches and failure reports.
        """
        return await _segment_worn_one(req, source_factory(settings), settings)

    return app


async def _segment_worn_one(req: WornGarmentRequest, source, settings: SamSettings) -> dict:
    """Never raises: a mask failure only costs the Tone Editor, never the mannequin cut."""
    t0 = time.monotonic()

    def fail(code: str, message: str) -> dict:
        log.warning("worn-garment failed: %s (%s)", code, message)
        return {"status": "failed", "code": code, "message": message,
                "latencyMs": int((time.monotonic() - t0) * 1000)}

    try:
        generated, _ = await asyncio.to_thread(source.fetch, req.sourceKey)
        base, _ = await asyncio.to_thread(source.fetch, req.baseKey)
    except SourceRejected as e:
        return fail("source_rejected", str(e))
    except SourceUnavailable as e:
        return fail("source_unavailable", str(e))
    except Exception as e:                       # noqa: BLE001
        return fail("source_error", f"{type(e).__name__}: {e}")

    source_hash = worn_garment.source_fingerprint(generated)
    out_key = worn_garment.mask_key(source_hash)
    existing = await asyncio.to_thread(source.head, out_key)
    if existing:
        log.info("worn-garment cache=hit key=%s", out_key)
        return {"status": "ready", "cached": True, "sourceHash": source_hash,
                "maskKey": out_key, "modelVersion": MODEL_VERSION,
                "algorithmVersion": worn_garment.ALGORITHM_VERSION,
                "selectorVersion": worn_garment.SELECTOR_VERSION,
                "grid": worn_garment.GRID, "m2m": worn_garment.M2M,
                "checksum": existing.get("checksum"), "bytes": existing.get("size"),
                "mime": "image/png", "latencyMs": int((time.monotonic() - t0) * 1000)}

    try:
        segmenter = await model_registry.get_segmenter(settings.model_id or None)
    except SegmentationUnavailable as e:
        return fail("model_unavailable", str(e))

    try:
        # Same single-inference slot as the canonical path. The two capabilities share one
        # process and one memory ceiling, so they must also share the one-at-a-time rule.
        async with _INFERENCE_SLOT:
            mask = await asyncio.wait_for(
                asyncio.to_thread(worn_garment.produce, segmenter, generated, base,
                                  clothing_type=req.clothingType,
                                  matching_side=req.matchingSide),
                timeout=VIEW_TIMEOUT_S)
    except TimeoutError:
        return fail("timeout", f"worn-garment segmentation exceeded {VIEW_TIMEOUT_S:.0f}s")
    except worn_garment.NoGarmentCandidate as e:
        return fail("no_garment_candidate", str(e))
    except SegmentationUnavailable as e:
        return fail("segmentation_failed", str(e))
    except Exception as e:                       # noqa: BLE001
        return fail("segmentation_error", f"{type(e).__name__}: {e}")

    try:
        await asyncio.to_thread(source.put, out_key, mask.png, "image/png")
    except Exception as e:                       # noqa: BLE001
        return fail("mask_store_failed", f"{type(e).__name__}: {e}")

    mem = _process_memory_kb()
    log.info("worn-garment latencyMs=%d areaFrac=%s candidates=%d vmRssKb=%s key=%s",
             int((time.monotonic() - t0) * 1000), mask.area_frac, mask.candidates,
             mem.get("vmRssKb"), out_key)
    return {"status": "ready", "cached": False, "sourceHash": mask.source_sha256,
            "maskKey": out_key, "modelVersion": MODEL_VERSION,
            "algorithmVersion": worn_garment.ALGORITHM_VERSION,
            "selectorVersion": worn_garment.SELECTOR_VERSION,
            "grid": worn_garment.GRID, "m2m": mask.m2m,
            "checksum": hashlib.sha256(mask.png).hexdigest(),
            "width": mask.width, "height": mask.height, "areaFrac": mask.area_frac,
            "candidates": mask.candidates, "plausibleCandidates": mask.plausible_candidates,
            "selectedScore": mask.selected_score, "evidence": mask.evidence,
            "matchingSide": mask.matching_side, "matchShare": mask.match_share,
            "mime": "image/png", "bytes": len(mask.png),
            "latencyMs": int((time.monotonic() - t0) * 1000)}


async def _segment_one(view: str, key: str, source, settings: SamSettings) -> dict:
    """Never raises. A view's failure is data, because the other view may still be good."""
    t0 = time.monotonic()

    def fail(code: str, message: str) -> dict:
        log.warning("sam2 view %s failed: %s (%s)", view, code, message)
        return {"status": "failed", "code": code, "message": message,
                "latencyMs": int((time.monotonic() - t0) * 1000)}

    try:
        data, _mime = await asyncio.to_thread(source.fetch, key)
    except SourceRejected as e:
        return fail("source_rejected", str(e))
    except SourceUnavailable as e:
        return fail("source_unavailable", str(e))
    except Exception as e:                       # noqa: BLE001
        return fail("source_error", f"{type(e).__name__}: {e}")

    # Cache check BEFORE inference. The key is derived from the source content, the view, the
    # model and the algorithm version, so a hit means this exact cutout already exists.
    source_hash = source_fingerprint(data)
    out_key = cutout_key(source_hash, view)
    existing = await asyncio.to_thread(source.head, out_key)
    if existing:
        log.info("sam2 view=%s cache=hit key=%s", view, out_key)
        return {"status": "ready", "cached": True, "sourceKey": key,
                "sourceHash": source_hash, "modelVersion": MODEL_VERSION,
                "algorithmVersion": ALGORITHM_VERSION, "cutoutKey": out_key,
                "checksum": existing.get("checksum"), "bytes": existing.get("size"),
                "mime": "image/png",
                "latencyMs": int((time.monotonic() - t0) * 1000)}

    try:
        segmenter = await model_registry.get_segmenter(settings.model_id or None)
    except SegmentationUnavailable as e:
        return fail("model_unavailable", str(e))

    try:
        async with _INFERENCE_SLOT:
            cut = await asyncio.wait_for(
                asyncio.to_thread(segmenter.cutout, data, view=view), timeout=VIEW_TIMEOUT_S)
    except TimeoutError:
        return fail("timeout", f"segmentation exceeded {VIEW_TIMEOUT_S:.0f}s")
    except SegmentationUnavailable as e:
        return fail("segmentation_failed", str(e))
    except Exception as e:                       # noqa: BLE001
        return fail("segmentation_error", f"{type(e).__name__}: {e}")

    try:
        await asyncio.to_thread(source.put, out_key, cut.png, "image/png")
    except Exception as e:                       # noqa: BLE001
        return fail("cutout_store_failed", f"{type(e).__name__}: {e}")

    mem = _process_memory_kb()
    log.info("sam2 view=%s latencyMs=%d areaFrac=%s vmRssKb=%s vmHwmKb=%s key=%s",
             view, int((time.monotonic() - t0) * 1000), cut.area_frac,
             mem.get("vmRssKb"), mem.get("vmHwmKb"), out_key)

    # The cutout itself is returned by REFERENCE. It used to come back as base64, which was
    # marked interim from the start: a 4MB PNG became ~5.6MB of JSON on every call, twice
    # encoded, for bytes both sides can already reach in R2.
    return {"status": "ready", "cached": False, "sourceKey": key,
            "sourceHash": cut.source_sha256, "modelVersion": cut.model_version,
            "algorithmVersion": ALGORITHM_VERSION, "cutoutKey": out_key,
            "checksum": hashlib.sha256(cut.png).hexdigest(),
            "cacheKey": cache_key(cut.source_sha256, view, cut.model_version),
            "width": cut.width, "height": cut.height, "areaFrac": cut.area_frac,
            "mime": "image/png", "bytes": len(cut.png),
            "latencyMs": int((time.monotonic() - t0) * 1000)}


app = create_app()
