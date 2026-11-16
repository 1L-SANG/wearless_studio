import test from 'node:test';
import assert from 'node:assert/strict';
import raw from '../../src/data/genExamples.json' with { type: 'json' };
import { setPhotoGroupId, setOptionState } from '../../src/lib/storyboardSetOptions.js';
import { createSpaceSetMembers } from '../../src/lib/storyboardSpaceSets.js';
import { createSimilarCutBlock } from '../../src/lib/storyboardSimilarCuts.js';
import { STORYBOARD_SPACE_SETS, spaceSetGroupId, storyboardSpaceSetsFor, withStoryboardSpaceSetExamples } from '../../src/lib/storyboardSpaceSetCatalog.js';
import * as generation from '../../src/lib/generationExamples.js';
import * as identity from '../../src/lib/identityScope.js';
import { nodes, textOf, hookHarness, loadStoryboardComponent } from './helpers/storyboardComponents.mjs';
import { storyboardPhotoPanelHarness } from './helpers/storyboardPhotoPanelHarness.mjs';
import { storyboardStage, stagePhoto } from './helpers/storyboardStageHarness.mjs';
import { storyboardScreenHarness } from './helpers/storyboardScreenHarness.mjs';

const set = STORYBOARD_SPACE_SETS.find(s => s.id === 'horizon-set-03-men-tonal-outer');
const groupId = spaceSetGroupId(set.id, 'r5fix');
const product = { clothingType: 'outer', colors: [{ id: 'sky', name: '스카이블루', swatchId: 'blue', isBase: true,
  images: [{ slot: 'Front', src: '/front.webp' }, { slot: 'Back', src: '/back.webp' }] }, { id: 'gray', name: '그레이', swatchId: 'gray', images: [{ slot: 'Front', src: '/gray.webp' }] }] };
const colorOpts = [{ id: 'sky', label: '스카이블루', hex: '#98BDE7' }];
const frontClosure = { version: 1, kind: 'zipper', range: 'graded' };
const members = createSpaceSetMembers(set, stagePhoto('host', { sectionId: 'wear', sectionRole: 'studio', contentRole: 'fit',
  colorId: 'sky', outerClosureState: 'closed' }), { spaceGroupId: groupId });
const catalogs = { ...withStoryboardSpaceSetExamples({ genExamples: raw }), detailProduct: product,
  shotTypes: [{ value: 'full' }, { value: 'medium' }], productShotTypes: [{ value: 'ghost' }, { value: 'detail' }] };
const buttonText = (tree, label) => nodes(tree).find(n => n.type === 'button' && textOf(n) === label);
const dialog = tree => nodes(tree).find(n => n.props?.role === 'dialog');
const tray = tree => nodes(tree).find(n => n.props?.['data-photo-tray']);
const photo = (tree, id) => nodes(tree).find(n => n.props?.['data-storyboard-photo'] === id);
const selected = h => h.DB.storyboard.find(b => b.slotKey === 'first.hero');

async function flushSave(h) {
  window.dispatchEvent(new Event('pagehide'));
  const { sbSaveIdle } = await h.load('/src/features/storyboard/storyboardPersistence.js');
  await sbSaveIdle(); await h.flush(); h.render();
}
async function openSection(h, key) {
  const row = nodes(h.tree).find(n => n.props?.['data-section-key'] === key);
  assert.ok(row); nodes(row).find(n => n.props?.role === 'button').props.onClick(); h.render(); await h.flush();
}
function findCard(h, predicate) {
  const previous = () => nodes(dialog(h.tree)).find(n => n.props?.['aria-label'] === '이전 예시 페이지');
  for (let i = 0; i < 20 && previous() && !previous().props.disabled; i++) { previous().props.onClick(); h.render(); }
  for (let i = 0; i < 20; i++) {
    const card = nodes(dialog(h.tree)).find(n => n.props?.['data-photo-choice'] && predicate(n));
    if (card) return card;
    const next = nodes(dialog(h.tree)).find(n => n.props?.['aria-label'] === '다음 예시 페이지');
    if (!next || next.props.disabled) break;
    next.props.onClick(); h.render();
  }
  assert.fail('expected released gallery card');
}
const scene = () => storyboardScreenHarness({ savedStyle: { templateId: 'show' },
  productPatch: product, analysisPatch: { clothingType: 'outer', subCategory: 'coat', targetGenders: ['men'], selectedModelId: null, selected_model_id: null, fitProfile: { gender: 'men' }, frontClosure } });

