import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { transferEditorPhoto } from '../../src/features/editor/editorPhotoEditing.js';
import { isPhotoSlotElement, removeSelectedElements } from '../../src/features/editor/editorSelection.js';
import { buildInfoBlock, defaultInfoFor } from '../../src/features/editor/presets/infoPresets.js';
import { autofillBlocks, filledSrcSet, buildRoledCutPool, isGeneratedCutBlock } from '../../src/features/editor/templates/autofill.js';
import { buildDetailPageTemplateSet, DETAIL_PAGE_TEMPLATE_SETS, buildImageBlock } from '../../src/features/editor/editorLibrary.js';
import { mergeEditorImagesIntoWardrobe } from '../../src/features/editor/editorWardrobe.js';
import { beginEditorCrop, ratioEditorCrop, dragEditorCrop } from '../../src/features/editor/editorCrop.js';

const editor = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
const image = { id: 'photo', type: 'image', src: '/original.jpg', sourceBlockId: 'cut',
  x: 40, y: 60, w: 400, h: 500, frameSlot: true, cutType: 'horizon' };
const board = [{ id: 'cut', source: 'ai', contentRole: 'fit', colorId: 'navy' }];
function relocate(target) {
  return transferEditorPhoto([{ id: 'source', kind: 'twocol', contentRole: 'fit', h: 700, elements: [image] }, target], {
    elementId: image.id, sourceBlockId: 'source', targetBlockId: target.id, x: 250, y: 30, emptyId: 'empty',
  });
}
function applyTemplate(current) {
  let seq = 0, result;
  const wardrobe = mergeEditorImagesIntoWardrobe({ blocks: current, storyboard: board, colorIds: ['navy'] });
  const scope = { roledCutsNow: () => buildRoledCutPool(wardrobe, board), autofillBlocks,
    buildDetailPageTemplateSet, uid: () => `new-${++seq}`, product: {}, latestBlocks: { current }, blocks: current,
    filledSrcSet, buildImageBlock, isGeneratedCutBlock, setBlocks: next => { result = next; },
    setSelBlock() {}, setBlockFocused() {}, setSelEl() {}, setSelEls() {}, toast: { push() {} } };
  const begin = editor.indexOf('  const overwriteDetailPageTemplate =');
  const end = editor.indexOf('  const addImageBlock =', begin);
  // The actual editor action and builders run; only React setters are a boundary.
  new Function(...Object.keys(scope), `${editor.slice(begin, end)}; return overwriteDetailPageTemplate;`)(...Object.values(scope))(DETAIL_PAGE_TEMPLATE_SETS[0].id);
  return result;
}

for (const type of ['size_table', 'care', 'policy']) {
  test(`template replacement preserves a ${type} block after a generated photo is moved into it`, () => {
    let n = 0;
    const info = type === 'size_table'
      ? { unit: 'cm', columns: ['totalLength'], rows: [{ label: 'S', values: { totalLength: 80 } }], withDiagram: false }
      : defaultInfoFor(type, {});
    const target = { ...buildInfoBlock(type, info, {}, () => `old-${++n}`), id: 'target' };
    const moved = relocate(target);
    const result = applyTemplate(moved);
    const retained = result.find(b => b.id === target.id);
    assert.ok(retained, 'template replacement must not discard the original information block');
    assert.deepEqual(retained.info, target.info);
    assert.deepEqual(retained.elements, moved[1].elements);
  });
}

test('template replacement protects a manually composed block but still replaces old photo templates', () => {
  const target = { id: 'target', kind: 'styling', contentRole: 'custom', h: 800,
    elements: [{ id: 'manual-text', type: 'text', text: '내가 입력한 문구', x: 0, y: 0, w: 300, h: 40 }] };
  const moved = relocate(target);
  const oldTemplate = { id: 'old-template', templateId: 'T1', kind: 'styling', contentRole: 'custom',
    h: 700, elements: [{ ...image, id: 'old-template-photo' }] };
  const result = applyTemplate([...moved, oldTemplate]);
  assert.ok(result.find(b => b.id === target.id)?.elements.some(e => e.id === 'manual-text'));
  assert.equal(result.some(b => b.id === oldTemplate.id), false);
});

for (const contentRole of ['fit', 'custom']) {
  test(`a photo moved into a ${contentRole} row is loose when deleted or moved again`, () => {
    const target = { id: 'target', kind: 'twocol', contentRole, h: 800, elements: [] };
    const moved = relocate(target);
    const photo = moved[1].elements[0];
    assert.equal(isPhotoSlotElement(moved[1], photo), false);
    const deleted = removeSelectedElements(moved, [photo.id], { allowRemoveContiSlots: true });
    assert.equal(deleted[1].elements.length, 0);
    const again = transferEditorPhoto([...moved, { id: 'third', h: 800, elements: [] }], {
      elementId: photo.id, sourceBlockId: 'target', targetBlockId: 'third', x: 40, y: 20, emptyId: 'must-not-create',
    });
    assert.equal(again[1].elements.length, 0);
    assert.equal(again[2].elements[0].sourceBlockId, 'cut');
  });
}

test('the source slot keeps its cleared provenance and the moved photo remains in the recovered library', () => {
  const moved = relocate({ id: 'target', kind: 'styling', contentRole: 'custom', h: 800, elements: [] });
  const empty = moved[0].elements[0];
  assert.equal(empty.src, null);
  assert.equal(empty.sourceBlockId, 'cut');
  assert.equal(empty.slotCleared, true);
  const restored = JSON.parse(JSON.stringify(moved));
  assert.equal(isPhotoSlotElement(restored[1], restored[1].elements[0]), false);
  const wardrobe = mergeEditorImagesIntoWardrobe({ blocks: restored, storyboard: board, colorIds: ['navy'] });
  assert.equal(Object.values(wardrobe).flat().length, 1);
  assert.equal(buildRoledCutPool(wardrobe, board).length, 1);
  assert.equal(applyTemplate(restored).some(b => b.id === 'source'), false);
});

test('explicit loose photos do not change the legacy undefined or explicit true slot rules', () => {
  const row = { kind: 'twocol' };
  const photo = { type: 'image', src: '/original.jpg', sourceBlockId: 'cut' };
  assert.equal(isPhotoSlotElement(row, photo), true);
  assert.equal(isPhotoSlotElement({ kind: 'styling' }, { ...photo, frameSlot: true }), true);
  assert.equal(isPhotoSlotElement(row, { ...photo, frameSlot: false }), false);
});

test('restoring the original crop also clears its ratio before the next edge drag', () => {
  let draft = ratioEditorCrop(beginEditorCrop('source', { ...image, x: 0, y: 0, w: 400, h: 600 }, { width: 400, height: 600 }), 1);
  const begin = editor.indexOf('  const resetCrop =');
  const end = editor.indexOf('  const cropDrag =', begin);
  const reset = new Function('setCropping', `${editor.slice(begin, end)}; return resetCrop;`)(fn => { draft = fn(draft); });
  reset();
  assert.equal(draft.ratio, null);
  assert.equal(draft.fw, 400);
  assert.equal(draft.fh, 600);
  const resized = dragEditorCrop(draft, 'frame', 'e', 10, 0);
  assert.equal(resized.fw, 400);
  assert.equal(resized.fh, 600);
});
