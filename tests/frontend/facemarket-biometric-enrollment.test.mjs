import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { registerHooks } from 'node:module';

// Plain `node --test` (no bundler) doesn't understand `.svg` specifiers the way Vite does.
// biometricEnrollment.js imports the pose SVGs as real Vite assets (so they resolve in a
// production build) — register an in-thread hook so this file can still exercise that module
// directly, mirroring Vite's own behavior (an asset import resolves to its URL string). Must
// load via dynamic import: static imports resolve the whole graph before any top-level code
// (including this registerHooks() call) executes.
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.endsWith('.svg')) {
      return { url: new URL(specifier, context.parentURL).href, shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url.endsWith('.svg')) {
      return { format: 'module', shortCircuit: true, source: `export default ${JSON.stringify(url)};` };
    }
    return nextLoad(url, context);
  },
});

const {
  ENROLLMENT_ANGLES,
  ENROLLMENT_STEPS,
  buildRegistrationCompletion,
  enrollmentReasonMessage,
  initialRegistrationStep,
  nextEnrollmentStep,
} = await import('../../src/features/model/biometricEnrollment.js');

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

const collectText = (node) => Array.isArray(node) ? node.map(collectText).join('') : node && typeof node === 'object' ? collectText(node.props?.children) : String(node ?? '');

const flush = () => new Promise((resolve) => setImmediate(resolve));

test('a revoked license owner can reach fresh enrollment from the license list', async () => {
  const destinations = [];
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelLicense.jsx',
    exportName: 'ModelLicense',
    initialStates: ['ready', 'cards', null, [], null],
    api: {},
  });
  try {
    harness.runtime.navigate = (to) => destinations.push(to);
    const tree = harness.render();
    const restart = findTree(tree, (node) => node.type === 'Button'
      && node.props.children === '새 생체 등록으로 라이선스 발급');
    assert.ok(restart, 'an empty list after revocation must offer a fresh enrollment entry');
    restart.props.onClick();
    assert.deepEqual(destinations, ['/model/register']);
  } finally {
    await harness.close();
  }
});

import { eventually, findTree, modelComponentHarness } from './helpers/facemarketHarness.mjs';
test('license toggles keep one allowed category and submit without price or expiry inputs', async () => {
  const requests = [];
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelLicense.jsx', exportName: 'ModelLicense',
    initialStates: ['ready', 'flow', { id: 'enrollment-1', status: 'license_pending' }, [], null],
    api: { createLicense: async (body) => { requests.push(body); return { id: 'license-1' }; } },
  });
  try {
    const terms = findTree(harness.render(), (node) => node.type?.name === 'TermsStep');
    assert.ok(terms);
    harness.runtime.states = [];
    const render = () => {
      harness.runtime.stateCursor = 0;
      return terms.type({ ...terms.props, onIssued: () => {} });
    };
    const toggle = (tree, label) => findTree(tree, (node) => node.type === 'Toggle' && node.props.label === label);
    const submit = (tree) => findTree(tree, (node) => node.type === 'Button' && node.props.children === '라이선스 발급');
    let tree = render();
    assert.equal(findTree(tree, (node) => node.type === 'Field' && node.props.type === 'number'), null);
    assert.equal(findTree(tree, (node) => node.type === 'Chips'), null);
    const priceText = collectText(tree);
    for (const text of ['한 건 14,900원', '월 이용권 49,900원(10건)', '70%가 내 몫']) {
      assert.ok(priceText.includes(text), text);
    }
    for (const category of ['일반 의류', '홈웨어·잠옷']) {
      assert.equal(toggle(tree, category).props.on, true);
      toggle(tree, category).props.onChange(false);
      tree = render();
      assert.equal(toggle(tree, category).props.on, false);
    }
    toggle(tree, '액티브웨어').props.onChange(false);
    tree = render();
    assert.equal(toggle(tree, '액티브웨어').props.on, true, '마지막 허용 품목은 끌 수 없어요');
    assert.equal(submit(tree).props.disabled, false);
    await submit(tree).props.onClick();
    assert.deepEqual(requests, [{ enrollmentId: 'enrollment-1', allowedUse: ['액티브웨어'] }]);
  } finally { await harness.close(); }
});

test('reissuing from completed registration requires two fresh consents before a new identity enrollment', async () => {
  const created = [];
  let restores = 0;
  const harness = await modelComponentHarness({
    initialStates: ['loading', null, 1, '', false, [true, true]],
    honorHookDependencies: true,
    api: {
      getCurrentEnrollment: async () => {
        restores += 1;
        throw Object.assign(new Error('no current enrollment'), { status: 404 });
      },
      listMyModels: async () => [{ id: 'model-1', status: 'verified' }],
      listLicenses: async () => [{ id: 'l1', modelId: 'model-1', status: 'active', allowedUse: ['일반 의류'] }],
      createEnrollment: async (body) => {
        created.push(body);
        return { id: 'new-enrollment', modelId: 'model-1', status: 'identity_pending', photos: [] };
      },
      runIdentityWidget: async () => 'identity-token',
      createIdentity: async (id, body) => {
        assert.equal(id, 'new-enrollment');
        assert.deepEqual(body, { token: 'identity-token' });
        return { id, modelId: 'model-1', status: 'photos_pending', photos: [] };
      },
    },
  });
  const commit = () => {
    const tree = harness.render();
    harness.runtime.effects.forEach((effect) => effect());
    return tree;
  };
  try {
    commit();
    await eventually(() => harness.runtime.states[0] === 'done', 'active license restores completed registration');
    let tree = commit();
    assert.equal(created.length, 0, 'visiting registration must never create a new enrollment');
    const restart = findTree(tree, (node) => node.type === 'button' && node.props.children === '새 생체 등록 시작');
    await restart.props.onClick();
    tree = commit();
    await flush();
    assert.equal(restores, 1, 'restore must not undo the explicit restart');
    assert.equal(harness.runtime.states[0], '1');
    assert.deepEqual(harness.runtime.states[5], [false, false]);
    const submit = () => findTree(tree, (node) => node.type === 'button' && node.props.children === '동의하고 신분증 인증하기');
    assert.equal(submit().props.disabled, true);
    assert.equal(created.length, 0);
    for (let i = 0; i < 2; i++) {
      findTree(tree, (node) => node.props?.id === `consent-${i}`).props.onChange({ target: { checked: true } });
      tree = commit();
      assert.equal(submit().props.disabled, i < 1);
    }
    await submit().props.onClick();
    assert.equal(created.length, 1);
    assert.equal(created[0].documentVersion, '2026-09-v1');
    assert.equal(created[0].enrollmentId, undefined);
    assert.equal(harness.runtime.states[0], '2');
    assert.equal(harness.runtime.states[1].id, 'new-enrollment');
  } finally { await harness.close(); }
});


