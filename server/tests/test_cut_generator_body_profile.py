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


# ---------------------------------------------------------------- 머리 블록 (LoRA 가 학습한 머리)


def _product_spec():
    return {"cutType": "product", "shot": "ghost", "colorId": None}


def test_render_includes_hair_block_when_profile_present():
    prompt = cut_generator.render_cut_prompt(
        cut_generator.load_cut_template(), cut_generator.normalize_spec(_spec(), clothing_type="top"),
        {"clothing_type": "top"}, {}, "top", "", has_face=True,
        hair_profile={"hairLength": "short", "hairColor": "black", "hairTexture": "straight"},
    )
    assert "SUBJECT HAIR" in prompt
    assert "short, straight, black hair" in prompt
    assert "no authority over the face" in prompt


def test_render_hair_block_accepts_partial_profile():
    prompt = cut_generator.render_cut_prompt(
        cut_generator.load_cut_template(), cut_generator.normalize_spec(_spec(), clothing_type="top"),
        {"clothing_type": "top"}, {}, "top", "", has_face=True,
        hair_profile={"hairLength": "long"},
    )
    assert "the model has long hair." in prompt


def test_render_omits_hair_block_when_none_byte_identical():
    """값이 없으면 프롬프트가 기존과 바이트 단위로 동일해야 한다."""
    args = (cut_generator.load_cut_template(),
            cut_generator.normalize_spec(_spec(), clothing_type="top"),
            {"clothing_type": "top"}, {}, "top", "")
    base = cut_generator.render_cut_prompt(*args, has_face=True)
    for profile in (None, {}, {"hairColor": "other"}):
        assert cut_generator.render_cut_prompt(*args, has_face=True, hair_profile=profile) == base


def test_render_omits_hair_block_on_product_cut():
    """product 컷은 사람이 없다 — 머리 블록을 내지 않는다."""
    prompt = cut_generator.render_cut_prompt(
        cut_generator.load_cut_template(),
        cut_generator.normalize_spec(_product_spec(), clothing_type="top"),
        {"clothing_type": "top"}, {}, "top", "",
        hair_profile={"hairLength": "short", "hairColor": "black", "hairTexture": "straight"},
    )
    assert "SUBJECT HAIR" not in prompt


def test_build_prompt_threads_hair_profile():
    product = {"clothing_type": "top", "colors": [{"id": "c1", "isBase": True, "images": []}]}
    prompt = cut_generator.build_prompt(
        _spec(), product,
        hair_profile={"hairLength": "medium", "hairTexture": "wavy", "hairColor": "brown"})
    assert "medium-length, wavy, brown hair" in prompt
