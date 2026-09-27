-- =============================================================
-- FaceMarket 자동 출처 추적(2026-09-27, 오너 결정) — 발견 원장 + 셀러 정상 판매처 + 순찰 기록.
--
-- 셀러에게 판매처 등록을 요구하지 않고, 실존 모델 얼굴 이미지가 어디에 쓰이는지 두 갈래로 모은다:
--   ① 순찰(patrol): 하루 1회 허용된 플랫폼(네이버 공식 검색 API · 지그재그)을 상품명으로 검색해
--      대표 이미지를 워터마크·지문(20260926213000)으로 대조한다. 29CM 는 약관(무신사 통합약관
--      제11조②)이 크롤러 수집을 금지해서 만들지 않는다. 무신사·에이블리·쿠팡도 대상 밖이다.
--   ② 모델 제보(model_report): 모델이 마이페이지에서 올린 이미지를 같은 대조로 돌린다.
--
-- 🔴 네이버 검색 API 특약(2026-09-07 개정) 2.3·2.4: 검색 결과 데이터(가공·파생물 포함)는
--    "서버 이력 조회 목적 최대 21일"만 보관할 수 있다. 그래서 네이버에서 온 원문 필드
--    (product_url·image_url·product_title·store_name)는 external_purge_at(last_seen + 21일)이
--    지나면 순찰 워커가 null 로 지운다. 멱등·판매처 대조에 쓰는 키는 sha256 해시로만 남긴다.
--
-- 추가만 한다(additive). 기존 테이블은 건드리지 않는다.
-- =============================================================

-- ── fm_trace_findings: 발견 1건 = 1행 ─────────────────────────
-- 순찰: (플랫폼, 상품, 매칭 대상)마다 1행 — 다시 보이면 last_seen_at·seen_count 만 갱신(dedupe_key).
-- 제보: 제보 1건 = 1행. 매칭이 없어도 남긴다(관리자가 직접 봐야 할 신호다) — dedupe_key 는 null.
-- 이미지 바이트는 어디에도 저장하지 않는다(관리자 추적과 같은 정책) — image_sha256 만.
create table if not exists public.fm_trace_findings (
  id                     uuid primary key default gen_random_uuid(),
  source                 text not null check (source in ('patrol', 'model_report')),
  platform               text not null check (platform in ('naver', 'zigzag', 'report')),
  dedupe_key             text,
  -- 발견 위치(외부). 네이버 행은 external_purge_at 이 지나면 넷 다 null 이 된다.
  product_url            text,
  image_url              text,
  product_title          text,
  store_name             text,
  store_key              text,          -- sha256(platform|판매처 식별자) — known_stores 대조용
  external_purge_at      timestamptz,
  -- 매칭 대상(원장). FK 는 set null, 증빙값(model_id·seller_id)은 비정규화로 남긴다.
  target                 text check (target in ('publication', 'cut')),
  publication_id         uuid references public.fm_publication_records(id) on delete set null,
  output_record_id       uuid references public.fm_output_records(id) on delete set null,
  model_id               uuid,
  seller_id              uuid,
  method                 text check (method in ('watermark', 'phash')),
  confidence             text check (confidence in ('high', 'medium', 'low')),
  phash_distance         integer,
  dhash_distance         integer,
  candidates             jsonb not null default '[]'::jsonb,
  image_sha256           text,
  -- 모델 제보
  reporter_model_id      uuid,
  reporter_user_id       uuid,
  report_page_url        text check (report_page_url is null or char_length(report_page_url) <= 500),
  report_note            text check (report_note is null or char_length(report_note) <= 1000),
  -- 관리자 판정: new → seller_own(셀러 본인 스토어 정상 사용) | misuse(무단 사용) | dismissed(오탐)
  status                 text not null default 'new'
                           check (status in ('new', 'seller_own', 'misuse', 'dismissed')),
  first_seen_at          timestamptz not null default now(),
  last_seen_at           timestamptz not null default now(),
  seen_count             integer not null default 1 check (seen_count >= 1),
  -- 슬랙 알림 아웃박스(fm_enrollment_completed_alerts 와 같은 패턴 — at least once).
  -- skipped = 알리지 않는다(이미 아는 셀러 판매처로 자동 분류된 발견).
  alert_status           text not null default 'pending'
                           check (alert_status in ('pending', 'processing', 'sent', 'skipped')),
  alert_attempts         integer not null default 0 check (alert_attempts >= 0),
  alert_next_at          timestamptz not null default now(),
  alert_lease_token      uuid,
  alert_lease_expires_at timestamptz,
  alert_sent_at          timestamptz,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  constraint fm_trace_findings_source_platform_check check (
    (source = 'patrol' and platform in ('naver', 'zigzag') and target is not null
       and dedupe_key is not null)
    or (source = 'model_report' and platform = 'report'))
);

create unique index if not exists fm_trace_findings_dedupe_key
  on public.fm_trace_findings (dedupe_key) where dedupe_key is not null;
create index if not exists fm_trace_findings_seen_idx
  on public.fm_trace_findings (last_seen_at desc, id desc);
create index if not exists fm_trace_findings_status_idx
  on public.fm_trace_findings (status, last_seen_at desc);
