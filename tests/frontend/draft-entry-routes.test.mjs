import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { transformWithEsbuild } from 'vite';
import { getDraftDeviceNoun, isMobileDraftWriter } from '../../src/lib/draftDeviceNoun.js';
import { flowScreenName, shouldWarnFlowAbandon, notifyFlowKept } from '../../src/lib/flowAbandon.js';
import * as entry from '../../src/lib/draftEntry.js';
import * as mobileGuestSave from '../../src/lib/mobileGuestSave.js';
import { checkDraftEntry, canAutoOpenInput } from '../../src/lib/draftEntryCheck.js';
import * as flow from '../../src/lib/flowSession.js';
import { loadDraft as readStoredDraft, clearDraft as clearStoredDraft, hasPendingDraft as hasStoredPendingDraft, saveProductDraft, queueProductDraftSave, flushProductDraftSave } from '../../src/lib/draftStore.js';
import { draftStoreFixture } from './helpers/draft-store-fixture.mjs';
import { createDraftSlotSync, formatDraftRelativeTime, localDraftMeta, draftSlot as storedDraftSlot } from '../../src/lib/draftSlot.js';
import { restoreDraftProduct } from '../../src/features/product-input/draftProductRestore.js';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const app = read('src/apps/seller/App.jsx');
const shell = read('src/features/shell/shell.jsx');
const hook = read('src/lib/useDraftEntryActions.js').replace(/^import .*;\n/gm, '').replace(/^export /gm, '');
const inputSource = app.slice(app.indexOf('function ProductInputRoute()'), app.indexOf("/* '/' 복귀의 리다이렉트"));
const navSource = shell.slice(shell.indexOf('export function TopNav()'), shell.indexOf('/* 로그인 사용자 프로필'))
  .replace(/^export /gm, '');
const dialogSource = read('src/features/shell/DraftEntryDialog.jsx').replace(/^import .*;\n/gm, '').replace(/^export /gm, '');
const profileSource = shell.slice(shell.indexOf('function ProfileMenu()'), shell.indexOf('export function Stepper'));
const rootSource = app.slice(app.indexOf('function RootRedirect()'), app.indexOf('/* 모델 섹션')).replace("import.meta.env.VITE_API_MODE", "'http'");
const tagSource = ['HangTag.jsx'].map((file) =>
  read(`src/features/shell/${file}`).replace(/^import .*;\n/gm, '').replace(/^export /gm, '')).join('\n');
const mobileDialogSource = read('src/features/shell/MobileDraftDialog.jsx').replace(/^import .*;\n/gm, '').replace(/^export /gm, '');
const guardSource = app.slice(app.indexOf('function RequireProject()'), app.indexOf('/* editor 는'));
const statusSource = ['DraftProductRow.jsx', 'HandoffStatusPage.jsx'].map((file) => read(`src/features/shell/${file}`).replace(/^import .*;\n/gm, '').replace(/^export /gm, '')).join('\n');
const authFile = read('src/features/auth/AuthProvider.jsx');
// 버려진 인증 Promise의 실패도 검사하되 테스트 프로세스의 미처리 거절로 번지지 않게 관찰한다.
const authSource = authFile.slice(authFile.indexOf('export function AuthProvider'), authFile.indexOf('export const useAuth')).replace(/^export /gm, '')
  .replaceAll('useAppStore.getState()', 'readAuthFlow()')
  .replace('return publication;', 'return trackPromise(publication);')
  .replace('(async () => {', 'trackPromise((async () => {').replace('    })();', '    })());');
const authGuardSource = read('src/apps/guards.jsx').slice(read('src/apps/guards.jsx').indexOf('export function RequireAuth()')).replace(/^export /gm, '');
const trackerSource = app.slice(app.indexOf('function ResumeTracker()'), app.indexOf("/* '새 제작'"));
const productSource = read('src/features/product-input/ProductInput.jsx');
const productLoad = productSource.slice(productSource.indexOf('  useEffect(() => {\n    let alive = true;\n    (async () => {\n      setLoadError'), productSource.indexOf('  const nextMobile ='));
const { code } = await transformWithEsbuild(`${authSource}\n${authGuardSource}\n${trackerSource}\n${statusSource}\n${mobileDialogSource}\n${guardSource}\n${hook}\n${inputSource}\n${navSource}\n${dialogSource}\n${rootSource}\n${profileSource}\n${tagSource}\nfunction ProductLoad() { ${productLoad} }`, 'DraftEntryRoutes.jsx', {
  jsx: 'transform', define: { 'import.meta.env.DEV': 'false', 'import.meta.env.VITE_API_MODE': "'http'" },
});
const slot = (meta = {}) => ({ meta: { hasContent: true, deviceLabel: 'Android Chrome', photoCount: 2, updatedAt: 'v1', ...meta }, payload: { product: { name: 'remote' } } });

// 실제 라우트와 훅을 실행하고 브라우저, 저장소 경계만 메모리로 대체한다.
function harness({ remote = slot(), confirmed = true, local = null, localReader = null, localPendingReader = null, loading = false, withAuth = false, authUser = 'seller', sessionResult = null, storedSession = null, resetGate = null, resetError = null,
  syncedAt = null, serverSyncedAt = null, syncedUserId = null, otherTab = false, token = null, savedFlow = {}, takeover = null, slotClient = null, mobile = false, mock = false, facemarket = false, guestSave = null, pendingLocal, returnIntent = '/create/storyboard' } = {}) {
  const calls = [];
  const resetOwners = [];
  const windowEvents = new EventTarget();
  const documentEvents = new EventTarget();
  documentEvents.visibilityState = 'visible';
  const toasts = [];
  const frames = new Map();
  let active;
  let cursor;
  let effects = [];
  let layoutEffects = [];
  let dirty = false;
  let staged = null;
  let currentRemote = remote;
  let listener;
  let providerKey;
  let identityEpoch = 0;
  let accountUserId;
  const flowReads = [];
  const products = [];
  let loadError = '';
  let entryFlush = null;
  let publishError = null;
  const publishedSessions = [];
  const auth = { session: { user: { id: 'seller' } }, loading, signingOut: false, openLogin: (path) => calls.push(`login:${path}`) };
  if (withAuth) Object.assign(auth, { session: null, loading: true });
  const location = { key: 'first', pathname: '/library' };
  const state = {
    projectId: confirmed ? 'project' : null, projectPersisted: confirmed,
    productInfoConfirmed: confirmed, projectGeneration: 0, resumePath: '/create/storyboard', ...savedFlow,
    async beginProject({ isCurrent = () => true, onStarted = () => {}, draftOwnerUserId } = {}) {
      if (!isCurrent()) return;
      calls.push('begin');
      resetOwners.push(draftOwnerUserId);
      if (resetError) throw resetError;
      if (resetGate) await resetGate;
      if (!isCurrent()) return;
      local = null;
      onStarted();
      state.projectId = null;
      state.projectPersisted = false;
      state.productInfoConfirmed = false;
      state.projectGeneration += 1;
      flow.clearFlowSession();
      return true;
    },
    setAccountIdentity(id) { const changed = id !== accountUserId; accountUserId = id; state.accountUserId = id; return changed; },
  };
  const storage = new Map();
  globalThis.sessionStorage = { getItem: (k) => storage.get(k), setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) };
  const draftSlot = slotClient || {
    async get() { calls.push('get'); if (currentRemote instanceof Error) throw currentRemote; return currentRemote; },
    async takeover() { calls.push('takeover'); if (takeover) return takeover(); if (currentRemote instanceof Error) throw currentRemote; return currentRemote; },
    resetIdentity() { calls.push('reset'); },
    async removeForNewFlow() { calls.push('remove'); return true; },
    async remove() { calls.push('remove'); },
    stage(value) { calls.push('stage'); staged = value; },
    hasStaged: () => Boolean(staged?.payload),
    discard() {}, pauseEntryWrites() {}, resumeEntryWrites() {}, getIdentityEpoch: () => identityEpoch, hasOtherTabOwner: () => otherTab, getSyncedUserId: () => syncedUserId,
    continueWithoutSlotCheck() { if (token) return false; calls.push('continueUnchecked'); return true; },
    getToken: () => token, hasPendingRemoval: () => false, getSyncedAt: () => syncedAt, getServerSyncedAt: () => serverSyncedAt,
    setUserId(id) { if (id !== accountUserId) identityEpoch += 1; },
    consumeStaged: () => null,
  };
  const toastOptions = [];
  const push = (message, options) => { toasts.push(message); toastOptions.push(options); };
  const useAppStore = (select) => select(state);
  useAppStore.getState = () => {
    if (active?.name !== 'AuthProvider') flowReads.push({ reader: active?.name, projectId: state.projectId });
    return withAuth ? { ...state } : state;
  };
  state.markFlowUsed = () => {};
  state.setResumePath = (path) => { state.resumePath = path; };
  state.adoptProject = (id) => { calls.push(`adopt:${id}`); };
  state.confirmProductInfo = () => { calls.push('confirm'); };
  const bindings = {
    useClientErrorCapture() {}, getMobileGuestDraft: () => null, getMobileGuestSave: (session) => typeof guestSave === 'function' ? guestSave(session) : guestSave, clearMobileGuestSave() { guestSave = null; calls.push('clearGuest'); },
    ...entry, ...flow, canAutoOpenInput, checkDraftEntry: (client, options) => checkDraftEntry(client, { ...options, setTimer: (fn, ms) => ms === 8000 ? setTimeout(fn, ms) : setImmediate(fn), clearTimer: clearTimeout }), getDraftDeviceNoun, isMobileDraftWriter, flowScreenName, shouldWarnFlowAbandon, notifyFlowKept, draftSlot, useAppStore, formatDraftRelativeTime, localDraftMeta,
    flowRouteSeenThisSession: false, flowDocumentEntryId: 'document',
    React: { createElement: (type, props, ...children) => ({ type, props: { ...props, children } }), Fragment: 'fragment' },
    useState(initial) {
      const frame = active;
      const index = cursor++;
      if (!(index in frame)) frame[index] = typeof initial === 'function' ? initial() : initial;
      return [frame[index], (value) => {
        const next = typeof value === 'function' ? value(frame[index]) : value;
        if (!Object.is(next, frame[index])) { frame[index] = next; dirty = true; }
      }];
    },
    useRef(initial) {
      const index = cursor++;
      if (!(index in active)) active[index] = { current: initial };
      return active[index];
    },
    useCallback(value, deps) {
      const index = cursor++;
      if (!active[index] || !deps.every((value, i) => Object.is(value, active[index].deps[i]))) active[index] = { value, deps };
      return active[index].value;
    },
    useEffect(effect, deps) {
      const index = cursor++;
      const prior = active[index];
      if (!prior || !deps.every((value, i) => Object.is(value, prior.deps[i]))) {
        prior?.cleanup?.();
        const record = { deps };
        active[index] = record;
        effects.push(() => { record.cleanup = effect(); });
      }
    },
    useLayoutEffect(effect, deps) {
      const index = cursor++, prior = active[index];
      if (!prior || !deps.every((value, i) => Object.is(value, prior.deps[i]))) {
        const record = { deps }; active[index] = record;
        layoutEffects.push(() => { prior?.cleanup?.(); record.cleanup = effect(); });
      }
    },
    useAuth: () => auth, useToast: () => ({ push }), useLocation: () => location,
    AuthCtx: { Provider: 'Provider' },
    useQueryClient: () => queryClient,
    clientEvents: { setSession(next) {
      if (publishError) { const error = publishError; publishError = null; throw error; }
      publishedSessions.push(next);
    } }, stampAppOrigin() {}, clearApplyDraft() {}, clearSignupConsent() {},
    trackPromise(promise) { promise.catch(() => {}); return promise; },
    FacemarketLoginPrompt: 'FacemarketLoginPrompt', IS_ADMIN: false,
    KAKAO_OIDC_ENABLED: false, LoginGate: 'LoginGate',
    MOCK_FACEMARKET: false, isKakaoCallbackPath: () => false, isPaymentResultPath: () => false, rememberPostLogin() {},
    readStoredAuthSession: () => storedSession,
    readAuthFlow: () => withAuth ? { ...state } : state,
    supabase: { auth: {
      signOut: async () => {},
      getSession: async () => {
        if (sessionResult instanceof Error) throw sessionResult;
        return sessionResult || { data: { session: authUser ? { user: { id: authUser } } : null }, error: null };
      },
      onAuthStateChange(callback) { listener = callback; return { data: { subscription: { unsubscribe() {} } } }; },
    } },
    useNavigate: () => navigate,
    isMockMode: mock, IS_FACEMARKET: facemarket, isMobileDevice: () => mobile,
    hasPendingDraft: localPendingReader || (() => pendingLocal === undefined ? Boolean(local) : typeof pendingLocal === 'function' ? pendingLocal() : pendingLocal), loadDraft: localReader || (async () => local), flushProductDraftSave: async () => entryFlush,
    Navigate: 'Navigate', Outlet: 'Outlet',
    readPostLogin: () => returnIntent, forgetPostLogin: () => calls.push('forget'),
    ensureDraftRemotePhotos: async (draft) => draft,
    promoteDraftToProject: async () => { calls.push('promote'); return { projectId: 'promoted' }; },
    clearDraft: async () => { calls.push('clear'); local = null; }, resetDraftSyncSingleFlight() {},
    DRAFT_SYNC_TIMEOUT_MS: 20000, setTimeout() {},
    ProductInput: 'ProductInput', Modal: 'Modal', Button: 'Button', Icon: 'Icon',
    Stepper: 'Stepper', ProfileMenuIcon: 'ProfileMenuIcon',
    p: new Proxy({}, { get: (_, key) => key }),
    s: new Proxy({}, { get: (_, key) => key }),
    window: windowEvents, document: documentEvents, Link: 'Link', STEPPER_STEPS: [],
    api: {
      async getProduct(id) { calls.push(`product:${id}`); if (id && !auth.session) throw new Error('로그인이 필요해요'); return { colors: [{}] }; },
      async getCatalogs() { return {}; }, async getAnalysis(id) { calls.push(`analysis:${id}`); return { projectId: id }; },
    },
    mobile, setLoadError: (value) => { loadError = value; }, setLoadOriginalError() {}, setCatalogs() {},
    applyDraftPayload: () => false, persistedColorsRef: {}, isAnalysisRunning: () => false, restoreDraftProduct,
    setProduct: (value) => products.push(value), loadAttempt: 0,
  };
  windowEvents.location = { search: '', pathname: '/create/input' };
  windowEvents.scrollTo = () => {};
  documentEvents.querySelector = () => null;
  const queryClient = { clear() {} };
  function navigate(path, options) { calls.push(`navigate:${path}`); if (options?.state) { location.state = options.state; location.key = `nav-${calls.length}`; } }
  const components = new Function(...Object.keys(bindings), `${code}; return { AuthProvider, RequireAuth, ResumeTracker, ProductLoad, HandoffStatusPage, RequireProject, MobileDraftDialog, MobileDraftStatus, DraftProductRow, ProductInputRoute, TopNav, ResumeChoiceModal, DraftEntryDialog, RootRedirect, ProfileMenu, HangTag };`)(...Object.values(bindings));
  function render(name, props) {
    if (!frames.has(name)) frames.set(name, []);
    active = frames.get(name);
    active.name = name;
    cursor = 0;
    dirty = false;
    return components[name](props);
  }
  async function settle(name, props) {
    let tree = render(name, props);
    for (let i = 0; i < 20; i += 1) {
      layoutEffects.splice(0).forEach((effect) => effect());
      const pending = effects;
      effects = [];
      pending.forEach((effect) => effect());
      await new Promise(setImmediate);
      if (!dirty && !effects.length && i >= 6) return tree;
      tree = render(name, props);
    }
    throw new Error('route did not settle');
  }
  async function settleApp(name = 'ProductInputRoute') {
    let tree;
    for (let i = 0; i < 25; i += 1) {
      const provider = render('AuthProvider', { children: 'App' });
      Object.assign(auth, provider.props.value);
      if (providerKey !== provider.props.key) {
        frames.get(name)?.forEach((record) => record?.cleanup?.()); frames.delete(name);
        providerKey = provider.props.key;
      }
      if (provider.props.children[0]) {
        const guard = name === 'RequireProject' ? render('RequireAuth') : null;
        tree = guard && guard.type !== 'Outlet' ? guard : render(name);
      }
      else { frames.get(name)?.forEach((record) => record?.cleanup?.()); frames.delete(name); tree = null; }
      layoutEffects.splice(0).forEach((effect) => effect());
      const pending = effects; effects = []; pending.forEach((effect) => effect());
      await new Promise(setImmediate);
    }
    return tree;
  }
  return { calls, resetOwners, toasts, toastOptions, auth, state, flowReads, products, publishedSessions, get localDraft() { return local; }, get loadError() { return loadError; }, windowEvents, documentEvents, draftSlot, location, components, render, settle, settleApp,
    flushLayout: () => layoutEffects.splice(0).forEach((effect) => effect()),
    emit(event, next) { listener(event, next); },
    get hasListener() { return Boolean(listener); }, setStoredSession(value) { storedSession = value; },
    setEntryFlush(value) { entryFlush = value; },
    failNextPublication(error) { publishError = error; }, setResetError(value) { resetError = value; },
    setRemote(value) { currentRemote = value; }, get staged() { return staged; },
    remount(name) { frames.get(name)?.forEach((record) => record?.cleanup?.()); frames.delete(name); },
  };
}

