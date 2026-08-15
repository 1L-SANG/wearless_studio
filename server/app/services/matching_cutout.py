"""커스텀 매칭 의류 누끼 결과를 시드 카탈로그 톤으로 정돈한다.

캐노니컬 컷아웃(sam_client)은 투명 RGBA 를 준다. 시드 카탈로그는 회색 스튜디오
flat-lay 라, 화면·생성입력에서 나란히 놨을 때 이질감이 없으려면 같은 회색 배경 위에
얹어 불투명으로 만든다. 배경색은 시드 이미지 모서리에서 실측한 상수 하나다.
"""
from __future__ import annotations

import hashlib
import io
import uuid
from collections.abc import Sequence

from PIL import Image

#: 시드 카탈로그(seed/matching/*.png) 모서리에서 측정한 회색. 상수 하나로 고정.
MATCHING_CUTOUT_BG = (232, 232, 230)
#: 누끼 파생 asset 의 알고리즘 신원. 소스 해시와 함께 재처리 중복을 막는다.
ALGORITHM_VERSION = "matching-cutout-v1"
CUTOUT_KIND = "matchingCutout"
PRODUCER = "sam2-matching-cutout"

#: 카드 썸네일은 화면에서 130px 남짓이다. 레티나 여유까지 512px·JPEG 면 충분하고,
#: 컷아웃 원본 해상도(CUTOUT_MAX_PX=2048) 무압축 PNG 는 grid 합성 입력으로만 쓴다.
THUMBNAIL_MAX_PX = 512
THUMBNAIL_QUALITY = 85

#: 파생 asset 의 정리 라벨. "내 옷 삭제" 가 파생분까지 회수하려면 기존 정리 경로
#: (repo.soft_delete_unreferenced_custom_assets)가 아는 purpose 여야 한다.
GRID_PURPOSE = "custom_match_grid"
CUTOUT_PURPOSE = "custom_match_cutout"


def flatten_on_bg(rgba_png: bytes) -> bytes:
    """투명 RGBA PNG → 회색배경 불투명 PNG. 소스 크기·옷 픽셀 보존."""
    with Image.open(io.BytesIO(rgba_png)) as opened:
        cut = opened.convert("RGBA")
        bg = Image.new("RGB", cut.size, MATCHING_CUTOUT_BG)
        bg.paste(cut, (0, 0), cut)  # 알파를 마스크로 — 투명부만 배경이 남는다
    out = io.BytesIO()
    bg.save(out, "PNG", optimize=False)
    return out.getvalue()


def encode_thumbnail(image_bytes: bytes, *, max_px: int = THUMBNAIL_MAX_PX) -> bytes:
    """카드 표시용 축소 JPEG. 배경이 이미 불투명이라 알파를 잃을 게 없다."""
    with Image.open(io.BytesIO(image_bytes)) as opened:
        thumb = opened.convert("RGB")
        thumb.thumbnail((max_px, max_px), Image.LANCZOS)  # 비율 유지·확대 안 함
        out = io.BytesIO()
        thumb.save(out, "JPEG", quality=THUMBNAIL_QUALITY, optimize=True)
    return out.getvalue()


def source_fingerprint(values: Sequence[str | None]) -> str:
    """소스들의 안정 지문. SAM 이 준 source_hash(없으면 소스 키)를 순서 그대로 묶는다."""
    joined = "|".join(value or "" for value in values)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def derived_asset_id(*, role: str, matching_item_id: str, source_hash: str) -> str:
    """파생 asset 의 결정론적 신원(소스 지문 + 알고리즘 버전).

    stale lease 복구가 잡을 되돌려 재실행해도 같은 asset id → 같은 derived R2 키로
    수렴한다. uuid4 였을 땐 재실행마다 R2 객체와 asset 행이 한 벌씩 더 쌓였다
    (2026-08-13 리뷰 I5). 커스텀 grid 의 uuid5 규약과 같은 방식이다.
    """
    return str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"wearless:matching-cutout:{role}:{matching_item_id}:{source_hash}"
        f":{ALGORITHM_VERSION}",
    ))


def cutout_status_for(*, is_custom: bool, image_meta: dict | None,
                      has_active_job: bool) -> str | None:
    """커스텀 매칭 아이템의 누끼 상태. 시드는 항상 None.

    - ready: 현재 생성입력 asset 이 이미 누끼 파생이다(스왑 완료).
    - processing: 아직 원본인데 누끼 잡이 돌고 있다.
    - failed: 잡이 끝났는데 여전히 원본이다(SAM 실패 등). 화면은 원본을 그대로 보여준다.
    """
    if not is_custom:
        return None
    if isinstance(image_meta, dict) and image_meta.get("type") == CUTOUT_KIND:
        return "ready"
    return "processing" if has_active_job else "failed"


def metadata_for(*, source_hash: str | None, source_asset_id: str | None,
                 matching_item_id: str, purpose: str = GRID_PURPOSE,
                 source_asset_ids: Sequence[str] | None = None) -> dict:
    """누끼 파생 asset 의 provenance + 정리 정보.

    `purpose` 와 `sourceAssetIds` 는 장식이 아니다 — "내 옷 삭제" 는 현재 image asset 의
    metadata 에서 정리 대상을 모으고 purpose 로 거른다. 스왑 뒤엔 이 파생 grid 가 그
    image asset 이므로, 여기 원본 업로드·원본 grid id 가 실려 있지 않으면 그것들이
    통째로 잔존한다(2026-08-13 리뷰 I4).
    """
    meta = {
        "type": CUTOUT_KIND,
        "producer": PRODUCER,
        "algorithmVersion": ALGORITHM_VERSION,
        "sourceHash": source_hash,
        "sourceAssetId": source_asset_id,
        "matchingItemId": matching_item_id,
        "purpose": purpose,
    }
    if source_asset_ids is not None:
        meta["sourceAssetIds"] = list(source_asset_ids)
    return meta
