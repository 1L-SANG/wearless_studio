import test from 'node:test';
import assert from 'node:assert/strict';
import * as actions from '../../src/lib/pageTemplates/sectionActions.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { getTemplate, pageTemplatesCatalog } from '../../src/lib/pageTemplates/catalog.js';
import { validateStoryboardV4 } from '../../src/lib/pageTemplates/validateStoryboard.js';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';
import { templateSectionName } from '../../src/lib/pageTemplates/slots.js';
import { ensureSections } from '../../src/lib/sections.js';
import { generationExampleSelectionPatch } from '../../src/lib/storyboardExampleSelection.js';
import { detachSpaceMembership } from '../../src/lib/storyboardSpaceSets.js';
import { withStoryboardSpaceSetExamples, storyboardSpaceSetsFor, spaceSetIdFromGroupId } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { reconcileSectionDependencies } from '../../src/lib/pageTemplates/sectionDependencies.js';
import { sectionRuns, changeTemplateSectionDesign as storedRunDesign } from '../../src/lib/pageTemplates/sectionRuns.js';
import { sectionDesignChoices } from '../../src/lib/pageTemplates/designChoices.js';
import rawExamples from '../../src/data/genExamples.json' with { type: 'json' };
import { assignGenerationExamples } from '../../src/lib/generationExamples.js';

const product = { name: '가을 셔츠', clothingType: 'top', colors: [
  { id: 'base', isBase: true, images: [{ slot: 'Front', url: 'source:front' }, { slot: 'Back', url: 'source:back' }] },
  { id: 'second' },
] };
const candidate = (id, kind = 'structure') => ({ id, kind, label: id, rank: 1, informationGroup: id,
  photoUse: 'standalone', visibility: 'clear', direction: 'front' });
const analysisFor = templateId => ({ targetGenders: ['women'], pageStyle: { ...defaultPageStyle(), templateId },
  detailRecommendations: { version: 1, status: 'ready', candidates: [candidate('fabric', 'fabric'), candidate('neck'), candidate('hem')] } });
const catalog = withStoryboardSpaceSetExamples({ genExamples: rawExamples }).genExamples;
const board = (templateId = 'emo') => seedStoryboard(product, analysisFor(templateId), { projectId: 'woc1' });
const keys = blocks => [...new Set(blocks.map(b => b.pageSectionKey))];
const valid = blocks => { assert.equal(validateStoryboardV4(blocks), blocks); return blocks; };

test('catalog names distinguish wear usage without rewriting an already-saved section title', () => {
  const original = board().map(block => ({ ...block, sectionTitle: '예전 저장 이름' }));
  assert.equal(templateSectionName(original.find(block => block.pageSectionUsage === 'styling')), '일상 착용컷');
  assert.equal(templateSectionName(original.find(block => block.pageSectionUsage === 'fit')), '스튜디오 착용컷');
  assert.equal(templateSectionName(original.find(block => block.pageSectionKind === 'fabric')), '원단 설명');
  assert.ok(ensureSections(original).every(block => block.sectionTitle === '예전 저장 이름'));
  assert.equal(templateSectionName({ pageSectionKind: 'wear', pageSectionKey: 'emo.wear_fit', name: '착용 사진' }), '스튜디오 착용컷');
  assert.equal(templateSectionName({ pageSectionKind: 'wear', pageSectionKey: 'emo.wear_1', pageDesignId: 'P02-D02', name: '스튜디오 착용컷' }), '스튜디오 착용컷');
  assert.equal(templateSectionName({ pageSectionKind: 'wear', pageSectionKey: 'show.wear_2', pageDesignId: 'P04-D01', name: '착용 사진' }), '스튜디오 착용컷');
});

