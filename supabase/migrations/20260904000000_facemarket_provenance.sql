-- =============================================================
-- FaceMarket 3층 출처증명 — 층① 사용 원장 + 층③ 앵커 큐.
-- 설계: docs/superpowers/specs/2026-09-04-facemarket-provenance-design.md
--
-- 🔴 원장은 부모보다 오래 산다. 모든 FK 는 on delete set null 이고, 실제 증빙값
--    (model_id·license_ref·seller_id·image_sha256)은 FK 없는 비정규화 컬럼이다.
--    fm_models → fm_licenses 가 cascade 라 모델 삭제가 라이선스를 지운다.
--    restrict 였다면 2026-08-29 prod 복구가 막혔고, cascade 였다면 원장이 사라졌다.
-- =============================================================

-- ── fm_output_records: 만든 컷 1장 = 1행 ─────────────────────
create table if not exists public.fm_output_records (
  id            uuid primary key default gen_random_uuid(),
  asset_id      uuid unique references public.assets(id) on delete set null,
  job_id        uuid references public.jobs(id) on delete set null,
  license_id    uuid references public.fm_licenses(id) on delete set null,
  license_ref   uuid not null,   -- 비정규화: license_id 가 null 이 돼도 남는다
  model_id      uuid not null,   -- 비정규화(FK 없음)
  seller_id     uuid not null,   -- 생성한 셀러(jobs.user_id)
  image_sha256  text not null,
  byte_size     bigint,
  created_at    timestamptz not null default now()
);
create index if not exists fm_output_records_license_idx
  on public.fm_output_records (license_ref, created_at desc);
create index if not exists fm_output_records_seller_idx
  on public.fm_output_records (seller_id, created_at desc);

-- ── fm_publication_records: 내려받은 파일 1건 = 1행 ──────────
create table if not exists public.fm_publication_records (
  id               uuid primary key default gen_random_uuid(),
  project_id       uuid references public.projects(id) on delete set null,
  seller_id        uuid not null,
  license_id       uuid references public.fm_licenses(id) on delete set null,
  license_ref      uuid not null,
  model_id         uuid not null,
  kind             text not null check (kind in ('long_png', 'block_png', 'zip')),
  image_sha256     text not null,   -- 서명 전 원본
  signed_sha256    text,            -- 서명 후(임베드로 바이트가 바뀐다)
  byte_size        bigint,
  r2_key           text,            -- 서명본 보관. 철회 시 삭제 대상(§9)
  source_asset_ids uuid[] not null default '{}',
  c2pa_manifest    jsonb not null default '{}'::jsonb,
  c2pa_status      text not null default 'skipped'
                     check (c2pa_status in ('signed', 'skipped', 'failed')),
  chain_status     text not null default 'pending'
                     check (chain_status in ('pending', 'confirmed', 'failed')),
  tx_hash          text,
  chain_id         text,
  recorded_block   bigint,
  revoked_at       timestamptz,     -- 철회 표시. 행은 지우지 않는다
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  constraint fm_publication_records_seller_hash_uniq unique (seller_id, image_sha256)
);
create index if not exists fm_publication_records_license_idx
  on public.fm_publication_records (license_ref, created_at desc);
create index if not exists fm_publication_records_seller_idx
  on public.fm_publication_records (seller_id, created_at desc);

drop trigger if exists fm_publication_records_set_updated_at on public.fm_publication_records;
create trigger fm_publication_records_set_updated_at
  before update on public.fm_publication_records
  for each row execute function public.set_updated_at();

-- ── fm_publication_anchor_jobs: 비동기 앵커 큐 ──────────────
-- jobs 테이블을 안 쓰는 이유: jobs_active_unique_idx 가 (project_id, kind) 동시 1건이라
-- 같은 프로젝트에서 연달아 내려받으면 앵커가 서로를 막는다. fm_vc_revocation_jobs 선례를 따른다.
create table if not exists public.fm_publication_anchor_jobs (
  publication_id uuid primary key
                   references public.fm_publication_records(id) on delete cascade,
  status         text not null default 'pending'
                   check (status in ('pending', 'processing', 'retry', 'anchored', 'dead')),
  attempts       integer not null default 0 check (attempts >= 0),
  lease_until    timestamptz,
  attempted_at   timestamptz,
  last_error     text,
  created_at     timestamptz not null default now()
);
create index if not exists fm_publication_anchor_jobs_pending_idx
  on public.fm_publication_anchor_jobs (status, created_at)
  where status in ('pending', 'retry', 'processing');

-- ── RLS: enable + 셀러 owner-select + 모델 owner-select. 쓰기=service-role ──
alter table public.fm_output_records         enable row level security;
alter table public.fm_publication_records    enable row level security;
alter table public.fm_publication_anchor_jobs enable row level security;
-- 앵커 큐는 운영 내부 데이터 — 정책 없음 = service-role 전용.

drop policy if exists fm_output_records_seller_select on public.fm_output_records;
create policy fm_output_records_seller_select on public.fm_output_records
  for select using (seller_id = (select auth.uid()));

drop policy if exists fm_output_records_model_select on public.fm_output_records;
create policy fm_output_records_model_select on public.fm_output_records
  for select using (exists (
    select 1 from public.fm_models m
    where m.id = fm_output_records.model_id and m.user_id = (select auth.uid())));

drop policy if exists fm_publication_records_seller_select on public.fm_publication_records;
create policy fm_publication_records_seller_select on public.fm_publication_records
  for select using (seller_id = (select auth.uid()));

drop policy if exists fm_publication_records_model_select on public.fm_publication_records;
create policy fm_publication_records_model_select on public.fm_publication_records
  for select using (exists (
    select 1 from public.fm_models m
    where m.id = fm_publication_records.model_id and m.user_id = (select auth.uid())));

comment on table public.fm_output_records is
  'FaceMarket 층① 사용 원장(컷 단위). 정산 근거·역추적·분쟁 증빙. 부모(모델·라이선스·자산)가 '
  '삭제돼도 남아야 하므로 FK 는 전부 set null 이고 증빙값은 비정규화 컬럼이다.';
comment on table public.fm_publication_records is
  'FaceMarket 층① 사용 원장(배포본 단위). C2PA 서명 결과와 온체인 앵커 상태를 함께 들고 있다. '
  '철회 시 r2_key 사본만 지우고 행은 revoked_at 표시로 남긴다 — 지우면 무단 사용과 구별이 안 된다.';
