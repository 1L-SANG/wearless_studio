import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { findTree } from './helpers/facemarketHarness.mjs';
import { adminUsageReportsHarness } from './helpers/adminUsageReportsHarness.mjs';

const flush = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; let reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const text = node => node == null || typeof node === 'boolean' ? '' : Array.isArray(node) ? node.map(text).join(' ') : typeof node === 'object' ? text(node.props?.children) : String(node);
const button = (tree, label) => findTree(tree, node => node.type === 'button' && text(node) === label);

const row = {
  modelId: 'model-1', modelName: '김서연', periodMonth: '2026-08', amount: 1407000,
  count: 201, status: 'scheduled', scheduledFor: '2026-09-10', paidAt: null, open: false,
  bankName: '신한은행', accountMasked: '***-****-6789', holderName: '김서연',
};

async function harness(api, { confirm = true, storage = new Map() } = {}) {
  const key = `__adminPayout${Math.random().toString(36).slice(2)}`;
  const runtime = { api, slots: [], effects: [], cleanups: [], index: 0, confirm, copied: [], timers: [], cleared: [] };
  globalThis[key] = runtime;
  const access = `globalThis[${JSON.stringify(key)}]`;
  const server = await createServer({ configFile: false, logLevel: 'silent', root: new URL('../..', import.meta.url).pathname,
    server: { middlewareMode: true }, ssr: { noExternal: true }, esbuild: { jsx: 'automatic' }, appType: 'custom',
    plugins: [{ name: 'admin-payout-test', enforce: 'pre', resolveId(id) {
      if (id === 'react') return '\0p-react';
      if (['react/jsx-runtime', 'react/jsx-dev-runtime'].includes(id)) return '\0p-jsx';
      if (id === '@/lib/api/facemarket.js') return '\0p-api';
      if (id.startsWith('@/')) return new URL(`../../src/${id.slice(2)}`, import.meta.url).pathname;
    }, load(id) {
      if (id === '\0p-jsx') return `export const jsx=(type,props,key)=>typeof type==='function'?{...type(props),key}:{type,props,key}; export const jsxs=jsx; export const jsxDEV=jsx;`;
      if (id === '\0p-react') return `const r=${access}; const same=(a,b)=>a&&b&&a.length===b.length&&b.every((v,i)=>Object.is(v,a[i]));
        export const useState=initial=>{const i=r.index++;if(!(i in r.slots))r.slots[i]=typeof initial==='function'?initial():initial;return[r.slots[i],v=>{r.slots[i]=typeof v==='function'?v(r.slots[i]):v;}];};
        export const useRef=initial=>{const i=r.index++;return r.slots[i]??={current:initial};};
        export const useCallback=(fn,deps)=>{const i=r.index++;if(!same(r.slots[i]?.deps,deps))r.slots[i]={fn,deps};return r.slots[i].fn;};
        export const useEffect=(fn,deps)=>{const i=r.index++;if(!same(r.slots[i],deps)){r.slots[i]=deps;r.effects.push(()=>{r.cleanups[i]?.();r.cleanups[i]=fn();});}};`;
      if (id === '\0p-api') return `const api=${access}.api;
        export const adminListPayoutStatements=(...args)=>api.adminListPayoutStatements(...args);
        export const adminSetPayoutStatementStatus=(...args)=>api.adminSetPayoutStatementStatus(...args);
        export const adminConfirmPayoutStatement=(...args)=>api.adminConfirmPayoutStatement(...args);
        export const adminAdvancePayoutConfirmation=(...args)=>api.adminAdvancePayoutConfirmation(...args);
        export const adminRevealPayoutConfirmation=(...args)=>api.adminRevealPayoutConfirmation(...args);
        export const adminRevealPayoutAccount=(...args)=>api.adminRevealPayoutAccount(...args);`;
    }}],
  });
  const oldWindow = globalThis.window; const oldNavigator = globalThis.navigator;
  globalThis.window = { sessionStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) }, confirm: () => runtime.confirm, setTimeout: (fn, ms) => { runtime.timers.push({ fn, ms }); return runtime.timers.length; }, clearTimeout: id => runtime.cleared.push(id) };
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { clipboard: { writeText: async value => runtime.copied.push(value) } } });
  const module = await server.ssrLoadModule('/src/features/admin/AdminPayoutStatements.jsx');
  const render = () => { runtime.index = 0; runtime.effects = []; const tree = module.AdminPayoutStatements(); runtime.effects.forEach(effect => effect()); return tree; };
  return { runtime, render, close: async () => { runtime.cleanups.forEach(cleanup => cleanup?.()); await server.close(); delete globalThis[key]; globalThis.window = oldWindow; Object.defineProperty(globalThis, 'navigator', { configurable: true, value: oldNavigator }); } };
}

