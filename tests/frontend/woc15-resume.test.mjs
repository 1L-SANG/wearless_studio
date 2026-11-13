import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as notices from '../../src/lib/pageTemplates/entryNotices.js';
import * as flowSession from '../../src/lib/flowSession.js';

const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const evaluate = (source, bindings, result) => new Function(...Object.keys(bindings), `${source}; return ${result};`)(...Object.values(bindings));
const library = read('src/features/library/Library.jsx');
const app = read('src/apps/seller/App.jsx');
const mannequin = read('src/features/mannequin/Mannequin.jsx');
const message = '콘티가 새 방식으로 바뀌었어요. 확인하고 다음을 눌러 주세요';
const memoryStorage = () => {
  const values = new Map();
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
};
function harness(version = 3) {
  const calls = [], project = { id: 'p', status: 'draft', selectedMannequinId: 'cut', latestJobs: {} };
  const state = { projectId: 'p', projectPersisted: true, projectGeneration: 1, userId: 'seller', resumePath: '/create/mannequin',
    confirmProductInfo() {}, markFlowUsed() {}, setResumePath(path) { state.resumePath = path; } };
  const api = { getProject: async () => project,
    getStoryboard: async id => { notices.setStoryboardEntryNotice(id, version === 3 ? 'rebuilt' : null); return [{ id: 'photo', taxonomyVersion: 4 }]; },
    storyboardEntryNotice: notices.storyboardEntryNotice,
    saveAnalysis: async () => calls.push(['analysis']), saveStoryboard: async () => calls.push(['save']) };
  const bindings = { isMannequinStageEnabled: () => true, ...notices, ...flowSession, api, useAppStore: { getState: () => state },
    navigate: path => calls.push(['navigate', path]), push: msg => calls.push(['toast', msg]), pushToast: msg => calls.push(['toast', msg]),
    userId: 'seller', projectId: 'p', resumeRunRef: { current: 0 }, loadRunRef: { current: 0 },
    isMobileDevice: () => false, openingRef: { current: false }, entryToken: { current: 0 }, userRef: { current: 'seller' },
    setOpeningId() {}, adoptProject() {}, draftSlot: { resumeEntryWrites() {} }, setEntryDecision() {}, setEntryError() {},
    busy: false, submittingRef: { current: false }, setBusy() {}, aiCutCount: 3,
    detailPageGenerationCreditShortfall: () => null, setCreditShortfall() {},
    cutFitProfileRecord: () => ({ recorded: false }), selected: { id: 'cut' }, selectionFitSaveRef: { current: Promise.resolve() },
    selectMannequin: async () => calls.push(['selection']), setAnalysis() {}, setResumePath: state.setResumePath,
  };
  return { api, state, project, calls, bindings };
}
function withStorage(t) {
  const previous = globalThis.sessionStorage;
  globalThis.sessionStorage = memoryStorage();
  t.after(() => { globalThis.sessionStorage = previous; });
}

for (const version of [3, 4]) test(`WOC15 Library v${version} resume uses the saved storyboard review requirement`, async t => {
  withStorage(t);
  const h = harness(version);
  const source = library.slice(library.indexOf('const resumePath ='), library.indexOf('export function Library'))
    + library.slice(library.indexOf('  const onResume ='), library.indexOf('  // 서버 상태는'));
  await evaluate(source, h.bindings, 'onResume')({ id: 'p' });
  const path = version === 3 ? '/create/storyboard' : '/create/mannequin';
  assert.deepEqual(h.calls.filter(c => c[0] === 'navigate'), [['navigate', path]]);
  assert.equal(h.state.resumePath, path);
  assert.equal(flowSession.readFlowSession().path, path);
  assert.deepEqual(h.calls.filter(c => c[0] === 'toast'), version === 3 ? [['toast', message]] : []);
  assert.equal(h.calls.some(c => c[0] === 'save'), false);
});

for (const version of [3, 4]) test(`WOC15 input-page v${version} resume records the actual destination`, async t => {
  withStorage(t);
  const h = harness(version);
  const source = app.slice(app.indexOf('  const resume = async ({ resumeWrites'), app.indexOf('  const startNew = () => mobile'));
  await evaluate(`let flowRouteSeenThisSession = false; ${source}`, h.bindings, 'resume')();
  const path = version === 3 ? '/create/storyboard' : '/create/mannequin';
  assert.deepEqual(h.calls.filter(c => c[0] === 'navigate'), [['navigate', path]]);
  assert.equal(h.state.resumePath, path);
  assert.equal(flowSession.readFlowSession().path, path);
});

