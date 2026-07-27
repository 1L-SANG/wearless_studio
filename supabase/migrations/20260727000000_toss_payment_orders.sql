-- 토스페이먼츠 크레딧 추가구매 주문(결제 인텐트) — WS3/T6.
-- 왜 필요한가: 결제 금액의 **정본**은 결제 요청 전에 서버가 pricing_plans 에서 스냅샷한 값이어야
-- 한다. 승인 단계에서 클라이언트가 보낸 금액을 그대로 믿으면 위변조로 크레딧을 싸게 살 수 있다.
-- 이 테이블이 그 스냅샷과 상태 전이(pending→paid/failed)를 보관해 이중 적립도 막는다.
-- 실제 크레딧 적립은 기존 purchase_topup(payment_history + credit_sources + 멱등 원장)이 담당.

create table if not exists public.toss_payment_orders (
  -- 토스 계약: 영문 대소문자·숫자·'-','_','=' 6~64자. 우리가 생성해 결제창에 넘긴다.
  order_id     text primary key check (char_length(order_id) between 6 and 64),
  user_id      uuid not null references auth.users (id) on delete cascade,
  plan_code    text not null,                                  -- pricing_plans.code (kind='topup')
  amount       integer not null check (amount >= 0),            -- 원(₩) — 결제 요청·승인 대조 기준
  credits      integer not null check (credits > 0),            -- 적립 예정 크레딧(스냅샷)
  status       text not null default 'pending'
                 check (status in ('pending', 'paid', 'failed', 'canceled')),
  payment_key  text,                                            -- 토스 결제 식별키(승인 성공 시)
  approved_at  timestamptz,
  fail_code    text,
  fail_message text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

-- 같은 paymentKey 가 두 주문에 붙는 일이 없게(부분 유니크 — null 다수 허용)
create unique index if not exists toss_payment_orders_payment_key_idx
  on public.toss_payment_orders (payment_key) where payment_key is not null;

create index if not exists toss_payment_orders_user_idx
  on public.toss_payment_orders (user_id, created_at desc);

-- RLS 활성 + 정책 없음 = service_role(RLS 우회)만 접근. 결제 금액 스냅샷을 클라이언트가
-- 직접 읽거나 고칠 수 없어야 한다(금액 위변조 차단의 일부).
alter table public.toss_payment_orders enable row level security;

create trigger toss_payment_orders_updated_at before update on public.toss_payment_orders
  for each row execute function public.set_updated_at();
