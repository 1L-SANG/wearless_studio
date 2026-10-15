"""Condition-table planning and persisted v1/v2 compatibility; no provider calls."""
import asyncio
import copy
import json
import re
from dataclasses import replace

import pytest

from app.agents import similar_cut, vision_llm
from app.agents.gemini_image import InlineImage
from app.agents.vision_llm import VisionError
from conftest import make_settings


def hand(state, obj="none"):
    return {"state": state, "object": obj}


def source_state(**overrides):
    return {
        "posture": "standing", "support": "none", "nearbySupports": [],
        "capture": "third_person", "framing": "full_body", "face": "visible",
        "hair": "short", "legs": "apart", "torso": "upright",
        "imageLeftHand": hand("free"), "imageRightHand": hand("free"),
        "gaze": "camera", "props": [], "garmentParts": ["collar", "cuff", "side_pocket"],
        **overrides,
    }


def choice(action="hand_on_hip", selected="imageLeft", secondary="none", target="hip"):
    return {"action": action, "hand": selected, "target": target, "secondary": secondary}


def new_plan(**overrides):
    return {
        "actionKey": "hand_on_hip", "actionSummary": "허리에 손을 올리기",
        "instructions": "The free hand rests on the hip, with its elbow angled outward.",
        "preserve": ["Same person and garment."], "avoid": ["Do not add objects."],
        "sourceState": source_state(), "choice": choice(),
        "fabricResponse": "The shirt gathers softly at the bent elbow and waist.",
        "keepUnchanged": ["posture stays standing; legs and feet stay where they are",
                          "gaze and expression stay as they are (camera)",
                          "full body framing, camera position and subject size stay the same"],
        "movingPart": "the free hand", **overrides,
    }


def model_plan(**overrides):
    result = new_plan(**overrides)
    del result["keepUnchanged"], result["movingPart"]
    return result


@pytest.mark.parametrize("provider", ["gpt", "gemini"])
def test_model_request_schemas_omit_string_limits_and_private_extensions(provider):
    original = copy.deepcopy(similar_cut._SCHEMA)
    if provider == "gpt":
        sent = vision_llm._gpt_body("gpt-test", "prompt", [], similar_cut._SCHEMA)
        schema = sent["response_format"]["json_schema"]["schema"]
        assert schema["properties"]["preserve"]["minItems"] == 1
        assert schema["properties"]["preserve"]["maxItems"] == 8
    else:
        sent = vision_llm._gemini_body("prompt", [], similar_cut._SCHEMA, "low")["generationConfig"]["responseSchema"]

    def assert_clean(node):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in {"minLength", "maxLength"}
                assert not key.startswith("x-")
                assert_clean(value)
        elif isinstance(node, list):
            for value in node:
                assert_clean(value)

    assert_clean(sent)
    assert similar_cut._SCHEMA == original


STATES = {
    "p2": source_state(posture="sitting", support="wooden chair", legs="crossed_at_knee",
                       imageLeftHand=hand("holding", "glass of iced tea"),
                       imageRightHand=hand("resting", "thigh"), props=["glass"],
                       garmentParts=["neckline", "hem"]),
    "p5": source_state(posture="leaning", support="concrete wall", nearbySupports=["concrete wall"],
                       torso="leaning_on_support", imageLeftHand=hand("in_pocket", "trouser pocket"),
                       imageRightHand=hand("in_pocket", "trouser pocket"),
                       garmentParts=["neckline", "cuff", "side_pocket", "waistband", "hem"]),
    "p4": source_state(capture="mirror_selfie", framing="knee_up", legs="not_visible",
                       hair="shoulder_or_longer", imageLeftHand=hand("touching", "jacket hem"),
                       imageRightHand=hand("holding", "phone"), props=["phone"],
                       garmentParts=["collar", "front_buttons", "cuff"]),
}


@pytest.mark.parametrize("photo,action,selected,target,clothing,allowed", [
    ("p2", "uncross_legs", "none", "skirt", "bottom", True),
    ("p2", "cross_legs", "none", "x", "bottom", False),
    ("p2", "lean_forward_seated", "none", "x", "bottom", True),
    ("p2", "hip_shift", "none", "x", "bottom", False),
    ("p5", "stand_away_from_support", "none", "wall", "bottom", True),
    ("p5", "lean_on_support", "none", "wall", "bottom", False),
    ("p5", "cross_arms", "both", "arms", "bottom", True),
    ("p5", "cross_arms", "both", "arms", "top", False),
    ("p5", "step_forward", "none", "legs", "bottom", False),
    ("p4", "hip_shift", "none", "hips", "outer", True),
    ("p4", "hand_on_hip", "imageRight", "hip", "outer", False),
    ("p4", "hand_on_hip", "imageLeft", "hip", "outer", True),
    ("p4", "step_forward", "none", "legs", "outer", False),
])
def test_prototype_thirteen_conditions(photo, action, selected, target, clothing, allowed):
    problems = similar_cut._choice_violations(STATES[photo], choice(action, selected, target=target), clothing)
    assert (not problems) is allowed, problems