test('지급 확인은 중복 클릭을 막고 송금 시작 전에는 완료 버튼을 제공하지 않아요', async () => {
  const request = deferred(); const calls = [];
  let listed = row;
  const confirmation = { id: 'confirmation-1', modelId: row.modelId, periodMonth: row.periodMonth, status: 'prepared', amount: row.amount, count: row.count, canManage: true, bankName: row.bankName, holderName: row.holderName, accountMasked: row.accountMasked };
  const h = await harness({ adminListPayoutStatements: async () => ({ items: [listed] }),
    adminConfirmPayoutStatement: (...args) => { calls.push(args); return request.promise; }, adminRevealPayoutAccount: async () => ({}) });
  try {
    let tree = h.render(); await flush(); tree = h.render();
    assert.equal(button(tree, '지급 완료 기록'), null);
    assert.equal(button(tree, '계좌 보기'), null);
    const pending = button(tree, '지급 확인').props.onClick(); button(h.render(), '지급 확인').props.onClick();
    assert.equal(calls.length, 1); assert.deepEqual(calls[0].slice(0, 2), ['model-1', '2026-08']);
    assert.match(calls[0][2], /^[0-9a-f-]{36}$/);
    listed = { ...row, status: 'processing', unpaidAmount: 0, unpaidCount: 0, confirmations: [confirmation] };
    request.resolve(confirmation); await pending; await flush(); tree = h.render();
    assert.ok(button(tree, '송금 시작')); assert.equal(button(tree, '지급 완료 기록'), null);
    assert.equal(button(tree, '예정으로 되돌리기'), null);
  } finally { await h.close(); }
});

test('확정 계좌 공개는 전체 은행·예금주·번호를 함께 보여주고 60초 뒤 다시 마스킹해요', async () => {
  const fullAccount = ['110', '123', '456', '789'].join('');
  const request = deferred(); let calls = 0;
  const confirmation = { id: 'confirmation-1', status: 'transfer_started', amount: row.amount, count: row.count, canManage: true, bankName: row.bankName, holderName: row.holderName, accountMasked: row.accountMasked };
  const h = await harness({ adminListPayoutStatements: async () => ({ items: [{ ...row, confirmations: [confirmation] }] }), adminSetPayoutStatementStatus: async () => row,
    adminRevealPayoutConfirmation: async () => { calls += 1; return request.promise; } });
  try {
    let tree = h.render(); await flush(); tree = h.render(); const pending = button(tree, '계좌 보기').props.onClick(); button(h.render(), '계좌 보기').props.onClick();
    assert.equal(calls, 1);
    request.resolve({ bankCode: 'kb', bankName: '국민은행', holderName: '새 예금주', accountNumber: fullAccount, accountMasked: '***-****-6789', updatedAt: '2026-09-01T00:00:00Z' });
    await pending; await flush(); tree = h.render();
    assert.ok(text(tree).includes(fullAccount)); assert.equal(h.runtime.timers[0].ms, 60000);
    assert.match(text(tree), /국민은행.*새 예금주/);
    await button(tree, '복사').props.onClick(); assert.deepEqual(h.runtime.copied, [fullAccount]);
    h.runtime.timers[0].fn(); tree = h.render(); assert.equal(text(tree).includes(fullAccount), false); assert.match(text(tree), /\*\*\*-\*\*\*\*-6789/);
  } finally { await h.close(); }
});

test('확인 응답을 잃고 화면을 다시 열어도 동일 UUID로 복구해요', async () => {
  const calls = []; const storage = new Map();
  const api = { adminListPayoutStatements: async () => ({ viewerId: 'admin-1', items: [row] }),
    adminConfirmPayoutStatement: async (...args) => { calls.push(args); throw new Error('응답 유실'); } };
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const h = await harness(api, { storage });
    try {
      h.render(); await flush(); const tree = h.render();
      await button(tree, '지급 확인').props.onClick();
      assert.match(text(h.render()), /다시 송금하지 마세요/);
    } finally { await h.close(); }
  }
  assert.equal(calls.length, 2); assert.equal(calls[0][2], calls[1][2]);
  assert.equal(storage.size, 1);
  assert.ok([...storage.keys()][0].includes('admin-1:model-1:2026-08'));
  assert.match([...storage.values()][0], /^[0-9a-f-]{36}$/);
});

