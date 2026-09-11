"""fm_model_loras — 실존 등록자별 LoRA 장부와 그 조회 경로.

얼굴 패스 근거가 가상모델 JSON 레지스트리 하나였던 것을 DB(등록자)까지 넓힌 변경의 회귀 고정.
가상모델 경로는 **바이트 단위로 그대로**여야 한다 — 여기서 같이 고정한다.
"""

import asyncio
import pathlib

import pytest

from app.agents import cut_generator, face_identity
from app.agents.identity_source import profiles_from_lora_row, resolve_enabled_lora
from app.facemarket_physique import (
    FACE_SHAPES,
    HAIR_COLORS,
    HAIR_LENGTHS,
    HAIR_TEXTURES,
    JAW_LINES,
)
from conftest import make_settings

_MODEL_ID = "44444444-4444-4444-4444-444444444444"
_MIGRATION = (
    pathlib.Path(__file__).resolve().parents[2]
    / "supabase" / "migrations" / "20260911000100_fm_model_loras.sql"
)


def _row(**over):
    row = {
        "id": "55555555-5555-5555-5555-555555555555",
        "version": 1,
        "lora_r2_key": "facemarket/models/m/loras/v1.safetensors",
        "bucket": "face",
        "trigger_token": "ohwx man",
        "hair_length": "short",
        "hair_color": "black",
        "hair_texture": "straight",
        "face_shape": "oval",
        "jaw_line": "defined",
        "trained_steps": 1500,
    }
    row.update(over)
    return row


class _Cur:
    def __init__(self, row, raise_exc=None):
        self._row = row
        self._raise = raise_exc
        self.sql = None
        self.params = None

    async def execute(self, sql, params=None):
        self.sql, self.params = sql, params
        if self._raise is not None:
            raise self._raise

    async def fetchone(self):
        return self._row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, row, raise_exc=None):
        self.cur = _Cur(row, raise_exc)

    def cursor(self):
        return self.cur


class _ExplodingConn:
    def cursor(self):
        raise AssertionError("가상모델 id 는 UUID 가 아니다 — DB 에 닿으면 안 된다")


# ── 마이그레이션: enum 이 프롬프트 모듈과 갈리면 값이 조용히 생략된다 ──
def test_migration_enums_match_physique_module():
    sql = _MIGRATION.read_text()
    for column, values in (
        ("hair_length", HAIR_LENGTHS), ("hair_color", HAIR_COLORS),
        ("hair_texture", HAIR_TEXTURES), ("face_shape", FACE_SHAPES), ("jaw_line", JAW_LINES),
    ):
        rendered = ",".join(f"'{v}'" for v in values)
        assert f"check ({column} is null or {column} in ({rendered}))" in sql, column


def test_migration_shape_is_additive_and_single_enabled():
    sql = _MIGRATION.read_text()
    assert "create table if not exists public.fm_model_loras" in sql
    assert "references public.fm_models(id) on delete cascade" in sql
    # 켜진 LoRA 는 모델당 최대 하나 — resolve_enabled_lora 의 limit 1 이 임의 선택이 되지 않는 근거
    assert "on public.fm_model_loras (model_id) where enabled" in sql
    assert "check (bucket = 'face')" in sql
    assert "enable row level security" in sql
    # PG16 안전: add constraint 앞에 drop constraint if exists (선례 20260828)
    assert sql.count("drop constraint if exists") == sql.count("add constraint")


# ── resolve_enabled_lora ──
def test_no_enabled_row_yields_no_spec():
    conn = _Conn(None)
    assert asyncio.run(resolve_enabled_lora(conn, _MODEL_ID)) is None
    assert "enabled" in conn.cur.sql and "status = 'ready'" in conn.cur.sql
    assert face_identity.face_identity_from_lora_row(None) is None


def test_enabled_row_yields_spec():
    row = asyncio.run(resolve_enabled_lora(_Conn(_row()), _MODEL_ID))
    assert row["lora_r2_key"] == "facemarket/models/m/loras/v1.safetensors"
    spec = face_identity.face_identity_from_lora_row(row)
    assert spec == face_identity.FaceIdentitySpec(
        "facemarket/models/m/loras/v1.safetensors", "ohwx man")


@pytest.mark.parametrize("over", [
    {"bucket": "main"},          # r2_face 아닌 버킷 — 파기가 못 지우는 자리라 받지 않는다
    {"lora_r2_key": "   "},
    {"lora_r2_key": None},
])
def test_unusable_rows_are_rejected(over):
    assert asyncio.run(resolve_enabled_lora(_Conn(_row(**over)), _MODEL_ID)) is None


