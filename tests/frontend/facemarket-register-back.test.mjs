import test from 'node:test';
import assert from 'node:assert/strict';
import { findTree, modelComponentHarness } from './helpers/facemarketHarness.mjs';

// 등록 위저드의 "이전" — 신분증 촬영(id_capture) 화면과 수단 선택(method) 화면.
// 잡는 회귀: 이 두 화면은 `next` 가 없어서 푸터 자체가 안 그려졌고, 사용자는 수단을 잘못
// 골라도(신분증 대신 간편인증, 또는 반대) 되돌아갈 길 없이 갇혔다. 서버는 identity_method
// 를 등록 생성 때 박고 바꿔 주지 않으므로, 되돌리기 = 등록 취소 + 수단 선택으로 복귀다.

const flush = () => new Promise((resolve) => setImmediate(resolve));
const button = (tree, label) => findTree(tree, (node) => node.type === 'button' && node.props.children === label);
const idCaptureRecord = () => ({
  id: 'enrollment-1', status: 'id_capture_pending', identityMethod: 'simple_auth', photos: [],
  consentDocumentVersion: '2026-09-v1', termsConsentVersion: '2026-09-v1', overseasConsentVersion: '2026-09-v1',
});
// states: 0 step · 1 enrollment · 2 sub · 3 error · 4 busy · 5 consents (ModelRegister 의 useState 순서)
const idCaptureStates = () => ['id_capture', idCaptureRecord(), 1, '', false, [true, true]];

// IDENTITY_METHODS 는 모듈 상수(import.meta.env)라 harness 를 만들기 전에 환경으로 정한다.
async function withMethods(methods, run) {
  const previous = process.env.VITE_FM_IDENTITY_METHODS;
  if (methods) process.env.VITE_FM_IDENTITY_METHODS = methods;
  else delete process.env.VITE_FM_IDENTITY_METHODS;
  try { await run(); } finally {
    if (previous === undefined) delete process.env.VITE_FM_IDENTITY_METHODS;
    else process.env.VITE_FM_IDENTITY_METHODS = previous;
  }
}

test('신분증 촬영 화면에 "이전" 버튼이 그려진다', async () => {
  const harness = await modelComponentHarness({ initialStates: idCaptureStates(), api: {} });
  try {
    const tree = harness.render();
    assert.ok(button(tree, '이전'), '신분증 촬영 화면에 이전 버튼이 없다 — 수단을 잘못 고른 사용자가 갇힌다');
    // 이 화면의 다음 동작은 IdDocumentStep 안의 "이 사진으로 확인 요청" 이다 — 푸터에 빈 primary 를 그리지 않는다.
    assert.equal(findTree(tree, (node) => node.type === 'button' && node.props.className === 'primary'), null);
  } finally { await harness.close(); }
});

test('이전을 누르면 등록을 취소하고 수단 선택으로 돌아가며 동의는 유지된다', async () => {
  await withMethods('mid,simple_auth', async () => {
    const cancelled = [];
    const harness = await modelComponentHarness({ initialStates: idCaptureStates(), api: {
      cancelEnrollment: async (id) => { cancelled.push(id); return { ...idCaptureRecord(), status: 'cancelled' }; },
    } });
    try {
      await button(harness.render(), '이전').props.onClick();
      await flush();
      assert.deepEqual(cancelled, ['enrollment-1'], '서버의 활성 등록을 취소해야 다른 수단으로 새 등록을 만들 수 있다');
      assert.equal(harness.runtime.states[0], 'method');
      assert.equal(harness.runtime.states[1], null, '취소된 등록을 화면이 계속 들고 있으면 다음 시작이 그 id 로 간다');
      assert.deepEqual(harness.runtime.states[5], [true, true], '이미 한 동의를 다시 시키지 않는다');
      assert.equal(harness.runtime.states[3], '');
      assert.equal(harness.runtime.states[4], false);
    } finally { await harness.close(); }
  });
});

test('수단이 하나뿐이면 이전은 동의 화면으로 간다(선택 화면은 자동으로 다시 시작해 버린다)', async () => {
  await withMethods(null, async () => {
    const harness = await modelComponentHarness({ initialStates: idCaptureStates(), api: {
      cancelEnrollment: async () => ({ ...idCaptureRecord(), status: 'cancelled' }),
    } });
    try {
      await button(harness.render(), '이전').props.onClick();
      await flush();
      assert.equal(harness.runtime.states[0], '1');
      assert.equal(harness.runtime.states[1], null);
    } finally { await harness.close(); }
  });
});

test('취소가 실패하면 신분증 화면에 남고 이유를 보여준다', async () => {
  const harness = await modelComponentHarness({ initialStates: idCaptureStates(), api: {
    cancelEnrollment: async () => { throw new Error('서버가 잠시 응답하지 않아요.'); },
  } });
  try {
    await button(harness.render(), '이전').props.onClick();
    await flush();
    assert.equal(harness.runtime.states[0], 'id_capture');
    assert.equal(harness.runtime.states[1]?.id, 'enrollment-1', '취소가 안 됐는데 등록을 잃으면 다음 동작이 전부 어긋난다');
    assert.equal(harness.runtime.states[3], '서버가 잠시 응답하지 않아요.');
    assert.equal(harness.runtime.states[4], false);
  } finally { await harness.close(); }
});

test('이전을 누른 뒤 도착한 신분증 업로드 응답은 무시한다(취소된 등록으로 실패 화면에 떨어지지 않게)', async () => {
  await withMethods('mid,simple_auth', async () => {
    const harness = await modelComponentHarness({ initialStates: idCaptureStates(), api: {
      cancelEnrollment: async () => ({ ...idCaptureRecord(), status: 'cancelled' }),
      // 이전으로 취소한 뒤 늦게 도착한 409 → onStale → 재조회는 취소된 등록을 돌려준다.
      getEnrollment: async () => ({ ...idCaptureRecord(), status: 'cancelled' }),
    } });
    try {
      const tree = harness.render();
      const idStep = findTree(tree, (node) => typeof node.type === 'function' && node.props?.onStale);
      assert.ok(idStep, 'IdDocumentStep 을 찾지 못했다');
      await button(tree, '이전').props.onClick();
      await flush();
      assert.equal(harness.runtime.states[0], 'method');
      await idStep.props.onStale(Object.assign(new Error('stale'), { status: 409 }));
      await flush();
      assert.equal(harness.runtime.states[0], 'method', '늦게 온 응답이 화면을 실패로 뒤집었다');
      assert.equal(harness.runtime.states[1], null);
    } finally { await harness.close(); }
  });
});

test('수단 선택 화면의 이전이 실제로 그려지고 동의 화면으로 돌아간다', async () => {
  await withMethods('mid,simple_auth', async () => {
    const harness = await modelComponentHarness({ initialStates: ['method', null, 1, '', false, [true, true]], api: {} });
    try {
      const tree = harness.render();
      const back = button(tree, '이전');
      assert.ok(back, '수단 선택 화면에 이전이 없다 — previous 가 정의돼 있어도 푸터가 next 없이는 안 그려졌다');
      assert.equal(findTree(tree, (node) => node.type === 'button' && node.props.className === 'primary'), null);
      back.props.onClick();
      assert.equal(harness.runtime.states[0], '1');
    } finally { await harness.close(); }
  });
});
