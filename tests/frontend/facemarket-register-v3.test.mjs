import test from 'node:test';
import assert from 'node:assert/strict';
const module = await import('../../src/features/model/registerSlots.js').catch(() => ({}));

test('18장의 서로 다른 슬롯이 있어야 확인 단계를 마칠 수 있어요', () => {
  assert.equal(typeof module.photoProgress, 'function');
  const photos = Array.from({ length: 18 }, (_, i) => ({ slot: i < 8 ? `face${String(i + 1).padStart(2, '0')}` : i < 13 ? `torso${String(i - 7).padStart(2, '0')}` : `full${String(i - 12).padStart(2, '0')}` }));
  assert.equal(module.photoProgress(photos).complete, true);
  assert.equal(module.photoProgress([...photos.slice(0, 17), photos[0]]).complete, false);
  assert.equal(module.photoProgress(photos.slice(0, 8), 'face').complete, true);
  assert.equal(module.photoProgress(photos.slice(0, 12), 'torso').complete, false);
});

test('구형 사진 각도는 해당 슬롯 한 칸으로만 복원해요', () => {
  assert.equal(typeof module.photoProgress, 'function');
  const progress = module.photoProgress([{ angle: 'front' }, { angle: 'angle45' }, { angle: 'side' }, { slot: 'face01' }]);
  assert.equal(progress.count, 3);
  assert.equal(progress.complete, false);
});

test('마지막 사용 카테고리는 끌 수 없고 등록 조건에는 허용 품목만 있어요', () => {
  assert.equal(typeof module.toggleRegisterCategory, 'function');
  assert.deepEqual(module.toggleRegisterCategory(['액티브웨어'], '액티브웨어'), ['액티브웨어']);
  assert.deepEqual(module.toggleRegisterCategory(['액티브웨어'], '없는 값'), ['액티브웨어']);
  assert.deepEqual(module.defaultRegisterTerms(), { allowedUse: ['일반 의류', '액티브웨어', '홈웨어·잠옷'] });
  const previous = globalThis.sessionStorage;
  let saved;
  globalThis.sessionStorage = {
    getItem: () => JSON.stringify({ allowedUse: ['액티브웨어'], validDays: 365 }),
    setItem: (_key, value) => { saved = JSON.parse(value); },
  };
  try {
    assert.deepEqual(module.readRegisterDraft('enrollment-1'), { allowedUse: ['액티브웨어'] });
    module.saveRegisterDraft('enrollment-1', { allowedUse: ['홈웨어·잠옷'], validDays: 730 });
    assert.deepEqual(saved, { allowedUse: ['홈웨어·잠옷'] });
  } finally {
    if (previous === undefined) delete globalThis.sessionStorage;
    else globalThis.sessionStorage = previous;
  }
});

test('복원은 동의와 사진, 발급 대기와 완료 상태를 구분해요', () => {
  assert.equal(typeof module.restoreRegisterScreen, 'function');
  assert.deepEqual(module.restoreRegisterScreen({ status: 'identity_pending' }), { step: '1', sub: 1 });
  assert.deepEqual(module.restoreRegisterScreen({ status: 'photos_pending', photos: [] }), { step: '2', sub: 1 });
  assert.deepEqual(module.restoreRegisterScreen({ status: 'license_pending' }), { step: '3', sub: 4 });
  assert.deepEqual(module.restoreRegisterScreen({ status: 'vc_pending' }), { step: '4b', sub: 4 });
  assert.deepEqual(module.restoreRegisterScreen({ status: 'passed' }), { step: 'done', sub: 4 });
});

import { findTree, modelComponentHarness, eventually } from './helpers/facemarketHarness.mjs';
const flush = () => new Promise((resolve) => setImmediate(resolve));
const baseEnrollment = { consentDocumentVersion:'2026-09-v1', termsConsentVersion:'2026-09-v1', overseasConsentVersion:'2026-09-v1', id: 'enrollment-1', modelId: 'model-1', status: 'identity_pending', photos: [] };
const button = (tree, label) => findTree(tree, (node) => node.type === 'button' && node.props.children === label);
const commit = (harness) => { const tree = harness.render(); harness.runtime.effects.forEach((effect) => effect()); return tree; };
const textOf = (node) => node == null || typeof node === 'boolean' ? '' : typeof node !== 'object' ? String(node) : Array.isArray(node) ? node.map(textOf).join('') : textOf(node.props?.children);

