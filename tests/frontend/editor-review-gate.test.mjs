// Phase 3 P0-C 8/N 최종 — wardrobe 이미지 사용 승인 게이트.
//
// 문자열 검사가 아니라 **실제 전이**를 돌린다. 이 정책의 버그는 "코드에 그 줄이 있다"로
// 잡히지 않는다: 승인 실패했는데 반영되거나, 한 번 승인에 두 번 반영되거나, 닫힌 폼에
// 반영되는 식으로 난다.
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';

import { createContinuationSlot, createReviewGate, needsReviewBeforeUse }
  from '../../src/features/editor/reviewGate.js';

const PLAIN = { id: 'w1', src: '/a.png' };                                  // 업로드·mode:new
const PASSED = { id: 'w2', src: '/b.png', needsReview: false, qcStatus: 'pass' };
const UNREVIEWED = { id: 'w3', src: '/c.png', needsReview: true, reviewDecision: null };
const ACCEPTED = { ...UNREVIEWED, id: 'w4', reviewDecision: 'accepted' };
const REJECTED = { ...UNREVIEWED, id: 'w5', reviewDecision: 'rejected' };

// 게이트 + 반영 대상을 한 벌 세워 준다. calls 로 실제 부수효과를 센다.
function harness({ record = async () => true } = {}) {
  const calls = { used: [], recorded: [], state: [] };
  const gate = createReviewGate({
    record: async (im, decision, reason) => {
      calls.recorded.push({ id: im.id, decision, reason });
      return record(im, decision, reason);
    },
    onChange: (s) => calls.state.push(s && { id: s.image.id, busy: s.busy }),
  });
  const use = (im) => calls.used.push(im.id);
  return { gate, calls, use };
}

// ── 정책 자체 ───────────────────────────────────────────────────────────────

test('policy: only unreviewed and rejected results need review', () => {
  assert.equal(needsReviewBeforeUse(PLAIN), false);
  assert.equal(needsReviewBeforeUse(PASSED), false);
  assert.equal(needsReviewBeforeUse(ACCEPTED), false);
  assert.equal(needsReviewBeforeUse(UNREVIEWED), true);
  assert.equal(needsReviewBeforeUse(REJECTED), true);
});

test('policy: a missing image is not treated as reviewable', () => {
  assert.equal(needsReviewBeforeUse(null), false);
  assert.equal(needsReviewBeforeUse(undefined), false);
});

// ── 통과 경로 ───────────────────────────────────────────────────────────────

test('a plain upload is used immediately with no API call', () => {
  const { gate, calls, use } = harness();
  assert.equal(gate.request(PLAIN, use), false);
  assert.deepEqual(calls.used, ['w1']);
  assert.deepEqual(calls.recorded, []);
  assert.equal(gate.pendingImage, null);
});

test('an already accepted result is used with no extra API call', () => {
  const { gate, calls, use } = harness();
  gate.request(ACCEPTED, use);
  assert.deepEqual(calls.used, ['w4']);
  assert.deepEqual(calls.recorded, []);
});

test('a machine-passed result is used immediately', () => {
  const { gate, calls, use } = harness();
  gate.request(PASSED, use);
  assert.deepEqual(calls.used, ['w2']);
});

// ── 검수 경로 ───────────────────────────────────────────────────────────────

test('an unreviewed result is not used until it is accepted', async () => {
  const { gate, calls, use } = harness();
  assert.equal(gate.request(UNREVIEWED, use), true);
  assert.deepEqual(calls.used, []);                 // 아직 아무 데도 안 들어갔다
  assert.equal(gate.pendingImage.id, 'w3');
  assert.equal(await gate.accept(), true);
  assert.deepEqual(calls.recorded, [{ id: 'w3', decision: 'accepted', reason: undefined }]);
  assert.deepEqual(calls.used, ['w3']);
});

