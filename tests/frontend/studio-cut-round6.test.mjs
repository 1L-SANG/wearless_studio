import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stageAdditions, addStageAddition } from '../../src/lib/pageTemplates/stageAdditions.js';
import { packStageRows, withStageAdditions } from '../../src/lib/pageTemplates/storyboardStage.js';
import { STORYBOARD_SPACE_SETS, spaceSetGroupId, storyboardSpaceSetsFor, withStoryboardSpaceSetExamples } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { photoGalleryChoices } from '../../src/lib/pageTemplates/storyboardPhotoChoices.js';
import { createSpaceSetMembers } from '../../src/lib/storyboardSpaceSets.js';
import { createSimilarCutBlock } from '../../src/lib/storyboardSimilarCuts.js';
import { setPhotoGroupId } from '../../src/lib/storyboardSetOptions.js';
import { hookHarness, loadStoryboardComponent, nodes, textOf } from './helpers/storyboardComponents.mjs';
import { storyboardPhotoPanelHarness } from './helpers/storyboardPhotoPanelHarness.mjs';
import { storyboardStage } from './helpers/storyboardStageHarness.mjs';
import * as generation from '../../src/lib/generationExamples.js';
import * as identity from '../../src/lib/identityScope.js';
import raw from '../../src/data/genExamples.json' with { type: 'json' };

const byClass = (tree, name) => nodes(tree).find(node => node.props?.className === name);
const card = (id, kind = 'stack') => {
  const example = { id: `${id}-front`, shot: 'medium', direction: 'front', cutType: 'horizon' };
  return { id, kind, example, ...(kind === 'stack' ? { members: [example, { ...example, id: `${id}-back`, direction: 'back' }] } : {}) };
};
async function galleryHarness(entries, extra = {}) {
  const h = hookHarness();
  const Gallery = await loadStoryboardComponent('StoryboardPhotoGallery.jsx', 'StoryboardPhotoGallery', {
    ...h.hooks, ...generation, Icon: 'icon', MineImageTab: 'mine-images', EditorMoodGuide: 'editor-gallery',
    filterSpaceSetsForModel: sets => sets, storyboardSpaceSetsFor: () => [],
    photoGalleryChoices: (_catalog, _sets, { place }) => ({ entries, tabs: [{ value: place, label: place }], place }),
  });
  let props = { block: { id: 'gallery', pageSectionKind: 'wear' }, catalogs: { genExamples: [] }, cut: 'horizon', studioStyle: 'horizon',
    studioOptions: [{ value: 'horizon', label: '호리존컷' }], onUseMine() {}, ...extra };
  return { render(next = {}) { props = { ...props, ...next }; h.render(Gallery, props); return h.render(Gallery, props); }, close: h.close };
}

test('R6 photo filters and tray share the white sticky region; list, pager and mine stay in gray', async t => {
  const entries = Array.from({ length: 8 }, (_, i) => card(`set${i}`));
  const h = await galleryHarness(entries); t.after(h.close);
  let tree = h.render(), top = byClass(tree, 'photoTop'), list = byClass(tree, 'photoList');
  assert.ok(nodes(top).some(node => node.type === 'photo-segment'));
  assert.ok(nodes(top).some(node => node.type === 'photo-framing'));
  assert.equal(nodes(top).find(node => node.props?.['data-photo-tray']), undefined);
  for (const name of ['photoListHead', 'photoGrid', 'textAction']) assert.ok(byClass(list, name));
  assert.ok(nodes(list).some(node => node.type === 'photo-pages'));
  byClass(list, 'textAction').props.onClick(); tree = h.render();
  assert.ok(nodes(byClass(tree, 'photoList')).some(node => node.type === 'mine-images'));
  nodes(tree).find(node => node.props?.['data-photo-choice'] === 'set0').props.onClick(); tree = h.render();
  assert.ok(nodes(byClass(tree, 'photoTop')).some(node => node.props?.['data-photo-tray'] === 'set0'));
  assert.ok(!nodes(byClass(tree, 'photoList')).some(node => node.props?.['data-photo-tray']));
  tree = h.render({ cut: 'styling', studioStyle: null });
  assert.ok(nodes(byClass(tree, 'photoTop')).some(node => node.type === 'photo-filters'));
});