test('동의 안내와 필수 표시를 읽고 마우스, 키보드, 터치로 철회 안내를 열어요', async () => {
  const h = await modelComponentHarness({ initialStates: ['1'], api: {} });
  try {
    let tree = h.render();
    const text = textOf(tree);
    for (const sentence of ['필수 항목을 확인한 뒤 신분증 인증을 진행해요.', '원본 얼굴 이미지는 비공개 저장소에 보관되며 노출되지 않습니다.', '철저한 본인인증을 위해 신분증 검사를 진행합니다. (이외 목적 사용X)', '언제든지 모델 등록을 잠시 중지하거나 철회할 수 있습니다']) assert.ok(text.includes(sentence));
    assert.equal((text.match(/\(필수\)/g) || []).length, 2);
    assert.ok(!text.includes('안내 · 동의 아님'));
    assert.equal(findTree(tree, node => node.props?.to === '/overseas-transfer'), null);
    assert.equal(findTree(tree, node => node.type === 'details'), null);
    const info = () => findTree(h.render(), node => node.type === 'button' && node.props['aria-label'] === '그만두면 이렇게 돼요');
    const tooltip = () => findTree(h.render(), node => node.props?.role === 'tooltip');
    assert.equal(info().props.children.props.size, 18);
    assert.equal(info().props['aria-describedby'], tooltip().props.id);
    assert.equal(tooltip().props.hidden, true);
    for (const event of ['onMouseEnter', 'onFocus', 'onClick']) {
      info().props[event](); assert.equal(tooltip().props.hidden, false);
      info().props.onKeyDown({ key: 'Escape' }); assert.equal(tooltip().props.hidden, true);
    }
    assert.ok(textOf(tooltip()).includes('이미 발행된 착용컷은 철회 후에도 기존 이용 조건에 따라 남아요.'));
  } finally { await h.close(); }
});

test('인증 대기자는 동의가 체크되어 있고 인증창 대기 중에도 같은 화면에 머물러요', async () => {
  let resolve;
  const h = await modelComponentHarness({ initialStates: [], honorHookDependencies: true, api: {
    getCurrentEnrollment: async () => baseEnrollment,
    runIdentityWidget: () => new Promise(done => { resolve = done; }),
    createIdentity: async () => ({ ...baseEnrollment, status: 'photos_pending' }),
  } });
  try {
    commit(h); await flush();
    const tree = h.render();
    assert.equal(h.runtime.states[0], '1');
    for (let i = 0; i < 2; i++) assert.equal(findTree(tree, node => node.props?.id === `consent-${i}`).props.checked, true);
    const pending = button(tree, '신분증 인증하기').props.onClick();
    assert.equal(h.runtime.states[0], '1');
    assert.equal(button(h.render(), '인증창에서 확인해 주세요').props.disabled, true);
    resolve('token'); await pending;
    assert.equal(h.runtime.states[0], '2'); assert.equal(h.runtime.states[2], 1);
  } finally { await h.close(); }
});

for (const [sub, encouragement] of [[1, null], [2, '거의 다 왔어요. 방금 하신 대로 아래 이미지들을 찍어 주세요.'], [3, '이제 마지막이에요. 아래 이미지들만 찍으면 끝나요.']]) {
  test(`사진 ${sub}단계는 공통 안내와 촬영 범위만 보여요`, async () => {
    const h = await modelComponentHarness({ initialStates: ['2', baseEnrollment, sub], api: {} });
    try {
      const tree = h.render(), text = textOf(tree);
      assert.equal(findTree(tree, node => node.type === 'h1').props.children, '사진을 등록해요');
      assert.ok(text.includes('모델 이미지를 만들려면 여러 방향의 사진이 필요해요. 각 카드의 예시와 같은 구도로 올려 주세요.'));
      assert.equal(findTree(tree, node => node.type === 'progress'), null);
      assert.doesNotMatch(text, /사진 18장|0 \/|전체 5단계|몸의 두께/);
      if (encouragement) assert.ok(text.includes(encouragement));
      assert.equal(button(tree, '다음').props.disabled, true);
    } finally { await h.close(); }
  });
}

test('확인 화면은 사진 그룹 세 행이며 각 고치기는 사진과 미리보기를 보존해요', async () => {
  const h = await modelComponentHarness({ initialStates: ['2', photoRecord(), 4], api: {} });
  try {
    h.render(); h.runtime.states[11] = { face01: 'blob:face' };
    for (const [label, sub] of [['얼굴', 1], ['상반신', 2], ['전신', 3]]) {
      const tree = h.render();
      assert.ok(textOf(tree).includes(`${label} ${sub === 1 ? 8 : 5}장`));
      findTree(tree, node => node.type === 'button' && node.props['aria-label'] === `${label} 사진 고치기`).props.onClick();
      assert.equal(h.runtime.states[2], sub);
      await button(h.render(), '다음').props.onClick();
      assert.equal(h.runtime.states[2], 4);
      assert.equal(h.runtime.states[1].photos.length, 18);
      assert.equal(h.runtime.states[11].face01, 'blob:face');
    }
    assert.ok(button(h.render(), '확인 완료'));
  } finally { await h.close(); }
});

