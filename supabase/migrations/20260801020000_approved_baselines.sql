-- Approved Baseline — 사용자가 **명시적으로 승인한** 마네킹 결과 (Phase 2, 2026-08-01).
--
-- 왜 필요한가: 지금 "이 프로젝트의 정본 컷"은 projects.selected_mannequin_id 하나뿐인데,
-- 그건 PATCHABLE_COLUMNS 를 통해 일반 PATCH 로 자유롭게 바뀌는 **UI 선택 포인터**다
-- (존재 검증도 감사 기록도 없다). 조정 편집의 기준이자 파생 계보의 뿌리가 되려면
-- "누가 언제 무엇을 승인했는가"가 불변으로 남아야 한다.
--
-- 설계 결정(기획서 5.8 대비 현 코드 기준):
--   · product_id: products.project_id 가 **unique** 라 product ↔ project 는 1:1 이다.
--     따라서 active baseline 의 scope 는 project 로 잡고, product_id 는 편의 컬럼으로만
--     둔다(nullable, 승인 시점 조회값).
--   · truth_package_id / baseline_qc_result_id: Phase 4(Product Truth)·QC Result 정규화가
--     아직 없다. **없는 엔티티를 억지로 만들지 않는다** — 컬럼만 nullable uuid 로 비워두고,
--     그 테이블이 생기는 시점에 FK 를 붙이는 후속 migration 을 낸다. 지금 FK 를 걸면
--     참조 대상이 없어 migration 자체가 실패한다.
--   · qc_scores_snapshot: QC Result 테이블이 없는 동안 **승인 시점 판정**을 잃지 않으려고
--     컷의 qc_scores 를 그대로 복사해 둔다. 임계값이 바뀌어도 과거 승인의 근거는 그대로다.
--   · framing/background/lighting profile: 현재 코드에 좌표·구조화 프로필이 **존재하지
--     않는다**(포즈·카메라·배경은 프롬프트 문장으로 고정된다). 거짓 값을 채우지 않고
--     locked_invariants 에 unavailable + 사유를 남긴다. 실제로 그 값을 고정하는 것은
--     프롬프트 버전이라 그것을 함께 스냅샷한다.
--
-- append-only. 기존 테이블/컬럼 무수정 — selected_mannequin_id 는 그대로 살아 있다.

create table if not exists public.approved_baselines (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects (id) on delete cascade,
  product_id uuid references public.products (id) on delete set null,
  baseline_cut_id uuid not null references public.mannequin_cuts (id) on delete restrict,
  output_id uuid references public.generation_outputs (id) on delete set null,
  generation_run_id uuid references public.generation_runs (id) on delete set null,
  truth_package_id uuid,
  baseline_qc_result_id uuid,
  mannequin_profile_snapshot jsonb,
  framing_profile_snapshot jsonb,
  background_profile_snapshot jsonb,
  lighting_profile_snapshot jsonb,
  locked_invariants jsonb,
  qc_scores_snapshot jsonb,
  approved_by uuid not null references auth.users (id) on delete cascade,
  approved_at timestamptz not null default now(),
  superseded_at timestamptz
);

comment on table public.approved_baselines is
  '사용자가 명시적으로 승인한 마네킹 결과. "가장 최근 생성본"이 아니다 — 생성 성공만으로는 '
  '절대 만들어지지 않고, 승인 API 만이 행을 만든다. 조정 편집의 기준이자 파생 계보의 뿌리.';

comment on column public.approved_baselines.baseline_cut_id is
  'mannequin_cuts.id (uuid). 클라이언트가 쓰는 "A-3" 형식 id 가 아니라 DB 정본 키다. '
  'on delete restrict — 승인된 컷은 삭제로 계보를 끊을 수 없다(supersede 로만 물러난다).';

comment on column public.approved_baselines.output_id is
  '승인된 컷의 generation_outputs 행. Phase 1 기록이 꺼져 있던 시기의 컷이면 null 이다 '
  '— 그때도 승인 자체는 유효하다(baseline_cut_id 는 항상 있다).';

comment on column public.approved_baselines.truth_package_id is
  'Phase 4(Product Truth) 예약. 테이블이 없어 FK 를 걸지 않았다 — 생기면 후속 migration 에서 건다.';

