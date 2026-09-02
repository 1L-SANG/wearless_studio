"""컴카드 스펙 마이그레이션(T11) 구조 검증 — SQL 텍스트 레벨."""

from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[2] / (
    "supabase/migrations/20260902140000_personalization_compcard.sql"
)


def _sql() -> str:
    return " ".join(MIGRATION.read_text().split()).lower()


def test_adds_compcard_columns():
    sql = _sql()
    for col in ("bust_cm", "waist_cm", "hip_cm", "hair_color", "hair_length", "eye_color"):
        assert col in sql, col


def test_all_additive_nullable():
    # 전부 add column if not exists (기존 행·동작 무영향).
    assert _sql().count("add column if not exists") >= 6


def test_enum_checks_present():
    sql = _sql()
    assert "hair_color_check" in sql and "blonde" in sql
    assert "hair_length_check" in sql and "medium" in sql
    assert "eye_color_check" in sql and "hazel" in sql
