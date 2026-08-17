"""상품 사진 src 위생 — 서버가 마지막으로 거른다 (2026-08-17 사고).

프론트 목 어댑터의 데모 플레이스홀더(data:image/svg+xml)가 셀러의 상품 사진 자리로 새어
`products.colors[].images[]` 에 저장되면, 생성 전부가 그 값을 정본으로 읽고(마네킹·컷·상세
페이지·에디터) 화면은 `image.src` 를 그대로 그린다. 프론트에서 막았지만 **캐시된 옛 번들**을
쓰는 브라우저는 배포 즉시 보호되지 않으므로 서버에도 같은 문지기를 둔다.
"""

from __future__ import annotations

from app.services.product_photos import (sanitize_analysis_colors, sanitize_product_colors,
                                         strip_placeholder_images)

PLACEHOLDER = "data:image/svg+xml;utf8,%3Csvg%3E%3C%2Fsvg%3E"
REAL = "https://api.wearless.kr/v1/assets/a1/file"


def _colors(*srcs):
    return [{"id": "col1", "name": "화이트", "swatchId": "white", "isBase": True,
             "images": [{"id": f"i{n}", "slot": "Front", "src": src}
                        for n, src in enumerate(srcs)]}]


def test_placeholder_photos_are_stripped():
    out = strip_placeholder_images(_colors(REAL, PLACEHOLDER))
    assert [i["src"] for i in out[0]["images"]] == [REAL]
    # 색상 메타는 손대지 않는다 — 셀러가 저장하려던 값이다.
    assert out[0]["swatchId"] == "white" and out[0]["isBase"] is True


def test_blob_urls_are_stripped_too():
    """blob: 은 그 브라우저에서만 유효한 주소다 — 저장되면 다른 기기·다음 방문에서 죽는다."""
    out = strip_placeholder_images(_colors("blob:http://localhost/abc"))
    assert out[0]["images"] == []


def test_real_photos_and_asset_only_images_are_untouched():
    """src 없이 asset id 만 있는 이미지는 계약상 정상이다 — 지우면 사진이 사라진다."""
    colors = [{"id": "col1", "images": [{"id": "asset-1", "slot": "Front"},
                                        {"id": "asset-2", "slot": "Back", "src": REAL},
                                        {"id": "asset-3", "slot": "Detail", "src": "/v1/assets/x/file"}]}]
    assert strip_placeholder_images(colors) == colors


def test_unknown_shapes_pass_through_untouched():
    """colors 의 shape 은 프론트 소유다 — 모르는 키·타입을 건드리지 않는다."""
    assert strip_placeholder_images(None) is None
    assert strip_placeholder_images("nope") == "nope"
    weird = [{"id": "c", "images": "not-a-list", "extra": {"deep": 1}}, "string-color"]
    assert strip_placeholder_images(weird) == weird


def test_untouched_payload_is_returned_unchanged():
    fields = {"name": "티셔츠"}
    assert sanitize_product_colors(fields) is fields
    assert sanitize_analysis_colors({"fit": "regular"}) == {"fit": "regular"}


def test_product_and_analysis_payloads_use_the_same_rule():
    fields = {"name": "티셔츠", "colors": _colors(PLACEHOLDER, REAL)}
    product = sanitize_product_colors(fields)
    analysis = sanitize_analysis_colors({"colors": _colors(PLACEHOLDER, REAL)})
    assert [i["src"] for i in product["colors"][0]["images"]] == [REAL]
    assert [i["src"] for i in analysis["colors"][0]["images"]] == [REAL]
    # 원본을 제자리에서 바꾸지 않는다(호출부가 같은 dict 를 다시 쓰는 경우 대비).
    assert len(fields["colors"][0]["images"]) == 2


def test_both_save_routes_actually_call_the_sanitizer():
    """순수 함수만 있고 라우트가 안 부르면 아무것도 막지 못한다 — 배선을 고정한다."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "app" / "routes.py").read_text(
        encoding="utf-8")
    assert "product_photos.sanitize_product_colors(fields)" in src
    assert "product_photos.sanitize_analysis_colors(analysis)" in src
    # 저장 호출보다 먼저 걸러야 한다.
    assert (src.index("product_photos.sanitize_product_colors(fields)")
            < src.index("row = await repo.save_product(conn, project_id, user_id, fields)"))
