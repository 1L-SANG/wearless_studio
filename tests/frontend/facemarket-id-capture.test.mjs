import test from 'node:test';
import assert from 'node:assert/strict';

// IdentityMethodStep.jsx·IdDocumentStep.jsx 는 이 레포에 DOM 렌더러가 없다는 제약 아래
// 실제 동작(effect 실행, onClick 호출, async submit)까지 검증해야 한다. tests/frontend/
// facemarket-biometric-enrollment.test.mjs 의 modelComponentHarness 와 같은 기법을 쓴다:
// Vite 의 SSR 로더로 실제 컴포넌트를 불러오되, react/jsx-runtime 을 순수 JS 객체 트리를
// 만드는 스텁으로 바꿔치기해서(JSDOM 없이) props·훅 상태를 직접 조작·관측한다.

const collectText = (node) => (Array.isArray(node) ? node.map(collectText).join('')
  : node && typeof node === 'object' ? collectText(node.props?.children) : String(node ?? ''));

function findTree(node, predicate) {
  if (!node || typeof node !== 'object') return null;
  if (Array.isArray(node)) {
    for (const child of node) {
      const found = findTree(child, predicate);
      if (found) return found;
    }
    return null;
  }
  if (predicate(node)) return node;
  const children = Array.isArray(node.props?.children) ? node.props.children : [node.props?.children];
  for (const child of children) {
    const found = findTree(child, predicate);
    if (found) return found;
  }
  return null;
}

// entry 가 기대하는 훅 호출 순서에 맞춰 initialStates(useState 인덱스)·initialRefs(useRef
// 인덱스)를 미리 채워 넣는다 — biometricEnrollment 테스트의 portraitRef=refs[2] 관례와 같다.
async function stepHarness({ entry, exportName = 'default', initialStates = [], initialRefs = [], api = {} }) {
  const key = `__fmIdCaptureTest${Math.random().toString(36).slice(2)}`;
  const runtime = {
    api,
    effects: [],
    states: [...initialStates],
    refs: initialRefs.map((current) => ({ current })),
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
      name: 'facemarket-id-capture-test-harness',
      enforce: 'pre',
      resolveId(id) {
        if (id === 'react') return '\0fm-id-test-react';
        if (id === 'react/jsx-dev-runtime' || id === 'react/jsx-runtime') return '\0fm-id-test-jsx';
        if (id === '@/components/ui.jsx') return '\0fm-id-test-ui';
        if (id === '@/lib/api/facemarket.js') return '\0fm-id-test-api';
        if (id.endsWith('.module.css')) return '\0fm-id-test-css';
        if (id.startsWith('@/')) return new URL('../../src/' + id.slice(2), import.meta.url).pathname;
        return null;
      },
      load(id) {
        if (id === '\0fm-id-test-react') return `
          const runtime = ${access};
          export const useCallback = (value) => value;
          export const useMemo = (factory) => factory();
          export const useState = (initial) => {
            const index = runtime.stateCursor++;
            if (!(index in runtime.states)) runtime.states[index] = typeof initial === 'function' ? initial() : initial;
            return [runtime.states[index], (value) => {
              runtime.states[index] = typeof value === 'function' ? value(runtime.states[index]) : value;
            }];
          };
          export const useRef = (initial) => {
            const index = runtime.refCursor++;
            if (!runtime.refs[index]) runtime.refs[index] = { current: initial };
            return runtime.refs[index];
          };
          export const useEffect = (effect) => { runtime.effects.push(effect); };
        `;
        if (id === '\0fm-id-test-jsx') return `
          export const Fragment = 'Fragment';
          export const jsx = (type, props, key) => ({ type, props: props || {}, key });
          export const jsxs = jsx;
          export const jsxDEV = jsx;
        `;
        if (id === '\0fm-id-test-ui') return `
          export const Button = 'Button';
          export const Icon = 'Icon';
        `;
        if (id === '\0fm-id-test-api') return `
          const api = ${access}.api;
          export const uploadIdDocument = (...args) => api.uploadIdDocument(...args);
        `;
        if (id === '\0fm-id-test-css') return 'export default new Proxy({}, { get: (_, key) => key });';
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
      runtime.effects = [];
      return module[exportName](props);
    },
    async close() {
      await server.close();
      delete globalThis[key];
    },
  };
}

// ── (a) 메서드가 하나면 화면을 그리지 않고 즉시 그 메서드로 진행한다 ───────────

test('메서드가 하나면 IdentityMethodStep 은 아무것도 그리지 않고 자동으로 그 메서드를 고른다', async () => {
  const harness = await stepHarness({ entry: '/src/features/model/IdentityMethodStep.jsx' });
  try {
    const picked = [];
    const tree = harness.render({ methods: ['mid'], onPick: (method) => picked.push(method) });
    assert.equal(tree, null, '단일 메서드면 렌더 결과가 null 이어야 한다(발표 모드에 빈 화면이 없어야 함)');
    assert.deepEqual(picked, [], '렌더 자체는 onPick 을 부르지 않는다 — effect 가 불러야 한다');
    harness.runtime.effects.forEach((effect) => effect());
    assert.deepEqual(picked, ['mid'], '단일 메서드면 effect 가 자동으로 그 메서드를 골라야 한다');
  } finally {
    await harness.close();
  }
});

