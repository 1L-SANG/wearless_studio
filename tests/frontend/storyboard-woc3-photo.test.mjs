import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import * as gallery from '../../src/lib/pageTemplates/storyboardGallery.js';
import * as generation from '../../src/lib/generationExamples.js';
import { filterExamplesForModel, filterSpaceSetsForModel } from '../../src/lib/identityScope.js';
import { storyboardSpaceSetsFor, withStoryboardSpaceSetExamples } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { spaceSetDisplayName } from '../../src/lib/spaceSetDisplayNames.js';
import { isSourceBasedDetailRecipe } from '../../src/lib/detailRecommendations.js';
import { hookHarness, loadStoryboardComponent, nodes, textOf } from './helpers/storyboardComponents.mjs';

const photos = ['a', 'b', 'c', 'd'].map(id => ({ id, shot: 'full', cutType: 'styling' }));
const demoSets = [{ id: 'set-a', name: 'A', members: [{ exampleId: 'b' }, { exampleId: 'c' }] }];

test('single gallery groups eligible set members in place and uses the selected photo as its cover', () => {
  assert.equal(typeof gallery.groupGalleryExamples, 'function');
  const entries = gallery.groupGalleryExamples(photos, demoSets, { selectedId: 'c' });
  assert.deepEqual(entries.map(item => [item.id, item.kind]), [['a', 'example'], ['set-a', 'stack'], ['d', 'example']]);
  assert.deepEqual(entries[1].members.map(item => item.id), ['b', 'c']);
  assert.equal(entries[1].example.id, 'c');
  assert.equal(gallery.groupGalleryExamples(photos, demoSets)[1].example.id, 'b');
  assert.deepEqual(gallery.groupGalleryExamples([photos[2]], demoSets).map(item => [item.id, item.kind]), [['c', 'example']]);
});

test('gallery sampling limits grouped entries to eight pages and pins an eligible selection before sampling', () => {
  assert.equal(typeof gallery.sampleGalleryEntries, 'function');
  const entries = Array.from({ length: 64 }, (_, i) => ({ id: `entry-${i}`, kind: 'example', example: { id: `photo-${i}` } }));
  entries[63] = { id: 'selected-set', kind: 'stack', members: [{ id: 'selected' }] };
  const sampled = gallery.sampleGalleryEntries(entries, { selectedId: 'selected', seed: 14 });
  assert.equal(sampled.length, 48);
  assert.equal(sampled[0].id, 'selected-set');
  assert.equal(new Set(sampled.map(item => item.id)).size, 48);
  assert.deepEqual(gallery.sampleGalleryEntries(entries, { selectedId: 'selected', seed: 14 }), sampled);
  assert.notDeepEqual(gallery.sampleGalleryEntries(entries, { selectedId: 'selected', seed: 15 }).map(item => item.id), sampled.map(item => item.id));
  assert.equal(gallery.sampleGalleryEntries(entries, { selectedId: 'outside-filters', seed: 14 }).some(item => item.id === 'outside-filters'), false);
  assert.deepEqual(entries.slice(0, 2).map(item => item.id), ['entry-0', 'entry-1'], 'sampling never mutates the catalog');
});

const catalog = withStoryboardSpaceSetExamples({ genExamples: [] });
const set = storyboardSpaceSetsFor({ gender: 'women', clothingType: 'top' }).find(item => item.setType === 'styling' && item.members.length > 2);
const props = { catalogs: catalog, cut: 'styling', shot: 'full', clothingType: 'top', gender: 'women',
  exampleId: set.members[0].exampleId, gallerySeedFactory: () => 2 };
const buttons = tree => nodes(tree).filter(node => node.type === 'button');
const byText = (tree, text) => buttons(tree).find(node => textOf(node) === text);
const hasClass = (node, value) => node.props?.className?.split(' ').includes(value);

async function guideHarness(extra = {}) {
  const h = hookHarness();
  const Component = await loadStoryboardComponent('StoryboardExampleGallery.jsx', 'MoodGuide', { ...h.hooks,
    ...gallery, ...generation, filterExamplesForModel, filterSpaceSetsForModel, storyboardSpaceSetsFor, spaceSetDisplayName,
    isSourceBasedDetailRecipe, ExampleThumb: 'example-thumb', MineImageTab: 'mine-images', ShotSegment: 'shots',
    StoryboardStackPopup: 'stack-popup', Icon: 'svg' });
  let current = { ...props, ...extra };
  return { render(next = {}, attach) { current = { ...current, ...next }; return h.render(Component, current, attach); }, close: h.close };
}

