-- =============================================================
-- 20260615202954_jobs_status_drop_cancelled.sql
-- jobs.status CHECK를 init.sql 현행 정의에 맞춰 'cancelled' 제거 (error로 통일).
--
-- 배경: init.sql의 in-place 'cancelled' 제거(docs 커밋 1e74490)는 이미 원격에
-- 마이그레이션이 적용된 뒤였다 → 이력 테이블은 "최신"이라 db push가 재적용하지
-- 않아 파일과 prod DB가 드리프트. 이 포워드 마이그레이션으로 정렬한다.
-- (TODO.md §1: status='cancelled' 설정 코드 경로 0건 검증됨, prod 해당 행 0개)
--
-- 멱등성: 새 DB(init.sql에서 이미 cancelled 없음)에서는 동일 제약 재적용 = 무영향.
--         prod에서는 cancelled 제거.
-- =============================================================

alter table public.jobs drop constraint jobs_status_check;
alter table public.jobs add constraint jobs_status_check
  check (status in ('pending', 'running', 'done', 'error'));
