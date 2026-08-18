"""로고·텍스트 상품 2K 승급 (2026-08-19 오너 승인).

근거 = 해상도 A/B(캘빈클라인 로고 티, 실프롬프트·실판정): 1K 실컷 0/3 통과(글자 깨짐),
2K 4장 중 3장 통과(역대 첫 pass·글자 판독), 4K 0/2(글자는 읽히나 다른 결함·+80% 비용).
gemini-3-pro-image 는 1K 와 2K 의 출력 토큰이 같아(공식 요금표 1,120) **승급 비용이 0**이다.

감지는 has_fine_pattern 과 같은 방식(이름·특징 텍스트) — 과탐(로고 없는데 2K)은 비용이
0이므로 넓게 잡는 쪽이 맞고, 미탐(로고인데 1K)만 실손해(글자 깨진 컷 출고)다.
"""

from types import SimpleNamespace

from app.agents import mannequin
from app.workers.mannequin_job import effective_image_size


# ---------------------------------------------------------------- 감지 (순수)


def test_has_logo_text_detects_korean_and_english_signals():
    assert mannequin.has_logo_text({"name": "캘빈클라인 가슴 로고 반팔 티셔츠"}, {})
    assert mannequin.has_logo_text({}, {"sellingPoints": ["가슴 레터링 로고"]})
    assert mannequin.has_logo_text({}, {"aiSuggestedPoints": ["브랜드 프린트"]})
    assert mannequin.has_logo_text({}, {"sellingPoints": ["백 그래픽 자수"]})
    assert mannequin.has_logo_text({"name": "graphic logo tee"}, {})
    assert mannequin.has_logo_text({}, {"suggestedName": "lettering print shirt"})


def test_has_logo_text_ignores_plain_products():
    assert not mannequin.has_logo_text({"name": "베이직 무지 반팔 티셔츠"},
                                       {"styleTags": ["basic", "minimal"]})
    assert not mannequin.has_logo_text(None, None)
    assert not mannequin.has_logo_text({}, {})


def test_has_logo_text_ignores_generic_marketing_words():
    """리뷰 지적(8/19): 흔한 단어로 과탐하면 사실상 전 상품 스위치가 된다.

    'text'⊂'textured' 같은 부분일치와, 셀러 문구에 늘 나오는 브랜드/라벨류는
    글자 재현과 무관하므로 트리거가 아니어야 한다. (진짜 로고 상품은 로고·레터링·
    프린트 같은 구체 토큰으로 잡힌다 — CK 실사례의 강조특징도 '가슴 레터링 로고'.)
    """
    assert not mannequin.has_logo_text({}, {"styleTags": ["textured knit"]})
    assert not mannequin.has_logo_text({}, {"sellingPoints": ["케어라벨 표기"]})
    assert not mannequin.has_logo_text({}, {"sellingPoints": ["인기 브랜드 감성"]})
    assert not mannequin.has_logo_text({"name": "context aware fit tee"}, {})


# ---------------------------------------------------------------- 해상도 결정 (순수)


def _s(base="1K", pattern="off", logo="2K"):
    return SimpleNamespace(
        mannequin_image_size=base,
        mannequin_pattern_image_size=pattern,
        mannequin_logo_image_size=logo,
    )


_LOGO = ({"name": "로고 티"}, {"sellingPoints": ["가슴 레터링 로고"]})
_PLAIN = ({"name": "무지 티"}, {"styleTags": ["basic"]})
_PATTERN_LOGO = ({"name": "스트라이프 로고 셔츠"}, {"sellingPoints": ["멀티 스트라이프", "가슴 로고"]})


def test_logo_product_upgrades_to_2k():
    assert effective_image_size(_s(), *_LOGO) == "2K"


def test_plain_product_stays_at_base_size():
    assert effective_image_size(_s(), *_PLAIN) == "1K"


def test_fine_pattern_wins_over_logo():
    """패턴 승급(4K)이 로고 승급(2K)의 상위 호환 — 둘 다면 4K."""
    assert effective_image_size(_s(pattern="4K"), *_PATTERN_LOGO) == "4K"


def test_logo_upgrade_survives_lowered_pattern_size():
    """리뷰 지적(8/19): 패턴 분기가 무조건 return 하면, 운영자가 패턴 크기를 1K 로
    내렸을 때 패턴+로고 상품의 로고 승급이 평가조차 안 된다 — 적용 가능한 승급 중
    최댓값을 골라야 한다."""
    assert effective_image_size(_s(pattern="1K"), *_PATTERN_LOGO) == "2K"
    assert effective_image_size(_s(pattern="2K"), *_PATTERN_LOGO) == "2K"


def test_logo_upgrade_never_downgrades_base():
    """기본이 이미 더 크면 승급이 깎아내리면 안 된다."""
    assert effective_image_size(_s(base="4K"), *_LOGO) == "4K"


def test_logo_upgrade_off_restores_current_behavior():
    assert effective_image_size(_s(logo="OFF"), *_LOGO) == "1K"
    assert effective_image_size(_s(logo=""), *_LOGO) == "1K"


# ---------------------------------------------------------------- config 배선


def test_logo_image_size_flag_wiring(monkeypatch):
    from app.config import Settings, load_settings
    assert Settings.__dataclass_fields__["mannequin_logo_image_size"].default == "2K"

    monkeypatch.delenv("MANNEQUIN_LOGO_IMAGE_SIZE", raising=False)
    assert load_settings().mannequin_logo_image_size == "2K"

    monkeypatch.setenv("MANNEQUIN_LOGO_IMAGE_SIZE", "off")
    assert load_settings().mannequin_logo_image_size == "OFF"

    monkeypatch.setenv("MANNEQUIN_LOGO_IMAGE_SIZE", "banana")
    assert load_settings().mannequin_logo_image_size == "2K", "모르는 값은 기본 2K"
