import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { componentModule, hooks } from './helpers/reactHarness.mjs';
import * as draftHelpers from '../../src/features/editor/mallRegisterDraft.js';
import * as noticeHelpers from '../../src/features/editor/mallNotice.js';
import { createMallRegisterSession } from '../../src/features/editor/mallRegisterSession.js';
const walk = (node) => [node, ...React.Children.toArray(node?.props?.children).flatMap(walk)];
const delay = () => new Promise((resolve) => setTimeout(resolve, 5));
async function harness(api) {
  const runtime = hooks(); const order = [];
  const { MallRegisterModal } = await componentModule('src/features/editor/MallRegisterModal.jsx', ['MallRegisterModal'], {
    ...runtime, ...draftHelpers, ...noticeHelpers, createMallRegisterSession, api, styles: {}, clientEvents: { getAccountGeneration: () => 0 },
    Modal: ({ children }) => children, Button: 'button', MallRegisterFields: 'fields', MallNoticeFields: 'notice', MallRegisterResults: 'results',
  });
  const props = { projectId: 'modal-project', blocks: [], product: { name: '셔츠' }, assets: [{ assetId: 'main', src: '/local.jpg' }], initialMall: 'cafe24',
    malls: { items: [{ mall: 'cafe24', status: 'connected', registration: { status: 'available' } }] },
    waitForSave: async () => { order.push('save'); return 2; }, onPublish: async (_assert, revision) => { order.push('publish'); return { publicationId: 'pub', editorRevision: revision }; } };
  const render = () => runtime.render(MallRegisterModal, props);
  return { runtime, order, props, render, html: () => renderToStaticMarkup(render()) };
}
test('registration modal restores history before saving and preparing the clicked mall', async () => {
  const calls = []; const h = await harness({ listMallRegistrations: async () => { calls.push('history'); return { items: [], recoveries: {} }; },
    getSalesDefaults: async () => ({}), getMallRegistrationCatalog: async () => ({ complete: true, priceBasis: 'A', categories: [] }) });
  assert.match(h.html(), /확인하고 등록해요/); h.runtime.flushEffects(); await delay();
  assert.deepEqual(calls, ['history']); assert.deepEqual(h.order, ['save', 'publish']);
  const tree = h.render(), fields = walk(tree).find((node) => node.type === 'fields');
  assert.deepEqual(fields.props.draft.selectedMalls, ['cafe24']); assert.equal(fields.props.draft.editorRevision, 2);
  const result = await harness({ listMallRegistrations: async () => { throw new Error('history unavailable'); } });
  result.render(); result.runtime.flushEffects(); await delay(); assert.deepEqual(result.order, []); assert.match(result.html(), /history unavailable/);
});

test('registration modal restores unfinished results without saving or publishing', async () => {
  const view = { mall: 'cafe24', jobId: 'unknown', status: 'done', outcome: 'unknown', retryAction: 'verify' };
  const h = await harness({ listMallRegistrations: async () => ({ items: [view], recoveries: { cafe24: [view] } }) });
  h.render(); const cleanups = h.runtime.flushEffects(); await delay(); assert.deepEqual(h.order, []);
  const tree = h.render(); assert.ok(walk(tree).some((node) => node.type === 'results'));
  assert.match(h.html(), /등록 여부를 확인해 주세요/); cleanups.forEach((cleanup) => cleanup?.());
});
