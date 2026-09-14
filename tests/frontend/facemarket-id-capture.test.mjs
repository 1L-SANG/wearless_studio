import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

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
    effectCursor: 0,
    effectDeps: [],
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
          // 실제 React 처럼 deps 를 얕은 비교로 본다(no deps → 항상 다시 돈다, deps 배열이면
          // 이전 값과 Object.is 비교) — IdentityMethodStep 의 "메서드 하나면 정확히 한 번만
          // onPick" 계약이 바로 이 스킵 로직에 의존하므로, 스킵을 안 하는 하네스로는 그
          // 계약을 검증할 수 없다.
          const unchanged = (previous, next) => previous && next && previous.length === next.length
            && next.every((value, index) => Object.is(value, previous[index]));
          export const useEffect = (effect, deps) => {
            const index = runtime.effectCursor++;
            const previous = runtime.effectDeps[index];
            if (deps && unchanged(previous, deps)) return;
            runtime.effectDeps[index] = deps;
            runtime.effects.push(effect);
          };
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

// 리뷰 IMPORTANT 2: methods·onPick 참조가 안정적이면(ModelRegister.jsx 가 IDENTITY_METHODS
// 모듈 상수 + useCallback 으로 고정한 onPick 을 넘기는 실제 배포와 같다) 부모가 몇 번을
// 리렌더해도(busy 토글 등 이 컴포넌트와 무관한 이유로) 이 컴포넌트의 effect 는 deps 가 그대로라
// 다시 안 돈다 — 다시 안 돌면 onPick 도 다시 안 불린다. 반대로 IdentityMethodStep.jsx 의
// useEffect 에서 deps 배열([methods, onPick])을 빼먹거나 잘못된 값으로 바꾸면(실제 React 는
// deps 없는 effect 를 매 렌더 다시 돈다) 이 테스트가 picked.length > 1 로 잡아낸다.
test('참조가 고정돼 있으면 여러 번 리렌더돼도 onPick 은 정확히 한 번만 불린다(중복 등록 생성 방지)', async () => {
  const harness = await stepHarness({ entry: '/src/features/model/IdentityMethodStep.jsx' });
  try {
    const picked = [];
    const methods = ['mid']; // 같은 배열 참조 재사용 — IDENTITY_METHODS 는 모듈 상수라 항상 같은 참조다.
    const onPick = (method) => picked.push(method); // 같은 함수 참조 재사용 — handleMethodPick(useCallback) 과 동등.
    for (let i = 0; i < 4; i += 1) {
      harness.render({ methods, onPick });
      harness.runtime.effects.forEach((effect) => effect());
    }
    assert.deepEqual(picked, ['mid'], `onPick 은 정확히 한 번만 불려야 한다(실제로는 ${picked.length}번)`);
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
// sequence 는 drawImage/fillRect 를 하나의 시간순 배열에 적재한다(길이만 세는 배열 두 개가
// 아니다) — drawImage.length===1 && fillRect.length===1 은 fillRect 를 먼저 부르고
// drawImage 로 원본을 그 위에 덧그려도(마스킹이 사라짐) 똑같이 통과해 버린다. 아래
// __maskedBlobMarker 소비 테스트는 이 sequence 로 "그린 뒤 채웠는가"까지 확인한다.
function fakeCanvasElement() {
  const sequence = [];
  return {
    width: 0,
    height: 0,
    sequence,
    getContext: () => ({
      drawImage: (...args) => sequence.push({ op: 'drawImage', args }),
      set fillStyle(_v) {},
      fillRect: (...args) => sequence.push({ op: 'fillRect', args }),
    }),
    toBlob(resolve, type) {
      // 이 blob 은 원본 File 이 절대 아니다 — 캔버스가 방금 그리고 채운 결과를 대신하는
      // 표식(__maskedBlobMarker)일 뿐이다. 아래 테스트는 uploadIdDocument 가 이 표식을
      // 받는지, 원본 File 객체를 받는지를 가른다.
      resolve({ __maskedBlobMarker: true, type, sequence: [...sequence] });
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
    const drawImageCalls = call.file.sequence.filter((c) => c.op === 'drawImage');
    const fillRectCalls = call.file.sequence.filter((c) => c.op === 'fillRect');
    assert.equal(drawImageCalls.length, 1, '캔버스에 이미지를 한 번은 그렸어야 한다');
    assert.equal(fillRectCalls.length, 1, '전송 전 마스킹 사각형을 실제로 채웠어야 한다');
    // 핵심: drawImage 가 fillRect 보다 먼저다 — 뒤집히면 마스킹이 원본 아래 깔려 사라진다.
    const drawImageIndex = call.file.sequence.findIndex((c) => c.op === 'drawImage');
    const fillRectIndex = call.file.sequence.findIndex((c) => c.op === 'fillRect');
    assert.ok(
      drawImageIndex < fillRectIndex,
      `drawImage(${drawImageIndex}) 가 fillRect(${fillRectIndex}) 보다 먼저 일어나야 한다`,
    );
    // 캔버스 크기는 화면 표시 크기가 아니라 원본 이미지의 실제 픽셀 크기를 따라간다.
    assert.equal(fakeCanvas.width, 800);
    assert.equal(fakeCanvas.height, 600);

    assert.deepEqual(uploaded, [true], '성공하면 onUploaded 가 불려야 한다');
  } finally {
    await harness.close();
  }
});

// 최종리뷰 C3: 화면이 레터박스된 상태에서도 실제로 칠린 사각형이 원본의 의도한
// 자리를 덮어야 한다. .idPreviewImage 는 `object-fit: contain` + `max-height: 60vh` 라
// 세로로 긴 사진은 엘리먼트 박스보다 작게 그려진다 — 그때 엘리먼트 박스 비율을 자연
// 픽셀에 그대로 곱하면(옛 코드) 마스크가 밀려 찍히고 주민등록번호가 그대로 올라간다.
// 여기서는 fillRect 인자(=실제로 칠한 자연 좌표)를 직접 못박는다.
test('레터박스된 미리보기에서도 fillRect 가 원본의 의도한 영역을 덮는다(3:2 사진 · 1:1 박스)', async () => {
  const uploads = [];
  // 원본 900x600(3:2)이 600x600(1:1) 박스에 contain 으로 들어가면 600x400 이 되고
  // 위아래로 100px 씩 여백이 생긴다. 사용자가 화면에서 맞춘 박스(엘리먼트 박스 비율
  // 0.1/0.6/0.5/0.1)는 원본 좌표 { x:90, y:390, w:450, h:90 } 를 덮어야 한다.
  const fakeImage = {
    naturalWidth: 900,
    naturalHeight: 600,
    getBoundingClientRect: () => ({ width: 600, height: 600 }),
  };
  const fakeCanvas = fakeCanvasElement();
  const harness = await stepHarness({
    entry: '/src/features/model/IdDocumentStep.jsx',
    initialStates: [
      'rrc', 'blob:fake-preview-url', true,
      { xr: 0.1, yr: 0.6, wr: 0.5, hr: 0.1 },
      true, false, '',
    ],
    initialRefs: [fakeImage, fakeCanvas, null],
    api: {
      uploadIdDocument: async (enrollmentId, body) => { uploads.push(body); return {}; },
    },
  });
  try {
    const tree = harness.render({ enrollmentId: 'enrollment-1', onUploaded: () => {}, onError: () => {} });
    const submitButton = findTree(tree, (node) => node.type === 'Button'
      && collectText(node).includes('확인 요청'));
    await submitButton.props.onClick();

    assert.equal(uploads.length, 1, '업로드가 일어나야 한다');
    const fillRectCalls = fakeCanvas.sequence.filter((c) => c.op === 'fillRect');
    assert.equal(fillRectCalls.length, 1);
    assert.deepEqual(
      fillRectCalls[0].args,
      [90, 390, 450, 90],
      'contain 레터박스를 반영하지 않으면 [90, 360, 450, 60] 이 찍힌다 — 세로로 30px 밀리고 '
      + '30px 짧아 주민등록번호 아랫부분이 그대로 남는다',
    );
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
    // image/* 는 브라우저가 못 그리는 HEIC 까지 통과시켰다(2026-09-14 프로덕션 버그).
    // 서버 ALLOWED_ID_MIME 과 같은 집합만 건다.
    assert.equal(fileInput.props.accept, 'image/jpeg,image/png,image/webp');
    assert.equal(fileInput.props.capture, 'environment');
  } finally {
    await harness.close();
  }
});

// ── ModelRegister.jsx 배선 잠금 (리뷰 IMPORTANT 3) ──────────────────────────
// 이 파일은 JSX 를 포함해 plain node 로 직접 import 할 수 없다(이 레포는 JSX 트랜스폼 없이
// `node --test` 를 돈다) — 그래서 아래는 이 레포의 기존 관례(예:
// facemarket-biometric-enrollment.test.mjs 의 "createEnrollment carries identityMethod...",
// identity-scope.test.mjs 의 "콘티보드가 범위를 실제로 쓴다")를 그대로 따라 readFileSync +
// 정규식으로 소스를 직접 대조한다. 각 테스트가 정확히 어떤 회귀를 잡는지 주석에 적는다.
const modelRegisterSource = readFileSync(
  new URL('../../src/features/model/ModelRegister.jsx', import.meta.url), 'utf8',
);

// #285/#287 이 위저드를 재구성하면서 OACX 위젯 호출은 ModelRegister.jsx 를 떠나
// src/lib/api/facemarketIdentityWidget.js 로 옮겨 갔다(runCxWidget → runIdentityWidget).
// 인증 수단 분기는 그 모듈에서 검증한다.
const identityWidgetSource = readFileSync(
  new URL('../../src/lib/api/facemarketIdentityWidget.js', import.meta.url), 'utf8',
);

test('runIdentityWidget 은 identityMethod 로 ENT_MID/ENT_SIMPLE_AUTH·설정 URL을 가른다', () => {
  // 잡는 회귀: isSimpleAuth 판정 조건이 바뀌거나 없어짐.
  assert.match(identityWidgetSource, /const isSimpleAuth = identityMethod === 'simple_auth';/);
  // 잡는 회귀: 간편인증 분기가 ENT_MID 로, 또는 반대로 바뀜(위젯이 카테고리를 강제하는
  // 실측 제약이 깨진다).
  assert.match(identityWidgetSource, /\{ contentInfo: \{ signType: 'ENT_SIMPLE_AUTH' \}, compareCI: false, isBirth: true \}/);
  assert.match(identityWidgetSource, /contentInfo: \{ signType: 'ENT_MID' \}/);
  // 잡는 회귀: configUrl 삼항이 뒤집혀 간편인증이 mid 설정(v1.0 경로)을 타거나 그 반대.
  assert.match(identityWidgetSource, /const configUrl = isSimpleAuth \? CX_AUTH_CONFIG_URL : CX_CONFIG_URL;/);
  // 잡는 회귀: 등록 화면이 수단을 안 넘겨 늘 mid 위젯이 열림.
  assert.match(
    modelRegisterSource,
    /runIdentityWidget\(\{ identityMethod: record\?\.identityMethod \|\| 'mid', signal: controller\.signal \}\)/,
  );
});

test('runIdentityWidget 은 간편인증인데 설정 URL이 없으면 빈 URL로 위젯을 열지 않고 즉시 에러를 던진다', () => {
  // 잡는 회귀: 이 방어 가드가 삭제되면 재개 등록(신규 진입 게이트를 거치지 않는 경로)이
  // OACX.LOAD_MODULE('', …) 을 그대로 호출해 알 수 없는 에러로 실패한다.
  assert.match(
    identityWidgetSource,
    /if \(isSimpleAuth && !CX_AUTH_CONFIG_URL\) \{\s*\n\s*throw new Error\(/,
  );
});

// 삭제된 테스트 두 개(라이브니스 이펙트의 portraitRef 가드 · finishMatch 의 idPhotoHex 생략):
// #285 가 클라이언트 쪽 신분증 초상 릴레이(portraitRef·idPhotoHex·reidentify 화면)를 통째로
// 걷어냈다 — 얼굴 매칭이 FM_FACE_MATCH_ENABLED 로 기본 off 가 되면서 /complete 는 sessionId
// 만 보낸다. 간편인증이 "초상 없이도 진행돼야 한다"는 원래 요구는 그래서 구조적으로 충족돼
// 있다(양쪽 경로 다 초상을 안 쓴다). 그 사실 자체를 아래에서 잠근다.
test('등록 화면은 신분증 초상(dlphotoimage)을 더 이상 클라에서 다루지 않는다', () => {
  for (const source of [modelRegisterSource, identityWidgetSource]) {
    assert.doesNotMatch(source, /dlphotoimage/);
    assert.doesNotMatch(source, /portraitRef/);
  }
  assert.doesNotMatch(modelRegisterSource, /idPhotoHex/);
});

test('startEnrollment 은 mid 가 아닌 identityMethod 만 요청 바디에 싣는다(mid 는 오늘과 바이트 단위로 동일)', () => {
  // 잡는 회귀: 이 스프레드 조건이 사라지거나 'mid' 조건이 빠지면, FM_IDENTITY_METHODS=mid
  // 인 발표/롤백 배포에서도 요청 바디가 오늘과 달라진다(계약: 안 보내던 필드를 보내면 안
  // 된다). 조건이 뒤집히면(정확히 mid 일 때만 보냄) simple_auth 요청에 필드가 아예
  // 빠져 서버가 mid 로 오인한다.
  assert.match(
    modelRegisterSource,
    /\.\.\.\(identityMethod && identityMethod !== 'mid' \? \{ identityMethod \} : \{\}\),/,
  );
});

test('동의 버튼은 메서드가 둘 이상일 때만 선택 화면으로 가고, 하나면 곧장 시작한다', () => {
  // 잡는 회귀: 이 라우팅 조건이 사라지면 단일 메서드(mid) 배포에서도 선택 화면을 거치게
  // 되거나(불필요한 클릭 발생, 브리프가 명시적으로 금지), 반대로 메서드가 둘인데도 선택
  // 화면을 안 거치고 곧장 시작해 버려 사용자가 방법을 고를 기회가 없어진다.
  assert.match(modelRegisterSource, /const chooseMethod = IDENTITY_METHODS\.length > 1;/);
  assert.match(
    modelRegisterSource,
    /action: identityPending \? \(\) => runIdentity\(\) : chooseMethod \? \(\) => setStep\('method'\) : \(\) => startEnrollment\(IDENTITY_METHODS\[0\]\),/,
  );
});

test('IdentityMethodStep 의 onPick 은 매 렌더 새 인라인 함수가 아니라 고정된 콜백이다', () => {
  // 잡는 회귀: onPick 이 인라인 화살표로 바뀌면 참조가 매 렌더 달라져 IdentityMethodStep 의
  // 자동선택 effect 가 부모 리렌더마다 다시 돌 수 있다(중복 등록 생성 위험).
  // 동시에: useCallback 으로 참조를 고정하면서 startEnrollment 자체를 클로저에 굳히면
  // 첫 렌더의 consents(전부 false)를 영원히 보게 돼 '동의했는데 아무 일도 안 일어남'이 된다
  // — 그래서 최신 함수는 ref 로 부른다.
  assert.match(
    modelRegisterSource,
    /const handleMethodPick = useCallback\(\(method\) => startEnrollmentRef\.current\?\.\(method\), \[\]\);/,
  );
  assert.match(modelRegisterSource, /startEnrollmentRef\.current = startEnrollment;/);
  assert.match(modelRegisterSource, /onPick=\{handleMethodPick\}/);
  assert.doesNotMatch(modelRegisterSource, /onPick=\{\(method\) => startEnrollment\(method\)\}/);
});

test('신분증 업로드가 성공하면 부모 에러 배너를 지운다(재시도 성공 후 낡은 메시지가 다음 스텝에 남지 않게)', () => {
  // 잡는 회귀: IdDocumentStep 의 onError 가 부모 setError 를 채운 뒤, 재시도가 성공해도
  // finishIdDocument 가 setError('') 를 안 부르면 다음 화면에서 지난 실패 메시지가 그대로
  // 떠 있는다.
  const start = modelRegisterSource.indexOf('const finishIdDocument = async () => {');
  assert.ok(start >= 0, 'finishIdDocument 정의를 찾을 수 없다');
  const end = modelRegisterSource.indexOf('const refreshReview = useCallback', start);
  assert.ok(end > start, 'finishIdDocument 함수 끝(다음 정의 시작 전)을 찾을 수 없다');
  const body = modelRegisterSource.slice(start, end);
  assert.match(body, /setEnrollment\(current\);/);
  assert.match(body, /setError\(''\);/);
  assert.match(body, /setStep\(screen\.step\);/);
  // 순서: setEnrollment → setError('') → setStep — setError 가 setStep 보다 먼저여야
  // 다음 화면이 그리기 전에 배너가 지워진다.
  assert.ok(
    body.indexOf("setError('');") < body.indexOf('setStep(screen.step);'),
    "setError('') 가 setStep 보다 먼저 일어나야 한다",
  );
});

// ── 심사 대기 화면의 통지·탈출구 (최종리뷰 I2 · I3) ───────────────────────────────

test('review 스텝은 수동 새로고침과 취소 탈출구를 준다', () => {
  // 잡는 회귀: 이 화면은 폴링하지 않는다(사람 심사는 즉시 안 끝난다). 새로고침이 없으면
  // 사용자는 메일이 올 때까지 아무것도 확인할 수 없고, 취소가 없으면 심사가 밀렸을 때
  // 단일 활성 등록 슬롯이 묶인 채 재등록도 못 한다(서버는 review_pending 취소를 이미
  // 허용한다 — 화면에만 길이 없었다).
  assert.match(modelRegisterSource, /const refreshReview = useCallback\(async \(\) => \{/);
  assert.match(modelRegisterSource, /const cancelReview = useCallback\(async \(\) => \{/);
  const start = modelRegisterSource.indexOf("} else if (step === 'review') {");
  assert.ok(start > 0, 'review 스텝 렌더 분기를 찾을 수 없다');
  const block = modelRegisterSource.slice(start, modelRegisterSource.indexOf("} else if (step === '2') {", start));
  assert.match(block, /action: refreshReview/, '새로고침 동작이 없다');
  assert.match(block, /action: cancelReview/, '취소 동작이 없다');
  assert.match(block, /결과는 메일로 알려 드려요/, '메일 통지 약속 문구가 사라졌다');
  assert.match(block, /\$\{REVIEW_DEADLINE_DAYS\}일이 지나면 자동으로 종료/, '심사 기한 안내가 없다');
  // review_pending 을 '끝났다'로 해석하면 증서도 없이 축하 화면이 뜬다.
  const slots = readFileSync(
    new URL('../../src/features/model/registerSlots.js', import.meta.url), 'utf8',
  );
  assert.match(slots, /if \(status === 'review_pending'\) return \{ step: 'review', sub: 4 \};/);
  assert.match(slots, /if \(status === 'id_capture_pending'\) return \{ step: 'id_capture', sub: 1 \};/);
  assert.doesNotMatch(slots, /\['passed', 'review_pending'\]\.includes\(status\)/);
});

test('심사 기한 상수가 서버(REVIEW_DEADLINE_DAYS)와 같다', () => {
  const server = readFileSync(
    new URL('../../server/app/facemarket_enrollment.py', import.meta.url), 'utf8',
  );
  const serverDays = /REVIEW_DEADLINE_DAYS = (\d+)/.exec(server)?.[1];
  const clientDays = /const REVIEW_DEADLINE_DAYS = (\d+);/.exec(modelRegisterSource)?.[1];
  assert.ok(serverDays, '서버 상수를 못 찾았다');
  assert.equal(clientDays, serverDays, '화면이 약속한 기한과 서버가 닫는 기한이 다르다');
});

test('심사 결과 사유에 실제 문구가 매핑돼 있다(일반 실패 문구로 새지 않는다)', () => {
  // biometricEnrollment.js 는 SVG 를 import 해서 plain node 로 못 불러온다 — 이 레포의
  // 기존 관례(소스텍스트 단언)를 따른다. 잡는 회귀: 매핑이 빠지면 사용자는 거절·기한초과
  // 모두 "인증을 완료하지 못했어요" 라는 일반 문구만 보고 원인을 끝내 알 수 없다.
  const source = readFileSync(
    new URL('../../src/features/model/biometricEnrollment.js', import.meta.url), 'utf8',
  );
  for (const reason of ['review_rejected', 'review_timeout']) {
    assert.match(
      source,
      new RegExp(`${reason}: '[^']{5,}'`),
      `REASON_COPY 에 ${reason} 문구가 없다`,
    );
  }
});

// ── 막다른 길 세 곳 (최종리뷰 I7 · I9 · I11) ────────────────────────────────────

test('신분증 업로드 409 는 부모의 재조회 경로로 되돌린다(사용자가 갇히지 않게)', async () => {
  const conflict = Object.assign(new Error('신분증을 올릴 수 있는 단계가 아니에요.'), {
    status: 409, code: 'invalid_enrollment_state',
  });
  const stale = [];
  const errors = [];
  const harness = await stepHarness({
    entry: '/src/features/model/IdDocumentStep.jsx',
    initialStates: [
      'rrc', 'blob:fake-preview-url', true,
      { xr: 0.1, yr: 0.6, wr: 0.5, hr: 0.15 }, true, false, '',
    ],
    initialRefs: [
      { naturalWidth: 800, naturalHeight: 600, getBoundingClientRect: () => ({ width: 800, height: 600 }) },
      fakeCanvasElement(),
      null,
    ],
    api: { uploadIdDocument: async () => { throw conflict; } },
  });
  try {
    const tree = harness.render({
      enrollmentId: 'enrollment-1',
      onUploaded: () => {},
      onStale: (e) => stale.push(e),
      onError: (e) => errors.push(e),
    });
    const submitButton = findTree(tree, (node) => node.type === 'Button'
      && collectText(node).includes('확인 요청'));
    await submitButton.props.onClick();
    assert.equal(stale.length, 1, '409 면 부모 재조회 경로(onStale)로 가야 한다');
    assert.deepEqual(errors, [], '409 를 일반 에러 배너로 처리하면 사용자가 이 스텝에 갇힌다');
    // 이 스텝은 성공으로만 빠져나간다 — 화면에 로컬 에러만 남기면 탈출구가 없다.
    assert.equal(harness.runtime.states[6], '', '409 는 로컬 에러 배너로 남기지 않는다');
  } finally {
    await harness.close();
  }
});

test('ModelRegister 는 IdDocumentStep 의 409 를 finishIdDocument 로 연결한다', () => {
  assert.match(modelRegisterSource, /onStale=\{finishIdDocument\}/);
});

test('본인확인 문구가 실제로 열리는 위젯(mid/간편인증)에 맞춰 갈린다', () => {
  // 잡는 회귀: 간편인증 사용자에게 "신분증 인증하기" 라고 적어 두면, 눌렀을 때 열리는
  // PASS·카카오·네이버 창과 화면 설명이 정면으로 어긋난다. (#285 이후 본인확인 버튼은
  // 동의 화면(step '1')의 하단 버튼 하나로 합쳐졌다 — 문구 분기는 그 라벨에 있다.)
  assert.match(
    modelRegisterSource,
    /const isSimpleAuthEnrollment = enrollment\?\.identityMethod === 'simple_auth';/,
  );
  assert.match(
    modelRegisterSource,
    /const verb = isSimpleAuthEnrollment \? '간편인증' : '신분증 인증';/,
  );
  assert.match(modelRegisterSource, /identityPending \? `\$\{verb\}하기`/);
  // 선택 화면의 간편인증 설명은 어떤 인증사가 열리는지 그대로 적는다.
  const methodStep = readFileSync(
    new URL('../../src/features/model/IdentityMethodStep.jsx', import.meta.url), 'utf8',
  );
  assert.match(methodStep, /PASS·카카오·네이버/);
});

test('간편인증 설정이 없으면 등록을 시작하기 전에 막는다(신분증 올린 뒤가 아니라)', () => {
  // 잡는 회귀: 수단이 하나뿐이면(VITE_FM_IDENTITY_METHODS=simple_auth) IdentityMethodStep
  // 이 아예 안 뜨고 startEnrollment 가 곧장 불린다 — 그러면 설정 부재를 runCxWidget 에서야
  // 만나고, 그때는 사용자가 이미 신분증을 찍어 올린 뒤다.
  const start = modelRegisterSource.indexOf('const startEnrollment = async (identityMethod) => {');
  assert.ok(start > 0, 'startEnrollment 를 못 찾았다');
  const body = modelRegisterSource.slice(start, modelRegisterSource.indexOf('const runCxWidget', start));
  assert.match(
    body,
    /if \(identityMethod === 'simple_auth' && SIMPLE_AUTH_UNAVAILABLE_REASON\) \{/,
    'startEnrollment 가 간편인증 설정 부재를 확인하지 않는다',
  );
  assert.ok(
    body.indexOf('SIMPLE_AUTH_UNAVAILABLE_REASON') < body.indexOf('await createEnrollment('),
    '등록을 만든 뒤에 확인하면 이미 늦다',
  );
});
