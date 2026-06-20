-- =============================================================
-- 20260620140000_seed_topup_skus.sql
-- 추가구매(top-up) SKU 3종 — 구독 플랜과 같은 용량·가격을 1회성(once) 구매로 제공.
-- 사용자 결정(2026-06-20): "추가구매 = 같은 3종을 1회성으로". credit_system_design.md §1·§2.1.
-- =============================================================

insert into public.pricing_plans (code, kind, name, credits, price, billing_period, sort_order)
values
  ('topup_basic',  'topup', '크레딧 200',   200, 19900, 'once', 11),
  ('topup_plus',   'topup', '크레딧 600',   600, 49900, 'once', 12),
  ('topup_seller', 'topup', '크레딧 1400', 1400, 99900, 'once', 13);
