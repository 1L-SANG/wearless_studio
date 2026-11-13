import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';
import { editorSlotKind } from '../../src/lib/editorSlotKind.js';
import { canvasAiHookHarness } from './helpers/editorCanvasAiHarness.mjs';

const cases = JSON.parse(readFileSync(new URL('../fixtures/page_templates_cases.json', import.meta.url), 'utf8')).cases;
function assembledPhoto(template, kind, { failed = false, blank = false } = {}) {
  const input = structuredClone(cases.find(value => value.name === `${template}-1-mono`).input);
  const source = input.storyboard.find(block => block.source === 'ai' && block.pageSectionKind === kind);
  source.matchIds = kind === 'points' ? [] : ['matching-retained'];
  input.analysis = { ...input.analysis, selectedModelId: 'selected-model' };
  if (failed || blank) input.cutResults = input.cutResults.filter(cut => cut.blockId !== source.id);
  if (failed) input.failedCuts = { [source.id]: { reason: 'temporary_error', retryable: true } };
  const blocks = assemblePage(input);
  const block = blocks.find(block => block.elements.some(element => element.type === 'image' && element.sourceBlockId === source.id));
  const element = block.elements.find(element => element.type === 'image' && element.sourceBlockId === source.id);
  assert.equal(block.kind, 'info');
  assert.equal(block.pageSectionKey, `${template}.${kind}`);
  assert.notEqual(element.sourceBlockId, block.id);
  return { input, source, blocks, block, element };
}

for (const [template, kind] of [['emo', 'points'], ['emo', 'review'], ['emo', 'faq'], ['show', 'points']]) {
  test(`WOC17 actual ${template} ${kind} info photos use normal slot precedence and storyboard linkage`, () => {
    const failed = assembledPhoto(template, kind, { failed: true });
    const classify = element => editorSlotKind(failed.block, element, failed.input.storyboard);
    assert.equal(failed.element.genFailed, true);
    assert.equal(classify(failed.element), 'planned');
    assert.equal(classify({ ...failed.element, genPending: true }), 'pending');
    assert.equal(classify({ ...failed.element, src: '/existing.jpg', genPending: true }), 'filled');
    const blank = assembledPhoto(template, kind, { blank: true });
    assert.equal(blank.element.genFailed, undefined);
    assert.equal(editorSlotKind(blank.block, blank.element, blank.input.storyboard), 'planned');
    assert.equal(editorSlotKind(blank.block, { ...blank.element, sourceBlockId: undefined }, blank.input.storyboard), 'unplanned');
    assert.equal(editorSlotKind(blank.block, blank.element, [{ ...blank.source, source: 'mine' }]), 'unplanned');
  });

  test(`WOC17 actual ${template} ${kind} retry menu submits the image source recipe`, async () => {
    const { input, source, blocks, block, element } = assembledPhoto(template, kind, { failed: true });
    const sent = [], results = [];
    const h = canvasAiHookHarness({ blocks, selectedId: element.id, storyboard: input.storyboard,
      product: input.product, analysis: input.analysis, handlers: { onResult: (...args) => results.push(args) },
      api: { generateImage: async (projectId, request) => { sent.push({ projectId, request }); return { data: { src: '/retried.png' } }; } } });
    try {
      assert.equal(h.hook.menu.kind, 'planned');
      const action = h.hook.menu.branches.find(branch => branch.id === 'retry');
      assert.equal(action.label, '원래 콘티대로 다시');
      assert.equal(action.disabled, false);
      const job = h.start(action.action);
      assert.equal(job.blockId, block.id);
      assert.equal(job.elementId, element.id);
      await h.run();
      assert.equal(sent.length, 1);
      assert.equal(sent[0].projectId, 'project');
      const request = sent[0].request;
      assert.equal(request.mode, 'new');
      assert.equal(request.retryBlockId, source.id);
      assert.equal(request.cutType, kind === 'points' ? 'product' : 'styling');
      assert.equal(request.shot, kind === 'points' ? 'detail' : 'full');
      assert.equal(request.contentRole, kind === 'points' ? 'detail' : 'coordination');
      assert.equal(request.colorId, 'blue');
      assert.equal(request.direction, 'front');
      assert.equal(request.modelId, kind === 'points' ? null : 'selected-model');
      assert.deepEqual(request.matchIds, kind === 'points' ? [] : ['matching-retained']);
      if (kind === 'points') assert.equal(request.detailTargetId, 'collar');
      assert.equal(Object.hasOwn(request, 'pageSectionKey'), false);
      assert.equal(results.length, 1);
      assert.equal(results[0][0].elementId, element.id);
    } finally { h.hook.controller.dispose(); }
  });
}

test('WOC17 auto, legacy info, hidden and text remain unsupported even beside template photos', () => {
  const { input, blocks, block, element } = assembledPhoto('emo', 'review', { failed: true });
  for (const kind of ['size', 'care', 'ai-notice', 'common-notice']) {
    const auto = blocks.find(item => item.kind === kind);
    assert.ok(auto, kind);
    assert.equal(editorSlotKind({ ...auto, pageSectionKey: 'emo.notice' }, element, input.storyboard), 'unsupported');
    assert.equal(editorSlotKind({ ...auto, kind: 'info', pageSectionKey: 'emo.notice' }, element, input.storyboard), 'unsupported');
    assert.equal(editorSlotKind({ ...block, kind, auto: false }, element, input.storyboard), 'unsupported');
  }
  for (const pageSectionKey of [undefined, '', '   ', 42]) {
    assert.equal(editorSlotKind({ ...block, pageSectionKey }, element, input.storyboard), 'unsupported');
  }
  assert.equal(editorSlotKind({ ...block, hidden: true }, element, input.storyboard), 'unsupported');
  assert.equal(editorSlotKind(block, { ...element, hidden: true }, input.storyboard), 'unsupported');
  assert.equal(editorSlotKind(block, { ...element, type: 'text' }, input.storyboard), 'unsupported');
});
