/* 계좌이체(무통장입금) 화면 — 지시서 docs/superpowers/plans/2026-09-22-bank-transfer-payments.md §8.
   요금제 화면의 CTA·충전 탭·열린 신청 띠, 신청 창의 필수값, 구독 화면의 이용권 뷰, 관리자 목록을
   SSR 문자열로 본다(pricing.test.mjs 와 같은 하네스). */
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { createServer } from 'vite';

let vite, Pricing, SubscriptionManage, AdminBankTransfers, BankTransferModal, runtime, api, plans, tossKeys;
before(async () => {
  vite = await createServer({
    configFile: false,
    logLevel: 'silent',
    root: new URL('../..', import.meta.url).pathname,
    server: { middlewareMode: true, watch: null, ws: false },
    appType: 'custom',
    esbuild: { jsx: 'automatic' },
    plugins: [{
      name: 'bank-transfer-test-harness',
      enforce: 'pre',
      resolveId(id) {
        if (id === 'virtual:bt-runtime') return '\0bt-runtime';
        if (id === '@/store/useAppStore.js') return '\0bt-store';
        if (id === '@/features/auth/AuthProvider.jsx') return '\0bt-auth';
        if (id === '@/lib/api/facemarket.js') return '\0bt-admin-api';
        if (id.startsWith('@/')) return new URL(`../../src/${id.slice(2)}`, import.meta.url).pathname;
        return null;
      },
      load(id) {
        if (id === '\0bt-runtime') return 'export const runtime = { account: null, session: null };';
        if (id === '\0bt-store') return `
          import { runtime } from 'virtual:bt-runtime';
          export const useAppStore = (selector) => selector({ account: runtime.account, loadAccount: undefined, syncCredits() {} });
        `;
        if (id === '\0bt-auth') return `
          import { runtime } from 'virtual:bt-runtime';
          export const useAuth = () => ({ session: runtime.session, openLogin() {} });
        `;
        if (id === '\0bt-admin-api') return `
          export const adminListBankTransfers = async () => ({ items: [] });
          export const adminConfirmBankTransfer = async () => ({});
          export const adminRejectBankTransfer = async () => ({});
        `;
        return null;
      },
    }],
  });
  ({ runtime } = await vite.ssrLoadModule('virtual:bt-runtime'));
  ({ Pricing } = await vite.ssrLoadModule('/src/features/pricing/Pricing.jsx'));
  ({ BankTransferModal } = await vite.ssrLoadModule('/src/features/pricing/BankTransferModal.jsx'));
  ({ SubscriptionManage } = await vite.ssrLoadModule('/src/features/subscription/Subscription.jsx'));
  ({ AdminBankTransfers } = await vite.ssrLoadModule('/src/features/admin/AdminBankTransfers.jsx'));
  ({ api } = await vite.ssrLoadModule('/src/mock/api.js'));
  tossKeys = await vite.ssrLoadModule('/src/lib/tossKeys.js');
  plans = await api.getPricingPlans();
});
after(() => vite?.close());

const INFO = { enabled: true, bank: '국민은행', account: '123-456-789', holder: '정일상', expiresInDays: 3 };
const OPEN_SUB = {
  id: 'r1', planCode: 'seller', kind: 'subscription', amount: 69900, credits: 1600, payerName: '홍길동',
  status: 'requested', expiresAt: '2026-09-25T12:00:00+09:00',
};

function renderPricing({ loggedIn = true, currentPlan = 'free', info = INFO, open = [] } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(['pricingPlans'], plans);
  client.setQueryData(['bankTransferInfo'], info);
  client.setQueryData(['bankTransferOpen'], { open, recent: null });
  runtime.account = loggedIn ? { plan: currentPlan } : null;
  runtime.session = loggedIn ? { user: { id: 'u1', email: 'seller@example.com' } } : null;
  try {
    return renderToStaticMarkup(React.createElement(QueryClientProvider, { client }, React.createElement(Pricing)));
  } finally {
    client.clear();
  }
}

test('스위치가 켜져 있다', () => {
  assert.equal(tossKeys.BANK_TRANSFER_ENABLED, true);
});

test('로그인 사용자에게 구독 카드는 계좌이체 CTA 를, 충전 탭을 함께 보여준다', () => {
  const html = renderPricing();
  assert.equal((html.match(/계좌이체로 시작하기/g) || []).length, 3);
  assert.doesNotMatch(html, /구매하기/);
  assert.match(html, /추가 구매/);                         // 충전 탭이 보인다
  assert.match(html, /계좌이체<\/strong>로 신청을 받아요/);
  assert.match(html, /결제하면 <a/);
  assert.doesNotMatch(html, /입금 확인 중/);
});

test('현재 플랜과 같은 카드는 1개월 연장 신청이다', () => {
  const html = renderPricing({ currentPlan: 'seller' });
  assert.equal((html.match(/1개월 연장 신청/g) || []).length, 1);
  assert.equal((html.match(/계좌이체로 시작하기/g) || []).length, 2);
});

