"""M-02 page-assembler 테스트 — mock buildEditorBlocksFromStoryboard 포팅 검증
(블록 매핑, 자동 블록, 카피라이팅 on/off, 빈 슬롯 폴백, 결정적 id)."""

from app.agents.page_assembler import assemble, build_auto_blocks

PRODUCT = {
    "name": "소프트 골지 라운드 니트",
    "measurements": [
        {"key": "totalLength", "value": 64, "unit": "cm"},
        {"key": "shoulderWidth", "value": 42, "unit": "cm"},
        {"key": "chestWidth", "value": 51, "unit": "cm"},
        {"key": "sleeveLength", "value": None, "unit": "cm"},
    ],
}


def _storyboard():
    return [
        {"id": "blk1", "kind": "hook", "title": "후킹", "source": "ai", "cutType": "horizon", "colorId": "col1"},
        {"id": "blk2", "kind": "selling", "title": "셀링포인트", "source": "ai", "cutType": "product", "colorId": "col1"},
        {"id": "blk3", "kind": "styling", "title": "스타일링컷", "source": "ai", "cutType": "styling", "colorId": "col1"},
    ]


# ── ai 블록 → image 엘리먼트 ────────────────────────────────────────────────
def test_ai_block_maps_to_image_element_with_cut_result_url():
    storyboard = [_storyboard()[2]]  # styling 블록 하나
    cut_results = [{"blockId": "blk3", "imageUrl": "https://cdn.example.com/blk3.png"}]
    blocks = assemble(storyboard, cut_results, [], PRODUCT, False)

    block = blocks[0]
    assert block["kind"] == "styling"
    assert block["name"] == "스타일링컷"
    assert block["h"] == 660
    img = block["elements"][0]
    assert img["type"] == "image"
    assert img["src"] == "https://cdn.example.com/blk3.png"
    assert img["cutType"] == "styling"
    assert img["radius"] == 12
    assert img["x"] == 60 and img["y"] == 50 and img["w"] == 880 and img["h"] == 560


# ── source='mine' 블록 ───────────────────────────────────────────────────────
def test_mine_block_uses_own_images_not_cut_results():
    storyboard = [{"id": "blk9", "kind": "info", "source": "mine", "ownImages": ["https://cdn.example.com/own.png"]}]
    blocks = assemble(storyboard, [], [], PRODUCT, False)

    block = blocks[0]
    assert block["name"] == "내 이미지"
    assert block["kind"] == "info"
    assert len(block["elements"]) == 1
    assert block["elements"][0]["src"] == "https://cdn.example.com/own.png"


def test_mine_block_with_no_own_images_has_no_elements():
    storyboard = [{"id": "blk9", "kind": "info", "source": "mine", "ownImages": []}]
    blocks = assemble(storyboard, [], [], PRODUCT, False)
    assert blocks[0]["elements"] == []


# ── 자동 블록 (size/care/ai-notice) ──────────────────────────────────────────
def test_auto_blocks_present_after_storyboard_blocks():
    storyboard = _storyboard()
    cut_results = [{"blockId": b["id"], "imageUrl": f"https://cdn.example.com/{b['id']}.png"} for b in storyboard]
    blocks = assemble(storyboard, cut_results, [], PRODUCT, False)

    auto_kinds = [b["kind"] for b in blocks[len(storyboard):]]
    assert auto_kinds == ["size", "care", "ai-notice"]
    assert all(b.get("auto") is True for b in blocks[len(storyboard):])


def test_size_block_reads_product_measurements():
    blocks = build_auto_blocks(PRODUCT)
    size_block = blocks[0]
    assert size_block["kind"] == "size"
    texts = [e["text"] for e in size_block["elements"] if e["type"] == "text"]
    assert "총장" in texts
    assert "64 cm" in texts
    assert "—" in texts  # sleeveLength value=None → em dash fallback


def test_care_and_ai_notice_blocks_have_fixed_copy():
    blocks = build_auto_blocks(PRODUCT)
    care_block, ai_notice_block = blocks[1], blocks[2]
    assert care_block["kind"] == "care"
    assert "케어라벨" in care_block["elements"][1]["text"]
    assert ai_notice_block["kind"] == "ai-notice"
    assert "AI를 활용해 생성" in ai_notice_block["elements"][0]["text"]


