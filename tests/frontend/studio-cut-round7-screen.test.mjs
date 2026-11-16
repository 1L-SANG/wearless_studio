import test from 'node:test';
import assert from 'node:assert/strict';
import { nodes, textOf } from './helpers/storyboardComponents.mjs';
import { storyboardScreenHarness } from './helpers/storyboardScreenHarness.mjs';
import { STORYBOARD_SPACE_SETS, spaceSetGroupId } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { createSpaceSetMembers, nextUnusedSpaceSetMember } from '../../src/lib/storyboardSpaceSets.js';
import { directionSpecFromExample } from '../../src/lib/storyboardExampleSelection.js';
import { createSimilarCutBlock } from '../../src/lib/storyboardSimilarCuts.js';

const set = STORYBOARD_SPACE_SETS.find(set => set.id === 'horizon-sequence-figma-s14-v2');
const groupId = spaceSetGroupId(set.id, 'round7-screen');
const product = { clothingType: 'outer', colors: [{ id: 'sky', name: '스카이블루', swatchId: 'blue', isBase: true,
  images: [{ slot: 'Front', src: '/front.webp' }, { slot: 'Back', src: '/back.webp' }] }] };
const button = (tree, label) => nodes(tree).find(node => node.type === 'button' && textOf(node) === label);
const photo = (tree, id) => nodes(tree).find(node => node.props?.['data-storyboard-photo'] === id);
const frame = (tree, id) => nodes(tree).find(node => node.props?.className?.includes('photoFrame') && photo(node, id));
const similar = (tree, id) => button(frame(tree, id), '비슷한 컷 추가');
const pale = tree => nodes(tree).find(node => node.props?.['aria-label'] === '1컷 더 넣기');

async function flushSave(h) {
  window.dispatchEvent(new Event('pagehide'));
  const { sbSaveIdle } = await h.load('/src/features/storyboard/storyboardPersistence.js');
  await sbSaveIdle(); await h.flush(); h.render();
}
async function openSection(h, key) {
  const row = nodes(h.tree).find(node => node.props?.['data-section-key'] === key);
  assert.ok(row); nodes(row).find(node => node.props?.role === 'button').props.onClick(); h.render(); await h.flush();
}
function remove(h, id) {
  const target = nodes(frame(h.tree, id)).find(node => node.type === 'button' && node.props['aria-label']?.endsWith('컷 빼기'));
  assert.ok(target); target.props.onClick(); h.render();
}
async function scene(t, { orders = [5, 1, 4], toned = true, follower = false, frontClosure = { version: 1, kind: 'zipper', range: 'graded' } } = {}) {
  const h = await storyboardScreenHarness({ withToast: true, savedStyle: { templateId: 'show' }, productPatch: product,
    analysisPatch: { clothingType: 'outer', subCategory: 'coat', targetGenders: ['men'], selectedModelId: null,
      selected_model_id: null, fitProfile: { gender: 'men' }, frontClosure } });
  t.after(() => h.close());
  const template = h.DB.storyboard.find(block => block.pageSectionKind === 'wear' && block.cutType === 'horizon' && block.spaceGroupId);
  assert.ok(template);
  const members = createSpaceSetMembers(set, { ...template, sectionCustom: true, outerClosureState: toned ? 'closed' : 'open' }, {
    spaceGroupId: groupId, members: orders.map(order => set.members.find(member => member.order === order)),
    setSelectionOrigin: 'seed', makeId: member => `r7-screen-${member.order}`,
  }).map((block, index) => {
    const next = { ...block, ...directionSpecFromExample(set.members.find(member => member.order === block.spaceSetMemberOrder)),
      slotKey: `${template.pageSectionKey.split('.').slice(1).join('.')}.r7${index + 1}`,
      horizonBackgroundMode: toned ? 'garment-tone' : 'reference', spaceVariation: set.spaceVariation };
    delete next.seedProductKey; delete next.seedFingerprint; return next;
  });
  let inserted = false;
  const arranged = follower ? [members[0], createSimilarCutBlock(members[0], 'r7-screen-follower'), ...members.slice(1)] : members;
  h.DB.storyboard = h.DB.storyboard.flatMap(block => {
    if (block.spaceGroupId !== template.spaceGroupId) return [block];
    if (inserted) return [];
    inserted = true; return arranged;
  });
  const { invalidateStoryboardEntryPrefetch, prefetchStoryboardEntry } = await h.load('/src/features/storyboard/storyboardEntryPrefetch.js');
  invalidateStoryboardEntryPrefetch(h.DB.project.id); await prefetchStoryboardEntry(h.DB.project.id);
  h.render(); await h.flush(); await openSection(h, template.pageSectionKey); await flushSave(h);
  return { h, members };
}

