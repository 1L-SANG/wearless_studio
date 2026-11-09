import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { seedStoryboard, wornCropRegions, resolveStoryboardEntry } from '../../src/lib/pageTemplates/seed.js';
import { addTemplateSection, removeTemplateSection, moveTemplateSection, changeTemplateSectionDesign } from '../../src/lib/pageTemplates/sectionActions.js';
import { reconcileSectionDependencies } from '../../src/lib/pageTemplates/sectionDependencies.js';
import { patchStoryboardPhoto, removeStoryboardPhoto } from '../../src/lib/pageTemplates/photoActions.js';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';
import { validateStoryboardV4 } from '../../src/lib/pageTemplates/validateStoryboard.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { storyboardPreviewCuts } from '../../src/features/storyboard/storyboardPreview.js';

const regions = {
  top: [['shoulder', '어깨선', [.44, .20, .30, .16]], ['neckline', '목선이 앉는 모양', [.36, .20, .28, .17]], ['length', '입었을 때 기장', [.30, .27, .40, .24]]],
  outer: [['shoulder', '어깨선', [.44, .20, .30, .16]], ['silhouette', '입었을 때 실루엣', [.28, .20, .44, .34]], ['length', '입었을 때 기장', [.30, .40, .40, .24]]],
  bottom: [['waistline', '입었을 때 허리선', [.34, .36, .32, .14]], ['silhouette', '입었을 때 실루엣', [.30, .40, .40, .50]], ['length', '입었을 때 기장', [.30, .72, .40, .24]]],
  dress: [['shoulder', '어깨선', [.44, .20, .30, .16]], ['silhouette', '입었을 때 실루엣', [.30, .30, .40, .40]], ['length', '입었을 때 기장', [.30, .60, .40, .26]]],
};
const product = { clothingType: 'top', colors: [{ id: 'base', isBase: true }] };
const analysis = templateId => ({ targetGenders: ['women'], pageStyle: { ...defaultPageStyle(), templateId } });
const items = blocks => blocks.flatMap(block => block.pointItems || []);
function added(clothingType = 'top', templateId = 'emo') {
  const p = { ...product, clothingType }, a = analysis(templateId);
  return addTemplateSection(seedStoryboard(p, a), p, a, 'points');
}
function simpleBoard(region = [.44, .20, .30, .16]) {
  return [{ id: 'wear-photo', taxonomyVersion: 4, source: 'ai', pageSectionKey: 'emo.wear_life', pageSectionKind: 'wear',
    pageSectionUsage: 'styling', pageDesignId: 'P02-D01', sectionId: 'wear', sectionLayout: 'stack', slotKey: null,
    contentRole: 'coordination', cutType: 'styling', shot: 'full', direction: 'front', colorId: 'base', faceExposure: 'same' },
  { id: 'points', taxonomyVersion: 4, source: 'text', pageSectionKey: 'emo.points', pageSectionKind: 'points', pageDesignId: 'PT-EMO-01',
    sectionId: 'points', sectionLayout: 'stack', slotKey: null, cutType: null, contentRole: null, sectionRole: null,
    pointItems: [{ kind: 'wornCrop', sourceBlockId: 'wear-photo', bodyPart: 'shoulder', label: '어깨선', cropRegion: region }] }];
}
const croppedImages = page => page.flatMap(block => block.elements).filter(element => element.pointItemKind === 'wornCrop');
const inputFor = meta => ({ storyboard: simpleBoard(), product, analysis: analysis('emo'),
  cutResults: [{ blockId: 'wear-photo', imageUrl: '/worn.png', ...meta }] });
const geometryCases = [
  { name: 'tiny measured source', meta: { width: 96, height: 144 }, crop: { iw: 96, ih: 144, ox: 0, oy: 0 } },
  { name: 'narrow measured source', meta: { width: 40, height: 1000 }, crop: { iw: 40, ih: 1000, ox: 0, oy: 66 } },
  { name: 'short measured source', meta: { width: 1000, height: 40 }, crop: { iw: 1000, ih: 40, ox: 376, oy: 0 } },
  { name: 'normal measured source', meta: { width: 1024, height: 1536 }, crop: { iw: 1024, ih: 1536, ox: 390, oy: 216 } },
  { name: 'measured source explicitly known', meta: { width: 1024, height: 1365, dimensionsKnown: true }, crop: { iw: 1024, ih: 1365, ox: 390, oy: 168 } },
  { name: 'larger measured source', meta: { width: 1536, height: 2048 }, crop: { iw: 1427, ih: 1902, ox: 628, oy: 319 } },
  { name: 'large measured source', meta: { width: 3072, height: 4608 }, crop: { iw: 1427, ih: 2140, ox: 628, oy: 385 } },
  { name: 'unknown dimensions use estimated aspect', meta: {}, crop: { iw: 1427, ih: 2140, ox: 628, oy: 385 } },
  { name: 'approximate preview dimensions are not native pixels', meta: { width: 1024, height: 1365, dimensionsKnown: false }, crop: { iw: 1427, ih: 1902, ox: 628, oy: 319 } },
  { name: 'nonpositive dimensions are unknown', meta: { width: -4, height: 0 }, crop: { iw: 1427, ih: 2140, ox: 628, oy: 385 } },
];

