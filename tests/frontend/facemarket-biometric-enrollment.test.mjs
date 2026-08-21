import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  ENROLLMENT_ANGLES,
  ENROLLMENT_STEPS,
  enrollmentReasonMessage,
  nextEnrollmentStep,
} from '../../src/features/model/biometricEnrollment.js';

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
          export const createLivenessSession = (...args) => api.createLivenessSession(...args);
          export const deleteEnrollmentPhoto = (...args) => api.deleteEnrollmentPhoto(...args);
          export const getCurrentEnrollment = (...args) => api.getCurrentEnrollment(...args);
          export const getEnrollment = (...args) => api.getEnrollment(...args);
          export const listMyModels = (...args) => api.listMyModels(...args);
          export const uploadEnrollmentPhoto = (...args) => api.uploadEnrollmentPhoto(...args);
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
  assert.equal(ENROLLMENT_ANGLES[1].label, '45도');
  assert.match(ENROLLMENT_ANGLES[1].guide, /45도|반측면/);
  assert.equal(ENROLLMENT_ANGLES[2].label, '측면');
  assert.match(ENROLLMENT_ANGLES[2].guide, /90도|옆모습/);
});

test('server status restores the next safe enrollment step', () => {
  assert.deepEqual(ENROLLMENT_STEPS, [
    'consent', 'photos', 'liveness', 'identity', 'processing', 'terms', 'done',
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

test('the browser wizard keeps raw authentication material in memory only', () => {
  const registerSource = read('../../src/features/model/ModelRegister.jsx');
  const livenessSource = read('../../src/features/model/FaceLivenessStep.jsx');

  assert.match(registerSource, /completeEnrollment\(enrollment\.id, \{ sessionId, token \}\)/);
  assert.match(registerSource, /새 생체 등록 시작/);
  assert.match(registerSource, /localStorage\.setItem\([^,]+,\s*deviceId\)/);
  assert.doesNotMatch(registerSource, /localStorage\.setItem\([^)]*(token|session|credentials|image)/i);
  assert.doesNotMatch(registerSource, /sessionStorage|indexedDB/i);
  assert.doesNotMatch(registerSource, /console\.(?:log|info|warn|error)/);
  assert.match(registerSource, /cxLoader = pending\.catch[\s\S]*cxLoader = undefined/);
  assert.match(registerSource, /role="alert"/);
  assert.match(livenessSource, /FaceLivenessDetectorCore/);
  assert.match(livenessSource, /region="us-east-1"/);
  assert.match(livenessSource, /config=\{config\}/);
  assert.doesNotMatch(livenessSource, /localStorage|sessionStorage|indexedDB|console\./i);
});

test('a liveness interruption cancels the enrollment before a fresh retry', () => {
  const apiSource = read('../../src/lib/api/facemarket.js');
  const registerSource = read('../../src/features/model/ModelRegister.jsx');
  const finishIdentitySource = registerSource.slice(
    registerSource.indexOf('const finishIdentity'),
    registerSource.indexOf("if (step === 'loading')"),
  );

  assert.match(apiSource, /export function cancelEnrollment/);
  assert.match(registerSource, /await cancelEnrollment\(enrollmentId\)/);
  assert.match(registerSource, /setEnrollment\(null\)/);
  assert.match(registerSource, /enrollmentReasonMessage\('liveness_retry'\)/);
  assert.match(registerSource, /issuedLivenessEnrollmentRef/);
  assert.match(registerSource, /if \(enrollmentId\) cancelEnrollment\(enrollmentId\)\.catch/);
  assert.match(registerSource, /setStep\('cancel_failed'\)/);
  assert.match(registerSource, /등록 취소 다시 시도/);
  assert.doesNotMatch(registerSource, /서버 sweep이 재시도/);
  assert.match(finishIdentitySource, /catch(?: \(requestError\))? \{\s*await abandonLiveness\(\);/);
  assert.doesNotMatch(registerSource, /resetLiveness|reuse(?:Photos|Enrollment)/i);
});

test('a liveness-session rejection after unmount cancels once without showing a retry CTA', async () => {
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
  try {
    harness.render();
    const cleanup = harness.runtime.effects[1]();
    cleanup();
    rejectSession(new Error('response lost after server commit'));
    await flush();

    assert.deepEqual(cancelled, ['enrollment-1']);
    assert.equal(
      harness.runtime.updates.some(([index, value]) => index === 0 && value === 'failed'),
      false,
    );
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
    globalThis.setTimeout = (callback) => { queueMicrotask(callback); return 1; };
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
    globalThis.setTimeout = (callback) => { queueMicrotask(callback); return 1; };
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
