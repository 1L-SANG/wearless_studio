import test from 'node:test';
import assert from 'node:assert/strict';
import policy from '../../server/app/data/horizon_background_policy.json' with { type: 'json' };
import { nodes, textOf } from './helpers/storyboardComponents.mjs';
import { storyboardScreenHarness } from './helpers/storyboardScreenHarness.mjs';
import { STORYBOARD_SPACE_SETS, spaceSetGroupId } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { createSpaceSetMembers } from '../../src/lib/storyboardSpaceSets.js';

const minimal = STORYBOARD_SPACE_SETS.find(set => set.id === 'set-02-men-top-indoor-concrete-byslim-28635');
const horizon = STORYBOARD_SPACE_SETS.find(set => set.id === 'horizon-sequence-figma-s14-v2');
const product = clothingType => ({ clothingType, colors: [{ id: 'blue', name: '스카이블루', swatchId: 'blue', isBase: true,
  images: [{ slot: 'Front', src: '/front.webp' }, { slot: 'Back', src: '/back.webp' }] }] });
const button = (tree, label) => nodes(tree).find(node => node.type === 'button' && textOf(node) === label);
const photo = (tree, id) => nodes(tree).find(node => node.props?.['data-storyboard-photo'] === id);

async function flushSave(h) {
  window.dispatchEvent(new Event('pagehide'));
  const { sbSaveIdle } = await h.load('/src/features/storyboard/storyboardPersistence.js');
  await sbSaveIdle(); await h.flush(); h.render();
}

async function scene(t, { legacy = false, legacySection = 'wear_styling', singleton = false, unavailable = false } = {}) {
  const set = legacy ? minimal : horizon, clothingType = legacy ? 'top' : 'outer';
  const h = await storyboardScreenHarness({ savedStyle: { templateId: legacy ? 'emo' : 'show' }, productPatch: product(clothingType),
    analysisPatch: { clothingType, subCategory: legacy ? 'knit' : 'coat', targetGenders: ['men'], selectedModelId: null,
      selected_model_id: null, fitProfile: { gender: 'men' }, garmentColorEvidence: unavailable ? {
        version: 1, clothingType, colors: [{ colorId: 'blue', status: 'ready', backgroundStatus: 'unavailable', backgroundPolicyVersion: policy.version }],
      } : null } });
  t.after(() => h.close());
  const source = h.DB.storyboard.find(block => block.pageSectionKey === `${legacy ? 'emo' : 'show'}.${legacy ? legacySection : 'wear_styling'}`
    && block.source === 'ai');
  assert.ok(source);
  const groupId = spaceSetGroupId(set.id, 'round8-screen');
  const members = createSpaceSetMembers(set, { ...source, sectionCustom: true }, { spaceGroupId: groupId,
    members: set.members.slice(0, singleton ? 1 : 2), setSelectionOrigin: 'auto', makeId: (_member, index) => `r8-source${index}` })
    .map((block, index) => {
      const next = { ...block, slotKey: legacy && legacySection !== 'wear_styling' ? `${legacySection}.background` : `wear_styling.g${index + 1}`,
        horizonBackgroundMode: legacy ? null : 'garment-tone' };
      delete next.seedProductKey; delete next.seedFingerprint; return next;
    });
  let inserted = false;
  h.DB.storyboard = h.DB.storyboard.flatMap(block => {
    if (!(source.spaceGroupId ? block.spaceGroupId === source.spaceGroupId : block.id === source.id)) return [block];
    if (inserted) return [];
    inserted = true; return members;
  });
  const { invalidateStoryboardEntryPrefetch, prefetchStoryboardEntry } = await h.load('/src/features/storyboard/storyboardEntryPrefetch.js');
  invalidateStoryboardEntryPrefetch(h.DB.project.id); await prefetchStoryboardEntry(h.DB.project.id);
  h.render(); await h.flush();
  const row = nodes(h.tree).find(node => node.props?.['data-section-key'] === source.pageSectionKey); assert.ok(row);
  nodes(row).find(node => node.props?.role === 'button').props.onClick(); h.render(); await h.flush();
  return { h, members, groupId };
}

