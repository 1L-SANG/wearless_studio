-- 법무 초안의 이력 구조에서 1차 MVP에 필요한 참여·프로필 동의만 사용해요.
-- 과거 동의를 덮어쓰지 않으며 고지 내용만 보관해요. 프로필 실제 값은 복제하지 않아요.
create table public.fm_sponsorship_consent_events (
  id bigint generated always as identity primary key,
  user_id uuid not null,
  model_id uuid not null,
  actor_user_id uuid not null,
  consent_type text not null check (consent_type in ('sponsorship_participation', 'sponsorship_profile_collection')),
  action text not null check (action in ('granted', 'withdrawn')),
  doc_version text not null check (length(doc_version) > 0),
  document_sha256 text not null check (document_sha256 ~ '^[0-9a-f]{64}$'),
  occurred_at timestamptz not null default now(),
  ip_address inet,
  screen_id text not null check (screen_id in ('model_register', 'model_mypage', 'sponsorship_settings')),
  reason text not null check (reason = 'model_toggle'),
  notice_snapshot jsonb not null check (jsonb_typeof(notice_snapshot) = 'object'),
  idempotency_key uuid not null,
  unique (user_id, idempotency_key, consent_type),
  check (actor_user_id = user_id)
);
create index fm_sponsorship_consent_events_latest_idx
  on public.fm_sponsorship_consent_events (user_id, consent_type, id desc);
alter table public.fm_sponsorship_consent_events enable row level security;
revoke all on public.fm_sponsorship_consent_events from public, anon, authenticated;
grant select, insert on public.fm_sponsorship_consent_events to service_role;
grant usage, select on sequence public.fm_sponsorship_consent_events_id_seq to service_role;
