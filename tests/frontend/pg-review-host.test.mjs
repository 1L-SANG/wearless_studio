/* 토스페이먼츠 심사 전용 호스트(toss.wearless.kr, 2026-09-29).

   운영(ai.wearless.kr)은 PG 심사 전이라 계좌이체로 받고, 토스는 별도 주소에서 결제창까지
   심사하겠다고 했다. 같은 셀러 번들을 두 호스트가 받으므로 결제 경로는 호스트명으로만 갈린다
   (lib/host.js IS_PG_REVIEW_HOST → lib/tossKeys.js BANK_TRANSFER_ENABLED).

   지키는 것 두 가지:
     · 심사 호스트에서는 구독 카드가 토스 결제창('구매하기')을 연다 — 계좌이체 CTA 가 남으면
       심사자가 카드 결제를 볼 수 없다.
     · ai.wearless.kr 은 그대로 계좌이체다 — 운영 손님에게 토스 결제창이 새면 안 된다. */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createServer } from 'vite';
import {
  GENERAL_PAYMENT_REVIEW_HOST,
  isGeneralPaymentReviewHost,
  isPgReviewHost,
  PG_REVIEW_HOST,
  sellerRootTarget,
} from '../../src/lib/host.js';
import { generalPaymentReviewOrder, requestTossCardPayment,
  resolveTopupCheckout } from '../../src/lib/generalPaymentReview.js';
import { TOPUP_VALIDITY_EFFECTIVE_DATE } from '../../src/lib/topupPolicy.js';

test('심사 호스트는 정확히 toss.wearless.kr 하나다', () => {
  assert.equal(PG_REVIEW_HOST, 'toss.wearless.kr');
  assert.equal(isPgReviewHost('toss.wearless.kr'), true);
  assert.equal(isPgReviewHost('TOSS.wearless.kr'), true);
  for (const host of ['ai.wearless.kr', 'wearless.kr', 'facemarket.wearless.kr', 'admin.wearless.kr',
    'toss.wearless.kr.evil.example', 'x.toss.wearless.kr', 'toss.vercel.app', 'localhost', '', undefined]) {
    assert.equal(isPgReviewHost(host), false, String(host));
  }
});

test('일반결제 심사 호스트는 pay.wearless.kr 하나이고 루트에서 충전 상품으로 간다', () => {
  assert.equal(GENERAL_PAYMENT_REVIEW_HOST, 'pay.wearless.kr');
  assert.equal(isGeneralPaymentReviewHost('pay.wearless.kr'), true);
  assert.equal(isGeneralPaymentReviewHost('PAY.wearless.kr'), true);
  for (const host of ['ai.wearless.kr', 'toss.wearless.kr', 'pay.wearless.kr.evil.example',
    'x.pay.wearless.kr', 'localhost', '', undefined]) {
    assert.equal(isGeneralPaymentReviewHost(host), false, String(host));
  }
  assert.equal(sellerRootTarget('pay.wearless.kr'), '/pricing?tab=topup');
  assert.equal(sellerRootTarget('ai.wearless.kr'), '/create/input');
  assert.equal(sellerRootTarget('toss.wearless.kr'), '/create/input');
});

let vite, runtime;
const previousWindow = globalThis.window;

function createHarnessServer() {
  return createServer({
    optimizeDeps: { noDiscovery: true, include: [] },
    configFile: false,
    logLevel: 'silent',
    root: new URL('../..', import.meta.url).pathname,
    server: { middlewareMode: true, watch: null, ws: false },
    appType: 'custom',
    esbuild: { jsx: 'automatic' },
    plugins: [{
      name: 'pg-review-host-harness',
      enforce: 'pre',
      resolveId(id) {
        if (id === 'virtual:pricing-runtime') return '\0pricing-runtime';
        if (id === '@/store/useAppStore.js') return '\0pricing-store';
        if (id === '@/features/auth/AuthProvider.jsx') return '\0pricing-auth';
        if (id.startsWith('@/')) return new URL(`../../src/${id.slice(2)}`, import.meta.url).pathname;
        return null;
      },
      load(id) {
        if (id === '\0pricing-runtime') return 'export const runtime = { account: null, session: null };';
        if (id === '\0pricing-store') return `
          import { runtime } from 'virtual:pricing-runtime';
          export const useAppStore = (selector) => selector({ account: runtime.account });
        `;
        if (id === '\0pricing-auth') return `
          import { runtime } from 'virtual:pricing-runtime';
          export const useAuth = () => ({ session: runtime.session, openLogin() {} });
        `;
        return null;
      },
    }],
  });
}

