-- =============================================================
-- 20260911150000_admin_devices.sql
-- 관리자 콘솔 기기 게이트 — 승인된 기기 목록
-- 설계: docs/superpowers/specs/2026-09-11-admin-device-gate-design.md §4
--
-- 기기 = 브라우저가 localStorage 에 쥔 랜덤 토큰. 여기엔 sha256 만 둔다(원문은 어디에도
-- 저장하지 않는다 — DB 가 새도 토큰을 재구성할 수 없게).
-- revoked 는 종점이다. 다시 쓰려면 새로 등록한다(부활 경로가 있으면 회수의 의미가 흐려진다).
-- 계정이 지워지면(30일 PII 스윕 등) 기기도 같이 간다. 누가 언제 승인·회수했는지는
-- admin_audit_log 에 남으므로 여기서는 잃어도 된다.
-- =============================================================

create table if not exists public.admin_devices (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  token_hash    text not null unique,
  label         text not null,
  status        text not null default 'pending'
                check (status in ('pending', 'approved', 'revoked')),
  user_agent    text,
  created_at    timestamptz not null default now(),
  last_seen_at  timestamptz,
  approved_by   uuid references auth.users(id) on delete set null,
  approved_at   timestamptz,
  revoked_by    uuid references auth.users(id) on delete set null,
  revoked_at    timestamptz
);

create index if not exists admin_devices_user_status_idx
  on public.admin_devices (user_id, status);
create index if not exists admin_devices_status_created_idx
  on public.admin_devices (status, created_at desc);

-- 정책 없이 RLS 만 켠다 = anon/PostgREST 경로 차단. 서버는 직결 DB 롤이라 영향 없다.
alter table public.admin_devices enable row level security;
