import assert from 'node:assert/strict';
import test from 'node:test';
import { resolve } from 'node:path';
import { createServer } from 'vite';
import { QueryClient } from '@tanstack/react-query';

const required = { terms: 'v1.0', privacy: 'v1.0' };
const missing = { required, accepted: null, needsConsent: true };
const accepted = { required, accepted: { termsVersion: 'v1.0', privacyVersion: 'v1.0', ageAttested: true }, needsConsent: false };
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

function nodes(tree, predicate) {
  if (!tree || typeof tree !== 'object') return [];
  if (Array.isArray(tree)) return tree.flatMap((node) => nodes(node, predicate));
  return [...(predicate(tree) ? [tree] : []), ...nodes(tree.props?.children, predicate)];
}
const checkbox = (tree) => nodes(tree, (node) => node.props?.type === 'checkbox')[0];
const google = (tree) => nodes(tree, (node) => node.props?.className?.includes('google'))[0];
const submit = (tree) => nodes(tree, (node) => node.type === 'Button')[0];
// 심사용 이메일 로그인은 토글이 아니라 '이메일로 로그인하기' 링크를 누른 뒤에만 폼이 그려진다(2026-09-22).
const emailLink = (tree) => nodes(tree, (node) => node.type === 'button' && node.props?.children === '이메일로 로그인하기')[0];
const forms = (tree) => nodes(tree, (node) => node.type === 'form');
function openEmail(h) {
  emailLink(h.login()).props.onClick();
  return h.login();
}

