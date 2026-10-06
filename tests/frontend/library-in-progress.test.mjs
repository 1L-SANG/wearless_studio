import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToString } from 'react-dom/server';
import { createServer, transformWithEsbuild } from 'vite';
import { createInitialGenerationAttempts, runInitialGenerationAttempt } from '../../src/features/mannequin/initialGenerationAttempts.js';
import { createMannequinGenerationRunner } from '../../src/features/mannequin/generationRunnerCore.js';
import { decorateGenBlocks } from '../../src/lib/editorWaitSkeleton.js';
import * as flowSession from '../../src/lib/flowSession.js';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const memoryStorage = () => {
  const values = new Map();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
};
const snapshot = (id, mannequin = null, detailPage = null) => ({ id, status: 'draft',
  selectedMannequinId: 'saved-cut', composeMode: 'extended', copywriting: false,
  adjustCount: 2, latestJobs: { mannequin, detailPage } });
const steps = ['storyboard', 'mannequin', 'generating', 'failed'];
const labels = ['콘티 단계', '마네킹 단계', '생성 중', '생성 실패'];
const resumeSnapshot = (id) => ({ ...snapshot(id),
  selectedMannequinId: id === 'p-storyboard' ? null : 'saved-cut',
  latestJobs: { mannequin: null, detailPage: id === 'p-generating' ? { id: 'detail', status: 'running' }
    : id === 'p-failed' ? { id: 'detail', status: 'error' } : null },
});
const rows = steps.map((step, i) => ({ id: `p-${step}`, step, title: `상품 ${i}`,
  cover: '/sample.png', updatedAt: new Date(Date.now() - 120000).toISOString() }));
const librarySource = read('src/features/library/Library.jsx');
const { code: libraryCode } = await transformWithEsbuild(librarySource
  .replace(/^import .*;\n/gm, '').replace(/^export /gm, '').replace('default Library;', ''), 'Library.jsx', { jsx: 'transform' });
function findAll(tree, predicate) {
  if (!tree || typeof tree !== 'object') return [];
  return [...(predicate(tree) ? [tree] : []), ...React.Children.toArray(tree.props?.children).flatMap((child) => findAll(child, predicate))];
}
function libraryHarness({ progress = rows, done = [{ id: 'done', title: '완성 상품', status: 'done', updatedAt: rows[0].updatedAt }], mobile = false, loading = false, error = false, getProject = async (id) => resumeSnapshot(id) } = {}) {
  const calls = [];
  const queries = [];
  const cleanups = [], refs = [];
  const state = { userId: 'seller', projectGeneration: 0, adoptProject: (id, options) => calls.push(['adopt', id, options]),
    confirmProductInfo: (id) => calls.push(['confirm', id]), markFlowUsed: (id) => calls.push(['used', id]),
    setResumePath: (path) => calls.push(['resumePath', path]) };
  const useAppStore = (selector) => selector(state);
  useAppStore.getState = () => state;
  const bindings = { React, useAppStore, useRef: (value) => { const ref = { current: value }; refs.push(ref); return ref; }, useState: (value) => [value, () => {}],
    useEffect: (effect) => { const cleanup = effect(); if (cleanup) cleanups.push(cleanup); },
    useNavigate: () => (path) => calls.push(['navigate', path]), useToast: () => ({ push: (msg) => calls.push(['toast', msg]) }),
    useAuth: () => ({ session: { user: { id: 'seller' } } }), isMobileDevice: () => mobile,
    authorizeFlowContinuation: (id, path) => calls.push(['authorize', id, path]),
    api: { async getProject(id) { calls.push(['get', id]); return getProject(id); }, getInProgressLibrary() {}, getLibrary() {} },
    useQuery: (query) => {
      queries.push(query);
      return query.queryKey.length === 1 ? { data: done }
        : { data: progress, isLoading: loading, isError: error, refetch() {} };
    }, Button: 'button', Skeleton: 'div', EmptyState: 'div', ErrorState: 'div',
  };
  const Library = new Function(...Object.keys(bindings), `${libraryCode}; return Library;`)(...Object.values(bindings));
  return { tree: Library(), calls, queries, state, refs, unmount: () => cleanups.forEach((cleanup) => cleanup()) };
}

