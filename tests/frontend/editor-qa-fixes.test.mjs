/* 2026-08-16 에디터 QA 수정(3-03~3-15 + 오너 추가 지시)의 동작 계약.
   소스 정규식이 아니라 순수 함수로 검증할 수 있는 것들을 여기 모은다. */
import test from 'node:test';
import assert from 'node:assert/strict';

import { DEFAULT_READ_RETRY_DELAYS, ReadRetryCancelled, isRetryableReadError, retryRead } from '../../src/lib/retryRead.js';
import { buildColorOpts, colorLabelOf, visibleColorOpts } from '../../src/lib/colorOpts.js';
import { classifyEditorLoadError } from '../../src/features/editor/editorLoadError.js';
import { canSafelyMergeServerBlocks, fillGenBlocks, mergeServerBlocks } from '../../src/lib/editorWaitSkeleton.js';
import { isPhotoSlotElement, removeSelectedElements, reorderElements } from '../../src/features/editor/editorSelection.js';

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
  assert.equal(isRetryableReadError({ status: 403 }), false, '4xx 는 다시 보내도 같은 답');
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
  // 조립 중 터진 건 통신 실패가 아니다 — 같은 응답으로 다시 시도해도 똑같이 터진다(2026-08-17 리뷰).
  const render = classifyEditorLoadError({ duringRender: true, status: 500, message: '서버가 응답하지 않아요' });
  assert.equal(render.kind, 'render');
  assert.doesNotMatch(render.message, /다시 시도/, '재시도를 권하면 무한 왕복이 된다');
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

/* ---------- 레이어 창 드래그로 순서 바꾸기 ---------- */

const layers = () => ['A', 'B', 'C', 'D'].map((id) => ({ id }));
const ids = (list) => list.map((e) => e.id).join('');

test('레이어 순서 — 바로 위 칸에 놓기가 제자리로 돌아오지 않는다(오너 8/16 먹통 신고)', () => {
  // 예전 구현은 요소를 먼저 꺼낸 뒤 목표 인덱스를 찾아서, 한 칸 위로 끄는 경우
  // 목표가 출발 자리와 같아져 아무 변화가 없었다 — '위로 옮기기'가 통째로 안 됐다.
  assert.equal(ids(reorderElements(layers(), 'A', 'B')), 'BACD');
  assert.equal(ids(reorderElements(layers(), 'B', 'C')), 'ACBD');
});

test('레이어 순서 — 아래로·맨 끝으로도 정확히 옮긴다', () => {
  assert.equal(ids(reorderElements(layers(), 'C', 'B')), 'ACBD');
  assert.equal(ids(reorderElements(layers(), 'A', 'D')), 'BCDA');
  assert.equal(ids(reorderElements(layers(), 'D', 'A')), 'DABC');
});

test('레이어 순서 — 제자리·없는 id 는 원본을 그대로 돌려준다(불필요한 리렌더 방지)', () => {
  const list = layers();
  assert.equal(reorderElements(list, 'A', 'A'), list);
  assert.equal(reorderElements(list, 'A', 'Z'), list);
  assert.equal(reorderElements(list, 'Z', 'A'), list);
});


/* ---------- 격자·프레임의 사진 자리는 지우면 '빈 자리'로 남는다 (오너 2026-08-17) ---------- */

const gridBlock = () => ({
  id: 'b1', kind: 'grid2x2',
  elements: [1, 2, 3, 4].map((i) => ({
    id: `i${i}`, type: 'image', src: `${i}.png`, radius: 0, cutType: 'styling',
    sourceBlockId: `sb${i}`,   // 조립된 '칸'의 표식 — 나중에 얹은 낱장 사진과 구분한다
    x: i % 2 ? 60 : 500, y: i <= 2 ? 50 : 610, w: 440, h: 560,
  })),
});

