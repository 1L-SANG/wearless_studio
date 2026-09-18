-- 오너 결정(2026-09-18): Seller 79,900원 → 69,900원, Pro 159,000원 → 119,000원.
-- 크레딧은 가격이 내린 만큼 비례해 줄인다(크레딧당 단가 유지): Seller 1,800 → 1,600, Pro 3,800 → 2,800.
-- 정본: documents/research/2026-09-07-credit-pricing-plans.md §5-3.
-- 활성 상품의 정확한 직전 값(가격과 크레딧 둘 다)이 맞을 때만 전진 변환한다. 다른 상태면 아무 행도 바뀌지 않는다.
-- updated_at 은 pricing_plans_updated_at 트리거가 채운다.
begin;

update public.pricing_plans set price = 69900, credits = 1600
  where code = 'seller' and kind = 'subscription'
    and price = 79900 and credits = 1800 and is_active = true;

update public.pricing_plans set price = 119000, credits = 2800
  where code = 'pro' and kind = 'subscription'
    and price = 159000 and credits = 3800 and is_active = true;

-- 결제 금액의 정본은 청구 시점의 이 표다. 신규 구독(subscriptions.py)과 갱신·재시도(workers/subscription_biller.py)
-- 모두 청구 직전에 pricing_plans 를 다시 읽으므로 옛 금액이 남은 pending 청구서는 재사용되지 않는다. 손대지 않는다.
-- 충전 상품(toss_payment_orders)은 가격이 바뀌지 않았다.

commit;