function find(tree, predicate) {
  if (!tree || typeof tree !== 'object') return null;
  if (predicate(tree)) return tree;
  for (const child of tree.props?.children?.flat(Infinity) || []) {
    const match = find(child, predicate);
    if (match) return match;
  }
  return null;
}

function storedDraftHarness(t, draft, options) {
  const storage = draftStoreFixture(t, draft);
  Object.defineProperty(globalThis, 'sessionStorage', { writable: true });
  const h = harness({ withAuth: true, remote: null, local: draft, localReader: readStoredDraft,
    localPendingReader: hasStoredPendingDraft, slotClient: storedDraftSlot, ...options });
  if (draft) sessionStorage.setItem('wl_draftPending', '1');
  const storeSource = read('src/store/useAppStore.js');
  const begin = storeSource.slice(storeSource.indexOf('  async beginProject('), storeSource.indexOf('  async ensureProject()'));
  const noop = () => {};
  const dependencies = {
    clearDraft: clearStoredDraft, clearDetailPageJobMarker: noop, resetAnalysisCache: noop,
    clearFlowSession: noop, clearDraftPromotionSession: noop, forgetDraftSyncProject: noop,
    api: { resetInputDraft: async () => {} }, get: () => h.state, set: (patch) => Object.assign(h.state, patch),
    initialFlow: { projectId: null, projectPersisted: false, productInfoConfirmed: false, userId: null, resumePath: null },
    initialMannequinJob: () => ({}), initialDetailPageJob: () => ({}), persistFlow: noop,
  };
  h.state.beginProject = new Function(...Object.keys(dependencies),
    `let ensureProjectInflight = null, detailJobSeq = 0, detailJobLoopProjectId = null; return ({ ${begin} }).beginProject;`)(...Object.values(dependencies));
  return { h, storage };
}

const legacyPhotoDraft = () => ({
  product: { name: 'A private input', colors: [{ id: 'base', isBase: true,
    images: [{ id: 'photo', slot: 'Front', src: 'blob:old-front', type: 'image/jpeg' },
      { id: 'back', slot: 'Back', src: 'blob:old-back', type: 'image/jpeg' }] }] },
  photos: [{ imageId: 'photo', colorId: 'base', slot: 'Front', mime: 'image/jpeg',
    blob: new Blob(['A synthetic private photo'], { type: 'image/jpeg' }) },
  { imageId: 'back', colorId: 'base', slot: 'Back', mime: 'image/jpeg',
    blob: new Blob(['A synthetic back photo'], { type: 'image/jpeg' }) }],
  updatedAt: 'legacy-A',
});

async function readRestoredPhoto(draft) {
  const photos = restoreDraftProduct(draft)?.colors[0]?.images || [];
  const photo = photos[0];
  if (!photo?.src?.startsWith('blob:')) return null;
  try { return await (await fetch(photo.src)).text(); }
  finally { photos.forEach((image) => URL.revokeObjectURL(image.src)); }
}

const choice = (h, tree) => find(tree, (node) => node.type === h.components.ResumeChoiceModal);
const dialog = (h, tree) => find(tree, (node) => node.type === h.components.DraftEntryDialog);
async function openNav(h) {
  const nav = await h.settle('TopNav');
  await find(nav, (node) => node.props.children.includes('상세페이지 제작')).props.onClick();
  return h.settle('TopNav');
}

test('C2 actual confirmed route waits for auth and shows flow plus remote before redirecting', async () => {
  const h = harness({ loading: true });
  assert.equal(await h.settle('ProductInputRoute'), null);
  assert.deepEqual(h.calls, []);
  h.auth.loading = false;
  const tree = await h.settle('ProductInputRoute');
  assert.deepEqual(choice(h, tree).props.sources.map((source) => source.id), ['flow', 'remote']);
  assert.deepEqual(h.calls, ['get']);
  await tree.props.onChoose('flow');
  assert.deepEqual(h.calls, ['get', 'navigate:/create/storyboard']);
});

test('C2 checking effect detects a confirmed flow restored after the initial render', async () => {
  const h = harness({ confirmed: false });
  h.render('ProductInputRoute');
  Object.assign(h.state, { projectId: 'restored', projectPersisted: true, productInfoConfirmed: true });
  const tree = await h.settle('ProductInputRoute');
  assert.deepEqual(tree.props.sources.map((source) => source.id), ['flow', 'remote']);
  assert.deepEqual(h.calls, ['get']);
});

for (const remote of [null]) {
  test(`M12 ineligible confirmed input asks on entry and starts new without a lost-work warning`, async () => {
    const h = harness({ remote });
    let tree = await h.settle('ProductInputRoute');
    assert.deepEqual(tree.props.sources.map((source) => source.id), ['flow']);
    assert.deepEqual(h.calls, ['get']);
    h.remount('ProductInputRoute');
    h.location.key = 'second';
    tree = await h.settle('ProductInputRoute');
    assert.deepEqual(tree.props.sources.map((source) => source.id), ['flow']);
    assert.ok(!h.calls.includes('begin'));
    const warning = h.render('ResumeChoiceModal', tree.props);
    find(warning, (n) => n.type === 'Button' && n.props.children.includes('새로 시작하기')).props.onClick();
    tree = await h.settle('ProductInputRoute');
    assert.equal(tree.type, 'ProductInput');
    assert.deepEqual(h.calls, ['get', 'get', 'get', 'remove', 'begin']);
    assert.deepEqual(h.toasts, ['만들던 상세페이지는 보관함의 만드는 중에 있어요']);
    h.toastOptions[0].action.onClick();
    assert.equal(h.calls.at(-1), 'navigate:/library');
  });
}

