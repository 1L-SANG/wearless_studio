import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { transformWithEsbuild } from 'vite';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const store = read('src/store/useAppStore.js');
const storageSource = store.slice(store.indexOf("const FLOW_KEY = 'wl_flow'"), store.indexOf('const initialFlow ='));

test('wl_flow persists current user and last use, while legacy flows remain unknown', () => {
  const values = new Map();
  const localStorage = { getItem: (key) => values.get(key), setItem: (key, value) => values.set(key, value) };
  const { loadPersistedFlow, persistFlow } = new Function('localStorage', `const mode = 'http'; const normalizeComposeMode = (v) => v; ${storageSource}; return { loadPersistedFlow, persistFlow };`)(localStorage);
  values.set('wl_flow', JSON.stringify({ projectId: 'old', projectPersisted: true }));
  assert.equal(loadPersistedFlow().userId, undefined);
  assert.equal(loadPersistedFlow().lastUsedAt, undefined);
  let state = { projectId: 'own', projectPersisted: true, resumePath: '/create/storyboard' };
  const methodSource = store.slice(store.indexOf('  markFlowUsed('), store.indexOf('  confirmProductInfo('));
  const markFlowUsed = new Function('set', 'get', 'persistFlow', `return ({ ${methodSource} }).markFlowUsed;`)((next) => { state = { ...state, ...next }; }, () => state, persistFlow);
  markFlowUsed('seller', 1000);
  assert.deepEqual(loadPersistedFlow(), { projectId: 'own', projectPersisted: true, resumePath: '/create/storyboard', userId: 'seller', lastUsedAt: 1000 });
  markFlowUsed('seller', 2000);
  assert.equal(loadPersistedFlow().lastUsedAt, 2000);
  state.projectPersisted = false; markFlowUsed('other', 3000);
  assert.equal(loadPersistedFlow().lastUsedAt, 2000);
});

const ui = read('src/components/ui.jsx');
const toastSource = ui.slice(ui.indexOf('const ToastCtx ='), ui.indexOf('export const useToast')).replace(/^export /gm, '');
const { code } = await transformWithEsbuild(toastSource, 'Toast.jsx', { jsx: 'transform' });
const find = (tree, predicate) => {
  if (!tree || typeof tree !== 'object') return null;
  if (predicate(tree)) return tree;
  return (tree.props?.children || []).flat(Infinity).map((child) => find(child, predicate)).find(Boolean);
};

test('toast action is a keyboard-accessible button that executes and dismisses', () => {
  let list = [], calls = 0, stopped = false;
  const bindings = {
    React: { createElement: (type, props, ...children) => ({ type, props: { ...props, children } }) },
    createContext: () => ({ Provider: 'Provider' }), useCallback: (fn) => fn,
    useState: () => [list, (update) => { list = update(list); }],
    createPortal: (tree) => tree, document: { body: {} }, window: {}, setTimeout() {}, Icon: 'Icon',
  };
  const ToastProvider = new Function(...Object.keys(bindings), `${code}; return ToastProvider;`)(...Object.values(bindings));
  let tree = ToastProvider({ children: null });
  tree.props.value.push('이어서 열었어요', { action: { label: '새로 시작', onClick: () => { calls += 1; } } });
  tree = ToastProvider({ children: null });
  const action = find(tree, (node) => node.type === 'button');
  assert.equal(action.props.children[0], '새로 시작');
  assert.equal(action.props.type, 'button');
  assert.equal(find(tree, (node) => node.props.className === 'toast').props.role, 'status');
  action.props.onClick({ stopPropagation() { stopped = true; } });
  assert.equal(calls, 1);
  assert.equal(stopped, true);
  assert.equal(list[0].exiting, true);
});
