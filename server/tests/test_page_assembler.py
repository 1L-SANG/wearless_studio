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
        {"id": "blk1", "sectionRole": "hooking", "contentRole": "hero", "source": "ai", "cutType": "styling", "colorId": "col1"},
        {"id": "blk2", "sectionRole": "hooking", "contentRole": "benefit", "source": "ai", "cutType": "horizon", "colorId": "col1"},
        {"id": "blk3", "sectionRole": "styling", "contentRole": "coordination", "source": "ai", "cutType": "styling", "colorId": "col1"},
    ]


# ── ai 블록 → image 엘리먼트 ────────────────────────────────────────────────
def test_ai_block_maps_to_image_element_with_cut_result_url():
    storyboard = [_storyboard()[2]]  # styling 블록 하나
    cut_results = [{"blockId": "blk3", "imageUrl": "https://cdn.example.com/blk3.png"}]
    blocks = assemble(storyboard, cut_results, [], PRODUCT, False)

    block = blocks[0]
    assert block["kind"] == "styling"
    assert block["contentRole"] == "coordination"
    assert block["name"] == "코디 활용"
    # dims 미제공 → 기본 세로비(2:3) 폴백. 빈 캡션도 편집 자리를 차지한다.
    assert block["h"] == 1512
    img = block["elements"][0]
    assert img["type"] == "image"
    assert img["src"] == "https://cdn.example.com/blk3.png"
    assert img["cutType"] == "styling"
    assert img["radius"] == 12
    assert img["x"] == 60 and img["y"] == 50 and img["w"] == 880 and img["h"] == 1320
    caption = block["elements"][1]
    assert (caption["x"], caption["y"], caption["w"], caption["h"]) == (60, 1402, 880, 60)
    assert caption["style"] == {
        "size": 44, "weight": 600, "color": "#0e0d14", "lineHeight": 60,
    }
    assert caption["text"] == ""
    assert caption["placeholder"] == "사진을 설명하는 소제목 (17자 내외)"
    assert caption["copyRole"] == "headline"
    assert caption["textSlotVersion"] == 1  # 위치를 바꿔도 옛 사진 위 카피로 삭제하지 않는다.


# ── 이미지 비율 보존 (WS2: 에디터·산출물 크롭 회귀 방지) ──────────────────────
def _one_ai_block(cut_result: dict):
    return assemble([_storyboard()[2]], [cut_result], [], PRODUCT, False)[0]


def test_image_element_keeps_source_aspect_ratio():
    # 실제 AG-06 컷(848×1264 = 2:3)을 주면 요소도 같은 비율이어야 한다.
    # 구현 전에는 880×560(가로)로 고정돼 object-fit:cover 가 높이를 ~58% 잘라먹었다.
    block = _one_ai_block({"blockId": "blk3", "imageUrl": "u", "width": 848, "height": 1264})
    img = block["elements"][0]
    assert (img["w"], img["h"]) == (880, 1312)
    assert abs(img["w"] / img["h"] - 848 / 1264) < 0.002


def test_image_element_follows_non_default_ratio():
    # 하드코딩이 아님을 증명 — 3:4 소스면 요소도 3:4.
    block = _one_ai_block({"blockId": "blk3", "imageUrl": "u", "width": 900, "height": 1200})
    img = block["elements"][0]
    assert (img["w"], img["h"]) == (880, 1173)
    assert abs(img["w"] / img["h"] - 0.75) < 0.002


def test_image_box_falls_back_when_dims_missing_or_invalid():
    # 구 데이터·측정 실패(None/0/문자열) → 2:3 폴백, 크래시 없음
    for bad in ({}, {"width": None, "height": None}, {"width": 0, "height": 0},
                {"width": "x", "height": "y"}):
        img = _one_ai_block({"blockId": "blk3", "imageUrl": "u", **bad})["elements"][0]
        assert (img["w"], img["h"]) == (880, 1320)


def test_block_contains_image_without_overflow():
    # 요소가 블록 밖으로 넘치면 에디터·산출물에서 잘린다 — 블록 h 가 항상 이미지를 담아야 한다.
    for dims in ({"width": 848, "height": 1264}, {"width": 900, "height": 1200},
                 {"width": 1200, "height": 900}, {}):
        block = _one_ai_block({"blockId": "blk3", "imageUrl": "u", **dims})
        img = block["elements"][0]
        assert block["h"] >= img["y"] + img["h"], f"{dims} 에서 이미지가 블록을 넘침"


