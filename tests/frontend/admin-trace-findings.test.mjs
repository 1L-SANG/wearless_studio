/* 관리자 출처 추적 · 자동 발견 탭(2026-09-27).

   이 화면의 사고 두 가지를 잠근다:
   ① 지문 유사도 발견을 워터마크처럼 확정으로 보여 주는 것(엉뚱한 셀러에게 연락한다).
   ② 네이버 순찰 원문을 21일 넘게 들고 있다고 오해하게 하는 것 — 검색 API 특약상 지워지는 날을 적는다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  FINDING_STATUSES, findingMatchText, findingSourceLabel, findingStatusLabel, initialTraceTab,
  purgeNote,
} from '../../src/features/admin/adminTraceFindings.js';
import { findTree, flush, traceFindingsHarness, treeText } from './helpers/traceFindingsHarness.mjs';

const root = new URL('../../', import.meta.url);
const read = name => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

const naver = {
  id: 'f-1', source: 'patrol', platform: 'naver', productUrl: 'https://smartstore.naver.com/a/products/1',
  imageUrl: 'https://shopping-phinf.pstatic.net/1.jpg', productTitle: '데님 셔츠', storeName: '가게',
  externalPurgeAt: '2026-10-18T00:00:00+00:00', target: 'cut', outputRecordId: 'out-1',
  model: { id: 'm-1', displayName: '김*연' }, seller: { id: 's-1', emailMasked: 'se***@example.com', nameMasked: '김*러' },
  license: { status: 'active' }, method: 'phash', confidence: 'medium', phashDistance: 3,
  status: 'new', canRememberStore: true, firstSeenAt: '2026-09-27T01:00:00+00:00',
  lastSeenAt: '2026-09-27T01:00:00+00:00', seenCount: 1,
};
const report = {
  id: 'f-2', source: 'model_report', platform: 'report', reportPageUrl: 'https://shop.example/x',
  reportNote: '제 얼굴이에요', reporterModelName: '박*민', target: null, method: null, confidence: null,
  model: { id: null }, seller: { id: null }, license: {}, status: 'new', canRememberStore: false,
  firstSeenAt: '2026-09-27T02:00:00+00:00', lastSeenAt: '2026-09-27T02:00:00+00:00', seenCount: 1,
};

test('출처·상태·근거 문구 — 워터마크만 확정으로 말한다', () => {
  assert.equal(findingSourceLabel(naver), '네이버 순찰');
  assert.equal(findingSourceLabel({ source: 'patrol', platform: 'zigzag' }), '지그재그 순찰');
  assert.equal(findingSourceLabel(report), '모델 제보');
  assert.equal(findingMatchText({ method: 'watermark', confidence: 'high' }), '워터마크 일치');
  assert.match(findingMatchText({ method: 'phash', confidence: 'medium', phashDistance: 3 }), /유사도.*3\/64/);
  assert.doesNotMatch(findingMatchText({ method: 'phash', confidence: 'medium', phashDistance: 3 }), /일치|확정/);
  assert.equal(findingMatchText(report), '일치하는 배포본·컷 없음 — 직접 확인');
  assert.deepEqual(FINDING_STATUSES.map(s => s.value), ['new', 'misuse', 'seller_own', 'dismissed', '']);
  assert.equal(findingStatusLabel('seller_own'), '셀러 정상 사용');
});

test('네이버 원문 삭제 예정일과 삭제 후 문구', () => {
  assert.match(purgeNote(naver), /네이버 검색 결과 원문은 .*10월 18일.*지워져요/);
  assert.equal(purgeNote({ ...naver, externalPurgeAt: null, productUrl: null }), '네이버 검색 결과 원문은 보관 기간(21일)이 지나 지웠어요.');
  assert.equal(purgeNote({ ...naver, platform: 'zigzag', externalPurgeAt: null }), '');
});

test('슬랙 링크(?tab=found)로 들어오면 자동 발견 탭이 먼저 열린다', () => {
  assert.equal(initialTraceTab('?tab=found'), 'found');
  assert.equal(initialTraceTab(''), 'manual');
  assert.equal(initialTraceTab('?tab=weird'), 'manual');
});

test('API 배선 — 목록 쿼리와 판정 PATCH', async () => {
  const h = await traceFindingsHarness();
  try {
    const api = await h.api();
    await api.adminListTraceFindings({ status: 'new', source: 'patrol', cursor: 'c+/=', limit: 30 });
    const url = new URL(h.runtime.calls[0].path, 'http://t');
    assert.equal(url.pathname, '/v1/facemarket/admin/trace/findings');
    assert.equal(url.searchParams.get('status'), 'new');
    assert.equal(url.searchParams.get('source'), 'patrol');
    assert.equal(url.searchParams.get('cursor'), 'c+/=');
    assert.equal(url.searchParams.get('limit'), '30');
    await api.adminUpdateTraceFinding('f/1', { status: 'seller_own', rememberStore: true });
    assert.deepEqual(h.runtime.calls[1], {
      path: '/v1/facemarket/admin/trace/findings/f%2F1',
      options: { method: 'PATCH', body: { status: 'seller_own', rememberStore: true } },
    });
  } finally { await h.close(); }
});

test('자동 발견 목록 — 순찰·제보를 구분하고, 판정하면 목록에서 빠진다', async () => {
  const lists = [];
  const updates = [];
  const h = await traceFindingsHarness({
    adminListTraceFindings: async args => { lists.push(args); return { items: [naver, report], nextCursor: null }; },
    adminUpdateTraceFinding: async (id, body) => { updates.push([id, body]); return { id, status: body.status, storeRemembered: body.rememberStore }; },
  });
  try {
    const render = await h.component('/src/features/admin/AdminTraceFindings.jsx', 'AdminTraceFindings');
    render();
    await flush();
    let tree = render();
    assert.deepEqual(lists[0], { status: 'new', source: undefined, cursor: undefined });
    const shown = treeText(tree);
    assert.match(shown, /네이버 순찰/);
    assert.match(shown, /모델 제보/);
    assert.match(shown, /박\*민/);
    assert.match(shown, /se\*\*\*@example\.com/);
    assert.match(shown, /유사도/);
    assert.match(shown, /라이선스 유효/);
    assert.doesNotMatch(shown, /라이선스 active/);
    assert.match(shown, /일치하는 배포본·컷 없음/);
    const link = findTree(tree, n => n.type === 'a' && n.props?.href === naver.productUrl);
    assert.equal(link.props.rel, 'noreferrer noopener');
    assert.equal(link.props.target, '_blank');
    // 제보 행에는 판매처 기억 버튼이 없다
    const rows = findTree(tree, n => n.type === 'tbody').props.children;
    assert.doesNotMatch(treeText(rows[1]), /판매처 기억/);
    const remember = findTree(rows[0], n => n.type === 'button' && treeText(n) === '셀러 정상 사용 · 판매처 기억');
    await remember.props.onClick();
    tree = render();
    assert.deepEqual(updates, [['f-1', { status: 'seller_own', rememberStore: true }]]);
    assert.doesNotMatch(treeText(tree), /네이버 순찰/);        // 'new' 필터에서 빠졌다
    assert.match(treeText(findTree(tree, n => n.props?.role === 'status')), /판매처를 기억/);
    const misuse = findTree(findTree(tree, n => n.type === 'tbody'), n => n.type === 'button' && treeText(n) === '무단 사용');
    await misuse.props.onClick();
    assert.deepEqual(updates[1], ['f-2', { status: 'misuse', rememberStore: false }]);
  } finally { await h.close(); }
});

test('라우트·탭 배선 — 출처 추적 화면 안에 자동 발견 탭', () => {
  const page = read('src/features/admin/AdminTrace.jsx');
  assert.ok(page.includes('<AdminTraceFindings />'));
  assert.ok(page.includes('initialTraceTab('));
  assert.ok(read('src/apps/admin/App.jsx').includes('path="trace" element={<AdminTrace />}'));
});
