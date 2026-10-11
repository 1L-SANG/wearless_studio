import assert from 'node:assert/strict';
import test from 'node:test';
import { ensureSections } from '../../src/lib/sections.js';
import { generationExampleSelectionPatch } from '../../src/lib/storyboardExampleSelection.js';
import { directionSpecFromExample } from '../../src/lib/storyboardExampleSelection.js';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { scopeOfBlock } from '../../src/lib/identityScope.js';

test('example selection supplies side style and does not create a pose override', () => {
  for (const previousId of [null, 'old']) {
    const result = generationExampleSelectionPatch({
      source: 'ai', cutType: 'horizon', shot: 'full', direction: 'back', exampleId: previousId,
    }, { id: 'quarter', cutType: 'horizon', direction: 'side', sideStyle: 'threeQuarter' });
    assert.equal(result.patch.direction, 'side');
    assert.equal(result.patch.sideStyle, 'threeQuarter');
    assert.ok(!('pose' in result.patch));
    assert.ok(!('poseLabel' in result.patch));
  }
});

test('worn new-image requests get direction and side style from the example', () => {
  assert.deepEqual(directionSpecFromExample({ direction: 'side', sideStyle: 'threeQuarter' }), {
    direction: 'side', sideStyle: 'threeQuarter',
  });
  assert.deepEqual(directionSpecFromExample({ direction: 'back' }), { direction: 'back', sideStyle: null });
  assert.deepEqual(directionSpecFromExample(null), {
    direction: 'front', sideStyle: null,
  });
});

test('model eligibility does not depend on retired pose fields', () => {
  const block = {
    cutType: 'styling', direction: 'front', shot: 'full', refScope: 'all',
    exampleId: 'ex_styling_women_top_full_snapshot_01',
  };
  assert.equal(scopeOfBlock(block), 'virtual');
  assert.equal(scopeOfBlock({ ...block, pose: 'walk' }), 'virtual');
});

test('loading a saved board removes old pose selections before saving it again', () => {
  const [result] = ensureSections([{
    id: 'b', source: 'ai', cutType: 'horizon', shot: 'full', direction: 'front',
    sectionRole: 'studio', contentRole: 'fit', taxonomyVersion: 3,
    pose: 'walk', poseLabel: '걷기', poseThumb: 'old.png',
  }]);
  assert.ok(!('pose' in result));
  assert.ok(!('poseLabel' in result));
  assert.ok(!('poseThumb' in result));
  assert.equal(result.cutType, 'horizon');
});

test('editor renders without manual direction controls for worn and product images', async (t) => {
  const vite = await createServer({
    configFile: false, plugins: [react()],
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null }, appType: 'custom', logLevel: 'silent',
  });
  t.after(() => vite.close());
  const { AIPanel } = await vite.ssrLoadModule('/src/features/editor/EditorPanels.jsx');
  const { MoodGuide } = await vite.ssrLoadModule('/src/features/storyboard/Storyboard.jsx');
  const { DB } = await vite.ssrLoadModule('/src/mock/db.js');
  const rendered = renderToStaticMarkup(createElement(AIPanel, {
    catalogs: DB.catalogs, onGenerate() {},
  }));
  assert.ok(rendered.includes('새 이미지 만들기'));
  assert.ok(rendered.includes('새 이미지 생성'));
  for (const retiredControl of ['보는 방향', 'direction-chips', '같은 장소 이미지 생성',
    '포즈 고르기', '표정 고르기', 'vary-tabs', '기준 사진']) assert.ok(!rendered.includes(retiredControl));
  const productGallery = renderToStaticMarkup(createElement(MoodGuide, {
    catalogs: DB.catalogs, cut: 'product', direction: 'back', shot: 'ghost',
    clothingType: 'top', gender: 'women', onExampleChange() {},
  }));
  assert.ok(!productGallery.includes('direction-chips'));
  assert.ok(!productGallery.includes('보는 방향'));
  const gallery = renderToStaticMarkup(createElement(MoodGuide, {
    catalogs: DB.catalogs, cut: 'horizon', direction: 'side', sideStyle: 'threeQuarter',
    shot: 'full', clothingType: 'top', gender: 'men', onExampleChange() {},
  }));
  assert.ok(!gallery.includes('sb-expage-label'));
  assert.ok(!gallery.includes('sb-exsection'));
  const backExample = DB.catalogs.genExamples.find(e => e.id === 'ex_horizon_women_top_medium_back_01');
  assert.ok(backExample);
  const savedGallery = renderToStaticMarkup(createElement(MoodGuide, {
    catalogs: DB.catalogs, cut: 'horizon', direction: 'back', shot: 'medium',
    clothingType: 'top', gender: 'women', exampleId: backExample.id, onExampleChange() {},
  }));
  assert.ok(savedGallery.includes('class="sb-excell sel"'));
});
