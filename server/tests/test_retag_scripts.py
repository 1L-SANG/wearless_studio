import json

from app.agents.gemini_image import InlineImage
from app.agents.style_tags import STYLE_TAGS
from scripts import retag_apply_sql, retag_matching_items, retag_review_html


def _items():
    return retag_review_html._load_list(retag_review_html.ITEMS_PATH)


def _proposals(items):
    return [
        {
            "id": item["id"],
            "name": item["name"],
            "current_tags": item["current_tags"],
            "proposed_tags": ["minimal", "modern"],
            "reason": "간결한 실루엣이 현대적인 미니멀 코디에 어울립니다.",
        }
        for item in items
    ]


def test_retag_payload_uses_one_inline_image_enum_schema_and_zero_temperature():
    item = _items()[0]
    payload = retag_matching_items.build_request_payload(
        item, InlineImage(mime="image/png", data=b"image-bytes")
    )

    parts = payload["contents"][0]["parts"]
    generation = payload["generationConfig"]
    tags_schema = generation["responseSchema"]["properties"]["style_tags"]
    assert len(parts) == 2
    assert parts[1]["inline_data"]["mime_type"] == "image/png"
    assert generation["temperature"] == 0
    assert tags_schema["minItems"] == 2
    assert tags_schema["maxItems"] == 4
    assert tags_schema["items"]["enum"] == list(STYLE_TAGS)


def test_review_html_contains_all_cards_sections_and_no_non_image_remote_assets():
    items = _items()
    rendered = retag_review_html.render_html(items, _proposals(items))

    assert rendered.count('<article class="card">') == 60
    assert "태그 분포 Before / After" in rendered
    assert "여성 · 상의" in rendered
    assert "남성 · 하의" in rendered
    assert '<script' not in rendered
    remote_lines = [line for line in rendered.splitlines() if "https://" in line]
    assert remote_lines
    assert all("<img " in line and "images.wearless.kr" in line for line in remote_lines)


def test_apply_sql_has_transaction_exactly_60_updates_and_count_check():
    rendered = retag_apply_sql.render_sql(_proposals(_items()))

    assert rendered.startswith("-- AI 재태깅")
    assert "BEGIN;" in rendered
    assert rendered.rstrip().endswith("COMMIT;")
    assert rendered.count("UPDATE matching_items SET style_tags = ") == 60
    # 검증 실패가 COMMIT 을 막아야 한다 — DO 블록 + RAISE EXCEPTION (2026-08-12 리뷰)
    assert "DO $$" in rendered
    assert "RAISE EXCEPTION" in rendered
    assert rendered.index("DO $$") < rendered.index("COMMIT;")
    # 큐레이션 행만 갱신 — 사용자 개별 등록(is_custom) 보호
    assert rendered.count("AND owner_user_id IS NULL AND project_id IS NULL;") == 60
    assert "::jsonb" in rendered


def test_seed_option_updates_existing_style_tags(tmp_path):
    items = _items()
    proposals = _proposals(items)
    seed_path = tmp_path / "matching_items.json"
    seed_path.write_text(
        json.dumps(
            [{"id": item["id"], "styleTags": ["basic", "daily"]} for item in items],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert retag_apply_sql.sync_seed(proposals, seed_path)
    updated = json.loads(seed_path.read_text(encoding="utf-8"))
    assert len(updated) == 60
    assert all(item["styleTags"] == ["minimal", "modern"] for item in updated)
