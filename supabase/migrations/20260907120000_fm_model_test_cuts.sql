-- FaceMarket 모델 테스트컷 확인 게이트.
-- VC 발급과 셀러 공개를 분리하고, 모델 본인의 최종 품질 동의 뒤에만 verified 로 전이한다.

alter table public.fm_models
  drop constraint if exists fm_models_status_check;
alter table public.fm_models
  add constraint fm_models_status_check
  check (status in (
    'pending', 'awaiting_confirm', 'verified', 'suspended', 'reverification_required'
  ));

alter table public.fm_models
  add column if not exists confirm_requested_at timestamptz,
  add column if not exists confirmed_at timestamptz,
  add column if not exists confirm_consent_version text,
  add column if not exists redo_reason text,
  add column if not exists redo_count integer not null default 0
    check (redo_count >= 0);

create table public.fm_model_test_cuts (
  id uuid primary key default gen_random_uuid(),
  model_id uuid not null references public.fm_models(id) on delete cascade,
  r2_key text not null,
  mime text not null check (mime in ('image/png', 'image/jpeg', 'image/webp')),
  sort integer not null check (sort >= 0),
  approved boolean,
  created_at timestamptz not null default now()
);

create unique index fm_model_test_cuts_model_sort_unique
  on public.fm_model_test_cuts(model_id, sort);
create index fm_model_test_cuts_model_created
  on public.fm_model_test_cuts(model_id, created_at);

-- 얼굴 파생 원본과 같은 경계: direct 정책 없음 = service role만 접근.
-- 모델·관리자 열람은 인증된 FastAPI 스트림을 통해서만 제공한다.
alter table public.fm_model_test_cuts enable row level security;
