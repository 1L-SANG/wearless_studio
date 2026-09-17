import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { StaticRouter } from 'react-router-dom/server.js';
import { createServer } from 'vite';
import { facemarketRootTarget } from '../../src/features/facemarket-landing/facemarketRootTarget.js';
import { SLOTS, PHOTO_GROUPS } from '../../src/features/model/registerSlots.js';
import { PHOTO_GUIDE_ASSETS } from '../../src/features/model/photoGuideAssets.js';
import { findTree, modelComponentHarness } from './helpers/facemarketHarness.mjs';

const root = new URL('../..', import.meta.url).pathname;
const cache = mkdtempSync(join(tmpdir(), 'fm-photo-guide-test-'));
let vite;
before(async () => {
  vite = await createServer({ configFile: false, root, cacheDir: cache, logLevel: 'silent', server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom', esbuild: { jsx: 'automatic' }, resolve: { alias: { '@': `${root}/src` } } });
});
after(async () => { await vite?.close(); rmSync(cache, { recursive: true, force: true }); });

test('공개 촬영 가이드가 업로드와 같은 순서로 18장 모두를 안내한다', async () => {
  const { PhotoGuide } = await vite.ssrLoadModule('/src/features/model/PhotoGuide.jsx');
  const html = renderToStaticMarkup(React.createElement(StaticRouter, null, React.createElement(PhotoGuide)));
  assert.deepEqual(PHOTO_GROUPS.map(({ id }) => SLOTS.filter((slot) => slot.group === id).length), [9, 3, 3, 3]);
  for (const slot of SLOTS) assert.ok(html.includes(slot.hint), `${slot.n}번 촬영 안내가 있어야 한다`);
  assert.deepEqual(Object.keys(PHOTO_GUIDE_ASSETS).sort(), SLOTS.map((slot) => slot.key).sort());
  for (const slot of SLOTS) {
    const asset = PHOTO_GUIDE_ASSETS[slot.key];
    assert.ok(existsSync(join(root, 'public', asset.src)), `${slot.n}번 이미지가 배포 폴더에 있어야 한다`);
    assert.ok(html.includes(`data-photo-example="${slot.key}"`));
    assert.ok(html.includes(`aria-label="${slot.n}번 ${slot.title} 촬영 예시"`));
  }
  for (const key of ['sh_side', 'sh_side_right']) assert.match(SLOTS.find((slot) => slot.key === key).title, /무표정/);
  assert.match(html, /약 15분/);
  assert.match(html, /도와줄 사람 1명/);
  assert.doesNotMatch(html, /type="checkbox"/);
  for (const title of ['안경 모자 벗기', '혼자 나오기', '후면카메라 촬영', '같은 날 찍기']) {
    assert.ok(html.indexOf(title) < html.indexOf('id="shoot-sh"'));
  }
  for (const { id } of PHOTO_GROUPS) {
    assert.ok(html.includes(`href="#shoot-${id}"`));
    assert.ok(html.includes(`id="shoot-${id}"`));
  }
  assert.equal(facemarketRootTarget('/photo-guide#shoot-sh'), '/photo-guide#shoot-sh');
  assert.equal(facemarketRootTarget('/photo-guide-evil'), null);
});

test('18장 진행률은 저장된 사진을 복원하고 현재 묶음의 부족한 장수를 안내한다', async () => {
  const photos = SLOTS.slice(0, 10).map((slot) => ({ slot: slot.key }));
  const enrollment = { id: 'enrollment-1', status: 'photos_pending', photos };
  const h = await modelComponentHarness({ initialStates: ['2', enrollment, 2], api: {} });
  try {
    const tree = h.render();
    const progress = findTree(tree, (node) => node.type === 'progress');
    assert.equal(progress.props.value, 10);
    assert.equal(progress.props.max, 18);
    const input = findTree(tree, (node) => node.type === 'input' && node.props['aria-label'] === '10번 정면 · 무표정 사진 바꾸기');
    assert.ok(input);
    assert.ok(findTree(tree, (node) => node.props.id === input.props['aria-describedby']));
    const status = findTree(tree, (node) => node.props.role === 'status');
    assert.equal(status.props.children, '2장을 더 올려 주세요.');
    assert.equal(findTree(tree, (node) => node.type === 'button' && node.props.children === '다음').props.disabled, true);
  } finally { await h.close(); }
});