// These values are runtime UI rules. Saved one-member location contracts stay intact.
test('R5 followup locks two AI originals, excludes similar cuts and changes immediately at one original', () => {
  const [root, second] = members, child = createSimilarCutBlock(root, 'child');
  for (const block of [root, child]) assert.equal(setPhotoGroupId(block, [root, child]), null);
  for (const block of [root, second, child]) assert.equal(setPhotoGroupId(block, [root, second, child]), groupId);
  assert.equal(setPhotoGroupId(root, [root, { ...second, source: 'mine' }, child]), null);
  assert.equal(setPhotoGroupId(root, [root]), null);
  assert.equal(setPhotoGroupId(root, [root, second]), groupId, 'undo restores the placed-set rule');
});

test('R5 followup tone drawings resolve the worn color ID through colorOpts, with gray only when missing', () => {
  assert.ok(product.colors.every(c => !Object.hasOwn(c, 'hex')));
  const state = setOptionState(members, product, frontClosure, colorOpts);
  assert.equal(state.color?.hex, colorOpts[0].hex);
  assert.equal(setOptionState(members, product, frontClosure, []).color?.hex, undefined);
  assert.equal(setOptionState(members, product, frontClosure, [{ ...colorOpts[0], id: 'other' }]).color?.hex, undefined);
  assert.equal(setOptionState(members.map(member => ({ ...member, colorId: null })), product, frontClosure, colorOpts).color?.hex, undefined);
});

test('R5 followup singleton has the ordinary photo panel, matching controls and an initially expanded set gallery', async () => {
  const block = { ...members[0], pageSectionUsage: 'styling', sectionRole: 'styling', contentRole: 'coordination',
    pageDesignId: 'P02-D01', slotKey: 'wear_styling.g1' };
  const panel = await storyboardPhotoPanelHarness({ block, blocks: [block], catalogs, clothingType: 'outer', exampleGender: 'men',
    colorOpts, detailColorOpts: colorOpts, matchClothing: [{ id: 'shirt', name: '셔츠' }] });
  const h = hookHarness();
  try {
    let tree = panel.render(); assert.ok(tree);
    assert.ok(nodes(tree).some(n => n.type === 'underline-tabs'));
    assert.ok(nodes(tree).some(n => n.type === 'color-dots'));
    assert.ok(nodes(tree).some(n => n.type === 'closure-field'));
    assert.ok(textOf(tree).includes('매칭 의류 바꾸기'));
    const guide = nodes(tree).find(n => n.type === 'mood-guide'); assert.ok(guide.props.onUseMine); assert.equal(guide.props.spaceContext, undefined);
    const Gallery = await loadStoryboardComponent('StoryboardPhotoGallery.jsx', 'StoryboardPhotoGallery', {
      ...h.hooks, ...generation, ...identity, storyboardSpaceSetsFor, Icon: 'icon' });
    const gallery = h.render(Gallery, guide.props); assert.equal(tray(gallery)?.props['data-photo-tray'], set.id);
    const other = set.members.find(m => m.exampleId !== block.exampleId && m.shot === block.shot);
    await guide.props.onExampleChange(other.exampleId, 'all');
    assert.equal(panel.block.spaceGroupId, groupId); assert.equal(panel.block.exampleId, other.exampleId);
    tree = panel.render({ blocks: [panel.block] }); assert.ok(tree);
    const next = nodes(tree).find(n => n.type === 'mood-guide');
    const standalone = raw.find(e => e.cutType === 'horizon' && e.gender === 'men' && e.shot === 'medium' && e.applicableClothingTypes.includes('outer'));
    await next.props.onExampleChange(standalone.id, 'all'); assert.equal(panel.block.spaceGroupId, null);
    assert.equal(panel.block.exampleId, standalone.id);
  } finally { panel.close(); h.close(); }
});

