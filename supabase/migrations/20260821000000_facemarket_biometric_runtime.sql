alter table public.fm_models drop constraint if exists fm_models_status_check;
alter table public.fm_models add constraint fm_models_status_check
  check (status in ('pending', 'verified', 'suspended', 'reverification_required'));

alter table public.fm_licenses drop constraint if exists fm_licenses_status_check;
alter table public.fm_licenses add constraint fm_licenses_status_check
  check (status in ('pending', 'active', 'revoked', 'expired', 'reverification_required'));

create table if not exists public.fm_biometric_enrollments (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id) on delete set null,
  model_id uuid references public.fm_models(id) on delete set null,
  device_digest text not null,
  consent_version text not null,
  consented_at timestamptz not null default now(),
  oacx_tx_digest text unique,
  liveness_session_digest text unique,
  liveness_nonce_digest text,
  status text not null default 'photos_pending'
    check (status in (
      'photos_pending', 'liveness_pending', 'processing', 'asset_building',
      'license_pending', 'vc_pending', 'passed', 'failed', 'cancelled', 'expired'
    )),
  decision text check (decision is null or decision in ('passed', 'failed')),
  reason text,
  provider_versions jsonb not null default '{}'::jsonb,
  match_policy_version text,
  raw_deletion_evidence jsonb not null default '{}'::jsonb,
  cooldown_until timestamptz,
  vc_id text,
  expires_at timestamptz not null default (now() + interval '24 hours'),
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.fm_biometric_enrollment_photos (
  enrollment_id uuid not null references public.fm_biometric_enrollments(id) on delete cascade,
  angle text not null check (angle in ('front', 'angle45', 'side')),
  r2_key text not null,
  image_digest text not null,
  mime_type text not null check (mime_type in ('image/png', 'image/jpeg', 'image/webp')),
  byte_size integer not null check (byte_size > 0 and byte_size <= 26214400),
  qc_status text not null default 'passed' check (qc_status = 'passed'),
  storage_state text not null default 'quarantine'
    check (storage_state in ('quarantine', 'approved', 'delete_pending')),
  uploaded_at timestamptz not null default now(),
  approved_at timestamptz,
  primary key (enrollment_id, angle)
);

create table if not exists public.fm_biometric_enrollment_photo_cleanup (
  enrollment_id uuid not null,
  angle text not null check (angle in ('front', 'angle45', 'side')),
  r2_key text not null,
  reason text not null check (reason in ('upload_orphan', 'superseded', 'delete')),
  created_at timestamptz not null default now(),
  not_before timestamptz not null default now(),
  primary key (enrollment_id, r2_key)
);

alter table public.fm_models
  add column if not exists current_enrollment_id uuid
    references public.fm_biometric_enrollments(id) on delete set null;
alter table public.fm_licenses
  add column if not exists enrollment_id uuid
    references public.fm_biometric_enrollments(id) on delete restrict;
alter table public.fm_model_assets
  add column if not exists source_enrollment_id uuid
    references public.fm_biometric_enrollments(id) on delete restrict,
  add column if not exists evidence_version text;

create unique index if not exists fm_biometric_active_per_user
  on public.fm_biometric_enrollments(user_id)
  where status in ('photos_pending', 'liveness_pending', 'processing', 'asset_building',
                   'license_pending', 'vc_pending');
create unique index if not exists fm_biometric_liveness_nonce_unique
  on public.fm_biometric_enrollments(liveness_nonce_digest)
  where liveness_nonce_digest is not null;
create unique index if not exists fm_biometric_liveness_session_unique
  on public.fm_biometric_enrollments(liveness_session_digest)
  where liveness_session_digest is not null;
create index if not exists fm_biometric_cleanup_due
  on public.fm_biometric_enrollments(expires_at)
  where status not in ('passed', 'cancelled', 'expired');
create index if not exists fm_biometric_failure_device_window
  on public.fm_biometric_enrollments(device_digest, completed_at desc);
create index if not exists fm_biometric_failure_user_window
  on public.fm_biometric_enrollments(user_id, completed_at desc);
create unique index if not exists fm_licenses_enrollment_unique
  on public.fm_licenses(enrollment_id) where enrollment_id is not null;

alter table public.fm_biometric_enrollments enable row level security;
alter table public.fm_biometric_enrollment_photos enable row level security;
alter table public.fm_biometric_enrollment_photo_cleanup enable row level security;

drop trigger if exists fm_biometric_enrollments_set_updated_at
  on public.fm_biometric_enrollments;
create trigger fm_biometric_enrollments_set_updated_at
  before update on public.fm_biometric_enrollments
  for each row execute function public.set_updated_at();
