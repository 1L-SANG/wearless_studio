-- App launches get consent before any Wearless account is selected.
create table public.cafe24_pending_installs (
  id uuid primary key default gen_random_uuid(),
  mall_id text not null,
  shop_name text,
  return_to text,
  cookie_hash bytea not null,
  callback_at timestamptz,
  access_token_enc bytea,
  refresh_token_enc bytea,
  token_expires_at timestamptz,
  refresh_expires_at timestamptz,
  scopes text[] not null default '{}',
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);
create index cafe24_pending_installs_expiry_idx on public.cafe24_pending_installs (expires_at);
alter table public.cafe24_pending_installs enable row level security;
revoke all on public.cafe24_pending_installs from public, anon, authenticated;
grant all on public.cafe24_pending_installs to service_role;
