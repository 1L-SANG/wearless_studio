import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { copyEditorBlock, copyEditorElements, pasteEditorBlock, pasteEditorElements } from '../../src/features/editor/editorClipboard.js';
import { getBlockExportElements } from '../../src/features/editor/editorGeometry.js';
import { editedTextElement } from '../../src/features/editor/editorTextEdit.js';
import * as wait from '../../src/lib/editorWaitSkeleton.js';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';

const { cases } = JSON.parse(readFileSync(new URL('../fixtures/page_templates_cases.json', import.meta.url)));
const input = name => structuredClone(cases.find(item => item.name === `woc20-${name}`).input);
const faq = blocks => blocks.find(block => block.pageSectionKind === 'faq');
const element = (block, role) => block.elements.find(item => item.copyRole === role);
const photo = block => block.elements.find(item => item.type === 'image');

function pendingFaq(template = 'emo') {
  const value = { ...input(`${template}-faq-pending`), cutResults: [], copyResults: [] };
  const pending = wait.decorateGenBlocks(assemblePage(value), { status: 'running', cuts: {}, copy: {} }, {});
  const source = value.storyboard.find(card => card.source !== 'text');
  const final = assemblePage({ ...value, cutResults: [{ blockId: source.id, imageUrl: 'photo:final', width: 1000, height: 1250,
    overlayTextPositions: { [`${source.pageSectionKey}:${source.slotKey}:heading`]: 'outside' } }] });
  return { pending, final };
}

test('FAQ headings are locked through generation and final merge without writing locks into the document', () => {
  const { pending } = pendingFaq();
  const before = structuredClone(pending);
  const headingIds = new Set(['label', 'title'].map(role => element(faq(pending), role).id));
  for (const status of ['running', 'done']) {
    assert.deepEqual(wait.pendingFaqHeadingIds(pending, { active: true, status }), headingIds);
  }
  assert.deepEqual(wait.pendingFaqHeadingIds(pending, { active: false, status: 'done' }), new Set());
  for (const status of ['error', 'blocked']) {
    assert.deepEqual(wait.pendingFaqHeadingIds(pending, { active: true, status }), new Set());
  }
  assert.deepEqual(wait.pendingFaqHeadingIds(pending, { active: true, status: 'done', finalizeError: true }), new Set());
  assert.deepEqual(pending, before);
});

for (const template of ['emo', 'show']) {
  test(`${template} FAQ keeps authored text and typography when applying the outside heading layout`, () => {
    const { pending, final } = pendingFaq(template);
    const block = faq(pending);
    const label = element(block, 'label');
    const title = element(block, 'title');
    block.elements = block.elements.map(item => item === label
      ? editedTextElement(label, { style: { ...label.style, italic: !label.style.italic } })
      : item === title ? editedTextElement(title, { text: '직접 쓴 FAQ 제목', style: { ...title.style, font: 'Pretendard' } }) : item);

    const saved = faq(wait.mergeServerBlocks(pending, final));
    assert.equal(photo(saved).y, 246);
    assert.equal(element(saved, 'label').y, 120);
    assert.equal(element(saved, 'label').style.color, element(faq(final), 'label').style.color);
    assert.equal(element(saved, 'label').style.italic, !label.style.italic);
    assert.equal(element(saved, 'title').text, '직접 쓴 FAQ 제목');
    assert.equal(element(saved, 'title').style.font, 'Pretendard');
    assert.equal(saved.elements.some(item => item.opacity === .3), false);
  });
}

test('FAQ with a manually positioned heading keeps the white on-photo label and scrim', () => {
  const { pending, final } = pendingFaq();
  const block = faq(pending);
  element(block, 'title').y += 50;
  const saved = faq(wait.mergeServerBlocks(pending, final));
  assert.equal(photo(saved).y, 0);
  assert.equal(element(saved, 'label').y, 68);
  assert.equal(element(saved, 'label').style.color, '#ffffff');
  assert.equal(element(saved, 'title').y, 167);
  assert.equal(saved.elements.some(item => item.opacity === .3), true);
});

test('the selected FAQ question and photo retain their identity when the server moves the title', () => {
  const { pending, final } = pendingFaq();
  const block = faq(pending);
  const selected = [element(block, 'items.0.q'), photo(block), block.elements.find(item => item.templateCopyDecoration === 'A.')];
  const completed = faq(wait.mergeServerBlocks(pending, final));
  assert.equal(photo(completed).y, 246);
  assert.deepEqual(selected.map(item => [item.id, item.type, item.copyRole, item.templateCopyDecoration]),
    selected.map(({ id }) => {
      const item = completed.elements.find(candidate => candidate.id === id);
      return [item.id, item.type, item.copyRole, item.templateCopyDecoration];
    }));
  assert.equal(new Set(completed.elements.map(item => item.id)).size, completed.elements.length);
});