test('section insertion uses the selected lower run, after hidden text, or the page end', () => {
  const original = board();
  const before = original.find(b => b.pageSectionKind === 'points').pageSectionKey;
  const inserted = valid(actions.addTemplateSection(original, product, analysisFor('emo'), 'wear', 'styling', { beforeSectionKey: before }));
  const added = inserted.filter(b => !original.some(old => old.id === b.id));
  assert.equal(added.length, 2);
  assert.deepEqual(added.map(b => b.shot).sort(), ['full', 'medium']);
  assert.equal(new Set(added.map(b => b.spaceGroupId)).size, 1);
  assert.ok(added[0].spaceGroupId);
  assert.equal(inserted.indexOf(added.at(-1)) + 1, inserted.findIndex(b => b.pageSectionKey === before));
  assert.deepEqual(inserted.slice(0, inserted.indexOf(added[0])), original.slice(0, original.findIndex(b => b.pageSectionKey === before)));
  const appended = valid(actions.addTemplateSection(original, product, analysisFor('emo'), 'wear', 'styling'));
  assert.deepEqual(appended.slice(0, original.length), original);
  assert.equal(actions.addTemplateSection(original, product, analysisFor('emo'), 'wear', 'styling', { beforeSectionKey: original[0].pageSectionKey }), original);
  assert.equal(actions.addTemplateSection(original, product, analysisFor('emo'), 'first'), original);
});

test('moving a section preserves complete runs and leaves hidden text before the lower destination', () => {
  const original = board(), source = original.find(b => b.pageSectionUsage === 'fit').pageSectionKey;
  const destination = original.find(b => b.pageSectionKind === 'points').pageSectionKey;
  const moving = original.filter(b => b.pageSectionKey === source);
  const unchanged = original.filter(b => b.pageSectionKey !== source);
  const index = unchanged.findIndex(b => b.pageSectionKey === destination);
  const next = valid(actions.moveTemplateSection(original, source, destination));
  assert.deepEqual(next, [...unchanged.slice(0, index), ...moving, ...unchanged.slice(index)]);
  assert.deepEqual(original, boardSnapshot(original));
  assert.equal(actions.moveTemplateSection(original, original[0].pageSectionKey, null), original);
  assert.equal(actions.moveTemplateSection(original, source, original[0].pageSectionKey), original);
  const restored = valid(actions.moveTemplateSection(next, source, keys(original)[keys(original).indexOf(source) + 1] || null));
  assert.deepEqual(restored, original);
});
function boardSnapshot(value) { return structuredClone(value); }

test('design changes update every block in the run and reject another kind', () => {
  const original = board(), source = original.find(b => b.pageSectionUsage === 'fit');
  const next = valid(actions.changeTemplateSectionDesign(original, source.pageSectionKey, 'WEAR-FIT-2COL-01'));
  assert.ok(next.filter(b => b.pageSectionKey === source.pageSectionKey).every(b => b.pageDesignId === 'WEAR-FIT-2COL-01'));
  assert.deepEqual(next.filter(b => b.pageSectionKey !== source.pageSectionKey), original.filter(b => b.pageSectionKey !== source.pageSectionKey));
  assert.equal(actions.changeTemplateSectionDesign(original, source.pageSectionKey, 'T08-D07'), original);
});

for (const templateId of ['emo', 'show', 'guide']) test(`${templateId}: every offered design changes the run and keeps its required photo slots filled`, () => {
  const original = board(templateId), a = analysisFor(templateId);
  for (const run of sectionRuns(original)) {
    for (const design of sectionDesignChoices(run, { storyboard: original, product })) {
      const changed = valid(actions.changeTemplateSectionDesign(original, run.key, design.id, { product }));
      assert.ok(changed.filter(block => block.pageSectionKey === run.key).every(block => block.pageDesignId === design.id),
        `${templateId} ${design.id} must actually be selected`);
      const page = assemblePage({ storyboard: changed, product, analysis: a,
        cutResults: changed.filter(block => block.source === 'ai').map(block => ({ blockId: block.id, imageUrl: `photo:${block.id}` })) });
      const images = page.filter(block => block.pageSectionKey === run.key).flatMap(block => block.elements).filter(element => element.type === 'image');
      assert.ok(images.every(image => image.src && image.sourceBlockId), `${templateId} ${design.id} has an empty photo`);
    }
  }
});