test('retouched photos are presented front, 45 degrees, then side', () => {
  assert.deepEqual(ENROLLMENT_ANGLES.map(({ value }) => value), [
    'front', 'angle45', 'side',
  ]);
  assert.match(ENROLLMENT_ANGLES[1].label, /45도/);
  assert.match(ENROLLMENT_ANGLES[1].guide, /45도|반측면|두 눈/);
  assert.match(ENROLLMENT_ANGLES[2].label, /측면/);
  assert.match(ENROLLMENT_ANGLES[2].guide, /90도|옆모습|한쪽/);
});

test('server status restores the next safe enrollment step', () => {
  assert.deepEqual(ENROLLMENT_STEPS, [
    'consent', 'identity', 'photos', 'physique', 'profile', 'liveness', 'processing', 'terms', 'done',
  ]);
  assert.equal(nextEnrollmentStep(null), 'consent');
  assert.equal(nextEnrollmentStep({ status: 'photos_pending', photos: [] }), 'photos');
  assert.equal(nextEnrollmentStep({ status: 'liveness_pending', photos: [{}, {}, {}] }), 'liveness');
  assert.equal(nextEnrollmentStep({ status: 'processing', photos: [{}, {}, {}] }), 'processing');
  assert.equal(nextEnrollmentStep({ status: 'asset_building', photos: [{}, {}, {}] }), 'processing');
  assert.equal(nextEnrollmentStep({ status: 'license_pending', photos: [{}, {}, {}] }), 'terms');
  assert.equal(nextEnrollmentStep({ status: 'vc_pending', photos: [{}, {}, {}] }), 'terms');
  assert.equal(nextEnrollmentStep({ status: 'passed', photos: [{}, {}, {}] }), 'done');
  assert.equal(nextEnrollmentStep({ status: 'failed', reason: 'face_match_failed' }), 'failed');
});

test('완료 전달값은 최소 체형 정보만 남기고 완료 화면을 먼저 선택한다', () => {
  assert.equal(typeof buildRegistrationCompletion, 'function');
  assert.equal(typeof initialRegistrationStep, 'function');
  const handoff = buildRegistrationCompletion(
    {
      id: 'e1', modelId: 'm1', gender: 'female', heightBucket: 'f_160_165',
      bodyType: 'slim_upper', photos: [{ angle: 'front' }], reason: 'private',
    },
    { id: 'l1', modelId: 'm1', unitPrice: 10_000, faceImageDigest: 'secret-digest' },
  );
  assert.deepEqual(handoff, {
    modelId: 'm1', gender: 'female', heightBucket: 'f_160_165', bodyType: 'slim_upper',
  });
  assert.equal(initialRegistrationStep(handoff), 'done');
  assert.equal(initialRegistrationStep(null), 'loading');
});

test('raw biometric reasons collapse to actionable copy', () => {
  assert.equal(enrollmentReasonMessage('id_portrait_unavailable'), '신분증 사진을 확인할 수 없어요.');
  assert.equal(enrollmentReasonMessage('face_match_failed'), '얼굴 일치 확인에 실패했어요.');
  assert.equal(enrollmentReasonMessage('unknown-provider-detail'), '인증을 완료하지 못했어요. 다시 시도해 주세요.');
});

test('ENROLLMENT_STEPS puts identity right after consent, before photos', () => {
  const i = ENROLLMENT_STEPS.indexOf('identity');
  assert.equal(ENROLLMENT_STEPS[0], 'consent');
  assert.equal(ENROLLMENT_STEPS[1], 'identity');
  assert.ok(i < ENROLLMENT_STEPS.indexOf('photos'));
  assert.ok(ENROLLMENT_STEPS.indexOf('profile') > ENROLLMENT_STEPS.indexOf('photos'));
});

test('nextEnrollmentStep maps identity_pending to identity', () => {
  assert.equal(nextEnrollmentStep({ status: 'identity_pending' }), 'identity');
});

test('ENROLLMENT_ANGLES carry pose example images', () => {
  for (const a of ENROLLMENT_ANGLES) assert.ok(a.exampleImage, a.value);
});

test('createIdentity posts token to enrollment-scoped identity route', () => {
  const apiSrc = read('../../src/lib/api/facemarket.js');
  assert.match(apiSrc, /createIdentity\(\s*enrollmentId\s*,\s*\{\s*token\s*\}\s*\)/);
  assert.match(apiSrc, /enrollments\/\$\{encodeURIComponent\(enrollmentId\)\}\/identity/);
});

test('uploadProfileImage mirrors multipart pattern', () => {
  const apiSrc = read('../../src/lib/api/facemarket.js');
  assert.match(apiSrc, /uploadProfileImage\(\{\s*enrollmentId,\s*fileBlob,\s*filename\s*\}\)/);
  assert.match(apiSrc, /profile-image/);
});

test('portrait is held in a ref, never stored/logged', () => {
  const reg = read('../../src/features/model/ModelRegister.jsx');
  assert.match(reg, /useRef\(/);
  assert.doesNotMatch(reg, /localStorage\.setItem\([^)]*dlphoto/i);
});

test('ModelHub reaches ready using FaceMarket state when personalization is unavailable', async () => {
  let personalizationCalls = 0;
  let modelCalls = 0;
  let enrollmentCalls = 0;
  const routeMissing = Object.assign(new Error('personalization route disabled'), { status: 404 });
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelHub.jsx',
    exportName: 'ModelHub',
    initialStates: ['loading', null, null, null],
    api: {
      getStatus: async () => { personalizationCalls += 1; throw routeMissing; },
      listMyModels: async () => {
        modelCalls += 1;
        return [{ id: 'model-1', status: 'verified' }];
      },
      getCurrentEnrollment: async () => {
        enrollmentCalls += 1;
        throw Object.assign(new Error('no active enrollment'), { status: 404 });
      },
    },
  });
  try {
    harness.render();
    harness.runtime.effects[0]();
    await eventually(() => harness.runtime.states[0] !== 'loading', 'hub load should settle');

    assert.equal(harness.runtime.states[0], 'ready');
    assert.equal(personalizationCalls, 0, 'disabled personalization must not be queried');
    assert.equal(modelCalls, 1);
    assert.equal(enrollmentCalls, 1);
  } finally {
    await harness.close();
  }
});

test('활동 마이페이지는 라이선스가 유효할 때 사용 기록과 정산 및 라이선스 탭을 열어요', async () => {
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelHub.jsx', exportName: 'ModelHub',
    initialStates: ['ready', { id: 'm1', status: 'verified', displayName: '모델' }, null, null, true,
      [{ id: 'l1', modelId: 'm1', status: 'active', allowedUse: ['일반 의류'] }]], api: {},
  });
  try {
    const page = harness.render();
    assert.equal(page.props.journey.mode, 'active');
    const active = findTree(page.type(page.props), node => node.type?.name === 'ActiveDashboard');
    assert.ok(active);
    const tree = active.type(active.props);
    assert.ok(findTree(tree, node => node.type?.name === 'MyPageUsage'));
    for (const label of ['사용된 페이지', '정산', '라이선스']) {
      assert.ok(findTree(tree, node => node.props?.role === 'tab' && node.props.children === label));
    }
    assert.ok(findTree(tree, node => node.type?.name === 'EarningsFigures'));
    assert.equal(findTree(tree, node => node.type === 'button' && node.props.children === '그만두기'), null);
    assert.equal(findTree(tree, node => node.type === 'Link' && node.props.to === '/model/withdraw'), null);
  } finally { await harness.close(); }
});

