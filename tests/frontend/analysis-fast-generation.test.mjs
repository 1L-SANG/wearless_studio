import test from 'node:test';
import assert from 'node:assert/strict';
import { waitForInitialMannequinPreparation } from '../../src/features/mannequin/generationPreparation.js';
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
function setup() {
  const gate = deferred(); let project = 'p', session = 1, active = true, attempt = null;
  const events = [];
  const io = { readAttempt: () => attempt, getPromotionTask: () => ({ status: 'pending' }), photosReady: async () => { events.push('wait'); await gate.promise; },
    readProject: () => project, readSession: () => session, isScreenActive: () => active };
  return { io, gate, events, change(value) { if ('project' in value) project = value.project; if ('session' in value) session = value.session; if ('active' in value) active = value.active; if ('attempt' in value) attempt = value.attempt; } };
}
test('최초 요청은 승격 정착을 기다리고 준비 중 job과 예약을 기록하지 않는다', async () => {
  const f = setup(); let reserved = false;
  const request = waitForInitialMannequinPreparation('p', f.io).then((ready) => { if (ready) reserved = true; });
  await Promise.resolve(); assert.equal(reserved, false); assert.deepEqual(f.events, ['wait']); assert.equal(f.io.readAttempt(), null);
  f.gate.resolve(); await request; assert.equal(reserved, true);
});
for (const change of [{ project: 'new' }, { session: 2 }, { active: false }]) test(`준비 중 경계 변경 ${JSON.stringify(change)} 뒤에는 접수하지 않는다`, async () => {
  const f = setup(); const waiting = waitForInitialMannequinPreparation('p', f.io); await Promise.resolve(); f.change(change); f.gate.resolve(); assert.equal(await waiting, false);
});
test('이미 접수된 job은 승격을 기다리지 않고 기존 식별자로 합류한다', async () => {
  const f = setup(); f.change({ attempt: { jobId: 'accepted', idempotencyKey: 'same' } });
  assert.equal(await waitForInitialMannequinPreparation('p', f.io), true); assert.deepEqual(f.events, []); assert.equal(f.io.readAttempt().idempotencyKey, 'same');
});
test('승격 실패는 첫 요청을 막는다', async () => {
  const f = setup(); const waiting = waitForInitialMannequinPreparation('p', f.io); f.gate.reject(new Error('upload failed')); await assert.rejects(waiting, /upload failed/);
});
