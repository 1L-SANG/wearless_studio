import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase/migrations/20261005000000_facemarket_publication_jpeg.sql"


def executable_sql() -> str:
    sql = MIGRATION.read_text(encoding="utf-8")
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return " ".join(sql.split()).lower()


def test_forward_migration_adds_jpeg_without_editing_the_original_migration():
    assert MIGRATION.exists()
    sql = executable_sql()
    assert "alter table public.fm_publication_records" in sql
    assert "drop constraint if exists fm_publication_records_kind_check" in sql
    assert "add constraint fm_publication_records_kind_check" in sql
    assert "check (kind in ('long_png', 'block_png', 'zip', 'jpeg'))" in sql


def test_original_provenance_migration_remains_the_historical_three_kind_definition():
    original = (ROOT / "supabase/migrations/20260904000000_facemarket_provenance.sql").read_text(
        encoding="utf-8"
    )
    assert "kind in ('long_png', 'block_png', 'zip')" in original
    assert "'jpeg'" not in original
