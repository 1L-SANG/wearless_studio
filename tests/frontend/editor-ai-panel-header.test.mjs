import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

let vite, AIPanel;
before(async () => {
  const root = fileURLToPath(new URL('../..', import.meta.url));
  vite = await createServer({ configFile: false, root, logLevel: 'silent',
    optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom',
    resolve: { alias: { '@': `${root}/src` } }, esbuild: { jsx: 'automatic' } });
  ({ AIPanel } = await vite.ssrLoadModule('/src/features/editor/EditorPanels.jsx'));
});
after(() => vite?.close());
const catalogs = {
  models: [{ id: 'mA', name: '기본 모델', gender: 'women' }], genExamples: [],
  productSourcePhotos: [{ value: 'front', slot: 'Front', label: '내 앞면 사진' }],
  shotTypes: [{ value: 'full', label: '전신' }],
  productShotTypes: [{ value: 'ghost', label: '고스트' }], creditCosts: { editorImage: 1 },
};
function render(extra = {}) {
  return renderToStaticMarkup(React.createElement(AIPanel, {
    catalogs, colorOpts: [{ id: 'white', label: '흰색' }],
    onGenerate() {}, onConfirmPendingGenerate() {}, ...extra,
  }));
}
test('AI panel starts directly with cut selection without a second title', () => {
  const html = render();
  assert.doesNotMatch(html, /새 이미지 만들기|class="panel-h"/);
  assert.match(html, /컷 종류/);
  assert.match(html, /새 이미지 생성/);
});
test('removing the normal AI title keeps pending-request confirmation and its safety message', () => {
  const html = render({ pendingGenerateRequest: { cutType: 'product', shot: 'ghost', colorId: 'white' } });
  assert.match(html, /이전 요청을 확인해 주세요/);
  assert.match(html, /이전 요청 확인하기/);
  assert.doesNotMatch(html, /새 이미지 생성/);
});
