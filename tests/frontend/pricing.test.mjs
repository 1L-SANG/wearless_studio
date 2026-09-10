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

function render(plansToShow, currentPlan = 'free', loggedIn = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(['pricingPlans'], plansToShow);
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
  for (const text of ['Starter', 'Seller', 'Pro', '₩29,900', '₩79,900', '₩159,000',
    '2,000 크레딧 추가 증정', '6,000 크레딧 추가 증정', '마네킹컷 1회 무료 수정 가능',
    '모든 AI 모델 50% 할인', '모든 AI 모델 무료 제공']) assert.ok(html.includes(text), text);
  assert.equal((html.match(/MOST POPULAR/g) || []).length, 1);
  assert.equal((html.match(/구매하기/g) || []).length, 3);
  // 계좌이체는 우리 MID 에서 아직 안 열려 숨겨 둔다(SUBSCRIPTION_TRANSFER_ENABLED=false).
  assert.doesNotMatch(html, /계좌이체로 구독하기/);
  // '준비 중'(기능 미구현)이 아니라 '구매하기'다.
  assert.doesNotMatch(html, /준비 중/);
  // 비활성 여부는 VITE_TOSS_BILLING_CLIENT_KEY 유무에 달렸다(로컬에 .env 가 있으면 활성,
  // CI 처럼 없으면 비활성). 환경에 따라 갈리는 값을 고정하면 테스트가 환경을 검사하게 된다 —
  // 여기서 지킬 계약은 '비활성이라면 그 사유가 결제 키 부재'라는 것뿐이다.
  const disabled = (html.match(/disabled=""/g) || []).length;
  const keyNotice = (html.match(/결제 키가 설정되지 않았어요/g) || []).length;
  assert.equal(disabled, keyNotice, '비활성 사유가 결제 키 부재로 설명돼야 한다');
  assert.ok(disabled === 0 || disabled === 3, `구독 버튼 비활성 수가 이상하다: ${disabled}`);
  assert.equal((html.match(/<li\b/g) || []).length, 12);
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

test('목 결제 주문은 새 충전 상품 다섯 개의 가격과 지급량을 사용한다', async () => {
  for (const [code, amount, credits] of [
    ['topup_finish', 9900, 1800], ['topup_start', 24900, 4700],
    ['topup_repeat', 69900, 13800], ['topup_season', 149000, 30500],
    ['topup_bulk', 299000, 64000],
  ]) {
    const order = await api.createTossCheckout(code);
    assert.equal(order.amount, amount);
    assert.equal(order.credits, credits);
  }
  await assert.rejects(api.createTossCheckout('starter'), /존재하지 않는 충전 상품/);
  await assert.rejects(api.createTossCheckout('unknown'), /존재하지 않는 충전 상품/);
});
