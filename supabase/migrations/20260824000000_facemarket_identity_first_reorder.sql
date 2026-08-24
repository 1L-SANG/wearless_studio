-- FaceMarket 생체등록 신분증-먼저 재배치: identity_pending 상태 + CI 증거/대표이미지 컬럼.
-- Additive·PG16-safe. 상태 enum 은 기존 fm_models_status_check 패턴(drop if exists→add)으로 확장.

-- 1) status CHECK 제약에 identity_pending 추가.
alter table public.fm_biometric_enrollments
  drop constraint if exists fm_biometric_enrollments_status_check;
alter table public.fm_biometric_enrollments
  add constraint fm_biometric_enrollments_status_check
  check (status in (
    'identity_pending', 'photos_pending', 'liveness_pending', 'processing',
    'asset_building', 'license_pending', 'vc_pending', 'passed', 'failed',
    'cancelled', 'expired'
  ));

-- 2) 신규 등록은 identity_pending 부터 시작.
alter table public.fm_biometric_enrollments
  alter column status set default 'identity_pending';

-- 3) "유저당 활성 등록 1개" partial unique index 에 identity_pending 포함(재생성).
drop index if exists public.fm_biometric_active_per_user;
create unique index if not exists fm_biometric_active_per_user
  on public.fm_biometric_enrollments(user_id)
  where status in ('identity_pending', 'photos_pending', 'liveness_pending',
                   'processing', 'asset_building', 'license_pending', 'vc_pending');

-- 4) CI 증거(원시 CI 아님 — HMAC ci_hash 만) + 대표이미지 R2 키 컬럼.
alter table public.fm_biometric_enrollments
  add column if not exists identity_ci_hash text,
  add column if not exists identity_name_masked text,
  add column if not exists identity_birth_year text,
  add column if not exists identity_tx_digest text,
  add column if not exists identity_contract_version text,
  add column if not exists profile_image_r2_key text;
