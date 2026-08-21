create table if not exists public.ai_output_cleanup_intents (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null,
  r2_key text not null unique,
  status text not null default 'pending'
    constraint ai_output_cleanup_intents_status_check
    check (status in ('pending','delete_pending')),
  not_before timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists ai_output_cleanup_intents_due_idx
  on public.ai_output_cleanup_intents(status, not_before, created_at);
create index if not exists ai_output_cleanup_intents_job_idx
  on public.ai_output_cleanup_intents(job_id);

drop trigger if exists ai_output_cleanup_intents_set_updated_at
  on public.ai_output_cleanup_intents;
create trigger ai_output_cleanup_intents_set_updated_at
  before update on public.ai_output_cleanup_intents
  for each row execute function public.set_updated_at();

alter table public.ai_output_cleanup_intents enable row level security;
revoke all on public.ai_output_cleanup_intents from anon, authenticated;