test('a rejected result is not used directly and needs a new acceptance', async () => {
  const { gate, calls, use } = harness();
  assert.equal(gate.request(REJECTED, use), true);
  assert.deepEqual(calls.used, []);
  await gate.accept();
  assert.deepEqual(calls.recorded, [{ id: 'w5', decision: 'accepted', reason: undefined }]);
  assert.deepEqual(calls.used, ['w5']);
});

test('rejecting records the decision and never uses the image', async () => {
  const { gate, calls, use } = harness();
  gate.request(UNREVIEWED, use);
  assert.equal(await gate.reject(), true);
  assert.deepEqual(calls.recorded, [{ id: 'w3', decision: 'rejected', reason: undefined }]);
  assert.deepEqual(calls.used, []);
  assert.equal(gate.pendingImage, null);            // continuation 폐기
});

test('a failed record blocks the use and keeps the dialog open', async () => {
  const { gate, calls, use } = harness({ record: async () => false });
  gate.request(UNREVIEWED, use);
  assert.equal(await gate.accept(), false);
  assert.deepEqual(calls.used, []);
  assert.equal(gate.pendingImage.id, 'w3');         // 다시 시도할 수 있어야 한다
  assert.equal(gate.isBusy, false);
});

test('a record that throws does not leave the gate stuck busy', async () => {
  const { gate, use } = harness({ record: async () => { throw new Error('offline'); } });
  gate.request(UNREVIEWED, use);
  await assert.rejects(() => gate.accept());
  assert.equal(gate.isBusy, false);
  assert.equal(gate.pendingImage.id, 'w3');
});

test('a retry after a failure can still succeed', async () => {
  let ok = false;
  const { gate, calls, use } = harness({ record: async () => ok });
  gate.request(UNREVIEWED, use);
  await gate.accept();
  ok = true;
  assert.equal(await gate.accept(), true);
  assert.deepEqual(calls.used, ['w3']);
  assert.equal(calls.recorded.length, 2);
});

test('closing cancels without recording or using', () => {
  const { gate, calls, use } = harness();
  gate.request(UNREVIEWED, use);
  assert.equal(gate.close(), true);
  assert.deepEqual(calls.recorded, []);
  assert.deepEqual(calls.used, []);
  assert.equal(gate.pendingImage, null);
});

// ── 중복 실행 방지 ──────────────────────────────────────────────────────────

test('a double click records once and uses once', async () => {
  let release;
  const { gate, calls, use } = harness({
    record: () => new Promise((r) => { release = () => r(true); }),
  });
  gate.request(UNREVIEWED, use);
  const first = gate.accept();
  const second = gate.accept();                     // 응답 전 두 번째 클릭
  release();
  assert.equal(await first, true);
  assert.equal(await second, false);
  assert.equal(calls.recorded.length, 1);
  assert.deepEqual(calls.used, ['w3']);
});

test('accept after accept cannot use the image twice', async () => {
  const { gate, calls, use } = harness();
  gate.request(UNREVIEWED, use);
  await gate.accept();
  assert.equal(await gate.accept(), false);         // pending 이 비었다
  assert.deepEqual(calls.used, ['w3']);
});

test('reject cannot follow a completed accept', async () => {
  const { gate, calls, use } = harness();
  gate.request(UNREVIEWED, use);
  await gate.accept();
  assert.equal(await gate.reject(), false);
  assert.equal(calls.recorded.length, 1);
});

test('the dialog cannot be closed while recording', async () => {
  let release;
  const { gate, calls, use } = harness({
    record: () => new Promise((r) => { release = () => r(true); }),
  });
  gate.request(UNREVIEWED, use);
  const p = gate.accept();
  assert.equal(gate.close(), false);                // 승인만 남고 반영 안 되는 상태 방지
  release(); await p;
  assert.deepEqual(calls.used, ['w3']);
});

