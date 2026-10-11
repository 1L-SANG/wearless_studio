import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { buildFailedCutRetry } from '../../src/features/editor/failedCutRetry.js';
import { canvasAiHookHarness, editorCanvasAiHandlers } from './helpers/editorCanvasAiHarness.mjs';
import { applyDetailJobEvents, detailPageDonePatch } from '../../src/lib/detailPageJobEvents.js';
import { decorateGenBlocks, fillGenBlocks, mergeServerBlocks } from '../../src/lib/editorWaitSkeleton.js';
import { applySlotFillToInfo } from '../../src/features/editor/presets/infoPresets.js';
import { dropUntouchedPlaceholders } from '../../src/features/editor/presets/textPresets.js';
import { fitImageToFrameBlock } from '../../src/features/editor/editorImageDrop.js';

// Saved A and B differ, but runtime fallback generated both from the first cut.
const root = { id: 'runtime-root', source: 'ai', cutType: 'horizon', shot: 'full',
  direction: 'front', colorId: 'base', matchIds: ['A'], exampleId: 'ref', refScope: 'all' };
const child = { ...root, id: 'child', matchIds: ['B'] };
const storyboard = [root, child, { ...root, id: 'independent', direction: 'back', matchIds: [] }];
const failure = { genFailed: true, genFailReason: 'duplicate_cut_source_failed',
  genFailRetryable: false, genFailSourceBlockId: 'runtime-root' };
const rootImage = { id: 'root-image', type: 'image', sourceBlockId: 'runtime-root',
  src: '/v1/assets/root/file', cutType: 'horizon' };
const image = (patch = {}) => ({ id: 'child-image', type: 'image', sourceBlockId: 'child',
  src: null, ...patch });
const job = () => ({ status: 'running', cuts: {}, copy: {}, live: [], failedCuts: [], failedCutReasons: {} });
const failureEvent = { type: 'step', payload: { blockId: 'child', status: 'cut_failed',
  reason: 'duplicate_cut_source_failed', retryable: false, sourceBlockId: 'runtime-root' } };
const retry = (blocks, options = {}) => buildFailedCutRetry(options.storyboard || storyboard, 'child', null, null,
  { editorBlocks: blocks, failure: options.failure || failure });

test('runtime duplicate recovery fills the recorded root despite different saved matching garments', () => {
  const result = retry([{ elements: [rootImage] }]);
  assert.equal(result.retryable, true);
  assert.equal(result.request, null, 'copying the root must not start generation or charge credits');
  assert.equal(result.fillImage.src, '/v1/assets/root/file');
  assert.equal(result.sourceBlockId, 'child', 'the failed slot keeps its own storyboard identity');
});

test('an explicit root waits for that cut and overrides a misleading matching recipe', () => {
  const misleading = { ...child, id: 'misleading' };
  const saved = [misleading, child, root];
  const otherImage = { ...rootImage, sourceBlockId: 'misleading', src: '/v1/assets/wrong/file' };
  const blocked = retry([{ elements: [otherImage] }], { storyboard: saved });
  assert.equal(blocked.retryable, false);
  assert.equal(blocked.fillImage, null);
  assert.equal(blocked.request, null);
  const ready = retry([{ elements: [otherImage, rootImage] }], { storyboard: saved });
  assert.equal(ready.retryable, true);
  assert.equal(ready.fillImage.src, '/v1/assets/root/file');
});

test('missing, self and non-AI explicit roots stay blocked even when a legacy recipe matches', () => {
  const legacy = { ...child, id: 'legacy' };
  const mine = { ...root, id: 'mine', source: 'mine' };
  const invalid = { ...root, id: 'invalid', cutType: null };
  const saved = [legacy, child, root, mine, invalid, { ...root, id: 7 }, { ...root, id: null }];
  const elements = ['legacy', 'child', 'foreign', 'mine', 'invalid', 7, null].map(sourceBlockId =>
    ({ ...rootImage, sourceBlockId, src: `/v1/assets/${sourceBlockId}/file` }));
  for (const genFailSourceBlockId of ['foreign', 'child', 'mine', 'invalid', '', null, 7]) {
    const result = retry([{ elements }], { storyboard: saved, failure: { ...failure, genFailSourceBlockId } });
    assert.equal(result.retryable, false, `invalid root ${String(genFailSourceBlockId)}`);
    assert.equal(result.fillImage, null);
    assert.equal(result.request, null);
  }
});

