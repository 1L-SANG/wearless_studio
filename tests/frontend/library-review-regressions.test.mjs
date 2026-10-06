import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { createConflictWorkCopies, restoreWorkCopyOnce } from '../../src/lib/conflictWorkCopy.js';
import { createInitialGenerationAttempts } from '../../src/features/mannequin/initialGenerationAttempts.js';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const memoryStorage = () => {
  const values = new Map();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
};
function evaluate(source, bindings, result) {
  return new Function(...Object.keys(bindings), `${source}; return ${result};`)(...Object.values(bindings));
}

test('① 수동 핏 재생성의 불명 결과는 새 예약 없이 같은 잡을 재조회한다', async (t) => {
  const storage = memoryStorage();
  const previous = globalThis.sessionStorage;
  globalThis.sessionStorage = storage;
  const requests = [], polls = [];
  let pollCode = 'api_network';
  let listUnavailable = false;
  const source = read('src/lib/api/httpAdapter.js');
  const method = source.slice(source.indexOf('  async regenerateMannequin('), source.indexOf('  // 에디터 Wardrobe'));
  const adapter = evaluate(`const adapter = {${method}}`, {
    http: async (path, options) => {
      if (options?.method === 'POST') { requests.push(options); return { jobId: `job-${requests.length}` }; }
      if (listUnavailable) throw Object.assign(new Error('offline'), { code: 'api_network' });
      return [{ id: 'A-1' }];
    },
    pollJob: async (id) => { polls.push(id); if (pollCode) throw Object.assign(new Error(pollCode), { code: pollCode }); return { credits: 10 }; },
    LONG_IMAGE_JOB_TIMEOUT_MS: 1, MANNEQUIN_JOB_TIMEOUT_MESSAGE: 'timeout',
  }, 'adapter');
  globalThis.__reviewApi = adapter;
  const root = fileURLToPath(new URL('../..', import.meta.url));
  const vite = await createServer({ configFile: false, root, logLevel: 'silent', server: { middlewareMode: true, watch: null },
    plugins: [{ name: 'review-boundaries', enforce: 'pre',
      resolveId(id) {
        if (id.endsWith('/lib/api/index.js')) return '\0review-api';
        if (id.endsWith('/store/useAppStore.js')) return '\0review-store';
      },
      load(id) {
        if (id === '\0review-api') return 'export const api = globalThis.__reviewApi;';
        if (id === '\0review-store') return 'export const useAppStore = {getState:()=>({projectId:"p",mannequinJob:{},setMannequinJob(){}})};';
      } }],
  });
  t.after(async () => { await vite.close(); globalThis.sessionStorage = previous; delete globalThis.__reviewApi; });
  let runner = await vite.ssrLoadModule('/src/features/mannequin/generationRunner.js');
  const options = { fitProfile: { axes: { fit: 'over' } }, expectedQuote: { free: false, credits: 45 } };
  await assert.rejects(runner.requestMannequinRegeneration('p', options), { code: 'api_network' });
  vite.moduleGraph.invalidateAll();
  runner = await vite.ssrLoadModule('/src/features/mannequin/generationRunner.js');
  const { initialGenerationAttempts } = await vite.ssrLoadModule('/src/features/mannequin/initialGenerationAttempts.js');
  initialGenerationAttempts.seed('p', { id: 'job-1', status: 'running' });
  await assert.rejects(runner.requestMannequinRegeneration('p', { ...options, retry: true }), { code: 'api_network' });
  assert.equal(requests.length, 1);
  assert.ok(requests[0].headers?.['Idempotency-Key']);
  assert.deepEqual(polls, ['job-1', 'job-1']);
  const attempt = createInitialGenerationAttempts({ storage, keyPrefix: 'wl_regenerate_mannequin_attempt:' }).read('p');
  assert.equal(attempt.jobId, 'job-1');
  assert.equal(attempt.idempotencyKey, requests[0].headers['Idempotency-Key']);
  pollCode = 'job_failed';
  await assert.rejects(runner.requestMannequinRegeneration('p', options), { code: 'job_failed' });
  await assert.rejects(runner.requestMannequinRegeneration('p', options), { code: 'job_failed' });
  assert.equal(requests.length, 1);
  pollCode = null;
  await runner.requestMannequinRegeneration('p', { ...options, retry: true });
  assert.equal(requests.length, 2);
  assert.notEqual(requests[1].headers['Idempotency-Key'], requests[0].headers['Idempotency-Key']);

  // 재진입에서 같은 서버 잡의 종결을 확인했으면 다음 클릭만 새 시도가 된다.
  pollCode = 'api_network';
  await assert.rejects(runner.requestMannequinRegeneration('reentry', options));
  initialGenerationAttempts.seed('reentry', { id: 'job-3', status: 'running' });
  adapter.generateMannequins = async (_id, { resumeJobId, onJobStarted }) => {
    assert.equal(resumeJobId, 'job-3'); onJobStarted(resumeJobId);
    return { data: { cuts: [{ id: 'A-2' }] }, credits: 10 };
  };
  await runner.requestMannequinGeneration('reentry');
  await assert.rejects(runner.requestMannequinRegeneration('reentry', options), { code: 'mannequin_job_done' });
  assert.equal(requests.length, 3);
  pollCode = null;
  await runner.requestMannequinRegeneration('reentry', { ...options, retry: true });
  assert.equal(requests.length, 4);

  listUnavailable = true;
  await assert.rejects(runner.requestMannequinRegeneration('finished-before-list', options), { code: 'api_network' });
  const finished = createInitialGenerationAttempts({ storage, keyPrefix: 'wl_regenerate_mannequin_attempt:' }).read('finished-before-list');
  assert.equal(finished.serverStatus, 'done');
  await assert.rejects(runner.requestMannequinRegeneration('finished-before-list', options), { code: 'mannequin_job_done' });
  assert.equal(requests.length, 5);
  listUnavailable = false;
  await runner.requestMannequinRegeneration('finished-before-list', { ...options, retry: true });
  assert.equal(requests.length, 6);
});

