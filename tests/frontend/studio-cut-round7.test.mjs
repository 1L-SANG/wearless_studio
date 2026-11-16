import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import raw from '../../src/data/genExamples.json' with { type: 'json' };
import { STORYBOARD_SPACE_SETS, spaceSetGroupId, withStoryboardSpaceSetExamples } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { createSpaceSetMembers, nextUnusedSpaceSetMember, unusedSpaceSetMembers, nextUnusedSimilarSpaceSetMember } from '../../src/lib/storyboardSpaceSets.js';
import { similarSpaceSetMember, insertSpaceSetMember, stageAdditions } from '../../src/lib/pageTemplates/stageAdditions.js';
import { createSimilarCutBlock } from '../../src/lib/storyboardSimilarCuts.js';
import { setPhotoGroupId, updateSetOuterClosure } from '../../src/lib/storyboardSetOptions.js';
import { updateHorizonGroupBackground } from '../../src/lib/horizonBackground.js';
import { removeStoryboardPhoto } from '../../src/lib/pageTemplates/photoActions.js';
import { directionSpecFromExample } from '../../src/lib/storyboardExampleSelection.js';
import * as generation from '../../src/lib/generationExamples.js';
import * as identity from '../../src/lib/identityScope.js';
import { hookHarness, loadStoryboardComponent, nodes, textOf } from './helpers/storyboardComponents.mjs';
import { storyboardPhotoPanelHarness } from './helpers/storyboardPhotoPanelHarness.mjs';
import { storyboardStage } from './helpers/storyboardStageHarness.mjs';

const set = STORYBOARD_SPACE_SETS.find(set => set.id === 'horizon-sequence-figma-s14-v2');
const product = { clothingType: 'outer', colors: [{ id: 'sky', name: '스카이블루', swatchId: 'blue', isBase: true,
  images: [{ slot: 'Front', src: '/front.webp' }, { slot: 'Back', src: '/back.webp' }] }] };
const colorOpts = [{ id: 'sky', label: '스카이블루', hex: '#98BDE7' }];
const catalogs = { ...withStoryboardSpaceSetExamples({ genExamples: raw }), detailProduct: product,
  shotTypes: [{ value: 'full' }, { value: 'medium' }], productShotTypes: [{ value: 'ghost' }, { value: 'detail' }] };
const byClass = (tree, name) => nodes(tree).find(node => node.props?.className === name);
const button = (tree, label) => nodes(tree).find(node => node.type === 'button' && textOf(node) === label);
const group = (orders = [5, 1], chosenSet = set) => createSpaceSetMembers(chosenSet, {
  source: 'ai', taxonomyVersion: 4, sectionRole: 'studio', contentRole: 'fit', sectionId: 'wear',
  pageSectionKey: 'emo.wear_fit', pageSectionKind: 'wear', pageSectionUsage: 'fit', pageDesignId: 'P02-D01',
  colorId: 'sky', outerClosureState: 'closed', sectionCustom: true,
}, { spaceGroupId: spaceSetGroupId(chosenSet.id, 'r7'), members: orders.map(order => chosenSet.members.find(member => member.order === order)),
  setSelectionOrigin: 'seed', makeId: member => `r7-${member.order}` })
  .map((block, index) => ({ ...block, ...directionSpecFromExample(chosenSet.members.find(member => member.order === block.spaceSetMemberOrder)),
    horizonBackgroundMode: block.cutType === 'horizon' ? 'garment-tone' : null, spaceVariation: 'expressive', slotKey: `wear_fit.front${index + 1}` }));

