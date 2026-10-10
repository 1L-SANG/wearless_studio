import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { transformWithEsbuild } from 'vite';
import * as taxonomy from '../../src/lib/storyboardTaxonomy.js';
import * as examples from '../../src/lib/generationExamples.js';
import * as selection from '../../src/lib/storyboardExampleSelection.js';
import * as details from '../../src/lib/detailRecommendations.js';

const photo = (slot, src = `/${slot}.jpg`) => ({ slot, src });
const product = { colors: [
  { id: 'base', isBase: true, images: [photo('Front'), photo('Front', '/second.jpg'), photo('Back')] },
  { id: 'extra', images: [photo('Front', '/extra.jpg')] },
  { id: 'details', images: [photo('Detail'), photo('BackDetail')] },
  { id: 'empty', images: [] },
  { id: 'back-only', images: [photo('Back')] },
] };
const catalogs = {
  productSourcePhotos: [{ value: 'front', slot: 'Front', label: '내 앞면 사진' }, { value: 'back', slot: 'Back', label: '내 뒷면 사진' }],
  models: [{ id: 'mA', gender: 'women' }],
  productShotTypes: [{ value: 'ghost', label: '고스트샷' }, { value: 'detail', label: '디테일샷' }],
  shotTypes: [{ value: 'full', label: '풀샷' }],
  genExamples: [{ id: 'ghost-front', cutType: 'product', shot: 'ghost', direction: 'front', applicableClothingTypes: ['top'], variants: ['all'] },
    { id: 'ghost-other', cutType: 'product', shot: 'ghost', direction: 'front', applicableClothingTypes: ['top'], variants: ['all'] }],
  creditCosts: { editorImage: 1 },
};
const loadPhotos = () => import('../../src/lib/productSourcePhotos.js');

test('고스트는 해당 색상의 첫 Front와 Back만 고르고 없는 면은 비운다', async () => {
  const { productSourcePhotos } = await loadPhotos();
  assert.deepEqual(productSourcePhotos(product, 'base', 'ghost'), { front: photo('Front'), back: photo('Back') });
  assert.deepEqual(productSourcePhotos(product, 'extra', 'ghost'), { front: photo('Front', '/extra.jpg'), back: null });
  assert.deepEqual(productSourcePhotos(product, 'details', 'ghost'), { front: null, back: null });
  assert.deepEqual(productSourcePhotos(product, 'missing', 'ghost'), { front: null, back: null });
});

test('대상 없는 디테일은 전체 사진을 우선하고 같은 면의 디테일 사진으로만 보완한다', async () => {
  const { productSourcePhotos } = await loadPhotos();
  assert.deepEqual(productSourcePhotos(product, 'base', 'detail'), { front: photo('Front'), back: photo('Back') });
  assert.deepEqual(productSourcePhotos(product, 'details', 'detail'), { front: photo('Detail'), back: photo('BackDetail') });
  assert.deepEqual(productSourcePhotos(product, 'empty', 'detail'), { front: null, back: null });
});

test('색상을 바꿔 고른 면이 없으면 있는 면으로 바꾸고 둘 다 없으면 보존한다', async () => {
  const { productSourceColorPatch } = await loadPhotos();
  const block = { cutType: 'product', shot: 'ghost', direction: 'back' };
  assert.deepEqual(productSourceColorPatch(block, product, 'extra'), { direction: 'front' });
  assert.deepEqual(productSourceColorPatch({ ...block, direction: 'front' }, product, 'back-only'), { direction: 'back' });
  assert.deepEqual(productSourceColorPatch(block, product, 'base'), {});
  assert.deepEqual(productSourceColorPatch(block, product, 'empty'), {});
  assert.deepEqual(productSourceColorPatch({ ...block, cutType: 'styling' }, product, 'extra'), {});
  const bound = { ...block, shot: 'detail', colorId: 'base', detailTargetId: 'back' };
  const changes = details.preserveDetailTargetBinding(bound, { colorId: 'extra' });
  assert.deepEqual(productSourceColorPatch({ ...bound, ...changes }, product, 'extra'), { direction: 'front' });
});

const React = { createElement: (type, props, ...children) => ({ type, props: { ...props, children: children.flat() } }) };
const nodes = (tree, predicate) => Array.isArray(tree) ? tree.flatMap(child => nodes(child, predicate))
  : tree && typeof tree === 'object' ? [...(predicate(tree) ? [tree] : []), ...nodes(tree.props?.children, predicate)] : [];
const evaluate = (code, scope) => new Function('scope', `with (scope) { ${code} }`)(scope);

