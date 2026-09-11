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

test('활동 마이페이지는 라이선스가 유효할 때 수익과 조건 영역을 함께 열어요', async () => {
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
    assert.ok(findTree(tree, node => node.type?.name === 'MyPageEarnings'));
    assert.ok(findTree(tree, node => node.type?.name === 'MyPageConditions'));
    assert.ok(findTree(tree, node => node.type === 'button' && node.props.children === '그만두기'));
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

test('라이선스 발급은 등록 완료 화면으로 체형과 발급 조건을 전달한다', () => {
  const source = read('../../src/features/model/ModelLicense.jsx');
  assert.match(
    source,
    /navigate\(\s*["']\/model\/register["'][\s\S]*completionSummary:\s*buildRegistrationCompletion\(enrollmentRecord,\s*lic\)/,
  );
});

test('지원서 제출 성공 뒤 완료 화면에 머물고 모델 리스트로 이어진다', async () => {
  const navigations = [];
  const submissions = [];
  const harness = await modelComponentHarness({
    entry: '/src/features/model/ModelApply.jsx',
    exportName: 'ModelApply',
    initialStates: [
      'ready',
      {
        contactEmail: 'model@example.com', lastName: '김', firstName: '하나', phone: '',
        birthdate: '2000-01-01', region: '서울, 대한민국', gender: 'female',
        experienceLevel: 'beginner', categories: ['fashion'], portfolioUrl: '', snsUrl: '', bio: '',
      },
      { profile: { staged: true, name: 'profile.jpg' } },
      { adultAndTruthful: true, photosAreMine: true },
      true,
      true,
      null,
    ],
    api: {
      getCurrentApplication: () => new Promise(() => {}),
      stageApplicationPhoto: async () => ({}),
      submitApplication: async (body) => { submissions.push(body); return { id: 'a1', status: 'under_review' }; },
    },
  });
  harness.runtime.navigate = (...args) => navigations.push(args);
  try {
    const form = harness.render();
    const submit = findTree(
      form,
      (node) => node.type === 'button' && node.props?.children === '지원서 제출하기',
    );
    assert.ok(submit, '완성된 지원서는 제출할 수 있어야 한다');
    await submit.props.onClick();
    await flush();

    assert.equal(submissions.length, 1);
    assert.equal(harness.runtime.states[0], 'complete');
    assert.deepEqual(navigations, [], '제출 직후 상태 허브로 자동 이동하지 않는다');

    const complete = harness.render();
    assert.ok(findTree(complete, (node) => node.props?.children === '지원서 접수가 끝났어요'));
    assert.ok(findTree(complete, (node) => node.props?.children === '승인되면 메일로 등록 링크가 가요 · 보통 1시간 안'));
    assert.ok(findTree(complete, (node) => node.props?.children === '정부 모바일 신분증 앱'));
    assert.ok(findTree(complete, (node) => node.props?.children === '본인 사진'));
    assert.ok(findTree(complete, (node) => node.type === 'Link' && node.props?.to === '/models'));
  } finally {
    await harness.close();
  }
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


test('그만두기는 확인 뒤 현재 FaceMarket 라이선스를 해지하고 화면 상태를 갱신해요', async () => {
  const requests = [], updates = [];
  const harness = await modelComponentHarness({
    entry:'/src/features/model/mypage/MyPage.jsx', exportName:'ActiveDashboard', initialStates:[],
    api:{ revokeLicense:async id=>{requests.push(id);return {id,modelId:'m1',status:'revoked'};} },
  });
  const props={journey:{flag:'none'},model:{id:'m1',status:'verified'},license:{id:'l1',status:'active'},licenses:[],onLicenseChange:value=>updates.push(value)};
  try {
    let tree=harness.render(props);
    findTree(tree,node=>node.type==='button'&&node.props.children==='그만두기').props.onClick();
    assert.deepEqual(requests,[]);
    tree=harness.render(props);
    const dialog=findTree(tree,node=>node.type?.name==='MyPageDialog');
    assert.ok(dialog);
    await findTree(dialog,node=>node.type==='button'&&node.props.children==='라이선스 해지하기').props.onClick();
    assert.deepEqual(requests,['l1']);
    assert.equal(updates[0].status,'revoked');
  } finally {await harness.close();}
});

test('영구로 바꾼 라이선스는 공개 증서에서도 영구라고 표시해요', async () => {
  const harness=await modelComponentHarness({entry:'/src/features/verify/PublicVerify.jsx',exportName:'PublicVerify',initialStates:['ok',{status:'active',valid:true,validUntil:null},null],api:{}});
  try { assert.ok(findTree(harness.render(), node=>node.type==='dd'&&node.props.children==='영구')); }
  finally {await harness.close();}
});
