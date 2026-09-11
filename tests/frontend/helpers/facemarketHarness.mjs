import assert from 'node:assert/strict';
const flush = () => new Promise((resolve) => setImmediate(resolve));
export async function eventually(predicate, message) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (predicate()) return;
    await flush();
  }
  assert.fail(message);
}

export function findTree(node, predicate) {
  if (!node || typeof node !== 'object') return null;
  // map 결과가 children 배열 안에 배열로 들어가는 경우(행 → 카드들)를 뚫는다.
  if (Array.isArray(node)) {
    for (const child of node) {
      const found = findTree(child, predicate);
      if (found) return found;
    }
    return null;
  }
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

export async function modelComponentHarness({
  initialStates,
  api,
  entry = '/src/features/model/ModelRegister.jsx',
  exportName = 'ModelRegister',
  // ModelFaceUpload 자체를 렌더하는 테스트는 스텁 치환을 꺼야 한다(등록 마법사 테스트는 계속 스텁).
  stubUpload = true,
  honorHookDependencies = false,
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
    honorHookDependencies,
    callbacks: [],
    callbackCursor: 0,
    effectDeps: [],
    effectCursor: 0,
  };
  globalThis[key] = runtime;
  const { createServer } = await import('vite');
  const access = `globalThis[${JSON.stringify(key)}]`;
  const server = await createServer({
    configFile: false,
    logLevel: 'silent',
    root: new URL('../../..', import.meta.url).pathname,
    server: { middlewareMode: true },
    ssr: { noExternal: true },
    esbuild: { jsx: 'automatic' },
    appType: 'custom',
    plugins: [{
      name: 'facemarket-register-test-harness',
      enforce: 'pre',
      resolveId(id) {
        if (id === 'qrcode') return '\0fm-test-qrcode';
        if (id === '@/lib/api/facemarketIdentityWidget.js') return '\0fm-test-identity-widget';
        if (id === '@/lib/brandUseCategories.js') return new URL('../../../src/lib/brandUseCategories.js', import.meta.url).pathname;
        // 날짜 표기는 스텁하지 않고 진짜 모듈을 쓴다 — 화면이 그리는 유효기간이 한국
        // 시간인지도 이 테스트가 지나는 경로다(src/lib/datetime.js).
        if (id === '@/lib/datetime.js') return new URL('../../../src/lib/datetime.js', import.meta.url).pathname;
        if (id === 'react') return '\0fm-test-react';
        if (id === 'react/jsx-dev-runtime' || id === 'react/jsx-runtime') return '\0fm-test-jsx';
        if (id === 'react-router-dom') return '\0fm-test-router';
        if (id === '@/components/ui.jsx') return '\0fm-test-ui';
        if (id === '@/lib/api/facemarket.js') return '\0fm-test-api';
        if (id === '@/lib/api/personalization.js') return '\0fm-test-personalization';
        if (stubUpload && id.endsWith('ModelFaceUpload.jsx')) return '\0fm-test-upload';
        if (id.endsWith('imageTranscode.js')) return '\0fm-test-transcode';
        if (id.endsWith('.module.css')) return '\0fm-test-css';
        return null;
      },
      load(id) {
        if (id === '\0fm-test-identity-widget') return `export const runIdentityWidget = (...args) => ${access}.api.runIdentityWidget(...args);`;
        // QR canvas rendering is unrelated to the empty/revoked license list entry.
        if (id === '\0fm-test-qrcode') return 'export default {};';
        if (id === '\0fm-test-react') return `
          const runtime = ${access};
          export const lazy = () => 'Lazy';
          export const Suspense = 'Suspense';
          const unchanged = (previous, next) => previous && next && previous.length === next.length
            && next.every((value, index) => Object.is(value, previous[index]));
          export const useCallback = (value, deps) => {
            if (!runtime.honorHookDependencies) return value;
            const index = runtime.callbackCursor++;
            const previous = runtime.callbacks[index];
            if (!previous || !unchanged(previous.deps, deps)) runtime.callbacks[index] = { value, deps };
            return runtime.callbacks[index].value;
          };
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
          export const useEffect = (effect, deps) => {
            if (runtime.honorHookDependencies) {
              const index = runtime.effectCursor++;
              if (unchanged(runtime.effectDeps[index], deps)) return;
              runtime.effectDeps[index] = deps;
            }
            runtime.effects.push(effect);
          };
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
          export const useLocation = () => ${access}.location || ({ state: null });
          export const useParams = () => ({ licenseId: 'l1' });
          export const useSearchParams = () => [new URLSearchParams(), () => {}];
        `;
        if (id === '\0fm-test-ui') return `
          export const Button = 'Button';
          export const ErrorState = 'ErrorState';
          export const Icon = 'Icon';
          export const Chips = 'Chips';
          export const Toggle = 'Toggle';
          export const Field = 'Field';
          export const useToast = () => ({ push: ${access}.push || (() => {}) });
        `;
        if (id === '\0fm-test-upload') return "export const ModelFaceUpload = 'ModelFaceUpload';";
        if (id === '\0fm-test-css') return 'export default new Proxy({}, { get: (_, key) => key });';
        if (id === '\0fm-test-api') return `
          const api = ${access}.api;
          export const cancelEnrollment = (...args) => api.cancelEnrollment(...args);
          export const createLicense = (...args) => api.createLicense(...args);
          export const revokeLicense = (...args) => api.revokeLicense(...args);
          export const verifyLicensePublic = (...args) => api.verifyLicensePublic(...args);
          export const completeEnrollment = (...args) => api.completeEnrollment(...args);
          export const createEnrollment = (...args) => api.createEnrollment(...args);
          export const createIdentity = (...args) => api.createIdentity(...args);
          export const createLivenessSession = (...args) => api.createLivenessSession(...args);
          export const deleteEnrollmentPhoto = (...args) => api.deleteEnrollmentPhoto(...args);
          export const getFacemarketConfig = (...args) => (
            api.getFacemarketConfig ? api.getFacemarketConfig(...args) : Promise.resolve({ livenessRequired: true })
          );
          export const getCurrentEnrollment = (...args) => api.getCurrentEnrollment(...args);
          // 지원서 리뉴얼(2026-09-02) — ModelHub 가 설정·지원서를 함께 조회한다. 테스트가 안 주면
          // "게이트 꺼짐 · 지원서 없음(404)" 으로 떨어져 종전 등록 여정만 검사한다.
          export const getApplicationConfig = (...args) => (
            api.getApplicationConfig ? api.getApplicationConfig(...args) : Promise.resolve({ applicationRequired: false })
          );
          export const getCurrentApplication = (...args) => (
            api.getCurrentApplication ? api.getCurrentApplication(...args)
              : Promise.reject(Object.assign(new Error('no application'), { status: 404 }))
          );
          export const updateLicenseTerms = (...args) => api.updateLicenseTerms(...args);
          export const pauseMyModel = (...args) => api.pauseMyModel(...args);
          export const resumeMyModel = (...args) => api.resumeMyModel(...args);
          export const reportUsage = (...args) => api.reportUsage(...args);
          export const cancelApplication = (...args) => api.cancelApplication(...args);
          export const getEnrollment = (...args) => api.getEnrollment(...args);
          export const listMyModels = (...args) => (
            api.listMyModels ? api.listMyModels(...args) : Promise.resolve([])
          );
          export const listLicenses = (...args) => (
            api.listLicenses ? api.listLicenses(...args) : Promise.resolve([])
          );
          export const listSettlements = (...args) => (
            api.listSettlements ? api.listSettlements(...args) : Promise.resolve([])
          );
          export const getSettlementSummary = (...args) => (
            api.getSettlementSummary ? api.getSettlementSummary(...args)
              : Promise.resolve({ monthCount: 0, monthAmount: 0, totalAmount: 0 })
          );
          export const stageApplicationPhoto = (...args) => api.stageApplicationPhoto(...args);
          export const submitApplication = (...args) => api.submitApplication(...args);
          export const fetchLicenseFaceUrl = (...args) => (
            api.fetchLicenseFaceUrl ? api.fetchLicenseFaceUrl(...args) : Promise.reject(new Error('no face'))
          );
          export const submitPhysique = (...args) => api.submitPhysique(...args);
          export const uploadEnrollmentPhoto = (...args) => api.uploadEnrollmentPhoto(...args);
          export const fetchEnrollmentPhotoUrl = (...args) => api.fetchEnrollmentPhotoUrl ? api.fetchEnrollmentPhotoUrl(...args) : Promise.reject(new Error('no preview')); 
          export const uploadProfileImage = (...args) => api.uploadProfileImage(...args);
        `;
        if (id === '\0fm-test-personalization') return `
          const api = ${access}.api;
          export const getStatus = (...args) => api.getStatus(...args);
          export const listFacePhotos = (...args) => api.listFacePhotos(...args);
          export const uploadFacePhoto = (...args) => api.uploadFacePhoto(...args);
          export const deleteFacePhoto = (...args) => api.deleteFacePhoto(...args);
          export const fetchFacePhotoUrl = (...args) => api.fetchFacePhotoUrl(...args);
        `;
        // 변환(HEIC→JPEG)은 canvas 를 쓰므로 노드에서 못 돈다 — 기본은 원본 통과, 필요하면 런타임이 교체.
        if (id === '\0fm-test-transcode') return `
          export const toUploadableImage = (file) => (
            ${access}.toUploadableImage ? ${access}.toUploadableImage(file) : Promise.resolve(file)
          );
        `;
        return null;
      },
    }],
  });
  let module;
  try { module = await server.ssrLoadModule(entry); }
  catch (error) { await server.close(); delete globalThis[key]; throw error; }
  return {
    runtime,
    render(props = {}) {
      runtime.stateCursor = 0;
      runtime.refCursor = 0;
      runtime.callbackCursor = 0;
      runtime.effectCursor = 0;
      runtime.effects = [];
      return module[exportName](props);
    },
    async close() {
      await server.close();
      delete globalThis[key];
    },
  };
}

