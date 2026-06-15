-- =============================================================
-- 20260612090000_init.sql — Phase 0 스키마 (backend_integration_plan §2)
-- 원칙: 컬럼 snake_case(API camelCase 변환은 FastAPI 책임) ·
--       enum은 text + CHECK · JSONB도 저장 전 Pydantic 검증 전제 ·
--       쓰기는 service-role(FastAPI)만 — RLS는 owner SELECT 2차 방어선
-- =============================================================

-- ---------- updated_at 공통 트리거 ----------
create function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ---------- profiles (계약 §3.7 Account) ----------
create table public.profiles (
  user_id uuid primary key references auth.users (id) on delete cascade,
  display_name text,
  avatar_asset_id uuid, -- FK는 assets 생성 후 (순환 참조)
  plan text not null default 'basic' check (plan in ('basic', 'plus', 'seller')), -- PlanTier (계약 §3.7/§4)
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create trigger profiles_updated_at before update on public.profiles
  for each row execute function public.set_updated_at();

-- ---------- credit_accounts (§6 reserve-then-confirm) ----------
-- 응답 credits = balance - reserved
create table public.credit_accounts (
  user_id uuid primary key references auth.users (id) on delete cascade,
  balance integer not null default 0 check (balance >= 0),
  reserved integer not null default 0 check (reserved >= 0),
  updated_at timestamptz not null default now(),
  check (reserved <= balance)
);
create trigger credit_accounts_updated_at before update on public.credit_accounts
  for each row execute function public.set_updated_at();

-- ---------- projects (계약 §2) ----------
create table public.projects (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  status text not null default 'draft' check (status in ('draft', 'generating', 'done')),
  title text not null default '',
  compose_mode text not null default 'basic',
  copywriting boolean not null default true,
  selected_mannequin_id text, -- 클라이언트 id `${candidate}-${version}` (계약 §3.3)
  adjust_count integer not null default 0 check (adjust_count >= 0),
  storyboard jsonb,
  editor_blocks jsonb,
  editor_revision integer not null default 0,
  cover_asset_id uuid, -- FK는 assets 생성 후 (순환 참조)
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  deleted_at timestamptz -- soft delete → 비동기 cleanup (§9)
);
-- 보관함 조회 (§2 인덱스)
create index projects_user_updated_idx on public.projects (user_id, updated_at desc);
create trigger projects_updated_at before update on public.projects
  for each row execute function public.set_updated_at();

-- ---------- products (계약 §3.1 — project당 1개) ----------
create table public.products (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null unique references public.projects (id) on delete cascade,
  name text not null default '',
  clothing_type text, -- 카탈로그 값 — 검증은 API(Pydantic)
  colors jsonb not null default '[]'::jsonb, -- 이미지 src는 asset URL
  measurements jsonb not null default '[]'::jsonb,
  measurements_unknown boolean not null default false,
  upload_complete boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create trigger products_updated_at before update on public.products
  for each row execute function public.set_updated_at();

-- ---------- analyses (계약 §3.2 — Product 소유 필드 제외) ----------
create table public.analyses (
  project_id uuid primary key references public.projects (id) on delete cascade,
  payload jsonb not null default '{}'::jsonb,
  locked boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create trigger analyses_updated_at before update on public.analyses
  for each row execute function public.set_updated_at();

-- ---------- assets (§3 — R2 키의 단일 레지스트리) ----------
create table public.assets (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users (id) on delete cascade,
  project_id uuid references public.projects (id) on delete set null,
  source text not null check (source in ('upload', 'ai', 'export', 'seed')),
  visibility text not null default 'private' check (visibility in ('private', 'public')),
  r2_bucket text not null,
  r2_key text not null unique,
  mime_type text not null,
  byte_size bigint,
  width integer,
  height integer,
  checksum text,
  original_filename text,
  metadata jsonb not null default '{}'::jsonb, -- 파생본은 metadata.variants (§3)
  created_at timestamptz not null default now(),
  deleted_at timestamptz,
  check (user_id is not null or source = 'seed') -- seed만 무소유 허용
);
create index assets_user_idx on public.assets (user_id);
create index assets_project_idx on public.assets (project_id);

alter table public.profiles
  add constraint profiles_avatar_asset_fk
  foreign key (avatar_asset_id) references public.assets (id) on delete set null;
alter table public.projects
  add constraint projects_cover_asset_fk
  foreign key (cover_asset_id) references public.assets (id) on delete set null;

-- ---------- mannequin_cuts (계약 §3.3) ----------
create table public.mannequin_cuts (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects (id) on delete cascade,
  candidate text not null check (candidate in ('A', 'B')),
  version integer not null default 1 check (version >= 1),
  asset_id uuid not null references public.assets (id),
  base_fit text not null,
  fit_adjust text, -- AdjustFit enum — null = 원본
  length_adjust text,
  match_adjust jsonb, -- { clothingId, fitAdjust, lengthAdjust }
  created_at timestamptz not null default now(),
  unique (project_id, candidate, version)
);

-- ---------- wardrobe_images (계약 §3.6) ----------
create table public.wardrobe_images (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects (id) on delete cascade,
  color_id text,
  asset_id uuid not null references public.assets (id),
  ai boolean not null default false,
  cut_type text,
  sort_order integer not null default 0,
  created_at timestamptz not null default now(),
  deleted_at timestamptz
);
create index wardrobe_images_project_idx on public.wardrobe_images (project_id, sort_order);

-- ---------- matching_items (M-01 데이터 — seedMatchingItems.js 이관) ----------
create table public.matching_items (
  id text primary key, -- seed id 유지 (match_top_white_oxford_shirt …)
  name text not null,
  clothing_type text not null,
  gender text not null,
  category text not null,
  color_name text not null,
  color_group text not null,
  style_tags jsonb not null default '[]'::jsonb,
  fit text not null,
  length text not null,
  image_asset_id uuid references public.assets (id),
  thumbnail_asset_id uuid references public.assets (id),
  is_active boolean not null default true,
  sort_order integer not null default 0
);

-- ---------- jobs (계약 §6 멱등의 구현체 · ai_pipeline_spec §4 · §5 큐) ----------
create table public.jobs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  project_id uuid not null references public.projects (id) on delete cascade,
  kind text not null
    check (kind in ('analyze', 'mannequin', 'mannequin_adjust', 'detail_page', 'editor_image')), -- 계약 §6 · ai_pipeline_spec §4

  status text not null default 'pending'
    check (status in ('pending', 'running', 'done', 'error')),
  progress integer not null default 0 check (progress between 0 and 100),
  steps jsonb not null default '[]'::jsonb,
  payload jsonb not null default '{}'::jsonb,
  result jsonb,
  error_message text,
  dedupe_key text unique,
  idempotency_key text unique,
  credits_reserved integer not null default 0,
  credits_charged integer,
  locked_by text, -- dispatcher lease (§5)
  locked_at timestamptz,
  metadata jsonb not null default '{}'::jsonb, -- agent call 로그 · creditCostVersion
  created_at timestamptz not null default now(),
  started_at timestamptz,
  updated_at timestamptz not null default now(),
  finished_at timestamptz
);
create index jobs_project_kind_status_idx on public.jobs (project_id, kind, status);
-- 같은 프로젝트·kind 동시 시작 DB 차단 (§2 인덱스).
-- 단 editor_image는 다중 허용 — Idempotency-Key(uq)로만 중복 방지 (§5)
create unique index jobs_active_unique_idx on public.jobs (project_id, kind)
  where status in ('pending', 'running') and kind <> 'editor_image';
-- dispatcher claim 스캔 (§5 SKIP LOCKED)
create index jobs_pending_idx on public.jobs (status, created_at) where status = 'pending';
create trigger jobs_updated_at before update on public.jobs
  for each row execute function public.set_updated_at();

-- ---------- job_events (§5 SSE replay · 폴링 폴백 원본) ----------
create table public.job_events (
  id bigint generated always as identity primary key,
  job_id uuid not null references public.jobs (id) on delete cascade,
  event_type text not null check (event_type in ('progress', 'step', 'done', 'error')),
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index job_events_job_idx on public.job_events (job_id, id);

-- ---------- exports (§7 — 내보내기 이력·실패율·옵션 기록) ----------
create table public.exports (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects (id) on delete cascade,
  job_id uuid references public.jobs (id) on delete set null,
  format text not null check (format in ('long', 'zip')),
  asset_id uuid references public.assets (id),
  status text not null default 'pending' check (status in ('pending', 'done', 'error')),
  snapshot_revision integer,
  created_at timestamptz not null default now(),
  finished_at timestamptz,
  expires_at timestamptz
);
create index exports_project_idx on public.exports (project_id, created_at desc);

-- ---------- credit_ledger (§6 — append-only) ----------
create table public.credit_ledger (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id),
  project_id uuid references public.projects (id) on delete set null,
  job_id uuid references public.jobs (id) on delete set null,
  action_key text not null, -- creditCosts 키 + grant/refund
  delta integer not null,
  balance_after integer not null,
  available_after integer not null,
  idempotency_key text unique,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index credit_ledger_user_idx on public.credit_ledger (user_id, created_at);

-- append-only 강제: service-role 포함 update/delete 차단 (§2)
create function public.forbid_credit_ledger_mutation()
returns trigger
language plpgsql
as $$
begin
  raise exception 'credit_ledger is append-only';
end;
$$;
create trigger credit_ledger_append_only
  before update or delete on public.credit_ledger
  for each statement execute function public.forbid_credit_ledger_mutation();

-- =============================================================
-- RLS (§2): 전 테이블 활성화. 쓰기 정책 없음 = 쓰기는 service-role(FastAPI)만.
-- FastAPI도 모든 쿼리에 JWT sub 조건을 명시한다 (§9).
-- =============================================================
alter table public.profiles enable row level security;
alter table public.credit_accounts enable row level security;
alter table public.credit_ledger enable row level security;
alter table public.projects enable row level security;
alter table public.products enable row level security;
alter table public.analyses enable row level security;
alter table public.assets enable row level security;
alter table public.mannequin_cuts enable row level security;
alter table public.wardrobe_images enable row level security;
alter table public.matching_items enable row level security;
alter table public.jobs enable row level security;
alter table public.job_events enable row level security;
alter table public.exports enable row level security;

create policy profiles_owner_select on public.profiles
  for select using (user_id = auth.uid());
create policy credit_accounts_owner_select on public.credit_accounts
  for select using (user_id = auth.uid());
create policy credit_ledger_owner_select on public.credit_ledger
  for select using (user_id = auth.uid());
create policy projects_owner_select on public.projects
  for select using (user_id = auth.uid());
create policy assets_owner_select on public.assets
  for select using (user_id = auth.uid() or source = 'seed');
create policy jobs_owner_select on public.jobs
  for select using (user_id = auth.uid());

-- project 경유 테이블 (§2: exists join)
create policy products_owner_select on public.products
  for select using (exists (
    select 1 from public.projects p
    where p.id = products.project_id and p.user_id = auth.uid()
  ));
create policy analyses_owner_select on public.analyses
  for select using (exists (
    select 1 from public.projects p
    where p.id = analyses.project_id and p.user_id = auth.uid()
  ));
create policy mannequin_cuts_owner_select on public.mannequin_cuts
  for select using (exists (
    select 1 from public.projects p
    where p.id = mannequin_cuts.project_id and p.user_id = auth.uid()
  ));
create policy wardrobe_images_owner_select on public.wardrobe_images
  for select using (exists (
    select 1 from public.projects p
    where p.id = wardrobe_images.project_id and p.user_id = auth.uid()
  ));
create policy exports_owner_select on public.exports
  for select using (exists (
    select 1 from public.projects p
    where p.id = exports.project_id and p.user_id = auth.uid()
  ));
create policy job_events_owner_select on public.job_events
  for select using (exists (
    select 1 from public.jobs j
    where j.id = job_events.job_id and j.user_id = auth.uid()
  ));

-- matching_items: is_active 행만 authenticated select (§2)
create policy matching_items_active_select on public.matching_items
  for select to authenticated using (is_active);
