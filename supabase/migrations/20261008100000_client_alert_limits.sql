-- 즉시 알림의 진행 위치와 최근 1시간 발송 예산을 모든 API 인스턴스가 공유한다.
alter table public.client_alert_ledger drop constraint client_alert_ledger_kind_check;
alter table public.client_alert_ledger add constraint client_alert_ledger_kind_check
    check (kind in ('burst', 'digest', 'overflow'));
alter table public.client_alert_ledger drop constraint client_alert_ledger_status_check;
alter table public.client_alert_ledger add constraint client_alert_ledger_status_check
    check (status in ('sending', 'sent', 'failed', 'empty', 'suppressed'));

create table public.client_alert_progress (
    singleton boolean primary key default true check (singleton),
    received_at timestamptz not null default '1970-01-01T00:00:00Z',
    event_id uuid not null default '00000000-0000-0000-0000-000000000000',
    suppressed_count bigint not null default 0,
    cases jsonb not null default '[]'::jsonb
);
insert into public.client_alert_progress default values;

create table public.client_alert_attempts (
    id uuid primary key default gen_random_uuid(),
    kind text not null check (kind in ('immediate', 'overflow')),
    attempted_at timestamptz not null
);
create index client_alert_attempts_time_idx on public.client_alert_attempts(attempted_at);
create index client_error_events_cursor_idx on public.client_error_events(received_at, event_id)
    where user_id is not null;

alter table public.client_alert_progress enable row level security;
alter table public.client_alert_attempts enable row level security;
revoke all on public.client_alert_progress, public.client_alert_attempts from public, anon, authenticated;
grant all on public.client_alert_progress, public.client_alert_attempts to service_role;
