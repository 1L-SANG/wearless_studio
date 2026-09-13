"""앞머리(hair_fringe) — 길이·머릿결·색으로는 표현이 안 되는 축.

왜 생겼나(2026-09-13 운영 테스트컷): LoRA 값이 short/black/straight 뿐이라 gpt-image 바탕이
**이마를 드러낸 짧은 머리**를 그렸는데 학습된 얼굴(v7)은 눈썹까지 오는 앞머리였다. 얼굴 패스는
타원 안만 바꾸므로 헤어라인이 어긋난 채 남고, 얼굴이 큰 클로즈업에서는 타원 밖에 남은 원본 머리
끝이 **공중에 뜬 덩어리**가 됐다(8장 중 3장 폐기). 바탕을 처음부터 맞게 그리게 하는 것이 수습이다.
"""

import pathlib

import pytest

from app import facemarket_physique as ph
from app.agents import identity_source

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_the_enum_and_its_labels_stay_in_step():
    assert ph.HAIR_FRINGES == ("none", "side_swept", "brow", "eye")
    assert set(ph._HAIR_FRINGE_LABELS) == set(ph.HAIR_FRINGES)
    for ko, en in ph._HAIR_FRINGE_LABELS.values():
        assert ko and en


@pytest.mark.parametrize("fringe,needle", [
    ("none", "no fringe, with the forehead visible"),
    ("side_swept", "a side-swept fringe"),
    ("brow", "reaching the eyebrows"),
    ("eye", "touching the eyes"),
])
def test_the_block_says_what_the_fringe_is(fringe, needle):
    block = ph.build_hair_block({"hairLength": "short", "hairTexture": "straight",
                                 "hairColor": "black", "hairFringe": fringe})
    assert needle in block
    assert "short, straight, black hair with " in block


def test_without_a_fringe_the_block_is_byte_identical_to_before():
    """기존 행은 hair_fringe 가 null 이다 — 그 컷들의 프롬프트가 한 바이트도 바뀌면 안 된다."""
    profile = {"hairLength": "short", "hairTexture": "straight", "hairColor": "black"}
    assert ph.build_hair_block(profile) == (
        "SUBJECT HAIR (generated; owned by the registrant's trained likeness): the model has "
        "short, straight, black hair. Keep it consistent across cuts; it has no authority over the face."
    )
    assert ph.build_hair_block(dict(profile, hairFringe=None)) == ph.build_hair_block(profile)
    assert ph.build_hair_block(dict(profile, hairFringe="상고머리")) == ph.build_hair_block(profile)


def test_a_fringe_alone_still_produces_a_block():
    """길이·색을 모르고 앞머리만 아는 경우도 낸다 — 헤어라인이 어긋나는 게 더 비싸다."""
    block = ph.build_hair_block({"hairFringe": "brow"})
    assert "hair with a straight fringe reaching the eyebrows" in block
    assert ph.build_hair_block({}) == ""
    assert ph.build_hair_block(None) == ""


def test_validate_accepts_the_new_axis_and_refuses_junk():
    ph.validate_hair(hair_length="short", hair_color="black", hair_texture="straight", hair_fringe="brow")
    ph.validate_hair(hair_length=None, hair_color=None, hair_texture=None, hair_fringe=None)
    with pytest.raises(ph.PhysiqueError):
        ph.validate_hair(hair_length=None, hair_color=None, hair_texture=None, hair_fringe="bangs")


def test_the_lora_row_carries_the_fringe_into_the_prompt_profile():
    hair, face = identity_source.profiles_from_lora_row(
        {"hair_length": "short", "hair_color": "black", "hair_texture": "straight",
         "hair_fringe": "brow", "face_shape": "round", "jaw_line": "soft"})
    assert hair == {"hairLength": "short", "hairColor": "black",
                    "hairTexture": "straight", "hairFringe": "brow"}
    assert face == {"faceShape": "round", "jawLine": "soft"}
    # 옛 행(컬럼 없음)도 그대로 돈다
    old, _ = identity_source.profiles_from_lora_row(
        {"hair_length": "short", "hair_color": "black", "hair_texture": "straight"})
    assert "hairFringe" not in old


def test_a_database_without_the_column_still_serves_the_face_pass():
    """배포가 마이그레이션보다 먼저 나가도 얼굴 패스가 꺼지면 안 된다 — 그 컬럼만 빼고 다시 묻는다."""
    assert "hair_fringe" in identity_source._LORA_COLUMNS
    assert "hair_fringe" not in identity_source._LORA_COLUMNS_LEGACY
    src = pathlib.Path(identity_source.__file__).read_text(encoding="utf-8")
    body = src.split("async def resolve_enabled_lora")[1]
    assert "_LORA_COLUMNS_LEGACY" in body, "옛 스키마 재시도 경로가 있어야 한다"


# ── 마이그레이션 ────────────────────────────────────────────────────────────
MIGRATION = ROOT / "supabase/migrations/20260913120000_fm_model_loras_hair_fringe.sql"


def test_the_migration_is_additive_and_pins_the_same_enum():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "add column if not exists hair_fringe text" in sql
    # 값 목록이 코드와 같아야 한다 — 한쪽만 늘면 조용히 거부된다
    for value in ph.HAIR_FRINGES:
        assert f"'{value}'" in sql
    assert "hair_fringe is null or" in sql, "기존 행(null)을 막지 않는다"
    for destructive in ("drop column", "alter column", "delete from", "update public.fm_model_loras"):
        assert destructive not in sql.lower(), destructive


def test_the_seed_script_can_set_it():
    text = (ROOT / "server/scripts/seed_model_lora.py").read_text(encoding="utf-8")
    assert '"--hair-fringe", choices=HAIR_FRINGES' in text
    assert "hair_fringe = excluded.hair_fringe" in text