test('ModelHub treats license API failure as unavailable instead of real zero/default terms', async () => {
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelHub.jsx',
    exportName: 'ModelHub',
    initialStates: ['loading', null, null, null, true, [], []],
    api: {
      listMyModels: async () => [{ id: 'model-1', status: 'verified' }],
      getCurrentEnrollment: async () => { throw Object.assign(new Error('none'), { status: 404 }); },
      listLicenses: async () => { throw Object.assign(new Error('license unavailable'), { status: 500 }); },
      listSettlements: async () => [],
    },
  });
  try {
    harness.render();
    harness.runtime.effects[0]();
    await eventually(() => harness.runtime.states[0] !== 'loading', 'hub load should settle');
    assert.equal(harness.runtime.states[0], 'error');
  } finally {
    await harness.close();
  }
});

test('지원 상태 로더는 수익 API를 호출하지 않아요', async () => {
  let earningsCalls = 0;
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelHub.jsx', exportName: 'ModelHub', initialStates: [],
    api: {
      listMyModels: async () => [],
      getCurrentEnrollment: async () => { throw Object.assign(new Error('none'), {status:404}); },
      getCurrentApplication: async () => ({status:'under_review'}),
      getSettlementSummary: async () => { earningsCalls += 1; throw new Error('offline'); },
    },
  });
  try {
    harness.render(); harness.runtime.effects[0]();
    await eventually(() => harness.runtime.states[0] !== 'loading', '상태 조회 완료');
    assert.equal(harness.runtime.states[0], 'ready');
    assert.equal(earningsCalls, 0);
    assert.equal(harness.render().props.journey.mode, 'onboarding');
  } finally { await harness.close(); }
});

test('확정 모델도 라이선스가 없으면 활동 중이라고 표시하지 않아요', async () => {
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelHub.jsx', exportName: 'ModelHub',
    initialStates: ['ready', {id:'m1',status:'verified'}, null, null, true, []], api:{},
  });
  try {
    const tree = harness.render();
    assert.equal(tree.props.journey.mode, 'onboarding');
    assert.equal(tree.props.license, null);
  } finally { await harness.close(); }
});

test('SlotCard renders a pose example image from the angle', () => {
  const upload = read('../../src/features/model/ModelFaceUpload.jsx');
  assert.match(upload, /exampleImage|example/);
  assert.match(upload, /<img[^>]+(example|pose)/i);
});

for (const p of ['pose-front', 'pose-angle45', 'pose-side']) {
  test(`asset ${p}.svg exists`, () => {
    assert.ok(existsSync(new URL(`../../src/features/model/assets/${p}.svg`, import.meta.url)));
  });
}

// ── 업로드한 사진을 사용자에게 되보여준다 ────────────────────────────────────
// 생체등록 어댑터에는 서버 프리뷰(fetchUrl)가 없다 — 격리 사진 바이트를 내주는 라우트가
// 없기 때문이다. 그래서 방금 올린 파일로 로컬 프리뷰를 만들어 슬롯에 그린다.

function findSlot(tree, angle) {
  return findTree(tree, (node) => node?.props?.angle === angle && typeof node.type === 'function');
}

async function uploadHarness({ states, api, urls }) {
  const originals = {
    create: globalThis.URL.createObjectURL,
    revoke: globalThis.URL.revokeObjectURL,
  };
  let seq = 0;
  globalThis.URL.createObjectURL = (file) => {
    const url = `blob:${file?.name || 'file'}-${(seq += 1)}`;
    urls.created.push(url);
    return url;
  };
  globalThis.URL.revokeObjectURL = (url) => { urls.revoked.push(url); };
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelFaceUpload.jsx',
    exportName: 'ModelFaceUpload',
    stubUpload: false,
    initialStates: states,
    api,
  });
  const close = harness.close;
  harness.close = async () => {
    globalThis.URL.createObjectURL = originals.create;
    globalThis.URL.revokeObjectURL = originals.revoke;
    await close();
  };
  return harness;
}

const PASSED_PHOTO = { angle: 'front', qcStatus: 'passed', qcReasons: [], uploadedAt: '2026-08-31T00:00:00Z' };

test('an uploaded photo is shown back in its slot even without a server preview URL', async () => {
  const urls = { created: [], revoked: [] };
  const harness = await uploadHarness({
    // phase, slots, previews, slotBusy, blocked
    states: ['ready', {}, {}, {}, null],
    urls,
    api: { uploadFacePhoto: async () => PASSED_PHOTO },
  });
  try {
    const props = { embedded: true, photoApi: { load: async () => ({ photos: [] }), upload: async () => PASSED_PHOTO } };
    let tree = harness.render(props);
    const slot = findSlot(tree, 'front');
    assert.ok(slot, 'the front slot must render');
    assert.equal(slot.props.localUrl, undefined, 'nothing is shown before a photo is picked');

    slot.props.onPicked('front', { name: 'front.jpg', type: 'image/jpeg' });
    await eventually(() => urls.created.length === 1, 'the picked photo must become a preview URL');
    await flush();

    tree = harness.render(props);
    assert.equal(findSlot(tree, 'front').props.localUrl, urls.created[0],
      'the slot must show the photo the user just uploaded');
    assert.equal(findSlot(tree, 'angle45').props.localUrl, undefined,
      'the preview belongs to its own angle only');
  } finally {
    await harness.close();
  }
});

test('a photo rejected by QC is still shown so the user can see what to retake', async () => {
  const urls = { created: [], revoked: [] };
  const harness = await uploadHarness({
    states: ['ready', {}, {}, {}, null],
    urls,
    api: {},
  });
  try {
    const failure = Object.assign(new Error('얼굴이 가려져 있어요.'), { reasons: ['occlusion'] });
    const props = {
      embedded: true,
      photoApi: { load: async () => ({ photos: [] }), upload: async () => { throw failure; } },
    };
    let tree = harness.render(props);
    findSlot(tree, 'front').props.onPicked('front', { name: 'front.jpg', type: 'image/jpeg' });
    await eventually(() => urls.created.length === 1, 'a preview is made before the upload is judged');
    await flush();

    tree = harness.render(props);
    const slot = findSlot(tree, 'front');
    assert.equal(slot.props.localUrl, urls.created[0], 'the rejected photo stays visible');
    assert.equal(slot.props.slot?.lastFail?.message, '얼굴이 가려져 있어요.');
    assert.deepEqual(urls.revoked, [], 'the preview must not be revoked while it is on screen');
  } finally {
    await harness.close();
  }
});

