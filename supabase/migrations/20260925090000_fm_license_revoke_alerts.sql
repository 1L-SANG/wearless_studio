-- 모델의 라이선스 해지와 같은 트랜잭션에 파기 안내 작업을 남긴다.
-- Slack 전송 후 확인 전에 프로세스가 종료되면 한 번 더 보낼 수 있다.
create table if not exists public.fm_license_revoke_alerts (
  license_id uuid primary key,
  model_id uuid not null,
  display_name text not null,
  revoked_on date not null,
  other_active_licenses integer not null default 0 check (other_active_licenses >= 0),
  status text not null default 'pending' check (status in ('pending', 'processing', 'sent')),
  attempts integer not null default 0 check (attempts >= 0),
  next_attempt_at timestamptz not null default now(),
  lease_token uuid,
  lease_expires_at timestamptz,
  sent_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists fm_license_revoke_alerts_due_idx
  on public.fm_license_revoke_alerts (next_attempt_at, created_at)
  where status = 'pending';

create index if not exists fm_license_revoke_alerts_lease_idx
  on public.fm_license_revoke_alerts (lease_expires_at)
  where status = 'processing';

create trigger fm_license_revoke_alerts_set_updated_at
  before update on public.fm_license_revoke_alerts
  for each row execute function public.set_updated_at();

alter table public.fm_license_revoke_alerts enable row level security;
revoke all on public.fm_license_revoke_alerts from anon, authenticated;