comment on column public.approved_baselines.baseline_qc_result_id is
  'QC Result 정규화 예약(동일 사유). 그 사이의 판정은 qc_scores_snapshot 이 보존한다.';

comment on column public.approved_baselines.locked_invariants is
  '승인 시점에 고정된 불변 항목. 값을 확보할 수 없는 항목은 거짓으로 채우지 않고 '
  '{"status":"unavailable","reason":...} 로 남긴다 — 나중에 프로필이 생겨도 과거 승인의 '
  '의미가 소급해 바뀌면 안 된다.';

comment on column public.approved_baselines.superseded_at is
  'null = active. 새 승인이 이 값을 채우고 자기 자신을 active 로 만든다(같은 tx).';

-- active baseline 은 project 당 **하나**. partial unique index 가 동시 승인 경쟁까지 막는다
-- (애플리케이션 락이 아니라 DB 제약이라 워커·라우트가 동시에 들어와도 성립한다).
create unique index if not exists approved_baselines_one_active_per_project
  on public.approved_baselines (project_id) where superseded_at is null;

create index if not exists approved_baselines_project_approved_idx
  on public.approved_baselines (project_id, approved_at desc);

create index if not exists approved_baselines_cut_idx
  on public.approved_baselines (baseline_cut_id);

-- ── 파생 계보: 편집 입력으로 쓴 이전 결과 ──
-- generation_run_id 와 혼동 금지:
--   · generation_run_id  = 이 결과를 만든 **provider 호출**
--   · parent_output_id   = 편집 입력으로 쓴 **이전 결과물**
-- 조정 편집은 이전 job 의 산출물을 입력으로 받으므로 둘은 다른 job 을 가리킨다.
comment on column public.generation_outputs.parent_output_id is
  '편집 입력으로 사용한 이전 output(대개 승인 baseline 의 output). 이 결과를 만든 호출은 '
  'generation_run_id 다 — 둘은 다른 축이다. legacy 컷처럼 output 행이 없으면 null(추정 금지).';

alter table public.generation_outputs
  add column if not exists baseline_id uuid
    references public.approved_baselines (id) on delete set null;

comment on column public.generation_outputs.baseline_id is
  '이 결과가 어느 승인 baseline 에서 파생됐는가. parent_output_id 가 그 baseline 의 output 이다.';

create index if not exists generation_outputs_parent_idx
  on public.generation_outputs (parent_output_id);

-- ── 승인 감사 기록 ──
-- job_events 는 job_id FK 라 "승인"처럼 job 이 없는 행위를 담을 수 없다. 범용 이벤트
-- 프레임워크를 새로 만들지 않고, Phase 2 에 필요한 최소 감사 테이블만 둔다.
create table if not exists public.baseline_review_events (
  id bigserial primary key,
  project_id uuid not null references public.projects (id) on delete cascade,
  baseline_id uuid references public.approved_baselines (id) on delete set null,
  mannequin_cut_id uuid references public.mannequin_cuts (id) on delete set null,
  output_id uuid references public.generation_outputs (id) on delete set null,
  actor_id uuid references auth.users (id) on delete set null,
  action text not null,
  detail jsonb,
  created_at timestamptz not null default now()
);

comment on table public.baseline_review_events is
  'baseline 승인/교체 감사 기록. action: baseline_approved | baseline_superseded | '
  'baseline_reapproved(멱등 재승인). 이미지 바이트·URL·프롬프트 원문 금지.';

create index if not exists baseline_review_events_project_idx
  on public.baseline_review_events (project_id, created_at desc);

-- ── RLS: 소유권은 projects 가 정본 ──
alter table public.approved_baselines enable row level security;

alter table public.baseline_review_events enable row level security;

drop policy if exists approved_baselines_owner_select on public.approved_baselines;

create policy approved_baselines_owner_select on public.approved_baselines
  for select using (
    exists (
      select 1 from public.projects p
      where p.id = approved_baselines.project_id and p.user_id = (select auth.uid())
    )
  );

drop policy if exists baseline_review_events_owner_select on public.baseline_review_events;

create policy baseline_review_events_owner_select on public.baseline_review_events
  for select using (
    exists (
      select 1 from public.projects p
      where p.id = baseline_review_events.project_id and p.user_id = (select auth.uid())
    )
  );
