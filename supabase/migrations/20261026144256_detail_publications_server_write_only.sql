drop policy if exists detail_publications_owner_insert on public.detail_publications;
drop policy if exists detail_publications_owner_update on public.detail_publications;
revoke all on public.detail_publications from anon, authenticated;
grant select on public.detail_publications to authenticated;
grant all on public.detail_publications to service_role;