test('ordinary set members keep all alternative gallery controls physically absent until expanded', async () => {
  const h = await guideHarness({ spaceContext: { set, siblings: [{ id: 'one' }, { id: 'two' }] } });
  let tree = h.render();
  assert.match(textOf(tree), /같은 장소의 다른 사진/);
  assert.equal(nodes(tree).some(node => hasClass(node, 'sb-exgallery')), false);
  assert.equal(nodes(tree).some(node => node.props?.['aria-label'] === '장소' || node.props?.['aria-label'] === '구도'), false);
  assert.equal(nodes(tree).some(node => node.type?.name === 'PlaceTabs'), false);
  assert.equal(byText(tree, '단일컷'), undefined);
  byText(tree, '이 컷만 다른 사진으로 바꾸기').props.onClick(); tree = h.render();
  assert.match(textOf(tree), /다른 장소 사진/);
  assert.ok(nodes(tree).some(node => hasClass(node, 'sb-exgallery')));
  byText(tree, '접기').props.onClick(); tree = h.render();
  assert.equal(nodes(tree).some(node => hasClass(node, 'sb-exgallery')), false);
  h.close();
});

test('D204 singleton instances and photos outside their original set start with alternatives expanded', async () => {
  for (const extra of [
    { spaceContext: { set, siblings: [{ id: 'only' }] } },
    { spaceContext: { set, siblings: [{ id: 'one' }, { id: 'two' }] }, exampleId: 'outside-original-set' },
  ]) {
    const h = await guideHarness(extra), tree = h.render();
    assert.match(textOf(tree), /다른 장소 사진/);
    assert.ok(nodes(tree).some(node => hasClass(node, 'sb-exgallery'))); h.close();
  }
});

test('single mode opens a captionless stack dialog while set mode retains inline set expansion', async () => {
  const h = await guideHarness();
  let tree = h.render();
  const setStack = buttons(tree).find(node => node.props['aria-label']?.includes('컷 펼치기'));
  assert.ok(setStack); setStack.props.onClick(); tree = h.render();
  assert.ok(byText(tree, '전체 예시')); byText(tree, '전체 예시').props.onClick(); tree = h.render();
  byText(tree, '단일컷').props.onClick(); tree = h.render();
  const stack = buttons(tree).find(node => node.props['aria-haspopup'] === 'dialog');
  assert.ok(stack);
  const wrapper = nodes(tree).find(node => node.props?.children?.includes?.(stack));
  assert.equal(nodes(wrapper).some(node => node.type === 'small'), false);
  assert.equal(stack.props['aria-expanded'], false);
  stack.props.onClick({ currentTarget: { id: 'trigger' }, detail: 0 }); tree = h.render();
  const popup = nodes(tree).find(node => node.type === 'stack-popup');
  assert.ok(popup); assert.equal(popup.props.keyboardOpen, true); assert.ok(popup.props.entry.members.length > 1);
  assert.equal(buttons(tree).find(node => node.props['aria-haspopup'] === 'dialog' && node.key === stack.key).props['aria-expanded'], true);
  h.close();
});