test('R7 A same-framing insertion follows the source family, inherits the pale-slot recipe and never creates a similar link', () => {
  const [source, other] = group(), follower = createSimilarCutBlock(source, 'follower');
  const blocks = [source, follower, other], before = structuredClone(blocks);
  const item = similarSpaceSetMember(blocks, source);
  assert.equal(item.member.order, 2, 'the earliest unused matching member wins before order 6');
  const next = insertSpaceSetMember(blocks, source.pageSectionKey, item, { product, makeId: () => 'added' }, 2);
  assert.deepEqual(blocks, before);
  assert.deepEqual(next.map(block => block.id), [source.id, follower.id, 'added', other.id]);
  const added = next[2];
  for (const key of ['spaceGroupId', 'spaceVariation', 'horizonBackgroundMode', 'outerClosureState', 'setSelectionOrigin']) {
    assert.equal(added[key], source[key], key);
  }
  assert.equal(added.slotKey, 'wear_fit.front3');
  assert.equal(added.exampleId, item.member.exampleId);
  assert.equal(added.spaceSetMemberOrder, item.member.order);
  assert.deepEqual({ direction: added.direction, sideStyle: added.sideStyle }, directionSpecFromExample(item.member));
  assert.equal(added.similarSourceBlockId, undefined);
  assert.equal(setPhotoGroupId(added, next), source.spaceGroupId);
});

test('R7 A similar insertion consumes the same pile as pale slots and leaves the next unused member', () => {
  const blocks = group(), source = blocks[0], before = stageAdditions(blocks, source.pageSectionKey, { product })[0];
  const item = similarSpaceSetMember(blocks, source);
  assert.equal(before.member.exampleId, item.member.exampleId);
  const next = insertSpaceSetMember(blocks, source.pageSectionKey, item, { product, makeId: () => 'added' }, 1);
  const after = stageAdditions(next, source.pageSectionKey, { product })[0];
  assert.equal(after.member.order, 3);
  assert.equal(after.member.exampleId, nextUnusedSpaceSetMember(set, next).exampleId);
});

test('R7 A unused membership checks example IDs and member orders independently, without crossing instances', () => {
  const blocks = group();
  const used = [...blocks, { exampleId: set.members[1].exampleId }, { spaceSetMemberOrder: 6 }];
  assert.deepEqual(unusedSpaceSetMembers(set, used).map(member => member.order), [3, 4]);
  const secondInstance = group().map(block => ({ ...block, spaceGroupId: spaceSetGroupId(set.id, 'other-instance'),
    exampleId: set.members[1].exampleId, spaceSetMemberOrder: 2 }));
  assert.equal(similarSpaceSetMember([...blocks, ...secondInstance], blocks[0]).member.order, 2);
});

test('R7 A no matching framing, direction, singleton or detached cut leaves the original similar-cut path available', () => {
  const blocks = group([1, 2]);
  assert.equal(similarSpaceSetMember(blocks, blocks[0]), null, 'no other front full-body member');
  assert.equal(similarSpaceSetMember(blocks, { ...blocks[1], direction: 'side' }), null, 'same shot alone is insufficient');
  const [source] = group(), child = createSimilarCutBlock(source, 'child');
  assert.equal(similarSpaceSetMember([source, child], source), null, 'followers do not make a placed set');
  assert.equal(similarSpaceSetMember([source], source), null);
  assert.equal(similarSpaceSetMember(blocks, { ...blocks[1], spaceGroupId: null }), null);
  assert.equal(createSimilarCutBlock(source, 'fallback').similarSourceBlockId, source.id);
});

test('R7 A side styles must agree when both sides specify one, with legacy absent styles still usable', () => {
  const sideSet = { ...set, members: set.members.map((member, index) => ({ ...member,
    shot: 'medium', direction: 'side', sideStyle: index === 1 ? 'profile' : 'threeQuarter' })) };
  const blocks = group([1, 4], sideSet), source = blocks[0];
  assert.equal(nextUnusedSimilarSpaceSetMember(sideSet, blocks, source).order, 3, 'order 2 has a different side style');
  assert.equal(nextUnusedSimilarSpaceSetMember(sideSet, blocks, { ...source, sideStyle: null }).order, 2);
  const legacy = { ...sideSet, members: sideSet.members.map(member => member.order === 2 ? { ...member, sideStyle: null } : member) };
  assert.equal(nextUnusedSimilarSpaceSetMember(legacy, blocks, source).order, 2);
});

