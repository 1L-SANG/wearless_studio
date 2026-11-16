import test from 'node:test';
import assert from 'node:assert/strict';
import { nodes, textOf } from './helpers/storyboardComponents.mjs';
import { storyboardScreenHarness } from './helpers/storyboardScreenHarness.mjs';
import { inferStoryboardSpaceSet } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { nextUnusedSpaceSetMember } from '../../src/lib/storyboardSpaceSets.js';

const button = (tree, label) => nodes(tree).find(node => node.type === 'button' && textOf(node) === label);
async function flushSave(h) {
  window.dispatchEvent(new Event('pagehide'));
  const { sbSaveIdle } = await h.load('/src/features/storyboard/storyboardPersistence.js');
  await sbSaveIdle(); await h.flush(); h.render();
}

test('R6 actual pale-slot click saves one complete toned set change, locks a singleton and undoes in one step', async t => {
  const h = await storyboardScreenHarness({ savedStyle: { templateId: 'show' }, productPatch: {
    clothingType: 'outer', colors: [{ id: 'sky', name: '스카이블루', swatchId: 'blue', isBase: true,
      images: [{ slot: 'Front', src: '/front.webp' }, { slot: 'Back', src: '/back.webp' }] }],
  }, analysisPatch: { clothingType: 'outer', subCategory: 'coat', targetGenders: ['men'], selectedModelId: null,
    selected_model_id: null, fitProfile: { gender: 'men' }, frontClosure: { version: 1, kind: 'zipper', range: 'graded' } } });
  t.after(() => h.close()); h.render(); await h.flush();
  const root = h.DB.storyboard.find(b => b.pageSectionKind === 'wear' && b.cutType === 'horizon' && b.spaceGroupId);
  assert.ok(root);
  const originals = h.DB.storyboard.filter(b => b.spaceGroupId === root.spaceGroupId && !b.similarSourceBlockId);
  const row = nodes(h.tree).find(n => n.props?.['data-section-key'] === root.pageSectionKey);
  nodes(row).find(n => n.props?.role === 'button').props.onClick(); h.render(); await h.flush();
  nodes(h.tree).find(n => n.props?.['data-set-change'] === root.spaceGroupId).props.onClick(); h.render();
  await button(h.tree, '배경색 맞추기').props.onClick(); h.render(); await h.flush();
  await button(h.tree, '아우터 잠그기').props.onClick(); h.render(); await h.flush();
  nodes(h.tree).find(n => n.props?.['aria-label'] === '옆 판 닫기').props.onClick(); h.render();
  for (const member of originals.slice(1)) {
    const photo = nodes(h.tree).find(n => n.props?.className?.includes('photoFrame')
      && nodes(n).some(child => child.props?.['data-storyboard-photo'] === member.id));
    nodes(photo).find(n => n.type === 'button' && n.props['aria-label']?.endsWith('컷 빼기')).props.onClick(); h.render(); await h.flush();
  }
  await flushSave(h);
  const before = structuredClone(h.DB.storyboard), host = before.find(b => b.id === root.id);
  assert.equal(host.horizonBackgroundMode, 'garment-tone'); assert.equal(host.outerClosureState, 'closed');
  assert.ok(!nodes(h.tree).some(n => n.props?.['data-set-change'] === root.spaceGroupId));
  const candidate = nextUnusedSpaceSetMember(inferStoryboardSpaceSet(root.spaceGroupId), [host]);
  const slot = nodes(h.tree).find(n => n.props?.['aria-label'] === '1컷 더 넣기'); assert.ok(slot);
  const saves = [], originalSave = h.api.saveStoryboard;
  h.api.saveStoryboard = async (...args) => { saves.push(structuredClone(args[1])); return originalSave(...args); };
  await slot.props.onClick(); h.render(); await h.flush(); await flushSave(h);
  assert.equal(saves.length, 1, 'one atomic board save includes the member and normalized group background');
  const added = h.DB.storyboard.find(b => !before.some(old => old.id === b.id)); assert.ok(added);
  assert.equal(added.exampleId, candidate.exampleId);
  assert.equal(h.DB.storyboard.indexOf(added), h.DB.storyboard.findIndex(b => b.id === root.id) + 1);
  for (const key of ['spaceGroupId', 'spaceVariation', 'horizonBackgroundMode', 'outerClosureState', 'setSelectionOrigin']) assert.equal(added[key], host[key], key);
  assert.ok(nodes(h.tree).some(n => n.props?.['data-set-change'] === host.spaceGroupId));
  const click = nodes(h.tree).find(n => n.props?.['data-storyboard-photo'] === added.id);
  assert.match(click.props['aria-label'], /^\d+번째 컷, 세트 사진$/);
  click.props.onClick(); h.render(); assert.ok(!nodes(h.tree).some(n => n.props?.role === 'dialog'));
  button(h.tree, '되돌리기').props.onClick(); h.render(); await h.flush(); await flushSave(h);
  assert.deepEqual(h.DB.storyboard, before);
  assert.ok(!nodes(h.tree).some(n => n.props?.['data-set-change'] === host.spaceGroupId));
  assert.ok(nodes(h.tree).some(n => n.props?.['aria-label'] === '1컷 더 넣기'));
});