test('격자 사진 하나를 지우면 자리·크기는 그대로 남고 사진만 빠진다', () => {
  const after = removeSelectedElements([gridBlock()], ['i4'])[0];
  assert.equal(after.elements.length, 4, '자리가 사라지면 격자가 무너지고 다시 넣은 사진이 블록을 덮는다');
  const slot = after.elements.find((el) => el.id === 'i4');
  assert.deepEqual([slot.x, slot.y, slot.w, slot.h], [500, 610, 440, 560]);
  assert.equal(slot.src, null);
  assert.equal(slot.cutType, null);
  assert.equal(slot.frameSlot, true, '드롭이 이 칸에 스냅되고 ＋ 버튼이 뜨는 근거');
  // 콘티 컷과의 연결(sourceBlockId)은 남긴다 — 끊으면 완료 병합의 안전 검사가
  // "배치가 다르다"고 보고 대기 중 편집분을 통째로 서버본으로 갈아끼운다.
  assert.equal(slot.sourceBlockId, 'sb4');
  assert.equal(slot.slotCleared, true, '되살리지 말라는 표식');
});

test('사진에 딸린 자국(크롭·실패 표식)은 비울 때 같이 걷어낸다', () => {
  const block = gridBlock();
  block.elements[0] = { ...block.elements[0], crop: { ox: 10 }, genFailed: true, genPending: 'wait' };
  const slot = removeSelectedElements([block], ['i1'])[0].elements[0];
  assert.ok(!('crop' in slot) && !('genFailed' in slot) && !('genPending' in slot));
});

test('사진 자리가 아닌 것은 예전처럼 지워진다 — 낱장 사진·격자 안 글자', () => {
  const single = { id: 'b2', kind: 'hooking', elements: [{ id: 'f1', type: 'image', src: 'x.png' }, { id: 't1', type: 'text', text: 'hi' }] };
  assert.deepEqual(removeSelectedElements([single], ['f1'])[0].elements.map((el) => el.id), ['t1']);
  const withText = gridBlock();
  withText.elements.push({ id: 't9', type: 'text', text: 'x' });
  assert.deepEqual(removeSelectedElements([withText], ['t9'])[0].elements.map((el) => el.id), ['i1', 'i2', 'i3', 'i4']);
});

test('사진 자리 판정 — 프레임 템플릿 칸과 조립된 사진 행·격자', () => {
  const slot = { type: 'image', sourceBlockId: 'sb1' };
  assert.equal(isPhotoSlotElement({ kind: 'custom' }, { type: 'image', frameSlot: true }), true);
  for (const kind of ['twocol', 'threecol', 'grid2x2', 'colorcmp']) {
    assert.equal(isPhotoSlotElement({ kind }, slot), true, kind);
  }
  assert.equal(isPhotoSlotElement({ kind: 'hooking' }, slot), false);
  assert.equal(isPhotoSlotElement({ kind: 'grid2x2' }, { type: 'text' }), false);
  // 격자 블록에 나중에 얹은 낱장 사진은 '칸'이 아니다 — 칸으로 보면 영영 못 지운다.
  assert.equal(isPhotoSlotElement({ kind: 'grid2x2' }, { type: 'image', src: 'x.png' }), false);
});

test('격자 블록에 얹은 낱장 사진은 예전처럼 지워진다 — 지워지지 않는 유령이 남으면 안 된다', () => {
  const block = gridBlock();
  block.elements.push({ id: 'loose', type: 'image', src: 'x.png', x: 100, y: 100, w: 300, h: 300 });
  const after = removeSelectedElements([block], ['loose'])[0];
  assert.deepEqual(after.elements.map((el) => el.id), ['i1', 'i2', 'i3', 'i4']);
});


test('retryRead: 화면이 떠났으면 남은 재시도를 멈춘다 — 버려진 사슬이 요청을 쌓지 않게', async () => {
  let calls = 0;
  let cancelled = false;
  await assert.rejects(() => retryRead(async () => {
    calls += 1;
    cancelled = true;            // 첫 실패 뒤 사용자가 화면을 떠났다고 가정
    const e = new Error('502'); e.status = 502; throw e;
  }, { delays: [1, 1, 1], sleep: async () => {}, isCancelled: () => cancelled }), ReadRetryCancelled);
  assert.equal(calls, 1, '취소 뒤에는 한 번도 더 보내지 않는다');
});

