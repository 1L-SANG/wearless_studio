-- 마네킹의 활성 작업에 합류한 요청도 응답 유실 후 같은 작업으로 복귀한다.
-- 서버 전용 매핑이며 기존 jobs.idempotency_key와 기존 요청의 의미는 유지한다.
create table public.mannequin_job_requests (
  user_id uuid not null references auth.users(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  idempotency_key text not null,
  job_id uuid not null references public.jobs(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (user_id, project_id, idempotency_key)
);

create index mannequin_job_requests_job_idx on public.mannequin_job_requests(job_id);
alter table public.mannequin_job_requests enable row level security;
revoke all on public.mannequin_job_requests from anon, authenticated;