@pytest.mark.parametrize("state,selected,clothing,allowed", [
    ({}, choice("cross_legs", "none"), "top", False),
    ({"posture": "sitting", "legs": "apart"}, choice("cross_legs", "none"), "top", True),
    ({"posture": "sitting", "torso": "leaning_forward"}, choice("lean_forward_seated", "none"), "top", False),
    ({"posture": "sitting", "support": "sofa"}, choice("sit_back_relaxed", "none"), "top", True),
    ({"posture": "sitting"}, choice("sit_back_relaxed", "none"), "top", False),
    ({"nearbySupports": ["railing"]}, choice("lean_on_support", "none"), "top", True),
    ({}, choice("lean_on_support", "none"), "top", False),
    ({}, choice("cross_ankles", "none"), "top", True),
    ({"legs": "ankles_crossed"}, choice("uncross_ankles", "none"), "top", True),
    ({"framing": "waist_up"}, choice("step_forward", "none"), "top", False),
    ({"framing": "chest_up"}, choice(), "top", False),
    ({}, choice("both_hands_in_pockets", "both"), "top", True),
    ({"garmentParts": []}, choice("both_hands_in_pockets", "both"), "top", False),
    ({"imageRightHand": hand("holding", "cup")}, choice("cross_arms", "both"), "bottom", False),
    ({}, choice("hand_into_pocket"), "top", True),
    ({"imageLeftHand": hand("in_pocket")}, choice("hand_into_pocket"), "top", False),
    ({"imageLeftHand": hand("in_pocket")}, choice("hand_out_of_pocket"), "top", True),
    ({}, choice("hand_out_of_pocket"), "top", False),
    ({}, choice("touch_collar"), "top", True),
    ({"garmentParts": []}, choice("touch_collar"), "top", False),
    ({}, choice("adjust_cuff"), "top", True),
    ({"garmentParts": []}, choice("adjust_cuff"), "top", False),
    ({"hair": "shoulder_or_longer"}, choice("tuck_hair"), "top", True),
    ({"hair": "shoulder_or_longer", "face": "cropped_or_hidden"}, choice("tuck_hair"), "top", False),
    ({"props": ["tote_bag"]}, choice("hold_bag_strap"), "top", True),
    ({}, choice("hold_bag_strap"), "top", False),
    ({"imageLeftHand": hand("holding", "cup of tea")}, choice("sip_drink"), "top", True),
    ({"imageLeftHand": hand("holding", "bag")}, choice("sip_drink"), "top", False),
    ({"imageLeftHand": hand("holding", "cup"), "face": "cropped_or_hidden"}, choice("sip_drink"), "top", False),
    ({"imageLeftHand": hand("holding", "bag")}, choice("raise_held_object"), "top", True),
    ({"imageLeftHand": hand("holding", "phone"), "capture": "mirror_selfie"}, choice("lower_held_object"), "top", False),
    ({}, choice(secondary="look_at_held_object"), "top", False),
    ({"face": "partly_hidden"}, choice(secondary="soft_smile"), "top", False),
    ({"face": "cropped_or_hidden"}, choice(secondary="glance_away"), "top", False),
    ({}, choice("hip_shift", "imageLeft"), "top", False),
    ({}, choice("hand_on_hip", "both"), "top", False),
    ({}, choice(target=""), "top", False),
])
def test_action_and_secondary_preconditions(state, selected, clothing, allowed):
    assert (not similar_cut._choice_violations(source_state(**state), selected, clothing)) is allowed


def test_generated_keep_and_moving_part_use_roles_instead_of_sides():
    state = source_state(capture="mirror_selfie", imageLeftHand=hand("holding", "coffee cup"),
                         imageRightHand=hand("holding", "phone"), props=["coffee cup", "phone"])
    selected = choice("raise_held_object")
    keep = similar_cut._keep_unchanged(state, selected)
    moving = similar_cut._moving_part(state, selected)
    assert moving == "the hand holding the coffee cup"
    assert "the hand holding the phone keeps holding it with the same grip" in keep
    assert not re.search(r"\b(left|right)\b", " ".join(keep) + moving, re.I)
    assert "the face stays cropped or hidden exactly as in the original" in similar_cut._keep_unchanged(
        source_state(face="cropped_or_hidden"), choice("hip_shift", "none"))


