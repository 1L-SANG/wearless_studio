import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React, { useState, useRef, useEffect } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { transformWithEsbuild } from 'vite';
import * as generation from '../../src/lib/generationExamples.js';
import * as detail from '../../src/lib/detailRecommendations.js';
import { filterExamplesForModel } from '../../src/lib/identityScope.js';
import { ALL_CUT_TYPE_OPTIONS, poseExampleDirectionCompatible } from '../../src/lib/storyboardTaxonomy.js';

const shots = [{ value: 'ghost', label: '고스트샷' }, { value: 'detail', label: '디테일샷' }];
const dressDetail = { cutType: 'product', shot: 'detail', clothingType: 'dress', gender: 'women' };

test('source detail recipe stays available without any published examples for every supported garment', () => {
  for (const clothingType of ['top', 'bottom', 'outer', 'dress']) {
    assert.equal(generation.hasAvailableGenerationRecipe([], { ...dressDetail, clothingType }), true);
    assert.equal(generation.hasAvailableGenerationRecipe([], { ...dressDetail, clothingType, shot: 'ghost' }), false);
  }
  assert.equal(generation.hasAvailableGenerationRecipe([], { ...dressDetail, clothingType: 'unknown' }), false);
  for (const cutType of ['styling', 'horizon', 'mirror']) {
    assert.equal(generation.hasAvailableGenerationRecipe([], { cutType, shot: 'full', clothingType: 'dress', gender: 'women' }), false);
  }
  assert.equal(generation.isGenerationCombinationPublic(dressDetail), false, 'example publication must remain unchanged');
  assert.deepEqual(generation.selectGenerationExamples([], dressDetail), []);
});

test('real dress shot button is enabled, chooses detail, and preserves a selected subject ID', async () => {
  const { ShotSegment } = await loadGalleryComponents();
  let block = { id: 'manual-dress', source: 'ai', cutType: 'product', shot: 'ghost' };
  const tree = ShotSegment({ options: shots, value: block.shot, cut: 'product', clothingType: 'dress',
    onChange: (shot) => { block = { ...block, shot }; } });
  const [ghostButton, detailButton] = tree.props.children;
  assert.equal(ghostButton.props.disabled, true);
  assert.equal(detailButton.props.disabled, false);
  detailButton.props.onClick();
  block = { ...block, ...detail.detailTargetPatch({ id: 'dress-waist', direction: 'front' }) };
  const saved = JSON.parse(JSON.stringify(block));
  assert.equal(saved.shot, 'detail');
  assert.equal(saved.detailTargetId, 'dress-waist');
  assert.equal(saved.detailTargetOrigin, 'user');
  assert.equal(saved.exampleId, undefined);
});

test('real editor product-tab handler chooses source detail when dress has no examples', () => {
  const source = readFileSync(new URL('../../src/features/editor/EditorPanels.jsx', import.meta.url), 'utf8');
  const gates = source.slice(source.indexOf('  const hasSelectableExamples ='), source.indexOf('  // 콘티보드 settingsReset'));
  const handler = source.slice(source.indexOf('  const selectCutType ='), source.indexOf('  const selectExample ='));
  const chosen = {};
  const dependencies = {
    hasAvailableGenerationRecipe: generation.hasAvailableGenerationRecipe, ALL_CUT_TYPE_OPTIONS,
    catalogs: { genExamples: [], productShotTypes: shots, shotTypes: [{ value: 'full' }, { value: 'medium' }] },
    clothingType: 'dress', modelGender: 'women', cutType: 'styling', NEW_CUT_DEFAULT_SHOT: { product: 'ghost' },
    setCutType: (value) => { chosen.cutType = value; }, setShot: (value) => { chosen.shot = value; },
    setDir() {}, setDetailTargetId() {}, setExampleId() {}, setRefScope() {}, resetRecipeSettings() {},
  };
  const run = new Function(...Object.keys(dependencies), `${gates}\n${handler}\nselectCutType('product'); return cutTypeOptions;`);
  const tabs = run(...Object.values(dependencies));
  assert.equal(tabs.find((tab) => tab.value === 'product').disabled, false);
  assert.ok(tabs.filter((tab) => tab.value !== 'product').every((tab) => tab.disabled));
  assert.deepEqual(chosen, { cutType: 'product', shot: 'detail' });
});

test('real optional detail gallery renders honest guidance without an empty gallery or retry gate', async () => {
  const { MoodGuide } = await loadGalleryComponents();
  const markup = renderToStaticMarkup(React.createElement(MoodGuide, {
    catalogs: { genExamples: [], productShotTypes: shots }, cut: 'product', shot: 'detail',
    clothingType: 'dress', onShotChange() {}, onExampleChange() {},
  }));
  assert.match(markup, /예시 없이 내 상품 원본으로 디테일을 촬영해요/);
  assert.doesNotMatch(markup, /sb-exgallery|sb-exempty|다시 시도|서비스에 공개되지 않았어요<\/div>/);
});

let components;
async function loadGalleryComponents() {
  if (components) return components;
  const source = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
  const segment = source.slice(source.indexOf('function ShotSegment('), source.indexOf('const MINE_SHOT_OPTION'));
  const gallery = source.slice(source.indexOf('export function MoodGuide('), source.indexOf('function Inspector('));
  const dependencies = { React, useState, useRef, useEffect, ...generation, ...detail, filterExamplesForModel, poseExampleDirectionCompatible };
  globalThis.__detailGalleryDependencies = dependencies;
  try {
    const code = `const { ${Object.keys(dependencies).join(', ')} } = globalThis.__detailGalleryDependencies;\n${segment}\n${gallery}\nexport { ShotSegment };`;
    const transformed = await transformWithEsbuild(code, 'detail-gallery-test.jsx', { loader: 'jsx', jsx: 'transform' });
    components = await import(`data:text/javascript;base64,${Buffer.from(transformed.code).toString('base64')}`);
    return components;
  } finally {
    delete globalThis.__detailGalleryDependencies;
  }
}
