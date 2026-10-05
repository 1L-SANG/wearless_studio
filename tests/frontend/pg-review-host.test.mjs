/* 토스페이먼츠 심사 전용 호스트(toss.wearless.kr, 2026-09-29).

   운영(ai.wearless.kr)은 PG 심사 전이라 계좌이체로 받고, 토스는 별도 주소에서 결제창까지
   심사하겠다고 했다. 같은 셀러 번들을 두 호스트가 받으므로 결제 경로는 호스트명으로만 갈린다
   (lib/host.js IS_PG_REVIEW_HOST → lib/tossKeys.js BANK_TRANSFER_ENABLED).

   지키는 것 두 가지:
     · 심사 호스트에서는 구독 카드가 토스 결제창('구매하기')을 연다 — 계좌이체 CTA 가 남으면
       심사자가 카드 결제를 볼 수 없다.
     · ai.wearless.kr 은 그대로 계좌이체다 — 운영 손님에게 토스 결제창이 새면 안 된다. */
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createServer } from 'vite';
import { isPgReviewHost, PG_REVIEW_HOST } from '../../src/lib/host.js';

test('심사 호스트는 정확히 toss.wearless.kr 하나다', () => {
  assert.equal(PG_REVIEW_HOST, 'toss.wearless.kr');
  assert.equal(isPgReviewHost('toss.wearless.kr'), true);
  assert.equal(isPgReviewHost('TOSS.wearless.kr'), true);
  for (const host of ['ai.wearless.kr', 'wearless.kr', 'facemarket.wearless.kr', 'admin.wearless.kr',
    'toss.wearless.kr.evil.example', 'x.toss.wearless.kr', 'toss.vercel.app', 'localhost', '', undefined]) {
    assert.equal(isPgReviewHost(host), false, String(host));
  }
});

let vite, runtime;
const previousWindow = globalThis.window;

before(async () => {
  vite = await createServer({
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
});

after(async () => {
  if (previousWindow === undefined) delete globalThis.window;
  else globalThis.window = previousWindow;
  await vite?.close();
});

/* 호스트 판정은 모듈을 읽는 순간 한 번 정해지는 상수다. 그래서 호스트마다 모듈 그래프를
   비우고 새 window 로 다시 읽는다. */
async function loadAs(hostname) {
  globalThis.window = { location: { hostname, search: '', origin: `https://${hostname}` } };
  vite.moduleGraph.invalidateAll();
  const tossKeys = await vite.ssrLoadModule('/src/lib/tossKeys.js');
  const { Pricing } = await vite.ssrLoadModule('/src/features/pricing/Pricing.jsx');
  ({ runtime } = await vite.ssrLoadModule('virtual:pricing-runtime'));
  const { api } = await vite.ssrLoadModule('/src/mock/api.js');
  const plans = await api.getPricingPlans();

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
  assert.equal((html.match(/구매하기/g) || []).length, 3);
  assert.doesNotMatch(html, /계좌이체로 시작하기|계좌이체로 충전하기/);
});

test('ai.wearless.kr 은 계좌이체 그대로다', async () => {
  const { tossKeys, html } = await loadAs('ai.wearless.kr');
  assert.equal(tossKeys.BANK_TRANSFER_ENABLED, true);
  assert.equal((html.match(/계좌이체로 시작하기/g) || []).length, 3);
  assert.doesNotMatch(html, /구매하기/);
});
