-- 신규 추가구매 크레딧의 사용기간을 구매일부터 12개월로 제한한다.
-- 기존 구매는 period_end=null 그대로 두어 당시의 무기한 약속을 보존한다.
begin;

alter table public.credit_sources
  drop constraint if exists credit_sources_source_period_check;

alter table public.credit_sources
  add constraint credit_sources_source_period_check
    check ((source_type = 'subscription' and period_end is not null)
        or source_type = 'topup'
        or (source_type = 'free' and period_end is null));

create index if not exists credit_sources_topup_expiry_idx
  on public.credit_sources (period_end, user_id)
  where source_type = 'topup' and status = 'active' and period_end is not null;

-- 배포 DB에 action_key CHECK가 추가돼 있는 경우 기존 식을 보존하면서 만료 원장을 허용한다.
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
      old_check.conname, old_check.expression, 'expire_topup',
      case when old_check.convalidated then '' else ' not valid' end
    );
  end loop;
end;
$$;

commit;
