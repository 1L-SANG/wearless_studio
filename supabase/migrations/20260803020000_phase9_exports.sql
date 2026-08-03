-- Phase 9 export backend: deterministic long PNG / ZIP outputs.
-- Export jobs are provider-free and idempotency-key driven; they should not join a
-- different active export for the same project.

alter table public.jobs drop constraint if exists jobs_kind_check;
alter table public.jobs add constraint jobs_kind_check
  check (kind in ('analyze', 'mannequin', 'mannequin_adjust', 'detail_page', 'editor_image',
                  'personalization_generation', 'personalization_purge',
                  'fm_model_asset_build', 'export'));

drop index if exists public.jobs_active_unique_idx;
create unique index jobs_active_unique_idx on public.jobs (project_id, kind)
  where status in ('pending', 'running')
    and kind not in ('editor_image', 'personalization_generation',
                     'personalization_purge', 'export');

alter table public.exports
  add column if not exists options jsonb not null default '{}'::jsonb,
  add column if not exists snapshot_hash text,
  add column if not exists manifest jsonb not null default '{}'::jsonb,
  add column if not exists error_code text,
  add column if not exists error_message text,
  add column if not exists byte_size bigint,
  add column if not exists mime_type text;

create table if not exists public.export_assets (
  id uuid primary key default gen_random_uuid(),
  export_id uuid not null references public.exports (id) on delete cascade,
  project_id uuid not null references public.projects (id) on delete cascade,
  asset_id uuid not null references public.assets (id) on delete cascade,
  role text not null check (role in ('long_png', 'zip')),
  filename text not null,
  mime_type text not null,
  byte_size bigint,
  sha256 text not null,
  created_at timestamptz not null default now(),
  unique (export_id, role)
);
create index if not exists export_assets_project_idx
  on public.export_assets (project_id, created_at desc);

create table if not exists public.export_provenance (
  export_id uuid primary key references public.exports (id) on delete cascade,
  project_id uuid not null references public.projects (id) on delete cascade,
  job_id uuid not null references public.jobs (id) on delete cascade,
  renderer_version text not null,
  snapshot_hash text not null,
  body_hash text not null,
  options_hash text not null,
  request_body jsonb not null default '{}'::jsonb,
  manifest jsonb not null default '{}'::jsonb,
  provider_calls integer not null default 0 check (provider_calls = 0),
  created_at timestamptz not null default now()
);
create index if not exists export_provenance_project_idx
  on public.export_provenance (project_id, created_at desc);

alter table public.export_assets enable row level security;
alter table public.export_provenance enable row level security;

drop policy if exists export_assets_owner_select on public.export_assets;
create policy export_assets_owner_select on public.export_assets
  for select using (exists (
    select 1 from public.projects p
    where p.id = export_assets.project_id and p.user_id = (select auth.uid())));

drop policy if exists export_provenance_owner_select on public.export_provenance;
create policy export_provenance_owner_select on public.export_provenance
  for select using (exists (
    select 1 from public.projects p
    where p.id = export_provenance.project_id and p.user_id = (select auth.uid())));
