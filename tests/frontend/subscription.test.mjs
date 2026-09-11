/* 구독 UI 계약 — 해지 확인창이 '무엇이 사라지는지' 를 숫자로 보여주는가.
   이월 정책이라 해지 시 여러 달치가 한 번에 소멸한다. 숫자 없이 확인만 받으면
   환불 분쟁이 난다(계획서 docs/plans/2026-09-09-toss-billing-subscription.md §0.1). */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';

const SUB = readFileSync('src/features/subscription/Subscription.jsx', 'utf8');
const PRICING = readFileSync('src/features/pricing/Pricing.jsx', 'utf8');
const API = readFileSync('src/lib/api/httpAdapter.js', 'utf8');
const APP = readFileSync('src/apps/seller/App.jsx', 'utf8');

test('해지 확인창이 소멸 예정 크레딧과 날짜를 보여준다', () => {
  assert.match(SUB, /expiring\.credits/);
  assert.match(SUB, /expiring\.expiresAt/);
  assert.match(SUB, /소멸/);
});

test('해지 고지가 이월분도 함께 사라진다는 사실을 밝힌다', () => {
  assert.match(SUB, /이월/);
});

test('요금제 화면의 구독 버튼이 더 이상 준비 중이 아니다', () => {
  assert.doesNotMatch(PRICING, /결제 연동 준비 중/);
  assert.doesNotMatch(PRICING, /'구독하기'\} \{!isCurrent && '\(준비 중\)'\}/);
  assert.match(PRICING, /requestBillingAuth/);
  assert.match(PRICING, /subscribe\(p\.code, 'CARD'\)/);
});

/* 퀵계좌이체 자동결제 — 같은 자동결제 계약 안에서 쓰고, 카드보다 수수료가 낮다
   (토스 문서: 카드 대비 1%p 이상). 등록은 method: 'TRANSFER' 한 글자 차이다. */