for (const version of [3, 4]) test(`WOC15 Mannequin v${version} generation continuation checks the persisted storyboard`, async t => {
  withStorage(t);
  const h = harness(version);
  const source = mannequin.slice(mannequin.indexOf('  const continueWithSelectedVersion ='), mannequin.indexOf('  const onContinueCurrentVersion ='));
  await evaluate(source, h.bindings, 'continueWithSelectedVersion')({ version: 2 });
  assert.deepEqual(h.calls.filter(c => c[0] === 'navigate'), [['navigate', version === 3 ? '/create/storyboard' : '/create/generating']]);
  assert.equal(h.calls.filter(c => c[0] === 'selection').length, version === 3 ? 0 : 1);
  assert.equal(h.calls.filter(c => c[0] === 'analysis').length, version === 3 ? 0 : 1);
  if (version === 3) {
    assert.equal(h.state.resumePath, '/create/storyboard');
    assert.equal(flowSession.readFlowSession().path, '/create/storyboard');
    assert.deepEqual(h.calls.filter(c => c[0] === 'toast'), [['toast', message]]);
  }
});

test('WOC15 unavailable storyboard blocks generation continuation', async t => {
  withStorage(t);
  const h = harness(4);
  h.api.getStoryboard = async () => { throw new Error('offline'); };
  const source = mannequin.slice(mannequin.indexOf('  const continueWithSelectedVersion ='), mannequin.indexOf('  const onContinueCurrentVersion ='));
  await evaluate(source, h.bindings, 'continueWithSelectedVersion')({ version: 2 });
  assert.equal(h.calls.some(c => ['navigate', 'selection', 'analysis'].includes(c[0])), false);
  assert.equal(h.bindings.submittingRef.current, false);
});

test('WOC15 rebuilt storyboard exit flush cannot persist before Next confirmation', async () => {
  const source = read('src/features/storyboard/Storyboard.jsx');
  notices.setStoryboardEntryNotice('review', 'rebuilt');
  const persisted = [];
  const bindings = { ...notices, api: { storyboardEntryNotice: notices.storyboardEntryNotice },
    saveTimer: { current: null }, latestBlocks: { current: [{ id: 'new', taxonomyVersion: 4 }] },
    sbSaveNow: async (_id, snapshot) => persisted.push(snapshot()), clearTimeout() {} };
  const callback = source.slice(source.indexOf('  const flushLatest ='), source.indexOf('  const saveNow ='));
  await evaluate(callback, bindings, 'flushLatest')('review', { keepalive: true });
  assert.deepEqual(persisted, []);
  assert.equal(notices.storyboardEntryNotice('review'), 'rebuilt');
});

test('WOC15 mock storyboard reads retain the old persisted board until explicit save', async () => {
  const source = read('src/mock/api.js');
  const method = source.slice(source.indexOf('  async getStoryboard('), source.indexOf('  storyboardEntryNotice,'));
  const saved = [{ id: 'old', taxonomyVersion: 3 }], rebuilt = [{ id: 'new', taxonomyVersion: 4 }];
  const DB = { storyboard: saved, product: {}, analysis: { pageStyle: {} }, project: { id: 'mock-review', status: 'draft' } };
  const api = evaluate(`const api = { ${method} };`, { ...notices, DB, wait: async () => {}, clone: structuredClone,
    resolveStoryboardEntry: board => ({ blocks: board === saved ? rebuilt : board, notice: board === saved ? 'rebuilt' : null }),
    identityKindOf: () => null, isRealModelSelection: () => false }, 'api');
  assert.deepEqual(await api.getStoryboard('mock-review'), rebuilt);
  assert.equal(DB.storyboard, saved);
  assert.deepEqual(await api.getStoryboard('mock-review'), rebuilt);
  assert.equal(notices.storyboardEntryNotice('mock-review'), 'rebuilt');
});

test('WOC15 an abandoned app resume cannot overwrite the current route metadata', async t => {
  withStorage(t);
  const h = harness(3);
  let release;
  h.api.getStoryboard = () => new Promise(resolve => { release = resolve; });
  const source = app.slice(app.indexOf('  const resume = async ({ resumeWrites'), app.indexOf('  const startNew = () => mobile'));
  const pending = evaluate(`let flowRouteSeenThisSession = false; ${source}`, h.bindings, 'resume')();
  h.bindings.resumeRunRef.current += 1;
  release([{ taxonomyVersion: 3 }]);
  await pending;
  assert.deepEqual(h.calls, []);
  assert.equal(h.state.resumePath, '/create/mannequin');
  assert.equal(flowSession.readFlowSession(), null);
});

