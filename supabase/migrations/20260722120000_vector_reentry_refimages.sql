-- 검색 증강 Phase 3 재진입 — pgvector + ref_images (ADR D2 보류 해제, 2026-07-22 사용자 결정).
-- 문서: documents/retrieval_upgrade_prd.md §5.3(원설계 데이터 모델) · §6 Phase 3 · §1.3-2("벡터가
-- 무조건 정당한 곳 = 레퍼런스 이미지 검색"). 짝 플랜: .omc/plans/ws1-t1r-rag-refimages-consistency.md.
--
-- 목적: 유사한 '성공 스튜디오 컷/레퍼런스'를 검색해 마네킹 생성 시 STYLE REFERENCE 로 첨부 →
-- 컷 간 톤·조명·마감 일관성 향상. 임베딩은 자체 호스팅 로컬 모델(SigLIP, ADR D2 v1.3),
-- 요청 경로 아닌 오프라인 배치(scripts/embed_corpus.py)로 사전 적재(FR-C2).
--
-- append-only(init.sql 무수정). 기존 스키마 무변경. 벡터 컬럼 차원은 config embed_*_dim 과 일치해야 함
-- (모델 교체 시 별도 forward 마이그레이션 — PRD §9-1 "차원 확정 시 조정").

create extension if not exists vector;

-- ---------- ref_images (FR-C — 레퍼런스 컷 코퍼스, 운영자 시드 → 성공 생성물) ----------
-- 결정적 프리필터(cut_type·clothing_type·gender·is_active)로 풀을 좁힌 뒤 그 안에서만 벡터 랭킹
-- (FR-A2 원칙 승계: 프리필터 불변, 랭킹은 통과 풀 내부에서만). 인덱스 없음 = exact scan
-- (~1만 행까지 충분, F2). 초과 시 hnsw (image_embedding vector_cosine_ops) 추가.
create table public.ref_images (
  id              text primary key,
  r2_bucket       text not null default 'wearless',
  r2_key          text not null,
  cut_type        text not null,                      -- 'mannequin' | 'styling' | 'product' | 'hero'
  mood_tags       jsonb not null default '[]'::jsonb, -- 무드/톤 태그 (관측·보조 필터)
  clothing_type   text,                               -- 결정적 프리필터 키 (top/bottom/outer/dress…)
  gender          text,                               -- 결정적 프리필터 키 (women/men/unisex)
  image_embedding vector(768),                        -- SigLIP base patch16-224 = 768. config embed_image_dim 와 일치
  embed_model     text,                               -- 임베딩 모델 id (재임베딩·차원 불일치 감지)
  source          text not null default 'seed' check (source in ('seed', 'generated')),
  project_id      uuid references public.projects (id) on delete set null,  -- generated 출처 추적(선택)
  is_active       boolean not null default true,
  created_at      timestamptz not null default now()
);
-- 프리필터 컬럼 exact scan 보조(소규모라 필수는 아님, 문서적 의도 표시)
create index ref_images_prefilter_idx on public.ref_images (cut_type, clothing_type, is_active);

-- ---------- kb_chunks.text_embedding (2b 챌린저 대비 — nullable, v1 정적 선택은 미사용) ----------
-- 지금은 스키마만. RETRIEVAL_KNOWLEDGE=vector 챌린저(스트레치)가 켜질 때 채운다.
alter table public.kb_chunks add column if not exists text_embedding vector(1024);  -- bge-m3/Qwen3-Embedding = 1024

-- =============================================================
-- RLS (NFR-3): ref_images 활성화, 정책 전무 = service-role 전용.
-- 코퍼스는 운영자 큐레이션·서버사이드 검색 전용. 클라(authenticated) 접근 기본 미허용
-- (kb_chunks·style_affinity 와 동일 패턴). 서버는 DATABASE_URL(superuser)로 RLS 우회.
-- =============================================================
alter table public.ref_images enable row level security;
