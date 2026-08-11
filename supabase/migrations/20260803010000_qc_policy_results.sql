-- Structured QC results and immutable policy snapshots (Phase 5-7).
-- Existing mannequin_cuts.qc_scores remains as a compatibility projection.

create table if not exists public.qc_results (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects (id) on delete cascade,
  truth_package_id uuid references public.product_truth_packages (id) on delete set null,
  generation_output_id uuid references public.generation_outputs (id) on delete set null,
  cut_id uuid references public.mannequin_cuts (id) on delete set null,
  policy_version text not null,
  pipeline_lane text not null,
  overall_decision text not null,
  scores jsonb not null default '{}'::jsonb,
  checks jsonb not null default '[]'::jsonb,
  critical_errors jsonb not null default '[]'::jsonb,
  warnings jsonb not null default '[]'::jsonb,
  failed_regions jsonb not null default '[]'::jsonb,
  regeneration_instructions jsonb not null default '[]'::jsonb,
  debug_assets jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  constraint qc_results_lane_check check (pipeline_lane in ('FAST', 'GUARDED', 'MANUAL')),
  constraint qc_results_decision_check check (overall_decision in ('pass', 'review', 'reject'))
);

comment on table public.qc_results is
  '한 최종 output에 대한 독립 QC check 결과와 결정적 policy 판정. Vision 관찰은 check 입력일 '
  '뿐 overall_decision을 직접 쓰지 않는다.';

comment on column public.qc_results.checks is
  'composition/image_quality/color_fidelity/pattern_fidelity/garment_structure/style_consistency '
  '등 단일 책임 check JSON 배열. unavailable은 pass가 아니라 review 근거다.';

create index if not exists qc_results_project_created_idx
  on public.qc_results (project_id, created_at desc);

create index if not exists qc_results_output_idx
  on public.qc_results (generation_output_id);

create index if not exists qc_results_truth_idx
  on public.qc_results (truth_package_id);

alter table public.qc_results enable row level security;

drop policy if exists qc_results_owner_select on public.qc_results;

create policy qc_results_owner_select on public.qc_results
  for select using (
    exists (
      select 1 from public.projects p
      where p.id = qc_results.project_id and p.user_id = (select auth.uid())
    )
  );
