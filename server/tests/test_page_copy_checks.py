"""Local lexical checks; the small r1 excerpts are intentionally self-contained."""
import re

import pytest

from app.agents import page_copy_checks as checks


# v2-06, r1_all/slots.jsonl. Keep the product sentence, drop whole sales sentences.
R1_SELLER = (
    "이번 주만 1+1 특가!! 재입고 문의 폭주한 그 슬랙스예요. "
    "핀턱이 깊게 잡혀서 다리가 곧게 떨어지고 뒷밴딩이라 허리가 편해요. "
    "인스타 팔로우하면 쿠폰 드려요"
)


def test_seller_cleanup_removes_complete_promotion_sentences_from_r1():
    assert checks.clean_seller_text(R1_SELLER) == (
        "핀턱이 깊게 잡혀서 다리가 곧게 떨어지고 뒷밴딩이라 허리가 편해요."
    )


def test_seller_cleanup_removes_design_directions_and_preserves_product_punctuation():
    text = "소매가 봉긋해서 팔뚝이 안 붙어요. 앞 핀턱 디테일 꼭 강조해 주세요! 사진은 화이트 위주로요"
    assert checks.clean_seller_text(text) == "소매가 봉긋해서 팔뚝이 안 붙어요."
    assert checks.clean_seller_text("폭은 1.5cm예요.\n구김이 자연스러워요!") == "폭은 1.5cm예요.\n구김이 자연스러워요!"


@pytest.mark.parametrize("sentence", [
    "불량 교환은 일주일 안에 연락해 주세요.",
    "https://example.test/item 에서 확인하세요.",
    "카톡 채널로 문의 주세요.",
    "인스타그램에서 디엠 보내 주세요.",
    "썸네일에는 이 문구를 넣어 주세요.",
    "배송비는 무료입니다.",
])
def test_seller_cleanup_drops_policy_external_channels_and_design_instructions(sentence):
    assert checks.clean_seller_text(sentence) == ""


def test_content_words_normalizes_particles_and_common_endings_without_one_letter_tokens():
    words = checks.content_words("골지는 부드러워요. 골지를 부드러운 느낌으로 입을 수 있어요.")
    assert "골지" in words
    assert len(words & checks.content_words("부드러운 골지")) == 2
    assert not {"있어요", "수", "입을", "으로"} & words
    assert all(len(word) >= 2 for word in words)
    assert {"허리", "소매", "사선", "컬러"} <= checks.content_words("허리 소매 사선 컬러")


def test_fabric_restatement_detects_inflections_but_preserves_new_information():
    assert checks.fabric_restatement("부드러운 골지", "골지는 부드러워요.")
    assert checks.fabric_restatement("탄탄하고 도톰한 원단", "도톰하고 탄탄한 원단이에요.")
    assert not checks.fabric_restatement("부드러운 골지", "부드러운 골지에 세로 결이 보여요.")
    assert not checks.fabric_restatement("", "")


def test_faq_overlap_groups_washing_paraphrases_and_catches_reused_answer():
    assert checks.faq_overlap("세탁은 어떻게 하나요?", "찬물에 손세탁해 주세요.",
                              "물빨래해도 될까요?", "세탁기 대신 단독 손세탁을 권해요.")
    assert checks.faq_overlap("사이즈는 어떻게 고르나요?", "상세 치수를 확인해 주세요.",
                              "어깨가 넓어도 되나요?", "상세 치수 확인을 부탁드려요.")
    assert not checks.faq_overlap("비침이 있나요?", "밝은 색은 이너를 함께 입어 주세요.",
                                  "세탁은 어떻게 하나요?", "찬물에 손세탁해 주세요.")
    assert not checks.faq_overlap("", "", "", "")


def test_recommendation_overlap_flags_later_item_and_ignores_shared_generic_ending():
    items = ["주말 산책에 편하게 입을 옷을 찾는 분", "출근할 때 셔츠와 입고 싶은 분",
             "주말 산책에 편하게 입고 싶은 분"]
    assert checks.recommendation_overlap(items) == [(0, 2)]
    assert checks.recommendation_overlap(["셔츠와 겹쳐 입고 싶은 분", "주말 산책에 입고 싶은 분"]) == []


