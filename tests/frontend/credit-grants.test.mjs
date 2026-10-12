import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createServer } from 'vite';

let vite, api, DB, CreditsHistory;
before(async () => {
  vite = await createServer({
    optimizeDeps: { noDiscovery: true, include: [] },
    configFile: false, logLevel: 'silent', root: new URL('../..', import.meta.url).pathname,
    server: { middlewareMode: true, watch: null, hmr: false }, appType: 'custom', esbuild: { jsx: 'automatic' },
    plugins: [{
      name: 'credit-grants-harness', enforce: 'pre',
      resolveId(id) {
        if (id === '@/store/useAppStore.js') return '\0credits-store';
        if (id.startsWith('@/')) return new URL(`../../src/${id.slice(2)}`, import.meta.url).pathname;
      },
      load(id) { if (id === '\0credits-store') return 'export const useAppStore = selector => selector({ account: null });'; },
    }],
  });
  ({ api } = await vite.ssrLoadModule('/src/mock/api.js'));
  ({ DB } = await vite.ssrLoadModule('/src/mock/db.js'));
  ({ CreditsHistory } = await vite.ssrLoadModule('/src/features/credits/CreditsHistory.jsx'));
});
after(() => vite?.close());

test('목 기본 설정의 첫 동의는 가입 크레딧을 지급하지 않고 동의를 저장한다', async () => {
  assert.equal(DB.config.signupCredits, 0);
  const start = DB.account.credits;
  const ledgerCount = (await api.getCreditHistory()).length;
  const sourceCount = (await api.getCreditSources()).length;
  const options = { expectedUserId: 'default-signup-user' };
  const state = await api.getSellerConsent(options);
  assert.equal(state.needsConsent, true);
  const first = await api.acceptSellerConsent({
    ...options, termsVersion: state.required.terms, privacyVersion: state.required.privacy,
  });
  assert.equal(first.signupCredits, 0);
  assert.equal(first.needsConsent, false);
  assert.ok(first.accepted.acceptedAt);
  assert.equal(DB.account.credits, start);
  assert.equal((await api.getCreditHistory()).length, ledgerCount);
  assert.equal((await api.getCreditSources()).length, sourceCount);
});

test('목 첫 셀러 동의는 가입 크레딧을 한번만 지급하고 무료 원장과 소멸 없는 버킷을 만든다', async t => {
  const original = DB.config.signupCredits;
  DB.config.signupCredits = 150;
  t.after(() => { DB.config.signupCredits = original; });
  const start = DB.account.credits;
  const user = 'signup-test-user';
  const options = { expectedUserId: user };
  assert.equal(typeof api.getSellerConsent, 'function');
  const state = await api.getSellerConsent(options);
  assert.equal(state.needsConsent, true);
  assert.equal(state.signupCredits, 0);
  const body = { ...options, termsVersion: state.required.terms, privacyVersion: state.required.privacy };
  const first = await api.acceptSellerConsent(body);
  assert.equal(first.signupCredits, 150);
  assert.equal(first.needsConsent, false);
  assert.ok(first.accepted.acceptedAt);
  assert.equal(DB.account.credits, start + 150);
  assert.equal((await api.acceptSellerConsent(body)).signupCredits, 0);
  assert.equal(DB.account.credits, start + 150);
  const ledger = (await api.getCreditHistory()).filter((row) => row.actionKey === 'grant_signup');
  assert.equal(ledger.length, 1);
  assert.equal(ledger[0].idempotencyKey, `signup:${user}`);
  assert.equal(ledger[0].delta, 150);
  const sources = (await api.getCreditSources()).filter((row) => row.sourceType === 'free');
  assert.equal(sources.length, 1);
  assert.equal(sources[0].initialCredits, 150);
  assert.equal(sources[0].periodEnd, null);
  assert.equal(sources[0].paymentId, null);
  assert.equal(ledger[0].creditSourceId, sources[0].id);
  assert.deepEqual(ledger[0].metadata, { reason: 'signup' });
});

test('가입 크레딧 0 설정도 동의를 기록하고 다음 동의에 다시 지급하지 않는다', async () => {
  assert.ok(DB.config, 'mock server config');
  const original = DB.config.signupCredits;
  DB.config.signupCredits = 0;
  try {
    const state = await api.getSellerConsent({ expectedUserId: 'zero-signup-user' });
    const body = { expectedUserId: 'zero-signup-user', termsVersion: state.required.terms, privacyVersion: state.required.privacy };
    const start = DB.account.credits;
    assert.equal((await api.acceptSellerConsent(body)).signupCredits, 0);
    DB.config.signupCredits = 150;
    assert.equal((await api.acceptSellerConsent(body)).signupCredits, 0);
    assert.equal(DB.account.credits, start);
  } finally { DB.config.signupCredits = original; }
});