test('R7 A shared insertion keeps the cap, rejects used members and reconciles neutral or unavailable backgrounds', () => {
  const blocks = group(), source = blocks[0], item = similarSpaceSetMember(blocks, source);
  const capped = [...blocks, ...Array.from({ length: 10 }, (_, index) => ({ ...createSimilarCutBlock(source, `f${index}`) }))];
  assert.equal(insertSpaceSetMember(capped, source.pageSectionKey, item, { product }, 1), capped);
  const added = insertSpaceSetMember(blocks, source.pageSectionKey, item, { product, makeId: () => 'added' }, 1);
  assert.equal(insertSpaceSetMember(added, source.pageSectionKey, item, { product }, 1), added);
  for (const missing of [{ ...product, colors: [{ ...product.colors[0], swatchId: 'black' }] },
    { ...product, colors: [{ ...product.colors[0], images: [] }] }]) {
    assert.ok(insertSpaceSetMember(blocks, source.pageSectionKey, item, { product: missing }, 1)
      .every(block => block.horizonBackgroundMode === 'reference'));
  }
});

async function panelHarness(extra = {}) {
  const h = hookHarness();
  const Panel = await loadStoryboardComponent('StoryboardSpaceSetPanel.jsx', 'StoryboardSpaceSetPanel', {
    ...h.hooks, ...identity, Icon: 'icon', storyboardSpaceSetsFor: () => [set, STORYBOARD_SPACE_SETS.find(set => set.id === 'horizon-set-03-men-tonal-outer')],
  });
  let props = { mode: 'replace', cutType: 'horizon', gender: 'men', clothingType: 'outer', product, colorOpts,
    currentMembers: group([2, 3]), onChoose() {}, ...extra };
  return { render(next = {}) { props = { ...props, ...next }; h.render(Panel, props); return h.render(Panel, props); }, close: h.close };
}

test('R7 B only the current replacement card counts unique placed members and identifies its unused photographs', async t => {
  const current = group([2, 3]);
  // One member is recognized by ID, another by order. A follower cannot claim a third member.
  const h = await panelHarness({ currentMembers: [{ ...current[0], spaceSetMemberOrder: null }, { ...current[1], exampleId: 'legacy' },
    { ...createSimilarCutBlock(current[0], 'follower'), exampleId: set.members[4].exampleId, spaceSetMemberOrder: 5 }] });
  t.after(h.close);
  const tree = h.render(), cards = nodes(tree).filter(node => node.props?.['data-space-set']);
  assert.equal(cards.length, 2);
  const card = cards.find(node => node.props['data-space-set'] === set.id), mosaic = nodes(card).find(node => node.type === 'photo-mosaic');
  assert.equal(textOf(byClass(card, 'cutCount')), '2컷');
  assert.match(card.props['aria-label'], /, 6장 중 2장 놓임$/);
  assert.deepEqual(mosaic.props.unplacedExampleIds, [1, 4, 5, 6].map(order => set.members.find(member => member.order === order).exampleId));
  assert.ok(mosaic.props.unplacedExampleIds.includes(mosaic.props.cover.id), 'the absent cover is also pale');
  const other = cards.find(node => node !== card), otherSet = STORYBOARD_SPACE_SETS.find(set => set.id === other.props['data-space-set']);
  assert.equal(textOf(byClass(other, 'cutCount')), `${otherSet.members.length}컷`);
  assert.deepEqual(nodes(other).find(node => node.type === 'photo-mosaic').props.unplacedExampleIds, []);
  assert.ok(!other.props['aria-label'].includes('놓임'));
});

test('R7 B a fully placed current card and add mode retain total counts and ordinary names', async t => {
  const h = await panelHarness({ currentMembers: group([1, 2, 3, 4, 5, 6]) }); t.after(h.close);
  for (const tree of [h.render(), h.render({ mode: 'add', currentMembers: group([2, 3]) })]) {
    const card = nodes(tree).find(node => node.props?.['data-space-set'] === set.id);
    assert.equal(textOf(byClass(card, 'cutCount')), '6컷');
    assert.deepEqual(nodes(card).find(node => node.type === 'photo-mosaic').props.unplacedExampleIds, []);
    assert.ok(!card.props['aria-label'].includes('놓임'));
  }
});

