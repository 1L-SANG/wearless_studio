-- 화면 오류와 서버 작업 조사용 기록. 일반 클라이언트는 직접 접근하지 않는다.
create table public.client_error_events (
    event_id uuid primary key,
    schema_version integer not null check (schema_version = 1),
    occurred_at timestamptz not null,
    received_at timestamptz not null default clock_timestamp(),
    user_id uuid references auth.users(id) on delete cascade,
    build_sha text,
    signature text not null,
    kind text not null,
    route text not null,
    action text not null,
    status integer,
    error_code text,
    request_id uuid,
    project_id uuid,
    job_id uuid,
    device text not null,
    blocking boolean not null default false,
    ip_hmac text not null,
    payload jsonb not null
);
create index client_error_events_received_idx on public.client_error_events(received_at);
create index client_error_events_signature_idx on public.client_error_events(signature, received_at) include (user_id);
create index client_error_events_user_idx on public.client_error_events(user_id, received_at);
create index client_error_events_request_idx on public.client_error_events(request_id);

create table public.client_alert_ledger (
    kind text not null check (kind in ('burst', 'digest')),
    key text not null,
    sent_at timestamptz,
    attempted_at timestamptz not null,
    status text not null check (status in ('sending', 'sent', 'failed', 'empty')),
    primary key (kind, key)
);

-- 고정 1분 창의 공유 카운터. 거절 요청은 카운터를 더 늘리지 않는다.
create table public.client_event_rate_limits (
    scope text not null check (scope in ('ip', 'user', 'global')),
    key text not null,
    window_start timestamptz not null,
    request_count integer not null,
    primary key (scope, key, window_start)
);

-- KST 날짜마다 모든 API 인스턴스가 같은 무작위 salt를 공유한다.
create table public.client_event_salts (
    day date primary key,
    salt bytea not null check (octet_length(salt) = 32),
    created_at timestamptz not null default clock_timestamp()
);

alter table public.client_error_events enable row level security;
alter table public.client_alert_ledger enable row level security;
alter table public.client_event_rate_limits enable row level security;
alter table public.client_event_salts enable row level security;
revoke all on public.client_error_events, public.client_alert_ledger, public.client_event_rate_limits, public.client_event_salts from public, anon, authenticated;
grant all on public.client_error_events, public.client_alert_ledger, public.client_event_rate_limits, public.client_event_salts to service_role;
