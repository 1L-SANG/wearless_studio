import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { randomUUID } from 'node:crypto';
import { createEditorRevisionSync } from '../../src/lib/editorRevisionSync.js';
import { componentModule, hooks } from './helpers/reactHarness.mjs';

const walk = (node) => [node, ...React.Children.toArray(node?.props?.children).flatMap(walk)];
const byName = (tree, name) => walk(tree).find((node) => node.props?.name === name);
const button = (tree, label) => walk(tree).find((node) => node.props?.onClick && renderToStaticMarkup(node).includes(label));
const settle = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
const publication = { publicationId: 'pub-current', status: 'ready', editorRevision: 4, productName: '니트', files: [{ index: 1 }] };
async function setup({ api: overrides = {}, strictEffects = false, ...extra } = {}) {
  const runtime = hooks(), calls = [];
  const api = {
    getEditorBlocks: async () => [],
    peekEditorRevision: async () => { calls.push('revision'); return 4; }, editorRevisionOf: () => 4,
    listDetailPublications: async () => ({ items: [publication] }), listCafe24Categories: async () => [{ categoryNo: 10, fullName: '의류 > 상의' }],
    createCafe24Registration: async (payload) => { calls.push(payload); return { jobId: 'job-one' }; },
    pollJob: async () => {}, getCafe24Registration: async () => ({ outcome: 'created', productNo: 123, thumbnail: 'none' }),
    ...overrides,
  };
  const { MallRegisterModal } = await componentModule('src/features/editor/MallRegisterModal.jsx', ['MallRegisterModal'], {
    ...runtime, useEffect: (effect, deps) => runtime.useEffect(strictEffects ? () => { effect()?.(); return effect(); } : effect, deps),
    api, styles: {}, Button: ({ children, ...props }) => React.createElement('button', props, children),
    Icon: () => null, Modal: ({ children }) => children, Link: ({ to, children, ...props }) => React.createElement('a', { href: to, ...props }, children),
    clientEvents: { getAccountGeneration: () => 0 }, crypto: { randomUUID },
  });
  const props = { projectId: 'project-one', productName: '니트', connection: { shopName: '스토어', mallId: 'myshop' },
    waitForSave: async () => { calls.push('save'); }, onPublish: async () => { calls.push('publish'); return publication; },
    assets: [{ assetId: 'asset-one', src: '/v1/assets/asset-one/file' }], onClose() {}, ...extra };
  const render = () => runtime.render(MallRegisterModal, props);
  render(); const cleanups = runtime.flushEffects(); await settle();
  const price = (value = '39000') => byName(render(), 'price').props.onChange({ target: { value } });
  const confirm = () => button(render(), '카페24에 상품 등록').props.onClick();
  return { render, price, confirm, calls, props, api, runtime, unmount: () => cleanups.forEach((cleanup) => cleanup?.()) };
}

test('register button disabled until price entered', async () => {
  const ui = await setup();
  for (const value of ['', '0', '-1', '1.5', '2147483648']) {
    ui.price(value); assert.equal(button(ui.render(), '카페24에 상품 등록').props.disabled, true, value);
  }
  ui.price(); assert.equal(button(ui.render(), '카페24에 상품 등록').props.disabled, false);
  const markup = renderToStaticMarkup(ui.render());
  assert.match(markup, /없이 등록/); assert.match(markup, /<select[^>]*name="categoryNo"/); assert.match(markup, /<details/);
  byName(ui.render(), 'thumbnailAssetId').props.onChange({ target: { value: '' } });
  await ui.confirm(); assert.equal(ui.calls.find((value) => value?.requestKey).thumbnailAssetId, undefined);
  const blocked = await setup({ waitForSave: async () => { throw new Error('저장을 마쳐 주세요'); } });
  blocked.price(); assert.equal(button(blocked.render(), '카페24에 상품 등록').props.disabled, true);
});

test('stale prepare run never sets error or phase after a newer run becomes ready', async () => {
  let saves = 0, categoryLoads = 0, rejectSave, resolveCategories;
  const ui = await setup({ strictEffects: true,
    waitForSave: () => ++saves === 1 ? new Promise((_, reject) => { rejectSave = reject; }) : Promise.resolve(),
    api: { listCafe24Categories: () => ++categoryLoads === 1
      ? new Promise((resolve) => { resolveCategories = resolve; })
      : Promise.resolve([{ categoryNo: 20, fullName: '최신 카테고리' }]) },
  });
  assert.equal(saves, 2);
  ui.price(); assert.equal(button(ui.render(), '카페24에 상품 등록').props.disabled, false);
  rejectSave(new Error('편집 내용이 바뀌었어요. 다시 열어 주세요'));
  resolveCategories([{ categoryNo: 10, fullName: '이전 카테고리' }]);
  await settle();
  const tree = ui.render(), markup = renderToStaticMarkup(tree);
  assert.doesNotMatch(markup, /편집 내용이 바뀌었어요|role="alert"/);
  assert.equal(byName(tree, 'price').props.disabled, false);
  assert.equal(button(tree, '카페24에 상품 등록').props.disabled, false);
  assert.match(markup, /최신 카테고리/);
  assert.doesNotMatch(markup, /이전 카테고리/);
});