// Execute the real three auth components and consent adapter. As in the enrollment
// harness, hooks retain state/dependencies between renders; cleanup runs before a
// changed effect. Only browser rendering, Supabase, and HTTP are replaced.
async function harness(t, { storage = new Map(), user = null, realHttp = false,
  reviewLogin = true, facemarket = false, admin = false, localSupabase = false, realAccountStore = false } = {}) {
  const key = `__signupTest${Math.random().toString(36).slice(2)}`;
  const previousStorage = globalThis.sessionStorage;
  const previousWindow = globalThis.window;
  const previousFetch = globalThis.fetch;
  const memory = storage;
  globalThis.sessionStorage = {
    getItem: (key) => memory.get(key) ?? null,
    setItem: (key, value) => memory.set(key, String(value)),
    removeItem: (key) => memory.delete(key),
  };
  globalThis.window = Object.assign(new EventTarget(), { location: { origin: 'https://ai.wearless.kr', search: '' } });
  const runtime = {
    frame: null, auth: null, user, listener: null, records: [], requests: [],
    oauth: async () => ({ error: null }),
    password: async () => ({ error: null }),
    get: async (user) => user === 'existing-A' ? accepted : missing,
    post: async () => accepted,
    queryClient: new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  };
  runtime.api = {
    getAccount: async () => ({ id: runtime.user, plan: runtime.user === 'A' ? 'seller' : 'free', credits: 0 }),
    resetInputDraft: async () => {},
  };
  runtime.supabase = { auth: {
    getSession: async () => ({ data: { session: runtime.user ? { user: { id: runtime.user }, access_token: runtime.user } : null } }),
    onAuthStateChange: (listener) => { runtime.listener = listener; return { data: { subscription: { unsubscribe() {} } } }; },
    signInWithOAuth: (...args) => runtime.oauth(...args),
    signInWithPassword: (...args) => runtime.password(...args),
    signOut: async () => { runtime.user = null; runtime.listener?.('SIGNED_OUT', null); return { error: null }; },
  } };
  runtime.http = (path, options = {}) => {
    assert.equal(path, '/v1/me/consents');
    const user = runtime.user;
    runtime.requests.push({ user, ...options });
    if (options.method === 'POST') {
      runtime.records.push({ user, ...options.body });
      return runtime.post(user, options);
    }
    return runtime.get(user, options);
  };
  if (realHttp) globalThis.fetch = async (url, options) => {
    assert.equal(new URL(url, 'https://api.test').pathname, '/v1/me/consents');
    const user = options.headers.Authorization.slice('Bearer '.length);
    const body = options.body ? JSON.parse(options.body) : undefined;
    runtime.requests.push({ user, ...options, body });
    let result;
    if (options.method === 'POST') {
      runtime.records.push({ user, ...body });
      result = await runtime.post(user, options);
    } else {
      result = await runtime.get(user, options);
    }
    return new Response(JSON.stringify(result), { status: 200 });
  };
  globalThis[key] = runtime;
  const access = `globalThis[${JSON.stringify(key)}]`;
  const root = new URL('../..', import.meta.url).pathname;
  const server = await createServer({
    configFile: false, root, logLevel: 'silent', appType: 'custom',
    server: { middlewareMode: true, watch: null, hmr: false }, ssr: { noExternal: true },
    esbuild: { jsx: 'automatic' },
    define: { 'import.meta.env.VITE_SUPABASE_URL': JSON.stringify(localSupabase ? 'http://127.0.0.1:54321' : 'https://auth.example.test') },
    plugins: [{
      name: 'signup-consent-component-harness', enforce: 'pre',
      resolveId(id) {
        const stubs = {
          react: 'react', 'react/jsx-runtime': 'jsx', 'react/jsx-dev-runtime': 'jsx',
          '@/lib/supabase.js': 'supabase', ...(!realHttp ? { '@/lib/api/httpAdapter.js': 'http' } : {}),
          '@/lib/api/index.js': 'mode', '@/components/ui.jsx': 'ui', '@/lib/host.js': 'host',
          '@/lib/tossKeys.js': 'toss',
          '@tanstack/react-query': 'query',
          '@/lib/draftSlot.js': 'draft', '@/lib/appOrigin.js': 'origin',
          ...(!realAccountStore ? { '@/store/useAppStore.js': 'store' } : {}),
        };
        if (stubs[id]) return `\0signup-test-${stubs[id]}`;
        if (id.endsWith('.module.css')) return '\0signup-test-css';
        if (id.startsWith('@/')) return resolve(root, 'src', id.slice(2));
      },
      load(id) {
        if (id === '\0signup-test-react') return `
          const r = ${access};
          const unchanged = (a, b) => a && b && a.length === b.length && b.every((v, i) => Object.is(v, a[i]));
          export const createContext = () => ({ Provider: 'Provider' });
          export const useContext = () => r.auth;
          export const useState = (initial) => {
            const f = r.frame, i = f.cursor++;
            if (!(i in f.hooks)) f.hooks[i] = typeof initial === 'function' ? initial() : initial;
            return [f.hooks[i], value => { f.hooks[i] = typeof value === 'function' ? value(f.hooks[i]) : value; }];
          };
          export const useRef = (initial) => {
            const f = r.frame, i = f.cursor++;
            return f.hooks[i] ||= { current: initial };
          };
          export const useCallback = (value, deps) => {
            const f = r.frame, i = f.cursor++;
            if (!unchanged(f.hooks[i]?.deps, deps)) f.hooks[i] = { value, deps };
            return f.hooks[i].value;
          };
          export const useEffect = (effect, deps) => {
            const f = r.frame, i = f.cursor++;
            if (unchanged(f.hooks[i]?.deps, deps)) return;
            f.effects.push(() => { f.hooks[i]?.cleanup?.(); f.hooks[i] = { deps, cleanup: effect() }; });
          };
        `;
        if (id === '\0signup-test-jsx') return "export const jsx=(type,props)=>({type,props});export const jsxs=jsx;export const jsxDEV=jsx;export const Fragment='Fragment';";
        if (id === '\0signup-test-supabase') return `export const supabase=${access}.supabase;`;
        if (id === '\0signup-test-http') return `export const http=(...args)=>${access}.http(...args); export const resetAnalysisCache=()=>{};`;
        if (id === '\0signup-test-mode') return `export const isMockMode=false; export const api=${access}.api;`;
        if (id === '\0signup-test-query') return `export const useQueryClient=()=>${access}.queryClient;`;
        if (id === '\0signup-test-host') return `export const IS_ADMIN=${admin}; export const IS_FACEMARKET=${facemarket};`;
        if (id === '\0signup-test-toss') return `export const PG_REVIEW_LOGIN_ENABLED=${reviewLogin};`;
        if (id === '\0signup-test-ui') return "export const Modal='Modal';export const Button='Button';export const Icon='Icon';";
        if (id === '\0signup-test-css') return 'export default new Proxy({}, {get: (_, key) => key});';
        if (id === '\0signup-test-draft') return 'export const draftSlot={resetIdentity(){}};';
        if (id === '\0signup-test-origin') return 'export const stampAppOrigin=()=>{};';
        if (id === '\0signup-test-store') return 'export const useAppStore={getState:()=>({beginProject:async()=>{},setAccountIdentity:()=>false})};';
      },
    }],
  });
  const { AuthProvider } = await server.ssrLoadModule('/src/features/auth/AuthProvider.jsx');
  const { LoginGate } = await server.ssrLoadModule('/src/features/auth/Login.jsx');
  const { SignupCompletion } = await server.ssrLoadModule('/src/features/auth/SignupCompletion.jsx');
  const consent = await server.ssrLoadModule('/src/lib/signupConsent.js');
  const http = realHttp ? (await server.ssrLoadModule('/src/lib/api/httpAdapter.js')).http : null;
  const store = realAccountStore ? (await server.ssrLoadModule('/src/store/useAppStore.js')).useAppStore : null;
  const frames = new Map();
  function render(name, component) {
    const frame = frames.get(name) || { hooks: [] };
    frames.set(name, frame);
    frame.cursor = 0; frame.effects = []; runtime.frame = frame;
    const tree = component({});
    if (name === 'auth') runtime.auth = tree.props.value;
    for (const effect of frame.effects) effect();
    return tree;
  }
  const h = {
    runtime, consent, memory, http, store,
    isLoginOpen: () => Boolean(render('auth', AuthProvider).props.children[1]),
    login: () => render('login', LoginGate),
    reopenLogin() {
      for (const hook of frames.get('login')?.hooks || []) hook?.cleanup?.();
      frames.delete('login');
      return h.login();
    },
    completion: () => render('completion', SignupCompletion),
    async flush() {
      for (let i = 0; i < 4; i++) {
        render('auth', AuthProvider);
        if (frames.has('completion')) h.completion();
        await new Promise(setImmediate);
      }
    },
    async user(id) {
      runtime.user = id;
      runtime.listener(id ? 'SIGNED_IN' : 'SIGNED_OUT', id ? { user: { id } } : null);
      await h.flush();
    },
    async signup() {
      nodes(h.login(), (node) => node.props?.role === 'tab')[1].props.onClick();
      checkbox(h.login()).props.onChange({ target: { checked: true } });
      await google(h.login()).props.onClick();
    },
  };
  t.after(async () => {
    runtime.queryClient.clear();
    for (const frame of frames.values()) for (const hook of frame.hooks) hook?.cleanup?.();
    await server.close(); delete globalThis[key];
    globalThis.sessionStorage = previousStorage; globalThis.window = previousWindow;
    globalThis.fetch = previousFetch;
  });
  await h.flush();
  return h;
}

