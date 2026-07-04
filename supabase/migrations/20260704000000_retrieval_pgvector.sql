-- 검색 증강(RAG) 파운데이션 AI 업그레이드 — Phase 0 스키마.
-- 문서: documents/retrieval_upgrade_prd.md §5.3(데이터 모델) · §5.1 F2~F4(검토 반영) · NFR-3(보안).
-- 신규 테이블 4종 + pgvector 확장만 추가. 기존 스키마(matching_items 등) 무변경.
-- append-only: 신규 forward 파일 (init.sql 수정 금지).

create extension if not exists vector;

-- ---------- kb_chunks (FR-B — 프롬프트 지식 청크, 운영자 큐레이션) ----------
-- v1(정적 선택)은 keys만 사용, v2(벡터 챌린저)가 이길 때만 text_embedding 사용 (PRD §5.1 F5).
create table public.kb_chunks (
  id             text primary key,
  kind           text not null,           -- 'styling' | 'composition' | 'brand' | ...
  keys           jsonb not null,          -- 카테고리·styleTags 매칭 키 (v1 정적 선택용)
  body_en        text not null,           -- canonical 영문 본문 (주입용)
  version        integer not null default 1,
  text_embedding vector(1536),            -- v2 챌린저용 (nullable — v1은 keys만 사용)
  is_active      boolean not null default true,
  updated_at     timestamptz not null default now()
);

-- ---------- ref_images (FR-C — 레퍼런스 이미지, 운영자 시드 → 추후 성공 생성물) ----------
-- image_embedding 차원은 Vertex multimodal 1408 확정치(PRD §5.3 주석 — R3 인증 전 폴백 시 재조정 가능).
create table public.ref_images (
  id              text primary key,
  r2_key          text not null,
  cut_type        text not null,          -- 'styling' | 'horizon' | 'product'
  mood_tags       jsonb not null default '[]',
  image_embedding vector(1408),           -- Vertex multimodal 차원(확정 시 조정)
  source          text not null default 'seed',  -- 'seed' | 'generated'
  is_active       boolean not null default true,
  created_at      timestamptz not null default now()
);

-- ---------- matching_embeddings (F3 — 매칭 임베딩 사이드카) ----------
-- matching_items 본체·seed_matching.py는 이 테이블과 무관하게 Vertex 무의존 유지 (PRD §5.1 F3).
create table public.matching_embeddings (
  item_id     text not null references public.matching_items (id) on delete cascade,
  kind        text not null,              -- 'image' | 'style'
  embedding   vector not null,
  model       text not null,
  embedded_at timestamptz not null default now(),
  primary key (item_id, kind)
);

-- ---------- style_affinity (FR-A1 v1 — 태그 친화도, 운영자 큐레이션 정적 맵) ----------
-- 결정적 baseline. 닫힌 카탈로그(styleTags 20~30개) 기준 소규모 테이블 — 벡터 불필요.
create table public.style_affinity (
  tag_a text not null,
  tag_b text not null,
  score real not null,                    -- 0..1
  primary key (tag_a, tag_b)
);

-- =============================================================
-- RLS (NFR-3): 4개 테이블 전부 활성화. 쓰기·조회 정책 전무 = service-role 전용.
-- kb_chunks·ref_images·matching_embeddings·style_affinity는 서버사이드 검색·배치 임베딩
-- 스크립트에서만 쓰인다. 코퍼스는 운영자 큐레이션만이므로 클라이언트(authenticated) select는
-- 기본 미허용 — 개방이 필요해지면 그때 명시적 정책을 추가한다(임의 확장 금지).
-- =============================================================
alter table public.kb_chunks enable row level security;
alter table public.ref_images enable row level security;
alter table public.matching_embeddings enable row level security;
alter table public.style_affinity enable row level security;

-- 인덱스: 없음(exact scan). n<1만에선 exact scan이 ivfflat/hnsw보다 빠르고 recall 100%
-- (PRD §2.2 비목표, §5.1 F2). ref_images가 ~1만 행을 넘어설 때만
-- `create index ... using hnsw (image_embedding vector_cosine_ops)` 도입을 재검토한다(FR-C5).
