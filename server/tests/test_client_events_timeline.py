from uuid import uuid4

import pytest

from scripts import client_events_timeline as timeline


def test_timeline_orders_breadcrumbs_and_events():
    event_id = str(uuid4())
    rows = [{'event_id': event_id, 'occurred_at': '2026-10-08T09:00:00+09:00',
             'received_at': '2026-10-08T09:01:00+09:00', 'request_id': str(uuid4()),
             'payload': {'breadcrumbs': [{'at': '2026-10-08T08:59:00+09:00', 'action': 'save'}]}}]
    result = timeline.assemble_timeline(rows)
    assert [entry['type'] for entry in result] == ['breadcrumb', 'event']
    assert result[0]['event_id'] == event_id


def test_investigation_requires_privileged_db_connection():
    class Conn:
        def execute(self, sql):
            return self

        def fetchone(self):
            return {'is_admin': False}

    with pytest.raises(PermissionError):
        timeline.require_admin_connection(Conn())


def test_timeline_includes_sanitized_diagnostic_context_and_build():
    from app.client_events import sanitize_event
    from test_client_event_context import diagnostics
    from test_client_events import sample

    clean = sanitize_event(sample(kind='error_boundary', **diagnostics()))
    row = {'event_id': clean['event_id'], 'occurred_at': clean['occurred_at'], 'build_sha': 'a' * 40,
           'payload': clean}
    result = timeline.assemble_timeline([row])[0]
    assert result['build_sha'] == 'a' * 40
    for field in ('browser', 'os', 'viewport', 'network', 'page_age_ms', 'step', 'message_code',
                  'visibility', 'tech_message', 'stack'):
        assert result[field] == clean[field]


def test_timeline_query_selects_build_sha():
    class Conn:
        def __init__(self):
            self.queries = []
        def execute(self, sql):
            return self
        def fetchone(self):
            return {'is_admin': True}
        def cursor(self):
            return self
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def fetchall(self):
            return []

    conn = Conn()
    original = conn.execute
    def record(sql, params=()):
        conn.queries.append(sql)
        return original(sql)
    conn.execute = record
    timeline.fetch_timeline(conn, request_id=str(uuid4()))
    assert 'build_sha' in conn.queries[1]
