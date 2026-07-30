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
  'AG-P2 4축 판정 스냅샷(서버 소유, 클라 표시 전용). 키: product_fidelity·physical_naturalness·'
  'image_quality·series_consistency (각 0-100 정수 또는 null=신호 없음), '
  'series_inconsistencies text[](선택), critical_errors text[](출고 불가 결함 — 점수 무관 재생성), '
  'outcome auto_pass|needs_review|regenerate (4축 최저값 + 치명오류 기반 등급), '
  'salvaged bool(예산 소진으로 재생성 못 하고 내보낸 컷), '
  'thresholds {auto_pass, review}(판정 시점 임계 — 임계를 바꿔도 과거 판정은 재계산되지 '
  '않으므로 이 값 없이 재계산하면 불일치가 버그처럼 보인다). '
  '컬럼 자체가 null = 판정 없음(QC off·판정 실패·이 마이그레이션 이전 행). '
  'API 노출 경로: routes._cut_to_api → models.MannequinCut.qc_scores → qcScores(camel).';
