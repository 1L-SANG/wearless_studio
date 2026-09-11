-- FaceMarket 실존 등록자별 LoRA 장부.
-- 얼굴 패스는 등록자마다 다른 LoRA·트리거 토큰을 써야 하는데, fm_model_assets 는 PK 가
-- (model_id, view) 라 버전을 못 담는다. 사람 하나가 재학습으로 여러 버전을 갖고, 버전마다
-- 학습된 머리·얼굴형이 다르다.
--
-- ★ hair_* / face_shape / jaw_line 은 등록자의 "현재" 모습이 아니라 **이 LoRA 가 학습한 모습**이다.
--   등록자가 나중에 머리를 기르거나 염색해도 그 LoRA 가 내는 얼굴은 안 바뀐다. 그래서 이 값은
--   fm_models(사람)이 아니라 이 테이블(버전)에 붙는다. enum 은 app/facemarket_physique.py 와 같다.
-- ★ bucket 은 'face' 고정. 모델 자산의 쓰기·삭제가 전부 r2_face 하나로만 돌고
--   biometric_purge 도 그 버킷에서 지운다(services/biometric_purge.py:540 근처).
-- Additive · PG16-safe · 기존 경로 무영향(행이 없으면 얼굴 패스는 지금처럼 안 걸린다).

create table if not exists public.fm_model_loras (
  id                   uuid primary key default gen_random_uuid(),
  model_id             uuid not null references public.fm_models(id) on delete cascade,
  version              int  not null,
  status               text not null default 'ready',
  enabled              boolean not null default false,
  base_model           text,
  lora_r2_key          text not null,
  bucket               text not null default 'face',
  trigger_token        text not null,
  hair_length          text,
  hair_color           text,
  hair_texture         text,
  face_shape           text,
  jaw_line             text,
  trained_steps        int,
  source_enrollment_id uuid,
  metrics              jsonb,
  created_at           timestamptz not null default now()
);

alter table public.fm_model_loras drop constraint if exists fm_model_loras_bucket_check;
alter table public.fm_model_loras add constraint fm_model_loras_bucket_check
  check (bucket = 'face');

alter table public.fm_model_loras drop constraint if exists fm_model_loras_status_check;
alter table public.fm_model_loras add constraint fm_model_loras_status_check
  check (status in ('training', 'ready', 'failed', 'retired'));

-- enum 은 facemarket_physique.py 와 같은 값이어야 한다(둘이 갈리면 프롬프트에서 조용히 생략된다).
alter table public.fm_model_loras drop constraint if exists fm_model_loras_hair_length_check;
alter table public.fm_model_loras add constraint fm_model_loras_hair_length_check
  check (hair_length is null or hair_length in ('buzz','short','medium','long'));

alter table public.fm_model_loras drop constraint if exists fm_model_loras_hair_color_check;
alter table public.fm_model_loras add constraint fm_model_loras_hair_color_check
  check (hair_color is null or hair_color in ('black','dark_brown','brown','blonde','gray','other'));

alter table public.fm_model_loras drop constraint if exists fm_model_loras_hair_texture_check;
alter table public.fm_model_loras add constraint fm_model_loras_hair_texture_check
  check (hair_texture is null or hair_texture in ('straight','wavy','curly'));

alter table public.fm_model_loras drop constraint if exists fm_model_loras_face_shape_check;
alter table public.fm_model_loras add constraint fm_model_loras_face_shape_check
  check (face_shape is null or face_shape in ('oval','round','square','heart','long'));

alter table public.fm_model_loras drop constraint if exists fm_model_loras_jaw_line_check;
alter table public.fm_model_loras add constraint fm_model_loras_jaw_line_check
  check (jaw_line is null or jaw_line in ('soft','defined','angular'));

-- 같은 모델에 같은 버전 두 번 금지.
create unique index if not exists fm_model_loras_model_version_uidx
  on public.fm_model_loras (model_id, version);

-- 모델당 켜진 LoRA 는 최대 하나. partial unique — 꺼진 버전은 몇 개든 남길 수 있다.
create unique index if not exists fm_model_loras_one_enabled_uidx
  on public.fm_model_loras (model_id) where enabled;

create index if not exists fm_model_loras_model_idx on public.fm_model_loras (model_id);

-- 생체 파생 자산이라 fm_model_assets 와 같은 규칙: RLS 활성 + 정책 없음 = service_role 만.
alter table public.fm_model_loras enable row level security;
