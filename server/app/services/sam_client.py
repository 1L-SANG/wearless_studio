"""HTTP client for the internal SAM2 segmentation service.

The boundary between the main backend and SAM is HTTP, deliberately. `sam_service/` carries
torch, transformers and the SAM2 weights; `app/` must never import it, or the production API
image grows ~700MB of machine-learning runtime it has no use for.

This client sends trusted R2 object keys — never image bytes, never URLs. The service resolves
keys against its own credentials and refuses anything outside the project's known prefixes, so
a compromised or buggy caller cannot turn it into a fetcher for arbitrary content.

Retries live in the job layer, not here. A single request already costs ~25s per view, so an
in-client retry storm would pile minutes of duplicated inference onto a service that is
deliberately limited to one inference at a time. `sam_preprocess` retries through the existing
JobDispatcher semantics instead, and the service's deterministic cutout keys make that cheap:
a retry after a partial success finds the finished views already in R2.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

VIEWS = ("Front", "Back")


class SamUnavailable(RuntimeError):
    """The service could not be reached, timed out, or answered with an error status.

    Always recoverable from the caller's side: SAM is augmentation infrastructure, so its
    absence degrades the garment to RAW-only rather than failing anything the seller sees.
    """


@dataclass(frozen=True)
class SamViewResult:
    """One view's outcome. `ready` False means this view has no usable cutout."""

    view: str
    ready: bool
    cutout_key: str | None = None
    source_hash: str | None = None
    model_version: str | None = None
    algorithm_version: str | None = None
    checksum: str | None = None
    width: int | None = None
    height: int | None = None
    area_frac: float | None = None
    byte_size: int | None = None
    cached: bool = False
    code: str | None = None
    message: str | None = None

    @classmethod
    def from_payload(cls, view: str, body: dict) -> SamViewResult:
        ready = body.get("status") == "ready" and bool(body.get("cutoutKey"))
        return cls(
            view=view, ready=ready, cutout_key=body.get("cutoutKey"),
            source_hash=body.get("sourceHash"), model_version=body.get("modelVersion"),
            algorithm_version=body.get("algorithmVersion"), checksum=body.get("checksum"),
            width=body.get("width"), height=body.get("height"),
            area_frac=body.get("areaFrac"), byte_size=body.get("bytes"),
            cached=bool(body.get("cached")), code=body.get("code"),
            message=body.get("message"),
        )


def configured(settings) -> bool:
    """Both a URL and a token, or this client stays out of the way entirely."""
    return bool(getattr(settings, "sam_service_url", None)
                and getattr(settings, "sam_internal_token", None))


async def segment_garment(settings, views: dict[str, str]) -> dict[str, SamViewResult]:
    """{view: r2_key} -> {view: SamViewResult}. Raises `SamUnavailable` on transport failure.

    A per-view failure inside a 200 response is NOT an exception: Front and Back are
    independent, and a broken Back photo must never discard a good Front cutout.
    """
    if not configured(settings):
        raise SamUnavailable("SAM service is not configured (SAM_SERVICE_URL / token)")
    wanted = {v: k for v, k in views.items() if v in VIEWS and k}
    if not wanted:
        return {}

    url = f"{settings.sam_service_url}/segment-garment"
    payload = {"views": {v: {"key": k} for v, k in wanted.items()}}
    timeout = float(getattr(settings, "sam_request_timeout_s", 90.0) or 90.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                url, json=payload,
                headers={"Authorization": f"Bearer {settings.sam_internal_token}"})
    except httpx.TimeoutException as e:
        raise SamUnavailable(f"SAM request timed out after {timeout}s") from e
    except httpx.HTTPError as e:
        raise SamUnavailable(f"SAM request failed: {type(e).__name__}") from e

    if r.status_code != 200:
        raise SamUnavailable(f"SAM responded {r.status_code}")
    try:
        body = r.json()
    except ValueError as e:
        raise SamUnavailable("SAM returned a non-JSON body") from e

    results = {v: SamViewResult.from_payload(v, d)
               for v, d in (body.get("views") or {}).items() if v in wanted}
    log.info("sam segment status=%s views=%s", body.get("status"),
             {v: ("ready" if x.ready else x.code) for v, x in results.items()})
    return results
