-- 인물 LoRA 학습 런 원장. **jobs 테이블을 쓰지 않는다** — jobs.project_id 가 not null 인데
-- 학습은 프로젝트가 아니라 사람(모델) 단위다. 억지로 프로젝트를 만들어 끼우면 크레딧·정산이
-- 그 프로젝트에 묶인다.
create table if not exists public.fm_lora_training_runs (
  id            uuid primary key default gen_random_uuid(),
  model_id      uuid not null references public.fm_models(id) on delete cascade,
  enrollment_id uuid,
  status        text not null default 'queued'
                check (status in ('queued', 'preparing', 'training', 'scoring', 'done', 'failed')),
  pod_id        text,
  gpu_type      text,
  dataset_key   text,
  ckpt_key      text,
  metrics       jsonb,
  error         text,
  attempts      int not null default 0,
  started_at    timestamptz,
  finished_at   timestamptz,
  created_at    timestamptz not null default now()
);

-- ★ 동시 1건. 전체에서 **한 건만** 살아 있을 수 있다(모델당이 아니라 전역이다).
--   GPU 파드는 시간당 $1.59~3.49 이고, 두 건이 겹치면 그만큼이 그대로 두 배다.
--   그리고 학습은 급하지 않다 — 줄 세우는 편이 싸고 안전하다.
create unique index if not exists fm_lora_training_runs_one_active
  on public.fm_lora_training_runs ((true))
  where status in ('preparing', 'training', 'scoring');

-- 큐에서 가장 오래된 것부터 집는다.
create index if not exists fm_lora_training_runs_queued
  on public.fm_lora_training_runs (created_at)
  where status = 'queued';

create index if not exists fm_lora_training_runs_model
  on public.fm_lora_training_runs (model_id, created_at desc);

comment on table public.fm_lora_training_runs is
  '인물 LoRA 학습 런. 동시 1건(partial unique). 가중치·데이터셋은 R2 키로만 가리킨다 — 바이트도 URL 도 여기 없다.';

alter table public.fm_lora_training_runs enable row level security;
