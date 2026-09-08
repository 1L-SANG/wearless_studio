import assert from 'node:assert/strict';
import test from 'node:test';
import { resolve } from 'node:path';
import { createServer } from 'vite';

const active = {
  status: 'awaiting_confirm',
  cuts: [{ id: 'close', kind: 'closeup' }, { id: 'full', kind: 'fullbody' }],
  profile: { displayName: '테스트 모델', license: { allowedUse: [], validDays: 365 } },
};

// Exercise the real confirmation component, including its preview children.
// Only hooks, router, API and visual primitives are replaced.
async function harness(t, profile) {
  const key = `__confirmTest${Math.random().toString(36).slice(2)}`;
  const runtime = {
    states: ['ready', { ...active, profile }, { closeupCutId: 'close', fullbodyCutId: 'full' }, true, false],
    cursor: 0, reads: 0, writes: 0,
    result: active,
    confirm: async () => {},
  };
  globalThis[key] = runtime;
  const r = `globalThis[${JSON.stringify(key)}]`;
  const root = new URL('../..', import.meta.url).pathname;
  const server = await createServer({
    configFile: false, root, logLevel: 'silent',
    server: { middlewareMode: true, hmr: false }, ssr: { noExternal: true },
    esbuild: { jsx: 'automatic' },
    plugins: [{
      name: 'confirmation-unavailable-test', enforce: 'pre',
      resolveId(id) {
        if (id === 'react') return '\0confirm-react';
        if (id.startsWith('react/jsx-')) return '\0confirm-jsx';
        if (id === 'react-router-dom') return '\0confirm-router';
        if (id === '@/components/ui.jsx') return '\0confirm-ui';
        if (id === '@/lib/api/facemarket.js') return '\0confirm-api';
        if (id.endsWith('.module.css')) return '\0confirm-css';
        if (id.startsWith('@/')) return resolve(root, 'src', id.slice(2));
      },
      load(id) {
        if (id === '\0confirm-react') return `
          export const useState = initial => {
            const r=${r}, i=r.cursor++;
            if (!(i in r.states)) r.states[i]=initial;
            return [r.states[i], v => r.states[i]=typeof v==='function'?v(r.states[i]):v];
          };
          export const useEffect=()=>{};
          export const useCallback=f=>f;`;
        if (id === '\0confirm-jsx') return 'export const jsx=(type,props)=>({type,props});export const jsxs=jsx;export const jsxDEV=jsx;';
        if (id === '\0confirm-router') return 'export const useNavigate=()=>()=>{};';
        if (id === '\0confirm-ui') return "export const Button='Button',ErrorState='ErrorState',Icon='Icon';export const useToast=()=>({push(){}});";
        if (id === '\0confirm-css') return 'export default {};';
        if (id === '\0confirm-api') return `
          export const getMyModelTestCuts=async()=>{${r}.reads++;return ${r}.result;};
          export const confirmMyModelTestCuts=async()=>{${r}.writes++;return ${r}.confirm();};
          export const fetchMyModelTestCutUrl=async()=>'';
          export const requestMyModelTestCutRedo=async()=>{};`;
      },
    }],
  });
  const { ModelConfirm } = await server.ssrLoadModule('/src/features/model/ModelConfirm.jsx');
  const expand = node => {
    if (!node || typeof node !== 'object') return [];
    if (Array.isArray(node)) return node.flatMap(expand);
    if (typeof node.type === 'function') return expand(node.type(node.props));
    return [node, ...expand(node.props?.children)];
  };
  t.after(async () => { await server.close(); delete globalThis[key]; });
  return {
    runtime,
    render() { runtime.cursor = 0; return expand(ModelConfirm()); },
  };
}

test('missing active license shows a retry notice, hides publication, and recovers after refresh', async t => {
  const h = await harness(t, null);
  const nodes = h.render();
  const notice = nodes.find(node => node.type === 'ErrorState');
  assert.match(notice.props.title, /라이선스/);
  assert.equal(nodes.some(node => node.props?.type === 'checkbox'), false);
  assert.equal(nodes.some(node => node.type === 'Button'), false);
  assert.equal(h.runtime.writes, 0);
  await notice.props.onRetry();
  const recovered = h.render();
  assert.equal(h.runtime.reads, 1);
  assert.equal(recovered.some(node => node.type === 'ErrorState'), false);
  assert.equal(recovered.find(node => node.props?.type === 'checkbox').props.checked, false);
});

test('license expiration during confirmation reloads into the unavailable notice', async t => {
  const h = await harness(t, active.profile);
  h.runtime.result = { ...active, profile: null };
  h.runtime.confirm = async () => { throw Object.assign(new Error('라이선스 만료'), { code: 'license_inactive' }); };
  const button = h.render().find(node => node.type === 'Button' && node.props.variant === 'primary');
  assert.equal(button.props.disabled, false);
  await button.props.onClick();
  assert.equal(h.runtime.writes, 1);
  assert.equal(h.runtime.reads, 1);
  assert.ok(h.render().find(node => node.type === 'ErrorState'));
});
