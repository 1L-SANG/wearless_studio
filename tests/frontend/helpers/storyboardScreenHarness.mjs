import React from 'react';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Real modules and callbacks, including api -> mockAdapter -> mock.saveAnalysis.
// Only the React host/DOM boundary is inert; no browser or network image loads.
export async function storyboardScreenHarness({ pairedFirst = false, lockSource = 'cut', savedStyle = {}, pauseLoad = false, withToast = false, viewportWidth = 1440, hasFrame = false, mallConnections = {} } = {}) {
  const scrollCalls = [];
  const descriptors = new Map(['window', 'document', 'requestAnimationFrame', 'cancelAnimationFrame'].map(k => [k, Object.getOwnPropertyDescriptor(globalThis, k)]));
  const host = () => ({ nodeType: 1, classList: { add() {}, remove() {}, contains: () => false },
    focus() {}, contains: () => true, querySelector: () => null, querySelectorAll: () => [],
    getBoundingClientRect: () => ({ top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 }),
    addEventListener() {}, removeEventListener() {}, style: { setProperty() {}, removeProperty() {} } });
  const document = Object.assign(new EventTarget(), { body: host(), documentElement: host(), visibilityState: 'visible', activeElement: null,
    querySelector: () => null, querySelectorAll: () => [], createElement: host });
  const window = Object.assign(new EventTarget(), { document, innerWidth: viewportWidth, innerHeight: 1000,
    location: { pathname: '/create/storyboard', search: '?mockTemplate=emo', href: 'http://localhost/create/storyboard?mockTemplate=emo' },
    matchMedia: query => ({ matches: query.includes('reduced-motion'), addEventListener() {}, removeEventListener() {} }),
    getComputedStyle: () => ({ getPropertyValue: () => '' }), scrollTo() {}, setTimeout, clearTimeout });
  const vite = await createServer({ configFile: false, plugins: [react()], logLevel: 'silent',
    resolve: { alias: { '@': fileURLToPath(new URL('../../../src', import.meta.url)) } },
    optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, watch: null, hmr: false, ws: false }, appType: 'custom' });
  const { DB } = await vite.ssrLoadModule('/src/mock/db.js');
  const { api } = await vite.ssrLoadModule('/src/lib/api/index.js');
  const { useAppStore } = await vite.ssrLoadModule('/src/store/useAppStore.js');
  const { prefetchStoryboardEntry, invalidateStoryboardEntryPrefetch } = await vite.ssrLoadModule('/src/features/storyboard/storyboardEntryPrefetch.js');
  const { waitForAnalysisEditSave } = await vite.ssrLoadModule('/src/features/product-input/saveRouting.js');
  const { Storyboard } = await vite.ssrLoadModule('/src/features/storyboard/Storyboard.jsx');
  const { ToastProvider, useToast } = await vite.ssrLoadModule('/src/components/ui.jsx');
  const { defaultPageStyle } = await vite.ssrLoadModule('/src/lib/pageTemplates/pageStyle.js');
  const { seedStoryboard } = await vite.ssrLoadModule('/src/lib/pageTemplates/seed.js');
  DB.analysis.pageStyle = { ...defaultPageStyle(), ...savedStyle }; DB.product.colors[0].swatchId = 'blue';
  DB.storyboard = seedStoryboard(DB.product, DB.analysis);
  DB.mallConnections = mallConnections;
  DB.account.name = '공개하면 안 되는 회원 실명';
  if (hasFrame) DB.storyboard = DB.storyboard.map(block => block.pageSectionKind === 'wear' && block.pageSectionUsage === 'styling' ? { ...block, pageDesignId: 'WEAR-STYLING-FRAME-COLUMN-02' } : block);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  if (pairedFirst) DB.storyboard = DB.storyboard.map(block => block.pageSectionKind === 'first' ? { ...block, pageDesignId: 'P01-D09-G' } : block);
  DB.mannequins = lockSource === 'cut' ? [{ id: 'existing-mannequin', previewOnly: false }] : [];
  DB.project.latestJobs = lockSource === 'job' ? { mannequin: { id: 'existing-job', status: 'running' } } : {};
  useAppStore.setState({ projectId: DB.project.id, projectPersisted: true, accountUserId: 'mock-owner', account: DB.account });
  invalidateStoryboardEntryPrefetch(DB.project.id); await prefetchStoryboardEntry(DB.project.id);
  const originalLoad = useAppStore.getState().loadProject, waitingLoads = [];
  if (pauseLoad) useAppStore.setState({ loadProject: () => new Promise(resolve => waitingLoads.push(resolve)) });
  Object.defineProperty(globalThis, 'window', { configurable: true, value: window });
  Object.defineProperty(globalThis, 'document', { configurable: true, value: document });
  globalThis.requestAnimationFrame = fn => setTimeout(fn, 0); globalThis.cancelAnimationFrame = clearTimeout;
  const instances = new Map(), contexts = new Map();
  const toast = { push() {} };
  let notifications;
  function CaptureNotifications() { notifications = useToast(); return null; }
  const currentDispatcher = React.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED.ReactCurrentDispatcher;
  let active, cursor = 0, dirty = true, tree, layout = [], effects = [], closed = false;
  const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const state = initial => {
    const owner = active, index = cursor++;
    if (!(index in owner.slots)) owner.slots[index] = typeof initial === 'function' ? initial() : initial;
    return [owner.slots[index], value => { if (closed) return; const next = typeof value === 'function' ? value(owner.slots[index]) : value;
      if (!Object.is(next, owner.slots[index])) { owner.slots[index] = next; dirty = true; } }];
  };
  const effect = (fn, deps, queue) => {
    const owner = active, index = cursor++;
    if (!same(owner.slots[index], deps)) { owner.slots[index] = deps; queue.push(() => { owner.cleanups[index]?.(); owner.cleanups[index] = fn(); }); }
  };
  const dispatcher = {
    useState: state, useReducer: (fn, initial, init) => { const [value, update] = state(() => init ? init(initial) : initial); return [value, action => update(v => fn(v, action))]; },
    useRef(initial) { const i = cursor++; return active.slots[i] ||= { current: initial }; },
    useMemo(fn, deps) { const i = cursor++; if (!same(active.slots[i]?.deps, deps)) active.slots[i] = { value: fn(), deps }; return active.slots[i].value; },
    useCallback(fn, deps) { return this.useMemo(() => fn, deps); },
    useEffect(fn, deps) { effect(fn, deps, effects); }, useLayoutEffect(fn, deps) { effect(fn, deps, layout); },
    useInsertionEffect(fn, deps) { effect(fn, deps, layout); },
    useContext: context => contexts.has(context) ? contexts.get(context) : context._currentValue,
    useSyncExternalStore(subscribe, get) {
      effect(() => subscribe(() => { if (!closed) dirty = true; }), [subscribe], effects);
      return get();
    }, useDebugValue() {},
    useId() { const i = cursor++; return active.slots[i] ||= `screen-${instances.size}-${i}`; },
    useImperativeHandle(ref, fn, deps) { effect(() => { if (ref) ref.current = fn(); }, deps, layout); },
  };
  const expand = (node, path = 'root') => {
    if (Array.isArray(node)) return node.map((child, i) => expand(child, `${path}.${child?.key ?? i}`));
    if (!node || typeof node !== 'object') return node;
    if (node.$$typeof === Symbol.for('react.portal')) return expand(node.children, `${path}.portal`);
    let type = node.type;
    if (type === React.Fragment) return expand(node.props.children, `${path}.fragment`);
    if (type?.$$typeof === Symbol.for('react.provider')) {
      const context = type._context, previous = contexts.get(context), had = contexts.has(context);
      contexts.set(context, node.props.value); const children = expand(node.props.children, `${path}.provider`);
      if (had) contexts.set(context, previous); else contexts.delete(context); return children;
    }
    if (type?.$$typeof === Symbol.for('react.memo')) type = type.type;
    if (typeof type === 'function' || type?.$$typeof === Symbol.for('react.forward_ref')) {
      let owner = instances.get(path);
      if (!owner || owner.type !== type) { owner?.cleanups.forEach(fn => fn?.()); owner = { type, slots: [], cleanups: [] }; instances.set(path, owner); }
      const previous = currentDispatcher.current; active = owner; cursor = 0; currentDispatcher.current = dispatcher;
      let child;
      try { child = typeof type === 'function' ? type(node.props) : type.render(node.props, node.ref); }
      finally { currentDispatcher.current = previous; }
      return expand(child, `${path}.render`);
    }
    if (node.ref) { const element = host(); element.scrollIntoView = options => scrollCalls.push({ sectionKey: node.props?.['data-section-key'], options }); if (typeof node.ref === 'function') node.ref(element); else node.ref.current = element; }
    return { type, props: { ...node.props, children: expand(node.props?.children, `${path}.children`) } };
  };
  const render = () => {
    let loops = 0;
    do {
      dirty = false; layout = []; effects = [];
      const screen = React.createElement(Storyboard, withToast ? {} : { toastOverride: toast });
      tree = expand(React.createElement(QueryClientProvider, { client: queryClient },
        React.createElement(MemoryRouter, { initialEntries: ['/create/storyboard'] }, withToast
        ? React.createElement(ToastProvider, null, React.createElement(CaptureNotifications), screen) : screen)));
      layout.splice(0).forEach(fn => fn()); effects.splice(0).forEach(fn => fn());
      if (++loops > 30) throw new Error('screen render did not settle');
    } while (dirty);
    return tree;
  };
  const flush = async () => { for (let i = 0; i < 20; i++) { await new Promise(setImmediate); if (dirty) render(); } return tree; };
  return { DB, api, queryClient, store: useAppStore, scrollCalls, render, flush, get tree() { return tree; }, waitForStyle: () => waitForAnalysisEditSave(DB.project.id),
    notify(message = '마네킹컷이 만들어졌어요') { return notifications.push(message, { variant: 'mannequinCompletion', sticky: true }); },
    dismissNotification(id) { notifications.dismiss(id, 'mannequinCompletion'); },
    async releaseLoad() { await originalLoad(); waitingLoads.splice(0).forEach(resolve => resolve()); },
    load: path => vite.ssrLoadModule(path),
    async close() {
      closed = true; instances.forEach(owner => owner.cleanups.forEach(fn => fn?.())); queryClient.clear();
      if (pauseLoad) useAppStore.setState({ loadProject: originalLoad });
      await vite.close();
      for (const [key, value] of descriptors) { if (value) Object.defineProperty(globalThis, key, value); else delete globalThis[key]; }
    } };
}
