import test, { before } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { transformWithEsbuild } from 'vite';
import { canPlanSimilarCut } from '../../src/lib/storyboardSimilarCuts.js';
import { detailTargetPresentation, isProductDetail } from '../../src/lib/detailRecommendations.js';
import { generationExampleImageSources } from '../../src/lib/generationExamples.js';

const source = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
const styles = readFileSync(new URL('../../src/styles/sellerDesign.css', import.meta.url), 'utf8');
const root = { id: 'root', source: 'ai', cutType: 'horizon', exampleId: 'example', thumb: '/example.jpg' };
let cardComponents;
let controls;

before(async () => {
  const jsx = source.slice(source.indexOf('function StoryboardMedia('), source.indexOf('function StoryboardStack('));
  const { code } = await transformWithEsbuild(jsx, 'cards.jsx', { loader: 'jsx' });
  cardComponents = new Function('scope', `with (scope) { ${code}
    return { StoryboardMedia, StoryboardCard, StoryboardFrame, StoryboardMoodGrid }; }`)({
    React, canPlanSimilarCut, detailTargetPresentation, isProductDetail, generationExampleImageSources,
    SimilarCutControlsContext: {}, useContext: () => controls,
    useRef: () => ({ current: null }), useState: () => [false, () => {}], useLayoutEffect: () => {},
    storyboardPreviewSource: block => block.thumb, cutNumber: (index, total) => `${index}/${total}`,
    Icon: 'svg', BundleSwapArrows: 'swap-arrows', StoryboardCardActions: 'card-actions',
    StoryboardCaption: 'caption', SelectionRing: 'selection-ring',
  });
});

function nodes(tree) {
  if (!React.isValidElement(tree)) return [];
  return [tree, ...React.Children.toArray(tree.props.children).flatMap(nodes)];
}
const hasClass = (node, name) => node.props.className?.split(' ').includes(name);
const findClass = (tree, name) => nodes(tree).find(node => hasClass(node, name));
const text = tree => typeof tree === 'string' || typeof tree === 'number' ? String(tree)
  : React.isValidElement(tree) ? React.Children.toArray(tree.props.children).map(text).join('') : '';
function media(block = root, { locked = false, counts = new Map(), addSimilar = () => {}, ...props } = {}) {
  controls = { locked, counts, addSimilar };
  return cardComponents.StoryboardMedia({ block, catalogs: {}, index: 6, total: 10, ...props });
}

test('only eligible original cards offer a plain-text similar-cut button with the cut number', () => {
  for (const cutType of ['horizon', 'styling', 'mirror']) {
    const button = findClass(media({ ...root, cutType }), 'sb-similar-oncard');
    assert.ok(button, cutType);
    assert.equal(button.type, 'button');
    assert.equal(text(button), '비슷한 컷 추가');
    assert.equal(button.props['aria-label'], '6번 컷과 비슷한 컷 추가');
    assert.equal(nodes(button).length, 1, '버튼에 아이콘을 넣지 않는다');
    assert.equal(button.props.tabIndex, undefined, '숨겨진 버튼도 기본 Tab 순서에 남는다');
    assert.equal(button.props.draggable, false);
  }
  for (const block of [
    { ...root, similarSourceBlockId: 'original' }, { ...root, source: 'mine' },
    { ...root, cutType: 'product' }, { ...root, cutType: null }, { ...root, exampleId: null },
  ]) assert.equal(findClass(media(block), 'sb-similar-oncard'), undefined);
});

test('similar-cut clicks call the existing action with the original ID and isolate card events', () => {
  const added = [];
  const button = findClass(media(root, { addSimilar: id => added.push(id) }), 'sb-similar-oncard');
  for (const name of ['onPointerDown', 'onMouseDown', 'onClick', 'onDragStart']) {
    let stopped = false, prevented = false;
    assert.equal(typeof button.props[name], 'function', name);
    button.props[name]({ stopPropagation: () => { stopped = true; }, preventDefault: () => { prevented = true; } });
    assert.ok(stopped, `${name} must not reach card selection or drag handlers`);
    if (name === 'onMouseDown' || name === 'onDragStart') assert.ok(prevented, `${name} must cancel native dragging`);
  }
  assert.deepEqual(added, ['root']);
  for (const key of ['Enter', ' ']) {
    let stopped = false;
    button.props.onKeyDown({ key, stopPropagation: () => { stopped = true; } });
    assert.ok(stopped, '키보드 활성화가 카드 선택 처리에 전달되지 않는다');
  }
  assert.equal(findClass(media(root, { locked: true }), 'sb-similar-oncard').props.disabled, true);
});