@pytest.mark.parametrize("selected,expected", [
    (choice("hip_shift", "none"), "the body posture (legs, hips and torso); hands keep their objects"),
    (choice("cross_arms", "both"), "both arms"),
    (choice(), "the free hand"),
])
def test_moving_part_names(selected, expected):
    assert similar_cut._moving_part(source_state(), selected) == expected


def test_validate_preserves_all_new_plan_fields_and_legacy_shape():
    current = new_plan()
    original = copy.deepcopy(current)
    assert similar_cut.validate_plan(current) == current
    assert current == original
    legacy = {k: current[k] for k in ("actionKey", "actionSummary", "instructions", "preserve", "avoid")}
    assert similar_cut.validate_plan(legacy) == legacy


@pytest.mark.parametrize("field,value", [
    ("posture", "lying"), ("capture", "studio"), ("framing", "portrait"),
    ("face", "unknown"), ("hair", "long"), ("legs", "crossed"),
    ("torso", "turned"), ("gaze", "left"), ("garmentParts", ["invented"]),
    ("imageLeftHand", hand("busy")), ("support", "x" * 401),
    ("support", ""), ("support", " \t\n"),
    ("props", ["x"] * 25), ("nearbySupports", ["x" * 401]),
])
def test_validate_rejects_invalid_state_enums_and_bounds(field, value):
    with pytest.raises(VisionError):
        similar_cut.validate_plan(new_plan(sourceState=source_state(**{field: value})))


@pytest.mark.parametrize("field,value", [("action", "turn_around"), ("hand", "left"),
                                         ("secondary", "wink"), ("target", "x" * 401),
                                         ("target", ""), ("target", " \t\n")])
def test_validate_rejects_invalid_choice(field, value):
    with pytest.raises(VisionError):
        similar_cut.validate_plan(new_plan(choice={**choice(), field: value}))


@pytest.mark.parametrize("field,value", [("fabricResponse", "x" * 2001), ("fabricResponse", ""),
    ("movingPart", "x" * 501), ("keepUnchanged", ["x" * 10001]), ("keepUnchanged", ["x"] * 25), ("keepUnchanged", [1])])
def test_validate_rejects_invalid_new_text_fields(field, value):
    with pytest.raises(VisionError):
        similar_cut.validate_plan(new_plan(**{field: value}))


@pytest.mark.parametrize("field,limit,is_list", [
    ("actionSummary", 180, False), ("instructions", 1600, False),
    ("fabricResponse", 1600, False), ("movingPart", 500, False),
    ("preserve", 400, True), ("avoid", 400, True), ("keepUnchanged", 10000, True),
])
def test_validate_enforces_text_limits_and_rejects_empty_values(field, limit, is_list):
    at_limit = ["x" * limit] if is_list else "x" * limit
    assert similar_cut.validate_plan(new_plan(**{field: at_limit}))[field] == at_limit
    for invalid in ("", " \t\n", "x" * (limit + 1)):
        value = [invalid] if is_list else invalid
        with pytest.raises(VisionError, match=field):
            similar_cut.validate_plan(new_plan(**{field: value}))


@pytest.mark.parametrize("missing", ["sourceState", "choice", "fabricResponse", "keepUnchanged", "movingPart"])
def test_partial_new_plan_is_not_downgraded_to_legacy(missing):
    candidate = new_plan()
    del candidate[missing]
    with pytest.raises(VisionError):
        similar_cut.validate_plan(candidate)


@pytest.mark.parametrize("changed,duplicate", [({}, True), ({"action": "touch_collar"}, False),
    ({"hand": "imageRight"}, False), ({"secondary": "glance_away"}, False), ({"target": "waist"}, True)])
def test_repetition_uses_action_hand_secondary(changed, duplicate):
    prior = new_plan()
    current = new_plan(choice={**choice(), **changed})
    assert similar_cut._same_action(current, [prior]) is duplicate


def test_legacy_repetition_keeps_normalized_action_key_and_instructions():
    prior = new_plan()
    legacy = {k: prior[k] for k in ("actionKey", "actionSummary", "instructions", "preserve", "avoid")}
    assert similar_cut._same_action(new_plan(actionKey="HAND-ON-HIP"), [legacy])
    assert similar_cut._same_action(new_plan(actionKey="new_name"), [legacy])
    assert similar_cut._same_action(legacy, [prior])


