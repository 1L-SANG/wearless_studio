import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';

import { seedStoryboard, isUntouchedTemplateStoryboard } from '../../src/lib/pageTemplates/seed.js';
import * as actions from '../../src/lib/pageTemplates/sectionActions.js';
import { sectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';
import { reconcileSectionDependencies } from '../../src/lib/pageTemplates/sectionDependencies.js';
import { patchStoryboardPhoto, removeStoryboardPhoto } from '../../src/lib/pageTemplates/photoActions.js';
import { assemblePage, pointPhotosForRun } from '../../src/lib/pageTemplates/assemble.js';
import { validateStoryboardV4 } from '../../src/lib/pageTemplates/validateStoryboard.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { spaceSetIdFromGroupId } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { withStoryboardSpaceSetExamples } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { assignGenerationExamples } from '../../src/lib/generationExamples.js';
import rawExamples from '../../src/data/genExamples.json' with { type: 'json' };

const product = { name: '가을 셔츠', clothingType: 'top', colors: [{ id: 'base', isBase: true }] };
const recommendations = (count, fabric = false) => ({ version: 1, status: 'ready', candidates: [
  ...(fabric ? [{ id: 'fabric', kind: 'fabric', label: '원단', rank: 1, informationGroup: 'fabric', photoUse: 'standalone', visibility: 'clear', direction: 'front' }] : []),
  ...Array.from({ length: count }, (_, index) => ({ id: `structure-${index + 1}`, kind: 'structure', label: ['목선', '단추', '소맷단'][index],
    rank: index + 2, informationGroup: `part-${index + 1}`, photoUse: 'standalone', visibility: 'clear', direction: 'front' })),
] });
const analysisFor = (templateId = 'emo', count = 0, fabric = false) => ({ targetGenders: ['women'],
  pageStyle: { ...defaultPageStyle(), templateId }, detailRecommendations: recommendations(count, fabric) });
const pointsRun = blocks => sectionRuns(blocks).find(run => run.kind === 'points');
const cropItems = blocks => blocks.flatMap(block => block.pointItems || []).filter(item => item.kind === 'wornCrop');
const seed = (templateId = 'emo', count = 0, fabric = false) => seedStoryboard(product, analysisFor(templateId, count, fabric));

test('signature autofill preserves the untouched seed fingerprint', () => {
  const blocks = seed('show');
  const filled = assignGenerationExamples(blocks, { product, gender: 'women',
    catalog: withStoryboardSpaceSetExamples({ genExamples: rawExamples }).genExamples }).blocks;
  assert.equal(blocks[0].shot, 'medium');
  assert.equal(isUntouchedTemplateStoryboard(filled), true);
});
function addedPoints(templateId = 'emo') {
  let before = seed(templateId);
  const current = pointsRun(before);
  if (current) before = actions.removeTemplateSection(before, current.key, { product });
  const blocks = actions.addTemplateSection(before, product, analysisFor(templateId), 'points');
  return { before, blocks, run: pointsRun(blocks) };
}

for (const templateId of ['emo', 'show']) {
  for (const count of [0, 1]) test(`${templateId}: ${count} clear structural targets omit the initial detail section`, () => {
    assert.equal(pointsRun(seed(templateId, count, true)), undefined);
  });
  for (const count of [2, 3]) test(`${templateId}: ${count} clear targets use only those detail images within the shared budget`, () => {
    const blocks = seed(templateId, count, true), run = pointsRun(blocks);
    assert.ok(run);
    assert.equal(run.blocks.filter(block => block.source === 'ai').length, count);
    assert.deepEqual(run.blocks[0].pointItems.map(item => item.kind), Array(count).fill('detail'));
    assert.equal(blocks.filter(block => block.source === 'ai' && block.shot === 'detail').length, count + 1);
    assert.ok(blocks.some(block => block.pageSectionKind === 'fabric' && block.source === 'ai'));
    validateStoryboardV4(blocks);
  });
  test(`${templateId}: adding details reuses one worn photo three times without billing a new image`, () => {
    const { before, blocks, run } = addedPoints(templateId);
    assert.ok(run);
    assert.equal(run.blocks.length, 1);
    assert.equal(run.blocks[0].source, 'text');
    assert.equal(cropItems(blocks).length, 3);
    assert.equal(new Set(cropItems(blocks).map(item => item.sourceBlockId)).size, 1);
    assert.equal(blocks.filter(block => block.source === 'ai').length, before.filter(block => block.source === 'ai').length);
    validateStoryboardV4(blocks);
  });
  test(`${templateId}: adding life uses two same-place full/medium photos and studio uses rotation`, () => {
    const before = seed(templateId);
    const life = actions.addTemplateSection(before, product, analysisFor(templateId), 'wear', 'styling');
    const addedLife = life.filter(block => !before.some(old => old.id === block.id));
    assert.equal(addedLife.length, 2);
    assert.ok(addedLife.every(block => block.cutType === 'styling' && block.pageDesignId === 'P02-D01'));
    assert.deepEqual(addedLife.map(block => block.shot).sort(), ['full', 'medium']);
    assert.equal(new Set(addedLife.map(block => block.spaceGroupId)).size, 1);
    const studio = actions.addTemplateSection(before, product, analysisFor(templateId), 'wear', 'fit');
    const addedStudio = studio.filter(block => !before.some(old => old.id === block.id));
    assert.equal(addedStudio.length, 3);
    assert.ok(addedStudio.every(block => block.cutType === 'horizon' && block.pageDesignId === 'P04-D01'));
    assert.deepEqual(addedStudio.map(block => block.direction), ['front', 'side', 'back']);
  });
}

test('show continuous studio still seeds its full sequence after its usage becomes fit', () => {
  const blocks = seed('show'), continuous = blocks.filter(block => block.pageSectionKey === 'show.wear_styling');
  assert.equal(continuous.length, 6);
  assert.ok(continuous.every(block => block.cutType === 'horizon' && block.pageSectionUsage === 'fit'));
  assert.ok(continuous.slice(0, 5).every(block => block.exampleId?.startsWith('ss_horizon_sequence_')));
  assert.equal(new Set(continuous.slice(0, 5).map(block => block.spaceGroupId)).size, 1);
});

test('crop removal retargets rotation to continuous studio, then life, then the final worn orientation', () => {
  let { blocks } = addedPoints('show');
  const rotation = sectionRuns(blocks).find(run => run.kind === 'wear' && run.blocks.some(block => block.slotKey?.endsWith('.side')));
  assert.ok(cropItems(blocks).every(item => item.sourceBlockId === rotation.blocks.find(block => block.direction === 'front').id));
  blocks = actions.removeTemplateSection(blocks, rotation.key, { product });
  const continuous = blocks.find(block => block.pageSectionKey === 'show.wear_styling' && block.direction === 'front');
  assert.ok(cropItems(blocks).every(item => item.sourceBlockId === continuous.id));
  blocks = actions.addTemplateSection(blocks, product, analysisFor('show'), 'wear', 'styling');
  blocks = actions.removeTemplateSection(blocks, 'show.wear_styling', { product });
  const life = blocks.find(block => block.pageSectionKind === 'wear' && block.direction === 'front');
  assert.ok(cropItems(blocks).every(item => item.sourceBlockId === life.id));
  const side = { ...life, id: 'last-worn', direction: 'side', slotKey: null, spaceGroupId: undefined,
    spaceVariation: undefined, spaceSetMemberOrder: undefined, exampleId: null };
  const changed = reconcileSectionDependencies(blocks, blocks.filter(block => block.pageSectionKind !== 'wear').concat(side), product);
  assert.ok(cropItems(changed).every(item => item.sourceBlockId === side.id));
  assert.equal(cropItems(changed).length, 3);
  const removed = actions.removeTemplateSection(changed, side.pageSectionKey, { product });
  assert.equal(pointsRun(removed), undefined);
});

test('an existing seller worn photo remains a usable crop source with its actual uploaded image', () => {
  const { blocks } = addedPoints(), sourceId = cropItems(blocks)[0].sourceBlockId;
  const next = patchStoryboardPhoto(blocks, sourceId, { source: 'mine', cutType: null, ownImages: ['/my-worn-photo.png'] }, product);
  assert.ok(cropItems(next).every(item => item.sourceBlockId === sourceId));
  const selected = pointPhotosForRun(pointsRun(next).blocks, next);
  assert.equal(selected.length, 3);
  assert.ok(selected.every(photo => photo.source === 'mine'));
  const images = assemblePage({ storyboard: next, product, analysis: analysisFor() })
    .filter(block => block.pageSectionKind === 'points').flatMap(block => block.elements).filter(element => element.type === 'image');
  assert.equal(images.length, 3);
  assert.ok(images.every(image => image.src === '/my-worn-photo.png' && image.pointItemKind === 'wornCrop'));
  validateStoryboardV4(next);
});

test('removing a photo source uses another worn orientation instead of retaining a dangling crop', () => {
  let { blocks } = addedPoints();
  const sourceId = cropItems(blocks)[0].sourceBlockId;
  const next = removeStoryboardPhoto(blocks, sourceId, product);
  assert.equal(cropItems(next).length, 3);
  assert.ok(cropItems(next).every(item => item.sourceBlockId !== sourceId && next.some(block => block.id === item.sourceBlockId)));
});

test('I09-D14 creates only sns1 after its source is removed and keeps a one-member published set', () => {
  const original = seed();
  const withSns = actions.addTemplateSection(original, product, analysisFor(), 'sns');
  const life = sectionRuns(withSns).find(run => run.kind === 'wear' && run.usage === 'styling');
  const source = life.blocks[0], snapshot = structuredClone(withSns);
  const removed = actions.removeTemplateSection(withSns, life.key, { product });
  const fallback = removed.filter(block => block.pageSectionKind === 'sns' && block.source !== 'text');
  assert.equal(fallback.length, 1);
  assert.equal(fallback[0].slotKey, 'sns.sns1');
  assert.equal(fallback[0].exampleId, source.exampleId);
  assert.equal(spaceSetIdFromGroupId(fallback[0].spaceGroupId), spaceSetIdFromGroupId(source.spaceGroupId));
  assert.notEqual(fallback[0].spaceGroupId, source.spaceGroupId);
  assert.deepEqual(withSns, snapshot, 'removal must preserve the complete undo snapshot');
  const restored = reconcileSectionDependencies(removed, snapshot, product);
  assert.deepEqual(restored, snapshot, 'restoring the saved snapshot must restore every original source setting');
  const runIndex = withSns.findIndex(block => block.pageSectionKey === life.key);
  const reinserted = [...removed.slice(0, runIndex), ...life.blocks, ...removed.slice(runIndex)];
  const reconciled = reconcileSectionDependencies(removed, reinserted, product);
  assert.equal(reconciled.filter(block => block.pageSectionKind === 'sns' && block.source !== 'text').length, 0);
});

test('section removal preflight confirms every worn section and reports actual crop/SNS consequences', () => {
  assert.equal(typeof actions.sectionRemovalImpact, 'function');
  let { blocks } = addedPoints();
  blocks = actions.addTemplateSection(blocks, product, analysisFor(), 'sns');
  const life = sectionRuns(blocks).find(run => run.kind === 'wear' && run.usage === 'styling');
  const impact = actions.sectionRemovalImpact(blocks, life.key, { product });
  assert.equal(impact.confirmationRequired, true);
  assert.equal(impact.nextBlocks.filter(block => block.pageSectionKind === 'sns' && block.source === 'ai').length, 1);
  assert.match(impact.consequenceText, /SNS.*1/);
  const review = blocks.find(block => block.pageSectionKind === 'review');
  assert.equal(actions.sectionRemovalImpact(blocks, review.pageSectionKey, { product }).confirmationRequired, false);
  const rotation = blocks.find(block => block.pageSectionKind === 'wear' && block.pageSectionUsage === 'fit');
  assert.match(actions.sectionRemovalImpact(blocks, rotation.pageSectionKey, { product }).consequenceText, /디테일.*옮/);
  const onlySource = blocks.filter(block => block.pageSectionKind !== 'wear' || block.id === cropItems(blocks)[0].sourceBlockId);
  assert.match(actions.sectionRemovalImpact(onlySource, rotation.pageSectionKey, { product }).consequenceText, /디테일.*함께/);
});

test('JS validates worn crop sources and rejects another section or an unavailable seller image', () => {
  const { blocks } = addedPoints(), sourceId = cropItems(blocks)[0].sourceBlockId;
  for (const changes of [{ cutType: 'product', shot: 'ghost' }, { source: 'mine', cutType: null, ownImages: [] },
    { source: 'mine', cutType: null, ownImages: '/unlisted-worn.png' }]) {
    const bad = blocks.map(block => block.id === sourceId ? { ...block, ...changes } : block);
    assert.throws(() => validateStoryboardV4(bad));
  }
});

test('both engines assemble all permitted crop sources with the same actual image and geometry', () => {
  const { blocks } = addedPoints();
  const sourceId = cropItems(blocks)[0].sourceBlockId;
  const replaceSource = changes => blocks.map(block => block.id === sourceId ? { ...block, ...changes } : block);
  const variants = [
    blocks,
    replaceSource({ direction: 'side', exampleId: null }),
    replaceSource({ cutType: 'styling', direction: 'front', exampleId: null }),
    replaceSource({ source: 'mine', cutType: null, ownImages: ['/my-worn-photo.png'] }),
    actions.removeTemplateSection(blocks, blocks.find(block => block.id === sourceId).pageSectionKey, { product }),
  ].map(storyboard => ({ storyboard, product, analysis: analysisFor(), cutResults: storyboard.filter(block => block.source === 'ai')
    .map(block => ({ blockId: block.id, imageUrl: `photo:${block.id}`, width: 1024, height: 1365 })) }));
  const script = `import json,sys
from app.agents.content_roles import validate_storyboard_v4
from app.agents.page_template_assembler import assemble_v4
values=json.load(sys.stdin)
results=[]
for value in values:
    validate_storyboard_v4(value['storyboard'])
    results.append(assemble_v4(value['storyboard'],value['cutResults'],[],value['product'],True,analysis=value['analysis']))
json.dump(results,sys.stdout,ensure_ascii=False)`;
  const result = spawnSync('/Users/daily/Documents/wearless_studio/server/.venv/bin/python', ['-c', script], {
    input: JSON.stringify(variants), encoding: 'utf8', maxBuffer: 32 * 1024 * 1024, env: { ...process.env, PYTHONPATH: 'server' },
  });
  assert.equal(result.status, 0, result.stderr);
  const python = JSON.parse(result.stdout);
  variants.forEach((input, index) => assert.deepEqual(assemblePage(input), python[index], `source variant ${index}`));
});