test('first page removal is rejected and normal deletion keeps a restorable snapshot', () => {
  const original = board();
  assert.equal(actions.removeTemplateSection(original, original[0].pageSectionKey), original);
  const review = original.find(b => b.pageSectionKind === 'review').pageSectionKey;
  const next = valid(actions.removeTemplateSection(original, review, { product }));
  assert.equal(next.some(b => b.pageSectionKey === review), false);
  assert.ok(original.some(b => b.pageSectionKey === review));
  valid(original);
});

for (const templateId of ['emo', 'show', 'guide']) test(`${templateId}: reuse slots survive reorder, materialize on source deletion and release on reinsertion`, () => {
  let original = board(templateId);
  if (!original.some(b => b.pageSectionKind === 'sns')) original = actions.addTemplateSection(original, product, analysisFor(templateId), 'sns');
  const styling = original.find(b => b.pageSectionKind === 'wear' && /\.g\d+$/.test(b.slotKey));
  const moved = valid(actions.moveTemplateSection(original, styling.pageSectionKey, null));
  const images = input => assemblePage({ storyboard: input, product, analysis: analysisFor(templateId),
    cutResults: input.filter(b => b.source === 'ai').map(b => ({ blockId: b.id, imageUrl: `photo:${b.id}` })) }).flatMap(b => b.elements).filter(e => e.type === 'image');
  const snsSources = input => images(input).filter(e => e.slotKey?.includes('sns')).map(e => e.sourceBlockId);
  assert.deepEqual(snsSources(moved), snsSources(original));
  const next = valid(actions.removeTemplateSection(original, styling.pageSectionKey, { product }));
  const fallback = next.filter(b => b.pageSectionKind === 'sns' && b.source === 'ai');
  assert.equal(fallback.length, 1);
  assert.ok(fallback.every(b => b.cutType === styling.cutType && !b.similarSourceBlockId));
  assert.equal(spaceSetIdFromGroupId(fallback[0].spaceGroupId), spaceSetIdFromGroupId(styling.spaceGroupId));
  assert.notEqual(fallback[0].spaceGroupId, styling.spaceGroupId);
  assert.ok(ensureSections(next).filter(b => b.pageSectionKind === 'sns' && b.source === 'ai').every(b => b.contentRole === 'coordination'));
  assert.equal(snsSources(next).length, 1);
  assert.ok(snsSources(next).every(id => fallback.some(b => b.id === id)));
  const sourceIndex = original.findIndex(block => block.pageSectionKey === styling.pageSectionKey);
  const restored = valid(reconcileSectionDependencies(next, [...next.slice(0, sourceIndex),
    ...original.filter(block => block.pageSectionKey === styling.pageSectionKey), ...next.slice(sourceIndex)], product));
  assert.equal(restored.some(b => b.pageSectionKind === 'sns' && b.source === 'ai'), false);
});

test('stored hidden showroom ghost design still reuses its source and materializes one cut on source removal', () => {
  // A persisted hidden design is valid even though it is no longer a picker item.
  const original = valid(JSON.parse(JSON.stringify(storedRunDesign(board('show'), 'show.first', 'P01-D01'))));
  const source = original.find(b => b.pageSectionKind === 'color_product');
  const rendered = assemblePage({ storyboard: original, product, analysis: analysisFor('show') });
  const reused = rendered.filter(block => block.pageSectionKind === 'first').flatMap(block => block.elements)
    .find(element => element.slotKey === 'first.ghost');
  assert.equal(reused.sourceBlockId, source.id);
  const next = valid(actions.removeTemplateSection(original, source.pageSectionKey, { product }));
  const fallback = next.filter(b => b.pageSectionKind === 'first' && b.cutType === 'product');
  assert.equal(fallback.length, 1);
  assert.equal(fallback[0].slotKey, 'first.ghost');
  assert.equal(fallback[0].shot, 'ghost');
  assert.equal(fallback[0].productPresentation, 'ghost');
  assert.equal(next.filter(b => b.source === 'ai').length, original.filter(b => b.source === 'ai' && b.pageSectionKey !== source.pageSectionKey).length + 1);
});