after(async () => {
  if (previousWindow === undefined) delete globalThis.window;
  else globalThis.window = previousWindow;
  await vite?.close();
});

/* 호스트 판정은 모듈을 읽는 순간 한 번 정해지는 상수다. 그래서 호스트마다 모듈 그래프를
   비우고 새 window 로 다시 읽는다. */
async function loadAs(hostname, { extraPlans = [] } = {}) {
  globalThis.window = { location: { hostname, search: '', origin: `https://${hostname}` } };
  await vite?.close();
  vite = await createHarnessServer();
  const tossKeys = await vite.ssrLoadModule('/src/lib/tossKeys.js');
  const { Pricing } = await vite.ssrLoadModule('/src/features/pricing/Pricing.jsx');
  ({ runtime } = await vite.ssrLoadModule('virtual:pricing-runtime'));
  const { api } = await vite.ssrLoadModule('/src/mock/api.js');
  const plans = [...await api.getPricingPlans(), ...extraPlans];

  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(['pricingPlans'], plans);
  runtime.account = { plan: 'free' };
  runtime.session = { user: { id: 'pg-review-user' } };
  try {
    const html = renderToStaticMarkup(
      React.createElement(QueryClientProvider, { client }, React.createElement(Pricing)));
    return { tossKeys, html };
  } finally {
    client.clear();
  }
}

test('심사 호스트에서는 구독 카드가 토스 결제창을 연다', async () => {
  const { tossKeys, html } = await loadAs('toss.wearless.kr');
  assert.equal(tossKeys.BANK_TRANSFER_ENABLED, false);
  assert.equal(tossKeys.TOPUP_ENABLED, false);
  assert.equal(tossKeys.SUBSCRIPTION_PRODUCTS_ENABLED, true);
  assert.equal((html.match(/구매하기/g) || []).length, 3);
  assert.doesNotMatch(html, /계좌이체로 시작하기|계좌이체로 충전하기/);
  assert.match(html, /<p\b[^>]*>매월 자동 결제되고 언제든 해지할 수 있어요\. 해지해도 결제한 기간까지 쓸 수 있어요\. 부가세 포함\.<\/p><p\b[^>]*>결제하면 /);
  assert.match(html, /<a href="https:\/\/wearless\.kr\/terms">이용약관<\/a>/);
  assert.match(html, /<a href="https:\/\/wearless\.kr\/refund">환불 정책<\/a>에 동의하는 것으로 봐요\./);
  assert.doesNotMatch(html, /상세페이지 1개의 제작|평균적으로|250크레딧|컷수에 따라/);
  assert.doesNotMatch(html, /월간 정기결제 상품입니다|결제 완료 즉시|구독 크레딧 및 환불 안내|미사용 구독 크레딧|이월분을 포함해|3일의 유예기간|7일 이내|환불 및 중도해지는|환불정책 보기|mailto:contact@wearless\.kr/);
});

test('일반결제 심사 호스트는 단건 크레딧 두 상품과 토스 결제 버튼만 보여준다', async () => {
  const { tossKeys, html } = await loadAs('pay.wearless.kr');
  assert.equal(tossKeys.BANK_TRANSFER_ENABLED, false);
  assert.equal(tossKeys.TOPUP_ENABLED, true);
  assert.equal(tossKeys.SUBSCRIPTION_PRODUCTS_ENABLED, false);
  assert.match(html, /크레딧 추가 구매/);
  assert.match(html, /100 크레딧/);
  assert.match(html, /500 크레딧/);
  assert.match(html, /₩5,500/);
  assert.match(html, /₩26,000/);
  assert.equal((html.match(/>충전하기<\/button>/g) || []).length, 2);
  assert.doesNotMatch(html, /Starter|Seller|Pro|정기 구독|매월 자동 결제|계좌이체/);
  assert.match(html, /1회 결제 · 결제 완료 즉시 지급 · 2026년 11월 23일 결제건부터 12개월 사용/);
  assert.match(html, /결제일부터 12개월 뒤 남은 수량이 소멸해요/);
  assert.match(html, /다른 사람에게 양도하거나 현금으로 전환할 수 없어요/);
  assert.match(html, /결제하면 .*이용약관.*환불 정책/);
});

test('일반결제 심사 호스트는 승인한 두 SKU와 정확한 가격만 허용한다', async () => {
  const { html } = await loadAs('pay.wearless.kr', { extraPlans: [
    { id: 'legacy', code: 'topup_legacy', kind: 'topup', name: '옛 충전', credits: 50,
      price: 3000, billingPeriod: 'once', sortOrder: 1 },
    { id: 'wrong-price', code: 'topup_100', kind: 'topup', name: '가격이 바뀐 100 크레딧',
      credits: 100, price: 6000, billingPeriod: 'once', sortOrder: 2 },
  ] });
  assert.equal((html.match(/>충전하기<\/button>/g) || []).length, 2);
  assert.doesNotMatch(html, /옛 충전|가격이 바뀐|₩3,000|₩6,000/);
});