test('a late response for a replaced review never uses the image', async () => {
  let release;
  const { gate, calls, use } = harness({
    record: () => new Promise((r) => { release = () => r(true); }),
  });
  gate.request(UNREVIEWED, use);
  const p = gate.accept();
  gate.request(REJECTED, use);                      // 다른 검수 대상이 pending 을 갈아치움
  release();
  assert.equal(await p, false);
  assert.deepEqual(calls.used, []);                 // 늦게 온 승인은 아무것도 넣지 않는다
  assert.equal(gate.pendingImage.id, 'w5');
});

test('a pass-through use during a pending review does not disturb it', async () => {
  let release;
  const { gate, calls, use } = harness({
    record: () => new Promise((r) => { release = () => r(true); }),
  });
  gate.request(UNREVIEWED, use);
  const p = gate.accept();
  gate.request(PLAIN, use);                         // 검수가 필요 없는 이미지는 즉시 사용
  assert.deepEqual(calls.used, ['w1']);
  release();
  assert.equal(await p, true);
  assert.deepEqual(calls.used, ['w1', 'w3']);
});

// ── continuation 이 목적을 보존하는가 ──────────────────────────────────────

test('the gate never guesses the purpose — it runs what it was given', async () => {
  const { gate } = harness();
  const sink = [];
  gate.request(UNREVIEWED, () => sink.push('photo-slot-2'));
  await gate.accept();
  assert.deepEqual(sink, ['photo-slot-2']);
});

test('two different purposes stay separate', async () => {
  const sink = [];
  const { gate } = harness();
  gate.request(UNREVIEWED, () => sink.push('canvas'));
  await gate.accept();
  gate.request(REJECTED, () => sink.push('slot-3'));
  await gate.accept();
  assert.deepEqual(sink, ['canvas', 'slot-3']);
});

// ── continuation 수명 (폼 쪽) ───────────────────────────────────────────────

test('a continuation applies to the slot that was claimed', () => {
  const slot = createContinuationSlot();
  const applied = [];
  const claimed = slot.claim(2);
  assert.equal(slot.run(claimed, (i) => applied.push(i)), true);
  assert.deepEqual(applied, [2]);
});

test('a continuation runs at most once', () => {
  const slot = createContinuationSlot();
  const applied = [];
  const claimed = slot.claim(0);
  slot.run(claimed, (i) => applied.push(i));
  assert.equal(slot.run(claimed, (i) => applied.push(i)), false);
  assert.deepEqual(applied, [0]);
});

test('a newer claim invalidates the older continuation', () => {
  const slot = createContinuationSlot();
  const applied = [];
  const first = slot.claim(1);
  const second = slot.claim(3);
  assert.equal(slot.run(first, (i) => applied.push(i)), false);
  assert.equal(slot.run(second, (i) => applied.push(i)), true);
  assert.deepEqual(applied, [3]);
});

test('a disposed form applies nothing', () => {
  const slot = createContinuationSlot();
  const applied = [];
  const claimed = slot.claim(1);
  slot.dispose();                                   // 폼이 닫혔다
  assert.equal(slot.run(claimed, (i) => applied.push(i)), false);
  assert.deepEqual(applied, []);
});

test('a claim of index 0 is still a valid target', () => {
  // 0 은 falsy 라 대충 짜면 "대상 없음"으로 오해된다.
  const slot = createContinuationSlot();
  const claimed = slot.claim(0);
  assert.equal(slot.pendingTarget, 0);
  assert.equal(slot.run(claimed, () => {}), true);
});

// ── 폼 + 게이트 통합 (InfoBlockModal 시나리오) ──────────────────────────────

function photoForm({ record = async () => true } = {}) {
  const photos = [null, null, null];
  const slot = createContinuationSlot();
  const gate = createReviewGate({ record, onChange: () => {} });
  // InfoBlockModal.onPick 과 같은 구조: 클릭 시점 대상을 표로 고정 → 게이트에 위임
  const pick = (im, index) => {
    const claimed = slot.claim(index);
    return gate.request(im, () => slot.run(claimed, (i) => { photos[i] = im.src; }));
  };
  return { photos, slot, gate, pick };
}