test('④ 오래된 콘티 카드는 최신 선택과 상세 잡 및 완료 상태로 재개한다', async (t) => {
  for (const [latest, path] of [
    [snapshot('p-storyboard'), '/create/mannequin'],
    [snapshot('p-storyboard', null, { id: 'detail', status: 'running' }), '/editor/p-storyboard'],
    [{ ...snapshot('p-storyboard'), status: 'done' }, '/editor/p-storyboard'],
  ]) await t.test(path + JSON.stringify(latest.latestJobs), async () => {
    const h = libraryHarness({ getProject: async () => latest });
    await findAll(h.tree, (node) => node.props?.role === 'button')[0].props.onClick();
    assert.deepEqual(h.calls.at(-1), ['navigate', path]);
  });
});

for (const change of ['새 제작', '로그아웃', '계정 전환', '세션 계정 전환', '다른 완성본 열기', '언마운트']) {
  test(`⑤ ${change} 뒤의 늦은 재개 응답은 흐름과 경로를 쓰지 않는다`, async () => {
    let resolve;
    const h = libraryHarness({ getProject: () => new Promise((r) => { resolve = r; }) });
    const pending = findAll(h.tree, (node) => node.props?.role === 'button')[0].props.onClick();
    if (change === '새 제작') h.state.projectGeneration++;
    if (change === '로그아웃') h.state.userId = null;
    if (change === '계정 전환') h.state.userId = 'seller-b';
    if (change === '세션 계정 전환') h.refs.find((ref) => ref.current === 'seller').current = 'seller-b';
    if (change === '다른 완성본 열기') findAll(h.tree, (node) => node.props?.className === 'lib-card' && !node.props.role)[0].props.onClick();
    if (change === '언마운트') h.unmount();
    const before = [...h.calls];
    resolve(snapshot('p-storyboard'));
    await pending;
    assert.deepEqual(h.calls, before);
  });
}

test('in-progress adapters preserve the rows and latestJobs contract', async () => {
  const source = read('src/lib/api/httpAdapter.js');
  const paths = [];
  const http = async (path) => { paths.push(path); return path.includes('?') ? rows : snapshot('p', { id: 'm', status: 'running', errorCode: null }); };
  const list = source.slice(source.indexOf('  async getInProgressLibrary'), source.indexOf('  async createProject'));
  const project = source.slice(source.indexOf('  async getProject'), source.indexOf('  // 상품 조회'));
  const adapter = new Function('http', `return ({ ${list} ${project} });`)(http);
  assert.deepEqual(await adapter.getInProgressLibrary(), rows);
  assert.deepEqual((await adapter.getProject('p')).latestJobs, { mannequin: { id: 'm', status: 'running', errorCode: null }, detailPage: null });
  assert.deepEqual(paths, ['/v1/projects?view=in_progress', '/v1/projects/p']);
});

