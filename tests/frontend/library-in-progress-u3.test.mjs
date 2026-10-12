import { stripFeatureClientFields } from '../../src/lib/featureRun.js';
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createServer, transformWithEsbuild } from 'vite';
import React from 'react';
import { createStoryboardRevisionSync } from '../../src/lib/storyboardRevisionSync.js';
import { createConflictWorkCopies, restoreWorkCopyOnce } from '../../src/lib/conflictWorkCopy.js';
import { mergeAnalysisPatch } from '../../src/lib/analysisPatch.js';
import { mergeMatchSelection } from '../../src/lib/api/matchSelection.js';
import { createMannequinQuoteConfirmation, expectedMannequinQuote } from '../../src/features/mannequin/quoteConfirmation.js';
import { createMannequinGenerationRunner } from '../../src/features/mannequin/generationRunnerCore.js';
import { createInitialGenerationAttempts, runInitialGenerationAttempt } from '../../src/features/mannequin/initialGenerationAttempts.js';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const memoryStorage = () => {
  const values = new Map();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
};
const httpSource = read('src/lib/api/httpAdapter.js');
const editorSource = read('src/features/editor/Editor.jsx');
let vite, createStoryboardPersistence, mockApi;
before(async () => {
  vite = await createServer({ optimizeDeps: { noDiscovery: true, include: [] }, configFile: false, resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null }, appType: 'custom', logLevel: 'silent' });
  ({ createStoryboardPersistence } = await vite.ssrLoadModule('/src/features/storyboard/storyboardPersistence.js'));
  ({ api: mockApi } = await vite.ssrLoadModule('/src/mock/api.js'));
});
after(async () => { await vite?.close(); });

test('M6 HTTP always uses revision read and baseRevision save flags, including keepalive', async () => {
  const source = httpSource.slice(httpSource.indexOf('const storyboardRevisions ='), httpSource.indexOf('// 공용 fetch 헬퍼'));
  const calls = [];
  const http = async (path, options) => {
    calls.push({ path, options });
    return { blocks: [{ id: 'server' }], revision: options?.method === 'PUT' ? 8 : 7 };
  };
  const sync = new Function('http', 'createStoryboardRevisionSync', 'relativizeAssetUrls', `${source}; return storyboardRevisions;`)(http, createStoryboardRevisionSync, (v) => v);
  await sync.load('a');
  await sync.save('a', [{ id: 'mine' }], { keepalive: true });
  assert.deepEqual(calls.map((c) => c.path), ['/v1/projects/a/storyboard?withRevision=1', '/v1/projects/a/storyboard?baseRevision=7']);
  assert.equal(calls[1].options.keepalive, true);
  assert.equal(sync.revisionOf('a'), 8);
  assert.equal(sync.revisionOf('b'), null);
  assert.match(httpSource, /storyboardRevisions.load\(projectId\)/);
  assert.match(httpSource, /return storyboardRevisions.save\(projectId, blocks, options\)/);
});

test('M6 conflict keeps a project work copy, loads server revision, skips queued saves and restores once', async () => {
  const storage = memoryStorage(), copies = createConflictWorkCopies('storyboard', storage);
  const mine = [{ id: 'mine' }], server = [{ id: 'server' }];
  let screen = mine, action, calls = [], timers = 0, repairs = 0, release;
  const sync = createStoryboardRevisionSync({ load: async () => ({ blocks: server, revision: 1 }),
    put: async (id, blocks, baseRevision) => {
      calls.push({ id, blocks, baseRevision });
      if (calls.length === 1) {
        await new Promise((resolve) => { release = resolve; });
        throw Object.assign(new Error('conflict'), { status: 409, code: 'revision_conflict', meta: { storyboard: server, revision: 4 } });
      }
      return { blocks, revision: 5 };
    } });
  await sync.load('p');
  const persistence = createStoryboardPersistence({ saveStoryboard: (...args) => sync.save(...args), workCopies: copies,
    invalidate() {}, onlineTarget: null, setTimer() { timers += 1; }, clearTimer() {} });
  persistence.setRepairRejected(() => { repairs += 1; return [{ id: 'repair' }]; });
  persistence.setConflictHandler((id, copy, latest) => {
    screen = latest;
    action = restoreWorkCopyOnce(async () => {
      screen = copy.blocks;
      await persistence.saveNow(id, () => screen, { restoreConflict: true });
      copies.clear(id, copy);
    });
  });
  const first = persistence.saveNow('p', () => mine);
  await new Promise(setImmediate);
  const queued = persistence.saveNow('p', () => [{ id: 'stale-queued' }]);
  release();
  await assert.rejects(first, { code: 'revision_conflict' });
  await assert.rejects(queued, { code: 'revision_conflict' });
  assert.deepEqual(screen, server);
  assert.deepEqual(copies.read('p').blocks, mine);
  assert.deepEqual(createConflictWorkCopies('storyboard', storage).read('p').blocks, mine);
  assert.equal(copies.read('other-project'), null);
  assert.equal(persistence.pending.has('p'), false);
  assert.equal(timers, 0);
  assert.equal(repairs, 0);
  await assert.rejects(persistence.saveNow('p', () => mine), { code: 'revision_conflict' });
  await persistence.saveNow('p', () => server);
  assert.equal(calls.length, 1);
  await action(); await action();
  assert.deepEqual(screen, mine);
  assert.equal(calls.length, 2);
  assert.equal(calls[1].baseRevision, 4);
  assert.equal(copies.read('p'), null);
  persistence.dispose();
});