test('R6 set-only list counts all framing cards across pages; opening and choosing keep the same regions', async t => {
  const entries = Array.from({ length: 8 }, (_, i) => card(`set${i}`));
  const h = await galleryHarness(entries); t.after(h.close);
  let tree = h.render(); assert.equal(textOf(byClass(tree, 'photoListHead')), '세트 목록8개');
  nodes(tree).find(node => node.type === 'photo-pages').props.onChange(1); tree = h.render();
  assert.equal(textOf(byClass(tree, 'photoListHead')), '세트 목록8개');
  const set = nodes(tree).find(node => node.props?.['data-photo-choice'] === 'set6'); set.props.onClick(); tree = h.render();
  const pendingTray = nodes(byClass(tree, 'photoTop')).find(node => node.props?.['data-photo-tray']);
  assert.equal(pendingTray.props['data-pending'], true); assert.ok(byClass(pendingTray, 'trayAnts'));
  tree = h.render({ exampleId: entries[6].members[0].id });
  const chosenTray = nodes(byClass(tree, 'photoTop')).find(node => node.props?.['data-photo-tray']);
  assert.equal(chosenTray.props['data-pending'], false); assert.equal(byClass(chosenTray, 'trayAnts'), undefined);
  assert.equal(byClass(pendingTray, 'trayCuts').props.className, byClass(chosenTray, 'trayCuts').props.className);
  assert.deepEqual(nodes(byClass(pendingTray, 'trayCuts')).map(n => n.props?.['data-example-id']).filter(Boolean),
    nodes(byClass(chosenTray, 'trayCuts')).map(n => n.props?.['data-example-id']).filter(Boolean));
  assert.equal(textOf(byClass(tree, 'photoListHead')), '세트 목록8개');
});

test('R6 one standalone card anywhere changes the title; repeated signatures count once', async t => {
  const entries = [...Array.from({ length: 6 }, (_, i) => card(`set${i}`)), card('single', 'example')];
  const h = await galleryHarness(entries); t.after(h.close);
  let tree = h.render(); assert.equal(textOf(byClass(tree, 'photoListHead')), '사진 목록7개');
  assert.ok(!nodes(byClass(tree, 'photoGrid')).some(n => n.props?.['data-photo-choice'] === 'single'));
  const signature = { id: 'sig_men_01', shot: 'medium', signature: true };
  const minimal = await galleryHarness(entries.slice(0, 6), { cut: 'styling', studioStyle: 'minimal', signature }); t.after(minimal.close);
  tree = minimal.render(); assert.equal(textOf(byClass(tree, 'photoListHead')), '사진 목록7개');
  nodes(tree).find(n => n.type === 'photo-pages').props.onChange(1); tree = minimal.render();
  assert.equal(textOf(byClass(tree, 'photoListHead')), '사진 목록7개');
  assert.equal(nodes(byClass(tree, 'photoGrid')).filter(n => n.props?.['data-photo-choice'] === signature.id).length, 1);
});

test('R6 empty lists omit the heading and keep the empty message in gray; signature alone is one photo', async t => {
  const h = await galleryHarness([]); t.after(h.close);
  let tree = h.render(); assert.equal(byClass(tree, 'photoListHead'), undefined);
  assert.equal(textOf(byClass(byClass(tree, 'photoList'), 'empty')), '이 조건의 장소 사진을 준비 중이에요.');
  tree = h.render({ cut: 'styling', studioStyle: 'minimal', signature: { id: 'sig_only', signature: true } });
  assert.equal(textOf(byClass(tree, 'photoListHead')), '사진 목록1개'); assert.equal(byClass(tree, 'empty'), undefined);
});

test('R6 released gallery count follows the chosen framing rather than the all-framing catalog', async t => {
  const h = hookHarness(); t.after(h.close);
  const Gallery = await loadStoryboardComponent('StoryboardPhotoGallery.jsx', 'StoryboardPhotoGallery', {
    ...h.hooks, ...generation, ...identity, storyboardSpaceSetsFor, Icon: 'icon',
  });
  const catalogs = withStoryboardSpaceSetExamples({ genExamples: raw });
  const props = { block: { id: 'framing-count' }, catalogs, gender: 'men', clothingType: 'top', cut: 'horizon', studioStyle: 'horizon' };
  const render = () => { h.render(Gallery, props); return h.render(Gallery, props); };
  let tree = render();
  for (const shot of ['full', 'medium']) {
    nodes(tree).find(n => n.type === 'photo-framing').props.onChange(shot); tree = render();
    const options = { gender: 'men', clothingType: 'top', cutTypes: ['horizon'], studioStyle: 'horizon', place: 'studio' };
    const count = photoGalleryChoices(catalogs.genExamples, storyboardSpaceSetsFor(options), { ...options, shot }).entries.length;
    const allCount = photoGalleryChoices(catalogs.genExamples, storyboardSpaceSetsFor(options), { ...options, shot: 'all' }).entries.length;
    assert.ok(count > 0 && count < allCount);
    assert.equal(textOf(nodes(byClass(tree, 'photoListHead')).find(n => n.type === 'span')), `${count}개`);
  }
});