test('email account switch reloads the account and discards the previous subscription cache', async t => {
  const h = await harness(t, { user: 'A', realAccountStore: true });
  await h.store.getState().loadAccount();
  h.runtime.queryClient.setQueryData(['subscription'], { userId: 'A', planCode: 'seller' });
  await h.runtime.auth.signOut();
  await h.flush();
  assert.equal(h.store.getState().account, null);
  assert.equal(h.runtime.queryClient.getQueryData(['subscription']), undefined);
  h.runtime.password = async () => { await h.user('B'); return { error: null }; };
  await h.runtime.auth.signInWithPassword({ email: 'b@example.test', password: 'test-only' });
  await h.store.getState().loadAccount();
  assert.equal(h.store.getState().account.id, 'B');
  assert.equal(h.store.getState().account.plan, 'free');
});

test('an old account response cannot replace the new account after a direct session switch', async t => {
  const h = await harness(t, { user: 'A', realAccountStore: true });
  const oldRequest = deferred();
  h.runtime.api.getAccount = () => h.runtime.user === 'A'
    ? oldRequest.promise : Promise.resolve({ id: 'B', plan: 'free', credits: 0 });
  const oldLoad = h.store.getState().loadAccount();
  await h.user('B');
  await h.store.getState().loadAccount();
  oldRequest.resolve({ id: 'A', plan: 'seller', credits: 1600 });
  assert.equal(await oldLoad, null);
  assert.equal(h.store.getState().account.id, 'B');
});

