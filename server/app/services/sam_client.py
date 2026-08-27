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



#: sam2 온디맨드(2026-08-21): SamUnavailable 이 나는 순간 "지금 켜라"를 쏜다. 업로드 없이
#: SAM 이 필요해지는 모든 경로(보관함 재진입 등)를 한 곳에서 덮는다. 훅은 실패해도 예외를
#: 바꾸지 않는다 — 원래의 SamUnavailable 이 같은 메시지·같은 __cause__ 로 그대로 올라간다.
PREWARM_HOOK = None


def install_prewarm_hook(fn) -> None:
    """lifespan 이 SamAutoscaler.prewarm 을 건다. None 이면 해제."""
    global PREWARM_HOOK
    PREWARM_HOOK = fn


async def _fire_prewarm() -> None:
    hook = PREWARM_HOOK
    if hook is None:
        return
    try:
        await hook()
    except Exception:  # noqa: BLE001 - 훅 실패가 SamUnavailable 을 가리면 안 된다
        log.warning("sam prewarm hook failed", exc_info=True)


async def _raise_unavailable(msg: str, cause: BaseException | None = None):
    """prewarm 을 먼저 쏘고 SamUnavailable 을 올린다. `from cause` 로 원인 체인을 그대로 보존한다."""
    await _fire_prewarm()
    if cause is not None:
        raise SamUnavailable(msg) from cause
    raise SamUnavailable(msg)

#: sam2 콜드스타트(2026-08-27 실측): Service Connect 이름은 +146.7초에야 통하는데 uvicorn 은
#: +59.6초에 이미 듣고 있다. 그 87초를 회수하려고 전송 실패 때 task IP 로 한 번 더 간다.
#: lifespan 이 SamEndpointResolver 를 건다. 안 걸리면 아무것도 달라지지 않는다.
ENDPOINT_RESOLVER = None


def install_endpoint_resolver(resolver) -> None:
    """lifespan 이 `SamEndpointResolver` 를 건다. None 이면 해제(테스트·종료)."""
    global ENDPOINT_RESOLVER
    ENDPOINT_RESOLVER = resolver


async def _direct_base() -> str | None:
    resolver = ENDPOINT_RESOLVER
    if resolver is None:
        return None
    try:
        return await resolver.direct_url()
    except Exception:  # noqa: BLE001 - 최적화 경로가 잡을 죽이면 안 된다
        log.warning("sam endpoint resolver failed", exc_info=True)
        return None


def _forget_direct() -> None:
    resolver = ENDPOINT_RESOLVER
    if resolver is None:
        return
    try:
        resolver.invalidate()
    except Exception:  # noqa: BLE001
        log.warning("sam endpoint invalidate failed", exc_info=True)


async def _post(settings, path: str, payload: dict, label: str):
    """Service Connect 이름으로 먼저, **전송이 끊겼을 때만** task IP 로 한 번 더.

    재시도 대상을 전송 오류로 좁힌 이유:
      - 타임아웃은 sam2 가 받아서 오래 도는 중이라는 뜻이다. 다시 걸면 90초를 두 번 쓴다.
      - 4xx/5xx 는 이미 sam2 가 답한 것이다. 주소를 바꿔도 같은 답이 온다.
    콜드스타트의 지문만 정확히 `httpx.ReadError`/`ConnectError` 다 — 실측에서 gen0·gen1 이
    전부 `SAM request failed: ReadError` 로 죽었다.
    """
    timeout = float(getattr(settings, "sam_request_timeout_s", 90.0) or 90.0)
    headers = {"Authorization": f"Bearer {settings.sam_internal_token}"}
    base = settings.sam_service_url
    transport_error: BaseException | None = None
    for attempt in (0, 1):
        if attempt:
            direct = await _direct_base()
            if not direct or direct == base:
                break
            base = direct
            log.info("sam %sretrying via task ip %s", label, direct)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(f"{base}{path}", json=payload, headers=headers)
        except httpx.TimeoutException as e:
            await _raise_unavailable(f"SAM {label}request timed out after {timeout}s", e)
        except httpx.HTTPError as e:
            transport_error = e
            if attempt:
                _forget_direct()          # 캐시해 둔 IP 도 죽었다 — 다음엔 다시 찾는다
            continue
        if r.status_code != 200:
            await _raise_unavailable(f"SAM {label}responded {r.status_code}")
        return r
    await _raise_unavailable(
        f"SAM {label}request failed: {type(transport_error).__name__}", transport_error)


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


