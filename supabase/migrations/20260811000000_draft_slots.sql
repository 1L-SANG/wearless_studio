create table if not exists public.draft_slots (
  user_id uuid primary key references auth.users (id) on delete cascade,
  payload jsonb not null,
  active_token uuid not null,
  device_label text,
  photos_pending boolean not null default false,
  updated_at timestamptz not null default now(),
  expires_at timestamptz not null
);

alter table public.draft_slots enable row level security;

create policy draft_slots_owner_select on public.draft_slots
  for select using (user_id = auth.uid());
