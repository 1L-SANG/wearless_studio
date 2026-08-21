from collections import Counter
from pathlib import Path


def test_supabase_migration_versions_are_unique():
    migrations = Path(__file__).parents[2] / "supabase" / "migrations"
    versions = [path.name.split("_", 1)[0] for path in migrations.glob("*.sql")]
    duplicates = sorted(version for version, count in Counter(versions).items() if count > 1)

    assert duplicates == []


def test_facemarket_migrations_follow_main_sam_migrations_in_dependency_order():
    migrations = Path(__file__).parents[2] / "supabase" / "migrations"
    names = (
        "20260821010000_sam_autoscale_index.sql",
        "20260821010100_facemarket_biometric_runtime.sql",
        "20260821010200_facemarket_mandatory_vc.sql",
        "20260821020000_facemarket_cutover_lifecycle.sql",
    )

    assert set(names) <= {path.name for path in migrations.glob("*.sql")}
    assert list(names) == sorted(names)
