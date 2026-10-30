-- Cafe24 OAuth connections and server-only installation attempts.
alter table public.mall_connections drop constraint if exists mall_connections_mall_check;
alter table public.mall_connections add constraint mall_connections_mall_check
  check (mall in ('zigzag','ably','smartstore','cafe24'));
alter table public.mall_defaults drop constraint if exists mall_defaults_mall_check;
alter table public.mall_defaults add constraint mall_defaults_mall_check
  check (mall in ('zigzag','ably','smartstore','cafe24'));
alter table public.mall_connections
  add column if not exists token_expires_at timestamptz,
  add column if not exists refresh_expires_at timestamptz,
  add column if not exists scopes text[] not null default '{}';

create table if not exists public.mall_install_attempts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  mall text not null check (mall in ('cafe24')),
  mall_id text not null,
  expected_generation text not null,
  return_to text,
  cookie_hash bytea not null,
  started_at timestamptz,
  consumed_at timestamptz,
  saved_at timestamptz,
  voided_at timestamptz,
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);
create index if not exists mall_install_attempts_owner_idx
  on public.mall_install_attempts (user_id, mall, created_at desc);
alter table public.mall_install_attempts enable row level security;
revoke all on public.mall_install_attempts from anon, authenticated;
grant all on public.mall_install_attempts to service_role;

-- Keep the existing active-job index and nullable project_id unchanged.
alter table public.jobs drop constraint if exists jobs_kind_check;
alter table public.jobs add constraint jobs_kind_check
  check (kind in ('analyze', 'mannequin', 'mannequin_adjust', 'detail_page', 'editor_image',
                  'personalization_generation', 'personalization_purge',
                  'fm_model_asset_build', 'export', 'sam_preprocess',
                  'base_fidelity_observe', 'editor_garment_mask', 'matching_cutout',
                  'mall_register'));
