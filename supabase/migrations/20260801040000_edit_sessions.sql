-- Edit Session — Approved Baseline 기반 **제한된 편집** (Phase 3 P0-C, 2026-08-01).
--
-- Phase 2 까지는 "무엇이 정본인가"(baseline)와 "무엇에서 파생됐는가"(parent_output_id)를
-- 남길 수 있게 됐다. 이 migration 은 그 위에 **한 번의 편집 시도**를 1급 행으로 만든다:
-- 무엇을 바꿔달라고 했는지, 무엇은 바꾸지 말라고 잠갔는지, 결과가 그 요청을 지켰는지.
--
-- 왜 generation_runs 로 부족한가: generation_runs 는 provider 호출 단위라 "요청한 변경"과
-- "잠근 항목"이라는 **의도**를 담지 않는다. 편집이 실패하는 방식은 호출이 실패하는 방식과
-- 다르다 — 호출은 200 을 주는데 소매가 같이 짧아지는 것이 편집의 실패다.
--
-- job kind 는 늘리지 않는다: jobs.kind CHECK 는 init.sql 의 5종으로 고정돼 있고, 새 값을
-- 넣으려면 그 제약을 갈아야 한다. 편집은 기존 'mannequin' 잡의 payload.mode='edit' 로
-- 구분한다 — dispatcher·lease·크레딧 경로를 그대로 쓰면서 의미만 나눈다.

create table if not exists public.edit_sessions (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects (id) on delete cascade,
  job_id uuid references public.jobs (id) on delete set null,
  baseline_id uuid not null references public.approved_baselines (id) on delete restrict,
  parent_output_id uuid references public.generation_outputs (id) on delete set null,
  edit_type text not null,
  requested_adjustments jsonb not null,
  locked_invariants jsonb not null,
  allowed_scope jsonb not null,
  status text not null default 'queued',
  prompt_sha256 text,
  prompt_r2_key text,
  model_snapshot jsonb,
  output_id uuid references public.generation_outputs (id) on delete set null,
  edit_qc_result jsonb,
  retry_count integer not null default 0,
  created_by uuid references auth.users (id) on delete set null,
  created_at timestamptz not null default now(),
  completed_at timestamptz,

  -- 상태는 자유 문자열이 아니다. 오타 하나가 "완료된 적 없는 세션"을 영구히 만든다.
  constraint edit_sessions_status_check check (status in (
    'queued', 'running', 'pass', 'review_required', 'reject', 'failed')),
  -- edit type 도 고정한다. 지원하지 않는 타입을 지원하는 척 통과시키지 않는다 —
  -- API 가 거부하지만 DB 도 같은 목록을 안다(두 곳이 갈라지면 API 만 고치고 끝난다).
  constraint edit_sessions_type_check check (edit_type in (
    'GARMENT_LENGTH_ONLY', 'BODY_WIDTH_ONLY', 'SLEEVE_LENGTH_ONLY',
    'SHOULDER_WIDTH_ONLY', 'TUCK_STATE_ONLY', 'MANNEQUIN_VOLUME_ONLY',
    'BACKGROUND_ONLY', 'LIGHTING_ONLY', 'CUSTOM_REVIEW_REQUIRED')),
  -- 종결 상태는 완료 시각을 가져야 한다. "성공했는데 언제인지 모른다"를 막는다.
  constraint edit_sessions_completed_check check (
    (status in ('queued', 'running') and completed_at is null)
    or (status in ('pass', 'review_required', 'reject', 'failed')
        and completed_at is not null)),
  -- 재시도 상한은 정책이 아니라 제약이다. 무제한 재시도는 비용 사고가 된다.
  constraint edit_sessions_retry_check check (retry_count >= 0 and retry_count <= 1)
);

comment on table public.edit_sessions is
  'Approved Baseline 기반 제한 편집 1회. 상태 전이: queued → running → '
  'pass|review_required|reject|failed. 어떤 결과도 baseline 을 자동 교체하지 않는다 — '
  '새 baseline 은 사용자가 승인 API 를 다시 호출해야만 생긴다.';

comment on column public.edit_sessions.baseline_id is
  '편집 입력이 된 baseline. on delete restrict — 파생이 있는 baseline 은 지울 수 없다. '
  'superseded 된 baseline 은 새 편집의 입력이 될 수 없다(서버가 active 만 고른다).';

comment on column public.edit_sessions.allowed_scope is
  '이 edit type 이 바꿔도 되는 항목. **서버가 결정한다** — 클라이언트가 보낸 값으로 '
  '잠금을 완화할 수 없다.';

comment on column public.edit_sessions.locked_invariants is
  'baseline 의 locked_invariants 스냅샷 + 이 edit type 의 금지 항목. 값을 모르는 항목은 '
  'unavailable 로 남는다(Phase 2 와 같은 규율 — 거짓 값 금지).';

comment on column public.edit_sessions.edit_qc_result is
  'Edit Intent QC 산출 JSON(decision·requestedChangeSatisfied·측정값·위반 목록·checks). '
  'decision 은 LLM 이 아니라 서버 정책이 정한다.';

comment on column public.edit_sessions.prompt_sha256 is
  '프롬프트 전문은 R2(prompt_r2_key) — DB 에는 해시만(Phase 1 과 같은 규율).';

create index if not exists edit_sessions_project_idx
  on public.edit_sessions (project_id, created_at desc);

create index if not exists edit_sessions_baseline_idx
  on public.edit_sessions (baseline_id);

create index if not exists edit_sessions_job_idx
  on public.edit_sessions (job_id);

-- 편집 결과 output 은 자기 세션을 가리킨다 — 계보의 반대 방향 조회용.
alter table public.generation_outputs
  add column if not exists edit_session_id uuid
    references public.edit_sessions (id) on delete set null;

comment on column public.generation_outputs.edit_session_id is
  '이 결과를 만든 편집 세션. parent_output_id 가 "무엇을 편집했는가"라면 이건 "어떤 의도로".';

-- ── RLS: 소유권은 projects 가 정본 (Phase 2 와 동일) ──
alter table public.edit_sessions enable row level security;

drop policy if exists edit_sessions_owner_select on public.edit_sessions;

create policy edit_sessions_owner_select on public.edit_sessions
  for select using (
    exists (
      select 1 from public.projects p
      where p.id = edit_sessions.project_id and p.user_id = (select auth.uid())
    )
  );
