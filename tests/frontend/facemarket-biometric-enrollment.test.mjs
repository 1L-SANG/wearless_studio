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
  enrollmentReasonMessage,
  nextEnrollmentStep,
} = await import('../../src/features/model/biometricEnrollment.js');

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

const flush = () => new Promise((resolve) => setImmediate(resolve));

async function eventually(predicate, message) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (predicate()) return;
    await flush();
  }
  assert.fail(message);
}

function findTree(node, predicate) {
  if (!node || typeof node !== 'object') return null;
  if (predicate(node)) return node;
  const children = Array.isArray(node.props?.children)
    ? node.props.children
    : [node.props?.children];
  for (const child of children) {
    const found = findTree(child, predicate);
    if (found) return found;
  }
  return null;
}

async function modelComponentHarness({
  initialStates,
  api,
  entry = '/src/features/model/ModelRegister.jsx',
  exportName = 'ModelRegister',
}) {
  const key = `__fmRegisterTest${Math.random().toString(36).slice(2)}`;
  const runtime = {
    api,
    effects: [],
    updates: [],
    states: [...initialStates],
    refs: [],
    stateCursor: 0,
    refCursor: 0,
  };
  globalThis[key] = runtime;
  const { createServer } = await import('vite');
  const access = `globalThis[${JSON.stringify(key)}]`;
  const server = await createServer({
    configFile: false,
    logLevel: 'silent',
    root: new URL('../..', import.meta.url).pathname,
    server: { middlewareMode: true },
    ssr: { noExternal: true },
    esbuild: { jsx: 'automatic' },
    appType: 'custom',
    plugins: [{
      name: 'facemarket-register-test-harness',
      enforce: 'pre',
      resolveId(id) {
        if (id === 'react') return '\0fm-test-react';
        if (id === 'react/jsx-dev-runtime' || id === 'react/jsx-runtime') return '\0fm-test-jsx';
        if (id === 'react-router-dom') return '\0fm-test-router';
        if (id === '@/components/ui.jsx') return '\0fm-test-ui';
        if (id === '@/lib/api/facemarket.js') return '\0fm-test-api';
        if (id === '@/lib/api/personalization.js') return '\0fm-test-personalization';
        if (id.endsWith('ModelFaceUpload.jsx')) return '\0fm-test-upload';
        if (id.endsWith('.module.css')) return '\0fm-test-css';
        return null;
      },
      load(id) {
        if (id === '\0fm-test-react') return `
          const runtime = ${access};
          export const lazy = () => 'Lazy';
          export const Suspense = 'Suspense';
          export const useCallback = (value) => value;
          export const useMemo = (factory) => factory();
          export const useState = (initial) => {
            const index = runtime.stateCursor++;
            if (!(index in runtime.states)) runtime.states[index] = typeof initial === 'function' ? initial() : initial;
            return [runtime.states[index], (value) => {
              runtime.states[index] = typeof value === 'function' ? value(runtime.states[index]) : value;
              runtime.updates.push([index, runtime.states[index]]);
            }];
          };
          export const useRef = (initial) => {
            const index = runtime.refCursor++;
            if (!runtime.refs[index]) runtime.refs[index] = { current: initial };
            return runtime.refs[index];
          };
          export const useEffect = (effect) => { runtime.effects.push(effect); };
        `;
        if (id === '\0fm-test-jsx') return `
          export const Fragment = 'Fragment';
          export const jsx = (type, props, key) => ({ type, props: props || {}, key });
          export const jsxs = jsx;
          export const jsxDEV = jsx;
        `;
        if (id === '\0fm-test-router') return `
          export const Link = 'Link';
          export const useNavigate = () => ${access}.navigate;
        `;
        if (id === '\0fm-test-ui') return `
          export const Button = 'Button';
          export const ErrorState = 'ErrorState';
          export const Icon = 'Icon';
          export const useToast = () => ({ push: ${access}.push || (() => {}) });
        `;
        if (id === '\0fm-test-upload') return "export const ModelFaceUpload = 'ModelFaceUpload';";
        if (id === '\0fm-test-css') return 'export default new Proxy({}, { get: (_, key) => key });';
        if (id === '\0fm-test-api') return `
          const api = ${access}.api;
          export const cancelEnrollment = (...args) => api.cancelEnrollment(...args);
          export const completeEnrollment = (...args) => api.completeEnrollment(...args);
          export const createEnrollment = (...args) => api.createEnrollment(...args);
          export const createIdentity = (...args) => api.createIdentity(...args);
          export const createLivenessSession = (...args) => api.createLivenessSession(...args);
          export const deleteEnrollmentPhoto = (...args) => api.deleteEnrollmentPhoto(...args);
          export const getFacemarketConfig = (...args) => (
            api.getFacemarketConfig ? api.getFacemarketConfig(...args) : Promise.resolve({ livenessRequired: true })
          );
          export const getCurrentEnrollment = (...args) => api.getCurrentEnrollment(...args);
          export const getEnrollment = (...args) => api.getEnrollment(...args);
          export const listMyModels = (...args) => api.listMyModels(...args);
          export const submitPhysique = (...args) => api.submitPhysique(...args);
          export const uploadEnrollmentPhoto = (...args) => api.uploadEnrollmentPhoto(...args);
          export const uploadProfileImage = (...args) => api.uploadProfileImage(...args);
        `;
        if (id === '\0fm-test-personalization') return `
          export const getStatus = (...args) => ${access}.api.getStatus(...args);
        `;
        return null;
      },
    }],
  });
  const module = await server.ssrLoadModule(entry);
  return {
    runtime,
    render() {
      runtime.stateCursor = 0;
      runtime.refCursor = 0;
      runtime.effects = [];
      return module[exportName]();
    },
    async close() {
      await server.close();
      delete globalThis[key];
    },
  };
}

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
    'consent', 'identity', 'photos', 'profile', 'liveness', 'processing', 'terms', 'done',
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

