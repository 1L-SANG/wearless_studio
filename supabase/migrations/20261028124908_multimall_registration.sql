begin;

create table public.mall_sales_defaults (
  user_id uuid primary key references auth.users(id) on delete cascade,
  shipping jsonb not null default '{}' check (jsonb_typeof(shipping) = 'object'),
  returns jsonb not null default '{}' check (jsonb_typeof(returns) = 'object'),
  disclosure jsonb not null default '{}' check (jsonb_typeof(disclosure) = 'object'),
  tax_type text not null default 'TAX' check (tax_type in ('TAX', 'FREE')),
  product_origin text not null default 'domestic' check (product_origin in ('domestic', 'imported')),
  shipping_days integer not null default 2 check (shipping_days between 1 and 30),
  listing_mode text not null default 'HIDDEN' check (listing_mode in ('HIDDEN', 'ON_SALE')),
  revision bigint not null default 1 check (revision >= 1),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table public.mall_sales_defaults enable row level security;
revoke all on public.mall_sales_defaults from anon, authenticated;
grant select, insert, update, delete on public.mall_sales_defaults to service_role;

alter table public.mall_defaults add column revision bigint not null default 1 check (revision >= 1);

-- Replace both admission indexes atomically, preserving the six existing exclusions.
drop index public.jobs_active_unique_idx;
create unique index jobs_active_unique_idx on public.jobs (project_id, kind)
  where status in ('pending', 'running')
    and kind not in ('editor_image', 'personalization_generation',
                     'personalization_purge', 'export', 'base_fidelity_observe',
                     'editor_garment_mask', 'mall_register');
create unique index jobs_mall_register_active_unique_idx
  on public.jobs (project_id, (payload->>'mall'))
  where kind = 'mall_register' and status in ('pending', 'running');
alter table public.jobs add constraint jobs_mall_register_payload_check
  check (kind <> 'mall_register' or
    (project_id is not null and coalesce(payload->>'mall', '') in ('cafe24', 'zigzag', 'ably')));
create index jobs_mall_register_batch_idx
  on public.jobs (user_id, (payload->>'batchId'), created_at)
  where kind = 'mall_register' and payload ? 'batchId';

commit;
