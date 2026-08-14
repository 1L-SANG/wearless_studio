"""커스텀 매칭 의류 누끼 결과를 시드 카탈로그 톤으로 정돈한다.

캐노니컬 컷아웃(sam_client)은 투명 RGBA 를 준다. 시드 카탈로그는 회색 스튜디오
flat-lay 라, 화면·생성입력에서 나란히 놨을 때 이질감이 없으려면 같은 회색 배경 위에
얹어 불투명으로 만든다. 배경색은 시드 이미지 모서리에서 실측한 상수 하나다.
"""
from __future__ import annotations

import io

from PIL import Image

#: 시드 카탈로그(seed/matching/*.png) 모서리에서 측정한 회색. 상수 하나로 고정.
MATCHING_CUTOUT_BG = (232, 232, 230)
#: 누끼 파생 asset 의 알고리즘 신원. 소스 해시와 함께 재처리 중복을 막는다.
ALGORITHM_VERSION = "matching-cutout-v1"
CUTOUT_KIND = "matchingCutout"
PRODUCER = "sam2-matching-cutout"


def flatten_on_bg(rgba_png: bytes) -> bytes:
    """투명 RGBA PNG → 회색배경 불투명 PNG. 소스 크기·옷 픽셀 보존."""
    with Image.open(io.BytesIO(rgba_png)) as opened:
        cut = opened.convert("RGBA")
        bg = Image.new("RGB", cut.size, MATCHING_CUTOUT_BG)
        bg.paste(cut, (0, 0), cut)  # 알파를 마스크로 — 투명부만 배경이 남는다
    out = io.BytesIO()
    bg.save(out, "PNG", optimize=False)
    return out.getvalue()


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


def metadata_for(*, source_hash: str | None, source_asset_id: str,
                 matching_item_id: str) -> dict:
    """누끼 파생 asset 의 provenance."""
    return {
        "type": CUTOUT_KIND,
        "producer": PRODUCER,
        "algorithmVersion": ALGORITHM_VERSION,
        "sourceHash": source_hash,
        "sourceAssetId": source_asset_id,
        "matchingItemId": matching_item_id,
    }
