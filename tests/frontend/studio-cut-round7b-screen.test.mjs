import test from 'node:test';
import assert from 'node:assert/strict';
import { nodes, textOf } from './helpers/storyboardComponents.mjs';
import { storyboardScreenHarness } from './helpers/storyboardScreenHarness.mjs';
import { STORYBOARD_SPACE_SETS, inferStoryboardSpaceSet, spaceSetGroupId } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { createSpaceSetMembers } from '../../src/lib/storyboardSpaceSets.js';
import { spaceSetMembersForProduct } from '../../src/lib/storyboardEntryPlacement.js';
import { createSimilarCutBlock } from '../../src/lib/storyboardSimilarCuts.js';

const seven = STORYBOARD_SPACE_SETS.find(set => set.id === 'horizon-sequence-figma-s11-v2');
const initialSet = STORYBOARD_SPACE_SETS.find(set => set.id === 'horizon-set-02-women-fluid-trousers');
const notice = '색이 많아서 2장만 먼저 놓았어요';
const button = (tree, text) => nodes(tree).find(node => node.type === 'button' && textOf(node) === text);
const card = tree => nodes(tree).find(node => node.props?.['data-space-set'] === seven.id);
const product = count => ({ clothingType: 'bottom', colors: Array.from({ length: count }, (_, index) => ({
  id: `r7b-color${index}`, name: `색 ${index + 1}`, swatchId: 'blue', isBase: index === 0,
  images: [{ slot: 'Front', src: `/r7b-front${index}.webp` }, { slot: 'Back', src: `/r7b-back${index}.webp` }],
})) });

async function flushSave(h) {
  window.dispatchEvent(new Event('pagehide'));
  const { sbSaveIdle } = await h.load('/src/features/storyboard/storyboardPersistence.js');
  await sbSaveIdle(); await h.flush(); h.render();
}
async function scene(t, count, { followers = false } = {}) {
  const p = product(count);
  const h = await storyboardScreenHarness({ withToast: true, savedStyle: { templateId: 'show' }, productPatch: p,
    analysisPatch: { clothingType: 'bottom', subCategory: 'pants', targetGenders: ['women'], selectedModelId: null,
      selected_model_id: null, fitProfile: { gender: 'women' } } });
  t.after(() => h.close());
  const source = h.DB.storyboard.find(block => block.pageSectionKey === 'show.wear_styling' && block.spaceGroupId);
  assert.ok(source);
  const seeded = h.DB.storyboard.filter(block => block.pageSectionKey === source.pageSectionKey);
  const base = seeded.filter(block => block.colorId === p.colors[0].id);
  assert.equal(base.length, count >= 3 ? 2 : inferStoryboardSpaceSet(source.spaceGroupId).members.length);
  const initialGroup = spaceSetGroupId(initialSet.id, 'round7b-screen');
  const members = createSpaceSetMembers(initialSet, { ...source, sectionCustom: true }, { spaceGroupId: initialGroup,
    members: initialSet.members.slice(0, followers ? 3 : count >= 3 ? 2 : initialSet.members.length),
    setSelectionOrigin: 'user', makeId: (_member, index) => `r7b-root${index}` }).map((block, index) => {
      const next = { ...block, slotKey: `wear_styling.base${index + 1}` };
      delete next.seedProductKey; delete next.seedFingerprint; return next;
    });
  const arranged = followers ? [members[0], createSimilarCutBlock(members[0], 'r7b-kept'), members[1], members[2],
    createSimilarCutBlock(members[2], 'r7b-dropped')] : members;
  let inserted = false;
  h.DB.storyboard = h.DB.storyboard.flatMap(block => {
    if (block.spaceGroupId !== source.spaceGroupId) return [block];
    if (inserted) return [];
    inserted = true; return arranged;
  });
  const { invalidateStoryboardEntryPrefetch, prefetchStoryboardEntry } = await h.load('/src/features/storyboard/storyboardEntryPrefetch.js');
  invalidateStoryboardEntryPrefetch(h.DB.project.id); await prefetchStoryboardEntry(h.DB.project.id);
  h.render(); await h.flush();
  const row = nodes(h.tree).find(node => node.props?.['data-section-key'] === source.pageSectionKey);
  nodes(row).find(node => node.props?.role === 'button').props.onClick(); h.render(); await h.flush(); await flushSave(h);
  nodes(h.tree).find(node => node.props?.['data-set-change'] === initialGroup).props.onClick(); h.render(); await h.flush();
  assert.ok(card(h.tree));
  return { h, p, members, initialGroup };
}

