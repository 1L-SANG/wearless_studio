import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createServer } from 'vite';

let vite, Pricing, runtime, api, plans;
before(async () => {
  vite = await createServer({
    configFile: false,
    logLevel: 'silent',
    root: new URL('../..', import.meta.url).pathname,
    server: { middlewareMode: true, watch: null, ws: false },
    appType: 'custom',
    esbuild: { jsx: 'automatic' },
    plugins: [{
      name: 'pricing-test-harness',
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
  ({ runtime } = await vite.ssrLoadModule('virtual:pricing-runtime'));
  ({ Pricing } = await vite.ssrLoadModule('/src/features/pricing/Pricing.jsx'));
  ({ api } = await vite.ssrLoadModule('/src/mock/api.js'));
  plans = await api.getPricingPlans();
});
after(() => vite?.close());

function render(plansToShow, currentPlan = 'free', loggedIn = true, queryError = null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(['pricingPlans'], plansToShow);
  if (queryError) {
    client.getQueryCache().find({ queryKey: ['pricingPlans'] }).setState({ status: 'error', error: queryError });
  }
  runtime.account = loggedIn ? { plan: currentPlan } : null;
  runtime.session = loggedIn ? { user: { id: 'pricing-user' } } : null;
  try {
    return renderToStaticMarkup(React.createElement(QueryClientProvider, { client }, React.createElement(Pricing)));
  } finally {
    client.clear();
  }
}

// 2026-09-09: 정기결제(빌링)를 붙이면서 '준비 중' 이 사라졌다. 이 테스트는 옛 상태를
// 고정하고 있었으므로 새 정책(구독 버튼 활성)으로 고쳐 쓴다 — 계획서
// docs/plans/2026-09-09-toss-billing-subscription.md Task 10.
test('구독 카드가 랜딩의 증정 문구와 기능을 표시하고 구독 버튼이 활성이다', () => {
  const html = render(plans);
  assert.doesNotMatch(html, /상세페이지 한 개에 13,000원/);
  for (const text of ['Starter', 'Seller', 'Pro', '₩29,900', '₩69,900', '₩119,000',
    '200 크레딧 추가 증정', '400 크레딧 추가 증정', '마네킹컷 1회 무료 수정 가능',
    '모든 AI 모델 50% 할인', '모든 AI 모델 무료 제공']) assert.ok(html.includes(text), text);
  assert.equal((html.match(/MOST POPULAR/g) || []).length, 1);
  // 2026-09-22: PG 심사 전에는 계좌이체 신청 창이 결제창을 대신한다(lib/tossKeys.js
  // BANK_TRANSFER_ENABLED). 토스 결제 버튼('구매하기')은 스위치를 끄면 그대로 돌아온다 —
  // 그 경로의 계약은 bank-transfer.test.mjs 와 이 파일의 다른 테스트가 나눠 본다.
  assert.equal((html.match(/계좌이체로 시작하기/g) || []).length, 3);
  assert.doesNotMatch(html, /구매하기|준비 중|계좌이체로 구독하기/);
  // 이 하네스는 계좌 정보(/v1/bank-transfer/info)를 채우지 않는다 → 구독 3개와 충전 2개 버튼이
  // 모두 잠기고, 잠긴 사유는 '계좌 미설정' 하나여야 한다(결제 키 유무와 무관).
  const disabled = (html.match(/disabled=""/g) || []).length;
  const notice = (html.match(/계좌이체 신청을 잠시 받지 않아요/g) || []).length;
  assert.equal(disabled, 5);
  assert.equal(notice, 5);
  assert.doesNotMatch(html, /결제 키가 설정되지 않았어요/);
  // 계좌이체 모드에서는 Seller·Pro 의 '충전할 때마다 크레딧 5%/10% 보너스' 줄을 숨긴다 — 지급 코드가
  // 없는데 충전 탭이 열려 있어 약속을 못 지키기 때문(bank-transfer.test.mjs). 그래서 12 가 아니라 10.
  assert.equal((html.match(/<li\b/g) || []).length, 10);
});

test('표에 없는 구독 코드도 이름과 가격, 크레딧만 안전하게 렌더링한다', () => {
  for (const code of ['basic', 'legacy_seller_v8', 'future_plan', 'constructor', '__proto__']) {
    const html = render([{ id: code, code, name: '이전 요금제', kind: 'subscription', credits: 200, price: 19900 }]);
    assert.match(html, /이전 요금제/);
    assert.match(html, /₩19,900/);
    assert.match(html, /200/);
    assert.doesNotMatch(html, /<li\b|추가 증정|MOST POPULAR/);
  }
});

test('로그인한 Seller 계정은 이용 중으로 표시한다', () => {
  assert.match(render(plans, 'seller'), /이용 중/);
});

test('비로그인 방문자는 세 구독 카드의 활성 버튼으로 로그인할 수 있다', () => {
  const html = render(plans, 'free', false);
  assert.equal((html.match(/로그인하고 시작하기/g) || []).length, 3);
  assert.equal((html.match(/disabled=""/g) || []).length, 0);
});

test('빈 요금제 목록은 준비 중 안내를 표시한다', () => {
  assert.match(render([]), /요금제를 준비 중이에요/);
});

// 2026-09-24 오너 결정: 충전 팩은 100·500 크레딧 두 종류뿐이다.
test('목 결제 주문은 충전 팩 두 개의 가격과 지급량을 사용한다', async () => {
  assert.deepEqual(plans.filter((p) => p.kind === 'topup').map((p) => p.code), ['topup_100', 'topup_500']);
  for (const [code, amount, credits] of [
    ['topup_100', 5500, 100], ['topup_500', 26000, 500],
  ]) {
    const order = await api.createTossCheckout(code);
    assert.equal(order.amount, amount);
    assert.equal(order.credits, credits);
  }
  await assert.rejects(api.createTossCheckout('starter'), /존재하지 않는 충전 상품/);
  await assert.rejects(api.createTossCheckout('unknown'), /존재하지 않는 충전 상품/);
});

// 상품 코드별 수량과 Seller 카드의 취소선/보너스를 함께 검증한다.
test('개정 환율 카탈로그와 Seller 카드 지급량이 일치한다', () => {
  for (const [code, credits] of [
    ['starter', 600], ['seller', 1600], ['pro', 2800],
    ['topup_100', 100], ['topup_500', 500],
  ]) assert.equal(plans.find((plan) => plan.code === code)?.credits, credits, code);
  const html = render(plans.filter((plan) => plan.code === 'seller'));
  for (const text of ['1,400', '1,600', '200 크레딧 추가 증정']) assert.ok(html.includes(text), text);
});

test('구독 카드의 상세페이지 예상 개수는 254크레딧 기준으로 내림한다', () => {
  const html = render(plans);
  assert.deepEqual([...html.matchAll(/상세페이지 약 <strong>(\d+)개<\/strong>/g)].map((match) => match[1]), ['2', '6', '11']);
  const starter = plans.find((plan) => plan.code === 'starter');
  for (const [credits, count] of [['507', 1], ['508', 2]]) {
    assert.ok(render([{ ...starter, credits }]).includes(`상세페이지 약 <strong>${count}개</strong>`), credits);
  }
});

test('구독 카드가 보일 때만 평균 크레딧 안내 두 문장을 줄을 나누어 표시한다', () => {
  const html = render(plans);
  const note = html.match(/<p\b[^>]*>상세페이지 1개의 제작[\s\S]*?<\/p>/)?.[0];
  assert.ok(note, '평균 크레딧 안내 문단');
  assert.deepEqual(note.replace(/<br\s*\/?\s*>/g, '\n').replace(/<[^>]*>/g, '').split('\n'), [
    '상세페이지 1개의 제작을 처음부터 끝까지 진행했을 때 평균적으로 약\u00a0250크레딧이 소모됩니다.',
    '컷수에 따라 소모되는 비용은 상이합니다.',
  ]);
  assert.ok(html.indexOf(note) < html.indexOf('id="topup"'));
  for (const hidden of [
    render([]),
    render(undefined),
    render(plans.filter((plan) => plan.kind === 'topup')),
    render(plans, 'free', true, new Error('요금제 조회 실패')),
  ]) assert.doesNotMatch(hidden, /상세페이지 약|상세페이지 1개의 제작|컷수에 따라/);
});
