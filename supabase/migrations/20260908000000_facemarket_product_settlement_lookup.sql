-- Product settlement windows join jobs.project_id to fm_settlements.job_id.
-- jobs_project_kind_status_idx already covers the project side of this lookup.
create index if not exists fm_settlements_job_created_idx
  on public.fm_settlements (job_id, created_at desc);
