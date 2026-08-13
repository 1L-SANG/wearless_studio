import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { after, before, test } from 'node:test';

import { createServer } from 'vite';

import { defaultStoryboard } from '../../src/lib/api/shapes.js';
import { getBlockRenderHeight } from '../../src/features/editor/editorGeometry.js';
import { applyOpeningRow } from '../../src/lib/storyboardEntryPlacement.js';

const PRODUCT = {
  name: '소프트 골지 라운드 니트',
  measurements: [],
};
const COLORS = [{ id: 'base', isBase: true, images: [] }];
const SERVER_DIR = fileURLToPath(new URL('../../server/', import.meta.url));
const VENV_PYTHON = fileURLToPath(new URL('../../server/.venv/bin/python', import.meta.url));
const PYTHON = existsSync(VENV_PYTHON) ? VENV_PYTHON : 'python3';

let vite;
let buildEditorBlocksFromStoryboard;
let buildStoryboard;

before(async () => {
  vite = await createServer({
    appType: 'custom',
    logLevel: 'silent',
    server: { middlewareMode: true },
  });
  ({ buildEditorBlocksFromStoryboard, buildStoryboard } = await vite.ssrLoadModule('/src/mock/db.js'));
});

after(async () => {
  await vite?.close();
});

function openingStoryboard() {
  return applyOpeningRow(defaultStoryboard(COLORS, 'basic', {
    projectId: 'opening-row-test',
    clothingType: 'top',
    targetGenders: ['women'],
  }));
}

function openingMockBlock(storyboard) {
  return buildEditorBlocksFromStoryboard(storyboard, PRODUCT, true)[0];
}

function withoutIds(value) {
  if (Array.isArray(value)) return value.map(withoutIds);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.entries(value)
    .filter(([key]) => key !== 'id')
    .map(([key, nested]) => [key, withoutIds(nested)]));
}

test('mock opening renders as one medium two-column row with copy below the images', () => {
  const storyboard = openingStoryboard();
  const opening = openingMockBlock(storyboard);

  assert.deepEqual(storyboard.slice(0, 2).map((block) => block.shot), ['medium', 'medium']);
  assert.equal(opening.kind, 'twocol');
  const images = opening.elements.filter((element) => element.type === 'image');
  assert.equal(images.length, 2);
  assert.deepEqual(images.map(({ x, y, w, h }) => ({ x, y, w, h })), [
    { x: 60, y: 50, w: 430, h: 500 },
    { x: 510, y: 50, w: 430, h: 500 },
  ]);

  const [headline, subtitle] = opening.elements.filter((element) => element.type === 'text');
  assert.equal(headline.text, `${PRODUCT.name}와 함께하는 하루`);
  assert.deepEqual(
    { x: headline.x, y: headline.y, w: headline.w, h: headline.h },
    { x: 60, y: 582, w: 880, h: 56 },
  );
  assert.ok(headline.y >= Math.max(...images.map((image) => image.y + image.h)));
  assert.deepEqual(
    { x: subtitle.x, y: subtitle.y, w: subtitle.w, h: subtitle.h },
    { x: 60, y: 650, w: 880, h: 34 },
  );
  assert.equal(getBlockRenderHeight(opening), 734);
});

test('mock colorway assembly renders a full-medium pair with product and matching labels', () => {
  const matches = [
    {
      id: 'match-light', name: '아이보리 셔츠', selected: true, selOrder: 1,
      isCompatible: true, colorName: '아이보리', colorGroup: 'ivory', colorBrightness: 93,
    },
    {
      id: 'match-dark', name: '블랙 셔츠', selected: true, selOrder: 2,
      isCompatible: true, colorName: '블랙', colorGroup: 'black', colorBrightness: 4,
    },
  ];
  const product = {
    ...PRODUCT,
    clothingType: 'top',
    colors: [
      { id: 'base', name: '블랙', swatchId: 'black', isBase: true, images: [] },
      { id: 'ivory', name: '', swatchId: 'ivory', images: [] },
    ],
  };
  const pair = defaultStoryboard(product.colors, 'extended', {
    projectId: 'mock-colorway', clothingType: 'top', targetGenders: ['women'], matchClothing: matches,
  }).filter((block) => block.colorwayGroupId);
  const blocks = buildEditorBlocksFromStoryboard(pair, product, false, { matchClothing: matches });

  assert.equal(blocks.length, 4);
  assert.equal(blocks[0].layoutType, 'colorwayPair');
  const images = blocks[0].elements.filter((element) => element.type === 'image');
  assert.deepEqual(images.map((image) => [image.x, image.y, image.w, image.h, image.radius]), [
    [60, 24, 430, 645, 0],
    [510, 24, 430, 645, 0],
  ]);
  assert.deepEqual(
    blocks[0].elements.filter((element) => element.type === 'text').map((element) => element.text),
    ['소프트 골지 라운드 니트 [아이보리]', '블랙 셔츠 [블랙]'],
  );
  assert.equal(blocks[0].h, 781);
});