test('retryRead: 취소 신호가 없으면 예전과 똑같이 동작한다(하위 호환)', async () => {
  let calls = 0;
  const value = await retryRead(async () => {
    calls += 1;
    if (calls < 3) { const e = new Error('503'); e.status = 503; throw e; }
    return 'ok';
  }, { delays: [1, 1, 1], sleep: async () => {} });
  assert.equal(value, 'ok');
  assert.equal(calls, 3);
});


test('일부러 비운 자리는 생성이 끝나도 되살아나지 않고, 편집분도 안 날아간다', () => {
  const local = [{ id: 'b1', kind: 'grid2x2', elements: [
    { id: 'i1', type: 'image', src: 'a.png', sourceBlockId: 'sb1', x: 60, y: 50, w: 440, h: 560 },
    { id: 'i2', type: 'image', src: 'b.png', sourceBlockId: 'sb2', x: 500, y: 50, w: 440, h: 560 },
  ] }];
  const blanked = removeSelectedElements(local, ['i1']);
  const server = [{ id: 'b1', kind: 'grid2x2', elements: [
    { id: 's1', type: 'image', src: '/final1.jpg', sourceBlockId: 'sb1' },
    { id: 's2', type: 'image', src: '/final2.jpg', sourceBlockId: 'sb2' },
  ] }];
  // 안전 검사가 통과해야 서버본 통째 교체(=대기 중 편집 소실)를 피한다.
  assert.equal(canSafelyMergeServerBlocks(blanked, server), true);
  const merged = mergeServerBlocks(blanked, server, new Set());
  assert.equal(merged[0].elements[0].src, null, '일부러 비운 자리는 그대로');
  assert.equal(merged[0].elements[1].src, '/final2.jpg', '나머지는 정상 채움');
});

test('이미 빈 자리는 Delete 로 없앨 수 있다 — 프레임 템플릿의 남는 칸이 영영 안 지워지면 안 된다', () => {
  const block = { id: 'b1', kind: 'custom', elements: [
    { id: 'empty', type: 'image', frameSlot: true, src: null, x: 0, y: 0, w: 100, h: 100 },
    { id: 'filled', type: 'image', frameSlot: true, src: 'a.png', x: 100, y: 0, w: 100, h: 100 },
  ] };
  assert.deepEqual(removeSelectedElements([block], ['empty'])[0].elements.map((el) => el.id), ['filled']);
  const after = removeSelectedElements([block], ['filled'])[0];
  assert.deepEqual(after.elements.map((el) => el.id), ['empty', 'filled'], '사진이 든 칸은 비우기');
  assert.equal(after.elements[1].src, null);
});


test('생성 중 도착한 컷도 일부러 비운 자리는 다시 채우지 않는다', () => {
  const blocks = [{ id: 'b1', kind: 'grid2x2', elements: [
    { id: 'i1', type: 'image', src: null, sourceBlockId: 'sb1', frameSlot: true, slotCleared: true },
    { id: 'i2', type: 'image', src: null, sourceBlockId: 'sb2', genPending: 'wait' },
  ] }];
  const job = { cuts: { sb1: { url: '/late1.jpg' }, sb2: { url: '/late2.jpg' } }, copy: {} };
  const filled = fillGenBlocks(blocks, job);
  assert.equal(filled[0].elements[0].src, null, '비운 자리는 그대로');
  assert.equal(filled[0].elements[1].src, '/late2.jpg', '기다리던 자리는 채워진다');
});

test('빈 콘티 컷 자리는 Delete 해도 요소가 사라지지 않는다 — 사라지면 대기 중 편집분이 덮인다', () => {
  const block = { id: 'b1', kind: 'grid2x2', elements: [
    { id: 'cut', type: 'image', src: null, frameSlot: true, slotCleared: true, sourceBlockId: 'sb1' },
    { id: 'tmpl', type: 'image', src: null, frameSlot: true },
  ] };
  assert.deepEqual(removeSelectedElements([block], ['cut'])[0].elements.map((el) => el.id), ['cut', 'tmpl']);
  // 콘티와 안 묶인 템플릿 빈 칸은 없앨 수 있다.
  assert.deepEqual(removeSelectedElements([block], ['tmpl'])[0].elements.map((el) => el.id), ['cut']);
});
