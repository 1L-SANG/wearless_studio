alter table public.matching_items
  add column owner_user_id uuid references auth.users (id) on delete cascade,
  add column project_id uuid references public.projects (id) on delete cascade,
  add constraint matching_items_owner_project_pair_chk check (
    (owner_user_id is null and project_id is null)
    or (owner_user_id is not null and project_id is not null)
  );

create unique index matching_items_custom_project_uniq
  on public.matching_items (project_id)
  where owner_user_id is not null;

alter table public.assets drop constraint assets_source_check;
alter table public.assets add constraint assets_source_check
  check (source in ('upload', 'ai', 'export', 'seed', 'derived'));

drop policy matching_items_active_select on public.matching_items;
create policy matching_items_curated_select on public.matching_items
  for select to authenticated
  using (is_active and owner_user_id is null and project_id is null);
create policy matching_items_custom_owner_select on public.matching_items
  for select to authenticated
  using (
    is_active
    and owner_user_id = (select auth.uid())
    and exists (
      select 1 from public.projects p
      where p.id = matching_items.project_id
        and p.user_id = (select auth.uid())
        and p.deleted_at is null
    )
  );