for (const count of [2, 3, 4]) test(`R7b G real ${count}-color replacement card saves ${count >= 3 ? 2 : 7} of seven members with correct count, pale photos and toast`, async t => {
  const { h, p } = await scene(t, count), before = structuredClone(h.DB.storyboard);
  const target = card(h.tree); assert.equal(target.props.disabled, false); assert.ok(!target.props['aria-label'].includes('자리가 부족해요'));
  const saves = [], originalSave = h.api.saveStoryboard;
  h.api.saveStoryboard = async (...args) => { saves.push(structuredClone(args[1])); return originalSave(...args); };
  await target.props.onClick(); h.render(); await h.flush(); await flushSave(h);
  assert.equal(saves.length, 1);
  const group = h.DB.storyboard.filter(block => inferStoryboardSpaceSet(block.spaceGroupId)?.id === seven.id && !block.similarSourceBlockId);
  assert.equal(group.length, count >= 3 ? 2 : 7);
  assert.deepEqual(group.map(block => block.exampleId), spaceSetMembersForProduct(seven, p).map(member => member.exampleId));
  const current = card(h.tree); assert.equal(current.props['aria-pressed'], true);
  const badge = nodes(current).find(node => node.props?.className?.includes('cutCount'));
  assert.equal(textOf(badge), count >= 3 ? '2컷' : '7컷');
  assert.equal(nodes(current).filter(node => node.props?.['data-unplaced'] === true).length, count >= 3 ? 5 : 0);
  assert.equal(current.props['aria-label'].includes(', 7장 중 2장 놓임'), count >= 3);
  assert.equal(textOf(h.tree).includes(notice), count >= 3);
  const photos = h.DB.storyboard.filter(block => block.pageSectionKey === group[0].pageSectionKey && block.source !== 'text');
  assert.equal(photos.length, count >= 3 ? count * 2 : 9);
  if (count === 4) {
    assert.equal(photos.length, 8, 'seven whole members plus six extra-color cuts would exceed twelve; selected two fit');
    nodes(h.tree).find(node => node.props?.['aria-label'] === '옆 판 닫기').props.onClick(); h.render();
    button(h.tree, '되돌리기').props.onClick(); h.render(); await h.flush(); await flushSave(h);
    assert.deepEqual(h.DB.storyboard, before);
  }
});

test('R7b G real partial replacement keeps retained followers, removes missing-source followers and shows both notices', async t => {
  const { h } = await scene(t, 4, { followers: true });
  await card(h.tree).props.onClick(); h.render(); await h.flush(); await flushSave(h);
  assert.ok(h.DB.storyboard.some(block => block.id === 'r7b-kept'));
  assert.ok(!h.DB.storyboard.some(block => block.id === 'r7b-dropped'));
  assert.ok(!h.DB.storyboard.some(block => block.id === 'r7b-root2'));
  assert.ok(textOf(h.tree).includes('원본과 연결된 비슷한 컷 1컷도 함께 정리했어요. 색이 많아서 2장만 먼저 놓았어요'));
  assert.equal(h.DB.storyboard.filter(block => inferStoryboardSpaceSet(block.spaceGroupId)?.id === seven.id && !block.similarSourceBlockId).length, 2);
});

test('R7b G failed partial replacement restores the complete board and leaves the partial-placement toast absent', async t => {
  const { h } = await scene(t, 4), before = structuredClone(h.DB.storyboard), originalSave = h.api.saveStoryboard;
  h.api.saveStoryboard = async () => { throw new Error('round7b save failure'); };
  await card(h.tree).props.onClick(); h.render(); await h.flush();
  assert.deepEqual(h.DB.storyboard, before); assert.ok(!textOf(h.tree).includes(notice));
  assert.ok(textOf(h.tree).includes('장소 세트를 저장하지 못했어요. 다시 시도해주세요.'));
  h.api.saveStoryboard = originalSave;
});