test('completeEnrollment no longer sends token', () => {
  const apiSrc = read('../../src/lib/api/facemarket.js');
  assert.match(apiSrc, /completeEnrollment\(enrollmentId,\s*\{\s*sessionId,\s*idPhotoHex\s*\}\)/);
  assert.doesNotMatch(apiSrc, /body:\s*\{\s*sessionId,\s*token,\s*idPhotoHex\s*\}/);
});

test('uploadProfileImage mirrors multipart pattern', () => {
  const apiSrc = read('../../src/lib/api/facemarket.js');
  assert.match(apiSrc, /uploadProfileImage\(\{\s*enrollmentId,\s*fileBlob,\s*filename\s*\}\)/);
  assert.match(apiSrc, /profile-image/);
});

test('identity step runs OACX widget at the FRONT and calls createIdentity with token', () => {
  const reg = read('../../src/features/model/ModelRegister.jsx');
  assert.match(reg, /createIdentity\(/);
  // completeEnrollment 호출은 token 없이 sessionId+idPhotoHex(ref)
  assert.match(reg, /completeEnrollment\(\s*[^,]+,\s*\{\s*sessionId[^}]*idPhotoHex[^}]*\}\s*\)/);
  assert.doesNotMatch(reg, /completeEnrollment\([^)]*token/);
});

