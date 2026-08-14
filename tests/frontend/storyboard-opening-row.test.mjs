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

before(async () => {
  vite = await createServer({
    appType: 'custom',
    logLevel: 'silent',
    server: { middlewareMode: true },
  });
  ({ buildEditorBlocksFromStoryboard } = await vite.ssrLoadModule('/src/mock/db.js'));
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
