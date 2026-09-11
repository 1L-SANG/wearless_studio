import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { adminUsageReportsHarness, findTree, flush, treeText } from './helpers/adminUsageReportsHarness.mjs';

const report = {
  id: 'report-1', settlementId: 'settlement-1', paymentId: 'payment:1', modelId: 'model-1',
  modelName: '신고된 모델', reason: '허용하지 않은 품목이에요', status: 'open',
  createdAt: '2026-09-11T16:30:00Z',
};
const page = (items = [report], nextCursor = null) => ({ items, nextCursor });
const button = (tree, label) => findTree(tree, (node) => node.type === 'button' && treeText(node) === label);
const actionButton = (tree, label) => button(findTree(tree, (node) => node.type === 'tbody'), label);
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
};

test('신고 API는 목록 위치와 PATCH 상태만 올바른 경로에 전달해요', async () => {
  const h = await adminUsageReportsHarness();
  try {
    const api = await h.api();
    assert.equal(typeof api.adminListUsageReports, 'function');
    assert.equal(typeof api.adminUpdateUsageReportStatus, 'function');
    await api.adminListUsageReports({ limit: 20, cursor: 'cursor+/=' });
    const url = new URL(h.runtime.calls[0].path, 'http://test.local');
    assert.equal(url.pathname, '/v1/facemarket/admin/usage-reports');
    assert.equal(url.searchParams.get('limit'), '20');
    assert.equal(url.searchParams.get('cursor'), 'cursor+/=');
    await api.adminUpdateUsageReportStatus('report/1', 'closed');
    assert.deepEqual(h.runtime.calls[1], {
      path: '/v1/facemarket/admin/usage-reports/report%2F1',
      options: { method: 'PATCH', body: { status: 'closed' } },
    });
  } finally { await h.close(); }
});

test('조회 중에는 빈 목록을 단정하지 않고 실패 뒤 다시 불러올 수 있어요', async () => {
  const request = deferred(); let attempts = 0;
  const h = await adminUsageReportsHarness({ adminListUsageReports: () => ++attempts === 1 ? request.promise : Promise.resolve(page([])) });
  try {
    const render = await h.component();
    assert.doesNotMatch(treeText(render()), /이 상태의 신고가 없어요/);
    request.reject(new Error('목록 연결 실패'));
    await flush();
    let tree = render();
    assert.match(treeText(tree), /목록 연결 실패/);
    button(tree, '다시 시도').props.onClick();
    await flush(); tree = render();
    assert.match(treeText(tree), /이 상태의 신고가 없어요/);
    assert.equal(attempts, 2);
  } finally { await h.close(); }
});

test('신고 내용과 한국 접수 시각을 표시하고 이름과 결제번호가 없으면 ID를 보여줘요', async () => {
  const h = await adminUsageReportsHarness({ adminListUsageReports: async () => page([
    report, { ...report, id: 'report-2', modelName: null, paymentId: null, reason: null },
  ]) });
  try {
    const render = await h.component(); render(); await flush();
    const text = treeText(render());
    for (const value of ['신고된 모델', 'payment:1', '허용하지 않은 품목이에요', 'model-1', 'settlement-1', '2026-09-12', '01:30']) assert.ok(text.includes(value), value);
  } finally { await h.close(); }
});

test('저장 중 중복 요청을 막고 실패한 상태 변경을 같은 값으로 재시도해요', async () => {
  const request = deferred(); const changes = []; let status = 'open';
  const h = await adminUsageReportsHarness({
    adminListUsageReports: async () => page([{ ...report, status }]),
    adminUpdateUsageReportStatus: (id, nextStatus) => {
      status = nextStatus;
      changes.push({ id, status });
      return changes.length === 1 ? request.promise : Promise.resolve({ id, status });
    },
  });
  try {
    const render = await h.component(); render(); await flush();
    let tree = render(); const close = actionButton(tree, '처리 완료');
    const pending = close.props.onClick(); close.props.onClick();
    tree = render();
    assert.equal(actionButton(tree, '처리 중...').props.disabled, true);
    assert.equal(changes.length, 1);
    request.reject(new Error('상태 저장 실패')); await pending; await flush();
    tree = render();
    assert.match(treeText(tree), /상태 저장 실패/);
    await button(tree, '변경 다시 시도').props.onClick(); await flush();
    tree = render();
    assert.deepEqual(changes, [{ id: 'report-1', status: 'closed' }, { id: 'report-1', status: 'closed' }]);
    assert.doesNotMatch(treeText(tree), /상태 저장 실패/);
    assert.equal(actionButton(tree, '처리 완료'), null);
    button(tree, '처리 완료').props.onClick(); await flush(); tree = render();
    assert.ok(actionButton(tree, '다시 열기'));
    await actionButton(tree, '다시 열기').props.onClick(); await flush();
    assert.equal(actionButton(render(), '다시 열기'), null);
    assert.deepEqual(changes[2], { id: 'report-1', status: 'open' });
  } finally { await h.close(); }
});

