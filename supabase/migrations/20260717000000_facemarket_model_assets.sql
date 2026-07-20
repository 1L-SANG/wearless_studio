-- FaceMarket 실존 모델 아이덴티티 자산 (handoff: 가상모델 fork).
-- 얼굴 사진 3장 → 2×2 그리드 합성 자산을 비공개 버킷에 저장하고 fm_models 에 등록한다.
-- 얼굴 = 생체 PII → r2_face 비공개 버킷 전용, 공개 도메인 미연결. API 응답에 키 미노출.

-- ① fm_model_assets: modelId → view별 비공개 R2 키 장부.
create table if not exists public.fm_model_assets (
  model_id   uuid not null references public.fm_models(id) on delete cascade,
  view       text not null check (view in ('face_front', 'grid_sedcard')),
  r2_key     text not null,          -- 비공개 버킷 키. 어떤 API 응답에도 미노출.
  mime       text not null,
  bucket     text not null default 'face' check (bucket in ('face', 'public')),
  created_at timestamptz not null default now(),
  primary key (model_id, view)
);

-- RLS 활성 + 정책 없음 = service_role(RLS 우회)만 접근. anon/authenticated 직접 차단.
-- 생체 파생 자산이라 셀러/모델 본인도 공유 테이블 경유로 못 읽고 인증 게이트 라우트만 서빙.
alter table public.fm_model_assets enable row level security;

-- ② fm_models 확장: 자산 빌드 상태·QC 점수·소스 지문.
alter table public.fm_models
  add column if not exists assets_status text not null default 'none'
    check (assets_status in ('none', 'building', 'ready', 'failed')),
  add column if not exists qc_score numeric(4, 3),          -- 최소 pairwise 코사인. 민감 → 집계만 노출.
  add column if not exists assets_source_hash text;         -- 소스 3장 지문 → 각도 변경 시 재빌드 감지.

-- ③ jobs.kind CHECK 확장 (현행: analyze/.../personalization_purge 에 fm_model_asset_build 추가).
--    개인화 선례(20260715)와 동일하게 drop 후 재정의.
alter table public.jobs drop constraint if exists jobs_kind_check;
alter table public.jobs add constraint jobs_kind_check
  check (kind in ('analyze', 'mannequin', 'mannequin_adjust', 'detail_page', 'editor_image',
                  'personalization_generation', 'personalization_purge', 'fm_model_asset_build'));

-- ④ 동시 빌드 1개(멱등) — 같은 modelId 로 pending/running 자산 빌드 잡은 하나만.
--    project 기반 jobs_active_unique_idx 는 project_id null 이라 안 걸리므로 modelId 전용 인덱스.
create unique index if not exists fm_model_asset_build_singleflight
  on public.jobs ((payload->>'modelId'))
  where kind = 'fm_model_asset_build' and status in ('pending', 'running');
