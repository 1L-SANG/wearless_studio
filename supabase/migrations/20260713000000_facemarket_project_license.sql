-- =============================================================
-- 20260713000000_facemarket_project_license.sql  (FM-30 verify-before-use 배선)
-- 셀러 상세페이지 프로젝트가 소비한 얼굴 라이선스 포인터.
--
-- 이 단일 컬럼이 이미 배선된 워커 정산 훅(detail_page_job.py:256-280)을 활성화한다:
--   · verify 게이트(routes.py generate_detail_page)가 통과 시 이 값을 채우고
--   · 워커가 성공 종결 시 이 값을 읽어 70/20/10 온체인 정산을 기록한다.
-- on delete set null — 라이선스가 삭제돼도 프로젝트는 남는다(정산 미러는 별도 보존).
-- 멱등(add column if not exists) — 재실행 안전.
-- =============================================================

alter table public.projects
  add column if not exists facemarket_license_id uuid
  references public.fm_licenses(id) on delete set null;