test('empty in-progress rows hide the section while the done list remains visible', () => {
  const h = libraryHarness({ progress: [] });
  assert.equal(findAll(h.tree, (node) => node.type === 'section').length, 0);
  assert.match(renderToString(h.tree), /완성 상품/);
  assert.deepEqual(h.queries.map((query) => query.queryKey), [['library'], ['library', 'in_progress']]);
});
for (const state of ['loading', 'error']) test(`in-progress ${state} does not block completed cards`, () => {
  const h = libraryHarness({ progress: [], loading: state === 'loading', error: state === 'error' });
  assert.match(renderToString(h.tree), /완성 상품/);
});
test('cards show all step chips and relative time, use the existing frame, and cap at 20', () => {
  const h = libraryHarness({ progress: Array.from({ length: 24 }, (_, i) => ({ ...rows[i % 4], id: `p${i}`, title: i === 0 ? '' : `상품 ${i}` })) });
  const html = renderToString(h.tree);
  labels.forEach((label) => assert.ok(html.includes(label)));
  assert.match(html, /새 상품/);
  assert.match(html, /2분 전/);
  assert.equal(findAll(h.tree, (node) => node.props?.role === 'button').length, 20);
  assert.equal(findAll(h.tree, (node) => node.props?.role === 'button').every((node) => node.props.className === 'lib-card'), true);
});
for (const step of steps) test(`phone/tablet ${step} card only shows the PC toast`, async () => {
  const h = libraryHarness({ mobile: true, progress: [rows.find((row) => row.step === step)] });
  await findAll(h.tree, (node) => node.props?.role === 'button')[0].props.onClick();
  assert.deepEqual(h.calls, [['toast', 'PC에서 열어 주세요']]);
});
for (const [step, path] of [['storyboard', '/create/storyboard'], ['mannequin', '/create/mannequin'],
  ['generating', '/editor/p-generating'], ['failed', '/editor/p-failed']]) test(`PC ${step} resumes the confirmed server snapshot at ${path}`, async () => {
  const h = libraryHarness({ progress: [rows.find((row) => row.step === step)] });
  await findAll(h.tree, (node) => node.props?.role === 'button')[0].props.onClick();
  assert.deepEqual(h.calls, [['get', `p-${step}`], ['adopt', `p-${step}`, { serverSnapshot: resumeSnapshot(`p-${step}`) }],
    ['confirm', `p-${step}`], ['authorize', `p-${step}`, path], ['used', 'seller'], ['resumePath', path], ['navigate', path]]);
  assert.equal(h.calls.some((call) => call[1] === '/create/generating'), false);
});
test('failed resume fetch keeps the current flow and does not navigate', async () => {
  const h = libraryHarness({ getProject: async () => { throw new Error('조회 실패'); } });
  await findAll(h.tree, (node) => node.props?.role === 'button')[0].props.onClick();
  assert.deepEqual(h.calls, [['get', 'p-storyboard'], ['toast', '조회 실패']]);
});
test('overlapping resume clicks fetch and adopt only one project', async () => {
  let resolve;
  const h = libraryHarness({ getProject: () => new Promise((r) => { resolve = r; }) });
  const cards = findAll(h.tree, (node) => node.props?.role === 'button');
  const first = cards[0].props.onClick();
  await cards[1].props.onClick();
  resolve(snapshot('p-storyboard'));
  await first;
  assert.equal(h.calls.filter((call) => call[0] === 'get').length, 1);
});

const storeSource = read('src/store/useAppStore.js');
test('two tabs restore their own project and choices before the shared wl_flow', () => {
  const shared = memoryStorage();
  const source = storeSource.slice(storeSource.indexOf("const FLOW_KEY = 'wl_flow'"), storeSource.indexOf('const initialFlow ='));
  const tab = () => new Function('sessionStorage', 'localStorage',
    `const mode='http'; const normalizeComposeMode = (v) => v === 'extended' ? v : 'basic'; ${source}; return {loadPersistedFlow,persistFlow};`)(memoryStorage(), shared);
  const a = tab(), b = tab();
  const first = { projectId: 'a', projectPersisted: true, composeMode: 'extended', copywriting: false, productInfoConfirmed: true };
  const second = { projectId: 'b', projectPersisted: true, composeMode: 'basic' };
  a.persistFlow(first); b.persistFlow(second);
  assert.deepEqual(a.loadPersistedFlow(), first);
  assert.deepEqual(b.loadPersistedFlow(), second);
  assert.deepEqual(tab().loadPersistedFlow(), second);
  a.persistFlow({ projectId: null, projectPersisted: false });
  assert.deepEqual(a.loadPersistedFlow(), { projectId: null, projectPersisted: false });
});

