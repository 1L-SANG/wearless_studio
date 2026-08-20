-- 실 TX 시뮬레이션은 API task가 여러 대여도 동일한 분당 한도를 공유한다.
-- 원본 admin id와 IP는 저장하지 않고 앱에서 scope별 HMAC-SHA256 digest만 보관한다.
create table if not exists public.fm_settlement_simulation_limits (
  scope         text        not null check (scope in ('admin', 'ip')),
  key_hash      text        not null check (key_hash ~ '^[0-9a-f]{64}$'),
  window_start  timestamptz not null,
  request_count integer     not null check (request_count > 0),
  primary key (scope, key_hash, window_start)
);

create index if not exists fm_settlement_simulation_limits_window_idx
  on public.fm_settlement_simulation_limits (window_start);

alter table public.fm_settlement_simulation_limits enable row level security;

comment on table public.fm_settlement_simulation_limits is
  'Distributed per-minute guard for admin-triggered real settlement transactions';
