"""커스텀 매칭 의류 누끼본을 시드 카탈로그와 같은 정면 flat-lay 로 다시 렌더한다.

누끼(matching_cutout)는 **배경**만 지운다 — 매장 옷걸이에 걸린 채 찍힌 사진은 각도가
그대로 남는다. 시드 카탈로그(seed/matching_items.json)는 전부 정면 flat-lay 상품컷이라,
배경을 맞춰도 커스텀 아이템만 여전히 튄다. 이 단계는 누끼 성공본 한 장을 Gemini 로
재렌더해 **카드 썸네일만** 교체한다.

규율(스파이크 2026-08-14 에서 실물 검증된 구성 그대로):

- 커스텀 아이템당 생성 1회. 생성 입력으로 쓰이는 grid 는 누끼 합성본 그대로 둔다 —
  거긴 배경 오염이 이미 해결됐고, 원본 1~4장을 재렌더하면 비용이 그만큼 곱해진다.
- 무과금 잡 안에서 돈다. 실비는 `image_usage` 로만 드러난다(stage=`matching_flatlay`).
- 어떤 실패도 None 으로 흡수한다. 호출자는 누끼 썸네일(그 폴백이 셀러 원본)을 그대로 쓴다.
- 프롬프트는 스파이크 **전략 B(Identity Lock)** 원문이다(flatlay-spike-inputs/out/report.html
  판정표: 두 의류 × 두 모델 모두 PASS, "추천 설정"). 의류 명사·배경 RGB 말고는 바꾸지 않는다.
  전략 C(시드 이미지 첨부)는 금지 — 시드 실루엣·색이 셀러 옷을 덮어쓴다(리포트 FAIL).
- 모델 티어는 settings.matching_flatlay_tier, **기본 image_high(Gemini 3 Pro Image)** —
  리포트의 추천 조합. flash 는 비용·속도 노브로 남는다.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass

from .. import image_usage
from ..agents.gemini_image import InlineImage
from ..agents.model_routing import resolve_model
from . import matching_cutout

log = logging.getLogger("wearless.matching_flatlay")

#: 플랫레이 파생 asset 의 알고리즘 신원. 누끼 지문에 섞여 썸네일 신원을 분리한다.
#: v2: 스파이크 전략 B(Identity Lock) 원문 프롬프트 + Pro 티어 기본. v1 시절 프롬프트로
#: 구운 flat-lay 는 다른 산출물이므로 파생 신원을 갈라 캐시가 섞이지 않게 한다.
ALGORITHM_VERSION = "matching-flatlay-v2"
PRODUCER = "gemini-matching-flatlay"
#: 실비 귀속 라벨. 디스패처가 걸어 둔 잡 문맥 위에 stage 만 덮어쓴다.
STAGE = "matching_flatlay"

#: 스파이크 승자 구성 — 레퍼런스 이미지 없이 1K·1:1, 실측 ~10s.
IMAGE_SIZE = "1K"
ASPECT_RATIO = "1:1"
#: 실측의 6배. 리스(900s)보다 훨씬 짧아 느린 생성이 잡을 물고 늘어지지 못한다.
TIMEOUT_S = 60.0

#: 카드 썸네일 인코딩은 누끼와 같은 계약(512px JPEG q85)을 쓴다. 재노출은 의도적이다 —
#: 이 단계가 저장하는 것도 결국 같은 카드 이미지다.
encode_thumbnail = matching_cutout.encode_thumbnail

#: 스파이크 **전략 B(Identity Lock)** 승자 프롬프트 원문 — flatlay-spike-inputs/out/report.html
#: 판정표에서 두 의류 × 두 모델 모두 PASS, "정체성 보존도 최상급, 추천 설정".
#: 명사(pants/garment)만 갈아끼우고 그 외에는 한 바이트도 바꾸지 않는다. 시드 이미지를
#: 참조로 첨부하는 전략 C 는 **금지** — 시드 실루엣·색이 셀러 옷을 덮어쓴다(리포트 FAIL).
_PROMPT = (
    "Professional studio flat-lay product catalog photograph of the EXACT same garment "
    "shown in the reference image.\n\n"
    "CRITICAL IDENTITY LOCK - DO NOT ALTER THE GARMENT'S FEATURES:\n"
    "1. Garment Identity: Preserve the exact color shade, fabric wash pattern, "
    "fading/whiskering, distressing, and material texture.\n"
    "2. Construction Details: Maintain the exact pocket shape and placement, belt loops, "
    "waistband, button/rivet hardware, and topstitching color.\n"
    "3. Silhouette: Maintain the exact leg cut (wide leg / straight fit) and hem finish.\n\n"
    "TRANSFORMATION INSTRUCTIONS:\n"
    "- Lay the {short} completely flat and neatly spread out on a seamless uniform light "
    "grey studio background (RGB: {r}, {g}, {b}, #E8E8E6).\n"
    "- Pure direct top-down 90-degree overhead angle (bird's eye view), perfectly straight "
    "vertical orientation.\n"
    "- Remove any hanger hooks, clothes clips, wall/floor artifacts, or background debris.\n"
    "- Soft diffused studio lighting, sharp focus on all fabric details, clean product "
    "catalog image."
)

#: clothing_type → (명사, 두 번째 문장의 명사, 그에 맞는 동사). 상의는 중립형을 쓴다 —
#: 프롬프트 뒤쪽에 "top-down view" 가 있어 "this exact top" 은 시점 지시와 부딪힌다.
_NOUNS = {
    "bottom": ("pair of pants", "pants", "are"),
    "top": ("garment", "garment", "is"),
}
_NEUTRAL = _NOUNS["top"]


@dataclass(frozen=True)
class FlatlayThumbnail:
    """재렌더 성공분. `image` 는 이미 카드 계약(512px JPEG)으로 인코딩돼 있다.

    `raw` 는 인코딩 전 1K 생성 원본 — full 모드에서 생성 입력 grid 의 front 칸이 된다.
    카드용 512px 를 grid 에 넣으면 착장 생성이 디테일을 잃으므로 원본을 따로 든다.
    """
    image: bytes
    raw: bytes
    model: str
    latency_ms: int


def enabled(settings) -> bool:
    """플래그. 미설정·off 면 이 모듈은 아무것도 하지 않는다."""
    return getattr(settings, "matching_flatlay", "off") in ("on", "full")


def grid_enabled(settings) -> bool:
    """full 모드 — flat-lay 를 카드 말고 **생성 입력 grid 의 front 칸**에도 쓴다.

    접힌 채 찍힌 하의는 실루엣·기장·광택 정보가 없어 착장 생성이 옷을 지어낸다
    (2026-08-14 실측: 접힌 갈색 배럴팬츠 → 갈색 청바지, 펼친 front 로 교체하니 회복).
    이미 만든 flat-lay 1장을 재사용하므로 이미지 호출은 늘지 않는다.
    """
    return getattr(settings, "matching_flatlay", "off") == "full"


def build_prompt(clothing_type: str | None) -> str:
    """의류 명사만 끼운 스파이크 전략 B 프롬프트(그 외 문구는 원문 고정)."""
    noun, short, verb = _NOUNS.get((clothing_type or "").strip().lower(), _NEUTRAL)
    r, g, b = matching_cutout.MATCHING_CUTOUT_BG
    return _PROMPT.format(noun=noun, short=short, verb=verb, r=r, g=g, b=b)


def flatlay_fingerprint(source_hash: str) -> str:
    """누끼 소스 지문 + 플랫레이 알고리즘 버전.

    같은 소스에서 나온 누끼 썸네일과 신원이 겹치면 R2 키가 충돌해 서로를 덮어쓴다.
    """
    return hashlib.sha256(
        f"{source_hash}|{ALGORITHM_VERSION}".encode("utf-8")).hexdigest()


def derived_asset_id(*, matching_item_id: str, source_hash: str) -> str:
    """재렌더 썸네일의 결정론적 신원. 재실행이 같은 asset 행·같은 R2 키로 수렴한다."""
    return matching_cutout.derived_asset_id(
        role="flatlay", matching_item_id=matching_item_id,
        source_hash=flatlay_fingerprint(source_hash))


def metadata_for(*, source_hash: str | None, source_asset_id: str | None,
                 matching_item_id: str, model: str) -> dict:
    """재렌더 썸네일의 provenance.

    `purpose` 는 누끼 썸네일과 같은 `custom_match_cutout` 을 유지한다 — "내 옷 삭제" 가
    파생분을 회수하는 화이트리스트가 그 값이라, 여기서 새 라벨을 쓰면 셀러가 지워도
    R2 객체가 영구히 남는다. 어느 단계가 만든 컷인지는 producer·algorithmVersion 이 말한다.
    """
    meta = matching_cutout.metadata_for(
        source_hash=source_hash, source_asset_id=source_asset_id,
        matching_item_id=matching_item_id, purpose=matching_cutout.CUTOUT_PURPOSE)
    meta["producer"] = PRODUCER
    meta["algorithmVersion"] = ALGORITHM_VERSION
    meta["model"] = model
    return meta


async def render_thumbnail(gemini, *, settings, cutout_png: bytes,
                           clothing_type: str | None) -> FlatlayThumbnail | None:
    """누끼 합성본 → 정면 flat-lay 카드 썸네일. 실패는 전부 None 으로 흡수한다.

    호출자(무과금 워커)는 이 결과에 아무것도 걸지 않는다 — 키 미설정·GeminiError·
    타임아웃·빈 응답·깨진 바이트 어느 쪽이든 누끼 썸네일이 그대로 남아야 한다.
    """
    if gemini is None:  # GEMINI_API_KEY 미설정 → main 이 클라이언트를 아예 안 만든다
        log.info("matching_flatlay skipped: no gemini client")
        return None
    try:
        # 모델 해석도 try 안에 둔다 — 라우팅 설정이 비어 있으면 여기서 터지는데, 밖에
        # 두면 그 예외가 워커의 광의 except 로 올라가 성공한 누끼까지 폐기된다(리뷰 I1).
        model = resolve_model(
            settings, getattr(settings, "matching_flatlay_tier", "image_high"))
        # 실비 귀속: 디스패처가 건 잡 문맥(job/user)은 두고 stage 만 이 단계로 덮는다.
        with image_usage.job_scope(stage=STAGE):
            res = await gemini.generate_content_image(
                model, build_prompt(clothing_type),
                [InlineImage("image/png", cutout_png)], IMAGE_SIZE,
                aspect_ratio=ASPECT_RATIO, timeout=TIMEOUT_S)
        # 빈·깨진 바이트는 여기서 PIL 이 터뜨린다 — 저장 전에 걸러진다.
        image = await asyncio.to_thread(encode_thumbnail, res.image)
        return FlatlayThumbnail(image=image, raw=res.image, model=model,
                                latency_ms=getattr(res, "latency_ms", 0))
    except Exception:  # noqa: BLE001 - 재렌더 실패가 누끼 결과를 되돌리지 않는다
        log.warning("matching_flatlay render failed; keeping cutout thumbnail",
                    exc_info=True)
        return None
