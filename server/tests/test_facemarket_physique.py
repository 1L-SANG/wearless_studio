import pytest
from app.facemarket_physique import (
    validate_physique, build_body_profile_block, PhysiqueError,
    HEIGHT_BUCKETS, BODY_TYPES, GENDERS,
)

def test_vocab_shapes():
    assert GENDERS == ("male", "female")
    assert len(BODY_TYPES) == 7 and "toned" in BODY_TYPES and "glamorous" in BODY_TYPES
    assert HEIGHT_BUCKETS["male"][0] == "m_lt170"
    assert HEIGHT_BUCKETS["female"][-1] == "f_gte175"

def test_validate_accepts_partial_and_none():
    validate_physique(height_bucket=None, body_type=None, gender=None)
    validate_physique(height_bucket=None, body_type="slim", gender=None)

def test_validate_rejects_unknown_body_type():
    with pytest.raises(PhysiqueError) as e:
        validate_physique(height_bucket=None, body_type="hulk", gender=None)
    assert e.value.code == "invalid_physique"

def test_validate_bucket_gender_prefix_mismatch():
    # female 모델에 male 버킷 → 거부
    with pytest.raises(PhysiqueError) as e:
        validate_physique(height_bucket="m_180_185", body_type=None, gender="female")
    assert e.value.code == "invalid_physique"

def test_validate_bucket_requires_gender():
    with pytest.raises(PhysiqueError):
        validate_physique(height_bucket="m_180_185", body_type=None, gender=None)

def test_block_empty_when_nothing():
    assert build_body_profile_block(None) == ""
    assert build_body_profile_block({}) == ""

def test_block_renders_fixed_phrases_no_freetext():
    block = build_body_profile_block(
        {"gender": "male", "heightBucket": "m_180_185", "bodyType": "toned"}
    )
    assert "180" in block and "185" in block
    assert "toned" in block.lower() or "lean" in block.lower()
    assert "SUBJECT BUILD" in block
    # 얼굴 속성 미방출
    assert "face" not in block.lower() or "unchanged" in block.lower()

def test_block_partial_only_body_type():
    block = build_body_profile_block({"bodyType": "glamorous"})
    assert block  # 비어있지 않음
    assert "glamorous" in block.lower() or "curvy" in block.lower()
