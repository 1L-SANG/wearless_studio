import pathlib
from app.workers.dispatcher import _WORKERS

SERVER = pathlib.Path(__file__).resolve().parents[1]


def test_kind_registered_and_in_db_constraint():
    assert "matching_cutout" in _WORKERS
    migrations = sorted((SERVER.parent / "supabase" / "migrations").glob("*.sql"))
    latest = ""
    for p in migrations:
        text = p.read_text(encoding="utf-8")
        if "jobs_kind_check" in text and "add constraint" in text:
            latest = text
    assert "'matching_cutout'" in latest


def test_flag_defaults_off():
    from app.config import Settings
    assert Settings.__dataclass_fields__["matching_cutout"].default == "off"