def test_related_detail_overlap_requires_substantial_shared_description():
    assert checks.related_detail_overlap("허리 스트링으로 폭을 조절해요.", "허리 스트링으로 폭을 조절할 수 있어요.")
    assert not checks.related_detail_overlap("허리 스트링으로 폭을 조절해요.", "굵은 스트링 끝에 금속 장식이 보여요.")
    assert not checks.related_detail_overlap("허리", "허리")


def _slots(count=5, section="emo.recommendation"):
    return [{"id": f"S1:items.{i}.text", "sectionKey": section, "kind": "recommendation",
             "role": f"items.{i}.text"} for i in range(count)]


def test_page_repetition_counts_distinct_slots_and_only_marks_later_candidates():
    slots = _slots()
    texts = dict(zip((s["id"] for s in slots), [
        "단정한 단정한 출근 모습", "단정하게 입는 여행", "단정한 색 조합",
        "단정하게 더한 셔츠", "산책에 어울리는 옷",
    ]))
    warnings = checks.page_repetitions(slots, texts, {})
    repeated = [w for w in warnings if w["reason"] == "page_content_repetition"]
    assert [w["slotId"] for w in repeated] == [s["id"] for s in slots[1:4]]
    assert all(w["count"] == 4 for w in repeated)
    assert checks.page_repetitions(slots[:3], texts, {}) == []


def test_page_repetition_excludes_garments_and_fact_names_not_arbitrary_evidence():
    slots = _slots(4)
    texts = {s["id"]: f"슬랙스에 아이보리 핀턱 단정한 조합 {i}" for i, s in enumerate(slots)}
    facts = {"garment": "슬랙스", "colors": ["아이보리"], "parts": [{"name": "핀턱", "evidence": "단정한 느낌"}]}
    warnings = checks.page_repetitions(slots, texts, facts)
    words = {w.get("word") for w in warnings if w["reason"] == "page_content_repetition"}
    assert "단정" in words
    assert not {"슬랙스", "아이보리", "핀턱"} & words


def test_section_frame_warns_only_for_majority_of_distinct_items():
    slots = _slots(3)
    texts = dict(zip((s["id"] for s in slots), [
        "주말에 입기 좋아요.", "출근에 입기 좋아요!", "셔츠와 매치해 보세요.",
    ]))
    warnings = checks.page_repetitions(slots, texts, {})
    frame = [w for w in warnings if w["reason"] == "section_sentence_frame"]
    assert [w["slotId"] for w in frame] == [slots[1]["id"]]
    assert frame[0]["frame"] == "입기 좋아요"
    assert not checks.page_repetitions(slots[:1], texts, {})
    # Two roles of the same item cannot manufacture a section-level majority.
    same_item = [dict(slots[0]), {**slots[0], "id": "S1:items.0.body", "role": "items.0.body"}]
    assert not checks.page_repetitions(same_item, {s["id"]: "주말에 입기 좋아요" for s in same_item}, {})


def test_section_frame_counts_short_items_in_majority_denominator():
    slots = _slots(4)
    texts = dict(zip((s["id"] for s in slots), ["주말에 입기 좋아요", "출근에 입기 좋아요", "니트", "셔츠"] ))
    assert not checks.page_repetitions(slots, texts, {})


def test_section_frame_counts_items_instead_of_the_section_title():
    slots = _slots(3)
    slots.append({"id": "S1:title", "role": "title", "sectionKey": "emo.recommendation"})
    texts = dict(zip((s["id"] for s in slots), ["주말에 입기 좋아요", "출근에 입기 좋아요", "셔츠와 입어요", "이런 분께 추천해요"]))
    assert [w["slotId"] for w in checks.page_repetitions(slots, texts, {})] == [slots[1]["id"]]


def test_exemplar_copy_uses_normalized_whitespace_and_eight_character_threshold():
    assert checks.exemplar_copy("아주  탄탄한\n원단입니다", ["탄탄한 원단입니다. 모양이 잡혀요"])
    assert checks.exemplar_copy("처음가나다라마바사아끝", ["가나다라마바사아"])
    assert not checks.exemplar_copy("가나다라마바사", ["가나다라마바사"])
    assert not checks.exemplar_copy("", [""])