test('single, paired and four-cut layouts all render the same per-card controls', () => {
  const items = [root, { ...root, id: 'child', similarSourceBlockId: 'root' },
    { ...root, id: 'product', cutType: 'product' }, { ...root, id: 'mirror', cutType: 'mirror' }]
    .map((block, i) => ({ block, index: i + 1 }));
  controls = { locked: true, counts: new Map([['root', 1]]), addSimilar: () => {} };
  const shared = { total: 4, catalogs: {}, colorOpts: [], selectedId: 'root', locked: true,
    dragFor: () => ({}), onSelect: () => {}, onDuplicate: () => {}, onDelete: () => {} };
  for (const [name, tree, expected] of [
    ['single', cardComponents.StoryboardCard({ ...shared, item: items[0], selected: true, locked: false }), 1],
    ['pair', cardComponents.StoryboardFrame({ ...shared, items: items.slice(0, 2) }), 1],
    ['grid', cardComponents.StoryboardMoodGrid({ ...shared, items }), 2],
  ]) {
    const mediaNodes = nodes(tree).filter(node => node.type === cardComponents.StoryboardMedia);
    const buttons = mediaNodes.map(node => cardComponents.StoryboardMedia(node.props))
      .map(tree => findClass(tree, 'sb-similar-oncard')).filter(Boolean);
    assert.equal(buttons.length, expected, name);
    assert.ok(buttons.every(button => button.props.disabled), '선택 카드의 흐림 여부와 잠금은 별개다');
  }
});

test('original badge counts its own followups, while a followup has an icon-free pending badge', () => {
  const start = source.indexOf('  const similarCutCounts =');
  assert.ok(start >= 0, '원본 ID별 개수 계산이 있어야 한다');
  const countCode = source.slice(start, source.indexOf('  const boardGroups =', start));
  const blocks = [root, { ...root, id: 'other' },
    { id: 'one', similarSourceBlockId: 'root' }, { id: 'two', similarSourceBlockId: 'root' },
    { id: 'three', similarSourceBlockId: 'other' }];
  const counts = new Function('blocks', `${countCode}\nreturn similarCutCounts;`)(blocks);
  assert.equal(counts.get('root'), 2);
  assert.equal(counts.get('other'), 1);
  const originalBadge = findClass(media(root, { counts }), 'sb-similar-badge');
  assert.equal(text(findClass(originalBadge, 'sb-similar-count-full')), '2개의 유사 컷 생성 예정');
  assert.equal(text(findClass(originalBadge, 'sb-similar-count-compact')), '유사 컷 2개');
  assert.equal(findClass(media(), 'sb-similar-badge'), undefined);
  const childBadge = findClass(media({ ...root, similarSourceBlockId: 'other' }, { counts }), 'sb-similar-badge');
  assert.equal(text(childBadge).replace(/\s+/g, ''), '유사컷생성예정');
  assert.equal(nodes(childBadge).some(node => node.type === 'svg'), false);
});

test('inspector contains neither the former add-button group nor its explanation', async () => {
  const start = source.indexOf('  const inspector =');
  const jsx = source.slice(start, source.indexOf('  const selectedOrdinal =', start));
  const { code } = await transformWithEsbuild(`${jsx}\nreturn inspector;`, 'inspector.jsx', { loader: 'jsx' });
  const scope = new Proxy({ React, selected: root, setPicker: null, canPlanSimilarCut,
    Inspector: 'inspector', catalogs: {}, catalogContext: { catalog: [] }, composeModeSeed: {} }, {
    has: (_, key) => key !== 'inspector', get: (target, key) => key === Symbol.unscopables ? undefined : target[key],
  });
  const tree = new Function('scope', `with (scope) { ${code} }`)(scope);
  assert.equal(findClass(tree, 'sb-similar-add'), undefined);
  assert.ok(!text(tree).includes('상세페이지를 만들 때 함께 생성해요.'));
  assert.doesNotMatch(styles, /\.sb-similar-add\b/);
});