test('M3 no mannequin history keeps automatic first generation and single-flight', async () => {
  const attempts = createInitialGenerationAttempts({ storage: null });
  attempts.seed('p', null);
  let posts = 0, resolve;
  const runner = createMannequinGenerationRunner({ readProgress: () => 0, onJobChange() {},
    generate: (id, options) => runInitialGenerationAttempt(attempts, async () => {
      posts++;
      return new Promise((r) => { resolve = r; });
    }, id, options) });
  const a = runner.request('p'), b = runner.request('p');
  assert.equal(a, b);
  resolve({ data: { cuts: ['new'] } });
  await a;
  assert.equal(posts, 1);
});
for (const status of ['pending', 'running']) test(`M3 ${status} joins the server job without POST after reload`, async () => {
  const storage = memoryStorage();
  createInitialGenerationAttempts({ storage }).seed('p', { id: 'server-job', status });
  const attempts = createInitialGenerationAttempts({ storage });
  let posts = 0, joined;
  await runInitialGenerationAttempt(attempts, async (_id, options) => {
    if (!options.resumeJobId) posts++;
    joined = options.resumeJobId;
    return { data: { cuts: ['existing'] } };
  }, 'p');
  assert.equal(posts, 0);
  assert.equal(joined, 'server-job');
});
test('M3 completed job recovery never claims a new initial generation or swallows dirty edits', async () => {
  const attempts = createInitialGenerationAttempts({ storage: null });
  attempts.seed('p', { id: 'finished-job', status: 'done' });
  let started = 0, progress = 0;
  await runInitialGenerationAttempt(attempts, async (_id, options) => {
    assert.equal(options.resumeJobId, 'finished-job');
    options.onJobStarted?.('finished-job'); options.onProgress?.(100);
    return { data: { cuts: ['old-cut'] } };
  }, 'p', { onJobStarted: () => started++, onProgress: () => progress++ });
  assert.equal(started, 0); assert.equal(progress, 0);
});
for (const [name, job] of [['error', { id: 'failed', status: 'error' }], ['cancelled', { id: 'failed', status: 'cancelled' }],
  ['quality failed', { id: 'failed', status: 'error', errorCode: 'mannequin_quality_failed' }], ['unknown', undefined]]) {
  test(`M3 ${name} never POSTs on entry/reload and explicit retry POSTs once`, async () => {
    const storage = memoryStorage();
    const first = createInitialGenerationAttempts({ storage });
    first.seed('p', job);
    let posts = 0;
    const generate = async (_id, options) => { if (!options.resumeJobId) posts++; return { data: { cuts: ['new'] } }; };
    await assert.rejects(runInitialGenerationAttempt(first, generate, 'p'));
    const reloaded = createInitialGenerationAttempts({ storage });
    reloaded.seed('p', job);
    reloaded.clear('p'); // 컷 조회나 dirty 표식 초기화는 종결을 지우지 않는다.
    await assert.rejects(runInitialGenerationAttempt(reloaded, generate, 'p'));
    assert.equal(posts, 0);
    assert.equal(reloaded.retry('p'), true);
    reloaded.seed('p', job); // 재시도 버튼의 화면 재조회가 클릭 의도를 지우지 않는다.
    await runInitialGenerationAttempt(reloaded, generate, 'p');
    assert.equal(posts, 1);
  });
}
for (const code of ['job_failed', 'job_cancelled', 'mannequin_quality_failed']) test(`M3 observed ${code} remains terminal until a click`, async () => {
  const attempts = createInitialGenerationAttempts({ storage: null });
  let posts = 0;
  const generate = async () => { posts++; throw Object.assign(new Error('실패'), { code }); };
  await assert.rejects(runInitialGenerationAttempt(attempts, generate, 'p'));
  await assert.rejects(runInitialGenerationAttempt(attempts, generate, 'p'));
  assert.equal(posts, 1);
});

