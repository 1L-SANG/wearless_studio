"""Shared segmentation results and recoverable errors, independent of the provider."""
from __future__ import annotations

from dataclasses import dataclass

VIEWS = ("Front", "Back")


class SamUnavailable(RuntimeError):
    """A recoverable segmentation/provider outage; callers keep original images usable."""


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