test('card hover, selection and keyboard focus reveal the button, and narrow cards shorten both badges', () => {
  const buttonStyle = styles.match(/\.sb-page \.sb-similar-oncard\s*\{([^}]+)\}/)?.[1];
  assert.ok(buttonStyle);
  assert.match(buttonStyle, /opacity:\s*0/);
  assert.match(buttonStyle, /pointer-events:\s*none/);
  assert.match(buttonStyle, /height:\s*32px/);
  assert.match(buttonStyle, /font-size:\s*12\.5px/);
  assert.match(buttonStyle, /white-space:\s*nowrap/);
  assert.doesNotMatch(buttonStyle, /visibility:\s*hidden|display:\s*none/);
  for (const surface of ['sb-cutcard', 'sb-frame-half']) {
    for (const state of [':hover', '.selected', ':focus-within'])
      assert.ok(styles.includes(`.${surface}${state} .sb-similar-oncard`), `${surface}${state}`);
  }
  assert.match(styles, /@container \(max-width: 159px\)[^]*?\.sb-similar-badge small\s*\{\s*display:\s*none/);
  assert.match(styles, /@container \(max-width: 159px\)[^]*?\.sb-similar-count-full\s*\{\s*display:\s*none/);
  assert.match(styles, /@container \(max-width: 159px\)[^]*?\.sb-similar-count-compact\s*\{\s*display:\s*inline/);
});

test('narrow free originals keep all action targets, counts, similar buttons and lower markers apart', () => {
  const featureStyles = readFileSync(new URL('../../src/styles/features.css', import.meta.url), 'utf8');
  const declarations = (css, selector) => {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const rules = [...css.matchAll(new RegExp(`(?:^|\\n)\\s*${escaped}\\s*\\{([^}]+)\\}`, 'g'))];
    return Object.assign({}, ...rules.map(([, body]) => Object.fromEntries(body.split(';').flatMap(declaration => {
      const colon = declaration.indexOf(':');
      return colon < 0 ? [] : [[declaration.slice(0, colon).trim(), declaration.slice(colon + 1).trim()]];
    }))));
  };
  const freeSelector = '.sb-page .sb-cutcard:has(.sb-similar-count):has(.sb-move-wrap)';
  const actions = { ...declarations(featureStyles, '.sb-canvas-actions'),
    ...declarations(styles, `${freeSelector} .sb-canvas-actions`) };
  const target = declarations(featureStyles, '.sb-canvas-actions button');
  const count = { ...declarations(styles, '.sb-similar-badge'),
    ...declarations(styles, '.sb-page .sb-similar-count'),
    ...declarations(styles, `${freeSelector} .sb-similar-count`) };
  const button = declarations(styles, '.sb-page .sb-similar-oncard');
  const pose = declarations(featureStyles, '.sb-pose-variation-note');
  const matching = declarations(featureStyles, '.sb-match-overlay');
  assert.match(styles, /@container \(max-width: 148px\)/, '147px와 148px 사이의 소수 폭도 조작 줄 감김을 피한다');
  const markerSelector = '.sb-page :is(.sb-cutcard, .sb-frame-half):has(.sb-match-overlay, .sb-pose-variation-note, .sb-out-of-scope) .sb-similar-oncard';
  const markerButton = { ...button, ...declarations(styles, markerSelector) };
  const px = value => parseFloat(value);
  const paddingY = rule => px(rule.padding.split(' ')[0]);
  for (const width of [136, 138, 147, 147 + 1 / 3, 147 + 2 / 3, 148]) {
    const height = width * 4 / 3;
    const gap = px(actions.gap);
    const actionWidth = actions.width ? px(actions.width) : width - 12;
    assert.ok(actionWidth <= width, '조작 버튼이 사진 밖으로 잘리지 않는다');
    const columns = Math.floor((actionWidth + gap) / (px(target.width) + gap));
    const actionBottom = px(actions.top) + Math.ceil(3 / columns) * px(target.height) + (Math.ceil(3 / columns) - 1) * gap;
    const countBottom = px(count.top) + px(count['line-height']) + paddingY(count) * 2;
    assert.ok(actionBottom <= px(count.top), `${width}px: 이동·복제·삭제와 개수 배지 겹침`);
    for (const marker of [null, 'pose', 'match']) {
      const currentButton = marker ? markerButton : button;
      const buttonTop = height - px(currentButton.bottom) - px(button.height);
      assert.ok(countBottom <= buttonTop, `${width}px ${marker}: 개수 배지와 버튼 겹침`);
      if (marker) {
        const markerTop = marker === 'pose'
          ? height - px(pose.bottom) - paddingY(pose) * 2 - px(pose['font-size']) * px(pose['line-height'])
          : height - px(matching.bottom) - 22 - paddingY(matching) * 2;
        assert.ok(height - px(currentButton.bottom) <= markerTop, `${width}px ${marker}: 버튼과 하단 표시 겹침`);
      }
    }
  }
});