test('원본 선택기는 두 3:4 사진 라디오와 카탈로그 이름, 선택 체크를 보여주고 고른 면을 전달한다', async () => {
  const module = await loadPhotos();
  const source = readFileSync(new URL('../../src/features/storyboard/ProductSourcePicker.jsx', import.meta.url), 'utf8');
  const body = source.slice(source.indexOf('export function ProductSourcePicker')).replace('export function', 'function');
  const { code } = await transformWithEsbuild(body + '\nreturn ProductSourcePicker;', 'picker.jsx', { loader: 'jsx' });
  const Picker = evaluate(code, { React, useId: () => 'test-picker', Icon: 'Icon', ...module });
  let chosen;
  const props = { block: { cutType: 'product', shot: 'ghost', colorId: 'base', direction: 'back' }, product, catalogs, onChange: value => { chosen = value; } };
  const tree = Picker(props);
  const radios = nodes(tree, node => node.type === 'input' && node.props.type === 'radio');
  assert.equal(radios.length, 2);
  assert.deepEqual(radios.map(node => node.props.checked), [false, true]);
  assert.deepEqual(nodes(tree, node => node.type === 'img').map(node => node.props.src), ['/Front.jpg', '/Back.jpg']);
  assert.equal(nodes(tree, node => node.props?.role === 'radiogroup').length, 1);
  assert.equal(nodes(tree, node => node.props?.className?.includes('product-source-check')).length, 1);
  assert.match(JSON.stringify(tree), /내 앞면 사진/);
  assert.match(JSON.stringify(tree), /내 뒷면 사진/);
  radios[0].props.onChange();
  assert.deepEqual(chosen, { direction: 'front' });
  const missing = Picker({ ...props, block: { ...props.block, colorId: 'extra' } });
  assert.equal(nodes(missing, node => node.type === 'input')[1].props.disabled, true);
  assert.match(JSON.stringify(missing), /사진 없음/);
  chosen = null;
  nodes(missing, node => node.type === 'input')[1].props.onChange();
  assert.equal(chosen, null);
  const css = readFileSync(new URL('../../src/styles/features.css', import.meta.url), 'utf8');
  assert.match(css, /\.product-source-tile[^}]*aspect-ratio:\s*3\s*\/\s*4/);
  assert.match(css, /\.product-source-tile[^}]*border:\s*2px/);
  assert.match(css, /focus-visible[^}]*product-source-tile[^}]*outline:/);
});

async function aiHarness() {
  const source = readFileSync(new URL('../../src/features/editor/EditorPanels.jsx', import.meta.url), 'utf8');
  const body = source.slice(source.indexOf('const NEW_CUT_DEFAULT_SHOT'), source.indexOf('\n/*', source.indexOf('export function AIPanel('))).replace('export function', 'function');
  const { code } = await transformWithEsbuild(body + '\nreturn AIPanel;', 'ai.jsx', { loader: 'jsx' });
  const module = await loadPhotos();
  const slots = []; let cursor = 0, request;
  const scope = { React, ...taxonomy, ...examples, ...selection, ...details, ...module,
    useState: initial => { const index = cursor++; if (!(index in slots)) slots[index] = initial; return [slots[index], value => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }]; },
    useRef: value => { const index = cursor++; return slots[index] ||= { current: value }; },
    isRealModelSelection: () => false, BRAND_USE_CATEGORIES: [],
  };
  for (const name of ['UnderlineTabs', 'ColorDots', 'MoodGuide', 'ProductSourcePicker', 'DetailTargetPicker', 'PanelHead', 'Chips', 'Button', 'Icon', 'OuterClosureIcon', 'VaryPanel']) scope[name] = name;
  const Panel = evaluate(code, scope);
  const render = () => { cursor = 0; return Panel({ catalogs, product, colorOpts: [{ id: 'base' }, { id: 'extra' }], detailColorOpts: [{ id: 'base' }, { id: 'extra' }],
    detailRecommendations: { version: 1, status: 'ready', candidates: [{ id: 'back-target', direction: 'back' }] }, onGenerate: value => { request = value; } }); };
  const component = (name) => nodes(render(), node => node.type === name)[0];
  nodes(render(), node => node.type === 'button' && node.props.children.includes('새 이미지 추가'))[0].props.onClick();
  return { render, component, request: () => request, generate: () => component('Button').props.onClick() };
}

test('에디터 고스트 요청은 고른 면을 쓰고 예시를 바꿔도 뒷면을 보존한다', async () => {
  const h = await aiHarness();
  h.component('UnderlineTabs').props.onChange('product');
  assert.equal(h.component('ProductSourcePicker').props.block.direction, 'front');
  h.component('ProductSourcePicker').props.onChange({ direction: 'back' });
  h.component('MoodGuide').props.onExampleChange('ghost-front');
  h.component('MoodGuide').props.onExampleChange('ghost-other');
  h.generate();
  assert.equal(h.request().direction, 'back');
  assert.equal(h.request().sideStyle, null);
  h.component('ColorDots').props.onChange('extra');
  h.generate();
  assert.equal(h.request().direction, 'front');
  assert.equal(h.component('ProductSourcePicker').props.block.colorId, 'extra');
  h.component('UnderlineTabs').props.onChange('styling');
  h.component('UnderlineTabs').props.onChange('product');
  assert.equal(h.component('ProductSourcePicker').props.block.direction, 'front');
});