def test_trade_terms_comes_from_config_and_handles_spacing_variants():
    assert checks.trade_terms("앞판에 입구를 낸 포켓과 뒤  허리의 주름 잡힌 모양") == ["앞판", "뒤 허리", "입구를 낸", "주름 잡힌"]
    assert checks.trade_terms("소매에 단추가 달려 있어요.") == []


def _materials(text):
    return {name for name, pattern in {"면": r"면|코튼", "울": r"울|양모", "린넨": r"린넨|리넨"}.items() if re.search(pattern, text)}


@pytest.mark.parametrize("draft, polished", [
    ("단추가 보여요.", "단추 세 개가 보여요."),
    ("포켓이 있어요.", "포켓이 두 군데 있어요."),
    ("총장이 길어요.", "총장이 90cm예요."),
    ("허리를 감싸요.", "허리를 2단계로 조절해요."),
    ("포켓이 두 개예요.", "포켓이 세 개예요."),
    ("금속 단추예요.", "금속 단추 열두 개예요."),
    ("한 가지 컬러예요.", "세 가지 컬러예요."),
    ("길이가 2cm예요.", "길이가 2mm예요."),
    ("단추가 있어요.", "단추가 십이 개예요."),
    ("단추가 있어요.", "단추가 열 개예요."),
    ("단추가 있어요.", "단추가 하나씩 달려 있어요."),
])
def test_unsupported_additions_rejects_new_arabic_and_korean_quantities(draft, polished):
    assert "number_added" in checks.unsupported_additions(draft, polished, _materials)


@pytest.mark.parametrize("draft, polished", [
    ("단추 두 개", "단추 2개예요."),
    ("총장 2.5cm", "총장이 2.50 cm예요."),
    ("스물두 개의 단추", "단추가 22개예요."),
    ("옆에 단추가 있어요.", "옆에 단추가 달려 있어요."),
    ("단추 한 개", "단추 하나"),
    ("두 개의 포켓", "두개의 포켓"),
    ("단추 12개", "단추 십이 개"),
    ("단추 20개", "단추 스무 개"),
    ("단추 10개", "단추 열 개"),
])
def test_unsupported_additions_accepts_equivalent_quantity_rephrasing(draft, polished):
    assert "number_added" not in checks.unsupported_additions(draft, polished, _materials)


def test_unsupported_additions_material_comparison_uses_caller_canonical_names():
    assert "material_added" in checks.unsupported_additions("골지 원단", "울 골지 원단", _materials)
    assert not checks.unsupported_additions("코튼 원단", "면 원단", _materials)


@pytest.mark.parametrize("polished", [
    "스트링으로 허리를 조절해요.", "포켓에 물건을 수납해요.", "지퍼로 여닫을 수 있어요.",
    "방수 기능이 있어요.", "보온성이 좋아요.", "쭉 늘어나는 원단이에요.",
    "바람을 막아줘요.", "땀을 흡수해요.",
    "늘어짐 없이 입어요.", "쾌적하게 입어요.", "누구나 입기 좋아요.",
])
def test_unsupported_additions_rejects_new_functional_claims(polished):
    assert "claim_added" in checks.unsupported_additions("겉에 디테일이 보여요.", polished, _materials)


def test_unsupported_additions_allows_known_function_rephrasing_but_detects_stance_reversal():
    assert not checks.unsupported_additions("스트링으로 허리 폭 조절이 돼요.", "끈으로 허리 폭을 조절할 수 있어요.", _materials)
    assert not checks.unsupported_additions("포켓에 물건을 넣어요.", "포켓에 물건을 수납해요.", _materials)
    assert "claim_added" in checks.unsupported_additions("방수가 안 돼요.", "방수가 돼요.", _materials)



def test_single_substantive_word_duplicates_are_detected():
    assert checks.recommendation_overlap(['출근하는 분', '출근하는 분']) == [(0,1)]
    assert checks.faq_overlap('어떻게 관리하나요?', '손세탁해요', '물빨래는요?', '손세탁해요')