def test_absurd_dims_are_clamped_and_block_stays_sane():
    # 파손·조작 dims(10000×1, 1×10000)가 레이아웃을 무너뜨리면 안 된다 (codex 리뷰 #4).
    flat = _one_ai_block({"blockId": "blk3", "imageUrl": "u", "width": 10000, "height": 1})
    tall = _one_ai_block({"blockId": "blk3", "imageUrl": "u", "width": 1, "height": 10000})
    assert 200 <= flat["elements"][0]["h"] <= 2400
    assert 200 <= tall["elements"][0]["h"] <= 2400
    assert flat["h"] < 3000 and tall["h"] < 3000


def test_short_image_still_contains_headline():
    # 이미지가 짧아도 헤드라인(y=110)이 블록 밖으로 잘리면 안 된다 — 블록 높이는 모든 요소 기준.
    storyboard = [dict(_storyboard()[0])]  # hero
    cut_results = [{"blockId": "blk1", "imageUrl": "u", "width": 10000, "height": 1}]
    copy_results = [{"blockId": "blk1", "texts": [{"role": "headline", "text": "봄의 무드"}]}]
    block = assemble(storyboard, cut_results, copy_results, PRODUCT, True)[0]
    for el in block["elements"]:
        assert el["y"] + el["h"] <= block["h"], f"{el['type']} 이 블록 밖으로 넘침"


def test_headline_copy_sits_below_dynamic_image_and_body_is_ignored():
    storyboard = [_storyboard()[2]]
    cut_results = [{"blockId": "blk3", "imageUrl": "u", "width": 848, "height": 1264}]
    copy_results = [{"blockId": "blk3", "texts": [
        {"role": "body", "text": "이 본문은 쓰지 않는다"},
        {"role": "headline", "text": "부드러운 촉감"},
    ]}]
    block = assemble(storyboard, cut_results, copy_results, PRODUCT, True)[0]
    img = block["elements"][0]
    caption = next(el for el in block["elements"] if el.get("copyRole") == "headline")
    assert caption["text"] == "부드러운 촉감"
    assert caption["y"] == img["y"] + img["h"] + 32
    assert caption["style"] == {
        "size": 44, "weight": 600, "color": "#0e0d14", "lineHeight": 60,
    }
    assert caption["y"] + caption["h"] <= block["h"]
    assert all(el.get("copyRole") != "body" for el in block["elements"])


def test_caption_height_keeps_twenty_korean_characters_on_one_line_and_wraps_twenty_one():
    storyboard = [_storyboard()[2]]
    cut_results = [{"blockId": "blk3", "imageUrl": "u"}]

    one_line = assemble(storyboard, cut_results, [{
        "blockId": "blk3", "texts": [{"role": "headline", "text": "가" * 20}],
    }], PRODUCT, True)[0]
    two_lines = assemble(storyboard, cut_results, [{
        "blockId": "blk3", "texts": [{"role": "headline", "text": "가" * 21}],
    }], PRODUCT, True)[0]

    one_caption = next(el for el in one_line["elements"] if el.get("copyRole") == "headline")
    two_caption = next(el for el in two_lines["elements"] if el.get("copyRole") == "headline")
    assert one_caption["h"] == 60 and one_line["h"] == 1512
    assert two_caption["h"] == 120 and two_lines["h"] == 1572


# ── source='mine' 블록 ───────────────────────────────────────────────────────
def test_mine_block_uses_own_images_not_cut_results():
    storyboard = [{"id": "blk9", "sectionRole": "styling", "source": "mine", "ownImages": ["https://cdn.example.com/own.png"]}]
    blocks = assemble(storyboard, [], [], PRODUCT, False)

    block = blocks[0]
    assert block["name"] == "내 이미지"
    assert block["kind"] == "styling"
    assert block["contentRole"] == "custom"
    assert len(block["elements"]) == 1
    assert block["elements"][0]["src"] == "https://cdn.example.com/own.png"


def test_mine_block_with_no_own_images_has_no_elements():
    storyboard = [{"id": "blk9", "sectionRole": "styling", "source": "mine", "ownImages": []}]
    blocks = assemble(storyboard, [], [], PRODUCT, False)
    assert blocks[0]["elements"] == []