test('replacing or deleting a photo releases the preview it was holding', async () => {
  const urls = { created: [], revoked: [] };
  const harness = await uploadHarness({
    states: ['ready', {}, {}, {}, null],
    urls,
    api: {},
  });
  const originalWindow = globalThis.window;
  globalThis.window = { ...(originalWindow || {}), confirm: () => true };
  try {
    const props = {
      embedded: true,
      photoApi: {
        load: async () => ({ photos: [] }),
        upload: async () => PASSED_PHOTO,
        remove: async () => {},
      },
    };
    let tree = harness.render(props);
    findSlot(tree, 'front').props.onPicked('front', { name: 'front.jpg', type: 'image/jpeg' });
    await eventually(() => urls.created.length === 1, 'first upload must make a preview');
    await flush();

    // 같은 각도를 다시 올리면 앞의 objectURL 은 회수돼야 한다(누수 금지).
    tree = harness.render(props);
    findSlot(tree, 'front').props.onPicked('front', { name: 'front-2.jpg', type: 'image/jpeg' });
    await eventually(() => urls.created.length === 2, 'the replacement must make its own preview');
    await flush();
    assert.deepEqual(urls.revoked, [urls.created[0]], 'the replaced preview must be revoked');

    tree = harness.render(props);
    await findSlot(tree, 'front').props.onDelete('front');
    await flush();

    tree = harness.render(props);
    assert.equal(findSlot(tree, 'front').props.localUrl, undefined, 'deleting clears the preview');
    assert.deepEqual(urls.revoked, [urls.created[0], urls.created[1]], 'the deleted preview must be revoked too');
  } finally {
    if (originalWindow === undefined) delete globalThis.window; else globalThis.window = originalWindow;
    await harness.close();
  }
});

// ── 각도 예시: 실사진 우선, 없으면 라인 일러스트 ──────────────────────────────

test('each angle offers a photo example with the pose drawing as fallback', () => {
  for (const angle of ENROLLMENT_ANGLES) {
    assert.equal(angle.examplePhoto, `/models/pose/${angle.value}.webp`,
      `${angle.value} must point at its example photo slot`);
    assert.ok(angle.exampleImage, `${angle.value} must keep the drawing as fallback`);
  }
  const upload = read('../../src/features/model/ModelFaceUpload.jsx');
  // 사진 파일이 아직 없으면 404 → onError 로 일러스트로 되돌아가야 한다(코드 수정 없이 교체).
  assert.match(upload, /onError/);
  // 예시 사진과 "내가 올린 사진"이 헷갈리면 안 된다.
  assert.match(upload, /예시/);
});

// ── 체형: 성별 분리 + 이미지 ────────────────────────────────────────────────

const {
  BODY_TYPES, bodyTypeLabel, bodyTypeOptions, bodyTypeMatrix, heightBucketLabel,
} = await import('../../src/lib/facemarketPhysique.js');

test('완료 요약은 저장된 키·복합 체형 코드를 사람말로 바꾼다', () => {
  assert.equal(typeof bodyTypeLabel, 'function');
  assert.equal(typeof heightBucketLabel, 'function');
  assert.equal(heightBucketLabel('f_160_165'), '160–165cm');
  assert.equal(bodyTypeLabel('slim'), '마름');
  assert.equal(bodyTypeLabel('slim_upper'), '마름 · 상체 볼륨');
  assert.equal(bodyTypeLabel(null), null);
});

test('body types are split by gender without inventing new server values', () => {
  const serverValues = new Set(BODY_TYPES.map((b) => b.value));
  const male = bodyTypeOptions('male');
  const female = bodyTypeOptions('female');
  assert.ok(male.length > 0 && female.length > 0);
  for (const option of [...male, ...female]) {
    assert.ok(serverValues.has(option.value), `${option.value} must exist in the server enum`);
  }
  assert.notDeepEqual(male.map((b) => b.value), female.map((b) => b.value),
    'the two lists must actually differ');
  assert.ok(female.some((b) => b.value === 'glamorous'));
  assert.ok(male.some((b) => b.value === 'bulk'));
  assert.deepEqual(bodyTypeOptions(null).map((b) => b.value), BODY_TYPES.map((b) => b.value),
    'unknown gender keeps every option — a choice must stay possible');
});

test('gendered body types carry an image path, the unknown-gender list does not', () => {
  for (const gender of ['male', 'female']) {
    for (const option of bodyTypeOptions(gender)) {
      assert.equal(option.image, `/models/physique/${gender}/${option.value}.webp`);
    }
  }
  assert.ok(bodyTypeOptions(null).every((b) => !b.image),
    'without a gender there is no image to show — text chips stay');
});

// ── 문구: 사용자 화면에서 "생체 확인" 걷어내기 ───────────────────────────────

test('등록 화면은 본인 확인 안내, 두 가지 필수 동의 링크, 국외 이전 안내 링크를 유지한다', async () => {
  const harness = await modelComponentHarness({ initialStates: ['1'], api: {} });
  try {
    const tree = harness.render();
    const text = collectText(tree);
    assert.ok(text.includes('먼저 본인인지 확인해요'));
    for (const path of ['/terms', '/privacy', '/biometric-consent', '/overseas-transfer']) {
      assert.ok(findTree(tree, (node) => node.type === 'Link' && node.props.to === path));
    }
    for (let i = 0; i < 2; i++) {
      assert.equal(findTree(tree, (node) => node.props?.id === `consent-${i}`).props.checked, false);
    }
    assert.equal(findTree(tree, (node) => node.props?.id === 'consent-2'), null, '국외 이전은 체크박스가 아니라 안내');
    {
    }
    const hubState = read('../../src/features/model/modelHubState.js');
    assert.match(hubState, /label: '모델 등록'/);
    assert.match(hubState, /label: '프로필 이미지 확정'/);
  } finally { await harness.close(); }
});

test('등록의 필수 항목 전체 동의는 법정 안내를 제외한 두 체크만 함께 선택한다', async () => {
  const harness = await modelComponentHarness({ initialStates: ['1'], api: {} });
  try {
    let tree = harness.render();
    let all = findTree(tree, (node) => node.props?.id === 'register-consent-all');
    assert.ok(all);
    assert.equal(all.props.checked, false);
    findTree(tree, (node) => node.props?.id === 'consent-0').props.onChange({ target: { checked: true } });
    tree = harness.render();
    all = findTree(tree, (node) => node.props?.id === 'register-consent-all');
    assert.equal(all.props.checked, false);
    assert.equal(all.props['aria-checked'], 'mixed');
    all.props.onChange({ target: { checked: true } });
    assert.deepEqual(harness.runtime.states[5], [true, true]);
    tree = harness.render();
    assert.equal(findTree(tree, (node) => node.props?.id === 'register-consent-all').props.checked, true);
    assert.equal(findTree(tree, (node) => node.type === 'button' && node.props.children === '동의하고 신분증 인증하기').props.disabled, false);
    assert.ok(collectText(tree).includes('두 가지 필수 항목을 모두 확인했어요'));
    findTree(tree, (node) => node.props?.id === 'register-consent-all').props.onChange({ target: { checked: false } });
    assert.deepEqual(harness.runtime.states[5], [false, false]);
  } finally { await harness.close(); }
});


// ── 체형 매트릭스(볼륨 × 실루엣) ────────────────────────────────────────────

