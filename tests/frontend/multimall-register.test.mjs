import test, { before, after } from 'node:test';
import { createServer } from 'vite';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { readFileSync } from 'node:fs';
import { componentModule, hooks } from './helpers/reactHarness.mjs';

let vite, mock, db;
before(async () => {
  const root = new URL('../..', import.meta.url).pathname;
  vite = await createServer({ configFile: false, root, logLevel: 'silent', esbuild: { jsx: 'automatic' },
    optimizeDeps: { noDiscovery: true, include: [] }, resolve: { alias: { '@': `${root}/src` } }, server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom' });
  ({ mockAdapter: mock } = await vite.ssrLoadModule('/src/lib/api/mockAdapter.js'));
  ({ DB: db } = await vite.ssrLoadModule('/src/mock/db.js'));
});
after(() => vite?.close());
const walk = (node) => [node, ...React.Children.toArray(node?.props?.children).flatMap(walk)];
const loadDraft = () => import('../../src/features/editor/mallRegisterDraft.js');
const loadSession = () => import('../../src/features/editor/mallRegisterSession.js');
const clone = (value) => structuredClone(value);
const base = { projectId: 'a05-project', selectedMalls: ['cafe24', 'zigzag'], productName: '셔츠', basePrice: 39000,
  defaultQuantity: 0, sizes: ['FREE'], priceOverrides: {}, variantOverrides: [], mainAssetId: 'one', additionalAssetIds: ['two'],
  categories: {}, editorRevision: 2, publicationId: 'pub', confirmedSourcesByMall: { cafe24: { editorRevision: 2 } } };
const receipt = { batchId: 'batch', items: [{ mall: 'cafe24', status: 'accepted', jobId: 'cafe-job', registration: {
  jobId: 'cafe-job', mall: 'cafe24', status: 'done', outcome: 'created', setupStatus: 'complete', retryAction: 'none', productId: '42' } },
{ mall: 'zigzag', status: 'blocked', issues: [{ message: '고시를 채워 주세요' }] }], readyCount: 1 };
function fakeApi(patch = {}) {
  const calls = [];
  return { calls, listMallRegistrations: async () => ({ items: [], recoveries: { cafe24: [], zigzag: [] } }),
    createMallRegistrationBatch: async (request) => { calls.push(clone(request)); return clone(receipt); },
    getMallRegistrationBatch: async () => clone(receipt), ...patch };
}
const sessionFor = async (api, extra = {}) => (await loadSession()).createMallRegisterSession({ api, createId: () => 'batch', isCurrent: () => true, onChange() {}, ...extra });

test('mall menus open one modal with clicked mall selected', async () => {
  const { buildExportMenuItems } = await componentModule('src/features/editor/ExportMenu.jsx', ['buildExportMenuItems']);
  const clicked = [], malls = { items: ['cafe24', 'zigzag'].map((mall) => ({ mall, status: 'connected', registration: { status: 'available' } })) };
  const items = buildExportMenuItems({ malls, publishEnabled: true, onRegisterMall: (mall) => clicked.push(mall) });
  const { buildMallRegisterDraft } = await loadDraft();
  for (const mall of ['cafe24', 'zigzag']) {
    const item = items.find((item) => item.id === mall); assert.ok(!item.disabled); item.onSelect();
    assert.deepEqual(buildMallRegisterDraft({ projectId: 'p', product: {}, blocks: [], assets: [], defaults: {}, selectedMall: mall }).selectedMalls, [mall]);
  }
  assert.deepEqual(clicked, ['cafe24', 'zigzag']);
  let reconnect = 0;
  const reconnectItem = buildExportMenuItems({ malls: { items: [{ mall: 'cafe24', status: 'connected', registration: { status: 'reconnect_required' } }] }, publishEnabled: true, onConnect: () => reconnect++ }).find((item) => item.id === 'cafe24');
  assert.equal(reconnectItem.label, '카페24 다시 연결하기'); assert.equal(reconnectItem.disabled, false); reconnectItem.onSelect(); assert.equal(reconnect, 1);

});

test('preflight drives partial registration labels and excludes preparing malls', async () => {
  const { registrationSelection } = await loadDraft();
  const result = registrationSelection(['cafe24', 'zigzag', 'ably'], { items: [
    { mall: 'cafe24', status: 'ready' }, { mall: 'zigzag', status: 'blocked' }, { mall: 'ably', status: 'preparing' }] });
  assert.equal(result.label, '카페24에만 등록'); assert.deepEqual(result.readyMalls, ['cafe24']);
  assert.deepEqual(result.selectedMalls, ['cafe24', 'zigzag']);
  assert.equal(registrationSelection(['cafe24', 'zigzag'], { items: ['cafe24', 'zigzag'].map((mall) => ({ mall, status: 'ready' })) }).label, '2곳에 등록');
  assert.equal(registrationSelection(['zigzag'], { items: [{ mall: 'zigzag', status: 'preparing' }] }).label, '확인 후 등록');
});

test('notice and size edits save then republish before admission', async () => {
  const { resolveMallNotice, patchNoticeBlocks } = await import('../../src/features/editor/mallNotice.js');
  const { patchRegistrationSizes, prepareMallRegisterDraft } = await loadDraft();
  const { buildInfoBlock } = await import('../../src/features/editor/presets/infoPresets.js');
  const size = { ...buildInfoBlock('size_table', { columns: ['chest'], rows: [{ label: 'FREE', values: { chest: 52 } }] }), sectionId: 'section', sectionLayout: 'columns', layoutRowId: 'row' };
  const old = buildInfoBlock('required_notice', { fields: [{ key: 'name', label: '품명', value: '직접 쓴 이름' }] });
  const resolution = resolveMallNotice({ blocks: [size, old], product: { name: '셔츠', colors: [{ id: 'white', name: '화이트' }] }, defaults: { disclosure: { asManager: '담당', phone: '02-111', manufacturer: '제조자' } } });
  assert.equal(resolution.fields.find((f) => f.key === 'name').value, '직접 쓴 이름');
  for (const key of ['madeAt', 'warranty']) assert.equal(resolution.fields.find((f) => f.key === key).value, '');
  assert.equal(resolution.fields.find((f) => f.key === 'contact').value, '담당 02-111');
  assert.equal(resolution.fields.find((f) => f.key === 'sizes').value, 'FREE: chest 52 cm');
  const empty = resolveMallNotice({ blocks: [], product: {} });
  assert.equal(empty.fields.find((f) => f.key === 'sizes').value, '');
  const notices = patchNoticeBlocks([size, old], { fields: resolution.fields.map((f) => ({ ...f, value: f.key === 'madeAt' ? '2025년 1월' : f.value })) }, {});
  assert.equal(notices[1].id, old.id); assert.ok(notices[1].elements.some((e) => e.text === '2025년 1월'));
  const blocks = patchRegistrationSizes(notices, ['M', 'XL'], {});
  assert.equal(blocks[0].id, size.id); assert.equal(blocks[0].sectionId, 'section'); assert.equal(blocks[0].layoutRowId, 'row');
  assert.deepEqual(blocks[0].info.rows[0].values, {}); assert.deepEqual(blocks[0].info.rows[1].values, {});
  const measured = { ...size, info: { ...size.info, rows: [{ label: 'S', values: { chest: 45 } }, { label: 'M', values: { chest: 50 } }] } };
  const resized = patchRegistrationSizes([measured], ['M', 'L'], {});
  assert.equal(resized[0].info.rows[0].values.chest, 50); assert.deepEqual(resized[0].info.rows[1].values, {});
  assert.equal(measured.info.rows[0].values.chest, 45);
  // Exercise the actual Editor callback with the HTTP publication DTO, which omits
  // editorRevision from complete and status from the ready-only listing.
  const editor = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
  const callbackSource = editor.slice(editor.indexOf('  const publishForMalls ='), editor.indexOf('  const registrationAssets ='));
  const source = [], ref = { current: source }, snapshot = { current: source }, requests = [];
  const readyPublication = { publicationId: 'published', editorRevision: 3, preset: 'smartstore', files: [{ index: 1 }] };
  let listed = 0, captures = 0;
  const bindings = { realModelBlocked: false, genActiveRef: { current: false }, dlBusy: false, pubCaptureRef: { current: false }, pubRequestRef: { current: 0 },
    clientEvents: { getAccountGeneration: () => 0 }, pubScope: 'p:a', pubScopeRef: { current: 'p:a' }, mallSavedSource: snapshot, latestBlocks: ref,
    api: { editorRevisionOf: () => 3, peekEditorRevision: async () => 3, listDetailPublications: async () => { requests.push('list'); return { items: listed++ === 0 ? [] : [readyPublication] }; } },
    canvasRef: { current: { querySelectorAll: () => [{}] } }, setPubCapturing() {}, setPubBusy() {}, setPublicationInfo() {}, projectId: 'p', productName: '셔츠',
    publishDetailPage: async () => { captures++; return { publicationId: 'published', files: [{ index: 1 }] }; } };
  const publish = new Function(...Object.keys(bindings), `${callbackSource}; return publishForMalls;`)(...Object.values(bindings));
  assert.equal((await publish(() => {}, 3)).editorRevision, 3);
  assert.equal(captures, 1); assert.deepEqual(requests, ['list', 'list']);
  await publish(() => {}, 3); assert.equal(captures, 1);
  const order = [];
  const prepared = await prepareMallRegisterDraft(base, { waitForSave: async () => { order.push('save'); return 3; },
    onPublish: async (_assert, revision) => { order.push(`publish:${revision}`); return { publicationId: 'new', editorRevision: revision }; },
    preflight: async (draft) => { order.push(`preflight:${draft.publicationId}`); return { items: [{ mall: 'cafe24', status: 'ready', sourceVersions: { editorRevision: 3 } }] }; }, assertCurrent() {} });
  const api = fakeApi(); const session = await sessionFor(api); await session.restore('p'); await session.submit(prepared.draft);
  assert.deepEqual(order, ['save', 'publish:3', 'preflight:new']); assert.equal(api.calls[0].draft.editorRevision, 3);
});

test('editor conflict or stale account response prevents submission', async () => {
  const { prepareMallRegisterDraft } = await loadDraft(); let publishes = 0;
  await assert.rejects(prepareMallRegisterDraft(base, { waitForSave: async () => { throw Object.assign(new Error('conflict'), { code: 'editor_conflict' }); }, onPublish: () => publishes++, assertCurrent() {} }), /conflict/);
  assert.equal(publishes, 0);
  const { preflightForDisplayedSources } = await loadDraft();
  const ready = { items: [{ mall: 'cafe24', status: 'ready', issues: [], sourceVersions: { connectionVersion: 'shop-1', salesDefaultsRevision: 2, mallDefaultsRevision: 0, catalogVersion: 'catalog-v1', editorRevision: 3 } }] };
  const displayed = { defaults: { revision: 1 }, catalogs: { cafe24: { catalogVersion: 'catalog-v1' } }, mallDefaults: { cafe24: { revision: 0 } }, malls: { items: [{ mall: 'cafe24', connectionVersion: 'shop-1' }] } };
  const checked = preflightForDisplayedSources(ready, displayed);
  assert.equal(checked.items[0].status, 'blocked'); assert.equal(checked.items[0].issues[0].code, 'defaults_changed');
  assert.equal(ready.items[0].status, 'ready');
  displayed.defaults.revision = 2; assert.equal(preflightForDisplayedSources(ready, displayed).items[0].status, 'ready');
  displayed.malls.items[0].connectionVersion = 'shop-2'; assert.equal(preflightForDisplayedSources(ready, displayed).items[0].issues[0].code, 'connection_changed');

  let current = true, release; const api = fakeApi({ listMallRegistrations: () => new Promise((r) => { release = r; }) });
  const session = await sessionFor(api, { isCurrent: () => current }); const pending = session.restore('p'); current = false; release({ items: [], recoveries: {} });
  await assert.rejects(pending, /바뀌/); await assert.rejects(session.submit(base)); assert.equal(api.calls.length, 0);
});

test('price stock and image overrides preserve their confirmed meaning', async () => {
  const { normalizeMallRegisterDraft, validateMallRegisterDraft, registrationVariants } = await loadDraft();
  const draft = normalizeMallRegisterDraft({ ...base, priceOverrides: { cafe24: { amount: 42000, basis: 'tax_excluded' } }, defaultQuantity: '',
    additionalAssetIds: ['two', 'one', 'two', 'three'], variantOverrides: [{ variantKey: '["white","FREE"]', quantity: 0, additionalAmount: 2000 }] });
  assert.equal(draft.defaultQuantity, 0); assert.deepEqual(draft.additionalAssetIds, ['two', 'three']); assert.equal(draft.priceOverrides.cafe24.basis, 'tax_excluded');
  assert.equal(draft.variantOverrides[0].additionalAmount, 2000);
  assert.equal(registrationVariants([{ id: 'a|b', name: '화이트' }], ['M|L'])[0].variantKey, '["a|b","M|L"]');
  for (const patch of [{ basePrice: true }, { defaultQuantity: -1 }, { defaultQuantity: 0.5 }, { sizes: ['M', ' M '] }]) assert.ok(validateMallRegisterDraft({ ...base, ...patch }).length);
});

test('lost batch response retries the identical key and frozen draft', async () => {
  const requests = []; const api = fakeApi({ createMallRegistrationBatch: async (request) => { requests.push(clone(request)); if (requests.length === 1) throw new Error('접수 응답 유실'); return receipt; } });
  const session = await sessionFor(api); await session.restore('p'); const input = clone(base);
  await assert.rejects(session.submit(input)); input.basePrice = 999;
  await session.submit(input); assert.deepEqual(requests[0], requests[1]); assert.equal(requests[1].draft.basePrice, 39000);
});

test('mixed results retry only failed mall and resume setup on the same job', async () => {
  const mixed = clone(receipt); mixed.items[1] = { mall: 'zigzag', status: 'accepted', jobId: 'zig-job', registration: { mall: 'zigzag', jobId: 'zig-job', status: 'error', outcome: 'failed', retryAction: 'new_request' } };
  const resumes = []; const api = fakeApi({ getMallRegistrationBatch: async () => mixed,
    resumeMallRegistration: async (jobId, request) => { resumes.push([jobId, request]); return { jobId, mall: 'cafe24', status: 'pending', retryAction: 'none', outcome: null }; } });
  const session = await sessionFor(api); await session.restore('p'); await session.submit(base); await session.refresh();
  await session.retryFailed('zigzag', base); assert.deepEqual(api.calls[1].draft.selectedMalls, ['zigzag']);
  await session.refresh(); mixed.items[0].registration.setupStatus = 'needs_attention'; mixed.items[0].registration.retryAction = 'resume';
  await session.refresh(); await session.resume('cafe-job'); assert.equal(resumes[0][0], 'cafe-job'); assert.equal(api.calls.length, 2);
  const live = {
    cafe: { jobId: 'cafe', mall: 'cafe24', status: 'error', outcome: 'created', setupStatus: 'needs_attention', retryAction: 'resume' },
    zig: { jobId: 'zig', mall: 'zigzag', status: 'error', outcome: 'failed', retryAction: 'new_request' },
  };
  const batchOne = { batchId: 'first', items: Object.values(live).map((registration) => ({ mall: registration.mall, status: 'accepted', jobId: registration.jobId, registration: clone(registration) })) };
  const batchTwo = { batchId: 'second', items: [{ mall: 'zigzag', status: 'accepted', jobId: 'zig-2', registration: { jobId: 'zig-2', mall: 'zigzag', status: 'done', outcome: 'created', setupStatus: 'complete', retryAction: 'none' } }] };
  let admissions = 0;
  const retainedApi = fakeApi({ createMallRegistrationBatch: async () => clone(++admissions === 1 ? batchOne : batchTwo),
    getMallRegistrationBatch: async () => clone(batchTwo), getMallRegistration: async (id) => clone(live[id]),
    resumeMallRegistration: async () => { live.cafe = { ...live.cafe, status: 'pending', outcome: null, retryAction: 'none' }; return clone(live.cafe); } });
  const previousStorage = globalThis.sessionStorage, savedStorage = new Map();
  globalThis.sessionStorage = { getItem: (key) => savedStorage.get(key) || null, setItem: (key, value) => savedStorage.set(key, value), removeItem: (key) => savedStorage.delete(key) };
  const retained = await sessionFor(retainedApi); await retained.restore('p'); await retained.submit(base);
  await retained.retryFailed('zigzag', base); await retained.resume('cafe');
  live.cafe = { ...live.cafe, status: 'done', outcome: 'created', setupStatus: 'complete', retryAction: 'none' };
  const reopenedRetained = await sessionFor(retainedApi);
  const restoredRetained = await reopenedRetained.restore('p'); assert.equal(restoredRetained.items.find((item) => item.mall === 'cafe24').registration.setupStatus, 'complete');
  const refreshed = await retained.refresh(); assert.equal(refreshed.items.find((item) => item.mall === 'cafe24').registration.setupStatus, 'complete');
  assert.equal(admissions, 2);
  if (previousStorage === undefined) delete globalThis.sessionStorage; else globalThis.sessionStorage = previousStorage;
  const { MallRegisterResults } = await vite.ssrLoadModule('/src/features/editor/MallRegisterResults.jsx');
  const resultHtml = (view) => renderToStaticMarkup(React.createElement(MallRegisterResults, { batch: { items: [{ mall: 'cafe24', jobId: view.jobId, registration: view }] } }));
  assert.match(resultHtml({ jobId: 'setup', mall: 'cafe24', status: 'done', outcome: 'created', setupStatus: 'needs_attention', retryAction: 'resume', stage: 'setting_inventory', stages: {} }), /재고 설정을 마치지 못해 진열과 판매를 꺼 두었어요/);
  const activationHtml = resultHtml({ jobId: 'activation', mall: 'cafe24', status: 'done', outcome: 'created', setupStatus: 'needs_attention', retryAction: 'open_admin', stage: 'activating', stages: { activating: { status: 'unknown', sentAt: 'now' } } });
  assert.match(activationHtml, /판매 상태를 확인해 주세요/); assert.doesNotMatch(activationHtml, /꺼 두었어요/);
  const previousLocation = globalThis.location;
  try {
    for (const demo of ['partial', 'mixed', 'unknown', 'setup', 'activation', 'lost']) {
      globalThis.location = { search: `?mallRegistrationDemo=${demo}` };
      db.mallConnections = {}; db.mallDefaults = {}; db.mallSalesDefaults = null; db.mallRegistrations = []; db.mallRegistrationBatches = {};
      db.detailPublications = [{ publicationId: 'pub', projectId: 'a05-project', status: 'ready', editorRevision: 2, files: [{ index: 1 }] }];
      const malls = await mock.listMalls(); assert.equal(malls.items.find((item) => item.mall === 'cafe24').registration.status, 'available');
      const input = { ...clone(base), selectedMalls: ['cafe24', ...(['partial', 'mixed'].includes(demo) ? ['zigzag'] : [])] };
      const checked = await mock.preflightMallRegistration(input);
      if (demo === 'partial') { assert.deepEqual(checked.readyMalls, ['cafe24']); assert.equal(checked.items.find((item) => item.mall === 'zigzag').issues[0].action, 'fill_notice'); }
      input.confirmedSourcesByMall = Object.fromEntries(checked.items.filter((item) => item.status === 'ready').map((item) => [item.mall, item.sourceVersions]));
      const request = { batchId: crypto.randomUUID(), draft: input };
      let accepted;
      if (demo === 'lost') { await assert.rejects(mock.createMallRegistrationBatch(request)); accepted = await mock.createMallRegistrationBatch(request); }
      else accepted = await mock.createMallRegistrationBatch(request);
      await new Promise((resolve) => setTimeout(resolve, 5));
      const batch = await mock.getMallRegistrationBatch(accepted.batchId);
      const cafe = batch.items.find((item) => item.mall === 'cafe24').registration;
      assert.equal('productNo' in cafe, false);
      if (demo === 'mixed') { assert.equal(cafe.outcome, 'created'); assert.equal(batch.items.find((item) => item.mall === 'zigzag').registration.outcome, 'failed'); }
      if (demo === 'unknown') { assert.equal(cafe.retryAction, 'verify'); assert.equal(cafe.outcome, 'unknown'); }
      if (demo === 'setup') {
        assert.equal(cafe.retryAction, 'resume');
        const resumed = await mock.resumeMallRegistration(cafe.jobId, { requestKey: crypto.randomUUID() }); assert.equal(resumed.jobId, cafe.jobId);
      }
      if (demo === 'activation') { assert.equal(cafe.code, 'activation_unconfirmed'); assert.equal(cafe.retryAction, 'open_admin'); }
    }
  } finally { if (previousLocation === undefined) delete globalThis.location; else globalThis.location = previousLocation; }

});

test('unknown outcome only verifies and never creates again', async () => {
  const view = { jobId: 'unknown', mall: 'cafe24', status: 'done', outcome: 'unknown', retryAction: 'verify' }; let verified = 0;
  const api = fakeApi({ listMallRegistrations: async () => ({ items: [view], recoveries: { cafe24: [view], zigzag: [] } }),
    verifyMallRegistration: async () => { verified++; return view; } });
  const session = await sessionFor(api); await session.restore('p'); await assert.rejects(session.submit(base));
  await assert.rejects(session.retryFailed('cafe24', base)); await assert.rejects(session.resume('unknown'));
  await session.verify('unknown'); assert.equal(verified, 1); assert.equal(api.calls.length, 0);
});

test('timeout reopen and history failure never fall through to a new create', async () => {
  const active = { jobId: 'active', mall: 'cafe24', status: 'running', outcome: null, retryAction: 'none' };
  const api = fakeApi({ listMallRegistrations: async () => ({ items: [active], recoveries: { cafe24: [active], zigzag: [] } }), getMallRegistration: async () => active });
  const session = await sessionFor(api); await session.restore('p'); const view = await session.refresh({ now: Date.now() + 600001 });
  assert.equal(view.timedOut, true); assert.equal(view.items[0].registration.outcome, null); await assert.rejects(session.submit(base));
  const reopened = await sessionFor(api); assert.equal((await reopened.restore('p')).items[0].registration.jobId, 'active'); assert.equal(api.calls.length, 0);
  const broken = fakeApi({ listMallRegistrations: async () => { throw new Error('history failed'); } });
  const failed = await sessionFor(broken); await assert.rejects(failed.restore('p')); await assert.rejects(failed.submit(base)); assert.equal(broken.calls.length, 0);
});

test('keyboard focus returns to menu and error summary opens the invalid field', async () => {
  const { trapRegistrationFocus, openRegistrationIssue } = await loadDraft();
  let focused = '', restored = 0; const first = { focus: () => { focused = 'first'; } }, last = { focus: () => { focused = 'last'; } };
  const dialog = { querySelectorAll: () => [first, last], ownerDocument: { activeElement: last } };
  trapRegistrationFocus({ key: 'Tab', currentTarget: dialog, preventDefault() {} }); assert.equal(focused, 'first');
  dialog.ownerDocument.activeElement = first; trapRegistrationFocus({ key: 'Tab', shiftKey: true, currentTarget: dialog, preventDefault() {} }); assert.equal(focused, 'last');
  const details = { tagName: 'DETAILS', open: false, parentElement: null };
  const field = { parentElement: details, focus: () => { focused = 'price'; }, scrollIntoView() {} };
  openRegistrationIssue({ querySelector: () => field }, { path: 'priceOverrides.cafe24' }); assert.equal(details.open, true); assert.equal(focused, 'price');
  const runtime = hooks();
  const helpers = await loadDraft();
  const noticeHelpers = await import('../../src/features/editor/mallNotice.js');
  const { MallRegisterModal } = await componentModule('src/features/editor/MallRegisterModal.jsx', ['MallRegisterModal'], {
    ...helpers, ...noticeHelpers, ...runtime, Modal: ({ children }) => children, styles: {}, api: {}, clientEvents: { getAccountGeneration: () => 0 },
    createMallRegisterSession: () => ({ restore: async () => null, getState: () => ({}) }), trapRegistrationFocus, Button: 'button', useAuth: () => ({ session: { user: { id: 'a' } } }),
  });
  runtime.render(MallRegisterModal, { projectId: 'p', blocks: [], product: {}, assets: [], anchorRef: { current: { focus: () => restored++ } } });
  const cleanups = runtime.flushEffects(); cleanups.forEach((cleanup) => cleanup?.()); assert.equal(restored, 1);
  const source = readFileSync(new URL('../../src/features/editor/MallRegisterModal.jsx', import.meta.url), 'utf8');
  assert.match(source, /aria-modal="true"/); assert.match(source, /aria-labelledby/); assert.match(source, /tabIndex=\{-1\}/);
});