test('mock colorway preview selects category-specific top and bottom framing assets', () => {
  const colors = [
    { id: 'col1', name: '블랙', isBase: true, images: [] },
    { id: 'col2', name: '추가 색상', images: [] },
  ];
  const topPair = buildStoryboard('extended', colors, {
    projectId: 'mock-preview-assets', clothingType: 'top', targetGenders: ['women'],
    previewProductName: '소프트 골지 라운드 니트',
  }).filter((block) => block.colorwayGroupId);
  const bottomPair = buildStoryboard('extended', colors, {
    projectId: 'mock-preview-assets', clothingType: 'bottom', targetGenders: ['men'],
    previewProductName: '세미 와이드 치노 팬츠',
  }).filter((block) => block.colorwayGroupId);

  assert.deepEqual(topPair.map((block) => block.previewThumb), [
    '/assets/colorway/soft-rib-knit-ivory-western-male-full-v2.png',
    '/assets/colorway/soft-rib-knit-ivory-western-male-medium-v2.png',
  ]);
  assert.deepEqual(bottomPair.map((block) => block.previewThumb), [
    '/assets/colorway/semi-wide-chino-beige-western-male-full-v1.png',
    '/assets/colorway/semi-wide-chino-beige-western-male-medium-v2.png',
  ]);

  const bottomEditor = buildEditorBlocksFromStoryboard(bottomPair, {
    ...PRODUCT,
    name: '세미 와이드 치노 팬츠',
    clothingType: 'bottom',
    colors,
  }, false);
  assert.deepEqual(
    bottomEditor[0].elements.filter((element) => element.type === 'image').map((element) => element.src),
    [
      '/assets/colorway/semi-wide-chino-beige-western-male-full-v1.png',
      '/assets/colorway/semi-wide-chino-beige-western-male-medium-v2.png',
    ],
  );

  const allTopPairs = buildStoryboard('extended', [
    { id: 'col1', name: '블랙', isBase: true, images: [] },
    { id: 'col2', name: '아이보리', images: [] },
    { id: 'col3', name: '스카이블루', images: [] },
    { id: 'col4', name: '그레이', images: [] },
  ], {
    projectId: 'mock-three-preview-assets', clothingType: 'top', targetGenders: ['men'],
    previewProductName: '소프트 골지 라운드 니트',
  }).filter((block) => block.colorwayGroupId);
  assert.deepEqual(allTopPairs.map((block) => block.previewThumb), [
    '/assets/colorway/soft-rib-knit-ivory-western-male-full-v2.png',
    '/assets/colorway/soft-rib-knit-ivory-western-male-medium-v2.png',
    '/assets/colorway/soft-rib-knit-ivory-western-male-full-v2.png',
    '/assets/colorway/soft-rib-knit-ivory-western-male-medium-v2.png',
    '/assets/colorway/soft-rib-knit-ivory-western-male-full-v2.png',
    '/assets/colorway/soft-rib-knit-ivory-western-male-medium-v2.png',
  ]);
});

test('mock and server assemblers emit the same opening-row block structure', () => {
  const storyboard = openingStoryboard().slice(0, 2);
  const mockOpening = openingMockBlock(storyboard);
  const mockImages = mockOpening.elements.filter((element) => element.type === 'image');
  const payload = {
    storyboard,
    cut_results: storyboard.map((block, index) => ({
      blockId: block.id,
      imageUrl: mockImages[index].src,
    })),
    copy_results: [
      {
        blockId: storyboard[0].id,
        texts: [{ role: 'headline', text: `${PRODUCT.name}와 함께하는 하루` }],
      },
      {
        blockId: storyboard[1].id,
        texts: [{ role: 'body', text: '강조 포인트를 살린 카피가 들어가는 자리예요.' }],
      },
    ],
    product: PRODUCT,
  };
  const python = spawnSync(PYTHON, ['-c', [
    'import json, sys',
    'from app.agents.page_assembler import assemble',
    'payload = json.load(sys.stdin)',
    'block = assemble(payload["storyboard"], payload["cut_results"], payload["copy_results"], payload["product"], True)[0]',
    'print(json.dumps(block, ensure_ascii=False))',
  ].join('\n')], {
    cwd: SERVER_DIR,
    encoding: 'utf8',
    env: { ...process.env, PYTHONPATH: '.' },
    input: JSON.stringify(payload),
  });

  assert.equal(python.status, 0, python.stderr);
  assert.deepEqual(withoutIds(JSON.parse(python.stdout)), withoutIds(mockOpening));
});
