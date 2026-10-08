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
