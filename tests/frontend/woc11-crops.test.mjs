import test from 'node:test';
import assert from 'node:assert/strict';
import { seedStoryboard, resolveStoryboardEntry, wornCropRegions } from '../../src/lib/pageTemplates/seed.js';
import { addTemplateSection, removeTemplateSection, moveTemplateSection } from '../../src/lib/pageTemplates/sectionActions.js';
import { preferredWornCropSource, reconcileSectionDependencies } from '../../src/lib/pageTemplates/sectionDependencies.js';
import { patchStoryboardPhoto, removeStoryboardPhoto } from '../../src/lib/pageTemplates/photoActions.js';
import { validateStoryboardV4 } from '../../src/lib/pageTemplates/validateStoryboard.js';
import { wornCropRegion, wornCropSourceAspect } from '../../src/lib/pageTemplates/wornCropGeometry.js';

const product = { clothingType: 'top', colors: [{ id: 'base', isBase: true }] };
const analysis = { targetGenders: ['women'], pageStyle: { templateId: 'emo' } };
const crops = blocks => blocks.flatMap(block => block.pointItems || []).filter(item => item.kind === 'wornCrop');
const lead = blocks => blocks.find(block => block.pointItems?.some(item => item.kind === 'wornCrop'));
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-10, `${actual} != ${expected}`);
function assertFourFive(items, aspect) {
  assert.equal(items.length, 3);
  for (const { cropRegion: [x, y, w, h] } of items) {
    close(aspect * w / h, .8);
    assert.ok(x >= 0 && y >= 0 && w > 0 && h > 0 && x + w <= 1.000001 && y + h <= 1.000001);
  }
}
function added({ context = {}, sourcePatch = {}, sourceProduct = product } = {}) {
  let before = seedStoryboard(sourceProduct, analysis);
  const source = preferredWornCropSource(before);
  before = before.map(block => block.id === source.id ? { ...block, ...sourcePatch } : block);
  const blocks = addTemplateSection(before, sourceProduct, analysis, 'points', undefined,
    typeof context === 'function' ? context(source) : context);
  return { before, blocks, sourceId: source.id };
}
function savedCustom(blocks) {
  return blocks.map(block => block.pointItems ? { ...block, pointItems: block.pointItems.map((item, i) => ({ ...item,
    cropRegion: [[.17, .22, .42, .11], [.24, .18, .22, .31], [.1, .7, .6, .2]][i] })) } : block);
}

test('WOC11 exported seed crop defaults are already 4:5 at the generated-wear fallback aspect', () => {
  for (const regions of Object.values(wornCropRegions)) {
    assertFourFive(regions.map(([, , cropRegion]) => ({ cropRegion })), .75);
  }
  assert.deepEqual(wornCropRegions.top.map(([bodyPart, label]) => [bodyPart, label]),
    [['shoulder', '어깨선'], ['neckline', '목선이 앉는 모양'], ['length', '입었을 때 기장']]);
});

for (const clothingType of ['top', 'outer', 'bottom', 'dress']) {
  test(`WOC11 ${clothingType}: newly added crops use 4:5 with unknown generated wear at 3:4`, () => {
    const { blocks } = added({ sourceProduct: { ...product, clothingType } });
    assertFourFive(crops(blocks), .75);
    assert.deepEqual(crops(blocks).map(({ bodyPart, label }) => [bodyPart, label]),
      wornCropRegions[clothingType].map(([bodyPart, label]) => [bodyPart, label]));
    validateStoryboardV4(blocks);
  });
}

for (const [width, height] of [[800, 1200], [900, 1200], [1600, 800]]) {
  test(`WOC11 new crops account for actual source cut dimensions ${width}:${height}`, () => {
    const { blocks } = added({ context: source => ({ cutResults: [{ blockId: source.id,
      imageUrl: '/actual-worn.jpg', width, height }] }) });
    assertFourFive(crops(blocks), width / height);
    validateStoryboardV4(blocks);
  });
}

test('WOC11 new crop retains its subject center when the normalized boundary permits it', () => {
  const { blocks } = added();
  const [x, y, w, h] = crops(blocks)[0].cropRegion;
  close(x + w / 2, .59); close(y + h / 2, .28);
  close(w, .30); close(h, .28125);
});

test('WOC11 unknown AI source never borrows product Front or estimated thumbnail dimensions', () => {
  const sourceProduct = { ...product, colors: [{ id: 'base', isBase: true,
    images: [{ slot: 'Front', src: '/product.jpg', width: 2000, height: 500 }] }] };
  const { blocks } = added({ sourceProduct, context: source => ({ cutResults: [
    { blockId: source.id, width: 600, height: 200, dimensionsKnown: false },
    { blockId: 'different-worn-cut', width: 1600, height: 900 },
  ] }) });
  assertFourFive(crops(blocks), .75);
});

