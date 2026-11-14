import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { componentModule, hooks } from './helpers/reactHarness.mjs';
import { EXPORT_PRESETS, normalizeExportSettings } from '../../src/features/editor/editorExport.js';
import { getAiDraftSectionNames, formatAiDraftSectionNames } from '../../src/features/editor/editorAiDrafts.js';

const { PUBLISH_PRESETS } = await componentModule('src/features/editor/detailPublish.js', ['PUBLISH_PRESETS'], { EXPORT_PRESETS });

const blocks = [{ id: 'b', name: '후기', pageSectionKind: 'review', elements: [
  { id: 't', type: 'text', text: 'AI 후기', aiDraft: true },
] }];
const summary = { width: 860, height: 1000, fileCount: 1, approxBytes: 1000 };
const result = { files: [{ index: 1, url: '/image.jpg' }], html: '<p>상세</p>' };
const style = new Proxy({}, { get: (_, key) => String(key) });

// These are the real modal JSX trees and click handlers, with React hook state retained between renders.
async function mount(kind, extra = {}) {
  const runtime = hooks(), calls = [];
  const bindings = { ...runtime, Button: props => React.createElement('button', props, props.children),
    styles: style, shared: style, getAiDraftSectionNames, formatAiDraftSectionNames };
  const shared = await componentModule('src/features/editor/EditorAiDraftConfirmation.jsx',
    ['useAiDraftConfirmation', 'EditorAiDraftConfirmation'], bindings);
  const name = kind === 'download' ? 'EditorDownloadModal' : 'EditorPublishModal';
  const module = await componentModule(`src/features/editor/${name}.jsx`, [name], {
    ...bindings, ...shared, EXPORT_PRESETS, normalizeExportSettings, PUBLISH_PRESETS, RadioCard: () => null,
  });
  let props = { blocks, settings: { preset: 'smartstore' }, summary, busy: false, error: '',
    result: null, previousCount: 0, onChange() {}, onCancel: () => calls.push('cancel'),
    onDownload: () => calls.push('download'), onPublish: () => calls.push('publish'),
    onDownloadZip: () => calls.push('zip'), onCopyHtml() {}, onCopyUrls() {}, ...extra };
  let tree;
  const expand = node => {
    if (!node || typeof node !== 'object') return node;
    if (Array.isArray(node)) return node.map(expand);
    if (typeof node.type === 'function') return expand(node.type(node.props));
    return React.cloneElement(node, {}, React.Children.map(node.props.children, expand));
  };
  const find = (node, predicate) => !node || typeof node !== 'object' ? null : predicate(node) ? node
    : React.Children.toArray(node.props?.children).map(child => find(child, predicate)).find(Boolean);
  const render = (patch = {}) => {
    props = { ...props, ...patch };
    tree = runtime.render(() => expand(module[name](props)));
    return renderToStaticMarkup(tree);
  };
  const click = label => {
    const node = find(tree, item => item.type === 'button' && React.Children.toArray(item.props.children).join('') === label);
    assert.ok(node, `button ${label}`);
    assert.ok(!node.props.disabled, `${label} is enabled`);
    node.props.onClick();
    return render();
  };
  return { render, click, calls };
}

for (const kind of ['download', 'publish']) {
  const action = kind === 'download' ? '다운로드' : '코드 만들기';
  const proceed = kind === 'download' ? '그대로 다운로드' : '그대로 만들기';
  test(`${kind}: 초안이 없으면 기존 동작과 문구를 유지한다`, async () => {
    const modal = await mount(kind, { blocks: [] });
    assert.doesNotMatch(modal.render(), /AI 초안|돌아가서 고치기|그대로 (다운로드|만들기)/);
    modal.click(action);
    assert.deepEqual(modal.calls, [kind]);
  });
  test(`${kind}: 첫 시도는 확인으로 바뀌고 고치기는 창을 닫는다`, async () => {
    const modal = await mount(kind);
    assert.doesNotMatch(modal.render(), /AI 초안/);
    const html = modal.click(action);
    assert.deepEqual(modal.calls, []);
    assert.match(html, /후기에 AI 초안이 그대로 있어요/);
    assert.match(html, new RegExp(`class="modal-actions[^"]*"[^>]*>.*돌아가서 고치기.*${proceed}`, 's'));
    modal.click('돌아가서 고치기');
    assert.deepEqual(modal.calls, ['cancel']);
  });
  test(`${kind}: 그대로 진행한 뒤 오류 재시도는 다시 묻지 않고 재열기는 다시 묻는다`, async () => {
    const modal = await mount(kind);
    modal.render(); modal.click(action);
    modal.click(proceed);
    assert.deepEqual(modal.calls, [kind]);
    assert.doesNotMatch(modal.render({ busy: true }), /AI 초안/);
    assert.doesNotMatch(modal.render({ busy: false, error: '실패' }), /AI 초안/);
    modal.click('다시 시도');
    assert.deepEqual(modal.calls, [kind, kind]);
    const reopened = await mount(kind);
    reopened.render();
    assert.match(reopened.click(action), /AI 초안/);
    assert.deepEqual(reopened.calls, []);
  });
  test(`${kind}: 확인 중 설정을 바꿔도 최신 실행 함수를 쓴다`, async () => {
    const modal = await mount(kind);
    modal.render(); modal.click(action);
    modal.render({ [kind === 'download' ? 'onDownload' : 'onPublish']: () => modal.calls.push('latest') });
    modal.click(proceed);
    assert.deepEqual(modal.calls, ['latest']);
  });
}

for (const state of [{ result }, { error: { message: '실패', files: [new Blob(['image'])] } }]) {
  test(`publish: ${state.result ? '결과' : '실패 복구'} ZIP도 같은 질문을 거친다`, async () => {
    const modal = await mount('publish', state);
    modal.render();
    assert.match(modal.click('ZIP 받기'), /후기에 AI 초안이 그대로 있어요/);
    assert.deepEqual(modal.calls, []);
    modal.click('그대로 다운로드');
    assert.deepEqual(modal.calls, ['zip']);
    modal.click('ZIP 받기');
    assert.deepEqual(modal.calls, ['zip', 'zip']);
  });
}
test('publish: 코드 만들기에서 확인했다면 성공 뒤 ZIP과 실패 복구 ZIP은 다시 묻지 않는다', async () => {
  const modal = await mount('publish');
  modal.render(); modal.click('코드 만들기'); modal.click('그대로 만들기');
  modal.render({ result }); modal.click('ZIP 받기');
  modal.render({ result: null, error: { message: '실패', files: [{}] } }); modal.click('ZIP 받기');
  assert.deepEqual(modal.calls, ['publish', 'zip', 'zip']);
});