test('메서드가 둘이면 선택 화면을 그리고 자동으로 고르지 않는다', async () => {
  const harness = await stepHarness({ entry: '/src/features/model/IdentityMethodStep.jsx' });
  try {
    const picked = [];
    const tree = harness.render({ methods: ['mid', 'simple_auth'], onPick: (method) => picked.push(method) });
    assert.notEqual(tree, null, '메서드가 둘이면 선택 화면이 있어야 한다');
    harness.runtime.effects.forEach((effect) => effect());
    assert.deepEqual(picked, [], '메서드가 둘이면 자동 선택하면 안 된다');
    const midButton = findTree(tree, (node) => node.type === 'button'
      && collectText(node).includes('모바일 신분증으로 확인'));
    assert.ok(midButton, '모바일 신분증 버튼을 찾을 수 없다');
    midButton.props.onClick();
    assert.deepEqual(picked, ['mid'], '버튼을 누르면 그 메서드로 onPick 이 불려야 한다');
  } finally {
    await harness.close();
  }
});

// ── (c) VITE_CX_AUTH_CONFIG_URL 이 없으면(=부모가 이유를 넘기면) 간편인증이 비활성화 ──

test('simpleAuthUnavailableReason 이 있으면 간편인증 버튼이 비활성화되고 이유가 보인다', async () => {
  const harness = await stepHarness({ entry: '/src/features/model/IdentityMethodStep.jsx' });
  try {
    const reason = '간편인증은 지금 설정되지 않았어요. 모바일 신분증으로 확인해 주세요.';
    const tree = harness.render({ methods: ['mid', 'simple_auth'], onPick: () => {}, simpleAuthUnavailableReason: reason });
    const simpleAuthButton = findTree(tree, (node) => node.type === 'button'
      && collectText(node).includes('간편인증으로 확인'));
    assert.ok(simpleAuthButton, '간편인증 버튼을 찾을 수 없다');
    assert.equal(simpleAuthButton.props.disabled, true, 'reason 이 있으면 버튼이 비활성화돼야 한다');
    assert.match(collectText(simpleAuthButton), /설정되지 않았어요/, '이유가 화면에 보여야 한다');
  } finally {
    await harness.close();
  }
});

test('simpleAuthUnavailableReason 이 없으면 간편인증 버튼이 활성화된다', async () => {
  const harness = await stepHarness({ entry: '/src/features/model/IdentityMethodStep.jsx' });
  try {
    const tree = harness.render({ methods: ['mid', 'simple_auth'], onPick: () => {} });
    const simpleAuthButton = findTree(tree, (node) => node.type === 'button'
      && collectText(node).includes('간편인증으로 확인'));
    assert.ok(simpleAuthButton);
    assert.equal(simpleAuthButton.props.disabled, false);
  } finally {
    await harness.close();
  }
});

// ── (b) IdDocumentStep: 서버로 가는 건 캔버스에서 뽑은 blob 이지 원본 File 이 아니다 ──

// 훅 호출 순서(IdDocumentStep.jsx 상단 주석과 동일해야 한다):
//   useState: documentType, imageUrl, imageLoaded, maskRatio, maskedConfirmed, busy, localError
//   useRef:   imageRef(0), canvasRef(1), dragRef(2)
function fakeCanvasElement() {
  const calls = { drawImage: [], fillRect: [] };
  return {
    width: 0,
    height: 0,
    calls,
    getContext: () => ({
      drawImage: (...args) => calls.drawImage.push(args),
      set fillStyle(_v) {},
      fillRect: (...args) => calls.fillRect.push(args),
    }),
    toBlob(resolve, type) {
      // 이 blob 은 원본 File 이 절대 아니다 — 캔버스가 방금 그리고 채운 결과를 대신하는
      // 표식(__maskedBlobMarker)일 뿐이다. 아래 테스트는 uploadIdDocument 가 이 표식을
      // 받는지, 원본 File 객체를 받는지를 가른다.
      resolve({ __maskedBlobMarker: true, type, drawImageCalls: calls.drawImage.length, fillRectCalls: calls.fillRect.length });
    },
  };
}