test('M6 a repeated restore conflict preserves the copy without autosave or repair loops', async () => {
  const copies = createConflictWorkCopies('storyboard', memoryStorage());
  const mine = [{ id: 'mine' }];
  let calls = 0, timers = 0;
  const persistence = createStoryboardPersistence({ workCopies: copies, invalidate() {}, onlineTarget: null,
    setTimer() { timers += 1; }, clearTimer() {}, saveStoryboard: async () => {
      calls += 1;
      throw Object.assign(new Error('conflict'), { status: 409, code: 'revision_conflict', meta: { storyboard: [{ id: 'server' }], revision: calls } });
    } });
  await assert.rejects(persistence.saveNow('p', () => mine));
  await assert.rejects(persistence.saveNow('p', () => mine, { restoreConflict: true }));
  await assert.rejects(persistence.saveNow('p', () => mine), { code: 'revision_conflict' });
  await persistence.saveIdle();
  assert.equal(calls, 2); assert.equal(timers, 0);
  assert.deepEqual(copies.read('p').blocks, mine);
  persistence.dispose();
  const source = read('src/features/storyboard/Storyboard.jsx');
  assert.match(source, /latestBlocks.current = server/);
  assert.match(source, /setBlocks\(server\)/);
  assert.match(source, /이 화면 내용으로 되돌리기/);
  assert.match(source, /if \(error\?\.code === 'revision_conflict'\) throw error/);
});

function analysisAdapter({ cached = { selectedModelId: 'old', brandUseCategory: 'old-brand', materials: ['old'] }, server = cached } = {}) {
  let saved = server;
  const calls = [];
  const body = httpSource.slice(httpSource.indexOf('  async saveAnalysis('), httpSource.indexOf('  // 저장된 분석 payload 조회'));
  const http = async (path, options) => {
    calls.push({ path, options });
    if (options) saved = mergeAnalysisPatch(saved, options.body);
    return saved;
  };
  const adapter = new Function('stripFeatureClientFields', 'http', 'cachedAnalysisFor', 'mergeAnalysisPatch', 'isMatchRefresh', 'recommendMatchHttp', 'mergeMatchSelection',
    `let analysisCache; return ({ ${body} });`)(stripFeatureClientFields, http, () => cached, mergeAnalysisPatch,
    (patch) => ['clothingType', 'targetGenders', 'styleTags'].some((k) => patch[k] != null),
    async () => [{ id: 'fresh', selected: false }], mergeMatchSelection);
  return { adapter, calls, saved: () => saved };
}

for (const [caller, patch] of [
  ['ProductInput', { sellingPoints: ['new'] }],
  ['editor brand', { brandUseCategory: 'new-brand' }],
  ['editor model', { selectedModelId: 'new-model' }],
  ['mannequin select', { fitProfile: { axes: { fit: 'regular' }, version: 2 } }],
  ['mannequin regeneration', { fitProfile: { axes: { length: 'long' }, version: 2 } }],
  ['mannequin continue', { fitProfile: null }],
]) test(`M7 ${caller} sends only its changed keys with merge=1`, async () => {
  const h = analysisAdapter();
  await h.adapter.saveAnalysis('p', patch);
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].path, '/v1/projects/p/analysis?merge=1');
  assert.deepEqual(h.calls[0].options.body, patch);
  assert.deepEqual(h.saved().materials, ['old']);
});

