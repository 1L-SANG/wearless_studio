-- FaceMarket 모델 physique(체형·키). 얼굴만 앵커되는 실모델의 몸을 컷 생성에 반영하기 위한
-- 모델 속성. gender 는 신원확인(identity)에서, height_bucket·body_type 은 physique 스텝에서 승격.
-- Additive · PG16-safe · 전부 nullable(선택 입력).

-- 1) fm_models: gender / height_bucket / body_type
alter table public.fm_models add column if not exists gender text;
alter table public.fm_models add column if not exists height_bucket text;
alter table public.fm_models add column if not exists body_type text;

alter table public.fm_models drop constraint if exists fm_models_gender_check;
alter table public.fm_models add constraint fm_models_gender_check
  check (gender is null or gender in ('male','female'));

alter table public.fm_models drop constraint if exists fm_models_height_bucket_check;
alter table public.fm_models add constraint fm_models_height_bucket_check
  check (height_bucket is null or height_bucket in (
    'm_lt170','m_170_175','m_175_180','m_180_185','m_185_190','m_gte190',
    'f_lt155','f_155_160','f_160_165','f_165_170','f_170_175','f_gte175'
  ));

alter table public.fm_models drop constraint if exists fm_models_body_type_check;
alter table public.fm_models add constraint fm_models_body_type_check
  check (body_type is null or body_type in (
    'delicate','slim','regular','plump','toned','bulk','glamorous'
  ));

-- 2) fm_biometric_enrollments: height_bucket / body_type 스테이징(gender 는 모델 소유)
alter table public.fm_biometric_enrollments add column if not exists height_bucket text;
alter table public.fm_biometric_enrollments add column if not exists body_type text;

alter table public.fm_biometric_enrollments drop constraint if exists fm_enrollments_height_bucket_check;
alter table public.fm_biometric_enrollments add constraint fm_enrollments_height_bucket_check
  check (height_bucket is null or height_bucket in (
    'm_lt170','m_170_175','m_175_180','m_180_185','m_185_190','m_gte190',
    'f_lt155','f_155_160','f_160_165','f_165_170','f_170_175','f_gte175'
  ));

alter table public.fm_biometric_enrollments drop constraint if exists fm_enrollments_body_type_check;
alter table public.fm_biometric_enrollments add constraint fm_enrollments_body_type_check
  check (body_type is null or body_type in (
    'delicate','slim','regular','plump','toned','bulk','glamorous'
  ));