for (const templateId of ['emo', 'show']) for (const clothingType of Object.keys(regions)) {
  test(`${templateId} new ${clothingType} manual crops use the exact owner regions without changing labels`, () => {
    const blocks = added(clothingType, templateId);
    assert.deepEqual(items(blocks).map(({ bodyPart, label, cropRegion }) => [bodyPart, label, cropRegion]), regions[clothingType]);
    validateStoryboardV4(blocks);
  });
}

for (const { name, meta, crop } of geometryCases) test(name, () => {
  const image = croppedImages(assemblePage(inputFor(meta)))[0];
  assert.deepEqual([image.w, image.h], [428, 428]);
  assert.deepEqual(image.crop, crop);
  assert.ok(image.w / image.crop.iw >= .3 - .0001, 'at least 30% of source width remains visible');
  if (meta.width > 0 && meta.height > 0 && meta.dimensionsKnown !== false) {
    assert.ok(image.crop.iw <= meta.width && image.crop.ih <= meta.height, 'native pixel cap survives a slot larger than the source');
  }
});

test('a single measured dimension does not activate the native scale cap', () => {
  for (const meta of [{ width: 96 }, { height: 144 }]) {
    const image = croppedImages(assemblePage(inputFor(meta)))[0];
    assert.ok(image.crop.iw > (meta.width || 2));
    assert.ok(image.w / image.crop.iw >= .3 - .0001);
  }
});

test('preview dimensions stay transient estimates and cannot silently become native metadata', () => {
  const blocks = added(), before = structuredClone(blocks);
  const cuts = storyboardPreviewCuts(blocks, {});
  assert.ok(cuts.length);
  assert.ok(cuts.every(cut => cut.dimensionsKnown === false));
  assert.deepEqual(blocks, before);
  assert.ok(blocks.every(block => !Object.hasOwn(block, 'dimensionsKnown')));
  const images = croppedImages(assemblePage({ storyboard: blocks, product, analysis: analysis('emo'), cutResults: cuts }));
  assert.ok(images.some(image => image.crop.iw > 1024), 'preview estimates still use the 30% zoom cap');
});

test('same source preserves custom saved regions through re-entry, moves, edits and design changes', () => {
  let blocks = added();
  const custom = [[.11, .12, .69, .32], [.17, .14, .61, .31], [.12, .39, .77, .46]];
  blocks = blocks.map(block => block.pointItems ? { ...block, pointItems: block.pointItems.map((item, index) => ({ ...item, cropRegion: custom[index] })) } : block);
  const before = structuredClone(blocks), sourceId = items(blocks)[0].sourceBlockId;
  assert.equal(resolveStoryboardEntry(blocks, product, analysis('emo')).blocks, blocks);
  const moved = moveTemplateSection(blocks, blocks.find(block => block.id === sourceId).pageSectionKey);
  const edited = patchStoryboardPhoto(moved, sourceId, { direction: 'side' }, product);
  const pointsKey = blocks.find(block => block.pointItems).pageSectionKey;
  const redesigned = changeTemplateSectionDesign(edited, pointsKey, 'T05-D07', { product });
  assert.equal(redesigned.find(block => block.pageSectionKey === pointsKey).pageDesignId, 'T05-D07');
  const reconciled = reconcileSectionDependencies(edited, redesigned, product);
  assert.deepEqual(items(reconciled).map(item => item.cropRegion), custom);
  assert.deepEqual(blocks, before);
});

test('source removal rebinds custom saved regions to current body parts even when items were reordered', () => {
  let blocks = added();
  blocks = blocks.map(block => block.pointItems ? { ...block, pointItems: [2, 0, 1].map(index => ({ ...block.pointItems[index], cropRegion: [.1, .1, .8, .8] })) } : block);
  const sourceId = items(blocks)[0].sourceBlockId, source = blocks.find(block => block.id === sourceId);
  const changed = removeTemplateSection(blocks, source.pageSectionKey, { product });
  assert.ok(items(changed).every(item => item.sourceBlockId !== sourceId));
  assert.deepEqual(items(changed).map(item => [item.bodyPart, item.cropRegion]), [
    ['length', [.30, .27, .40, .24]], ['shoulder', [.44, .20, .30, .16]], ['neckline', [.36, .20, .28, .17]],
  ]);
  const direct = reconcileSectionDependencies(blocks, blocks.filter(block => block.id !== sourceId), product);
  assert.deepEqual(items(direct).map(item => item.cropRegion), items(changed).map(item => item.cropRegion));
});