test('더 보기 실패 시 기존 신고를 보존하고 같은 커서부터 다시 불러와요', async () => {
  const calls = []; const pending = deferred();
  const h = await adminUsageReportsHarness({ adminListUsageReports: (options) => {
    calls.push(options);
    if (calls.length === 1) return Promise.resolve(page([report], 'next-page'));
    if (calls.length === 2) return pending.promise;
    return Promise.resolve(page([{ ...report, id: 'report-2', paymentId: 'payment:2' }]));
  } });
  try {
    const render = await h.component(); render(); await flush();
    const more = button(render(), '더 보기');
    const loading = more.props.onClick(); more.props.onClick();
    assert.equal(calls.length, 2);
    pending.reject(new Error('다음 목록 실패')); await loading; await flush();
    let tree = render();
    assert.match(treeText(tree), /payment:1/);
    assert.match(treeText(tree), /다음 목록 실패/);
    await button(tree, '다시 시도').props.onClick(); await flush();
    tree = render();
    assert.match(treeText(tree), /payment:1/); assert.match(treeText(tree), /payment:2/);
    assert.equal(calls[1].cursor, 'next-page'); assert.equal(calls[2].cursor, 'next-page');
    assert.equal(button(tree, '더 보기'), null);
  } finally { await h.close(); }
});

test('필터를 바꾼 뒤에는 이전 조회 응답이 현재 결과를 덮지 않아요', async () => {
  const first = deferred();
  const h = await adminUsageReportsHarness({ adminListUsageReports: ({ status }) => status === 'open'
    ? first.promise : Promise.resolve(page([{ ...report, status: 'closed', paymentId: 'closed-payment' }])) });
  try {
    const render = await h.component(); render();
    button(render(), '처리 완료').props.onClick(); render(); await flush();
    assert.match(treeText(render()), /closed-payment/);
    first.resolve(page([report])); await flush();
    const text = treeText(render());
    assert.match(text, /closed-payment/);
    assert.doesNotMatch(text, /payment:1/);
  } finally { await h.close(); }
});

test('변경 중에는 다른 행과 필터를 잠가 응답이 서로 엇갈리지 않아요', async () => {
  const request = deferred(); const changes = [];
  const h = await adminUsageReportsHarness({
    adminListUsageReports: async () => page([report, { ...report, id: 'report-2' }]),
    adminUpdateUsageReportStatus: (id, status) => { changes.push(id); return request.promise; },
  });
  try {
    const render = await h.component(); render(); await flush();
    const pending = actionButton(render(), '처리 완료').props.onClick();
    const tree = render();
    assert.equal(button(tree, '전체').props.disabled, true);
    const other = actionButton(tree, '처리 완료');
    assert.equal(other.props.disabled, true);
    other.props.onClick();
    assert.equal(changes.length, 1);
    request.resolve({ id: 'report-1', status: 'closed' }); await pending; await flush();
    assert.equal(button(render(), '전체').props.disabled, false);
  } finally { await h.close(); }
});

test('관리자 메뉴와 라우트가 신고 화면에 연결돼요', () => {
  const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
  assert.match(read('src/apps/admin/App.jsx'), /path="usage-reports" element=\{<AdminUsageReports \/>\}/);
  assert.match(read('src/features/admin/AdminShell.jsx'), /to: '\/usage-reports'/);
});