for (const [name, latest] of [['new failed job', { id: 'new-job', status: 'error' }], ['unknown fetch', undefined]]) {
  test(`M3 late completion cannot clear ${name} seeded during project re-entry`, async () => {
    const attempts = createInitialGenerationAttempts({ storage: null });
    attempts.seed('p', { id: 'old-job', status: 'running' });
    let resolve;
    const old = runInitialGenerationAttempt(attempts, () => new Promise((r) => { resolve = r; }), 'p');
    attempts.seed('p', latest);
    resolve({ data: { cuts: ['old-cut'] } });
    await old;
    await assert.rejects(runInitialGenerationAttempt(attempts, () => { throw new Error('POST must not happen'); }, 'p'));
    assert.equal(attempts.read('p').terminalFailure, true);
  });
}

test('real store, shared runner, editor entry and mock rows honor server resume state', async (t) => {
  const previousStorage = globalThis.sessionStorage;
  globalThis.sessionStorage = memoryStorage();
  globalThis.__libraryU2Api = {};
  const root = fileURLToPath(new URL('../..', import.meta.url));
  const vite = await createServer({ configFile: false, root, logLevel: 'silent',
    resolve: { alias: { '@': `${root}/src` } }, server: { middlewareMode: true, watch: null },
    plugins: [{ name: 'library-u2-boundaries', enforce: 'pre',
      resolveId(id) {
        if (id === '@/lib/api/index.js' || id.endsWith('/lib/api/index.js')) return '\0u2-api';
        if (id === '@/lib/api/httpAdapter.js' || id.endsWith('/lib/api/httpAdapter.js')) return '\0u2-http';
      },
      load(id) {
        if (id === '\0u2-api') return 'export const isMockMode=false; export const api = new Proxy({}, {get: (_t,k) => globalThis.__libraryU2Api[k] || (async()=>({}))});';
        if (id === '\0u2-http') return 'export const resetAnalysisCache=()=>{};';
      } }],
  });
  const { useAppStore: store } = await vite.ssrLoadModule('/src/store/useAppStore.js');
  const { initialGenerationAttempts: attempts } = await vite.ssrLoadModule('/src/features/mannequin/initialGenerationAttempts.js');
  const runner = await vite.ssrLoadModule('/src/features/mannequin/generationRunner.js');
  t.after(async () => { store.getState().resetDetailPageJob(); await vite.close(); delete globalThis.__libraryU2Api; globalThis.sessionStorage = previousStorage; });

  await t.test('adopt snapshot restores server values for different and same project without PATCH', () => {
    let patches = 0;
    globalThis.__libraryU2Api.patchProject = () => { patches++; };
    store.getState().adoptProject('resume', { serverSnapshot: snapshot('resume') });
    store.getState().confirmProductInfo('resume');
    flowSession.authorizeFlowContinuation('resume', '/create/mannequin');
    store.getState().markFlowUsed('seller', 1000);
    assert.equal(store.getState().selectedMannequinId, 'saved-cut');
    assert.equal(store.getState().composeMode, 'extended');
    assert.equal(store.getState().copywriting, false);
    assert.equal(store.getState().productInfoConfirmed, true);
    assert.equal(store.getState().userId, 'seller');
    assert.equal(flowSession.hasFlowContinuation('resume'), true);
    store.setState({ selectedMannequinId: 'stale', composeMode: 'basic', copywriting: true });
    store.getState().adoptProject('resume', { serverSnapshot: snapshot('resume') });
    assert.equal(store.getState().selectedMannequinId, 'saved-cut');
    assert.equal(store.getState().composeMode, 'extended');
    assert.equal(store.getState().copywriting, false);
    assert.equal(patches, 0);
    store.getState().adoptProject('legacy');
    assert.equal(store.getState().selectedMannequinId, null);
    assert.equal(store.getState().composeMode, 'basic');
    assert.equal(store.getState().copywriting, true);
  });
  await t.test('loadProject refreshes latestJobs and choices on every project entry', async () => {
    const s = snapshot('reload', { id: 'active', status: 'running' });
    let reads = 0;
    globalThis.__libraryU2Api.getProject = async () => { reads++; return s; };
    store.getState().adoptProject('reload');
    await store.getState().loadProject(); await store.getState().loadProject();
    assert.equal(reads, 2);
    assert.equal(store.getState().composeMode, 'extended');
    assert.equal(attempts.read('reload').jobId, 'active');
  });
  for (const [name, job] of [['running', { id: 'm-running', status: 'running' }], ['error', { id: 'm-error', status: 'error' }],
    ['cancelled', { id: 'm-cancelled', status: 'cancelled' }], ['quality', { id: 'm-quality', status: 'error', errorCode: 'mannequin_quality_failed' }], ['unknown', undefined]]) {
    await t.test(`shared runner blocks dirty automatic regeneration for ${name}, including existing cuts`, async () => {
      const id = `dirty-${name}`;
      attempts.seed(id, job);
      let posts = 0;
      globalThis.__libraryU2Api.regenerateMannequin = async () => { posts++; return {}; };
      await assert.rejects(runner.requestMannequinRegeneration(id, {}));
      assert.equal(posts, 0);
      if (name !== 'running') {
        attempts.retry(id);
        await runner.requestMannequinRegeneration(id, {});
        assert.equal(posts, 1);
      }
    });
  }
  await t.test('failed project fetch seeds unknown and blocks the shared auto runner', async () => {
    store.getState().adoptProject('unknown-fetch');
    globalThis.__libraryU2Api.getProject = async () => { throw Object.assign(new Error('offline'), { status: 503 }); };
    globalThis.__libraryU2Api.generateMannequins = async () => { throw new Error('POST must not happen'); };
    assert.equal(await store.getState().loadProject(), 'unknown-fetch');
    await assert.rejects(runner.requestMannequinGeneration('unknown-fetch'), { code: 'mannequin_state_unknown' });
  });

  const editor = read('src/features/editor/Editor.jsx');
  const entry = editor.slice(editor.indexOf('  useEffect(() => {\n    setLoadError(null);'), editor.indexOf('  // 상세페이지 생성 사진은'));
  const bandSource = editor.slice(editor.indexOf('      {((genActive && dpJob.status'), editor.indexOf('      {/* FaceMarket 차단'));
  const { code: bandCode } = await transformWithEsbuild(`function Band(){return <>${bandSource}</>}`, 'Band.jsx', { jsx: 'transform' });
  for (const status of ['pending', 'running', 'error', 'cancelled', 'done']) {
    await t.test(`M5 ${status} editor entry works without a local draft or automatic POST`, async () => {
      store.getState().resetDetailPageJob();
      const id = `editor-${status}`;
      const s = snapshot(id, null, { id: `job-${status}`, status, errorCode: null });
      if (status === 'done') s.status = 'done';
      let posts = 0, polls = 0;
      Object.assign(globalThis.__libraryU2Api, { getJob: async () => { polls++; return { id: `job-${status}`, status, progress: 10 }; },
        getJobEvents: async () => ({ events: [] }), startDetailPage: async () => { posts++; return { data: [{ id: 'finished', elements: [] }], credits: 5 }; },
        getEditorBlocks: async () => status === 'done' ? [{ id: 'finished', elements: [] }] : [],
        getWardrobe: async () => ({}), getCatalogs: async () => ({}), getProduct: async () => ({ name: '상품', colors: [] }),
        getStoryboard: async () => [{ id: 'sb' }], getAnalysis: async () => ({}), getMatchClothing: async () => [], getAccount: async () => ({ credits: 5 }) });
      store.getState().adoptProject(id, { serverSnapshot: s });
      const initial = store.getState().detailPageJob;
      assert.deepEqual(initial.cuts, {}); assert.deepEqual(initial.copy, {}); assert.deepEqual(initial.live, []);
      const view = {};
      const identity = (value) => value;
      const bindings = { projectId: id, loadAttempt: 0, useEffect: (effect) => effect(), useAppStore: store,
        editorWorkCopies: { read: () => null }, loadEditorWaitDraftEntry: () => null, pendingGenerationDraft: { current: false }, genActiveRef: { current: false },
        wardrobeContext: { current: {} }, isMockMode: true, api: globalThis.__libraryU2Api, retryRead: (get) => get(),
        withStoryboardSpaceSetExamples: identity, normalizeEditorBlockRole: identity, buildColorOpts: () => [], visibleColorOpts: identity,
        hexForCol: () => '', buildEditorBlocksFromStoryboard: () => [{ id: 'waiting', elements: [] }], alignSkeletonToServer: identity,
        decorateGenBlocks, buildInfoCtx: () => ({}), needsDefaultTemplate: () => false, editorWaitDraftDecision: () => 'none',
        clearEditorWaitDraft() {}, mergeServerBlocks: identity, ensureShippingReturnsBlock: identity,
        upgradeLegacyKiwiTemplateBlocks: identity, stripPhotoBlockTextElements: identity, uid: () => 'id',
        mergeEditorImagesIntoWardrobe: ({ wardrobe }) => wardrobe, classifyEditorLoadError: identity, toast: { push() {} },
      };
      for (const name of ['LoadError', 'WaitBoardError', 'GenActive', 'Blocks', 'Wardrobe', 'Catalogs', 'FmModels', 'SelBlock',
        'ProductName', 'ClothingType', 'MatchClothing', 'Product', 'Analysis', 'DetailColorOpts', 'ColorOpts']) bindings[`set${name}`] = (value) => { view[name] = value; };
      new Function(...Object.keys(bindings), entry)(...Object.values(bindings));
      await new Promise(setImmediate); await new Promise(setImmediate);
      assert.equal(view.LoadError, null);
      assert.equal(posts, 0);
      const dpJob = store.getState().detailPageJob;
      assert.equal(view.GenActive, status === 'done' ? undefined : true);
      assert.equal(view.Blocks[0].id, status === 'done' ? 'finished' : 'waiting');
      assert.equal(polls > 0, status !== 'done');
      const bandBindings = { React, dpJob, projectId: id, genActive: view.GenActive, genFailed: dpJob.status === 'error',
        genIncomplete: null, genFinalizeError: null, genFailureMessage: dpJob.errorMessage, genReuseHint: '', genNotif: 'denied',
        genMergedRef: { current: false }, genActiveRef: { current: false }, setGenActive() {}, useAppStore: store,
        Icon: 'svg', Button: 'button', SmoothProgressTrack: 'span', EXPECTED_MS: { detailPage: 1 }, leaveToLibrary() {} };
      const Band = new Function(...Object.keys(bandBindings), `${bandCode}; return Band;`)(...Object.values(bandBindings));
      const tree = Band(), html = renderToString(tree);
      if (status === 'error' || status === 'cancelled') {
        assert.match(html, /ed-genbar error/);
        const retry = findAll(tree, (node) => node.type === 'button' && node.props.children === '다시 시도')[0];
        assert.ok(retry);
        retry.props.onClick();
        await new Promise(setImmediate);
        assert.equal(posts, 1);
      } else if (status !== 'done') assert.match(html, /상세페이지를 만들고 있어요/);
      else assert.equal(html.trim(), '');
      store.getState().resetDetailPageJob();
    });
  }
  await t.test('every editor route entry adopts the checked latestJobs snapshot before mounting', async () => {
    const app = read('src/apps/seller/App.jsx');
    const guard = app.slice(app.indexOf('function RequireEditorProject()'), app.indexOf("/* 상세페이지 제작 플로우에서"));
    const { code } = await transformWithEsbuild(guard, 'Guard.jsx', { jsx: 'transform' });
    const effects = [], adopted = [];
    const s = snapshot('direct', null, { id: 'direct-job', status: 'pending' });
    const useAppStore = (selector) => selector({ projectId: 'direct', adoptProject: (id, options) => adopted.push([id, options]) });
    const bindings = { React, useAppStore, useParams: () => ({ id: 'direct' }), useState: () => ['loading', () => {}],
      useEffect: (effect) => effects.push(effect), api: { getProject: async () => s }, markEditorEntered() {}, flowRouteSeenThisSession: false,
      shouldAdoptRouteProject: () => false, Navigate: 'div', Outlet: 'div', ErrorState: 'div' };
    new Function(...Object.keys(bindings), `${code}; RequireEditorProject();`)(...Object.values(bindings));
    effects.forEach((effect) => effect());
    await new Promise(setImmediate);
    assert.deepEqual(adopted, [['direct', { serverSnapshot: s }]]);
  });
  await t.test('mock adapter has all four resumable steps with covers and keeps switched projects', async () => {
    const { api } = await vite.ssrLoadModule('/src/mock/api.js');
    const list = await api.getInProgressLibrary();
    assert.deepEqual(new Set(list.map((row) => row.step)), new Set(steps));
    assert.ok(list.every((row) => row.cover && row.latestMannequinJob !== undefined && row.latestDetailPageJob !== undefined));
    for (const row of list) assert.equal((await api.getProject(row.id)).id, row.id);
    const again = await api.getInProgressLibrary();
    assert.ok(list.every((row) => again.some((item) => item.id === row.id)));
    assert.deepEqual(await api.getInProgressLibrary({ forceEmpty: true }), []);
  });
  await t.test('mock detail jobs stay attached to their project when another card opens', async () => {
    const { api } = await vite.ssrLoadModule('/src/mock/api.js');
    await api.getProject('in-progress-mannequin');
    const first = await api.startDetailPage('in-progress-mannequin');
    await api.getProject('in-progress-failed');
    const second = await api.startDetailPage('in-progress-failed');
    assert.notEqual(first.jobId, second.jobId);
    for (const id of [first.jobId, second.jobId]) {
      while ((await api.getJob(id)).status === 'running') await new Promise(setImmediate);
      assert.equal((await api.getJob(id)).status, 'done');
      assert.ok((await api.getJobEvents(id)).events.length > 0);
    }
    assert.equal((await api.getProject('in-progress-mannequin')).status, 'done');
    assert.equal((await api.getProject('in-progress-failed')).status, 'done');
  });
});


