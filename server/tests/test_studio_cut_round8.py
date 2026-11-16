from copy import deepcopy

import pytest

from app.agents import content_roles, page_templates, space_set_assets
from app.agents.studio_examples import slot_allows_cut_type


def legacy_minimal_blocks(template, section_key):
    section = next(section for section in page_templates.get_template(template)["sections"]
                   if section["instanceKey"] == section_key)
    slot = next(slot for slot in section["photoSlots"] if slot.get("fill") == "generate")
    _, registry = space_set_assets.load_space_set_registry()
    group = registry["set-02-men-top-indoor-concrete-byslim-28635"]
    meta = dict(taxonomyVersion=4, sectionId="legacy-section", pageSectionKey=f"{template}.{section_key}",
                pageSectionKind=section["kind"], pageDesignId=section["designId"])
    if section["kind"] == "wear":
        meta["pageSectionUsage"] = section["usage"]
    blocks = []
    if page_templates.CATALOG["kinds"][section["kind"]]["storyboardTextCard"] == "lead":
        blocks.append(dict(meta, id="legacy-text", source="text", cutType=None, contentRole=None,
                           sectionRole=None, slotKey=None))
    eligible = [member for member in group["members"] if member["shot"] in slot["recipe"]["allowedShots"]]
    for member in eligible[:2] if section["kind"] == "wear" and section_key == "wear_styling" else eligible[:1]:
        suffix = str(member["order"]) if section_key == "wear_styling" else ""
        blocks.append(dict(meta, id=f"legacy-{member['order']}", source="ai", cutType="styling",
                           contentRole=slot["contentRole"], slotKey=f"{section_key}.{slot['slotKey']}{suffix}",
                           shot=member["shot"], direction=member["direction"], exampleId=member["exampleId"],
                           exampleSelectionOrigin="auto", refScope="pose", colorId="base",
                           spaceGroupId=f"ssg1__{group['setId']}__legacy", spaceVariation=group["spaceVariation"],
                           spaceSetMemberOrder=member["order"], setSelectionOrigin="auto"))
    return blocks


@pytest.mark.parametrize("section_key", ["wear_styling", "review", "faq"])
def test_legacy_styling_only_minimal_set_survives_the_storage_validation_pipeline(section_key):
    old = legacy_minimal_blocks("emo", section_key)
    before = deepcopy(old)
    canonical = content_roles.canonicalize_storyboard(old, for_storage=True)
    # Saving calls these real normalization and binding stages in this order.
    bindings = space_set_assets.bind_storyboard_space_sets(canonical, clothing_type="top", gender="men")
    photos = [block for block in canonical if block["source"] == "ai"]
    assert [block["exampleId"] for block in photos] == [block["exampleId"] for block in old if block["source"] == "ai"]
    assert all(block["cutType"] == "styling" for block in photos)
    assert all(bindings[id(block)]["poseReference"]["exampleId"] == block["exampleId"] for block in photos)
    assert content_roles.canonicalize_storyboard(canonical, for_storage=True) == canonical
    assert old == before


def test_horizon_only_slot_still_accepts_minimal_styling():
    blocks = legacy_minimal_blocks("show", "wear_fit")
    canonical = content_roles.canonicalize_storyboard(blocks, for_storage=True)
    bindings = space_set_assets.bind_storyboard_space_sets(canonical, clothing_type="top", gender="men")
    assert canonical[0]["cutType"] == "styling"
    assert canonical[0]["exampleId"] == "ss_set_02_men_top_indoor_concrete_byslim_28635_01"
    assert bindings[id(canonical[0])]["poseReference"]["exampleId"] == canonical[0]["exampleId"]


def test_product_only_slot_reverts_minimal_to_the_product_recipe():
    section = next(section for section in page_templates.get_template("emo")["sections"] if section["kind"] == "color_product")
    slot = next(slot for slot in section["photoSlots"] if slot.get("fill") == "generate")
    block = dict(id="product", taxonomyVersion=4, source="ai", sectionId="product",
                 pageSectionKey="emo.color_product", pageSectionKind="color_product", pageDesignId=section["designId"],
                 slotKey=f"color_product.{slot['slotKey']}", cutType="styling", shot="full", direction="front",
                 exampleId="ss_set_02_men_top_indoor_concrete_byslim_28635_01")
    canonical = content_roles.canonicalize_storyboard([block], for_storage=True)[0]
    assert canonical["cutType"] == "product"
    assert canonical["shot"] == "ghost"
    assert canonical["exampleId"] is None
    assert canonical["contentRole"] == slot["contentRole"]


@pytest.mark.parametrize("allowed,want", [(["styling"], True), (["horizon"], True), (["styling", "horizon"], True), (["product"], False)])
def test_minimal_slot_acceptance_keeps_direct_type_compatibility(allowed, want):
    assert slot_allows_cut_type({"allowedCutTypes": allowed}, {
        "cutType": "styling", "exampleId": "ss_set_02_men_top_indoor_concrete_byslim_28635_01",
    }) is want