test('C2 unauthenticated confirmed route resets the expired flow without querying', async () => {
  const h = harness({ withAuth: true, authUser: null });
  const tree = await h.settleApp();
  assert.equal(tree.type, 'ProductInput');
  assert.deepEqual(h.calls, ['begin']);
  assert.equal(h.state.projectId, null);
});

test('C3 actual TopNav selects remote and input mounts directly with staged payload', async () => {
  const h = harness();
  const tree = await openNav(h);
  const modal = choice(h, tree);
  assert.deepEqual(modal.props.sources.map((source) => source.id), ['flow', 'remote']);
  await modal.props.onChoose('remote');
  const input = await h.settle('ProductInputRoute');
  assert.equal(input.type, 'ProductInput');
  assert.equal(h.staged.payload.product.name, 'remote');
  assert.deepEqual(h.calls, ['get', 'get', 'takeover', 'begin', 'stage', 'navigate:/create/input']);
});

test('C3 TopNav without a slot keeps the original resume choice and route', async () => {
  const h = harness({ remote: null });
  const tree = await openNav(h);
  const modal = choice(h, tree);
  assert.equal(modal.props.sources, null);
  modal.props.onResume();
  assert.deepEqual(h.calls, ['get', 'navigate:/create/storyboard']);
});

for (const location of ['input', 'nav', 'local']) {
  test(`C4 actual ${location} choice waits for confirmation before mutating`, async () => {
    const h = harness(location === 'local' ? { confirmed: false, local: {
      updatedAt: 'local', product: { name: 'local', colors: [] },
    } } : {});
    const component = location === 'nav' ? 'TopNav' : 'ProductInputRoute';
    const tree = location === 'nav' ? await openNav(h) : await h.settle(component);
    const modal = choice(h, tree);
    if (location === 'local') await modal.props.onChoose('local');
    else await modal.props.onNew();
    let confirmation = dialog(h, await h.settle(component));
    assert.equal(confirmation.props.dialog.kind, 'confirm');
    assert.deepEqual(h.calls, ['get', 'get']);
    h.setRemote(slot({ updatedAt: 'v2', photoCount: 4 }));
    await confirmation.props.actions.confirm();
    confirmation = dialog(h, await h.settle(component));
    assert.equal(confirmation.props.dialog.meta.photoCount, 4);
    assert.deepEqual(h.calls, ['get', 'get', 'get']);
    await confirmation.props.actions.confirm();
    await h.settle(component);
    assert.deepEqual(h.calls, ['get', 'get', 'get', 'get',
      ...(location === 'local' ? ['takeover', 'stage'] : ['remove', 'begin']),
      ...(location === 'nav' ? ['navigate:/create/input'] : []),
    ]);
  });
}

for (const entryPoint of ['input', 'nav']) {
  test(`stale ${entryPoint} remote card waits for newly pending phone photos without taking ownership`, async () => {
    const h = harness({ remote: slot({ photosPending: false }) });
    const component = entryPoint === 'nav' ? 'TopNav' : 'ProductInputRoute';
    const tree = entryPoint === 'nav' ? await openNav(h) : await h.settle(component);
    h.setRemote(slot({ photosPending: true, updatedAt: 'v2', photoCount: 4 }));
    await choice(h, tree).props.onChoose('remote');
    const warning = dialog(h, await h.settle(component));
    assert.equal(warning?.props.dialog.kind, 'pending');
    assert.equal(warning.props.dialog.meta.photoCount, 4);
    assert.equal(h.state.projectId, 'project');
    assert.equal(h.staged, null);
    assert.deepEqual(h.calls, ['get', 'get']);
  });
}

test('C5 actual pending remote card opens warning and partial confirmation before takeover', async () => {
  const h = harness({ remote: slot({ photosPending: true }) });
  const tree = await h.settle('ProductInputRoute');
  await tree.props.onChoose('remote');
  let warning = dialog(h, await h.settle('ProductInputRoute'));
  assert.equal(warning.props.dialog.kind, 'pending');
  assert.deepEqual(h.calls, ['get', 'get']);
  const warningUI = h.components.DraftEntryDialog(warning.props);
  const partial = find(warningUI, (node) => node.type === 'Button' && node.props.children.includes('이대로 열기'));
  await partial.props.onClick();
  warning = dialog(h, await h.settle('ProductInputRoute'));
  assert.equal(warning.props.dialog.message, '아직 올라오지 않은 사진은 빠져요.');
  assert.deepEqual(h.calls, ['get', 'get', 'get']);
  h.setRemote(slot({ photosPending: true, photoCount: 4, updatedAt: 'v2' }));
  await warning.props.actions.confirm();
  warning = dialog(h, await h.settle('ProductInputRoute'));
  const confirmUI = h.components.DraftEntryDialog(warning.props);
  assert.deepEqual(find(confirmUI, (node) => node.type === 'h3').props.children, ['이대로 열기']);
  assert.deepEqual(h.calls, ['get', 'get', 'get', 'get']);
  await find(confirmUI, (node) => node.type === 'Button' && node.props.children.includes('이대로 열기')).props.onClick();
  assert.equal((await h.settle('ProductInputRoute')).type, 'ProductInput');
  assert.deepEqual(h.calls, ['get', 'get', 'get', 'get', 'get', 'takeover', 'begin', 'stage']);
});

for (const photosPending of [false, true]) {
  test(`matching localFallback waits for fresh ownership and pending photos: ${photosPending}`, async () => {
    let rejectTakeover;
    const h = harness({ confirmed: false, syncedAt: 'v1', serverSyncedAt: 'v1',
      remote: slot({ photosPending }), local: { updatedAt: 'v1', product: { name: 'local', colors: [] } },
      takeover: () => new Promise((_, reject) => { rejectTakeover = reject; }),
    });
    const tree = await h.settle('ProductInputRoute');
    const opening = tree.props.onChoose('remote');
    await new Promise(setImmediate);
    assert.equal(h.staged, null);
    if (photosPending) {
      assert.equal(dialog(h, await h.settle('ProductInputRoute')).props.dialog.kind, 'pending');
      assert.equal(rejectTakeover, undefined);
    } else {
      assert.ok(choice(h, await h.settle('ProductInputRoute')));
      rejectTakeover(new Error('offline'));
      await opening;
      assert.equal(h.staged, null);
      assert.equal(h.toasts.length, 1);
    }
  });
}

for (const component of ['ProductInputRoute', 'TopNav']) {
  test(`${component} starts directly when this browser last wrote the slot`, async () => {
    const h = harness({ serverSyncedAt: 'v1' });
    const tree = component === 'TopNav' ? await openNav(h) : await h.settle(component);
    await choice(h, tree).props.onNew();
    const result = await h.settle(component);
    assert.equal(dialog(h, result), null);
    assert.deepEqual(h.calls, ['get', 'get', 'remove', 'begin',
      ...(component === 'TopNav' ? ['navigate:/create/input'] : []),
    ]);
  });
}

for (const confirmed of [false, true]) {
  test(`G1 mobile guest opens real input without slot work (confirmed=${confirmed})`, async () => {
    const h = harness({ mobile: true, confirmed });
    h.auth.session = null;
    const tree = await h.settle('ProductInputRoute');
    assert.equal(tree.type, 'ProductInput');
    assert.deepEqual(h.calls, []);
  });
}

for (const mobile of [true, false]) {
  for (const hasContainer of [true, false]) {
    test(`input login and account change reset scroll before paint once, same-user refresh preserves it (mobile=${mobile}, container=${hasContainer})`, async () => {
      const h = harness({ mobile, confirmed: false, remote: null });
      h.location.pathname = '/create/input';
      h.auth.session = null;
      const windowScrolls = [], containerScrolls = [];
      const container = { scrollTop: 0, scrollTo(options) { containerScrolls.push(options); this.scrollTop = options.top; } };
      h.windowEvents.scrollY = 0;
      h.windowEvents.scrollTo = (options) => { windowScrolls.push(options); h.windowEvents.scrollY = options.top; };
      h.documentEvents.querySelector = (selector) => selector === '.app-main' && hasContainer ? container : null;
      await h.settle('ProductInputRoute');
      assert.equal(windowScrolls.length, 0);
      for (const userId of ['seller', 'another-seller']) {
        h.windowEvents.scrollY = 1200; container.scrollTop = 800;
        h.auth.loading = true;
        h.auth.session = { user: { id: userId } };
        await h.settle('ProductInputRoute');
        assert.equal(h.windowEvents.scrollY, 1200);
        h.auth.loading = false;
        h.render('ProductInputRoute');
        h.flushLayout();
        assert.equal(h.windowEvents.scrollY, 0);
        if (hasContainer) assert.equal(container.scrollTop, 0);
        const expectedCount = userId === 'seller' ? 1 : 2;
        assert.equal(windowScrolls.length, expectedCount);
        assert.equal(containerScrolls.length, hasContainer ? expectedCount : 0);
        assert.equal(windowScrolls.at(-1).behavior, 'instant');
        await h.settle('ProductInputRoute');
        h.windowEvents.scrollY = 640; container.scrollTop = 320;
        h.auth.session = { user: { id: userId }, access_token: 'refreshed' };
        await h.settle('ProductInputRoute');
        assert.equal(h.windowEvents.scrollY, 640);
        assert.equal(container.scrollTop, 320);
        assert.equal(windowScrolls.length, expectedCount);
        assert.equal(containerScrolls.length, hasContainer ? expectedCount : 0);
      }
    });
  }
}

test('M1 email login offers guest local and server cards without automatically overwriting either', async () => {
  const h = harness({ mobile: true, confirmed: false, local: {
    updatedAt: 'local', product: { name: 'guest draft', colors: [] },
  } });
  h.auth.session = null;
  await h.settle('ProductInputRoute');
  h.auth.session = { user: { id: 'logged-in' } };
  assert.equal(h.render('ProductInputRoute'), null);
  const tree = await h.settle('ProductInputRoute');
  assert.deepEqual(choice(h, tree).props.sources.map((source) => source.id), ['local', 'remote']);
  assert.deepEqual(h.calls, ['get']);
});

test('M1 account change after continue blocks input until the new slot decision settles', async () => {
  const h = harness({ mobile: true, confirmed: false, remote: null });
  assert.equal((await h.settle('ProductInputRoute')).type, 'ProductInput');
  h.setRemote(slot());
  h.auth.session = { user: { id: 'second-user' } };
  assert.equal(h.render('ProductInputRoute'), null);
  const tree = await h.settle('ProductInputRoute');
  assert.equal(dialog(h, tree).props.dialog.kind, 'mobile');
  assert.deepEqual(h.calls, ['get', 'get']);
});

test('M1 desktop guests and mobile mock guests retain input access', async () => {
  for (const options of [{ mobile: false }, { mobile: true, mock: true }]) {
    const h = harness({ ...options, confirmed: false, remote: null });
    h.auth.session = null;
    assert.equal((await h.settle('ProductInputRoute')).type, 'ProductInput');
  }
});

for (const returnIntent of ['/create/storyboard', '/editor/old', '/library', null]) {
  test(`mobile RootRedirect returns to input without promotion for ${returnIntent}`, async () => {
    const h = harness({ mobile: true, returnIntent, local: { product: { name: 'guest' } } });
    const tree = await h.settle('RootRedirect');
    assert.equal(tree.props.to, '/create/input');
    assert.deepEqual(h.calls, ['forget']);
  });
}