test('portrait is held in a ref, never stored/logged', () => {
  const reg = read('../../src/features/model/ModelRegister.jsx');
  assert.match(reg, /useRef\(/);
  assert.doesNotMatch(reg, /localStorage\.setItem\([^)]*dlphoto/i);
});

test('profile step wired between photos and liveness', () => {
  const reg = read('../../src/features/model/ModelRegister.jsx');
  assert.match(reg, /uploadProfileImage\(/);
  assert.match(reg, /step === 'profile'/);
});

test('physique 스텝: 키·체형 선택→제출→대표이미지 스텝', async () => {
  // photos 완료 상태로 진입(physique) → 성별(female)로 좁혀진 키 구간 + 체형 7종 렌더 확인 →
  // 각각 하나씩 선택 → 제출 → fakeApi.submitPhysique 호출 + 다음 스텝(profile) 확인.
  const physiqueCalls = [];
  const harness = await modelComponentHarness({
    initialStates: [
      'physique',
      { id: 'enrollment-1', status: 'liveness_pending', gender: 'female' },
      null, '', false, false,
    ],
    api: {
      submitPhysique: async (args) => {
        physiqueCalls.push(args);
        return {
          id: 'enrollment-1', status: 'liveness_pending', gender: 'female',
          heightBucket: args.heightBucket, bodyType: args.bodyType,
        };
      },
      getCurrentEnrollment: () => new Promise(() => {}),
    },
  });
  try {
    const tree = harness.render();
    const heightChip = findTree(
      tree,
      (node) => node.type === 'button' && node.props?.children === '155cm 미만',
    );
    assert.ok(heightChip, 'the physique step must render female height buckets');
    assert.equal(
      findTree(tree, (node) => node.type === 'button' && node.props?.children === '180–185cm'),
      null,
      'male-only height buckets must not render for a female enrollment',
    );
    const bodyChip = findTree(
      tree,
      (node) => node.type === 'button' && node.props?.children === '마름',
    );
    assert.ok(bodyChip, 'the physique step must render the body-type chips');
    heightChip.props.onClick();
    bodyChip.props.onClick();

    const submitTree = harness.render();
    const submit = findTree(
      submitTree,
      (node) => node.type === 'Button' && node.props?.children === '저장하고 계속',
    );
    assert.ok(submit, 'the physique step exposes a save-and-continue action');
    await submit.props.onClick();
    await flush();

    assert.equal(physiqueCalls.length, 1, 'submitPhysique must be called once');
    assert.equal(physiqueCalls[0].enrollmentId, 'enrollment-1');
    assert.equal(physiqueCalls[0].heightBucket, 'f_lt155');
    assert.equal(physiqueCalls[0].bodyType, 'slim');
    assert.equal(harness.runtime.states[0], 'profile', 'submitting physique advances to the profile step');
  } finally {
    await harness.close();
  }
});

test('physique 스텝 건너뛰기는 서버를 부르지 않고 바로 대표이미지 스텝으로', async () => {
  const physiqueCalls = [];
  const harness = await modelComponentHarness({
    initialStates: [
      'physique',
      { id: 'enrollment-1', status: 'liveness_pending', gender: null },
      null, '', false, false,
    ],
    api: {
      submitPhysique: async (args) => { physiqueCalls.push(args); return {}; },
      getCurrentEnrollment: () => new Promise(() => {}),
    },
  });
  try {
    const tree = harness.render();
    // gender 가 null 이면 남녀 통합 목록(양쪽 다 렌더)이어야 한다.
    assert.ok(
      findTree(tree, (node) => node.type === 'button' && node.props?.children === '155cm 미만'),
      'a null gender shows the unified bucket list (female buckets present)',
    );
    assert.ok(
      findTree(tree, (node) => node.type === 'button' && node.props?.children === '180–185cm'),
      'a null gender shows the unified bucket list (male buckets present)',
    );
    const skip = findTree(
      tree,
      (node) => node.type === 'Button' && node.props?.children === '건너뛰기',
    );
    assert.ok(skip, 'the physique step exposes a skip action');
    skip.props.onClick();

    assert.equal(physiqueCalls.length, 0, 'skipping must not call submitPhysique');
    assert.equal(harness.runtime.states[0], 'profile', 'skipping physique advances to the profile step');
  } finally {
    await harness.close();
  }
});

test('a lost portrait after liveness re-fetches identity in place, keeping the photos', async () => {
  // 진행상황 보존: 라이브니스 후 매치(finishMatch) 시점에 초상 ref 를 잃어도(새로고침 등)
  // 등록을 취소하지 않는다. 사진은 서버에 저장돼 있으니 신분증만 다시 확인(reidentify)해 초상을
  // 되찾아 이어서 진행한다. 보안은 최종 매치의 SFace 대조(초상↔라이브 동일인)가 지킨다.
  const cancelled = [];
  let completeAttempts = 0;
  const harness = await modelComponentHarness({
    initialStates: [
      'liveness',
      { id: 'enrollment-1', status: 'liveness_pending' },
      { sessionId: 'session-1' },
      '',
      false,
      false,
    ],
    api: {
      cancelEnrollment: async (id) => { cancelled.push(id); },
      completeEnrollment: async () => { completeAttempts += 1; return {}; },
      getCurrentEnrollment: () => new Promise(() => {}),
    },
  });
  // 초상 ref(세 번째 ref)를 잃은 상태를 재현한다.
  harness.runtime.refs[2] = { current: null };
  try {
    const tree = harness.render();
    const liveness = findTree(tree, (node) => node.type === 'Lazy');
    await liveness.props.onAnalysisComplete();
    await flush();

    assert.equal(completeAttempts, 0, 'a lost portrait must never attempt server completion');
    assert.deepEqual(cancelled, [], 'the enrollment must NOT be cancelled — progress is preserved');
    assert.equal(harness.runtime.states[0], 'reidentify', 'a lost portrait routes to in-place identity re-fetch');
    assert.equal(harness.runtime.states[1]?.id, 'enrollment-1', 'the enrollment (and its saved photos) are retained');
    assert.notEqual(harness.runtime.states[1], null, 'the enrollment must not be discarded');
    assert.match(harness.runtime.states[3], /사진 그대로/, 'the copy reassures the photos are kept');
  } finally {
    await harness.close();
  }
});

test('the browser wizard keeps raw authentication material in memory only', () => {
  const registerSource = read('../../src/features/model/ModelRegister.jsx');
  const livenessSource = read('../../src/features/model/FaceLivenessStep.jsx');

  // 재정렬 후: complete 는 token 없이 sessionId+idPhotoHex(ref) 만 전달한다.
  assert.match(registerSource, /completeEnrollment\(enrollmentId, \{ sessionId, idPhotoHex \}\)/);
  assert.doesNotMatch(registerSource, /completeEnrollment\([^)]*token/);
  assert.match(registerSource, /useConvertor:\s*true/);
  assert.match(registerSource, /portraitRef\.current\s*=\s*parsed\?\.data\?\.dlphotoimage/);
  assert.match(registerSource, /새 생체 등록 시작/);
  assert.match(registerSource, /localStorage\.setItem\([^,]+,\s*deviceId\)/);
  assert.doesNotMatch(registerSource, /localStorage\.setItem\([^)]*(token|session|credentials|image|dlphotoimage|idPhotoHex)/i);
  assert.doesNotMatch(registerSource, /sessionStorage|indexedDB/i);
  assert.doesNotMatch(registerSource, /console\.(?:log|info|warn|error)/);
  assert.match(registerSource, /cxLoader = pending\.catch[\s\S]*cxLoader = undefined/);
  assert.match(registerSource, /role="alert"/);
  const facemarketApiSource = read('../../src/lib/api/facemarket.js');
  assert.match(
    facemarketApiSource,
    /getEnrollment\(id, \{ signal \} = \{\}\)[\s\S]*?http\([^;]+\{ signal \}\)/,
  );
  assert.match(
    facemarketApiSource,
    /completeEnrollment\(enrollmentId, \{ sessionId, idPhotoHex \}\)/,
  );
  assert.doesNotMatch(facemarketApiSource, /console\.(?:log|info|warn|error)/);
  assert.match(livenessSource, /FaceLivenessDetectorCore/);
  assert.match(livenessSource, /region="us-east-1"/);
  assert.match(livenessSource, /config=\{config\}/);
  assert.doesNotMatch(livenessSource, /localStorage|sessionStorage|indexedDB|console\./i);
});

test('a liveness interruption cancels the enrollment before a fresh retry', () => {
  const apiSource = read('../../src/lib/api/facemarket.js');
  const registerSource = read('../../src/features/model/ModelRegister.jsx');
  // 재정렬 후: 라이브니스 후 매치는 finishMatch 가 담당한다(위젯 없이 저장된 세션+초상 ref).
  const finishMatchSource = registerSource.slice(
    registerSource.indexOf('const finishMatch'),
    registerSource.indexOf("if (step === 'loading')"),
  );

  assert.match(apiSource, /export function cancelEnrollment/);
  assert.match(registerSource, /await cancelEnrollment\(enrollmentId\)/);
  assert.match(registerSource, /setEnrollment\(null\)/);
  assert.match(registerSource, /enrollmentReasonMessage\('liveness_retry'\)/);
  assert.match(registerSource, /issuedLivenessEnrollmentRef/);
  // 언마운트(라우트 이동·HMR·StrictMode 이중 마운트)로는 등록을 취소하지 않는다 — 진행상황 보존.
  // (예전엔 unmount cleanup 이 cancelEnrollment 를 불러 HMR 리마운트마다 등록이 조용히 취소됐다.)
  assert.doesNotMatch(registerSource, /if \(enrollmentId\) cancelEnrollment\(enrollmentId\)\.catch/);
  assert.match(registerSource, /언마운트[^\n]*취소하지 않는다/);
  assert.match(registerSource, /setStep\('cancel_failed'\)/);
  assert.match(registerSource, /등록 취소 다시 시도/);
  assert.doesNotMatch(registerSource, /서버 sweep이 재시도/);
  assert.match(finishMatchSource, /if \(isTransientIdentityError\(requestError\)\)/);
  assert.match(finishMatchSource, /setStep\('identity_failed'\)/);
  assert.match(finishMatchSource, /await abandonLiveness\(\);/);
  assert.doesNotMatch(registerSource, /resetLiveness|reuse(?:Photos|Enrollment)/i);
});

test('a transient identity-completion error retries in place instead of abandoning the enrollment', async () => {
  // 재정렬 후: 라이브니스 후 매치(finishMatch)는 위젯을 다시 띄우지 않고, 앞단에서 담아 둔
  // 초상 ref + 저장된 세션으로 completeEnrollment 만 재시도한다.
  const cancelled = [];
  let completeAttempts = 0;
  const harness = await modelComponentHarness({
    initialStates: [
      'liveness',
      { id: 'enrollment-1', status: 'liveness_pending' },
      { sessionId: 'session-1' },
      '',
      false,
      false,
    ],
    api: {
      cancelEnrollment: async (id) => { cancelled.push(id); },
      completeEnrollment: async () => {
        completeAttempts += 1;
        if (completeAttempts === 1) throw new Error('서버에 연결하지 못했어요.');
        return { passed: true, retryable: false, reason: null, status: 'asset_building', modelId: 'model-1' };
      },
      getCurrentEnrollment: () => new Promise(() => {}),
    },
  });
  // 앞단 identity 스텝에서 초상을 담아 둔 상태를 재현한다(portraitRef 는 세 번째 ref).
  harness.runtime.refs[2] = { current: 'a1b2c3d4' };
  try {
    let tree = harness.render();
    const liveness = findTree(tree, (node) => node.type === 'Lazy');
    await liveness.props.onAnalysisComplete();
    await flush();

    assert.equal(completeAttempts, 1, 'identity completion must have been attempted once');
    assert.equal(cancelled.length, 0, 'a network failure must not cancel the retained enrollment');
    assert.equal(harness.runtime.states[0], 'identity_failed');
    assert.equal(harness.runtime.states[1]?.id, 'enrollment-1', 'the enrollment id must be retained, not discarded');
    assert.ok(harness.runtime.states[2], 'the liveness session must be retained so photos/consent are not re-required');
    assert.equal(harness.runtime.refs[2].current, 'a1b2c3d4', 'the id portrait ref survives a transient failure for retry');

    tree = harness.render();
    const retry = findTree(
      tree,
      (node) => node.type === 'Button' && node.props?.children === '다시 시도',
    );
    assert.ok(retry, 'a transient identity failure must expose a retry action');
    await retry.props.onClick();
    await flush();

    assert.equal(completeAttempts, 2, 'retry must re-drive identity completion using the retained session');
    assert.equal(cancelled.length, 0, 'the retained enrollment must never be cancelled by a transient retry');
    assert.equal(harness.runtime.states[0], 'processing');
    assert.equal(harness.runtime.states[1]?.id, 'enrollment-1');
    assert.equal(harness.runtime.refs[2].current, null, 'the id portrait is discarded once the match completes');
  } finally {
    await harness.close();
  }
});

test('the front identity step authenticates via OACX then advances to photos', async () => {
  const originalWindow = globalThis.window;
  const originalRaf = globalThis.requestAnimationFrame;
  globalThis.requestAnimationFrame = (cb) => { cb(); return 1; };
  globalThis.window = {
    OACX: {
      LOAD_MODULE: (_url, _options, callback) => {
        queueMicrotask(() => callback(JSON.stringify({ token: 'cx-token', data: { dlphotoimage: 'a1b2c3' } })));
      },
    },
  };
  const identityCalls = [];
  const harness = await modelComponentHarness({
    initialStates: ['identity', { id: 'enrollment-1', status: 'identity_pending' }, null, '', false, false],
    api: {
      createIdentity: async (id, body) => { identityCalls.push([id, body]); return {}; },
      getEnrollment: async () => ({ id: 'enrollment-1', status: 'photos_pending', photos: [] }),
      cancelEnrollment: async () => {},
      getCurrentEnrollment: () => new Promise(() => {}),
    },
  });
  try {
    const tree = harness.render();
    const start = findTree(
      tree,
      (node) => node.type === 'Button' && node.props?.children === '모바일 신분증으로 인증',
    );
    assert.ok(start, 'the front identity step exposes an authenticate control');
    await start.props.onClick();
    await flush();

    assert.equal(identityCalls.length, 1, 'createIdentity must be called once with the widget token');
    assert.equal(identityCalls[0][0], 'enrollment-1');
    assert.equal(identityCalls[0][1]?.token, 'cx-token', 'the CX token — not raw PII — is posted to createIdentity');
    assert.equal(harness.runtime.states[0], 'photos', 'a passed identity check advances to photos');
    assert.equal(harness.runtime.refs[2].current, 'a1b2c3', 'the id portrait is captured into a ref, not storage');
  } finally {
    globalThis.window = originalWindow;
    globalThis.requestAnimationFrame = originalRaf;
    await harness.close();
  }
});

test('a liveness-session rejection after the effect is torn down preserves the enrollment', async () => {
  // 정리(언마운트·리렌더) 이후 도착한 세션 실패는 등록을 취소하지 않고 어떤 상태도 바꾸지 않는다.
  let rejectSession;
  const cancelled = [];
  const harness = await modelComponentHarness({
    initialStates: ['liveness', { id: 'enrollment-1' }, null, '', false, false],
    api: {
      cancelEnrollment: async (id) => { cancelled.push(id); },
      createLivenessSession: () => new Promise((_resolve, reject) => { rejectSession = reject; }),
      getCurrentEnrollment: () => new Promise(() => {}),
    },
  });
  // 초상 ref 가 있어야 라이브니스 이펙트가 세션 생성까지 진행한다(없으면 reidentify 로 빠짐).
  harness.runtime.refs[2] = { current: 'portrait-hex' };
  try {
    harness.render();
    const cleanup = harness.runtime.effects[1]();
    cleanup();
    rejectSession(new Error('response lost after server commit'));
    await flush();

    assert.deepEqual(cancelled, [], 'a torn-down session failure must never cancel the retained enrollment');
    assert.equal(
      harness.runtime.updates.some(([index, value]) => index === 0 && (value === 'failed' || value === 'liveness_failed')),
      false,
      'a torn-down session failure changes no step',
    );
  } finally {
    await harness.close();
  }
});

test('an active liveness-session rejection retries in place instead of cancelling the enrollment', async () => {
  // 활성 상태의 세션 생성 실패는 등록(신분증·사진)을 버리지 않고 liveness_failed 로 보내 재시도만 시킨다.
  let rejectSession;
  const cancelled = [];
  const harness = await modelComponentHarness({
    initialStates: ['liveness', { id: 'enrollment-1' }, null, '', false, false],
    api: {
      cancelEnrollment: async (id) => { cancelled.push(id); },
      createLivenessSession: () => new Promise((_resolve, reject) => { rejectSession = reject; }),
      getCurrentEnrollment: () => new Promise(() => {}),
    },
  });
  harness.runtime.refs[2] = { current: 'portrait-hex' };
  try {
    harness.render();
    harness.runtime.effects[1]();
    rejectSession(new Error('rekognition session start failed'));
    await flush();

    assert.deepEqual(cancelled, [], 'a transient session failure must never cancel the retained enrollment');
    assert.equal(harness.runtime.states[0], 'liveness_failed', 'an active session failure routes to the retry state');
    assert.equal(harness.runtime.states[1]?.id, 'enrollment-1', 'the enrollment is retained for retry');
  } finally {
    await harness.close();
  }
});

test('liveness disabled auto-completes without a session, anchoring on the id portrait', async () => {
  // FM_LIVENESS_ENABLED=false: 라이브 단계에서 세션/위젯 없이 신분증 초상 앵커로 바로 완료.
  let completeArgs = null;
  const harness = await modelComponentHarness({
    // 마지막 상태(index 6) = livenessRequired=false.
    initialStates: ['liveness', { id: 'enrollment-1', status: 'liveness_pending' }, null, '', false, false, false],
    api: {
      completeEnrollment: async (id, body) => {
        completeArgs = { id, body };
        return { passed: true, retryable: false, reason: null, status: 'asset_building', modelId: 'model-1' };
      },
      createLivenessSession: () => { throw new Error('createLivenessSession must not run when liveness is disabled'); },
      getCurrentEnrollment: () => new Promise(() => {}),
    },
  });
  harness.runtime.refs[2] = { current: 'portrait-hex' };
  try {
    harness.render();
    harness.runtime.effects[1]();  // 라이브니스 이펙트 — off 면 세션 없이 finishMatch 자동완료
    await flush();
    assert.ok(completeArgs, 'completeEnrollment must run without a liveness session');
    assert.equal(completeArgs.body?.sessionId, undefined, 'no session id is sent when liveness is disabled');
    assert.equal(completeArgs.body?.idPhotoHex, 'portrait-hex', 'the match anchors on the OACX id portrait');
    assert.equal(harness.runtime.states[0], 'processing', 'a passing match moves to processing');
  } finally {
    await harness.close();
  }
});

test('resuming at liveness without a portrait re-fetches identity instead of creating a session', async () => {
  // 새로고침·복귀로 초상 ref 를 잃은 채 라이브니스에 진입하면, 세션을 만들기 전에 reidentify 로
  // 보내 신분증만 다시 확인시킨다 — 사진(서버 저장)은 그대로. 라이브니스 세션은 만들지 않는다.
  let sessionCalls = 0;
  const cancelled = [];
  const harness = await modelComponentHarness({
    initialStates: ['liveness', { id: 'enrollment-1', status: 'liveness_pending' }, null, '', false, false],
    api: {
      cancelEnrollment: async (id) => { cancelled.push(id); },
      createLivenessSession: async () => { sessionCalls += 1; return { sessionId: 's1' }; },
      getCurrentEnrollment: () => new Promise(() => {}),
    },
  });
  // 초상 ref 유실 재현.
  harness.runtime.refs[2] = { current: null };
  try {
    harness.render();
    harness.runtime.effects[1]();
    await flush();

    assert.equal(sessionCalls, 0, 'no liveness session is created while the portrait is missing');
    assert.deepEqual(cancelled, [], 'the enrollment (and its photos) must be preserved, not cancelled');
    assert.equal(harness.runtime.states[0], 'reidentify', 'a missing portrait routes to in-place identity re-fetch');
  } finally {
    await harness.close();
  }
});

test('processing polling retries a transient GET failure and reaches the terminal step', async () => {
  const responses = [
    () => Promise.reject(new Error('temporary network failure')),
    () => Promise.reject(new Error('temporary network failure')),
    () => Promise.reject(new Error('temporary network failure')),
    () => Promise.resolve({ id: 'enrollment-1', status: 'processing' }),
    () => Promise.resolve({ id: 'enrollment-1', status: 'license_pending' }),
  ];
  let calls = 0;
  const harness = await modelComponentHarness({
    initialStates: ['processing', { id: 'enrollment-1', status: 'processing' }, null, '', false, false],
    api: {
      getEnrollment: () => { calls += 1; return responses.shift()(); },
      getCurrentEnrollment: () => new Promise(() => {}),
    },
  });
  const originalSetTimeout = globalThis.setTimeout;
  try {
    harness.render();
    globalThis.setTimeout = (callback, delay) => {
      if (delay >= 100_000) return { callback, delay };
      queueMicrotask(callback);
      return 1;
    };
    harness.runtime.effects[2]();
    await eventually(() => harness.runtime.states[0] === 'terms', 'polling should recover and restore terms');

    assert.equal(calls, 5);
    assert.equal(harness.runtime.states[3], '');
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    await harness.close();
  }
});

test('processing polling stops after four consecutive GET failures', async () => {
  let calls = 0;
  const harness = await modelComponentHarness({
    initialStates: ['processing', { id: 'enrollment-1', status: 'processing' }, null, '', false, false],
    api: {
      getEnrollment: async () => { calls += 1; throw new Error('offline'); },
      getCurrentEnrollment: () => new Promise(() => {}),
    },
  });
  const originalSetTimeout = globalThis.setTimeout;
  try {
    harness.render();
    globalThis.setTimeout = (callback, delay) => {
      if (delay >= 100_000) return { callback, delay };
      queueMicrotask(callback);
      return 1;
    };
    harness.runtime.effects[2]();
    await eventually(() => harness.runtime.states[0] === 'poll_error', 'polling must stop');
    assert.equal(calls, 4);
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    await harness.close();
  }
});

test('processing timeout exposes a restore action that resumes without reloading the page', async () => {
  let currentCalls = 0;
  const harness = await modelComponentHarness({
    initialStates: ['processing', { id: 'enrollment-1', status: 'processing' }, null, '', false, false],
    api: {
      getEnrollment: async () => ({ id: 'enrollment-1', status: 'processing' }),
      getCurrentEnrollment: async () => {
        currentCalls += 1;
        return { id: 'enrollment-1', status: 'license_pending' };
      },
    },
  });
  const originalSetTimeout = globalThis.setTimeout;
  try {
    harness.render();
    globalThis.setTimeout = (callback) => { queueMicrotask(callback); return 1; };
    harness.runtime.effects[2]();
    await eventually(
      () => harness.runtime.states[0] === 'poll_timeout',
      'timeout should leave processing and expose recovery',
    );

    const tree = harness.render();
    const retry = findTree(
      tree,
      (node) => node.type === 'Button' && node.props?.children === '다시 확인하기',
    );
    assert.ok(retry, 'timeout must render an explicit retry/restore control');
    retry.props.onClick();
    await eventually(() => harness.runtime.states[0] === 'terms', 'restore should resume the server step');
    assert.equal(currentCalls, 1);
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    await harness.close();
  }
});

test('an absolute processing deadline aborts a never-settling GET and exposes restore', async () => {
  let requestOptions;
  const timers = [];
  const harness = await modelComponentHarness({
    initialStates: ['processing', { id: 'enrollment-1', status: 'processing' }, null, '', false, false],
    api: {
      getEnrollment: (_id, options) => {
        requestOptions = options;
        return new Promise(() => {});
      },
      getCurrentEnrollment: async () => ({ id: 'enrollment-1', status: 'license_pending' }),
    },
  });
  const originalSetTimeout = globalThis.setTimeout;
  const originalClearTimeout = globalThis.clearTimeout;
  try {
    globalThis.setTimeout = (callback, delay) => {
      const timer = { callback, delay };
      timers.push(timer);
      return timer;
    };
    globalThis.clearTimeout = (timer) => {
      const index = timers.indexOf(timer);
      if (index >= 0) timers.splice(index, 1);
    };
    harness.render();
    const cleanup = harness.runtime.effects[2]();
    await flush();

    assert.equal(requestOptions.signal.aborted, false);
    const deadline = timers.find(({ delay }) => delay >= 119_000);
    assert.ok(deadline, 'the effect must own an absolute 120 second deadline');
    deadline.callback();

    assert.equal(requestOptions.signal.aborted, true);
    assert.equal(harness.runtime.states[0], 'poll_timeout');
    cleanup();
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
    await harness.close();
  }
});

test('unmount aborts the in-flight processing GET without showing timeout recovery', async () => {
  let requestOptions;
  const harness = await modelComponentHarness({
    initialStates: ['processing', { id: 'enrollment-1', status: 'processing' }, null, '', false, false],
    api: {
      getEnrollment: (_id, options) => {
        requestOptions = options;
        return new Promise(() => {});
      },
      getCurrentEnrollment: () => new Promise(() => {}),
    },
  });
  try {
    harness.render();
    const cleanup = harness.runtime.effects[2]();
    await flush();
    cleanup();

    assert.equal(requestOptions.signal.aborted, true);
    assert.equal(harness.runtime.states[0], 'processing');
  } finally {
    await harness.close();
  }
});

test('OACX readiness timeout removes loader-owned scripts and retries without touching existing tags', async () => {
  const elements = new Map();
  const existingVendor = { id: 'oacx-vendor', tagName: 'script' };
  elements.set(existingVendor.id, existingVendor);
  const appended = [];
  let intervalCallback;
  const originals = {
    window: globalThis.window,
    document: globalThis.document,
    setInterval: globalThis.setInterval,
    clearInterval: globalThis.clearInterval,
  };
  globalThis.window = {};
  globalThis.document = {
    getElementById: (id) => elements.get(id) || null,
    createElement: (tagName) => ({
      tagName,
      remove() { elements.delete(this.id); },
    }),
    head: {
      appendChild(element) {
        elements.set(element.id, element);
        if (element.tagName === 'script') {
          appended.push(element.id);
          queueMicrotask(() => element.onload?.());
        }
      },
    },
  };
  globalThis.setInterval = (callback) => { intervalCallback = callback; return callback; };
  globalThis.clearInterval = () => {};
  // 재정렬 후: CX 위젯은 앞단 identity 스텝(runIdentity)에서 로드된다.
  const harness = await modelComponentHarness({
    initialStates: [
      'identity', { id: 'enrollment-1', status: 'identity_pending' }, null, '', false, false,
    ],
    api: { cancelEnrollment: async () => {}, createIdentity: async () => ({}) },
  });
  try {
    const tree = harness.render();
    const start = findTree(
      tree,
      (node) => node.type === 'Button' && node.props?.children === '모바일 신분증으로 인증',
    );
    const first = start.props.onClick();
    await eventually(() => intervalCallback, 'OACX readiness timer should start');
    for (let attempt = 0; attempt <= 50; attempt += 1) intervalCallback();
    await first;

    assert.equal(elements.get('oacx-vendor'), existingVendor);
    assert.equal(elements.has('oacx-ux'), false);

    intervalCallback = undefined;
    const second = start.props.onClick();
    await eventually(() => intervalCallback, 'a fresh OACX readiness timer should start');
    assert.deepEqual(appended, ['oacx-ux', 'oacx-ux']);
    for (let attempt = 0; attempt <= 50; attempt += 1) intervalCallback();
    await second;
  } finally {
    await harness.close();
    for (const [key, value] of Object.entries(originals)) {
      if (value === undefined) delete globalThis[key];
      else globalThis[key] = value;
    }
  }
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

test('enrollment terms and routes cannot revive direct face licensing', () => {
  const apiSource = read('../../src/lib/api/facemarket.js');
  const uploadSource = read('../../src/features/model/ModelFaceUpload.jsx');
  const licenseSource = read('../../src/features/model/ModelLicense.jsx');
  const hubSource = read('../../src/features/model/ModelHub.jsx');
  const appSource = read('../../src/App.jsx');

  assert.match(apiSource, /body:\s*\{ enrollmentId, allowedUse, forbiddenUse, unitPrice, validDays \}/);
  assert.doesNotMatch(apiSource, /fd\.append\(['"]face['"]/);
  assert.doesNotMatch(apiSource, /fd\.append\(['"]profile_id['"]/);
  assert.match(uploadSource, /angles = ENROLLMENT_ANGLES/);
  assert.match(licenseSource, /enrollmentId/);
  assert.doesNotMatch(licenseSource, /profileId|faceBlob/);
  assert.doesNotMatch(hubSource, /buildMyModelAssets|onBuildAssets|자산 생성 중/);
  assert.match(appSource, /function RequireOwnedModel\(\)/);
  assert.match(appSource, /path="generate" element=\{<RequireVerifiedModel \/>\}/);
});