test('WOC11 seller worn source uses existing image dimensions only on an exact selected URL match', () => {
  const sourceProduct = { ...product, colors: [{ id: 'base', isBase: true, images: [
    { slot: 'Front', src: '/other.jpg', width: 500, height: 2000 },
    { slot: 'Detail', src: '/seller-worn.jpg', width: 1600, height: 900 },
  ] }] };
  const { blocks } = added({ sourceProduct,
    sourcePatch: { source: 'mine', cutType: null, ownImages: ['/seller-worn.jpg'] } });
  assertFourFive(crops(blocks), 16 / 9);
  validateStoryboardV4(blocks);
});

test('WOC11 same-source reload, moves and settings preserve arbitrary saved crop rectangles exactly', () => {
  const saved = savedCustom(added().blocks), original = structuredClone(saved);
  const sourceId = crops(saved)[0].sourceBlockId;
  assert.deepEqual(resolveStoryboardEntry(saved, product, analysis).blocks, saved);
  const moved = moveTemplateSection(saved, lead(saved).pageSectionKey,
    saved.find(block => block.pageSectionKind === 'wear').pageSectionKey);
  const reconciled = reconcileSectionDependencies(saved, moved, product,
    { cropRegions: wornCropRegions.top, cutResults: [{ blockId: sourceId, width: 2000, height: 1000 }] });
  assert.deepEqual(crops(reconciled), crops(saved));
  assert.deepEqual(crops(patchStoryboardPhoto(saved, sourceId, { direction: 'side', faceExposure: 'none' }, product)), crops(saved));
  assert.deepEqual(saved, original);
});

test('WOC11 deleting a source photo rebinds legacy crops using the new source dimensions', () => {
  const saved = savedCustom(added().blocks), sourceId = crops(saved)[0].sourceBlockId;
  const after = removeStoryboardPhoto(saved, sourceId, product);
  assert.ok(crops(after).every(item => item.sourceBlockId !== sourceId));
  assertFourFive(crops(after), .75);
  const replacementId = crops(after)[0].sourceBlockId;
  const known = reconcileSectionDependencies(saved, saved.filter(block => block.id !== sourceId), product,
    { cropRegions: wornCropRegions.top, cutResults: [{ blockId: replacementId, width: 800, height: 1200 }] });
  assertFourFive(crops(known), 2 / 3);
  validateStoryboardV4(known);
});

test('WOC11 changing a mine image in the same source block creates fresh source-aware rectangles', () => {
  const sourceProduct = { ...product, colors: [{ id: 'base', isBase: true, images: [
    { slot: 'Detail', src: '/new-worn.jpg', width: 1600, height: 800 },
  ] }] };
  const saved = savedCustom(added({ sourcePatch: { source: 'mine', cutType: null, ownImages: ['/old-worn.jpg'] } }).blocks);
  const sourceId = crops(saved)[0].sourceBlockId;
  const next = patchStoryboardPhoto(saved, sourceId, { ownImages: ['/new-worn.jpg'], thumb: '/new-worn.jpg' }, sourceProduct);
  assertFourFive(crops(next), 2);
  assert.deepEqual(crops(next).map(({ bodyPart, label }) => [bodyPart, label]), crops(saved).map(({ bodyPart, label }) => [bodyPart, label]));
  validateStoryboardV4(next);
});

test('WOC11 photo source-type replacement uses supplied real metadata and its undo restores the exact saved ROI', () => {
  const saved = savedCustom(added().blocks), snapshot = structuredClone(saved);
  const sourceId = crops(saved)[0].sourceBlockId;
  const options = { cutResults: [{ blockId: sourceId, imageUrl: '/uploaded.jpg', width: 2000, height: 1000 }] };
  const next = patchStoryboardPhoto(saved, sourceId,
    { source: 'mine', cutType: null, ownImages: ['/uploaded.jpg'] }, product, options);
  assertFourFive(crops(next), 2);
  const undo = reconcileSectionDependencies(next, snapshot, product);
  assert.deepEqual(crops(undo), crops(snapshot));
  assert.deepEqual(saved, snapshot);
  validateStoryboardV4(undo);
});