test('modal waits for save then publishes when latest publication revision differs', async () => {
  let saved, resolveSave, resolvePublish;
  const ui = await setup({ waitForSave: () => new Promise((resolve) => { resolveSave = () => { saved = true; resolve(); }; }),
    onPublish: () => new Promise((resolve) => { resolvePublish = resolve; }),
    api: { peekEditorRevision: async () => { assert.equal(saved, true); return 4; }, listDetailPublications: async () => ({ items: [{ ...publication, editorRevision: 3 }] }) } });
  ui.price(); assert.equal(button(ui.render(), '카페24에 상품 등록').props.disabled, true);
  resolveSave(); await settle();
  assert.match(renderToStaticMarkup(ui.render()), /지금 상세페이지를 먼저 게시해요/);
  assert.equal(button(ui.render(), '카페24에 상품 등록').props.disabled, true);
  assert.equal(ui.calls.filter((entry) => entry?.requestKey).length, 0);
  resolvePublish(publication); await settle();
  assert.match(renderToStaticMarkup(ui.render()), /사진 1장/);
  await ui.confirm(); assert.equal(ui.calls[0].publicationId, 'pub-current');
  const current = await setup(); current.price(); await current.confirm(); assert.ok(!current.calls.includes('publish'));
});

test('opening the modal never advances the editor save baseline', async () => {
  const original = [{ id: 'text', text: 'A' }];
  let server = { revision: 4, blocks: original }, peeks = 0;
  const writes = [];
  const sync = createEditorRevisionSync({ load: async () => server,
    put: async (_id, body, baseRevision) => {
      writes.push(baseRevision);
      if (baseRevision !== server.revision) throw Object.assign(new Error('conflict'), { code: 'editor_conflict' });
      server = { revision: server.revision + 1, blocks: body };
      return server;
    } });
  await sync.load('project-one');
  server = { revision: 5, blocks: [{ id: 'text', text: 'B saved in another tab' }] };
  const ui = await setup({ waitForSave: () => sync.save('project-one', original), api: {
    getEditorBlocks: (id) => sync.load(id),
    peekEditorRevision: async () => { peeks++; return server.revision; },
    editorRevisionOf: (id) => sync.revisionOf(id),
  } });
  assert.equal(ui.api.editorRevisionOf('project-one'), 4);
  assert.equal(peeks, 1);
  assert.deepEqual(writes, []);
  ui.props.onClose(); ui.unmount();
  await assert.rejects(sync.save('project-one', [{ id: 'text', text: 'A edited after closing modal' }]),
    (error) => error.code === 'editor_conflict');
  assert.deepEqual(writes, [4]);
  assert.equal(server.blocks[0].text, 'B saved in another tab');
});

test('modal detects revision changed by another tab and routes to conflict recovery without publishing', async () => {
  let recovered = 0, listings = 0;
  const ui = await setup({ onConflict: () => recovered++, api: {
    peekEditorRevision: async () => 5,
    listDetailPublications: async () => { listings++; return { items: [] }; },
  } });
  assert.equal(recovered, 1);
  assert.equal(listings, 0);
  assert.equal(ui.calls.includes('publish'), false);
  assert.equal(ui.calls.some((entry) => entry?.requestKey), false);
  const markup = renderToStaticMarkup(ui.render());
  assert.match(markup, /role="alert"/);
  assert.match(markup, /편집 내용이 다른 창에서 바뀌었어요. 다시 불러온 뒤 등록해 주세요/);
  ui.price();
  assert.equal(button(ui.render(), '카페24에 상품 등록').props.disabled, true);
});

test('closed modal ignores a pending revision conflict', async () => {
  let recovered = 0, listings = 0, resolvePeek;
  const ui = await setup({ onConflict: () => recovered++, api: {
    peekEditorRevision: () => new Promise((resolve) => { resolvePeek = resolve; }),
    listDetailPublications: async () => { listings++; return { items: [] }; },
  } });
  ui.unmount();
  resolvePeek(5); await settle();
  assert.equal(recovered, 0);
  assert.equal(listings, 0);
  assert.equal(ui.calls.includes('publish'), false);
});

test('request key is created once per confirm and reused on retry', async () => {
  let attempts = 0; const received = [];
  const ui = await setup({ api: { createCafe24Registration: async (payload) => {
    received.push(payload); if (++attempts === 1) throw new Error('network'); return { jobId: 'job-one' };
  } } });
  ui.price(); await ui.confirm();
  await button(ui.render(), '다시 시도').props.onClick();
  assert.equal(received.length, 2); assert.equal(received[0].requestKey, received[1].requestKey);
  assert.deepEqual(received[0], received[1]); assert.match(received[0].requestKey, /^[\da-f-]{36}$/);
  const reopened = await setup(); reopened.price(); await reopened.confirm();
  assert.notEqual(reopened.calls.find((entry) => entry?.requestKey).requestKey, received[0].requestKey);
});

