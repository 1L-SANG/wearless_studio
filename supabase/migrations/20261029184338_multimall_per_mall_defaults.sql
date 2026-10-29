begin;

alter table public.mall_defaults
  add column shipping_days integer not null default 2 check (shipping_days between 1 and 30),
  add column listing_mode text not null default 'HIDDEN' check (listing_mode in ('HIDDEN', 'ON_SALE'));

drop table public.mall_sales_defaults;

commit;
