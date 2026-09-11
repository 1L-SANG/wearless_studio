-- 환율 정본: .scratch/task-pricing-14900-credit-rate-20260911.md §1.2, §4.2.
-- 100원 = 2크레딧, 1크레딧 = 50원. 활성 상품의 정확한 직전 값만 전진 변환한다.
begin;

update public.pricing_plans set credits = 600 where code = 'starter'
  and kind = 'subscription' and credits = 6000 and is_active = true;
update public.pricing_plans set credits = 1800 where code = 'seller'
  and kind = 'subscription' and credits = 18000 and is_active = true;
update public.pricing_plans set credits = 3800 where code = 'pro'
  and kind = 'subscription' and credits = 38000 and is_active = true;
update public.pricing_plans set credits = 180 where code = 'topup_finish'
  and kind = 'topup' and credits = 1800 and is_active = true;
update public.pricing_plans set credits = 470 where code = 'topup_start'
  and kind = 'topup' and credits = 4700 and is_active = true;
update public.pricing_plans set credits = 1380 where code = 'topup_repeat'
  and kind = 'topup' and credits = 13800 and is_active = true;
update public.pricing_plans set credits = 3050 where code = 'topup_season'
  and kind = 'topup' and credits = 30500 and is_active = true;
update public.pricing_plans set credits = 6400 where code = 'topup_bulk'
  and kind = 'topup' and credits = 64000 and is_active = true;

-- 환율 변경 전에 만들어진 미승인 주문은 10배 크레딧 스냅샷을 지급할 수 없게 닫는다.
update public.toss_payment_orders set status = 'canceled'
where status = 'pending' and plan_code = 'topup_finish' and credits = 1800;

commit;