test('gallery session order survives paging and reseeds when filters change or the gallery reopens', async () => {
  let seeds = 0;
  const h = await guideHarness({ exampleId: null, gallerySeedFactory: () => ++seeds });
  let tree = h.render(); const initialSeedCalls = seeds;
  const firstOrder = buttons(tree).filter(node => hasClass(node, 'sb-excell')).map(node => node.key);
  const page = buttons(tree).find(node => node.props['aria-label'] === '2페이지'); assert.ok(page); page.props.onClick(); tree = h.render();
  assert.equal(seeds, initialSeedCalls);
  assert.deepEqual(buttons(tree).filter(node => hasClass(node, 'sb-excell')).map(node => node.key), firstOrder);
  byText(tree, '전신').props.onClick(); tree = h.render(); assert.equal(seeds, initialSeedCalls + 1);
  const places = nodes(tree).find(node => node.type?.name === 'PlaceTabs');
  places.props.onChange('home'); tree = h.render(); assert.equal(seeds, initialSeedCalls + 2);
  places.props.onChange('all'); tree = h.render();
  const beforeGender = seeds;
  tree = h.render({ gender: 'men' }); assert.equal(seeds, beforeGender + 1);
  tree = h.render({ cut: 'horizon' }); assert.equal(seeds, beforeGender + 2);
  tree = h.render({ spaceContext: { set, siblings: [{}, {}] }, cut: 'styling', exampleId: set.members[0].exampleId });
  tree = h.render(); const beforeOpen = seeds;
  byText(tree, '이 컷만 다른 사진으로 바꾸기').props.onClick(); tree = h.render();
  assert.equal(seeds, beforeOpen + 1); h.close();
});

test('keyboard choosing restores the stack immediately and again after an asynchronous selection reorder', async () => {
  const focused = [], h = await guideHarness();
  const attach = tree => {
    const region = nodes(tree).find(node => hasClass(node, 'sb-exgallery'));
    if (region?.ref) region.ref.current = { focus() {}, querySelectorAll: () => buttons(tree)
      .filter(node => node.props['data-storyboard-stack']).map(node => ({ dataset: { storyboardStack: node.props['data-storyboard-stack'] },
        focus: () => focused.push(node.props['data-storyboard-stack']) })) };
  };
  let tree = h.render({}, attach); byText(tree, '단일컷').props.onClick(); tree = h.render({}, attach);
  const stack = buttons(tree).find(node => node.props['aria-haspopup'] === 'dialog');
  stack.props.onClick({ currentTarget: {}, detail: 0 }); tree = h.render({}, attach);
  const popup = nodes(tree).find(node => node.type === 'stack-popup');
  const chosen = popup.props.entry.members.find(item => item.id !== props.exampleId).id;
  popup.props.onClose(true, chosen); tree = h.render({}, attach);
  assert.deepEqual(focused, [stack.key], 'closing returns focus while persistence is pending');
  tree = h.render({ exampleId: chosen }, attach);
  assert.deepEqual(focused, [stack.key, stack.key], 'the selected stack remains focused after moving to page one'); h.close();
});

test('reopening alternatives returns to the first page of the fresh session sample', async () => {
  const h = await guideHarness({ spaceContext: { set, siblings: [{}, {}] }, exampleId: set.members[0].exampleId });
  let tree = h.render(); byText(tree, '이 컷만 다른 사진으로 바꾸기').props.onClick(); tree = h.render(); tree = h.render();
  buttons(tree).find(node => node.props['aria-label'] === '2페이지').props.onClick(); tree = h.render();
  byText(tree, '접기').props.onClick(); tree = h.render();
  byText(tree, '이 컷만 다른 사진으로 바꾸기').props.onClick(); tree = h.render(); tree = h.render();
  assert.equal(buttons(tree).find(node => node.props['aria-current'] === 'page').props['aria-label'], '1페이지'); h.close();
});

test('popup geometry prefers above, flips below, clamps horizontally and scrolls the drawer if both sides are short', () => {
  assert.equal(typeof gallery.stackPopupPlacement, 'function');
  assert.equal(typeof gallery.stackPopupColumns, 'function');
  assert.deepEqual([2, 4, 5, 6, 7, 8, 9].map(count => gallery.stackPopupColumns(count)), [2, 4, 3, 3, 4, 4, 4]);
  const base = { gallery: { left: 100, right: 580, width: 480 }, viewport: { top: 0, bottom: 800 }, width: 432, height: 300, scrollTop: 200 };
  let placed = gallery.stackPopupPlacement({ ...base, anchor: { top: 420, bottom: 620, left: 480, width: 150 } });
  assert.deepEqual(placed, { side: 'top', left: 148, top: 112, pointer: 407, scrollBy: 0 });
  placed = gallery.stackPopupPlacement({ ...base, anchor: { top: 50, bottom: 250, left: 100, width: 150 } });
  assert.equal(placed.side, 'bottom'); assert.equal(placed.left, 100); assert.equal(placed.top, 258);
  placed = gallery.stackPopupPlacement({ ...base, scrollTop: 0, height: 390, anchor: { top: 300, bottom: 500, left: 265, width: 150 } });
  assert.equal(placed.side, 'bottom'); assert.equal(placed.scrollBy, 106);
  assert.equal(gallery.stackPopupColumns(7, 240), 2);
});