test('R5 followup singleton and its similar cut have no stage set heading, nudge or marks', async () => {
  const root = { ...members[0], outerClosureState: 'closed', horizonBackgroundMode: 'garment-tone' };
  const child = createSimilarCutBlock(root, 'similar'), calls = [];
  let tree = await storyboardStage([root, child], { onSelectPhoto: id => calls.push(id),
    onNudgeSet() { assert.fail('singleton must not nudge a set'); } });
  assert.ok(!nodes(tree).some(n => n.props?.['data-set-change']));
  assert.ok(!nodes(tree).some(n => n.type === 'set-head-marks'));
  assert.equal(photo(tree, root.id).props['aria-label'], '1번째 컷 사진 바꾸기');
  photo(tree, root.id).props.onClick(); assert.deepEqual(calls, [root.id]);
  tree = await storyboardStage([root, members[1], child]); assert.ok(nodes(tree).some(n => n.props?.['data-set-change']));
});

test('R5 followup set options and card tone marks require two originals, not one plus similar cuts', async () => {
  const root = { ...members[0], horizonBackgroundMode: 'garment-tone' }, child = createSimilarCutBlock(root, 'similar');
  const h = hookHarness();
  const Picker = await loadStoryboardComponent('StoryboardSpaceSetPanel.jsx', 'StoryboardSpaceSetPanel', {
    ...h.hooks, ...identity, storyboardSpaceSetsFor: () => [set], Icon: 'icon' });
  const props = { mode: 'replace', cutType: 'horizon', gender: 'men', clothingType: 'outer', product, frontClosure, colorOpts };
  try {
    let tree = h.render(Picker, { ...props, currentMembers: [root, child] });
    assert.equal(buttonText(tree, '아우터 잠그기'), undefined); assert.equal(buttonText(tree, '배경색 맞추기'), undefined);
    assert.ok(!nodes(tree).some(n => n.type === 'tone-icon'));
    tree = h.render(Picker, { ...props, currentMembers: [root, members[1], child] });
    assert.ok(buttonText(tree, '아우터 잠그기')); assert.ok(buttonText(tree, '배경색 맞추기'));
    assert.ok(nodes(tree).filter(n => n.type === 'tone-icon').every(n => n.props.color?.hex === colorOpts[0].hex));
  } finally { h.close(); }
});

test('R5 followup first hero stays editable after a released set photo is saved through the mock adapter', async t => {
  const h = await scene(); t.after(() => h.close()); h.render(); await h.flush();
  const initial = selected(h), count = h.DB.storyboard.filter(b => b.pageSectionKind === 'first' && b.source === 'ai').length;
  photo(h.tree, initial.id).props.onClick(); h.render();
  const studio = buttonText(dialog(h.tree), '스튜디오컷'); if (studio) { studio.props.onClick(); h.render(); }
  buttonText(dialog(h.tree), '호리존컷').props.onClick(); h.render();
  findCard(h, n => n.props['data-photo-choice'] === set.id).props.onClick(); h.render();
  const chosen = nodes(tray(h.tree)).find(n => n.props?.['data-example-id'] === 'ss_horizon_set_03_men_tonal_outer_03');
  assert.ok(chosen); await chosen.props.onClick(); h.render(); await h.flush(); await flushSave(h);
  assert.equal(selected(h).exampleId, chosen.props['data-example-id']); assert.ok(selected(h).spaceGroupId);
  assert.equal(h.DB.storyboard.filter(b => b.pageSectionKind === 'first' && b.source === 'ai').length, count);
  assert.ok(dialog(h.tree), 'choosing one photo must keep the photo panel open');
  assert.ok(!nodes(h.tree).some(n => n.props?.['data-set-change']));
  assert.ok(buttonText(dialog(h.tree), '내 사진으로 바꾸기'));
  nodes(dialog(h.tree)).find(n => n.props?.['aria-label'] === '옆 판 닫기').props.onClick(); h.render();
  photo(h.tree, initial.id).props.onClick(); h.render(); assert.ok(dialog(h.tree));
  assert.equal(tray(h.tree)?.props['data-photo-tray'], set.id);
  const beforeGroup = selected(h).spaceGroupId;
  const other = nodes(tray(h.tree)).find(n => n.type === 'button' && n.props['data-example-id'] !== selected(h).exampleId);
  assert.ok(other); await other.props.onClick(); h.render(); await h.flush(); await flushSave(h);
  assert.equal(selected(h).exampleId, other.props['data-example-id']); assert.equal(selected(h).spaceGroupId, beforeGroup);
  assert.ok(dialog(h.tree));
});