test('the female body matrix pairs every volume with its silhouettes', () => {
  const rows = bodyTypeMatrix('female');
  assert.ok(rows, 'female must get a matrix');
  assert.deepEqual(rows.map((r) => r.value), ['delicate', 'slim', 'regular', 'plump']);
  // 통통 · 상하 볼륨은 옆 칸과 시각적으로 안 갈려 일부러 뺐다.
  assert.deepEqual(rows.at(-1).options.map((o) => o.value),
    ['plump_basic', 'plump_upper', 'plump_hip']);
  for (const row of rows) {
    for (const option of row.options) {
      assert.match(option.value, new RegExp(`^${row.value}_`));
      assert.equal(option.image, `/models/physique/female/${option.value}.webp`);
    }
  }
  assert.equal(rows.flatMap((r) => r.options).length, 15);
});

test('men keep the flat chip list — a matrix is female-only for now', () => {
  assert.equal(bodyTypeMatrix('male'), null);
  assert.equal(bodyTypeMatrix(null), null);
  assert.ok(bodyTypeOptions('male').length > 0);
});

test('every matrix photo referenced by the UI actually exists', () => {
  for (const row of bodyTypeMatrix('female')) {
    for (const option of row.options) {
      assert.ok(
        existsSync(new URL(`../../public${option.image}`, import.meta.url)),
        `${option.image} must be present`,
      );
    }
  }
});

test('조건 화면은 사용료 동의 뒤 같은 위저드에서 증서를 발급한다', async () => {
  const calls = [];
  const harness = await modelComponentHarness({
    initialStates: ['3', { id: 'e1', status: 'license_pending' }],
    api: { createLicense: async (body) => { calls.push(body); return { id: 'l1', vcId: 'vc-1', allowedUse: body.allowedUse }; } },
  });
  try {
    let tree = harness.render();
    const issue = () => findTree(tree, (node) => node.type === 'button' && node.props.children === '라이선스 증서 발급하기');
    assert.equal(issue().props.disabled, true);
    findTree(tree, (node) => node.props?.id === 'price-agreed').props.onChange({ target: { checked: true } });
    tree = harness.render();
    assert.equal(issue().props.disabled, false);
    await issue().props.onClick();
    assert.deepEqual(calls, [{ enrollmentId: 'e1', allowedUse: ['일반 의류', '액티브웨어', '홈웨어·잠옷'] }]);
    assert.equal(harness.runtime.states[0], 'done');
    assert.equal(harness.runtime.states[9].vcId, 'vc-1');
  } finally { await harness.close(); }
});

test('등록 완료 화면은 발급된 조건과 영구 유효 안내 및 마이페이지 경로를 보여 준다', async () => {
  const license = { id: 'l1', vcId: 'vc-1', allowedUse: ['일반 의류', '액티브웨어'], licenseValidUntil: null };
  const harness = await modelComponentHarness({
    initialStates: ['done', { id: 'e1', modelId: 'm1', status: 'passed' }, 1, '', false, [true, true], { allowedUse: license.allowedUse }, null, null, license],
    api: {},
  });
  try {
    const tree = harness.render();
    const text = collectText(tree);
    assert.ok(findTree(tree, (node) => node.type === 'h1' && node.props.children === '축하해요, 등록이 끝났어요'));
    assert.ok(text.includes('일반 의류, 액티브웨어에 쓸 수 있고 철회하기 전까지 유효해요.'));
    assert.ok(text.includes('증서 번호 vc-1'));
    assert.ok(findTree(tree, (node) => node.type === 'Link' && node.props.to === '/status' && node.props.children === '마이페이지로'));
    assert.ok(findTree(tree, (node) => node.type === 'Link' && node.props.to === '/model/license'));
    assert.equal(findTree(tree, (node) => node.type === 'Chips'), null);
  } finally { await harness.close(); }
});

test('라이선스 발급은 등록 완료 화면으로 체형과 발급 조건을 전달한다', () => {
  const source = read('../../src/features/model/ModelLicense.jsx');
  assert.match(
    source,
    /navigate\(\s*["']\/model\/register["'][\s\S]*completionSummary:\s*buildRegistrationCompletion\(enrollmentRecord,\s*lic\)/,
  );
});

test('등록 완료 화면은 발급 경로의 최소 체형 정보로 완료 상태를 복원한다', async () => {
  const harness = await modelComponentHarness({ initialStates: [], api: {} });
  const handoff = { modelId: 'm1', gender: 'female', heightBucket: 'f_160_165', bodyType: 'slim_upper' };
  harness.runtime.location = { state: { completionSummary: handoff } };
  try {
    const tree = harness.render();
    assert.equal(harness.runtime.states[0], 'done');
    assert.deepEqual(harness.runtime.states[1], handoff);
    assert.ok(findTree(tree, (node) => node.type === 'h1' && node.props.children === '축하해요, 등록이 끝났어요'));
    assert.ok(findTree(tree, (node) => node.type === 'Link' && node.props.to === '/model/license'));
  } finally { await harness.close(); }
});

test('등록 완료 조건이 없으면 증서 확인을 안내하고 기본 조건을 발급된 값처럼 표시하지 않는다', async () => {
  const harness = await modelComponentHarness({ initialStates: ['done', { modelId: 'm1' }], api: {} });
  try {
    const tree = harness.render();
    const text = collectText(tree);
    assert.ok(text.includes('발급한 조건은 증서에서 확인할 수 있어요.'));
    for (const value of ['14,900원', '49,900원', '철회하기 전까지 유효해요.', '증서 번호']) assert.ok(!text.includes(value), value);
    assert.ok(findTree(tree, (node) => node.type === 'Link' && node.props.to === '/model/license'));
  } finally { await harness.close(); }
});

const completeApplicationForm = {
  contactEmail: 'model@example.com', applicantName: '김하나', phone: '010-1234-5678',
  birthdate: '2000-01-01', gender: 'female', heightCm: '170', weightKg: '55',
  experienceLevel: 'beginner', agencyContracted: false, portfolioUrl: '', snsUrl: '',
};
const applicationAttestations = {
  adultAndTruthful: true, photosAreMine: true, noAgencyContract: true,
  reviewOnlyUse: true, privacyPolicy: true,
};
const applicationPhoto = { staged: true, stageId: 'a'.repeat(64) };

test('지원 완료는 새 payload를 보내고 접수 완료 화면에서 상태 보기로 이어진다', async () => {
  const navigations = [];
  const submissions = [];
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelApply.jsx', exportName: 'ModelApply',
    initialStates: ['ready', { ...completeApplicationForm, portfolioUrl: 'www.portfolio.com', snsUrl: 'www.instagram.com/example' }, { ...applicationPhoto, previewUrl: 'blob:profile' }, applicationAttestations, 3, null, false, ''],
    api: { submitApplication: async (body) => { submissions.push(body); return { id: 'a1', status: 'under_review' }; } },
  });
  harness.runtime.navigate = (...args) => navigations.push(args);
  try {
    const form = harness.render();
    const submit = findTree(form, (node) => node.type === 'button' && node.props?.children === '지원 완료');
    assert.ok(submit);
    assert.equal(submit.props.disabled, false);
    await submit.props.onClick();
    assert.equal(submissions.length, 1);
    assert.deepEqual(submissions[0], {
      contactEmail: 'model@example.com', applicantName: '김하나', phone: '010-1234-5678',
      birthdate: '2000-01-01', gender: 'female', heightCm: 170, weightKg: 55,
      experienceLevel: 'beginner', agencyContracted: false, portfolioUrl: 'https://www.portfolio.com', snsUrl: 'https://www.instagram.com/example',
      profileStageId: 'a'.repeat(64),
      attestations: applicationAttestations,
      privacyConsent: { accepted: true, documentVersion: '2026-09-v1' },
    });
    assert.equal(harness.runtime.states[0], 'complete');
    assert.deepEqual(navigations, []);
    const complete = harness.render();
    assert.ok(findTree(complete, (node) => node.props?.children === '지원서가 접수 완료됐어요'));
    assert.ok(findTree(complete, (node) => node.type === 'Link' && node.props?.to === '/status' && node.props.children === '지원 상태 보기'));
    assert.equal(findTree(complete, (node) => node.props?.children === '정부 모바일 신분증 앱'), null);
  } finally { await harness.close(); }
});

