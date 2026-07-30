-- 마네킹 컷 QC 4축 점수 영속 (플랜 Phase 2 — 2026-07-31).
--
-- 지금까지 QC 판정은 job_events 에만 남았다. 프론트는 SSE 를 안 쓰고 jobs.result 봉투와
-- GET /mannequins 재조회만 읽으므로(httpAdapter pollJob), 이벤트에만 있는 값은 셀러·검수
-- 화면에 영원히 도달하지 못한다. 특히 재생성 경로는 result 를 버리고 GET /mannequins 로
-- 스트립을 다시 채우기 때문에, 컬럼 없이는 "생성 직후엔 점수가 보이다 재생성하면 사라지는"
-- 비대칭이 남는다.
--
-- append-only(init.sql 무수정). nullable — 기존 행과 QC off 경로는 null 그대로다.

alter table public.mannequin_cuts
  add column if not exists qc_scores jsonb;

comment on column public.mannequin_cuts.qc_scores is
  'AG-P2 4축 점수 스냅샷: {product_fidelity, physical_naturalness, image_quality, '
  'series_consistency, critical_errors[], outcome}. null = 판정 없음(QC off·shadow 실패·구 행).';
