-- =============================================================
-- 20260809000000_jobs_status_allow_cancelled.sql
-- 마네킹 생성의 협조적 취소를 위해 jobs.status에 cancelled를 정식 재도입한다.
--
-- 머지 전 적용 필수: 서버가 먼저 배포되어 cancelled를 쓰면 기존 CHECK 위반으로 500이 난다.
-- 적용은 오너가 Supabase SQL Editor에서 직접 수행한다.
--
-- 멱등성: 기존 제약을 이름으로 DROP IF EXISTS 한 뒤 현행 정의를 다시 만든다.
-- =============================================================

alter table public.jobs drop constraint if exists jobs_status_check;
alter table public.jobs add constraint jobs_status_check
  check (status in ('pending', 'running', 'done', 'error', 'cancelled'));
