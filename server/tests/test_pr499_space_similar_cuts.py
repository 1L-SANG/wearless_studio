import asyncio

import pytest

from app.agents import content_roles, horizon_background, space_set_assets
from app.workers import detail_page_job as dpj
from conftest import worker_job
from test_space_set_assets import _blocks, space_registry
from test_storyboard_similar_cuts import child, item, setup_generation, source


def test_set_followup_inherits_only_source_membership_and_never_reserves_a_member():
    original = source(spaceGroupId="set", spaceVariation="subtle", spaceSetMemberOrder=1)
    followup = child(spaceGroupId="wrong", spaceVariation="fixed", spaceSetMemberOrder=3,
                     hookSlotRole="right", colorwayId="old")
    result = content_roles.inherit_similar_cut_recipes([original, followup])[1]
    assert result.get("spaceGroupId") == "set", "후속 컷은 원본 세트에 포함된다"
    assert result.get("spaceVariation") == "subtle"
    assert not any(key in result for key in ("spaceSetMemberOrder", "hookSlotRole", "colorwayId"))
    outside = content_roles.inherit_similar_cut_recipes([source(), followup])[1]
    assert "spaceGroupId" not in outside
    assert "spaceVariation" not in outside


def test_contiguous_set_with_followup_passes_binding_and_counts_every_cut(space_registry):
    members = [source(**block) for block in _blocks()]
    followup = child(similarSourceBlockId=members[0]["id"])
    blocks = content_roles.inherit_similar_cut_recipes([members[0], followup, members[1]])
    bindings = space_set_assets.bind_storyboard_space_sets(blocks, clothing_type="top", gender="women")
    assert len(bindings) == 3, "후속 컷을 포함한 세 장이 같은 세트 검증을 통과한다"
    assert {binding["groupId"] for binding in bindings.values()} == {members[0]["spaceGroupId"]}
    assert dpj._duplicate_source_indexes(blocks, "top") == [None, None, None]


@pytest.mark.parametrize("background", ["reference", "garment-tone"])
def test_horizon_followup_keeps_group_background_mode(background):
    original = source(cutType="horizon", contentRole="fit", sectionRole="studio",
                      spaceGroupId="set", spaceVariation="fixed", horizonBackgroundMode=background)
    result = content_roles.inherit_similar_cut_recipes([original, child(horizonBackgroundMode="wrong")])
    assert result[1].get("spaceGroupId") == "set"
    assert horizon_background.mode(result[1]) == background


def test_set_followup_uses_generated_original_instead_of_set_example_packet(monkeypatch):
    app, seen = setup_generation(monkeypatch)
    original = source(spaceGroupId="set", spaceVariation="subtle")
    followup = child()
    canonical = content_roles.inherit_similar_cut_recipes([original, followup])
    assert canonical[1].get("spaceGroupId") == "set"
    results, assets, *_ = asyncio.run(dpj._gen_cuts(
        app, worker_job(), [item(block) for block in canonical], {"clothingType": "top"}, {}))
    assert seen["generate"] == ["original"]
    assert [entry[0] for entry in seen["vary"]] == [b"GENERATED-ORIGINAL"]
    assert [entry["blockId"] for entry in results] == ["original", "next"]
    assert assets[1]["metadata"]["similar_cut"]["sourceBlockId"] == "original"