test('token refresh for the same user keeps the current account and subscription cache', async t => {
  const h = await harness(t, { user: 'A', realAccountStore: true });
  const account = await h.store.getState().loadAccount();
  h.runtime.queryClient.setQueryData(['subscription'], { userId: 'A' });
  h.runtime.listener('TOKEN_REFRESHED', { user: { id: 'A' }, access_token: 'renewed' });
  await h.flush();
  assert.equal(h.store.getState().account, account);
  assert.deepEqual(h.runtime.queryClient.getQueryData(['subscription']), { userId: 'A' });
});

test('a subscription request started by the previous user cannot repopulate the new user cache', async t => {
  const h = await harness(t, { user: 'A', realAccountStore: true });
  const oldRequest = deferred();
  const client = h.runtime.queryClient;
  const oldLoad = client.fetchQuery({ queryKey: ['subscription'], queryFn: () => oldRequest.promise })
    .catch(() => null);
  await h.user('B');
  await client.fetchQuery({ queryKey: ['subscription'], queryFn: async () => ({ userId: 'B', planCode: null }) });
  oldRequest.resolve({ userId: 'A', planCode: 'seller' });
  await oldLoad;
  assert.deepEqual(client.getQueryData(['subscription']), { userId: 'B', planCode: null });
});

test('a failed account request from a signed-out user is ignored after the next login', async t => {
  const h = await harness(t, { user: 'A', realAccountStore: true });
  const oldRequest = deferred();
  h.runtime.api.getAccount = () => h.runtime.user === 'A'
    ? oldRequest.promise : Promise.resolve({ id: 'B', plan: 'free', credits: 0 });
  const oldLoad = h.store.getState().loadAccount();
  await h.user('B');
  await h.store.getState().loadAccount();
  oldRequest.reject(new Error('old session expired'));
  assert.equal(await oldLoad, null);
  assert.equal(h.store.getState().account.id, 'B');
});

test('review password login preserves the purchase destination and does not grant signup consent', async t => {
  const h = await harness(t);
  h.runtime.auth.openLogin('/pricing');
  h.consent.markSignupConsent();
  // 누르기 전엔 폼이 없고 링크만 있다. 누르면 링크가 사라지고 폼 하나가 그 자리에 온다.
  assert.equal(forms(h.login()).length, 0);
  assert.ok(emailLink(h.login()));
  openEmail(h);
  assert.equal(emailLink(h.login()), undefined);
  assert.equal(forms(h.login()).length, 1);
  const input = type => nodes(h.login(), node => node.type === 'input' && node.props.type === type)[0];
  assert.equal(input('email').props.value, '');
  input('email').props.onChange({ target: { value: ' reviewer@example.test ' } });
  input('password').props.onChange({ target: { value: 'test-only-password' } });
  const calls = [];
  h.runtime.password = async credentials => {
    calls.push(credentials);
    await h.user('reviewer');
    return { error: null };
  };
  await nodes(h.login(), node => node.type === 'form')[0].props.onSubmit({ preventDefault() {} });
  assert.deepEqual(calls, [{ email: 'reviewer@example.test', password: 'test-only-password' }]);
  assert.equal(h.runtime.auth.user.id, 'reviewer');
  assert.equal(h.isLoginOpen(), false);
  assert.equal(h.memory.get('wl_postLogin'), '/pricing');
  assert.equal(h.consent.hasFreshSignupConsent(), false);
  assert.deepEqual(h.runtime.records, []);
});