test('조건에는 옷, 몸의 두께, 사용료 규칙, 필수 동의가 차례로 있고 동의 전에는 발급하지 않아요', async () => {
  const h = await modelComponentHarness({ initialStates: ['3', photoRecord(), 4], api: { createLicense: () => assert.fail('동의 전 발급 금지') } });
  try {
    const tree = h.render(), text = textOf(tree);
    assert.equal(findTree(tree, node => node.type === 'h1').props.children, '사용 조건을 정해요');
    assert.ok(text.includes('내 얼굴을 쓸 수 있는 옷과 몸의 두께를 알려 주세요. 여기서 정한 조건이 셀러 화면에 그대로 보여요.'));
    assert.ok(findTree(tree, node => node.props?.['aria-label'] === '몸의 두께, 선택 항목'));
    assert.ok(text.indexOf('몸의 두께셀러가 옷을') < text.indexOf('셀러 사용료 규칙'));
    assert.ok(text.indexOf('셀러 사용료 규칙') < text.indexOf('셀러 사용료 규칙에 동의해요'));
    assert.ok(text.includes('셀러가 한 번 모델을 이용한다면 14,900원 / 월정액으로 이용한다면 49,900원을 결제해요. (1개월 당 10회 제한)'));
    assert.ok(text.includes('이 중 70% 금액을 모델님께 자동 정산해드려요.'));
    assert.ok(text.includes('셀러 사용료 규칙에 동의해요 (필수)'));
    assert.doesNotMatch(text, /지급은 아직 시작 전|마이페이지에서 내역 보기/);
    assert.ok(findTree(tree, node => node.type === 'Link' && node.props.to === '/license-agreement'));
    assert.equal(button(tree, '라이선스 증서 발급하기').props.disabled, true);
    await button(tree, '라이선스 증서 발급하기').props.onClick();
    assert.equal(h.runtime.states[0], '3');
  } finally { await h.close(); }
});

test('조건 초안에 사용료 동의가 있어도 새로고침 후 다시 동의해야 해요', async () => {
  const previous = globalThis.sessionStorage;
  globalThis.sessionStorage = { getItem: () => JSON.stringify({ allowedUse: ['액티브웨어'], priceAgreed: true }), setItem: (_key, value) => assert.equal('priceAgreed' in JSON.parse(value), false) };
  const h = await modelComponentHarness({ initialStates: [], honorHookDependencies: true, api: { getCurrentEnrollment: async () => ({ ...photoRecord(), status: 'license_pending' }) } });
  try {
    commit(h); await flush(); const tree = commit(h);
    assert.equal(findTree(tree, node => node.props?.id === 'price-agreed').props.checked, false);
    assert.equal(button(tree, '라이선스 증서 발급하기').props.disabled, true);
  } finally { globalThis.sessionStorage = previous; await h.close(); }
});

test('발급 중에는 예상 시간과 이메일, 화면을 닫아도 계속된다는 안내를 보여요', async () => {
  const h = await modelComponentHarness({ initialStates: ['4b', photoRecord(), 4], api: {} });
  try {
    const text = textOf(h.render());
    assert.ok(text.includes('라이선스 증서를 발급하고 있어요'));
    assert.ok(text.includes('발급에 3분 정도 걸려요. 발급되면 이메일로 알려드려요. 로그인 후 마이페이지에서도 확인할 수 있어요.'));
    assert.ok(text.includes('이 화면을 닫아도 발급은 계속돼요.'));
  } finally { await h.close(); }
});

test('두 개 동의를 모두 받아야 인증을 시작하며 신분증 사진은 요청하지 않아요', async () => {
  const requests = [];
  const harness = await modelComponentHarness({ initialStates: [], honorHookDependencies: true, api: {
    getCurrentEnrollment: async () => { throw Object.assign(new Error(), { status: 404 }); },
    getFacemarketConfig: async () => ({ livenessRequired: false }),
    createEnrollment: async (body) => { requests.push(body); return baseEnrollment; },
    runIdentityWidget: async () => 'token-1',
    createIdentity: async (id, body) => { requests.push({ id, ...body }); return { ...baseEnrollment, status: 'photos_pending', nameMasked: '김*연' }; },
  } });
  try {
    commit(harness); await flush(); let tree = commit(harness);
    const start = () => button(tree, '동의하고 신분증 인증하기');
    assert.equal(start()?.props.disabled, true);
    for (let i = 0; i < 2; i++) {
      const check = findTree(tree, (node) => node.type === 'input' && node.props.id === `consent-${i}`);
      assert.ok(check); assert.equal(check.props.checked, false);
      check.props.onChange({ target: { checked: true } }); tree = commit(harness);
    }
    assert.equal(start().props.disabled, false);
    await start().props.onClick();
    assert.equal(requests[0].documentVersion, '2026-09-v1');
    assert.deepEqual(requests[1], { id: 'enrollment-1', token: 'token-1' });
    assert.equal(harness.runtime.states[0], '2');
  } finally { await harness.close(); }
});

