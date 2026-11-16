import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { componentModule } from './helpers/reactHarness.mjs';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { getTemplate, pageTemplatesCatalog } from '../../src/lib/pageTemplates/catalog.js';
import { defaultPageStyle, analysisPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { seedDetailTargets } from '../../src/lib/pageTemplates/detailBudget.js';
import { wornCropRegion } from '../../src/lib/pageTemplates/wornCropGeometry.js';
import { validateStoryboardV4 } from '../../src/lib/pageTemplates/validateStoryboard.js';
import { genderForClothingType } from '../../src/lib/productGender.js';
import { matchingIdsForColor } from '../../src/lib/colorwayMatching.js';
import { ensureSections } from '../../src/lib/sections.js';
import { productPresentationOf } from '../../src/lib/storyboardTaxonomy.js';
import { Placeholder } from '../../src/mock/placeholders.js';
import { uid } from '../../src/lib/ids.js';
import { colorList, pickEntrySets, entryStylingMembers, hasFullAndMediumMembers, seededPick, spaceSetMembersForProduct } from '../../src/lib/storyboardEntryPlacement.js';
import { filterSpaceSetsForModel } from '../../src/lib/identityScope.js';
import { STORYBOARD_SPACE_SETS, inferStoryboardSpaceSet, spaceSetGroupId } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { createSpaceSetMembers, replaceSpaceSetRun, insertSpaceSet } from '../../src/lib/storyboardSpaceSets.js';
import { canReplaceStoryboardSet } from '../../src/lib/pageTemplates/photoActions.js';
import { stageAdditions, similarSpaceSetMember } from '../../src/lib/pageTemplates/stageAdditions.js';
import { sectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';
import { wearUsageForBlock } from '../../src/lib/pageTemplates/slots.js';
import { createSimilarCutBlock } from '../../src/lib/storyboardSimilarCuts.js';
import { setPhotoGroupId, normalizeNeutralSetBackground } from '../../src/lib/storyboardSetOptions.js';
import { updateHorizonGroupBackground } from '../../src/lib/horizonBackground.js';
import { reconcileHorizonGroupBackground } from '../../src/lib/horizonBackgroundAvailability.js';

const byId = id => STORYBOARD_SPACE_SETS.find(set => set.id === id);
const seven = byId('horizon-sequence-figma-s11-v2');
const linen = byId('horizon-sequence-06-women-top-linen-r2');
const mixed = byId('horizon-sequence-05-women-top-draped');
const product = (count, clothingType = 'bottom') => ({ clothingType, colors: Array.from({ length: count }, (_, index) => ({
  id: `c${index}`, name: `색 ${index + 1}`, swatchId: 'blue', isBase: index === 0,
  images: [{ slot: 'Front', src: `/front-${index}.webp` }, { slot: 'Back', src: `/back-${index}.webp` }],
})) });
const analysis = (templateId = 'show') => ({ targetGenders: ['women'], pageStyle: { ...defaultPageStyle(), templateId } });
const album = (blocks, template = 'show') => blocks.filter(block => block.pageSectionKey === `${template}.wear_styling` && block.source !== 'text');

async function fixtureSeed(pool) {
  const { pickEntrySets: pick } = await componentModule('src/lib/storyboardEntryPlacement.js', ['pickEntrySets'], {
    filterSpaceSetsForModel, storyboardSpaceSetsFor: () => pool,
  });
  const picks = [];
  const { seedStoryboard: seed } = await componentModule('src/lib/pageTemplates/seed.js', ['seedStoryboard'], {
    uid, getTemplate, pageTemplatesCatalog, analysisPageStyle, seedDetailTargets, genderForClothingType,
    entryStylingMembers, colorList, spaceSetMembersForProduct, spaceSetGroupId, matchingIdsForColor, ensureSections,
    productPresentationOf, Placeholder, wornCropRegion, pickEntrySets: options => { picks.push(options); return pick(options); },
  });
  return { seed, picks };
}

test('R7b F one-color sequences keep the first six set members or pad to six ordinary cuts', async () => {
  for (const set of [seven, mixed]) {
    const { seed } = await fixtureSeed([set]);
    const p = product(1, set.applicableClothingTypes[0]);
    const blocks = album(seed(p, analysis())), placed = blocks.filter(block => block.spaceGroupId);
    assert.equal(blocks.length, 6); assert.ok(blocks.every(block => block.colorId === 'c0'));
    assert.deepEqual(placed.map(block => block.exampleId), set.members.slice(0, 6).map(member => member.exampleId));
    assert.equal(blocks.filter(block => !block.spaceGroupId).length, Math.max(0, 6 - set.members.length));
  }
});

test('R7b F two colors use the entire representative set, including a seven-member set, then two ordinary extra-color cuts', async () => {
  const { seed, picks } = await fixtureSeed([seven]);
  const blocks = album(seed(product(2), analysis()));
  assert.equal(blocks.length, 9);
  assert.deepEqual(blocks.slice(0, 7).map(block => block.exampleId), seven.members.map(member => member.exampleId));
  assert.equal(new Set(blocks.slice(0, 7).map(block => block.spaceGroupId)).size, 1);
  assert.deepEqual(blocks.slice(7).map(block => [block.colorId, block.shot, block.spaceGroupId]), [['c1', 'full', undefined], ['c1', 'medium', undefined]]);
  assert.equal(picks[0].preferSequenceFraming, false);
});

for (const count of [3, 4]) test(`R7b F ${count} colors each receive two cuts and representative full/medium cuts stay a placed set`, () => {
  const p = product(count), blocks = seedStoryboard(p, analysis(), { projectId: 'r7b-colors' }), photos = album(blocks);
  assert.equal(photos.length, count * 2);
  for (const color of p.colors) {
    const pair = photos.filter(block => block.colorId === color.id);
    assert.equal(pair.length, 2); assert.deepEqual(new Set(pair.map(block => block.shot)), new Set(['full', 'medium']));
  }
  const base = photos.filter(block => block.colorId === 'c0'), set = inferStoryboardSpaceSet(base[0].spaceGroupId);
  assert.ok(set); assert.equal(base[0].spaceGroupId, base[1].spaceGroupId);
  assert.equal(setPhotoGroupId(base[0], blocks), base[0].spaceGroupId);
  assert.deepEqual(base.map(block => block.exampleId), spaceSetMembersForProduct(set, p).map(member => member.exampleId));
  assert.deepEqual(base.map(block => block.spaceSetMemberOrder), [...base.map(block => block.spaceSetMemberOrder)].sort((a, b) => a - b));
  assert.ok(photos.filter(block => block.colorId !== 'c0').every(block => !block.spaceGroupId));
  assert.equal(validateStoryboardV4(blocks), blocks);
});

test('R7b F representative color comes from isBase rather than array position and extra colors still stop at three', () => {
  const p = product(5); p.colors[0].isBase = false; p.colors[3].isBase = true;
  const blocks = album(seedStoryboard(p, analysis()));
  assert.equal(blocks.length, 8);
  assert.deepEqual(blocks.slice(0, 2).map(block => block.colorId), ['c3', 'c3']);
  assert.deepEqual([...new Set(blocks.map(block => block.colorId))], ['c3', 'c0', 'c1', 'c2']);
  assert.ok(blocks.length <= 12);
});

test('R7b F three-color entry prefers full and medium sequences over linen using the unchanged sequence seed', async () => {
  const { pickEntrySets: pick } = await componentModule('src/lib/storyboardEntryPlacement.js', ['pickEntrySets'], {
    filterSpaceSetsForModel, storyboardSpaceSetsFor: () => [linen, mixed],
  });
  assert.equal(hasFullAndMediumMembers(linen), false); assert.equal(hasFullAndMediumMembers(mixed), true);
  for (const projectId of ['default', 'r7b-1', 'r7b-2', 'r7b-3']) {
    const options = { gender: 'women', clothingType: 'top', projectId, stylingCount: 2 };
    assert.equal(pick(options).sequenceSet, seededPick([linen, mixed], `${projectId}:entry:sequence`));
    assert.equal(pick({ ...options, preferSequenceFraming: true }).sequenceSet, seededPick([mixed], `${projectId}:entry:sequence`));
  }
  const { seed, picks } = await fixtureSeed([linen, mixed]);
  const base = album(seed(product(3, 'top'), analysis())).filter(block => block.colorId === 'c0');
  assert.deepEqual(base.map(block => block.exampleId), mixed.members.slice(0, 2).map(member => member.exampleId));
  assert.equal(picks[0].preferSequenceFraming, true);
});

test('R7b F linen-only fallback selects its first two members in one placed group', async () => {
  const { seed } = await fixtureSeed([linen]);
  const blocks = album(seed(product(3, 'top'), analysis()));
  assert.deepEqual(blocks.slice(0, 2).map(block => block.exampleId), linen.members.slice(0, 2).map(member => member.exampleId));
  assert.deepEqual(blocks.slice(0, 2).map(block => block.shot), ['medium', 'medium']);
  assert.equal(setPhotoGroupId(blocks[0], blocks), blocks[0].spaceGroupId);
});

test('R7b F shared selection prefers front for both framings, fills a missing framing, and returns set order without mutation', () => {
  const set = { setType: 'horizon-sequence', members: [
    { order: 5, shot: 'medium', direction: 'front' }, { order: 2, shot: 'full', direction: 'side' },
    { order: 4, shot: 'full', direction: 'front' }, { order: 1, shot: 'medium', direction: 'back' },
  ] }, before = structuredClone(set);
  assert.deepEqual(spaceSetMembersForProduct(set, product(3)).map(member => member.order), [4, 5]);
  assert.deepEqual(set, before);
  const noFront = { ...set, members: set.members.map(member => ({ ...member, direction: 'back' })) };
  assert.deepEqual(spaceSetMembersForProduct(noFront, product(3)).map(member => member.order), [1, 2]);
  const noMedium = { ...set, members: set.members.map(member => ({ ...member, shot: 'full' })) };
  assert.deepEqual(spaceSetMembersForProduct(noMedium, product(3)).map(member => member.order), [1, 4]);
  assert.deepEqual(spaceSetMembersForProduct({ ...set, members: [{ order: 1, shot: 'full', direction: 'front' }] }, product(3)).map(member => member.order), [1]);
});

for (const [count, baseCount] of [[1, 6], [2, 4], [3, 2], [4, 2]]) test(`R7b F ${count}-color sequence without an eligible set falls back to ${baseCount} ordinary representative cuts`, async () => {
  const { seed } = await fixtureSeed([]);
  const blocks = album(seed(product(count), analysis()));
  assert.equal(blocks.filter(block => block.colorId === 'c0').length, baseCount);
  assert.deepEqual(blocks.filter(block => block.colorId === 'c0').map(block => block.shot), Array.from({ length: baseCount }, (_, i) => i % 2 ? 'medium' : 'full'));
  assert.ok(blocks.every(block => !block.spaceGroupId));
  assert.equal(blocks.length, baseCount + Math.min(count - 1, 3) * 2);
});

for (const count of [1, 2, 3, 4]) test(`R7b F ${count} colors retain daily-styling and rotation counts`, () => {
  const p = product(count), daily = seedStoryboard(p, analysis('emo')), studio = seedStoryboard(p, analysis());
  assert.equal(album(daily, 'emo').filter(block => block.colorId === 'c0').length, count === 1 ? 6 : 4);
  assert.equal(album(daily, 'emo').length, (count === 1 ? 6 : 4) + Math.min(count - 1, 3) * 2);
  for (const [blocks, template] of [[daily, 'emo'], [studio, 'show']]) {
    const rotation = blocks.filter(block => block.pageSectionKey === `${template}.wear_fit`);
    assert.equal(rotation.length, 3); assert.deepEqual(rotation.map(block => block.direction), ['front', 'side', 'back']);
  }
});

test('R7b F numeric menu sequence count and selection are unchanged even with four colors', async () => {
  const { seed, picks } = await fixtureSeed([linen, mixed]);
  const section = structuredClone(getTemplate('show').sections.find(section => section.photoSlots?.[0]?.recipe?.set === 'sequence'));
  section.photoSlots[0].count = 4;
  const blocks = seed(product(4, 'top'), analysis(), { sectionDefinitions: [section], stylingSet: linen });
  assert.equal(blocks.length, 4); assert.ok(blocks.every(block => block.colorId === 'c0'));
  const expected = entryStylingMembers(linen);
  assert.deepEqual(blocks.map(block => block.exampleId), Array.from({ length: 4 }, (_, index) => expected[index % expected.length].exampleId));
  assert.equal(picks[0].preferSequenceFraming, false);
});

test('R7b F remaining set photos use the existing pale row rules and same-framing addition path', () => {
  const p = product(4, 'outer'), a = { ...analysis(), targetGenders: ['men'] };
  const blocks = seedStoryboard(p, a), photos = album(blocks);
  assert.equal(photos.length, 8); assert.deepEqual(stageAdditions(blocks, photos[0].pageSectionKey, { product: p }), []);
  const medium = photos.find(block => block.spaceGroupId && block.shot === 'medium');
  assert.ok(similarSpaceSetMember(blocks, medium));
});

const group = (count = 3, set = mixed) => createSpaceSetMembers(set, { source: 'ai', taxonomyVersion: 4,
  pageSectionKey: 'show.wear_styling', pageSectionKind: 'wear', pageSectionUsage: 'fit', pageDesignId: 'P02-D01',
  sectionId: 'wear', sectionRole: 'studio', contentRole: 'fit', colorId: 'c0', sectionCustom: true,
}, { spaceGroupId: spaceSetGroupId(set.id, 'r7b'), members: set.members.slice(0, count), makeId: (_member, index) => `root${index}` });

test('R7b G cap checks selected members and retained followers, matching the actual replacement', () => {
  const roots = group(), outside = Array.from({ length: 10 }, (_, i) => ({ ...roots[0], id: `outside${i}`, spaceGroupId: null }));
  assert.equal(canReplaceStoryboardSet([...roots, ...outside], roots[0].spaceGroupId, seven, product(4)), true);
  assert.equal(canReplaceStoryboardSet([...roots, ...outside], roots[0].spaceGroupId, seven, product(2)), false);
  const kept = createSimilarCutBlock(roots[0], 'kept'), dropped = createSimilarCutBlock(roots[2], 'dropped');
  const before = [...roots.slice(0, 1), kept, ...roots.slice(1), dropped, ...outside];
  assert.equal(canReplaceStoryboardSet(before, roots[0].spaceGroupId, seven, product(4)), false, 'retained follower makes 13 photos');
  const withoutKept = before.filter(block => block.id !== kept.id);
  assert.equal(canReplaceStoryboardSet(withoutKept, roots[0].spaceGroupId, seven, product(4)), true, 'a removed original follower does not count');
  const next = replaceSpaceSetRun(withoutKept, roots[0].spaceGroupId, seven, { spaceGroupId: 'new', product: product(4) });
  assert.equal(next.length, 12); assert.ok(!next.some(block => block.id === dropped.id));
  assert.deepEqual(next.slice(0, 2).map(block => block.exampleId), [seven.members[0].exampleId, seven.members[4].exampleId]);
});

test('R7b G one/two colors, place sets and rotations still replace with the complete set; explicit shuffle members stay explicit', () => {
  const roots = group();
  for (const count of [1, 2]) assert.equal(replaceSpaceSetRun(roots, roots[0].spaceGroupId, seven,
    { spaceGroupId: 'new', product: product(count) }).length, 7);
  for (const set of STORYBOARD_SPACE_SETS.filter(set => set.setType !== 'horizon-sequence').slice(0, 10)) {
    assert.equal(spaceSetMembersForProduct(set, product(4)), set.members);
    assert.equal(replaceSpaceSetRun(roots, roots[0].spaceGroupId, set, { spaceGroupId: 'new', product: product(4) }).length, set.members.length);
  }
  const explicit = seven.members.slice(0, 4);
  assert.equal(replaceSpaceSetRun(roots, roots[0].spaceGroupId, seven, { spaceGroupId: 'new', product: product(4), members: explicit }).length, 4);
});

function chooseHarness(initial, p, mode = 'replace') {
  let blocks = initial, picker = { mode, targetSid: initial[0].sectionId, spaceGroupId: mode === 'replace' ? initial[0].spaceGroupId : null }, n = 0;
  const messages = [], errors = [], snapshots = [];
  const scope = { sectionRuns, wearUsageForBlock, setPhotoGroupId, canReplaceStoryboardSet, inferStoryboardSpaceSet,
    spaceSetGroupId, spaceSetMembersForProduct, replaceSpaceSetRun, insertSpaceSet, updateHorizonGroupBackground,
    normalizeNeutralSetBackground, reconcileHorizonGroupBackground, catalogs: { detailProduct: p },
    atomicSavingRef: { current: false }, uid: () => `choose${++n}`, toast: { push: message => messages.push(message) },
    setSetPickerError: error => errors.push(error), setSetPicker: value => { picker = value; },
    atomicBoardChange: async build => { snapshots.push(blocks); blocks = build(blocks); return blocks; } };
  Object.defineProperties(scope, { blocks: { get: () => blocks }, setPicker: { get: () => picker } });
  const source = readFileSync('src/features/storyboard/Storyboard.jsx', 'utf8');
  const start = source.indexOf('  const chooseSpaceSet ='), end = source.indexOf('  const locked =', start);
  const choose = new Function('scope', `with (scope) { ${source.slice(start, end)} return chooseSpaceSet; }`)(scope);
  return { choose, messages, errors, snapshots, blocks: () => blocks };
}

for (const count of [1, 2, 3, 4]) test(`R7b G actual choose callback uses ${count >= 3 ? 2 : 7} members with ${count} colors and the correct toast`, async () => {
  const roots = group(), h = chooseHarness(roots, product(count)); await h.choose(seven);
  assert.equal(h.blocks().length, count >= 3 ? 2 : 7);
  assert.equal(h.messages.includes('색이 많아서 2장만 먼저 놓았어요'), count >= 3);
  assert.equal(h.snapshots.length, 1); assert.ok(!h.errors.filter(Boolean).length);
});

test('R7b G complete short sets have no partial toast and add mode always adds the full set', async () => {
  const roots = group();
  for (const count of [1, 2]) {
    const short = { ...seven, members: seven.members.slice(0, count) }, h = chooseHarness(roots, product(4));
    await h.choose(short); assert.equal(h.blocks().length, count); assert.deepEqual(h.messages, []);
  }
  const placeSet = STORYBOARD_SPACE_SETS.find(set => set.setType === 'styling' && set.gender === 'women' && set.applicableClothingTypes.includes('bottom'));
  const place = group(2, placeSet).map(block => ({ ...block, pageSectionKey: 'emo.wear_styling', pageSectionUsage: 'styling', sectionRole: 'styling' }));
  const add = chooseHarness(place, product(4), 'add'); await add.choose(seven);
  assert.equal(add.blocks().length, place.length + seven.members.length); assert.deepEqual(add.messages, []);
});

test('R7b G partial replacement preserves retained followers, removes missing-source followers and combines both approved notices', async () => {
  const roots = group(), kept = createSimilarCutBlock(roots[0], 'kept'), dropped = createSimilarCutBlock(roots[2], 'dropped');
  const h = chooseHarness([roots[0], kept, roots[1], roots[2], dropped], product(4)); await h.choose(seven);
  assert.deepEqual(h.blocks().map(block => block.id), [roots[0].id, kept.id, roots[1].id]);
  assert.equal(h.messages.length, 1);
  assert.equal(h.messages[0], '원본과 연결된 비슷한 컷 1컷도 함께 정리했어요. 색이 많아서 2장만 먼저 놓았어요');
});

test('R7b F/G color counting uses the same product list and its established empty-product fallback', () => {
  const p = product(2); assert.equal(colorList(p), p.colors);
  assert.equal(colorList({ colors: [] }).length, 1); assert.equal(colorList().length, 1);
  assert.equal(spaceSetMembersForProduct(seven, p).length, 7);
  assert.equal(spaceSetMembersForProduct(seven, { ...p, colors: product(3).colors }).length, 2);
});