for (const failsWithThrow of [false, true]) {
  test(`review password failure permits retry and keeps purchase intent (throw=${failsWithThrow})`, async t => {
    const h = await harness(t);
    h.runtime.auth.openLogin('/pricing');
    const request = deferred();
    h.runtime.password = () => request.promise;
    assert.equal(forms(h.login()).length, 0);
    openEmail(h);
    const pending = forms(h.login())[0].props.onSubmit({ preventDefault() {} });
    assert.equal(google(h.login()).props.disabled, true);
    assert.equal(nodes(h.login(), node => node.props?.type === 'submit')[0].props.disabled, true);
    if (failsWithThrow) request.reject(new Error('network unavailable'));
    else request.resolve({ error: { message: 'Invalid login credentials' } });
    await pending;
    assert.equal(h.isLoginOpen(), true);
    assert.equal(google(h.login()).props.disabled, false);
    assert.equal(nodes(h.login(), node => node.props?.type === 'submit')[0].props.disabled, false);
    assert.ok(nodes(h.login(), node => node.props?.role === 'alert')[0]);
    assert.equal(h.memory.get('wl_postLogin'), '/pricing');
    assert.equal(h.runtime.auth.session, null);
  });
}

for (const options of [{ reviewLogin: false }, { facemarket: true }, { admin: true }]) {
  test(`review email login stays hidden outside its enabled seller scope: ${JSON.stringify(options)}`, async t => {
    const h = await harness(t, options);
    assert.equal(forms(h.login()).length, 0);
    assert.equal(emailLink(h.login()), undefined);
    assert.ok(google(h.login()));
  });
}

test('review login has no email signup, and switching off review preserves local QA', async t => {
  const h = await harness(t, { reviewLogin: false, localSupabase: true });
  // 로컬 QA 는 링크 없이 처음부터 폼이 펼쳐져 있다.
  assert.equal(forms(h.login()).length, 1);
  assert.equal(emailLink(h.login()), undefined);
  nodes(h.login(), node => node.props?.role === 'tab')[1].props.onClick();
  assert.equal(forms(h.login()).length, 0);
});

test('existing account signup, logout, then unchecked new account never records consent', async (t) => {
  const h = await harness(t);
  await h.signup();
  h.completion();
  await h.user('existing-A');
  await h.runtime.auth.signOut();
  await h.flush();
  await google(h.reopenLogin()).props.onClick();
  await h.user('new-B');
  assert.deepEqual(h.runtime.records, []);
  assert.equal(checkbox(h.completion()).props.checked, false);
  assert.equal(submit(h.completion()).props.disabled, true);
});

for (const response of [accepted, { ...missing, accepted: accepted.accepted }, { ...missing, needsConsent: false }]) {
  test(`confirmed account consumes pending signup intent: ${JSON.stringify(response)}`, async (t) => {
    const h = await harness(t);
    h.runtime.get = async () => response;
    h.consent.markSignupConsent(); h.completion(); await h.user('existing-A');
    assert.equal(h.consent.hasFreshSignupConsent(), false);
    assert.deepEqual(h.runtime.records, []);
  });
}

test('logout clears signup intent independently of consent lookup', async (t) => {
  const h = await harness(t);
  h.consent.markSignupConsent();
  await h.runtime.auth.signOut();
  assert.equal(h.consent.hasFreshSignupConsent(), false);
});

