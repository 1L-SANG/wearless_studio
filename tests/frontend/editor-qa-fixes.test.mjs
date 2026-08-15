/* 2026-08-16 에디터 QA 수정(3-03~3-15 + 오너 추가 지시)의 동작 계약.
   소스 정규식이 아니라 순수 함수로 검증할 수 있는 것들을 여기 모은다. */
import test from 'node:test';
import assert from 'node:assert/strict';

import { DEFAULT_READ_RETRY_DELAYS, isRetryableReadError, retryRead } from '../../src/lib/retryRead.js';
import { buildColorOpts, colorLabelOf, visibleColorOpts } from '../../src/lib/colorOpts.js';
import { classifyEditorLoadError } from '../../src/features/editor/editorLoadError.js';
import { mergeServerBlocks } from '../../src/lib/editorWaitSkeleton.js';

/* ---------- 3-14 콘티 조회 자동 재시도 ---------- */

test('retryRead: 일시 장애는 조용히 다시 시도해 성공으로 끝낸다', async () => {
  let calls = 0;
  const slept = [];
  const value = await retryRead(async () => {
    calls += 1;
    if (calls < 3) { const e = new Error('502'); e.status = 502; throw e; }
    return 'ok';
  }, { sleep: async (ms) => { slept.push(ms); } });
  assert.equal(value, 'ok');
  assert.equal(calls, 3);
  assert.deepEqual(slept, DEFAULT_READ_RETRY_DELAYS.slice(0, 2));
});

test('retryRead: 4xx 는 다시 보내도 같은 답이라 즉시 포기한다', async () => {
  let calls = 0;
  await assert.rejects(() => retryRead(async () => {
    calls += 1;
    const e = new Error('404'); e.status = 404; throw e;
  }, { sleep: async () => {} }));
  assert.equal(calls, 1, '재시도하지 않는다');
  assert.equal(isRetryableReadError({ status: 500 }), true);
  assert.equal(isRetryableReadError({ status: 403 }), true === false);
  assert.equal(isRetryableReadError(new Error('network')), true, '네트워크 오류는 일시적으로 본다');
});

test('retryRead: 재시도를 다 쓰면 마지막 오류를 그대로 던진다', async () => {
  const fail = async () => { const e = new Error('boom'); e.status = 503; throw e; };
  await assert.rejects(
    () => retryRead(fail, { delays: [1, 1], sleep: async () => {} }),
    /boom/,
  );
});

/* ---------- X-C 의류 색상 원 ↔ 이름 불일치 ---------- */

test('색상 라벨과 원 색은 같은 근거(swatchId)를 쓴다', () => {
  const catalogs = { swatchColors: [{ id: 'ivory', label: '아이보리' }] };
  // 오너가 본 화면: 스와치는 아이보리인데 이름만 시드 잔재 '블랙'으로 남아 있었다.
  const color = { id: 'c1', swatchId: 'ivory', name: '블랙' };
  assert.equal(colorLabelOf(color, catalogs, 0), '아이보리');
  // 스와치를 안 골랐으면 저장된 이름 → 그것도 없으면 순번
  assert.equal(colorLabelOf({ id: 'c2', name: '  민트  ' }, catalogs, 1), '민트');
  assert.equal(colorLabelOf({ id: 'c3', name: '' }, catalogs, 2), '색상 3');
});

test('색상 옵션 목록: 사진이 있거나 기준 색상만 노출된다', () => {
  const colors = [
    { id: 'c1', swatchId: 'ivory', isBase: true, images: [] },
    { id: 'c2', name: '네이비', images: [{ id: 'i1' }] },
    { id: 'c3', name: '', images: [] },
  ];
  const catalogs = { swatchColors: [{ id: 'ivory', label: '아이보리' }] };
  const all = buildColorOpts(colors, catalogs, (c) => (c.swatchId ? '#f3eee1' : '#123456'));
  assert.deepEqual(all.map((o) => o.label), ['아이보리', '네이비', '색상 3']);
  assert.deepEqual(visibleColorOpts(all, colors).map((o) => o.id), ['c1', 'c2']);
});

/* ---------- 3-05 에디터 첫 로딩 실패 ---------- */

test('로딩 실패 분류: 로그인 만료·없는 작업·일시 장애를 구분한다', () => {
  assert.equal(classifyEditorLoadError({ status: 401 }).kind, 'auth');
  assert.equal(classifyEditorLoadError({ message: '로그인이 필요해요' }).kind, 'auth');
  assert.equal(classifyEditorLoadError({ status: 404 }).kind, 'notFound');
  const network = classifyEditorLoadError({ status: 502, message: '서버가 응답하지 않아요' });
  assert.equal(network.kind, 'network');
  assert.equal(network.message, '서버가 응답하지 않아요');
  assert.match(classifyEditorLoadError({}).message, /다시 시도/);
});

/* ---------- 3-09 실패 컷이 조용히 빈칸이 되지 않는다 ---------- */

const waitBlocks = [{
  id: 'b1',
  elements: [
    { id: 'e1', type: 'image', sourceBlockId: 'sb1', src: null, genPending: 'wait' },
    { id: 'e2', type: 'image', sourceBlockId: 'sb2', src: null, genPending: 'wait' },
  ],
}];
const serverBlocks = [{
  id: 'b1',
  elements: [
    { id: 'e1', type: 'image', sourceBlockId: 'sb1', src: '/v1/assets/a1/file' },
    { id: 'e2', type: 'image', sourceBlockId: 'sb2', src: null },
  ],
}];

test('완료 병합: 못 만든 컷은 표식을 남기고, 만든 컷은 그대로 채운다', () => {
  const merged = mergeServerBlocks(waitBlocks, serverBlocks, new Set(['sb2']));
  const [ok, failed] = merged[0].elements;
  assert.equal(ok.src, '/v1/assets/a1/file');
  assert.ok(!ok.genFailed, '성공한 컷에는 실패 표식이 없다');
  assert.equal(failed.src, null);
  assert.equal(failed.genFailed, true, '실패 컷은 일반 빈 슬롯으로 둔갑하지 않는다');
  // 표식은 blocks 에 저장되므로 새로고침 후에도 남는다(예전엔 완전 소실).
  assert.ok(!('genPending' in failed), '대기용 표식은 걷어낸다');
});

test('완료 병합: 실패 목록을 안 넘기면 예전과 똑같이 동작한다(하위 호환)', () => {
  const merged = mergeServerBlocks(waitBlocks, serverBlocks);
  assert.equal(merged[0].elements[1].src, null);
  assert.ok(!merged[0].elements[1].genFailed);
});
