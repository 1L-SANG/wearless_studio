import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';

const payoutDataUrl = new URL('../../src/features/facemarket-landing/payoutData.js', import.meta.url);

async function loadPayoutData() {
  assert.ok(existsSync(payoutDataUrl), '정산 계산 모듈이 필요합니다');
  return import(payoutDataUrl);
}

test('정산일 당일은 오늘을, 정산일이 지난 뒤에는 다음 달 10일을 안내한다', async () => {
  const { nextSettlement } = await loadPayoutData();
  assert.deepEqual(
    nextSettlement(new Date('2026-09-10T03:00:00Z')),
    { year: 2026, month: 9, day: 10 },
  );
  assert.deepEqual(
    nextSettlement(new Date('2026-09-11T03:00:00Z')),
    { year: 2026, month: 10, day: 10 },
  );
  assert.deepEqual(
    nextSettlement(new Date('2026-12-11T03:00:00Z')),
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

test('프론트 API는 내 정산 목록과 전체 합계 엔드포인트를 호출한다', () => {
  const source = readFileSync(new URL('../../src/lib/api/facemarket.js', import.meta.url), 'utf8');
  assert.match(source, /function getSettlementSummary\(\)[\s\S]*?http\('\/v1\/facemarket\/settlements\/summary'\)/);
  assert.match(source, /function listSettlements\(\)[\s\S]*?http\('\/v1\/facemarket\/settlements'\)/);
});


import { loadEarningsHarness, findTree } from './helpers/mypageHarness.mjs';

test('옛 정산 페이지는 마이페이지 수익 위치로 이동해요', async () => {
  const harness = await loadEarningsHarness();
  try {
    const { PayoutPage } = await harness.server.ssrLoadModule('/src/features/facemarket-landing/pages/PayoutPage.jsx');
    const tree = PayoutPage();
    assert.equal(tree.type, 'Navigate');
    assert.equal(tree.props.to, '/status#earnings');
    assert.equal(tree.props.replace, true);
  } finally { await harness.close(); }
});

test('수익은 최근 200건보다 큰 서버 전체 합계를 그대로 표시해요', async () => {
  const harness = await loadEarningsHarness();
  try {
    const tree = harness.module.EarningsFigures({ summary: { monthCount: 201, monthAmount: 1407000, totalCount: 300, totalAmount: 2100000 } });
    assert.ok(findTree(tree, node => node.props?.children === '1,407,000원'));
    assert.ok(findTree(tree, node => Array.isArray(node.props?.children) && node.props.children.includes(300)));
  } finally { await harness.close(); }
});

test('수익 조회 실패는 수익 영역에서만 재시도하고 실제 합계를 다시 불러와요', async () => {
  let calls = 0;
  const harness = await loadEarningsHarness({ getSettlementSummary: async () => {
    calls += 1;
    if (calls === 1) throw new Error('offline');
    return { monthCount: 0, monthAmount: 0, totalCount: 0, totalAmount: 0 };
  } });
  try {
    let tree = await harness.load();
    const retry = findTree(tree, node => node.type === 'button' && node.props.children === '다시 시도');
    assert.ok(retry);
    assert.equal(findTree(tree, node => node.type?.name === 'EarningsFigures'), null);
    retry.props.onClick();
    tree = await harness.load();
    assert.equal(calls, 2);
    assert.ok(findTree(tree, node => node.type?.name === 'EarningsFigures'));
  } finally { await harness.close(); }
});

test('처음에는 사용 기록 5건을 보이고 전체 보기로 받은 기록을 펼쳐요', async () => {
  const harness = await loadEarningsHarness({ listSettlements: async () => Array.from({length: 8}, (_, i) => ({
    id: `s${i}`, paymentId: `p${i}`, createdAt: '2026-09-01T00:00:00Z', productName: `상품${i}`, sellerName: '상점', modelAmount: 10430, reported: i === 0,
  })) });
  try {
    let tree = await harness.load();
    assert.equal(findTree(tree, node => node.type === 'ul').props.children.length, 5);
    assert.ok(findTree(tree, node => node.props?.children === '신고됨'));
    findTree(tree, node => node.props?.children === '전체 보기').props.onClick();
    tree = harness.render();
    assert.equal(findTree(tree, node => node.type === 'ul').props.children.length, 8);
  } finally { await harness.close(); }
});
