-- 추가 구매(충전) 팩을 5종에서 2종으로 줄인다 — 오너 결정 2026-09-24.
--   100 크레딧 ₩5,500  (크레딧당 55원)
--   500 크레딧 ₩26,000 (크레딧당 52원)
-- 둘 다 구독(Starter 49.8원, Seller 43.7원, Pro 42.5원)보다 크레딧당 비싸다. 자주 쓰면 구독이 이득이라는
-- 구조를 유지한다. 충전 크레딧은 소멸하지 않는다.
--
-- 기존 5종은 지우지 않고 판매만 끈다(is_active=false). 결제·지급 기록(payment_history, credit_sources,
-- bank_transfer_requests)이 plan_id·plan_code 로 이 행들을 가리키기 때문이다. 이미 들어온 계좌이체 신청은
-- 신청 행의 금액·크레딧 스냅샷으로 지급하므로(bank_transfer_service.confirm_request → purchase_topup(snapshot=…),
-- 스냅샷 경로는 is_active 를 보지 않는다) 판매 중지 뒤에도 그대로 확인·지급된다.
begin;

update public.pricing_plans set is_active = false
  where kind = 'topup' and is_active = true
    and code in ('topup_finish', 'topup_start', 'topup_repeat', 'topup_season', 'topup_bulk');

insert into public.pricing_plans (code, kind, name, credits, price, billing_period, sort_order, is_active)
values
  ('topup_100', 'topup', '100 크레딧', 100, 5500, 'once', 11, true),
  ('topup_500', 'topup', '500 크레딧', 500, 26000, 'once', 12, true)
on conflict (code) do nothing;

commit;