test('R7 A actual similar click inserts the unused set photo behind its source, saves once, advances the pale pile and undoes just that addition', async t => {
  const { h, members } = await scene(t);
  remove(h, members[2].id); await h.flush(); await flushSave(h);
  const before = structuredClone(h.DB.storyboard), source = before.find(block => block.id === members[0].id);
  const current = before.filter(block => block.spaceGroupId === groupId), candidate = nextUnusedSpaceSetMember(set, current);
  assert.equal(candidate.order, 2); assert.ok(pale(h.tree));
  const saves = [], originalSave = h.api.saveStoryboard;
  h.api.saveStoryboard = async (...args) => { saves.push(structuredClone(args[1])); return originalSave(...args); };
  await similar(h.tree, source.id).props.onClick(); h.render(); await h.flush(); await flushSave(h);
  assert.equal(saves.length, 1);
  const added = h.DB.storyboard.find(block => !before.some(previous => previous.id === block.id)); assert.ok(added);
  assert.equal(added.exampleId, candidate.exampleId); assert.equal(added.similarSourceBlockId, undefined);
  assert.equal(h.DB.storyboard.indexOf(added), h.DB.storyboard.findIndex(block => block.id === source.id) + 1);
  assert.ok(h.DB.storyboard.indexOf(added) < h.DB.storyboard.findIndex(block => block.id === members[1].id));
  for (const key of ['spaceGroupId', 'spaceVariation', 'horizonBackgroundMode', 'outerClosureState', 'setSelectionOrigin']) {
    assert.equal(added[key], source[key], key);
  }
  assert.ok(!textOf(frame(h.tree, added.id)).includes('번째 컷과 비슷하게'));
  assert.ok(textOf(h.tree).includes('세트의 같은 구도 사진을 넣었어요'));
  assert.match(photo(h.tree, added.id).props['aria-label'], /세트 사진$/);
  assert.ok(!nodes(h.tree).some(node => node.props?.role === 'dialog'), 'placed-set insertion does not move to a photo panel');
  assert.ok(pale(h.tree));
  const next = nextUnusedSpaceSetMember(set, h.DB.storyboard.filter(block => block.spaceGroupId === groupId));
  assert.equal(next.order, 3);
  assert.ok(nodes(pale(h.tree)).some(node => node.type === 'img' && node.props.src === (next.thumb || next.thumbUrl || next.allUrl)));
  button(h.tree, '되돌리기').props.onClick(); h.render(); await h.flush(); await flushSave(h);
  assert.deepEqual(h.DB.storyboard, before, 'the earlier deletion stays in place after one undo');
  assert.ok(!h.DB.storyboard.some(block => block.id === members[2].id));
});

test('R7 A actual insertion stays after existing source followers and before the next original set photo', async t => {
  const { h, members } = await scene(t, { orders: [5, 1], follower: true });
  const before = structuredClone(h.DB.storyboard);
  await similar(h.tree, members[0].id).props.onClick(); h.render(); await h.flush(); await flushSave(h);
  const added = h.DB.storyboard.find(block => !before.some(previous => previous.id === block.id)); assert.ok(added);
  assert.deepEqual(h.DB.storyboard.filter(block => block.spaceGroupId === groupId).map(block => block.id),
    [members[0].id, 'r7-screen-follower', added.id, members[1].id]);
  assert.equal(added.similarSourceBlockId, undefined);
});

