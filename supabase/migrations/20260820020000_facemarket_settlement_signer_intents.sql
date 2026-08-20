-- Crash-safe fence for the single FaceMarket settlement signer.
-- A broadcasting row is committed before RPC submission so the next API task
-- reconciles that payment before it may reuse the signer's latest nonce.

create table if not exists public.fm_settlement_signer_intents (
  payment_id       text primary key,
  license_id       uuid references public.fm_licenses(id) on delete set null,
  job_id           uuid references public.jobs(id) on delete set null,
  credit_ledger_id uuid references public.credit_ledger(id) on delete set null,
  model_id         text not null,
  total_amount     bigint not null check (total_amount > 0),
  status           text not null default 'queued'
                     check (status in ('queued', 'broadcasting', 'confirmed')),
  attempted_at     timestamptz,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);

create index if not exists fm_settlement_signer_intents_recovery_idx
  on public.fm_settlement_signer_intents(status, attempted_at)
  where status = 'broadcasting';

drop trigger if exists fm_settlement_signer_intents_set_updated_at
  on public.fm_settlement_signer_intents;
create trigger fm_settlement_signer_intents_set_updated_at
  before update on public.fm_settlement_signer_intents
  for each row execute function public.set_updated_at();

alter table public.fm_settlement_signer_intents enable row level security;

comment on table public.fm_settlement_signer_intents is
  'Service-only durable fence for crash recovery of the single chain signer nonce.';