test('recorded roots with pending, failed or cleared images are not ready to fill a duplicate', () => {
  for (const patch of [{ src: null }, { src: '' }, { genPending: 'wait' }, { genPending: 'live' },
    { genPending: 'done' }, { genPending: 'failed' }, { genFailed: true }, { slotCleared: true }]) {
    const result = retry([{ elements: [{ ...rootImage, ...patch }] }],
      { storyboard: [root, { ...child, matchIds: ['A'] }] });
    assert.equal(result.retryable, false, JSON.stringify(patch));
    assert.equal(result.fillImage, null);
    assert.equal(result.request, null);
  }
});

test('legacy duplicate failures keep the prior same-recipe lookup without a root marker', () => {
  const legacyFailure = { genFailReason: 'duplicate_cut_source_failed', genFailRetryable: false };
  const saved = [{ ...child, id: 'legacy' }, child];
  const result = retry([{ elements: [{ ...rootImage, sourceBlockId: 'legacy' }] }],
    { storyboard: saved, failure: legacyFailure });
  assert.equal(result.retryable, true);
  assert.equal(result.request, null);
  assert.equal(result.fillImage.sourceBlockId, 'legacy');
  assert.equal(retry([{ elements: [rootImage] }], { failure: legacyFailure }).retryable, false);
});

test('cut failure event replay keeps the actual root separately from the failed child id', () => {
  const initial = job();
  const result = applyDetailJobEvents(initial, [failureEvent]);
  assert.deepEqual(result.failedCutReasons.child, { reason: 'duplicate_cut_source_failed',
    retryable: false, sourceBlockId: 'runtime-root' });
  assert.deepEqual(result.failedCuts, ['child']);
  assert.deepEqual(applyDetailJobEvents(result, [failureEvent]).failedCutReasons, result.failedCutReasons);
  assert.deepEqual(initial.failedCutReasons, {});
});

test('done reconciliation adds partial failure roots and preserves earlier event-only failures', () => {
  const current = applyDetailJobEvents(job(), [{ type: 'step', payload: {
    blockId: 'other-failed', status: 'cut_failed', reason: 'provider_error', retryable: true,
  } }]);
  const partial = { requestedCuts: 3, completedCuts: 1, failedCuts: [failureEvent.payload] };
  const patch = detailPageDonePatch({ result: { partial } }, current);
  assert.deepEqual(patch.failedCutReasons, {
    'other-failed': { reason: 'provider_error', retryable: true },
    child: { reason: 'duplicate_cut_source_failed', retryable: false, sourceBlockId: 'runtime-root' },
  });
  assert.deepEqual(patch.failedCuts, ['other-failed', 'child']);
  assert.deepEqual(patch.partial, partial);
});

test('live decoration and event fill retain failure root metadata on the child slot', () => {
  const failed = applyDetailJobEvents(job(), [failureEvent]);
  const blocks = [{ id: 'page', elements: [image()] }];
  for (const result of [decorateGenBlocks(blocks, failed, {}),
    fillGenBlocks(decorateGenBlocks(blocks, job(), {}), failed)]) {
    assert.equal(result[0].elements[0].sourceBlockId, 'child');
    assert.equal(result[0].elements[0].genFailSourceBlockId, 'runtime-root');
    assert.equal(result[0].elements[0].genFailReason, 'duplicate_cut_source_failed');
  }
});

test('completion and refresh merges preserve the root from server, events, partial and saved local slots', () => {
  const plain = [{ id: 'page', elements: [image()] }];
  const saved = [{ id: 'page', elements: [image(failure)] }];
  for (const local of [plain, []]) {
    const merged = mergeServerBlocks(local, saved, new Set());
    assert.equal(merged[0].elements[0].genFailSourceBlockId, 'runtime-root');
    const refreshed = mergeServerBlocks([], JSON.parse(JSON.stringify(merged)));
    assert.equal(refreshed[0].elements[0].sourceBlockId, 'child');
    assert.equal(refreshed[0].elements[0].genFailSourceBlockId, 'runtime-root');
  }
  const serverWithoutRoot = [{ id: 'page', elements: [image({ genFailed: true,
    genFailReason: 'duplicate_cut_source_failed', genFailRetryable: false })] }];
  for (const reasons of [{ child: failureEvent.payload }, applyDetailJobEvents(job(), [failureEvent]).failedCutReasons]) {
    for (const local of [plain, []]) {
      assert.equal(mergeServerBlocks(local, serverWithoutRoot, new Set(['child']), reasons)
        [0].elements[0].genFailSourceBlockId, 'runtime-root');
    }
  }
  assert.equal(mergeServerBlocks(saved, plain)[0].elements[0].genFailSourceBlockId, 'runtime-root');
});

