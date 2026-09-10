-- 퀵계좌이체 자동결제 지원 — 결제수단을 카드 전용에서 일반화한다.
-- 계획서 docs/plans/2026-09-09-toss-billing-subscription.md
--
-- 토스는 같은 requestBillingAuth 로 카드(method='CARD')와 퀵계좌이체(method='TRANSFER')를
-- 모두 받는다. 빌링키 발급 응답이 갈릴 뿐이다:
--   카드   → card.issuerCode · card.number
--   계좌   → transfers[].bankName · transfers[].bankAccountNumber
-- 화면에 필요한 건 양쪽 다 "이름 + 뒤 4자리" 하나뿐이라, 컬럼을 카드 이름으로 두면
-- 계좌 정보를 card_brand 에 넣는 거짓말이 된다. 지금 이름을 바꾼다 — 이 테이블은
-- 아직 프로덕션에 없다(플래그 off). 나중에 바꾸면 데이터 마이그레이션이 붙는다.
alter table public.subscriptions
  rename column card_brand to method_label;
alter table public.subscriptions
  rename column card_last4 to method_last4;

alter table public.subscriptions
  add column if not exists pay_method text not null default 'CARD'
    check (pay_method in ('CARD', 'TRANSFER'));

comment on column public.subscriptions.pay_method is
  '토스 빌링키의 결제수단. CARD=카드, TRANSFER=퀵계좌이체.';
comment on column public.subscriptions.method_label is
  '표시용 이름. 카드면 카드사명(현대), 계좌면 은행명(토스). 토스가 안 주면 null.';
comment on column public.subscriptions.method_last4 is
  '표시용 뒤 4자리. 카드번호 또는 계좌번호의 마지막 4자다.';