test('desktop RootRedirect still promotes a pending guest draft for storyboard', async () => {
  const h = harness({ local: { product: { name: 'guest' } } });
  const tree = await h.settle('RootRedirect');
  assert.equal(tree.props.to, '/create/storyboard');
  assert.deepEqual(h.calls, ['forget', 'remove', 'promote', 'adopt:promoted', 'confirm', 'clear']);
});

test('mobile RootRedirect waits for auth bootstrap before navigating', async () => {
  const h = harness({ mobile: true, loading: true, returnIntent: null });
  assert.equal((await h.settle('RootRedirect')).props.to, undefined);
  assert.deepEqual(h.calls, []);
  h.auth.loading = false;
  assert.equal((await h.settle('RootRedirect')).props.to, '/create/input');
});

for (const mobile of [false, true]) {
  test(`M8 seller nav uses device policy and library account menu navigates without deletion (mobile=${mobile})`, async () => {
    const h = harness({ mobile });
    const nav = find(await h.settle('TopNav'), (node) => node.type === 'nav');
    assert.equal(nav.props.className.includes('topnav-handoff'), mobile);
    assert.ok(find(nav, (node) => node.props.className === 'brand'));
    assert.ok(find(nav, (node) => node.type === h.components.ProfileMenu));
    let menu = await h.settle('ProfileMenu');
    find(menu, (node) => node.props.className === 'profile-btn').props.onClick();
    menu = await h.settle('ProfileMenu');
    const library = find(menu, (node) => node.props.role === 'menuitem' && node.props.children.includes('보관함'));
    assert.ok(library);
    library.props.onClick();
    assert.deepEqual(h.calls, ['navigate:/library']);
    assert.equal(find(await h.settle('ProfileMenu'), (node) => node.props.role === 'menu'), null);
  });
}
test('M8 FaceMarket retains its nav and does not gain a seller library menu', async () => {
  const h = harness({ mobile: true, facemarket: true });
  const nav = find(await h.settle('TopNav'), (node) => node.type === 'nav');
  assert.equal(nav.props.className, 'topnav');
  let menu = await h.settle('ProfileMenu');
  find(menu, (node) => node.props.className === 'profile-btn').props.onClick();
  menu = await h.settle('ProfileMenu');
  assert.equal(find(menu, (node) => node.props.children.includes('보관함')), null);
});

test('D1 remote-only entry asks directly; local-only new start is immediate', async () => {
  const h = harness({ mobile: true, confirmed: false });
  let tree = await h.settle('ProductInputRoute');
  assert.equal(tree.props.dialog.kind, 'mobile');
  assert.deepEqual(h.calls, ['get']);
  await tree.props.actions.confirm();
  tree = await h.settle('ProductInputRoute');
  assert.equal(tree.type, 'ProductInput');
  assert.ok(h.calls.includes('remove'));
  const local = harness({ mobile: true, confirmed: false, remote: null,
    local: { product: { name: 'local' }, updatedAt: 'v1' } });
  tree = await local.settle('ProductInputRoute');
  await tree.props.onNew();
  assert.equal((await local.settle('ProductInputRoute')).type, 'ProductInput');
  assert.ok(!local.calls.includes('remove'));
});

test('M4 mobile card nouns and desktop modal copy', () => {
  for (const mobile of [true, false]) {
    const h = harness({ mobile });
    const modal = h.render('ResumeChoiceModal', { sources: [
      { id: 'local', title: '이 기기에서 입력하던 내용', meta: { deviceLabel: 'iPad Safari' } },
      { id: 'remote', title: '입력하던 상품 정보', meta: { deviceLabel: 'iPhone Safari' } },
    ] });
    assert.ok(find(modal, (node) => node.props.children.includes(mobile ? '업로드한 상품이 있어요' : '하던 작업이 있어요')));
    assert.ok(find(modal, (node) => node.props.children.includes(mobile ? '태블릿 작업 이어가기' : '이 기기에서 입력하던 내용')));
    assert.ok(find(modal, (node) => node.props.children.includes(mobile ? '스마트폰 작업 이어가기' : '입력하던 상품 정보')));
  }
});

test('entry card kind follows remote writer even with a product name', () => {
  for (const mobile of [true, false]) {
    const h = harness({ mobile, confirmed: false });
    const chosen = [];
    const sources = [
      { id: 'flow', title: '만들던 상세페이지' },
      { ...entry.remoteDraftSource(slot({ productName: '셔츠' }).meta) },
      { ...entry.remoteDraftSource(slot({ deviceLabel: 'iPad Safari' }).meta) },
      { id: 'local', title: '이 기기에서 입력하던 내용' },
    ];
    const modal = h.render('ResumeChoiceModal', { sources, onChoose: (id) => chosen.push(id) });
    const cards = find(modal, (node) => node.props.className === 'draft-entry-sources').props.children.flat(Infinity);
    assert.deepEqual(cards.map((node) => node.props.className), [
      'draft-entry-source is-flow', 'draft-entry-source is-handoff',
      'draft-entry-source is-handoff', 'draft-entry-source',
    ]);
    cards.forEach((node) => node.props.onClick());
    assert.deepEqual(chosen, ['flow', 'remote', 'remote', 'local']);
  }
});

test('F8 entry query retries silently, shows status, and manual retry rechecks', async () => {
  const h = harness({ confirmed: false, remote: new Error('offline') });
  let tree = await h.settle('ProductInputRoute');
  assert.equal(tree.type, h.components.HandoffStatusPage);
  const status = h.render('HandoffStatusPage', tree.props);
  assert.ok(find(status, (node) => node.props.children.includes('저장된 상품을 확인하지 못했어요')));
  assert.deepEqual(h.calls, ['get', 'get', 'get']);
  h.setRemote(slot());
  tree.props.onRetry();
  tree = await h.settle('ProductInputRoute');
  assert.ok(tree.props.sources.some((source) => source.id === 'remote'));
});

test('F8 ineligible confirmed flow can explicitly resume after exhausted query retries', async () => {
  const h = harness({ remote: new Error('offline') });
  const tree = await h.settle('ProductInputRoute');
  assert.deepEqual(tree.props.sources.map((source) => source.id), ['flow']);
  assert.deepEqual(h.calls, ['get', 'get', 'get']);
  tree.props.onChoose('flow');
  assert.equal((await h.settle('ProductInputRoute')).type, 'ProductInput');
  assert.deepEqual(h.calls, ['get', 'get', 'get', 'navigate:/create/storyboard']);
});

test('mobile ignores a persisted desktop flow with or without remote content', async () => {
  for (const remote of [null, slot()]) {
    const h = harness({ mobile: true, confirmed: true, remote });
    const tree = await h.settle('ProductInputRoute');
    if (remote) assert.equal(tree.props.dialog.kind, 'mobile');
    else assert.equal(tree.type, 'ProductInput');
    assert.deepEqual(h.calls, ['get']);
  }
});

test('desktop empty input checks on focus and visibility with a 15 second throttle', async (t) => {
  let now = 100000;
  t.mock.method(Date, 'now', () => now);
  const h = harness({ confirmed: false, remote: null });
  let tree = await h.settle('ProductInputRoute');
  tree.props.onContentChange(false);
  h.windowEvents.dispatchEvent(new Event('focus'));
  await h.settle('ProductInputRoute');
  assert.deepEqual(h.calls, ['get', 'get']);
  h.setRemote(slot());
  h.documentEvents.dispatchEvent(new Event('visibilitychange'));
  await h.settle('ProductInputRoute');
  assert.equal(h.calls.length, 2);
  now += 15000;
  h.documentEvents.dispatchEvent(new Event('visibilitychange'));
  tree = await h.settle('ProductInputRoute');
  assert.deepEqual(tree.props.sources.map((source) => source.id), ['remote']);
  assert.equal(h.calls.length, 3);
});

for (const reason of ['content', 'token', 'mobile', 'hidden']) {
  test(`return to input skips remote check for ${reason}`, async () => {
    const h = harness({ confirmed: false, remote: null, mobile: reason === 'mobile' });
    const tree = await h.settle('ProductInputRoute');
    tree.props.onContentChange(reason === 'content');
    if (reason === 'token') h.draftSlot.getToken = () => 'owned';
    if (reason === 'hidden') h.documentEvents.visibilityState = 'hidden';
    h.setRemote(slot());
    h.documentEvents.dispatchEvent(new Event('visibilitychange'));
    await h.settle('ProductInputRoute');
    assert.deepEqual(h.calls, ['get']);
  });
}

test('remote card renders product name and optional square thumbnail on both devices', () => {
  for (const mobile of [false, true]) {
    const h = harness({ mobile });
    for (const thumbnailUrl of ['/photo.jpg', null]) {
      const source = entry.remoteDraftSource(slot({ productName: '셔츠', thumbnailUrl }).meta);
      const tree = h.render('ResumeChoiceModal', { sources: [source] });
      assert.ok(find(tree, (node) => node.props.children.includes('셔츠')));
      const image = find(tree, (node) => node.type === 'img');
      if (thumbnailUrl) {
        assert.equal(image.props.src, '/photo.jpg');
        assert.equal(image.props.width, 40);
        assert.equal(image.props.height, 40);
        assert.equal(image.props.className, 'draft-entry-thumbnail');
      } else assert.equal(image, null);
    }
  }
});

test('local entry card says 방금 저장', async () => {
  const h = harness({ confirmed: false, remote: null, local: { product: { name: '셔츠' }, updatedAt: new Date().toISOString() } });
  const tree = await h.settle('ProductInputRoute');
  assert.match(tree.props.sources[0].description, /^방금 저장/);
});

test('a remote response arriving after local input starts does not replace the input screen', async () => {
  const h = harness({ confirmed: false, remote: null });
  const tree = await h.settle('ProductInputRoute');
  tree.props.onContentChange(false);
  let finish;
  h.draftSlot.get = () => new Promise((resolve) => { finish = resolve; });
  h.windowEvents.dispatchEvent(new Event('focus'));
  tree.props.onContentChange(true);
  finish(slot());
  assert.equal((await h.settle('ProductInputRoute')).type, 'ProductInput');
});

for (const deviceLabel of ['iPhone Safari', 'iPad Safari', 'Mac Chrome']) {
  test(`D1 entry keep lands in B or C without unintended slot mutation: ${deviceLabel}`, async () => {
    const h = harness({ mobile: true, confirmed: false, remote: slot({ deviceLabel }) });
    let tree = await h.settle('ProductInputRoute');
    assert.equal(tree.props.dialog.kind, 'mobile');
    await tree.props.actions.keepMobile();
    tree = await h.settle('ProductInputRoute');
    if (isMobileDraftWriter({ deviceLabel })) {
      assert.equal(tree.type, 'ProductInput');
      assert.equal(tree.props.initialSavedMeta.deviceLabel, deviceLabel);
      assert.deepEqual(h.calls, ['get', 'get', 'takeover', 'begin', 'stage']);
      h.state.projectGeneration += 1;
      assert.equal((await h.settle('ProductInputRoute')).props.initialSavedMeta, null);
      tree.props.onFreshStart();
      assert.equal((await h.settle('ProductInputRoute')).props.initialSavedMeta, null);
    } else {
      assert.ok(find(tree, (n) => n.type === h.components.MobileDraftStatus));
      assert.deepEqual(h.calls, ['get', 'get']);
      const card = find(tree, (n) => n.type === h.components.MobileDraftStatus);
      await card.props.onNext();
      tree = await h.settle('ProductInputRoute');
      const d1 = dialog(h, tree);
      assert.equal(d1.props.dialog.kind, 'mobile');
      await d1.props.actions.keepMobile();
      assert.deepEqual(h.calls, ['get', 'get', 'get', 'get']);
    }
  });
}