test('successful generation decoration, event fill and stable merge clear the failure root', () => {
  const blocks = [{ id: 'page', elements: [image({ ...failure, genPending: 'failed' })] }];
  const complete = { ...job(), cuts: { child: { url: '/v1/assets/child/file', done: true } } };
  const server = [{ id: 'page', elements: [image({ src: '/v1/assets/child/file' })] }];
  for (const result of [decorateGenBlocks(blocks, complete, {}), fillGenBlocks(blocks, complete),
    mergeServerBlocks(blocks, server, new Set())]) {
    const filled = result[0].elements[0];
    assert.equal(filled.src, '/v1/assets/child/file');
    assert.equal(filled.sourceBlockId, 'child');
    for (const key of ['genFailed', 'genFailReason', 'genFailRetryable', 'genFailSourceBlockId']) {
      assert.equal(key in filled, false, key);
    }
  }
});

// Execute the actual Editor handlers and persistence gate, as the existing retry tests do.
const editor = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
const source = (start, end) => editor.slice(editor.indexOf(start), editor.indexOf(end, editor.indexOf(start)));
const evaluate = (body, scope) => new Function(...Object.keys(scope), body)(...Object.values(scope));

test('the actual completion handler preserves an event root when partial omits that optional field', () => {
  const finalJob = { failedCuts: ['child'], failedCutReasons: { child: failureEvent.payload },
    partial: { failedCuts: [{ blockId: 'child', reason: 'duplicate_cut_source_failed', retryable: false }] } };
  const { failedCutIds, failedReasons } = evaluate(
    `${source('        const failedCutIds =', '        let restoredServerLayout =')}; return { failedCutIds, failedReasons };`,
    { finalJob });
  const blocks = [{ id: 'page', elements: [image()] }];
  const saved = [{ id: 'page', elements: [image({ genFailed: true,
    genFailReason: 'duplicate_cut_source_failed', genFailRetryable: false })] }];
  const merged = mergeServerBlocks(blocks, saved, failedCutIds, failedReasons);
  assert.equal(merged[0].elements[0].sourceBlockId, 'child');
  assert.equal(merged[0].elements[0].genFailSourceBlockId, 'runtime-root');
});

test('selecting a saved duplicate forwards its root and free fill clears every failure marker', async () => {
  let blocks = [{ id: 'page', elements: [rootImage, image(failure)] }];
  let pendingSlot = null;
  const scope = {
    applySlotFillToInfo, fitImageToFrameBlock,
    setBlocks: update => { blocks = update(blocks); },
    selectEl: () => {}, setTab: () => {}, setPendingSlot: value => { pendingSlot = value; },
  };
  const persistable = evaluate(`${source('  const persistable =', '\n')}; return persistable;`,
    { dropUntouchedPlaceholders, FRESH_TEXT_IDS: new Set() });
  blocks = JSON.parse(JSON.stringify(persistable(blocks)));
  const requestSlotImage = evaluate(`${source('  const requestSlotImage =', '  const dropSlotImage =')}; return requestSlotImage;`, scope);
  requestSlotImage('page', blocks[0].elements[1]);
  assert.equal(pendingSlot.sourceBlockId, 'child');
  assert.equal(pendingSlot.genFailSourceBlockId, 'runtime-root');
  const failedCutRetry = buildFailedCutRetry(storyboard, pendingSlot.sourceBlockId, null, null,
    { failure: pendingSlot, editorBlocks: blocks });
  assert.equal(failedCutRetry.retryable, true);
  const setSlotImage = evaluate(`${source('  const setSlotImage =', '  const insertImage =')}; return setSlotImage;`, scope);
  const api = { generateImage: async () => assert.fail('free fill cannot start a paid generation') };
  const handlers = editorCanvasAiHandlers({ blocks, latestBlocks: { current: blocks }, api, setSlotImage });
  const h = canvasAiHookHarness({ blocks, storyboard, selectedId: blocks[0].elements[1].id, api, handlers });
  h.start('retry'); await h.run();
  const filled = JSON.parse(JSON.stringify(persistable(blocks)))[0].elements[1];
  assert.equal(filled.src, '/v1/assets/root/file');
  assert.equal(filled.sourceBlockId, 'child');
  for (const key of ['genFailed', 'genFailReason', 'genFailRetryable', 'genFailSourceBlockId']) {
    assert.equal(key in filled, false, key);
  }
});