create index if not exists fm_trace_findings_alert_due_idx
  on public.fm_trace_findings (alert_next_at) where alert_status = 'pending';
create index if not exists fm_trace_findings_alert_lease_idx
  on public.fm_trace_findings (alert_lease_expires_at) where alert_status = 'processing';
create index if not exists fm_trace_findings_purge_idx
  on public.fm_trace_findings (external_purge_at) where external_purge_at is not null;
create index if not exists fm_trace_findings_reporter_idx
  on public.fm_trace_findings (reporter_model_id, created_at desc)
  where reporter_model_id is not null;

drop trigger if exists fm_trace_findings_set_updated_at on public.fm_trace_findings;
create trigger fm_trace_findings_set_updated_at
  before update on public.fm_trace_findings
  for each row execute function public.set_updated_at();

-- ── fm_trace_known_stores: 셀러 본인 판매처(관리자가 한 번 확인한 것) ──
-- 관리자가 발견을 "셀러 본인 스토어의 정상 사용"으로 판정하면 (셀러, 플랫폼, 판매처 해시)를 여기에
-- 남긴다. 다음 순찰에서 같은 쌍이 나오면 seller_own 으로 자동 분류하고 알리지 않는다.
-- 판매처 원문 이름은 두지 않는다(네이버 데이터 영구 보관 금지) — 해시만.
create table if not exists public.fm_trace_known_stores (
  seller_id          uuid not null,
  platform           text not null check (platform in ('naver', 'zigzag')),
  store_key          text not null,
  source_finding_id  uuid references public.fm_trace_findings(id) on delete set null,
  created_by         uuid,
  created_at         timestamptz not null default now(),
  primary key (seller_id, platform, store_key)
);

-- ── fm_trace_patrol_runs: 하루 1회 순찰 기록 ─────────────────
-- run_date(KST 날짜)가 unique 라 여러 API 태스크가 떠 있어도 하루에 한 번만 돈다. 도중에 배포로
-- 끊기면 lease 가 만료된 뒤 다음 태스크가 이어서 다시 돈다(attempts 상한 3).
create table if not exists public.fm_trace_patrol_runs (
  id           uuid primary key default gen_random_uuid(),
  run_date     date not null unique,
  status       text not null default 'running' check (status in ('running', 'done', 'failed')),
  attempts     integer not null default 1 check (attempts >= 1),
  lease_until  timestamptz,
  stats        jsonb not null default '{}'::jsonb,
  last_error   text,
  started_at   timestamptz not null default now(),
  finished_at  timestamptz
);

-- ── fm_image_fingerprints: 쇼핑몰 썸네일 크롭 변형(kind='cut_crop') ──
-- 순찰이 받는 대표 이미지는 정사각(1:1)·3:4·4:5 로 잘려 있다. 컷 전체 지문만으로는 정사각 크롭이
-- 0/22 맞았다(2026-09-27 실측). 컷마다 크롭 다섯 개의 지문을 같이 둔다 — 구간은 폭 860 정규화
-- 좌표(region_y0·y1). 기존 제약을 **넓히기만** 한다(기존 행은 전부 그대로 통과한다).
alter table public.fm_image_fingerprints
  drop constraint if exists fm_image_fingerprints_kind_check;
alter table public.fm_image_fingerprints
  add constraint fm_image_fingerprints_kind_check
  check (kind in ('publication', 'strip', 'cut', 'cut_crop'));
alter table public.fm_image_fingerprints
  drop constraint if exists fm_image_fingerprints_parent_check;
alter table public.fm_image_fingerprints
  add constraint fm_image_fingerprints_parent_check check (
    (kind in ('cut', 'cut_crop') and output_record_id is not null and publication_id is null)
    or (kind not in ('cut', 'cut_crop') and publication_id is not null and output_record_id is null));
alter table public.fm_image_fingerprints
  drop constraint if exists fm_image_fingerprints_cut_crop_region_check;
alter table public.fm_image_fingerprints
  add constraint fm_image_fingerprints_cut_crop_region_check
  check (kind <> 'cut_crop' or region_y0 is not null);
create unique index if not exists fm_image_fingerprints_cut_crop_key
  on public.fm_image_fingerprints (output_record_id, region_y0, region_y1)
  where kind = 'cut_crop';

-- 운영 내부 데이터 — 정책 없음 = service-role 전용(셀러·모델에게 직접 노출하지 않는다).
alter table public.fm_trace_findings enable row level security;
alter table public.fm_trace_known_stores enable row level security;
alter table public.fm_trace_patrol_runs enable row level security;
revoke all on public.fm_trace_findings from anon, authenticated;
revoke all on public.fm_trace_known_stores from anon, authenticated;
revoke all on public.fm_trace_patrol_runs from anon, authenticated;

comment on table public.fm_trace_findings is
  'FaceMarket 자동 출처 추적 발견 원장(순찰·모델 제보). 네이버 원문 필드는 external_purge_at 뒤 삭제(검색 API 특약 21일).';
comment on table public.fm_trace_known_stores is
  '관리자가 셀러 본인 판매처로 확인한 (셀러, 플랫폼, 판매처 해시). 순찰 자동 분류용.';
comment on table public.fm_trace_patrol_runs is
  '하루 1회 출처 순찰 실행 기록(KST 날짜당 1행).';
