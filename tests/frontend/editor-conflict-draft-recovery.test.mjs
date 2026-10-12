import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createConflictWorkCopies, restoreWorkCopyOnce } from '../../src/lib/conflictWorkCopy.js';
import { clearEditorWaitDraft, editorWaitDraftDecision, loadEditorWaitDraftEntry, saveEditorWaitDraft } from '../../src/lib/editorWaitDraft.js';

const source = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
const entry = source.slice(source.indexOf('  useEffect(() => {\n    setLoadError(null);'), source.indexOf('  // 상세페이지 생성 사진은'));
const backup = source.slice(source.indexOf('  const backupLatestEditorDraft ='), source.indexOf('  const [genFinalizeAttempt'));
const reload = source.slice(source.indexOf('  const reloadAfterEditorConflict ='), source.indexOf('  // 자동 저장', source.indexOf('  const reloadAfterEditorConflict =')));
const evaluate = (code, bindings, result = 'undefined') => new Function(...Object.keys(bindings), `${code}; return ${result};`)(...Object.values(bindings));
const identity = (value) => value;
const memoryStorage = () => {
  const values = new Map();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
};
const blocks = (text) => [{ id: 'text', text }];

function harness({ storage = memoryStorage(), copies = createConflictWorkCopies('editor', memoryStorage()), loadAttempt = 0, server = blocks('server'), serverRevision = 4 } = {}) {
  const view = {}, notices = [];
  const bindings = {
    projectId: 'p', loadAttempt, useEffect: (fn) => fn(), useCallback: (fn) => fn,
    flushSync: (fn) => fn(), document: { activeElement: { blur() {} } },
    useAppStore: { getState: () => ({ projectId: 'p', detailPageJob: {}, loadAccount: async () => ({}) }) },
    editorWorkCopies: copies, restoreWorkCopyOnce,
    loadEditorWaitDraftEntry: (id) => loadEditorWaitDraftEntry(id, storage),
    saveEditorWaitDraft: (id, value, _storage, options) => saveEditorWaitDraft(id, value, storage, options),
    clearEditorWaitDraft: (id) => clearEditorWaitDraft(id, storage), editorWaitDraftDecision,
    pendingGenerationDraft: { current: false }, editorConflictPending: { current: false },
    genActiveRef: { current: false }, wardrobeContext: { current: {} }, latestBlocks: { current: null },
    hist: { current: { past: [], future: [] } }, prevBlocks: { current: null }, fromHistory: { current: false },
    saveTimer: { current: null }, saveGuard: { current: { invalidate() {} } }, conflictReloadAt: { current: 0 },
    setLoadAttempt() {}, persistable: identity, isMockMode: true,
    api: { getEditorBlocks: async () => server, editorRevisionOf: () => serverRevision,
      getWardrobe: async () => ({}), getCatalogs: async () => ({}), getProduct: async () => ({ colors: [] }),
      getAnalysis: async () => ({}), getStoryboard: async () => [], getMatchClothing: async () => [] },
    withStoryboardSpaceSetExamples: identity, normalizeEditorBlockRole: identity, buildColorOpts: () => [], visibleColorOpts: identity,
    hexForCol: () => '', buildInfoCtx: () => ({}), needsDefaultTemplate: () => false,
    mergeServerBlocks: (draft) => draft, ensureShippingReturnsBlock: identity, upgradeLegacyKiwiTemplateBlocks: identity,
    stripPhotoBlockTextElements: identity, mergeEditorImagesIntoWardrobe: ({ wardrobe }) => wardrobe,
    uid: () => 'id', classifyEditorLoadError: identity, toast: { push: (message, options) => notices.push({ message, ...options }) },
  };
  for (const key of ['SaveError', 'LoadError', 'Blocks', 'Wardrobe', 'Catalogs', 'FmModels', 'SelBlock', 'ProductName', 'ClothingType', 'MatchClothing', 'Product', 'Analysis', 'DetailColorOpts', 'ColorOpts']) {
    bindings[`set${key}`] = (value) => { view[key] = value; };
  }
  bindings.backupLatestEditorDraft = evaluate(backup, bindings, 'backupLatestEditorDraft');
  return { storage, copies, view, notices, bindings,
    reload: () => evaluate(reload, bindings, 'reloadAfterEditorConflict')(),
    backup: (value) => bindings.backupLatestEditorDraft(value),
    async enter() { evaluate(entry, bindings); await new Promise(setImmediate); assert.equal(view.LoadError, null); },
  };
}

