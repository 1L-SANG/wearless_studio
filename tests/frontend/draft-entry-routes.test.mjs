import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { transformWithEsbuild } from 'vite';
import { getDraftDeviceNoun, isMobileDraftWriter } from '../../src/lib/draftDeviceNoun.js';
import { flowScreenName, shouldWarnFlowAbandon } from '../../src/lib/flowAbandon.js';
import * as entry from '../../src/lib/draftEntry.js';
import { checkDraftEntry, canAutoOpenInput } from '../../src/lib/draftEntryCheck.js';
import * as flow from '../../src/lib/flowSession.js';
import { createDraftSlotSync, formatDraftRelativeTime, localDraftMeta } from '../../src/lib/draftSlot.js';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const app = read('src/apps/seller/App.jsx');
const shell = read('src/features/shell/shell.jsx');
const hook = read('src/lib/useDraftEntryActions.js').replace(/^import .*;\n/gm, '').replace(/^export /gm, '');
const inputSource = app.slice(app.indexOf('function ProductInputRoute()'), app.indexOf("/* '/' 복귀의 리다이렉트"));
const navSource = shell.slice(shell.indexOf('export function TopNav()'), shell.indexOf('/* 로그인 사용자 프로필'))
  .replace(/^export /gm, '');
const dialogSource = read('src/features/shell/DraftEntryDialog.jsx').replace(/^import .*;\n/gm, '').replace(/^export /gm, '');
const profileSource = shell.slice(shell.indexOf('function ProfileMenu()'), shell.indexOf('export function Stepper'));
const rootSource = app.slice(app.indexOf('function RootRedirect()'), app.indexOf('/* 모델 섹션')).replace("import.meta.env.VITE_API_MODE", "'http'");
const introSource = ['HangTag.jsx', 'PhoneUploadIntro.jsx'].map((file) =>
  read(`src/features/shell/${file}`).replace(/^import .*;\n/gm, '').replace(/^export /gm, '')).join('\n');
const mobileDialogSource = read('src/features/shell/MobileDraftDialog.jsx').replace(/^import .*;\n/gm, '').replace(/^export /gm, '');
const guardSource = app.slice(app.indexOf('function RequireProject()'), app.indexOf('/* editor 는'));
const statusSource = ['DraftProductRow.jsx', 'HandoffStatusPage.jsx'].map((file) => read(`src/features/shell/${file}`).replace(/^import .*;\n/gm, '').replace(/^export /gm, '')).join('\n');
const { code } = await transformWithEsbuild(`${statusSource}\n${mobileDialogSource}\n${guardSource}\n${hook}\n${inputSource}\n${navSource}\n${dialogSource}\n${rootSource}\n${profileSource}\n${introSource}`, 'DraftEntryRoutes.jsx', { jsx: 'transform' });
const slot = (meta = {}) => ({ meta: { hasContent: true, deviceLabel: 'Android Chrome', photoCount: 2, updatedAt: 'v1', ...meta }, payload: { product: { name: 'remote' } } });