@dataclass(frozen=True)
class WornGarmentResult:
    """Editor garment mask for one generated mannequin cut. `ready` False = no usable mask."""

    ready: bool
    mask_key: str | None = None
    source_hash: str | None = None
    model_version: str | None = None
    algorithm_version: str | None = None
    selector_version: str | None = None
    checksum: str | None = None
    width: int | None = None
    height: int | None = None
    area_frac: float | None = None
    byte_size: int | None = None
    grid: int | None = None
    m2m: bool | None = None
    #: 서비스가 본 매칭 의류 쪽과, 내준 마스크가 그 밴드에 걸친 비율. 구버전 서비스는 둘 다
    #: 주지 않는다 — 그래서 API 쪽이 픽셀로 한 번 더 확인한다(editor_garment_mask).
    matching_side: str | None = None
    match_share: float | None = None
    cached: bool = False
    #: 내준 마스크가 몇 등 후보에서 나왔고, 그 전에 veto 가 몇 장을 물렸는지. 판단에는 쓰지
    #: 않는다 — 원장이 "무엇을 시도했는지"에 답하게 하는 계측값이다(2026-08-18).
    #: None 은 "모른다"다 — 캐시로 답한 응답은 어떤 순위였는지 알 수 없다. 0 으로 채우면
    #: 원장이 "1등을 거부 없이 골랐다"고 거짓 진술한다(2026-08-18).
    selected_rank: int | None = None
    vetoed_attempts: int | None = None
    #: 내준 마스크가 셀러가 올린 주상품 컷아웃과 얼마나 닮았나(0..1). 0.0 은 "닮지 않았다"가
    #: 아니라 "레퍼런스가 없었다"일 수 있다 — 둘을 가르려면 productKey 를 함께 본다.
    product_match: float | None = None
    code: str | None = None
    message: str | None = None

    @classmethod
    def from_payload(cls, body: dict) -> WornGarmentResult:
        return cls(
            ready=body.get("status") == "ready" and bool(body.get("maskKey")),
            mask_key=body.get("maskKey"), source_hash=body.get("sourceHash"),
            model_version=body.get("modelVersion"),
            algorithm_version=body.get("algorithmVersion"),
            selector_version=body.get("selectorVersion"), checksum=body.get("checksum"),
            width=body.get("width"), height=body.get("height"),
            area_frac=body.get("areaFrac"), byte_size=body.get("bytes"),
            grid=body.get("grid"), m2m=body.get("m2m"),
            matching_side=body.get("matchingSide"), match_share=body.get("matchShare"),
            cached=bool(body.get("cached")),
            selected_rank=(None if body.get("selectedRank") is None
                           else int(body["selectedRank"])),
            vetoed_attempts=(None if body.get("vetoedAttempts") is None
                             else int(body["vetoedAttempts"])),
            product_match=(None if body.get("productMatch") is None
                           else float(body["productMatch"])),
            code=body.get("code"), message=body.get("message"))


def configured(settings) -> bool:
    """Both a URL and a token, or this client stays out of the way entirely."""
    return bool(getattr(settings, "sam_service_url", None)
                and getattr(settings, "sam_internal_token", None))


async def segment_worn_garment(settings, *, source_key: str, base_key: str,
                               clothing_type: str | None,
                               sub_category: str | None = None,
                               matching_side: str | None = None,
                               product_key: str | None = None) -> WornGarmentResult:
    """Generated mannequin cut -> editor garment mask. Raises `SamUnavailable` on transport failure.

    A separate route from `segment_garment` on purpose: that one background-removes a product
    photograph, this one finds the sold garment on a dressed mannequin. Same service, different
    algorithm and cache namespace.

    `matching_side` ("top"/"bottom") tells the service the cut also wears a coordinating garment
    on that side, so the mask stays on the garment being sold. A service that predates the field
    ignores it — the caller verifies the returned mask itself, so deploy order cannot regress the
    guarantee.

    `product_key` is the R2 key of the background-removed cutout of the seller's own front
    photograph, when one is ready. It answers "what does the garment being sold look like",
    which geometry alone gets wrong whenever the product is low-contrast against the mannequin
    and the coordinating garment is not (2026-08-18). Scoring evidence only — never a prompt.
    """
    if not configured(settings):
        await _raise_unavailable("SAM service is not configured (SAM_SERVICE_URL / token)")

    payload = {"sourceKey": source_key, "baseKey": base_key,
               "clothingType": clothing_type, "subCategory": sub_category,
               "matchingSide": matching_side, "productKey": product_key}
    r = await _post(settings, "/segment-worn-garment", payload, "worn-garment ")
    try:
        body = r.json()
    except ValueError as e:
        await _raise_unavailable("SAM worn-garment returned a non-JSON body", e)

    result = WornGarmentResult.from_payload(body)
    log.info("sam worn-garment status=%s cached=%s area=%s",
             "ready" if result.ready else (result.code or "failed"),
             result.cached, result.area_frac)
    return result


async def segment_garment(settings, views: dict[str, str]) -> dict[str, SamViewResult]:
    """{view: r2_key} -> {view: SamViewResult}. Raises `SamUnavailable` on transport failure.

    A per-view failure inside a 200 response is NOT an exception: Front and Back are
    independent, and a broken Back photo must never discard a good Front cutout.
    """
    if not configured(settings):
        await _raise_unavailable("SAM service is not configured (SAM_SERVICE_URL / token)")
    wanted = {v: k for v, k in views.items() if v in VIEWS and k}
    if not wanted:
        return {}

    payload = {"views": {v: {"key": k} for v, k in wanted.items()}}
    r = await _post(settings, "/segment-garment", payload, "")
    try:
        body = r.json()
    except ValueError as e:
        await _raise_unavailable("SAM returned a non-JSON body", e)

    results = {v: SamViewResult.from_payload(v, d)
               for v, d in (body.get("views") or {}).items() if v in wanted}
    log.info("sam segment status=%s views=%s", body.get("status"),
             {v: ("ready" if x.ready else x.code) for v, x in results.items()})
    return results