for (const provider of ['google', 'kakao']) {
  test(`back navigation after ${provider} unlocks both social buttons and preserves the return destination`, async t => {
    const h = await harness(t);
    const social = (tree, name) => nodes(tree, node => node.props?.className?.includes(name))[0];
    const calls = [];
    h.runtime.oauth = async ({ provider }) => { calls.push(provider); return { error: null }; };
    h.runtime.auth.openLogin('/create/storyboard');
    await social(h.login(), provider).props.onClick();
    assert.equal(google(h.login()).props.disabled, true);
    window.dispatchEvent(Object.assign(new Event('pageshow'), { persisted: true }));
    assert.equal(google(h.login()).props.disabled, false);
    assert.equal(social(h.login(), 'kakao').props.disabled, false);
    assert.equal(h.memory.get('wl_postLogin'), '/create/storyboard');
    await social(h.login(), provider === 'google' ? 'kakao' : 'google').props.onClick();
    assert.equal(calls.length, 2);
  });
}

test('back from signup discards cancelled consent, and an old error cannot unlock a newer login', async t => {
  const h = await harness(t);
  const old = deferred();
  h.runtime.oauth = () => old.promise;
  const oldSignup = h.signup();
  assert.equal(h.consent.hasFreshSignupConsent(), true);
  window.dispatchEvent(Object.assign(new Event('pageshow'), { persisted: true }));
  assert.equal(h.consent.hasFreshSignupConsent(), false);
  h.runtime.oauth = async () => ({ error: null });
  await google(h.login()).props.onClick();
  old.resolve({ error: new Error('cancelled previous request') });
  await oldSignup;
  assert.equal(google(h.login()).props.disabled, true);
  assert.equal(h.consent.hasFreshSignupConsent(), true);
});

test('ordinary pageshow does not unlock an OAuth request or consume its signup consent', async t => {
  const h = await harness(t);
  await h.signup();
  window.dispatchEvent(Object.assign(new Event('pageshow'), { persisted: false }));
  assert.equal(google(h.login()).props.disabled, true);
  assert.equal(h.consent.hasFreshSignupConsent(), true);
});

test('normal login discards an old signup marker but preserves the return destination', async (t) => {
  const h = await harness(t);
  h.runtime.auth.openLogin('/create/storyboard');
  h.consent.markSignupConsent();
  await google(h.login()).props.onClick();
  assert.equal(h.consent.hasFreshSignupConsent(), false);
  assert.equal(h.memory.get('wl_postLogin'), '/create/storyboard');
});

for (const throws of [false, true]) {
  test(`failed signup discards the marker (${throws ? 'rejection' : 'error result'})`, async (t) => {
    const h = await harness(t);
    h.runtime.oauth = async () => { if (throws) throw new Error('OAuth failed'); return { error: new Error('OAuth failed') }; };
    await h.signup();
    assert.equal(h.consent.hasFreshSignupConsent(), false);
    assert.equal(google(h.login()).props.disabled, false);
  });
}

test('checked signup survives OAuth and silently records the required versions and adulthood', async (t) => {
  const h = await harness(t);
  await h.signup();
  assert.equal(h.consent.hasFreshSignupConsent(), true);
  h.completion(); await h.user('new-B');
  assert.deepEqual(h.runtime.records, [{ user: 'new-B', termsVersion: 'v1.0', privacyVersion: 'v1.0', ageAttested: true }]);
  assert.equal(h.completion(), null);
  assert.equal(h.consent.hasFreshSignupConsent(), false);
});

test('an old OAuth failure cannot discard a newer checked signup attempt in the same millisecond', async (t) => {
  const h = await harness(t);
  t.mock.method(Date, 'now', () => 1800000000000);
  const firstOAuth = deferred();
  h.runtime.oauth = () => firstOAuth.promise;
  const firstSignup = h.signup();
  h.reopenLogin();
  h.runtime.oauth = async () => ({ error: null });
  await h.signup();
  firstOAuth.resolve({ error: new Error('cancelled') }); await firstSignup;
  assert.equal(h.consent.hasFreshSignupConsent(), true);
});

test('a consent lookup cannot consume a signup attempt started after that lookup', async (t) => {
  const h = await harness(t);
  const lookup = deferred();
  h.runtime.get = () => lookup.promise;
  h.completion(); await h.user('existing-A');
  h.consent.markSignupConsent();
  lookup.resolve(accepted); await h.flush();
  assert.equal(h.consent.hasFreshSignupConsent(), true);
});

