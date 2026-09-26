/* 모델 마이페이지 · 내 얼굴 찾기 신고(2026-09-27).

   모델이 발견한 이미지를 올리면 서버가 배포본과 대조해 관리자에게 알린다. 모델 화면에서 지킬 것:
   ① 셀러·배포본 정보를 보여 주지 않는다(접수와 관리자 판정 상태만).
   ② 올린 이미지는 저장하지 않는다고 말하고, 페이지 주소를 함께 받는다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  SIGHTING_MAX_BYTES, sightingStatusLabel, validateSighting,
} from '../../src/features/model/mypage/sightingReport.js';
import { findTree, flush, traceFindingsHarness, treeText } from './helpers/traceFindingsHarness.mjs';

const root = new URL('../../', import.meta.url);
const read = name => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');
const img = { name: 'found.jpg', type: 'image/jpeg', size: 2048 };

test('입력 검사 — 서버와 같은 한도', () => {
  assert.equal(SIGHTING_MAX_BYTES, 30 * 1024 * 1024);
  assert.equal(validateSighting({ file: null }), '발견한 이미지를 골라 주세요.');
  assert.match(validateSighting({ file: { type: 'application/pdf', size: 1 } }), /이미지 파일만/);
  assert.match(validateSighting({ file: { ...img, size: SIGHTING_MAX_BYTES + 1 } }), /30MB/);
  assert.match(validateSighting({ file: img, pageUrl: 'javascript:alert(1)' }), /http/);
  assert.match(validateSighting({ file: img, pageUrl: `https://${'a'.repeat(600)}` }), /500자/);
  assert.match(validateSighting({ file: img, note: 'x'.repeat(1001) }), /1000자/);
  assert.equal(validateSighting({ file: img, pageUrl: ' https://shop.example/1 ', note: '' }), '');
});

test('판정 상태는 모델에게 쉬운 말로', () => {
  assert.equal(sightingStatusLabel('new'), '확인하고 있어요');
  assert.equal(sightingStatusLabel('misuse'), '허락 없이 쓰인 걸 확인했어요');
  assert.equal(sightingStatusLabel('seller_own'), '라이선스로 허락된 사용이에요');
  assert.equal(sightingStatusLabel('dismissed'), '내 얼굴과 관련 없는 이미지였어요');
});

test('API 배선 — 멀티파트로 이미지·주소·메모를 보낸다', async () => {
  const h = await traceFindingsHarness();
  const original = globalThis.fetch;
  const sent = [];
  globalThis.fetch = async (url, opts) => { sent.push({ url, opts }); return new Response(JSON.stringify({ id: 'f-1', status: 'received' }), { status: 201 }); };
  try {
    const api = await h.api();
    const file = new Blob(['x'], { type: 'image/jpeg' });
    const out = await api.reportSighting(file, { pageUrl: ' https://shop.example/1 ', note: '제 얼굴' });
    assert.deepEqual(out, { id: 'f-1', status: 'received' });
    assert.ok(sent[0].url.endsWith('/v1/facemarket/me/sightings'));
    assert.equal(sent[0].opts.method, 'POST');
    const form = sent[0].opts.body;
    assert.ok(form.get('image'));
    assert.equal(form.get('pageUrl'), 'https://shop.example/1');
    assert.equal(form.get('note'), '제 얼굴');
    assert.equal(sent[0].opts.headers.Authorization, 'Bearer tok');
    await api.listMySightings();
    assert.equal(h.runtime.calls.at(-1).path, '/v1/facemarket/me/sightings');
  } finally { globalThis.fetch = original; await h.close(); }
});

test('신고 흐름 — 접수 뒤 목록을 다시 불러오고 셀러 정보는 없다', async () => {
  const sent = [];
  let lists = 0;
  const h = await traceFindingsHarness({
    listMySightings: async () => { lists += 1; return { items: lists > 1 ? [{ id: 'f-1', status: 'new', createdAt: '2026-09-27T01:00:00Z', pageUrl: 'https://shop.example/1' }] : [] }; },
    reportSighting: async (file, fields) => { sent.push([file, fields]); return { id: 'f-1', status: 'received' }; },
  });
  try {
    const render = await h.component('/src/features/model/mypage/MyPageSightings.jsx', 'MyPageSightings');
    render();
    await flush();
    let tree = render();
    assert.match(treeText(tree), /저장하지 않아요/);
    findTree(tree, n => n.type === 'button' && treeText(n) === '내 얼굴 찾기 신고').props.onClick();
    tree = render();
    findTree(tree, n => n.type === 'input' && n.props?.type === 'file').props.onChange({ target: { files: [img] } });
    tree = render();
    findTree(tree, n => n.type === 'input' && n.props?.type === 'url').props.onChange({ target: { value: 'https://shop.example/1' } });
    tree = render();
    await findTree(tree, n => n.type === 'form').props.onSubmit({ preventDefault() {} });
    await flush();
    tree = render();
    assert.deepEqual(sent, [[img, { pageUrl: 'https://shop.example/1', note: '' }]]);
    assert.match(treeText(findTree(tree, n => n.props?.role === 'status')), /접수했어요/);
    assert.equal(lists, 2);
    assert.match(treeText(tree), /확인하고 있어요/);
  } finally { await h.close(); }
});

test('서버 오류(하루 한도 등)는 폼 안에 알린다', async () => {
  const h = await traceFindingsHarness({
    listMySightings: async () => ({ items: [] }),
    reportSighting: async () => { throw Object.assign(new Error('제보는 하루 10건까지 보낼 수 있어요.'), { status: 429 }); },
  });
  try {
    const render = await h.component('/src/features/model/mypage/MyPageSightings.jsx', 'MyPageSightings');
    render();
    await flush();
    let tree = render();
    findTree(tree, n => n.type === 'button' && treeText(n) === '내 얼굴 찾기 신고').props.onClick();
    tree = render();
    findTree(tree, n => n.type === 'input' && n.props?.type === 'file').props.onChange({ target: { files: [img] } });
    tree = render();
    await findTree(tree, n => n.type === 'form').props.onSubmit({ preventDefault() {} });
    tree = render();
    assert.equal(treeText(findTree(tree, n => n.props?.role === 'alert')), '제보는 하루 10건까지 보낼 수 있어요.');
  } finally { await h.close(); }
});

test('마이페이지 사용된 페이지 탭에 붙는다', () => {
  const dash = read('src/features/model/mypage/MyPageDashboard.jsx');
  assert.ok(dash.includes('<MyPageSightings />'));
});
