import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';

const payoutDataUrl = new URL('../../src/features/facemarket-landing/payoutData.js', import.meta.url);

async function loadPayoutData() {
  assert.ok(existsSync(payoutDataUrl), '정산 계산 모듈이 필요합니다');
  return import(payoutDataUrl);
}

function findTree(node, predicate) {
  if (!node || typeof node !== 'object') return null;
  if (Array.isArray(node)) {
    for (const child of node) {
      const found = findTree(child, predicate);
      if (found) return found;
    }
    return null;
  }
  if (predicate(node)) return node;
  return findTree(node.props?.children, predicate);
}

async function payoutViewHarness() {
  const { createServer } = await import('vite');
  const server = await createServer({
    configFile: false,
    logLevel: 'silent',
    root: new URL('../..', import.meta.url).pathname,
    server: { middlewareMode: true },
    esbuild: { jsx: 'automatic' },
    plugins: [{
      name: 'facemarket-payout-test-harness',
      enforce: 'pre',
      resolveId(id) {
        if (id === 'react-router-dom') return '\0payout-router';
        if (id === '@/features/auth/AuthProvider.jsx') return '\0payout-auth';
        if (id === '@/components/ui.jsx') return '\0payout-ui';
        if (id === '@/lib/api/facemarket.js') return '\0payout-api';
        if (id.endsWith('LandingShell.jsx')) return '\0payout-shell';
        if (id.endsWith('.module.css')) return '\0payout-css';
        return null;
      },
      load(id) {
        if (id === '\0payout-router') return "export const Link = 'Link';";
        if (id === '\0payout-auth') return 'export const useAuth = () => ({ session: null, loading: false, openLogin() {} });';
        if (id === '\0payout-ui') return "export const ErrorState = 'ErrorState'; export const Icon = 'Icon';";
        if (id === '\0payout-api') return 'export const listLicenses = async () => []; export const listSettlements = async () => [];';
        if (id === '\0payout-shell') return "export const LandingShell = 'LandingShell';";
        if (id === '\0payout-css') return 'export default new Proxy({}, { get: (_, key) => key });';
        return null;
      },
    }],
  });
  return { module: await server.ssrLoadModule('/src/features/facemarket-landing/pages/PayoutPage.jsx'), server };
}

test('정산 요약은 한국 시간 월 경계로 이번 달과 누적 금액을 나눈다', async () => {
  const { summarizeSettlements } = await loadPayoutData();
  const rows = [
    { id: 's1', createdAt: '2026-08-31T15:05:00Z', modelAmount: 7_000 }, // KST 9월 1일
    { id: 's2', createdAt: '2026-09-03T02:00:00Z', modelAmount: 12_000 },
    { id: 's3', createdAt: '2026-08-31T14:59:00Z', modelAmount: 5_000 }, // KST 8월
  ];

  assert.deepEqual(summarizeSettlements(rows, new Date('2026-09-04T03:00:00Z')), {
    monthCount: 2,
    monthAmount: 19_000,
    totalAmount: 24_000,
    nextSettlement: { year: 2026, month: 9, day: 10 },
  });
});

test('정산일 당일은 오늘을, 정산일이 지난 뒤에는 다음 달 10일을 안내한다', async () => {
  const { summarizeSettlements } = await loadPayoutData();
  assert.deepEqual(
    summarizeSettlements([], new Date('2026-09-10T03:00:00Z')).nextSettlement,
    { year: 2026, month: 9, day: 10 },
  );
  assert.deepEqual(
    summarizeSettlements([], new Date('2026-09-11T03:00:00Z')).nextSettlement,
    { year: 2026, month: 10, day: 10 },
  );
  assert.deepEqual(
    summarizeSettlements([], new Date('2026-12-11T03:00:00Z')).nextSettlement,
    { year: 2027, month: 1, day: 10 },
  );
});

test('쇼핑몰·품목·썸네일 열은 응답에 그 필드가 있을 때만 나타난다', async () => {
  const { settlementColumns } = await loadPayoutData();
  const currentServerRows = [{
    id: 's1', createdAt: '2026-09-03T02:00:00Z', modelAmount: 7_000,
    licenseId: 'l1', chainStatus: 'confirmed',
  }];
  assert.deepEqual(settlementColumns(currentServerRows).map((column) => column.key), [
    'date', 'share', 'status',
  ]);

  const enrichedRows = [{
    ...currentServerRows[0], shopName: '오늘의 상점', productName: '린넨 셔츠', thumbnailUrl: '/shirt.webp',
  }];
  assert.deepEqual(settlementColumns(enrichedRows).map((column) => column.key), [
    'date', 'shop', 'item', 'thumbnail', 'share', 'status',
  ]);
  assert.equal(settlementColumns(enrichedRows).some((column) => column.key === 'evidence'), false);

  const billingOnly = [{ ...currentServerRows[0], billingType: 'monthly' }];
  assert.deepEqual(settlementColumns(billingOnly).map((column) => column.key), [
    'date', 'share', 'status',
  ]);
});

