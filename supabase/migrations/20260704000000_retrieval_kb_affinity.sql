-- 검색 증강(RAG) 파운데이션 AI 업그레이드 — Phase 0 결정적 스키마.
-- 문서: documents/retrieval_upgrade_prd.md §5.3(데이터 모델, 임베딩 0 축소) · NFR-3(보안).
-- 결정적 테이블 2종만 추가(kb_chunks·style_affinity). 벡터/임베딩(pgvector 확장,
-- text/image_embedding 컬럼, ref_images·matching_embeddings 테이블)은 전면 보류(ADR D2) —
-- 이미지 유사도가 필요하다고 판명될 때 별도 forward 마이그레이션으로 재진입.
-- 기존 스키마(matching_items 등) 무변경. append-only(init.sql 수정 금지).

-- ---------- kb_chunks (FR-B/2a — 프롬프트 지식 청크, 운영자 큐레이션) ----------
-- 정적 선택은 keys(카테고리·styleTags)만 사용. 벡터 컬럼 없음(결정적).
create table public.kb_chunks (
  id         text primary key,
  kind       text not null,           -- 'styling' | 'composition' | 'brand' | ...
  keys       jsonb not null,          -- 카테고리·styleTags 매칭 키 (정적 선택용)
  body_en    text not null,           -- canonical 영문 본문 (주입용)
  version    integer not null default 1,
  is_active  boolean not null default true,
  updated_at timestamptz not null default now()
);

-- ---------- style_affinity (FR-A1 — 태그 친화도, 운영자 큐레이션 정적 맵) ----------
-- 결정적 baseline. 닫힌 카탈로그(styleTags 20~30개) 기준 소규모 테이블 — 벡터 불필요.
create table public.style_affinity (
  tag_a text not null,
  tag_b text not null,
  score real not null,                -- 0..1
  primary key (tag_a, tag_b)
);

-- =============================================================
-- RLS (NFR-3): 두 테이블 활성화, 정책 전무 = service-role 전용.
-- 서버사이드 검색·배치 스크립트에서만 쓰인다. 코퍼스는 운영자 큐레이션만이므로
-- 클라이언트(authenticated) select는 기본 미허용 — 개방이 필요해지면 그때 명시적
-- 정책을 추가한다(임의 확장 금지).
-- =============================================================
alter table public.kb_chunks enable row level security;
alter table public.style_affinity enable row level security;

-- 인덱스: 없음(exact scan). 소규모 결정적 테이블 — ANN 불필요.
