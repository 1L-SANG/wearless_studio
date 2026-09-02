"""신분증 대조 로직(E13) 단위 테스트 — cx_identity.compare_identity_claim.

보안·정확성 핵심: 지원서 이름·생년월일이 신분증과 일치하는지 판정. raw 값은 함수 밖으로
나가지 않고 match 결과만 반환한다(4A 계약). year-only 엣지·라틴 정규화·불일치 경로를 고정한다.
"""

from datetime import date

from app import cx_identity

CONTRACT = cx_identity.DEV_MOCK_OACX_BIOMETRIC_CONTRACT


def _cmp(trans, name, birth):
    return cx_identity.compare_identity_claim(
        trans, contract=CONTRACT, expected_name=name, expected_birthdate=birth
    )


def test_exact_match_full_birthdate():
    m = _cmp({"name": "노정운", "birth": "20040722"}, "노정운", date(2004, 7, 22))
    assert m.matched and m.name_matched and m.birth_precision == "full"


def test_latin_case_and_whitespace_insensitive():
    # 'KIM MIN SU' == 'KimMinsu' (설계 명세 예시).
    m = _cmp({"name": "KIM MIN SU", "birth": "19900101"}, "KimMinsu", date(1990, 1, 1))
    assert m.matched
    m2 = _cmp({"name": "kim min su", "birth": "19900101"}, "KIM MINSU", date(1990, 1, 1))
    assert m2.matched


def test_year_only_passes_on_name_and_year():
    # OACX 가 생년도만 반환하는 실존 엣지 — 이름 일치 + 연도 일치면 통과(약한 보장).
    m = _cmp({"name": "홍길동", "birth": "1990"}, "홍길동", date(1990, 5, 5))
    assert m.matched and m.birth_precision == "year"


def test_year_only_rejects_on_year_mismatch():
    m = _cmp({"name": "홍길동", "birth": "1990"}, "홍길동", date(1991, 5, 5))
    assert not m.matched and m.name_matched and m.birth_precision == "year"


def test_year_only_rejects_on_name_mismatch():
    m = _cmp({"name": "홍길동", "birth": "1990"}, "이영희", date(1990, 5, 5))
    assert not m.matched and not m.name_matched


def test_name_mismatch():
    m = _cmp({"name": "김철수", "birth": "19900101"}, "이영희", date(1990, 1, 1))
    assert not m.matched and not m.name_matched


def test_day_mismatch():
    m = _cmp({"name": "홍길동", "birth": "19900101"}, "홍길동", date(1990, 1, 2))
    assert not m.matched and m.name_matched and m.birth_precision == "full"


def test_month_mismatch():
    m = _cmp({"name": "홍길동", "birth": "19900101"}, "홍길동", date(1990, 2, 1))
    assert not m.matched


def test_hyphenated_birth_normalizes():
    m = _cmp({"name": "홍길동", "birth": "1990-01-01"}, "홍길동", date(1990, 1, 1))
    assert m.matched and m.birth_precision == "full"


def test_missing_name_is_not_match():
    m = _cmp({"birth": "19900101"}, "홍길동", date(1990, 1, 1))
    assert not m.matched


def test_garbage_birth_is_not_match():
    m = _cmp({"name": "홍길동", "birth": "abc"}, "홍길동", date(1990, 1, 1))
    assert not m.matched and m.birth_precision == "none"


def test_nested_result_scope():
    # 실 OACX 는 result/data 아래 중첩 — dig 가 흡수하는지.
    m = _cmp({"result": {"name": "홍길동", "birth": "19900101"}}, "홍길동", date(1990, 1, 1))
    assert m.matched