test('가입 크레딧 원장은 화면에 사용자가 읽는 이름으로 표시한다', () => {
  const client = new QueryClient();
  client.setQueryData(['library'], []);
  client.setQueryData(['creditHistory'], [{
    id: 'signup', actionKey: 'grant_signup', delta: 150, createdAt: '2026-10-06T03:00:00Z',
  }]);
  try {
    const html = renderToStaticMarkup(React.createElement(QueryClientProvider, { client }, React.createElement(CreditsHistory)));
    assert.match(html, /가입 크레딧/);
    assert.match(html, /\+150/);
    assert.doesNotMatch(html, /grant_signup|환불 요청/);
  } finally { client.clear(); }
});

test('목 기본 계정은 충전 보너스가 꺼졌다고 응답한다', async () => {
  assert.equal((await api.getAccount()).topupBonusEnabled, false);
});

test('목 토스 결제는 주문 시점 보너스와 기본 수량을 구분하고 총량을 지급한다', async () => {
  const original = { ...DB.account };
  try {
    for (const [plan, bonus, total] of [['seller', 25, 525], ['pro', 50, 550], ['free', 0, 500]]) {
      DB.account.plan = plan;
      DB.account.topupBonusEnabled = true;
      const start = DB.account.credits;
      const order = await api.createTossCheckout('topup_500');
      assert.equal(order.credits, 500);
      assert.equal(order.bonusCredits, bonus);
      assert.equal(order.totalCredits, total);
      assert.equal(order.planAtOrder, plan);
      assert.equal(order.bonusPercent, plan === 'seller' ? 5 : plan === 'pro' ? 10 : 0);
      DB.account.plan = 'starter';
      DB.account.topupBonusEnabled = false;
      const result = await api.confirmTossPayment({ orderId: order.orderId, amount: order.amount });
      const { idempotent, ...grant } = result;
      assert.deepEqual(grant, { credits: total, baseCredits: 500, bonusCredits: bonus, totalCredits: total, available: start + total });
      assert.notEqual(idempotent, true);
      DB.account.credits -= 19;
      const repeated = await api.confirmTossPayment({ orderId: order.orderId, amount: order.amount });
      assert.equal(repeated.idempotent, true);
      assert.equal(repeated.credits, total);
      assert.equal(repeated.available, start + total - 19);
      assert.equal(DB.account.credits, start + total - 19);
    }
  } finally { Object.assign(DB.account, original); }
});

test('목 토스 주문은 보너스가 켜지지 않은 Pro도 기본 수량만 저장한다', async () => {
  const original = { ...DB.account };
  try {
    for (const enabled of [undefined, false, 'true']) {
      DB.account.plan = 'pro';
      if (enabled === undefined) delete DB.account.topupBonusEnabled;
      else DB.account.topupBonusEnabled = enabled;
      const order = await api.createTossCheckout('topup_500');
      assert.equal(order.bonusCredits, 0);
      assert.equal(order.totalCredits, 500);
      assert.equal(order.bonusPercent, 0);
    }
  } finally { Object.assign(DB.account, original); }
});

test('목 토스 승인은 새로 읽은 지급 완료 주문을 재지급하지 않고 이전 주문 총량도 복원한다', async t => {
  const previousStorage = globalThis.sessionStorage;
  const start = DB.account.credits;
  let saved;
  globalThis.sessionStorage = {
    getItem: () => JSON.stringify(saved),
    setItem: (_key, value) => { saved = JSON.parse(value); },
  };
  t.after(() => { globalThis.sessionStorage = previousStorage; DB.account.credits = start; });
  saved = {
    orderId: 'paid-from-reload', amount: 5500, credits: 100, bonusCredits: 5, status: 'paid',
    result: { credits: 105, baseCredits: 100, bonusCredits: 5, totalCredits: 105 },
  };
  const paid = await api.confirmTossPayment({ orderId: saved.orderId, amount: saved.amount });
  assert.equal(paid.idempotent, true);
  assert.equal(paid.available, start);
  assert.equal(DB.account.credits, start);
  saved = { orderId: 'legacy-pending', amount: 5500, credits: 100, bonusCredits: 5 };
  const legacy = await api.confirmTossPayment({ orderId: saved.orderId, amount: saved.amount });
  assert.equal(legacy.credits, 105);
  assert.equal(legacy.baseCredits, 100);
  assert.equal(DB.account.credits, start + 105);
  assert.equal(saved.status, 'paid');
  assert.equal(saved.result.totalCredits, 105);
});
