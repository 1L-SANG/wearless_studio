import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

let vite, AIPanel, WardrobePanel, ImagePanel;
before(async () => {
  const root = new URL('../..', import.meta.url).pathname;
  vite = await createServer({ optimizeDeps: { noDiscovery: true, include: [] }, configFile: false, root, logLevel: 'silent',
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom',
    resolve: { alias: { '@': `${root}/src` } }, esbuild: { jsx: 'automatic' } });
  ({ AIPanel, WardrobePanel, ImagePanel } = await vite.ssrLoadModule('/src/features/editor/EditorPanels.jsx'));
});
after(() => vite?.close());
const catalogs = {
  models: [{ id: 'mA', name: '기본 모델', gender: 'women' }], genExamples: [],
  productSourcePhotos: [{ value: 'front', slot: 'Front', label: '내 앞면 사진' }, { value: 'back', slot: 'Back', label: '내 뒷면 사진' }],
  shotTypes: [{ value: 'full', label: '전신' }],
  productShotTypes: [{ value: 'ghost', label: '고스트' }, { value: 'detail', label: '디테일' }],
  creditCosts: { editorImage: 1 },
};
const pending = { cutType: 'product', shot: 'ghost', colorId: 'white', modelId: null };
function render(extra = {}) {
  return renderToStaticMarkup(React.createElement(AIPanel, {
    catalogs, colorOpts: [{ id: 'white', label: '흰색' }, { id: 'black', label: '검은색' }],
    onGenerate() {}, onConfirmPendingGenerate() {}, ...extra,
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
  assert.doesNotMatch(html, /새 이미지 만들기/);
  assert.match(html, /컷 종류/);
  assert.match(html, /새 이미지 생성/);
  assert.doesNotMatch(html, /현재 이미지 수정|새 이미지 추가|기준 사진을 골라주세요/);
});

test('최초 접수 대기 중에는 생성 버튼을 잠그고 편집 탭 전환 없이 새 요청 폼을 유지한다', () => {
  const html = render({ generateSubmitting: true });
  assert.doesNotMatch(html, /새 이미지 만들기/);
  assert.match(html, /<button[^>]*disabled[^>]*>.*?접수 중/s);
  assert.doesNotMatch(html, /현재 이미지 수정|새 이미지 추가/);
});

test('접수 중 AI 패널을 다시 열어도 현재 이미지 수정 화면에 진입하지 않는다', () => {
  const html = render({ generateSubmitting: true });
  assert.doesNotMatch(html, /수정할 컷을 골라주세요/);
  assert.match(html, /접수 중/);
});

test('실패한 칸을 선택해도 AI 패널은 새 요청 폼을 제공한다', () => {
  const html = render({ failedCutRetry: { signature: true, request: { colorId: 'black' }, thumb: '/retry.jpg' } });
  assert.doesNotMatch(html, /새 이미지 만들기/);
  assert.match(html, /새 이미지 생성/);
  assert.doesNotMatch(html, /원래 콘티로 다시 만들기|시그니처 컷 다시 만들기|retry.jpg/);
});

test('의류 사진과 이미지 속성에는 캔버스 AI 메뉴로 중복 진입하는 버튼이 없다', () => {
  const wardrobe = renderToStaticMarkup(React.createElement(WardrobePanel, {
    wardrobe: { misc: [{ id: 'image', src: '/photo.jpg' }] }, onInsert() {}, onUpload() {}, onDeleteImage() {},
  }));
  const inspector = renderToStaticMarkup(React.createElement(ImagePanel, {
    el: { id: 'image', type: 'image', src: '/photo.jpg', w: 300, h: 400 }, onChange() {}, onLayer() {}, onCrop() {},
  }));
  assert.match(wardrobe, /photo.jpg/);
  assert.match(inspector, /이미지 크기/);
  assert.doesNotMatch(wardrobe + inspector, /AI 편집|AI로 편집|AI로 컷 변형/);
});

test('실제 모델의 브랜드 유형은 새 폼에서 고른 모델을 기준으로 생성 버튼을 잠근다', () => {
  const realModelCatalogs = { ...catalogs, models: [{ id: 'real-model', name: '실제 모델', gender: 'women' }] };
  const blocked = render({ catalogs: realModelCatalogs });
  assert.match(blocked, /사용 브랜드 유형/);
  assert.match(blocked, /<button[^>]*disabled[^>]*class="[^"]*btn-primary/);
  const ready = render({ catalogs: realModelCatalogs, brandUseCategory: '일반 의류' });
  assert.match(ready, /새 이미지 생성/);
  assert.doesNotMatch(ready, /<button[^>]*disabled[^>]*class="[^"]*btn-primary/);
  const saving = render({ catalogs: realModelCatalogs, brandUseCategory: '일반 의류', brandUseCategorySaving: true });
  assert.match(saving, /<button[^>]*disabled[^>]*class="[^"]*btn-primary/);
});
