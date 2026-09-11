create table public.fm_payout_statements (
  id uuid primary key default gen_random_uuid(),
  model_id uuid not null references public.fm_models(id) on delete cascade,
  period_month date not null,
  amount bigint not null check (amount >= 0),
  count integer not null check (count >= 0),
  status text not null default 'scheduled' check (status in ('scheduled','paid','held')),
  scheduled_for date not null,
  paid_at timestamptz,
  note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (model_id, period_month)
);
alter table public.fm_payout_statements enable row level security;
create trigger fm_payout_statements_set_updated_at before update on public.fm_payout_statements
  for each row execute function public.set_updated_at();