test('F9 mobile PC-written remote hides the local copy and opens D1 directly', async () => {
  const h = harness({ mobile: true, confirmed: false, remote: slot({ deviceLabel: 'Mac Chrome', productName: '셔츠' }),
    local: { product: { name: 'local' }, updatedAt: 'local' } });
  const tree = await h.settle('ProductInputRoute');
  assert.equal(tree.type, h.components.DraftEntryDialog);
  assert.equal(tree.props.dialog.kind, 'mobile');
  assert.equal(tree.props.dialog.meta.productName, '셔츠');
  assert.equal(h.staged, null);
  assert.deepEqual(h.calls, ['get']);
});

test('pending mobile single remote keeps the pending-photo choice', async () => {
  const h = harness({ mobile: true, confirmed: false, remote: slot({ photosPending: true }) });
  const tree = await h.settle('ProductInputRoute');
  assert.ok(choice(h, tree));
});

for (const change of ['unmount', 'generation', 'project']) {
  test(`R2-2 late PC auto-open leaves the newer project intact after ${change}`, async () => {
    let finish;
    const response = new Promise((resolve) => { finish = resolve; });
    const h = harness({ confirmed: false, syncedUserId: 'seller', serverSyncedAt: 'v1', takeover: () => response });
    await h.settle('ProductInputRoute');
    assert.ok(h.calls.includes('takeover'));
    if (change === 'unmount') h.remount('ProductInputRoute');
    if (change === 'generation') h.state.projectGeneration += 1;
    Object.assign(h.state, { projectId: 'library-completed', projectPersisted: true });
    if (change !== 'unmount') {
      const tree = await h.settle('ProductInputRoute');
      assert.equal(tree.props.busy, false);
    }
    finish(slot());
    for (let i = 0; i < 5; i++) await new Promise(setImmediate);
    assert.equal(h.state.projectId, 'library-completed');
    assert.equal(h.staged, null);
    assert.equal(h.calls.includes('begin'), false);
    assert.deepEqual(h.toasts, []);
  });
}

test('R2-2 TopNav entry action drops its response after a route change with the same project', async () => {
  let finish;
  const response = new Promise((resolve) => { finish = resolve; });
  const h = harness({ takeover: () => response });
  const tree = await openNav(h);
  const opening = choice(h, tree).props.onChoose('remote');
  await new Promise(setImmediate);
  h.location.key = 'another-route'; await h.settle('TopNav');
  finish(slot()); await opening;
  assert.equal(h.state.projectId, 'project');
  assert.equal(h.staged, null);
  assert.equal(h.calls.includes('begin'), false);
  assert.equal(h.calls.some((call) => call.startsWith('navigate:')), false);
});

for (const reason of ['stale', 'other-user', 'other-tab', 'legacy']) {
  test(`R2-4 confirmed ${reason} flow asks on input instead of redirecting`, async () => {
    const h = harness({ remote: null, otherTab: reason === 'other-tab', savedFlow: reason === 'legacy' ? {} : {
      userId: reason === 'other-user' ? 'alice' : 'seller', lastUsedAt: Date.now() - (reason === 'stale' ? 86400001 : 1000),
    } });
    const tree = await h.settle('ProductInputRoute');
    assert.ok(choice(h, tree));
    assert.deepEqual(tree.props.sources.map((source) => source.id), ['flow']);
    assert.equal(h.calls.some((call) => call.startsWith('navigate:')), false);
    assert.deepEqual(h.toasts, []);
  });
}

test('R2-5 input check pauses an already queued PUT and only fresh writes resume after success', async (t) => {
  let finishPut, finishGet;
  const putResponse = new Promise((resolve) => { finishPut = resolve; });
  const getResponse = new Promise((resolve) => { finishGet = resolve; });
  const values = new Map([['wl_draftSlotToken', 'owned']]), puts = [];
  const slotClient = createDraftSlotSync({ storage: {
    getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key),
  }, setTimer: () => null, clearTimer() {}, adapter: {
    getDraftSlot: () => getResponse,
    putDraftSlot(body) { puts.push(body.payload.product.name); return puts.length === 1 ? putResponse : Promise.resolve({ token: 'owned' }); },
  } });
  t.after(() => slotClient.dispose()); slotClient.setUserId('seller');
  const draft = (name) => ({ product: { name, colors: [] }, localUpdatedAt: name });
  slotClient.queue(draft('first')); const first = slotClient.flush(); await new Promise(setImmediate);
  slotClient.queue(draft('stale-second')); const second = slotClient.flush();
  const h = harness({ confirmed: false, slotClient });
  t.after(() => { h.remount('ProductInputRoute'); finishGet(null); });
  await h.settle('ProductInputRoute');
  finishPut({ token: 'owned' }); await Promise.all([first, second]);
  assert.deepEqual(puts, ['first']);
  slotClient.queue(draft('while-checking')); await slotClient.flush();
  assert.deepEqual(puts, ['first']);
  finishGet(null); assert.equal((await h.settle('ProductInputRoute')).type, 'ProductInput');
  slotClient.queue(draft('fresh')); await slotClient.flush();
  assert.deepEqual(puts, ['first', 'fresh']);
});

test('R2-5 failed check keeps a held-token queue paused when a recent flow returns automatically', async () => {
  const h = harness({ token: 'owned', remote: new Error('offline'), savedFlow: { userId: 'seller', lastUsedAt: Date.now() } });
  let paused = false;
  h.draftSlot.pauseEntryWrites = () => { paused = true; };
  h.draftSlot.resumeEntryWrites = () => { paused = false; };
  await h.settle('ProductInputRoute');
  assert.ok(h.calls.includes('navigate:/create/storyboard'));
  assert.equal(paused, true);
});

for (const event of ['focus', 'visibilitychange']) {
  test(`desktop refocus lookup failure stays silent: ${event}`, async () => {
    const h = harness({ confirmed: false, remote: null });
    let tree = await h.settle('ProductInputRoute');
    tree.props.onContentChange(false);
    h.setRemote(new Error('offline'));
    (event === 'focus' ? h.windowEvents : h.documentEvents).dispatchEvent(new Event(event));
    tree = await h.settle('ProductInputRoute');
    assert.equal(tree.type, 'ProductInput');
    assert.deepEqual(h.toasts, []);
  });
}

const uiButton = (tree, label) => find(tree, (n) => n.type === 'Button' && n.props.children.includes(label));
for (const path of ['/create/storyboard', '/create/mannequin', '/create/input', '/editor/finished']) {
  for (const component of ['ProductInputRoute', 'TopNav', 'RequireProject']) {
    test(`M12 ${component} new start keeps the project without warning: ${path}`, async () => {
      const h = harness({ remote: null });
      h.state.resumePath = path;
      let tree;
      if (component === 'TopNav') {
        tree = await h.settle(component);
        find(tree, (n) => n.type === 'button' && n.props.children.includes('상세페이지 제작')).props.onClick();
        tree = await h.settle(component);
      } else if (component === 'ProductInputRoute') {
        h.setRemote(slot());
        tree = await h.settle(component);
      } else tree = await h.settle(component);
      const props = choice(h, tree).props;
      let modal = h.render('ResumeChoiceModal', props);
      uiButton(modal, '새로 시작하기').props.onClick();
      modal = h.render('ResumeChoiceModal', props);
      const warning = uiButton(modal, '만들던 것 이어서 하기');
      assert.equal(Boolean(warning), false);
      await h.settle(component);
      if (component !== 'ProductInputRoute') assert.ok(h.calls.includes('begin'));
      if (h.calls.includes('begin')) assert.equal(h.toasts.includes('만들던 상세페이지는 보관함의 만드는 중에 있어요'),
        path.startsWith('/create/storyboard') || path.startsWith('/create/mannequin'));
    });
  }
}

for (const id of ['remote', 'local']) {
  test(`M12 choosing ${id} over flow switches without a lost-work warning`, async () => {
    const h = harness();
    const original = await h.settle('ProductInputRoute');
    const source = id === 'remote' ? original.props.sources[1] : { id: 'local', draft: { product: { name: 'local' } }, meta: {} };
    const chosen = [];
    const props = { sources: [original.props.sources[0], source], onChoose: (id) => chosen.push(id) };
    let modal = h.render('ResumeChoiceModal', props);
    find(modal, (n) => n.type === 'button' && n.props.key === id).props.onClick();
    modal = h.render('ResumeChoiceModal', props);
    assert.deepEqual(chosen, [id]);
  });
}

for (const event of ['online', 'visibilitychange']) {
  test(`F8 failed entry automatically rechecks on ${event}`, async () => {
    const h = harness({ confirmed: false, remote: new Error('offline') });
    assert.equal((await h.settle('ProductInputRoute')).type, h.components.HandoffStatusPage);
    h.setRemote(slot());
    (event === 'online' ? h.windowEvents : h.documentEvents).dispatchEvent(new Event(event));
    assert.deepEqual((await h.settle('ProductInputRoute')).props.sources.map((s) => s.id), ['remote']);
    assert.deepEqual(h.calls, ['get', 'get', 'get', 'get']);
  });
}

for (const token of [null, 'owned']) {
  test(`F8 failure allows upload only without a slot token: ${token}`, async () => {
    const h = harness({ confirmed: false, token, remote: new Error('offline') });
    const tree = await h.settle('ProductInputRoute');
    assert.equal(tree.type, h.components.HandoffStatusPage);
    assert.equal(Boolean(tree.props.onUpload), !token);
    if (!token) {
      tree.props.onUpload();
      assert.equal((await h.settle('ProductInputRoute')).type, 'ProductInput');
      assert.deepEqual(h.calls, ['get', 'get', 'get', 'continueUnchecked']);
      assert.equal(h.calls.includes('takeover'), false);
      assert.equal(h.calls.includes('remove'), false);
    } else {
      h.setRemote(null); tree.props.onRetry();
      assert.equal((await h.settle('ProductInputRoute')).type, 'ProductInput');
    }
  });
}

test('F8 persisted unconfirmed PC flow is not blocked by failed query', async () => {
  const h = harness({ confirmed: false, remote: new Error('offline'), savedFlow: { projectId: 'existing', projectPersisted: true } });
  assert.equal((await h.settle('ProductInputRoute')).type, 'ProductInput');
  assert.deepEqual(h.calls, ['get', 'get', 'get', 'navigate:/create/storyboard']);
});

for (const variant of ['pc', 'gone', 'error']) {
  test(`shared status ${variant} renders exact copy, brand tag, and actions`, () => {
    const h = harness();
    const props = { variant, meta: slot({ productName: '셔츠' }).meta, onNext() {}, onRetry() {}, onUpload() {} };
    const tree = h.render('HandoffStatusPage', props);
    const expected = {
      pc: ['ON PC', 'IN PROGRESS', 'PC에서 이 상품을 이어서 만들고 있어요'],
      gone: ['CLOSED', 'DONE', '이 상품은 PC에서 마무리됐거나 정리됐어요', '새 상품을 올릴 수 있어요.'],
      error: ['RETRY', 'OFFLINE', '저장된 상품을 확인하지 못했어요', '인터넷 연결을 확인하고 다시 시도해 주세요.'],
    }[variant];
    for (const copy of expected) assert.ok(find(tree, (n) => n.props.children.includes(copy)));
    assert.equal(tree.props.className, 'page');
    assert.ok(find(tree, (n) => n.type === h.components.HangTag));
    const row = find(tree, (n) => n.type === h.components.DraftProductRow);
    assert.equal(Boolean(row), variant === 'pc');
    const actionTree = find(tree, (n) => n.props.className === 'actions actions');
    const buttons = variant === 'error' ? actionTree.props.children[0].props.children.flat(Infinity).filter(Boolean) : actionTree.props.children.flat(Infinity).filter(Boolean);
    assert.deepEqual(buttons.map((n) => n.props.children[0]), variant === 'error' ? ['다시 확인', '사진부터 올리기'] : ['다음 상품 올리기']);
    assert.equal(buttons[0].props.variant, 'primary');
  });
}

