-- =============================================================
-- FaceMarket 추적 층(2026-09-26, 오너 결정) — 배포본 워터마크 코드 + 지각 해시 지문.
--
-- 쇼핑몰에서 실존 모델 얼굴 이미지를 발견했을 때, 셀러에게 스토어 주소 등록을 요구하지 않고도
-- "어느 배포본 → 셀러·모델·라이선스"를 되짚기 위한 두 장치:
--   ① 배포본 픽셀에 박는 32비트 워터마크 코드(fm_publication_records.wm_code)
--   ② 워터마크가 지워졌을 때를 위한 pHash/dHash 지문(fm_image_fingerprints)
-- 관리자 추적 도구(POST /v1/facemarket/admin/trace)가 둘을 읽는다.
--
-- 추가만 한다(additive). 기존 행·기존 제약은 건드리지 않는다. 이 마이그레이션 이전 배포본은
-- wm_* 가 null 이고, 앵커 워커는 coalesce(wm_sha256, image_sha256) 로 예전 그대로 동작한다.
-- =============================================================

-- ── fm_publication_records: 워터마크 3열 ─────────────────────
-- wm_code   : 배포본 픽셀에 박은 코드(1 ≤ code < 2^32). uuid 가 아니라 짧은 난수 — 픽셀에 실을 수
--             있는 비트가 48비트(코드 32 + CRC 16)뿐이다. unique 로 충돌을 막고 서버가 재발급한다.
-- wm_status : embedded | failed | skipped. 실패해도 다운로드는 나간다(원본 바이트) — 기록만 남긴다.
-- wm_sha256 : 워터마크본(C2PA 서명 전) 해시. 앵커가 체인에 올리는 imageHash 가 이 값이다.
--             image_sha256 은 업로드 원본 해시로 남아 (seller_id, image_sha256) 멱등 키를 지킨다.
alter table public.fm_publication_records
  add column if not exists wm_code bigint,
  add column if not exists wm_status text,
  add column if not exists wm_sha256 text;

alter table public.fm_publication_records
  drop constraint if exists fm_publication_records_wm_code_range;
alter table public.fm_publication_records
  add constraint fm_publication_records_wm_code_range
  check (wm_code is null or (wm_code >= 1 and wm_code <= 4294967295));

alter table public.fm_publication_records
  drop constraint if exists fm_publication_records_wm_status_check;
alter table public.fm_publication_records
  add constraint fm_publication_records_wm_status_check
  check (wm_status is null or wm_status in ('embedded', 'failed', 'skipped'));

create unique index if not exists fm_publication_records_wm_code_key
  on public.fm_publication_records (wm_code) where wm_code is not null;

-- ── fm_image_fingerprints: 지각 해시 지문 ────────────────────
-- kind='publication' : 배포본 전체(ZIP 이면 블록마다 구간 달린 행도)
-- kind='strip'       : 배포본의 세로 띠(폭 860 정규화 기준 높이 700, 100 간격)
-- kind='cut'         : REAL 컷 1장(fm_output_records 1행 = 지문 1행)
-- phash·dhash        : 64비트 해시를 부호 있는 bigint 로(imagehash 4.3.x 와 같은 비트열).
-- region_y0·y1       : 폭 860 정규화 좌표의 세로 구간. 전체 이미지 행은 null.
--
-- 부모가 지워지면 지문도 같이 지운다(cascade) — 두 원장은 삭제하지 않는 테이블이라(설계 §9)
-- 실제로는 거의 일어나지 않는다. 지문은 64비트 요약값이라 얼굴을 복원할 수 없는 파일 지문이다
-- (생체정보 아님) — 모델 철회 뒤에도 남겨야 철회 후 무단 사용을 추적할 수 있다.
create table if not exists public.fm_image_fingerprints (
  id               uuid primary key default gen_random_uuid(),
  publication_id   uuid references public.fm_publication_records(id) on delete cascade,
  output_record_id uuid references public.fm_output_records(id) on delete cascade,
  kind             text not null check (kind in ('publication', 'strip', 'cut')),
  region_y0        integer,
  region_y1        integer,
  phash            bigint not null,
  dhash            bigint not null,
  created_at       timestamptz not null default now(),
  constraint fm_image_fingerprints_parent_check check (
    (kind = 'cut' and output_record_id is not null and publication_id is null)
    or (kind <> 'cut' and publication_id is not null and output_record_id is null)),
  constraint fm_image_fingerprints_region_check check (
    (region_y0 is null and region_y1 is null)
    or (region_y0 >= 0 and region_y1 > region_y0)),
  constraint fm_image_fingerprints_strip_region_check check (
    kind <> 'strip' or region_y0 is not null)
);

-- 멱등 키 — 서명 재시도·백필이 같은 지문을 두 번 넣지 않게(on conflict do nothing).
create unique index if not exists fm_image_fingerprints_cut_key
  on public.fm_image_fingerprints (output_record_id) where kind = 'cut';
create unique index if not exists fm_image_fingerprints_publication_key
  on public.fm_image_fingerprints (publication_id, kind, coalesce(region_y0, -1))
  where publication_id is not null;
create index if not exists fm_image_fingerprints_kind_idx
  on public.fm_image_fingerprints (kind);

-- 운영 내부 데이터 — 정책 없음 = service-role 전용(셀러·모델에게 노출하지 않는다).
alter table public.fm_image_fingerprints enable row level security;

comment on column public.fm_publication_records.wm_code is
  '배포본 픽셀에 박은 32비트 워터마크 코드. 관리자 추적 도구가 발견 이미지에서 읽어 이 행을 찾는다.';
comment on column public.fm_publication_records.wm_sha256 is
  '워터마크본(C2PA 서명 전) sha256. 앵커 imageHash = coalesce(wm_sha256, image_sha256).';
comment on table public.fm_image_fingerprints is
  'FaceMarket 추적 층 지문(pHash/dHash). 워터마크가 지워진 발견 이미지를 배포본·컷에 잇는다. '
  '좌표는 폭 860 정규화 기준. 관리자 추적 전용 — RLS 정책 없음(service-role).';
