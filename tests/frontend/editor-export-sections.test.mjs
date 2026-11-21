import test from 'node:test';
import assert from 'node:assert/strict';
import {
  ORIGINAL_PNG_MAX_HEIGHT,
  buildExportSummary,
  exportNodeBlock,
  renderStorefrontFiles,
  splitOversizeJpegRanges,
} from '../../src/features/editor/editorExport.js';
import { isExportBlankBlock } from '../../src/features/editor/editorGeometry.js';

const settings = { preset: 'custom', customWidth: 1000, format: 'png', delivery: 'split' };
const block = (height, pageSectionKey, blank = false) => ({ height, pageSectionKey, blank });
const idGroups = (plan) => plan.pieces.map((piece) => [...new Set(piece.slices.map((slice) => slice.blockIndex))]);

function assertCoverage(plan, limit) {
  const blockOffsets = [];
  let total = 0;
  for (const height of plan.scaledHeights) {
    blockOffsets.push(total);
    total += height;
  }
  let cursor = 0;
  for (const piece of plan.pieces) {
    assert.ok(piece.height > 0 && piece.height <= limit);
    assert.equal(piece.height, piece.slices.reduce((sum, slice) => sum + slice.height, 0));
    for (const { blockIndex, y, height } of piece.slices) {
      assert.equal(blockOffsets[blockIndex] + y, cursor, '출력 순서는 누락이나 중복 없이 이어진다');
      assert.ok(y + height <= plan.scaledHeights[blockIndex]);
      cursor += height;
    }
  }
  assert.equal(cursor, total);
  assert.equal(cursor, plan.height);
}

test('큰 섹션은 섞지 않고, 키 없는 블록과 떨어져 나온 같은 키도 별개 섹션이다', () => {
  const plan = buildExportSummary([
    block(800, 'a'), block(900, 'a'), block(900, 'b'), block(800), block(800), block(800, 'a'),
  ], settings);
  assert.deepEqual(idGroups(plan), [[0, 1], [2], [3], [4], [5]]);
  assertCoverage(plan, 3000);
});

test('짧은 섹션은 맨 앞에서만 뒤에 붙고, 앞이 넘칠 때는 새 조각을 시작한다', () => {
  const plan = buildExportSummary([
    block(300), block(700, 'a'), block(680), block(2500, 'b'), block(501), block(600), block(700, 'c'),
  ], settings);
  assert.deepEqual(idGroups(plan), [[0, 1, 2], [3], [4, 5], [6]]);
  assertCoverage(plan, 3000);
});

test('700px 판정에는 해당 섹션에 속한 경계 여백의 높이도 들어간다', () => {
  const plan = buildExportSummary([block(1600, 'a'), block(500, 'b'), block(500, 'b', true), block(1000, 'c')], settings);
  assert.deepEqual(idGroups(plan), [[0], [1], [2, 3]]);
});

test('여백은 연속 블록을 묶어 뒤에 붙이고, 뒤가 넘치거나 끝이면 앞에 붙인다', () => {
  const plan = buildExportSummary([
    block(1500, 'a'), block(300, undefined, true), block(300, undefined, true), block(1000, 'b'),
    block(400, 'b', true), block(2800, 'c'), block(200, 'c', true),
  ], settings);
  assert.deepEqual(idGroups(plan), [[0], [1, 2, 3, 4], [5, 6]]);
  assert.ok(plan.pieces.every((piece) => !piece.blank));
  assertCoverage(plan, 3000);
});

test('양쪽 섹션이 모두 가득 차면 여백 예외도 원래 위치와 높이를 보존한다', () => {
  const plan = buildExportSummary([block(3000, 'a'), block(300, undefined, true), block(3000, 'b')], settings);
  assert.deepEqual(idGroups(plan), [[0], [1], [2]]);
  assert.deepEqual(plan.pieces.map((piece) => piece.blank), [false, true, false]);
  assertCoverage(plan, 3000);
});

test('긴 섹션 안의 여백도 뒤 내용에 붙이고 첫 짧은 섹션을 위해 경계를 조정한다', () => {
  const gap = buildExportSummary([block(1500, 'a'), block(500, 'a', true), block(2300, 'a')], settings);
  assert.deepEqual(idGroups(gap), [[0], [1, 2]]);
  assertCoverage(gap, 3000);
  const first = buildExportSummary([block(600), ...[1400, 1200, 2700].map((height) => block(height, 'a'))], settings);
  assert.deepEqual(idGroups(first), [[0, 1], [2], [3]]);
  assertCoverage(first, 3000);
});

test('상한보다 긴 여백은 양쪽의 빈 공간으로 모두 배치할 수 있으면 빈 파일을 만들지 않는다', () => {
  for (const keys of [['a', undefined, 'b'], ['a', 'a', 'a']]) {
    const plan = buildExportSummary([block(800, keys[0]), block(4000, keys[1], true), block(1200, keys[2])], settings);
    assert.deepEqual(idGroups(plan), [[0, 1], [1, 2]]);
    assert.deepEqual(plan.pieceHeights, [3000, 3000]);
    assert.ok(plan.pieces.every((piece) => !piece.blank));
    assertCoverage(plan, 3000);
  }
});

