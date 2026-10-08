from pathlib import Path

import psycopg
import pytest

import test_client_events_db as db


def test_ci_db_job_runs_all_client_event_database_files():
    workflow = (Path(__file__).resolve().parents[2] / '.github/workflows/deploy-server.yml').read_text()
    unit, database = workflow.split('    test-db:', 1)
    database = database.split('    changes:', 1)[0]
    for filename in ('test_client_events_db.py', 'test_client_event_salts.py', 'test_client_alert_limits_db.py'):
        assert 'tests/' + filename in database
        assert '--ignore=tests/' + filename in unit
    assert 'CLIENT_EVENTS_REQUIRE_TEST_DB: "1"' in database
    assert 'CLIENT_EVENTS_REQUIRE_TEST_DB' not in unit


@pytest.mark.parametrize('required', [False, True])
def test_database_unavailable_is_failure_only_when_required(monkeypatch, required):
    if required:
        monkeypatch.setenv('CLIENT_EVENTS_REQUIRE_TEST_DB', '1')
    else:
        monkeypatch.delenv('CLIENT_EVENTS_REQUIRE_TEST_DB', raising=False)

    def unavailable(*args, **kwargs):
        raise psycopg.OperationalError('local test database unavailable')

    monkeypatch.setattr(db.psycopg, 'connect', unavailable)
    try:
        db.local_db.__wrapped__()
    except pytest.skip.Exception:
        assert not required, 'CI DB job skipped an unreachable database'
    except pytest.fail.Exception:
        assert required
    else:
        pytest.fail('Database connection failure was ignored')
