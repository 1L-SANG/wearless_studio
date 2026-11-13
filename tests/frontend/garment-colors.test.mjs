import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { transformWithEsbuild } from 'vite';
import React from 'react';
import { garmentColorOptions, garmentColorSelectionPatch } from '../../src/lib/garmentColors.js';
import { nodes, textOf } from './helpers/storyboardComponents.mjs';
import { storyboardPhotoPanelHarness } from './helpers/storyboardPhotoPanelHarness.mjs';

const image = (slot, src) => ({ slot, src });
const product = { colors: [
  { id: 'base', isBase: true, name: '블랙', images: [image('Front', '/black-front.jpg'), image('Back', '/black-back.jpg')] },
  { id: 'extra', name: '아이보리', images: [image('Front', '/ivory-front.jpg')] },
  { id: 'empty', name: '네이비', images: [] },
  { id: 'pending', name: '그레이', images: [image('Front', '')] },
  { id: 'back', name: '브라운', images: [image('Back', '/brown-back.jpg')] },
] };
const colorOpts = [{ id: 'base', label: '블랙', hex: '#111111' }, { id: 'extra', label: '아이보리', hex: '#eeeeee' }];

test('착용컷 색상은 각 색상의 실제 정면 사진이 있어야 선택할 수 있다', () => {
  const options = garmentColorOptions(product, colorOpts, { cutType: 'styling' });
  assert.deepEqual(options.map(option => option.id), ['base', 'extra', 'empty', 'pending', 'back']);
  assert.deepEqual(options.map(option => option.disabled), [false, false, true, true, true]);
  assert.equal(options[1].source.src, '/ivory-front.jpg');
  assert.equal(options[1].label, '아이보리');
  assert.match(options[2].reason, /정면 사진/);
  assert.equal(garmentColorSelectionPatch({ cutType: 'horizon' }, product, 'empty'), null);
  assert.equal(garmentColorSelectionPatch({ cutType: 'styling' }, product, 'deleted'), null);
});

test('제품컷은 같은 색의 원본 면을 보정하고 다른 색상의 뒷면을 빌리지 않는다', () => {
  const block = { cutType: 'product', shot: 'ghost', direction: 'back', colorId: 'base' };
  const option = garmentColorOptions(product, colorOpts, block).find(option => option.id === 'extra');
  assert.equal(option.disabled, false);
  assert.equal(option.source.src, '/ivory-front.jpg');
  assert.match(option.note, /뒷면.*앞면/);
  assert.deepEqual(garmentColorSelectionPatch(block, product, 'extra'), { colorId: 'extra', direction: 'front' });
  assert.deepEqual(garmentColorSelectionPatch({ ...block, direction: 'front' }, product, 'back'), { colorId: 'back', direction: 'back' });
});

test('목표색만 저장하고 색을 바꿀 때 디테일 표적 바인딩을 해제한다', () => {
  const block = { cutType: 'product', shot: 'detail', colorId: 'base', direction: 'back', detailTargetId: 'pocket' };
  assert.deepEqual(garmentColorSelectionPatch(block, product, 'extra'), {
    colorId: 'extra', detailTargetId: null, detailTargetOrigin: 'user', direction: 'front',
  });
  assert.deepEqual(garmentColorSelectionPatch(block, product, 'base'), { colorId: 'base' });
  const changed = { ...block, ...garmentColorSelectionPatch(block, product, 'extra') };
  assert.equal(changed.colorId, 'extra');
  assert.equal(changed.detailTargetId, null);
  assert.equal('mode' in changed, false);
});

test('새 콘티의 실제 선택 콜백은 목표색만 바꾸고 원본 없는 색 변경을 거부한다', async () => {
  const h = await storyboardPhotoPanelHarness({
    block: { id: 'worn', source: 'ai', cutType: 'styling', shot: 'full', colorId: 'base', contentRole: 'coordination' },
    catalogs: { detailProduct: product, genExamples: [], shotTypes: [{ value: 'full' }], productShotTypes: [] },
    colorOpts, detailColorOpts: colorOpts,
  });
  const field = () => nodes(h.render()).find(node => node.type === 'color-dots');
  assert.equal(field().props.product, product);
  field().props.onChange('extra');
  assert.equal(h.block.colorId, 'extra');
  assert.deepEqual(h.changes.at(-1), { colorId: 'extra' });
  const count = h.changes.length;
  field().props.onChange('empty');
  assert.equal(h.changes.length, count);
  assert.equal(h.block.colorId, 'extra');
  h.close();
});

test('공용 색상 필드는 이름과 선택색 원본, 비활성 이유와 접근성 상태를 보여준다', async () => {
  const source = readFileSync(new URL('../../src/components/GarmentColorField.jsx', import.meta.url), 'utf8');
  const body = source.slice(source.indexOf('export function GarmentColorField')).replace('export function', 'function');
  const { code } = await transformWithEsbuild(body + '\nreturn GarmentColorField;', 'garment-color-field.jsx', { loader: 'jsx' });
  const styles = new Proxy({}, { get: (_target, key) => String(key) });
  const Field = new Function('React', 'useId', 'styles', 'garmentColorOptions', `${code}`)(React, () => 'colors', styles, garmentColorOptions);
  let selected;
  const props = { product, colorOpts, block: { cutType: 'styling' }, value: 'extra', onChange: value => { selected = value; } };
  const tree = Field(props);
  const buttons = nodes(tree).filter(node => node.type === 'button');
  assert.equal(buttons[1].props['aria-pressed'], true);
  assert.equal(buttons[2].props.disabled, true);
  assert.ok(buttons[2].props['aria-describedby']);
  assert.match(textOf(tree), /아이보리/);
  assert.match(textOf(tree), /정면 사진/);
  assert.deepEqual(nodes(tree).filter(node => node.type === 'img').map(node => node.props.src), ['/ivory-front.jpg']);
  buttons[0].props.onClick();
  assert.equal(selected, 'base');
  selected = null;
  buttons[2].props.onClick();
  assert.equal(selected, null);
  assert.match(textOf(Field({ ...props, value: 'deleted' })), /색상.*다시/);
  assert.match(textOf(Field({ ...props, block: { cutType: 'product', shot: 'detail', detailTargetId: 'pocket' } })), /대표 색상/);
  const css = readFileSync(new URL('../../src/components/GarmentColorField.module.css', import.meta.url), 'utf8');
  assert.match(css, /min-width:\s*44px/);
  assert.match(css, /min-height:\s*44px/);
  assert.match(css, /width:\s*34px/);
  assert.match(css, /:focus-visible/);
});
