-- 지원서 승인/거절 메일 발송 원장(T4/E4). 단일 email_sent_at 로는 승인 메일 뒤 이어지는
-- 자동거절 메일을 표현하지 못한다 — 타입별 행으로 기록하고, provider 성공 후 DB 기록 전
-- 장애 시 중복 재발송을 막는 멱등 근거로 쓴다. 상태는 진실원천이 아니다(지원서가 진실, 2A) —
-- 이 원장은 "어떤 메일이 나갔나"만 기록하고, 관리자 대시보드가 '미발송' 뱃지·재발송에 읽는다.
-- Additive · PG16-safe.

create table if not exists public.fm_model_application_emails (
  id uuid primary key default gen_random_uuid(),
  application_id uuid not null
    references public.fm_model_applications(id) on delete cascade,
  email_type text not null check (email_type in ('approved', 'rejected')),
  status text not null default 'pending'
    check (status in ('pending', 'sent', 'failed')),
  provider_message_id text,
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists fm_model_application_emails_by_app
  on public.fm_model_application_emails(application_id, created_at desc);

alter table public.fm_model_application_emails enable row level security;

drop trigger if exists fm_model_application_emails_set_updated_at
  on public.fm_model_application_emails;
create trigger fm_model_application_emails_set_updated_at
  before update on public.fm_model_application_emails
  for each row execute function public.set_updated_at();
