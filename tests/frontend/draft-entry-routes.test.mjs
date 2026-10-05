import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { transformWithEsbuild } from 'vite';
import { getDraftDeviceNoun } from '../../src/lib/draftDeviceNoun.js';
import * as entry from '../../src/lib/draftEntry.js';
import * as flow from '../../src/lib/flowSession.js';
import { formatDraftRelativeTime, localDraftMeta } from '../../src/lib/draftSlot.js';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const app = read('src/apps/seller/App.jsx');
const shell = read('src/features/shell/shell.jsx');
const hook = read('src/lib/useDraftEntryActions.js').replace(/^import .*;\n/gm, '').replace(/^export /gm, '');
const inputSource = app.slice(app.indexOf('const FLOW_SCREEN_NAMES'), app.indexOf("/* '/' 복귀의 리다이렉트"));
const navSource = shell.slice(shell.indexOf('export function TopNav()'), shell.indexOf('/* 로그인 사용자 프로필'))
  .replace(/^export /gm, '');
const dialogSource = read('src/features/shell/DraftEntryDialog.jsx').replace(/^import .*;\n/gm, '').replace(/^export /gm, '');
const profileSource = shell.slice(shell.indexOf('function ProfileMenu()'), shell.indexOf('export function Stepper'));
const rootSource = app.slice(app.indexOf('function RootRedirect()'), app.indexOf('/* 모델 섹션')).replace("import.meta.env.VITE_API_MODE", "'http'");
const { code } = await transformWithEsbuild(`${hook}\n${inputSource}\n${navSource}\n${dialogSource}\n${rootSource}\n${profileSource}`, 'DraftEntryRoutes.jsx', { jsx: 'transform' });
const slot = (meta = {}) => ({ meta: { hasContent: true, deviceLabel: 'Android Chrome', photoCount: 2, updatedAt: 'v1', ...meta }, payload: { product: { name: 'remote' } } });

// 실제 라우트와 훅을 실행하고 브라우저, 저장소 경계만 메모리로 대체한다.
function harness({ remote = slot(), confirmed = true, local = null, loading = false,
  syncedAt = null, serverSyncedAt = null, takeover = null, mobile = false, mock = false, facemarket = false, returnIntent = '/create/storyboard' } = {}) {
  const calls = [];
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
    productInfoConfirmed: confirmed, projectGeneration: 0, resumePath: '/create/storyboard',
    async beginProject() {
      calls.push('begin');
      state.projectId = null;
      state.projectPersisted = false;
      state.productInfoConfirmed = false;
      state.projectGeneration += 1;
      flow.clearFlowSession();
    },
  };
  const storage = new Map();
  globalThis.sessionStorage = { getItem: (k) => storage.get(k), setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) };
  const draftSlot = {
    async get() { calls.push('get'); if (currentRemote instanceof Error) throw currentRemote; return currentRemote; },
    async takeover() { calls.push('takeover'); if (takeover) return takeover(); if (currentRemote instanceof Error) throw currentRemote; return currentRemote; },
    async removeForNewFlow() { calls.push('remove'); },
    async remove() { calls.push('remove'); },
    stage(value) { calls.push('stage'); staged = value; },
    hasStaged: () => Boolean(staged?.payload),
    hasPendingRemoval: () => false, getSyncedAt: () => syncedAt, getServerSyncedAt: () => serverSyncedAt,
  };
  const push = (message) => toasts.push(message);
  const useAppStore = (select) => select(state);
  useAppStore.getState = () => state;
  state.adoptProject = (id) => { calls.push(`adopt:${id}`); };
  state.confirmProductInfo = () => { calls.push('confirm'); };
  const bindings = {
    ...entry, ...flow, getDraftDeviceNoun, draftSlot, useAppStore, formatDraftRelativeTime, localDraftMeta,
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
    hasPendingDraft: () => Boolean(local), loadDraft: async () => local,
    Navigate: 'Navigate',
    readPostLogin: () => returnIntent, forgetPostLogin: () => calls.push('forget'),
    ensureDraftRemotePhotos: async (draft) => draft,
    promoteDraftToProject: async () => { calls.push('promote'); return { projectId: 'promoted' }; },
    clearDraft: async () => { calls.push('clear'); }, resetDraftSyncSingleFlight() {},
    DRAFT_SYNC_TIMEOUT_MS: 20000, setTimeout() {},
    ProductInput: 'ProductInput', Modal: 'Modal', Button: 'Button', Icon: 'Icon',
    Stepper: 'Stepper', ProfileMenuIcon: 'ProfileMenuIcon',
    document: { addEventListener() {}, removeEventListener() {} }, Link: 'Link', STEPPER_STEPS: [],
  };
  function navigate(path) { calls.push(`navigate:${path}`); }
  const components = new Function(...Object.keys(bindings), `${code}; return { ProductInputRoute, TopNav, ResumeChoiceModal, DraftEntryDialog, RootRedirect, ProfileMenu };`)(...Object.values(bindings));
  function render(name) {
    if (!frames.has(name)) frames.set(name, []);
    active = frames.get(name);
    cursor = 0;
    dirty = false;
    return components[name]();
  }
  async function settle(name) {
    let tree = render(name);
    for (let i = 0; i < 20; i += 1) {
      const pending = effects;
      effects = [];
      pending.forEach((effect) => effect());
      await new Promise(setImmediate);
      if (!dirty && !effects.length) return tree;
      tree = render(name);
    }
    throw new Error('route did not settle');
  }
  return { calls, toasts, auth, state, location, components, render, settle,
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

for (const remote of [null, new Error('offline')]) {
  test(`C2 actual route keeps first redirect and quick-repeat start-new for ${remote ? 'query failure' : 'no slot'}`, async () => {
    const h = harness({ remote });
    await h.settle('ProductInputRoute');
    assert.deepEqual(h.calls, ['get', 'navigate:/create/storyboard']);
    h.remount('ProductInputRoute');
    h.location.key = 'second';
    const tree = await h.settle('ProductInputRoute');
    assert.equal(tree.type, 'ProductInput');
    assert.deepEqual(h.calls, ['get', 'navigate:/create/storyboard', 'get', 'begin', 'navigate:/create/input']);
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
  assert.deepEqual(h.calls, ['get', 'takeover', 'begin', 'stage', 'navigate:/create/input']);
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
      ...(location === 'local' ? ['stage', 'takeover'] : ['remove', 'begin']),
      ...(location === 'nav' ? ['navigate:/create/input'] : []),
    ]);
  });
}