test('M7 fresh empty projects persist a delta without needing a cached full body', async () => {
  const h = analysisAdapter({ cached: null, server: {} });
  assert.deepEqual(await h.adapter.saveAnalysis('empty', { selectedModelId: 'new-model' }), {
    selectedModelId: 'new-model', frontClosure: null,
  });
  assert.equal(h.calls.length, 1);
  assert.deepEqual(h.calls[0].options.body, { selectedModelId: 'new-model' });
});

test('M7 selection uses latest candidates and transmits only the resulting array; empty and null remove deliberately', async () => {
  const h = analysisAdapter({ cached: { matchClothing: [{ id: 'old' }] }, server: { fitProfile: { old: true },
    matchClothing: [{ id: 'fresh', selected: false, clothingType: 'top' }], serverOnly: true } });
  await h.adapter.saveAnalysis('p', { matchClothing: [{ id: 'fresh', selected: true, selOrder: 1 }] });
  assert.equal(h.calls[0].options, undefined);
  assert.deepEqual(Object.keys(h.calls[1].options.body), ['matchClothing']);
  assert.equal(h.calls[1].options.body.matchClothing[0].id, 'fresh');
  assert.equal(h.calls[1].options.body.matchClothing[0].selected, true);
  await h.adapter.saveAnalysis('p', { matchClothing: [] });
  assert.deepEqual(h.saved().matchClothing, []);
  await h.adapter.saveAnalysis('p', { fitProfile: null });
  assert.equal('fitProfile' in h.saved(), false);
  assert.equal(h.saved().serverOnly, true);
});

