-- =============================================================
-- 20260616115945_advisor_hardening.sql
-- DB advisor 대응 + 스키마 정합성 (read-only MCP 점검 결과 2026-06-16)
--   1) jobs.kind CHECK 추가 — 계약 "enum = text + CHECK" 원칙, init 누락분
--   2) 함수 search_path 고정 — security: function_search_path_mutable (WARN)
--   3) RLS owner SELECT 정책 12개 → (select auth.uid()) — perf: auth_rls_initplan (WARN)
-- 동작·권한은 동일. 3)은 행마다 재평가만 제거(initPlan 캐싱).
-- 근거: get_advisors + https://supabase.com/docs/guides/database/postgres/row-level-security#call-functions-with-select
-- =============================================================

-- 1) jobs.kind enum 가드 (다른 enum 컬럼과 동일하게 text + CHECK) -------------
-- init.sql은 in-place로 jobs.kind 인라인 CHECK(자동명 jobs_kind_check)를 갖지만,
-- 그 편집은 prod init 적용 후 커밋(1e74490)돼 prod엔 없다 → 파일/prod 드리프트.
-- jobs_status_drop_cancelled와 동일하게 멱등 DROP+ADD로 정렬한다:
--   새 DB = init 인라인 제약을 DROP 후 동일 재생성(무영향), prod = 신규 추가.
--   (plain ADD면 새 DB에서 이름 중복으로 rebuild 깨짐 — 그래서 DROP IF EXISTS 선행)
alter table public.jobs drop constraint if exists jobs_kind_check;
alter table public.jobs add constraint jobs_kind_check
  check (kind in ('analyze', 'mannequin', 'mannequin_adjust', 'detail_page', 'editor_image'));

-- 2) 함수 search_path 고정 (둘 다 본문이 public 테이블 미참조 → '' 안전) -------
alter function public.set_updated_at() set search_path = '';
alter function public.forbid_credit_ledger_mutation() set search_path = '';

-- 3) RLS owner SELECT: auth.uid() → (select auth.uid()) ----------------------
--    roles/qual 구조는 원본 그대로, auth.uid() 호출만 서브쿼리로 감쌈.

-- 3a) 직접 user_id 비교
drop policy if exists profiles_owner_select on public.profiles;
create policy profiles_owner_select on public.profiles
  for select using (user_id = (select auth.uid()));

drop policy if exists credit_accounts_owner_select on public.credit_accounts;
create policy credit_accounts_owner_select on public.credit_accounts
  for select using (user_id = (select auth.uid()));

drop policy if exists credit_ledger_owner_select on public.credit_ledger;
create policy credit_ledger_owner_select on public.credit_ledger
  for select using (user_id = (select auth.uid()));

drop policy if exists jobs_owner_select on public.jobs;
create policy jobs_owner_select on public.jobs
  for select using (user_id = (select auth.uid()));

drop policy if exists projects_owner_select on public.projects;
create policy projects_owner_select on public.projects
  for select using (user_id = (select auth.uid()));

-- assets: 본인 소유 OR seed(무소유 공용)
drop policy if exists assets_owner_select on public.assets;
create policy assets_owner_select on public.assets
  for select using ((user_id = (select auth.uid())) or (source = 'seed'));

-- 3b) project 경유 (exists join)
drop policy if exists products_owner_select on public.products;
create policy products_owner_select on public.products
  for select using (exists (
    select 1 from public.projects p
    where p.id = products.project_id and p.user_id = (select auth.uid())));

drop policy if exists analyses_owner_select on public.analyses;
create policy analyses_owner_select on public.analyses
  for select using (exists (
    select 1 from public.projects p
    where p.id = analyses.project_id and p.user_id = (select auth.uid())));

drop policy if exists mannequin_cuts_owner_select on public.mannequin_cuts;
create policy mannequin_cuts_owner_select on public.mannequin_cuts
  for select using (exists (
    select 1 from public.projects p
    where p.id = mannequin_cuts.project_id and p.user_id = (select auth.uid())));

drop policy if exists wardrobe_images_owner_select on public.wardrobe_images;
create policy wardrobe_images_owner_select on public.wardrobe_images
  for select using (exists (
    select 1 from public.projects p
    where p.id = wardrobe_images.project_id and p.user_id = (select auth.uid())));

drop policy if exists exports_owner_select on public.exports;
create policy exports_owner_select on public.exports
  for select using (exists (
    select 1 from public.projects p
    where p.id = exports.project_id and p.user_id = (select auth.uid())));

-- 3c) job_events: jobs 경유
drop policy if exists job_events_owner_select on public.job_events;
create policy job_events_owner_select on public.job_events
  for select using (exists (
    select 1 from public.jobs j
    where j.id = job_events.job_id and j.user_id = (select auth.uid())));
