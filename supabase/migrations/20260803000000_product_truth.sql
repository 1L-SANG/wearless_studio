-- Product Truth Package — 상품 원본 사실의 승인 revision (Phase 4, 2026-08-03).
--
-- 생성 모델은 마네킹 착용 형태·주름·조명·배경을 담당하고, 상품 사실은 원본 이미지와
-- 사용자가 승인한 Product Truth revision 이 정본이다. 승인된 revision 은 덮어쓰지 않고
-- 새 revision 으로만 교체한다. 다른 상품의 텍스처/로고/프린트는 여기로 복사하면 안 된다.
--
-- append-only 후속 migration. 기존 products/analyses/assets 구조는 유지한다.

create table if not exists public.product_truth_packages (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects (id) on delete cascade,
  product_id uuid references public.products (id) on delete set null,
  version integer not null,
  status text not null default 'draft',
  schema_version text not null,
  garment_spec jsonb not null default '{}'::jsonb,
  color_spec jsonb not null default '{}'::jsonb,
  pattern_spec jsonb not null default '{}'::jsonb,
  protected_details jsonb not null default '{}'::jsonb,
  source_evidence jsonb not null default '{}'::jsonb,
  uncertain_fields jsonb not null default '[]'::jsonb,
  garment_profile jsonb,
  analysis_confidence numeric,
  source_fingerprint text not null,
  created_by uuid references auth.users (id) on delete set null,
  approved_by uuid references auth.users (id) on delete set null,
  rejected_by uuid references auth.users (id) on delete set null,
  created_at timestamptz not null default now(),
  approved_at timestamptz,
  rejected_at timestamptz,
  superseded_at timestamptz,
  constraint product_truth_packages_version_positive check (version >= 1),
  constraint product_truth_packages_status_check
    check (status in ('draft', 'approved', 'superseded', 'rejected')),
  constraint product_truth_packages_terminal_timestamp_check check (
    (status = 'approved' and approved_at is not null and rejected_at is null and superseded_at is null)
    or (status = 'rejected' and rejected_at is not null and approved_at is null)
    or (status = 'superseded' and approved_at is not null and superseded_at is not null)
    or (status = 'draft' and approved_at is null and rejected_at is null and superseded_at is null)
  )
);

comment on table public.product_truth_packages is
  '상품 원본 사실의 revision. draft 는 분석/사용자 수정 중이고, approved 만 생성·QC 의 정본으로 '
  '쓸 수 있다. 상품 사진/분석이 바뀌면 source_fingerprint 가 달라져 새 revision 을 만들어야 한다.';
comment on column public.product_truth_packages.garment_spec is
  '의류 구조 사실(category, fit, collar, sleeve, buttonCount, pocketCount 등). 생성 모델 추정값이 '
  '아니라 원본/사용자 승인 facts 이다.';
comment on column public.product_truth_packages.color_spec is
  'Lab 기준 색상 facts. 고해상도 출력 여부와 색상 정확도는 별도 QC 대상이다.';
comment on column public.product_truth_packages.pattern_spec is
  '체크/스트라이프/무지 등 패턴 facts. 방향·주기·색 순서가 준비되면 여기에 누적한다.';
comment on column public.product_truth_packages.protected_details is
  '로고·프린팅·자수·단추 수·주머니 수처럼 생성 결과를 그대로 믿으면 안 되는 보호 대상.';
comment on column public.product_truth_packages.source_fingerprint is
  'product JSON + analysis JSON + source asset role/checksum snapshot 의 sha256. 현재 값과 다르면 stale.';

create unique index if not exists product_truth_packages_project_version_idx
  on public.product_truth_packages (project_id, version);
create unique index if not exists product_truth_packages_one_draft_per_project
  on public.product_truth_packages (project_id) where status = 'draft';
create unique index if not exists product_truth_packages_one_approved_per_project
  on public.product_truth_packages (project_id) where status = 'approved';
create index if not exists product_truth_packages_project_created_idx
  on public.product_truth_packages (project_id, created_at desc);
create index if not exists product_truth_packages_fingerprint_idx
  on public.product_truth_packages (project_id, source_fingerprint);

