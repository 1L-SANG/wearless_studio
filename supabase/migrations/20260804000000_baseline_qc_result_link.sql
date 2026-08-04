-- Approved Baseline ↔ Structured QC Result linkage.
--
-- 20260801020000 created baseline_qc_result_id before qc_results existed, so the column was
-- intentionally nullable and unconstrained. 20260803010000 now provides qc_results; this
-- append-only migration closes the reserved relationship without rewriting either migration.

alter table public.approved_baselines
  drop constraint if exists approved_baselines_baseline_qc_result_id_fkey;

alter table public.approved_baselines
  add constraint approved_baselines_baseline_qc_result_id_fkey
  foreign key (baseline_qc_result_id) references public.qc_results (id) on delete set null;

create index if not exists approved_baselines_qc_result_idx
  on public.approved_baselines (baseline_qc_result_id);

comment on column public.approved_baselines.baseline_qc_result_id is
  '승인한 output에 대해 가장 최근 저장된 구조화 QC 결과. QC 기록이 꺼졌거나 legacy 컷이면 null.';
