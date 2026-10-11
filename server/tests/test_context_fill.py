"""계획 없는 빈 이미지 칸의 판단 규칙을 저장 데이터로 검증한다."""

from copy import deepcopy
import importlib
import importlib.util
import json
from pathlib import Path
import uuid

import pytest


def rules():
    assert importlib.util.find_spec("app.agents.context_fill") is not None, "칸 판단 모듈이 필요하다"
    return importlib.import_module("app.agents.context_fill")


def asset_id(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def image(name, *, x=0, y=0, w=100, h=100, filled=True, **extra):
    return {"id": name, "type": "image", "x": x, "y": y, "w": w, "h": h,
            "src": f"/v1/assets/{asset_id(name)}/file" if filled else None, **extra}


def block(name, elements, **extra):
    return {"id": name, "kind": "styling", "contentRole": "custom", "elements": elements, **extra}


def recipe(*, cut="styling", shot="full", direction="front", color="base", **extra):
    role = "productOverview" if cut == "product" else "fit" if cut == "horizon" else "coordination"
    if shot == "detail":
        role = "detail"
    return {"cutType": cut, "shot": shot, "direction": direction, "colorId": color,
            "contentRole": role, "sectionRole": {"styling": "styling", "horizon": "studio", "product": "product"}.get(cut, "styling"), **extra}


def asset(recipe_value, **extra):
    return {"origin": "editor_new", "recipe": recipe_value, "recipeFrom": "self", "sourceAssetId": None,
            "cutType": recipe_value.get("cutType"), "colorId": recipe_value.get("colorId"),
            "upload": False, "realDerived": False, **extra}


def examples():
    return {f"test_{cut}_{shot}_{direction}_{i}": {"all": "https://example.test/photo.png",
             "cutType": cut, "shot": shot, "direction": direction, "gender": "women",
             "applicableClothingTypes": ["top", "outer"], "sideStyle": "profile" if direction == "side" else None}
            for cut in ("styling", "horizon") for shot in ("full", "medium")
            for direction in ("front", "side", "back") for i in range(2)}


def inputs(*, elements=None, before=None, after=None, assets=None, purpose=None, **overrides):
    target = image("empty", x=110, filled=False, **({"roleHint": purpose} if purpose else {}))
    current = block("target", elements if elements is not None else [target], templateId="test-template")
    result = {"project_id": "project", "slot": {"blockId": "target", "elementId": "empty"},
              "blocks": (before or []) + [current] + (after or []), "storyboard": [],
              "product": {"clothingType": "outer", "colors": [
                  {"id": "base", "name": "베이지", "isBase": True, "images": [{"id": "front", "slot": "Front"}, {"id": "back", "slot": "Back"}]},
                  {"id": "black", "name": "블랙", "images": [{"id": "black-front", "slot": "Front"}]}]},
              "analysis": {"targetGenders": ["women"], "selectedModelId": "mA"},
              "asset_recipes": assets or {}, "examples": examples(), "previous_recipes": [], "attempt": 0,
              "ai_asset_ids": set((assets or {}).keys())}
    result.update(overrides)
    return result


def decide(data):
    result = rules().decide_context_fill(**data)
    assert result is not None
    return result


_SLOT_CASES = json.loads((Path(__file__).resolve().parents[2] / "tests/fixtures/editor-slot-kind.json").read_text())


@pytest.mark.parametrize("case", _SLOT_CASES, ids=lambda case: case["name"])
def test_slot_kind_shared_table(case):
    storyboard = {item["id"]: item for item in case["storyboard"]}
    assert rules().slot_kind(case["block"], case["element"], storyboard) == case["expected"]


@pytest.mark.parametrize("elements,anchor,position", [
    ([image("left"), image("empty", x=110, filled=False)], "left", "left"),
    ([image("left"), image("empty", x=110, filled=False), image("right", x=220)], "left", "left"),
    ([image("above"), image("empty", y=110, filled=False), image("other-empty", x=110, y=110, filled=False)], "above", "above"),
    ([image("empty", filled=False), image("right", x=110)], "right", "right"),
])
def test_worn_pair_by_geometry_uses_existing_similar_payload(elements, anchor, position):
    data = inputs(elements=elements, assets={asset_id(anchor): asset(recipe(color="black"))})
    result = decide(data)
    assert result["payload"] == {"mode": "vary", "variationMode": "similar", "changes": [],
                                 "source": {"src": f"/v1/assets/{asset_id(anchor)}/file", "cutType": "styling"}}
    assert result["contextFill"]["rule"] == "pair_similar"
    assert result["contextFill"]["anchorAssetId"] == asset_id(anchor)
    assert result["contextFill"]["colorId"] == "black"
    assert rules().build_context(**data)["pairPosition"] == position


def test_half_overlap_boundary_and_left_precedes_closer_right():
    data = inputs(elements=[image("left", x=-200, y=50), image("empty", filled=False), image("right", x=110)],
                  assets={asset_id("left"): asset(recipe()), asset_id("right"): asset(recipe())})
    assert decide(data)["contextFill"]["anchorAssetId"] == asset_id("left")
    data["blocks"][0]["elements"][0]["y"] = 51
    assert decide(data)["contextFill"]["anchorAssetId"] == asset_id("right")


def test_three_columns_studio_selects_missing_side_with_pair_shot():
    data = inputs(elements=[image("left"), image("empty", x=110, filled=False), image("right", x=220)],
                  assets={asset_id("left"): asset(recipe(cut="horizon")),
                          asset_id("right"): asset(recipe(cut="horizon", direction="back"))})
    result = decide(data)
    assert result["contextFill"]["purpose"] == "fit"
    assert result["contextFill"]["rule"] == "row_direction"
    assert (result["payload"]["cutType"], result["payload"]["direction"], result["payload"]["shot"]) == ("horizon", "side", "full")


def test_full_width_new_cut_uses_upper_section_and_alternates_shot():
    data = inputs(before=[block("above", [image("street")])], after=[block("below", [image("studio")])],
                  assets={asset_id("street"): asset(recipe(shot="medium", direction="side", outerClosureState="closed")),
                          asset_id("studio"): asset(recipe(cut="horizon", outerClosureState="closed"))})
    result = decide(data)
    assert (result["contextFill"]["purpose"], result["contextFill"]["rule"]) == ("coordination", "section_new")
    # 위의 옆모습과 아래의 정면을 뒤로 미루면 뒷모습이 먼저다.
    assert (result["payload"]["cutType"], result["payload"]["shot"], result["payload"]["direction"]) == ("styling", "full", "back")
    assert result["payload"]["outerClosureState"] == "closed"
    assert result["payload"]["modelId"] == "mA"
    assert result["payload"]["source"] == "ai"
    assert result["payload"]["taxonomyVersion"] == 3


def test_page_top_template_hero_and_later_hero_becomes_benefit():
    data = inputs(purpose="hero", after=[block("signature", [image("signature")])],
                  assets={asset_id("signature"): asset(recipe(contentRole="hero", sectionRole="hooking"))})
    result = decide(data)
    assert result["contextFill"]["purpose"] == "hero"
    assert result["payload"]["cutType"] == "styling"
    data["blocks"].insert(0, block("first", [image("existing")]))
    result = decide(data)
    assert result["contextFill"]["purpose"] == "benefit"
    assert (result["payload"]["cutType"], result["payload"]["shot"]) == ("horizon", "medium")


def detail(name, *, kind="cuff", rank=1, group=None, **extra):
    return {"id": name, "kind": kind, "rank": rank, "label": "소매 끝단", "informationGroup": group or name,
            "photoUse": "standalone", "visibility": "clear", "direction": "front", **extra}


def test_detail_slot_skips_existing_target_and_information_group():
    candidates = [detail("collar", kind="neckline", rank=1), detail("closure", kind="closure", rank=2),
                  detail("closure-other", group="closure", rank=3), detail("cuff", rank=4)]
    data = inputs(purpose="detail", before=[block("details", [image("collar"), image("closure", x=110)])],
                  assets={asset_id("collar"): asset(recipe(cut="product", shot="detail", detailTargetId="collar")),
                          asset_id("closure"): asset(recipe(cut="product", shot="detail", detailTargetId="closure"))},
                  analysis={"detailRecommendations": {"version": 1, "status": "ready", "candidates": candidates}})
    result = decide(data)
    assert result["payload"]["detailTargetId"] == "cuff"
    assert result["payload"]["detailTargetOrigin"] == "auto"
    assert result["contextFill"]["rule"] == "detail_unused"


@pytest.mark.parametrize("change", [{"photoUse": "context"}, {"visibility": "uncertain"}, {"direction": "side"}])
def test_detail_unusable_candidate_falls_back_to_existing_photo_face(change):
    data = inputs(purpose="detail", analysis={"detailRecommendations": {"version": 1, "status": "ready", "candidates": [detail("bad", **change)]}})
    result = decide(data)
    assert (result["payload"]["shot"], result["contextFill"]["rule"]) == ("ghost", "detail_to_ghost")


@pytest.mark.parametrize("version,status", [(2, "ready"), (1, "pending"), (True, "ready")])
def test_detail_only_reads_ready_version_one(version, status):
    data = inputs(purpose="detail", analysis={"detailRecommendations": {"version": version, "status": status, "candidates": [detail("cuff")]}})
    assert decide(data)["contextFill"]["rule"] == "detail_to_ghost"


def test_product_b_without_details_uses_missing_back_ghost():
    data = inputs(purpose="detail", before=[block("ghost", [image("front")])],
                  assets={asset_id("front"): asset(recipe(cut="product", shot="ghost"))},
                  product={"clothingType": "top", "colors": [{"id": "base", "name": "화이트", "isBase": True,
                           "images": [{"id": "f", "slot": "Front"}, {"id": "b", "slot": "Back"}]}]},
                  analysis={"targetGenders": ["men"], "detailRecommendations": {"version": 1, "status": "ready", "candidates": []}})
    result = decide(data)
    assert (result["payload"]["shot"], result["payload"]["direction"], result["payload"]["colorId"]) == ("ghost", "back", "base")
    assert result["contextFill"]["rule"] == "detail_to_ghost"


def test_product_pair_prefers_other_face_before_unused_additional_color():
    data = inputs(purpose="productOverview", elements=[image("front"), image("empty", x=110, filled=False, roleHint="productOverview")],
                  assets={asset_id("front"): asset(recipe(cut="product", shot="ghost"))})
    result = decide(data)
    assert (result["payload"]["colorId"], result["payload"]["direction"], result["contextFill"]["rule"]) == ("base", "back", "ghost_other_face")


def test_color_compare_copies_cut_with_missing_row_color_then_ghost_order():
    data = inputs(elements=[image("front"), image("empty", x=110, filled=False)],
                  assets={asset_id("front"): asset(recipe(cut="product", shot="ghost"))})
    data["blocks"][0].update(templateId="kiwi-5", name="컬러 비교 카드")
    result = decide(data)
    assert (result["payload"]["colorId"], result["payload"]["direction"], result["contextFill"]["rule"]) == ("black", "front", "color_compare")
    data["blocks"][0]["elements"].append(image("black", x=220))
    data["asset_recipes"][asset_id("black")] = asset(recipe(cut="product", shot="ghost", color="black"))
    result = decide(data)
    assert (result["payload"]["colorId"], result["payload"]["direction"], result["contextFill"]["rule"]) == ("base", "back", "ghost_other_face")


def test_color_compare_without_pair_uses_front_only_with_photo():
    data = inputs(elements=[image("empty", filled=False)])
    data["blocks"][0]["kind"] = "colorcmp"
    data["product"]["colors"].append({"id": "missing", "images": [{"id": "b", "slot": "Back"}]})
    candidates = rules().build_candidates(rules().build_context(**data))
    primary = [c for c in candidates if c["rule"] == "color_compare"]
    assert {c["recipe"]["colorId"] for c in primary} == {"base", "black"}
    assert all(c["recipe"]["direction"] == "front" for c in primary)


@pytest.mark.parametrize("asset_extra,ai_ids", [({"upload": True, "origin": "upload"}, None),
    ({"realDerived": True}, None), ({}, set()), ({"detail_recipe": {"version": 1}}, None)])
def test_ineligible_worn_photo_never_becomes_similar_anchor(asset_extra, ai_ids):
    data = inputs(elements=[image("left"), image("empty", x=110, filled=False)],
                  assets={asset_id("left"): asset(recipe(), **asset_extra)},
                  before=[block("above", [image("upper")])])
    data["asset_recipes"][asset_id("upper")] = asset(recipe())
    if ai_ids is not None:
        data["ai_asset_ids"] = ai_ids
    result = decide(data)
    assert result["payload"]["mode"] == "new"
    assert result["payload"]["shot"] == "medium"


def test_closure_free_outer_omits_closure_and_uploaded_photo_is_not_pair():
    data = inputs(elements=[image("upload"), image("empty", x=110, filled=False)],
                  assets={asset_id("upload"): asset(recipe(outerClosureState="closed"), upload=True)},
                  before=[block("above", [image("upper")])])
    data["asset_recipes"][asset_id("upper")] = asset(recipe())
    data["analysis"]["frontClosure"] = {"version": 1, "kind": "none", "range": None, "secondary": None, "evidenceSlots": ["Front"], "note": None}
    result = decide(data)
    assert "outerClosureState" not in result["payload"]
    assert result["payload"]["mode"] == "new"
    assert result["payload"]["shot"] == "medium"


def test_closure_pair_then_majority_then_default_and_binary_normalization():
    data = inputs(purpose="fit", elements=[image("left"), image("empty", x=110, filled=False, roleHint="fit")],
                  assets={asset_id("left"): asset(recipe(cut="horizon", outerClosureState="partial"))})
    data["analysis"]["frontClosure"] = {"version": 1, "kind": "belt", "range": "binary", "secondary": None, "evidenceSlots": ["Front"], "note": None}
    assert decide(data)["payload"]["outerClosureState"] == "closed"
    assert decide(inputs())["payload"]["outerClosureState"] == "open"


def test_new_worn_matching_uses_pair_then_same_color_storyboard():
    data = inputs(purpose="fit", elements=[image("left"), image("empty", x=110, filled=False, roleHint="fit")],
                  assets={asset_id("left"): asset(recipe(cut="horizon", matchIds=["anchor-match"]))},
                  storyboard=[{"id": "board", "source": "ai", **recipe(cut="horizon", matchIds=["board-match"])}])
    assert decide(data)["payload"]["matchIds"] == ["anchor-match"]
    data["asset_recipes"][asset_id("left")]["recipe"].pop("matchIds")
    assert decide(data)["payload"]["matchIds"] == ["board-match"]


def test_recipe_lookup_never_reads_element_source_block_id():
    data = inputs(elements=[image("left", sourceBlockId="fake"), image("empty", x=110, filled=False)],
                  storyboard=[{"id": "fake", "source": "ai", **recipe(cut="horizon", color="black")}])
    result = decide(data)
    assert result["contextFill"]["purpose"] == "coordination"
    assert result["payload"]["mode"] == "new"
    assert result["payload"]["colorId"] == "base"


def test_purpose_precedence_skips_info_and_automatic_neighbors():
    data = inputs(before=[block("wear", [image("wear")]), block("info", [image("info")], kind="info"),
                          block("auto", [image("auto")], auto=True)],
                  after=[block("below", [image("below")])],
                  assets={asset_id("wear"): asset(recipe(cut="horizon")), asset_id("info"): asset(recipe(cut="product")),
                          asset_id("auto"): asset(recipe(cut="product")), asset_id("below"): asset(recipe())})
    assert rules().build_context(**data)["purpose"] == "fit"
    data["blocks"][3]["contentRole"] = "detail"
    assert rules().build_context(**data)["purpose"] == "detail"
    data["blocks"][3]["elements"][0]["roleHint"] = "realWear"
    result = decide(data)
    assert result["contextFill"]["purpose"] == "realWear"
    assert result["payload"]["cutType"] == "styling"


def test_fit_without_pair_uses_page_missing_direction_then_other_shot():
    data = inputs(purpose="fit", before=[block("above", [image("front"), image("side", x=110)])],
                  assets={asset_id("front"): asset(recipe(cut="horizon")), asset_id("side"): asset(recipe(cut="horizon", direction="side"))})
    result = decide(data)
    assert (result["payload"]["direction"], result["contextFill"]["rule"]) == ("back", "page_direction")
    data["blocks"][0]["elements"].append(image("back", x=220))
    data["asset_recipes"][asset_id("back")] = asset(recipe(cut="horizon", direction="back"))
    result = decide(data)
    assert (result["payload"]["direction"], result["payload"]["shot"]) == ("front", "medium")


def test_legacy_horizon_photo_without_recipe_uses_known_section_and_default_shot():
    legacy = asset(recipe(cut="horizon"))
    legacy["recipe"] = None
    data = inputs(before=[block("above", [image("legacy")])], assets={asset_id("legacy"): legacy})
    result = decide(data)
    assert result["contextFill"]["purpose"] == "fit"
    assert (result["payload"]["cutType"], result["payload"]["shot"], result["payload"]["direction"]) == ("horizon", "full", "front")


def test_examples_filter_public_gender_clothing_cut_shot_and_excludes_sig_ss():
    data = inputs()
    data["examples"] = {"valid": {"all": "public", "cutType": "styling", "shot": "full", "direction": "front", "gender": "women", "applicableClothingTypes": ["outer"]}}
    for key, change in [("men", {"gender": "men"}), ("top", {"applicableClothingTypes": ["top"]}), ("horizon", {"cutType": "horizon"}),
                        ("medium", {"shot": "medium"}), ("unpublished", {"all": None}), ("sig_foo", {}), ("ss_foo", {})]:
        data["examples"][key] = {**data["examples"]["valid"], **change}
    primary = [c for c in rules().build_candidates(rules().build_context(**data)) if c["rule"] == "section_new"]
    assert [c["recipe"]["exampleId"] for c in primary] == ["valid"]


def test_real_registry_new_cut_is_public_applicable_and_canonical():
    registry = json.loads((Path(__file__).resolve().parents[1] / "app/data/example_assets.json").read_text())["assets"]
    result = decide(inputs(examples=registry))
    picked = registry[result["payload"]["exampleId"]]
    assert picked["gender"] == "women" and "outer" in picked["applicableClothingTypes"]
    assert picked["cutType"] == result["payload"]["cutType"] == "styling"
    assert picked["direction"] == result["payload"]["direction"]


def test_same_page_example_and_same_slot_previous_recipe_are_excluded():
    data = inputs(before=[block("above", [image("existing")])],
                  assets={asset_id("existing"): asset(recipe(shot="medium", direction="back", exampleId="test_styling_full_front_0"))})
    first = decide(data)
    assert first["payload"]["exampleId"] == "test_styling_full_front_1"
    data["previous_recipes"] = [first["payload"]]
    data["attempt"] = 1
    second = decide(data)
    assert second["payload"]["exampleId"] != first["payload"]["exampleId"]


def test_previous_ghost_and_detail_candidates_are_excluded_after_slot_cleared():
    data = inputs(purpose="productOverview", previous_recipes=[recipe(cut="product", shot="ghost")])
    assert decide(data)["payload"]["direction"] == "back"
    data = inputs(purpose="detail", analysis={"detailRecommendations": {"version": 1, "status": "ready", "candidates": [detail("one"), detail("two", rank=2)]}},
                  previous_recipes=[recipe(cut="product", shot="detail", detailTargetId="one")])
    assert decide(data)["payload"]["detailTargetId"] == "two"


def test_fallback_order_other_color_then_shot_then_section_default():
    single = {"only": {"all": "public", "cutType": "styling", "shot": "full", "direction": "front", "gender": "women", "applicableClothingTypes": ["outer"]}}
    data = inputs(examples=single, previous_recipes=[recipe(exampleId="only")])
    result = decide(data)
    assert (result["contextFill"]["rule"], result["payload"]["colorId"]) == ("relax_other_color", "black")
    data["product"]["colors"] = data["product"]["colors"][:1]
    data["examples"]["medium"] = {**single["only"], "shot": "medium"}
    result = decide(data)
    assert (result["contextFill"]["rule"], result["payload"]["shot"]) == ("relax_shot", "medium")
    data["examples"].pop("medium")
    assert decide(data)["contextFill"]["rule"] == "section_default"


def test_ghost_face_order_and_missing_faces_never_invented():
    data = inputs(purpose="productOverview")
    data["product"]["colors"][1]["images"].append({"id": "b", "slot": "Back"})
    data["product"]["colors"].append({"id": "invalid", "images": [{"id": "back-only", "slot": "Back"}]})
    candidates = rules().build_candidates(rules().build_context(**data))
    assert [(c["recipe"]["colorId"], c["recipe"]["direction"]) for c in candidates if c["rule"] == "ghost_order"] == [
        ("base", "front"), ("base", "back"), ("black", "front"), ("black", "back")]


def test_no_product_photos_has_no_candidate_even_with_examples():
    data = inputs()
    for color in data["product"]["colors"]:
        color["images"] = []
    assert rules().decide_context_fill(**data) is None


def test_deterministic_picker_uses_exact_seed_and_only_best_rank():
    candidates = [{"rank": 0, "recipe": {"exampleId": value}} for value in range(5)] + [{"rank": 1, "recipe": {"exampleId": "lower"}}]
    chosen = rules().pick_candidate(candidates, project_id="project", slot={"blockId": "target", "elementId": "empty"}, attempt=0)
    # 별도 JavaScript 계산의 FNV-1a 씨앗 2396722673, 첫 값 약 0.334367219.
    assert chosen["recipe"]["exampleId"] == 1
    assert rules().pick_candidate([], project_id="p", slot={"blockId": "b", "elementId": "e"}, attempt=0) is None


def test_builder_and_picker_are_separate_and_inputs_unchanged():
    data = inputs()
    original = deepcopy(data)
    context = rules().build_context(**data)
    candidates = rules().build_candidates(context)
    selected = rules().pick_candidate(candidates, project_id="project", slot=data["slot"], attempt=0)
    assert selected in candidates
    assert candidates == rules().build_candidates(context)
    assert data == original


@pytest.mark.parametrize("purpose", ["hero", "coordination", "fit", "detail", "productOverview", "realWear"])
def test_labels_are_short_korean_and_context_record_is_complete(purpose):
    data = inputs(purpose=purpose)
    data["product"]["colors"][0]["name"] = "엄청나게긴한국어색상이름스무자를넘기는색상"
    data["analysis"]["detailRecommendations"] = {"version": 1, "status": "ready", "candidates": [detail("long", label="엄청나게긴한국어소매끝단스트랩이름여기까지")]}
    record = decide(data)["contextFill"]
    assert 0 < len(record["label"]) <= 20
    assert "—" not in record["label"] and "–" not in record["label"] and "-" not in record["label"]
    assert set(record) == {"version", "slot", "slotKey", "purpose", "rule", "label", "path", "colorId", "anchorAssetId", "attempt"}
    assert record["slot"] == data["slot"] and record["slotKey"] == "target:empty" and record["attempt"] == 0
    if purpose == "detail":
        assert record["label"] == "디테일 · 소매 끝"
    if purpose == "productOverview":
        assert record["label"] == "고스트샷 · 앞면"


@pytest.mark.parametrize("position", ["left", "right", "above"])
def test_similar_labels_fit_twenty_characters(position):
    elements = {"left": [image("pair"), image("empty", x=110, filled=False)],
                "right": [image("empty", filled=False), image("pair", x=110)],
                "above": [image("pair"), image("empty", y=110, filled=False)]}[position]
    result = decide(inputs(elements=elements, assets={asset_id("pair"): asset(recipe())}))
    assert len(result["contextFill"]["label"]) <= 20


def test_second_empty_hero_slot_is_not_page_first_photo():
    data = inputs(elements=[image("earlier", filled=False), image("empty", x=110, filled=False, roleHint="hero")])
    assert decide(data)["contextFill"]["purpose"] == "benefit"


def test_previous_similar_anchor_is_not_reused_when_new_cut_available():
    data = inputs(elements=[image("left"), image("empty", x=110, filled=False)],
                  assets={asset_id("left"): asset(recipe())},
                  previous_recipes=[{**recipe(), "anchorAssetId": asset_id("left")}], attempt=1)
    result = decide(data)
    assert result["payload"]["mode"] == "new"
    assert result["contextFill"]["attempt"] == 1


def test_new_color_compare_worn_uses_public_example_and_selected_model():
    data = inputs(elements=[image("left"), image("empty", x=110, filled=False)],
                  assets={asset_id("left"): asset(recipe(exampleId="sig_old", modelId="old-model", matchIds=["match"]))})
    data["blocks"][0]["kind"] = "colorcmp"
    result = decide(data)
    assert result["contextFill"]["rule"] == "color_compare"
    assert result["payload"]["exampleId"].startswith("test_styling_full_front_")
    assert result["payload"]["exampleSelectionOrigin"] == "auto"
    assert result["payload"]["modelId"] == "mA"
    assert result["payload"]["matchIds"] == ["match"]


@pytest.mark.parametrize("example_id", ["ss_old", "sig_old", "unknown"])
def test_new_color_compare_drops_pair_set_bindings(example_id):
    data = inputs(elements=[image("left"), image("empty", x=110, filled=False)],
                  assets={asset_id("left"): asset(recipe(exampleId=example_id, spaceGroupId="ssg1__old__instance",
                                                        spaceVariation="fixed", spaceSetMemberOrder=2, horizonBackgroundMode="reference"))})
    data["blocks"][0]["kind"] = "colorcmp"
    payload = decide(data)["payload"]
    assert payload["exampleId"].startswith("test_styling_full_front_")
    assert all(key not in payload for key in ("spaceGroupId", "spaceVariation", "spaceSetMemberOrder", "horizonBackgroundMode"))


def test_color_compare_worn_without_compatible_public_example_uses_ghost():
    data = inputs(elements=[image("left"), image("empty", x=110, filled=False)],
                  assets={asset_id("left"): asset(recipe(exampleId="ss_retired"))}, examples={})
    data["blocks"][0]["kind"] = "colorcmp"
    result = decide(data)
    assert result["payload"]["cutType"] == "product"
    assert result["payload"]["shot"] == "ghost"
    assert result["payload"]["exampleId"] is None


def test_base_without_front_photo_does_not_create_detail_on_additional_color_evidence():
    data = inputs(purpose="detail", analysis={"detailRecommendations": {"version": 1, "status": "ready", "candidates": [detail("one")]}})
    data["product"]["colors"][0]["images"] = []
    result = decide(data)
    assert result["payload"]["colorId"] == "black"
    assert result["payload"]["shot"] == "ghost"


def test_new_non_outer_worn_omits_inherited_outer_state():
    data = inputs(elements=[image("left"), image("empty", x=110, filled=False)],
                  assets={asset_id("left"): asset(recipe(outerClosureState="partial"))})
    data["blocks"][0]["kind"] = "colorcmp"
    data["product"]["clothingType"] = "top"
    assert "outerClosureState" not in decide(data)["payload"]


def test_direction_preference_reads_known_neighbor_after_unknown_photo():
    data = inputs(before=[block("above", [image("unknown"), image("known", x=110)])],
                  assets={asset_id("known"): asset(recipe(shot="medium", direction="front"))})
    assert decide(data)["payload"]["direction"] == "side"


@pytest.mark.parametrize("template_id,expected", [("kiwi-5", "color_compare"), ("5", "ghost_other_face")])
def test_color_compare_card_is_found_by_real_template_id(template_id, expected):
    data = inputs(elements=[image("front"), image("empty", x=110, filled=False)],
                  assets={asset_id("front"): asset(recipe(cut="product", shot="ghost"))})
    data["blocks"][0].update(templateId=template_id, name="이름을 바꾼 블록")
    assert decide(data)["contextFill"]["rule"] == expected