for (const savedSelection of ['A-1', 'missing-from-partial']) {
  test(`② 진행 중 재생성 합류는 전체 컷과 최신 선택 ${savedSelection}을 보존한다`, async () => {
    const source = read('src/features/mannequin/Mannequin.jsx');
    const callback = source.slice(source.indexOf('  const loadMannequins ='), source.indexOf('  useEffect(() => {\n    loadMannequins();'));
    const full = [{ id: 'A-1' }, { id: 'B-1' }, { id: 'B-2' }];
    const patches = [], view = {}, calls = [];
    let reads = 0;
    const state = { projectId: 'p', selectedMannequinId: 'B-1', loadProject: async () => {},
      clearGenerationRelevantEdits() {}, adoptProject: (_id, { serverSnapshot }) => { state.selectedMannequinId = serverSnapshot.selectedMannequinId; } };
    const bindings = {
      useCallback: (fn) => fn, useAppStore: { getState: () => state }, loadRunRef: { current: 0 }, initialCutsExistedRef: { current: false },
      window: { scrollTo() {} }, document: { querySelector: () => null }, navigate() {}, isMockMode: true,
      api: { getProduct: async () => ({}), getAnalysis: async () => ({}), getCatalogs: async () => ({}), getStoryboard: async () => [],
        getCreditQuote: async () => ({}), getMannequins: async () => { calls.push('cuts'); return ++reads === 1 ? full.slice(0, 2) : full; },
        getProject: async () => { calls.push('selection'); return { id: 'p', selectedMannequinId: savedSelection }; } },
      generationProgressFor: () => 0, uniqueGenerationCutCount: () => 0, realFaceAllowedCut: () => true,
      resolveMainMatchingItem: () => null, createFitProfileDraft: () => ({}), createCutFitProfileDraft: () => ({}),
      initStepState: () => ({}), axesFor: () => ({}), matchingFitDefinition: () => null,
      landedGenerationRelevantEditsAttemptRevision: () => null, cutsExistedBeforeInitialGeneration: () => true,
      hasActiveMannequinGeneration: () => true,
      resolveInitialGenerationCuts: async () => { calls.push('join'); return { cuts: [{ id: 'B-2' }], cutsExisted: true }; },
      requestMannequinGeneration() {}, extractCuts: (v) => v, syncCredits() {}, clearInitialGenerationRequested() {}, updateMannequinJob() {},
      selectMannequin: (id) => patches.push(id), analysisForCutFitProfile: (an) => an, pushToast() {},
    };
    for (const key of ['Phase', 'ErrorMsg', 'Progress', 'Product', 'Analysis', 'Catalogs', 'RealModels', 'CreditQuote', 'AiCutCount', 'HorizonCutCount', 'FitProfileDraft', 'StepState', 'Cuts', 'ChangedQuote']) bindings[`set${key}`] = (value) => { view[key] = value; };
    await evaluate(callback, bindings, 'loadMannequins')();
    assert.equal(view.Phase, 'ready');
    assert.deepEqual(view.Cuts, full);
    assert.equal(state.selectedMannequinId, savedSelection);
    assert.deepEqual(patches, []);
    assert.deepEqual(calls, ['cuts', 'join', 'cuts', 'selection']);
  });
}