test('popup photo frames retain the specified 8px radius and the drawer popup has a no-anchor fallback', () => {
  const css = readFileSync(new URL('../../src/features/storyboard/StoryboardPanels.module.css', import.meta.url), 'utf8');
  const tokens = readFileSync(new URL('../../src/styles/tokens.css', import.meta.url), 'utf8');
  const frame = css.match(/\.stackPopupGrid \.example:global\(\.sb-excell\)\s*\{([^}]+)\}/)?.[1];
  assert.ok(frame);
  const radiusToken = frame.match(/border-radius:\s*var\((--[\w-]+)\)/)?.[1];
  const resolvedRadius = tokens.match(new RegExp(`${radiusToken}:\\s*([^;]+)`))?.[1];
  assert.equal(resolvedRadius, '8px');
  assert.match(css, /\.stackPopup\[data-column="0"\]/);
  assert.match(css, /\.stackPopup\[data-column="2"\]/);
  assert.match(css, /@supports \(anchor-name:/);
});

test('native popover shell lets its outside pointer paint while the photo grid keeps scrolling', () => {
  const css = readFileSync(new URL('../../src/features/storyboard/StoryboardPanels.module.css', import.meta.url), 'utf8');
  const shell = css.match(/\.stackPopup\s*\{([^}]+)\}/)?.[1] || '';
  const grid = css.match(/\.stackPopupGrid\s*\{([^}]+)\}/)?.[1] || '';
  // A native [popover] otherwise keeps the UA overflow:auto and clips the -6px pointer.
  assert.equal(shell.match(/\boverflow:\s*([^;]+)/)?.[1]?.trim() || 'auto', 'visible');
  assert.equal(grid.match(/\boverflow-y:\s*([^;]+)/)?.[1]?.trim(), 'auto');
});

async function popupHarness({ keyboardOpen = true, selectedId = 'b', native = false, animationFrame = false } = {}) {
  assert.ok(existsSync(new URL('../../src/features/storyboard/StoryboardStackPopup.jsx', import.meta.url)), 'stack picker component exists');
  const h = hookHarness(), listeners = new Map(), focused = [], closed = [], selected = [], dragged = [], frames = [];
  const doc = { activeElement: null, defaultView: { CSS: { supports: () => native }, addEventListener() {}, removeEventListener() {},
    ...(animationFrame ? { requestAnimationFrame: callback => frames.push(callback) } : {}) },
    addEventListener(type, listener) { listeners.set(type, listener); }, removeEventListener(type) { listeners.delete(type); } };
  const galleryElement = { contains: target => target?.inGallery === true, getBoundingClientRect: () => ({ left: 100, right: 580, width: 480 }) };
  const body = { scrollTop: 0, getBoundingClientRect: () => ({ top: 0, bottom: 800 }), addEventListener() {}, removeEventListener() {} };
  const trigger = { isConnected: true, inGallery: true, dataset: { storyboardStack: 'demo' },
    getBoundingClientRect: () => ({ top: 420, bottom: 620, left: 300, width: 150 }),
    closest: () => body, focus: () => { focused.push('stack'); doc.activeElement = trigger; } };
  const elements = photos.map(photo => ({ dataset: { exampleId: photo.id }, tabIndex: -1,
    focus() { focused.push(photo.id); doc.activeElement = this; }, scrollIntoView() {} }));
  const popup = { ownerDocument: doc, dataset: {}, offsetWidth: 432, offsetHeight: 300,
    contains: node => elements.includes(node), querySelectorAll: selector => selector === '[data-example-id]' ? elements : [],
    setAttribute() {}, removeAttribute() {}, getBoundingClientRect: () => ({ height: 300, width: 432 }),
    ...(native ? { showPopover() { popup.shown = true; }, hidePopover() { popup.shown = false; } } : {}) };
  const Component = await loadStoryboardComponent('StoryboardStackPopup.jsx', 'StoryboardStackPopup', { ...h.hooks, ...gallery,
    generationExampleImageSources: generation.generationExampleImageSources, spaceSetDisplayName,
    ExampleThumb: 'example-thumb', Icon: 'svg' });
  const popupProps = { entry: { id: 'demo', set: { id: 'demo', name: 'Demo' }, members: photos }, selectedId,
    keyboardOpen, anchor: trigger, galleryElement, row: 0, column: 1, usedExampleIds: new Set(['a']),
    onClose: back => closed.push(back), onChoose: id => selected.push(id), onDrag: (event, example) => dragged.push(example.id), onDragEnd() {} };
  return { listeners, elements, focused, closed, selected, dragged, trigger, popup, doc, frames,
    render: () => h.render(Component, popupProps, tree => { if (tree.ref) tree.ref.current = popup; }), close: h.close,
    event(key, target = doc.activeElement) { return { key, target, detail: 0, preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; }, stopImmediatePropagation() { this.immediate = true; } }; } };
}

