from app.agents.style_affinity import affinity_map
from app.agents.style_tags import STYLE_TAG_SET, STYLE_TAGS, is_style_tag


def test_style_tags_include_clean_base_and_are_lowercase():
    base = {"basic", "daily", "minimal", "casual", "formal", "classic", "sporty", "trendy"}
    assert base <= STYLE_TAG_SET            # 부트스트랩 8 포함
    assert len(STYLE_TAGS) == len(STYLE_TAG_SET) == 24  # 중복 없이 확장 24개
    assert all(t == t.lower() and t.isascii() and "/" not in t for t in STYLE_TAGS)
    assert STYLE_TAG_SET == set(STYLE_TAGS)


def test_affinity_keys_are_subset_of_style_tags():
    # 매칭 친화도(style_affinity)의 모든 태그가 계약 enum 안에 있어야 한다 —
    # 오염 시드(한글 태그 등)가 들어오면 이 테스트가 잡는다.
    used = {t for pair in affinity_map() for t in pair}
    assert used <= STYLE_TAG_SET, f"enum 밖 태그: {used - STYLE_TAG_SET}"


def test_is_style_tag():
    assert is_style_tag("minimal")
    assert not is_style_tag("스트라이프")
    assert not is_style_tag("")