def test_explicit_detail_role_sets_editor_identity():
    storyboard = [{
        "id": "blk-new", "sectionRole": "product", "contentRole": "detail",
        "source": "ai", "cutType": "product",
    }]
    block = assemble(storyboard, [], [], PRODUCT, False)[0]
    assert block["kind"] == "product"
    assert block["contentRole"] == "detail"
    assert block["name"] == "디테일"


def test_mine_block_preserves_explicit_section_role():
    storyboard = [{
        "id": "mine-product", "source": "mine", "sectionRole": "product",
        "ownImages": ["https://cdn.example.com/detail.png"],
    }]
    block = assemble(storyboard, [], [], PRODUCT, False)[0]
    assert block["kind"] == "product"
    assert block["contentRole"] == "custom"


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


# ── AI 고지 분기 (FaceMarket 라이선스 실제 모델, FM-31) ──────────────────────
def test_ai_notice_without_license_keeps_legacy_copy_verbatim():
    # 라이선스 미사용(마네킹·AI) 경로 무변경 — 문구·높이까지 기존 그대로.
    for blocks in (build_auto_blocks(PRODUCT),
                   assemble(_storyboard(), [], [], PRODUCT, False)[-3:]):
        el = blocks[-1]["elements"][0]
        assert el["text"] == (
            "본 상세페이지의 일부 이미지는 AI를 활용해 생성되었습니다. "
            "실제 상품의 색상과 핏은 촬영 환경 및 화면 설정에 따라 다르게 보일 수 있습니다."
        )
        assert el["h"] == 60
        assert "실제 모델" not in el["text"]


def test_ai_notice_with_license_states_verified_real_model():
    # 26.06 가상인물 표기 의무는 가상인물에만 — 라이선스 실제 모델은 대상이 아니다.
    # 다만 AI 로 생성한 이미지라는 사실 고지는 유지된다. 전 컷이 라이선스 얼굴인 경우.
    notice = {"modelName": "김*연", "licenseId": "lic-123", "faceCuts": 3, "totalCuts": 3}
    blocks = build_auto_blocks(PRODUCT, license_notice=notice)
    el = blocks[2]["elements"][0]
    assert blocks[2]["kind"] == "ai-notice"
    assert "검증된 실제 모델 김*연 님" in el["text"]
    assert "가상인물 아님" in el["text"]
    assert "AI로 생성했습니다" in el["text"]          # AI 생성 사실 고지는 유지
    assert "/verify/lic-123" in el["text"]           # 라이선스 진위 확인 경로
    assert "일부 이미지는 AI를 활용해 생성되었습니다" not in el["text"]  # 기본 문구로 회귀하지 않음


def test_ai_notice_partial_license_does_not_claim_whole_page_is_real_model():
    """일부 컷만 라이선스 얼굴이면 페이지 전체를 '가상인물 아님' 으로 주장하면 안 된다.

    얼굴 레퍼런스는 얼굴이 식별되는 컷에만 붙는다(뒷모습·하반신 제외 — cut_generator
    ._face_fits). 그 제외 컷에도 인물은 렌더되지만 **AI 가 지어낸 가상인물**이라 26.06 표기
    의무 대상이다. 페이지 전체 고지를 뒤집으면 표기 의무 대상 컷에 반대 표기가 붙는다(허위표시).
    뒷모습 컷에서 이 경로는 도달 가능하다.
    """
    notice = {"modelName": "김*연", "licenseId": "lic-123", "faceCuts": 1, "totalCuts": 3}
    el = build_auto_blocks(PRODUCT, license_notice=notice)[2]["elements"][0]
    assert "검증된 실제 모델 김*연 님" in el["text"]      # 라이선스 사실은 고지
    assert "얼굴이 드러나지 않는 컷의 인물은 AI가 생성했습니다" in el["text"]  # 나머지는 AI 임을 명시
    assert "본 상세페이지의 인물 이미지는 검증된 실제 모델" not in el["text"]  # 전체 주장 금지


def test_ai_notice_without_cut_counts_falls_back_to_partial_claim():
    """카운트를 모르면 안전측(일부 컷)으로 — 과대 주장이 허위표시 방향이다."""
    el = build_auto_blocks(
        PRODUCT, license_notice={"modelName": "김*연", "licenseId": "lic-123"}
    )[2]["elements"][0]
    assert "얼굴이 드러나지 않는 컷의 인물은 AI가 생성했습니다" in el["text"]


