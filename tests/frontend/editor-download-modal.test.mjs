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

function render(settings = { preset: 'smartstore', format: 'jpeg', delivery: 'split' }, notice = '', blocks = [], busy = false) {
  return renderToStaticMarkup(React.createElement(EditorDownloadModal, {
    settings,
    summary,
    notice,
    blocks,
    busy,
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

const noticeBlock = () => buildInfoBlock('required_notice', defaultInfoFor('required_notice', {
  productName: '셔츠', materials: [{ name: '면', ratio: 100 }], colorLabels: ['흰색'],
}));
const warningText = (html) => html.match(/<p[^>]*role="status"[^>]*>(.*?)<\/p>/s)?.[1] || '';

test('고시정보의 빈 항목 이름을 안내하고 그대로 받기를 막지 않는다', () => {
  const block = noticeBlock();
  const before = structuredClone(block);
  const html = render(undefined, '', [block]);
  const warning = warningText(html);
  for (const label of ['치수', '제조자(수입자)', '제조국', '제조연월', '품질보증기준', 'A/S 책임자와 전화번호']) {
    assert.ok(warning.includes(label), label);
  }
  assert.doesNotMatch(warning, /품명|소재|색상|세탁방법/);
  const downloadButton = [...html.matchAll(/<button\b[^>]*>.*?<\/button>/gs)].map(([button]) => button)
    .find((button) => button.includes('그대로 받기'));
  assert.ok(downloadButton);
  assert.doesNotMatch(downloadButton, /\bdisabled/);
  assert.deepEqual(block, before, '경고 때문에 고시 내용이나 저장 문서를 바꾸지 않는다');
});

test('직접 고친 출력 문구를 기준으로 감지하고 빈 항목이 없으면 안내하지 않는다', () => {
  const block = noticeBlock();
  block.elements = block.elements.map((el) => el.text === '정보 입력 필요' ? { ...el, text: '셀러 입력값' } : el);
  const html = render(undefined, '', [block]);
  assert.equal(warningText(html), '');
  assert.doesNotMatch(html, /그대로 받기/);
  assert.match(html, />다운로드<\/button>/);
});

test('일반 블록과 숨긴 문구는 경고하지 않고 중복 고시 항목은 한 번만 안내한다', () => {
  const first = noticeBlock();
  const maker = first.elements.find((el) => el.text === '제조자(수입자)');
  first.elements = first.elements.map((el) => el.text === '정보 입력 필요' && el.y !== maker.y ? { ...el, hidden: true } : el);
  const second = structuredClone(first);
  const ordinary = { kind: 'info', infoType: 'benefit_copy', elements: [{ type: 'text', text: '정보 입력 필요' }] };
  const warning = warningText(render(undefined, '', [first, second, ordinary]));
  assert.equal((warning.match(/제조자\(수입자\)/g) || []).length, 1);
  assert.doesNotMatch(warning, /치수|제조국|제조연월|품질보증|전화번호/);
  assert.equal(warningText(render(undefined, '', [ordinary])), '');
});

test('고시정보 레이어 순서가 바뀌어도 같은 행의 항목을 찾는다', () => {
  const block = noticeBlock();
  const size = block.elements.find((el) => el.text === '치수');
  const value = block.elements.find((el) => el.y === size.y && el.text === '정보 입력 필요');
  block.elements = [...block.elements.filter((el) => el !== value).map((el) => el.text === '정보 입력 필요' ? { ...el, text: '입력함' } : el), value];
  assert.match(warningText(render(undefined, '', [block])), /치수/);
  assert.doesNotMatch(warningText(render(undefined, '', [block])), /전화번호/);
});

test('빈 항목 안내가 있어도 처리 중에는 중복 다운로드를 막는다', () => {
  const html = render(undefined, '', [noticeBlock()], true);
  assert.match(warningText(html), /제조국/);
  assert.match(html, /<button\b[^>]*disabled[^>]*>.*?만드는 중/s);
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