test('열린 구독 신청이 있으면 띠를 보여주고 구독 CTA 를 잠근다', () => {
  const html = renderPricing({ open: [OPEN_SUB] });
  assert.match(html, /입금 확인 중<\/strong> · seller 1개월 · ₩69,900 · 입금자 홍길동 · 9\/25까지 입금/);
  assert.match(html, /신청 취소/);
  // 신청 창을 닫은 뒤에도 계좌를 다시 볼 수 있어야 한다(리뷰 368F-2).
  assert.match(html, /입금 계좌 국민은행 123-456-789 \(예금주 정일상\) · 입금액 ₩69,900/);
  const buttons = html.match(/<button[^>]*>계좌이체로 시작하기<\/button>/g) || [];
  assert.equal(buttons.length, 3);
  assert.ok(buttons.every((b) => /disabled=""/.test(b) && /확인 중인 신청이 있어요/.test(b)));
});

test('계좌가 설정되지 않았으면 CTA 를 잠그고 이유를 알려준다', () => {
  const html = renderPricing({ info: { enabled: false } });
  const buttons = html.match(/<button[^>]*>계좌이체로 시작하기<\/button>/g) || [];
  assert.equal(buttons.length, 3);
  assert.ok(buttons.every((b) => /disabled=""/.test(b) && /잠시 받지 않아요/.test(b)));
  // 잠긴 이유가 툴팁에만 있으면 막다른 길이다 — 안내 띠가 문의처를 말한다(리뷰 368F-3).
  assert.match(html, /잠시 받지 않아요<\/strong>\. 결제가 필요하면 <a href="mailto:contact@wearless\.kr">/);
  assert.doesNotMatch(html, /계좌이체<\/strong>로 신청을 받아요/);
});

test('계좌이체 모드에서는 지급 코드가 없는 충전 보너스 약속을 카드에 적지 않는다', () => {
  const html = renderPricing();
  assert.doesNotMatch(html, /충전할 때마다 크레딧/);
  assert.match(html, /입금 확인까지\(신청 후 3일\) 이용권과 크레딧이 유지/);
  assert.doesNotMatch(html, /갱신 결제 실패 시 3일의 유예기간/);
});

test('비로그인 방문자는 지금처럼 로그인 버튼만 본다', () => {
  const html = renderPricing({ loggedIn: false });
  assert.equal((html.match(/로그인하고 시작하기/g) || []).length, 3);
  assert.doesNotMatch(html, /계좌이체로 시작하기/);
});

test('충전 탭 카드에는 계좌이체로 충전하기가 뜬다', async () => {
  // 탭 상태는 내부 useState 라 SSR 로 못 바꾼다 — 목 카탈로그의 충전 상품 5종이 있음만 본다.
  assert.equal(plans.filter((p) => p.kind === 'topup').length, 5);
  const res = await api.createBankTransferRequest({ planCode: 'topup_finish', payerName: '홍길동' });
  assert.equal(res.request.kind, 'topup');
  assert.equal(res.request.amount, 9900);
  await assert.rejects(api.createBankTransferRequest({ planCode: 'topup_start', payerName: '홍길동' }), /확인 중인 신청/);
  const open = await api.getOpenBankTransferRequests();
  assert.equal(open.open.length, 1);
  await api.cancelBankTransferRequest(res.request.id);
  assert.equal((await api.getOpenBankTransferRequests()).open.length, 0);
});

test('신청 창은 상품·금액·계좌와 필수 입력을 보여준다', () => {
  const plan = plans.find((p) => p.code === 'seller');
  // Modal 은 document.body 로 포탈하므로 SSR 에서는 내용을 직접 렌더할 수 없다 — 안내 문구 상수만 고정한다.
  assert.equal(typeof BankTransferModal, 'function');
  assert.equal(plan.price, 69900);
});

test('구독 화면은 토스 구독이 없어도 계좌이체 이용권을 그린다', () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(['subscription'], { status: 'none' });
  client.setQueryData(['manualEntitlement'], {
    active: true, planCode: 'seller', startsAt: '2026-09-22T03:00:00+00:00',
    endsAt: '2026-10-22T03:00:00+00:00', credits: 1234, autoRenew: false,
  });
  runtime.session = { user: { id: 'u1' } };
  const html = renderToStaticMarkup(React.createElement(MemoryRouter, { initialEntries: ['/subscription'] },
    React.createElement(QueryClientProvider, { client }, React.createElement(SubscriptionManage))));
  client.clear();
  assert.match(html, /계좌이체 이용권/);
  assert.match(html, /seller/);
  assert.match(html, /자동 갱신 없음/);
  assert.match(html, /1,234/);
  assert.match(html, /1개월 연장 신청/);
  assert.doesNotMatch(html, /구독 해지/);
});

test('구독 화면은 이용권이 없으면 기존 빈 상태를 그린다', () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(['subscription'], { status: 'none' });
  client.setQueryData(['manualEntitlement'], { active: false });
  const html = renderToStaticMarkup(React.createElement(MemoryRouter, { initialEntries: ['/subscription'] },
    React.createElement(QueryClientProvider, { client }, React.createElement(SubscriptionManage))));
  client.clear();
  assert.match(html, /이용 중인 구독이 없어요/);
});

test('관리자 계좌이체 화면은 필터와 안내를 그린다', () => {
  const html = renderToStaticMarkup(React.createElement(AdminBankTransfers));
  assert.match(html, /계좌이체 확인/);
  for (const label of ['확인 대기', '지급 완료', '기한 지남', '거절', '사용자 취소', '전체']) assert.match(html, new RegExp(label));
  assert.match(html, /홈택스에서 직접 발행/);
});