def test_missing_table_or_db_error_falls_back_to_no_face_pass():
    # 마이그 미적용 환경에서도 컷은 계속 만들어야 한다 — 얼굴 패스만 안 걸린다.
    conn = _Conn(None, raise_exc=RuntimeError('relation "fm_model_loras" does not exist'))
    assert asyncio.run(resolve_enabled_lora(conn, _MODEL_ID)) is None


def test_virtual_model_id_never_reaches_db():
    for vid in ("mA", "mB", "model-1", ""):
        assert asyncio.run(resolve_enabled_lora(_ExplodingConn(), vid)) is None


def test_trigger_token_falls_back_to_default():
    spec = face_identity.face_identity_from_lora_row(_row(trigger_token=None))
    assert spec.token == face_identity.DEFAULT_TOKEN


# ── profiles_from_lora_row ──
def test_profiles_are_camel_case_for_prompt_blocks():
    hair, face = profiles_from_lora_row(_row())
    assert hair == {"hairLength": "short", "hairColor": "black", "hairTexture": "straight"}
    assert face == {"faceShape": "oval", "jawLine": "defined"}


def test_absent_columns_yield_none_so_prompt_is_unchanged():
    assert profiles_from_lora_row(None) == (None, None)
    assert profiles_from_lora_row(_row(
        hair_length=None, hair_color=None, hair_texture=None,
        face_shape=None, jaw_line=None)) == (None, None)
    hair, face = profiles_from_lora_row(_row(hair_color=None, jaw_line=None))
    assert hair == {"hairLength": "short", "hairTexture": "straight"}
    assert face == {"faceShape": "oval"}


# ── cut_generator._face_identity_spec — provided(DB) 우선, 레지스트리는 무회귀 ──
def _spec():
    return {"cutType": "styling", "modelId": _MODEL_ID, "shot": "full",
            "direction": "front", "faceExposure": "same"}


def _settings(enabled=True):
    return make_settings(gemini_api_key="x", r2_bucket="b", face_identity_enabled=enabled)


def test_provided_spec_wins_and_registry_is_not_read(monkeypatch):
    monkeypatch.setattr(cut_generator, "load_virtual_model_registry",
                        lambda: (_ for _ in ()).throw(AssertionError("registry must not be read")))
    provided = face_identity.FaceIdentitySpec("r2/key.safetensors", "ohwx man")
    assert cut_generator._face_identity_spec(_settings(), _spec(), "top", provided) is provided


def test_virtual_models_have_no_face_pass_path(monkeypatch):
    """가상모델 JSON(faceIdentity) 경로는 2026-09-11 삭제 — 항목이 0개였고, 근거가 두 곳이면
    "왜 이 컷만 얼굴이 바뀌었나"를 두 군데서 찾게 된다. 근거는 fm_model_loras 하나다."""
    monkeypatch.setattr(cut_generator, "load_virtual_model_registry",
                        lambda: {"mA": {"faceIdentity": {"loraPath": "local/mA.safetensors"}}})
    spec = dict(_spec(), modelId="mA")
    assert cut_generator._face_identity_spec(_settings(), spec, "top") is None
    assert cut_generator._face_identity_spec(_settings(), spec, "top", None) is None
    assert not hasattr(face_identity, "face_identity_from_registry_entry")


@pytest.mark.parametrize("spec_over,flag", [
    ({}, False),                                   # 플래그 off
    ({"cutType": "product"}, True),                # 착용컷 아님
    ({"faceExposure": "hide"}, True),              # 얼굴이 안 담김
    ({"direction": "back"}, True),
    ({"modelId": None}, True),
])
def test_gates_still_win_over_provided_spec(spec_over, flag):
    provided = face_identity.FaceIdentitySpec("r2/key.safetensors", "ohwx man")
    assert cut_generator._face_identity_spec(
        _settings(flag), dict(_spec(), **spec_over), "top", provided) is None


def test_bottom_medium_shot_has_no_face_even_with_lora():
    provided = face_identity.FaceIdentitySpec("r2/key.safetensors", "ohwx man")
    spec = dict(_spec(), shot="medium")
    assert cut_generator._face_identity_spec(_settings(), spec, "bottom", provided) is None
    assert cut_generator._face_identity_spec(_settings(), spec, "top", provided) is provided