test('M7 caller audit retains small patches and promotion explicitly deletes former omissions', async () => {
  const route = await vite.ssrLoadModule('/src/features/product-input/saveRouting.js');
  const calls = [];
  await route.persistAnalysisEdit({ saveProduct: async (_id, patch) => calls.push(['product', patch]),
    saveAnalysis: async (_id, patch) => calls.push(['analysis', patch]) }, 'p', { materials: [] });
  assert.deepEqual(calls, [['analysis', { materials: [] }]]);
  const syncSource = read('src/lib/draftSync.js');
  assert.match(syncSource, /analysis.confirmedGptProductEvidenceHandoff = null/);
  assert.match(syncSource, /analysis.detailRecommendationsHandoff = null/);
  assert.match(syncSource, /\['clothingType', 'measurements', 'measurementsUnknown'\][\s\S]*analysis\[k\] = null/);
  assert.match(syncSource, /delete analysis.detailRecommendations/);
  assert.match(read('src/lib/customMatchPromotion.js'), /saveAnalysis\(projectId, \{\s*matchClothing:/);
  assert.match(editorSource, /saveAnalysis\(projectId, \{ brandUseCategory: value \}\)/);
  assert.match(editorSource, /saveAnalysis\(projectId, \{ selectedModelId: modelId \}\)/);
  const mannequin = read('src/features/mannequin/Mannequin.jsx');
  for (const field of ['record.profile', 'profile']) assert.ok(mannequin.includes(`{ fitProfile: ${field} }`));
  assert.match(mannequin, /persistFitProfile: \(fitProfile\) => api.saveAnalysis\(generationProjectId, \{ fitProfile \}\)/);
});

test('M7 mock merges only supplied keys, deletes nulls, and replaces arrays and fitProfile', async () => {
  await mockApi.saveAnalysis(null, { keepU3: 'kept', removeU3: 'remove', materials: ['old'], fitProfile: { version: 2, axes: { fit: 'regular', length: 'long' } } });
  await mockApi.saveAnalysis(null, { removeU3: null, materials: [], fitProfile: { version: 2, axes: { fit: 'slim' } } });
  const saved = await mockApi.getAnalysis(null);
  assert.equal(saved.keepU3, 'kept'); assert.equal('removeU3' in saved, false);
  assert.deepEqual(saved.materials, []);
  assert.deepEqual(saved.fitProfile.axes, { fit: 'slim' });
  await mockApi.saveAnalysis(null, { fitProfile: null });
  assert.equal('fitProfile' in (await mockApi.getAnalysis(null)), false);
});

for (const operation of ['generate', 'regenerate']) test(`M9 ${operation} sends the shown quote, blocks repeat requests and requires a new click`, async () => {
  const storage = memoryStorage(), confirmation = createMannequinQuoteConfirmation(storage);
  const shown = expectedMannequinQuote(0), changed = expectedMannequinQuote(45);
  confirmation.remember('p', operation, shown);
  let calls = 0, reserved = 0;
  const request = async (options) => {
    calls += 1;
    if (calls === 1) {
      assert.deepEqual(options.expectedQuote, shown);
      throw Object.assign(new Error('new quote'), { status: 409, code: 'quote_changed', meta: { quote: changed } });
    }
    assert.deepEqual(options.expectedQuote, changed);
    reserved += changed.credits;
    return { data: [] };
  };
  await assert.rejects(confirmation.request('p', operation, request), { code: 'quote_changed' });
  assert.deepEqual(confirmation.pending('p'), { operation, quote: changed });
  assert.equal(reserved, 0);
  await assert.rejects(confirmation.request('p', operation, request), { code: 'quote_changed' });
  await assert.rejects(createMannequinQuoteConfirmation(storage).request('p', operation, request), { code: 'quote_changed' });
  assert.equal(calls, 1);
  await confirmation.request('p', operation, request, { confirmed: true });
  assert.equal(calls, 2); assert.equal(reserved, 45);
  assert.equal(confirmation.pending('p'), null);
});

test('M9 HTTP generate and regenerate carry expectedQuote, and runner forwards it through initial attempt', async () => {
  const calls = [];
  const http = async (path, options) => { calls.push({ path, options }); return { data: ['cut'], credits: 1 }; };
  const methods = httpSource.slice(httpSource.indexOf('  async generateMannequins('), httpSource.indexOf('  // 에디터 Wardrobe'));
  const adapter = new Function('http', 'recordClientAction', `return ({ ${methods} });`)(http, () => {});
  const expectedQuote = expectedMannequinQuote(45);
  await adapter.generateMannequins('p', { expectedQuote });
  await adapter.regenerateMannequin('p', { expectedQuote, fitProfile: { version: 2 } });
  assert.deepEqual(calls.map((c) => c.options.body.expectedQuote), [expectedQuote, expectedQuote]);
  const attempts = createInitialGenerationAttempts({ storage: memoryStorage() });
  const runner = createMannequinGenerationRunner({ generate: (pid, options) => runInitialGenerationAttempt(attempts, adapter.generateMannequins, pid, options),
    readProgress: () => 0, onJobChange() {} });
  await runner.request('p', { expectedQuote });
  assert.deepEqual(calls[2].options.body.expectedQuote, expectedQuote);
});

test('M9 changed quote UI returns to idle before retries and renders the new price on existing confirm buttons', async () => {
  const source = read('src/features/mannequin/Mannequin.jsx');
  const start = source.indexOf("        if (error?.code === 'quote_changed') {", source.indexOf('const runGenerationAttempts'));
  const stop = source.indexOf('        // pollJob', start);
  const block = source.slice(start, stop);
  const state = {}, quote = expectedMannequinQuote(45);
  const result = new Function('error', 'clearWaitCopyTimers', 'submittingRef', 'setBusy', 'setRegenerateState', 'setChangedQuote', 'pushToast', block)(
    { code: 'quote_changed', meta: { quote }, message: 'new quote' }, () => {}, { current: true },
    (v) => { state.busy = v; }, (v) => { state.phase = v; }, (v) => { state.quote = v; }, () => {});
  assert.equal(result, false); assert.equal(state.busy, false); assert.equal(state.phase, 'idle');
  assert.deepEqual(state.quote, { operation: 'regenerate', quote });
  assert.match(source, /nextCost: changedQuote.quote.credits/);
  assert.match(source, /confirmed: manual && changedQuote\?\.operation === 'regenerate'/);
  assert.match(source, /마네킹 다시 만들기 · \{mannequinQuoteLabel\(quote\)\}/);
  assert.match(read('src/features/storyboard/Storyboard.jsx'), /requestMannequinGeneration\(projectId, \{ confirmed: true \}\)/);
  assert.match(read('src/features/analysis/AnalysisForm.jsx'), /onNext\(\{ expectedQuote: \{ free: mannequinGenerationCost === 0, credits: mannequinGenerationCost \} \}\)/);
});

test('M11 actual editor conflict callback keeps local draft and undo, then restoration uses the newly read revision', () => {
  const copies = createConflictWorkCopies('editor', memoryStorage());
  const mine = [{ id: 'mine' }], old = [{ id: 'undo' }], future = [{ id: 'redo' }];
  const hist = { current: { past: [old], future: [future] } }, latestBlocks = { current: mine };
  let loadAttempt = 0, invalidations = 0, backup, action, restored, revision = 1;
  const bindings = { projectId: 'p', latestBlocks, hist, editorWorkCopies: copies,
    backupLatestEditorDraft: (blocks) => { backup = { blocks, revision }; }, useCallback: (fn) => fn,
    clearTimeout() {}, saveTimer: { current: null }, saveGuard: { current: { invalidate() { invalidations += 1; } } },
    conflictReloadAt: { current: 0 }, editorConflictPending: { current: false }, setSaveError() {}, setLoadAttempt: (fn) => { loadAttempt = fn(loadAttempt); } };
  const callback = editorSource.slice(editorSource.indexOf('  const reloadAfterEditorConflict'), editorSource.indexOf('  // 자동 저장', editorSource.indexOf('  const reloadAfterEditorConflict')));
  const reload = new Function(...Object.keys(bindings), `${callback}; return reloadAfterEditorConflict;`)(...Object.values(bindings));
  reload(); reload();
  assert.equal(loadAttempt, 1); assert.equal(invalidations, 2);
  assert.deepEqual(backup, { blocks: mine, revision: 1 });
  assert.deepEqual(hist.current, { past: [old], future: [future] });
  const conflictCopy = copies.read('p');
  assert.deepEqual(conflictCopy.history, hist.current);
  revision = 7;
  const restoreBindings = { conflictCopy, loadAttempt, restoredSavedDraft: false, editorConflictPending: bindings.editorConflictPending, withH: [{ id: 'server' }], hist, latestBlocks, editorWorkCopies: copies,
    flushSync: (fn) => fn(), document: { activeElement: null },
    projectId: 'p', fromHistory: { current: false }, prevBlocks: { current: null }, restoreWorkCopyOnce,
    useAppStore: { getState: () => ({ projectId: 'p' }) }, backupLatestEditorDraft: bindings.backupLatestEditorDraft,
    setBlocks: (blocks) => { restored = blocks; }, toast: { push(message, options) {
      assert.equal(message, '다른 곳에서 먼저 저장해서 최신 내용을 불러왔어요.');
      assert.equal(options.action.label, '이 화면 내용으로 되돌리기'); action = options.action.onClick;
    } } };
  const start = editorSource.indexOf('        if (conflictCopy && (editorConflictPending.current || !restoredSavedDraft)) {');
  const restore = editorSource.slice(start, editorSource.indexOf('        setBlocks(withH);', start));
  new Function(...Object.keys(restoreBindings), restore)(...Object.values(restoreBindings));
  action(); action();
  assert.deepEqual(restored, mine); assert.deepEqual(latestBlocks.current, mine);
  assert.equal(backup.revision, 7);
  assert.deepEqual(hist.current, { past: [old], future: [future] });
  assert.equal(copies.read('p'), null);
});

test('U3 editor error download label is 다운로드 and remains disabled while no finished page exists', async () => {
  const start = editorSource.indexOf('<Button variant="primary" size="sm" icon="download" disabled={genActive}');
  const jsx = editorSource.slice(start, editorSource.indexOf('</Button>', start) + '</Button>'.length);
  const { code } = await transformWithEsbuild(`function header() { return (${jsx}); }`, 'Header.jsx', { jsx: 'transform' });
  const header = new Function('React', 'Button', 'genActive', 'genFailed', 'setDownload', `${code}; return header;`);
  const error = header(React, 'button', true, true, () => {})();
  assert.equal(error.props.children, '다운로드'); assert.equal(error.props.disabled, true);
  assert.equal(header(React, 'button', true, false, () => {})().props.children, '생성 중…');
  assert.equal(header(React, 'button', false, false, () => {})().props.disabled, false);
});


test('M7 project mock preserves protected server evidence and locked state during merges', async () => {
  await mockApi.saveAnalysis(null, { locked: true, detailRecommendations: { status: 'ready' }, sourceMirrored: true });
  await mockApi.saveAnalysis('p', { locked: false, detailRecommendations: null, sourceMirrored: null, changedU3: 'new' });
  const saved = await mockApi.getAnalysis('p');
  assert.equal(saved.locked, true);
  assert.deepEqual(saved.detailRecommendations, { status: 'ready' });
  assert.equal(saved.sourceMirrored, true);
  assert.equal(saved.changedU3, 'new');
});

test('M9 mock quote change creates no job or charge before a fresh click', async () => {
  const { DB } = await vite.ssrLoadModule('/src/mock/db.js');
  const credits = DB.account.credits, count = DB.mannequins.length, job = DB.project.latestJobs.mannequin;
  const quote = (await mockApi.getCreditQuote('p')).mannequinRegenerate;
  await assert.rejects(mockApi.regenerateMannequin('p', { expectedQuote: expectedMannequinQuote(quote.nextCost + 1) }),
    (error) => error.code === 'quote_changed' && error.meta.quote.credits === quote.nextCost);
  assert.equal(DB.account.credits, credits); assert.equal(DB.mannequins.length, count);
  assert.equal(DB.project.latestJobs.mannequin, job);
});

test('M9 input-authorized free regeneration stays the expected quote even if the next screen reads a paid quote', async () => {
  const confirmation = createMannequinQuoteConfirmation(memoryStorage());
  const source = read('src/features/product-input/ProductInput.jsx');
  const guardSource = source.slice(source.indexOf('  const guardMannequinCredits ='), source.indexOf('  // 콘티 이동'));
  const bindings = { session: {}, useAppStore: { getState: () => ({ account: { plan: 'basic', credits: 100 } }) },
    normalizePlanTier: () => 'basic', creditQuote: { plan: 'basic', mannequinGenerate: { selectedModelId: 'model' }, mannequinRegenerate: { nextCost: 0 } },
    analysis: { selectedModelId: 'model' }, generationWorkKind: 'cuts', analysisProjectId: 'p',
    mannequinGenerationCreditShortfall: () => null, setCreditShortfall() {}, mannequinQuoteConfirmation: confirmation, expectedMannequinQuote };
  const guard = new Function(...Object.keys(bindings), `${guardSource}; return guardMannequinCredits;`)(...Object.values(bindings));
  assert.equal(guard(), true);
  bindings.mannequinGenerationCreditShortfall = () => ({ requiredCredits: 100 });
  bindings.creditQuote.mannequinRegenerate.nextCost = 100;
  const blockedGuard = new Function(...Object.keys(bindings), `${guardSource}; return guardMannequinCredits;`)(...Object.values(bindings));
  assert.equal(blockedGuard(), false);
  assert.deepEqual(confirmation.seen('p', 'regenerate'), { free: true, credits: 0 });
  const mannequin = read('src/features/mannequin/Mannequin.jsx');
  const expression = mannequin.match(/expectedQuote: \(!manual && mannequinQuoteConfirmation.seen\(generationProjectId, 'regenerate'\)\)\s*\|\| expectedMannequinQuote\(regenerationQuote.nextCost\)/)[0].replace('expectedQuote: ', '');
  const seen = new Function('manual', 'mannequinQuoteConfirmation', 'generationProjectId', 'expectedMannequinQuote', 'regenerationQuote', `return ${expression};`)(false, confirmation, 'p', expectedMannequinQuote, { nextCost: 45 });
  assert.deepEqual(seen, { free: true, credits: 0 });
  await assert.rejects(confirmation.request('p', 'regenerate', async ({ expectedQuote }) => {
    assert.deepEqual(expectedQuote, seen);
    throw Object.assign(new Error('changed'), { status: 409, code: 'quote_changed', meta: { quote: expectedMannequinQuote(45) } });
  }, { expectedQuote: seen }), { code: 'quote_changed' });
  assert.equal(confirmation.pending('p').quote.credits, 45);
});