test('사진 그룹이 비어 있으면 다음 버튼이 잠겨요', async () => {
  const harness = await modelComponentHarness({ initialStates: ['2', { ...baseEnrollment, status: 'photos_pending' }], api: {} });
  try {
    const tree = harness.render();
    assert.equal(button(tree, '다음')?.props.disabled, true);
  } finally { await harness.close(); }
});

const agreePrice = (harness) => findTree(harness.render(), node => node.type === 'input' && node.props.id === 'price-agreed').props.onChange({ target: { checked: true } });

test('발급 실패 뒤 조건이 남고 재시도로 완료돼요', async () => {
  let attempts = 0;
  const requests = [];
  const harness = await modelComponentHarness({ initialStates: ['3', { ...baseEnrollment, status: 'license_pending' }, 4], api: {
    createLicense: async (body) => { requests.push(body); if (++attempts === 1) throw new Error('발급 서버가 응답하지 않아요'); return { id: 'license-1', modelId: 'model-1', status: 'active' }; },
  } });
  try {
    agreePrice(harness);
    await button(harness.render(), '라이선스 증서 발급하기').props.onClick();
    assert.equal(harness.runtime.states[0], '4c');
    await button(harness.render(), '다시 발급하기').props.onClick();
    assert.equal(harness.runtime.states[0], 'done');
    assert.equal(requests.length, 2); assert.deepEqual(requests[0], { enrollmentId: 'enrollment-1', allowedUse: ['일반 의류', '액티브웨어', '홈웨어·잠옷'] });
  } finally { await harness.close(); }
});

const allPhotos = () => module.SLOTS.map((slot) => ({ slot: slot.key, angle: slot.key, qcStatus: 'passed' }));
const photoRecord = () => ({ ...baseEnrollment, status: 'liveness_pending', photos: allPhotos() });

test('모든 사진을 올리면 세션이나 초상 없이 완료 요청을 보내요', async () => {
  const calls = [];
  const harness = await modelComponentHarness({ initialStates: ['2', photoRecord(), 4], api: {
    getFacemarketConfig: async () => ({ livenessRequired: false, faceMatchEnabled: false }),
    completeEnrollment: async (id, body) => { calls.push({ id, body }); return { passed: true, status: 'license_pending', modelId: 'model-1' }; },
    createLivenessSession: () => assert.fail('라이브니스가 꺼져 있어요'),
    runIdentityWidget: () => assert.fail('이미 본인확인을 마쳤어요'),
  } });
  try {
    await button(harness.render(), '확인 완료').props.onClick();
    assert.deepEqual(calls, [{ id: 'enrollment-1', body: { sessionId: undefined } }]);
    assert.equal(harness.runtime.states[0], '3');
  } finally { await harness.close(); }
});

for (const bodyType of [null, 'plump']) {
  test(`조건에서 체형 ${bodyType || '미선택'}을 저장한 뒤 미리보기 없이 발급해요`, async () => {
    const calls = [];
    const harness = await modelComponentHarness({ initialStates: ['3', photoRecord(), 4], api: {
      submitPhysique: async (body) => { calls.push(body); return { ...photoRecord(), bodyType: body.bodyType }; },
      createLicense: async (body) => { calls.push(body); return { id: 'license-1', status: 'active' }; },
    } });
    try {
      if (bodyType) findTree(harness.render(), node => node.type === 'button' && node.props.children?.some?.(child => child?.props?.children === '통통')).props.onClick();
      agreePrice(harness);
      await button(harness.render(), '라이선스 증서 발급하기').props.onClick();
      if (bodyType) assert.deepEqual(calls.shift(), { enrollmentId: 'enrollment-1', bodyType: 'plump' });
      assert.deepEqual(calls, [{ enrollmentId: 'enrollment-1', allowedUse: ['일반 의류', '액티브웨어', '홈웨어·잠옷'] }]);
      assert.equal(harness.runtime.states[0], 'done');
    } finally { await harness.close(); }
  });
}

