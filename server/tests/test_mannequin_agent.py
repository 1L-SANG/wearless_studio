from app.agents import mannequin


def test_dress_always_uses_women_base_gender():
    assert mannequin.select_base_gender(
        {"targetGenders": ["men"]},
        "dress",
    ) == "women"
    assert mannequin.select_base_gender(
        {"targetGenders": ["men"]},
        "outer",
    ) == "men"


def test_base_gender_follows_the_chip_the_seller_sees():
    """셀러 화면의 '대상 성별'은 단일 선택 칩이고 targetGenders[0] 만 보여준다 — 서버도 그걸 따른다.

    2026-08-01 실측: AI 분석이 ["men","women"] 을 넣어둔 프로젝트에서 화면에는 '남성'이 선택돼
    보이는데 여성 베이스가 나가고 가슴 2패스까지 돌았다(회색 후드·회색 니트). 옛 규칙이
    "전부 남성 토큰일 때만 men" 이라 혼합이면 women 으로 눕혔기 때문이다.
    """
    assert mannequin.select_base_gender({"targetGenders": ["men", "women"]}, "top") == "men"
    assert mannequin.select_base_gender({"targetGenders": ["women", "men"]}, "top") == "women"
    assert mannequin.select_base_gender({"targetGenders": ["men"]}, "top") == "men"
    assert mannequin.select_base_gender({"targetGenders": []}, "top") == "women"      # 미선택 기본
    assert mannequin.select_base_gender({}, "top") == "women"
    # 원피스는 성별 선택과 무관하게 항상 여성 (기존 계약 유지)
    assert mannequin.select_base_gender({"targetGenders": ["men", "women"]}, "dress") == "women"
