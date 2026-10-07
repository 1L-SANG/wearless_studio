import test, { after, before } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

const root = new URL('../..', import.meta.url).pathname;
let vite;
let EditorDownloadModal;

before(async () => {
  vite = await createServer({
    configFile: false,
    root,
    logLevel: 'silent',
    server: { middlewareMode: true, watch: null, ws: false },
    appType: 'custom',
    resolve: { alias: { '@': `${root}/src` } },
    esbuild: { jsx: 'automatic' },
  });
  ({ EditorDownloadModal } = await vite.ssrLoadModule('/src/features/editor/EditorDownloadModal.jsx'));
});

after(() => vite?.close());

const summary = {
  width: 860,
  height: 6020,
  fileCount: 3,
  maxPieceHeight: 3000,
  approxBytes: 4.4 * 1024 * 1024,
  approximate: true,
};

function render(settings = { preset: 'smartstore', format: 'jpeg', delivery: 'split' }, notice = '') {
  return renderToStaticMarkup(React.createElement(EditorDownloadModal, {
    settings,
    summary,
    notice,
    busy: false,
    progress: null,
    onChange() {},
    onCancel() {},
    onDownload() {},
  }));
}

test('다운로드 모달은 폭·형식·받는 방식을 기본값과 함께 보여준다', () => {
  const html = render();
  for (const copy of [
    '스마트스토어·통용 860px', '쿠팡 780px', '고화질 1,290px', '원본 PNG(2,000px)',
    'JPG', '가볍게, 품질 90', 'PNG', '나눠서 받기', '한 장으로 받기',
  ]) assert.ok(html.includes(copy), copy);
  assert.match(html, /name="export-preset"[^>]*checked[^>]*value="smartstore"/);
  assert.match(html, /name="export-format"[^>]*checked[^>]*value="jpeg"/);
  assert.match(html, /name="export-delivery"[^>]*checked[^>]*value="split"/);
});

test('요약은 최종 px·파일 수·예상 용량과 업로드 순서 안내를 표시한다', () => {
  const html = render(undefined, '세로가 20,000px를 넘어 나눠서 받기로 바꿨어요.');
  assert.match(html, /860 × 총 6,020px/);
  assert.match(html, /각 최대 3,000px/);
  assert.match(html, /약 3개/);
  assert.match(html, /약 4\.4MB/);
  assert.match(html, /여러 장을 올린 뒤 쇼핑몰 화면에서 순서를 한 번 확인해 주세요/);
  assert.match(html, /ZIP 안 파일명은 영문과 숫자/);
  assert.match(html, /20,000px를 넘어/);
});

test('원본 2,000px 선택은 형식을 PNG로 고정한다', () => {
  const html = render({ preset: 'original', format: 'jpeg', delivery: 'single' });
  assert.match(html, /name="export-format"[^>]*disabled[^>]*value="jpeg"/);
  assert.match(html, /name="export-format"[^>]*disabled[^>]*checked[^>]*value="png"/);
  assert.match(html, /원본 2,000px는 PNG로만 저장해요/);
  assert.match(html, /폭 2,000px PNG로 저장하고, 긴 블록은 안전하게 나눠요/);
  assert.doesNotMatch(html, /블록 경계를 살려 3,000px 이하 조각/);
});
