-- =============================================================
-- 20260616105229_profiles_plan_tokens.sql
-- profiles.plan을 계약 PlanTier(basic|plus|seller)에 정렬 (TODO §1 🆕).
-- init.sql은 default 'free'(CHECK 없음)로 만들었으나 계약은 basic/plus/seller.
-- 적용된 마이그레이션은 in-place 편집 금지 — 포워드로 정렬.
-- =============================================================

update public.profiles set plan = 'basic' where plan not in ('basic', 'plus', 'seller');
alter table public.profiles alter column plan set default 'basic';
alter table public.profiles add constraint profiles_plan_check
  check (plan in ('basic', 'plus', 'seller'));
