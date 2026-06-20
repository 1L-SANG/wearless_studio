-- =============================================================
-- 20260620100000_credit_subscription_system.sql
-- 크레딧 시스템 (구독제 + 추가구매) — documents/credit_system_design.md
-- 원칙(init.sql 동일): text+CHECK enum · user_id → auth.users.id 직접 참조 ·
--   쓰기는 service-role(FastAPI)만, RLS는 owner SELECT 2차 방어선 ·
--   기존 credit_accounts(balance/reserved)·credit_ledger(append-only) 재사용.
-- 단가(op별 cost)·topup SKU·PG 연동은 본 마이그레이션 밖(출시 직전, §5 TBD).
-- =============================================================

-- ---------- pricing_plans (요금제/상품 카탈로그 — user_id 없음, §2.1) ----------
create table public.pricing_plans (
  id uuid primary key default gen_random_uuid(),
  code text not null unique,                                   -- 'basic'/'plus'/'seller'/topup SKU
  kind text not null check (kind in ('subscription', 'topup')),
  name text not null,
  credits integer not null check (credits > 0),
  price integer not null check (price >= 0),                   -- 원(₩)
  billing_period text not null check (billing_period in ('monthly', 'once')),
  is_active boolean not null default true,
  sort_order integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- subscription=monthly · topup=once 조합만 허용 (잘못된 카탈로그 행 차단)
  check ((kind = 'subscription' and billing_period = 'monthly')
      or (kind = 'topup' and billing_period = 'once'))
);
create trigger pricing_plans_updated_at before update on public.pricing_plans
  for each row execute function public.set_updated_at();

-- ---------- payment_history (결제 내역 — 테스트용, PG 나중, §2.3) ----------
create table public.payment_history (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  plan_id uuid references public.pricing_plans (id),
  amount integer not null check (amount >= 0),                 -- 원(₩)
  kind text not null check (kind in ('subscription', 'topup')),
  provider text not null default 'test',                       -- 나중 toss/stripe
  provider_ref text,
  status text not null default 'paid' check (status in ('paid', 'refunded')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index payment_history_user_idx on public.payment_history (user_id, created_at desc);
create trigger payment_history_updated_at before update on public.payment_history
  for each row execute function public.set_updated_at();

-- ---------- credit_sources (버킷 = 잔액의 정본, FIFO/환불 추적, §2.2) ----------
-- balance(credit_accounts) = Σ active 버킷 remaining. 소진순서: 구독 먼저 → topup FIFO.
create table public.credit_sources (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  source_type text not null check (source_type in ('subscription', 'topup')),
  plan_id uuid references public.pricing_plans (id),           -- 어떤 상품에서 왔는지
  initial_credits integer not null check (initial_credits >= 0),
  remaining_credits integer not null check (remaining_credits >= 0),
  status text not null default 'active'
    check (status in ('active', 'pending_refund', 'refunded', 'expired')),
  period_end timestamptz,                                      -- 구독 버킷 만료(topup=null)
  payment_id uuid references public.payment_history (id),       -- 결제 연결
  created_at timestamptz not null default now(),               -- FIFO 정렬 키
  updated_at timestamptz not null default now(),
  check (remaining_credits <= initial_credits),
  -- 구독 버킷은 만료일(period_end) 필수(월 1회 자동갱신, 해지 시 구독일+1달 만료) · topup은 만료 없음
  check ((source_type = 'subscription' and period_end is not null)
      or (source_type = 'topup' and period_end is null)),
  unique (id, user_id)                                         -- refund_requests 교차-유저 무결성 FK용
);
-- FIFO 소진·가용합 계산 (active 버킷을 구독먼저→오래된순으로)
create index credit_sources_fifo_idx
  on public.credit_sources (user_id, status, source_type, created_at);
create trigger credit_sources_updated_at before update on public.credit_sources
  for each row execute function public.set_updated_at();

-- ---------- refund_requests (추가구매 환불 요청 — topup 버킷만, §2.4) ----------
create table public.refund_requests (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  credit_source_id uuid not null,
  status text not null default 'pending' check (status in ('pending', 'approved', 'rejected')),
  reason text,
  created_at timestamptz not null default now(),
  resolved_at timestamptz,
  resolved_by uuid references auth.users (id),
  -- 환불요청 버킷은 반드시 같은 user 소유 — 교차-유저 환불 DB 차단 (무결성)
  foreign key (credit_source_id, user_id)
    references public.credit_sources (id, user_id) on delete cascade,
  -- 종결(approved/rejected) 요청은 resolved_at 필수
  -- (종결 비가역=approved/rejected→pending 되돌리기 차단은 앱 가드로 강제 —
  --  설계 §불변식4: approve/reject는 req.status=='pending' AND bucket=='pending_refund'일 때만)
  check (status = 'pending' or resolved_at is not null)
);
create index refund_requests_user_idx on public.refund_requests (user_id, created_at desc);
-- 버킷당 진행중(pending) 요청 1개만 — 중복 요청 차단 (§2.4)
create unique index refund_requests_pending_unique_idx
  on public.refund_requests (credit_source_id) where status = 'pending';

-- ---------- credit_ledger 확장: 버킷별 소비/충전/환불 1행씩 (§2.5) ----------
-- (ADD COLUMN은 DDL이라 credit_ledger append-only 트리거(update/delete DML)와 무관)
-- on delete restrict: ① 버킷별 감사추적 보존(§2.5) — set null이면 원장에서 출처 버킷이 지워짐
--   ② set null cascade-UPDATE가 row-level append-only 트리거(20260616105745)에 막히는 함정 회피.
--   버킷은 하드삭제 안 하고 status 전환(refunded/expired)이 원칙이라 restrict가 정답.
alter table public.credit_ledger
  add column credit_source_id uuid references public.credit_sources (id) on delete restrict;

-- =============================================================
-- RLS: 전 신규 테이블 활성화. 쓰기 정책 없음 = 쓰기는 service-role(FastAPI)만.
-- =============================================================
alter table public.pricing_plans enable row level security;
alter table public.payment_history enable row level security;
alter table public.credit_sources enable row level security;
alter table public.refund_requests enable row level security;

-- pricing_plans: is_active 행만 authenticated select (카탈로그, matching_items 패턴)
create policy pricing_plans_active_select on public.pricing_plans
  for select to authenticated using (is_active);

create policy payment_history_owner_select on public.payment_history
  for select using (user_id = (select auth.uid()));
create policy credit_sources_owner_select on public.credit_sources
  for select using (user_id = (select auth.uid()));
create policy refund_requests_owner_select on public.refund_requests
  for select using (user_id = (select auth.uid()));

-- =============================================================
-- 초기 데이터: 구독 요금제 3종 (§초기 데이터). topup SKU·가격은 TBD.
-- =============================================================
insert into public.pricing_plans (code, kind, name, credits, price, billing_period, sort_order)
values
  ('basic',  'subscription', 'Basic',   200, 19900, 'monthly', 1),
  ('plus',   'subscription', 'Plus',    600, 49900, 'monthly', 2),
  ('seller', 'subscription', 'Seller', 1400, 99900, 'monthly', 3);
