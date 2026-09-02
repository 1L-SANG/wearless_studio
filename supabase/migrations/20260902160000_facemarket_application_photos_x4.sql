-- 지원서 UI 리뉴얼(레퍼런스 정합): 사진 4종(프로필·클로즈업·상반신·전신) + 전화·경력 수준 +
-- 확인 서명(에이전시 미소속·성인/진실·사진 본인) 기록. Additive · PG16-safe.

-- 1) 스테이징: 사용자당 1슬롯 → 종류별 슬롯(user_id, kind).
alter table public.fm_model_application_photo_staging
  add column if not exists kind text not null default 'profile';
alter table public.fm_model_application_photo_staging
  drop constraint if exists fm_model_application_photo_staging_pkey;
alter table public.fm_model_application_photo_staging
  add primary key (user_id, kind);
alter table public.fm_model_application_photo_staging
  drop constraint if exists fm_model_application_photo_staging_kind_check;
alter table public.fm_model_application_photo_staging
  add constraint fm_model_application_photo_staging_kind_check
  check (kind in ('profile', 'closeup', 'waist_up', 'full_length'));

-- 2) 지원서: 사진 키 맵(kind → r2_key), 전화·경력, 확인 서명.
--    profile_image_r2_key 는 유지(= photo_keys.profile, 관리자 썸네일·purge·카탈로그 커버 호환).
alter table public.fm_model_applications
  add column if not exists phone text,
  add column if not exists experience_level text,
  add column if not exists photo_keys jsonb not null default '{}'::jsonb,
  add column if not exists attestations jsonb not null default '{}'::jsonb;

alter table public.fm_model_applications
  drop constraint if exists fm_model_applications_experience_level_check;
alter table public.fm_model_applications
  add constraint fm_model_applications_experience_level_check
  check (experience_level is null or experience_level in (
    'none', 'beginner', 'intermediate', 'professional'
  ));
