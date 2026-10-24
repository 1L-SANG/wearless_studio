-- Server encrypts mall credentials with a dedicated MALL_KEYS_KEK.
create table if not exists public.mall_connections (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  mall text not null check (mall in ('zigzag','ably','smartstore')),
  status text not null default 'connected' check (status in ('connected','revoked')),
  shop_id text,
  shop_name text,
  key_hint text check (char_length(key_hint) <= 8),
  access_key_enc bytea,
  secret_key_enc bytea,
  permissions text[] not null default '{}',
  connected_at timestamptz not null default now(),
  last_registered_at timestamptz,
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create unique index if not exists mall_connections_active_owner_mall
  on public.mall_connections (user_id, mall) where status = 'connected';
alter table public.mall_connections enable row level security;
create policy mall_connections_owner_select on public.mall_connections
  for select to authenticated using ((select auth.uid()) = user_id);
revoke all on public.mall_connections from anon, authenticated;
grant select (id, user_id, mall, status, shop_id, shop_name, key_hint, permissions,
  connected_at, last_registered_at, revoked_at, created_at, updated_at)
  on public.mall_connections to authenticated;
grant all on public.mall_connections to service_role;

create table if not exists public.mall_defaults (
  user_id uuid not null references auth.users(id) on delete cascade,
  mall text not null check (mall in ('zigzag','ably','smartstore')),
  shop_id text,
  shipping jsonb not null default '{}',
  returns jsonb not null default '{}',
  addresses jsonb not null default '{}',
  disclosure jsonb not null default '{}',
  tax_type text not null default 'TAX' check (tax_type in ('TAX','FREE')),
  product_origin text not null default 'domestic' check (product_origin in ('domestic','imported')),
  completed_items text[] not null default '{}',
  updated_at timestamptz not null default now(),
  primary key (user_id, mall)
);
alter table public.mall_defaults enable row level security;
create policy mall_defaults_owner_select on public.mall_defaults
  for select to authenticated using ((select auth.uid()) = user_id);
revoke all on public.mall_defaults from anon, authenticated;
grant select on public.mall_defaults to authenticated;
grant all on public.mall_defaults to service_role;