test('WOC15 a missing storyboard response cannot start generation', async t => {
  withStorage(t);
  const h = harness(4);
  h.api.getStoryboard = async () => null;
  const source = mannequin.slice(mannequin.indexOf('  const continueWithSelectedVersion ='), mannequin.indexOf('  const onContinueCurrentVersion ='));
  await evaluate(source, h.bindings, 'continueWithSelectedVersion')({ version: 2 });
  assert.equal(h.calls.some(c => ['navigate', 'selection', 'analysis'].includes(c[0])), false);
});

for (const outcome of ['success', 'failure']) test(`WOC15 Next ${outcome} controls rebuilt notice and persists only confirmed blocks`, async () => {
  const source = read('src/features/storyboard/Storyboard.jsx');
  notices.setStoryboardEntryNotice('review', 'rebuilt');
  const snapshots = [], board = [{ id: 'new', taxonomyVersion: 4 }];
  let screenNotice = 'rebuilt';
  const bindings = { ...notices, api: { storyboardEntryNotice: notices.storyboardEntryNotice },
    saveTimer: { current: null }, latestBlocks: { current: board }, clearTimeout() {},
    setEntryNotice: value => { screenNotice = value; },
    sbSaveNow: async (_id, getSnapshot, options) => {
      snapshots.push({ blocks: getSnapshot(), options });
      if (outcome === 'failure') throw new Error('offline');
    } };
  const callback = source.slice(source.indexOf('  const flushLatest ='), source.indexOf('  // 저장이 다시 성공하면'));
  const save = evaluate(callback, bindings, 'saveNow');
  if (outcome === 'failure') await assert.rejects(save('review'), /offline/);
  else await save('review');
  assert.deepEqual(snapshots, [{ blocks: board, options: {} }]);
  assert.equal(notices.storyboardEntryNotice('review'), outcome === 'success' ? null : 'rebuilt');
  assert.equal(screenNotice, outcome === 'success' ? null : 'rebuilt');
});

for (const version of [3, 4, null]) test(`WOC15 direct Mannequin load with ${version ?? 'unavailable'} storyboard cannot bypass review`, async t => {
  withStorage(t);
  const h = harness(version);
  let generated = 0;
  Object.assign(h.state, { loadProject: async () => {}, selectedMannequinId: 'cut' });
  Object.assign(h.api, { getProduct: async () => ({}), getAnalysis: async () => ({}), getCatalogs: async () => ({}),
    getCreditQuote: async () => ({}), getMannequins: async () => [],
    getStoryboard: async id => {
      notices.setStoryboardEntryNotice(id, version === 3 ? 'rebuilt' : null);
      if (version == null) throw new Error('offline');
      return [{ taxonomyVersion: 4 }];
    } });
  const bindings = { ...h.bindings, useCallback: callback => callback, initialCutsExistedRef: { current: false },
    window: { scrollTo() {} }, document: { querySelector: () => null },
    generationProgressFor: () => 0, uniqueGenerationCutCount: () => 1, resolveMainMatchingItem: () => null,
    createFitProfileDraft: () => ({}), createCutFitProfileDraft: () => ({}), initStepState: () => ({}), axesFor: () => ({}),
    matchingFitDefinition: () => null, landedGenerationRelevantEditsAttemptRevision: () => null,
    cutsExistedBeforeInitialGeneration: () => false, hasActiveMannequinGeneration: () => false,
    resolveInitialGenerationCuts: async () => { generated += 1; return { cuts: [{ id: 'cut' }], cutsExisted: false }; },
    requestMannequinGeneration() {}, extractCuts: value => value, syncCredits() {}, clearInitialGenerationRequested() {},
    updateMannequinJob() {}, analysisForCutFitProfile: analysis => analysis };
  for (const key of ['Phase', 'ErrorMsg', 'OriginalError', 'Progress', 'Product', 'Analysis', 'Catalogs', 'CreditQuote', 'AiCutCount', 'FitProfileDraft', 'StepState', 'Cuts', 'ChangedQuote']) bindings[`set${key}`] = () => {};
  const callback = mannequin.slice(mannequin.indexOf('  const loadMannequins ='), mannequin.indexOf('  useEffect(() => {\n    loadMannequins();'));
  await evaluate(callback, bindings, 'loadMannequins')();
  assert.equal(generated, version === 4 ? 1 : 0);
  assert.deepEqual(h.calls.filter(c => c[0] === 'navigate'), version === 3 ? [['navigate', '/create/storyboard']] : []);
});
