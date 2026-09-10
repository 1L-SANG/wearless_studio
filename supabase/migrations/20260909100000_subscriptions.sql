-- 토스 자동결제(빌링) 구독 — 계획서 docs/plans/2026-09-09-toss-billing-subscription.md
-- 왜 toss_payment_orders 를 재사용하지 않는가: 그 테이블은 리다이렉트 기반 1회 결제의
-- 주문 인텐트다(결제창이 만든 paymentKey 를 사용자가 들고 돌아온다). 빌링은 서버가 직접
-- 승인하고 주기마다 반복되며 유예·재시도 상태를 갖는다 — 상태기계가 다르다.
create extension if not exists pgcrypto;

-- pgcrypto 가 어느 스키마에 깔렸든 동작하게 감싼다.
-- Supabase 는 확장을 `extensions` 스키마에 설치하는데, 앱 커넥션(db.py _configure)은
-- search_path 를 건드리지 않는다. 그래서 SQL 에 pgp_sym_encrypt 를 그대로 쓰면 역할의
-- 기본 search_path 에 extensions 가 없는 순간 `function ... does not exist` 로 죽는다 —
-- 로컬(공용 스키마 설치)에서는 멀쩡하고 prod 에서만 터지는 종류의 사고다.
-- 함수에 search_path 를 박아 두면 호출 시점 세션 설정과 무관하게 해석된다.
create or replace function public.wl_billing_encrypt(p_value text, p_kek text)
returns bytea
language sql
immutable
set search_path = public, extensions
as $$ select pgp_sym_encrypt(p_value, p_kek) $$;

create or replace function public.wl_billing_decrypt(p_value bytea, p_kek text)
returns text
language sql
immutable
set search_path = public, extensions
as $$ select pgp_sym_decrypt(p_value, p_kek)::text $$;

-- 빌링키 복호화는 서버(service_role)만 한다. anon/authenticated 가 이 함수를 부를 수
-- 있으면 RLS 로 테이블을 막아 둔 의미가 없다.
revoke all on function public.wl_billing_encrypt(text, text) from public, anon, authenticated;
revoke all on function public.wl_billing_decrypt(bytea, text) from public, anon, authenticated;

create table if not exists public.subscriptions (
  id uuid primary key default gen_random_uuid(),
  -- 1인 1구독. 등급 변경은 이 행을 갱신한다(새 행을 만들지 않는다).
  user_id uuid not null unique references auth.users (id) on delete cascade,
  plan_code text not null,                       -- pricing_plans.code (kind='subscription')
  status text not null default 'active'
    check (status in ('active', 'past_due', 'canceled', 'ended')),
  -- 토스 빌링키. 평문 저장 금지 — customerKey(=user_id)는 사실상 공개값이라
  -- 빌링키만 새면 무단 결제가 가능하다. KEK 는 앱 env(SSM)에만 있다.
  billing_key_enc bytea not null,
  card_brand text,                               -- 표시 전용(카드사명)
  card_last4 text,                               -- 표시 전용(마지막 4자리)
  current_period_start timestamptz not null default now(),
  current_period_end timestamptz not null,
  next_billing_at timestamptz,                   -- null = 청구 대상 아님(canceled/ended)
  scheduled_plan_code text,                      -- 다운그레이드 예약(다음 갱신에 적용)
  fail_count integer not null default 0 check (fail_count >= 0),
  grace_until timestamptz,                       -- past_due 유예 종료(실패시각 + 3일)
  last_failure_code text,
  last_failure_message text,
  billing_key_invalid boolean not null default false,   -- BILLING_DELETED 웹훅 힌트
  canceled_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- 종료 상태는 청구 대상이 아니다(스케줄러가 집지 않게 DB 가 강제한다)
  check ((status in ('canceled', 'ended') and next_billing_at is null)
      or (status in ('active', 'past_due') and next_billing_at is not null)),
  check (current_period_end > current_period_start)
);

-- 스케줄러의 유일한 스캔 축. 부분 인덱스로 종료 구독을 아예 제외한다.
create index if not exists subscriptions_due_idx
  on public.subscriptions (next_billing_at)
  where status in ('active', 'past_due');
create index if not exists subscriptions_period_end_idx
  on public.subscriptions (current_period_end)
  where status in ('canceled', 'past_due');

create table if not exists public.subscription_invoices (
  id uuid primary key default gen_random_uuid(),
  subscription_id uuid not null references public.subscriptions (id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  -- 토스 계약: 영문 대소문자·숫자·'-','_','=' 6~64자. Idempotency-Key 로도 쓴다.
  order_id text not null unique check (char_length(order_id) between 6 and 64),
  kind text not null check (kind in ('initial', 'renewal', 'upgrade_proration')),
  plan_code text not null,
  amount integer not null check (amount >= 0),
  credits integer not null check (credits >= 0),
  period_start timestamptz not null,
  period_end timestamptz not null,
  status text not null default 'pending' check (status in ('pending', 'paid', 'failed')),
  payment_key text,
  attempt integer not null default 1 check (attempt >= 1),
  fail_code text,
  fail_message text,
  approved_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
-- 같은 paymentKey 가 두 청구서에 붙지 않게(부분 유니크 — null 다수 허용)
create unique index if not exists subscription_invoices_payment_key_idx
  on public.subscription_invoices (payment_key) where payment_key is not null;
-- 한 구독의 같은 주기 갱신 청구서는 하나뿐이다. 스케줄러가 두 번 돌아도 두 번 청구하지
-- 않는 최후 방어선(advisory lock 이 뚫려도 DB 가 막는다). 업그레이드 비례분은 같은 주기에
-- 여러 번 있을 수 있으므로 renewal/initial 만 건다.
create unique index if not exists subscription_invoices_period_idx
  on public.subscription_invoices (subscription_id, period_start)
  where kind in ('initial', 'renewal');
create index if not exists subscription_invoices_user_idx
  on public.subscription_invoices (user_id, created_at desc);

-- RLS 활성 + 정책 없음 = service_role 만. 빌링키·금액 스냅샷을 클라이언트가 못 읽는다.
alter table public.subscriptions enable row level security;
alter table public.subscription_invoices enable row level security;

create trigger subscriptions_updated_at before update on public.subscriptions
  for each row execute function public.set_updated_at();
create trigger subscription_invoices_updated_at before update on public.subscription_invoices
  for each row execute function public.set_updated_at();
