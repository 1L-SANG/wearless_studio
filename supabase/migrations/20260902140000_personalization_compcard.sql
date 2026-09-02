-- 모델 컴카드 스펙(T11/6-1B) — 승인 후 프로필 단계에서 받는 선택 정보.
-- 3사이즈(가슴·허리·엉덩이)·헤어컬러·헤어길이·눈색. 전부 nullable·비게이팅(입력 안 해도
-- 등록 진행). 생성 프롬프트 경로에는 연결하지 않는다(순수 프로필 메타데이터).
-- Additive · PG16-safe.

alter table public.personalization_profiles
  add column if not exists bust_cm numeric(5,2),
  add column if not exists waist_cm numeric(5,2),
  add column if not exists hip_cm numeric(5,2),
  add column if not exists hair_color text,
  add column if not exists hair_length text,
  add column if not exists eye_color text;

alter table public.personalization_profiles
  drop constraint if exists personalization_profiles_hair_color_check;
alter table public.personalization_profiles
  add constraint personalization_profiles_hair_color_check
  check (hair_color is null or hair_color in (
    'black', 'dark_brown', 'brown', 'light_brown', 'blonde', 'red', 'gray', 'other'
  ));

alter table public.personalization_profiles
  drop constraint if exists personalization_profiles_hair_length_check;
alter table public.personalization_profiles
  add constraint personalization_profiles_hair_length_check
  check (hair_length is null or hair_length in ('short', 'medium', 'long'));

alter table public.personalization_profiles
  drop constraint if exists personalization_profiles_eye_color_check;
alter table public.personalization_profiles
  add constraint personalization_profiles_eye_color_check
  check (eye_color is null or eye_color in (
    'black', 'brown', 'hazel', 'green', 'blue', 'gray', 'other'
  ));
