alter table public.fm_licenses
  alter column status set default 'pending';

create table if not exists public.fm_vc_revocation_jobs (
  id                uuid primary key default gen_random_uuid(),
  license_id        uuid not null references public.fm_licenses(id) on delete restrict,
  model_id          uuid not null references public.fm_models(id) on delete restrict,
  vc_id             text not null unique,
  status            text not null default 'pending'
                      check (status in ('pending', 'processing', 'retry', 'revoked')),
  attempts          integer not null default 0 check (attempts >= 0),
  next_attempt_at   timestamptz not null default now(),
  lease_token       uuid,
  lease_expires_at  timestamptz,
  last_error_code   text,
  revoked_at        timestamptz,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

create index if not exists fm_vc_revocation_jobs_claim_idx
  on public.fm_vc_revocation_jobs (next_attempt_at, created_at)
  where status in ('pending', 'retry');

alter table public.fm_vc_revocation_jobs enable row level security;

drop trigger if exists fm_vc_revocation_jobs_set_updated_at
  on public.fm_vc_revocation_jobs;
create trigger fm_vc_revocation_jobs_set_updated_at
  before update on public.fm_vc_revocation_jobs
  for each row execute function public.set_updated_at();
