create table public.fm_payout_accounts (
  model_id uuid primary key references public.fm_models(id) on delete cascade,
  bank_code text not null check (bank_code in ('shinhan','kb','woori','hana','nh','ibk','kakao','toss')),
  account_number_enc text not null,
  account_last4 text not null check (account_last4 ~ '^[0-9]{4}$'),
  holder_name text not null check (char_length(holder_name) between 1 and 40),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table public.fm_payout_accounts enable row level security;
create trigger fm_payout_accounts_set_updated_at before update on public.fm_payout_accounts
  for each row execute function public.set_updated_at();
