import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer, transformWithEsbuild } from 'vite';
import react from '@vitejs/plugin-react';
import * as generation from '../../src/lib/generationExamples.js';
import { generationExampleSelectionPatch } from '../../src/lib/storyboardExampleSelection.js';
import { filterExamplesForModel } from '../../src/lib/identityScope.js';
import { isSourceBasedDetailRecipe } from '../../src/lib/detailRecommendations.js';
import { poseExampleDirectionCompatible } from '../../src/lib/storyboardTaxonomy.js';

const examples = Array.from({ length: 16 }, (_, index) => ({
  id: `gallery-${index + 1}`, rank: index + 1, cutType: 'horizon', shot: 'full',
  gender: 'women', applicableClothingTypes: ['top', 'outer'], variants: ['all'],
  direction: index === 13 ? 'side' : 'front', sideStyle: index === 13 ? 'threeQuarter' : null,
  thumb: `/gallery-${index + 1}.webp`,
}));
const catalogs = { genExamples: examples, shotTypes: [{ value: 'full' }, { value: 'medium' }] };
const baseProps = { catalogs, cut: 'horizon', shot: 'full', clothingType: 'top', gender: 'women', direction: 'front' };
const pages = generation.paginateGenerationGalleryItems(examples);

test('gallery pagination keeps all 16 items in 6, 6, 4 order and keeps one empty page', () => {
  assert.deepEqual(pages.map((page) => page.length), [6, 6, 4]);
  assert.deepEqual(pages.flat(), examples);
  assert.deepEqual(generation.paginateGenerationGalleryItems(examples.slice(0, 6)).map((page) => page.length), [6]);
  assert.deepEqual(generation.paginateGenerationGalleryItems([]), [[]]);
});

test('selected example starts on its page, and missing or absent selections start on page one', () => {
  assert.equal(typeof generation.generationGalleryPageForExample, 'function');
  assert.equal(generation.generationGalleryPageForExample(pages, examples[13].id), 2);
  for (const id of ['missing', null, undefined]) {
    assert.equal(generation.generationGalleryPageForExample(pages, id), 0);
  }
  assert.equal(generation.generationGalleryPageForExample([[]], examples[13].id), 0);
});

test('keyboard paging and clamping stop at the ends without wrapping', () => {
  assert.equal(typeof generation.generationGalleryPageForKey, 'function');
  assert.equal(typeof generation.clampGenerationGalleryPage, 'function');
  assert.equal(generation.generationGalleryPageForKey(0, 'ArrowRight', 3), 1);
  assert.equal(generation.generationGalleryPageForKey(2, 'ArrowRight', 3), 2);
  assert.equal(generation.generationGalleryPageForKey(1, 'ArrowLeft', 3), 0);
  assert.equal(generation.generationGalleryPageForKey(0, 'ArrowLeft', 3), 0);
  for (const key of ['Enter', ' ', 'ArrowDown', 'Tab']) {
    assert.equal(generation.generationGalleryPageForKey(1, key, 3), 1);
  }
  assert.equal(generation.clampGenerationGalleryPage(2, 2), 1);
  assert.equal(generation.clampGenerationGalleryPage(-1, 3), 0);
  assert.equal(generation.clampGenerationGalleryPage(2, 1), 0);
});

let vite;
let loadedGuide;
async function loadGuide() {
  loadedGuide ||= (async () => {
    vite = await createServer({
      configFile: false, plugins: [react()],
      resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
      server: { middlewareMode: true, watch: null }, appType: 'custom', logLevel: 'silent',
    });
    return (await vite.ssrLoadModule('/src/features/storyboard/Storyboard.jsx')).MoodGuide;
  })();
  return loadedGuide;
}
after(async () => { await vite?.close(); });

