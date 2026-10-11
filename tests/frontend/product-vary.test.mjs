import test from 'node:test';
import assert from 'node:assert/strict';
import { buildCanvasAiMenu, buildCanvasAiRequest, canvasAiKind } from '../../src/features/editor/canvasAiActions.js';
import { canvasAiHookHarness } from './helpers/editorCanvasAiHarness.mjs';

const product = { colors: [{ id: 'navy', name: '네이비', images: [{ slot: 'Front', src: '/front.jpg' }] }] };
const element = { id: 'photo', type: 'image', src: '/v1/assets/photo/file', sourceBlockId: 'detail-slot' };
const block = { id: 'page', elements: [element] };
const info = (kind) => ({ kind, recipe: kind === 'upload' ? null : {
  cutType: kind === 'worn' ? 'styling' : 'product', shot: kind === 'detail' ? 'detail' : 'ghost',
  direction: 'front', colorId: 'navy',
} });

for (const [kind, branches, action, expected] of [
  ['worn', ['similar', 'color'], 'similar', { mode: 'vary', variationMode: 'similar', source: { src: element.src, cutType: 'styling' } }],
  ['detail', ['reshoot', 'detail'], 'reshoot', { mode: 'vary', source: { src: element.src, cutType: 'product' } }],
  ['ghost', ['color', 'direction', 'detail'], 'reshoot', null],
]) test(`캔버스 ${kind} 사진은 허용된 갈래와 요청만 제공한다`, () => {
  const menu = buildCanvasAiMenu({ block, element, assetRecipe: info(kind), product });
  assert.deepEqual(menu.branches.map((branch) => branch.id), branches);
  assert.deepEqual(buildCanvasAiRequest({ action, source: menu.source, assetRecipe: info(kind) }), expected);
  if (kind === 'ghost') assert.equal(buildCanvasAiRequest({ action: 'similar', source: menu.source, assetRecipe: info(kind) }), null);
});

test('같은 부위 재촬영은 폐기된 변경값과 배경 참조 없이 원본 사진만 보낸다', async () => {
  let request;
  const sourceElement = { ...element, changes: ['background'], refBg: 'old', refBgAssetId: 'old' };
  const h = canvasAiHookHarness({ blocks: [{ ...block, elements: [sourceElement] }], selectedId: element.id,
    assetRecipe: info('detail'), api: { generateImage: async (_project, value) => {
      request = value; return { data: { src: '/new.jpg' }, credits: 9 };
    } } });
  h.start('reshoot'); await h.run();
  assert.equal(request.mode, 'vary');
  assert.deepEqual(request.source, { src: element.src, cutType: 'product' });
  for (const key of ['changes', 'refBg', 'refBgAssetId', 'variationMode']) assert.equal(key in request, false, key);
});

test('캔버스와 의류 사진의 재촬영 여부는 서버 레시피로 판정한다', () => {
  for (const sourceBlockId of ['detail-slot', 'ghost-slot', null]) {
    for (const [kind, expected] of [['detail', true], ['ghost', false]]) {
      const placed = { ...element, sourceBlockId };
      const menu = buildCanvasAiMenu({ block: { ...block, elements: [placed] }, element: placed,
        storyboard: [{ id: 'detail-slot', cutType: 'product', shot: 'detail' }], assetRecipe: info(kind), product });
      assert.equal(menu.branches.some((branch) => branch.id === 'reshoot'), expected);
      const request = buildCanvasAiRequest({ action: 'reshoot', source: menu.source, assetRecipe: info(kind) });
      if (expected) assert.deepEqual(request, { mode: 'vary', source: { src: element.src, cutType: 'product' } });
      else assert.equal(request, null);
    }
  }
});

test('디테일 칸에 놓인 고스트와 종류를 모르는 옛 제품컷에 같은 부위 재촬영을 만들지 않는다', () => {
  const storyboard = [{ id: 'detail-slot', cutType: 'product', shot: 'detail' }];
  assert.equal(canvasAiKind({ block, element, storyboard, assetRecipe: info('ghost') }), 'ghost');
  const unknown = { kind: 'product_unknown', cutType: 'product' };
  const menu = buildCanvasAiMenu({ block, element, storyboard, assetRecipe: unknown, product });
  assert.deepEqual(menu.branches.map((branch) => branch.id), ['detail']);
  assert.equal(buildCanvasAiRequest({ action: 'reshoot', source: menu.source, assetRecipe: unknown }), null);
});

test('직접 올린 사진도 비슷한 컷을 만들며 원본 레시피 값을 꾸미지 않는다', () => {
  const menu = buildCanvasAiMenu({ block, element, assetRecipe: info('upload'), product });
  assert.deepEqual(menu.branches.map((branch) => branch.id), ['similar', 'color']);
  assert.equal(menu.current.colorId, null);
  assert.equal(menu.current.direction, null);
  assert.deepEqual(buildCanvasAiRequest({ action: 'similar', source: menu.source, assetRecipe: info('upload') }), {
    mode: 'vary', variationMode: 'similar', source: { src: element.src, cutType: null },
  });
});
