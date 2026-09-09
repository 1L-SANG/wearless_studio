-- 요금제 정본: documents/research/2026-09-07-credit-pricing-plans.md §5 (v9).
-- 기존 상품 ID와 결제/크레딧 참조를 보존한다. 기능별 차감값은 변경하지 않는다.
begin;

do $$
declare
  legacy_plan_tiers boolean;
begin
  -- 새 seller는 중간 등급이다. 재실행 때 새 seller를 pro로 다시 올리지 않는다.
  select exists (
    select 1 from pg_constraint
    where conrelid = 'public.profiles'::regclass
      and conname = 'profiles_plan_check'
      and pg_get_constraintdef(oid) like '%''basic''%'
  ) into legacy_plan_tiers;

  -- 옛 CHECK는 free/pro를 거부하므로 먼저 제거한다.
  alter table public.profiles drop constraint if exists profiles_plan_check;
  if legacy_plan_tiers then
    -- 단일 CASE로 옛 등급을 이관해 plus가 seller를 거쳐 pro로 승급되는 것을 막는다.
    update public.profiles
    set plan = case plan
      when 'basic' then 'free'
      when 'plus' then 'seller'
      when 'seller' then 'pro'
      else plan
    end
    where plan in ('basic', 'plus', 'seller');
  end if;

  alter table public.profiles alter column plan set default 'free';
  alter table public.profiles add constraint profiles_plan_check
    check (plan in ('free', 'starter', 'seller', 'pro'));
end $$;

-- 옛 seller와 새 seller는 code UNIQUE가 겹친다. 옛 행의 ID/가격/크레딧은 그대로
-- 보존하고 코드만 보관용으로 바꿔 새 상품이 별도 ID를 받도록 한다.
-- 옛 시드의 수치로 한정해 재실행 시 새 seller를 비활성화하지 않는다.
update public.pricing_plans
set code = 'legacy_seller_v8', is_active = false
where code = 'seller' and kind = 'subscription' and credits = 1400 and price = 99900;

update public.pricing_plans
set is_active = false
where code in ('basic', 'plus', 'legacy_seller_v8', 'topup_basic', 'topup_plus', 'topup_seller');

insert into public.pricing_plans
  (code, kind, name, credits, price, billing_period, sort_order, is_active)
values
  ('starter',      'subscription', 'Starter',     6000,  29900, 'monthly',  1, true),
  ('seller',       'subscription', 'Seller',     18000,  79900, 'monthly',  2, true),
  ('pro',          'subscription', 'Pro',        38000, 159000, 'monthly',  3, true),
  ('topup_finish', 'topup',        '마무리 충전',  1900,   9900, 'once',    11, true),
  ('topup_start',  'topup',        '시작 팩',      4700,  24900, 'once',    12, true),
  ('topup_repeat', 'topup',        '반복 팩',     13800,  69900, 'once',    13, true),
  ('topup_season', 'topup',        '시즌 팩',     30500, 149000, 'once',    14, true),
  ('topup_bulk',   'topup',        '대량 팩',     64000, 299000, 'once',    15, true)
on conflict (code) do update set
  kind = excluded.kind,
  name = excluded.name,
  credits = excluded.credits,
  price = excluded.price,
  billing_period = excluded.billing_period,
  sort_order = excluded.sort_order,
  is_active = excluded.is_active;

commit;
