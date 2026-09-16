-- 테스트컷 자동 생성 원장. **jobs 테이블을 쓰지 않는다** — jobs.project_id 가 not null 인데
-- 이 일은 프로젝트가 아니라 사람(모델) 단위다(fm_lora_training_runs 와 같은 이유).
--
-- 한 건 = 한 모델의 12장(보정 3종 × (클로즈업 2 + 전신 2)). 컷 생성(gpt-image)은 하지 않는다 —
-- 고정 기준 원본 4장에 **얼굴만 3번 다시 그린다**. 그래서 생성 비용 0, 얼굴 렌더 12회다.
create table if not exists public.fm_test_cut_builds (
  id          uuid primary key default gen_random_uuid(),
  model_id    uuid not null references public.fm_models(id) on delete cascade,
  lora_id     uuid,
  status      text not null default 'queued'
              check (status in ('queued', 'running', 'partial', 'done', 'failed')),
  requested   int not null default 0,
  produced    int not null default 0,
  error       text,
  attempts    int not null default 0,
  started_at  timestamptz,
  finished_at timestamptz,
  created_at  timestamptz not null default now()
);

-- ★ 동시 1건. 얼굴 렌더 파드는 **모델 하나당 하나**라(fm_face_render_pod), 두 모델을 같이
--   돌리면 두 번째는 409 만 받는다. 학습 큐와 같은 규칙이고 같은 방식(부분 유니크)이다.
create unique index if not exists fm_test_cut_builds_one_active
  on public.fm_test_cut_builds ((true))
  where status = 'running';

-- 한 모델에 큐가 쌓이지 않게. 다시 생성은 앞 건이 끝난 뒤에만 큐에 들어간다.
create unique index if not exists fm_test_cut_builds_one_queued_per_model
  on public.fm_test_cut_builds (model_id)
  where status in ('queued', 'running');

create index if not exists fm_test_cut_builds_queued
  on public.fm_test_cut_builds (created_at)
  where status = 'queued';

create index if not exists fm_test_cut_builds_model
  on public.fm_test_cut_builds (model_id, created_at desc);

comment on table public.fm_test_cut_builds is
  '테스트컷 12장 자동 생성 런. 동시 1건(partial unique). 이미지는 R2 에만 있고 여기엔 개수와 사유만 남는다.';

alter table public.fm_test_cut_builds enable row level security;