def test_assemble_threads_license_notice_to_auto_block():
    notice = {"modelName": "박*수", "licenseId": "lic-9", "faceCuts": 2, "totalCuts": 2}
    blocks = assemble(_storyboard(), [], [], PRODUCT, False, license_notice=notice)
    assert "검증된 실제 모델 박*수 님" in blocks[-1]["elements"][0]["text"]


def test_ai_notice_licensed_copy_never_carries_unmasked_pii():
    # 상세페이지는 무인증 공개면 — 공개 검증(QR)의 하드룰과 같은 기준으로 마스킹된
    # display_name 만 싣는다. 얼굴 키·digest 류는 애초에 전달 경로가 없어야 한다.
    notice = {"modelName": "홍*동", "licenseId": "lic-1"}
    text = build_auto_blocks(PRODUCT, license_notice=notice)[2]["elements"][0]["text"]
    assert "홍*동" in text
    for leaked in ("face_image_key", "sha256-", "r2_key", "faces/"):
        assert leaked not in text


# ── step03 검증 배지 + QR (제안서 "& DID 서명 첨부") ─────────────────────────
def test_ai_notice_licensed_appends_verified_badge_and_verify_element():
    # 라이선스 잠긴 페이지에만 '검증된 실제 모델' 배지 + license-verify(QR) 요소가 붙는다.
    notice = {"modelName": "김*연", "licenseId": "lic-123", "faceCuts": 3, "totalCuts": 3}
    ai_block = build_auto_blocks(PRODUCT, license_notice=notice)[2]
    types = [e["type"] for e in ai_block["elements"]]
    assert types == ["text", "text", "license-verify"]  # 고지문 + 배지 + QR

    badge = ai_block["elements"][1]
    assert "검증된 실제 모델" in badge["text"]

    verify = ai_block["elements"][2]
    assert verify["type"] == "license-verify"
    assert verify["licenseId"] == "lic-123"        # 프론트 QR = {origin}/verify/{licenseId}
    assert verify["id"] == "b2e2"                  # 결정적 id 유지


def test_ai_notice_block_exposes_license_id_as_meta():
    # 프론트가 QR 생성용 licenseId 를 요소 밖에서도 읽을 수 있게 블록 메타로 노출.
    notice = {"modelName": "김*연", "licenseId": "lic-9"}
    ai_block = build_auto_blocks(PRODUCT, license_notice=notice)[2]
    assert ai_block["licenseId"] == "lic-9"


def test_verify_element_carries_only_license_id_no_face_or_pii():
    # QR·배지에는 licenseId(공개 검증용 능력토큰)만 — 얼굴·digest·CI·생년월일 전달 경로 없음.
    notice = {"modelName": "홍*동", "licenseId": "lic-1", "faceCuts": 1, "totalCuts": 2}
    verify = build_auto_blocks(PRODUCT, license_notice=notice)[2]["elements"][2]
    assert set(verify.keys()) == {"id", "type", "x", "y", "w", "h", "licenseId"}
    assert "홍*동" not in repr(verify)              # 마스킹 이름조차 QR 요소엔 없다


def test_ai_notice_without_license_has_no_badge_qr_or_license_meta():
    # 회귀 0: 라이선스 없는 일반 상세페이지는 배지·QR·licenseId 메타가 전혀 없다.
    for blocks in (build_auto_blocks(PRODUCT),
                   assemble(_storyboard(), [], [], PRODUCT, False)[-3:]):
        ai_block = blocks[-1]
        assert len(ai_block["elements"]) == 1               # 고지문 하나뿐 (기존 그대로)
        assert ai_block["elements"][0]["type"] == "text"
        assert "licenseId" not in ai_block                  # 블록 메타 미노출
        assert all(e["type"] != "license-verify" for e in ai_block["elements"])