test('removing the reused original section materializes the slot even when another same-kind section remains', () => {
  let original = board();
  original = actions.addTemplateSection(original, product, analysisFor('emo'), 'sns');
  original = actions.addTemplateSection(original, product, analysisFor('emo'), 'wear', 'styling');
  const source = original.find(block => block.pageSectionKey === 'emo.wear_styling');
  const next = valid(actions.removeTemplateSection(original, source.pageSectionKey, { product }));
  assert.ok(next.some(block => block.pageSectionUsage === 'styling'));
  assert.equal(next.filter(block => block.pageSectionKind === 'sns' && block.source === 'ai').length, 1);
  const later = valid(actions.addTemplateSection(next, product, analysisFor('emo'), 'wear', 'fit'));
  assert.equal(later.filter(block => block.pageSectionKind === 'sns' && block.source === 'ai').length, 1);
});

test('worn crops retarget to remaining worn photos and the derived run disappears only without any worn source', () => {
  const a = { ...analysisFor('show'), detailRecommendations: { version: 1, status: 'ready', candidates: [] } };
  let original = seedStoryboard(product, a);
  original = actions.addTemplateSection(original, product, a, 'points');
  original = actions.addTemplateSection(original, product, a, 'wear', 'fit');
  const fitKeys = [...new Set(original.filter(b => b.pageSectionUsage === 'fit').map(b => b.pageSectionKey))];
  const one = valid(actions.removeTemplateSection(original, fitKeys[0], { product }));
  const byId = new Map(one.map(b => [b.id, b]));
  const crops = one.flatMap(b => b.pointItems || []).filter(i => i.kind === 'wornCrop');
  assert.ok(crops.length);
  assert.ok(crops.every(i => byId.get(i.sourceBlockId)?.pageSectionKey === fitKeys[1]));
  let none = one;
  for (const key of fitKeys.slice(1)) none = valid(actions.removeTemplateSection(none, key, { product }));
  assert.equal(none.flatMap(b => b.pointItems || []).some(i => i.kind === 'wornCrop'), false);
  assert.equal(none.some(block => block.pageSectionKind === 'points'), false);
  const withSource = valid(actions.addTemplateSection(none, product, a, 'wear', 'fit'));
  const restored = valid(actions.addTemplateSection(withSource, product, a, 'points'));
  assert.ok(restored.flatMap(b => b.pointItems || []).some(i => i.kind === 'wornCrop'));
});

test('a user example replacement detaches only the selected set member, including selection-origin metadata', () => {
  const original = board(), member = original.find(b => b.spaceGroupId && b.cutType === 'styling');
  const example = catalog.find(e => e.cutType === 'styling' && e.shot === 'medium' && e.id !== member.exampleId);
  const { patch } = generationExampleSelectionPatch(member, example, { clothingType: 'top' });
  const next = { ...member, ...patch };
  assert.equal(next.spaceGroupId, null);
  assert.equal(next.spaceVariation, null);
  assert.equal(next.spaceSetMemberOrder, null);
  assert.equal(next.setSelectionOrigin, null);
  assert.equal(next.refScope, 'all');
  assert.equal(next.shot, example.shot);
  assert.equal(original.find(b => b.id === member.id), member);
  assert.equal(Object.hasOwn(detachSpaceMembership(member), 'setSelectionOrigin'), false);
  valid(original.map(b => b.id === member.id ? next : b));
});