// 실제 라우트와 훅을 실행하고 브라우저, 저장소 경계만 메모리로 대체한다.
function harness({ remote = slot(), confirmed = true, local = null, loading = false,
  syncedAt = null, serverSyncedAt = null, syncedUserId = null, otherTab = false, token = null, savedFlow = {}, takeover = null, slotClient = null, mobile = false, mock = false, facemarket = false, returnIntent = '/create/storyboard' } = {}) {
  const calls = [];
  const windowEvents = new EventTarget();
  const documentEvents = new EventTarget();
  documentEvents.visibilityState = 'visible';
  const toasts = [];
  const frames = new Map();
  let active;
  let cursor;
  let effects = [];
  let dirty = false;
  let staged = null;
  let currentRemote = remote;
  const auth = { session: { user: { id: 'seller' } }, loading, signingOut: false, openLogin: (path) => calls.push(`login:${path}`) };
  const location = { key: 'first', pathname: '/library' };
  const state = {
    projectId: confirmed ? 'project' : null, projectPersisted: confirmed,
    productInfoConfirmed: confirmed, projectGeneration: 0, resumePath: '/create/storyboard', ...savedFlow,
    async beginProject({ isCurrent = () => true, onStarted = () => {} } = {}) {
      if (!isCurrent()) return;
      calls.push('begin');
      onStarted();
      state.projectId = null;
      state.projectPersisted = false;
      state.productInfoConfirmed = false;
      state.projectGeneration += 1;
      flow.clearFlowSession();
      return true;
    },
  };
  const storage = new Map();
  globalThis.sessionStorage = { getItem: (k) => storage.get(k), setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) };
  const draftSlot = slotClient || {
    async get() { calls.push('get'); if (currentRemote instanceof Error) throw currentRemote; return currentRemote; },
    async takeover() { calls.push('takeover'); if (takeover) return takeover(); if (currentRemote instanceof Error) throw currentRemote; return currentRemote; },
    resetIdentity() { calls.push('reset'); },
    async removeForNewFlow() { calls.push('remove'); },
    async remove() { calls.push('remove'); },
    stage(value) { calls.push('stage'); staged = value; },
    hasStaged: () => Boolean(staged?.payload),
    discard() {}, pauseEntryWrites() {}, resumeEntryWrites() {}, getIdentityEpoch: () => 0, hasOtherTabOwner: () => otherTab, getSyncedUserId: () => syncedUserId,
    continueWithoutSlotCheck() { if (token) return false; calls.push('continueUnchecked'); return true; },
    getToken: () => token, hasPendingRemoval: () => false, getSyncedAt: () => syncedAt, getServerSyncedAt: () => serverSyncedAt,
  };
  const toastOptions = [];
  const push = (message, options) => { toasts.push(message); toastOptions.push(options); };
  const useAppStore = (select) => select(state);
  useAppStore.getState = () => state;
  state.markFlowUsed = () => {};
  state.adoptProject = (id) => { calls.push(`adopt:${id}`); };
  state.confirmProductInfo = () => { calls.push('confirm'); };
  const bindings = {
    ...entry, ...flow, canAutoOpenInput, checkDraftEntry: (client, options) => checkDraftEntry(client, { ...options, setTimer: (fn, ms) => ms === 8000 ? setTimeout(fn, ms) : setImmediate(fn), clearTimer: clearTimeout }), getDraftDeviceNoun, isMobileDraftWriter, flowScreenName, shouldWarnFlowAbandon, draftSlot, useAppStore, formatDraftRelativeTime, localDraftMeta,
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
    useAuth: () => auth, useToast: () => ({ push }), useLocation: () => location,
    useNavigate: () => navigate,
    isMockMode: mock, IS_FACEMARKET: facemarket, isMobileDevice: () => mobile,
    hasPendingDraft: () => Boolean(local), loadDraft: async () => local, flushProductDraftSave: async () => null,
    Navigate: 'Navigate', Outlet: 'Outlet',
    readPostLogin: () => returnIntent, forgetPostLogin: () => calls.push('forget'),
    ensureDraftRemotePhotos: async (draft) => draft,
    promoteDraftToProject: async () => { calls.push('promote'); return { projectId: 'promoted' }; },
    clearDraft: async () => { calls.push('clear'); }, resetDraftSyncSingleFlight() {},
    DRAFT_SYNC_TIMEOUT_MS: 20000, setTimeout() {},
    ProductInput: 'ProductInput', Modal: 'Modal', Button: 'Button', Icon: 'Icon',
    Stepper: 'Stepper', ProfileMenuIcon: 'ProfileMenuIcon',
    p: new Proxy({}, { get: (_, key) => key }),
    s: new Proxy({}, { get: (_, key) => key }),
    window: windowEvents, document: documentEvents, Link: 'Link', STEPPER_STEPS: [],
  };
  function navigate(path, options) { calls.push(`navigate:${path}`); if (options?.state) { location.state = options.state; location.key = `nav-${calls.length}`; } }
  const components = new Function(...Object.keys(bindings), `${code}; return { HandoffStatusPage, RequireProject, MobileDraftDialog, MobileDraftStatus, DraftProductRow, ProductInputRoute, TopNav, ResumeChoiceModal, DraftEntryDialog, RootRedirect, ProfileMenu, PhoneUploadIntro, HangTag };`)(...Object.values(bindings));
  function render(name, props) {
    if (!frames.has(name)) frames.set(name, []);
    active = frames.get(name);
    cursor = 0;
    dirty = false;
    return components[name](props);
  }
  async function settle(name, props) {
    let tree = render(name, props);
    for (let i = 0; i < 20; i += 1) {
      const pending = effects;
      effects = [];
      pending.forEach((effect) => effect());
      await new Promise(setImmediate);
      if (!dirty && !effects.length && i >= 6) return tree;
      tree = render(name, props);
    }
    throw new Error('route did not settle');
  }
  return { calls, toasts, toastOptions, auth, state, windowEvents, documentEvents, draftSlot, location, components, render, settle,
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
  test(`E ineligible confirmed input asks on every entry and warns before starting new`, async () => {
    const h = harness({ remote });
    let tree = await h.settle('ProductInputRoute');
    assert.deepEqual(tree.props.sources.map((source) => source.id), ['flow']);
    assert.deepEqual(h.calls, ['get']);
    h.remount('ProductInputRoute');
    h.location.key = 'second';
    tree = await h.settle('ProductInputRoute');
    assert.deepEqual(tree.props.sources.map((source) => source.id), ['flow']);
    assert.ok(!h.calls.includes('begin'));
    let warning = h.render('ResumeChoiceModal', tree.props);
    find(warning, (n) => n.type === 'Button' && n.props.children.includes('새로 시작하기')).props.onClick();
    warning = h.render('ResumeChoiceModal', tree.props);
    assert.ok(uiButton(warning, '만들던 것 이어서 하기'));
    uiButton(warning, '새로 시작하기').props.onClick();
    tree = await h.settle('ProductInputRoute');
    assert.equal(tree.type, 'ProductInput');
    assert.deepEqual(h.calls, ['get', 'get', 'get', 'remove', 'begin']);
  });
}