test('제출하면 원본 File 이 아니라 캔버스에서 뽑은 마스킹된 blob 이 uploadIdDocument 로 간다', async () => {
  const uploads = [];
  const originalFile = { __originalFileMarker: true, name: 'my-id-card.jpg' };
  const fakeImage = { naturalWidth: 800, naturalHeight: 600, __originalImageEl: true };
  const fakeCanvas = fakeCanvasElement();

  const harness = await stepHarness({
    entry: '/src/features/model/IdDocumentStep.jsx',
    initialStates: [
      'rrc',                                    // documentType
      'blob:fake-preview-url',                  // imageUrl (object URL — 원본 File 자체가 아니다)
      true,                                      // imageLoaded
      { xr: 0.1, yr: 0.6, wr: 0.5, hr: 0.15 },   // maskRatio
      true,                                      // maskedConfirmed
      false,                                     // busy
      '',                                        // localError
    ],
    initialRefs: [fakeImage, fakeCanvas, null],
    api: {
      uploadIdDocument: async (enrollmentId, body) => {
        uploads.push({ enrollmentId, ...body });
        return { status: 'identity_pending' };
      },
    },
  });
  try {
    const uploaded = [];
    const tree = harness.render({
      enrollmentId: 'enrollment-1',
      onUploaded: () => uploaded.push(true),
      onError: () => {},
    });
    const submitButton = findTree(tree, (node) => node.type === 'Button'
      && collectText(node).includes('확인 요청'));
    assert.ok(submitButton, '제출 버튼을 찾을 수 없다');
    assert.equal(submitButton.props.disabled, false, '필요한 상태가 다 갖춰졌으면 제출 버튼이 활성화돼야 한다');

    await submitButton.props.onClick();

    assert.equal(uploads.length, 1, 'uploadIdDocument 가 정확히 한 번 불려야 한다');
    const [call] = uploads;
    assert.equal(call.enrollmentId, 'enrollment-1');
    assert.equal(call.documentType, 'rrc');
    assert.equal(call.maskedConfirmed, true);
    // 핵심 계약: 넘어간 file 은 캔버스 blob 이지 원본 File 객체가 아니다.
    assert.notEqual(call.file, originalFile, '원본 File 객체가 그대로 넘어가면 안 된다');
    assert.equal(call.file.__maskedBlobMarker, true, 'uploadIdDocument 는 캔버스에서 뽑은 blob 을 받아야 한다');
    assert.equal(call.file.drawImageCalls, 1, '캔버스에 이미지를 한 번은 그렸어야 한다');
    assert.equal(call.file.fillRectCalls, 1, '전송 전 마스킹 사각형을 실제로 채웠어야 한다');
    // 캔버스 크기는 화면 표시 크기가 아니라 원본 이미지의 실제 픽셀 크기를 따라간다.
    assert.equal(fakeCanvas.width, 800);
    assert.equal(fakeCanvas.height, 600);

    assert.deepEqual(uploaded, [true], '성공하면 onUploaded 가 불려야 한다');
  } finally {
    await harness.close();
  }
});

test('마스킹 확인 체크가 안 됐으면 제출 버튼이 비활성화된다', async () => {
  const fakeImage = { naturalWidth: 800, naturalHeight: 600 };
  const fakeCanvas = fakeCanvasElement();
  const harness = await stepHarness({
    entry: '/src/features/model/IdDocumentStep.jsx',
    initialStates: ['rrc', 'blob:fake-preview-url', true, { xr: 0.1, yr: 0.6, wr: 0.5, hr: 0.15 }, false, false, ''],
    initialRefs: [fakeImage, fakeCanvas, null],
    api: { uploadIdDocument: async () => { throw new Error('불려서는 안 된다'); } },
  });
  try {
    const tree = harness.render({ enrollmentId: 'enrollment-1', onUploaded: () => {}, onError: () => {} });
    const submitButton = findTree(tree, (node) => node.type === 'Button'
      && collectText(node).includes('확인 요청'));
    assert.equal(submitButton.props.disabled, true, '마스킹 확인 체크 없이는 제출할 수 없어야 한다');
  } finally {
    await harness.close();
  }
});

// 이 레포엔 ESLint 가 없어 미선언 식별자가 조용히 통과한다(CLAUDE.md 경고 — 실제로 에디터
// 전체가 이렇게 죽은 적이 있다). 위 테스트들은 전부 documentType/imageUrl 이 이미 채워진
// 상태로 렌더해서 "사진을 아직 안 고른" 첫 화면(업로드 존) JSX 는 한 번도 실행되지
// 않았다 — 그 분기에서만 쓰는 식별자(Icon "imagePlus" 등)가 안전한지 여기서 확인한다.
test('사진을 고르기 전 첫 화면도 예외 없이 그려진다(업로드 존 분기 점검)', async () => {
  const harness = await stepHarness({
    entry: '/src/features/model/IdDocumentStep.jsx',
    initialStates: [null, null, false, null, false, false, ''],
    initialRefs: [null, null, null],
  });
  try {
    const tree = harness.render({ enrollmentId: 'enrollment-1', onUploaded: () => {}, onError: () => {} });
    assert.notEqual(tree, null);
    assert.match(collectText(tree), /먼저 신분증 종류를 골라 주세요/);
    const rrcChip = findTree(tree, (node) => node.type === 'button' && collectText(node).includes('주민등록증'));
    assert.ok(rrcChip, '신분증 종류 칩(주민등록증)이 보여야 한다');
    rrcChip.props.onClick();
    const fileInput = findTree(tree, (node) => node.type === 'input' && node.props.type === 'file');
    assert.ok(fileInput, '파일 입력이 있어야 한다');
    assert.equal(fileInput.props.accept, 'image/*');
    assert.equal(fileInput.props.capture, 'environment');
  } finally {
    await harness.close();
  }
});
