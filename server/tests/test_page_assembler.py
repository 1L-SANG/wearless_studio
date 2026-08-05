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
        {"id": "blk1", "sectionRole": "benefit", "contentRole": "hero", "source": "ai", "cutType": "styling", "colorId": "col1"},
        {"id": "blk2", "sectionRole": "benefit", "contentRole": "benefit", "source": "ai", "cutType": "horizon", "colorId": "col1"},
        {"id": "blk3", "sectionRole": "fit", "contentRole": "coordination", "source": "ai", "cutType": "styling", "colorId": "col1"},
    ]


# ── ai 블록 → image 엘리먼트 ────────────────────────────────────────────────
def test_ai_block_maps_to_image_element_with_cut_result_url():
    storyboard = [_storyboard()[2]]  # styling 블록 하나
    cut_results = [{"blockId": "blk3", "imageUrl": "https://cdn.example.com/blk3.png"}]
    blocks = assemble(storyboard, cut_results, [], PRODUCT, False)

    block = blocks[0]
    assert block["kind"] == "fit"
    assert block["contentRole"] == "coordination"
    assert block["name"] == "코디 활용"
    # dims 미제공 → 기본 세로비(2:3) 폴백. 블록은 이미지+여백을 담는다.
    assert block["h"] == 1420
    img = block["elements"][0]
    assert img["type"] == "image"
    assert img["src"] == "https://cdn.example.com/blk3.png"
    assert img["cutType"] == "styling"
    assert img["radius"] == 12
    assert img["x"] == 60 and img["y"] == 50 and img["w"] == 880 and img["h"] == 1320


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


def test_body_copy_sits_near_image_bottom_inside_block():
    # 이미지가 커지면 body 카피도 따라 내려가야 한다(하드코딩 y=560 이면 이미지 한가운데 박힘).
    storyboard = [_storyboard()[2]]
    cut_results = [{"blockId": "blk3", "imageUrl": "u", "width": 848, "height": 1264}]
    copy_results = [{"blockId": "blk3", "texts": [{"role": "body", "text": "부드러운 촉감"}]}]
    block = assemble(storyboard, cut_results, copy_results, PRODUCT, True)[0]
    img = block["elements"][0]
    body = next(el for el in block["elements"] if el["type"] == "text")
    assert img["y"] + img["h"] - 100 <= body["y"] <= img["y"] + img["h"]  # 이미지 하단 근처
    assert body["y"] + body["h"] <= block["h"]                            # 블록 안


# ── source='mine' 블록 ───────────────────────────────────────────────────────
def test_mine_block_uses_own_images_not_cut_results():
    storyboard = [{"id": "blk9", "sectionRole": "fit", "source": "mine", "ownImages": ["https://cdn.example.com/own.png"]}]
    blocks = assemble(storyboard, [], [], PRODUCT, False)

    block = blocks[0]
    assert block["name"] == "내 이미지"
    assert block["kind"] == "fit"
    assert block["contentRole"] == "custom"
    assert len(block["elements"]) == 1
    assert block["elements"][0]["src"] == "https://cdn.example.com/own.png"


def test_mine_block_with_no_own_images_has_no_elements():
    storyboard = [{"id": "blk9", "sectionRole": "fit", "source": "mine", "ownImages": []}]
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


def test_cut_type_inference_maps_mirror_to_real_wear():
    storyboard = [{"id": "mirror", "source": "ai", "cutType": "mirror"}]
    block = assemble(storyboard, [], [], PRODUCT, False)[0]
    assert block["kind"] == "fit"
    assert block["contentRole"] == "realWear"
    assert block["name"] == "실제 착용 느낌"


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

    얼굴 레퍼런스는 얼굴이 식별되는 컷에만 붙는다(거울샷·뒷모습·하반신 제외 — cut_generator
    ._face_fits). 그 제외 컷에도 인물은 렌더되지만 **AI 가 지어낸 가상인물**이라 26.06 표기
    의무 대상이다. 페이지 전체 고지를 뒤집으면 표기 의무 대상 컷에 반대 표기가 붙는다(허위표시).
    거울샷 얼굴 기본값이 hide 라 이 경로는 **기본 설정으로 도달 가능**하다.
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
def test_copywriting_on_places_headline_and_selling_text():
    storyboard = _storyboard()
    cut_results = [{"blockId": b["id"], "imageUrl": f"https://cdn.example.com/{b['id']}.png"} for b in storyboard]
    copy_results = [
        {"blockId": "blk1", "texts": [{"role": "headline", "text": "겨울을 부드럽게"}]},
        {"blockId": "blk2", "texts": [{"role": "body", "text": "강조 포인트를 살린 카피"}]},
    ]
    blocks = assemble(storyboard, cut_results, copy_results, PRODUCT, True)

    hero_block, benefit_block, coordination_block = blocks[0], blocks[1], blocks[2]
    assert len(hero_block["elements"]) == 2
    assert hero_block["elements"][1]["type"] == "text"
    assert hero_block["elements"][1]["text"] == "겨울을 부드럽게"

    assert len(benefit_block["elements"]) == 2
    assert benefit_block["elements"][1]["text"] == "강조 포인트를 살린 카피"

    # 카피 결과가 없으면 coordination 블록은 이미지만 남는다.
    assert len(coordination_block["elements"]) == 1


def test_copywriting_places_body_for_non_hero_content_roles():
    storyboard = [{
        "id": "fit-1", "sectionRole": "fit", "contentRole": "fit",
        "source": "ai", "cutType": "horizon",
    }]
    copy_results = [{"blockId": "fit-1", "texts": [{"role": "body", "text": "실루엣을 확인해보세요"}]}]
    block = assemble(storyboard, [], copy_results, PRODUCT, True)[0]
    assert block["kind"] == "fit"
    assert block["contentRole"] == "fit"
    assert block["elements"][1]["text"] == "실루엣을 확인해보세요"


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
    storyboard = [_storyboard()[0]]  # hero block, no copy_results entry
    blocks = assemble(storyboard, [], [], PRODUCT, True)
    assert len(blocks[0]["elements"]) == 1  # image only, no headline injected


# ── 영속 행 조립 + 오프닝 카피 ───────────────────────────────────────────────
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

    headline, subtitle = [
        element for element in opening["elements"] if element["type"] == "text"
    ]
    assert (headline["x"], headline["y"], headline["w"], headline["h"]) == (60, 582, 880, 56)
    assert headline["text"] == "겨울을 부드럽게"
    assert headline["style"]["size"] == 40 and headline["style"]["weight"] == 600
    assert (subtitle["x"], subtitle["y"], subtitle["w"], subtitle["h"]) == (60, 650, 880, 34)
    assert subtitle["text"] == "골지 짜임의 포인트"
    assert subtitle["style"] == {"size": 18, "color": "#6b6b73"}
    assert headline["y"] >= max(image["y"] + image["h"] for image in images)


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
