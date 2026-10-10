import test, { after, before } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import { buildInfoBlock, defaultInfoFor } from '../../src/features/editor/presets/infoPresets.js';

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

function render(settings = { preset: 'smartstore', format: 'jpeg', delivery: 'split' }, notice = '', blocks = [], busy = false, state = {}) {
  return renderToStaticMarkup(React.createElement(EditorDownloadModal, {
    settings,
    summary,
    notice,
    blocks,
    busy,
    progress: null,
    error: '',
    onChange() {},
    onCancel() {},
    onDownload() {},
    ...state,
  }));
}

test('다운로드 모달은 폭·형식·받는 방식을 기본값과 함께 보여준다', () => {
  const html = render();
  for (const copy of [
    '스마트스토어·통용', '860px', '쿠팡', '780px', '고화질', '1,290px', '원본 PNG', '2,000px',
    'JPG', '가볍게, 품질 90', 'PNG', '한 장씩 개별로 저장', '한 번에 묶어서 저장',
  ]) assert.ok(html.includes(copy), copy);
  assert.match(html, /name="export-preset"[^>]*checked[^>]*value="smartstore"/);
  assert.match(html, /name="export-format"[^>]*checked[^>]*value="jpeg"/);
  assert.match(html, /name="export-delivery"[^>]*checked[^>]*value="split"/);
});

const noticeBlock = () => buildInfoBlock('required_notice', defaultInfoFor('required_notice', {
  productName: '셔츠', materials: [{ name: '면', ratio: 100 }], colorLabels: ['흰색'],
}));
const buttons = (html) => [...html.matchAll(/<button\b[^>]*>.*?<\/button>/gs)].map(([button]) => button);

test('빈 고시정보가 있어도 다운로드 창에 입력 안내를 띄우거나 문서를 바꾸지 않는다', () => {
  const block = noticeBlock();
  const before = structuredClone(block);
  const html = render(undefined, '', [block]);
  assert.doesNotMatch(html, /고시정보 입력 필요|치수|제조자\(수입자\)|제조국|제조연월|품질보증기준|A\/S 책임자와 전화번호|그대로 받기/);
  const downloadButton = buttons(html).find((button) => button.endsWith('다운로드</button>'));
  assert.ok(downloadButton);
  assert.doesNotMatch(downloadButton, /\bdisabled/);
  assert.deepEqual(block, before, '다운로드 창이 고시 내용이나 저장 문서를 바꾸지 않는다');
});

test('고시정보 입력 여부와 관계없이 기본 다운로드 행동을 유지한다', () => {
  const block = noticeBlock();
  block.elements = block.elements.map((el) => el.text === '정보 입력 필요' ? { ...el, text: '셀러 입력값' } : el);
  const html = render(undefined, '', [block]);
  assert.doesNotMatch(html, /고시정보 입력 필요/);
  assert.doesNotMatch(html, /그대로 받기/);
  assert.match(html, />다운로드<\/button>/);
});

test('처리 중에는 다운로드와 취소와 옵션을 잠그고 준비 상태를 알린다', () => {
  const html = render(undefined, '', [noticeBlock()], true);
  assert.equal(buttons(html).length, 2);
  for (const button of buttons(html)) assert.match(button, /\bdisabled/);
  for (const [input] of html.matchAll(/<input\b[^>]*>/g)) assert.match(input, /\bdisabled/);
  assert.match(html, /role="status"[^>]*aria-live="polite"/);
  assert.match(html, /다운로드 준비 중/);
});

test('블록 처리 진행 상태는 작업 단계와 완료 수를 분리해서 보여준다', () => {
  const html = render(undefined, '', [], true, { progress: { done: 2, total: 8, stage: 'capture' } });
  assert.match(html, /페이지를 이미지로 만드는 중/);
  assert.match(html, /2[^<]*\/[^<]*8개 블록/);
  assert.match(html, /<progress\b[^>]*max="8"[^>]*value="2"/);
  assert.doesNotMatch(html, /\[object Object\]/);
});

test('검사와 변환과 ZIP 단계는 완료 전 저장 결과를 단정하지 않는다', () => {
  for (const [stage, title] of [
    ['verify', '이미지 확인 중'],
    ['encode', '파일로 변환 중'],
    ['zip', 'ZIP으로 묶는 중'],
  ]) {
    const html = render(undefined, '', [], true, { progress: { done: 8, total: 8, stage } });
    assert.ok(html.includes(title), stage);
    assert.match(html, /<progress\b[^>]*aria-label="다운로드 처리 중"/);
    assert.doesNotMatch(html, /<progress\b[^>]*\bvalue=/);
  }
});

test('이전 진행 문자열도 준비 문구와 함께 표시한다', () => {
  const html = render(undefined, '', [], true, { progress: '3/12' });
  assert.match(html, /다운로드 준비 중/);
  assert.match(html, /3\/12/);
});

test('실패 이유를 창 안에 남기고 다운로드를 다시 시도할 수 있다', () => {
  const html = render(undefined, '', [], false, { error: '이미지를 만들지 못했어요. 나눠서 받아주세요.' });
  assert.match(html, /role="alert"/);
  assert.match(html, /이미지를 만들지 못했어요\. 나눠서 받아주세요/);
  assert.match(html, /설정을 바꾸거나 다시 시도해 주세요/);
  const retry = buttons(html).find((button) => button.endsWith('다시 시도</button>'));
  assert.ok(retry);
  assert.doesNotMatch(retry, /\bdisabled/);
  assert.doesNotMatch(html, /<progress/);
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