test('송금 시작 후에는 취소와 재송금 버튼 없이 원래 금액 완료만 기록해요', async () => {
  const calls = [];
  const confirmation = { id: 'confirmation-1', status: 'transfer_started', amount: 7000, count: 1, canManage: true, bankName: '국민은행', holderName: '원래 예금주', accountMasked: '***-****-1234' };
  let listed = { ...row, unpaidAmount: 9000, unpaidCount: 1, confirmations: [confirmation] };
  const h = await harness({ adminListPayoutStatements: async () => ({ items: [listed] }),
    adminAdvancePayoutConfirmation: async (...args) => { calls.push(args); listed = { ...listed, confirmations: [{ ...confirmation, status: 'paid', paidAt: '2026-09-12T15:30:00Z' }] }; return listed.confirmations[0]; } });
  try {
    h.render(); await flush(); let tree = h.render();
    assert.equal(button(tree, '송금 시작'), null); assert.equal(button(tree, '확인 취소'), null);
    assert.match(text(tree), /9,000원/); assert.match(text(tree), /7,000원/);
    await button(tree, '지급 완료 기록').props.onClick(); tree = h.render();
    assert.deepEqual(calls, [['confirmation-1', 'paid']]);
    assert.match(text(tree), /2026-09-13/); assert.equal(button(tree, '예정으로 되돌리기'), null);
  } finally { await h.close(); }
});

test('진행 중인 지급은 변경된 현재 계좌 대신 확인 ID의 저장 계좌만 공개해요', async () => {
  const calls = [];
  const confirmation = { id: 'confirmation-1', status: 'transfer_started', amount: 7000, count: 1, canManage: true, bankName: '국민은행', holderName: '원래 예금주', accountMasked: '***-****-1234' };
  const h = await harness({ adminListPayoutStatements: async () => ({ items: [{ ...row, holderName: '바뀐 예금주', confirmations: [confirmation] }] }),
    adminRevealPayoutAccount: () => assert.fail('live account must not be revealed during transfer'),
    adminRevealPayoutConfirmation: async id => { calls.push(id); return { bankName: '국민은행', holderName: '원래 예금주', accountNumber: '000000001234' }; } });
  try {
    h.render(); await flush(); let tree = h.render();
    await button(tree, '계좌 보기').props.onClick(); tree = h.render();
    assert.deepEqual(calls, ['confirmation-1']);
    assert.match(text(tree), /국민은행.*000000001234.*원래 예금주/);
  } finally { await h.close(); }
});

test('월을 바꾼 뒤 늦게 온 이전 목록 응답은 현재 달을 덮지 않아요', async () => {
  const old = deferred(); let calls = 0;
  const h = await harness({ adminListPayoutStatements: ({ month }) => ++calls === 1 ? old.promise : Promise.resolve({ items: [{ ...row, periodMonth: month, modelName: '새 달 모델' }] }),
    adminSetPayoutStatementStatus: async () => row, adminRevealPayoutAccount: async () => ({}) });
  try {
    let tree = h.render(); const input = findTree(tree, node => node.type === 'input' && node.props.type === 'month');
    input.props.onChange({ target: { value: '2026-07' } }); tree = h.render(); await flush(); tree = h.render(); assert.match(text(tree), /새 달 모델/);
    old.resolve({ items: [row] }); await flush(); assert.match(text(h.render()), /새 달 모델/);
  } finally { await h.close(); }
});

test('지급 명세 API는 월과 식별자를 인코딩하고 상태 본문을 보냅니다', async () => {
  const h = await adminUsageReportsHarness();
  try {
    const api = await h.api();
    await api.getPayoutStatements();
    await api.getPublicationPreviewUrl('pub/1');
    await api.adminListPayoutStatements({ month: '2026-08' });
    await api.adminSetPayoutStatementStatus('model/1', '2026-08', 'held', '확인 중');
    await api.adminRevealPayoutAccount('model/1');
    await api.adminConfirmPayoutStatement('model/1', '2026-08', 'confirmation-1');
    await api.adminAdvancePayoutConfirmation('confirmation/1', 'start');
    await api.adminRevealPayoutConfirmation('confirmation/1');
    await api.adminSetPayoutStatementStatus('model/1', '2026-08', 'held', undefined, 'confirmation-1');
    assert.deepEqual(h.runtime.calls, [
      { path: '/v1/facemarket/payout-statements', options: undefined },
      { path: '/v1/facemarket/model/publications/pub%2F1/preview-url', options: { suppressErrorLog: true } },
      { path: '/v1/facemarket/admin/payout-statements?month=2026-08', options: undefined },
      { path: '/v1/facemarket/admin/payout-statements/model%2F1/2026-08/status', options: { method: 'POST', body: { status: 'held', note: '확인 중' } } },
      { path: '/v1/facemarket/admin/models/model%2F1/payout-account', options: undefined },
      { path: '/v1/facemarket/admin/payout-statements/model%2F1/2026-08/confirm', options: { method: 'POST', body: { confirmationId: 'confirmation-1' } } },
      { path: '/v1/facemarket/admin/payout-confirmations/confirmation%2F1/start', options: { method: 'POST' } },
      { path: '/v1/facemarket/admin/payout-confirmations/confirmation%2F1/account', options: undefined },
      { path: '/v1/facemarket/admin/payout-statements/model%2F1/2026-08/status', options: { method: 'POST', body: { status: 'held', expectedConfirmationId: 'confirmation-1' } } },
    ]);
  } finally { await h.close(); }
});
