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

async function harness(api, { confirm = true } = {}) {
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
        export const adminRevealPayoutAccount=(...args)=>api.adminRevealPayoutAccount(...args);`;
    }}],
  });
  const oldWindow = globalThis.window; const oldNavigator = globalThis.navigator;
  globalThis.window = { confirm: () => runtime.confirm, setTimeout: (fn, ms) => { runtime.timers.push({ fn, ms }); return runtime.timers.length; }, clearTimeout: id => runtime.cleared.push(id) };
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { clipboard: { writeText: async value => runtime.copied.push(value) } } });
  const module = await server.ssrLoadModule('/src/features/admin/AdminPayoutStatements.jsx');
  const render = () => { runtime.index = 0; runtime.effects = []; const tree = module.AdminPayoutStatements(); runtime.effects.forEach(effect => effect()); return tree; };
  return { runtime, render, close: async () => { runtime.cleanups.forEach(cleanup => cleanup?.()); await server.close(); delete globalThis[key]; globalThis.window = oldWindow; Object.defineProperty(globalThis, 'navigator', { configurable: true, value: oldNavigator }); } };
}

test('지급 상태 변경은 중복 클릭을 막고 서버가 돌려준 전체 행만 교체해요', async () => {
  const request = deferred(); const calls = [];
  const updated = { ...row, status: 'paid', paidAt: '2026-09-10T01:00:00Z' };
  const h = await harness({ adminListPayoutStatements: async () => ({ items: [row] }),
    adminSetPayoutStatementStatus: (...args) => { calls.push(args); return request.promise; }, adminRevealPayoutAccount: async () => ({}) });
  try {
    let tree = h.render(); await flush(); tree = h.render();
    const pending = button(tree, '지급 완료').props.onClick(); button(h.render(), '처리 중...').props.onClick();
    assert.deepEqual(calls, [['model-1', '2026-08', 'paid', undefined]]);
    request.resolve(updated); await pending; await flush(); tree = h.render();
    assert.match(text(tree), /지급 완료/); assert.ok(button(tree, '예정으로 되돌리기'));
  } finally { await h.close(); }
});

test('계좌 공개는 확인 뒤 한 행만 보여주고 60초 뒤 다시 마스킹해요', async () => {
  const fullAccount = ['110', '123', '456', '789'].join('');
  const request = deferred(); let calls = 0;
  const h = await harness({ adminListPayoutStatements: async () => ({ items: [row] }), adminSetPayoutStatementStatus: async () => row,
    adminRevealPayoutAccount: async () => { calls += 1; return request.promise; } });
  try {
    let tree = h.render(); await flush(); tree = h.render(); const pending = button(tree, '계좌 보기').props.onClick(); button(h.render(), '계좌 보기').props.onClick();
    assert.equal(calls, 1);
    request.resolve({ bankCode: 'shinhan', bankName: '신한은행', holderName: '김서연', accountNumber: fullAccount, accountMasked: '***-****-6789', updatedAt: '2026-09-01T00:00:00Z' });
    await pending; await flush(); tree = h.render();
    assert.ok(text(tree).includes(fullAccount)); assert.equal(h.runtime.timers[0].ms, 60000);
    await button(tree, '복사').props.onClick(); assert.deepEqual(h.runtime.copied, [fullAccount]);
    h.runtime.timers[0].fn(); tree = h.render(); assert.equal(text(tree).includes(fullAccount), false); assert.match(text(tree), /\*\*\*-\*\*\*\*-6789/);
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
    assert.deepEqual(h.runtime.calls, [
      { path: '/v1/facemarket/payout-statements', options: undefined },
      { path: '/v1/facemarket/model/publications/pub%2F1/preview-url', options: { suppressErrorLog: true } },
      { path: '/v1/facemarket/admin/payout-statements?month=2026-08', options: undefined },
      { path: '/v1/facemarket/admin/payout-statements/model%2F1/2026-08/status', options: { method: 'POST', body: { status: 'held', note: '확인 중' } } },
      { path: '/v1/facemarket/admin/models/model%2F1/payout-account', options: undefined },
    ]);
  } finally { await h.close(); }
});