test('F7 mobile C and D recheck on focus and visible with a 5s throttle', async (t) => {
  let now = 100000;
  t.mock.method(Date, 'now', () => now);
  const h = harness({ mobile: true, confirmed: false, remote: slot({ deviceLabel: 'Mac Chrome' }) });
  let tree = await h.settle('ProductInputRoute');
  await tree.props.actions.keepMobile(); tree = await h.settle('ProductInputRoute');
  let status = find(tree, (n) => n.type === h.components.MobileDraftStatus);
  await h.settle('HandoffStatusPage', h.components.MobileDraftStatus(status.props).props);
  h.setRemote(null); h.windowEvents.dispatchEvent(new Event('focus'));
  tree = await h.settle('ProductInputRoute');
  status = find(tree, (n) => n.type === h.components.MobileDraftStatus);
  assert.equal(status.props.gone, true);
  await h.settle('HandoffStatusPage', h.components.MobileDraftStatus(status.props).props);
  h.setRemote(slot()); h.documentEvents.dispatchEvent(new Event('visibilitychange'));
  await h.settle('ProductInputRoute');
  assert.equal(h.calls.filter((c) => c === 'get').length, 3);
  now += 5000;
  h.documentEvents.dispatchEvent(new Event('visibilitychange'));
  tree = await h.settle('ProductInputRoute');
  assert.equal(dialog(h, tree).props.dialog.kind, 'mobile');
  assert.equal(dialog(h, tree).props.dialog.meta.deviceLabel, 'Android Chrome');
  assert.equal(h.calls.includes('takeover'), false);
});

test('own single remote auto-opens and its toast action runs fresh startNew', async () => {
  const h = harness({ confirmed: false, syncedUserId: 'seller', serverSyncedAt: 'v1' });
  assert.equal((await h.settle('ProductInputRoute')).type, 'ProductInput');
  assert.equal(h.staged.payload.product.name, 'remote');
  assert.deepEqual(h.toasts, ['이어서 열었어요']);
  assert.equal(h.toastOptions[0].action.label, '새로 시작');
  h.toastOptions[0].action.onClick();
  await h.settle('ProductInputRoute');
  assert.ok(h.calls.includes('remove'));
  assert.ok(h.calls.includes('begin'));
  assert.equal(h.calls.at(-2), 'remove');
  assert.equal(h.calls.at(-1), 'begin');
});

test('auto-open toast new start still asks C4 for a new other-device write', async () => {
  const h = harness({ confirmed: false, syncedUserId: 'seller', serverSyncedAt: 'v1' });
  await h.settle('ProductInputRoute'); h.setRemote(slot({ updatedAt: 'v2' }));
  h.toastOptions[0].action.onClick();
  const tree = await h.settle('ProductInputRoute');
  assert.equal(dialog(h, tree).props.dialog.kind, 'confirm');
  assert.equal(h.calls.includes('remove'), false);
});

for (const reason of ['other-device', 'two-candidates', 'pending', 'other-tab', 'unknown-user']) {
  test(`PC input keeps asking for ${reason}`, async () => {
    const h = harness({ confirmed: false, syncedUserId: reason === 'unknown-user' ? null : 'seller',
      serverSyncedAt: reason === 'other-device' || reason === 'two-candidates' ? 'other-version' : 'v1',
      otherTab: reason === 'other-tab', remote: slot({ photosPending: reason === 'pending' }),
      local: reason === 'two-candidates' ? { product: { name: 'local' }, updatedAt: 'local' } : null });
    const tree = await h.settle('ProductInputRoute');
    assert.ok(choice(h, tree));
    assert.equal(h.staged, null);
    assert.deepEqual(h.toasts, []);
    assert.equal(h.calls.includes('takeover'), false);
  });
}

for (const kind of ['recent', 'stale', 'other-user', 'legacy']) {
  test(`PC input auto-open policy for ${kind} persisted flow`, async () => {
    const savedFlow = { projectId: 'flow', projectPersisted: true, resumePath: '/create/storyboard',
      ...(kind !== 'legacy' ? { userId: kind === 'other-user' ? 'other' : 'seller', lastUsedAt: Date.now() - (kind === 'stale' ? 86400001 : 1000) } : {}) };
    const h = harness({ confirmed: false, remote: null, savedFlow, withAuth: kind === 'other-user' });
    const tree = kind === 'other-user' ? await h.settleApp() : await h.settle('ProductInputRoute');
    if (kind === 'recent') {
      assert.equal(tree.type, 'ProductInput');
      assert.deepEqual(h.toasts, ['이어서 열었어요']);
      assert.ok(h.calls.includes('navigate:/create/storyboard'));
    } else if (kind === 'other-user') {
      assert.equal(tree.type, 'ProductInput');
      assert.equal(h.state.projectId, null);
      assert.deepEqual(h.toasts, []);
    } else { assert.ok(choice(h, tree)); assert.deepEqual(h.toasts, []); }
  });
}

for (const path of ['/create/storyboard', '/create/mannequin']) {
  for (const kind of ['recent', 'stale', 'other-user', 'legacy', 'other-tab']) {
    test(`M13 guard auto-open and toast policy: ${path}, ${kind}`, async () => {
      const h = harness({ withAuth: kind === 'other-user', remote: null, otherTab: kind === 'other-tab', savedFlow: kind === 'legacy' ? {} : {
        userId: kind === 'other-user' ? 'other' : 'seller',
        lastUsedAt: Date.now() - (kind === 'stale' ? 86400001 : 1000),
      } });
      h.location.pathname = path;
      const tree = kind === 'other-user' ? await h.settleApp('RequireProject') : await h.settle('RequireProject');
      assert.equal(tree.type, kind === 'recent' ? 'Outlet' : kind === 'other-user' ? 'Navigate' : h.components.ResumeChoiceModal);
      assert.equal(h.calls.includes('begin'), kind === 'other-user');
      assert.deepEqual(h.toasts, kind === 'recent' ? ['이어서 열었어요'] : []);
      if (kind === 'recent') {
        assert.equal(h.toastOptions[0].action.label, '새로 시작');
        h.toastOptions[0].action.onClick();
        assert.ok(h.calls.includes('navigate:/create/input'));
        assert.equal(h.location.state.startNewFromResume, true);
      }
    });
  }
}

test('M12 recent confirmed flow new start only asks C4 for the other-device draft', async () => {
  const h = harness({ remote: null, savedFlow: { userId: 'seller', lastUsedAt: Date.now(), resumePath: '/create/storyboard' } });
  await h.settle('ProductInputRoute');
  assert.equal(h.toasts[0], '이어서 열었어요');
  h.setRemote(slot({ updatedAt: 'new-phone' })); h.toastOptions[0].action.onClick();
  const tree = await h.settle('ProductInputRoute');
  assert.equal(dialog(h, tree).props.dialog.kind, 'confirm');
  assert.equal(h.calls.includes('remove'), false);
});

test('own merged newer local auto-opens after the same-product merge', async () => {
  const h = harness({ confirmed: false, syncedUserId: 'seller', serverSyncedAt: '2026-10-06T01:00:00Z',
    remote: slot({ updatedAt: '2026-10-06T01:00:00Z' }), local: { updatedAt: '2026-10-06T01:01:00Z', product: { name: 'newer local' } } });
  assert.equal((await h.settle('ProductInputRoute')).type, 'ProductInput');
  assert.equal(h.staged.payload.product.name, 'newer local');
  assert.deepEqual(h.toasts, ['이어서 열었어요']);
});

test('F9 pending PC-written mobile slot still hides its local copy and shows remote-only D1', async () => {
  const h = harness({ confirmed: false, mobile: true, remote: slot({ deviceLabel: 'Mac Chrome', photosPending: true }),
    local: { updatedAt: 'local', product: { name: 'old phone copy' } } });
  const tree = await h.settle('ProductInputRoute');
  assert.equal(tree.props.dialog.kind, 'mobile');
  await tree.props.actions.keepMobile();
  assert.ok(find(await h.settle('ProductInputRoute'), (node) => node.type === h.components.MobileDraftStatus));
  assert.equal(h.calls.includes('takeover'), false);
});

const savedByA = (confirmed = false) => ({ projectId: 'A-project', projectPersisted: true,
  productInfoConfirmed: confirmed, userId: 'A', lastUsedAt: Date.now() - 3600000, resumePath: '/create/mannequin' });

for (const confirmed of [false, true]) {
  test(`guest resume R${confirmed ? 2 : 1}: auth discards A flow before input entry and loads anonymous input`, async () => {
    const h = harness({ withAuth: true, authUser: null, remote: null, confirmed: false, savedFlow: savedByA(confirmed) });
    h.location.pathname = '/create/input';
    const tree = await h.settleApp();
    assert.equal(tree?.type, 'ProductInput');
    assert.equal(choice(h, tree), null);
    await h.settle('ProductLoad');
    assert.equal(h.loadError, '');
    assert.equal(h.state.projectId, null);
    assert.deepEqual(h.calls, ['begin', 'product:null']);
    assert.equal(h.products.length, 1);
    assert.deepEqual(h.toasts, []);
    assert.ok(h.flowReads.every((read) => read.projectId !== 'A-project'));
  });
}

for (const component of ['ProductInputRoute', 'TopNav', 'RequireProject', 'ResumeTracker']) {
  test(`B never observes A flow in ${component} after bootstrap`, async () => {
    const h = harness({ withAuth: true, authUser: 'B', remote: null, savedFlow: savedByA(true) });
    if (component === 'ResumeTracker' || component === 'RequireProject') h.location.pathname = '/create/mannequin';
    const tree = await h.settleApp(component);
    if (component === 'TopNav') {
      find(tree, (node) => node.props.children.includes('상세페이지 제작')).props.onClick();
      assert.equal(choice(h, await h.settleApp(component)), null);
      assert.ok(h.calls.includes('navigate:/create/input'));
    } else if (component === 'ProductInputRoute') assert.equal(tree?.type, 'ProductInput');
    else if (component === 'RequireProject') assert.equal(tree?.type, 'Navigate');
    assert.equal(h.state.projectId, null);
    assert.ok(h.flowReads.every((read) => read.projectId !== 'A-project'));
    assert.deepEqual(h.toasts, []);
  });
}

for (const event of ['SIGNED_OUT', 'SIGNED_IN']) {
  test(`later ${event} during an input entry check resets before publication and does not leave checking blank`, async () => {
    const h = harness({ withAuth: true, authUser: 'A', confirmed: false, remote: null });
    await h.settleApp();
    Object.assign(h.state, savedByA());
    h.remount('ProductInputRoute');
    let release;
    h.setEntryFlush(new Promise((resolve) => { release = resolve; }));
    assert.equal(await h.settleApp(), null);
    h.emit(event, event === 'SIGNED_OUT' ? null : { user: { id: 'B' } });
    h.setEntryFlush(null);
    release();
    const tree = await h.settleApp();
    assert.equal(tree?.type, 'ProductInput');
    assert.equal(h.state.projectId, null);
    assert.equal(h.auth.session?.user?.id || null, event === 'SIGNED_OUT' ? null : 'B');
    assert.equal(h.calls.filter((call) => call === 'begin').length, 1);
    assert.deepEqual(h.toasts, []);
  });
}