test('photo deletion and source invalidation also refresh saved regions on rebind', () => {
  let blocks = added();
  blocks = blocks.map(block => block.pointItems ? { ...block, pointItems: block.pointItems.map(item => ({ ...item, cropRegion: [.1, .1, .8, .8] })) } : block);
  const sourceId = items(blocks)[0].sourceBlockId;
  for (const next of [removeStoryboardPhoto(blocks, sourceId, product), patchStoryboardPhoto(blocks, sourceId, { cutType: 'product', shot: 'ghost' }, product)]) {
    assert.ok(items(next).every(item => item.sourceBlockId !== sourceId));
    assert.deepEqual(items(next).map(item => item.cropRegion), regions.top.map(row => row[2]));
  }
});

test('positive narrow crops validate while finite bounds, source and body-part rules remain enforced', () => {
  for (const region of [[.36, .20, .28, .17], [.1, .1, .01, .01]]) validateStoryboardV4(simpleBoard(region));
  for (const region of [[0, 0, 0, .1], [0, 0, .3, 0], [.8, 0, .28, .2], [0, .9, .28, .2],
    [-.1, .2, .28, .2], [0, 0, Infinity, .2], [0, 0, NaN, .2], [0, 0, '0.28', .2]]) {
    assert.throws(() => validateStoryboardV4(simpleBoard(region)), /invalid_storyboard_point_items/);
  }
  const invalidPart = simpleBoard(); invalidPart[1].pointItems[0].bodyPart = 'face';
  assert.throws(() => validateStoryboardV4(invalidPart), /invalid_storyboard_point_items/);
  const invalidSource = simpleBoard(); invalidSource[0].cutType = 'product'; invalidSource[0].shot = 'ghost';
  assert.throws(() => validateStoryboardV4(invalidSource), /invalid_storyboard_point_items/);
});

test('JS and Python agree exactly for every geometry case, old saved regions and all new category regions', () => {
  const values = geometryCases.map(({ meta }) => inputFor(meta));
  values.push({ ...inputFor({ width: 1024, height: 1536 }), storyboard: simpleBoard([.15, .16, .7, .35]) });
  for (const clothingType of Object.keys(regions)) {
    const storyboard = added(clothingType);
    values.push({ storyboard, product: { ...product, clothingType }, analysis: analysis('emo'), cutResults: storyboardPreviewCuts(storyboard, {}) });
  }
  const result = spawnSync(process.env.PAGE_TEMPLATE_PYTHON || '/Users/daily/Documents/wearless_studio/server/.venv/bin/python', ['-c', `import json,sys
from app.agents.content_roles import validate_storyboard_v4
from app.agents.page_template_assembler import assemble_v4
values=json.load(sys.stdin)
out=[]
for value in values:
 validate_storyboard_v4(value['storyboard'])
 out.append(assemble_v4(value['storyboard'],value['cutResults'],[],value['product'],True,analysis=value['analysis']))
json.dump(out,sys.stdout,ensure_ascii=False)`], { input: JSON.stringify(values), encoding: 'utf8', maxBuffer: 32 * 1024 * 1024,
    env: { ...process.env, PYTHONPATH: 'server' } });
  assert.equal(result.status, 0, result.stderr);
  const python = JSON.parse(result.stdout);
  values.forEach((input, index) => assert.deepEqual(assemblePage(input), python[index], `case ${index}`));
});

test('shared fixture retains legacy saved-region and new geometry boundary coverage', () => {
  const fixture = JSON.parse(readFileSync(new URL('../fixtures/page_templates_cases.json', import.meta.url)));
  for (const name of ['woc2-emo-crops-saved', 'woc2-show-crops-saved', 'woc2-emo-crops-tiny', 'woc2-emo-crops-large', 'woc2-emo-crops-unknown', 'woc2-emo-crops-preview']) {
    assert.ok(fixture.cases.some(value => value.name === name), name);
  }
  for (const templateId of ['emo', 'show']) assert.deepEqual(items(fixture.cases.find(value => value.name === `woc2-${templateId}-crops-saved`).input.storyboard)
    .map(item => item.cropRegion), [[.15, .16, .7, .35], [.2, .15, .6, .3], [.1, .38, .8, .45]]);
  assert.deepEqual(wornCropRegions, regions);
});