test('failed consent lookup retains the existing non-blocking behavior', async (t) => {
  const h = await harness(t);
  h.runtime.get = async () => { throw new Error('offline'); };
  h.completion(); await h.user('new-B');
  assert.equal(h.completion(), null);
  assert.deepEqual(h.runtime.records, []);
});

test('changing users resets a checked gate and ignores an old manual POST response', async (t) => {
  const h = await harness(t);
  const oldPost = deferred();
  h.runtime.post = () => oldPost.promise;
  h.completion(); await h.user('new-A');
  checkbox(h.completion()).props.onChange({ target: { checked: true } });
  const saving = submit(h.completion()).props.onClick();
  await h.user('new-B');
  assert.equal(checkbox(h.completion()).props.checked, false);
  assert.equal(submit(h.completion()).props.disabled, true);
  h.consent.markSignupConsent();
  oldPost.resolve(accepted); await saving; await h.flush();
  assert.ok(checkbox(h.completion()), 'old response must not dismiss the new user gate');
  assert.equal(h.consent.hasFreshSignupConsent(), true, 'old save must not clear the next signup attempt');
  assert.equal(h.runtime.requests.find((request) => request.method === 'POST').signal?.aborted, true);
});

test('old automatic POST cannot hide the next account gate or consume its signup marker', async (t) => {
  const h = await harness(t);
  const oldPost = deferred();
  h.runtime.post = () => oldPost.promise;
  h.consent.markSignupConsent(); h.completion(); await h.user('new-A');
  await h.user('new-B');
  assert.equal(h.runtime.records.filter((record) => record.user === 'new-B').length, 0);
  h.consent.markSignupConsent();
  oldPost.resolve(accepted); await h.flush();
  assert.ok(checkbox(h.completion()));
  assert.equal(h.consent.hasFreshSignupConsent(), true);
  assert.equal(h.runtime.requests.find((request) => request.method === 'POST').signal?.aborted, true);
});

for (const intermediateLogout of [true, false]) {
  test(`another tab switching accounts cannot reuse pending signup consent (${intermediateLogout ? 'logout then login' : 'direct account change'})`, async (t) => {
    const h = await harness(t);
    const oldGet = deferred();
    h.runtime.get = (user) => user === 'new-A' ? oldGet.promise : Promise.resolve(missing);
    await h.signup(); h.completion(); await h.user('new-A');
    if (intermediateLogout) await h.user(null);
    await h.user('new-B');
    assert.deepEqual(h.runtime.records, []);
    assert.equal(checkbox(h.completion()).props.checked, false);
    assert.equal(submit(h.completion()).props.disabled, true);
    oldGet.resolve(missing); await h.flush();
    assert.deepEqual(h.runtime.records, []);
  });
}

test('a failed lookup cannot leave the previous account signup consent available to another account', async (t) => {
  const h = await harness(t);
  h.runtime.get = async (user) => { if (user === 'new-A') throw new Error('offline'); return missing; };
  await h.signup(); h.completion(); await h.user('new-A');
  await h.user('new-B');
  assert.deepEqual(h.runtime.records, []);
  assert.equal(checkbox(h.completion()).props.checked, false);
});

test('a page reload with a different account cannot reuse the previous account signup consent', async (t) => {
  const h = await harness(t);
  h.runtime.get = () => deferred().promise;
  await h.signup(); h.completion(); await h.user('new-A');
  // New modules and component state, only browser sessionStorage survives reload.
  const reloaded = await harness(t, { storage: new Map(h.memory), user: 'new-B' });
  reloaded.completion(); await reloaded.flush();
  assert.deepEqual(reloaded.runtime.records, []);
  assert.equal(checkbox(reloaded.completion()).props.checked, false);
});