for (const [label, orders] of [['no matching shot', [1, 2]], ['one original', [5]]]) {
  test(`R7 A actual similar click falls back for ${label}, preserving the linked-cut path without a set-photo toast`, async t => {
    const { h, members } = await scene(t, { orders });
    const before = structuredClone(h.DB.storyboard), source = members[0];
    await similar(h.tree, source.id).props.onClick(); h.render(); await h.flush(); await flushSave(h);
    const added = h.DB.storyboard.find(block => !before.some(previous => previous.id === block.id)); assert.ok(added);
    assert.equal(added.similarSourceBlockId, source.id);
    assert.equal(added.exampleId, source.exampleId);
    assert.equal(h.DB.storyboard.indexOf(added), h.DB.storyboard.findIndex(block => block.id === source.id) + 1);
    assert.ok(textOf(frame(h.tree, added.id)).includes('번째 컷과 비슷하게'));
    assert.ok(!textOf(h.tree).includes('세트의 같은 구도 사진을 넣었어요'));
  });
}

test('R7 A failed atomic set-photo insertion rolls back and uses the pale-slot save failure notice', async t => {
  const { h, members } = await scene(t, { orders: [5, 1] });
  const before = structuredClone(h.DB.storyboard), originalSave = h.api.saveStoryboard;
  h.api.saveStoryboard = async () => { throw new Error('round7 save failure'); };
  await similar(h.tree, members[0].id).props.onClick(); h.render(); await h.flush();
  assert.deepEqual(h.DB.storyboard, before);
  const photoIds = nodes(h.tree).filter(node => node.props?.['data-storyboard-photo']).map(node => node.props['data-storyboard-photo']);
  assert.ok(photoIds.every(id => before.some(block => block.id === id)));
  assert.ok(textOf(h.tree).includes('컷을 저장하지 못했어요. 다시 시도해주세요.'));
  assert.ok(!textOf(h.tree).includes('세트의 같은 구도 사진을 넣었어요'));
  h.api.saveStoryboard = originalSave;
});

for (const [kind, range] of [['zipper', 'graded'], ['button', 'binary'], ['belt', 'binary']]) {
  test(`R7 D deleting an enabled set to a singleton opens the actual ${kind} outer control at 전체 닫힘`, async t => {
    const { h, members } = await scene(t, { orders: [1, 2], toned: false, frontClosure: { version: 1, kind, range } });
    nodes(h.tree).find(node => node.props?.['data-set-change'] === groupId).props.onClick(); h.render();
    await button(h.tree, '배경색 맞추기').props.onClick(); h.render(); await h.flush();
    await button(h.tree, '아우터 잠그기').props.onClick(); h.render(); await h.flush();
    nodes(h.tree).find(node => node.props?.['aria-label'] === '옆 판 닫기').props.onClick(); h.render();
    remove(h, members[1].id); await h.flush(); await flushSave(h);
    const remaining = h.DB.storyboard.filter(block => block.spaceGroupId === groupId);
    assert.equal(remaining.length, 1); assert.equal(remaining[0].outerClosureState, 'closed');
    assert.equal(remaining[0].horizonBackgroundMode, 'garment-tone'); assert.equal(remaining[0].spaceGroupId, groupId);
    assert.ok(!nodes(h.tree).some(node => node.props?.['data-set-change'] === groupId));
    assert.ok(nodes(h.tree).some(node => node.props?.['aria-label'] === '배경색 맞춤' && node.props.tabIndex === 0));
    const target = photo(h.tree, remaining[0].id); assert.match(target.props['aria-label'], /컷 사진 바꾸기$/);
    target.props.onClick(); h.render(); await h.flush();
    assert.ok(nodes(h.tree).some(node => node.props?.role === 'dialog'));
    const slider = nodes(h.tree).find(node => node.props?.role === 'slider'); assert.ok(slider);
    assert.equal(slider.props['aria-valuetext'], '전체 닫힘');
    assert.ok(textOf(h.tree).includes('아우터 열림 정도'));
  });
}
