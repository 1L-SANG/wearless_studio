-- FaceMarket 간편인증 경로: 신분증 촬영 수용 + 관리자 육안 심사.
-- Additive·PG16-safe. 기존 mid 경로 동작은 건드리지 않는다(status 기본값 유지).

-- 1) status CHECK 에 id_capture_pending(경로 S 시작) + review_pending(심사 대기) 추가.
alter table public.fm_biometric_enrollments
  drop constraint if exists fm_biometric_enrollments_status_check;
alter table public.fm_biometric_enrollments
  add constraint fm_biometric_enrollments_status_check
  check (status in (
    'id_capture_pending', 'identity_pending', 'photos_pending', 'review_pending',
    'liveness_pending', 'processing', 'asset_building', 'license_pending',
    'vc_pending', 'passed', 'failed', 'cancelled', 'expired'
  ));

-- 2) "유저당 활성 등록 1개" 인덱스에 두 신규 상태를 넣는다. 빠뜨리면 심사 대기 중인
--    사용자가 등록을 하나 더 만들어 같은 CI 로 두 모델이 생긴다.
drop index if exists public.fm_biometric_active_per_user;
create unique index if not exists fm_biometric_active_per_user
  on public.fm_biometric_enrollments(user_id)
  where status in ('id_capture_pending', 'identity_pending', 'photos_pending',
                   'review_pending', 'liveness_pending', 'processing',
                   'asset_building', 'license_pending', 'vc_pending');

-- 3) 인증 수단 + 신분증 촬영본 + 심사 결과. 전부 nullable/기본값이라 기존 행은 무영향.
--    identity_contract_version(기존)이 계약 버전을 이미 담으므로 별도 컬럼을 만들지 않는다.
alter table public.fm_biometric_enrollments
  add column if not exists identity_method text default 'mid',
  add column if not exists id_document_r2_key text,
  add column if not exists id_document_type text,
  add column if not exists id_document_uploaded_at timestamptz,
  add column if not exists id_document_purged_at timestamptz,
  add column if not exists review_status text,
  add column if not exists reviewed_by uuid,
  add column if not exists reviewed_at timestamptz,
  add column if not exists review_reason text,
  add column if not exists match_scores jsonb;

-- 4) 심사 큐 조회용 부분 인덱스.
create index if not exists fm_biometric_review_queue
  on public.fm_biometric_enrollments(created_at desc)
  where review_status = 'pending';
