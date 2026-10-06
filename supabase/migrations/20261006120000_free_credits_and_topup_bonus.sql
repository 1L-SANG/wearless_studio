-- 가입 무료 크레딧과 충전 보너스. 기존 이력을 수정하지 않고 새 계약을 추가한다.
begin;

-- 처음 만든 두 CHECK는 이름이 자동 생성됐다. 실제 컬럼과 정의로 찾아 교체한다.
do $$
declare
  old_check record;
begin
  for old_check in
    select c.conname
      from pg_constraint c
     where c.conrelid = 'public.credit_sources'::regclass
       and c.contype = 'c'
       and pg_get_constraintdef(c.oid) ~ '\msource_type\M'
       and exists (
         select 1 from pg_attribute a
          where a.attrelid = c.conrelid and a.attname = 'source_type'
            and a.attnum = any(c.conkey)
       )
       and not exists (
         select 1
           from unnest(c.conkey) as checked_column(attnum)
           join pg_attribute a
             on a.attrelid = c.conrelid and a.attnum = checked_column.attnum
          where a.attname not in ('source_type', 'period_end')
       )
  loop
    execute format('alter table public.credit_sources drop constraint %I', old_check.conname);
  end loop;
end;
$$;

alter table public.credit_sources
  add constraint credit_sources_source_type_check
    check (source_type in ('subscription', 'topup', 'free')),
  add constraint credit_sources_source_period_check
    check ((source_type = 'subscription' and period_end is not null)
        or (source_type in ('topup', 'free') and period_end is null));

-- 기본 원장은 action_key CHECK가 없다. 배포 DB에 있는 경우만 기존 식을 보존해 확장한다.
do $$
declare
  old_check record;
begin
  for old_check in
    select c.conname, pg_get_expr(c.conbin, c.conrelid) as expression, c.convalidated
      from pg_constraint c
     where c.conrelid = 'public.credit_ledger'::regclass
       and c.contype = 'c'
       and pg_get_constraintdef(c.oid) ~ '\maction_key\M'
  loop
    execute format('alter table public.credit_ledger drop constraint %I', old_check.conname);
    execute format(
      'alter table public.credit_ledger add constraint %I check ((%s) or action_key = %L)%s',
      old_check.conname, old_check.expression, 'grant_signup',
      case when old_check.convalidated then '' else ' not valid' end
    );
  end loop;
end;
$$;

-- credits는 기본 구매량이다. 과거 주문은 보너스 0과 플랜 미기록 상태를 유지한다.
alter table public.bank_transfer_requests
  add column bonus_credits integer not null default 0,
  add column bonus_percent integer not null default 0,
  add column plan_at_order text,
  add constraint bank_transfer_requests_bonus_credits_check check (bonus_credits >= 0),
  add constraint bank_transfer_requests_bonus_percent_check check (bonus_percent >= 0);

alter table public.toss_payment_orders
  add column bonus_credits integer not null default 0,
  add column bonus_percent integer not null default 0,
  add column plan_at_order text,
  add constraint toss_payment_orders_bonus_credits_check check (bonus_credits >= 0),
  add constraint toss_payment_orders_bonus_percent_check check (bonus_percent >= 0);

commit;