test('bootstrap waits for asynchronous logout reset before mounting any entry reader', async () => {
  let release;
  const resetGate = new Promise((resolve) => { release = resolve; });
  const h = harness({ withAuth: true, authUser: null, remote: null, savedFlow: savedByA(), resetGate });
  assert.equal(await h.settleApp(), null);
  assert.deepEqual(h.flowReads, []);
  release();
  assert.equal((await h.settleApp())?.type, 'ProductInput');
  assert.ok(h.flowReads.every((read) => read.projectId !== 'A-project'));
});

test('same-user TOKEN_REFRESHED preserves flow and current auto-open behavior', async () => {
  const h = harness({ withAuth: true, authUser: 'A', remote: null, savedFlow: savedByA() });
  await h.settleApp();
  h.emit('TOKEN_REFRESHED', { user: { id: 'A' }, access_token: 'renewed' });
  await h.settleApp();
  assert.equal(h.state.projectId, 'A-project');
  assert.equal(h.calls.includes('begin'), false);
  assert.ok(h.calls.includes('navigate:/create/mannequin'));
  assert.deepEqual(h.toasts, ['이어서 열었어요']);
});

test('legacy flow without userId keeps the current signed-in resume choice', async () => {
  const h = harness({ withAuth: true, authUser: 'B', remote: null, savedFlow: { userId: null } });
  const tree = await h.settleApp();
  assert.ok(choice(h, tree));
  assert.equal(h.state.projectId, 'project');
  assert.equal(h.calls.includes('begin'), false);
});

test('mock guest retains persisted resume without auth cleanup', async () => {
  const h = harness({ withAuth: true, authUser: null, mock: true, remote: null, savedFlow: savedByA() });
  assert.ok(choice(h, await h.settleApp()));
  assert.equal(h.state.projectId, 'A-project');
  assert.equal(h.calls.includes('begin'), false);
});

for (const source of ['getSession-error', 'INITIAL_SESSION-null']) {
  test(`stored flow survives ${source} but the reported null session is published`, async () => {
    const session = { user: { id: 'A' }, access_token: 'valid', refresh_token: 'retryable', expires_at: Date.now() / 1000 + 20 };
    const h = harness({ withAuth: true, authUser: 'A', remote: null, savedFlow: savedByA(), storedSession: session,
      sessionResult: source === 'getSession-error' ? { data: { session: null }, error: { name: 'AuthRetryableFetchError' } } : null });
    await h.settleApp();
    if (source === 'INITIAL_SESSION-null') { h.emit('INITIAL_SESSION', null); await h.settleApp(); }
    assert.equal(h.auth.session, null);
    assert.equal(h.publishedSessions.at(-1), null);
    assert.equal(h.state.projectId, 'A-project');
    assert.equal(h.calls.includes('begin'), false);
  });
}

test('ResumeChoiceModal without onResume has no dead continue button', () => {
  const h = harness();
  const tree = h.render('ResumeChoiceModal', { onNew() {} });
  assert.equal(uiButton(tree, '이어서 하기'), null);
  assert.ok(uiButton(tree, '새로 시작하기'));
});

for (const component of ['TopNav', 'RequireProject']) {
  test(`batched SIGNED_OUT does not retain ${component}'s old resume modal`, async () => {
    const h = harness({ withAuth: true, authUser: 'A', remote: null,
      savedFlow: { ...savedByA(), lastUsedAt: 1 } });
    let tree = await h.settleApp(component);
    if (component === 'TopNav') {
      find(tree, (node) => node.props.children.includes('상세페이지 제작')).props.onClick();
      tree = await h.settleApp(component);
    }
    assert.ok(choice(h, tree));
    h.emit('SIGNED_OUT', null);
    // React가 loading=true를 커밋하기 전에 초기화와 인증 발행이 모두 끝나는 경우다.
    for (let i = 0; i < 12; i += 1) await new Promise(setImmediate);
    tree = await h.settleApp(component);
    assert.equal(h.state.projectId, null);
    assert.equal(choice(h, tree), null);
    if (component === 'RequireProject') assert.equal(tree.type, 'Navigate');
  });
}

test('bootstrap exception publishes null, preserves stored flow and still subscribes to subsequent SIGNED_OUT', async () => {
  const session = { user: { id: 'A' }, access_token: 'valid', refresh_token: 'stored' };
  const h = harness({ withAuth: true, remote: null, savedFlow: savedByA(), storedSession: session, sessionResult: new Error('refresh interrupted') });
  await h.settleApp();
  assert.equal(h.state.projectId, 'A-project');
  assert.equal(h.auth.session, null);
  assert.equal(h.publishedSessions.at(-1), null);
  assert.equal(h.hasListener, true);
  h.setStoredSession(null);
  h.emit('SIGNED_OUT', null);
  assert.equal((await h.settleApp()).type, 'ProductInput');
  assert.equal(h.state.projectId, null);
});

for (const phase of ['bootstrap', 'late-SIGNED_OUT']) {
  test(`a newer stored same-user session survives a stale null during ${phase}`, async () => {
    const session = { user: { id: 'A' }, access_token: 'newer', refresh_token: 'stored' };
    const h = harness({ withAuth: true, authUser: phase === 'bootstrap' ? null : 'A', remote: null,
      savedFlow: savedByA(), storedSession: session });
    await h.settleApp();
    if (phase === 'late-SIGNED_OUT') { h.emit('SIGNED_OUT', null); await h.settleApp(); }
    assert.equal(h.auth.session, null);
    assert.equal(h.publishedSessions.at(-1), null);
    assert.equal(h.state.projectId, 'A-project');
    assert.equal(h.calls.includes('begin'), false);
  });
}

for (const facemarket of [false, true]) {
  test(`normal http boot renders public children while authentication is pending (facemarket=${facemarket})`, async () => {
    let release;
    const sessionResult = new Promise((resolve) => { release = resolve; });
    const h = harness({ withAuth: true, confirmed: false, facemarket, sessionResult });
    const provider = h.render('AuthProvider', { children: 'App' });
    assert.equal(provider.props.value.loading, true);
    assert.equal(provider.props.children[0], 'App');
    assert.equal((await h.settleApp('TopNav')).type, 'fragment');
    assert.equal(h.auth.loading, true);
    assert.deepEqual(h.calls, []);
    release({ data: { session: null } });
    await h.settleApp('TopNav');
    assert.equal(h.auth.loading, false);
  });
}

test('stale guest boot hides children only during the flow reset and then shows anonymous input', async () => {
  let release;
  const resetGate = new Promise((resolve) => { release = resolve; });
  const h = harness({ withAuth: true, authUser: null, remote: null, savedFlow: savedByA(), resetGate });
  const initial = h.render('AuthProvider', { children: 'App' });
  assert.equal(initial.props.children[0], 'App');
  assert.equal(await h.settleApp(), null);
  assert.equal(h.render('AuthProvider', { children: 'App' }).props.children[0], false);
  release();
  assert.equal((await h.settleApp()).type, 'ProductInput');
  assert.equal(h.auth.loading, false);
  assert.equal(h.state.projectId, null);
  assert.equal(h.render('AuthProvider', { children: 'App' }).props.children[0], 'App');
});

test('failed bootstrap reset publishes B, releases the gate and processes a later auth event', async () => {
  const h = harness({ withAuth: true, authUser: 'B', remote: null, savedFlow: savedByA(), resetError: new Error('reset failed') });
  await h.settleApp('TopNav');
  assert.equal(h.auth.session?.user?.id, 'B');
  assert.equal(h.auth.loading, false);
  assert.equal(h.render('AuthProvider', { children: 'App' }).props.children[0], 'App');
  assert.equal(h.hasListener, true);
  h.setResetError(null);
  const next = { user: { id: 'B' }, access_token: 'refreshed' };
  h.emit('TOKEN_REFRESHED', next);
  await h.settleApp('TopNav');
  assert.equal(h.auth.session, next);
  assert.equal(h.publishedSessions.at(-1), next);
  assert.equal(h.state.projectId, null);
});

test('a publication failure after reset cannot close the gate or stop the next auth event', async () => {
  const h = harness({ withAuth: true, authUser: 'A', remote: null, savedFlow: savedByA() });
  await h.settleApp('TopNav');
  h.failNextPublication(new Error('client event publication failed'));
  h.emit('SIGNED_IN', { user: { id: 'B' } });
  await h.settleApp('TopNav');
  assert.equal(h.render('AuthProvider', { children: 'App' }).props.children[0], 'App');
  assert.equal(h.auth.loading, false);
  const next = { user: { id: 'C' } };
  h.emit('SIGNED_IN', next);
  await h.settleApp('TopNav');
  assert.equal(h.auth.session, next);
  assert.equal(h.publishedSessions.at(-1), next);
});


for (const login of ['email', 'redirect', 'other-tab']) {
  test(`G4/G6 ${login} login stages guest photos directly without a resume card`, async () => {
    const local = { updatedAt: 'guest', product: { name: 'phone photos', colors: [] } };
    const h = harness({ mobile: true, confirmed: false, local, remote: null,
      guestSave: { id: 'intent', draftUpdatedAt: 'guest' } });
    if (login === 'email') {
      h.auth.session = null; await h.settle('ProductInputRoute');
      h.auth.session = { user: { id: 'seller' } };
    }
    const tree = await h.settle('ProductInputRoute');
    assert.equal(tree.type, 'ProductInput');
    assert.equal(tree.props.autoSaveIntent.id, 'intent');
    assert.deepEqual(h.calls, ['get', 'stage']);
  });
}

for (const writer of ['Android Chrome', 'Windows Chrome']) {
  for (const choice of ['keep', 'replace']) {
    test(`G5 guest return asks D1 before ${choice} of ${writer} slot`, async () => {
      const local = { updatedAt: 'guest', product: { name: 'phone photos', colors: [] } };
      const h = harness({ mobile: true, confirmed: false, local, remote: slot({ deviceLabel: writer }),
        guestSave: { id: 'intent', draftUpdatedAt: 'guest' } });
      let tree = await h.settle('ProductInputRoute');
      const modal = dialog(h, tree);
      assert.equal(modal.props.dialog.kind, 'mobile');
      assert.deepEqual(h.calls, ['get']);
      if (choice === 'replace') {
        h.setRemote(slot({ deviceLabel: writer, updatedAt: 'v2' }));
        await modal.props.actions.confirm();
        tree = await h.settle('ProductInputRoute');
        assert.equal(dialog(h, tree).props.dialog.meta.updatedAt, 'v2');
        assert.deepEqual(h.calls, ['get', 'get']);
        await dialog(h, tree).props.actions.confirm();
        tree = await h.settle('ProductInputRoute');
        assert.equal(tree.type, 'ProductInput');
        assert.equal(tree.props.autoSaveIntent.id, 'intent');
        assert.deepEqual(h.calls, ['get', 'get', 'get', 'takeover', 'stage']);
      } else {
        await modal.props.actions.keepMobile();
        tree = await h.settle('ProductInputRoute');
        assert.ok(!tree.props.autoSaveIntent);
        assert.ok(!h.calls.includes('remove'));
        assert.ok(h.calls.includes('clearGuest'));
      }
    });
  }
}

test('G6 explicit logout clears the same-browser guest save marker', async () => {
  const h = harness({ withAuth: true, mobile: true, confirmed: false, remote: null });
  await h.settleApp();
  await h.auth.signOut();
  assert.ok(h.calls.includes('clearGuest'));
});