test('picking an unreviewed photo sets nothing until it is accepted', async () => {
  const f = photoForm();
  assert.equal(f.pick(UNREVIEWED, 1), true);
  assert.deepEqual(f.photos, [null, null, null]);
  await f.gate.accept();
  assert.deepEqual(f.photos, [null, '/c.png', null]);
});

test('picking a rejected photo does not fill the slot', async () => {
  const f = photoForm();
  f.pick(REJECTED, 0);
  assert.deepEqual(f.photos, [null, null, null]);
  await f.gate.accept();
  assert.deepEqual(f.photos, ['/c.png', null, null]);
});

test('rejecting from the photo picker leaves the slot empty', async () => {
  const f = photoForm();
  f.pick(UNREVIEWED, 2);
  await f.gate.reject();
  assert.deepEqual(f.photos, [null, null, null]);
});

test('a failed record leaves the form untouched', async () => {
  const f = photoForm({ record: async () => false });
  f.pick(UNREVIEWED, 1);
  await f.gate.accept();
  assert.deepEqual(f.photos, [null, null, null]);
  assert.equal(f.gate.pendingImage.id, 'w3');       // 폼·검수 상태 유지
});

test('closing the review dialog leaves the form untouched', () => {
  const f = photoForm();
  f.pick(UNREVIEWED, 1);
  f.gate.close();
  assert.deepEqual(f.photos, [null, null, null]);
});

test('an accepted photo fills the slot immediately without an API call', () => {
  const recorded = [];
  const f = photoForm({ record: async (...a) => { recorded.push(a); return true; } });
  assert.equal(f.pick(ACCEPTED, 2), false);
  assert.deepEqual(f.photos, [null, null, '/b.png'.replace('/b', '/c')]);
  assert.deepEqual(recorded, []);
});

test('a plain image fills the slot with no review at all', () => {
  const f = photoForm();
  assert.equal(f.pick(PLAIN, 0), false);
  assert.deepEqual(f.photos, ['/a.png', null, null]);
});

test('a form closed during review applies nothing after approval', async () => {
  const f = photoForm();
  f.pick(UNREVIEWED, 1);
  f.slot.dispose();                                 // InfoBlockModal unmount
  await f.gate.accept();                            // 승인은 기록된다(사용자가 눌렀으니)
  assert.deepEqual(f.photos, [null, null, null]);   // 없어진 폼에는 쓰지 않는다
});

test('switching the target slot mid-review applies to neither the old target', async () => {
  const f = photoForm();
  f.pick(UNREVIEWED, 1);
  f.slot.claim(2);                                  // 대상이 바뀌었다
  await f.gate.accept();
  assert.deepEqual(f.photos, [null, null, null]);
});

test('approving twice fills the slot once', async () => {
  const f = photoForm();
  f.pick(UNREVIEWED, 0);
  await f.gate.accept();
  f.photos[0] = 'MARK';
  await f.gate.accept();
  assert.equal(f.photos[0], 'MARK');                // 두 번째 승인은 아무것도 하지 않는다
});

// ── sink 인벤토리 회귀 ──────────────────────────────────────────────────────
// rg 로 조사한 "wardrobe 이미지가 상세페이지 데이터로 들어가는" 경로 전부.
// 새 sink 가 생기면 이 테스트가 먼저 깨져야 한다.

const editor = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
const infoModal = readFileSync(new URL('../../src/features/editor/InfoBlockModal.jsx', import.meta.url), 'utf8');
const panels = readFileSync(new URL('../../src/features/editor/EditorPanels.jsx', import.meta.url), 'utf8');

