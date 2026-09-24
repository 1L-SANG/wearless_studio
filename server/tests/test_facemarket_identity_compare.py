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


# --- 간편인증(simple-auth-v1) 대조, 2026-09-25 운영 사고 ---
# 9/13 토스 실거래로 확인한 간편인증 응답은 생년월일을 `birthday` 에 담는다. 파서
# (parse_simple_auth_evidence)는 그 칸을 읽었지만 대조는 `birth` 만 봐서, 지원서와 똑같이
# 적어도 매번 '불일치'가 났고 세 번째에 지원서가 자동 거절됐다. 대조는 파서와 **같은
# 칸 목록**을 읽어야 한다. 아래 테스트가 그 약속을 고정한다.
import pytest

SIMPLE = cx_identity.SIMPLE_AUTH_CONTRACT

# test_facemarket_identity_method.py 의 _REAL_SIMPLE_AUTH_SHAPE 와 같은 칸 구성(값은 합성).
_REAL_SIMPLE_AUTH = {
    "ci": "SYNTHETIC-CI",
    "name": "홍길동",
    "birthday": "19900101",
    "phone": "01000000000",
    "pid": "cotoss",
    "provider": "cotoss",
}


def _cmp_simple(trans, name, birth):
    return cx_identity.compare_identity_claim(
        trans, contract=SIMPLE, expected_name=name, expected_birthdate=birth
    )


def test_simple_auth_real_shape_matches_same_claim():
    m = _cmp_simple(dict(_REAL_SIMPLE_AUTH), "홍길동", date(1990, 1, 1))
    assert m.matched and m.name_matched and m.birth_precision == "full"


def test_simple_auth_real_shape_still_rejects_wrong_birthdate():
    m = _cmp_simple(dict(_REAL_SIMPLE_AUTH), "홍길동", date(1990, 1, 2))
    assert not m.matched and m.name_matched and m.birth_precision == "full"


def test_simple_auth_real_shape_still_rejects_wrong_name():
    m = _cmp_simple(dict(_REAL_SIMPLE_AUTH), "이영희", date(1990, 1, 1))
    assert not m.matched and not m.name_matched


def test_simple_auth_utf8_name_and_birthdate_keys_match():
    trans = {"ci": "SYNTHETIC-CI", "utf8Nm": "홍길동", "birthdate": "1990-01-01"}
    assert _cmp_simple(trans, "홍길동", date(1990, 1, 1)).matched


def test_simple_auth_name_found_even_when_birth_missing():
    # 생년월일을 못 찾아도 이름은 따로 판정해 둔다(불일치 로그에서 어느 쪽이 문제인지 보려고).
    m = _cmp_simple({"ci": "SYNTHETIC-CI", "name": "홍길동"}, "홍길동", date(1990, 1, 1))
    assert not m.matched and m.name_matched and m.birth_precision == "none"


@pytest.mark.parametrize(
    "trans",
    [
        {"ci": "SYNTHETIC-CI", "name": "홍길동", "birthday": "19900101"},
        {"ci": "SYNTHETIC-CI", "utf8Nm": "홍길동", "birthdate": "19900101"},
        {"ci": "SYNTHETIC-CI", "nm": "홍길동", "birth": "19900101"},
        {"ci": "SYNTHETIC-CI", "result": {"userName": "홍길동", "birthday": "19900101"}},
        {"ci": "SYNTHETIC-CI", "data": {"name": "홍길동", "birth": "1990-01-01"}},
    ],
)
def test_simple_auth_whatever_parser_accepts_compare_reads_the_same(trans):
    """파서가 받아들인 응답이면 대조도 같은 이름·생년월일을 본다(두 함수의 칸 목록 동기화)."""
    evidence = cx_identity.parse_simple_auth_evidence(dict(trans), contract=SIMPLE)
    digits = "".join(ch for ch in evidence.birth if ch.isdigit())
    parsed_birth = date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    m = _cmp_simple(dict(trans), "홍길동", parsed_birth)
    assert m.matched, trans


def test_mid_contract_does_not_start_reading_simple_auth_keys():
    # 모바일 신분증(mid) 계약은 지금처럼 `birth` 만 본다. 이번 수정으로 넓어지지 않는다.
    m = _cmp({"name": "홍길동", "birthday": "19900101"}, "홍길동", date(1990, 1, 1))
    assert not m.matched and m.birth_precision == "none"


def test_name_unicode_composition_is_ignored():
    # 한글 자모 분리형(NFD)과 완성형(NFC)은 같은 이름이다.
    import unicodedata

    nfd = unicodedata.normalize("NFD", "홍길동")
    assert nfd != "홍길동"
    m = _cmp_simple(dict(_REAL_SIMPLE_AUTH, name=nfd), "홍길동", date(1990, 1, 1))
    assert m.matched


def test_field_names_lists_keys_only_never_values():
    names = cx_identity.field_names(
        {"ci": "SECRET-CI", "name": "홍길동", "result": {"birthday": "19900101"}}
    )
    assert names == ["ci", "name", "result", "result.birthday"]
    assert not any(v in " ".join(names) for v in ("SECRET-CI", "홍길동", "19900101"))
