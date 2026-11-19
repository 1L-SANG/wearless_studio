import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { componentModule, hooks } from './helpers/reactHarness.mjs';
import * as draftHelpers from '../../src/features/editor/mallRegisterDraft.js';
import * as noticeHelpers from '../../src/features/editor/mallNotice.js';
import { createMallRegisterSession } from '../../src/features/editor/mallRegisterSession.js';
import { downloadChecks } from '../../src/features/editor/editorDownloadWarnings.js';
import { buildInfoBlock } from '../../src/features/editor/presets/infoPresets.js';

const { DownloadChecklist } = await componentModule('src/features/editor/DownloadChecklist.jsx',
  ['DownloadChecklist'], { styles: {}, downloadChecks });
const walk = (node) => [node, ...React.Children.toArray(node?.props?.children).flatMap(walk)];
const delay = () => new Promise((resolve) => setTimeout(resolve, 5));
async function harness(api) {
  const runtime = hooks(); const order = [];
  const { MallRegisterModal } = await componentModule('src/features/editor/MallRegisterModal.jsx', ['MallRegisterModal'], {
    ...runtime, ...draftHelpers, ...noticeHelpers, createMallRegisterSession, DownloadChecklist, api, styles: {}, clientEvents: { getAccountGeneration: () => 0 },
    useLocation: () => ({ pathname: '/editor/local', search: '', hash: '' }), useNavigate: () => () => {}, Link: 'a', Modal: ({ children }) => children, Button: 'button', MallRegisterFields: 'fields', MallNoticeFields: 'notice', MallRegisterResults: 'results',
  });
  const props = { projectId: 'modal-project', blocks: [], product: { name: '셔츠' }, assets: [{ assetId: 'main', src: '/local.jpg' }], initialMall: 'cafe24',
    malls: { items: [{ mall: 'cafe24', status: 'connected', registration: { status: 'available' } }] },
    waitForSave: async () => { order.push('save'); return 2; }, onPublish: async (_assert, revision) => { order.push('publish'); return { publicationId: 'pub', editorRevision: revision }; } };
  const render = () => runtime.render(MallRegisterModal, props);
  return { runtime, order, props, render, html: () => renderToStaticMarkup(render()) };
}
test('registration modal restores history before saving and preparing the clicked mall', async () => {
  const calls = []; const h = await harness({ listMallRegistrations: async () => { calls.push('history'); return { items: [], recoveries: {} }; },
    getMallDefaults: async () => ({}), getMallRegistrationCatalog: async () => ({ complete: true, priceBasis: 'A', categories: [] }) });
  assert.match(h.html(), /확인하고 등록해요/); h.runtime.flushEffects(); await delay();
  assert.deepEqual(calls, ['history']); assert.deepEqual(h.order, ['save', 'publish']);
  const tree = h.render(), fields = walk(tree).find((node) => node.type === 'fields');
  assert.deepEqual(fields.props.draft.selectedMalls, ['cafe24']); assert.equal(fields.props.draft.editorRevision, 2);
  assert.match(h.html(), /에이블리 준비 중/); assert.doesNotMatch(h.html(), /에이블리 연결 필요/);
  const choices = walk(tree).filter((node) => node.type === 'input' && node.props.type === 'checkbox');
  assert.equal(choices.length, 3); assert.equal(choices[0].props.checked, true); assert.equal(choices[2].props.disabled, true);
  assert.ok(choices.every((node) => node.props.tabIndex !== -1 && !node.props['aria-hidden']));
  const result = await harness({ listMallRegistrations: async () => { throw new Error('history unavailable'); } });
  result.render(); result.runtime.flushEffects(); await delay(); assert.deepEqual(result.order, []); assert.match(result.html(), /history unavailable/);
});

test('registration modal restores unfinished results without saving or publishing', async () => {
  const view = { mall: 'cafe24', jobId: 'unknown', status: 'done', outcome: 'unknown', retryAction: 'verify' };
  const h = await harness({ listMallRegistrations: async () => ({ items: [view], recoveries: { cafe24: [view] } }) });
  h.props.blocks = [{ id: 'empty', elements: [{ type: 'image' }] }];
  h.render(); const cleanups = h.runtime.flushEffects(); await delay(); assert.deepEqual(h.order, []);
  const tree = h.render(); assert.ok(walk(tree).some((node) => node.type === 'results'));
  assert.match(h.html(), /등록 여부를 확인해 주세요/);
  assert.doesNotMatch(h.html(), /받기 전에 확인해 주세요/);
  cleanups.forEach((cleanup) => cleanup?.());
});

test('몰 등록 창은 확인할 항목이 없으면 상자를 표시하지 않는다', async () => {
  const h = await harness({});
  h.props.blocks = [{ id: 'filled', elements: [{ type: 'image', src: '/ready.jpg' }] }];
  assert.doesNotMatch(h.html(), /받기 전에 확인할 것|받기 전에 확인해 주세요|그대로 받아도 돼요/);
});

