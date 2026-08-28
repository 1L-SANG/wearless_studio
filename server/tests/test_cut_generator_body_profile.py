from app.agents import cut_generator


def _spec():
    return {"cutType": "styling", "shot": "full", "faceExposure": "show", "modelId": "m1"}


def test_render_includes_body_block_when_profile_present():
    prompt = cut_generator.render_cut_prompt(
        cut_generator.load_cut_template(), cut_generator.normalize_spec(_spec(), clothing_type="top"),
        {"clothing_type": "top"}, {}, "top", "", has_face=True,
        body_profile={"gender": "male", "heightBucket": "m_180_185", "bodyType": "toned"},
    )
    assert "SUBJECT BUILD" in prompt and "180" in prompt


def test_render_omits_body_block_when_none():
    prompt = cut_generator.render_cut_prompt(
        cut_generator.load_cut_template(), cut_generator.normalize_spec(_spec(), clothing_type="top"),
        {"clothing_type": "top"}, {}, "top", "", has_face=True, body_profile=None,
    )
    assert "SUBJECT BUILD" not in prompt