# ── copywriting on/off ───────────────────────────────────────────────────────
def test_copywriting_on_places_headline_and_selling_text():
    storyboard = _storyboard()
    cut_results = [{"blockId": b["id"], "imageUrl": f"https://cdn.example.com/{b['id']}.png"} for b in storyboard]
    copy_results = [
        {"blockId": "blk1", "texts": [{"role": "headline", "text": "겨울을 부드럽게"}]},
        {"blockId": "blk2", "texts": [{"role": "body", "text": "강조 포인트를 살린 카피"}]},
    ]
    blocks = assemble(storyboard, cut_results, copy_results, PRODUCT, True)

    hook_block, selling_block, styling_block = blocks[0], blocks[1], blocks[2]
    assert len(hook_block["elements"]) == 2
    assert hook_block["elements"][1]["type"] == "text"
    assert hook_block["elements"][1]["text"] == "겨울을 부드럽게"

    assert len(selling_block["elements"]) == 2
    assert selling_block["elements"][1]["text"] == "강조 포인트를 살린 카피"

    # styling 블록은 hook/selling 이 아니므로 카피 미배치 (이미지 엘리먼트만)
    assert len(styling_block["elements"]) == 1


def test_copywriting_off_has_no_text_elements():
    storyboard = _storyboard()
    cut_results = [{"blockId": b["id"], "imageUrl": f"https://cdn.example.com/{b['id']}.png"} for b in storyboard]
    copy_results = [
        {"blockId": "blk1", "texts": [{"role": "headline", "text": "겨울을 부드럽게"}]},
        {"blockId": "blk2", "texts": [{"role": "body", "text": "강조 포인트를 살린 카피"}]},
    ]
    blocks = assemble(storyboard, cut_results, copy_results, PRODUCT, False)

    for block in blocks[:3]:
        assert all(e["type"] != "text" for e in block["elements"])


def test_copywriting_on_but_no_matching_copy_result_omits_text():
    storyboard = [_storyboard()[0]]  # hook block, no copy_results entry
    blocks = assemble(storyboard, [], [], PRODUCT, True)
    assert len(blocks[0]["elements"]) == 1  # image only, no headline injected


# ── 빈 슬롯 폴백 (컷 생성 실패) ───────────────────────────────────────────────
def test_missing_cut_result_renders_empty_slot_without_crash():
    storyboard = [_storyboard()[0]]  # blk1, no cut_results entry for it
    blocks = assemble(storyboard, [], [], PRODUCT, False)

    img = blocks[0]["elements"][0]
    assert img["type"] == "image"
    assert img["src"] is None


def test_partial_cut_results_only_missing_block_is_empty():
    storyboard = _storyboard()
    cut_results = [
        {"blockId": "blk1", "imageUrl": "https://cdn.example.com/blk1.png"},
        # blk2, blk3 없음 — 생성 실패 시나리오
    ]
    blocks = assemble(storyboard, cut_results, [], PRODUCT, False)

    assert blocks[0]["elements"][0]["src"] == "https://cdn.example.com/blk1.png"
    assert blocks[1]["elements"][0]["src"] is None
    assert blocks[2]["elements"][0]["src"] is None


# ── 결정적 id ─────────────────────────────────────────────────────────────────
def test_ids_are_deterministic_across_calls():
    storyboard = _storyboard()
    cut_results = [{"blockId": b["id"], "imageUrl": f"https://cdn.example.com/{b['id']}.png"} for b in storyboard]
    copy_results = [{"blockId": "blk1", "texts": [{"role": "headline", "text": "겨울을 부드럽게"}]}]

    run1 = assemble(storyboard, cut_results, copy_results, PRODUCT, True)
    run2 = assemble(storyboard, cut_results, copy_results, PRODUCT, True)
    assert run1 == run2

    block_ids = [b["id"] for b in run1]
    assert block_ids == ["b0", "b1", "b2", "b3", "b4", "b5"]  # 3 storyboard + size/care/ai-notice
    assert run1[0]["elements"][0]["id"] == "b0e0"
    assert run1[0]["elements"][1]["id"] == "b0e1"  # headline text el


def test_no_uuid_or_random_module_used():
    import inspect

    from app.agents import page_assembler

    src = inspect.getsource(page_assembler)
    assert "import uuid" not in src
    assert "import random" not in src
