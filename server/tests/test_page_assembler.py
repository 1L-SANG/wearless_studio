from storyboard_v4_cases import v4_blocks
"""M-02 page-assembler 테스트 — mock buildEditorBlocksFromStoryboard 포팅 검증
(블록 매핑, 자동 블록, 카피라이팅 on/off, 빈 슬롯 폴백, 결정적 id)."""

from app.agents.page_assembler import assemble, build_auto_blocks
import pytest

PRODUCT = {
    "name": "소프트 골지 라운드 니트",
    "measurements": [
        {"key": "totalLength", "value": 64, "unit": "cm"},
        {"key": "shoulderWidth", "value": 42, "unit": "cm"},
        {"key": "chestWidth", "value": 51, "unit": "cm"},
        {"key": "sleeveLength", "value": None, "unit": "cm"},
    ],
}


def _ghost_storyboard(direction="front", color_id="navy"):
    return [
        {"id": "hero", "sectionRole": "hooking", "contentRole": "hero", "source": "ai", "cutType": "styling"},
        {"id": "ghost", "sectionRole": "product", "contentRole": "productOverview", "source": "ai", "cutType": "product", "direction": direction, "colorId": color_id},
    ]












def _storyboard():
    return [
        {"id": "blk1", "sectionRole": "hooking", "contentRole": "hero", "source": "ai", "cutType": "styling", "colorId": "col1"},
        {"id": "blk2", "sectionRole": "hooking", "contentRole": "benefit", "source": "ai", "cutType": "horizon", "colorId": "col1"},
        {"id": "blk3", "sectionRole": "styling", "contentRole": "coordination", "source": "ai", "cutType": "styling", "colorId": "col1"},
    ]


# ── ai 블록 → image 엘리먼트 ────────────────────────────────────────────────


# ── 이미지 비율 보존 (WS2: 에디터·산출물 크롭 회귀 방지) ──────────────────────
def _one_ai_block(cut_result: dict):
    block = v4_blocks([_storyboard()[2]])[0]
    # An unbound manual photo is appended using the design overflow's source aspect.
    block["slotKey"] = None
    return next(b for b in assemble([block], [cut_result], [], PRODUCT, False) if any(el["type"] == "image" for el in b["elements"]))


def test_image_element_keeps_source_aspect_ratio():
    # 실제 AG-06 컷(848×1264 = 2:3)을 주면 요소도 같은 비율이어야 한다.
    # 구현 전에는 880×560(가로)로 고정돼 object-fit:cover 가 높이를 ~58% 잘라먹었다.
    block = _one_ai_block({"blockId": "blk3", "imageUrl": "u", "width": 848, "height": 1264})
    img = next(el for el in block["elements"] if el["type"] == "image")
    assert (img["w"], img["h"]) == (888, 1324)
    assert abs(img["w"] / img["h"] - 848 / 1264) < 0.002


def test_image_element_follows_non_default_ratio():
    # 하드코딩이 아님을 증명 — 3:4 소스면 요소도 3:4.
    block = _one_ai_block({"blockId": "blk3", "imageUrl": "u", "width": 900, "height": 1200})
    img = next(el for el in block["elements"] if el["type"] == "image")
    assert (img["w"], img["h"]) == (888, 1184)
    assert abs(img["w"] / img["h"] - 0.75) < 0.002


def test_image_box_falls_back_when_dims_missing_or_invalid():
    # 구 데이터·측정 실패(None/0/문자열) → 2:3 폴백, 크래시 없음
    for bad in ({}, {"width": None, "height": None}, {"width": 0, "height": 0},
                {"width": "x", "height": "y"}):
        img = next(el for el in _one_ai_block({"blockId": "blk3", "imageUrl": "u", **bad})["elements"] if el["type"] == "image")
        assert (img["w"], img["h"]) == (888, 1332)


def test_block_contains_image_without_overflow():
    # 요소가 블록 밖으로 넘치면 에디터·산출물에서 잘린다 — 블록 h 가 항상 이미지를 담아야 한다.
    for dims in ({"width": 848, "height": 1264}, {"width": 900, "height": 1200},
                 {"width": 1200, "height": 900}, {}):
        block = _one_ai_block({"blockId": "blk3", "imageUrl": "u", **dims})
        img = next(el for el in block["elements"] if el["type"] == "image")
        assert block["h"] >= img["y"] + img["h"], f"{dims} 에서 이미지가 블록을 넘침"


