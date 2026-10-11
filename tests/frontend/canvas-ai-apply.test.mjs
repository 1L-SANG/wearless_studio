import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';

const moduleUrl = new URL('../../src/features/editor/canvasAiApply.js', import.meta.url);
const helpers = await import(moduleUrl).catch(() => ({}));
const element = { id: 'el', type: 'image', src: 'before', x: 40, y: 60, w: 300, h: 450,
  radius: 18, rotate: 7, crop: { ox: 5 }, sourceBlockId: 'old-recipe' };
const blocks = [{ id: 'block', elements: [element] }];
const job = { blockId: 'block', elementId: 'el', sourceSrc: 'before', group: 'black' };

test('교체는 원래 요소의 자리와 프레임을 보존하고 자르기만 초기화한다', () => {
  assert.equal(typeof helpers.replaceCanvasAiImage, 'function');
  const next = helpers.replaceCanvasAiImage(blocks, job, { src: 'after', cutType: 'styling' });
  assert.deepEqual(next[0].elements[0], { ...element, src: 'after', crop: null });
  assert.equal(blocks[0].elements[0].src, 'before');
});

test('지워졌거나 다른 이미지가 들어간 원본은 결과로 덮지 않는다', () => {
  assert.equal(typeof helpers.replaceCanvasAiImage, 'function');
  const changed = [{ id: 'block', elements: [{ ...element, src: 'seller-photo' }] }];
  assert.equal(helpers.replaceCanvasAiImage(changed, job, { src: 'after' }), changed);
  assert.equal(helpers.replaceCanvasAiImage([], job, { src: 'after' }).length, 0);
});

test('빈 칸 도착도 현재 요소가 이미지이며 비어 있을 때만 넣는다', () => {
  assert.equal(typeof helpers.canFillCanvasAiSlot, 'function');
  assert.equal(helpers.canFillCanvasAiSlot(blocks, job), false);
  assert.equal(helpers.canFillCanvasAiSlot([{ id: 'block', elements: [{ ...element, src: null }] }], job), true);
  assert.equal(helpers.canFillCanvasAiSlot([{ id: 'block', elements: [{ ...element, type: 'text', src: null }] }], job), false);
  assert.equal(helpers.canFillCanvasAiSlot([], job), false);
});

test('같은 자산이 두 요소에서 도착해도 의류 목록에 한 장만 남는다', () => {
  const editor = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
  const source = editor.slice(editor.indexOf('  const placeGeneratedImage ='), editor.indexOf('  const dropLoadingTile ='));
  let wardrobe = { black: [{ id: 'asset', src: 'new' }, { id: 'loading', loading: true }] };
  const place = new Function('setWardrobe', `${source}; return placeGeneratedImage;`)((update) => { wardrobe = update(wardrobe); });
  place('black', 'loading', { id: 'asset', src: 'new' });
  assert.equal(wardrobe.black.length, 1);
  assert.equal(wardrobe.black[0].fresh, true);
});

test('정사각형 프레임에서 만들더라도 결과 비율은 원본 사진을 따른다', () => {
  assert.equal(typeof helpers.canvasAiSourceDimensions, 'function');
  const square = { w: 300, h: 300 };
  assert.deepEqual(helpers.canvasAiSourceDimensions(square, { width: 1000, height: 1500 }), { width: 1000, height: 1500 });
  assert.deepEqual(helpers.canvasAiSourceDimensions(square, null, { width: 1200, height: 1800 }), { width: 1200, height: 1800 });
});

test('3열 빈 칸의 진행 내용은 실제 보이는 칸 폭과 높이에 맞춘다', () => {
  assert.equal(typeof helpers.canvasAiProgressSize, 'function');
  assert.deepEqual(helpers.canvasAiProgressSize({ width: 112, height: 168 }), { width: 96, height: 152 });
  assert.deepEqual(helpers.canvasAiProgressSize({ width: 72, height: 108 }), { width: 56, height: 92 });
});

test('빈 칸을 다른 사진으로 채웠으면 새 컷 표시를 그 사진에 붙이지 않는다', () => {
  assert.equal(typeof helpers.canvasAiSlotArrivalVisible, 'function');
  const arrived = { isSlot: true, status: 'arrived', result: { src: 'generated' } };
  assert.equal(helpers.canvasAiSlotArrivalVisible({ src: 'seller-photo' }, arrived), false);
  assert.equal(helpers.canvasAiSlotArrivalVisible({ src: 'generated' }, arrived), true);
});