test('내용 뒤 길이가 큰 여백과 여백만 있는 페이지도 원래 높이를 보존한다', () => {
  for (const blocks of [[block(5000, undefined, true)], [block(1200), block(5000, undefined, true)]]) {
    const plan = buildExportSummary(blocks, settings);
    assertCoverage(plan, 3000);
    assert.equal(plan.pieces.at(-1).blank, true);
  }
});

test('인접한 긴 섹션의 경계를 조정해서 흡수할 수 있는 긴 여백은 빈 파일이 되지 않는다', () => {
  const plan = buildExportSummary([
    block(800, 'a'), block(4000, undefined, true), block(800, 'b'), block(1400, 'b'), block(3000, 'b'),
  ], settings);
  assert.deepEqual(idGroups(plan), [[0, 1], [1, 2], [3], [4]]);
  assert.ok(plan.pieces.every((piece) => !piece.blank));
  assertCoverage(plan, 3000);
});

test('여백 여부는 배경과 무관하며 숨긴 요소, 빈 텍스트와 사진 자리에는 콘텐츠가 없다', () => {
  const elements = [{ type: 'shape', hidden: true }, { type: 'text', text: ' ' }, { type: 'image' }];
  assert.equal(isExportBlankBlock({ bg: '#000', elements }), true);
  assert.equal(isExportBlankBlock({ bg: '#fff', elements: [...elements, { type: 'image', src: '/photo.jpg' }] }), false);
});

test('콘텐츠가 빠진 블록도 캡처 border-box의 최소 높이를 보존한다', () => {
  const node = {
    offsetHeight: 450, style: { height: '450px' }, dataset: { exportHeight: '50', exportBlank: 'true' },
    ownerDocument: { defaultView: { getComputedStyle: () => ({ boxSizing: 'border-box', paddingTop: '50px', paddingBottom: '50px' }) } },
  };
  assert.deepEqual(exportNodeBlock(node), { height: 100, pageSectionKey: undefined, blank: true });
});

test('긴 섹션은 조각 수를 최소로 하고 블록 경계에서 고르게 나눈다', () => {
  const blocks = [900, 900, 900, 900, 700].map((height) => block(height, 'long'));
  const plan = buildExportSummary(blocks, settings);
  assert.deepEqual(plan.pieceHeights, [1800, 2500]);
  assert.deepEqual(idGroups(plan), [[0, 1], [2, 3, 4]]);
  assertCoverage(plan, 3000);
  const needsMore = buildExportSummary([1600, 1600, 1600].map((height) => block(height, 'long')), settings);
  assert.deepEqual(needsMore.pieceHeights, [1600, 1600, 1600]);
});

test('860px 목 상세페이지는 마지막 색상과 제품 블록에 안내 다섯을 모아 13조각으로 나눈다', () => {
  const sections = [
    ['첫 장', [1178]], ['후기', [1259]], ['상품 소개', [285]], ['추천 대상', [231]],
    ['일상 착용컷', [1155, ...Array(8).fill(1075), 1178]], ['궁금한 점', [1259]],
    ['스튜디오 착용컷', [1155, 1075, 1178]], ['색상과 제품', [1277, 1075, 1075, 1075, 1178]],
    ['사이즈 안내', [251]], ['세탁 안내', [189]], ['AI 생성 안내', [189]],
    ['공통 주의', [103]], ['배송·교환 안내', [405]],
  ];
  const outputHeights = sections.flatMap(([, heights]) => heights);
  const blocks = sections.flatMap(([key, heights]) => heights.map((height) => block(Math.ceil(height / 0.86), key)));
  const plan = buildExportSummary(blocks, { preset: 'smartstore' });
  assert.equal(blocks.length, 28);
  assert.deepEqual(plan.scaledHeights, outputHeights);
  assert.equal(plan.height, 25_370);
  assert.equal(plan.fileCount, 13);
  assert.deepEqual(idGroups(plan).slice(-3), [[18, 19], [20, 21], [22, 23, 24, 25, 26, 27]]);
  assert.deepEqual(plan.pieceHeights.slice(-3), [2352, 2150, 2315]);
  assertCoverage(plan, 3000);
});

test('이어지는 안내가 어떤 마지막 조각에도 전부 들어가지 않으면 기존 방식으로 넘친 안내를 묶는다', () => {
  const plan = buildExportSummary([
    block(1600, 'long'), block(1600, 'long'), block(500), block(600), block(600), block(500),
  ], settings);
  assert.deepEqual(idGroups(plan), [[0], [1, 2, 3], [4, 5]]);
  assert.deepEqual(plan.pieceHeights, [1600, 2700, 1100]);
  assertCoverage(plan, 3000);
});

