create table if not exists public.detail_publications (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  project_id uuid references public.projects(id) on delete set null,
  version integer not null,
  status text not null default 'pending'
    check (status in ('pending','ready','deleted')),
  preset text not null,
  width integer not null,
  slice_count integer not null default 0,
  total_bytes bigint not null default 0,
  product_name text not null default '',
  editor_revision integer,
  files jsonb not null default '[]'::jsonb,
  expires_at timestamptz,
  created_at timestamptz not null default now(),
  ready_at timestamptz,
  deleted_at timestamptz,
  unique (project_id, version)
);

create index if not exists detail_publications_user_created_idx
  on public.detail_publications (user_id, created_at desc);
create index if not exists detail_publications_expiry_idx
  on public.detail_publications (expires_at) where status = 'ready';

alter table public.detail_publications enable row level security;

drop policy if exists detail_publications_owner_select on public.detail_publications;
create policy detail_publications_owner_select on public.detail_publications
  for select to authenticated using (auth.uid() = user_id);
drop policy if exists detail_publications_owner_insert on public.detail_publications;
create policy detail_publications_owner_insert on public.detail_publications
  for insert to authenticated with check (auth.uid() = user_id);
drop policy if exists detail_publications_owner_update on public.detail_publications;
create policy detail_publications_owner_update on public.detail_publications
  for update to authenticated using (auth.uid() = user_id)
  with check (auth.uid() = user_id);