# ── copywriting on/off ───────────────────────────────────────────────────────
def test_copywriting_on_uses_headline_for_every_single_photo_caption():
    storyboard = _storyboard()
    cut_results = [{"blockId": b["id"], "imageUrl": f"https://cdn.example.com/{b['id']}.png"} for b in storyboard]
    copy_results = [
        {"blockId": "blk1", "texts": [{"role": "headline", "text": "겨울을 부드럽게"}]},
        {"blockId": "blk2", "texts": [
            {"role": "body", "text": "무시할 본문"},
            {"role": "headline", "text": "강조 포인트를 살린 카피"},
        ]},
    ]
    blocks = assemble(storyboard, cut_results, copy_results, PRODUCT, True)

    hero_block, benefit_block, coordination_block = blocks[0], blocks[1], blocks[2]
    hero_caption = next(el for el in hero_block["elements"] if el.get("copyRole") == "headline")
    assert hero_caption["text"] == "겨울을 부드럽게"

    benefit_caption = next(el for el in benefit_block["elements"] if el.get("copyRole") == "headline")
    assert benefit_caption["text"] == "강조 포인트를 살린 카피"
    assert all(el.get("copyRole") != "body" for el in benefit_block["elements"])

    # 카피 결과가 없어도 셀러가 고칠 수 있는 빈 캡션 슬롯은 남는다.
    coordination_caption = next(el for el in coordination_block["elements"] if el.get("copyRole") == "headline")
    assert coordination_caption["text"] == ""
    assert coordination_caption["placeholder"] == "사진을 설명하는 소제목 (17자 내외)"


def test_copywriting_ignores_body_for_non_hero_content_roles():
    storyboard = [{
        "id": "fit-1", "sectionRole": "studio", "contentRole": "fit",
        "source": "ai", "cutType": "horizon",
    }]
    copy_results = [{"blockId": "fit-1", "texts": [{"role": "body", "text": "실루엣을 확인해보세요"}]}]
    block = assemble(storyboard, [], copy_results, PRODUCT, True)[0]
    assert block["kind"] == "studio"
    assert block["contentRole"] == "fit"
    caption = next(el for el in block["elements"] if el.get("copyRole") == "headline")
    assert caption["text"] == ""
    assert caption["placeholder"] == "사진을 설명하는 소제목 (17자 내외)"


def test_copywriting_off_keeps_empty_caption_and_deterministic_labels():
    storyboard = _storyboard()
    cut_results = [{"blockId": b["id"], "imageUrl": f"https://cdn.example.com/{b['id']}.png"} for b in storyboard]
    copy_results = [
        {"blockId": "blk1", "texts": [{"role": "headline", "text": "겨울을 부드럽게"}]},
        {"blockId": "blk2", "texts": [{"role": "body", "text": "강조 포인트를 살린 카피"}]},
    ]
    blocks = assemble(storyboard, cut_results, copy_results, PRODUCT, False)

    assert [
        next(e for e in block["elements"] if e.get("copyRole") == "headline")["text"]
        for block in blocks[:3]
    ] == ["", "", ""]
    assert not any(e.get("copyRole") == "label" for e in blocks[0]["elements"])
    assert next(e for e in blocks[1]["elements"] if e.get("copyRole") == "label")["text"] == "POINT 01"
    assert next(e for e in blocks[2]["elements"] if e.get("copyRole") == "label")["text"] == "STYLING"


def test_copywriting_on_but_no_matching_copy_result_keeps_placeholder_slot():
    storyboard = [_storyboard()[0]]  # hero block, no copy_results entry
    blocks = assemble(storyboard, [], [], PRODUCT, True)
    caption = next(e for e in blocks[0]["elements"] if e.get("copyRole") == "headline")
    assert caption["text"] == ""
    assert caption["placeholder"] == "사진을 설명하는 소제목 (17자 내외)"


def test_single_photo_labels_follow_content_role_and_point_sequence():
    storyboard = [
        {"id": "hero", "sectionRole": "hooking", "contentRole": "hero", "source": "ai", "cutType": "styling"},
        {"id": "point-a", "sectionRole": "hooking", "contentRole": "benefit", "source": "ai", "cutType": "styling"},
        {"id": "point-b", "sectionRole": "hooking", "contentRole": "benefit", "source": "ai", "cutType": "horizon"},
        {"id": "fit", "sectionRole": "studio", "contentRole": "fit", "source": "ai", "cutType": "horizon"},
        {"id": "front", "sectionRole": "product", "contentRole": "productOverview", "source": "ai", "cutType": "product", "direction": "front"},
        {"id": "back", "sectionRole": "product", "contentRole": "productOverview", "source": "ai", "cutType": "product", "direction": "back"},
        {"id": "detail", "sectionRole": "product", "contentRole": "detail", "source": "ai", "cutType": "product"},
        {"id": "coord", "sectionRole": "styling", "contentRole": "coordination", "source": "ai", "cutType": "styling"},
        {"id": "wear", "sectionRole": "styling", "contentRole": "coordination", "source": "ai", "cutType": "styling"},
    ]
    blocks = assemble(storyboard, [], [], PRODUCT, False)[:len(storyboard)]

    labels = [
        next((e for e in block["elements"] if e.get("copyRole") == "label"), None)
        for block in blocks
    ]
    assert labels[0] is None, "첫 장에는 꼬리표가 없다"
    assert [label["text"] for label in labels[1:]] == [
        "POINT 01", "POINT 02", "FIT", "FRONT", "BACK", "DETAIL", "STYLING", "STYLING",
    ]
    assert all((label["x"], label["y"], label["w"], label["h"]) == (60, 44, 400, 38)
               for label in labels[1:])
    assert all(label["style"] == {
        "size": 28, "weight": 700, "tracking": 3, "color": "#9a9aa2", "lineHeight": 38,
    } for label in labels[1:])
    first_image = next(e for e in blocks[0]["elements"] if e["type"] == "image")
    assert first_image["y"] == 50
    for block in blocks[1:]:
        image = next(e for e in block["elements"] if e["type"] == "image")
        caption = next(e for e in block["elements"] if e.get("copyRole") == "headline")
        assert image["y"] == 114
        assert caption["y"] == image["y"] + image["h"] + 32