test('확인에서 고치기로 돌아가도 입력값을 유지하고 다음은 다시 확인을 연다', async () => {
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelApply.jsx', exportName: 'ModelApply',
    initialStates: ['ready', completeApplicationForm, applicationPhoto, applicationAttestations, 3, null, false, ''], api: {},
  });
  try {
    let tree = harness.render();
    findTree(tree, (node) => node.type === 'button' && node.props['aria-label'] === '기본 정보 고치기').props.onClick();
    tree = harness.render();
    assert.ok(findTree(tree, (node) => node.type?.name === 'FormInput' && node.props.value === '김하나'));
    const email = findTree(tree, (node) => node.type?.name === 'FormInput' && node.props.type === 'email');
    assert.ok(!email.props.readOnly);
    email.props.onChange({ target: { value: 'contact@example.com' } });
    tree = harness.render();
    findTree(tree, (node) => node.type === 'button' && node.props.children === '다음').props.onClick();
    assert.equal(harness.runtime.states[4], 3);
    assert.deepEqual(harness.runtime.states[1], { ...completeApplicationForm, contactEmail: 'contact@example.com' });
    assert.ok(findTree(harness.render(), (node) => node.type === 'dd' && node.props.children === 'contact@example.com'));
  } finally { await harness.close(); }
});

test('체크사항 하나가 비어 있으면 지원 완료가 잠기고 미동의 개수를 안내한다', async () => {
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelApply.jsx', exportName: 'ModelApply',
    initialStates: ['ready', completeApplicationForm, applicationPhoto, { ...applicationAttestations, privacyPolicy: false }, 3, null, false, ''], api: {},
  });
  try {
    const tree = harness.render();
    assert.equal(findTree(tree, (node) => node.type === 'button' && node.props.children === '지원 완료').props.disabled, true);
    assert.ok(findTree(tree, (node) => node.props.children === '아직 표시하지 않은 체크사항이 1개 있어요.'));
    assert.ok(findTree(tree, (node) => node.type === 'a' && node.props.href === '/privacy' && node.props.target === '_blank'));
  } finally { await harness.close(); }
});



// ── 대표 이미지: 올리기 전에 확인, 그리고 되돌아가기 ──────────────────────────
// 예전엔 파일을 고르는 순간 업로드하고 다음 단계로 넘어가버려, 잘못 고르면 되돌릴 방법이
// 아예 없었다(위저드에 뒤로가기가 하나도 없었다).

test('registration entry directs an awaiting model to confirmation', async () => {
  const destinations = [];
  let created = 0;
  const harness = await modelComponentHarness({
    initialStates: ['loading', null, null, '', false, false, true],
    honorHookDependencies: true,
    api: {
      listMyModels: async () => [{ id: 'm1', status: 'awaiting_confirm' }],
      getCurrentEnrollment: async () => { throw Object.assign(new Error('none'), { status: 404 }); },
      createEnrollment: async () => { created += 1; },
    },
  });
  try {
    harness.runtime.navigate = (to) => destinations.push(to);
    harness.render();
    harness.runtime.effects.forEach((effect) => effect());
    await flush();
    assert.deepEqual(destinations, ['/model/confirm']);
    assert.equal(created, 0);
    assert.equal(findTree(harness.render(), (node) => node.props?.children === '동의하고 본인 확인 시작'), null);
  } finally { await harness.close(); }
});

test('completion handoff also redirects when cuts have arrived since issuance', async () => {
  const destinations = [];
  const harness = await modelComponentHarness({
    initialStates: ['done', { modelId: 'm1', status: 'passed' }, null, '', false, false, true,
      null, null, null, [], null, { phase: 'error', model: null, summary: null }],
    honorHookDependencies: true,
    api: {
      listMyModels: async () => [{ id: 'm1', status: 'awaiting_confirm' }],
      listLicenses: async () => [{ modelId: 'm1', status: 'active' }],
    },
  });
  try {
    harness.runtime.location = { state: { completionSummary: { modelId: 'm1' } } };
    harness.runtime.navigate = (to) => destinations.push(to);
    harness.render();
    harness.runtime.effects.forEach((effect) => effect());
    await flush();
    assert.deepEqual(destinations, ['/model/confirm']);
  } finally { await harness.close(); }
});


test('라이선스 종료는 활동 관리의 확인 뒤 현재 라이선스를 철회하고 화면 상태를 갱신해요', async () => {
  const requests = [], updates = [];
  const harness = await modelComponentHarness({
    entry:'/src/features/model/mypage/MyPageActivity.jsx', exportName:'MyPageActivity', initialStates:[],
    api:{ revokeLicense:async id=>{requests.push(id);return {id,modelId:'m1',status:'revoked'};} },
  });
  let dialogName = 'manage';
  const props={journey:{mode:'active',flag:'none'},model:{id:'m1',status:'verified'},license:{id:'l1',status:'active'},onDialogChange:value=>{dialogName=value;},onLicenseChange:value=>updates.push(value)};
  try {
    let tree=harness.render({...props,dialog:dialogName});
    findTree(tree,node=>node.type==='button' && findTree(node,child=>child.type==='strong'&&child.props.children==='라이선스 종료')).props.onClick();
    assert.deepEqual(requests,[]);
    tree=harness.render({...props,dialog:dialogName});
    const dialog=findTree(tree,node=>node.type?.name==='MyPageDialog');
    assert.equal(dialog.props.title,'라이선스 종료');
    await findTree(dialog,node=>node.type==='button'&&node.props.children==='라이선스 종료하기').props.onClick();
    assert.deepEqual(requests,['l1']);
    assert.equal(updates[0].status,'revoked');
    assert.equal(dialogName,null);
  } finally {await harness.close();}
});