test('R7 B mouse previews keep the shown photo opacity for absent cover and side members', async t => {
  const h = hookHarness(); t.after(h.close);
  const Mosaic = await loadStoryboardComponent('PhotoMosaic.jsx', 'PhotoMosaic', { ...h.hooks, ...generation });
  const members = set.members.slice(0, 3).map(member => ({ ...member, id: member.exampleId }));
  const props = { members, cover: members[0], all: true, unplacedExampleIds: [members[0].id, members[2].id] };
  const render = () => h.render(Mosaic, props);
  let tree = render(); assert.equal(byClass(tree, 'mosaicCover').props['data-unplaced'], true);
  let sides = nodes(tree).filter(node => node.props?.className === 'mosaicSide');
  assert.equal(sides[0].props['data-unplaced'], undefined); assert.equal(sides[1].props['data-unplaced'], true);
  sides[1].props.onPointerEnter({ pointerType: 'mouse' }); tree = render();
  assert.equal(nodes(byClass(tree, 'mosaicCover')).find(node => node.type === 'example-thumb').props.example.id, members[2].id);
  assert.equal(byClass(tree, 'mosaicCover').props['data-unplaced'], true);
  sides[0].props.onPointerEnter({ pointerType: 'mouse' }); tree = render();
  assert.equal(byClass(tree, 'mosaicCover').props['data-unplaced'], undefined);
  sides[0].props.onPointerLeave(); tree = render(); assert.equal(byClass(tree, 'mosaicCover').props['data-unplaced'], true);
  assert.ok(nodes(h.render(Mosaic, { members, cover: members[0] })).every(node => node.props?.['data-unplaced'] === undefined));
});

const oldExample = raw.find(example => example.cutType === 'styling' && example.gender === 'men' && example.applicableClothingTypes.includes('outer'));
const oldBlock = { id: 'pending', source: 'ai', pageSectionKey: 'emo.first', pageSectionKind: 'first', sectionRole: 'hooking', contentRole: 'hero',
  cutType: 'styling', shot: oldExample.shot, exampleId: oldExample.id, colorId: 'sky', outerClosureState: 'closed', matchIds: ['shirt'] };
async function photoPanel(extra = {}) {
  return storyboardPhotoPanelHarness({ block: { ...oldBlock }, catalogs, colorOpts, detailColorOpts: colorOpts, clothingType: 'outer', exampleGender: 'men',
    matchClothing: [{ id: 'shirt', name: '셔츠', thumb: '/shirt.webp' }], ...extra });
}
function expectLock(tree, locked) {
  const options = byClass(tree, 'photoOptions'); assert.ok(options);
  assert.equal(options.props.inert, locked ? '' : undefined);
  assert.equal(options.props['aria-disabled'], locked ? true : undefined);
  return options;
}

test('R7 C pending cut changes lock all existing lower options, including an open matching panel, and photo success unlocks them', async t => {
  const h = await photoPanel(); t.after(h.close);
  let tree = h.render(); button(tree, '매칭 의류 바꾸기').props.onClick(); tree = h.render();
  expectLock(tree, false);
  nodes(tree).find(node => node.type === 'underline-tabs').props.onChange('horizon'); tree = h.render();
  const options = expectLock(tree, true);
  assert.equal(nodes(options).find(node => node.type === 'color-dots').props.block.cutType, oldBlock.cutType);
  assert.equal(nodes(options).find(node => node.type === 'closure-field').props.value, 'closed');
  assert.ok(button(options, '매칭 의류 바꾸기'));
  assert.ok(byClass(options, 'sb-match-inline')); assert.ok(byClass(options, 'match-grid'));
  await nodes(tree).find(node => node.type === 'mood-guide').props.onExampleChange(set.members[0].exampleId);
  tree = h.render(); expectLock(tree, false); assert.equal(h.block.cutType, 'horizon');
});

test('R7 C applying a source-based product recipe unlocks color and updates options to the new cut', async t => {
  const h = await photoPanel(); t.after(h.close);
  let tree = h.render(); nodes(tree).find(node => node.type === 'underline-tabs').props.onChange('product'); tree = h.render();
  expectLock(tree, true); assert.ok(nodes(tree).some(node => node.type === 'closure-field'));
  await nodes(tree).find(node => node.type === 'button' && textOf(node).endsWith(' 적용')).props.onClick();
  tree = h.render(); expectLock(tree, false); assert.equal(h.block.cutType, 'product');
  assert.ok(!nodes(tree).some(node => node.type === 'closure-field'));
  assert.ok(!button(tree, '매칭 의류 바꾸기'));
});

