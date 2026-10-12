import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import {
  FRAME_LIBRARY_ITEMS, buildDetailPageTemplateSet, buildFrameBlock, buildImageBlock,
  getDetailPageFrame, upgradeLegacyKiwiTemplateBlocks,
} from '../../src/features/editor/editorLibrary.js';

const seqId = () => { let n = 0; return (prefix) => `${prefix}${++n}`; };
const PRODUCT = { clothingType: 'top', measurements: [
  { key: 'totalLength', value: 67, unit: 'cm' },
  { key: 'shoulderWidth', value: 43.5, unit: 'cm' },
  { key: 'chestWidth', value: 54, unit: 'cm' },
  { key: 'sleeveLength', value: 60.5, unit: 'cm' },
] };
const CASES = [
  { id: 'kiwi-t4-10-productinfo', set: 'modern-minimal', positions: [[741, 651], [741, 707], [741, 763], [741, 819]], values: ['67', '43.5', '60.5', '54'] },
  { id: 'kiwi-t2-05-sizeinfo', set: 'magazine', positions: [[71, 967], [285, 967], [499, 967], [713, 967]], values: ['67', '43.5', '54', '60.5'] },
];
const cells = (block, positions) => positions.map(([x, y]) => block.elements.find((el) => el.type === 'text' && el.x === x && el.y === y));

for (const entry of CASES) {
  test(`${entry.id} uses product measurements for individual and full set insertion`, () => {
    const frame = getDetailPageFrame(entry.id);
    for (const block of [buildFrameBlock(frame, seqId(), PRODUCT),
      buildDetailPageTemplateSet(entry.set, seqId(), PRODUCT).find((item) => item.templateId === entry.id)]) {
      assert.deepEqual(cells(block, entry.positions).map((el) => el.text), entry.values);
      assert.ok(block.elements.every((el) => !Object.hasOwn(el, 'measurementKey')));
    }
    assert.deepEqual(cells(buildFrameBlock(frame, seqId(), PRODUCT), entry.positions).map((el) => el.text), entry.values);
  });

  test(`${entry.id} leaves missing, unknown and inapplicable measurements empty`, () => {
    const frame = getDetailPageFrame(entry.id);
    for (const product of [null, {}, { ...PRODUCT, measurements: [] }, { ...PRODUCT, measurementsUnknown: true },
      { ...PRODUCT, measurements: PRODUCT.measurements.map((m) => ({ ...m, value: null })) }]) {
      const result = cells(buildFrameBlock(frame, seqId(), product), entry.positions);
      assert.deepEqual(result.map((el) => el.text), ['', '', '', '']);
      assert.deepEqual(result.map((el) => el.placeholder), ['실측', '실측', '실측', '실측']);
    }
    const bottom = cells(buildFrameBlock(frame, seqId(), { ...PRODUCT, clothingType: 'bottom' }), entry.positions);
    assert.deepEqual(bottom.map((el) => el.text), ['67', '', '', '']);
  });
}

test('measurement insertion keeps zero, formats tenths and rejects nonnumbers', () => {
  const block = buildFrameBlock(getDetailPageFrame(CASES[0].id), seqId(), {
    ...PRODUCT, measurements: [
      { key: 'totalLength', value: 0 }, { key: 'shoulderWidth', value: 43.56 },
      { key: 'sleeveLength', value: '60.5' }, { key: 'chestWidth', value: NaN },
    ],
  });
  assert.deepEqual(cells(block, CASES[0].positions).map((el) => el.text), ['0', '43.6', '', '']);
});