create table if not exists public.product_truth_assets (
  id uuid primary key default gen_random_uuid(),
  truth_package_id uuid not null references public.product_truth_packages (id) on delete cascade,
  asset_id uuid references public.assets (id) on delete restrict,
  role text not null,
  view text,
  color_id text,
  part text,
  sort_order integer not null default 0,
  checksum text,
  width integer,
  height integer,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint product_truth_assets_role_check check (role in (
    'FRONT', 'BACK', 'DETAIL', 'FIT', 'FABRIC_MACRO', 'LOGO', 'PRINT', 'EMBROIDERY',
    'COLLAR', 'SLEEVE', 'CUFF', 'BUTTON', 'POCKET', 'CARE_LABEL', 'OTHER'
  )),
  constraint product_truth_assets_dimensions_check check (
    (width is null or width > 0) and (height is null or height > 0)
  )
);

comment on table public.product_truth_assets is
  'Product Truth revision 이 참조한 원본/파생 asset snapshot. raw 와 normalized derivative 는 '
  '서로 다른 assets 행으로 저장하고 role/checksum 으로 증거를 남긴다.';
comment on column public.product_truth_assets.role is
  'FRONT/BACK/DETAIL/FABRIC_MACRO/LOGO/PRINT 등. Detail 슬롯 하나를 원단·로고·카라 등으로 '
  '세분화할 수 있게 truth role 이 정본이다.';
comment on column public.product_truth_assets.checksum is
  '분석/생성에 실제 근거가 된 asset checksum. asset 행이 null 이 되더라도 revision 증거는 남는다.';

create index if not exists product_truth_assets_package_idx
  on public.product_truth_assets (truth_package_id, sort_order);
create index if not exists product_truth_assets_asset_idx
  on public.product_truth_assets (asset_id);
create index if not exists product_truth_assets_role_idx
  on public.product_truth_assets (truth_package_id, role);

create table if not exists public.product_truth_review_events (
  id bigserial primary key,
  project_id uuid not null references public.projects (id) on delete cascade,
  truth_package_id uuid references public.product_truth_packages (id) on delete set null,
  actor_id uuid references auth.users (id) on delete set null,
  action text not null,
  detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint product_truth_review_events_action_check check (
    action in ('truth_drafted', 'truth_updated', 'truth_approved', 'truth_rejected', 'truth_superseded')
  )
);

comment on table public.product_truth_review_events is
  'Product Truth draft/update/approve/reject/supersede 감사 기록. 이미지 바이트·URL·프롬프트 원문 금지.';

create index if not exists product_truth_review_events_project_idx
  on public.product_truth_review_events (project_id, created_at desc);

-- 기존 Phase 2 예약 컬럼에 FK 를 붙인다. baseline 은 승인 당시 Product Truth revision 과
-- 함께 해석되어야 하므로 package 삭제는 restrict 가 맞다.
alter table public.approved_baselines
  drop constraint if exists approved_baselines_truth_package_id_fkey;
alter table public.approved_baselines
  add constraint approved_baselines_truth_package_id_fkey
  foreign key (truth_package_id) references public.product_truth_packages (id) on delete restrict;

-- Generation Run 도 어떤 truth revision 으로 호출됐는지 저장한다. nullable:
-- flag off/legacy/migration 미적용 환경과 호환하기 위함이다.
alter table public.generation_runs
  add column if not exists truth_package_id uuid
    references public.product_truth_packages (id) on delete set null;

comment on column public.generation_runs.truth_package_id is
  '이 provider 호출이 기준으로 삼은 승인 Product Truth revision. null = legacy/flag off/기록 실패.';

create index if not exists generation_runs_truth_package_idx
  on public.generation_runs (truth_package_id);

-- ── RLS: 읽기는 프로젝트 소유권 기준. 쓰기는 service-role 백엔드 전용. ──
alter table public.product_truth_packages enable row level security;
alter table public.product_truth_assets enable row level security;
alter table public.product_truth_review_events enable row level security;

drop policy if exists product_truth_packages_owner_select on public.product_truth_packages;
create policy product_truth_packages_owner_select on public.product_truth_packages
  for select using (
    exists (
      select 1 from public.projects p
      where p.id = product_truth_packages.project_id and p.user_id = (select auth.uid())
    )
  );

drop policy if exists product_truth_assets_owner_select on public.product_truth_assets;
create policy product_truth_assets_owner_select on public.product_truth_assets
  for select using (
    exists (
      select 1
      from public.product_truth_packages tp
      join public.projects p on p.id = tp.project_id
      where tp.id = product_truth_assets.truth_package_id
        and p.user_id = (select auth.uid())
    )
  );

drop policy if exists product_truth_review_events_owner_select on public.product_truth_review_events;
create policy product_truth_review_events_owner_select on public.product_truth_review_events
  for select using (
    exists (
      select 1 from public.projects p
      where p.id = product_truth_review_events.project_id and p.user_id = (select auth.uid())
    )
  );