test('R7 C canceling a requested section move unlocks unchanged options', async t => {
  let canceled = 0;
  const h = await photoPanel({ requestedRecipe: { blockId: oldBlock.id, cutType: 'horizon', shot: 'full' }, onCancelRequestedRecipe() { canceled++; } });
  t.after(h.close); h.render(); let tree = h.render(); expectLock(tree, true);
  button(tree, '섹션 이동 취소').props.onClick(); tree = h.render();
  expectLock(tree, false); assert.equal(canceled, 1); assert.equal(h.block.cutType, oldBlock.cutType);
});

test('R7 C pending save and failed save keep the lock; retry success removes it', async t => {
  let rejectSave;
  const h = await photoPanel({ onAtomicChange: () => new Promise((_resolve, reject) => { rejectSave = reject; }) }); t.after(h.close);
  let tree = h.render(); nodes(tree).find(node => node.type === 'underline-tabs').props.onChange('horizon'); tree = h.render();
  const pending = nodes(tree).find(node => node.type === 'mood-guide').props.onExampleChange(set.members[0].exampleId);
  tree = h.render(); expectLock(tree, true);
  rejectSave(new Error('save failed')); await pending; tree = h.render(); expectLock(tree, true);
  assert.ok(textOf(tree).includes('변경 내용을 저장하지 못했어요'));
  assert.equal(h.block.cutType, oldBlock.cutType);
  tree = h.render({ onAtomicChange: async () => {} }); await button(tree, '다시 시도').props.onClick();
  expectLock(h.render(), false);
});

test('R7 D deleting a toned closed horizon set down to one preserves both option values and the space contract', () => {
  const original = group([1, 2, 3]);
  const enabled = updateHorizonGroupBackground(updateSetOuterClosure(original, original[0].spaceGroupId, true), original[0].spaceGroupId, 'garment-tone');
  const next = enabled.slice(1).reduce((blocks, block) => removeStoryboardPhoto(blocks, block.id, product), enabled);
  assert.equal(next.length, 1); assert.equal(next[0].outerClosureState, 'closed');
  assert.equal(next[0].horizonBackgroundMode, 'garment-tone'); assert.equal(next[0].spaceGroupId, original[0].spaceGroupId);
  assert.equal(setPhotoGroupId(next[0], next), null);
});

test('R7 D singleton tone dots follow the caption color after the color name and stay absent from placed set captions', async () => {
  const [source, other] = group(), colors = [...product.colors, { id: 'gray', name: '그레이', swatchId: 'gray' }];
  const tree = await storyboardStage([source], { catalogs: { detailProduct: { ...product, colors } }, colorOpts });
  const caption = byClass(tree, 'photoCaption'), garment = byClass(caption, 'captionGarment');
  const items = nodes(garment), nameIndex = items.findIndex(node => node.props?.className === 'captionColorName');
  const markIndex = items.findIndex(node => node.type === 'set-caption-tone');
  assert.ok(nameIndex >= 0 && markIndex > nameIndex);
  assert.equal(items[markIndex].props.color.hex, colorOpts[0].hex);
  assert.equal(items.find(node => node.type === 'circle').props.fill, colorOpts[0].hex);
  assert.equal(caption.props.children.at(-1).type, 'strong'); assert.equal(textOf(caption.props.children.at(-1)), '1번째 컷');
  const placed = await storyboardStage([source, other], { catalogs: { detailProduct: { ...product, colors } }, colorOpts });
  assert.ok(nodes(placed).some(node => node.type === 'set-head-marks'));
  assert.ok(!nodes(placed).some(node => node.type === 'set-caption-tone'));
});