test('C2 unauthenticated confirmed route preserves the auth-expiry continuation without querying', async () => {
  const h = harness();
  h.auth.session = null;
  const tree = await h.settle('ProductInputRoute');
  assert.equal(tree.type, 'ProductInput');
  assert.deepEqual(h.calls, []);
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
  test(`M1 mobile guest waits for login without input or slot work (confirmed=${confirmed})`, async () => {
    const h = harness({ mobile: true, confirmed });
    h.auth.session = null;
    const tree = await h.settle('ProductInputRoute');
    assert.equal(find(tree, (node) => node.type === 'ProductInput'), null);
    assert.equal(tree.type, h.components.PhoneUploadIntro);
    const intro = h.components.PhoneUploadIntro(tree.props);
    assert.equal(intro.props.className, 'page');
    assert.equal(intro.props['aria-labelledby'], 'phone-upload-title');
    const login = find(intro, (node) => node.type === 'button' && node.props.children.includes('로그인하고 사진 업로드'));
    assert.ok(login);
    assert.equal(login.props.className, 'btn btn-primary btn-block');
    assert.ok(find(intro, (node) => node.props.className === 'eyebrow' && node.props.children.includes('PHONE UPLOAD')));
    assert.ok(find(intro, (node) => node.type === 'h1' && node.props.id === 'phone-upload-title' && node.props.children.includes('휴대폰으로 사진부터 업로드해요')));
    assert.ok(find(intro, (node) => node.props.className === 'desc' && node.props.children.includes('로그인하고 사진을 업로드하면 PC에서 바로 이어서 만들어요.')));
    const tagNode = find(intro, (node) => node.type === h.components.HangTag);
    const tag = h.components.HangTag(tagNode.props);
    assert.equal(tag.props['aria-label'], 'UPLOAD ON PHONE 이라고 적힌 옷 태그');
    assert.equal(tag.props.viewBox, '0 0 280 230');
    assert.ok(find(tag, (node) => node.type === 'rect' && node.props.width === '22' && node.props.height === '38'));
    assert.ok(find(tag, (node) => node.props.className === 'mono accentFill' && node.props.children.includes('UPLOAD ON PHONE')));
    assert.ok(find(tag, (node) => node.props.className === 'mono mutedFill' && node.props.children.includes('FINISH ON PC')));
    assert.equal(find(tag, (node) => node.props.className === 'stroke care'), null);
    assert.deepEqual(h.calls, []);
    login.props.onClick();
    assert.deepEqual(h.calls, ['login:/create/input']);
  });
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
    test(`E ${component} new start protects only confirmed storyboard/mannequin: ${path}`, async () => {
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
      assert.equal(Boolean(warning), path.startsWith('/create/storyboard') || path.startsWith('/create/mannequin'));
      if (warning) {
        assert.ok(find(modal, (n) => n.type === 'h3' && n.props.children.includes('만들던 상세페이지는 다시 열 수 없어요')));
        assert.ok(!h.calls.includes('begin'));
        assert.ok(!h.calls.includes('remove'));
        warning.props.onClick();
        await h.settle(component);
        assert.ok(!h.calls.includes('begin'));
      }
    });
  }
}