test('시크릿 발급 전 확인 주문은 고정 상품으로만 결제창 입력값을 만든다', async () => {
  const order = generalPaymentReviewOrder('topup_100', 'review-user');
  assert.match(order.orderId, /^wl-review-/);
  assert.ok(order.orderId.length >= 6 && order.orderId.length <= 64);
  assert.deepEqual({ ...order, orderId: '<random>' }, {
    orderId: '<random>', amount: 5500, credits: 100, bonusCredits: 0, totalCredits: 100,
    orderName: '100 크레딧', customerKey: 'review-user',
  });
  assert.equal(generalPaymentReviewOrder('topup_legacy', 'review-user'), null);
  assert.equal(generalPaymentReviewOrder('topup_100', ''), null);
  const unavailable = Object.assign(new Error('not configured'), { code: 'payment_not_configured' });
  const fallback = await resolveTopupCheckout({
    createCheckout: async () => { throw unavailable; }, planCode: 'topup_100',
    customerKey: 'review-user', isReviewHost: true,
  });
  assert.equal(fallback.amount, 5500);
  await assert.rejects(resolveTopupCheckout({
    createCheckout: async () => { throw unavailable; }, planCode: 'topup_100',
    customerKey: 'review-user', isReviewHost: false,
  }), (error) => error === unavailable);

  const calls = [];
  await requestTossCardPayment({
    clientKey: 'test_ck_example', order: fallback, origin: 'https://pay.wearless.kr',
    loadTossPayments: async (key) => ({ payment: ({ customerKey }) => ({
      requestPayment: async (request) => calls.push({ key, customerKey, request }),
    }) }),
  });
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], {
    key: 'test_ck_example', customerKey: 'review-user', request: {
      method: 'CARD', amount: { currency: 'KRW', value: 5500 },
      orderId: fallback.orderId, orderName: '100 크레딧',
      successUrl: 'https://pay.wearless.kr/payments/success',
      failUrl: 'https://pay.wearless.kr/payments/fail',
    },
  });
});

test('ai.wearless.kr 은 계좌이체 그대로다', async () => {
  const { tossKeys, html } = await loadAs('ai.wearless.kr');
  assert.equal(tossKeys.BANK_TRANSFER_ENABLED, true);
  assert.equal(tossKeys.TOPUP_ENABLED, false);
  assert.equal(tossKeys.SUBSCRIPTION_PRODUCTS_ENABLED, true);
  assert.equal((html.match(/계좌이체로 시작하기/g) || []).length, 3);
  assert.doesNotMatch(html, /구매하기/);
  assert.doesNotMatch(html, /매월 자동 결제되고|해지해도 결제한 기간까지 쓸 수 있어요|부가세 포함/);
  assert.doesNotMatch(html, /상세페이지 1개의 제작|평균적으로|250크레딧|컷수에 따라/);
  assert.match(html, /<a href="https:\/\/wearless\.kr\/refund">결제·환불 조건<\/a>/);
});

test('두 심사 호스트는 검색 노출을 막는다', () => {
  const config = JSON.parse(readFileSync(new URL('../../vercel.json', import.meta.url), 'utf8'));
  const noindexHosts = (config.headers || [])
    .filter((rule) => rule.headers?.some((header) => header.key === 'X-Robots-Tag'
      && header.value === 'noindex, nofollow'))
    .flatMap((rule) => rule.has || [])
    .filter((condition) => condition.type === 'host')
    .map((condition) => condition.value);
  assert.deepEqual(noindexHosts.sort(), ['pay.wearless.kr', 'toss.wearless.kr']);
});

test('추가구매 유효기간 시행일은 화면·서버·공개 법무문서가 같다', () => {
  assert.equal(TOPUP_VALIDITY_EFFECTIVE_DATE, '2026-11-23');
  const serverPolicy = readFileSync(new URL('../../server/app/topup_policy.py', import.meta.url), 'utf8');
  assert.match(serverPolicy, /TOPUP_VALIDITY_EFFECTIVE_DATE = date\(2026, 11, 23\)/);
  const manifest = JSON.parse(readFileSync(new URL('../../public/legal/manifest.json', import.meta.url), 'utf8'));
  for (const slug of ['terms-seller', 'refund']) {
    assert.equal(manifest.find((item) => item.slug === slug)?.effectiveDate, '2026-11-23', slug);
  }
});
