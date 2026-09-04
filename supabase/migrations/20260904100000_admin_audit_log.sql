-- =============================================================
-- 20260904100000_admin_audit_log.sql
-- 관리자 행위 감사 원장 + 대시보드 집계 인덱스
-- 설계: docs/superpowers/specs/2026-09-04-facemarket-admin-console-design.md §4.3·§5.3
--
-- 원장은 행위자보다 오래 산다 — actor 는 on delete set null 이다. 관리자 계정을 지우면
-- 누가 했는지는 잃어도 무엇이 일어났는지는 남아야 한다.
-- before/after 에는 상태 전이·식별자만 넣는다. 지원자 실명·생년월일·사진 키는 넣지 않는다
-- (그 값들은 30일 PII 스윕 대상이고, 원장에 복사하면 스윕을 우회한다).
-- =============================================================

create table if not exists public.admin_audit_log (
  id            uuid primary key default gen_random_uuid(),
  actor_user_id uuid references auth.users(id) on delete set null,
  action        text not null,
  target_type   text not null,
  target_id     text,
  before        jsonb not null default '{}'::jsonb,
  after         jsonb not null default '{}'::jsonb,
  note          text,
  created_at    timestamptz not null default now()
);

create index if not exists admin_audit_log_created_idx
  on public.admin_audit_log (created_at desc);
create index if not exists admin_audit_log_target_idx
  on public.admin_audit_log (target_type, target_id, created_at desc);

-- ---------- 대시보드 집계 인덱스 ----------
-- 전부 count/sum 이라 필터 컬럼만 있으면 된다. 지금 규모에서는 없어도 도는데,
-- 행이 늘었을 때 대시보드 한 번이 테이블 5개를 순차 스캔하는 걸 막는다.
create index if not exists fm_model_applications_status_idx
  on public.fm_model_applications (status);
create index if not exists fm_model_applications_created_idx
  on public.fm_model_applications (created_at);
create index if not exists fm_licenses_created_idx
  on public.fm_licenses (created_at);
create index if not exists fm_settlements_chain_created_idx
  on public.fm_settlements (chain_status, created_at);
create index if not exists payment_history_status_created_idx
  on public.payment_history (status, created_at);