test('에디터 디테일은 대상 방향이나 직접 고른 원본 면을 쓰고 색상 변경 때 대상을 푼다', async () => {
  const h = await aiHarness();
  h.component('UnderlineTabs').props.onChange('product');
  h.component('MoodGuide').props.onShotChange('detail');
  h.component('DetailTargetPicker').props.onChange({ direction: 'back' });
  h.generate();
  assert.equal(h.request().direction, 'back');
  h.component('DetailTargetPicker').props.onChange({ detailTargetId: 'back-target', direction: 'back', colorId: 'base' });
  h.generate();
  assert.equal(h.request().direction, 'back');
  assert.equal(h.request().detailTargetId, 'back-target');
  h.component('ColorDots').props.onChange('extra');
  h.generate();
  assert.equal(h.request().detailTargetId, null);
  assert.equal(h.request().direction, 'front');
});

test('제품 카드 캡션은 샷 이름 뒤에 고른 원본 면을 붙이고 착용컷은 그대로다', () => {
  const source = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
  const body = source.slice(source.indexOf('function cardLabels('), source.indexOf('\n/* 캡션'));
  const labels = evaluate(`${body}; return cardLabels;`, {});
  for (const shot of ['ghost', 'detail']) for (const direction of ['front', 'back']) {
    assert.equal(labels({ cutType: 'product', shot, direction }, catalogs).shot, `${shot === 'ghost' ? '고스트샷' : '디테일샷'} · ${direction === 'back' ? '뒷면' : '앞면'} 사진`);
  }
  assert.equal(labels({ cutType: 'styling', shot: 'full', direction: 'back' }, catalogs).shot, '풀샷');
});

test('콘티 대상 색상 변경은 디테일 대상을 먼저 풀고 새 색상의 원본 면을 고른다', async () => {
  const source = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
  const start = source.indexOf('          value={block.colorId} onChange={(colorId) => {');
  const body = source.slice(start + '          value={block.colorId} onChange={'.length, source.indexOf('} /></div>', start));
  const module = await loadPhotos();
  for (const shot of ['ghost', 'detail']) {
    let changes;
    const block = { cutType: 'product', shot, colorId: 'base', direction: 'back', ...(shot === 'detail' ? { detailTargetId: 'back-target' } : {}) };
    const change = evaluate(`return (${body});`, { block, catalogs: { detailProduct: product }, ...details, ...module, onChange: value => { changes = value; } });
    change('extra');
    assert.equal(changes.direction, 'front');
    assert.equal(changes.colorId, 'extra');
    if (shot === 'detail') assert.equal(changes.detailTargetId, null);
  }
});

test('디테일 대상이 없으면 공통 원본 타일을 보여주고 대상 선택은 원본 면에 묶는다', async () => {
  const source = readFileSync(new URL('../../src/features/storyboard/DetailTargetPicker.jsx', import.meta.url), 'utf8');
  const body = source.slice(source.indexOf('export function DetailTargetPicker')).replace('export function', 'function');
  const { code } = await transformWithEsbuild(body + '\nreturn DetailTargetPicker;', 'detail-picker.jsx', { loader: 'jsx' });
  const Picker = evaluate(code, { React, ...details, ProductSourcePicker: 'ProductSourcePicker' });
  let changes;
  const recommendations = { version: 1, status: 'ready', candidates: [{ id: 'back-target', direction: 'back', sourceSlot: 'Back', sourceIndex: 1,
    informationGroup: 'back', photoUse: 'standalone', visibility: 'clear', label: '등판' }] };
  const block = { id: 'detail', source: 'ai', cutType: 'product', shot: 'detail', colorId: 'base', direction: 'front' };
  const props = { block, product, catalogs, recommendations, onChange: value => { changes = value; } };
  const unbound = Picker(props);
  assert.equal(nodes(unbound, node => node.type === 'ProductSourcePicker').length, 1);
  assert.equal(nodes(unbound, node => node.type === 'img').length, 0);
  nodes(unbound, node => node.type === 'select')[0].props.onChange({ target: { value: 'back-target' } });
  assert.equal(changes.direction, 'back');
  const bound = Picker({ ...props, block: { ...block, ...changes } });
  assert.equal(nodes(bound, node => node.type === 'ProductSourcePicker').length, 0);
  assert.equal(nodes(bound, node => node.type === 'img')[0].props.src, '/Back.jpg');
  assert.match(JSON.stringify(bound), /등판/);
});