test('a newly checked signup replaces the old account binding while its POST is pending', async (t) => {
  const h = await harness(t);
  t.mock.method(Date, 'now', () => 1800000000000);
  const oldPost = deferred(), newPost = deferred();
  h.runtime.post = (user) => user === 'new-A' ? oldPost.promise : newPost.promise;
  await h.signup(); h.completion(); await h.user('new-A');
  h.reopenLogin(); await h.signup(); await h.user('new-B');
  assert.deepEqual(h.runtime.records.map((record) => record.user), ['new-A', 'new-B']);
  oldPost.resolve(accepted); await h.flush();
  assert.equal(h.consent.hasFreshSignupConsent(), true);
  newPost.resolve(accepted); await h.flush();
  assert.equal(h.consent.hasFreshSignupConsent(), false);
  assert.equal(h.completion(), null);
});

test('old GET response cannot replace the next account consent gate', async (t) => {
  const h = await harness(t);
  const oldGet = deferred();
  h.runtime.get = (user) => user === 'new-A' ? oldGet.promise : Promise.resolve(missing);
  h.completion(); await h.user('new-A'); await h.user('new-B');
  oldGet.resolve(accepted); await h.flush();
  assert.ok(checkbox(h.completion()));
});

test('HTTP rejects a changed bearer account before sending consent', async (t) => {
  const h = await harness(t, { user: 'new-B', realHttp: true });
  await assert.rejects(h.http('/v1/me/consents', {
    method: 'POST', expectedUserId: 'new-A', body: { ageAttested: true },
  }), { code: 'auth_user_changed' });
  assert.deepEqual(h.runtime.requests, []);
});

test('HTTP sends consent using the same session that passed the account check', async (t) => {
  const h = await harness(t, { user: 'new-A', realHttp: true });
  let reads = 0;
  h.runtime.supabase.auth.getSession = async () => {
    reads += 1;
    const session = { user: { id: h.runtime.user }, access_token: h.runtime.user };
    h.runtime.user = 'new-B';
    return { data: { session } };
  };
  assert.deepEqual(await h.http('/v1/me/consents', {
    method: 'POST', expectedUserId: 'new-A', body: { ageAttested: true },
  }), accepted);
  assert.equal(reads, 1);
  assert.deepEqual(h.runtime.records, [{ user: 'new-A', ageAttested: true }]);
});

test('signup cannot consent to a changed transport account before its auth notification arrives', async (t) => {
  const h = await harness(t, { realHttp: true });
  const oldGet = deferred();
  h.runtime.get = () => oldGet.promise;
  await h.signup(); h.completion(); await h.user('new-A');
  // Shared Supabase storage changes before BroadcastChannel delivery and React cleanup.
  h.runtime.user = 'new-B';
  oldGet.resolve(missing); await h.flush();
  assert.deepEqual(h.runtime.records, []);
  assert.equal(h.runtime.requests.length, 1, 'only the original account GET was sent');
  await h.user('new-B');
  assert.equal(checkbox(h.completion()).props.checked, false);
});

test('manual consent version conflict still requires a fresh unchecked confirmation', async (t) => {
  const h = await harness(t);
  h.completion(); await h.user('new-B');
  checkbox(h.completion()).props.onChange({ target: { checked: true } });
  h.runtime.post = async () => { throw Object.assign(new Error('version changed'), { status: 409 }); };
  h.runtime.get = async () => ({ ...missing, required: { terms: 'v2.0', privacy: 'v2.0' } });
  await submit(h.completion()).props.onClick(); await h.flush();
  assert.equal(checkbox(h.completion()).props.checked, false);
  assert.equal(submit(h.completion()).props.disabled, true);
  assert.ok(nodes(h.completion(), (node) => node.props?.role === 'alert').length);
  assert.deepEqual(h.runtime.requests.map(({ method, expectedUserId }) => ({
    method: method || 'GET', expectedUserId,
  })), [
    { method: 'GET', expectedUserId: 'new-B' },
    { method: 'POST', expectedUserId: 'new-B' },
    { method: 'GET', expectedUserId: 'new-B' },
  ]);
});