test('status screens map derived outcomes created unknown duplicate failed', async () => {
  for (const [outcome, copy] of [['created', '등록 완료'], ['unknown', '등록 여부를 확인하고 있어요'], ['duplicate_suspected', '같은 코드의 상품이 여러 개예요'], ['failed', '등록하지 못했어요']]) {
    const ui = await setup({ api: { getCafe24Registration: async () => ({ status: 'error', outcome, productNo: outcome === 'created' ? 123 : null, thumbnail: 'skipped', code: 'invalid_request' }) } });
    ui.price(); await ui.confirm(); const markup = renderToStaticMarkup(ui.render());
    assert.ok(markup.includes(copy), outcome);
    if (outcome === 'created') { assert.match(markup, /123/); assert.match(markup, /대표 이미지는 카페24 관리자에서 넣어 주세요/); }
  }
  let closed = 0, focused;
  const ui = await setup({ onClose: () => closed++ });
  const dialog = ui.render().props.children;
  const controls = [0, 1].map((index) => ({ focus: () => { focused = index; }, getClientRects: () => [1] }));
  const ownerDocument = { activeElement: controls[1] };
  dialog.ref.current = { querySelectorAll: () => controls, ownerDocument, focus() {} };
  let prevented = false, stopped = false;
  const key = (key, shiftKey = false) => dialog.props.onKeyDown({ key, shiftKey, preventDefault: () => { prevented = true; }, stopPropagation: () => { stopped = true; } });
  key('Tab'); assert.equal(focused, 0); assert.equal(prevented, true);
  ownerDocument.activeElement = controls[0]; key('Tab', true); assert.equal(focused, 1);
  key('ArrowDown'); assert.equal(stopped, true);
  key('Escape'); assert.equal(closed, 1);
});

test('error poll result still fetches registration detail for code and copy', async () => {
  let details = 0;
  const ui = await setup({ api: { pollJob: async (_id, options) => { assert.equal(options.timeoutMs, 600000); throw new Error('generic raw failure'); },
    getCafe24Registration: async () => { details++; return { status: 'error', outcome: 'failed', code: 'reconnect_required', errorMessage: '카페24 연결이 끊겼어요. 쇼핑몰 연결에서 다시 설치해 주세요' }; } } });
  ui.price(); await ui.confirm();
  const markup = renderToStaticMarkup(ui.render()); assert.equal(details, 1); assert.match(markup, /쇼핑몰 연결에서 다시 설치해 주세요/);
  assert.match(markup, /href="\/settings\/malls"/); assert.doesNotMatch(markup, /generic raw failure/);
});

test('poll timeout retains job id and request key without creating another job', async () => {
  let polls = 0, details = 0;
  const ui = await setup({ api: { pollJob: async (id) => { assert.equal(id, 'job-one'); if (++polls === 1) throw Object.assign(new Error('timeout'), { code: 'job_timeout', jobId: id }); },
    getCafe24Registration: async () => ++details === 1 ? { status: 'running', outcome: null } : { outcome: 'created', productNo: 123 } } });
  ui.price(); await ui.confirm(); const request = ui.calls.find((entry) => entry?.requestKey);
  assert.match(renderToStaticMarkup(ui.render()), /아직 진행 중이에요. 이 창을 닫아도 계속돼요/);
  await button(ui.render(), '상태 확인').props.onClick();
  assert.equal(ui.calls.filter((entry) => entry?.requestKey).length, 1); assert.ok(request.requestKey); assert.equal(polls, 2); assert.equal(details, 2);
  assert.match(renderToStaticMarkup(ui.render()), /등록 완료/);
});

test('unknown state offers verify button and never re-registers automatically', async () => {
  let verifies = 0, details = 0;
  const ui = await setup({ api: { getCafe24Registration: async () => (++details >= 3 ? { outcome: 'created', productNo: 123 } : { outcome: 'unknown', verifiable: true }),
    verifyCafe24Registration: async (id) => { assert.equal(id, 'job-one'); return { found: ++verifies === 2 }; } } });
  ui.price(); await ui.confirm(); assert.equal(verifies, 0);
  assert.ok(!button(ui.render(), '카페24에 상품 등록'));
  await button(ui.render(), '다시 확인').props.onClick();
  assert.match(renderToStaticMarkup(ui.render()), /아직 확인되지 않았어요/);
  await button(ui.render(), '다시 확인').props.onClick();
  assert.match(renderToStaticMarkup(ui.render()), /등록 완료/); assert.equal(ui.calls.filter((entry) => entry?.requestKey).length, 1);
  let checks = 0;
  const concurrent = await setup({ api: {
    getCafe24Registration: async () => ++checks === 1 ? { outcome: 'unknown', verifiable: true } : { status: 'done', outcome: 'created', productNo: 123 },
    verifyCafe24Registration: async () => { throw Object.assign(new Error('already verified'), { code: 'not_verifiable' }); },
  } });
  concurrent.price(); await concurrent.confirm(); await button(concurrent.render(), '다시 확인').props.onClick();
  assert.match(renderToStaticMarkup(concurrent.render()), /등록 완료/); assert.equal(checks, 2);
});
