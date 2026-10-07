import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

let vite, AIPanel;
before(async () => {
  const root = new URL('../..', import.meta.url).pathname;
  vite = await createServer({ configFile: false, root, logLevel: 'silent',
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom',
    resolve: { alias: { '@': `${root}/src` } }, esbuild: { jsx: 'automatic' } });
  ({ AIPanel } = await vite.ssrLoadModule('/src/features/editor/EditorPanels.jsx'));
});
after(() => vite?.close());
const catalogs = {
  models: [{ id: 'mA', name: '기본 모델', gender: 'women' }], genExamples: [],
  directions: [{ value: 'front', label: '정면' }], productDirections: [{ value: 'front', label: '정면' }],
  shotTypes: [{ value: 'full', label: '전신' }],
  productShotTypes: [{ value: 'ghost', label: '고스트' }, { value: 'detail', label: '디테일' }],
  creditCosts: { editorImage: 1 },
};
const pending = { cutType: 'product', shot: 'ghost', colorId: 'white', modelId: null };
function render(extra = {}) {
  return renderToStaticMarkup(React.createElement(AIPanel, {
    catalogs, colorOpts: [{ id: 'white', label: '흰색' }, { id: 'black', label: '검은색' }],
    onGenerate() {}, onRetryFailedCut() {}, ...extra,
  }));
}

test('응답 유실 화면은 이전 색상과 컷을 보여주고 새 생성 대신 요청 확인만 제공한다', () => {
  const html = render({ pendingGenerateRequest: pending });
  assert.match(html, /흰색/);
  assert.match(html, /제품컷/);
  assert.match(html, /고스트/);
  assert.match(html, /이전 요청 확인하기/);
  assert.doesNotMatch(html, /새 이미지 생성|검은색|현재 이미지 수정/);
  assert.doesNotMatch(html, /<button[^>]*disabled/);
});

test('이전 요청을 재확인하는 동안은 확인 중으로 표시하고 버튼을 잠근다', () => {
  const html = render({ pendingGenerateRequest: pending, generateSubmitting: true });
  assert.match(html, /흰색/);
  assert.match(html, /<button[^>]*disabled[^>]*>.*?확인 중/s);
});

test('다른 실패 슬롯을 선택해도 새 슬롯 대신 이전 요청의 설정을 표시한다', () => {
  const html = render({ pendingGenerateRequest: pending,
    failedCutRetry: { request: { cutType: 'styling', shot: 'full', colorId: 'black' } } });
  assert.match(html, /흰색/);
  assert.match(html, /이전 요청 확인하기/);
  assert.doesNotMatch(html, /이 컷 다시 만들기|검은색/);
});

test('미확인 요청이 없으면 원래 AI 패널로 돌아온다', () => {
  const html = render();
  assert.doesNotMatch(html, /이전 요청 확인하기/);
  assert.match(html, /현재 이미지 수정/);
});

test('최초 접수 대기 중에는 다른 편집 탭으로 이동해 새 선택을 만들지 못한다', () => {
  const html = render({ generateSubmitting: true });
  assert.match(html, /<button[^>]*disabled[^>]*>새 이미지 추가<\/button>/);
  assert.match(html, /<button[^>]*disabled[^>]*>현재 이미지 수정<\/button>/);
});

test('접수 중 AI 패널을 다시 열어도 현재 이미지 수정 화면에 진입하지 않는다', () => {
  const html = render({ generateSubmitting: true });
  assert.doesNotMatch(html, /수정할 컷을 골라주세요/);
  assert.match(html, /접수 중/);
});
