-- Manual transfers retain their original amount, selected entries and encrypted account.
alter table public.fm_payout_accounts add column account_version uuid not null default gen_random_uuid();
create function public.fm_payout_account_version() returns trigger language plpgsql as $$
begin
  new.account_version := gen_random_uuid();
  return new;
end;
$$;
create trigger fm_payout_account_version before update on public.fm_payout_accounts
  for each row execute function public.fm_payout_account_version();

create table public.fm_payout_confirmations (
  id uuid primary key,
  model_id uuid not null references public.fm_models(id),
  period_month date not null check (extract(day from period_month) = 1),
  responsible_admin text not null,
  amount bigint not null check (amount > 0),
  count integer not null check (count > 0),
  bank_code text not null,
  holder_name text not null,
  account_number_enc text not null,
  account_last4 text not null,
  account_version uuid not null,
  status text not null default 'prepared' check (status in ('prepared','transfer_started','paid','cancelled')),
  created_at timestamptz not null default now(),
  started_at timestamptz,
  paid_at timestamptz,
  cancelled_at timestamptz,
  check ((status = 'prepared' and started_at is null and paid_at is null and cancelled_at is null)
      or (status = 'transfer_started' and started_at is not null and paid_at is null and cancelled_at is null)
      or (status = 'paid' and started_at is not null and paid_at is not null and cancelled_at is null)
      or (status = 'cancelled' and started_at is null and paid_at is null and cancelled_at is not null))
);
create unique index fm_payout_one_active_confirmation on public.fm_payout_confirmations(model_id, period_month)
  where status in ('prepared','transfer_started');
create table public.fm_payout_confirmation_entries (
  confirmation_id uuid not null references public.fm_payout_confirmations(id),
  settlement_id uuid not null references public.fm_settlements(id),
  amount bigint not null check (amount >= 0),
  released_at timestamptz,
  primary key (confirmation_id, settlement_id)
);
create unique index fm_payout_one_active_allocation on public.fm_payout_confirmation_entries(settlement_id)
  where released_at is null;
alter table public.fm_payout_confirmations enable row level security;
alter table public.fm_payout_confirmation_entries enable row level security;

create function public.fm_payout_confirmation_immutable() returns trigger language plpgsql as $$
begin
  if tg_op = 'DELETE' then raise exception 'payout confirmation history cannot be deleted'; end if;
  if row(new.id,new.model_id,new.period_month,new.responsible_admin,new.amount,new.count,
         new.bank_code,new.holder_name,new.account_number_enc,new.account_last4,new.account_version,new.created_at)
     is distinct from
     row(old.id,old.model_id,old.period_month,old.responsible_admin,old.amount,old.count,
         old.bank_code,old.holder_name,old.account_number_enc,old.account_last4,old.account_version,old.created_at)
     or (old.started_at is not null and new.started_at is distinct from old.started_at)
     or (old.paid_at is not null and new.paid_at is distinct from old.paid_at)
     or (old.cancelled_at is not null and new.cancelled_at is distinct from old.cancelled_at)
     or not (new.status = old.status or (old.status = 'prepared' and new.status in ('transfer_started','cancelled'))
             or (old.status = 'transfer_started' and new.status = 'paid')) then
    raise exception 'payout confirmation is immutable';
  end if;
  return new;
end;
$$;
create trigger fm_payout_confirmation_immutable before update or delete on public.fm_payout_confirmations
  for each row execute function public.fm_payout_confirmation_immutable();

create function public.fm_payout_allocation_immutable() returns trigger language plpgsql as $$
begin
  if tg_op = 'DELETE' then raise exception 'payout allocation history cannot be deleted'; end if;
  if row(new.confirmation_id,new.settlement_id,new.amount) is distinct from row(old.confirmation_id,old.settlement_id,old.amount)
     or old.released_at is not null or new.released_at is null
     or not exists (select 1 from public.fm_payout_confirmations where id = old.confirmation_id and status = 'cancelled') then
    raise exception 'only cancelled allocations can be released';
  end if;
  return new;
end;
$$;
create trigger fm_payout_allocation_immutable before update or delete on public.fm_payout_confirmation_entries
  for each row execute function public.fm_payout_allocation_immutable();