test('확인 화면에서 전신 고치기로 사진을 교체하고 확인으로 돌아와요', async () => {
  const calls = [];
  const harness = await modelComponentHarness({ initialStates: ['2', photoRecord(), 4], api: {
    uploadEnrollmentPhoto: async (body) => { calls.push(body); return { slot: body.slot, qcStatus: 'passed' }; },
  } });
  try {
    const file = new File(['photo'], 'mine.jpg', { type: 'image/jpeg' });
    findTree(harness.render(), node => node.type === 'button' && node.props['aria-label'] === '전신 사진 고치기').props.onClick();
    assert.equal(harness.runtime.states[2], 3);
    let tree = harness.render();
    const input = findTree(tree, (node) => node.type === 'input' && node.props['aria-label']?.startsWith('18번'));
    input.props.onChange({ target: { files: [file], value: '' } });
    await eventually(() => calls.length === 1); await flush();
    assert.equal(calls[0].slot, 'full05'); assert.equal(calls[0].fileBlob, file);
    tree = harness.render();
    assert.ok(findTree(tree, (node) => node.type === 'img' && node.props.alt === '18번 내 사진'));
    await button(tree, '다음').props.onClick();
    assert.equal(harness.runtime.states[2], 4);
    assert.ok(findTree(harness.render(), node => node.type === 'img' && node.props.alt === '18번 내 사진'));
    Object.values(harness.runtime.states[11]).forEach(URL.revokeObjectURL);
  } finally { await harness.close(); }
});

test('사진 삭제가 실패하면 기존 사진과 완료 판정을 유지해요', async () => {
  const harness = await modelComponentHarness({ initialStates: ['2', photoRecord(), 1], api: { deleteEnrollmentPhoto: async () => { throw new Error('삭제 실패'); } } });
  try {
    await findTree(harness.render(), (node) => node.type === 'button' && node.props['aria-label'] === '1번 사진 지우기').props.onClick();
    assert.equal(harness.runtime.states[1].photos.length, 18); assert.equal(harness.runtime.states[3], '삭제 실패');
  } finally { await harness.close(); }
});

test('사진 삭제가 성공하면 다음 버튼이 잠기고 사진 개수가 줄어요', async () => {
  const calls = [];
  const harness = await modelComponentHarness({ initialStates: ['2', photoRecord(), 1], api: { deleteEnrollmentPhoto: async (...args) => calls.push(args) } });
  try {
    await findTree(harness.render(), (node) => node.type === 'button' && node.props['aria-label'] === '1번 사진 지우기').props.onClick();
    assert.deepEqual(calls, [['enrollment-1', 'face01']]); assert.equal(harness.runtime.states[1].photos.length, 17);
    assert.equal(button(harness.render(), '다음').props.disabled, true);
  } finally { await harness.close(); }
});

for (const condition of ['일반 의류', '액티브웨어', '홈웨어·잠옷']) {
  test(`조건 화면에서 ${condition}만 남기면 마지막 스위치가 잠겨요`, async () => {
    const harness = await modelComponentHarness({ initialStates: ['3', photoRecord(), 4], api: {} });
    try {
      for (const other of ['일반 의류', '액티브웨어', '홈웨어·잠옷'].filter((value) => value !== condition)) {
        findTree(harness.render(), (node) => node.props?.role === 'switch' && node.props['aria-label'] === `${other} 허용`).props.onClick();
      }
      const last = findTree(harness.render(), (node) => node.props?.role === 'switch' && node.props['aria-label'] === `${condition} 허용`);
      assert.equal(last.props.disabled, true); assert.equal(last.props['aria-checked'], true);
    } finally { await harness.close(); }
  });
}

test('조건 화면에는 유효기간 선택 없이 허용 품목만 남아요', async () => {
  const harness = await modelComponentHarness({ initialStates: ['3', photoRecord(), 4], api: {} });
  try {
    assert.equal(button(harness.render(), '1년'), null);
    assert.equal(button(harness.render(), '2년'), null);
    assert.equal(button(harness.render(), '영구'), null);
    assert.equal(button(harness.render(), '라이선스 증서 발급하기').props.disabled, true);
    agreePrice(harness);
    assert.equal(button(harness.render(), '라이선스 증서 발급하기').props.disabled, false);
    button(harness.render(), '이전').props.onClick(); assert.equal(harness.runtime.states[0], '2');
    assert.equal(harness.runtime.states[2], 4);
    assert.deepEqual(harness.runtime.states[6], { allowedUse: ['일반 의류', '액티브웨어', '홈웨어·잠옷'] });
  } finally { await harness.close(); }
});

for (const outcome of ['awaiting_confirm', 'verified', 'error']) {
  test(`화면을 떠난 뒤 새 등록 조회 응답을 무시해요: ${outcome}`, async () => {
    let settle, reject;
    const lookup = new Promise((resolve, fail) => { settle = resolve; reject = fail; });
    const destinations = [];
    const harness = await modelComponentHarness({ initialStates: ['done', { modelId: 'model-1' }], honorHookDependencies: true, api: { listMyModels: () => lookup } });
    try {
      harness.runtime.navigate = (to) => destinations.push(to);
      const tree = harness.render(); const cleanup = harness.runtime.effects[0]();
      const pending = button(tree, '새 생체 등록 시작').props.onClick(); cleanup();
      const updates = harness.runtime.updates.length;
      if (outcome === 'error') reject(new Error('late')); else settle([{ id: 'model-1', status: outcome }]);
      await pending; await flush();
      assert.deepEqual(destinations, []); assert.equal(harness.runtime.updates.length, updates);
    } finally { await harness.close(); }
  });
}