for (const editing of [false, true]) test(`③ 409 최신본 대기 중 ${editing ? '입력 중인' : '확정한'} 문구와 undo 이력을 복구 버튼이 모두 되살린다`, async () => {
  const source = read('src/features/editor/Editor.jsx');
  const entry = source.slice(source.indexOf('  useEffect(() => {\n    setLoadError(null);'), source.indexOf('  // 상세페이지 생성 사진은'));
  const reload = source.slice(source.indexOf('  const reloadAfterEditorConflict ='), source.indexOf('  // 자동 저장', source.indexOf('  const reloadAfterEditorConflict =')));
  const pending = deferred(), view = {}, toast = [], copies = createConflictWorkCopies('editor', memoryStorage());
  const first = [{ id: 'mine', text: '409 때 입력' }], typed = [{ id: 'mine', text: '409 때 입력, 기다리는 중 추가 입력' }];
  const hist = { current: { past: [], future: [] } }, latestBlocks = { current: first };
  const identity = (v) => v;
  const bindings = { projectId: 'p', loadAttempt: 1, useEffect: (fn) => fn(), useCallback: (fn) => fn,
    flushSync: (fn) => fn(), document: { activeElement: { blur: () => {
      if (editing) { latestBlocks.current = typed; hist.current = { past: [first], future: [] }; }
    } } },
    useAppStore: { getState: () => ({ projectId: 'p', detailPageJob: {}, loadAccount: async () => ({}) }) },
    editorWorkCopies: copies, restoreWorkCopyOnce, loadEditorWaitDraftEntry: () => null, pendingGenerationDraft: { current: false },
    genActiveRef: { current: false }, wardrobeContext: { current: {} }, latestBlocks, hist,
    prevBlocks: { current: first }, fromHistory: { current: false }, saveTimer: { current: null }, saveGuard: { current: { invalidate() {} } },
    conflictReloadAt: { current: 0 }, backupLatestEditorDraft() {}, setSaveError() {}, setLoadAttempt() {},
    isMockMode: true, api: { getEditorBlocks: () => pending.promise, getWardrobe: async () => ({}), getCatalogs: async () => ({}),
      getProduct: async () => ({ colors: [] }), getAnalysis: async () => ({}), getStoryboard: async () => [], getMatchClothing: async () => [] },
    withStoryboardSpaceSetExamples: identity, normalizeEditorBlockRole: identity, buildColorOpts: () => [], visibleColorOpts: identity,
    hexForCol: () => '', buildInfoCtx: () => ({}), needsDefaultTemplate: () => false, editorWaitDraftDecision: () => 'none',
    ensureShippingReturnsBlock: identity, upgradeLegacyKiwiTemplateBlocks: identity, stripPhotoBlockTextElements: identity,
    mergeEditorImagesIntoWardrobe: ({ wardrobe }) => wardrobe, uid: () => 'id', classifyEditorLoadError: identity,
    toast: { push: (_message, options) => toast.push(options) },
  };
  for (const key of ['LoadError', 'Blocks', 'Wardrobe', 'Catalogs', 'FmModels', 'SelBlock', 'ProductName', 'ClothingType', 'MatchClothing', 'Product', 'Analysis', 'DetailColorOpts', 'ColorOpts']) bindings[`set${key}`] = (value) => { view[key] = value; };
  evaluate(reload, bindings, 'reloadAfterEditorConflict')();
  evaluate(entry, bindings, 'undefined');
  if (!editing) {
    latestBlocks.current = typed;
    hist.current = { past: [first], future: [] };
  }
  pending.resolve([{ id: 'server', text: '다른 PC 최신 저장' }]);
  await new Promise(setImmediate);
  assert.equal(view.LoadError, null);
  assert.equal(view.Blocks[0].id, 'server');
  toast[0].action.onClick();
  assert.deepEqual(view.Blocks, typed);
  assert.deepEqual(hist.current, { past: [first], future: [] });
});

test('⑥ 콘티 409 metadata는 정상 GET과 같은 API 에셋 주소를 쓴다', async () => {
  const source = read('src/lib/api/httpAdapter.js');
  const body = source.slice(source.indexOf('  if (!res.ok) {'), source.indexOf('/* 톤 에디터 전용'));
  const storyboard = [{ id: 'photo', src: '/v1/assets/photo/file' }];
  const normalize = (value) => JSON.parse(JSON.stringify(value).replaceAll('/v1/assets/', 'https://api.example/v1/assets/'));
  const response = async (res) => evaluate(`async function finish(){${body}`, {
    res, absolutizeAssetUrls: normalize, suppressErrorLog: true, path: '/storyboard',
  }, 'finish')();
  const get = await response({ ok: true, status: 200, json: async () => storyboard });
  await assert.rejects(response({ ok: false, status: 409, json: async () => ({ error: {
    code: 'revision_conflict', meta: { storyboard, revision: 3 },
  } }) }), (error) => { assert.deepEqual(error.meta.storyboard, get); return true; });
});
