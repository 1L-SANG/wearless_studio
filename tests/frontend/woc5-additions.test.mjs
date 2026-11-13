import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stageAdditions, addStageAddition, removePairedPhoto } from '../../src/lib/pageTemplates/stageAdditions.js';
import { sectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';
import { sectionDesignChoices, currentSectionDesignId } from '../../src/lib/pageTemplates/designChoices.js';
import { changeTemplateSectionDesign, removeTemplateSection, sectionRemovalImpact } from '../../src/lib/pageTemplates/sectionActions.js';
import { patchStoryboardPhoto, removeStoryboardPhoto } from '../../src/lib/pageTemplates/photoActions.js';
import { reconcileSectionDependencies } from '../../src/lib/pageTemplates/sectionDependencies.js';
import { uniqueGenerationCutCount } from '../../src/lib/generationCutCount.js';
import { validateStoryboardV4 } from '../../src/lib/pageTemplates/validateStoryboard.js';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';
import { detailPageCreditCost } from '../../src/lib/limits.js';
import { createSpaceSetMembers } from '../../src/lib/storyboardSpaceSets.js';
import { storyboardSpaceSetsFor, spaceSetGroupId } from '../../src/lib/storyboardSpaceSetCatalog.js';

const cases = JSON.parse(readFileSync(new URL('../fixtures/page_templates_cases.json', import.meta.url))).cases;
const setup = (template = 'emo') => {
  const { storyboard: blocks, product, analysis } = structuredClone(cases.find(item => item.name === `${template}-2-mono`).input);
  blocks.filter(b => b.pageSectionKind === 'color_product').forEach(b => { b.productPresentation = 'ghost'; });
  blocks.filter(b => b.pageSectionKind === 'first').forEach(b => { b.pageDesignId = template === 'emo' ? 'P01-D11' : 'P01-SHOW-01'; });
  const lead = blocks.find(b => b.pageSectionKind === 'points' && b.source === 'text');
  if (lead) lead.pointItems = blocks.filter(b => b.pageSectionKind === 'points' && b.source === 'ai').map(b => ({ kind: 'detail', sourceBlockId: b.id, detailTargetId: b.detailTargetId }));
  return { blocks, product, analysis: analysis || {} };
};
const runOf = (blocks, kind) => sectionRuns(blocks).find(run => run.kind === kind);
const additions = (state, kind) => stageAdditions(state.blocks, runOf(state.blocks, kind)?.key, state);
const add = (state, kind, item = additions(state, kind)[0]) => addStageAddition(state.blocks, runOf(state.blocks, kind).key, item, state);
const count = blocks => uniqueGenerationCutCount(blocks, { clothingType: 'top' });
const checked = blocks => { assert.equal(validateStoryboardV4(blocks), blocks); return blocks; };

for (const template of ['emo', 'show']) test(`${template} first ghost reuse costs zero and paired removal/undo preserves the source`, () => {
  const state = setup(template), before = structuredClone(state.blocks);
  assert.equal(additions(state, 'first')[0].kind, 'firstGhost');
  const next = checked(add(state, 'first'));
  assert.equal(next.length, before.length); assert.equal(count(next), count(before));
  assert.equal(additions({ ...state, blocks: next }, 'first').length, 0);
  const removed = checked(removePairedPhoto(next, runOf(next, 'first').key, state.product));
  assert.deepEqual(removed, before); assert.deepEqual(state.blocks, before);
  assert.equal(currentSectionDesignId(runOf(next, 'first'), next), template === 'emo' ? 'P01-D11' : 'P01-SHOW-01');
});

test('first ghost uses a clean dedicated recipe for flat, survives mode transitions and is idempotent', () => {
  const state = setup(), source = state.blocks.find(b => b.pageSectionKind === 'color_product');
  state.blocks = patchStoryboardPhoto(state.blocks, source.id, { productPresentation: 'flat' }, state.product);
  const next = checked(add(state, 'first'));
  const ghost = next.find(b => b.slotKey === 'first.ghost');
  assert.ok(ghost); assert.equal(count(next), count(state.blocks) + 1);
  for (const key of ['exampleId', 'spaceGroupId', 'spaceVariation', 'sideStyle', 'similarSourceBlockId', 'ownImages', 'pointItems']) assert.equal(ghost[key], undefined, key);
  assert.equal(ghost.productPresentation, 'ghost'); assert.deepEqual(ghost.matchIds, []); assert.deepEqual(ghost.refImages, []); assert.deepEqual(ghost.refAssetIds, []); assert.equal(ghost.angle, 'same');
  assert.deepEqual(reconcileSectionDependencies(next, next, state.product), next);
  const natural = checked(patchStoryboardPhoto(next, source.id, { productPresentation: 'natural_light' }, state.product));
  assert.equal(natural.find(b => b.slotKey === 'first.ghost').id, ghost.id);
  const reusable = checked(patchStoryboardPhoto(natural, source.id, { productPresentation: 'ghost' }, state.product));
  assert.ok(!reusable.some(b => b.slotKey === 'first.ghost')); assert.equal(count(reusable), count(state.blocks));
  assert.deepEqual(next.find(b => b.id === ghost.id), ghost, 'undo snapshot stays unchanged');
});

test('hidden pairs retain enabled ghost across base design choices without appearing in the picker', () => {
  const state = setup(); state.blocks = checked(add(state, 'first'));
  const key = runOf(state.blocks, 'first').key;
  const choices = sectionDesignChoices(runOf(state.blocks, 'first'), { storyboard: state.blocks, product: state.product });
  assert.ok(choices.some(d => d.id === 'P01-D09')); assert.ok(choices.every(d => !d.pickerHidden));
  const next = checked(changeTemplateSectionDesign(state.blocks, key, 'P01-D09', state));
  assert.equal(runOf(next, 'first').designId, 'P01-D09-G'); assert.equal(count(next), count(state.blocks));
  const removed = checked(removePairedPhoto(next, key, state.product));
  assert.equal(runOf(removed, 'first').designId, 'P01-D09');
});

test('first ghost waits for undecided product modes and materializes when the original source disappears', () => {
  const state = setup();
  state.blocks.find(b => b.pageSectionKind === 'color_product').productPresentation = undefined;
  assert.equal(additions(state, 'first').length, 0);
  const valid = setup(); valid.blocks = add(valid, 'first');
  const removed = checked(removeTemplateSection(valid.blocks, runOf(valid.blocks, 'color_product').key, valid));
  assert.equal(removed.find(b => b.slotKey === 'first.ghost').productPresentation, 'ghost');
  assert.equal(reconcileSectionDependencies(removed, valid.blocks, valid.product).some(b => b.slotKey === 'first.ghost'), false);
});

test('fabric product reuses front at zero cost and disappears with that source, never creates a product block', () => {
  const state = setup();
  assert.equal(additions(state, 'fabric')[0].kind, 'fabricProduct');
  const next = checked(add(state, 'fabric'));
  assert.equal(count(next), count(state.blocks)); assert.equal(next.length, state.blocks.length);
  assert.deepEqual(removePairedPhoto(next, runOf(next, 'fabric').key, state.product), state.blocks);
  const removed = checked(removeTemplateSection(next, runOf(next, 'color_product').key, state));
  assert.equal(runOf(removed, 'fabric').designId, 'T03-D01');
  assert.equal(additions({ ...state, blocks: removed }, 'fabric').length, 0);
});

test('daily pale member is next unused in this instance, inserts in place, and stops at exhaustion or cap', () => {
  const state = setup(), run = runOf(state.blocks, 'wear');
  const set = storyboardSpaceSetsFor({ gender: 'women', clothingType: 'top' }).find(s => s.setType === 'styling' && s.members.length >= 3);
  assert.ok(set);
  const members = createSpaceSetMembers(set, run.blocks.find(b => b.source === 'ai'), { spaceGroupId: spaceSetGroupId(set.id, 'woc5'), members: set.members.slice(0, 2) });
  state.blocks = [...state.blocks.slice(0, run.start), ...members, ...state.blocks.slice(run.end)];
  const item = additions(state, 'wear')[0]; assert.equal(item.kind, 'spaceMember'); assert.equal(item.member.exampleId, set.members[2].exampleId);
  const next = checked(add(state, 'wear', item)), added = next.find(b => !state.blocks.some(old => old.id === b.id));
  assert.equal(added.spaceGroupId, members[0].spaceGroupId); assert.equal(added.refScope, 'pose'); assert.equal(count(next), count(state.blocks) + 1);
  assert.deepEqual(removeStoryboardPhoto(next, added.id, state.product), state.blocks);
  const full = createSpaceSetMembers(set, members[0], { spaceGroupId: members[0].spaceGroupId });
  assert.equal(stageAdditions(full, run.key, state).length, 0);
  const capped = Array.from({ length: 12 }, (_, i) => ({ ...members[i % 2], id: `cap-${i}` }));
  assert.equal(stageAdditions(capped, run.key, state).length, 0);
  assert.equal(stageAdditions(members.map(b => ({ ...b, cutType: 'horizon', pageSectionUsage: 'fit' })), run.key, state).length, 0);
});

test('points fabric requires a real candidate, no fabric run, and available four-photo budget', () => {
  const state = setup(); state.blocks = state.blocks.filter(b => b.pageSectionKind !== 'fabric');
  const pointRun = runOf(state.blocks, 'points'), third = { ...pointRun.blocks.at(-1), id: 'third-detail', slotKey: 'points.detail3', detailTargetId: 'third' };
  state.blocks.splice(pointRun.end, 0, third);
  pointRun.blocks[0].pointItems.push({ kind: 'detail', sourceBlockId: third.id, detailTargetId: third.detailTargetId });
  const target = { id: 'dt1_fabric', kind: 'fabric', informationGroup: 'fabric', photoUse: 'standalone', visibility: 'clear', direction: 'front', rank: 1,
    sourceSlot: 'Front', sourceIndex: 0, region: { x: .2, y: .3, w: .3, h: .2 }, label: '원단 확대' };
  state.analysis = { detailRecommendations: { version: 1, status: 'ready', candidates: [target] } };
  assert.equal(additions({ ...state, analysis: {} }, 'points').length, 0);
  const item = additions(state, 'points')[0]; assert.equal(item.kind, 'pointsFabric'); assert.equal(item.block.detailTargetId, target.id);
  const next = checked(add(state, 'points', item));
  assert.equal(count(next), count(state.blocks) + 1); assert.equal(next.filter(b => b.shot === 'detail').length, 4);
  assert.equal(next.find(b => b.pointItems).pointItems.at(-1).detailTargetId, target.id);
  assert.equal(additions({ ...state, blocks: next }, 'points').length, 0);
  const added = next.find(b => b.detailTargetId === target.id);
  assert.deepEqual(removeStoryboardPhoto(next, added.id, state.product), state.blocks);
  assert.equal(additions({ ...state, blocks: setup().blocks }, 'points').length, 0);
});

test('color product never has a pale tile and keeps requiresBack behavior', () => {
  const state = setup(); assert.equal(additions(state, 'color_product').length, 0);
  for (const back of [false, true]) {
    const product = { clothingType: 'top', colors: [{ id: 'base', isBase: true, images: [{ slot: 'Front', src: 'front' }, ...(back ? [{ slot: 'Back', src: 'back' }] : [])] }] };
    for (const templateId of ['emo', 'show']) {
      const board = seedStoryboard(product, { pageStyle: { ...defaultPageStyle(), templateId } });
      assert.equal(board[0].pageSectionKey, `${templateId}.first`);
      assert.equal(board.some(b => b.pageSectionKind === 'color_product' && b.direction === 'back'), back);
      checked(board);
    }
  }
});

test('first ghost updates to the new base without changing its ID or inheriting source settings', () => {
  const state = setup(); state.blocks = state.blocks.filter(b => b.pageSectionKind !== 'color_product');
  const next = checked(add(state, 'first')), ghost = next.find(b => b.slotKey === 'first.ghost');
  const product = { ...state.product, colors: state.product.colors.map((c, i) => ({ ...c, isBase: i === 1 })) };
  const updated = checked(reconcileSectionDependencies(next, next, product));
  assert.equal(updated.find(b => b.id === ghost.id).colorId, product.colors[1].id);
  assert.equal(updated.length, next.length); assert.equal(count(updated), count(next));
  assert.equal(detailPageCreditCost(count(next)) - detailPageCreditCost(count(state.blocks)), 19);
});

test('dedicated ghosts use the same recipe identity as another identical generated product cut', () => {
  const state = setup(), source = state.blocks.find(b => b.pageSectionKind === 'color_product' && b.direction === 'front');
  const without = state.blocks.filter(b => b.pageSectionKind !== 'color_product');
  const next = add({ ...state, blocks: without }, 'first'), ghost = next.find(b => b.slotKey === 'first.ghost');
  const equivalent = { ...ghost, id: source.id, sectionId: source.sectionId, pageSectionKey: source.pageSectionKey, pageSectionKind: 'color_product', slotKey: source.slotKey };
  assert.equal(count([ghost, equivalent]), 1);
  assert.equal(count([ghost, { ...equivalent, productPresentation: 'flat' }]), 2);
});

test('a mixed crop/detail points run retains its independent fabric when the last wearer is removed', () => {
  const fixture = structuredClone(cases.find(c => c.name === 'woc5-emo-crops-and-fabric').input);
  const before = fixture.storyboard, lead = before.find(b => b.pointItems), detail = before.find(b => b.id === lead.pointItems.find(i => i.kind === 'detail').sourceBlockId);
  const after = checked(reconcileSectionDependencies(before, before.filter(b => b.pageSectionKind !== 'wear'), fixture.product));
  assert.deepEqual(after.find(b => b.id === lead.id).pointItems, [{ kind: 'detail', sourceBlockId: detail.id, detailTargetId: detail.detailTargetId }]);
  assert.ok(after.some(b => b.id === detail.id));
  const cropOnly = before.filter(b => b.id !== detail.id).map(b => b.id === lead.id ? { ...b, pointItems: b.pointItems.filter(i => i.kind === 'wornCrop') } : b);
  assert.ok(!reconcileSectionDependencies(cropOnly, cropOnly.filter(b => b.pageSectionKind !== 'wear'), fixture.product).some(b => b.pageSectionKind === 'points'));
});

test('wear removal confirmation explains lost crops while the independent detail stays', () => {
  const fixture = structuredClone(cases.find(c => c.name === 'woc5-emo-crops-and-fabric').input);
  const lead = fixture.storyboard.find(b => b.pointItems), cropSource = lead.pointItems.find(item => item.kind === 'wornCrop').sourceBlockId;
  const key = fixture.storyboard.find(b => b.id === cropSource).pageSectionKey;
  const before = fixture.storyboard.filter(b => b.pageSectionKind !== 'wear' || b.pageSectionKey === key);
  const impact = sectionRemovalImpact(before, key, { product: fixture.product });
  assert.equal(impact.confirmationRequired, true);
  assert.match(impact.consequenceText, /따온 사진만 빠지고 디테일 사진은 남아요/);
  assert.doesNotMatch(impact.consequenceText, /원본을 남은 착용 사진으로 옮겨요/);
  checked(impact.nextBlocks);
});

test('daily member insertion stays inside the original group in both storage and assembled image order', () => {
  const state = setup(), run = runOf(state.blocks, 'wear');
  const sets = storyboardSpaceSetsFor({ gender: 'women', clothingType: 'top' }).filter(s => s.setType === 'styling' && s.members.length >= 3).slice(0, 2);
  const groups = sets.map((set, i) => createSpaceSetMembers(set, run.blocks[0], { spaceGroupId: spaceSetGroupId(set.id, `group-${i}`), members: set.members.slice(0, 2),
    makeId: (_, j) => `member-${i}-${j}` }).map((b, j) => ({ ...b, slotKey: `wear_styling.wear${i * 2 + j + 1}` })));
  state.blocks = [...state.blocks.slice(0, run.start), ...groups.flat(), ...state.blocks.slice(run.end)];
  const next = checked(add(state, 'wear')), added = next.find(b => !state.blocks.some(old => old.id === b.id));
  const expected = [groups[0][0].id, groups[0][1].id, added.id, ...groups[1].map(b => b.id)];
  assert.deepEqual(runOf(next, 'wear').blocks.map(b => b.id), expected);
  const page = assemblePage({ storyboard: next, product: state.product, analysis: state.analysis });
  assert.deepEqual(page.filter(b => b.pageSectionKey === run.key).flatMap(b => b.elements).filter(e => e.type === 'image').map(e => e.sourceBlockId), expected);
});
