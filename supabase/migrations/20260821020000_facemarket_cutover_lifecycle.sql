create table if not exists public.fm_cutover_batches (
  id uuid primary key default gen_random_uuid(),
  status text not null default 'planned'
    constraint fm_cutover_batches_status_check
    check (status in ('planned','approved','draining','applying','reconciling','completed','failed')),
  target_digest text not null,
  model_count integer not null check (model_count >= 0),
  license_count integer not null check (license_count >= 0),
  job_count integer not null check (job_count >= 0),
  asset_count integer not null check (asset_count >= 0),
  approved_by uuid references auth.users(id),
  approved_at timestamptz,
  started_at timestamptz,
  completed_at timestamptz,
  last_error_code text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists fm_cutover_batches_one_active_idx
  on public.fm_cutover_batches ((true))
  where status in ('approved','draining','applying','reconciling','failed');

alter table public.fm_models add column if not exists previous_status text;
alter table public.fm_models add column if not exists reverification_batch_id uuid;

do $$
begin
  if not exists (
    select 1
      from pg_constraint
     where conrelid = 'public.fm_models'::regclass
       and conname = 'fm_models_reverification_batch_id_fkey'
  ) then
    alter table public.fm_models
      add constraint fm_models_reverification_batch_id_fkey
      foreign key (reverification_batch_id) references public.fm_cutover_batches(id);
  end if;
end $$;

alter table public.fm_licenses alter column face_image_digest drop not null;
alter table public.fm_licenses add column if not exists previous_status text;
alter table public.fm_licenses add column if not exists reverification_batch_id uuid;

do $$
begin
  if not exists (
    select 1
      from pg_constraint
     where conrelid = 'public.fm_licenses'::regclass
       and conname = 'fm_licenses_reverification_batch_id_fkey'
  ) then
    alter table public.fm_licenses
      add constraint fm_licenses_reverification_batch_id_fkey
      foreign key (reverification_batch_id) references public.fm_cutover_batches(id);
  end if;
end $$;

create index if not exists fm_models_reverification_batch_idx
  on public.fm_models(reverification_batch_id);

create index if not exists fm_licenses_reverification_batch_idx
  on public.fm_licenses(reverification_batch_id);

drop trigger if exists fm_cutover_batches_set_updated_at on public.fm_cutover_batches;
create trigger fm_cutover_batches_set_updated_at
  before update on public.fm_cutover_batches
  for each row execute function public.set_updated_at();

alter table public.fm_cutover_batches enable row level security;
revoke all on public.fm_cutover_batches from anon, authenticated;