test('발급 직후 새 등록을 시작해도 동의를 다시 받아요', async () => {
  const harness = await modelComponentHarness({ initialStates: ['done', { modelId: 'model-1' }], api: { listMyModels: async () => [{ id: 'model-1', status: 'verified' }] } });
  try {
    await button(harness.render(), '새 생체 등록 시작').props.onClick();
    assert.equal(harness.runtime.states[0], '1'); assert.deepEqual(harness.runtime.states[5], [false, false]);
    assert.equal(button(harness.render(), '동의하고 신분증 인증하기').props.disabled, true);
  } finally { await harness.close(); }
});

test('동의를 누른 사이 테스트컷이 도착했으면 확정 화면으로 이동해요', async () => {
  const destinations = [];
  const harness = await modelComponentHarness({ initialStates: ['1', null, 1, '', false, [true, true]], api: { createEnrollment: async () => { throw Object.assign(new Error('먼저 확정해 주세요'), { code: 'model_confirmation_required' }); } } });
  try { harness.runtime.navigate = (to) => destinations.push(to); await button(harness.render(), '동의하고 신분증 인증하기').props.onClick(); assert.deepEqual(destinations, ['/model/confirm']); }
  finally { await harness.close(); }
});

test('일시적인 인증 오류 뒤에는 등록을 취소하지 않고 다시 인증해요', async () => {
  let tries = 0;
  const harness = await modelComponentHarness({ initialStates: ['1', baseEnrollment, 1, '', false, [true, true]], api: {
    runIdentityWidget: async () => { if (++tries === 1) throw new Error('인증창이 닫혔어요'); return 'token'; },
    createIdentity: async () => ({ ...baseEnrollment, status: 'photos_pending' }),
    cancelEnrollment: () => assert.fail('등록을 취소하지 않아요'),
  } });
  try {
    await button(harness.render(), '신분증 인증하기').props.onClick(); assert.equal(harness.runtime.states[0], '1');
    assert.equal(findTree(harness.render(), node => node.props?.role === 'alert').props.children, '인증창이 닫혔어요');
    await button(harness.render(), '다시 인증하기').props.onClick(); assert.equal(harness.runtime.states[0], '2'); assert.equal(tries, 2);
  } finally { await harness.close(); }
});

test('서버 설정을 못 읽으면 완료 요청을 보내지 않고 다시 시도할 수 있어요', async () => {
  let tries = 0;
  const harness = await modelComponentHarness({ initialStates: ['2', photoRecord(), 4], api: {
    getFacemarketConfig: async () => { if (++tries === 1) throw new Error('설정 조회 실패'); return { livenessRequired: false }; },
    completeEnrollment: async () => ({ status: 'license_pending' }),
  } });
  try {
    await button(harness.render(), '확인 완료').props.onClick(); assert.equal(harness.runtime.states[0], '2');
    await button(harness.render(), '확인 완료').props.onClick(); assert.equal(harness.runtime.states[0], '3');
  } finally { await harness.close(); }
});

test('라이선스 목록의 조건 폼도 기간 선택 없이 발급해요', async () => {
  const calls = [];
  const harness = await modelComponentHarness({ entry: '/src/features/model/ModelLicense.jsx', exportName: 'ModelLicense', initialStates: ['ready','flow',{id:'enrollment-1',status:'license_pending'},[],null], api: { createLicense: async (body) => { calls.push(body); return {id:'license-1'}; } } });
  try {
    const termsStep = findTree(harness.render(), (node) => node.type?.name === 'TermsStep');
    harness.runtime.states=[];
    const render=()=>{harness.runtime.stateCursor=0;return termsStep.type({...termsStep.props,onIssued:()=>{}});};
    for (const label of ['일반 의류','홈웨어·잠옷']) findTree(render(),n=>n.type==='Toggle'&&n.props.label===label).props.onChange(false);
    const tree=render();
    assert.equal(findTree(tree,n=>n.type==='Field'&&n.props.type==='number'),null);
    assert.equal(findTree(tree,n=>n.type==='Chips'),null);
    await findTree(tree,n=>n.type==='Button'&&n.props.children==='라이선스 발급').props.onClick();
    assert.deepEqual(calls,[{enrollmentId:'enrollment-1',allowedUse:['액티브웨어']}]);
  } finally { await harness.close(); }
});

