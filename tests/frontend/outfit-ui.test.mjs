import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
let vite, OutfitSelection;
before(async () => {
  vite = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null }, appType: 'custom', logLevel: 'silent' });
  ({ OutfitSelection } = await vite.ssrLoadModule('/src/features/analysis/OutfitSelection.jsx'));
});
after(async () => { await vite?.close(); });
test('등록된 역할 사진을 접힌 키보드 버튼과 96×120 썸네일로 보여준다', () => {
  assert.equal(typeof OutfitSelection, 'function');
  const html = renderToStaticMarkup(React.createElement(OutfitSelection, { analysis: {
    clothingType: 'dress', outfitSelections: { version: 1, slots: { footwear: { itemId: 'shoe', origin: 'ai' } } },
    outfitRecommendation: { status: 'ready', reason: '검정 신발로 색을 맞췄어요.', candidates: {
      footwear: [{ id: 'shoe', name: '검정 신발', thumb: '/shoe.jpg' }], bag: [], accessory: [] } },
  }, onChange() {} }));
  assert.match(html, /aria-expanded="false"/);
  assert.match(html, /width="96" height="120"/);
  assert.match(html, /검정 신발/);
  assert.match(html, /바꾸기/);
  assert.equal(html.includes('기본 옷으로 찍어요'), false);
  assert.equal(html.includes('가방 선택'), false);
});
test('빈 카탈로그에서는 가짜 사진과 선택 버튼을 표시하지 않는다', () => {
  assert.equal(typeof OutfitSelection, 'function');
  const html = renderToStaticMarkup(React.createElement(OutfitSelection, { analysis: {
    clothingType: 'dress', outfitRecommendation: { status: 'unavailable', candidates: {} },
  }, onChange() {} }));
  assert.equal(html.includes('신발 선택'), false);
  assert.equal(html.includes('<img'), false);
  assert.equal(html.includes('aria-pressed'), false);
});

test('추천 설명은 숨기고 소품은 켜기와 끄기를 제공한다', () => {
  const html = renderToStaticMarkup(React.createElement(OutfitSelection, { analysis: {
    clothingType: 'dress', outfitSelections: { version: 1, optional: { bag: false, accessory: false }, slots: {} },
    outfitRecommendation: { status: 'ready', sets: [{ slots: { bag: 'bag' }, reason: '화면에 숨길 설명' }],
      candidates: { bag: [{ id: 'bag', name: '가방', thumb: '/bag.jpg', inSets: true }] } },
  }, onChange() {} }));
  assert.equal(html.includes('화면에 숨길 설명'), false);
  assert.match(html, /role="switch"/);
  assert.match(html, /aria-checked="false"/);
});