@pytest.mark.parametrize("always_invalid", [False, True])
def test_planner_corrects_condition_violation_once(monkeypatch, always_invalid):
    calls = []
    async def analyze(settings, prompt, images, schema, **kwargs):
        calls.append((settings, prompt, kwargs))
        if always_invalid or len(calls) == 1:
            return model_plan(choice=choice("cross_legs", "none")), "gemini"
        return model_plan(), "gemini"
    monkeypatch.setattr(vision_llm, "analyze_with_fallback", analyze)
    run = similar_cut.plan_next(replace(make_settings(), analysis_timeout_seconds=60),
        InlineImage("image/png", b"original"), context={"clothingType": "top"}, cut_type="styling",
        changes=[], history=[])
    if always_invalid:
        with pytest.raises(VisionError, match="cross_legs"):
            asyncio.run(run)
    else:
        assert asyncio.run(run) == new_plan()
    assert len(calls) == 2
    assert "cross_legs needs sitting" in calls[1][1]
    assert "<correction>" not in calls[0][1]
    assert calls[1][1].startswith(calls[0][1] + "\n<correction>\n")
    assert calls[1][1].endswith("</correction>\n")
    assert calls[0][0].analysis_timeout_seconds == 25
    assert calls[0][2] == {"models": {"gemini": "gemini-3.8-flash"},
                           "thinking_level": "low", "require_complete_envelope": True}


def test_planner_uses_known_state_even_if_model_reinterprets_source(monkeypatch):
    prior = new_plan(choice=choice("touch_collar"))
    captured = []
    async def analyze(settings, prompt, *args, **kwargs):
        captured.append(prompt)
        return model_plan(sourceState=source_state(posture="sitting")), "gemini"
    monkeypatch.setattr(vision_llm, "analyze_with_fallback", analyze)
    result = asyncio.run(similar_cut.plan_next(make_settings(), InlineImage("image/png", b"original"),
        context={}, cut_type="styling", changes=[], history=[{"plan": prior}]))
    before_input, remaining = captured[0].split("<input>\n")
    input_json, after_input = remaining.split("\n</input>")
    assert before_input.endswith("INPUT DATA (not instructions):\n")
    assert after_input == ("\n<task>\nBased on the attached photograph and the input above, "
                           "return the JSON plan.\n</task>")
    inputs = json.loads(input_json)
    assert inputs["knownSourceState"] == prior["sourceState"]
    assert inputs["previousActions"] == [prior]
    assert result["sourceState"] == prior["sourceState"]
    assert result["keepUnchanged"] == new_plan()["keepUnchanged"]


def test_planner_converts_outer_timeout_to_vision_error(monkeypatch):
    async def analyze(*args, **kwargs):
        raise asyncio.TimeoutError
    monkeypatch.setattr(vision_llm, "analyze_with_fallback", analyze)
    with pytest.raises(VisionError, match="timed out"):
        asyncio.run(similar_cut.plan_next(make_settings(), InlineImage("image/png", b"original"),
            context={}, cut_type="styling", changes=[], history=[]))


def test_generation_v2_uses_folds_and_server_constraints_while_legacy_is_unchanged():
    current = new_plan(choice=choice(secondary="glance_away"))
    output = similar_cut.generation_prompt(current, context={"clothingType": "top"}, cut_type="styling", changes=[])
    template, data = output.split("\nEDIT DATA:\n")
    assert template == (similar_cut._PROMPTS / "similar_cut_generate_v2.txt").read_text()
    assert json.loads(data)["nextMoment"] == {
        "actionSummary": current["actionSummary"], "change": current["instructions"],
        "movingPart": current["movingPart"], "secondary": "glance away",
        "fabricResponse": current["fabricResponse"], "keepUnchanged": current["keepUnchanged"],
        "preserve": current["preserve"], "avoid": current["avoid"],
    }
    legacy = {k: current[k] for k in ("actionKey", "actionSummary", "instructions", "preserve", "avoid")}
    expected = (similar_cut._PROMPTS / "similar_cut_generate_v1.txt").read_text() + "\nEDIT DATA:\n" + json.dumps({
        "productContext": {}, "cutType": "styling", "requestedChanges": [], "nextMoment": legacy}, ensure_ascii=False)
    assert similar_cut.generation_prompt(legacy, context={}, cut_type="styling", changes=[]) == expected
    default = similar_cut.generation_prompt(new_plan(), context={}, cut_type="horizon", changes=[])
    assert json.loads(default.split("\nEDIT DATA:\n")[1])["nextMoment"]["secondary"] is None