def test_signature_first_cut_keeps_only_contrast_aware_hook_title():
    storyboard = [{
        "id": "signature", "sectionRole": "hooking", "contentRole": "hero",
        "source": "ai", "cutType": "horizon", "hookTitleOverlay": True,
    }]
    cut_results = [{
        "blockId": "signature", "imageUrl": "signature.png", "width": 1000, "height": 1000,
        "hookTitleColor": "#111111",
    }]
    copy_results = [{"blockId": "signature", "texts": [{"role": "headline", "text": "쓰지 않는 캡션"}]}]
    block = assemble(storyboard, cut_results, copy_results, PRODUCT, True)[0]

    texts = [element for element in block["elements"] if element["type"] == "text"]
    assert len(texts) == 1
    assert texts[0]["copyRole"] == "hookTitle"
    assert texts[0]["text"] == PRODUCT["name"]
    assert (texts[0]["y"], texts[0]["h"]) == (454, 72)
    assert texts[0]["style"] == {
        "font": "Pretendard", "size": 60, "weight": 700,
        "color": "#111111", "align": "center", "lineHeight": 72,
    }
    assert texts[0]["style"]["color"] == "#111111"
    assert all(element.get("copyRole") not in {"headline", "label"} for element in texts)


def test_signature_long_product_name_wraps_at_words_into_two_centered_lines():
    product = {**PRODUCT, "name": "프리미엄 울 블렌드 오버핏 케이블 라운드 니트"}
    storyboard = [{
        "id": "signature", "sectionRole": "hooking", "contentRole": "hero",
        "source": "ai", "cutType": "horizon", "hookTitleOverlay": True,
    }]
    cut_results = [{
        "blockId": "signature", "imageUrl": "signature.png", "width": 1000, "height": 1000,
        "hookTitleColor": "#ffffff",
    }]

    block = assemble(storyboard, cut_results, [], product, True)[0]
    title = next(element for element in block["elements"] if element.get("copyRole") == "hookTitle")
    assert title["text"] == "프리미엄 울 블렌드\n오버핏 케이블 라운드 니트"
    assert title["text"].count("\n") == 1
    assert (title["y"], title["h"]) == (418, 144)
    assert title["style"]["size"] == 60
    assert title["style"]["lineHeight"] == 72


def test_two_and_three_column_rows_place_labels_below_each_photo():
    product_row = [
        {"id": direction, "sectionRole": "product", "contentRole": "productOverview",
         "source": "ai", "cutType": "product", "direction": direction,
         "sectionId": "product-row", "sectionLayout": "twoColumn", "layoutRowId": "row-product"}
        for direction in ("front", "back")
    ]
    product_block = assemble(product_row, [], [], PRODUCT, False)[0]
    labels = [element for element in product_block["elements"] if element.get("copyRole") == "label"]
    assert [label["text"] for label in labels] == ["FRONT", "BACK"]
    assert [(label["x"], label["y"], label["w"], label["h"]) for label in labels] == [
        (60, 566, 430, 42), (510, 566, 430, 42),
    ]
    assert product_block["h"] == 658

    fit_row = [
        {"id": f"fit-{index}", "sectionRole": "studio", "contentRole": "fit",
         "source": "ai", "cutType": "horizon", "direction": "front",
         "sectionId": "fit-row", "sectionLayout": "threeColumn", "layoutRowId": "row-fit"}
        for index in range(3)
    ]
    fit_block = assemble(fit_row, [], [], PRODUCT, False)[0]
    fit_labels = [element for element in fit_block["elements"] if element.get("copyRole") == "label"]
    assert [label["text"] for label in fit_labels] == ["", "", ""]
    assert all(label["placeholder"] == "짧은 라벨 (선택)" for label in fit_labels)
    assert all(label["style"] == {
        "size": 30, "weight": 600, "color": "#0e0d14", "align": "center", "lineHeight": 42,
    } for label in fit_labels)
    assert all(label["h"] == 42 for label in fit_labels)


