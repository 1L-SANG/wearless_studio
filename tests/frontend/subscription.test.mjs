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
  assert.match(PRICING, /subscribe\(p\.code\)/);
});

test('구독 고지문이 이월 정책과 일치한다', () => {
  assert.doesNotMatch(PRICING, /소멸하고 이월되지 않아요/);
  assert.match(PRICING, /이월/);
});

test('구독 API 5개가 어댑터에 있다', () => {
  for (const name of ['startSubscription', 'getMySubscription', 'cancelSubscription',
    'resumeSubscription', 'replaceSubscriptionCard']) {
    assert.match(API, new RegExp(`${name}\\(`), `${name} 없음`);
  }
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
