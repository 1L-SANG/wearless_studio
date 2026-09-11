import test from 'node:test';
import assert from 'node:assert/strict';
import { modelComponentHarness, findTree, eventually } from './helpers/facemarketHarness.mjs';
import { SLOTS } from '../../src/features/model/registerSlots.js';

const record = {
  id: 'enrollment-recovery', modelId: 'model-1', status: 'license_pending',
  consentDocumentVersion: '2026-09-v1', termsConsentVersion: '2026-09-v1',
  photos: SLOTS.map(slot => ({ slot: slot.key })),
};
const button = (tree, label) => findTree(tree, node => node.type === 'button' && node.props.children === label);
async function mount(api, enrollment = record) {
  const h = await modelComponentHarness({ initialStates: [], honorHookDependencies: true, api: {
    getCurrentEnrollment: async () => enrollment,
    getFacemarketConfig: async () => ({ livenessRequired: false }), ...api,
  } });
  h.render(); h.runtime.effects.forEach(effect => effect());
  await eventually(() => !['loading', 'error'].includes(h.runtime.states[0]), 'registration restored');
  return h;
}

for (const status of ['failed', 'expired']) {
  test(`a ${status} completion can restart without cancelling terminal enrollment`, async () => {
    const calls = [];
    const h = await mount({
      completeEnrollment: async () => ({ status, reason: 'enrollment_expired', passed: false }),
      cancelEnrollment: async () => { calls.push('cancel'); throw new Error('terminal cancellation is unsupported'); },
    }, { ...record, status: 'liveness_pending' });
    try {
      await button(h.render(), '확인 완료').props.onClick();
      assert.equal(h.runtime.states[0], 'failed');
      await button(h.render(), '다시 시작하기').props.onClick();
      assert.equal(h.runtime.states[0], '1');
      assert.equal(h.runtime.states[1], null);
      assert.deepEqual(calls, []);
    } finally { await h.close(); }
  });
}

test('viewing photos from conditions returns to conditions without re-completing', async () => {
  let completions = 0;
  const h = await mount({ completeEnrollment: async () => { completions++; throw new Error('already completed'); } });
  try {
    button(h.render(), '이전').props.onClick();
    await button(h.render(), '확인 완료').props.onClick();
    assert.equal(h.runtime.states[0], '3');
    assert.equal(completions, 0);
  } finally { await h.close(); }
});

test('changing a completed photo first reopens and then uploads under preserved identity', async () => {
  const calls = [];
  const h = await mount({
    reopenEnrollmentPhotos: async id => { calls.push(['reopen', id]); return { ...record, status: 'liveness_pending', photoRevision: 1 }; },
    uploadEnrollmentPhoto: async args => { calls.push(['upload', args.slot]); return { slot: args.slot }; },
  });
  try {
    button(h.render(), '이전').props.onClick();
    findTree(h.render(), node => node.props?.['aria-label'] === '얼굴 사진 고치기').props.onClick();
    assert.deepEqual(calls, []);
    const input = findTree(h.render(), node => node.type === 'input' && node.props.type === 'file');
    input.props.onChange({ target: { files: [new Blob(['new-photo'], { type: 'image/jpeg' })], value: 'upload' } });
    await eventually(() => !h.runtime.states[4], 'photo upload completed');
    assert.deepEqual(calls, [['reopen', 'enrollment-recovery'], ['upload', 'face01']]);
    assert.equal(h.runtime.states[1].status, 'liveness_pending');
  } finally { await h.close(); }
});

test('a rejected reopen leaves the stored photo intact and never uploads', async () => {
  let uploads = 0;
  const h = await mount({
    reopenEnrollmentPhotos: async () => { throw new Error('증서 발급을 시작해서 사진을 고칠 수 없어요.'); },
    uploadEnrollmentPhoto: async () => { uploads++; return { slot: 'face01' }; },
  });
  try {
    button(h.render(), '이전').props.onClick();
    findTree(h.render(), node => node.props?.['aria-label'] === '얼굴 사진 고치기').props.onClick();
    findTree(h.render(), node => node.type === 'input' && node.props.type === 'file').props.onChange({ target: { files: [new Blob(['new'])], value: 'upload' } });
    await eventually(() => !h.runtime.states[4], 'reopen rejected');
    assert.equal(uploads, 0);
    assert.equal(h.runtime.states[1].photos.length, 18);
    assert.match(h.runtime.states[3], /증서 발급/);
  } finally { await h.close(); }
});