test('영구 라이선스는 공개 증서에서 철회 시까지로 표시해요', async () => {
  const harness=await modelComponentHarness({entry:'/src/features/verify/PublicVerify.jsx',exportName:'PublicVerify',initialStates:['ok',{status:'active',valid:true,validUntil:null},null],api:{}});
  try { assert.ok(findTree(harness.render(), node=>node.type==='dd'&&node.props.children==='철회 시까지')); }
  finally {await harness.close();}
});

test('지원 기본 정보는 유효한 생일과 전화번호를 요구하고 전화번호를 자동 정리한다', async () => {
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelApply.jsx', exportName: 'ModelApply',
    initialStates: ['ready', { ...completeApplicationForm, birthdate: '2099-01-01', phone: '' }, null, {}, 1, null, false, ''], api: {},
  });
  try {
    let tree = harness.render();
    assert.equal(findTree(tree, (node) => node.type === 'button' && node.props.children === '다음').props.disabled, true);
    findTree(tree, (node) => node.type?.name === 'FormInput' && node.props.type === 'tel').props.onChange({ target: { value: '010abc12345678' } });
    assert.equal(harness.runtime.states[1].phone, '010-1234-5678');
    findTree(tree, (node) => node.type?.name === 'BirthdateInput').props.onChange('2000-01-01');
    tree = harness.render();
    const next = findTree(tree, (node) => node.type === 'button' && node.props.children === '다음');
    assert.equal(next.props.disabled, false);
    next.props.onClick();
    assert.equal(harness.runtime.states[4], 2);
  } finally { await harness.close(); }
});

test('지원 프로필은 사진 업로드와 에이전시 답을 요구하며 선택 값은 비워둘 수 있다', async () => {
  const uploaded = [];
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelApply.jsx', exportName: 'ModelApply',
    initialStates: ['ready', { ...completeApplicationForm, weightKg: '', experienceLevel: '', agencyContracted: null }, null, {}, 2, null, false, ''],
    api: { stageApplicationPhoto: async (body) => { uploaded.push(body); return { staged: true, stageId: 'a'.repeat(64) }; } },
  });
  let preview;
  try {
    let tree = harness.render();
    assert.equal(findTree(tree, (node) => node.type === 'button' && node.props.children === '다음').props.disabled, true);
    const file = Object.assign(new Blob(['profile'], { type: 'image/jpeg' }), { name: 'profile.jpg' });
    await findTree(tree, (node) => node.type?.name === 'ImageUpload').props.onSelect(file);
    assert.equal(uploaded[0].kind, 'profile');
    assert.equal(uploaded[0].fileBlob, file);
    preview = harness.runtime.states[2].previewUrl;
    tree = harness.render();
    assert.equal(findTree(tree, (node) => node.type?.name === 'ImageUpload').props.previewUrl, preview);
    assert.equal(findTree(tree, (node) => node.type === 'button' && node.props.children === '다음').props.disabled, true);
    findTree(tree, (node) => node.type === 'button' && node.props.children === '아니오').props.onClick();
    tree = harness.render();
    const next = findTree(tree, (node) => node.type === 'button' && node.props.children === '다음');
    assert.equal(next.props.disabled, false);
    next.props.onClick();
    assert.equal(harness.runtime.states[4], 3);
  } finally { if (preview) URL.revokeObjectURL(preview); await harness.close(); }
});

test('재지원은 이전 입력만 복원하고 사진과 다섯 체크는 다시 받는다', async () => {
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelApply.jsx', exportName: 'ModelApply', initialStates: [],
    api: { getCurrentApplication: async () => ({ ...completeApplicationForm, contactEmail: 'previous@example.com', status: 'rejected', hasProfileImage: true, photoKinds: ['profile'], attestations: applicationAttestations }) },
  });
  try {
    harness.render();
    harness.runtime.effects.forEach((effect) => effect());
    await eventually(() => harness.runtime.states[0] === 'ready', 'the rejected form loads');
    assert.equal(harness.runtime.states[1].applicantName, '김하나');
    assert.equal(harness.runtime.states[1].contactEmail, 'previous@example.com');
    assert.equal(harness.runtime.states[2], null);
    assert.deepEqual(Object.values(harness.runtime.states[3]), [false, false, false, false, false]);
  } finally { await harness.close(); }
});

test('공개 지원 시작 화면은 지원서 링크와 가격, 키보드 안내 말풍선을 렌더한다', async () => {
  const harness = await modelComponentHarness({
    entry: '/src/features/facemarket-landing/pages/ApplyStartPage.jsx', exportName: 'ApplyStartPage', initialStates: [], api: {},
  });
  try {
    const shell = harness.render();
    const tree = shell.props.children();
    assert.ok(findTree(tree, (node) => node.type === 'Link' && node.props.to === '/model/apply' && node.props.children === '지원서 쓰기'));
    assert.ok(findTree(tree, (node) => node.type === 'b' && node.props.children === '14,900원'));
    assert.ok(findTree(tree, (node) => node.type === 'b' && node.props.children === '49,900원'));
    assert.ok(findTree(tree, (node) => node.props.tabIndex === 0 && node.props['aria-describedby'] === 'apply-settlement-tooltip'));
    assert.ok(findTree(tree, (node) => node.type === 'summary' && node.props.children === 'FaceMarket에서 모델은 무슨 일을 하나요?'));
    assert.equal(findTree(tree, (node) => node.type === 'summary' && node.props.children === '제 얼굴이 확실히 지켜지는 건가요?'), null);
  } finally { await harness.close(); }
});

test('지원 시작 상단바는 중복 지원 CTA 대신 로그인 버튼을 제공한다', async () => {
  const harness = await modelComponentHarness({
    entry: '/src/features/facemarket-landing/pages/ApplyStartPage.jsx', exportName: 'ApplyStartPage',
    initialStates: [{ label: '얼리버드 지원하기', to: '/apply' }, 'anonymous', true, false], api: {},
  });
  const logins = [];
  harness.runtime.session = null;
  harness.runtime.location = { pathname: '/apply', search: '' };
  harness.runtime.openLogin = (path) => logins.push(path);
  try {
    const start = harness.render();
    const shell = start.type(start.props);
    const header = findTree(shell, (node) => node.type?.name === 'LandingHeader');
    assert.equal(header.props.primaryLabel, null);
    const tree = header.type(header.props);
    const login = findTree(tree, (node) => node.type === 'button' && node.props.children === '로그인');
    assert.ok(login);
    login.props.onClick();
    assert.deepEqual(logins, ['/apply']);
  } finally { await harness.close(); }
});

