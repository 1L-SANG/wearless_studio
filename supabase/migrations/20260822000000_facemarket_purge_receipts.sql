create table if not exists public.fm_biometric_purge_receipts (
  id uuid primary key default gen_random_uuid(),
  source_job_id uuid unique references public.jobs(id) on delete set null,
  reason text not null default 'account_delete'
    constraint fm_biometric_purge_receipts_reason_check
    check (reason = 'account_delete'),
  outcome text not null default 'ready_for_identity_delete'
    constraint fm_biometric_purge_receipts_outcome_check
    check (outcome = 'ready_for_identity_delete'),
  target_count integer not null check (target_count >= 0),
  confirmed_absent_count integer not null check (confirmed_absent_count >= 0),
  model_count integer not null check (model_count >= 0),
  profile_count integer not null check (profile_count >= 0),
  enrollment_count integer not null check (enrollment_count >= 0),
  asset_count integer not null check (asset_count >= 0),
  completed_at timestamptz not null default now(),
  constraint fm_biometric_purge_receipts_confirmed_absence
    check (target_count = confirmed_absent_count)
);

alter table public.fm_biometric_purge_receipts enable row level security;
revoke all on public.fm_biometric_purge_receipts from anon, authenticated;