test('C5 actual pending remote card opens warning and partial confirmation before takeover', async () => {
  const h = harness({ remote: slot({ photosPending: true }) });
  const tree = await h.settle('ProductInputRoute');
  await tree.props.onChoose('remote');
  let warning = dialog(h, await h.settle('ProductInputRoute'));
  assert.equal(warning.props.dialog.kind, 'pending');
  assert.deepEqual(h.calls, ['get']);
  const warningUI = h.components.DraftEntryDialog(warning.props);
  const partial = find(warningUI, (node) => node.type === 'Button' && node.props.children.includes('지금 있는 사진 '));
  await partial.props.onClick();
  warning = dialog(h, await h.settle('ProductInputRoute'));
  assert.equal(warning.props.dialog.message, '아직 올라오지 않은 사진은 빠져요.');
  assert.deepEqual(h.calls, ['get', 'get']);
  h.setRemote(slot({ photosPending: true, photoCount: 4, updatedAt: 'v2' }));
  await warning.props.actions.confirm();
  warning = dialog(h, await h.settle('ProductInputRoute'));
  const confirmUI = h.components.DraftEntryDialog(warning.props);
  assert.deepEqual(find(confirmUI, (node) => node.type === 'h3').props.children, ['지금 있는 사진 4장으로 열기']);
  assert.deepEqual(h.calls, ['get', 'get', 'get']);
  await find(confirmUI, (node) => node.type === 'Button' && node.props.children.includes('지우고 계속')).props.onClick();
  assert.equal((await h.settle('ProductInputRoute')).type, 'ProductInput');
  assert.deepEqual(h.calls, ['get', 'get', 'get', 'get', 'takeover', 'begin', 'stage']);
});

