import test from 'node:test';
import assert from 'node:assert/strict';
import { findTree, modelComponentHarness } from './helpers/facemarketHarness.mjs';
import { facemarketRootTarget } from '../../src/features/facemarket-landing/facemarketRootTarget.js';

const model = {
  id: 'model-1', kind: 'real', name: '모델',
  sponsorship: { enabled: true, instagramHandle: 'daily', instagramFollowers: 1200, sizeTop: 'M', sizeBottomWaist: 28 },
};
const interestButton = tree => findTree(tree, node => node.type === 'button' && node.props.className === 'interestButton');

for (const result of ['not-interested', 'error']) {
  test(`알림 신청 완료 뒤 늦은 최초 조회 ${result} 응답이 신청 상태를 되돌리지 않아요`, async () => {
    let resolveLookup, rejectLookup;
    const lookup = new Promise((resolve, reject) => { resolveLookup = resolve; rejectLookup = reject; });
    const requests = [];
    const h = await modelComponentHarness({
      entry: '/src/features/facemarket-landing/sections/ModelDetailDialog.jsx', exportName: 'ModelDetailDialog',
      initialStates: [], honorHookDependencies: true, api: {
        getSponsorshipInterest: () => lookup,
        requestSponsorshipInterest: async id => { requests.push(id); return { interested: true }; },
      },
    });
    h.runtime.session = { user: { id: 'seller-1' } };
    const props = { model, onClose: () => {} };
    try {
      h.render(props);
      const cleanup = h.runtime.effects[0]();
      await interestButton(h.render(props)).props.onClick();
      assert.equal(interestButton(h.render(props)).props.children, '알림 신청됨');
      if (result === 'error') rejectLookup(new Error('late lookup error'));
      else resolveLookup({ interested: false });
      await new Promise(resolve => setImmediate(resolve));
      const tree = h.render(props);
      assert.equal(interestButton(tree).props.children, '알림 신청됨');
      assert.equal(interestButton(tree).props.disabled, true);
      assert.equal(findTree(tree, node => node.props?.role === 'alert'), null);
      assert.deepEqual(requests, ['model-1']);
      cleanup?.();
    } finally { await h.close(); }
  });
}

test('비로그인 알림 신청의 로그인 복귀 경로는 선택한 모델 쿼리를 보존해요', async () => {
  const loginTargets = [];
  let closed = false;
  const h = await modelComponentHarness({
    entry: '/src/features/facemarket-landing/sections/ModelDetailDialog.jsx', exportName: 'ModelDetailDialog',
    initialStates: [], api: {},
  });
  h.runtime.session = null;
  h.runtime.openLogin = target => loginTargets.push(target);
  try {
    await interestButton(h.render({ model, onClose: () => { closed = true; } })).props.onClick();
    assert.equal(closed, true);
    assert.deepEqual(loginTargets, ['/models?model=model-1']);
    assert.equal(facemarketRootTarget(loginTargets[0]), '/models?model=model-1');
  } finally { await h.close(); }
});
