import test from 'node:test';
import assert from 'node:assert/strict';

const draftModule = () => import('../../src/lib/applyDraft.js');

function storage(t) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage');
  const memory = new Map();
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: {
    getItem: key => memory.get(key) ?? null,
    setItem: (key, value) => memory.set(key, value),
    removeItem: key => memory.delete(key),
  } });
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, 'sessionStorage', previous);
    else delete globalThis.sessionStorage;
  });
  return memory;
}

test('지원 초안은 사용자별로 저장하고 해당 사용자 초안만 지워요', async t => {
  const memory = storage(t);
  const { readApplyDraft, writeApplyDraft, clearApplyDraft } = await draftModule();
  writeApplyDraft('A', { applicantName: '김하나', heightCm: 170, agencyContracted: false, weightKg: null });
  writeApplyDraft('B', { applicantName: '이두리' });
  assert.deepEqual(readApplyDraft('A'), { applicantName: '김하나', heightCm: 170, agencyContracted: false, weightKg: null });
  assert.equal(memory.has('wl_fmApplyDraft:A'), true);
  assert.equal(readApplyDraft('C'), null);
  clearApplyDraft('A');
  assert.equal(readApplyDraft('A'), null);
  assert.deepEqual(readApplyDraft('B'), { applicantName: '이두리' });
});

test('사용자가 없거나 저장된 JSON이 객체가 아니면 초안을 복원하지 않아요', async t => {
  const memory = storage(t);
  const { readApplyDraft, writeApplyDraft, clearApplyDraft } = await draftModule();
  for (const value of [undefined, null, '']) {
    writeApplyDraft(value, { applicantName: '저장하지 않음' });
    clearApplyDraft(value);
    assert.equal(readApplyDraft(value), null);
  }
  assert.equal(memory.size, 0);
  for (const value of ['{broken', '[]', 'null', '123', 'true', '"name"']) {
    memory.set('wl_fmApplyDraft:A', value);
    assert.equal(readApplyDraft('A'), null);
  }
});

test('저장소 접근과 직렬화에 실패해도 지원서 흐름을 막지 않아요', async t => {
  storage(t);
  const { readApplyDraft, writeApplyDraft, clearApplyDraft } = await draftModule();
  const circular = {}; circular.self = circular;
  assert.doesNotThrow(() => writeApplyDraft('A', circular));
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, get() { throw new Error('blocked'); } });
  assert.equal(readApplyDraft('A'), null);
  assert.doesNotThrow(() => writeApplyDraft('A', {}));
  assert.doesNotThrow(() => clearApplyDraft('A'));
  delete globalThis.sessionStorage;
  assert.equal(readApplyDraft('A'), null);
  assert.doesNotThrow(() => writeApplyDraft('A', {}));
  assert.doesNotThrow(() => clearApplyDraft('A'));
});

for (const mock of [false, true]) test(`${mock ? '모의' : '실제'} 로그아웃은 현재 계정의 지원 초안만 지워요`, async t => {
  const memory = storage(t);
  memory.set('wl_fmApplyDraft:A', '{"applicantName":"김하나"}');
  memory.set('wl_fmApplyDraft:B', '{"applicantName":"이두리"}');
  const { readFileSync } = await import('node:fs');
  const { transformWithEsbuild } = await import('vite');
  const { clearApplyDraft } = await draftModule();
  // 실제 Provider를 컴파일하고 인증 서비스와 렌더링 경계만 대체해요.
  const source = readFileSync(new URL('../../src/features/auth/AuthProvider.jsx', import.meta.url), 'utf8')
    .replace(/^import .+;\n/gm, '').replace(/^export /gm, '');
  const { code } = await transformWithEsbuild(source, 'AuthProvider.jsx', {
    jsx: 'transform', define: { 'import.meta.env.DEV': 'true', 'import.meta.env.VITE_API_MODE': JSON.stringify(mock ? 'mock' : 'http') },
  });
  let signedOut = 0;
  const states = [{ user: { id: 'A' } }, false, false, false];
  const bindings = {
    React: { createElement: (type, props) => ({ type, props }) },
    createContext: () => ({ Provider: 'Provider' }),
    useState: () => [states.shift(), () => {}], useCallback: value => value, useEffect() {},
    useQueryClient: () => ({ clear() {} }), IS_FACEMARKET: true,
    supabase: { auth: { signOut: async () => { signedOut += 1; } } },
    clearApplyDraft, clearSignupConsent() {}, draftSlot: { resetIdentity() {} },
    useAppStore: { getState: () => ({ beginProject: async () => {}, setAccountIdentity: () => true }) },
  };
  const AuthProvider = new Function(...Object.keys(bindings), `${code}; return AuthProvider;`)(...Object.values(bindings));
  await AuthProvider({ children: null }).props.value.signOut();
  assert.equal(memory.has('wl_fmApplyDraft:A'), false);
  assert.equal(memory.has('wl_fmApplyDraft:B'), true);
  assert.equal(signedOut, mock ? 0 : 1);
});
