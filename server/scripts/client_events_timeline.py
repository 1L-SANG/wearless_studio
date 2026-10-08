"""관리자 DB 연결로 화면 오류와 관련 작업을 시간순 조사한다. 읽기 전용."""

import argparse
import json
import os
from datetime import datetime
from uuid import UUID

import psycopg
from psycopg.rows import dict_row


def require_admin_connection(conn):
    row = conn.execute(
        "select rolsuper or rolbypassrls as is_admin from pg_roles where rolname = current_user"
    ).fetchone()
    if not row or not row['is_admin']:
        raise PermissionError('관리자 DB 역할(superuser 또는 BYPASSRLS)이 필요해요.')


def assemble_timeline(events):
    timeline = []
    for event in events:
        public = {key: value for key, value in event.items() if key != 'payload'}
        payload = event.get('payload') or {}
        for key in ('step', 'message_code', 'browser', 'os', 'viewport', 'network', 'page_age_ms',
                    'visibility', 'tech_message', 'stack', 'context'):
            if key in payload:
                public[key] = payload[key]
        timeline.append({'type': 'event', 'at': event['occurred_at'], **public})
        for crumb in payload.get('breadcrumbs', []):
            timeline.append({**crumb, 'type': 'breadcrumb', 'breadcrumb_type': crumb.get('type'),
                             'at': crumb.get('at') or event['occurred_at'], 'event_id': event['event_id']})

    def timestamp(entry):
        at = entry['at']
        return at if isinstance(at, datetime) else datetime.fromisoformat(at)

    return sorted(timeline, key=timestamp)


def fetch_timeline(conn, *, user_id=None, request_id=None):
    require_admin_connection(conn)
    if user_id:
        predicate, params = 'user_id = %s', (user_id,)
    else:
        predicate = """request_id = %s or exists (
            select 1 from jsonb_array_elements(payload->'breadcrumbs') crumb
            where crumb->>'request_id' = %s)"""
        params = (request_id, request_id)
    with conn.cursor() as cur:
        cur.execute(
            'select event_id::text, occurred_at, received_at, user_id::text, kind, route, action, '
            'status, error_code, request_id::text, job_id::text, project_id::text, build_sha, payload '
            f'from client_error_events where {predicate} order by occurred_at, received_at, event_id', params)
        events = cur.fetchall()
        ids = {e['job_id'] for e in events if e.get('job_id')}
        request_ids = {request_id} if request_id else set()
        for event in events:
            if event.get('request_id'):
                request_ids.add(event['request_id'])
            request_ids.update(c['request_id'] for c in (event.get('payload') or {}).get('breadcrumbs', []) if c.get('request_id'))
        cur.execute(
            "select id::text, kind, status, user_id::text, project_id::text, created_at, updated_at, finished_at, "
            "metadata->>'requestId' as request_id, error_message from jobs "
            "where (%s::uuid is not null and user_id = %s::uuid) "
            "or metadata->>'requestId' = any(%s::text[]) or id = any(%s::uuid[]) order by created_at, id",
            (user_id, user_id, sorted(request_ids), sorted(ids)))
        jobs = cur.fetchall()
    return {'timeline': assemble_timeline(events), 'jobs': jobs,
            'client_claimed_job_ids': sorted(ids)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument('--user-id', type=UUID)
    selector.add_argument('--request-id', type=UUID)
    args = parser.parse_args()
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        parser.error('관리자 DATABASE_URL을 환경변수로 지정해 주세요.')
    with psycopg.connect(database_url, row_factory=dict_row, connect_timeout=5) as conn:
        conn.execute('set transaction read only')
        conn.execute("set local statement_timeout = '30s'")
        result = fetch_timeline(conn, user_id=str(args.user_id) if args.user_id else None,
                                request_id=str(args.request_id) if args.request_id else None)
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