test('real MoodGuide SSR renders three pages and marks the selected third page immediately', async () => {
  const MoodGuide = await loadGuide();
  const markup = renderToStaticMarkup(React.createElement(MoodGuide, { ...baseProps, exampleId: examples[13].id }));
  const pageMarkup = markup.split(/<div class="sb-expage"/).slice(1);
  assert.equal(pageMarkup.length, 3);
  assert.deepEqual(pageMarkup.map((page) => (page.match(/class="sb-excell/g) || []).length), [6, 6, 4]);
  assert.deepEqual(pageMarkup.map((page) => page.startsWith(' aria-hidden="true"')), [true, true, false]);
  assert.equal((markup.match(/tabindex="-1"/g) || []).length, 12);
  assert.match(pageMarkup[2], /class="sb-excell sel"[\s\S]*?src="\/gallery-14.webp"/);
  const dots = markup.match(/<button[^>]*aria-label="\d+페이지"[^>]*>/g) || [];
  assert.equal(dots.length, 3);
  assert.doesNotMatch(dots[0] + dots[1], /aria-current/);
  assert.match(dots[2], /aria-current="page"/);
  assert.match(markup, /aria-label="총 3페이지 중 3페이지"/);
  assert.match(markup, /aria-label="다음 예시 페이지" disabled=""/);
});

test('real MoodGuide SSR starts unselected and unknown IDs on page one and hides single-page controls', async () => {
  const MoodGuide = await loadGuide();
  for (const exampleId of [undefined, 'missing']) {
    const markup = renderToStaticMarkup(React.createElement(MoodGuide, { ...baseProps, exampleId }));
    assert.match(markup, /aria-label="1페이지" aria-current="page"/);
    assert.match(markup, /aria-label="이전 예시 페이지" disabled=""/);
  }
  for (const count of [0, 1, 6]) {
    const markup = renderToStaticMarkup(React.createElement(MoodGuide, {
      ...baseProps, catalogs: { ...catalogs, genExamples: examples.slice(0, count) },
    }));
    assert.equal((markup.match(/class="sb-expage"/g) || []).length, 1);
    assert.doesNotMatch(markup, /sb-excontrols/);
    assert.equal(markup.includes('sb-exempty'), count === 0);
  }
});

const source = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
const guideSource = source.slice(source.indexOf('export function MoodGuide('), source.indexOf('function Inspector('));
const { code } = await transformWithEsbuild(guideSource.replace('export function', 'function'), 'gallery-paging.jsx', { jsx: 'transform' });
const nodes = (tree, predicate) => {
  if (Array.isArray(tree)) return tree.flatMap((child) => nodes(child, predicate));
  if (!tree || typeof tree !== 'object') return [];
  return [...(predicate(tree) ? [tree] : []), ...nodes(tree.props?.children, predicate)];
};
const byClass = (tree, name) => nodes(tree, (node) => node.props?.className?.split(' ').includes(name));
const currentPage = (tree) => nodes(tree, (node) => node.props?.['aria-current'] === 'page')[0]?.props['aria-label'];

// Run the real component across renders, including its effects and event handlers.
// Only React's hook storage and browser scroll/focus APIs are supplied here; no DOM dependency.
function galleryHarness(initialProps = {}) {
  let props = { ...baseProps, ...initialProps }, cursor = 0, dirty = false, effects = [];
  const slots = [], scrolls = [], focuses = [], timers = new Map(), observers = new Set(), retries = new Map();
  let timerId = 0;
  const document = { activeElement: null };
  let pageElements = [];
  const region = { focus(options) { focuses.push(options); document.activeElement = region; } };
  const grid = {
    ownerDocument: document, scrollLeft: 0,
    querySelectorAll: () => pageElements,
    contains: (cell) => pageElements.some((page) => page.contains(cell)),
    scrollTo(options) {
      scrolls.push(options);
      if (options.behavior !== 'smooth') grid.scrollLeft = options.left;
    },
  };
  const same = (left, right) => left && right && left.length === right.length && left.every((value, index) => Object.is(value, right[index]));
  const useMemo = (factory, deps) => {
    const index = cursor++;
    if (!same(slots[index]?.deps, deps)) slots[index] = { deps, value: factory() };
    return slots[index].value;
  };
  const dependencies = {
    ...generation, filterExamplesForModel, isSourceBasedDetailRecipe, poseExampleDirectionCompatible,
    React: { ...React, useMemo }, ExampleThumb: () => null, Icon: () => null, ShotSegment: () => null,
    MineImageTab: () => null, MINE_SHOT_OPTION: { value: 'mine' },
    setTimeout(callback) { timers.set(++timerId, callback); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    MutationObserver: class {
      constructor(callback) { this.callback = callback; }
      observe() { observers.add(this); }
      disconnect() { observers.delete(this); }
    },
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
      return [slots[index], (next) => {
        const value = typeof next === 'function' ? next(slots[index]) : next;
        if (!Object.is(value, slots[index])) { slots[index] = value; dirty = true; }
      }];
    },
    useRef(initial) { const index = cursor++; return slots[index] ||= { current: initial }; },
    useEffect(effect, deps) {
      const index = cursor++;
      const previous = slots[index];
      if (!same(previous?.deps, deps)) {
        const record = { deps }; slots[index] = record;
        effects.push(() => { previous?.cleanup?.(); record.cleanup = effect(); });
      }
    },
  };
  const MoodGuide = new Function(...Object.keys(dependencies), `${code}; return MoodGuide;`)(...Object.values(dependencies));
  return {
    grid, scrolls, focuses,
    flushScroll() {
      const callbacks = [...timers.values()];
      timers.clear();
      callbacks.forEach((callback) => callback());
    },
    failThumb(exampleId) {
      const retry = { tabIndex: 0 };
      retries.set(exampleId, retry);
      observers.forEach((observer) => observer.callback());
      return retry;
    },
    unmount() {
      slots.forEach((slot) => slot?.cleanup?.());
      assert.equal(timers.size, 0, 'unmount clears pending scroll settlement');
      assert.equal(observers.size, 0, 'unmount disconnects descendant focus observers');
    },
    render(changes = {}) {
      props = { ...props, ...changes };
      for (let attempt = 0; attempt < 10; attempt += 1) {
        cursor = 0; dirty = false; effects = [];
        const tree = MoodGuide(props);
        pageElements = byClass(tree, 'sb-expage').map((page, index) => ({
          offsetLeft: index * 310,
          getAttribute: (name) => name === 'aria-hidden' ? String(page.props['aria-hidden']) : null,
          querySelectorAll: () => byClass(page, 'sb-excell').flatMap((cell) => [
            { tabIndex: cell.props.tabIndex }, ...(retries.has(cell.key) ? [retries.get(cell.key)] : []),
          ]),
          contains: (cell) => nodes(page, (node) => node.key === cell?.exampleId).length > 0,
        }));
        const gridNode = byClass(tree, 'sb-exgrid')[0];
        if (gridNode?.ref) gridNode.ref.current = grid;
        const regionNode = byClass(tree, 'sb-exgallery')[0];
        if (regionNode?.ref) regionNode.ref.current = region;
        observers.forEach((observer) => observer.callback());
        effects.forEach((effect) => effect());
        if (!dirty) return tree;
      }
      assert.fail('gallery did not settle after 10 renders');
    },
    focusCell(exampleId) {
      const cell = { exampleId, closest: (selector) => selector === '.sb-excell' ? cell : null };
      document.activeElement = cell;
    },
  };
}

test('picking a third-page photo updates its direction and sideStyle without moving the gallery', () => {
  let block = { ...baseProps, source: 'ai', cutType: 'horizon', exampleId: examples[0].id };
  const changes = [];
  const h = galleryHarness({ ...block, onExampleChange(id, refScope) {
    changes.push(id);
    block = { ...block, ...generationExampleSelectionPatch(block, examples.find((example) => example.id === id), { refScope }).patch };
  } });
  let tree = h.render();
  nodes(tree, (node) => node.props?.['aria-label'] === '3페이지')[0].props.onClick();
  tree = h.render();
  assert.equal(currentPage(tree), '3페이지');
  assert.deepEqual(changes, [], 'browsing never selects a photo');
  const scrollCount = h.scrolls.length;
  nodes(tree, (node) => node.key === examples[13].id)[0].props.onClick();
  tree = h.render(block);
  assert.equal(block.direction, 'side');
  assert.equal(block.sideStyle, 'threeQuarter');
  assert.equal(currentPage(tree), '3페이지');
  assert.equal(h.scrolls.length, scrollCount, 'selection on the visible page never scrolls');
  tree = h.render({ direction: 'back', sideStyle: null });
  assert.equal(currentPage(tree), '3페이지');
  assert.equal(h.scrolls.length, scrollCount, 'direction-only changes never reset paging');
});

test('mounting, condition changes, outside-page selection and shrinking lists align to the right page', () => {
  const h = galleryHarness({ exampleId: examples[13].id });
  let tree = h.render();
  assert.equal(currentPage(tree), '3페이지');
  assert.deepEqual(h.scrolls[0], { left: 620, behavior: 'auto' });
  tree = h.render({ clothingType: 'outer' });
  assert.equal(currentPage(tree), '3페이지');
  assert.equal(h.scrolls.at(-1).behavior, 'auto');
  tree = h.render({ exampleId: examples[0].id });
  assert.equal(currentPage(tree), '1페이지');
  nodes(tree, (node) => node.props?.['aria-label'] === '3페이지')[0].props.onClick();
  tree = h.render();
  tree = h.render({ catalogs: { ...catalogs, genExamples: examples.slice(0, 8) } });
  assert.equal(currentPage(tree), '2페이지', 'a shorter list clamps to its last page, not the selection');
  tree = h.render({ shot: 'medium' });
  assert.equal(byClass(tree, 'sb-expage')[0].props['aria-hidden'], false);
  assert.equal(h.scrolls.at(-1).left, 0);
});

test('each gallery condition returns to page one when the selected example leaves the list', () => {
  for (const changes of [{ cut: 'styling' }, { shot: 'medium' }, { clothingType: 'bottom' }, { gender: 'men' }]) {
    const h = galleryHarness({ exampleId: examples[13].id });
    assert.equal(currentPage(h.render()), '3페이지');
    const tree = h.render(changes);
    assert.equal(byClass(tree, 'sb-expage')[0].props['aria-hidden'], false);
    assert.equal(byClass(tree, 'sb-excell').length, 0);
    assert.equal(h.scrolls.at(-1).left, 0);
  }
});

test('the existing retry button selects the first available photo and returns to its page', () => {
  const stale = { ...examples[13], id: 'stale', applicableClothingTypes: ['bottom'] };
  const choices = [];
  const h = galleryHarness({
    catalogs: { ...catalogs, genExamples: [...examples, stale] }, exampleId: stale.id,
    onExampleChange: (...args) => choices.push(args),
  });
  let tree = h.render();
  nodes(tree, (node) => node.props?.['aria-label'] === '3페이지')[0].props.onClick();
  tree = h.render();
  assert.equal(currentPage(tree), '3페이지');
  byClass(tree, 'sb-example-retry')[0].props.onClick();
  assert.deepEqual(choices, [[examples[0].id, 'all']]);
  tree = h.render({ exampleId: choices[0][0] });
  assert.equal(currentPage(tree), '1페이지');
  assert.equal(byClass(tree, 'sb-example-retry').length, 0);
});

test('arrow keys and horizontal scrolling update the visible page and restore offscreen cell focus', () => {
  const changes = [];
  const h = galleryHarness({ onExampleChange: (id) => changes.push(id) });
  let tree = h.render();
  let prevented = 0;
  h.focusCell(examples[0].id);
  byClass(tree, 'sb-exgallery')[0].props.onKeyDown({ key: 'ArrowRight', preventDefault() { prevented += 1; } });
  tree = h.render();
  assert.equal(currentPage(tree), '2페이지');
  assert.equal(prevented, 1);
  assert.deepEqual(h.focuses, [{ preventScroll: true }]);
  assert.ok(byClass(byClass(tree, 'sb-expage')[0], 'sb-excell').every((cell) => cell.props.tabIndex === -1));
  byClass(tree, 'sb-exgallery')[0].props.onKeyDown({ key: ' ', preventDefault() { prevented += 1; } });
  tree = h.render();
  assert.equal(currentPage(tree), '2페이지');
  assert.equal(prevented, 1, 'Space remains the button selection key');
  h.grid.scrollLeft = 580;
  byClass(tree, 'sb-exgrid')[0].props.onScroll();
  h.flushScroll();
  tree = h.render();
  assert.equal(currentPage(tree), '3페이지');
  assert.deepEqual(changes, []);
});

test('intermediate smooth scroll events preserve repeated paging until scrolling settles', () => {
  const h = galleryHarness();
  let tree = h.render();
  const arrow = () => byClass(tree, 'sb-exgallery')[0].props.onKeyDown({ key: 'ArrowRight', preventDefault() {} });
  arrow();
  tree = h.render();
  assert.equal(currentPage(tree), '2페이지');
  h.grid.scrollLeft = 30;
  byClass(tree, 'sb-exgrid')[0].props.onScroll();
  tree = h.render();
  assert.equal(currentPage(tree), '2페이지', 'an intermediate event must not undo the requested page');
  arrow();
  tree = h.render();
  assert.equal(currentPage(tree), '3페이지');
  assert.equal(h.scrolls.at(-1).left, 620);
  h.grid.scrollLeft = 310;
  byClass(tree, 'sb-exgrid')[0].props.onScroll();
  tree = h.render();
  assert.equal(currentPage(tree), '3페이지');
  h.flushScroll();
  tree = h.render();
  assert.equal(currentPage(tree), '2페이지', 'an interrupted scroll settles to its actual nearest page');
});

test('failed offscreen thumbnails stay out of Tab order and become focusable on their visible page', () => {
  const h = galleryHarness();
  let tree = h.render();
  const retry = h.failThumb(examples[13].id);
  assert.equal(retry.tabIndex, -1, 'the retry added by ExampleThumb must stay outside hidden-page Tab order');
  nodes(tree, (node) => node.props?.['aria-label'] === '3페이지')[0].props.onClick();
  tree = h.render();
  assert.equal(retry.tabIndex, 0);
  nodes(tree, (node) => node.props?.['aria-label'] === '1페이지')[0].props.onClick();
  tree = h.render();
  assert.equal(retry.tabIndex, -1);
  h.grid.scrollLeft = 30;
  byClass(tree, 'sb-exgrid')[0].props.onScroll();
  h.unmount();
});

test('offscreen photos retain the existing native drag payload and never select while dragging', () => {
  const drags = [], choices = [], payloads = [];
  const h = galleryHarness({ onExampleDrag: (id) => drags.push(id), onExampleChange: (id) => choices.push(id) });
  const tree = h.render();
  const cell = nodes(tree, (node) => node.key === examples[13].id)[0];
  assert.equal(cell.props.tabIndex, -1);
  assert.equal(cell.props.draggable, true);
  const dataTransfer = { setData: (...args) => payloads.push(args) };
  cell.props.onDragStart({ dataTransfer });
  cell.props.onDragEnd();
  assert.equal(dataTransfer.effectAllowed, 'copy');
  assert.deepEqual(payloads, [['text/example-id', examples[13].id]]);
  assert.deepEqual(drags, [examples[13].id, null]);
  assert.deepEqual(choices, []);
});

test('gallery CSS restores horizontal snap and top-aligned 3 by 2 pages without wheel or mouse paging', () => {
  const css = readFileSync(new URL('../../src/styles/features.css', import.meta.url), 'utf8');
  const grid = css.match(/\.sb-exgrid\s*\{([^}]+)\}/)?.[1] || '';
  const page = css.match(/\.sb-expage\s*\{([^}]+)\}/)?.[1] || '';
  assert.match(grid, /display:\s*flex/);
  assert.match(grid, /align-items:\s*flex-start/);
  assert.match(grid, /overflow-x:\s*auto/);
  assert.match(grid, /overflow-y:\s*hidden/);
  assert.match(grid, /overscroll-behavior-x:\s*contain/);
  assert.match(grid, /scroll-snap-type:\s*x mandatory/);
  assert.match(page, /grid-template-columns:\s*repeat\(3,/);
  assert.match(page, /grid-template-rows:\s*repeat\(2,/);
  assert.match(page, /flex:\s*0 0 100%/);
  assert.match(page, /scroll-snap-stop:\s*always/);
  assert.doesNotMatch(css, /\.sb-exsection|\.sb-expage-label|\.sb-expage-cells/);
  assert.doesNotMatch(guideSource, /onWheel|onMouseDown|onPointerDown|\binert\s*=/);
  assert.match(guideSource, /limit:\s*\(catalogs\.genExamples \|\| \[\]\)\.length/);
});