for (const photosPending of [false, true]) {
  test(`matching localFallback opens before takeover settles and survives its failure (pending=${photosPending})`, async () => {
    const local = { updatedAt: 'local-v1', product: { name: 'local copy', colors: [] } };
    let rejectTakeover;
    const h = harness({
      confirmed: false, local, remote: slot({ photosPending }),
      syncedAt: 'local-v1', serverSyncedAt: 'v1',
      takeover: () => new Promise((resolve, reject) => { rejectTakeover = reject; }),
    });
    const tree = await h.settle('ProductInputRoute');
    assert.deepEqual(tree.props.sources.map((source) => source.id), ['remote']);
    assert.equal(tree.props.sources[0].localFallback, local);
    const opening = tree.props.onChoose('remote');
    assert.equal((await h.settle('ProductInputRoute')).type, 'ProductInput');
    assert.equal(h.staged.payload, local);
    assert.deepEqual(h.calls, ['get', 'stage', 'takeover']);
    rejectTakeover(new Error('offline'));
    await opening;
    assert.equal((await h.settle('ProductInputRoute')).type, 'ProductInput');
    assert.deepEqual(h.toasts, []);
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
    const login = find(tree, (node) => node.type === 'Button' && node.props.children.includes('로그인하고 사진 담기'));
    assert.ok(login);
    assert.ok(find(tree, (node) => node.props.children.includes('상품 사진을 여기서 담아두면, PC에서 바로 이어서 만들 수 있어요.')));
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
  assert.deepEqual(choice(h, tree).props.sources.map((source) => source.id), ['remote']);
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

for (const source of ['remote', 'local']) {
  test(`M4 mobile ${source} entry uses mobile copy and confirms deletion`, async () => {
    const local = source === 'local' ? {
      product: { name: 'local', colors: [{ images: [{ id: 'a' }, { id: 'b' }] }] }, updatedAt: 'local-v1',
    } : null;
    const h = harness({ mobile: true, confirmed: false, remote: source === 'remote' ? slot() : null, local });
    const tree = await h.settle('ProductInputRoute');
    const modal = h.components.ResumeChoiceModal(tree.props);
    assert.ok(find(modal, (node) => node.props.children.includes('담아둔 상품이 있어요')));
    const button = find(modal, (node) => node.props.children.includes('지우고 새 상품 담기'));
    await button.props.onClick();
    const warning = dialog(h, await h.settle('ProductInputRoute'));
    assert.equal(warning.props.dialog.message, '담아둔 사진 2장이 지워져요. 한 번에 한 상품만 담아둘 수 있어요.');
    assert.ok(!h.calls.includes('remove'));
    await warning.props.actions.confirm();
    assert.ok(h.calls.includes('remove'));
  });
}

test('M4 mobile card nouns and desktop modal copy', () => {
  for (const mobile of [true, false]) {
    const h = harness({ mobile });
    const modal = h.components.ResumeChoiceModal({ sources: [
      { id: 'local', title: '이 기기에서 입력하던 내용', meta: { deviceLabel: 'iPad Safari' } },
      { id: 'remote', title: '입력하던 상품 정보', meta: { deviceLabel: 'iPhone Safari' } },
    ] });
    assert.ok(find(modal, (node) => node.props.children.includes(mobile ? '담아둔 상품이 있어요' : '하던 작업이 있어요')));
    assert.ok(find(modal, (node) => node.props.children.includes(mobile ? '태블릿 작업 이어가기' : '이 기기에서 입력하던 내용')));
    assert.ok(find(modal, (node) => node.props.children.includes(mobile ? '스마트폰 작업 이어가기' : '입력하던 상품 정보')));
  }
});

test('entry card kind follows the displayed title and flow id, retaining selection', () => {
  for (const mobile of [true, false]) {
    const h = harness({ mobile });
    const chosen = [];
    const sources = [
      { id: 'flow', title: '만들던 상세페이지' },
      { ...entry.remoteDraftSource(slot().meta), id: 'phone' },
      { ...entry.remoteDraftSource(slot({ deviceLabel: 'iPad Safari' }).meta), id: 'tablet' },
      { id: 'local', title: '이 기기에서 입력하던 내용' },
    ];
    const modal = h.components.ResumeChoiceModal({ sources, onChoose: (id) => chosen.push(id) });
    const cards = find(modal, (node) => node.props.className === 'draft-entry-sources').props.children.flat(Infinity);
    assert.deepEqual(cards.map((node) => node.props.className), [
      'draft-entry-source is-flow', 'draft-entry-source is-handoff',
      'draft-entry-source is-handoff', 'draft-entry-source',
    ]);
    cards.forEach((node) => node.props.onClick());
    assert.deepEqual(chosen, ['flow', 'phone', 'tablet', 'local']);
  }
});
