-- FaceMarket C1 사용 신고, 조건 변경 이력, 활동 일시 중단.

alter table public.fm_licenses
  alter column license_valid_until drop not null;

alter table public.fm_models
  add column suspension_source text,
  add column suspended_at timestamptz;

-- 기존 suspended 행은 관리 경로에서 만들어진 것으로 안전하게 간주한다.
update public.fm_models
   set suspension_source = 'admin',
       suspended_at = coalesce(updated_at, created_at, now())
 where status = 'suspended';

alter table public.fm_models
  add constraint fm_models_suspension_source_check
  check (suspension_source is null or suspension_source in ('owner', 'admin'));

create table public.fm_license_term_changes (
  id uuid primary key default gen_random_uuid(),
  license_id uuid not null references public.fm_licenses(id) on delete cascade,
  before jsonb not null,
  after jsonb not null,
  actor uuid not null,
  changed_at timestamptz not null default now()
);

create index fm_license_term_changes_license_changed_idx
  on public.fm_license_term_changes(license_id, changed_at desc);

alter table public.fm_license_term_changes enable row level security;

create policy fm_license_term_changes_owner_select
  on public.fm_license_term_changes for select
  using (exists (
    select 1 from public.fm_licenses l
    join public.fm_models m on m.id = l.model_id
    where l.id = fm_license_term_changes.license_id
      and m.user_id = (select auth.uid())
  ));

create table public.fm_usage_reports (
  id uuid primary key default gen_random_uuid(),
  settlement_id uuid not null unique
    references public.fm_settlements(id) on delete cascade,
  model_id uuid not null,
  reason text,
  status text not null default 'open' check (status = 'open'),
  created_at timestamptz not null default now(),
  check (reason is null or char_length(reason) <= 1000)
);

create index fm_usage_reports_model_created_idx
  on public.fm_usage_reports(model_id, created_at desc);

alter table public.fm_usage_reports enable row level security;

create policy fm_usage_reports_owner_select
  on public.fm_usage_reports for select
  using (exists (
    select 1 from public.fm_models m
    where m.id = fm_usage_reports.model_id
      and m.user_id = (select auth.uid())
  ));