test('W11 leaves all twenty-four measurements and four body ranges empty even with measurements', () => {
  const product = { clothingType: 'bottom', measurements: ['waistWidth', 'hipWidth', 'rise', 'thighWidth', 'hemWidth', 'totalLength']
    .map((key) => ({ key, value: 70, unit: 'cm' })) };
  const block = buildDetailPageTemplateSet('denim-casual', seqId(), product).find((item) => item.templateId === 'kiwi-t5-11-sizeguide');
  const measurements = cells(block, [693, 778, 863, 948, 1033, 1118].flatMap((y) => [234, 412, 590, 768].map((x) => [x, y])));
  assert.equal(measurements.length, 24);
  assert.ok(measurements.every((el) => el.text === '' && el.placeholder === '실측'));
  assert.deepEqual(cells(block, [71, 301, 531, 760].map((x) => [x, 522])).map((el) => el.text), ['', '', '', '']);
  assert.deepEqual(cells(block, [71, 301, 531, 760].map((x) => [x, 522])).map((el) => el.placeholder), ['체형 범위', '체형 범위', '체형 범위', '체형 범위']);
  assert.deepEqual(cells(block, [234, 412, 590, 768].map((x) => [x, 615])).map((el) => el.text), ['S', 'M', 'L', 'XL']);
  assert.ok(block.elements.some((el) => el.text === 'Size Guide'));
  assert.ok(block.elements.every((el) => !Object.hasOwn(el, 'measurementKey')));
});

test('legacy upgrade preserves stored size template text, including old samples and seller edits', () => {
  const blocks = [...CASES.map((entry) => buildFrameBlock(getDetailPageFrame(entry.id), seqId(), PRODUCT)),
    buildFrameBlock(getDetailPageFrame('kiwi-t5-11-sizeguide'), seqId())];
  cells(blocks[0], CASES[0].positions)[0].text = '셀러 입력 72';
  cells(blocks[1], CASES[1].positions)[0].text = '64';
  cells(blocks[2], [[234, 693]])[0].text = '29';
  const saved = JSON.parse(JSON.stringify(blocks));
  const upgraded = upgradeLegacyKiwiTemplateBlocks(blocks, seqId());
  assert.deepEqual(upgraded, saved);
  assert.ok(upgraded.every((block, index) => block === blocks[index]));
});

// Run the actual insertion handlers with local state and the real template builders.
function insertionHandlers(product) {
  const source = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
  const start = source.indexOf('  const addFrame =');
  const end = source.indexOf('  const addShape =', start);
  let blocks = [];
  const noop = () => {};
  const scope = { product, uid: seqId(), buildFrameBlock, buildDetailPageTemplateSet, buildImageBlock,
    FRAME_LIBRARY_ITEMS, getDetailPageFrame, autofillBlocks: (value) => value, roledCutsNow: () => [],
    filledSrcSet: () => new Set(), latestBlocks: { current: [] }, blocks,
    isGeneratedCutBlock: () => false, setBlocks: (next) => { blocks = typeof next === 'function' ? next(blocks) : next; },
    setSelBlock: noop, setBlockFocused: noop, setSelEl: noop, setSelEls: noop, toast: { push: noop },
    decodeWardrobeImage: () => null, WARDROBE_IMAGE_MIME: 'image', EDITOR_INFO_PRESET_DRAG_TYPE: 'info',
    EDITOR_FRAME_DRAG_TYPE: 'frame', setFrameOver: noop, setFrameDragging: noop, setDragFrame: noop,
  };
  const handlers = new Function(...Object.keys(scope), `${source.slice(start, end)}; return { addFrame, overwriteDetailPageTemplate, onCanvasInsertDrop };`)(...Object.values(scope));
  return { ...handlers, getBlocks: () => blocks };
}

test('editor click, drag and full template handlers all pass the current product measurements', () => {
  for (const entry of CASES) {
    const click = insertionHandlers(PRODUCT);
    click.addFrame(getDetailPageFrame(entry.id));
    const drag = insertionHandlers(PRODUCT);
    drag.onCanvasInsertDrop({ preventDefault() {}, dataTransfer: { getData: (type) => type === 'frame' ? entry.id : '' } }, 0);
    const set = insertionHandlers(PRODUCT);
    set.overwriteDetailPageTemplate(entry.set);
    for (const handler of [click, drag, set]) {
      const block = handler.getBlocks().find((item) => item.templateId === entry.id);
      assert.deepEqual(cells(block, entry.positions).map((el) => el.text), entry.values);
    }
  }
});