for (const screen of ['loading','1','2','3','4b','4c','done','processing','poll_error','failed']) {
  test(`등록 ${screen} 화면에서 훅 개수가 같아요`, async () => {
    const harness=await modelComponentHarness({initialStates:['loading',photoRecord(),4],api:{}});
    try {
      harness.render();const counts=[harness.runtime.stateCursor,harness.runtime.refCursor,harness.runtime.effects.length];
      harness.runtime.states[0]=screen;harness.render();
      assert.deepEqual([harness.runtime.stateCursor,harness.runtime.refCursor,harness.runtime.effects.length],counts);
    } finally {await harness.close();}
  });
}

test('발급 중 같은 버튼을 두 번 눌러도 요청은 한 번만 보내요', async () => {
  let resolve, calls=0;
  const harness=await modelComponentHarness({initialStates:['3',{...baseEnrollment,status:'license_pending'},4],api:{createLicense:()=>{calls++;return new Promise(done=>resolve=done);}}});
  try {
    agreePrice(harness);
    const action=button(harness.render(),'라이선스 증서 발급하기').props.onClick;
    const pending=action();await action();assert.equal(calls,1);resolve({id:'l1',status:'active'});await pending;
  } finally {await harness.close();}
});

test('완료 조건 조회 실패 때 가격이나 유효기간을 실제 발급값처럼 표시하지 않아요', async () => {
  const harness=await modelComponentHarness({initialStates:['done',{modelId:'model-1'}],api:{}});
  try {
    assert.ok(findTree(harness.render(),n=>n.type==='p'&&n.props.children==='발급한 조건은 증서에서 확인할 수 있어요.'));
    assert.ok(findTree(harness.render(),n=>n.type==='Link'&&n.props.to==='/status'&&n.props.children==='마이페이지로'));
  } finally {await harness.close();}
});

test('새 등록을 시작하면 이전 등록의 사진 미리보기를 해제해요', async () => {
  const harness=await modelComponentHarness({initialStates:['done',{modelId:'model-1'}],api:{listMyModels:async()=>[]}});
  const revoke=URL.revokeObjectURL;const released=[];URL.revokeObjectURL=(value)=>released.push(value);
  try {
    harness.render();harness.runtime.refs[2].current={face01:'blob:previous'};harness.runtime.states[11]={face01:'blob:previous'};
    await button(harness.render(),'새 생체 등록 시작').props.onClick();
    assert.deepEqual(released,['blob:previous']);assert.deepEqual(harness.runtime.states[11],{});
  } finally {URL.revokeObjectURL=revoke;await harness.close();}
});


test('이전 버전 등록을 이어갈 때 사진보다 새 필수 동의를 먼저 받아요', async () => {
  const oldRecord={...photoRecord(),consentDocumentVersion:'2026-08-v2',termsConsentVersion:null,overseasConsentVersion:null};
  const harness=await modelComponentHarness({initialStates:[],honorHookDependencies:true,api:{getCurrentEnrollment:async()=>oldRecord}});
  try {commit(harness);await flush();assert.equal(harness.runtime.states[0],'1');assert.deepEqual(harness.runtime.states[5],[false,false]);}
  finally {await harness.close();}
});

test('발급 중 새로고침하면 저장한 조건으로 발급을 이어가요', async () => {
  const calls=[];
  const record={...photoRecord(),status:'vc_pending',licenseId:'license-1',licenseTerms:{allowedUse:['액티브웨어'],validDays:730}};
  const harness=await modelComponentHarness({initialStates:[],honorHookDependencies:true,api:{getCurrentEnrollment:async()=>record,createLicense:async body=>{calls.push(body);return {id:'license-1',status:'active',modelId:'model-1'};}}});
  try {
    commit(harness);await flush();assert.equal(harness.runtime.states[0],'4b');
    commit(harness);await flush();assert.equal(harness.runtime.states[0],'done');
    assert.deepEqual(calls,[{enrollmentId:'enrollment-1',allowedUse:['액티브웨어']}]);
  } finally {await harness.close();}
});

test('사진 완료를 두 번 눌러도 라이브니스 세션은 한 번만 만들어요',async()=>{
  let calls=0,resolve;
  const harness=await modelComponentHarness({initialStates:['2',photoRecord(),4],api:{getFacemarketConfig:async()=>({livenessRequired:true}),createLivenessSession:()=>{calls++;return new Promise(done=>resolve=done);}}});
  try {
    const action=button(harness.render(),'확인 완료').props.onClick;
    const first=action();const second=action();await flush();assert.equal(calls,1);
    resolve({sessionId:'session-1'});await first;await second;
    assert.equal(harness.runtime.states[0],'liveness');
  }finally{if(resolve)resolve({sessionId:'session-1'});await harness.close();}
});

