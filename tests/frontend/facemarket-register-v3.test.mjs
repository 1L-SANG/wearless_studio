import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const module = await import('../../src/features/model/registerSlots.js').catch(() => ({}));

test('동의 문서 버전이 서버 상수와 같다', () => {
  // 화면이 보여 준 동의문의 버전이 곧 서버가 기록하는 값이다. 둘이 갈라지면 서버가
  // stale_consent_version 으로 등록을 막거나(프론트가 낡음), 실제로 보여 준 적 없는
  // 버전이 동의 이벤트 테이블에 남는다(서버가 낡음) — 어느 쪽도 증거가 안 된다.
  const server = readFileSync(
    new URL('../../server/app/facemarket_enrollment.py', import.meta.url), 'utf8',
  );
  const shipped = /BIOMETRIC_CONSENT_VERSION = "([^"]+)"/.exec(server)?.[1];
  assert.ok(shipped, '서버 상수를 못 찾았다');
  assert.equal(module.CONSENT_VERSION, shipped);
  // 게시본 manifest 도 같은 값이어야 한다 — 사용자가 실제로 읽는 문서다.
  const manifest = JSON.parse(readFileSync(
    new URL('../../public/legal/manifest.json', import.meta.url), 'utf8',
  ));
  const consent = manifest.find((item) => item.slug === 'biometric-consent');
  assert.equal(consent.version, shipped, '게시된 동의서 버전이 기록되는 값과 다르다');
});

test('18장의 서로 다른 슬롯이 있어야 확인 단계를 마칠 수 있어요', () => {
  assert.equal(typeof module.photoProgress, 'function');
  const photos = module.SLOTS.map((slot) => ({ slot: slot.key }));
  assert.equal(photos.length, 18);
  assert.equal(module.photoProgress(photos).complete, true);
  // 같은 칸을 두 번 올려도 채워지지 않는다
  assert.equal(module.photoProgress([...photos.slice(0, 17), photos[0]]).complete, false);
  // 그늘 묶음은 9장(기존 7 + 옆모습 오른쪽 + 뒷모습)
  assert.equal(module.photoProgress(photos.slice(0, 9), 'sh').complete, true);
  assert.equal(module.photoProgress(photos.slice(0, 8), 'sh').complete, false);
  assert.equal(module.photoProgress(photos.slice(0, 11), 'sl').complete, false);
});

test('슬롯 이름과 순서가 서버 정본과 같다', () => {
  // 이름이 갈라지면 업로드가 invalid_slot 으로 막히고, 순서가 갈라지면 촬영 안내와
  // 파일 이름(<조명>__<컷>)이 어긋난다. 정본은 서버 facemarket_photos.PHOTO_SLOTS 다.
  const server = readFileSync(
    new URL('../../server/app/facemarket_photos.py', import.meta.url), 'utf8',
  );
  const block = /PHOTO_SLOTS: tuple\[str, \.\.\.\] = \(([\s\S]*?)\)/.exec(server)?.[1];
  assert.ok(block, '서버 PHOTO_SLOTS 를 못 찾았다');
  const slots = [...block.matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1]);
  assert.deepEqual(module.SLOTS.map((slot) => slot.key), slots);
});

test('옆모습 오른쪽·뒷모습 칸이 있고, 옆모습 왼쪽은 키를 그대로 쓴다', () => {
  const byKey = Object.fromEntries(module.SLOTS.map((slot) => [slot.key, slot]));
  // sh_side 는 이미 올라온 사진이 있는 칸이다 — 키를 바꾸면 그 사진들이 길을 잃는다.
  assert.ok(byKey.sh_side, '옆모습(왼쪽) 칸의 키는 그대로여야 한다');
  assert.equal(byKey.sh_side.angle, 'left');
  assert.equal(byKey.sh_side_right.angle, 'right');
  assert.equal(byKey.sh_back.angle, 'back');
  assert.equal(byKey.sh_side.group, byKey.sh_back.group, '옆모습·뒷모습은 그늘에서 함께 찍는다');
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
  const review = module.PHOTO_REVIEW_SUB;
  assert.equal(review, 5, '조명 네 화면 다음이 확인 화면이다');
  assert.deepEqual(module.restoreRegisterScreen({ status: 'license_pending' }), { step: '3', sub: review });
  assert.deepEqual(module.restoreRegisterScreen({ status: 'vc_pending' }), { step: 'done', sub: review });
  assert.deepEqual(module.restoreRegisterScreen({ status: 'passed' }), { step: 'done', sub: review });
});

