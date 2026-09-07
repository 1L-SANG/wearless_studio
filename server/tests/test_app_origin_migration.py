"""profiles.app_origin 마이그레이션 구조 검증 — SQL 텍스트 레벨.

되돌아가면 둘이다. (1) CHECK 없이 열면 아무 문자열이나 들어가 콘솔 필터가 조용히 빈
목록을 준다. (2) 백필이 기존 가입자 전원을 한쪽으로 몰아 넣으면, 화면이 '미상' 을 말할
방법을 잃고 틀린 라벨이 사실처럼 보인다 — 빈 칸보다 나쁘다.
"""

from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[2] / (
    "supabase/migrations/20260907000000_profiles_app_origin.sql"
)


def _sql() -> str:
    return " ".join(MIGRATION.read_text().split()).lower()


def test_adds_the_column_to_profiles():
    assert "alter table public.profiles add column if not exists app_origin text" in _sql()


def test_only_three_values_are_storable():
    assert "check (app_origin in ('seller', 'facemarket', 'both'))" in _sql()


def test_column_is_nullable_so_unknown_stays_unknown():
    """null = '아직 모른다'. not null 로 열면 기본값을 지어내야 하고, 그게 곧 오라벨이다."""
    column = _sql().split("app_origin text", 1)[1].split(";", 1)[0]
    assert "not null" not in column


def test_backfill_touches_admins_only():
    backfill = _sql().split("update public.profiles", 1)[1].split(";", 1)[0]
    assert "set app_origin = 'both'" in backfill
    assert "where role = 'admin'" in backfill
    # 이미 값이 있는 행을 덮지 않는다 — 재실행해도 안전해야 한다.
    assert "app_origin is null" in backfill


def test_filter_and_paging_indexes_exist():
    sql = _sql()
    assert "profiles_app_origin_idx" in sql
    # 콘솔 목록은 (created_at desc, user_id desc) keyset 으로 넘긴다.
    assert "profiles_created_at_idx" in sql
    assert "(created_at desc, user_id desc)" in sql


def test_migration_is_rerunnable():
    """같은 마이그레이션이 두 번 적용돼도 죽지 않아야 한다(로컬 재적용·복구 절차)."""
    sql = _sql()
    assert "add column if not exists" in sql
    assert sql.count("create index if not exists") == 2