for (const [legacySection, singleton] of [['wear_styling', false], ['wear_styling', true], ['review', true], ['faq', true]]) {
test(`R8-1 legacy ${legacySection} minimal ${singleton ? 'singleton' : 'set'} opens, keeps its photo and saves through the real frontend path`, async t => {
  const { h, members, groupId } = await scene(t, { legacy: true, legacySection, singleton });
  const before = members.map(block => ({ id: block.id, exampleId: block.exampleId, cutType: block.cutType, spaceGroupId: block.spaceGroupId }));
  assert.ok(photo(h.tree, members[0].id));
  const stage = nodes(h.tree).find(node => node.type === 'section' && node.props['aria-label']?.endsWith('무대'));
  const stageName = stage.props['aria-label'];
  let activeStudio = null, largeTabs = [];
  if (singleton) {
    photo(h.tree, members[0].id).props.onClick(); h.render(); await h.flush();
    const dialog = nodes(h.tree).find(node => node.props?.role === 'dialog'); assert.ok(dialog);
    const studio = nodes(dialog).find(node => node.props?.['aria-label'] === '스튜디오컷 종류');
    activeStudio = studio ? nodes(studio).filter(node => node.type === 'button' && node.props['aria-pressed']).map(textOf) : null;
    largeTabs = nodes(dialog).filter(node => node.props?.className?.includes('cutTabs')).map(textOf);
    assert.ok(!textOf(dialog).includes('저장된 예시를 다시 골라주세요'));
    assert.ok(!textOf(dialog).includes('변경 내용을 저장하지 못했어요'));
    nodes(h.tree).find(node => node.props?.['aria-label'] === '옆 판 닫기').props.onClick(); h.render();
  } else {
    assert.match(photo(h.tree, members[0].id).props['aria-label'], /세트 사진$/);
    nodes(h.tree).find(node => node.props?.['data-set-change'] === groupId).props.onClick(); h.render(); await h.flush();
    const studio = nodes(h.tree).find(node => node.props?.['aria-label'] === '스튜디오컷 종류');
    activeStudio = studio ? nodes(studio).filter(node => node.type === 'button' && node.props['aria-pressed']).map(textOf) : null;
    nodes(h.tree).find(node => node.props?.['aria-label'] === '옆 판 닫기').props.onClick(); h.render();
  }
  t.diagnostic(JSON.stringify({ legacySection, singleton, stageName, activeStudio, largeTabs }));
  let saves = 0; const originalSave = h.api.saveStoryboard;
  h.api.saveStoryboard = async (...args) => { saves++; return originalSave(...args); };
  const frame = nodes(h.tree).find(node => node.props?.className?.includes('photoFrame') && photo(node, members[0].id));
  await button(frame, '비슷한 컷 추가').props.onClick(); h.render(); await h.flush(); await flushSave(h);
  assert.ok(saves >= 1);
  for (const old of before) {
    const saved = h.DB.storyboard.find(block => block.id === old.id); assert.ok(saved);
    assert.deepEqual({ id: saved.id, exampleId: saved.exampleId, cutType: saved.cutType, spaceGroupId: saved.spaceGroupId }, old);
    assert.ok(photo(h.tree, old.id));
  }
});
}

for (const singleton of [false, true]) test(`R8-2 returned unavailable evidence hides ${singleton ? 'caption' : 'head and card'} tone indicators without resetting saved blocks`, async t => {
  const { h, members, groupId } = await scene(t, { singleton, unavailable: true });
  assert.ok(!nodes(h.tree).some(node => node.type === 'svg' && node.props.className?.includes('toneDots')));
  if (!singleton) {
    nodes(h.tree).find(node => node.props?.['data-set-change'] === groupId).props.onClick(); h.render(); await h.flush();
    const chip = button(h.tree, '배경색 맞추기'); assert.equal(chip.props['aria-disabled'], true); assert.equal(chip.props['aria-pressed'], false);
    assert.ok(!nodes(h.tree).some(node => node.type === 'svg' && node.props.className?.includes('toneDots')));
  }
  await flushSave(h);
  for (const member of members) assert.equal(h.DB.storyboard.find(block => block.id === member.id).horizonBackgroundMode, 'garment-tone');
});
