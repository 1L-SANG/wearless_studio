-- =============================================================
-- 20260613041903_profiles_role_and_signup_trigger.sql
-- profiles.role(admin|user|null) 추가 + 회원가입 시 본인 행 자동 프로비저닝
-- 참조: https://supabase.com/docs/guides/auth/managing-user-data
-- 설계 변형: 문서의 authenticated GRANT는 생략 — 쓰기는 service-role(FastAPI)만 (§9).
--           트리거는 security definer라 RLS/GRANT 없이도 행 생성 가능.
-- =============================================================

-- ---------- profiles.role (admin | user | null) ----------
-- null 허용(역할 미지정), 기본 'user', admin은 운영자가 수동 승격.
alter table public.profiles
  add column role text default 'user' check (role in ('admin', 'user'));

-- ---------- 회원가입 자동 프로비저닝 (auth.users INSERT 트리거) ----------
-- 문서 §4 경고: 트리거가 실패하면 회원가입 자체가 막힘 → on conflict 가드로 멱등 처리.
create function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = ''
as $$
begin
  insert into public.profiles (user_id, display_name, role)
  values (
    new.id,
    coalesce(
      new.raw_user_meta_data ->> 'display_name',
      new.raw_user_meta_data ->> 'full_name',
      new.raw_user_meta_data ->> 'name'
    ),
    'user'
  )
  on conflict (user_id) do nothing;

  -- reserve-then-confirm(§6)이 전제하는 잔액 0짜리 빈 계정 — 첫 job "계정 없음" 방지.
  insert into public.credit_accounts (user_id)
  values (new.id)
  on conflict (user_id) do nothing;

  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
