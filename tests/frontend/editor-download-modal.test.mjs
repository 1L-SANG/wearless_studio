import test, { after, before } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import { buildInfoBlock, defaultInfoFor } from '../../src/features/editor/presets/infoPresets.js';
import { buildExportSummary, renderStorefrontFiles } from '../../src/features/editor/editorExport.js';

const root = new URL('../..', import.meta.url).pathname;
let vite;
let EditorDownloadModal;

before(async () => {
  vite = await createServer({
    optimizeDeps: { noDiscovery: true, include: [] },
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

function render(settings = { preset: 'smartstore', format: 'jpeg', delivery: 'split' }, blocks = [], busy = false, state = {}) {
  return renderToStaticMarkup(React.createElement(EditorDownloadModal, {
    settings,
    blocks,
    summary: buildExportSummary([1000, 2000, 4000], settings),
    productName: '셔츠',
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
    '쇼핑몰에 맞는 크기와 형식을 골라 주세요.', '스마트스토어', '(기본)', '860px', '쿠팡', '780px',
    '카페24', '800px', '고화질', '1,290px', '몰에 따라 줄어들 수 있어요', '원본 PNG', '2,000px', '직접 입력', '640~2,000px',
    'JPG', '품질 (90%), 파일이 가벼움', 'PNG', '원본 화질, 파일이 무거움',
    '분할 이미지', '여러 장을 하나씩 ZIP으로 압축해서 다운받기', '통 이미지', '전부 들어간 이미지 한 장으로 다운받기',
  ]) assert.ok(html.includes(copy), copy);
  assert.match(html, /name="export-preset"[^>]*checked[^>]*value="smartstore"/);
  assert.match(html, /name="export-format"[^>]*checked[^>]*value="jpeg"/);
  assert.match(html, /name="export-delivery"[^>]*checked[^>]*value="split"/);
});

const noticeBlock = () => buildInfoBlock('required_notice', defaultInfoFor('required_notice', {
  productName: '셔츠', materials: [{ name: '면', ratio: 100 }], colorLabels: ['흰색'],
}));
const buttons = (html) => [...html.matchAll(/<button\b[^>]*>.*?<\/button>/gs)].map(([button]) => button);

test('확인 목록을 이미지 설정 위에 순서대로 표시하고 다운로드할 수 있다', () => {
  const block = noticeBlock();
  const before = structuredClone(block);
  const html = render(undefined, [block, { id: 'photos', elements: [
    { type: 'image', genPending: 'live' }, { type: 'image' }, { type: 'image', genFailed: true },
  ] }]);
  assert.match(html, /받기 전에 확인해 주세요.*그대로 받아도 돼요.*만들지 못한 컷 1장.*빈 사진 자리 1곳.*비어 있는 고시정보 6칸.*아직 만드는 중인 컷 1장.*이미지 크기/s);
  assert.match(html, /치수 · 제조자\(수입자\) · 제조국 · 제조연월 · 품질보증기준 · A\/S 책임자와 전화번호/);
  assert.match(html, /<li\b[^>]*><span[^>]*>아직 만드는 중인 컷 1장<span[^>]*>다 되면 자동으로 들어가요<\/span><\/span><\/li>/);
  const downloadButton = buttons(html).find((button) => button.endsWith('다운로드</button>'));
  assert.ok(downloadButton);
  assert.doesNotMatch(downloadButton, /\bdisabled/);
  assert.deepEqual(block, before, '다운로드 창이 고시 내용이나 저장 문서를 바꾸지 않는다');
});

test('확인 항목을 모두 채우면 다운로드 창에 상자가 없다', () => {
  const block = noticeBlock();
  block.elements = block.elements.map((el) => el.text === '정보 입력 필요' ? { ...el, text: '셀러 입력값' } : el);
  const html = render(undefined, [block]);
  assert.doesNotMatch(html, /받기 전에 확인할 것|받기 전에 확인해 주세요|그대로 받아도 돼요/);
  assert.match(html, />다운로드<\/button>/);
});

test('처리 중에는 다운로드와 취소와 옵션과 블록 이동을 잠근다', () => {
  const html = render(undefined, [noticeBlock()], true);
  assert.equal(buttons(html).length, 3);
  assert.match(html, /<button[^>]*disabled[^>]*>채우기<\/button>/);
  for (const button of buttons(html)) assert.match(button, /\bdisabled/);
  for (const [input] of html.matchAll(/<input\b[^>]*>/g)) assert.match(input, /\bdisabled/);
  assert.match(html, /role="status"[^>]*aria-live="polite"/);
  assert.match(html, /다운로드 준비 중/);
});

test('블록 처리 진행 상태는 작업 단계와 완료 수를 분리해서 보여준다', () => {
  const html = render(undefined, [], true, { progress: { done: 2, total: 8, stage: 'capture' } });
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
    const html = render(undefined, [], true, { progress: { done: 8, total: 8, stage } });
    assert.ok(html.includes(title), stage);
    assert.match(html, /<progress\b[^>]*aria-label="다운로드 처리 중"/);
    assert.doesNotMatch(html, /<progress\b[^>]*\bvalue=/);
  }
});

test('이전 진행 문자열도 준비 문구와 함께 표시한다', () => {
  const html = render(undefined, [], true, { progress: '3/12' });
  assert.match(html, /다운로드 준비 중/);
  assert.match(html, /3\/12/);
});

test('실패 이유를 창 안에 남기고 다운로드를 다시 시도할 수 있다', () => {
  const html = render(undefined, [], false, { error: '이미지를 만들지 못했어요. 나눠서 받아주세요.' });
  assert.match(html, /role="alert"/);
  assert.match(html, /이미지를 만들지 못했어요\. 나눠서 받아주세요/);
  assert.match(html, /설정을 바꾸거나 다시 시도해 주세요/);
  const retry = buttons(html).find((button) => button.endsWith('다시 시도</button>'));
  assert.ok(retry);
  assert.doesNotMatch(retry, /\bdisabled/);
  assert.doesNotMatch(html, /<progress/);
});

test('파일명 아래에 요약 한 줄과 필요한 경우 저장할 이름을 표시한다', () => {
  const html = render(undefined, [], false, { productName: '  셔츠? ' });
  assert.match(html, /파일명.*value="  셔츠\? ".*약 4개 파일, 약 1\.2MB, 총 6,020px.*저장할 이름: 셔츠/s);
  assert.doesNotMatch(html, /다운로드 요약|최종 크기|예상 용량|쇼핑몰 화면에서 순서를|ZIP 안 파일명은 영문과 숫자/);
});

test('원본 2,000px 선택은 형식을 PNG로 고정한다', () => {
  const html = render({ preset: 'original', format: 'jpeg', delivery: 'single' });
  assert.match(html, /name="export-format"[^>]*disabled[^>]*value="jpeg"/);
  assert.match(html, /name="export-format"[^>]*checked[^>]*value="png"/);
  assert.match(html, /원본 2,000px는 PNG로만 저장해요/);
});

test('일반 폭과 원본 PNG에서 미리보기 점선, 요약과 실제 조각 수가 일치한다', async () => {
  for (const preset of ['smartstore', 'original']) {
    const settings = { preset, delivery: 'split' };
    const measured = [
      { height: 1000, pageSectionKey: 'ab' }, { height: 2000, pageSectionKey: 'ab' },
      { height: 10000, pageSectionKey: 'c' },
    ];
    const summary = buildExportSummary(measured, settings);
    const fileCount = 4;
    const canvas = (width, height) => ({ width, height, getContext: () => ({ drawImage() {} }) });
    const rendered = await renderStorefrontFiles(measured, settings, {
      measureBlock: (block) => block,
      captureBlock: async (_, __, ___, slices) => ({
        canvases: slices.map(({ height }) => canvas(summary.width, height)), softFailed: 0,
      }),
      createCanvas: canvas, releaseCanvas() {}, encodeCanvas: async ({ height }) => ({ height, size: 100 }),
    });
    assert.equal(rendered.files.length, fileCount);
    assert.deepEqual(rendered.files.map((file) => file.height), summary.pieceHeights);
    const blocks = ['a', 'b', 'c'].map((id) => ({ id, elements: [] }));
    const html = render(settings, blocks, false, { summary, renderPreviewBlock: (block) => `preview-${block.id}` });
    assert.match(html, new RegExp(`점선에서 ${fileCount}장으로 나뉘어요`));
    assert.match(html, new RegExp(`${fileCount}개 파일`));
    assert.match(html, /preview-a.*preview-b.*preview-c/s);
    const cuts = html.match(/<svg[^>]+previewCuts[^>]*>(.*?)<\/svg>/s)[1];
    assert.equal([...cuts.matchAll(/<line\b/g)].length, fileCount - 1);
    assert.equal([...cuts.matchAll(/<text\b/g)].length, fileCount);
    assert.ok(cuts.includes('translate(0 600)'), '첫 점선은 해당 블록 경계에 놓인다');
  }
});

test('통 이미지 분할 안내는 파일명 아래에 있고 미리보기에는 분할 번호가 나온다', () => {
  const settings = { preset: 'original', delivery: 'single' };
  const html = render(settings, [], false, { summary: buildExportSummary([11000], settings) });
  assert.match(html, /점선에서 3장으로 나뉘어요.*파일명.*3개 파일.*세로가 20,000px를 넘어/s);
  assert.match(render({ preset: 'smartstore', delivery: 'single' }), /한 장으로 저장돼요/);
});

test('직접 입력이 비거나 범위를 벗어나면 다운로드를 막는다', () => {
  for (const customWidth of [undefined, 639, 2001, 640.5]) {
    const html = render({ preset: 'custom', customWidth });
    assert.match(html, /type="number"[^>]*aria-invalid="true"/);
    assert.match(html, /640~2,000px 사이로 넣어 주세요/);
    assert.match(html, /가로폭을 확인해 주세요/);
    assert.match(buttons(html).find((button) => button.endsWith('다운로드</button>')), /\bdisabled/);
  }
  const html = render({ preset: 'custom', customWidth: 900 });
  assert.doesNotMatch(html, /가로폭을 확인해 주세요|640~2,000px 사이로 넣어 주세요/);
  assert.doesNotMatch(buttons(html).find((button) => button.endsWith('다운로드</button>')), /\bdisabled/);
});
