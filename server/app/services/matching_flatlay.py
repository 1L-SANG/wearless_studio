"""커스텀 매칭 의류 원본을 정면 flat-lay로 한 번에 정리한다.

누끼(matching_cutout)는 **배경**만 지운다 — 매장 옷걸이에 걸린 채 찍힌 사진은 각도가
그대로 남는다. 시드 카탈로그(seed/matching_items.json)는 전부 정면 flat-lay 상품컷이라,
배경을 맞춰도 커스텀 아이템만 여전히 튄다. 원본 첫 사진을 Gemini에 직접 넣어
배경과 옷걸이를 제거하고 펼친 사진을 만든다. full이면 같은 결과를 생성 입력에도 쓴다.

규율(8/14의 프롬프트는 유지하고, 2026-10-08에 원본 직접 입력으로 변경):

- 커스텀 아이템당 생성 1회. 나머지 사진은 원본으로 유지한다.
- 무과금 잡 안에서 돈다. 실비는 `image_usage` 로만 드러난다(stage=`matching_flatlay`).
- 일반 실패는 None이며 원본을 유지한다. 서버 종료 취소는 다시 전파한다.
- 프롬프트는 스파이크 **전략 B(Identity Lock)** 원문이다(flatlay-spike-inputs/out/report.html
  판정표: 두 의류 × 두 모델 모두 PASS, "추천 설정"). 의류 명사·배경 RGB 말고는 바꾸지 않는다.
  전략 C(시드 이미지 첨부)는 금지 — 시드 실루엣·색이 셀러 옷을 덮어쓴다(리포트 FAIL).
- 기본 image_high(Gemini 3 Pro Image)는 디테일을 우선한 8/15 오너 선택이다.
  당시 Pro와 Flash 모두 보존 검사에 통과했고 기록은 Flash의 속도와 비용을 추천했다.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import time
import logging
from dataclasses import dataclass
from PIL import Image, ImageOps

from .. import image_usage
from ..agents.gemini_image import InlineImage
from ..agents.model_routing import resolve_model
from . import matching_cutout

log = logging.getLogger("wearless.matching_flatlay")

#: 플랫레이 파생 asset 의 알고리즘 신원. 누끼 지문에 섞여 썸네일 신원을 분리한다.
#: v2: 스파이크 전략 B(Identity Lock) 원문 프롬프트 + Pro 티어 기본. v1 시절 프롬프트로
#: 구운 flat-lay 는 다른 산출물이므로 파생 신원을 갈라 캐시가 섞이지 않게 한다.
#: v3: SAM 누끼 대신 원본 사진을 직접 입력한다. 이전 결과와 신원을 분리한다.
ALGORITHM_VERSION = "matching-flatlay-v3-original"
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
    "3. Silhouette: {silhouette}\n\n"
    "TRANSFORMATION INSTRUCTIONS:\n"
    "- Lay the {short} completely flat and neatly spread out on a seamless uniform light "
    "grey studio background (RGB: {r}, {g}, {b}, #E8E8E6).\n"
    "- Pure direct top-down 90-degree overhead angle (bird's eye view), perfectly straight "
    "vertical orientation.\n"
    "- Remove any hanger hooks, clothes clips, wall/floor artifacts, or background debris.\n"
    "- Soft diffused studio lighting, sharp focus on all fabric details, clean product "
    "catalog image."
)

#: clothing_type → (지시문 명사, 실루엣 항목 문장).
#: 하의는 스파이크 원문 그대로. 상의는 그 문장의 "leg cut" 이 의미가 없으므로 같은 층위의
#: 중립 문장으로 바꾼다 — 안 그러면 상의 재렌더에 다리 지시가 그대로 흘러간다(2026-08-15
#: 전수조사). 상의 표본은 스파이크에 없으므로 이 문장은 검증 대상이다.
_BOTTOM_SILHOUETTE = "Maintain the exact leg cut (wide leg / straight fit) and hem finish."
_TOP_SILHOUETTE = ("Maintain the exact cut, length, sleeve shape, neckline and hem finish.")
_NOUNS = {
    "bottom": ("pants", _BOTTOM_SILHOUETTE),
    "top": ("garment", _TOP_SILHOUETTE),
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
    """의류 명사·실루엣 항목만 끼운 스파이크 전략 B 프롬프트(그 외 문구는 원문 고정).

    하의는 리포트 원문과 바이트 동일하다(테스트로 고정).
    """
    short, silhouette = _NOUNS.get((clothing_type or "").strip().lower(), _NEUTRAL)
    r, g, b = matching_cutout.MATCHING_CUTOUT_BG
    return _PROMPT.format(short=short, silhouette=silhouette, r=r, g=g, b=b)


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


async def render_thumbnail(gemini, *, settings, source_image: bytes,
                           clothing_type: str | None) -> FlatlayThumbnail | None:
    """첫 원본 사진으로 배경 정리와 정면 사진 생성을 함께 한다. 실패는 None이다.

    호출자(무과금 워커)는 이 결과에 아무것도 걸지 않는다 — 키 미설정·GeminiError·
    타임아웃·빈 응답·깨진 바이트 어느 쪽이든 셀러 원본을 유지한다.
    """
    if gemini is None:  # GEMINI_API_KEY 미설정 → main 이 클라이언트를 아예 안 만든다
        log.info("matching_flatlay skipped: no gemini client")
        return None
    try:
        # JPEG·WebP·EXIF 회전도 원본의 옷을 그대로 PNG로 전달한다. 생성은 한 번뿐이다.
        with Image.open(io.BytesIO(source_image)) as source:
            normalized = ImageOps.exif_transpose(source).convert("RGB")
            png = io.BytesIO()
            normalized.save(png, "PNG")
        # 모델 해석도 try 안에 둔다 — 라우팅 설정이 비어 있으면 여기서 터지는데, 밖에
        # 두면 그 예외가 워커의 광의 except 로 올라가 성공한 누끼까지 폐기된다(리뷰 I1).
        model = resolve_model(
            settings, getattr(settings, "matching_flatlay_tier", "image_high"))
        # 실비 귀속: 디스패처가 건 잡 문맥(job/user)은 두고 stage 만 이 단계로 덮는다.
        # 200 응답 뒤 디코드 중 취소될 수 있으므로 이미 기록한 호출은 다시 적지 않는다.
        with image_usage.job_scope(stage=STAGE), image_usage.observe_recording() as recording:
            started = time.perf_counter()
            try:
                res = await asyncio.wait_for(gemini.generate_content_image(
                    model, build_prompt(clothing_type),
                    [InlineImage("image/png", png.getvalue())], IMAGE_SIZE,
                    aspect_ratio=ASPECT_RATIO, timeout=TIMEOUT_S), timeout=TIMEOUT_S)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                if not recording.recorded:
                    image_usage.record(model=model, image_size=IMAGE_SIZE, usage=None,
                                       latency_ms=int((time.perf_counter() - started) * 1000),
                                       has_image=False)
                raise
        # 빈·깨진 바이트는 여기서 PIL 이 터뜨린다 — 저장 전에 걸러진다.
        image = await asyncio.to_thread(encode_thumbnail, res.image)
        return FlatlayThumbnail(image=image, raw=res.image, model=model,
                                latency_ms=getattr(res, "latency_ms", 0))
    except Exception:  # noqa: BLE001 - 재렌더 실패가 누끼 결과를 되돌리지 않는다
        log.warning("matching_flatlay render failed; keeping original photos",
                    exc_info=True)
        return None