test('R7 D one-color captions show only the tone dots at left, gated by horizon, group and enabled mode', async () => {
  const [source] = group();
  const tree = await storyboardStage([source], { catalogs: { detailProduct: product }, colorOpts });
  const caption = byClass(tree, 'photoCaption');
  assert.ok(nodes(caption).some(node => node.type === 'set-caption-tone'));
  assert.ok(!byClass(caption, 'captionColorName')); assert.ok(!byClass(caption, 'captionColor'));
  for (const patch of [{ cutType: 'styling' }, { spaceGroupId: null }, { horizonBackgroundMode: 'reference' }, { horizonBackgroundMode: null }]) {
    const tree = await storyboardStage([{ ...source, ...patch }], { catalogs: { detailProduct: product }, colorOpts });
    assert.ok(!nodes(tree).some(node => node.type === 'set-caption-tone'), JSON.stringify(patch));
  }
});

test('R7 D caption tone has the same mouse and keyboard tooltip and a focusable accessible name', async t => {
  const Mark = await loadStoryboardComponent('SetOptionMarks.jsx', 'SetCaptionTone');
  const mark = Mark({ color: colorOpts[0] });
  assert.equal(mark.props.reason, '배경색 맞춤');
  const focus = nodes(mark).find(node => node.props?.tabIndex === 0); assert.equal(focus.props['aria-label'], '배경색 맞춤');
  const icon = nodes(mark).find(node => typeof node.type === 'function' && node.type.name === 'ToneIcon');
  assert.equal(icon.props.dots, true);
  assert.ok(nodes(icon.type(icon.props)).filter(node => node.type === 'circle' && node.props.fill === colorOpts[0].hex).length === 2);
  const h = hookHarness(); t.after(h.close);
  const Reason = await loadStoryboardComponent('StoryboardChoiceControls.jsx', 'ChoiceReason', { ...h.hooks });
  const tree = h.render(Reason, mark.props), tooltip = nodes(tree).find(node => node.props?.role === 'tooltip');
  assert.equal(textOf(tooltip), '배경색 맞춤');
  let shown = 0, open = false;
  tooltip.ref.current = { matches: () => open, showPopover() { shown++; open = true; }, hidePopover() { open = false; } };
  tree.props.onMouseEnter(); assert.equal(shown, 1);
  open = false; tree.props.onFocusCapture(); assert.equal(shown, 2);
});

test('R7 D choosing another singleton photo keeps same-set background, resets outer opening and resets background for other sets or standalone photos', async t => {
  const [source] = group([2]);
  const otherSet = STORYBOARD_SPACE_SETS.find(set => set.id === 'horizon-set-03-men-tonal-outer');
  const standalone = raw.find(example => example.cutType === 'horizon' && example.gender === 'men' && example.applicableClothingTypes.includes('outer'));
  for (const [exampleId, expectedGroup, expectedMode] of [[set.members[4].exampleId, 'same', 'garment-tone'],
    [otherSet.members[0].exampleId, 'new', 'reference'], [standalone.id, 'none', null]]) {
    const h = await storyboardPhotoPanelHarness({ block: source, blocks: [source], catalogs, clothingType: 'outer', exampleGender: 'men', colorOpts, detailColorOpts: colorOpts });
    t.after(h.close);
    const tree = h.render(); await nodes(tree).find(node => node.type === 'mood-guide').props.onExampleChange(exampleId, 'all');
    assert.equal(h.block.outerClosureState, 'open'); assert.equal(h.block.horizonBackgroundMode, expectedMode);
    if (expectedGroup === 'same') assert.equal(h.block.spaceGroupId, source.spaceGroupId);
    else if (expectedGroup === 'new') assert.ok(h.block.spaceGroupId && h.block.spaceGroupId !== source.spaceGroupId);
    else assert.equal(h.block.spaceGroupId, null);
  }
});

