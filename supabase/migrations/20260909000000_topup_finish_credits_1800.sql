-- 마무리 충전 지급량을 정본에 맞춘다.
-- 요금제 정본 documents/research/2026-09-07-credit-pricing-plans.md §5 는 충전 다섯 상품을
-- 1,800 / 4,700 / 13,800 / 30,500 / 64,000 으로 정한다. 바로 앞 마이그레이션
-- (20260908120000_pricing_plans_v9.sql) 이 한 판 이전 검토값인 1,900 을 넣었다.
-- append-only 규칙에 따라 그 파일을 고치지 않고 여기서 앞으로 굴려 바로잡는다.
-- credits 조건을 달아 이미 1,800 인 DB 나 이후 값 변경분을 덮어쓰지 않는다.
update public.pricing_plans
set credits = 1800
where code = 'topup_finish' and credits = 1900;
