import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { buildImageBlock } from '../../src/features/editor/editorLibrary.js';
import { createCanvasAiJobController } from '../../src/features/editor/canvasAiJobs.js';

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

const editor = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
const addResultSource = editor.slice(editor.indexOf('  const addCanvasAiResult ='), editor.indexOf('  // setBlockFocused 없이'));
function resultAdder(readDimensions) {
  let nextId = 0;
  const added = [];
  const controller = createCanvasAiJobController({ schedule: () => 0, cancelSchedule() {}, createKey: () => 'key' });
  const job = controller.start('el', { blockId: 'block', elementId: 'el', sourceWidth: 1000, sourceHeight: 1500 }, async () => null);
  controller.patch('el', { status: 'arrived',
    result: { id: 'result', assetId: 'result', src: 'generated', ai: true, cutType: 'styling' } });
  const add = new Function('readCanvasAiResultDimensions', 'canvasAiResultDimensions', 'canvasAiAdding',
    'latestBlocks', 'blocks', 'lastPush', 'addImageBlock', 'canvasAi',
    `${addResultSource}; return addCanvasAiResult;`)(
    readDimensions, helpers.canvasAiResultDimensions, { current: new Set() },
    { current: blocks }, blocks, { current: 0 },
    (image, idx, options) => added.push({ block: buildImageBlock(image, (prefix) => `${prefix}${++nextId}`), idx, options }),
    { controller },
  );
  return { add, job, added, controller };
}

for (const [label, dimensions, expectedHeight] of [
  ['정사각형', { width: 880, height: 880 }, 880],
  ['가로형', { width: 1600, height: 1000 }, 550],
  ['3:4', { width: 900, height: 1200 }, 1173],
]) {
  test(`새 블록은 원본 대신 ${label} 결과 사진의 실제 비율로 만든다`, async () => {
    const { add, job, added } = resultAdder(async (src) => {
      assert.equal(src, 'generated');
      return dimensions;
    });
    job.result.width = 1000;
    job.result.height = 1500;
    await add(job);
    assert.equal(added.length, 1);
    assert.equal(added[0].block.elements[0].w, 880);
    assert.equal(added[0].block.elements[0].h, expectedHeight);
    assert.equal(added[0].block.h, expectedHeight + 100);
    assert.equal(added[0].idx, 1);
    assert.deepEqual(added[0].options, { keepTab: true });
  });
}

test('결과 크기를 못 재면 응답 크기를 쓰고 없으면 기존 880×1320 기본값을 쓴다', async () => {
  for (const [response, expectedHeight] of [
    [{ width: 880, height: 880 }, 880],
    [{}, 1320],
    [{ width: 0, height: 880 }, 1320],
    [{ width: Infinity, height: 880 }, 1320],
  ]) {
    const { add, job, added } = resultAdder(async () => null);
    Object.assign(job.result, response);
    await add(job);
    assert.equal(added[0].block.elements[0].h, expectedHeight);
  }
});

test('측정 중 두 번 눌러도 블록은 하나만 생기고 완료된 결과를 다시 추가하지 않는다', async () => {
  let finish;
  const measured = new Promise((resolve) => { finish = resolve; });
  const { add, job, added } = resultAdder(() => measured);
  const first = add(job);
  const second = add(job);
  assert.equal(added.length, 0);
  finish({ width: 880, height: 880 });
  await Promise.all([first, second]);
  await add(job);
  assert.equal(added.length, 1);
});

test('크기 측정 실패 뒤 다른 결과도 새 블록에 추가할 수 있다', async () => {
  const { add, job, added, controller } = resultAdder(async () => null);
  await add(job);
  const nextJob = controller.start('el', { blockId: 'block', elementId: 'el' }, async () => null);
  controller.patch('el', { status: 'arrived', result: { ...job.result, src: 'next-result' } });
  await add(nextJob);
  assert.equal(added.length, 2);
});

for (const action of ['dismiss', 'new-job']) {
  test(`측정 중 ${action}이면 이전 결과를 추가하거나 새 작업을 닫지 않는다`, async () => {
    let finish;
    const measured = new Promise((resolve) => { finish = resolve; });
    const { add, job, added, controller } = resultAdder(() => measured);
    const pending = add(job);
    if (action === 'dismiss') controller.dismiss('el');
    else controller.start('el', { blockId: 'block', elementId: 'el' }, async () => null);
    const current = controller.get('el');
    finish({ width: 880, height: 880 });
    await pending;
    assert.equal(added.length, 0);
    assert.equal(controller.get('el'), current);
    if (action === 'new-job') assert.equal(current.dismissed, false);
  });
}

test('크기 선택은 결과 사진의 유효한 실측값을 응답 값보다 우선한다', () => {
  assert.equal(typeof helpers.canvasAiResultDimensions, 'function');
  assert.deepEqual(helpers.canvasAiResultDimensions({ width: 1000, height: 1500 }, { width: 880, height: 880 }),
    { width: 880, height: 880 });
  assert.deepEqual(helpers.canvasAiResultDimensions({ width: 900, height: 1200 }, { width: 0, height: 0 }),
    { width: 900, height: 1200 });
});

for (const outcome of ['load', 'cached', 'error', 'timeout', 'throw']) {
  test(`결과 사진 측정은 ${outcome}을 처리하고 이벤트 핸들러를 정리한다`, async () => {
    assert.equal(typeof helpers.readCanvasAiResultDimensions, 'function');
    const image = {
      naturalWidth: 880, naturalHeight: 880, complete: outcome === 'cached',
      set src(value) {
        assert.equal(value, 'generated');
        if (outcome === 'load') queueMicrotask(() => this.onload?.());
        if (outcome === 'error') queueMicrotask(() => this.onerror?.());
        if (outcome === 'throw') throw new Error('cannot load');
      },
    };
    const dimensions = await helpers.readCanvasAiResultDimensions('generated', {
      createImage: () => image, timeoutMs: 5,
    });
    assert.deepEqual(dimensions, ['load', 'cached'].includes(outcome) ? { width: 880, height: 880 } : null);
    assert.equal(image.onload, null);
    assert.equal(image.onerror, null);
  });
}