import { findTree, modelComponentHarness, eventually } from './helpers/facemarketHarness.mjs';
const flush = () => new Promise((resolve) => setImmediate(resolve));
const baseEnrollment = { consentDocumentVersion:module.CONSENT_VERSION, termsConsentVersion:module.CONSENT_VERSION, overseasConsentVersion:module.CONSENT_VERSION, id: 'enrollment-1', modelId: 'model-1', status: 'identity_pending', photos: [] };
const button = (tree, label) => findTree(tree, (node) => node.type === 'button' && node.props.children === label);
const commit = (harness) => { const tree = harness.render(); harness.runtime.effects.forEach((effect) => effect()); return tree; };
const textOf = (node) => node == null || typeof node === 'boolean' ? '' : typeof node !== 'object' ? String(node) : Array.isArray(node) ? node.map(textOf).join('') : textOf(node.props?.children);

test('동의 안내와 필수 표시를 읽고 마우스, 키보드, 터치로 철회 안내를 열어요', async () => {
  const h = await modelComponentHarness({ initialStates: ['1'], api: {} });
  try {
    let tree = h.render();
    const text = textOf(tree);
    for (const sentence of ['약관에 동의한 뒤 본인인증을 진행해요.', '원본 사진은 공개되지 않아요.', '신분증은 본인확인에만 사용해요.', '언제든 등록을 중지하거나 철회할 수 있어요.']) assert.ok(text.includes(sentence));
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

// 최종리뷰 C1: runIdentity 가 '2'(사진 화면)를 하드코딩하면 simple_auth 지원자는 방금
// 끝낸 간편인증 뒤 신분증 촬영 화면을 영영 못 보고 사진 화면에서 "현재 등록 단계에서는
// 사진을 고칠 수 없어요"만 본다. 위 테스트(mid, photos_pending → '2')와 짝을 이뤄 두
// 경로를 실제로 렌더/클릭/await 해서 증명한다 — 이 파일은 JSX 라 node --test 가 직접
// import 를 못 하므로, ModelRegister.jsx 에 대한 유일한 보증이 정규식 소스 대조뿐이던
// 문제(최종리뷰가 지적한 바로 그 결함)를 modelComponentHarness(Vite SSR + jsx 스텁)로
// 실제 실행 검증한다.
test('간편인증 지원자는 인증이 끝나면 신분증 촬영 화면으로 가요(사진 화면으로 새지 않아요)', async () => {
  const simpleAuthEnrollment = { ...baseEnrollment, identityMethod: 'simple_auth' };
  let resolve;
  const h = await modelComponentHarness({ initialStates: [], honorHookDependencies: true, api: {
    getCurrentEnrollment: async () => simpleAuthEnrollment,
    runIdentityWidget: () => new Promise(done => { resolve = done; }),
    createIdentity: async () => ({ ...simpleAuthEnrollment, status: 'id_capture_pending' }),
  } });
  try {
    commit(h); await flush();
    const tree = h.render();
    assert.equal(h.runtime.states[0], '1');
    // 간편인증 사용자에겐 "신분증 인증하기"가 아니라 "간편인증하기"로 보여요(PASS·카카오
    // 창과 문구가 어긋나면 안 되니까) — 그 버튼이 runIdentity() 를 부릅니다.
    const pending = button(tree, '간편인증하기').props.onClick();
    resolve('token'); await pending;
    assert.equal(h.runtime.states[0], 'id_capture', '신분증 촬영 화면(id_capture)으로 가야 한다 — 사진 화면(2)이 아니다');
    assert.equal(h.runtime.states[2], 1);
  } finally { await h.close(); }
});

for (const sub of [1, 2, 4]) {
  test(`사진 ${sub}단계는 공통 안내와 촬영 범위만 보여요`, async () => {
    const h = await modelComponentHarness({ initialStates: ['2', baseEnrollment, sub], api: {} });
    try {
      const tree = h.render(), text = textOf(tree);
      assert.equal(findTree(tree, node => node.type === 'h1').props.children, '온라인 모델 생성을 위해 필요한 이미지들을 업로드해요');
      assert.ok(text.includes('나중에 이어서 등록해도 돼요.'));
      const progress = findTree(tree, node => node.type === 'progress');
      assert.equal(progress.props.value, 0);
      assert.equal(progress.props.max, 18);
      assert.equal(findTree(tree, node => node.type === 'Link' && node.props.to === '/photo-guide'), null);
      assert.ok(text.includes(`${[1, 10, 13, 16][sub - 1]}~${[9, 12, 15, 18][sub - 1]}번째`));
      assert.ok(text.includes('흐리거나 실내라면'));
      assert.ok(text.includes(sub === 1 ? '9장을 더 올려 주세요.' : '3장을 더 올려 주세요.'));
      assert.doesNotMatch(text, /사진 17장|전체 5단계|몸의 두께/);
      assert.equal(button(tree, '다음').props.disabled, true);
    } finally { await h.close(); }
  });
}

test('확인 화면은 조명 네 행이며 각 고치기는 사진과 미리보기를 보존해요', async () => {
  const h = await modelComponentHarness({ initialStates: ['2', photoRecord(), module.PHOTO_REVIEW_SUB], api: {} });
  try {
    h.render(); h.runtime.states[11] = { sh_front: 'blob:face' };
    for (const [index, group] of module.PHOTO_GROUPS.entries()) {
      const tree = h.render();
      const count = module.SLOTS.filter((slot) => slot.group === group.id).length;
      assert.ok(textOf(tree).includes(`${group.title} ${count}장`));
      findTree(tree, node => node.type === 'button' && node.props['aria-label'] === `${group.title} 사진 고치기`).props.onClick();
      assert.equal(h.runtime.states[2], index + 1);
      await button(h.render(), '다음').props.onClick();
      assert.equal(h.runtime.states[2], module.PHOTO_REVIEW_SUB);
      assert.equal(h.runtime.states[1].photos.length, module.SLOTS.length);
      assert.equal(h.runtime.states[11].sh_front, 'blob:face');
    }
    assert.ok(button(h.render(), '확인 완료'));
  } finally { await h.close(); }
});

test('조건에는 옷, 사용료 규칙, 필수 동의가 있고 동의 전에는 발급하지 않아요', async () => {
  const h = await modelComponentHarness({ initialStates: ['3', { ...photoRecord(), status: 'license_pending' }, module.PHOTO_REVIEW_SUB], api: { createLicense: () => assert.fail('동의 전 발급 금지') } });
  try {
    const tree = h.render(), text = textOf(tree);
    assert.equal(findTree(tree, node => node.type === 'h1').props.children, '사용 조건을 정해요');
    assert.ok(text.includes('내 얼굴을 사용할 옷 종류를 골라 주세요.'));
    assert.equal(findTree(tree, node => node.props?.['aria-label'] === '몸의 두께, 선택 항목'), null);
    assert.doesNotMatch(text, /몸의 두께/);
    assert.ok(text.indexOf('셀러 사용료 규칙') < text.indexOf('증서 발급하기를 누르면 FaceMarket'));
    assert.ok(text.includes('셀러 이용료: 1회 14,900원, 월 49,900원 (월 10회)'));
    assert.ok(text.includes('결제 금액의 70%를 정산받아요.'));
    assert.ok(text.includes('증서 발급하기를 누르면 FaceMarket의 사용료 분배 규정(모델 몫 70%)에 동의하며, 초상 라이선스 계약에 서명되는 것으로 간주합니다. (필수)'));
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
    assert.equal(requests[0].documentVersion, module.CONSENT_VERSION);
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

const agreePrice = (harness) => {
  // 기존 조건 시나리오는 설정을 불러온 상태에서 시작해요. 실제 조회와 실패 차단은
  // facemarket-sponsorship-settings.test.mjs에서 효과를 실행해 검증해요.
  harness.runtime.states[17] = harness.runtime.states[1]?.modelId;
  findTree(harness.render(), node => node.type === 'input' && node.props.id === 'price-agreed').props.onChange({ target: { checked: true } });
};

test('발급 실패 뒤 조건이 남고 재시도로 완료돼요', async () => {
  let attempts = 0;
  const requests = [];
  const harness = await modelComponentHarness({ initialStates: ['3', { ...baseEnrollment, status: 'license_pending' }, module.PHOTO_REVIEW_SUB], api: {
    createLicense: async (body) => { requests.push(body); if (++attempts === 1) throw new Error('발급 서버가 응답하지 않아요'); return { id: 'license-1', modelId: 'model-1', status: 'active' }; },
  } });
  try {
    agreePrice(harness);
    await button(harness.render(), '라이선스 증서 발급하기').props.onClick();
    assert.equal(harness.runtime.states[0], '3');
    await button(harness.render(), '라이선스 증서 발급하기').props.onClick();
    assert.equal(harness.runtime.states[0], 'done');
    assert.equal(requests.length, 2); assert.deepEqual(requests[0], { enrollmentId: 'enrollment-1', allowedUse: ['일반 의류', '액티브웨어', '홈웨어·잠옷'] });
  } finally { await harness.close(); }
});

const allPhotos = () => module.SLOTS.map((slot) => ({ slot: slot.key, angle: slot.key, qcStatus: 'passed' }));
const photoRecord = () => ({ ...baseEnrollment, status: 'liveness_pending', photos: allPhotos() });

test('모든 사진을 올리면 세션이나 초상 없이 완료 요청을 보내요', async () => {
  const calls = [];
  const harness = await modelComponentHarness({ initialStates: ['2', photoRecord(), module.PHOTO_REVIEW_SUB], api: {
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
  test(`조건 발급은 기존 체형 ${bodyType || '없음'}을 수정하지 않아요`, async () => {
    const calls = [];
    const harness = await modelComponentHarness({ initialStates: ['3', { ...photoRecord(), status: 'license_pending', bodyType }, module.PHOTO_REVIEW_SUB], api: {
      submitPhysique: () => assert.fail('체형 저장을 요청하지 않아요'),
      createLicense: async body => { calls.push(body); return { id: 'license-1', status: 'pending' }; },
    } });
    try {
      agreePrice(harness);
      await button(harness.render(), '라이선스 증서 발급하기').props.onClick();
      assert.deepEqual(calls, [{ enrollmentId: 'enrollment-1', allowedUse: ['일반 의류', '액티브웨어', '홈웨어·잠옷'] }]);
      assert.equal(harness.runtime.states[0], 'done');
    } finally { await harness.close(); }
  });
}

test('확인 화면에서 역광 고치기로 사진을 교체하고 확인으로 돌아와요', async () => {
  const calls = [];
  const harness = await modelComponentHarness({ initialStates: ['2', photoRecord(), module.PHOTO_REVIEW_SUB], api: {
    uploadEnrollmentPhoto: async (body) => { calls.push(body); return { slot: body.slot, qcStatus: 'passed' }; },
  } });
  try {
    const file = new File(['photo'], 'mine.jpg', { type: 'image/jpeg' });
    findTree(harness.render(), node => node.type === 'button' && node.props['aria-label'] === `${module.PHOTO_GROUPS.at(-1).title} 사진 고치기`).props.onClick();
    assert.equal(harness.runtime.states[2], module.PHOTO_GROUPS.length, '역광은 마지막 조명 화면이다');
    let tree = harness.render();
    // 역광 3/4 는 마지막 칸이다 — 옆모습 둘·뒷모습이 그늘에 붙으면서 16번 → 18번이 됐다.
    const input = findTree(tree, (node) => node.type === 'input' && node.props['aria-label']?.startsWith('18번'));
    input.props.onChange({ target: { files: [file], value: '' } });
    await eventually(() => calls.length === 1); await flush();
    assert.equal(calls[0].slot, 'bl_34'); assert.equal(calls[0].fileBlob, file);
    tree = harness.render();
    assert.ok(findTree(tree, (node) => node.type === 'img' && node.props.alt === '18번 내 사진'));
    assert.ok(findTree(tree, (node) => node.type === 'label' && node.props.htmlFor === 'photo-input-bl_34' && textOf(node).trim() === '교체'));
    assert.ok(findTree(tree, (node) => node.type === 'button' && node.props.children?.[1] === '삭제'));
    await button(tree, '다음').props.onClick();
    assert.equal(harness.runtime.states[2], module.PHOTO_REVIEW_SUB);
    const reviewedPhoto = findTree(harness.render(), node => node.type === 'img' && node.props.alt === `18번 ${module.SLOTS.at(-1).title}`);
    assert.ok(reviewedPhoto);
    assert.equal(reviewedPhoto.props.src, harness.runtime.states[11].bl_34);
    Object.values(harness.runtime.states[11]).forEach(URL.revokeObjectURL);
  } finally { await harness.close(); }
});

test('사진 삭제가 실패하면 기존 사진과 완료 판정을 유지해요', async () => {
  const harness = await modelComponentHarness({ initialStates: ['2', photoRecord(), 1], api: { deleteEnrollmentPhoto: async () => { throw new Error('삭제 실패'); } } });
  try {
    await findTree(harness.render(), (node) => node.type === 'button' && node.props['aria-label'] === '1번 사진 삭제').props.onClick();
    assert.equal(harness.runtime.states[1].photos.length, module.SLOTS.length); assert.equal(harness.runtime.states[3], '삭제 실패');
  } finally { await harness.close(); }
});

test('사진 삭제가 성공하면 다음 버튼이 잠기고 사진 개수가 줄어요', async () => {
  const calls = [];
  const harness = await modelComponentHarness({ initialStates: ['2', photoRecord(), 1], api: { deleteEnrollmentPhoto: async (...args) => calls.push(args) } });
  try {
    await findTree(harness.render(), (node) => node.type === 'button' && node.props['aria-label'] === '1번 사진 삭제').props.onClick();
    assert.deepEqual(calls, [['enrollment-1', 'sh_front']]); assert.equal(harness.runtime.states[1].photos.length, module.SLOTS.length - 1);
    assert.equal(button(harness.render(), '다음').props.disabled, true);
  } finally { await harness.close(); }
});

for (const condition of ['일반 의류', '액티브웨어', '홈웨어·잠옷']) {
  test(`조건 화면에서 ${condition}만 남기면 마지막 스위치가 잠겨요`, async () => {
    const harness = await modelComponentHarness({ initialStates: ['3', { ...photoRecord(), status: 'license_pending' }, module.PHOTO_REVIEW_SUB], api: {} });
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
  const harness = await modelComponentHarness({ initialStates: ['3', { ...photoRecord(), status: 'license_pending' }, module.PHOTO_REVIEW_SUB], api: {} });
  try {
    assert.equal(button(harness.render(), '1년'), null);
    assert.equal(button(harness.render(), '2년'), null);
    assert.equal(button(harness.render(), '영구'), null);
    assert.equal(button(harness.render(), '라이선스 증서 발급하기').props.disabled, true);
    agreePrice(harness);
    assert.equal(button(harness.render(), '라이선스 증서 발급하기').props.disabled, false);
    button(harness.render(), '이전').props.onClick(); assert.equal(harness.runtime.states[0], '2');
    assert.equal(harness.runtime.states[2], module.PHOTO_REVIEW_SUB);
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
      const pending = button(tree, '새로 등록하기').props.onClick(); cleanup();
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
    await button(harness.render(), '새로 등록하기').props.onClick();
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
  const harness = await modelComponentHarness({ initialStates: ['2', photoRecord(), module.PHOTO_REVIEW_SUB], api: {
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

for (const screen of ['loading','1','2','3','done','processing','poll_error','failed']) {
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
    assert.ok(findTree(harness.render(),n=>n.type==='p'&&n.props.children==='발급한 라이선스 증서는 모델님이 철회하기 전까지 유효해요.'));
    assert.ok(findTree(harness.render(),n=>n.type==='Link'&&n.props.to==='/status'&&n.props.children==='마이페이지로'));
  } finally {await harness.close();}
});

test('새 등록을 시작하면 이전 등록의 사진 미리보기를 해제해요', async () => {
  const harness=await modelComponentHarness({initialStates:['done',{modelId:'model-1'}],api:{listMyModels:async()=>[]}});
  const revoke=URL.revokeObjectURL;const released=[];URL.revokeObjectURL=(value)=>released.push(value);
  try {
    harness.render();harness.runtime.refs[2].current={face01:'blob:previous'};harness.runtime.states[11]={face01:'blob:previous'};
    await button(harness.render(),'새로 등록하기').props.onClick();
    assert.deepEqual(released,['blob:previous']);assert.deepEqual(harness.runtime.states[11],{});
  } finally {URL.revokeObjectURL=revoke;await harness.close();}
});


test('이전 버전 등록을 이어갈 때 사진보다 새 필수 동의를 먼저 받아요', async () => {
  const oldRecord={...photoRecord(),consentDocumentVersion:'2026-08-v2',termsConsentVersion:null,overseasConsentVersion:null};
  const harness=await modelComponentHarness({initialStates:[],honorHookDependencies:true,api:{getCurrentEnrollment:async()=>oldRecord}});
  try {commit(harness);await flush();assert.equal(harness.runtime.states[0],'1');assert.deepEqual(harness.runtime.states[5],[false,false]);}
  finally {await harness.close();}
});

test('발급 대기 등록을 복원하면 재발급 없이 완료 화면으로 가요', async () => {
  const calls=[];
  const record={...photoRecord(),status:'vc_pending',licenseId:'license-1',licenseTerms:{allowedUse:['액티브웨어'],validDays:730}};
  const harness=await modelComponentHarness({initialStates:[],honorHookDependencies:true,api:{getCurrentEnrollment:async()=>record,createLicense:async body=>{calls.push(body);return {id:'license-1',status:'active',modelId:'model-1'};}}});
  try {
    commit(harness);await flush();assert.equal(harness.runtime.states[0],'done');
    commit(harness);await flush();assert.equal(harness.runtime.states[0],'done');
    assert.deepEqual(calls,[]);
  } finally {await harness.close();}
});

test('사진 완료를 두 번 눌러도 라이브니스 세션은 한 번만 만들어요',async()=>{
  let calls=0,resolve;
  const harness=await modelComponentHarness({initialStates:['2',photoRecord(),module.PHOTO_REVIEW_SUB],api:{getFacemarketConfig:async()=>({livenessRequired:true}),createLivenessSession:()=>{calls++;return new Promise(done=>resolve=done);}}});
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

test('문구를 바꿔도 슬롯 키와 순서는 그대로다', () => {
  // 2026-09-16 조명 용어를 뺀 문구 교체 — **데이터는 한 글자도 안 바뀐다**. 키가 하나라도
  // 달라지면 업로드가 invalid_slot 으로 막히고, 순서가 달라지면 촬영 안내 번호와 파일 이름
  // (<조명>__<컷>)이 어긋난다. 그래서 목록을 통째로 못박는다.
  assert.deepEqual(module.SLOTS.map((slot) => slot.key), [
    'sh_front', 'sh_smile', 'sh_34', 'sh_front2', 'sh_gaze_left', 'sh_gaze_right',
    'sh_side', 'sh_side_right', 'sh_back',
    'sl_front', 'sl_smile', 'sl_34',
    'sr_front', 'sr_smile', 'sr_34',
    'bl_front', 'bl_smile', 'bl_34',
  ]);
  assert.deepEqual(module.SLOTS.map((slot) => slot.n), Array.from({ length: 18 }, (_v, i) => i + 1));
  assert.deepEqual(module.PHOTO_GROUPS.map((group) => group.id), ['sh', 'sl', 'sr', 'bl']);
  // 묶음별 장수도 그대로 — 그늘 9 + 나머지 3씩.
  assert.deepEqual(
    module.PHOTO_GROUPS.map((group) => module.SLOTS.filter((slot) => slot.group === group.id).length),
    [9, 3, 3, 3],
  );
  // 18칸 전부 얼굴·어깨까지다(전신·반신을 받지 않는다).
  assert.ok(module.SLOTS.every((slot) => slot.framing === 'face'));
});

test('등록 촬영 안내는 그늘부터 역광까지 네 자리를 짧게 설명한다', () => {
  assert.deepEqual(module.PHOTO_GROUPS.map((group) => group.title),
    ['그늘', '햇빛', '90도 회전', '한 번 더 회전']);
  assert.match(module.PHOTO_GROUPS[0].note, /1~9번/);
  assert.match(module.PHOTO_GROUPS[2].action, /90도/);
  assert.match(module.PHOTO_GROUPS[3].badge, /해를 등지고/);
  assert.deepEqual(module.SHOOT_RULES.map((rule) => rule.title),
    ['안경 모자 벗기', '혼자 나오기', '후면카메라 촬영', '같은 날 찍기']);
});

test('서버의 조명 이름은 그대로다 — 학습 캡션·파일명이 거기서 나온다', () => {
  const photos = readFileSync(
    new URL('../../server/app/facemarket_photos.py', import.meta.url), 'utf8',
  );
  for (const word of ['그늘', '해가왼쪽', '해가오른쪽', '해등지고']) {
    assert.ok(photos.includes(word), `서버에서 "${word}" 가 사라졌다 — 내보내기 파일명이 바뀐다`);
  }
});

/* 칸 그림 (2026-09-16) — RegisterIllustration 은 각도만 구분해서 정면 네 칸이 똑같이 보였고,
   'back' 을 몰라 9번 뒷모습 칸에 앞모습이 나왔다. SlotDiagram 이 그 아홉을 갈라 그린다. */

test('18칸 전부 cut 이 있고 서버 CUT_LABELS 키와 같다', () => {
  // cut 이 없으면 그림이 기본값(정면)으로 떨어져 아홉 칸이 다시 한 그림이 된다. 이름이
  // 서버와 갈라지면 관리자 화면·내보내기 파일명과 부르는 이름이 달라진다.
  const photos = readFileSync(
    new URL('../../server/app/facemarket_photos.py', import.meta.url), 'utf8',
  );
  const block = /CUT_LABELS: dict\[str, tuple\[str, str\]\] = \{([\s\S]*?)\n\}/.exec(photos)?.[1];
  assert.ok(block, '서버 CUT_LABELS 를 못 찾았다');
  const keys = [...block.matchAll(/^\s+"([a-z0-9_]+)":/gm)].map((m) => m[1]);
  assert.equal(keys.length, 9);
  for (const slot of module.SLOTS) {
    assert.ok(keys.includes(slot.cut), `${slot.key} 의 cut(${slot.cut}) 이 서버에 없다`);
    // 슬롯 키에서 조명 접두어를 뗀 것과 같아야 한다 — 서버 cut_of() 가 그렇게 자른다.
    assert.equal(slot.cut, slot.key.slice(slot.group.length + 1));
  }
  assert.deepEqual([...new Set(module.SLOTS.map((slot) => slot.cut))].sort(), [...keys].sort());
  // 기존 호출부가 쓰는 framing과 angle 값은 유지해요.
  assert.ok(module.SLOTS.every((slot) => slot.framing && slot.angle));
});

test('SlotDiagram 이 아홉 컷을 서로 다르게 그린다', async () => {
  const h = await modelComponentHarness({
    initialStates: [], api: {},
    entry: '/src/features/model/SlotDiagram.jsx', exportName: 'SlotDiagram',
  });
  // 함수 컴포넌트를 펼쳐야 진짜 도형이 나온다(FaceFront/HeadTop 은 안이 다르다).
  const expand = (node) => !node || typeof node !== 'object' ? node : Array.isArray(node) ? node.map(expand)
    : typeof node.type === 'function' ? expand(node.type(node.props))
      : { ...node, props: { ...node.props, children: expand(node.props?.children) } };
  try {
    const cuts = ['front', 'front2', 'smile', 'gaze_left', 'gaze_right', '34', 'side', 'side_right', 'back'];
    const drawn = new Map();
    for (const cut of cuts) {
      const markup = JSON.stringify(expand(h.render({ cut })));
      const twin = [...drawn].find(([, value]) => value === markup)?.[0];
      assert.ok(!twin, `${cut} 과 ${twin} 이 같은 그림이다`);
      drawn.set(cut, markup);
      assert.match(markup, /"svg"/);
      // 사진 파일은 쓰지 않는다 — 도형과 currentColor 뿐이다.
      assert.ok(!markup.includes('<img'), `${cut} 이 사진을 쓴다`);
    }
    // 모르는 값이 와도 빈 화면은 안 나온다(옛 사진이 cut 없이 복원될 수 있다).
    assert.ok(JSON.stringify(expand(h.render({}))).includes('svg'));
  } finally { await h.close(); }
});

test('촬영 화면과 재촬영 화면은 같은 클레이 예시를 쓴다', () => {
  const screens = readFileSync(
    new URL('../../src/features/model/RegisterScreens.jsx', import.meta.url), 'utf8',
  );
  assert.equal(screens.split('<PhotoPoseIllustration').length - 1, 1);
  assert.match(screens, /slot=\{slot\}/);
  assert.match(screens, /renderPhotoCard\(\{ slot/);
  assert.match(screens, /<ShootChecklist/);
  const art = readFileSync(
    new URL('../../src/features/model/PhotoPoseIllustration.jsx', import.meta.url), 'utf8',
  );
  assert.match(art, /PHOTO_GUIDE_ASSETS/);
  assert.match(art, /data-photo-example/);
});
