-- 셀러가 직접 올린 매칭 의류(커스텀 매칭) — 큐레이션 카탈로그와 같은 테이블에 공존시킨다.
--
-- 번호 이력: 원래 20260805000000 이었으나 운영 DB 가 그 번호를 image_usage_events 로
-- 이미 기록하고 있어 20260807000000 로 옮겼다(같은 번호 두 개는 db push 가 조용히 건너뛴다).
--
-- 멱등 작성 이유: 이 스키마는 **운영 DB 에 이미 손으로 적용돼 있는데 migration 이력에는
-- 없다**(2026-08-07 실측: 컬럼·제약·인덱스·정책 전부 존재, schema_migrations 엔 미기록).
-- 그래서 이 파일은 fresh/로컬 DB 에서는 스키마를 만들고, 운영에서는 no-op 이어야 한다.
-- if not exists / if exists 없이 두면 운영 적용 시 첫 문장에서 실패한다.

alter table public.matching_items
  add column if not exists owner_user_id uuid references auth.users (id) on delete cascade,
  add column if not exists project_id uuid references public.projects (id) on delete cascade;

alter table public.matching_items
  drop constraint if exists matching_items_owner_project_pair_chk;

alter table public.matching_items
  add constraint matching_items_owner_project_pair_chk check (
    (owner_user_id is null and project_id is null)
    or (owner_user_id is not null and project_id is not null)
  );

create unique index if not exists matching_items_custom_project_uniq
  on public.matching_items (project_id)
  where owner_user_id is not null;

-- 커스텀 매칭 원본은 셀러 업로드에서 파생된 정규화본이라 'derived' 를 허용한다.
alter table public.assets drop constraint if exists assets_source_check;
alter table public.assets add constraint assets_source_check
  check (source in ('upload', 'ai', 'export', 'seed', 'derived'));

-- 큐레이션(공용)과 커스텀(내 것)을 분리한다. 기존 통합 정책은 커스텀 행까지 모두에게
-- 보여주므로 반드시 걷어낸다.
drop policy if exists matching_items_active_select on public.matching_items;

drop policy if exists matching_items_curated_select on public.matching_items;
create policy matching_items_curated_select on public.matching_items
  for select to authenticated
  using (is_active and owner_user_id is null and project_id is null);

drop policy if exists matching_items_custom_owner_select on public.matching_items;
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