test('WOC11 section and photo removal accept actual remaining-source cut dimensions', () => {
  const saved = savedCustom(added().blocks), sourceId = crops(saved)[0].sourceBlockId;
  const source = saved.find(block => block.id === sourceId);
  const cutResults = saved.filter(block => block.pageSectionKind === 'wear')
    .map(block => ({ blockId: block.id, imageUrl: `/${block.id}.jpg`, width: 800, height: 1200 }));
  assertFourFive(crops(removeStoryboardPhoto(saved, sourceId, product, { cutResults })), 2 / 3);
  assertFourFive(crops(removeTemplateSection(saved, source.pageSectionKey, { product, cutResults })), 2 / 3);
});

test('WOC11 restoring an undo snapshot preserves its crop, while reinserting only its old source keeps the current binding', () => {
  const saved = savedCustom(added().blocks), snapshot = structuredClone(saved);
  const sourceId = crops(saved)[0].sourceBlockId, source = saved.find(block => block.id === sourceId);
  const removed = removeTemplateSection(saved, source.pageSectionKey, { product });
  assertFourFive(crops(removed), .75);
  const undo = reconcileSectionDependencies(removed, snapshot, product, { cropRegions: wornCropRegions.top });
  assert.deepEqual(crops(undo), crops(snapshot));
  const sourceRun = saved.filter(block => block.pageSectionKey === source.pageSectionKey);
  const reinserted = reconcileSectionDependencies(removed, [...removed, ...sourceRun], product, { cropRegions: wornCropRegions.top });
  assert.deepEqual(crops(reinserted), crops(removed));
  const noPoints = removeTemplateSection(saved, lead(saved).pageSectionKey, { product });
  const restoredPoints = reconcileSectionDependencies(noPoints, snapshot, product);
  assert.deepEqual(crops(restoredPoints), crops(snapshot));
  assert.deepEqual(saved, snapshot);
  validateStoryboardV4(undo); validateStoryboardV4(reinserted); validateStoryboardV4(restoredPoints);
});

test('WOC11 central reconciliation normalizes passed crop templates only for fresh fill and rebinding', () => {
  const { blocks } = added();
  const sourceId = crops(blocks)[0].sourceBlockId;
  const partial = blocks.map(block => block.pointItems ? { ...block, pointItems: block.pointItems.slice(0, 1) } : block);
  const passedRegions = [
    ['shoulder', '어깨선', [.44, .20, .30, .16]],
    ['neckline', '목선이 앉는 모양', [.36, .20, .28, .17]],
    ['length', '입었을 때 기장', [.30, .27, .40, .24]],
  ];
  const snapshot = structuredClone(passedRegions);
  const next = reconcileSectionDependencies(partial, partial, product, { cropRegions: passedRegions });
  assertFourFive(crops(next), .75);
  assert.deepEqual(passedRegions, snapshot);
  const rebound = reconcileSectionDependencies(blocks, blocks.filter(block => block.id !== sourceId), product,
    { cropRegions: passedRegions });
  assertFourFive(crops(rebound), .75);
});

test('WOC11 source-aware crop helper is pure and bounded for portrait, landscape and edge anchors', () => {
  const anchors = [[.44, .2, .3, .16], [.3, .72, .4, .24], [.9, .95, .8, .4], [-.3, -.2, .3, .2]];
  const snapshot = structuredClone(anchors);
  for (const region of anchors) for (const aspect of [2 / 3, 3 / 4, 16 / 9, 3, .1, 17.17]) {
    const result = wornCropRegion(region, aspect);
    const [x, y, w, h] = result;
    assert.notEqual(result, region);
    close(aspect * w / h, .8);
    assert.ok(result.every(value => Number.isFinite(value) && value >= 0 && value <= 1));
    assert.ok(x + w <= 1.000001 && y + h <= 1.000001);
  }
  assert.deepEqual(anchors, snapshot);
});

test('WOC11 missing and invalid source metadata use 3:4 and stale seller cut metadata is ignored', () => {
  const source = { id: 'worn', source: 'mine', ownImages: ['/current.jpg'] };
  for (const bad of [{ width: 0, height: 200 }, { width: 200, height: NaN },
    { width: Infinity, height: 200 }, { width: 600, height: 300, dimensionsKnown: false }]) {
    close(wornCropSourceAspect(source, { cutResults: [{ blockId: 'worn', imageUrl: '/current.jpg', ...bad }] }), .75);
  }
  close(wornCropSourceAspect(source, { cutResults: [{ blockId: 'worn', imageUrl: '/old.jpg', width: 800, height: 1200 }] }), .75);
  close(wornCropSourceAspect({ id: 'unknown', source: 'ai' }), .75);
  const [, , w, h] = wornCropRegion([.2, .2, .3, .2], NaN);
  close(.75 * w / h, .8);
});