test('the editor commits a focused FAQ answer before merging and saving the measured block', async () => {
  const { pending, final } = pendingFaq();
  const block = faq(pending);
  const answer = element(block, 'items.0.a');
  const typedText = '사진 만들기를 기다리면서 작성한 답이에요';
  const latestBlocks = { current: pending };
  const saved = [];
  let finalizeError;
  const source = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
  const finalEffect = source.slice(source.indexOf("  useEffect(() => {\n    if (!genActive || dpJob.status !== 'done'"),
    source.indexOf('  // history (rapid bursts'));
  const commitSource = source.slice(source.indexOf('  const commitTextValue ='), source.indexOf('  // 내용이 빈 일반 텍스트'));
  const identity = value => value;
  const setBlocks = update => { latestBlocks.current = update(latestBlocks.current); };
  const compile = (code, bindings, result = '') => new Function(...Object.keys(bindings), code + result)(...Object.values(bindings));
  const commit = compile(commitSource, { setBlocks, editedTextElement }, 'return commitTextValue;');
  const activeElement = { isContentEditable: true, blur: () => commit(block.id, answer.id, typedText) };
  compile(finalEffect, {
    useEffect: effect => effect(), genActive: true, dpJob: { status: 'done' }, genMergedRef: { current: false },
    setGenFinalizeError: error => { finalizeError = error; }, setGenFinalizeOriginalError() {},
    getFinalEditorBlocks: async () => final, projectId: 'fixture',
    api: { getAnalysis: async () => null, saveEditorBlocks: async (_id, value) => { saved.push(value); } },
    useAppStore: { getState: () => ({ detailPageJob: {} }) }, latestBlocks,
    canvasRef: { current: { contains: item => item === activeElement } }, document: { activeElement }, flushSync: action => action(),
    canSafelyMergeServerBlocks: wait.canSafelyMergeServerBlocks, mergeServerBlocks: wait.mergeServerBlocks,
    stripPhotoBlockTextElements: identity, buildInfoCtx: () => ({}), needsDefaultTemplate: () => false,
    productName: '', clothingType: '', catalogs: {}, product: {}, analysis: {}, colorOpts: [], fmModels: {},
    ensureShippingReturnsBlock: identity, normalizeEditorSelectionGroups: identity, expandBlockHeights: identity,
    upgradeLegacyKiwiTemplateBlocks: identity, uid: identity, setBlocksState() {}, persistable: identity,
    clearEditorWaitDraft() {}, pendingGenerationDraft: { current: false }, genActiveRef: { current: true },
    setGenActive() {}, detailPageCompletionMessage: () => null, toast: { push() {} }, genFinalizeAttempt: 0,
  });
  await new Promise(setImmediate);
  assert.equal(finalizeError, '');
  assert.equal(saved.length, 1);
  const completed = faq(saved[0]);
  assert.equal(photo(completed).y, 246);
  assert.equal(element(completed, 'items.0.a').id, answer.id);
  assert.equal(element(completed, 'items.0.a').text, typedText);
});

test('copying an empty FAQ frame keeps its decorative placeholders out of the export', () => {
  const block = faq(assemblePage({ ...input('emo-faq-pending'), copyResults: [] }));
  let serial = 0;
  const pasted = pasteEditorBlock(copyEditorBlock([block], block.id).block, prefix => `${prefix}-${++serial}`);
  assert.equal(pasted.elements.filter(item => item.templateCopyDecoration).length, 6);
  assert.equal(getBlockExportElements(pasted).filter(item => item.templateCopyDecoration || item.opacity === .96).length, 0);
});

test('pasting selected elements keeps a blank template decoration hidden and a manual shape visible', () => {
  const block = faq(assemblePage({ ...input('emo-faq-pending'), copyResults: [] }));
  const marker = block.elements.find(item => item.templateCopyDecoration === 'Q.');
  const manual = { id: 'manual', groupId: 'grp-manual', type: 'shape', x: 20, y: 20, w: 40, h: 40 };
  const copied = copyEditorElements([{ ...block, elements: [...block.elements, manual] }], [marker.id, manual.id]);
  let serial = 0;
  const pasted = pasteEditorElements(block, copied.elements, prefix => `${prefix}-${++serial}`).elements;
  assert.notEqual(pasted[0].groupId, marker.groupId);
  assert.deepEqual(getBlockExportElements({ ...block, elements: pasted }).map(item => item.type), ['shape']);
});
