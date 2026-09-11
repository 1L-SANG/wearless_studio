from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase/migrations/20260911130000_mannequin_cut_assets_not_real_derived.sql"


def test_backfill_is_scoped_to_mannequin_cut_assets():
    """백필 범위는 mannequin_cuts 조인 안쪽으로 못 박는다.

    이 마커는 `/assets/{id}/file` 을 R2 공개 URL + immutable 캐시로 돌려보낸다. 범위가
    `source = 'ai'` 같은 넓은 조건으로 벌어지면 계보가 불완전한 legacy detail-page 컷까지
    같이 풀려서, 0a7f33f6 이 막으려던 노출이 그대로 다시 열린다.
    """
    sql = MIGRATION.read_text().lower()

    assert "update public.assets" in sql
    assert "from public.mannequin_cuts mc" in sql
    assert "where mc.asset_id = a.id" in sql
    assert "'facemarket_real_derived', false" in sql
    # 넓은 분류 조건으로 대상을 잡지 않는다.
    assert "source = 'ai'" not in sql


def test_backfill_never_overwrites_an_existing_marker():
    """이미 true 로 표시된 자산을 되돌리면 안 되고, 재실행도 안전해야 한다."""
    sql = MIGRATION.read_text().lower()

    assert "not jsonb_exists(coalesce(a.metadata, '{}'::jsonb), 'facemarket_real_derived')" in sql
