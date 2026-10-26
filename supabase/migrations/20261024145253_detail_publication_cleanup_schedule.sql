-- A direct PUT may finish after URL expiry. Keep deleted rows as scheduled
-- cleanup tombstones even after their first successful storage/CDN cleanup.
alter table public.detail_publications
  add column cleanup_after timestamptz not null default now() + interval '65 minutes',
  add column cleanup_generation bigint not null default 0;

create index detail_publications_cleanup_idx
  on public.detail_publications (cleanup_after, id) where status = 'deleted';

comment on column public.detail_publications.cleanup_after is
  'Next deletion cleanup, initially latest issued PUT expiry plus one hour, then daily integrity rechecks.';

comment on column public.detail_publications.cleanup_generation is
  'Monotonic cleanup attempt fence; only the latest claimant can acknowledge storage and CDN cleanup.';