# ── 영속 행 조립 + 오프닝 카피 ───────────────────────────────────────────────
def test_four_cut_grid_merges_into_one_block_with_adjacent_images():
    """2×2 격자는 사진 넷이 딱 붙은 한 덩어리(오너 2026-08-16) — 간격 0·모서리 각짐.

    카피 자리는 다른 행과 같은 규칙(사진 아래)이다. 격자만 카피를 위로 올려 자리를 비워
    뒀더니 에디터가 사진 행의 카피를 걷어내는 규칙(stripPhotoBlockTextElements)과 맞물려
    격자 위에 빈 띠만 남았다 — 2026-08-16 리뷰에서 실측하고 되돌렸다."""
    storyboard = [
        {
            "id": f"hook-{index}", "sectionRole": "benefit",
            "contentRole": "hero" if index == 1 else "benefit",
            "source": "ai", "cutType": "styling", "shot": "medium",
            "sectionId": "hook", "sectionLayout": "grid2x2", "layoutRowId": "row-hook",
        }
        for index in range(1, 5)
    ]
    cut_results = [
        {"blockId": f"hook-{index}", "imageUrl": f"https://cdn.example.com/{index}.png"}
        for index in range(1, 5)
    ]
    copy_results = [
        {"blockId": "hook-1", "texts": [{"role": "headline", "text": "겨울을 부드럽게"}]},
        {"blockId": "hook-2", "texts": [{"role": "body", "text": "골지 짜임의 포인트"}]},
    ]

    blocks = assemble(storyboard, cut_results, copy_results, PRODUCT, True)

    assert len(blocks) == 4  # 격자 한 덩어리 1 + 자동 블록 3 — 두 블록으로 쪼개지지 않는다
    grid = blocks[0]
    assert grid["kind"] == "grid2x2"
    images = [element for element in grid["elements"] if element["type"] == "image"]
    # 사진 시작 y 는 어느 배치든 50. 칸끼리 간격 0, 모서리도 각지게(radius 0).
    assert [(image["x"], image["y"], image["w"], image["h"]) for image in images] == [
        (60, 50, 440, 560),
        (500, 50, 440, 560),
        (60, 610, 440, 560),
        (500, 610, 440, 560),
    ]
    assert all(image["radius"] == 0 for image in images)

    captions = [element for element in grid["elements"] if element.get("copyRole") == "headline"]
    assert len(captions) == 1
    headline = captions[0]
    assert (headline["x"], headline["y"], headline["w"], headline["h"]) == (60, 1202, 880, 60)
    assert headline["text"] == "겨울을 부드럽게"
    assert headline["style"] == {
        "size": 44, "weight": 600, "color": "#0e0d14", "lineHeight": 60,
    }
    assert grid["h"] == 1312