for (const accountEmail of [null, 'google@example.com']) {
  test(`지원서 연락 이메일은 계정 이메일(${accountEmail})과 달라도 제출된다`, async () => {
    const submissions = [];
    const harness = await modelComponentHarness({
      entry: '/src/features/model/ModelApply.jsx', exportName: 'ModelApply',
      initialStates: ['ready', { ...completeApplicationForm, contactEmail: accountEmail || '' }, { ...applicationPhoto, previewUrl: 'blob:profile' }, applicationAttestations, 1, null, false, ''],
      api: { submitApplication: async (body) => { submissions.push(body); return { status: 'under_review' }; } },
    });
    harness.runtime.session = { user: { email: accountEmail } };
    try {
      let tree = harness.render();
      const email = findTree(tree, (node) => node.type?.name === 'FormInput' && node.props.type === 'email');
      assert.equal(email.props.value, accountEmail || '');
      assert.ok(!email.props.readOnly);
      for (const invalid of ['', 'wrong-address', 'a'.repeat(255) + '@example.com']) {
        email.props.onChange({ target: { value: invalid } });
        tree = harness.render();
        assert.equal(findTree(tree, (node) => node.type === 'button' && node.props.children === '다음').props.disabled, true);
      }
      email.props.onChange({ target: { value: ' contact@example.com ' } });
      tree = harness.render();
      const next = findTree(tree, (node) => node.type === 'button' && node.props.children === '다음');
      assert.equal(next.props.disabled, false);
      next.props.onClick();
      findTree(harness.render(), (node) => node.type === 'button' && node.props.children === '다음').props.onClick();
      tree = harness.render();
      assert.ok(findTree(tree, (node) => node.type === 'dd' && node.props.children === 'contact@example.com'));
      const submit = findTree(tree, (node) => node.type === 'button' && node.props.children === '지원 완료');
      assert.equal(submit.props.disabled, false);
      await submit.props.onClick();
      assert.equal(submissions[0].contactEmail, 'contact@example.com');
      assert.equal(harness.runtime.states[0], 'complete');
    } finally { await harness.close(); }
  });
}

test('지원서의 긴 링크와 잘못된 주소는 프로필 단계에서 알리고 수정 후 진행한다', async () => {
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelApply.jsx', exportName: 'ModelApply',
    initialStates: ['ready', completeApplicationForm, applicationPhoto, applicationAttestations, 2, null, false, ''], api: {},
  });
  try {
    for (const label of ['포트폴리오 링크', 'SNS 링크']) {
      const input = findTree(harness.render(), (node) => node.type?.name === 'FormInput' && node.props.label === label);
      // https:// is added before submission; it must count towards the server's limit.
      for (const invalid of ['example.com/' + 'a'.repeat(490), 'ftp://example.com/file']) {
        input.props.onChange({ target: { value: invalid } });
        const tree = harness.render();
        assert.equal(findTree(tree, (node) => node.type === 'button' && node.props.children === '다음').props.disabled, true);
        assert.ok(findTree(tree, (node) => node.type?.name === 'FormInput' && node.props.label === label).props.error);
        assert.equal(findTree(tree, (node) => node.type?.name === 'FormInput' && node.props.label === label).props.value, invalid, 'pasting must not silently truncate the URL');
      }
      input.props.onChange({ target: { value: 'https://example.com/' + 'a'.repeat(480) } });
      assert.equal(findTree(harness.render(), (node) => node.type === 'button' && node.props.children === '다음').props.disabled, false);
      input.props.onChange({ target: { value: '' } });
    }
  } finally { await harness.close(); }
});

for (const failure of [
  { status: 400, code: 'invalid_url', message: '포트폴리오 링크가 너무 깁니다.', expected: /포트폴리오 링크가 너무 깁니다/, step: 2 },
  { status: 400, code: 'invalid_email', message: '이메일 형식이 올바르지 않습니다.', expected: /이메일 형식/, step: 1 },
  { status: 409, code: 'application_exists', message: '이미 검토 중인 지원서가 있습니다.', expected: /이미/, step: 3 },
  { status: 503, message: 'internal server detail', expected: /잠시 후/, step: 3 },
  { message: 'Failed to fetch', expected: /인터넷 연결/, step: 3 },
]) {
  test(`지원서 제출 실패(${failure.code || failure.status || 'network'})는 원인을 구분하고 작성 내용을 보존한다`, async () => {
    let attempts = 0;
    const form = { ...completeApplicationForm, portfolioUrl: 'https://example.com/portfolio' };
    const photo = { ...applicationPhoto, previewUrl: 'blob:profile' };
    const harness = await modelComponentHarness({
      entry: '/src/features/model/ModelApply.jsx', exportName: 'ModelApply',
      initialStates: ['ready', form, photo, applicationAttestations, 3, null, false, ''],
      api: { submitApplication: async () => { attempts += 1; if (attempts === 1) throw Object.assign(new Error(failure.message), failure); return { status: 'under_review' }; } },
    });
    try {
      await findTree(harness.render(), (node) => node.type === 'button' && node.props.children === '지원 완료').props.onClick();
      let tree = harness.render();
      assert.ok(findTree(tree, (node) => node.props.role === 'alert'));
      assert.match(harness.runtime.states[7], failure.expected);
      assert.equal(harness.runtime.states[4], failure.step);
      assert.deepEqual(harness.runtime.states[1], form);
      assert.deepEqual(harness.runtime.states[2], photo);
      assert.deepEqual(harness.runtime.states[3], applicationAttestations);
      if (failure.status === 409) {
        assert.ok(findTree(tree, (node) => node.type === 'Link' && node.props.to === '/status'));
      } else {
        if (failure.step !== 3) {
          findTree(tree, (node) => node.type === 'button' && node.props.children === '다음').props.onClick();
          tree = harness.render();
        }
        const retry = findTree(tree, (node) => node.type === 'button' && node.props.children === '지원 완료');
        assert.equal(retry.props.disabled, false);
        await retry.props.onClick();
        assert.equal(harness.runtime.states[0], 'complete');
      }
    } finally { await harness.close(); }
  });
}


for (const [expiry, expected] of [[null, '철회 시까지'], ['2027-09-07T22:00:00Z', '2027. 9. 8.까지']]) {
  test(`VC card preserves the license boundary: ${expiry}`, async () => {
    const originalWindow = globalThis.window;
    globalThis.window = { location: { origin: 'https://facemarket.example' } };
    const license = { id: 'license-1', status: 'active', unitPrice: 14900, licenseValidUntil: expiry };
    const h = await modelComponentHarness({
      entry: '/src/features/model/ModelLicense.jsx', exportName: 'ModelLicense',
      initialStates: ['ready', 'cards', null, [license], null], api: {},
    });
    try {
      const card = findTree(h.render(), node => node.type?.name === 'VcCard');
      h.runtime.states = [];
      h.runtime.stateCursor = 0;
      const text = collectText(card.type(card.props));
      assert.ok(text.includes(expected), text);
      assert.ok(!text.includes('철회 시까지까지'));
    } finally { globalThis.window = originalWindow; await h.close(); }
  });

  for (const screen of ['PublicVerify', 'PublicVerifyPublication']) {
    test(`${screen} displays the license boundary without duplicate suffix: ${expiry}`, async () => {
      const h = await modelComponentHarness({
        entry: `/src/features/verify/${screen}.jsx`, exportName: screen,
        initialStates: ['ok', { valid: true, status: 'active', validUntil: expiry, licenseValidUntil: expiry, allowedUse: [], imageHashPrefix: 'hash' }, null],
        api: {},
      });
      try {
        const text = collectText(h.render());
        assert.ok(text.includes(expected), text);
        assert.ok(!text.includes('철회 시까지까지'));
      } finally { await h.close(); }
    });
  }
}