def test_absurd_dims_are_clamped_and_block_stays_sane():
    # 파손·조작 dims(10000×1, 1×10000)가 레이아웃을 무너뜨리면 안 된다 (codex 리뷰 #4).
    flat = _one_ai_block({"blockId": "blk3", "imageUrl": "u", "width": 10000, "height": 1})
    tall = _one_ai_block({"blockId": "blk3", "imageUrl": "u", "width": 1, "height": 10000})
    assert 200 <= next(el for el in flat["elements"] if el["type"] == "image")["h"] <= 2728
    assert 200 <= next(el for el in tall["elements"] if el["type"] == "image")["h"] <= 2728
    assert flat["h"] < 3300 and tall["h"] < 3300








# ── source='mine' 블록 ───────────────────────────────────────────────────────








# ── 자동 블록 (size/care/ai-notice) ──────────────────────────────────────────


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




# ── copywriting on/off ───────────────────────────────────────────────────────
















# ── 영속 행 조립 + 오프닝 카피 ───────────────────────────────────────────────




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






# ── 빈 슬롯 폴백 (컷 생성 실패) ───────────────────────────────────────────────




# ── 결정적 id ─────────────────────────────────────────────────────────────────


def test_no_uuid_or_random_module_used():
    import inspect

    from app.agents import page_assembler

    src = inspect.getsource(page_assembler)
    assert "import uuid" not in src
    assert "import random" not in src


def test_ai_notice_without_license_keeps_legacy_copy_verbatim():
    expected = (
        "본 상세페이지의 일부 이미지는 AI를 활용해 생성되었습니다. "
        "실제 상품의 색상과 핏은 촬영 환경 및 화면 설정에 따라 다르게 보일 수 있습니다."
    )
    for blocks in (build_auto_blocks(PRODUCT), assemble(v4_blocks(_storyboard()), [], [], PRODUCT, False)):
        notice = next(b for b in blocks if b["kind"] == "ai-notice")
        assert notice["elements"][0]["text"] == expected
        assert notice["elements"][0]["h"] == 60


def test_assemble_threads_license_notice_to_auto_block():
    notice = {"modelName": "박*수", "licenseId": "lic-9", "faceCuts": 2, "totalCuts": 2}
    blocks = assemble(v4_blocks(_storyboard()), [], [], PRODUCT, False, license_notice=notice)
    assert "검증된 실제 모델 박*수 님" in next(b for b in blocks if b["kind"] == "ai-notice")["elements"][0]["text"]


def test_ai_notice_without_license_has_no_badge_qr_or_license_meta():
    for blocks in (build_auto_blocks(PRODUCT), assemble(v4_blocks(_storyboard()), [], [], PRODUCT, False)):
        notice = next(b for b in blocks if b["kind"] == "ai-notice")
        assert len(notice["elements"]) == 1
        assert notice["elements"][0]["type"] == "text"
        assert "licenseId" not in notice


def test_auto_blocks_present_after_storyboard_blocks():
    blocks = assemble(v4_blocks(_storyboard()), [], [], PRODUCT, False)
    assert [b["kind"] for b in blocks[-4:]] == ["size", "care", "ai-notice", "common-notice"]
    assert all(b.get("auto") for b in blocks[-4:])


def test_ids_are_deterministic_across_calls():
    storyboard = v4_blocks(_storyboard())
    cut_results = [{"blockId": b["id"], "imageUrl": "generated:" + b["id"]} for b in storyboard]
    first = assemble(storyboard, cut_results, [], PRODUCT, True)
    assert first == assemble(storyboard, cut_results, [], PRODUCT, True)
    assert len({b["id"] for b in first}) == len(first)
    assert len({e["id"] for b in first for e in b["elements"]}) == sum(len(b["elements"]) for b in first)


def test_mine_block_uses_own_images_not_cut_results():
    storyboard = v4_blocks([{"id": "own", "source": "mine", "ownImages": ["/my-photo.png"]}])
    blocks = assemble(storyboard, [{"blockId": "own", "imageUrl": "/wrong.png"}], [], PRODUCT, False)
    photos = [e for b in blocks for e in b["elements"] if e["type"] == "image" and e.get("src")]
    assert [e["src"] for e in photos] == ["/my-photo.png"]
    assert photos[0]["sourceBlockId"] == "own"
