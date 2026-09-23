-- 계좌이체(무통장입금) 결제 경로 — 지시서 docs/superpowers/plans/2026-09-22-bank-transfer-payments.md §2.
-- PG(토스) 심사 전까지 사용자가 사업자 통장으로 입금하고 관리자가 확인해 지급한다.
--   bank_transfer_requests : 사용자 신청(상품·금액·크레딧을 신청 시점에 스냅샷) → 관리자 확인/거절
--   manual_plan_grants     : 구독 플랜 1개월 수동 이용권. 토스 subscriptions 와는 별개 개념이다
--                            (billing_key_enc not null 이고 빌러·구독 화면이 그 행을 카드 구독으로 취급한다).
begin;

create table if not exists public.bank_transfer_requests (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  plan_code text not null,                                   -- pricing_plans.code
  kind text not null check (kind in ('subscription', 'topup')),
  amount integer not null check (amount > 0),                -- 신청 시점 가격 스냅샷(원)
  credits integer not null check (credits > 0),              -- 신청 시점 지급량 스냅샷
  payer_name text not null check (char_length(payer_name) between 1 and 30),   -- 입금자 실명
  phone text,
  -- 세금계산서는 셀러가 원할 때만 운영자가 홈택스에서 직접 발행한다. 시스템은 필요 여부와
  -- 발행에 필요한 최소 정보만 받는다.
  tax_invoice boolean not null default false,
  business_no text,
  business_name text,
  representative_name text,
  invoice_email text,
  note text,
  status text not null default 'requested'
    check (status in ('requested', 'paid', 'rejected', 'canceled', 'expired')),
  expires_at timestamptz not null,                           -- 신청 + 3일(정확한 일시)
  paid_at timestamptz,                                       -- 관리자가 입력한 실제 입금 일시
  confirmed_by uuid references auth.users (id),
  confirmed_at timestamptz,
  admin_note text,
  -- 지급 결과와의 명시적 연결(멱등 재확인은 이 값을 그대로 돌려준다)
  payment_id uuid references public.payment_history (id),
  credit_source_id uuid references public.credit_sources (id),
  manual_plan_grant_id uuid,                                 -- FK 는 아래 grants 생성 뒤에 붙인다
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- 종류당 열린 신청 1건(구독 1 + 충전 1 동시 허용). 동시 신청의 최종 방어선.
create unique index if not exists bank_transfer_requests_open_idx
  on public.bank_transfer_requests (user_id, kind) where status = 'requested';
create index if not exists bank_transfer_requests_status_idx
  on public.bank_transfer_requests (status, created_at desc);
create index if not exists bank_transfer_requests_user_idx
  on public.bank_transfer_requests (user_id, created_at desc);

create table if not exists public.manual_plan_grants (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  plan_code text not null,
  request_id uuid references public.bank_transfer_requests (id),   -- 마지막으로 지급·연장한 신청
  starts_at timestamptz not null default now(),
  ends_at timestamptz not null,
  status text not null default 'active' check (status in ('active', 'ended')),
  ended_reason text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (ends_at > starts_at)
);

-- 사용자당 활성 이용권 1건. 같은 플랜 연장은 이 행의 ends_at 을 늘린다.
create unique index if not exists manual_plan_grants_active_idx
  on public.manual_plan_grants (user_id) where status = 'active';
create index if not exists manual_plan_grants_due_idx
  on public.manual_plan_grants (ends_at) where status = 'active';

alter table public.bank_transfer_requests
  add constraint bank_transfer_requests_grant_fk
  foreign key (manual_plan_grant_id) references public.manual_plan_grants (id);

-- RLS 활성 + 정책 없음 = service_role 만. 금액·크레딧 스냅샷과 사업자 정보를 클라이언트가
-- 직접 읽거나 고칠 수 없다(toss_payment_orders 와 같은 방식).
alter table public.bank_transfer_requests enable row level security;
alter table public.manual_plan_grants enable row level security;

create trigger bank_transfer_requests_updated_at before update on public.bank_transfer_requests
  for each row execute function public.set_updated_at();
create trigger manual_plan_grants_updated_at before update on public.manual_plan_grants
  for each row execute function public.set_updated_at();

commit;
