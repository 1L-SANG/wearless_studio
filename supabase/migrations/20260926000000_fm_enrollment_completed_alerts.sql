-- 라이선스 생성과 같은 트랜잭션에 등록 완료 Slack 작업을 저장한다.
-- Slack 성공 응답 뒤 sent 기록 전에 프로세스가 종료되면 재전송될 수 있다 (at least once).
create table if not exists public.fm_enrollment_completed_alerts (
  license_id uuid primary key,
  enrollment_id uuid not null unique,
  model_id uuid not null,
  display_name text not null,
  identity_method text not null check (identity_method in ('mid', 'simple_auth')),
  admin_link text not null,
  status text not null default 'pending' check (status in ('pending', 'processing', 'sent')),
  attempts integer not null default 0 check (attempts >= 0),
  next_attempt_at timestamptz not null default now(),
  lease_token uuid,
  lease_expires_at timestamptz,
  sent_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists fm_enrollment_completed_alerts_due_idx
  on public.fm_enrollment_completed_alerts (next_attempt_at, created_at)
  where status = 'pending';

create index if not exists fm_enrollment_completed_alerts_lease_idx
  on public.fm_enrollment_completed_alerts (lease_expires_at)
  where status = 'processing';

create trigger fm_enrollment_completed_alerts_set_updated_at
  before update on public.fm_enrollment_completed_alerts
  for each row execute function public.set_updated_at();

alter table public.fm_enrollment_completed_alerts enable row level security;
revoke all on public.fm_enrollment_completed_alerts from anon, authenticated;