def test_two_column_opening_row_merges_and_keeps_copy_below_images():
    storyboard = [
        {
            "id": "opening-style", "sectionRole": "benefit", "contentRole": "hero",
            "source": "ai", "cutType": "styling", "shot": "medium",
            "sectionId": "opening", "sectionLayout": "twoColumn", "layoutRowId": "row-opening",
        },
        {
            "id": "opening-horizon", "sectionRole": "benefit", "contentRole": "benefit",
            "source": "ai", "cutType": "horizon", "shot": "medium",
            "sectionId": "opening", "sectionLayout": "twoColumn", "layoutRowId": "row-opening",
        },
    ]
    cut_results = [
        {"blockId": "opening-style", "imageUrl": "https://cdn.example.com/style.png"},
        {"blockId": "opening-horizon", "imageUrl": "https://cdn.example.com/horizon.png"},
    ]
    copy_results = [
        {"blockId": "opening-style", "texts": [{"role": "headline", "text": "겨울을 부드럽게"}]},
        {"blockId": "opening-horizon", "texts": [{"role": "body", "text": "골지 짜임의 포인트"}]},
    ]

    blocks = assemble(storyboard, cut_results, copy_results, PRODUCT, True)

    assert len(blocks) == 4  # 오프닝 행 1 + 자동 블록 3
    opening = blocks[0]
    assert opening["name"] == "2단 구성"
    assert opening["kind"] == "twocol"
    images = [element for element in opening["elements"] if element["type"] == "image"]
    assert [(image["x"], image["y"], image["w"], image["h"]) for image in images] == [
        (60, 50, 430, 500),
        (510, 50, 430, 500),
    ]
    assert [image["cutType"] for image in images] == ["styling", "horizon"]

    captions = [element for element in opening["elements"] if element.get("copyRole") == "headline"]
    assert len(captions) == 1
    headline = captions[0]
    assert (headline["x"], headline["y"], headline["w"], headline["h"]) == (60, 582, 880, 60)
    assert headline["text"] == "겨울을 부드럽게"
    assert headline["style"] == {
        "size": 44, "weight": 600, "color": "#0e0d14", "lineHeight": 60,
    }
    assert headline["y"] >= max(image["y"] + image["h"] for image in images)
    assert opening["h"] == 692


def _colorway_pair(match_ids=None):
    shared = {
        "sectionRole": "studio", "contentRole": "fit", "source": "ai",
        "cutType": "horizon", "direction": "front", "colorId": "ivory",
        "matchIds": match_ids if match_ids is not None else ["match-dark"],
        "sectionLayout": "twoColumn", "layoutRowId": "row-colorway-ivory",
        "layoutRowVersion": 1, "colorwayGroupId": "colorway-ivory",
        "colorwayPairVersion": 1,
    }
    # 입력 순서가 뒤집혀도 조립 결과는 풀샷 왼쪽·미디움샷 오른쪽이어야 한다.
    return [
        {**shared, "id": "ivory-medium", "shot": "medium"},
        {**shared, "id": "ivory-full", "shot": "full"},
    ]


def _colorway_product():
    return {
        **PRODUCT,
        "colors": [{"id": "ivory", "name": "", "swatchId": "ivory"}],
        "_matchClothing": [{
            "id": "match-dark", "name": "세미 와이드 치노 팬츠", "colorName": "블랙",
        }],
    }


def test_extended_colorway_pair_assembles_full_left_medium_right_with_labels():
    storyboard = _colorway_pair()
    cut_results = [
        {"blockId": "ivory-medium", "imageUrl": "medium.png", "width": 860, "height": 1290},
        {"blockId": "ivory-full", "imageUrl": "full.png", "width": 860, "height": 1290},
    ]

    blocks = assemble(storyboard, cut_results, [], _colorway_product(), False)

    assert len(blocks) == 4  # 컬러웨이 1블록 + 자동 블록 3
    colorway = blocks[0]
    assert colorway["kind"] == "twocol"
    assert colorway["layoutType"] == "colorwayPair"
    assert colorway["name"] == "컬러 룩 · 아이보리"
    images = [element for element in colorway["elements"] if element["type"] == "image"]
    assert [
        (image["src"], image["x"], image["y"], image["w"], image["h"], image["radius"])
        for image in images
    ] == [
        ("full.png", 60, 24, 430, 645, 0),
        ("medium.png", 510, 24, 430, 645, 0),
    ]
    assert [image["sourceBlockId"] for image in images] == ["ivory-full", "ivory-medium"]
    labels = [element for element in colorway["elements"] if element["type"] == "text"]
    assert [label["text"] for label in labels] == [
        "소프트 골지 라운드 니트 [아이보리]",
        "세미 와이드 치노 팬츠 [블랙]",
    ]
    assert labels[0]["style"]["align"] == labels[1]["style"]["align"] == "center"
    assert labels[1]["style"]["weight"] == 700
    assert colorway["h"] == 781


def test_colorway_pair_falls_back_to_separate_blocks_when_matching_garments_diverge():
    first, second = _colorway_pair()
    second = {**second, "matchIds": ["another-match"]}
    blocks = assemble([first, second], [], [], _colorway_product(), False)

    assert len(blocks) == 5  # 개별 컷 2 + 자동 블록 3
    assert all(block.get("layoutType") != "colorwayPair" for block in blocks[:2])


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