for (const loadAttempt of [0, 1]) test(`newer post-conflict draft restores without an older recovery action, load attempt ${loadAttempt}`, async () => {
  const h = harness({ loadAttempt });
  h.copies.keep('p', { blocks: blocks('before conflict'), history: { past: [], future: [] } });
  saveEditorWaitDraft('p', blocks('new edits after conflict'), h.storage, { baseRevision: 4 });
  await h.enter();
  assert.deepEqual(h.view.Blocks, blocks('new edits after conflict'));
  assert.deepEqual(h.copies.read('p').blocks, blocks('before conflict'));
  assert.equal(h.notices.some((notice) => notice.action), false);
  assert.deepEqual(loadEditorWaitDraftEntry('p', h.storage).blocks, blocks('new edits after conflict'));
});

test('backups taken during conflict reload stay quarantined, later backups restore normally', async () => {
  const h = harness({ loadAttempt: 1 });
  h.bindings.latestBlocks.current = blocks('before conflict');
  h.reload();
  h.bindings.latestBlocks.current = blocks('typed while fetching latest');
  h.backup(h.bindings.latestBlocks.current);
  assert.equal(loadEditorWaitDraftEntry('p', h.storage).conflictPending, true);
  await h.enter();
  assert.deepEqual(h.view.Blocks, blocks('server'));
  assert.deepEqual(h.copies.read('p').blocks, blocks('typed while fetching latest'));
  h.backup(blocks('new edits after conflict'));
  assert.notEqual(loadEditorWaitDraftEntry('p', h.storage).conflictPending, true);
  const reopened = harness({ storage: h.storage, copies: h.copies });
  await reopened.enter();
  assert.deepEqual(reopened.view.Blocks, blocks('new edits after conflict'));
});

test('refresh during conflict reload keeps the server visible and preserves the explicit recovery copy', async () => {
  const h = harness();
  h.bindings.latestBlocks.current = blocks('before conflict');
  h.reload();
  const reopened = harness({ storage: h.storage, copies: h.copies });
  await reopened.enter();
  assert.deepEqual(reopened.view.Blocks, blocks('server'));
  reopened.notices.find((notice) => notice.action).action.onClick();
  assert.deepEqual(reopened.view.Blocks, blocks('before conflict'));
});

test('a post-conflict draft cannot overwrite a newer server revision', async () => {
  const h = harness({ serverRevision: 5 });
  h.copies.keep('p', { blocks: blocks('before conflict') });
  saveEditorWaitDraft('p', blocks('unsaved revision four'), h.storage, { baseRevision: 4 });
  await h.enter();
  assert.deepEqual(h.view.Blocks, blocks('server'));
  assert.deepEqual(h.copies.read('p').blocks, blocks('before conflict'));
});

test('another tab draft cannot suppress recovery of live edits made while resolving a conflict', async () => {
  const h = harness({ loadAttempt: 1 });
  const before = blocks('A before conflict'), typed = blocks('A typed during reload');
  h.bindings.latestBlocks.current = before;
  h.reload();
  // 다른 탭은 같은 localStorage 키에 충돌하지 않은 자기 임시본을 저장할 수 있다.
  saveEditorWaitDraft('p', blocks('B current draft'), h.storage, { baseRevision: 4 });
  let resolveServer, blurred = false;
  h.bindings.api.getEditorBlocks = () => new Promise((resolve) => { resolveServer = resolve; });
  h.bindings.document.activeElement.blur = () => {
    blurred = true;
    h.bindings.latestBlocks.current = typed;
    h.bindings.hist.current = { past: [before], future: [] };
  };
  const entering = h.enter();
  resolveServer(blocks('server'));
  await entering;
  assert.equal(blurred, true);
  assert.deepEqual(h.copies.read('p').blocks, typed);
  h.notices.find((notice) => notice.action).action.onClick();
  assert.deepEqual(h.view.Blocks, typed);
  assert.deepEqual(h.bindings.hist.current.past, [before]);
});