for (const templateId of ['emo', 'show', 'guide']) for (const kind of Object.keys(pageTemplatesCatalog.kinds)) {
  test(`${templateId}: auto-fill ${kind} leaves no empty photo slot`, () => {
    const a = analysisFor(templateId), original = board(templateId);
    let before = original;
    if (!pageTemplatesCatalog.kinds[kind].repeatable && kind !== 'first') {
      const existing = before.find(b => b.pageSectionKind === kind);
      if (existing) before = actions.removeTemplateSection(before, existing.pageSectionKey, { product });
    }
    const added = valid(actions.addTemplateSection(before, product, a, kind, kind === 'wear' ? 'styling' : undefined, { catalog, gender: 'women' }));
    const filled = assignGenerationExamples(added, { catalog, product, gender: 'women' }).blocks;
    valid(filled);
    const page = assemblePage({ storyboard: filled, product, analysis: a, cutResults: filled.filter(b => b.source === 'ai').map(b => ({ blockId: b.id, imageUrl: `photo:${b.id}` })) });
    const images = page.filter(b => b.pageSectionKey).flatMap(b => b.elements).filter(e => e.type === 'image');
    assert.ok(images.length);
    assert.ok(images.every(e => e.src && e.sourceBlockId), `${kind} has an empty photo`);
  });
}

test('dragging a generated example creates two cuts with the selected example and a different shot in the same place', () => {
  const original = board(), set = storyboardSpaceSetsFor({ gender: 'women', clothingType: 'top' }).find(s => s.setType === 'styling' && new Set(s.members.map(m => m.shot)).size === 2);
  const example = catalog.find(e => e.id === set.members[0].exampleId);
  const next = valid(actions.addTemplateSection(original, product, analysisFor('emo'), 'wear', 'styling', { example, catalog, gender: 'women' }));
  const added = next.filter(b => !original.some(old => old.id === b.id));
  assert.equal(added.length, 2);
  assert.equal(added[0].exampleId, example.id);
  assert.notEqual(added[0].shot, added[1].shot);
  assert.ok(added.every(b => set.members.some(m => m.exampleId === b.exampleId)));
});

test('a standalone styling example dragged into showroom keeps its family and location through normalization', () => {
  const original = board('show');
  const meta = { gender: 'women', applicableClothingTypes: ['top'], variants: ['all'] };
  const examples = [{ ...meta, id: 'standalone-full', cutType: 'styling', shot: 'full', direction: 'front', placeType: 'park-garden', thumb: 'photo:full' },
    { ...meta, id: 'standalone-medium', cutType: 'styling', shot: 'medium', direction: 'front', placeType: 'park-garden', thumb: 'photo:medium' }];
  const next = valid(actions.addTemplateSection(original, product, analysisFor('show'), 'wear', 'styling', { example: examples[0], catalog: examples }));
  const added = ensureSections(next).filter(b => !original.some(old => old.id === b.id));
  assert.deepEqual(added.map(b => b.exampleId), ['standalone-full', 'standalone-medium']);
  assert.ok(added.every(b => b.cutType === 'styling' && !b.spaceGroupId && b.refScope === 'all'));
});

test('drag companion respects the current product, gender and model eligibility', () => {
  const original = board(), example = catalog.find(item => item.id === 'ex_styling_women_top_full_snapshot_02');
  const next = actions.addTemplateSection(original, product, analysisFor('emo'), 'wear', 'styling', { example, catalog, gender: 'women' });
  const added = next.filter(block => !original.some(old => old.id === block.id));
  assert.equal(added.length, 2);
  assert.ok(added.every(block => { const e = catalog.find(item => item.id === block.exampleId); return e.gender === 'women' && e.applicableClothingTypes.includes('top'); }));
});

test('dragging a rotation-set example uses another angle from that same set for the new daily section', () => {
  const original = board(), set = storyboardSpaceSetsFor({ gender: 'women', clothingType: 'top' }).find(item => item.setType === 'horizon-rotation');
  const example = catalog.find(item => item.id === set.members[0].exampleId);
  const next = valid(actions.addTemplateSection(original, product, analysisFor('emo'), 'wear', 'styling', { example, catalog, gender: 'women' }));
  const added = ensureSections(next).filter(block => !original.some(old => old.id === block.id));
  assert.equal(added.length, 2);
  assert.ok(added.every(block => set.members.some(member => member.exampleId === block.exampleId)));
  assert.equal(new Set(added.map(block => block.spaceGroupId)).size, 1);
  assert.ok(added[0].spaceGroupId);
  assert.notEqual(added[0].direction, added[1].direction);
  assert.ok(added.every(block => block.contentRole === 'coordination'));
});
