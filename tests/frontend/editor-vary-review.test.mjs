// Phase 3 P0-C 8/N — vary 결과의 QC 상태 전달과 실패 정리.
//
// 계약:
//   · 신규/replay 응답은 같은 모양이다(jobId) — 프론트가 둘을 구분할 필요가 없다.
//   · 검수 API 는 같은 판단의 재시도가 이력을 부풀리지 않게 Idempotency-Key 를 보낸다.
//   · 생성 실패는 자리 표시자를 남기지 않고 카운터를 반드시 되돌린다.
import assert from 'node:assert/strict';
import test from 'node:test';

// httpAdapter 는 supabase 세션을 요구하므로 여기서는 계약 형태만 검증한다.
// (실제 네트워크 호출 0 — 이 프로젝트의 다른 frontend 테스트와 같은 규율)
import { readFileSync } from 'node:fs';

const adapter = readFileSync(new URL('../../src/lib/api/httpAdapter.js', import.meta.url), 'utf8');
const editor = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
const panels = readFileSync(new URL('../../src/features/editor/EditorPanels.jsx', import.meta.url), 'utf8');
const modal = readFileSync(new URL('../../src/features/editor/VaryReviewModal.jsx', import.meta.url), 'utf8');

test('http() forwards an Idempotency-Key when given one', () => {
  assert.match(adapter, /idempotencyKey \} = \{\}\) \{/);
  assert.match(adapter, /'Idempotency-Key': idempotencyKey/);
});

test('reviewEditSession posts a decision with a stable idempotency key', () => {
  assert.match(adapter, /edit-sessions\/\$\{sessionId\}:review/);
  assert.match(adapter, /idempotencyKey: key \|\| `\$\{sessionId\}:\$\{decision\}`/);
});