test('real popup traps keyboard navigation, consumes Escape and Tab and restores its stack', async () => {
  const h = await popupHarness(); const tree = h.render();
  assert.equal(tree.props.role, 'dialog'); assert.match(tree.props['aria-label'], /4컷 중 한 컷 고르기/);
  assert.equal(h.doc.activeElement, h.elements[1]);
  for (const [key, id] of [['ArrowRight', 'c'], ['ArrowDown', 'c'], ['Home', 'a'], ['End', 'd'], ['ArrowLeft', 'c']]) {
    const event = h.event(key); h.listeners.get('keydown')(event);
    assert.equal(event.prevented, true); assert.equal(event.stopped, true); assert.equal(h.focused.at(-1), id);
    assert.equal(h.elements.filter(item => item.tabIndex === 0).length, 1);
  }
  const outsideFocus = h.event('focusin', {}); h.listeners.get('focusin')(outsideFocus);
  assert.equal(h.doc.activeElement, h.elements[2]);
  for (const key of ['Escape', 'Tab']) { const event = h.event(key); h.listeners.get('keydown')(event); assert.equal(event.stopped, true); }
  assert.deepEqual(h.closed, [true, true]); h.close(); assert.equal(h.listeners.size, 0);
});

test('dragging lets the browser capture its native drag image before removing the popup', async () => {
  const h = await popupHarness({ animationFrame: true }), tree = h.render(), sourceEvents = new Map();
  const tile = buttons(tree)[0];
  tile.props.onDragStart({ currentTarget: { addEventListener: (name, callback) => sourceEvents.set(name, callback) } });
  assert.deepEqual(h.dragged, ['a']); assert.deepEqual(h.closed, []);
  assert.equal(h.frames.length, 1); h.frames[0](); assert.deepEqual(h.closed, [false]);
  assert.equal(typeof sourceEvents.get('dragend'), 'function', 'the detached source retains native drag cleanup');
  h.close();
});

test('real popup swallows outside clicks, allows switching a sibling stack and closes on choose or drag', async () => {
  const h = await popupHarness({ native: false }); const tree = h.render();
  assert.equal(h.popup.dataset.floating, 'fallback', 'missing native popover remains usable in the gallery');
  const outside = h.event('click', { closest: () => null }); h.listeners.get('click')(outside);
  assert.equal(outside.prevented, true); assert.deepEqual(h.closed, [true]);
  const other = { inGallery: true, dataset: { storyboardStack: 'another' } };
  const switchEvent = h.event('click', { closest: () => other }); h.listeners.get('click')(switchEvent);
  assert.equal(switchEvent.prevented, undefined); assert.equal(h.closed.length, 1);
  const tile = buttons(tree).find(node => node.props['data-example-id'] === 'a');
  assert.equal(tile.props.draggable, true); assert.match(textOf(tile), /이미 사용/);
  tile.props.onClick(h.event('click')); assert.deepEqual(h.selected, ['a']); assert.equal(h.closed.at(-1), true);
  const count = h.closed.length; tile.props.onDragStart({}); assert.deepEqual(h.dragged, ['a']); assert.equal(h.closed.length, count + 1);
  h.close();
});
