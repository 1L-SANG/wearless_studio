"""지원서 UI 리뉴얼 마이그레이션(사진 4종·전화·경력·확인 서명) 구조 검증 — SQL 텍스트 레벨."""

from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[2] / (
    "supabase/migrations/20260902160000_facemarket_application_photos_x4.sql"
)


def _sql() -> str:
    return " ".join(MIGRATION.read_text().split()).lower()


def test_staging_becomes_per_kind_slot():
    sql = _sql()
    assert "add column if not exists kind" in sql
    assert "add primary key (user_id, kind)" in sql
    for k in ("profile", "closeup", "waist_up", "full_length"):
        assert k in sql, k


def test_application_gets_photo_map_and_details():
    sql = _sql()
    for col in ("phone", "experience_level", "photo_keys", "attestations"):
        assert f"add column if not exists {col}" in sql, col


def test_experience_level_enum():
    sql = _sql()
    assert "experience_level_check" in sql
    for v in ("none", "beginner", "intermediate", "professional"):
        assert v in sql, v