test('처리 중 일시적인 조회 오류 뒤에는 등록을 이어가요',async()=>{
  const original=setTimeout;globalThis.setTimeout=(callback,ms,...args)=>original(callback,ms===2500?0:ms,...args);
  let calls=0;
  const harness=await modelComponentHarness({initialStates:['processing',photoRecord(),4],api:{getEnrollment:async()=>{if(++calls===1)throw new Error('temporary');return {...photoRecord(),status:'license_pending'};}}});
  let cleanup;
  try{harness.render();cleanup=harness.runtime.effects[4]();await eventually(()=>harness.runtime.states[0]==='3');assert.equal(calls,2);}
  finally{cleanup?.();globalThis.setTimeout=original;await harness.close();}
});

for (const action of ['deadline','unmount']) {
  test(`처리 조회가 멈춰도 ${action==='deadline'?'제한 시간 뒤':'화면을 떠나면'} 요청을 중단해요`,async()=>{
    const original=setTimeout;let deadline,requestSignal;
    globalThis.setTimeout=(callback,ms,...args)=>{if(ms===120000){deadline=callback;return 0;}return original(callback,ms,...args);};
    const harness=await modelComponentHarness({initialStates:['processing',photoRecord(),4],api:{getEnrollment:(id,{signal})=>{requestSignal=signal;return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(new Error('aborted')),{once:true}));}}});
    let cleanup;
    try{
      harness.render();cleanup=harness.runtime.effects[4]();assert.ok(requestSignal);const updates=harness.runtime.updates.length;
      if(action==='deadline'){deadline();await eventually(()=>harness.runtime.states[0]==='poll_error');}
      else {cleanup();await flush();assert.equal(harness.runtime.updates.length,updates);}
      assert.equal(requestSignal.aborted,true);
    }finally{cleanup?.();globalThis.setTimeout=original;await harness.close();}
  });
}


test('구버전 동의의 인증 대기자는 새 동의를 서버에 기록한 뒤 인증해요', async () => {
  const calls = [];
  const old = { ...baseEnrollment, termsConsentVersion: null };
  const h = await modelComponentHarness({ initialStates: [], honorHookDependencies: true, api: {
    getCurrentEnrollment: async () => old,
    createEnrollment: async () => { calls.push('consent'); return baseEnrollment; },
    runIdentityWidget: async () => { calls.push('identity'); return 'token'; },
    createIdentity: async () => ({ ...baseEnrollment, status: 'photos_pending' }),
  } });
  try {
    commit(h); await flush();
    for (let i = 0; i < 2; i++) findTree(h.render(), node => node.props?.id === `consent-${i}`).props.onChange({ target: { checked: true } });
    const tree = h.render();
    await findTree(tree, node => node.type === 'button' && node.props.className === 'primary').props.onClick();
    assert.deepEqual(calls, ['consent', 'identity']);
  } finally { await h.close(); }
});

test('조건에서 기존 체형을 지우면 서버에 해제를 저장한 다음 발급해요', async () => {
  const calls = [];
  const record = { ...photoRecord(), status: 'license_pending', bodyType: 'plump' };
  const h = await modelComponentHarness({ initialStates: [], honorHookDependencies: true, api: {
    getCurrentEnrollment: async () => record,
    submitPhysique: async body => { calls.push(body); return { ...record, bodyType: null }; },
    createLicense: async () => { calls.push('issue'); return { id: 'license-1' }; },
  } });
  try {
    commit(h); await flush();
    button(h.render(), '선택 지우기').props.onClick();
    agreePrice(h);
    await button(h.render(), '라이선스 증서 발급하기').props.onClick();
    assert.deepEqual(calls, [{ enrollmentId: 'enrollment-1', bodyType: null }, 'issue']);
  } finally { await h.close(); }
});

test('체형 저장 실패는 조건 화면에서 재시도하며 저장 전에는 발급하지 않아요', async () => {
  let attempts = 0, issues = 0;
  const h = await modelComponentHarness({ initialStates: ['3', photoRecord(), 4], api: {
    submitPhysique: async () => { if (++attempts === 1) throw new Error('체형 저장 실패'); return { ...photoRecord(), bodyType: 'plump' }; },
    createLicense: async () => { issues++; return { id: 'license-1' }; },
  } });
  try {
    findTree(h.render(), node => node.type === 'button' && node.props.children?.some?.(child => child?.props?.children === '통통')).props.onClick();
    agreePrice(h);
    await button(h.render(), '라이선스 증서 발급하기').props.onClick();
    assert.equal(issues, 0); assert.equal(h.runtime.states[0], '3');
    assert.equal(findTree(h.render(), node => node.props?.role === 'alert').props.children, '체형 저장 실패');
    await button(h.render(), '라이선스 증서 발급하기').props.onClick();
    assert.equal(issues, 1); assert.equal(h.runtime.states[0], 'done');
  } finally { await h.close(); }
});
