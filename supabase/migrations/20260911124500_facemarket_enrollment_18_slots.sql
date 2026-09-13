alter table public.fm_biometric_enrollment_photos
  drop constraint if exists fm_biometric_enrollment_photos_angle_check;

alter table public.fm_biometric_enrollment_photos
  add constraint fm_biometric_enrollment_photos_angle_check
  check (angle in (
    'face01','face02','face03','face04','face05','face06','face07','face08',
    'torso01','torso02','torso03','torso04','torso05',
    'full01','full02','full03','full04','full05',
    'front','angle45','side'
  ));

alter table public.fm_biometric_enrollment_photo_cleanup
  drop constraint if exists fm_biometric_enrollment_photo_cleanup_angle_check;

alter table public.fm_biometric_enrollment_photo_cleanup
  add constraint fm_biometric_enrollment_photo_cleanup_angle_check
  check (angle in (
    'face01','face02','face03','face04','face05','face06','face07','face08',
    'torso01','torso02','torso03','torso04','torso05',
    'full01','full02','full03','full04','full05',
    'front','angle45','side'
  ));

alter table public.fm_biometric_enrollments
  add column if not exists terms_consent_version text,
  add column if not exists overseas_consent_version text;

create table if not exists public.fm_enrollment_consent_events (
  id uuid primary key default gen_random_uuid(),
  enrollment_id uuid not null references public.fm_biometric_enrollments(id) on delete cascade,
  user_id uuid not null,
  biometric_version text not null,
  terms_version text not null,
  overseas_version text not null,
  accepted_at timestamptz not null default now()
);

alter table public.fm_enrollment_consent_events enable row level security;