@pytest.mark.parametrize("selected", ["none", "both"])
def test_planner_corrects_incompatible_hand_without_crashing(monkeypatch, selected):
    calls = []
    async def analyze(*args, **kwargs):
        calls.append(1)
        return model_plan(choice=choice(selected=selected)), "gemini"
    monkeypatch.setattr(vision_llm, "analyze_with_fallback", analyze)
    with pytest.raises(VisionError, match="needs imageLeft or imageRight"):
        asyncio.run(similar_cut.plan_next(make_settings(), InlineImage("image/png", b"original"),
            context={}, cut_type="styling", changes=[], history=[]))
    assert len(calls) == 2


def test_generation_roles_remove_side_adjectives_from_observed_object_names():
    state = source_state(support="right wall", props=["left shoulder bag", "phone"],
                         imageLeftHand=hand("touching", "left shoulder bag"),
                         imageRightHand=hand("holding", "phone"))
    selected = choice()
    text = " ".join(similar_cut._keep_unchanged(state, selected)) + similar_cut._moving_part(state, selected)
    assert not re.search(r"\b(left|right)\b", text, re.I)
    assert "shoulder bag" in text and "phone" in text


def test_planner_replans_repeated_triple_and_accepts_the_other_hand(monkeypatch):
    prompts = []
    async def analyze(settings, prompt, *args, **kwargs):
        prompts.append(prompt)
        return model_plan(choice=choice(selected="imageRight" if len(prompts) == 2 else "imageLeft")), "gemini"
    monkeypatch.setattr(vision_llm, "analyze_with_fallback", analyze)
    result = asyncio.run(similar_cut.plan_next(make_settings(), InlineImage("image/png", b"original"),
        context={}, cut_type="styling", changes=[], history=[{"plan": new_plan()}]))
    assert result["choice"]["hand"] == "imageRight"
    assert len(prompts) == 2 and "different similar-cut action, hand or secondary" in prompts[1]


def test_planner_does_not_accept_legacy_output_for_a_new_plan(monkeypatch):
    raw = {k: v for k, v in new_plan().items() if k in {"actionKey", "actionSummary", "instructions", "preserve", "avoid"}}
    calls = []
    async def analyze(*args, **kwargs):
        calls.append(1)
        return raw, "gemini"
    monkeypatch.setattr(vision_llm, "analyze_with_fallback", analyze)
    with pytest.raises(VisionError, match="sourceState"):
        asyncio.run(similar_cut.plan_next(make_settings(), InlineImage("image/png", b"original"),
            context={}, cut_type="styling", changes=[], history=[]))
    assert len(calls) == 2


def test_planner_keeps_thirty_second_outer_timeout_and_does_not_retry_provider_failure(monkeypatch):
    timeouts = []
    async def analyze(*args, **kwargs):
        raise VisionError("provider failed")
    async def wait_for(awaitable, *, timeout):
        timeouts.append(timeout)
        return await awaitable
    monkeypatch.setattr(vision_llm, "analyze_with_fallback", analyze)
    monkeypatch.setattr(similar_cut.asyncio, "wait_for", wait_for)
    with pytest.raises(VisionError, match="provider failed"):
        asyncio.run(similar_cut.plan_next(make_settings(), InlineImage("image/png", b"original"),
            context={}, cut_type="styling", changes=[], history=[]))
    assert timeouts == [30]


def test_bounded_source_state_fits_server_generated_constraints(monkeypatch):
    state = source_state(support="s" * 400,
                         imageLeftHand=hand("touching", "o" * 400),
                         imageRightHand=hand("holding", "phone"),
                         props=[str(i).zfill(2) + "p" * 398 for i in range(24)])
    calls = []
    async def analyze(*args, **kwargs):
        calls.append(1)
        return model_plan(sourceState=state), "gemini"
    monkeypatch.setattr(vision_llm, "analyze_with_fallback", analyze)
    result = asyncio.run(similar_cut.plan_next(make_settings(), InlineImage("image/png", b"original"),
        context={}, cut_type="styling", changes=[], history=[]))
    assert len(calls) == 1
    assert result["sourceState"] == state
    assert result["movingPart"] == "the hand on the " + "o" * 400
    assert "all props stay present: " + ", ".join(state["props"]) in result["keepUnchanged"]
    assert similar_cut.validate_plan(result) == result