test('계좌이체는 코드로 남기되 플래그로 가려 둔다', () => {
  // 우리 MID 에서 아직 안 열린다(2026-09-10). 지우면 열리는 날 다시 만들어야 하므로
  // 서버·스키마는 그대로 두고 버튼만 가린다.
  assert.match(KEYS, /SUBSCRIPTION_TRANSFER_ENABLED = false/);
  assert.match(PRICING, /SUBSCRIPTION_TRANSFER_ENABLED && !isCurrent/);
  assert.match(PRICING, /subscribe\(p\.code, 'TRANSFER'\)/);
  // method 를 하드코딩하지 않고 인자로 받아야 두 갈래가 같은 코드를 지난다.
  // (충전 경로의 requestPayment 는 일반결제라 method: 'CARD' 가 맞다 — 그건 건드리지 않는다)
  assert.match(PRICING, /async function subscribe\(planCode, method = 'CARD'\)/);
  const billingCall = PRICING.slice(PRICING.indexOf('requestBillingAuth'));
  assert.match(billingCall, /requestBillingAuth\(\{\s*method,/);
});

test('구독 관리는 결제수단 종류를 구분해 표시한다', () => {
  assert.match(SUB, /changeMethod\('CARD'\)/);
  assert.match(SUB, /SUBSCRIPTION_TRANSFER_ENABLED && \(/);
  assert.match(SUB, /data\.method === 'TRANSFER'/);
});

/* 충전은 일반결제라 계약 전까지 라이브에서 실패한다(2026-09-10 토스 확인: 자동결제 MID 로
   단건 결제 대체도 불가). 누르면 실패할 버튼을 남기면 사용자가 결제 실패를 겪는다.
   서버 라우트는 살려 두고 화면만 닫는다 — 계약이 생기면 플래그만 켜면 된다. */
test('충전 탭은 계약 전까지 닫아 둔다', () => {
  assert.match(KEYS, /TOPUP_ENABLED = false/);
  assert.match(PRICING, /\{TOPUP_ENABLED && \(/);
  // 상태가 남아도 빈 화면을 그리지 않게 구독으로 되돌린다
  assert.match(PRICING, /TOPUP_ENABLED \? tab : 'subscription'/);
});

test('충전 서버 라우트는 지우지 않는다', () => {
  // 계약이 생기는 날 다시 만들 이유가 없다. 키가 없으면 이미 503 이라 위험하지도 않다.
  const payments = readFileSync('server/app/payments.py', 'utf8');
  assert.match(payments, /\/toss\/checkout/);
  assert.match(payments, /payment_not_configured/);
});

test('요금제에서 긴 구독 고지문을 표시하지 않는다', () => {
  assert.doesNotMatch(PRICING, /구독은 해지할 때까지|청약철회|전액 환불/);
});

test('구독 API 5개가 어댑터에 있다', () => {
  for (const name of ['startSubscription', 'getMySubscription', 'cancelSubscription',
    'resumeSubscription', 'replaceSubscriptionCard']) {
    assert.match(API, new RegExp(`${name}\\(`), `${name} 없음`);
  }
});

/* 구독 관리로 가는 길이 화면에 있어야 한다. /pricing 은 '무엇을 살까' 라서
   이미 구독 중인 사람이 해지·카드변경 하러 갈 곳이 없었다(2026-09-10 QA 지적). */
test('프로필 메뉴에 구독 관리가 있다', () => {
  const shell = readFileSync('src/features/shell/shell.jsx', 'utf8');
  assert.match(shell, /구독 관리/);
  assert.match(shell, /navigate\('\/subscription'\)/);
});

test('카드사명이 없어도 등록된 카드로 표시한다', () => {
  // 토스 API 2024-06-01 부터 자동결제 응답에 cardCompany 가 없다(문서 명시).
  // brand 로 분기하면 등록된 카드를 '없음'으로 표시한다.
  assert.match(SUB, /data\.card\?\.last4/);
  assert.doesNotMatch(SUB, /data\.card\?\.brand \? `\$\{data\.card\.brand\} ····/);
});

test('구독 라우트 3개가 등록돼 있다', () => {
  assert.match(APP, /path="subscription"/);
  assert.match(APP, /path="subscription\/success"/);
  assert.match(APP, /path="subscription\/fail"/);
});

test('날짜는 KST 헬퍼로 표시한다 (ko-KR 직접 호출 금지)', () => {
  assert.match(SUB, /from '@\/lib\/datetime\.js'/);
  assert.doesNotMatch(SUB, /toLocaleDateString\('ko-KR'/);
});

test('빌링키를 프런트에서 다루지 않는다', () => {
  assert.doesNotMatch(SUB, /billingKey/);
  assert.doesNotMatch(PRICING, /billingKey/);
});

/* MID 가 갈리면 클라이언트 키도 갈린다(토스 문서: 클라이언트·시크릿은 MID 단위 한 세트).
   충전과 구독이 같은 키를 쓰면 둘 중 하나가 INVALID_API_KEY / NOT_SUPPORTED_METHOD 로
   반드시 깨진다. 2026-09-09 실제로 이 상태였고, 여기서 다시 붙지 않게 고정한다. */
const KEYS = readFileSync('src/lib/tossKeys.js', 'utf8');

test('충전과 구독의 클라이언트 키가 분리돼 있다', () => {
  assert.match(KEYS, /VITE_TOSS_CLIENT_KEY/);
  assert.match(KEYS, /VITE_TOSS_BILLING_CLIENT_KEY/);
  // MID 가 하나인 상점은 빌링 키를 비워도 되게 — 서버 fallback 규칙과 같은 모양
  assert.match(KEYS, /VITE_TOSS_BILLING_CLIENT_KEY \|\| TOSS_CLIENT_KEY/);
});

test('빌링 인증(requestBillingAuth)은 빌링 클라이언트 키로만 부른다', () => {
  for (const [name, src] of [['Pricing', PRICING], ['Subscription', SUB]]) {
    if (!src.includes('requestBillingAuth')) continue;
    assert.match(src, /loadTossPayments\(TOSS_BILLING_CLIENT_KEY\)/, name);
  }
  // 충전 경로는 그대로 일반결제 키를 쓴다
  assert.match(PRICING, /loadTossPayments\(TOSS_CLIENT_KEY\)/);
});

test('클라이언트 키를 컴포넌트에서 직접 읽지 않는다', () => {
  for (const src of [PRICING, SUB]) {
    assert.doesNotMatch(src, /import\.meta\.env\.VITE_TOSS/);
  }
});

/* 토스는 결제·카드등록 실패를 `?code=PAY_PROCESS_CANCELED` 로 돌려보낸다. AuthProvider 가
   그 code 를 OAuth PKCE 코드로 오인하면 ① 교환 실패가 setSession(null) 로 이어져 **멀쩡한
   세션이 로그아웃**되고 ② URL 에서 code 를 지워 실패 화면이 사유를 못 읽는다.
   2026-09-10 로컬 QA 에서 실제로 ①에 막혔다 — 구독 요청이 서버까지 오지 못했다. */
const AUTH = readFileSync('src/features/auth/AuthProvider.jsx', 'utf8');

test('결제 결과 경로에서는 OAuth code 처리를 건너뛴다', () => {
  for (const p of ['/payments/success', '/payments/fail',
    '/subscription/success', '/subscription/fail']) {
    assert.ok(AUTH.includes(`'${p}'`), `${p} 가 예외 경로에 없음`);
  }
  assert.match(AUTH, /isPaymentResultPath\(\)\s*\?\s*null/);
});

test('OAuth code 교환 실패가 기존 세션을 지우지 않는다', () => {
  // 교환은 자체 try/catch 안에 있어야 한다 — 바깥 catch(setSession(null))로 떨어지면 안 된다.
  assert.match(AUTH, /try\s*\{\s*await exchangeOAuthCodeOnce\(code\);\s*\}\s*catch/);
});