test('sink: the canvas element is written only from behind the gate', () => {
  // 캔버스 element 를 만드는 유일한 곳이고, 호출 지점은 insertPastGate 하나뿐이다.
  assert.equal((editor.match(/const insertImage = /g) || []).length, 1);
  assert.equal((editor.match(/insertImage\(/g) || []).length, 1);
  const past = editor.slice(editor.indexOf('const insertPastGate'), editor.indexOf('const requestWardrobeUse'));
  assert.match(past, /else insertImage\(im\)/);
});

test('sink: the pendingSlot fill is written only from behind the gate', () => {
  assert.equal((editor.match(/applySlotFillToInfo\(/g) || []).length, 1);
  const past = editor.slice(editor.indexOf('const insertPastGate'), editor.indexOf('const requestWardrobeUse'));
  assert.match(past, /applySlotFillToInfo\(/);
});

test('sink: insertPastGate is only ever handed to the gate', () => {
  // 직접 호출(`insertPastGate(`)이 남아 있으면 그게 우회로다.
  assert.equal((editor.match(/insertPastGate\(im\)/g) || []).length, 0);
  assert.match(editor, /requestWardrobeUse\(im, insertPastGate\)/);
});

test('sink: the wardrobe panel can only insert through the gate', () => {
  assert.match(editor, /onInsert=\{wardrobeInsert\}/);
  assert.match(panels, /onClick=\{\(\) => onInsert\(im\)\}/);
  assert.equal((panels.match(/onInsert\(/g) || []).length, 1);
});

test('sink: the photo slot is written only from a gated continuation or a clear', () => {
  // 호출 지점은 딱 둘: 게이트를 통과한 continuation, 그리고 슬롯 비우기.
  assert.equal((infoModal.match(/setPhotoAt\(/g) || []).length, 2);
  const pick = infoModal.slice(infoModal.indexOf('onPick={'), infoModal.indexOf('onClear={'));
  assert.match(pick, /requestUse\(im, \(\) => slot\.current\.run\(claimed/);
  assert.doesNotMatch(pick, /setPhotoAt\(photoFor/);      // 직접 반영이 남아 있으면 안 된다
  const clear = infoModal.slice(infoModal.indexOf('onClear={'), infoModal.indexOf('onClear={') + 80);
  assert.match(clear, /setPhotoAt\(photoFor, null\)/);    // 비우기는 사용이 아니다
});

test('sink: the info modal receives the gate from the editor', () => {
  assert.match(editor, /onRequestUse=\{requestWardrobeUse\}/);
  assert.match(infoModal, /const requestUse = onRequestUse \|\| \(\(im, use\) => use\(im\)\)/);
});

test('sink: the review dialog renders above the info modal', () => {
  // 둘 다 같은 overlay 라 뒤에 온 쪽이 위에 뜬다 — 검수가 폼 뒤에 가리면 못 누른다.
  assert.ok(editor.indexOf('<InfoBlockModal') < editor.indexOf('<VaryReviewModal'));
});

test('sink: vary source is generation input, not detail-page data', () => {
  // 편집 대상으로 고르는 건 상세페이지 사용이 아니다 — 거절한 컷을 고쳐 쓸 수 있어야 한다.
  const varySource = editor.slice(editor.indexOf('const varySource'), editor.indexOf('const varyImage') + 200);
  assert.doesNotMatch(varySource, /requestWardrobeUse|insertPastGate/);
});

test('sink: the editor holds exactly one gate', () => {
  assert.equal((editor.match(/createReviewGate\(/g) || []).length, 1);
  assert.match(editor, /gateRef\.current \|\|= createReviewGate/);
});

test('sink: the gate always records through the latest handler', () => {
  // ref 를 안 쓰면 게이트가 첫 렌더의 projectId·토스트를 영원히 붙들고 있게 된다.
  assert.match(editor, /record: \(im, decision, reason\) => recordReview\.current\(im, decision, reason\)/);
  assert.match(editor, /recordReview\.current = reviewVaryResult;/);
});