test('같은 파일 수라면 안내 사이의 여백을 마지막 내용 조각에 담고, 다음 섹션에 붙은 여백은 그 자리에 둔다', () => {
  const plan = buildExportSummary([
    ...[1800, 1000, 1800, 900].map((height) => block(height, 'long')),
    block(300, undefined, true), block(450), block(650, undefined, true), block(800, 'next'),
  ], settings);
  assert.deepEqual(idGroups(plan), [[0], [1, 2], [3, 4, 5], [6, 7]]);
  assert.deepEqual(plan.pieceHeights, [1800, 2800, 1650, 1450]);
  assertCoverage(plan, 3000);
});

test('긴 섹션 뒤에 여백만 붙는 경우에도 여백을 포함한 조각 높이를 고르게 한다', () => {
  const plan = buildExportSummary([
    ...[900, 900, 700, 1200].map((height) => block(height, 'long')), block(900, undefined, true),
  ], settings);
  assert.deepEqual(idGroups(plan), [[0, 1, 2], [3, 4]]);
  assert.deepEqual(plan.pieceHeights, [2500, 2100]);
  assertCoverage(plan, 3000);
});

test('개별 블록이 상한을 넘으면 분할하고 원본 PNG에도 같은 규칙과 고유 상한을 쓴다', () => {
  const blocks = [block(5000, 'long'), block(1200, 'next')];
  const normal = buildExportSummary(blocks, settings);
  assert.deepEqual(normal.pieceHeights, [2500, 2500, 1200]);
  assertCoverage(normal, 3000);
  const original = buildExportSummary(blocks, { preset: 'original' });
  assert.deepEqual(original.pieceHeights, [5000, 5000, 2400]);
  assertCoverage(original, ORIGINAL_PNG_MAX_HEIGHT);
});

test('JPG 추가 분할은 가까운 블록 경계를 먼저 쓰고 전체 범위를 순서대로 보존한다', async () => {
  const parts = await splitOversizeJpegRanges({ y: 100, height: 1400 }, async (range) => ({ size: range.height }), {
    maxBytes: 500, blockBoundaries: [400, 820, 1190],
    findUniformRow() { assert.fail('가운데 근처에 블록 경계가 먼저 있다'); },
  });
  assert.deepEqual(parts.map(({ range }) => range), [
    { y: 100, height: 300 }, { y: 400, height: 420 }, { y: 820, height: 370 }, { y: 1190, height: 310 },
  ]);
});

test('JPG 단일 블록은 단색 줄을 우선하고 양쪽이 없을 때 가운데에서 자른다', async () => {
  const encode = async (range) => ({ size: range.height });
  const range = { y: 10, height: 1100 };
  const uniform = await splitOversizeJpegRanges(range, encode, {
    maxBytes: 900, findUniformRow: (_range, middle) => middle + 5,
  });
  assert.deepEqual(uniform.map(({ range }) => range.height), [555, 545]);
  const middle = await splitOversizeJpegRanges(range, encode, { maxBytes: 900 });
  assert.deepEqual(middle.map(({ range }) => range.height), [550, 550]);
  await assert.rejects(splitOversizeJpegRanges({ y: 0, height: 1 }, async () => ({ size: 900 }), { maxBytes: 1 }),
    /JPG 조각을 3MB 이하로 만들 수 없어요. PNG로 다시 시도해 주세요./);
});

test('실제 JPG 렌더에서 추가 분할은 블록 경계를 사용하며 합친 픽셀 행을 보존한다', async () => {
  const blocks = [780, 500, 820].map((height) => block(height, 'section'));
  const encoded = [];
  let offset = 0;
  function canvas(width, height) {
    const rows = [];
    return {
      width, height, rows,
      getContext: () => ({
        drawImage(source, ...args) {
          const [start, amount, target] = args.length === 2 ? [0, source.height, args[1]] : [args[1], args[3], args[5]];
          source.rows.slice(start, start + amount).forEach((row, i) => { rows[target + i] = row; });
        },
        getImageData() { assert.fail('3MB를 넘는 조각에는 먼저 쓸 수 있는 블록 경계가 있다'); },
      }),
    };
  }
  const rendered = await renderStorefrontFiles(blocks, { ...settings, format: 'jpeg' }, {
    measureBlock: (value) => value,
    createCanvas: canvas,
    releaseCanvas() {},
    captureBlock: async ({ height }) => {
      const source = canvas(1000, height);
      source.rows.push(...Array.from({ length: height }, () => offset++));
      return { canvas: source, softFailed: 0 };
    },
    encodeCanvas: async ({ height, rows }) => {
      encoded.push(height);
      return { rows, size: height > 900 ? 3 * 1024 * 1024 + 1 : 100 };
    },
  });
  assert.deepEqual(encoded, [2100, 1280, 780, 500, 820]);
  assert.deepEqual(rendered.files.flatMap(({ rows }) => rows), Array.from({ length: 2100 }, (_, row) => row));
});
