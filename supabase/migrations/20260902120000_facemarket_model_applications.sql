-- FaceMarket 모델 지원서·관리자 검토 리뉴얼 (T1).
-- 생체 enrollment 와 별개 aggregate 인 fm_model_applications 신설:
--   지원 → 관리자 검토(승인/거절) → 승인 지원서가 create_enrollment 게이트 통과 근거.
-- 설계: docs/designs/facemarket-application-renewal.md (CEO+ENG 리뷰 확정)
-- Additive · PG16-safe. 기존 테이블 변경은 enrollment 에 nullable application_id FK 한 컬럼뿐(E5).
--
--   상태 머신:
--     under_review ──승인──▶ approved ──(enrollment 생성 근거)
--          │                    │
--       거절│                취소│(사용자)      3회 대조 실패(E2) ──▶ rejected
--          ▼                    ▼
--     rejected(터미널)      cancelled(터미널)
--   활성 = {under_review, approved} (유저당 1개, E9). 터미널이면 재지원 허용.

create table if not exists public.fm_model_applications (
  id uuid primary key default gen_random_uuid(),
  -- 계정 삭제 시 지원서도 제거(승인 경로 PII 는 계정·라이선스에 종속, 3A/E12).
  user_id uuid not null references auth.users(id) on delete cascade,
  status text not null default 'under_review'
    check (status in ('under_review', 'approved', 'rejected', 'cancelled')),

  -- 지원자 입력 필드 -----------------------------------------------------------
  contact_email text not null,               -- 승인/거절 메일 수신처(auth 엔 이메일 없음, T2-A)
  applicant_name text not null,              -- 신분증 대조 대상(4A, in-flight 전체 비교)
  birthdate date not null,                   -- 신분증 대조 대상(YYYY-MM-DD)
  region text not null,
  gender text check (gender is null or gender in ('male', 'female')),
  height_cm integer check (height_cm is null or (height_cm between 100 and 250)),
  agency_contracted boolean not null default false,
  categories jsonb not null default '[]'::jsonb,  -- ['fashion','commercial','fitness','lifestyle'] (앱 검증)
  portfolio_url text,
  sns_url text,
  bio text,
  profile_image_r2_key text,                 -- application 귀속 키(스테이징에서 승격, E11)

  -- 개인정보 수집·이용 동의(생체 동의와 별개, E3) -------------------------------
  privacy_consent_version text not null,
  privacy_consented_at timestamptz not null default now(),

  -- 신분증 대조 실패 누적(enrollment 재생성과 무관, 지원서에 귀속, E2) -----------
  identity_mismatch_count integer not null default 0,

  -- 관리자 검토·감사(3A) -------------------------------------------------------
  reviewed_by uuid references auth.users(id) on delete set null,
  reviewed_at timestamptz,
  reject_reason text,

  -- 라이프사이클 --------------------------------------------------------------
  terminated_at timestamptz,                 -- rejected/cancelled 시각(30일 PII sweep 기준)
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- 유저당 활성 지원서 1개(승인 보유 중 새 지원 불가 → 모호성 제거, E9).
-- 터미널(rejected/cancelled)은 제외되어 재지원 허용.
create unique index if not exists fm_model_applications_active_per_user
  on public.fm_model_applications(user_id)
  where status in ('under_review', 'approved');

-- 관리자 대시보드: 상태별 필터 + 최신순.
create index if not exists fm_model_applications_status_created
  on public.fm_model_applications(status, created_at desc);

-- 30일 PII 익명화 sweep(데모 후 구현, TODOS P1)이 스캔할 터미널 지원서.
create index if not exists fm_model_applications_terminated_due
  on public.fm_model_applications(terminated_at)
  where terminated_at is not null;

-- 제출 전 임시 프로필 사진(사용자당 1슬롯, 재업로드 시 교체). 미제출 시 orphan cleanup 이 회수(E11).
create table if not exists public.fm_model_application_photo_staging (
  user_id uuid primary key references auth.users(id) on delete cascade,
  r2_key text not null,
  mime_type text not null check (mime_type in ('image/png', 'image/jpeg', 'image/webp')),
  byte_size integer not null check (byte_size > 0 and byte_size <= 26214400),
  created_at timestamptz not null default now()
);

create index if not exists fm_model_application_photo_staging_due
  on public.fm_model_application_photo_staging(created_at);

-- E5: enrollment ↔ 승인 지원서 불변 연결. "최신 승인 지원서 조회" 대신 이 FK 로 대조·strike 대상 고정.
alter table public.fm_biometric_enrollments
  add column if not exists application_id uuid
    references public.fm_model_applications(id) on delete set null;

alter table public.fm_model_applications enable row level security;
alter table public.fm_model_application_photo_staging enable row level security;

drop trigger if exists fm_model_applications_set_updated_at
  on public.fm_model_applications;
create trigger fm_model_applications_set_updated_at
  before update on public.fm_model_applications
  for each row execute function public.set_updated_at();