for (const id of ['remote', 'local']) {
  test(`E choosing ${id} over flow warns before acting and danger continues`, async () => {
    const h = harness();
    const original = await h.settle('ProductInputRoute');
    const source = id === 'remote' ? original.props.sources[1] : { id: 'local', draft: { product: { name: 'local' } }, meta: {} };
    const chosen = [];
    const props = { sources: [original.props.sources[0], source], onChoose: (id) => chosen.push(id) };
    let modal = h.render('ResumeChoiceModal', props);
    find(modal, (n) => n.type === 'button' && n.props.key === id).props.onClick();
    modal = h.render('ResumeChoiceModal', props);
    assert.deepEqual(chosen, []);
    assert.equal(uiButton(modal, '이 상품으로 시작').props.variant, 'danger');
    uiButton(modal, '이 상품으로 시작').props.onClick();
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
    const h = harness({ confirmed: false, remote: null, savedFlow });
    const tree = await h.settle('ProductInputRoute');
    if (kind === 'recent') {
      assert.equal(tree.type, 'ProductInput');
      assert.deepEqual(h.toasts, ['이어서 열었어요']);
      assert.ok(h.calls.includes('navigate:/create/storyboard'));
    } else { assert.ok(choice(h, tree)); assert.deepEqual(h.toasts, []); }
  });
}

test('recent confirmed flow toast new start retains warning E before C4', async () => {
  const h = harness({ remote: null, savedFlow: { userId: 'seller', lastUsedAt: Date.now(), resumePath: '/create/storyboard' } });
  await h.settle('ProductInputRoute');
  assert.equal(h.toasts[0], '이어서 열었어요');
  h.setRemote(slot({ updatedAt: 'new-phone' })); h.toastOptions[0].action.onClick();
  const tree = await h.settle('ProductInputRoute');
  assert.equal(tree.props.abandonOnOpen, true);
  assert.equal(h.calls.includes('remove'), false);
  const warning = h.render('ResumeChoiceModal', tree.props);
  uiButton(warning, '새로 시작하기').props.onClick();
  assert.equal(dialog(h, await h.settle('ProductInputRoute')).props.dialog.kind, 'confirm');
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
