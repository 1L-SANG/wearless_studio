-- Generation Run — provider call 1건의 재현 스냅샷 (Phase 1: 마네킹 경로 한정, 2026-08-01).
--
-- 지금까지 "이 컷이 어떤 입력·프롬프트·모델·해상도로 나왔는가"는 job_events 에만 흩어져
-- 있었다. 이벤트는 보존 기간·조회 경로가 컷과 다르고(프론트는 SSE 를 안 쓴다), 무엇보다
-- **호출 단위**가 아니라 스텝 단위라 한 컷을 만든 생성+편집 3패스를 하나로 묶을 수 없다.
-- 여기서 provider call 을 1급 행으로 승격해 재현·비용·실패를 한 곳에서 센다.
--
-- 설계 계약:
--   · generation_runs = **provider call 단위**. 생성 1회, axis/bust/untuck 편집 각 1회가
--     모두 독립 행이다. 호출 직전 status='created' 로 먼저 쓰고(프로세스가 중간에 죽어도
--     기록이 남는다), 응답 후 usage/latency/error 로 갱신한다.
--   · generation_outputs = **최종 채택 산출물 단위**. 편집 중간본은 행을 만들지 않는다 —
--     중간본은 사용자에게 도달하지 않고, 그 이력은 이미 runs 에 있다. 채택본이 어느
--     호출에서 나왔는지는 run_id 가 가리킨다(편집이 회귀로 되돌려지면 그 이전 run 을 가리킨다).
--   · 프롬프트 **전문은 이 테이블에 넣지 않는다**. R2 object + sha256 만 남긴다 — 기존
--     이벤트 규율(원문 미포함, 해시만)과 같은 이유이고, 행 크기가 프롬프트 길이에 끌려가지
--     않게 한다. R2 업로드가 실패해도 sha256 은 남는다(prompt_r2_key null).
--   · settings_snapshot 은 **allowlist**만 담는다. 키·시크릿·presigned URL 은 금지다.
--
-- append-only(기존 migration 무수정). 전 컬럼 nullable 중심 — 기록이 실패해도 생성 경로는
-- 살아야 하므로 부분 기록이 정상 상태다. 플래그(GENERATION_RUN_LOG) off 면 행 자체가 없다.

create table if not exists public.generation_runs (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references public.jobs (id) on delete cascade,
  project_id uuid not null references public.projects (id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  kind text not null,
  candidate text,
  attempt integer,
  status text not null default 'created',
  model text,
  image_size text,
  aspect_ratio text,
  prompt_version text,
  prompt_sha256 text,
  prompt_r2_key text,
  input_assets jsonb,
  fit_profile_snapshot jsonb,
  settings_snapshot jsonb,
  usage jsonb,
  latency_ms integer,
  provider_error text,
  created_at timestamptz not null default now(),
  completed_at timestamptz
);

comment on table public.generation_runs is
  'provider(이미지 모델) 호출 1건의 재현 스냅샷. 호출 직전 created 로 insert, 응답 후 '
  'succeeded|failed 로 update. 프롬프트 전문은 R2(prompt_r2_key) — 여기엔 sha256 만.';
comment on column public.generation_runs.kind is
  'mannequin_generate | mannequin_adjust_edit | mannequin_axis_edit | mannequin_bust_edit | '
  'mannequin_untuck_edit. 문자열 자유형 — 새 호출 지점이 생겨도 migration 없이 늘어난다.';
comment on column public.generation_runs.status is 'created | succeeded | failed';
comment on column public.generation_runs.input_assets is
  '[{assetId, slot, sha256}] — 어떤 역할의 어떤 원본이 이 호출에 들어갔는가. 바이트·URL 금지.';
comment on column public.generation_runs.settings_snapshot is
  '판정·생성에 영향을 준 설정의 allowlist 스냅샷. 시크릿/URL/토큰 금지(테스트로 강제).';
comment on column public.generation_runs.provider_error is
  '실패 시 예외 타입+메시지 앞 200자. 성공이면 null.';

create index if not exists generation_runs_job_idx
  on public.generation_runs (job_id);
create index if not exists generation_runs_project_created_idx
  on public.generation_runs (project_id, created_at desc);

create table if not exists public.generation_outputs (
  id uuid primary key default gen_random_uuid(),
  generation_run_id uuid references public.generation_runs (id) on delete set null,
  project_id uuid not null references public.projects (id) on delete cascade,
  mannequin_cut_id uuid references public.mannequin_cuts (id) on delete cascade,
  asset_id uuid references public.assets (id) on delete set null,
  parent_output_id uuid references public.generation_outputs (id) on delete set null,
  created_at timestamptz not null default now()
);

comment on table public.generation_outputs is
  '채택되어 사용자에게 나간 산출물 ↔ 그것을 만든 run/cut/asset 연결(thin). 편집 중간본은 '
  '행을 만들지 않는다. parent_output_id 는 Phase 2(Approved Baseline) 파생 추적용 예약 —'
  'Phase 1 에서는 항상 null.';
comment on column public.generation_outputs.generation_run_id is
  '채택된 **이미지 바이트를 실제로 만든** 호출. 편집이 회귀 판정으로 되돌려졌다면 편집 run 이 '
  '아니라 그 이전 run 을 가리킨다(이미지 해시로 역참조).';

create index if not exists generation_outputs_run_idx
  on public.generation_outputs (generation_run_id);
create index if not exists generation_outputs_cut_idx
  on public.generation_outputs (mannequin_cut_id);

-- ── RLS: enable + owner-select 만. 쓰기는 service-role(RLS bypass) 백엔드 전용 ──
-- personalization_core(20260715) 선례와 동일 규율. 사용자는 자기 프로젝트의 기록만 읽는다.

alter table public.generation_runs enable row level security;
alter table public.generation_outputs enable row level security;

drop policy if exists generation_runs_owner_select on public.generation_runs;
create policy generation_runs_owner_select on public.generation_runs
  for select using (user_id = (select auth.uid()));

drop policy if exists generation_outputs_owner_select on public.generation_outputs;
create policy generation_outputs_owner_select on public.generation_outputs
  for select using (
    exists (
      select 1 from public.projects p
      where p.id = generation_outputs.project_id and p.user_id = (select auth.uid())
    )
  );