test('R5 followup removing placed-set originals down to one closes set editing and reopens as an ordinary photo; undo locks again', async t => {
  const h = await scene(); t.after(() => h.close()); h.render(); await h.flush();
  const root = h.DB.storyboard.find(b => b.pageSectionKind === 'wear' && b.pageSectionUsage === 'fit' && b.spaceGroupId);
  const group = h.DB.storyboard.filter(b => b.spaceGroupId === root.spaceGroupId && !b.similarSourceBlockId);
  assert.ok(group.length >= 2); await openSection(h, root.pageSectionKey);
  photo(h.tree, root.id).props.onClick(); h.render(); assert.ok(!dialog(h.tree));
  nodes(h.tree).find(n => n.props?.['data-set-change'] === root.spaceGroupId).props.onClick(); h.render();
  assert.ok(dialog(h.tree));
  for (const member of group.slice(1)) {
    const frame = nodes(h.tree).find(n => n.props?.className?.includes('photoFrame') && nodes(n).some(c => c.props?.['data-storyboard-photo'] === member.id));
    assert.ok(frame); nodes(frame).find(n => n.type === 'button' && n.props['aria-label']?.endsWith('컷 빼기')).props.onClick(); h.render(); await h.flush();
  }
  assert.ok(!dialog(h.tree)); assert.ok(!nodes(h.tree).some(n => n.props?.['data-set-change'] === root.spaceGroupId));
  photo(h.tree, root.id).props.onClick(); h.render(); assert.ok(dialog(h.tree));
  nodes(dialog(h.tree)).find(n => n.props?.['aria-label'] === '옆 판 닫기').props.onClick(); h.render();
  const undo = buttonText(h.tree, '되돌리기'); assert.ok(undo); undo.props.onClick(); h.render(); await h.flush();
  assert.ok(nodes(h.tree).some(n => n.props?.['data-set-change'] === root.spaceGroupId));
  photo(h.tree, root.id).props.onClick(); h.render(); assert.ok(!dialog(h.tree));
});

test('R5 followup chip, selected set card and stage tone dots use the same hex as the garment caption', async t => {
  const h = await scene(); t.after(() => h.close()); h.render(); await h.flush();
  assert.ok(h.DB.product.colors.every(c => c.hex === undefined));
  const root = h.DB.storyboard.find(b => b.pageSectionKind === 'wear' && b.pageSectionUsage === 'fit' && b.spaceGroupId);
  await openSection(h, root.pageSectionKey);
  const caption = nodes(h.tree).find(n => n.type === 'svg' && n.props.className?.includes('captionColor'));
  const hex = nodes(caption).find(n => n.type === 'circle').props.fill; assert.ok(hex.startsWith('#'));
  nodes(h.tree).find(n => n.props?.['data-set-change'] === root.spaceGroupId).props.onClick(); h.render();
  let chip = buttonText(dialog(h.tree), '배경색 맞추기'); assert.ok(chip);
  assert.equal(nodes(chip).find(n => n.type === 'circle').props.fill, hex);
  await chip.props.onClick(); h.render(); await h.flush();
  const selectedCard = nodes(dialog(h.tree)).find(n => n.props?.['data-space-set'] && n.props['aria-pressed']);
  const dots = nodes(selectedCard).find(n => n.type === 'svg' && n.props.className?.includes('toneDots'));
  assert.equal(nodes(dots).find(n => n.type === 'circle').props.fill, hex);
  const marks = nodes(h.tree).find(n => n.props?.['aria-label'] === '배경색 맞춤'); assert.ok(marks);
  assert.equal(nodes(marks).find(n => n.type === 'circle').props.fill, hex);
});
