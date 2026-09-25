-- 협찬 동의 VC(fmsponsorship-v1). 협찬을 켜면 pending 행이 생기고 워커가 발급해 active 로,
-- 끄면 revoked 로 닫고 발급된 VC 는 폐기 큐(fm_vc_revocation_jobs, kind='sponsorship')로 보낸다.
-- 켜고 끈 횟수만큼 행이 남는다 — 협찬 이력이 VC 발급·폐기 기록으로 증명된다.
-- 설계: docs/superpowers/specs/2026-09-25-facemarket-sponsorship-vc-design.md
create table public.fm_sponsorship_credentials (
  id uuid primary key default gen_random_uuid(),
  model_id uuid not null references public.fm_models (id) on delete cascade,
  consent_event_key uuid not null,
  status text not null default 'pending'
    check (status in ('pending', 'active', 'revoked')),
  vc_id text unique,
  consent_doc_version text not null check (length(consent_doc_version) > 0),
  participation_doc_sha256 text not null check (participation_doc_sha256 ~ '^[0-9a-f]{64}$'),
  profile_doc_sha256 text not null check (profile_doc_sha256 ~ '^[0-9a-f]{64}$'),
  consented_at timestamptz not null,
  issued_at timestamptz,
  revoked_at timestamptz,
  attempts integer not null default 0 check (attempts >= 0),
  next_attempt_at timestamptz not null default now(),
  last_error_code text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (status <> 'active' or (vc_id is not null and issued_at is not null)),
  check (status <> 'revoked' or revoked_at is not null)
);

-- 모델당 열린(pending·active) 증서는 하나.
create unique index fm_sponsorship_credentials_one_open_uidx
  on public.fm_sponsorship_credentials (model_id)
  where status in ('pending', 'active');

create index fm_sponsorship_credentials_claim_idx
  on public.fm_sponsorship_credentials (next_attempt_at)
  where status = 'pending';

alter table public.fm_sponsorship_credentials enable row level security;
revoke all on public.fm_sponsorship_credentials from public, anon, authenticated;
grant select, insert, update on public.fm_sponsorship_credentials to service_role;

create trigger fm_sponsorship_credentials_set_updated_at
  before update on public.fm_sponsorship_credentials
  for each row execute function public.set_updated_at();

-- 폐기 큐를 협찬 VC 와 공유한다. 폐기 워커는 원래 vc_id 기준이라 로직은 그대로다.
alter table public.fm_vc_revocation_jobs
  add column if not exists kind text not null default 'license';
alter table public.fm_vc_revocation_jobs
  drop constraint if exists fm_vc_revocation_job_kind_check;
alter table public.fm_vc_revocation_jobs
  add constraint fm_vc_revocation_job_kind_check
  check (kind in ('license', 'sponsorship') and (kind <> 'license' or license_id is not null));
alter table public.fm_vc_revocation_jobs
  alter column license_id drop not null;
