alter table public.fm_model_applications alter column region drop not null;
alter table public.fm_model_applications add column if not exists weight_kg integer
  check (weight_kg is null or (weight_kg between 30 and 200));
