"""핏 확인 섹션의 side 컷에 갈래(sideStyle)가 반드시 붙는가.

2026-09-22 운영 E2E 에서 **사선이 0장** 나왔다. 원인은 배분이 아니라 그 앞이었다:
콘티 AI 가 direction 을 직접 배정하면(front 6·back 2·side 1) 배분은 "방향을 안 정한 컷"만
건드리므로 통째로 건너뛰고, side 컷은 sideStyle=None 으로 남는다. 소비자는 미기재를
옆모습으로 읽으므로(cut_generator._side_style_of) 그 컷이 90도로 나간다 — 사선은 영영 안 나온다.

여기서 고정하는 계약: **direction="side" 인 핏 확인 컷은 sideStyle 이 비어 있으면 안 된다.**
"""

from __future__ import annotations

from app.agents import content_roles


def _studio(direction=None, **over):
    block = {"source": "ai", "sectionRole": "studio", "cutType": "horizon"}
    if direction is not None:
        block["direction"] = direction
    block.update(over)
    return block


def _fill(blocks):
    content_roles._spread_studio_directions(blocks)
    return blocks


def test_ai_given_side_gets_a_side_style_even_when_the_spread_is_skipped():
    """콘티가 방향을 직접 주면 배분은 건너뛴다 — 그래도 갈래는 채워져야 한다."""
    blocks = [
        _studio("front"), _studio("back"), _studio("side"), _studio("front"),
    ]
    _fill(blocks)
    side = [b for b in blocks if b.get("direction") == "side"]
    assert side and all(b.get("sideStyle") for b in side), "side 컷에 갈래가 안 붙었다"
    # 한 장뿐이면 사선 — 얼굴이 보이는 컷이 핏 확인에서 값이 더 크다.
    assert side[0]["sideStyle"] == "threeQuarter"


def test_several_side_cuts_alternate_between_profile_and_three_quarter():
    """side 가 여러 장이면 한 갈래로 쏠리지 않는다."""
    blocks = [_studio("front")] + [_studio("side") for _ in range(4)]
    _fill(blocks)
    styles = [b["sideStyle"] for b in blocks if b.get("direction") == "side"]
    assert set(styles) == {"profile", "threeQuarter"}, styles


def test_an_explicit_side_style_is_never_overwritten():
    """콘티나 셀러가 이미 고른 갈래는 정본이다."""
    blocks = [_studio("front"), _studio("side", sideStyle="profile"), _studio("back"), _studio("front")]
    _fill(blocks)
    assert blocks[1]["sideStyle"] == "profile"


def test_the_spread_still_labels_its_own_side_cuts():
    """방향을 안 정한 섹션에서는 배분이 돌고, 그 side 컷들도 갈래를 갖는다."""
    blocks = [_studio() for _ in range(4)]
    _fill(blocks)
    for block in blocks:
        if block.get("direction") == "side":
            assert block.get("sideStyle"), "배분이 만든 side 컷에 갈래가 없다"


def test_seller_cuts_and_other_sections_are_left_alone():
    """셀러 카드와 다른 섹션은 건드리지 않는다 — 배분의 기존 계약."""
    mine = {"source": "mine", "sectionRole": "studio", "cutType": "horizon", "direction": "side"}
    other = {"source": "ai", "sectionRole": "hooking", "cutType": "horizon", "direction": "side"}
    blocks = [_studio("front"), mine, other, _studio("front")]
    _fill(blocks)
    assert "sideStyle" not in mine or not mine.get("sideStyle")
    assert "sideStyle" not in other or not other.get("sideStyle")
