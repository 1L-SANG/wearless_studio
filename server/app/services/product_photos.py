"""상품 사진 src 위생 — 서버가 마지막으로 거른다.

`products.colors[].images[]` 는 생성의 정본이다: 마네킹(`mannequin.base_color_images`),
컷(`cut_generator`), 상세페이지(`detail_page_job`), 에디터(`editor_image_job`)가 모두 여기서
사진을 찾고, 에디터·콘티 화면은 `image.src` 를 그대로 그린다.

그래서 `data:` 로 시작하는 src 는 여기 들어와선 안 된다. 이 앱에서 그건 셀러 사진이 아니라
프론트 목 어댑터의 데모 플레이스홀더(SVG)뿐이고, 실제로 2026-08-17 에 그게 새어 들어와
셀러 화면에 데모 실루엣이 뜨고 확정 업로드가 400 으로 막혔다.

프론트에서 이미 다섯 겹으로 막았지만 서버에도 둔다 — 이유는 하나다: **셀러 브라우저의
캐시된 옛 번들**. 클라이언트 수정은 그 브라우저가 새로 받을 때까지 효력이 없고, 그동안 새는
것을 막을 수 있는 곳은 서버뿐이다.

거절(400)이 아니라 조용히 걷어내고 로그를 남긴다: 셀러가 저장하려는 나머지 값(상품명·색상명·
실측)은 정상이고, 저장 자체를 실패시키면 화면이 막힌다. 걷어낸 사진은 애초에 셀러의 사진이
아니므로 잃는 것이 없다.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: 사진 src 로 허용하지 않는 스킴. `blob:` 은 그 브라우저 안에서만 유효한 주소라 서버에
#: 저장돼도 다른 기기·다음 방문에서 죽는다 — 데모 플레이스홀더와 같은 등급으로 본다.
_REJECTED_SRC_PREFIXES = ("data:", "blob:")


def _is_rejected(src) -> bool:
    return isinstance(src, str) and src.strip().lower().startswith(_REJECTED_SRC_PREFIXES)


def strip_placeholder_images(colors, *, where: str = "product") -> list:
    """colors 에서 저장해선 안 되는 src 의 이미지를 걷어낸다. 그 외는 손대지 않는다.

    입력 shape 은 프론트 소유(계약 §3.1)라 알 수 없는 키·타입은 그대로 통과시킨다 —
    이 함수가 하는 일은 딱 하나, 사진 목록에서 저장 불가한 항목을 빼는 것이다.
    """
    if not isinstance(colors, list):
        return colors
    removed = 0
    out = []
    for color in colors:
        if not isinstance(color, dict) or not isinstance(color.get("images"), list):
            out.append(color)
            continue
        kept = [image for image in color["images"]
                if not (isinstance(image, dict) and _is_rejected(image.get("src")))]
        removed += len(color["images"]) - len(kept)
        out.append({**color, "images": kept} if len(kept) != len(color["images"]) else color)
    if removed:
        log.warning("stripped %d placeholder photo(s) from %s colors", removed, where)
    return out


def sanitize_product_colors(fields: dict) -> dict:
    """상품 저장 payload 의 colors 를 위생 처리한 새 dict. colors 가 없으면 그대로."""
    if not isinstance(fields, dict) or "colors" not in fields:
        return fields
    return {**fields, "colors": strip_placeholder_images(fields["colors"], where="product")}


def sanitize_analysis_colors(payload: dict) -> dict:
    """분석 저장 payload 의 colors 도 같은 규칙으로 위생 처리한다.

    지금은 이 값을 읽는 코드가 없지만(생성은 전부 product.colors 를 본다), 2026-08-17 사고의
    플레이스홀더가 실제로 여기 3건 저장됐다. 나중에 누가 이 필드를 읽기 시작할 때 오염을
    물려받지 않게 입구에서 막는다.
    """
    if not isinstance(payload, dict) or "colors" not in payload:
        return payload
    return {**payload, "colors": strip_placeholder_images(payload["colors"], where="analysis")}