test('몰 등록 준비 중에는 확인 목록의 이동을 잠그고, 준비가 끝나면 첫 블록으로 이동한다', async () => {
  const h = await harness({
    listMallRegistrations: async () => ({ items: [], recoveries: {} }),
    getMallDefaults: async () => ({}),
    getMallRegistrationCatalog: async () => ({ complete: true, categories: [] }),
  });
  const notice = buildInfoBlock('required_notice', { fields: [
    { key: 'origin', label: '제조국', value: '' }, { key: 'sizes', label: '치수', value: '' },
  ] });
  h.props.blocks = [
    { id: 'failed-first', elements: [{ type: 'image', genPending: 'failed' }] },
    notice,
    { id: 'failed-later', elements: [{ type: 'image', genFailed: true }] },
    { ...notice, id: 'notice-later' },
  ];
  const actions = [];
  h.props.onClose = () => actions.push('close');
  h.props.onJumpToBlock = (id) => actions.push(id);
  const checklistButtons = () => {
    const checklist = walk(h.render()).find((node) => node.type === DownloadChecklist);
    assert.ok(checklist);
    return walk(DownloadChecklist(checklist.props)).filter((node) => node.type === 'button');
  };

  assert.match(h.html(), /받기 전에 확인해 주세요.*만들지 못한 컷 2장.*비어 있는 고시정보 2칸.*제조국 · 치수.*이전 등록을 확인하고 있어요/s);
  const disabled = checklistButtons();
  assert.equal(disabled.length, 2);
  for (const button of disabled) {
    assert.equal(button.props.disabled, true);
    button.props.onClick();
  }
  assert.deepEqual(actions, []);

  const cleanups = h.runtime.flushEffects();
  await delay();
  const [view, fill] = checklistButtons();
  assert.equal(view.props.children, '보러 가기');
  assert.equal(fill.props.children, '채우기');
  for (const button of [view, fill]) {
    assert.equal(button.props.disabled, false);
    button.props.onClick();
  }
  assert.deepEqual(actions, ['close', 'failed-first', 'close', notice.id]);
  cleanups.forEach((cleanup) => cleanup?.());
});

test('unavailable zigzag connection is shown as preparing and stays disabled', async () => {
  const h = await harness({
    listMallRegistrations: async () => ({ items: [], recoveries: {} }),
    listMalls: async () => ({ items: [
      { mall: 'cafe24', status: 'connected', registration: { status: 'available' } },
      { mall: 'zigzag', status: 'unavailable', registration: { status: 'preparing' } },
    ] }),
    getMallDefaults: async () => ({}), getMallRegistrationCatalog: async () => ({ complete: true, categories: [] }),
  });
  h.render(); const cleanups = h.runtime.flushEffects(); await delay();
  assert.match(h.html(), /지그재그 준비 중/); assert.doesNotMatch(h.html(), /지그재그 연결 필요/);
  const choices = walk(h.render()).filter((node) => node.type === 'input' && node.props.type === 'checkbox');
  assert.equal(choices[1].props.disabled, true); assert.equal(choices[1].props.checked, false);
  cleanups.forEach((cleanup) => cleanup?.());
});

test('category placeholder stops loading after the catalog response even when settings fail', async () => {
  const runtime = hooks();
  const { MallRegisterFields } = await componentModule('src/features/editor/MallRegisterFields.jsx', ['MallRegisterFields'], {
    ...runtime, ...draftHelpers, styles: {}, Button: 'button',
  });
  const draft = draftHelpers.buildMallRegisterDraft({ projectId: 'modal-project', product: { name: '셔츠' }, selectedMall: 'cafe24' });
  const props = { draft, preflight: { context: { catalogs: {} } }, onChange: () => {} };
  const select = () => walk(runtime.render(MallRegisterFields, props)).find((node) => node.props?.['data-field'] === 'categories.cafe24');
  assert.match(renderToStaticMarkup(select()), /카테고리 확인 중/);
  for (const complete of [false, true]) {
    props.preflight.context.catalogs.cafe24 = { complete, categories: [{ categoryId: '27', name: '상의' }] };
    const html = renderToStaticMarkup(select());
    assert.match(html, /카테고리를 골라 주세요/); assert.match(html, /상의/); assert.doesNotMatch(html, /카테고리 확인 중/);
  }
});

test('open_defaults links to the affected mall and preserves editor returnTo', async () => {
  const h = await harness({
    listMallRegistrations: async () => ({ items: [], recoveries: {} }), getMallDefaults: async () => ({ revision: 1 }),
    getMallRegistrationCatalog: async () => ({ complete: true, categories: [] }),
    preflightMallRegistration: async () => ({ items: [{ mall: 'cafe24', status: 'blocked', issues: [
      { code: 'defaults_changed', action: 'open_defaults', message: '판매 정보를 확인해 주세요' },
    ] }] }),
  });
  h.render(); const cleanups = h.runtime.flushEffects(); await delay();
  let tree = h.render();
  const fields = walk(tree).find((node) => node.type === 'fields');
  fields.props.onChange({ ...fields.props.draft, basePrice: 39000 });
  h.render(); h.runtime.flushEffects(); await new Promise((resolve) => setTimeout(resolve, 280));
  tree = h.render();
  const link = walk(tree).find((node) => node.type === 'a' && node.props.to === '/settings/malls/cafe24');
  assert.ok(link); assert.deepEqual(link.props.state, { returnTo: '/editor/local' });
  cleanups.forEach((cleanup) => cleanup?.());
});