test('월정액 행은 유형과 기간을 한 문장으로 표시한다', async () => {
  const { normalizeSettlementRow } = await loadPayoutData();
  const row = normalizeSettlementRow({
    id: 's1',
    createdAt: '2026-09-01T00:00:00Z',
    modelAmount: 25_000,
    billingType: 'monthly',
    periodStart: '2026-09-01T00:00:00Z',
    periodEnd: '2026-09-30T00:00:00Z',
    shopName: '오늘의 상점',
    productName: '월간 카탈로그',
    thumbnailUrl: '/catalog.webp',
    licenseId: 'l1',
  }, [{ id: 'l1', status: 'active', licenseValidUntil: '2027-01-01T00:00:00Z' }], new Date('2026-09-04T00:00:00Z'));

  assert.equal(row.billingLabel, '월정액 · 2026.09.01~2026.09.30');
  assert.equal(row.date, '2026.09.01');
  assert.equal(row.shop, '오늘의 상점');
  assert.equal(row.item, '월간 카탈로그');
  assert.equal(row.thumbnail, '/catalog.webp');
  assert.equal(row.status, '활성');
  assert.equal(row.share, 25_000);
});

test('라이선스가 만료됐으면 정산 행 상태도 만료로 표시한다', async () => {
  const { normalizeSettlementRow } = await loadPayoutData();
  const row = normalizeSettlementRow(
    { id: 's1', createdAt: '2026-09-01T00:00:00Z', modelAmount: 7_000, licenseId: 'l1' },
    [{ id: 'l1', status: 'active', licenseValidUntil: '2026-09-03T00:00:00Z' }],
    new Date('2026-09-04T00:00:00Z'),
  );
  assert.equal(row.status, '만료');

  const omittedRevokedLicense = normalizeSettlementRow(
    { id: 's2', createdAt: '2026-09-01T00:00:00Z', modelAmount: 7_000, licenseId: 'revoked-l1' },
    [],
    new Date('2026-09-04T00:00:00Z'),
  );
  assert.equal(omittedRevokedLicense.status, '만료');
});

test('프론트 API는 내 정산 목록 엔드포인트를 호출한다', () => {
  const source = readFileSync(new URL('../../src/lib/api/facemarket.js', import.meta.url), 'utf8');
  assert.match(source, /function listSettlements\(\)[\s\S]*?http\('\/v1\/facemarket\/settlements'\)/);
});

test('정산 화면은 준비 칩·세 숫자·빈 상태와 가변 열 표를 실제로 렌더한다', async () => {
  const { module, server } = await payoutViewHarness();
  try {
    assert.equal(typeof module.PayoutView, 'function');
    const empty = module.PayoutView({ settlements: [], licenses: [], now: new Date('2026-09-04T00:00:00Z') });
    assert.ok(findTree(empty, (node) => node.props?.children === '지급 준비 중 · 기록은 쌓이고 있어요'));
    assert.ok(findTree(empty, (node) => node.props?.children === '아직 쌓인 정산 기록이 없어요'));

    const enriched = module.PayoutView({
      settlements: [{
        id: 's1', createdAt: '2026-09-03T00:00:00Z', modelAmount: 7_000,
        shopName: '오늘의 상점', productName: '린넨 셔츠', thumbnailUrl: '/shirt.webp',
        licenseId: 'l1',
      }],
      licenses: [{ id: 'l1', status: 'active', licenseValidUntil: '2027-01-01T00:00:00Z' }],
      now: new Date('2026-09-04T00:00:00Z'),
    });
    for (const label of ['날짜', '쇼핑몰', '품목', '썸네일', '내 몫', '상태']) {
      assert.ok(findTree(enriched, (node) => node.type === 'th' && node.props?.children === label), label);
    }
    assert.equal(findTree(enriched, (node) => node.type === 'th' && node.props?.children === '증빙'), null);

    const monthlyWithoutItem = module.PayoutView({
      settlements: [{
        id: 'monthly', createdAt: '2026-09-03T00:00:00Z', modelAmount: 17_500,
        billingType: 'monthly', periodStart: '2026-09-01T00:00:00Z', periodEnd: '2026-09-30T00:00:00Z',
        licenseId: 'l1',
      }],
      licenses: [{ id: 'l1', status: 'active', licenseValidUntil: '2027-01-01T00:00:00Z' }],
      now: new Date('2026-09-04T00:00:00Z'),
    });
    const dateCell = findTree(
      monthlyWithoutItem,
      (node) => node.type?.name === 'TableCell' && node.props?.column?.key === 'date',
    );
    const renderedDate = dateCell.type(dateCell.props);
    assert.ok(findTree(renderedDate, (node) => node.props?.children === '월정액 · 2026.09.01~2026.09.30'));
  } finally {
    await server.close();
  }
});
