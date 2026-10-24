import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

let vite, EditorPublishModal;
const source = () => readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
before(async () => {
  const root = new URL('../..', import.meta.url).pathname;
  vite = await createServer({ configFile: false, root, logLevel: 'silent',
    optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, watch: null, ws: false },
    appType: 'custom', resolve: { alias: { '@': `${root}/src` } }, esbuild: { jsx: 'automatic' } });
  ({ EditorPublishModal } = await vite.ssrLoadModule('/src/features/editor/EditorPublishModal.jsx'));
});
after(() => vite?.close());
const summary = { width: 860, height: 1000, fileCount: 2, approxBytes: 1048576, approximate: true };
const result = { publicationId: 'pub', version: 1, html: '<div>상세</div>', files: [{ index: 1, url: '/01.jpg' }] };
const props = (extra = {}) => ({ settings: { preset: 'smartstore' }, summary, busy: false, progress: null,
  error: '', result: null, previousCount: 0, realModelBlocked: false, onChange() {}, onCancel() {},
  onPublish() {}, onCopyHtml() {}, onCopyUrls() {}, onDownloadZip() {}, ...extra });
const render = (extra) => renderToStaticMarkup(React.createElement(EditorPublishModal, props(extra)));
const buttons = (html) => [...html.matchAll(/<button\b[^>]*>.*?<\/button>/gs)].map(([value]) => value);
test('renders width options without original', () => {
  const html = render();
  for (const text of ['860px', '780px', '1,290px', '지그재그, 에이블리, 스마트스토어 모두 860이면 충분해요.']) assert.ok(html.includes(text));
  assert.doesNotMatch(html, /원본 PNG|2,000px/);
  assert.equal((html.match(/type="radio"/g) || []).length, 3);
});
test('shows retention line', () => assert.match(render(), /구독 중에는 계속 보관해요\. 구독이 없으면 게시일부터 6개월 뒤 지워져요\. 언제든 ZIP으로 받을 수 있어요\./));
test('result view has three pill buttons inside modal-actions', () => {
  const html = render({ result, previousCount: 2 });
  assert.match(html, /class="modal-actions[^\"]*"[^>]*>.*HTML 복사.*이미지 주소 복사.*ZIP 받기/s);
  assert.equal(buttons(html.match(/<div class="modal-actions[^\"]*"[^>]*>(.*?)<\/div>/s)[1]).length, 3);
  assert.match(html, /이전 버전 2개가 남아 있어요\. 몰을 새 주소로 바꾼 뒤 보관함에서 지울 수 있어요\./);
  assert.equal((html.match(/<details\b/g) || []).length, 1);
});
test('result view has close button', () => {
  assert.match(render({ result }), /<footer\b[^>]*><div class="modal-actions[^\"]*"[^>]*><button\b[^>]*>닫기<\/button><\/div><\/footer>/);
});
test('publish modal cannot close while busy', () => {
  for (const button of buttons(render({ busy: true }))) assert.match(button, /disabled/);
  const match = source().match(/\{publish && \(\s*<Modal onClose=\{\(\) => \{ ([^}]+) \}\}/);
  assert.ok(match, 'Editor owns the Modal close gate');
  let closed = false;
  new Function('pubBusy', 'setPublish', match[1])(true, () => { closed = true; });
  assert.equal(closed, false);
  assert.match(source(), /<EditorPublishModal[\s\S]*?busy=\{pubBusy\}/);
});
test('real model shows blocked notice', () => {
  const html = render({ realModelBlocked: true });
  assert.match(html, /실존 모델 컷은 다운로드로 받아 몰에 직접 올려 주세요/);
  assert.match(buttons(html).find((value) => value.includes('코드 만들기')), /disabled/);
});
test('failed publication offers local ZIP and rejected clipboard leaves selectable HTML', () => {
  assert.match(render({ error: Object.assign(new Error('지금은 올리지 못했어요. 대신 ZIP으로 받아 두세요'), { files: [new Blob(['image'])] }) }), /ZIP 받기/);
  assert.match(render({ result: { ...result, showHtml: true } }), /<textarea[^>]*readonly[^>]*>&lt;div&gt;상세&lt;\/div&gt;<\/textarea>/);
});
test('local ZIP fallback reports ZIP packaging while publication has no result', () => {
  assert.match(render({ busy: true, result: null, progress: { stage: 'zip' } }), /ZIP으로 묶는 중/);
});

function action(name, bindings) {
  const match = source().match(new RegExp(`  const ${name} = async \\(([^)]*)\\) => \\{([\\s\\S]*?)\\n  \\};`));
  assert.ok(match, `Editor ${name} handler exists`);
  return new Function(...Object.keys(bindings), `return async (${match[1]}) => {${match[2]}\n};`)(...Object.values(bindings));
}
test('clipboard rejection reports the mandated toast and reveals HTML', async () => {
  let current = result; const toasts = [];
  const copy = action('copyPublication', { navigator: { clipboard: { writeText: async () => { throw new Error('denied'); } } },
    pubScopeRef: { current: 'p' }, pubScope: 'p', clientEvents: { getAccountGeneration: () => 1 },
    setPubResult: (update) => { current = update(current); }, toast: { push: (value) => toasts.push(value) } });
  await copy(result.html);
  assert.equal(current.showHtml, true);
  assert.deepEqual(toasts, ['복사하지 못했어요. 텍스트를 길게 눌러 복사해 주세요']);
});
test('late publication result cannot update another project or account', async () => {
  let resolve; const state = {}; const request = { current: 0 }, scope = { current: 'p' };
  const bindings = { pubBusy: false, dlBusy: false, genActive: false, realModelBlocked: false,
    pubCaptureRef: { current: false }, pubRequestRef: request, pubScopeRef: scope, pubScope: 'p',
    canvasRef: { current: { querySelectorAll: () => [{}] } }, clientEvents: { getAccountGeneration: () => 1 },
    publishDetailPage: () => new Promise((r) => { resolve = r; }), projectId: 'p', productName: '셔츠', pubSettings: { preset: 'smartstore' },
    setPubBusy: (value) => { state.busy = value; }, setPubCapturing() {}, setPubProg() {}, setPubError: (value) => { state.error = value; },
    setPubResult: (value) => { state.result = value; } };
  const pending = action('runPublish', bindings)();
  scope.current = 'other'; request.current++;
  resolve(result); await pending;
  assert.equal(state.result, null);
  assert.equal(state.error, '');
});