test('generateImage polls by jobId — the same shape for new and replay', () => {
  // 서버가 replay 에서 job row 를 펼쳐 주면 jobId 가 없어 이 폴링이 깨진다.
  assert.match(adapter, /const result = await pollJob\(res\.jobId/);
});

test('varyGenerate restores the counter in finally', () => {
  const body = editor.slice(editor.indexOf('const varyGenerate'), editor.indexOf('const varyImage'));
  assert.match(body, /try \{/);
  assert.match(body, /\} finally \{[\s\S]*genCount\.current -= 1/);
});

test('varyGenerate removes the loading placeholder on failure', () => {
  const body = editor.slice(editor.indexOf('const varyGenerate'), editor.indexOf('const varyImage'));
  assert.match(body, /catch \(err\)[\s\S]*filter\(\(x\) => x\.id !== loadingId\)/);
});

test('varyGenerate never decrements the counter twice on success', () => {
  const body = editor.slice(editor.indexOf('const varyGenerate'), editor.indexOf('const varyImage'));
  assert.equal((body.match(/genCount\.current -= 1/g) || []).length, 1);
});

test('review updates only the local decision, never the machine QC fields', () => {
  const body = editor.slice(editor.indexOf('const reviewVaryResult'), editor.indexOf('const varyImage'));
  assert.match(body, /reviewDecision: decision/);
  assert.doesNotMatch(body, /qcStatus:/);
  assert.doesNotMatch(body, /needsReview:/);
});

test('review does not update local state when the API fails', () => {
  const body = editor.slice(editor.indexOf('const reviewVaryResult'), editor.indexOf('const varyImage'));
  const catchIdx = body.indexOf('catch (err)');
  const setIdx = body.indexOf('setWardrobe');
  assert.ok(catchIdx > -1 && setIdx > catchIdx, 'setWardrobe must come after the failure return');
  assert.match(body.slice(catchIdx, setIdx), /return false;/);
});

// ── 검수 UI (item 5) ────────────────────────────────────────────────────────

test('an unreviewed review_required result never reaches the canvas', () => {
  const body = editor.slice(editor.indexOf('const wardrobeInsert'), editor.indexOf('  // fresh ='));
  const gate = editor.slice(editor.indexOf('const needsReviewNow'), editor.indexOf('const wardrobeInsert'));
  assert.match(gate, /!!im\?\.needsReview && !im\?\.reviewDecision/);
  // 게이트가 pendingSlot 분기보다 **먼저** 있어야 슬롯 채우기로도 새지 않는다.
  assert.ok(body.indexOf('needsReviewNow(im)') < body.indexOf('if (pendingSlot)'));
  assert.match(body, /needsReviewNow\(im\)\) \{ setReview\(\{ image: im, busy: false \}\); return; \}/);
});

test('an already-decided result inserts without re-asking', () => {
  // reviewDecision 이 있으면 needsReviewNow 는 false — accepted 도 rejected 도 모달을 다시 열지 않는다.
  const gate = editor.slice(editor.indexOf('const needsReviewNow'), editor.indexOf('const wardrobeInsert'));
  assert.doesNotMatch(gate, /=== 'accepted'/);
});

test('acceptance is recorded before the image is inserted', () => {
  const body = editor.slice(editor.indexOf('const acceptReview'), editor.indexOf('const rejectReview'));
  assert.ok(body.indexOf("reviewVaryResult(im, 'accepted')") < body.indexOf('insertImage(im)'));
});

test('a failed acceptance blocks the insert and reopens the dialog', () => {
  const body = editor.slice(editor.indexOf('const acceptReview'), editor.indexOf('const rejectReview'));
  assert.match(body, /if \(!ok\) \{ setReview\(\(r\) => \(r \? \{ \.\.\.r, busy: false \} : r\)\); return; \}/);
  assert.ok(body.indexOf('if (!ok)') < body.indexOf('insertImage(im)'));
});

test('rejection records a decision and never deletes the image', () => {
  const body = editor.slice(editor.indexOf('const rejectReview'), editor.indexOf('const varyImage'));
  assert.match(body, /reviewVaryResult\(im, 'rejected'\)/);
  assert.doesNotMatch(body, /deleteWardrobeImages|setWardrobe\(/);
});

test('acceptance honours a pending info-block slot', () => {
  // 슬롯 채우기 도중 검수가 뜨면 승인 후에도 슬롯으로 가야 한다(캔버스 한복판이 아니라).
  const body = editor.slice(editor.indexOf('const acceptReview'), editor.indexOf('const rejectReview'));
  assert.match(body, /if \(pendingSlot\)[\s\S]*applySlotFillToInfo/);
});

test('badges distinguish machine review from user decision', () => {
  const badge = panels.slice(panels.indexOf('function WardrobeQcBadge'), panels.indexOf('export function WardrobePanel'));
  assert.match(badge, /reviewDecision === 'rejected'/);
  assert.match(badge, /reviewDecision === 'accepted'/);
  assert.match(badge, /im\.needsReview/);
  // 사용자 판단이 있으면 그게 이긴다 — 판정 배지가 결정 배지를 가리지 않게 순서가 중요.
  assert.ok(badge.indexOf("reviewDecision === 'rejected'") < badge.indexOf('im.needsReview'));
});

test('mode:new results carry no badge', () => {
  const badge = panels.slice(panels.indexOf('function WardrobeQcBadge'), panels.indexOf('export function WardrobePanel'));
  assert.match(badge, /return null;/);
});

test('the dialog shows the source next to the result', () => {
  assert.match(modal, /image\?\.sourceSrc/);
  assert.match(modal, /원본/);
  assert.match(modal, /편집 결과/);
});

test('the dialog offers exactly the three documented actions', () => {
  assert.match(modal, /확인 후 사용/);
  assert.match(modal, /사용하지 않음/);
  assert.match(modal, />닫기</);
});

test('the dialog renders only safe qc summary fields', () => {
  assert.match(modal, /summary\.unexpectedChanges/);
  assert.match(modal, /summary\.lockedInvariantViolations/);
  assert.match(modal, /summary\.requestedChangeSatisfied/);
  for (const leak of ['metrics', 'regenerationInstructions', 'observation', 'promptSha', 'r2Key']) {
    assert.doesNotMatch(modal, new RegExp(`summary\\.${leak}`), `${leak} 는 표시하면 안 된다`);
  }
});

test('the dialog cannot be dismissed mid-submit', () => {
  // 기록 중 닫히면 승인만 남고 삽입은 안 되는 상태가 만들어진다.
  assert.match(modal, /onClose=\{busy \? \(\) => \{\} : onClose\}/);
  assert.match(modal, /disabled=\{busy\}/);
});