async function galleryHarness() {
  const h = hookHarness(), entries = Array.from({ length: 8 }, (_, index) => {
    const members = [{ id: `s${index}-front`, cutType: 'horizon', shot: 'medium', direction: 'front' },
      { id: `s${index}-back`, cutType: 'horizon', shot: 'medium', direction: 'back' }];
    return { id: `s${index}`, kind: 'stack', example: members[0], members };
  });
  const Gallery = await loadStoryboardComponent('StoryboardPhotoGallery.jsx', 'StoryboardPhotoGallery', {
    ...h.hooks, ...generation, Icon: 'icon', filterSpaceSetsForModel: value => value, storyboardSpaceSetsFor: () => [],
    photoGalleryChoices: () => ({ entries, tabs: [{ value: 'studio', label: '스튜디오' }], place: 'studio' }),
  });
  const props = { block: { id: 'keys' }, catalogs: { genExamples: [] }, cut: 'horizon', studioStyle: 'horizon',
    studioOptions: [{ value: 'horizon', label: '호리존컷' }] };
  return { render() { h.render(Gallery, props); return h.render(Gallery, props); }, close: h.close };
}
const keyEvent = (key, target) => ({ key, target, prevented: false, stopped: false,
  preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; } });

test('R7 E top filters, studio switches and tray arrows never paginate or consume their native arrow events', async t => {
  const h = await galleryHarness(); t.after(h.close); let tree = h.render();
  nodes(tree).find(node => node.props?.['data-photo-choice'] === 's0').props.onClick(); tree = h.render();
  const targets = [{ control: 'framing' }, { control: 'studio' }, { control: 'tray' }];
  byClass(tree, 'photoTop').ref.current = { contains: target => targets.includes(target) };
  for (const target of targets) for (const key of ['ArrowRight', 'ArrowLeft']) {
    const event = keyEvent(key, target); byClass(tree, 'galleryRegion').props.onKeyDown(event); tree = h.render();
    assert.equal(nodes(tree).find(node => node.type === 'photo-pages').props.page, 0);
    assert.equal(event.prevented, false); assert.equal(event.stopped, false);
    assert.equal(nodes(tree).find(node => node.props?.['data-photo-tray']).props['data-photo-tray'], 's0');
  }
});

test('R7 E region and list arrows still paginate; Escape inside the top tray closes it and restores card focus', async t => {
  const h = await galleryHarness(); t.after(h.close); let tree = h.render();
  const topTarget = {}, listTarget = {}, regionTarget = {};
  byClass(tree, 'photoTop').ref.current = { contains: target => target === topTarget };
  for (const target of [regionTarget, listTarget]) {
    let event = keyEvent('ArrowRight', target); byClass(tree, 'galleryRegion').props.onKeyDown(event); tree = h.render();
    assert.equal(nodes(tree).find(node => node.type === 'photo-pages').props.page, 1); assert.equal(event.prevented, true);
    event = keyEvent('ArrowLeft', target); byClass(tree, 'galleryRegion').props.onKeyDown(event); tree = h.render();
    assert.equal(nodes(tree).find(node => node.type === 'photo-pages').props.page, 0); assert.equal(event.prevented, true);
  }
  const card = nodes(tree).find(node => node.props?.['data-photo-choice'] === 's0');
  let focused = 0; card.ref({ focus() { focused++; } }); card.props.onClick(); tree = h.render();
  const event = keyEvent('Escape', topTarget); byClass(tree, 'galleryRegion').props.onKeyDown(event); tree = h.render();
  assert.ok(!nodes(tree).some(node => node.props?.['data-photo-tray'])); assert.equal(focused, 1);
  assert.equal(event.prevented, true); assert.equal(event.stopped, true);
});

test('R7 UI states use shared tokens and scope caption chip rules away from tooltip descendants', () => {
  const choices = readFileSync('src/features/storyboard/StoryboardChoices.module.css', 'utf8');
  const panels = readFileSync('src/features/storyboard/StoryboardPanels.module.css', 'utf8');
  const layout = readFileSync('src/features/storyboard/StoryboardLayout.module.css', 'utf8');
  assert.match(readFileSync('src/styles/tokens.css', 'utf8'), /--sb-set-unplaced-opacity: \.4;/);
  assert.match(choices, /\.mosaic \.mosaicCover\[data-unplaced="true"\] img, \.mosaic \.mosaicSide\[data-unplaced="true"\] img \{ opacity: var\(--sb-set-unplaced-opacity\); \}/);
  assert.match(panels, /\.photoOptions\[aria-disabled="true"\] \{ opacity: var\(--sb-option-disabled-opacity\); \}/);
  assert.match(layout, /\.photoCaption > span \{/); assert.doesNotMatch(layout, /\.photoCaption span \{/);
});