test('U3 library sections show matching headings and a compact completed-empty line', () => {
  const h = libraryHarness({ done: [] });
  const html = renderToString(h.tree);
  assert.match(html, /class="sec-title handoff-title">만드는 중/);
  assert.match(html, /class="sec-title handoff-title">완성본/);
  assert.match(html, /완성된 상세페이지가 여기에 모여요\./);
  assert.doesNotMatch(html, /아직 만든 상세페이지가 없어요|첫 상세페이지 만들기/);
  assert.equal(findAll(h.tree, (n) => n.type === 'div' && n.props.icon === 'library').length, 0);
  assert.doesNotMatch(renderToString(libraryHarness({ progress: [], loading: true }).tree), /만드는 중인 상품을 불러오고 있어요/);
  assert.doesNotMatch(renderToString(libraryHarness({ done: [], progress: [], loading: true }).tree), /아직 만든 상세페이지가 없어요|완성된 상세페이지/);
  const retry = findAll(libraryHarness({ progress: [], error: true }).tree, (n) => n.type === 'button');
  assert.ok(retry.some((n) => n.props.children === '다시 불러오기'));
  assert.doesNotMatch(renderToString(libraryHarness({ progress: [] }).tree), /완성본/);
});

test('U3 both library grids share two equal phone columns and readable clamped titles', () => {
  const css = read('src/styles/features.css');
  const phone = css.slice(css.indexOf('@media (max-width: 560px) {\n  .lib-grid'), css.indexOf('.lib-new {'));
  assert.match(phone, /grid-template-columns: repeat\(2, minmax\(0, 1fr\)\); gap: var\(--sp-8\)/);
  assert.match(phone, /-webkit-line-clamp: 2/);
  assert.match(phone, /text-wrap: balance/);
  assert.match(phone, /overflow-wrap: normal/);
  assert.match(phone, /\.lib-info \.m span \{ white-space: nowrap; \}/);
  assert.match(phone, /flex-wrap: wrap/);
  assert.doesNotMatch(phone, /word-break:/);
  assert.equal(findAll(libraryHarness().tree, (n) => n.props.className?.includes('lib-grid')).length, 2);
});