test('R6 focusing a covered list control scrolls it below the measured sticky region', async t => {
  const h = await galleryHarness([card('set')]); t.after(h.close);
  const tree = h.render(), calls = [];
  byClass(tree, 'photoTop').ref.current = { getBoundingClientRect: () => ({ bottom: 240 }) };
  byClass(tree, 'galleryRegion').ref.current = { closest: selector => {
    assert.equal(selector, '[data-storyboard-scroll]'); return { scrollBy: options => calls.push(options) };
  } };
  const target = top => ({ getBoundingClientRect: () => ({ top }), ownerDocument: {
    defaultView: { getComputedStyle: () => ({ scrollMarginTop: '4px' }) } } });
  byClass(tree, 'photoList').props.onFocusCapture({ target: target(200) });
  assert.deepEqual(calls, [{ top: -44, behavior: 'instant' }]);
  byClass(tree, 'photoList').props.onFocusCapture({ target: target(250) }); assert.equal(calls.length, 1);
});

test('R6 sticky surfaces use drawer insets; tray outline is absolute and never changes the photo position', () => {
  const css = readFileSync('src/features/storyboard/StoryboardChoices.module.css', 'utf8');
  const tokens = readFileSync('src/styles/tokens.css', 'utf8');
  const panels = readFileSync('src/features/storyboard/StoryboardPanels.module.css', 'utf8');
  const rule = name => css.match(new RegExp(`\\.${name} \\{([^}]+)\\}`))[1];
  assert.match(rule('photoTop'), /position: sticky; top: calc\(-1 \* var\(--sb-drawer-padding-top\)\)/);
  for (const name of ['photoTop', 'photoList']) assert.match(rule(name), /margin-inline: calc\(-1 \* var\(--sb-drawer-padding-inline\)\)/);
  assert.match(rule('photoTop'), /background: var\(--bg-1\)/); assert.match(rule('photoTop'), /box-shadow: var\(--sb-photo-sticky-shadow\)/);
  assert.match(rule('photoList'), /padding: var\(--sp-16\) var\(--sb-drawer-padding-inline\) var\(--sp-20\); background: var\(--bg-2\)/);
  assert.match(rule('photoListHead'), /margin-bottom: calc\(var\(--sp-8\) \+ var\(--sp-2\)\)/);
  assert.match(rule('tray'), /padding: calc\(var\(--sp-8\) \+ var\(--sp-2\)\) 0 0/);
  assert.match(rule('tray'), /background: var\(--bg-1\); overflow: visible/); assert.doesNotMatch(rule('tray'), /--bg-2/);
  assert.match(rule('trayAnts'), /position: absolute; inset: calc\(-1 \* var\(--sb-tray-outline-top\)\) calc\(-1 \* var\(--sb-tray-outline-inline\)\) calc\(-1 \* var\(--sb-tray-outline-bottom\)\)/);
  assert.match(tokens, /--sb-tray-outline-top: 2px;\s*--sb-tray-outline-inline: 9px;\s*--sb-tray-outline-bottom: 10px/);
  assert.match(tokens, /--sb-photo-sticky-shadow: 0 1px 0 var\(--ring-strong\), 0 8px 16px -12px/);
  assert.match(panels, /\.drawerBody \{[^}]*padding: var\(--sb-drawer-padding-top\) var\(--sb-drawer-padding-inline\)/);
  assert.match(panels, /\.splitPhotoPanel \.garmentColorField:global\(\.insp-sec\) \{ margin-top: 0; padding-top: var\(--sp-20\); border-top: 0/);
  assert.doesNotMatch(css, /\.tray\[data-pending[^}]+(?:padding|margin|border):/);
  assert.match(css, /\.tray\[data-dragging="true"\] \.trayAnts rect \{ animation-play-state: paused/);
  assert.match(css, /prefers-reduced-motion: reduce[^\n]+\.trayAnts rect \{ animation: none/);
});

test('R6 changing cut kind keeps the pending panel unboxed and preserves cancel, apply and retry paths', async t => {
  const example = raw.find(item => item.cutType === 'styling' && item.gender === 'men' && item.applicableClothingTypes.includes('top'));
  const h = await storyboardPhotoPanelHarness({ block: { id: 'first', source: 'ai', pageSectionKind: 'first', sectionRole: 'hooking',
    cutType: 'styling', shot: example.shot, exampleId: example.id }, catalogs: { genExamples: raw,
    shotTypes: [{ value: 'full' }, { value: 'medium' }], productShotTypes: [{ value: 'ghost' }, { value: 'detail' }] }, exampleGender: 'men', clothingType: 'top' });
  t.after(h.close);
  let tree = h.render(); nodes(tree).find(n => n.type === 'underline-tabs').props.onChange('horizon'); tree = h.render();
  assert.ok(byClass(tree, 'sb-pending-recipe')); assert.ok(tree.props.className.includes('splitPhotoPanel'));
  assert.equal(nodes(tree).find(n => n.type === 'mood-guide').props.exampleId, null);
  const features = readFileSync('src/styles/features.css', 'utf8');
  assert.match(features, /\.sb-pending-recipe \{ padding: 0; border-radius: 0; background: transparent; \}/);
  const source = readFileSync('src/features/storyboard/StoryboardPhotoPanel.jsx', 'utf8');
  for (const label of ['섹션 이동 취소', '다시 시도', ' 적용']) assert.ok(source.includes(label));
});

const product = { clothingType: 'outer', colors: [{ id: 'blue', swatchId: 'blue', isBase: true, images: [{ slot: 'Front', src: '/front.webp' }] }] };
const setFor = type => STORYBOARD_SPACE_SETS.find(set => set.setType === type && set.gender === 'men' && set.applicableClothingTypes.includes('outer'));
const makeGroup = (set, count, instance = 'a', usage = 'styling') => createSpaceSetMembers(set, {
  source: 'ai', taxonomyVersion: 4, sectionRole: usage === 'styling' ? 'styling' : 'studio', contentRole: usage === 'styling' ? 'coordination' : 'fit',
  sectionId: 'wear', pageSectionKey: `emo.wear_${usage}`, pageSectionKind: 'wear', pageSectionUsage: usage, pageDesignId: 'P02-D01',
  colorId: 'blue', outerClosureState: 'closed', sectionCustom: true,
}, { spaceGroupId: spaceSetGroupId(set.id, instance), members: set.members.slice(0, count), setSelectionOrigin: 'seed',
  makeId: (_member, index) => `${instance}-${index}` }).map((block, index) => ({ ...block, slotKey: `wear_${usage}.wear${index + 1}` }));
const suggestions = blocks => stageAdditions(blocks, blocks[0].pageSectionKey, { product });

for (const type of ['horizon-sequence', 'horizon-rotation', 'styling']) test(`R6 ${type} suggests the next unused member at the group end with its approved label`, async () => {
  const set = setFor(type), group = makeGroup(set, 1, type, type === 'styling' ? 'styling' : 'fit');
  const [item] = suggestions(group); assert.ok(item); assert.equal(item.member.exampleId, set.members[1].exampleId);
  assert.equal(item.label, type === 'styling' ? '같은 장소 1컷 더' : '1컷 더');
  const tree = await storyboardStage(group, { catalogs: { detailProduct: product } });
  const tile = nodes(tree).find(n => n.type === 'pale-slot'); assert.equal(tile.props.item.id, item.id);
  const Slot = await loadStoryboardComponent('StoryboardPaleSlot.jsx', 'StoryboardPaleSlot', { useMemo: fn => fn() });
  assert.equal(Slot(tile.props).props['aria-label'], `${item.label} 넣기`);
  assert.equal(setPhotoGroupId(group[0], group), null);
});

test('R6 studio candidates are independent of wear usage and stop for other sections, exhaustion and 12 photos', () => {
  const set = setFor('horizon-sequence');
  for (const usage of ['styling', 'fit', 'studio']) assert.equal(suggestions(makeGroup(set, 1, usage, usage)).length, 1);
  const group = makeGroup(set, 1);
  for (const kind of ['first', 'review', 'faq', 'sns', 'color_product']) {
    const blocks = group.map(b => ({ ...b, pageSectionKind: kind, pageSectionKey: `emo.${kind}`, pageDesignId: 'none' }));
    assert.ok(suggestions(blocks).every(item => item.kind !== 'spaceMember'), kind);
  }
  assert.equal(suggestions(makeGroup(setFor('horizon-rotation'), 3)).length, 0);
  const capped = Array.from({ length: 12 }, (_, i) => ({ ...group[0], id: `cap-${i}` })); assert.equal(suggestions(capped).length, 0);
});

test('R6 no pale-only row after a full four-photo group, even if the add slot already occupies the next row', () => {
  const set = setFor('horizon-sequence');
  for (const usage of ['styling', 'fit']) assert.equal(suggestions(makeGroup(set, 4, usage, usage)).length, 0);
  const [root] = makeGroup(set, 1);
  assert.equal(suggestions([root, ...Array.from({ length: 3 }, (_, i) => createSimilarCutBlock(root, `similar${i}`))]).length, 0);
});

test('R6 a pale slot never pushes a later group or only the add slot onto another row', () => {
  const set = setFor('styling'), a = makeGroup(set, 2, 'a'), b = makeGroup(set, 2, 'b');
  assert.equal(packStageRows([...a, ...b]).length, 2); assert.equal(suggestions([...a, ...b]).length, 0);
  const three = [...a, createSimilarCutBlock(a[0], 'similar')]; assert.equal(packStageRows(three).length, 1); assert.equal(suggestions(three).length, 0);
  assert.equal(suggestions(makeGroup(setFor('horizon-sequence'), 3, 'no-add', 'fit')).length, 1, 'without an add slot the fourth column is spare');
});

test('R6 multiple candidates are judged in stage order against earlier accepted pale slots', () => {
  const set = setFor('styling'), a = makeGroup(set, 1, 'a'), b = makeGroup(set, 1, 'b');
  const both = [...suggestions(a), ...suggestions(b)]; assert.equal(both.length, 2);
  const staged = withStageAdditions([...a, ...b], both.reverse());
  assert.deepEqual(staged.filter(b => b.paleAddition).map(b => b.paleAddition.groupId), [a[0].spaceGroupId]);
  assert.deepEqual(suggestions([...a, ...b]).map(item => item.groupId), [a[0].spaceGroupId]);
  assert.equal(packStageRows(staged).length, 1);
});

test('R6 a set with more than four placed photos may use spare columns on its existing last row', () => {
  const set = setFor('horizon-sequence'), group = makeGroup(set, 5, 'long', 'fit');
  assert.equal(suggestions(group).length, 1);
  const staged = withStageAdditions(group, suggestions(group), { add: false });
  assert.equal(packStageRows(staged, { add: false }).length, 2);
});

test('R6 member insertion follows similar cuts and inherits the complete set recipe without mutating the board', () => {
  const set = setFor('horizon-sequence'), group = makeGroup(set, 1, 'inherit', 'fit').map(b => ({ ...b,
    horizonBackgroundMode: 'garment-tone', spaceVariation: 'expressive', outerClosureState: 'closed' }));
  const blocks = [...group, createSimilarCutBlock(group[0], 'similar')], before = structuredClone(blocks), item = suggestions(blocks)[0];
  const next = addStageAddition(blocks, group[0].pageSectionKey, item, { product, makeId: () => 'added' });
  assert.deepEqual(blocks, before); assert.deepEqual(next.map(b => b.id), [group[0].id, 'similar', 'added']);
  const added = next.at(-1);
  for (const key of ['spaceGroupId', 'spaceVariation', 'horizonBackgroundMode', 'outerClosureState', 'setSelectionOrigin']) assert.equal(added[key], group[0][key], key);
  assert.equal(added.exampleId, item.member.exampleId); assert.equal(added.spaceSetMemberOrder, item.member.order);
  assert.equal(added.slotKey, 'wear_fit.front1'); assert.equal(added.similarSourceBlockId, undefined);
  assert.equal(setPhotoGroupId(added, next), group[0].spaceGroupId);
  assert.equal(suggestions(next)[0].member.exampleId, set.members[2].exampleId);
});

test('R6 inserting a member normalizes neutral tones and then reconciles unavailable background evidence', () => {
  const set = setFor('horizon-sequence'), group = makeGroup(set, 1, 'neutral', 'fit').map(b => ({ ...b, horizonBackgroundMode: 'garment-tone' }));
  const item = suggestions(group)[0];
  const neutral = { ...product, colors: [{ ...product.colors[0], swatchId: 'black' }] };
  const next = addStageAddition(group, group[0].pageSectionKey, item, { product: neutral, makeId: () => 'added' });
  assert.ok(next.every(b => b.horizonBackgroundMode === 'reference'));
  const missing = { ...product, colors: [{ ...product.colors[0], images: [] }] };
  const reconciled = addStageAddition(group, group[0].pageSectionKey, item, { product: missing, makeId: () => 'added' });
  assert.ok(reconciled.every(b => b.horizonBackgroundMode === 'reference'));
});

test('R6 stale pale actions recheck row availability before adding', () => {
  const set = setFor('horizon-sequence'), group = makeGroup(set, 1), item = suggestions(group)[0];
  const full = makeGroup(set, 4);
  assert.equal(addStageAddition(full, full[0].pageSectionKey, item, { product }), full);
});
