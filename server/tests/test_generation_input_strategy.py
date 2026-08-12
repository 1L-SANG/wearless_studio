"""입력 전략 라우터 — 셔츠만 AUGMENTED, 나머지는 전부 RAW.

근거(2026-08-11, 6개 상품 A/B, arm 당 생성 1회): 구조적 셔츠 2/3 은 canonical Front 를
추가로 받았을 때 스트라이프의 두 번째 색이 살아났고 새 결함은 없었다. 드레이프 의존 상의는
반대로 canonical 이 넥라인을 바꾸거나(4ff2132f: 라운드→스퀘어) 색을 밀었다(레이스탑).

여기서 잠그는 것은 그 규칙이 아니라 **틀렸을 때 어디로 떨어지는가** 다. RAW 는 오늘의
프로덕션 경로이므로, 분류가 애매하거나 canonical 이 없거나 조회가 터지면 전부 RAW 다.
"""
import pytest

from app.services import generation_input_strategy as gis


def truth(*, status="approved", flags=(), subcategory=None) -> dict:
    return {"status": status,
            "garmentSpec": {"structureFlags": list(flags), "subcategory": subcategory}}


# ── 분류 ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["스트라이프 셔츠", "옥스포드 셔츠", "denim shirt", "오버셔츠"])
def test_shirt_names_classify_as_shirt(name):
    assert gis.classify_garment(truth(), {"name": name}) == gis.CATEGORY_SHIRT


@pytest.mark.parametrize("name", [
    "소프트 골지 블라우스 레드", "타이 핀턱 셔링 블라우스", "여성 아일렛 퍼프 스퀘어넥 티셔츠",
    "레이스 시스루 탑", "peplum blouse",
])
def test_drape_sensitive_names_classify_as_blouse(name):
    assert gis.classify_garment(truth(), {"name": name}) == gis.CATEGORY_BLOUSE


def test_blouse_vocabulary_wins_over_shirt_vocabulary():
    """'셔링 블라우스' 는 셔츠가 아니다. 둘 다 걸리면 안전한 쪽(RAW)로 간다."""
    assert gis.classify_garment(truth(), {"name": "셔링 블라우스 셔츠"}) == gis.CATEGORY_BLOUSE


def test_collar_and_buttons_together_classify_as_shirt():
    assert gis.classify_garment(truth(flags=["COLLAR", "BUTTONS"]), {"name": "무지 상의"}) \
        == gis.CATEGORY_SHIRT


def test_collar_alone_is_not_a_shirt():
    """칼라만 있으면 폴로거나 칼라 달린 블라우스다."""
    assert gis.classify_garment(truth(flags=["COLLAR"]), {"name": "상의"}) == gis.CATEGORY_UNKNOWN


def test_buttons_alone_is_not_a_shirt():
    assert gis.classify_garment(truth(flags=["BUTTONS"]), {"name": "상의"}) == gis.CATEGORY_UNKNOWN


def test_unapproved_truth_structure_is_not_trusted():
    """draft 는 아무도 확인하지 않은 값이다. 이 판단은 생성 입력을 바꾸므로 추측하지 않는다."""
    assert gis.classify_garment(truth(status="draft", flags=["COLLAR", "BUTTONS"]),
                                {"name": "상의"}) == gis.CATEGORY_UNKNOWN


def test_no_truth_at_all_is_unknown():
    assert gis.classify_garment(None, None) == gis.CATEGORY_UNKNOWN


# ── 라우팅 ───────────────────────────────────────────────────────────────────

def test_shirt_with_canonical_routes_to_augmented():
    out = gis.resolve(truth(flags=["COLLAR", "BUTTONS"]), {"name": "스트라이프 셔츠"},
                      canonical_available=True)
    assert out.strategy == gis.STRATEGY_AUGMENTED
    assert out.use_canonical is True


def test_blouse_routes_to_raw_even_with_canonical():
    out = gis.resolve(truth(), {"name": "소프트 골지 블라우스 레드"}, canonical_available=True)
    assert out.strategy == gis.STRATEGY_RAW
    assert out.use_canonical is False


def test_uncertain_category_routes_to_raw():
    out = gis.resolve(truth(), {"name": "이름 없는 상의"}, canonical_available=True)
    assert (out.strategy, out.category) == (gis.STRATEGY_RAW, gis.CATEGORY_UNKNOWN)


def test_shirt_without_canonical_routes_to_raw():
    out = gis.resolve(truth(flags=["COLLAR", "BUTTONS"]), {"name": "셔츠"},
                      canonical_available=False)
    assert out.strategy == gis.STRATEGY_RAW
    assert "no usable canonical" in out.reason


def test_strategy_is_recorded_for_the_job_event():
    event = gis.resolve(truth(), {"name": "블라우스"}).as_event()
    assert event["strategy"] == gis.STRATEGY_RAW
    assert event["category"] == gis.CATEGORY_BLOUSE
    assert event["version"] == gis.VERSION


def test_canonical_manifest_line_names_no_defect():
    """추가 증거지 교정 지시가 아니다 — 결함을 지목하면 다른 실험이 된다."""
    line = gis.CANONICAL_MANIFEST_LINE.lower()
    assert "background removed" in line
    for word in ("fix", "correct", "restore", "wrong", "missing", "improve"):
        assert word not in line


# ── Product Truth 는 어느 경로에서도 유지된다 ────────────────────────────────

def test_product_truth_is_never_replaced_by_the_canonical():
    """canonical 은 **추가** 증거다. 어느 경로도 원본 참조를 대체하지 않는다.

    (canonical 조회 자체의 실패 내성 — 로더 없음·예외·빈 바이트 — 은 이제 실제 배선이 있는
    `test_canonical_pipeline.py` 와 `test_sam_fallback.py` 가 담당한다.)
    """
    from app.workers.mannequin_job import _SLOT_LABEL
    # 매니페스트에서 canonical 은 Front/Back 원본과 **별도 슬롯**으로만 등장한다
    assert "CanonicalFront" in _SLOT_LABEL
    assert _SLOT_LABEL["Front"] != _SLOT_LABEL["CanonicalFront"]
    for slot in ("Front", "Back", "Detail", "BackDetail"):
        assert slot in _SLOT_LABEL
