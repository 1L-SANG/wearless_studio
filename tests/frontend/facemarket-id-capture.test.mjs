import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { modelComponentHarness } from './helpers/facemarketHarness.mjs';
import { CONSENT_VERSION } from '../../src/features/model/registerSlots.js';

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
    server: { middlewareMode: true, hmr: false, watch: null },
    ssr: { noExternal: true, external: ['lucide-react'] },
    esbuild: { jsx: 'automatic' },
    appType: 'custom',
    plugins: [{
      name: 'facemarket-id-capture-test-harness',
      enforce: 'pre',
      resolveId(id) {
        if (id.endsWith('imageTranscode.js')) return '\0fm-id-test-transcode';
        if (id === 'react') return '\0fm-id-test-react';
        if (id === 'react/jsx-dev-runtime' || id === 'react/jsx-runtime') return '\0fm-id-test-jsx';
        if (id === '@/components/ui.jsx') return '\0fm-id-test-ui';
        if (id === '@/lib/api/facemarket.js') return '\0fm-id-test-api';
        if (id.endsWith('.module.css')) return '\0fm-id-test-css';
        if (id.startsWith('@/')) return new URL('../../src/' + id.slice(2), import.meta.url).pathname;
        return null;
      },
      load(id) {
        if (id === '\0fm-id-test-transcode') return `export const toUploadableImage = file => ${access}.api.toUploadableImage ? ${access}.api.toUploadableImage(file) : Promise.resolve(file);`;
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

// ── (d) isMobileLike 힌트: 간편인증은 폰에서만 시작하게 안내한다 (Task 8) ─────────
// IdentityMethodStep.jsx 는 isMobileLike() 를 인자 없이 호출해 실행 시점의 실제 window
// 전역을 본다 — 그래서 여기서는(unit 테스트처럼 인자로 넣는 대신) globalThis.window 를
// 직접 몽키패치해 컴포넌트가 실제로 그 전역을 읽는지까지 통합 검증한다. 각 테스트가 끝나면
// 원래 상태로 되돌려(finally) 같은 파일의 다른 테스트로 전역이 새지 않게 한다.
function withWindow(windowLike, fn) {
  const hadOwn = Object.prototype.hasOwnProperty.call(globalThis, 'window');
  const original = globalThis.window;
  if (windowLike === undefined) delete globalThis.window;
  else globalThis.window = windowLike;
  return Promise.resolve().then(fn).finally(() => {
    if (hadOwn) globalThis.window = original; else delete globalThis.window;
  });
}

test('거친 포인터(폰)면 간편인증이 선택 가능하다', async () => {
  const harness = await stepHarness({ entry: '/src/features/model/IdentityMethodStep.jsx' });
  try {
    await withWindow({ matchMedia: () => ({ matches: true }) }, () => {
      const tree = harness.render({ methods: ['mid', 'simple_auth'], onPick: () => {} });
      const simpleAuthButton = findTree(tree, (node) => node.type === 'button'
        && collectText(node).includes('간편인증으로 확인'));
      assert.ok(simpleAuthButton, '간편인증 버튼을 찾을 수 없다');
      assert.equal(simpleAuthButton.props.disabled, false, 'coarse pointer 면 막으면 안 된다');
    });
  } finally {
    await harness.close();
  }
});

test('정밀 포인터(PC)면 간편인증을 막고 폰에서 진행하라고 안내한다', async () => {
  const harness = await stepHarness({ entry: '/src/features/model/IdentityMethodStep.jsx' });
  try {
    await withWindow({ matchMedia: () => ({ matches: false }) }, () => {
      const tree = harness.render({ methods: ['mid', 'simple_auth'], onPick: () => {} });
      const simpleAuthButton = findTree(tree, (node) => node.type === 'button'
        && collectText(node).includes('간편인증으로 확인'));
      assert.ok(simpleAuthButton, '간편인증 버튼을 찾을 수 없다');
      assert.equal(simpleAuthButton.props.disabled, true, 'coarse pointer 가 없으면 막아야 한다');
      assert.match(collectText(simpleAuthButton), /휴대폰에서 진행해 주세요/, '왜 막혔는지 알려줘야 한다');
      assert.match(collectText(simpleAuthButton), /이어져요/, '진행 상황이 안 사라진다는 안심 문구가 있어야 한다(핸드오프를 만드는 대신 GET /enrollments/current 이어받기를 안내)');
    });
  } finally {
    await harness.close();
  }
});

test('matchMedia 를 못 구하면(구형 브라우저·window 부재) 간편인증을 막지 않는다(fail open)', async () => {
  const harness = await stepHarness({ entry: '/src/features/model/IdentityMethodStep.jsx' });
  try {
    await withWindow(undefined, () => {
      const tree = harness.render({ methods: ['mid', 'simple_auth'], onPick: () => {} });
      const simpleAuthButton = findTree(tree, (node) => node.type === 'button'
        && collectText(node).includes('간편인증으로 확인'));
      assert.ok(simpleAuthButton, '간편인증 버튼을 찾을 수 없다');
      assert.equal(simpleAuthButton.props.disabled, false, '판별이 불확실하면 허용 쪽으로 접어야 한다');
    });
    await withWindow({}, () => {
      const tree = harness.render({ methods: ['mid', 'simple_auth'], onPick: () => {} });
      const simpleAuthButton = findTree(tree, (node) => node.type === 'button'
        && collectText(node).includes('간편인증으로 확인'));
      assert.equal(simpleAuthButton.props.disabled, false, 'matchMedia 가 없는 window 도 fail open 이어야 한다');
    });
  } finally {
    await harness.close();
  }
});

test('기기 판별과 무관하게 모바일 신분증(mid)은 세 경우 모두 그대로 선택 가능하다', async () => {
  // mid 는 애초에 simpleAuthReason 을 전혀 참조하지 않는다 — 이 테스트는 그 무관함을
  // "코드를 안 읽는다"가 아니라 세 device 상태 각각에서 렌더+클릭까지 직접 증명한다.
  const harness = await stepHarness({ entry: '/src/features/model/IdentityMethodStep.jsx' });
  try {
    const cases = [
      { label: 'coarse(폰)', windowLike: { matchMedia: () => ({ matches: true }) } },
      { label: 'fine(PC)', windowLike: { matchMedia: () => ({ matches: false }) } },
      { label: 'window 없음', windowLike: undefined },
    ];
    for (const { label, windowLike } of cases) {
      // eslint-disable-next-line no-loop-func
      await withWindow(windowLike, () => {
        const picked = [];
        const tree = harness.render({ methods: ['mid', 'simple_auth'], onPick: (m) => picked.push(m) });
        const midButton = findTree(tree, (node) => node.type === 'button'
          && collectText(node).includes('모바일 신분증으로 확인'));
        assert.ok(midButton, `mid 버튼을 찾을 수 없다 (${label})`);
        assert.equal(Boolean(midButton.props.disabled), false, `mid 는 disabled 가 아니어야 한다 (${label})`);
        midButton.props.onClick();
        assert.deepEqual(picked, ['mid'], `mid 클릭이 그대로 onPick('mid') 로 이어져야 한다 (${label})`);
      });
    }
  } finally {
    await harness.close();
  }
});

const modelRegisterSource = readFileSync(
  new URL('../../src/features/model/ModelRegister.jsx', import.meta.url), 'utf8',
);
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

// ── 심사 대기 화면의 통지·탈출구 (최종리뷰 I2 · I3) ───────────────────────────────

test('옛 review_pending 행은 등록 마무리 대기 화면으로 복원해요', () => {
  const slots = readFileSync(new URL('../../src/features/model/registerSlots.js', import.meta.url), 'utf8');
  assert.match(slots, /status === 'review_pending'[^\n]*step: 'processing'/);
  assert.doesNotMatch(modelRegisterSource, /검수 중이에요|REVIEW_DEADLINE_DAYS|refreshReview|cancelReview/);
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

// ── 수단이 하나뿐이면 선택 화면이 안 뜬다 → 기기 힌트도 시작 전에 막아야 한다 ────────
// (Task 8 리뷰 IMPORTANT) IdentityMethodStep.jsx 의 isMobileLike() 체크는 그 화면 안에서만
// 산다. VITE_FM_IDENTITY_METHODS=simple_auth 처럼 수단이 하나뿐이면 그 화면 자체가 안 뜨고
// (useEffect 가 onPick 을 곧장 부른다) startEnrollment 가 곧장 불린다 — 그러면 PC 사용자가
// 기기 힌트를 한 번도 못 보고 곧장 위젯을 연다. 위의 SIMPLE_AUTH_UNAVAILABLE_REASON 가드와
// 정확히 같은 구조적 이유로, 같은 자리에 두 번째 가드가 있어야 한다.
//
// ModelRegister.jsx 는 JSX 를 포함해 plain node 로 직접 import·렌더할 수 없어(파일 상단
// 관례 그대로) 아래도 readFileSync + 정규식으로 source 를 직접 대조한다. 대신 여기서 쓰는
// isMobileLike() 자체의 네 가지 동작(coarse→true, fine→false, matchMedia 없음→true,
// window 없음→true)은 identity-method-config.test.mjs 가 이미 함수 단위로 증명해 뒀다 —
// 아래 테스트들은 "그 함수를 뒤집지 않고, mid 로 새지 않게, createEnrollment 전에" 정확히
// 그대로 불러 쓰는지만 source 레벨에서 고정한다(함수 동작 증명 + 배선 증명 = 합쳐서
// 시나리오 전체 증명).
function startEnrollmentBody() {
  const start = modelRegisterSource.indexOf('const startEnrollment = async (identityMethod) => {');
  assert.ok(start > 0, 'startEnrollment 를 못 찾았다');
  // 기존 IMPORTANT 3 테스트의 'const runCxWidget' 종료 마커는 #285/#287 리네임(runCxWidget →
  // runIdentityWidget) 이후 더 이상 소스에 없어(더 아래까지 슬라이스됨) — 여기서는 실제로
  // startEnrollment 바로 다음 줄에 있는 startEnrollmentRef 선언을 경계로 써서 함수 본문만
  // 정확히 잘라낸다.
  const end = modelRegisterSource.indexOf('const startEnrollmentRef = useRef(null);', start);
  assert.ok(end > start, 'startEnrollment 함수 끝(startEnrollmentRef 선언)을 찾을 수 없다');
  return modelRegisterSource.slice(start, end);
}

test('간편인증이 기기 힌트로 막히면(수단이 하나뿐인 배포에서도) 시작 전에 막는다', () => {
  const body = startEnrollmentBody();
  assert.match(
    body,
    /if \(identityMethod === 'simple_auth' && !isMobileLike\(\)\) \{/,
    'startEnrollment 가 기기 힌트를 확인하지 않는다 — 수단이 하나뿐인 배포에서 PC 사용자가 그대로 위젯을 연다',
  );
  assert.match(
    body,
    /setError\(SIMPLE_AUTH_DEVICE_REASON\);/,
    '기기 힌트로 막을 때 IdentityMethodStep 과 같은 문구(SIMPLE_AUTH_DEVICE_REASON)를 보여줘야 한다',
  );
  assert.ok(
    body.indexOf('!isMobileLike()') < body.indexOf('await createEnrollment('),
    '등록을 만든 뒤에 확인하면 이미 늦다 — 신분증 촬영까지 다 끝난 뒤 막히는 것과 같은 문제가 재발한다',
  );
});

test('기기 힌트 가드는 isMobileLike/SIMPLE_AUTH_DEVICE_REASON 을 새로 만들지 않고 그대로 재사용한다', () => {
  // 잡는 회귀: 이 가드가 identityMethodConfig.js 의 공유 헬퍼·문구를 안 쓰고 로컬로
  // 다시 정의하면, 언젠가 IdentityMethodStep.jsx 쪽만 고쳐지고 여기는 안 고쳐져 말이 갈린다.
  assert.match(
    modelRegisterSource,
    /import \{ deriveSimpleAuthUnavailableReason, isMobileLike, parseIdentityMethods, SIMPLE_AUTH_DEVICE_REASON \} from '\.\/identityMethodConfig\.js';/,
  );
});

test('기기 힌트 가드는 mid 로 새지 않는다(simple_auth 에만 걸린다)', () => {
  // 잡는 회귀: identityMethod === 'simple_auth' 조건이 빠지면 mid 단일 배포에서도(발표/
  // 롤백 모드) 이 가드가 걸려, PC 로 접속한 정상적인 mid 사용자까지 막아 버린다.
  const body = startEnrollmentBody();
  assert.doesNotMatch(
    body,
    /if \(!isMobileLike\(\)\) \{/,
    'identityMethod 조건 없이 isMobileLike() 만 보면 mid 까지 막아 버린다',
  );
});

test('기기 힌트 가드는 방향이 뒤집히지 않았다(!isMobileLike, isMobileLike 단독 아님)', () => {
  // 잡는 회귀: '!' 가 빠지면(fail closed 로 뒤집히면) coarse pointer(폰)에서도 간편인증이
  // 막혀 버린다 — 반대로 폰이 아닌데 통과시키는 것보다 더 나쁘다(정상 사용자를 막음).
  const body = startEnrollmentBody();
  assert.doesNotMatch(
    body,
    /if \(identityMethod === 'simple_auth' && isMobileLike\(\)\) \{\s*\n\s*setError\(SIMPLE_AUTH_DEVICE_REASON\)/,
    '부정(!)이 빠지면 방향이 뒤집혀 폰 사용자까지 막는다',
  );
});

const idEntry = '/src/features/model/IdDocumentStep.jsx';
const findButton = (tree, label) => findTree(tree, n => n.type === 'button' && collectText(n) === label);
const camera = tree => findTree(tree, n => n.type?.name === 'IdCameraCapture');
const reviewBlob = new Blob(['masked'], { type: 'image/jpeg' });
const testMaskRegion = { xr: .3, yr: .4, wr: .25, hr: .05 };
const reviewStates = () => ['review', 'blob:raw', reviewBlob, false, '', true, 'blob:review', testMaskRegion, true];
const idEnrollment = { id: 'e1', status: 'id_capture_pending', identityMethod: 'simple_auth', photos: [], consentDocumentVersion: CONSENT_VERSION, termsConsentVersion: CONSENT_VERSION };

test('촬영 원본은 업로드할 수 없고 직접 가린 결과를 확인한 뒤에만 전송해요', async () => {
  const uploads = [];
  const h = await stepHarness({ entry: idEntry, api: { uploadIdDocument: async (...args) => uploads.push(args) } });
  const props = { enrollmentId: 'e1' };
  const raw = new Blob(['raw-id'], { type: 'image/jpeg' });
  const masked = new Blob(['opaque-masked-id'], { type: 'image/jpeg' });
  const region = { xr: .3, yr: .4, wr: .25, hr: .05 };
  try {
    camera(h.render(props)).props.onCaptured(raw);
    let tree = h.render(props);
    const editor = findTree(tree, n => n.type?.name === 'IdMaskEditor');
    assert.ok(editor, '촬영 뒤 필수 가림 단계를 열어야 해요');
    assert.equal(findButton(tree, '이 사진으로 확인 요청'), null);
    assert.equal(h.runtime.states[2], null, '원본을 업로드할 blob에 담지 않아요');
    editor.props.onMasked(masked, region);
    tree = h.render(props);
    findTree(tree, n => n.type === 'img').props.onLoad();
    tree = h.render(props);
    assert.equal(findButton(tree, '이 사진으로 확인 요청').props.disabled, true);
    await findButton(tree, '이 사진으로 확인 요청').props.onClick();
    assert.deepEqual(uploads, []);
    findTree(tree, n => n.props.id === 'id-mask-confirmed').props.onChange({ target: { checked: true } });
    tree = h.render(props);
    assert.equal(findButton(tree, '이 사진으로 확인 요청').props.disabled, false);
    await findButton(tree, '이 사진으로 확인 요청').props.onClick();
    assert.deepEqual(uploads, [['e1', { file: masked, documentType: 'rrc', maskedConfirmed: true, maskRegion: region }]]);
    findButton(h.render(props), '다시 가리기').props.onClick();
    assert.equal(findButton(h.render(props), '이 사진으로 확인 요청'), null);
    assert.equal(h.runtime.states[2], null);
  } finally { await h.close(); }
});

test('FIX1 신분증 업로드 응답으로 사진 화면을 열고 재조회 없이 이전 오류를 지워요', async () => {
  let lookups = 0;
  const parent = await modelComponentHarness({ initialStates: ['id_capture', idEnrollment, 1, '이전 실패'], api: {
    getEnrollment: async () => { lookups++; throw new Error('등록 상태 조회 실패'); },
  } });
  const uploaded = { ...idEnrollment, status: 'photos_pending' };
  const child = await stepHarness({ entry: idEntry, initialStates: reviewStates(), api: { uploadIdDocument: async () => uploaded } });
  try {
    const props = findTree(parent.render(), n => n.type?.name === 'IdDocumentStep').props;
    await findButton(child.render(props), '이 사진으로 확인 요청').props.onClick();
    assert.equal(parent.runtime.states[0], '2');
    assert.deepEqual(parent.runtime.states[1], uploaded);
    assert.equal(parent.runtime.states[3], '');
    assert.equal(lookups, 0);
  } finally { await child.close(); await parent.close(); }
});

for (const path of ['conflict', 'missing-response']) {
  test(`FIX1 ${path} 재조회 실패는 열린 촬영 시트에 보이고 재시도할 수 있어요`, async () => {
    let lookups = 0;
    const parent = await modelComponentHarness({ initialStates: ['id_capture', idEnrollment], api: {
      getEnrollment: async () => { lookups++; if (lookups === 1) throw new Error('등록 상태 조회 실패'); return { ...idEnrollment, status: 'photos_pending' }; },
    } });
    const child = await stepHarness({ entry: idEntry, initialStates: reviewStates(), api: { uploadIdDocument: async () => {
      if (path === 'conflict') throw Object.assign(new Error('단계가 바뀌었어요'), { status: 409 });
    } } });
    try {
      const props = findTree(parent.render(), n => n.type?.name === 'IdDocumentStep').props;
      await findButton(child.render(props), '이 사진으로 확인 요청').props.onClick();
      const dialog = findTree(child.render(props), n => n.props.role === 'dialog');
      assert.ok(dialog);
      assert.equal(collectText(findTree(dialog, n => n.props.role === 'alert')), '등록 상태 조회 실패');
      assert.equal(parent.runtime.states[0], 'id_capture');
      assert.equal(parent.runtime.states[3], '');
      assert.equal(findButton(dialog, '이 사진으로 확인 요청').props.disabled, false);
      await findButton(dialog, '이 사진으로 확인 요청').props.onClick();
      assert.equal(parent.runtime.states[0], '2');
      assert.equal(lookups, 2);
    } finally { await child.close(); await parent.close(); }
  });
}

test('R2 간편인증 선택지에는 테스트 환경 안내가 있어요', async () => {
  const h = await stepHarness({ entry: '/src/features/model/IdentityMethodStep.jsx' });
  try {
    const tree = h.render({ methods: ['mid', 'simple_auth'], simpleAuthUnavailableReason: '설정 중이에요' });
    assert.match(collectText(tree), /테스트 환경이라 이미지가 깨질 수 있어요./);
    assert.ok(findTree(tree, n => n.props.className === 'methodChoiceNotice'));
  } finally { await h.close(); }
});

test('신분증 선택 전에는 가림 확인을 받지 않고 촬영 후 필수 단계를 안내해요', async () => {
  const h = await stepHarness({ entry: idEntry, initialStates: ['choose'] });
  try {
    const tree = h.render();
    assert.doesNotMatch(collectText(tree), /가렸어요|가리기|주민등록증|꼭 확인|수동/);
    assert.equal(findTree(tree, n => n.type === 'input' && n.props.type === 'checkbox'), null);
    assert.ok(findButton(tree, '카메라로 촬영'));
    assert.ok(findButton(tree, '앨범에서 사진 선택'));
    assert.match(modelRegisterSource, /신분증 이미지는 심사 이후 바로 삭제되며, 다른 어떠한 용도로도 활용되지 않습니다./);
    assert.match(modelRegisterSource, /촬영 후 주민등록번호 뒤 7자리를 가리고 확인해야 올릴 수 있어요/);
  } finally { await h.close(); }
});

test('R6 기본은 전체 화면 카메라이고 촬영 후 검토 전에는 업로드하지 않아요', async () => {
  const uploads = [];
  const h = await stepHarness({ entry: idEntry, api: { uploadIdDocument: async (...args) => uploads.push(args) } });
  const props = { enrollmentId: 'e1' };
  try {
    const tree = h.render(props);
    assert.ok(findTree(tree, n => n.props.role === 'dialog' && n.props['aria-modal']));
    camera(tree).props.onCaptured(reviewBlob);
    assert.equal(h.runtime.states[0], 'mask');
    assert.deepEqual(uploads, []);
    findTree(h.render(props), n => n.type?.name === 'IdMaskEditor').props.onMasked(reviewBlob, testMaskRegion);
    let review = h.render(props);
    assert.ok(findButton(review, '다시 찍기')); assert.ok(findButton(review, '삭제'));
    assert.equal(findButton(review, '이 사진으로 확인 요청').props.disabled, true);
    findTree(review, n => n.type === 'img').props.onLoad();
    findTree(h.render(props), n => n.props.id === 'id-mask-confirmed').props.onChange({ target: { checked: true } });
    review = h.render(props);
    await findButton(review, '이 사진으로 확인 요청').props.onClick();
    assert.equal(uploads.length, 1);
    assert.deepEqual(uploads[0], ['e1', { file: reviewBlob, documentType: 'rrc', maskedConfirmed: true, maskRegion: testMaskRegion }]);
  } finally { await h.close(); }
});

for (const [label, phase] of [['다시 찍기', 'camera'], ['삭제', 'choose']]) {
  test(`R6 ${label}는 업로드 없이 ${phase} 화면으로 가요`, async () => {
    const h = await stepHarness({ entry: idEntry, initialStates: reviewStates(), api: { uploadIdDocument: () => assert.fail('올리지 않아요') } });
    try {
      findButton(h.render(), label).props.onClick();
      assert.equal(h.runtime.states[0], phase);
      assert.equal(h.runtime.states[1], null);
      assert.equal(h.runtime.states[2], null);
    } finally { await h.close(); }
  });
}

for (const reason of ['permission', 'unsupported']) {
  test(`R6 카메라 ${reason} 오류는 앨범 선택 화면에 보여요`, async () => {
    const h = await stepHarness({ entry: idEntry });
    try {
      camera(h.render()).props.onUnavailable(reason);
      const tree = h.render();
      assert.equal(camera(tree), null);
      assert.ok(findButton(tree, '앨범에서 사진 선택'));
      assert.ok(findTree(tree, n => n.props.role === 'alert'));
    } finally { await h.close(); }
  });
}

test('R6 앨범을 열면 카메라를 닫고 HEIC 변환 후 안내 틀에 맞춰요', async () => {
  const original = new File(['heic'], 'id.heic', { type: 'image/heic' });
  const converted = new Blob(['jpeg'], { type: 'image/jpeg' });
  const conversions = [];
  const h = await stepHarness({ entry: idEntry, api: { toUploadableImage: async file => { conversions.push(file); return converted; } } });
  try {
    let clicked = false;
    h.render(); h.runtime.refs[0].current = { click: () => { clicked = true; } };
    camera(h.render()).props.onGallery();
    assert.equal(clicked, true); assert.equal(h.runtime.states[0], 'choose');
    const input = findTree(h.render(), n => n.type === 'input' && n.props.type === 'file');
    assert.equal(input.props.capture, undefined); assert.match(input.props.accept, /image\/heic/);
    const target = { files: [original], value: 'picked' };
    await input.props.onChange({ target });
    assert.equal(target.value, ''); assert.deepEqual(conversions, [original]);
    assert.equal(h.runtime.states[0], 'fit');
    const fit = findTree(h.render(), n => n.type?.name === 'IdGalleryFit');
    assert.ok(fit); fit.props.onCaptured(reviewBlob);
    assert.equal(h.runtime.states[0], 'mask'); assert.equal(h.runtime.states[2], null);
    assert.ok(findTree(h.render(), n => n.type?.name === 'IdMaskEditor'));
  } finally { await h.close(); }
});

test('빈 촬영 결과와 미리보기 실패는 현장에서 오류를 보여요', async () => {
  const h = await stepHarness({ entry: idEntry });
  try {
    camera(h.render()).props.onCaptured(null);
    assert.equal(h.runtime.states[0], 'camera');
    assert.match(collectText(h.render()), /사진을 저장하지 못했어요/);
    camera(h.render()).props.onCaptured(reviewBlob);
    findTree(h.render(), n => n.type?.name === 'IdMaskEditor').props.onMasked(reviewBlob, testMaskRegion);
    findTree(h.render(), n => n.type === 'img').props.onError();
    assert.equal(findButton(h.render(), '이 사진으로 확인 요청').props.disabled, true);
    assert.match(collectText(h.render()), /사진을 열지 못했어요/);
  } finally { await h.close(); }
});

for (const code of ['id_mask_not_applied', 'id_face_not_detected', 'qc_unavailable']) {
  test(`신분증 업로드 ${code} 오류는 검토 사진을 유지하고 재시도할 수 있어요`, async () => {
    const h = await stepHarness({ entry: idEntry, initialStates: reviewStates(), api: { uploadIdDocument: async () => { throw Object.assign(new Error('다시 시도해 주세요.'), { code }); } } });
    try {
      await findButton(h.render({ enrollmentId: 'e1' }), '이 사진으로 확인 요청').props.onClick();
      const tree = h.render({ enrollmentId: 'e1' });
      assert.equal(h.runtime.states[0], 'review');
      assert.ok(findTree(tree, n => n.props.role === 'alert'));
      assert.equal(findButton(tree, '이 사진으로 확인 요청').props.disabled, false);
    } finally { await h.close(); }
  });
}

test('신분증 409 오류는 부모 재조회로 돌려보내요', async () => {
  const conflict = Object.assign(new Error('단계가 바뀌었어요'), { status: 409 });
  const stale = [];
  const h = await stepHarness({ entry: idEntry, initialStates: reviewStates(), api: { uploadIdDocument: async () => { throw conflict; } } });
  try {
    await findButton(h.render({ enrollmentId: 'e1', onStale: error => stale.push(error) }), '이 사진으로 확인 요청').props.onClick();
    assert.deepEqual(stale, [conflict]); assert.equal(h.runtime.states[4], '');
  } finally { await h.close(); }
});

test('업로드 중 중복 클릭과 사진 변경을 막아요', async () => {
  let finish, calls = 0;
  const h = await stepHarness({ entry: idEntry, initialStates: reviewStates(), api: { uploadIdDocument: () => { calls++; return new Promise(resolve => { finish = resolve; }); } } });
  try {
    const upload = findButton(h.render({ enrollmentId: 'e1' }), '이 사진으로 확인 요청').props.onClick;
    const pending = upload(); await upload();
    assert.equal(calls, 1);
    const tree = h.render({ enrollmentId: 'e1' });
    for (const label of ['다시 찍기', '삭제', '올리는 중이에요']) assert.equal(findButton(tree, label).props.disabled, true);
    finish(); await pending;
  } finally { await h.close(); }
});

test('업로드 중 활성 버튼이 없어도 촬영 대화상자 밖으로 초점이 빠지지 않아요', async () => {
  const previous = globalThis.document;
  const listeners = new Map(); let focused = 0, prevented = 0;
  globalThis.document = { activeElement: {}, documentElement: { style: { overflow: 'auto' } }, addEventListener: (key, fn) => listeners.set(key, fn), removeEventListener: key => listeners.delete(key) };
  const h = await stepHarness({ entry: idEntry, initialStates: ['review', 'blob:review', reviewBlob, true, '', true] });
  let cleanup;
  try {
    const tree = h.render();
    const dialog = findTree(tree, n => n.props.role === 'dialog');
    assert.equal(dialog.props.tabIndex, -1);
    h.runtime.refs[1].current = { querySelectorAll: () => [], contains: () => false, focus: () => { focused++; } };
    cleanup = h.runtime.effects[2]();
    assert.equal(document.documentElement.style.overflow, 'hidden');
    listeners.get('keydown')({ key: 'Tab', preventDefault: () => { prevented++; } });
    assert.equal(prevented, 1); assert.equal(focused, 1);
    cleanup(); cleanup = null;
    assert.equal(document.documentElement.style.overflow, 'auto');
  } finally { cleanup?.(); globalThis.document = previous; await h.close(); }
});