test('G6 another-tab SIGNED_OUT clears the guest save marker', async () => {
  const h = harness({ withAuth: true, mobile: true, confirmed: false, remote: null });
  await h.settleApp();
  h.emit('SIGNED_OUT', null); await h.settleApp();
  assert.ok(h.calls.includes('clearGuest'));
});


for (const age of [30 * 60 * 1000 - 1, 30 * 60 * 1000]) {
  test(`G6 real marker restores other-tab input only inside the 30-minute window (${age})`, async (t) => {
    const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
    const shared = new Map();
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
      getItem: (key) => shared.get(key) ?? null, setItem: (key, value) => shared.set(key, value), removeItem: (key) => shared.delete(key),
    } });
    t.after(() => { if (original) Object.defineProperty(globalThis, 'localStorage', original); else delete globalThis.localStorage; });
    const marker = mobileGuestSave.rememberMobileGuestSave('guest');
    const h = harness({ mobile: true, confirmed: false, remote: null, pendingLocal: false,
      local: { updatedAt: 'guest', product: { name: 'phone photos', colors: [] } },
      guestSave: (session) => mobileGuestSave.getMobileGuestSave(session, { mobile: true, now: marker.createdAt + age }),
    });
    h.auth.session = { user: { id: 'new-login', last_sign_in_at: new Date(marker.createdAt + 1000).toISOString() } };
    const tree = await h.settle('ProductInputRoute');
    assert.equal(tree.type, 'ProductInput');
    assert.equal(Boolean(tree.props.autoSaveIntent), age < 30 * 60 * 1000);
    assert.equal(h.calls.includes('stage'), age < 30 * 60 * 1000);
    if (age === 30 * 60 * 1000) assert.equal(shared.has('wl_mobileGuestSave'), false);
  });
}


test('G6 external mobile logout clears unpersisted account photos before publishing guest input', async () => {
  const h = harness({ withAuth: true, authUser: 'A', mobile: true, confirmed: false, remote: null,
    local: { updatedAt: 'saved', ownerUserId: 'A', product: { name: 'A photos' } },
  });
  await h.settleApp();
  assert.ok(h.localDraft);
  h.emit('SIGNED_OUT', null); await h.settleApp();
  assert.equal(h.auth.session, null);
  assert.equal(h.localDraft, null);
});

test('G6 mobile external logout holds input until the existing flow reset finishes', async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const h = harness({ withAuth: true, authUser: 'A', mobile: true, confirmed: false, remote: null, resetGate: gate,
    local: { updatedAt: 'saved', ownerUserId: 'A', product: { name: 'A photos' } },
  });
  await h.settleApp();
  h.emit('SIGNED_OUT', null); const during = await h.settleApp();
  assert.equal(during, null);
  assert.equal(h.auth.session?.user?.id, 'A');
  release(); await h.settleApp();
  assert.equal(h.auth.session, null); assert.equal(h.localDraft, null);
});

test('a guest save intent cannot exempt an already confirmed account change', async () => {
  const h = harness({ withAuth: true, authUser: 'A', mobile: true, confirmed: false, remote: null,
    local: { updatedAt: 'guest', ownerUserId: 'A', product: { name: 'A photos', colors: [] } },
    guestSave: { id: 'intent', draftUpdatedAt: 'guest', ownerUserId: 'A' } });
  await h.settleApp();
  h.emit('SIGNED_IN', { user: { id: 'B' } });
  await h.settleApp();
  assert.equal(h.localDraft, null);
  assert.deepEqual(h.resetOwners, ['A']);
  assert.equal(h.auth.session.user.id, 'B');
  assert.equal(h.auth.sellerConsentReady, false);
});

test('guest save still exempts a persisted flow before the first account is confirmed', async () => {
  const h = harness({ withAuth: true, authUser: 'B', mobile: true, remote: null,
    savedFlow: { userId: 'A' },
    guestSave: { id: 'intent', draftUpdatedAt: 'guest', ownerUserId: 'B' } });
  await h.settleApp();
  assert.equal(h.calls.includes('begin'), false);
  assert.equal(h.state.projectId, 'project');
  assert.equal(h.auth.session.user.id, 'B');
});

test('sibling-domain B bootstrap never offers A newly saved unconfirmed input with no flow owner', async (t) => {
  const storage = draftStoreFixture(t);
  queueProductDraftSave({ name: 'Account A private input', colors: [] });
  await flushProductDraftSave();
  Object.defineProperty(globalThis, 'sessionStorage', { writable: true });
  const h = harness({ withAuth: true, authUser: 'B', confirmed: false, remote: null,
    local: storage.saved, localReader: readStoredDraft, slotClient: storedDraftSlot, savedFlow: { userId: null } });
  const tree = await h.settleApp();
  assert.equal(h.auth.session.user.id, 'B');
  assert.equal(h.state.projectId, null);
  assert.equal(h.state.userId, null);
  assert.equal(choice(h, tree), null);
  assert.equal(tree?.type, 'ProductInput');
});

for (const next of [null, { user: { id: 'B' } }]) {
  test(`a live account change to ${next?.user.id || 'guest'} clears unconfirmed input before publication`, async () => {
    const h = harness({ withAuth: true, authUser: 'A', confirmed: false, remote: null,
      local: { ownerUserId: 'A', product: { name: 'Account A private input', colors: [] } }, savedFlow: { userId: null } });
    await h.settleApp();
    h.emit(next ? 'SIGNED_IN' : 'SIGNED_OUT', next);
    const tree = await h.settleApp();
    assert.equal(h.calls.filter((call) => call === 'begin').length, 1);
    assert.equal(choice(h, tree), null);
    assert.equal(tree?.type, 'ProductInput');
  });
}

for (const initialUser of [null, 'A']) {
  test(`unconfirmed input survives ${initialUser ? 'same-user refresh' : 'guest login'}`, async () => {
    const h = harness({ withAuth: true, authUser: initialUser, confirmed: false, remote: null,
      local: { product: { name: 'Continuing input', colors: [] } }, savedFlow: { userId: null } });
    await h.settleApp();
    h.emit(initialUser ? 'TOKEN_REFRESHED' : 'SIGNED_IN', { user: { id: 'A' } });
    const tree = await h.settleApp();
    assert.equal(h.calls.includes('begin'), false);
    assert.equal(choice(h, tree)?.props.sources[0].draft.product.name, 'Continuing input');
  });
}


test('a delayed A auth transition preserves B input already saved in another tab', async (t) => {
  const { h, storage } = storedDraftHarness(t,
    { ownerUserId: 'A', product: { name: 'A input', colors: [] }, photos: [], updatedAt: 'A' },
    { authUser: 'A', confirmed: false, savedFlow: { userId: null } });
  await h.settleApp();
  // Another tab has B's identity and writes the same origin's IndexedDB. The old
  // tab still holds A until its delayed auth event is delivered below.
  storedDraftSlot.setUserId('B');
  await saveProductDraft({ name: 'B newer input', colors: [] });
  storedDraftSlot.setUserId('A');
  const originalB = storage.saved;

  h.emit('SIGNED_IN', { user: { id: 'B' } });
  const tree = await h.settleApp();
  assert.equal(h.auth.session.user.id, 'B');
  assert.deepEqual(storage.saved, originalB);
  assert.equal(hasStoredPendingDraft(), true);
  assert.equal(choice(h, tree)?.props.sources.find((source) => source.id === 'local')?.draft.product.name, 'B newer input');
});

for (const transition of ['bootstrap', 'live']) {
  test(`known A to B ${transition} cannot restore legacy A photo bytes`, async (t) => {
    const draft = legacyPhotoDraft();
    if (transition === 'live') {
      draft.analysis = { featureRun: { runId: 'A-in-progress', stage: 'requested', coreReceived: false } };
    }
    const { h, storage } = storedDraftHarness(t, draft, {
      authUser: transition === 'bootstrap' ? 'B' : 'A',
      confirmed: transition === 'bootstrap',
      savedFlow: transition === 'bootstrap'
        ? { projectId: 'A-project', projectPersisted: true, userId: 'A', productInfoConfirmed: true }
        : { userId: null },
    });
    let tree = await h.settleApp();
    if (transition === 'live') {
      h.emit('SIGNED_IN', { user: { id: 'B' } });
      tree = await h.settleApp();
    }
    const modal = choice(h, tree);
    let recoveredBytes = null;
    if (modal) {
      await modal.props.onChoose('local');
      await h.settleApp();
      recoveredBytes = await readRestoredPhoto(storedDraftSlot.consumeStaged()?.payload);
    }
    assert.equal(recoveredBytes, null, 'B must not recover A photo bytes through the local continuation action');
    assert.equal(modal, null);
    assert.equal(h.auth.session.user.id, 'B');
    assert.equal(h.state.projectId, null);
    assert.equal(hasStoredPendingDraft(), false);
    assert.equal(await readStoredDraft(), null, 'Direct promotion/feature recovery readers cannot bypass the entry gate');
    assert.deepEqual(storage.saved, draft, 'Unattributed bytes remain available to the other tab that saved them');
    h.remount('ProductInputRoute');
    assert.equal((await h.settleApp()).type, 'ProductInput');
    await h.settle('ProductLoad');
    assert.equal(h.loadError, '');
    assert.equal(h.products.at(-1).name, '');
    assert.deepEqual(h.products.at(-1).colors[0].images, []);

    // A full document reload keeps sessionStorage, while auth/route state starts afresh.
    const tabStorage = sessionStorage;
    const reloaded = harness({ withAuth: true, authUser: 'B', confirmed: false, remote: null,
      local: storage.saved, localReader: readStoredDraft, localPendingReader: hasStoredPendingDraft,
      slotClient: storedDraftSlot, savedFlow: { userId: null } });
    globalThis.sessionStorage = tabStorage;
    assert.equal(await readStoredDraft(), null, 'The same tab keeps the legacy read restriction after a document reload');
    assert.equal((await reloaded.settleApp()).type, 'ProductInput');
    await reloaded.settle('ProductLoad');
    assert.equal(reloaded.loadError, '');
    assert.equal(reloaded.products.at(-1).name, '');
    assert.deepEqual(reloaded.products.at(-1).colors[0].images, []);
    await reloaded.settleApp('RootRedirect');
    assert.equal(reloaded.calls.includes('promote'), false, 'OAuth/root recovery cannot promote the preserved legacy draft');
  });
}

for (const continuation of ['same-user reload', 'same-user token refresh', 'guest login']) {
  test(`real stored photo bytes survive ${continuation}`, async (t) => {
    const draft = legacyPhotoDraft();
    if (continuation === 'guest login') draft.ownerUserId = null;
    const { h, storage } = storedDraftHarness(t, draft, {
      authUser: continuation === 'guest login' ? null : 'A', confirmed: false, savedFlow: { userId: null },
    });
    let tree = await h.settleApp();
    if (continuation !== 'same-user reload') {
      h.emit(continuation === 'guest login' ? 'SIGNED_IN' : 'TOKEN_REFRESHED', { user: { id: 'A' } });
      tree = await h.settleApp();
    }
    const local = choice(h, tree)?.props.sources.find((source) => source.id === 'local');
    assert.ok(local, 'Continuation remains available without another confirmation step');
    await choice(h, tree).props.onChoose('local');
    await h.settleApp();
    assert.equal(await readRestoredPhoto(storedDraftSlot.consumeStaged()?.payload), 'A synthetic private photo');
    assert.equal(hasStoredPendingDraft(), true);
    assert.deepEqual(storage.saved, draft);
  });
}
