import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { transformWithEsbuild } from 'vite';
import { isMobileDraftWriter } from '../../src/lib/draftDeviceNoun.js';
import { draftSavedTime, createDraftEntryActions } from '../../src/lib/draftEntry.js';
import * as entry from '../../src/lib/draftEntry.js';
import * as flow from '../../src/lib/flowSession.js';
import * as localStore from '../../src/lib/draftStore.js';
import { draftSlot as localIdentity, localDraftMeta } from '../../src/lib/draftSlot.js';
import { checkDraftEntry, canAutoOpenInput } from '../../src/lib/draftEntryCheck.js';
import { draftStoreFixture, drainDraftTasks } from './helpers/draft-store-fixture.mjs';
import { createProductPhotoPreviewRegistry } from '../../src/features/product-input/productPhotoPreviewRegistry.js';
import { getBaseSlotUploadRoom, getPendingTileCount, PENDING_TILE_DELAY_MS } from '../../src/features/product-input/pendingTiles.js';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const clean = (source) => source.replace(/^import [\s\S]*?;\n/gm, '').replace(/^export default .*;$/gm, '').replace(/^export /gm, '');
const app = read('src/apps/seller/App.jsx');
const inputRoute = app.slice(app.indexOf('function ProductInputRoute()'), app.indexOf("/* '/' 복귀의 리다이렉트"));
const { code } = await transformWithEsbuild([
  'src/features/product-input/ProductInput.jsx', 'src/features/product-input/MobileInputHandoff.jsx',
  'src/features/shell/MobileDraftDialog.jsx', 'src/features/shell/DraftProductRow.jsx', 'src/features/shell/HandoffStatusPage.jsx', 'src/features/shell/HangTag.jsx', 'src/features/shell/AddressCopyChip.jsx',
  'src/features/library/Library.jsx', 'src/lib/useDraftEntryActions.js', 'src/features/shell/DraftEntryDialog.jsx',
].map((path) => clean(read(path))).join('\n') + '\n' + inputRoute, 'MobileInput.jsx', { jsx: 'transform' });
const product = () => ({ name: '니트', colors: [{ id: 'base', isBase: true, images: [
  { id: 'front', slot: 'Front', src: '/front.jpg' }, { id: 'back', slot: 'Back', src: '/back.jpg' },
] }] });
const find = (tree, predicate) => {
  if (!tree || typeof tree !== 'object') return null;
  if (predicate(tree)) return tree;
  return (tree.props?.children || []).flat(Infinity).map((child) => find(child, predicate)).find(Boolean);
};
const text = (tree) => typeof tree === 'string' || typeof tree === 'number' ? String(tree)
  : (tree?.props?.children || []).flat(Infinity).map(text).join('');
const node = (tree, type) => find(tree, (value) => value.type === type || value.type?.name === type);
const button = (tree, label) => find(tree, (value) => value.type === 'Button' && text(value) === label);
const actionLabels = (tree) => !tree || typeof tree !== 'object' ? [] : [
  ...(tree.type === 'Button' ? [text(tree)] : []),
  ...(tree.props?.children || []).flat(Infinity).flatMap(actionLabels),
];

function harness({ mobile = true, analysis = null, saveAll, transcode, photos = product(), doneBlocked = false, persisted = false, stagedDraft = true, initialSavedMeta = null, session = { user: { id: 'seller' } }, draftStore = null, slotClient = null, previewRegistry = null } = {}) {
  const calls = [];
  const content = [];
  const onContentChange = (value) => content.push(value);
  const documentEvents = new EventTarget();
  documentEvents.visibilityState = 'visible';
  let pendingHandler, conflictHandler;
  const frames = new Map();
  let active, cursor, effects = [], dirty = false;
  let staged = stagedDraft ? { payload: { product: photos, analysis, composeMode: 'basic' } } : null;
  const state = { projectId: persisted ? 'desktop' : null, projectPersisted: persisted, composeMode: 'basic', mannequinJob: {}, projectGeneration: 0,
    markFlowUsed() {}, resumePath: '/create/input', restoreComposeMode() {}, adoptProject(id) { calls.push(['adopt', id]); }, async beginProject() { calls.push('begin'); }, setInputPromotionLocked() {} };
  const useAppStore = (select) => select(state);
  useAppStore.getState = () => state;
  const location = { key: 'input', pathname: '/create/input', state: null };
  const slot = slotClient || {
    onPhotosPending(handler) { pendingHandler = handler; handler(true); return () => { pendingHandler = null; }; },
    retryFailedUploads() { calls.push('retryUploads'); },
    async checkOwnership() { calls.push('ownership'); },
    configure() {}, onConflict: (handler) => { conflictHandler = handler; return () => {}; },
    getIdentityEpoch: () => 0,
    resetIdentity() { calls.push('reset'); }, activate() {}, queue(snapshot) { calls.push(['queue', snapshot]); },
    consumeStaged() { const next = staged; staged = null; return next; },
    get: async () => ({ meta: { photoCount: 2, updatedAt: 'v1' } }),
    getServerSyncedAt: () => 'v1', hasPendingRemoval: () => false,
    removeForNewFlow: async () => { calls.push('remove'); },
    saveAll: async (snapshot) => { calls.push(['saveAll', snapshot]); return saveAll ? saveAll(snapshot) : { status: 'saved', photoCount: 2 }; },
  };
  const bindings = {
    useClientErrorCapture() {}, ClientErrorCapture: () => null, recordClientAction() {},
    ...entry, ...flow, checkDraftEntry, canAutoOpenInput, localDraftMeta,
    flowRouteSeenThisSession: false, flowDocumentEntryId: 'document',
    flowScreenName: () => '입력 화면', shouldWarnFlowAbandon: () => false,
    React: { createElement: (type, props, ...children) => ({ type, props: { ...props, children } }), Fragment: 'fragment' },
    useState(initial) {
      const frame = active, index = cursor++;
      if (!(index in frame)) frame[index] = typeof initial === 'function' ? initial() : initial;
      return [frame[index], (value) => { frame[index] = typeof value === 'function' ? value(frame[index]) : value; dirty = true; }];
    },
    useRef(initial) { const index = cursor++; return active[index] ||= { current: initial }; },
    useEffect(effect, deps) {
      const index = cursor++, prior = active[index];
      if (!prior || !deps.every((value, i) => Object.is(value, prior.deps[i]))) {
        const record = { deps }; active[index] = record;
        effects.push(() => { prior?.cleanup?.(); record.cleanup = effect(); });
      }
    },
    useNavigate: () => (path) => calls.push(['navigate', path]), useLocation: () => location,
    useAuth: () => ({ session, loading: false }),
    useDoneGuard: () => doneBlocked, useToast: () => ({ push: (...args) => calls.push(['toast', ...args]) }),
    useQuery: ({ queryKey }) => ({ data: queryKey.length === 1 ? [{ id: 'finished', title: '완성본' }] : [], isLoading: false }),
    useAppStore, isMobileDevice: () => mobile, isMockMode: false, draftSlot: slot, createDraftEntryActions, isMobileDraftWriter, draftSavedTime,
    api: { getProduct: async (id) => { calls.push(['getProduct', id]); return photos; },
      getAnalysis: async () => ({ projectId: 'desktop', fit: 'regular' }), getCatalogs: async () => ({ angleSlots: ['Front', 'Back'], swatchColors: [] }),
      analyzeProduct: async () => { calls.push('analyze'); return { suggestedName: '니트' }; } },
    createProductPhotoPreviewRegistry: previewRegistry || (() => ({ sync() {}, dispose() {}, release() {}, displayUrl: (_id, src) => src })),
    createTrailingPatchScheduler: () => ({ flush() {}, schedule() {} }),
    generationWorkWarningKind: () => 'none', restoreDraftProduct: (draft) => draft.product,
    normalizeAnalysisFit: (value) => value, isAnalysisRunning: () => false,
    queueProductDraftSave: () => {}, flushProductDraftSave: async () => {}, hasPendingDraft: () => false,
    ...(draftStore || {}),
    formatDraftRelativeTime: () => '1분 전', getBaseSlotUploadRoom, getPendingTileCount, PENDING_TILE_DELAY_MS,
    invalidateStoryboardForProductPhotoEdit() {}, invalidateStoryboardEntryPrefetch() {},
    analysisResponseProductPatch: () => ({}), autofillColorGroups: (colors) => colors,
    mergeProductOwnedAnalysisFields: (value) => value, photoCaption: () => '사진', uid: () => 'new-photo',
    looksLikeImageFile: () => true,
    toUploadableImages: transcode || (async () => ({ files: [], failed: [] })),
    getUploadValidationError: () => null, MAX_UPLOAD_BYTES: 25000000,
    window: { addEventListener() {}, removeEventListener() {}, scrollTo() {}, location: { origin: 'https://ai.wearless.kr', host: 'ai.wearless.kr' } },
    document: documentEvents,
    copyHandoffAddress: async (pushToast) => pushToast('주소를 복사했어요'),
    p: new Proxy({}, { get: (_, key) => key }),
    s: new Proxy({}, { get: (_, key) => key }),
  };
  for (const name of ['Button', 'Modal', 'Icon', 'IconButton', 'ErrorState', 'UploadPendingTile', 'PageHead',
    'ResumeChoiceModal', 'PhoneUploadIntro', 'WizardCTA', 'DoneGuardModal', 'CreditShortfallModal', 'AnalysisForm', 'AnalysisProgress', 'AnalysisSkeleton']) bindings[name] = name;
  const components = new Function(...Object.keys(bindings), `${code}; return { ProductInputRoute, HandoffStatusPage, MobileDraftDialog, MobileDraftStatus, DraftProductRow, Library, ProductInput, ColorImageGroup, AddDrop, InputMainAction, MobileSaveModal, MobileSaveStatus, MobileAnalyzedNotice, EditingRightsLock, DraftEntryDialog, HangTag, AddressCopyChip };`)(...Object.values(bindings));
  function render(name = 'ProductInput', props = {}) {
    if (!frames.has(name)) frames.set(name, []);
    active = frames.get(name); cursor = 0; dirty = false;
    return components[name](name === 'ProductInput' ? { onContentChange, initialSavedMeta, ...props } : props);
  }
  async function settle(name = 'ProductInput') {
    let tree = render(name);
    for (let i = 0; i < 30; i++) {
      const batch = effects; effects = []; batch.forEach((effect) => effect());
      await new Promise(setImmediate);
      if (!dirty && !effects.length) return tree;
      tree = render(name);
    }
    throw new Error('did not settle');
  }
  return { calls, content, location, render, settle, components, documentEvents, slot,
    emitConflict: (meta) => conflictHandler(meta),
    emitPending: (value) => pendingHandler?.(value),
    unmount(name) { frames.get(name)?.forEach((record) => record?.cleanup?.()); frames.delete(name); },
    cleanup() { for (const frame of frames.values()) frame.forEach((record) => record?.cleanup?.()); },
    expand: (tree, name) => render(name, node(tree, name).props),
  };
}

test('M2 mobile CTA saves the full snapshot, locks editing, and never analyzes', async () => {
  let finish;
  const h = harness({ saveAll: () => new Promise((resolve) => { finish = resolve; }) });
  let tree = await h.settle();
  const cta = h.expand(tree, 'InputMainAction');
  assert.equal(button(cta, '저장하고 PC에서 이어하기').props.disabled, false);
  const saving = button(cta, '저장하고 PC에서 이어하기').props.onClick();
  tree = await h.settle();
  const modal = h.expand(tree, 'MobileSaveModal');
  assert.equal(text(node(modal, 'h3')), '사진을 저장하고 있어요');
  assert.equal(text(node(modal, 'p')), '다 저장될 때까지 이 화면을 닫지 말아 주세요.');
  assert.equal(node(modal, 'Button'), undefined);
  assert.equal(modal.props.onClose, undefined);
  assert.ok(node(modal, 'UploadPendingTile'));
  assert.equal(node(tree, 'ColorImageGroup').props.photosLocked, true);
  const snapshot = h.calls.find((call) => call[0] === 'saveAll')[1];
  assert.equal(snapshot.product.name, '니트');
  assert.equal(snapshot.analysis, null);
  assert.equal(snapshot.composeMode, 'basic');
  assert.ok(snapshot.localUpdatedAt);
  finish({ status: 'saved', photoCount: 2 }); await saving;
  tree = await h.settle();
  assert.equal(node(tree, 'MobileSaveStatus'), undefined);
  const saved = h.expand(tree, 'MobileSaveModal');
  assert.equal(text(node(saved, 'h3')), '사진 2장을 저장했어요');
  assert.equal(text(node(saved, 'p')), 'PC에서도 같은 계정으로 로그인해 주세요.');
  const tag = h.expand(saved, 'HangTag');
  assert.equal(tag.props['aria-label'], '사진 2장 저장됨이라고 적힌 옷 태그');
  assert.equal(tag.props.className, 'pi-save-tag');
  const count = find(tag, (value) => value.type === 'text' && text(value) === '2');
  assert.equal(count.props.fontSize, '46');
  assert.equal(count.props.y, '146');
  assert.ok(text(tag).includes('PHOTOS SAVED'));
  assert.ok(text(tag).includes('KEEP 7 DAYS'));
  assert.equal(find(tag, (value) => value.props.className === 'stroke care'), undefined);
  const chip = h.expand(saved, 'AddressCopyChip');
  assert.equal(chip.props['aria-label'], '주소 복사');
  assert.ok(text(chip).includes('ai.wearless.kr'));
  await chip.props.onClick();
  assert.ok(h.calls.some((call) => call[0] === 'toast' && call[1] === '주소를 복사했어요'));
  assert.equal(button(saved, 'PC로 링크 보내기'), undefined);
  assert.equal(find(saved, (value) => value.props.className === 'handoff-actions'), undefined);
  assert.deepEqual(actionLabels(saved), ['확인']);
  assert.equal(button(saved, '확인').props.variant, 'primary');
  assert.equal(button(saved, '확인').props.block, true);
  button(saved, '확인').props.onClick();
  tree = await h.settle();
  const banner = h.expand(tree, 'MobileSaveStatus');
  assert.equal(banner.props.role, 'status');
  assert.ok(text(banner).includes('사진 2장을 저장했어요'));
  assert.ok(text(banner).includes('PC에서 ai.wearless.kr로 이어서 만들어요.'));
  assert.equal(node(banner, 'Icon').props.name, 'check');
  assert.equal(node(tree, 'ColorImageGroup').props.readOnly, true);
  button(h.expand(tree, 'InputMainAction'), '사진 고치기').props.onClick();
  tree = await h.settle();
  node(tree, 'ColorImageGroup').props.onRemove('front');
  tree = await h.settle();
  const changed = h.expand(tree, 'MobileSaveStatus');
  assert.ok(text(changed).includes('사진이 바뀌었어요'));
  assert.ok(text(changed).includes('아래 버튼을 다시 눌러 저장해 주세요.'));
  assert.ok(changed.props.className.includes('pi-save-status-changed'));
  assert.equal(node(changed, 'Icon').props.name, 'refresh');
  assert.ok(!text(changed).includes('사진 2장을 저장했어요'));
  node(tree, 'ColorImageGroup').props.onAddFiles('Front', [{ src: '/new.jpg' }]);
  tree = await h.settle();
  const again = button(h.expand(tree, 'InputMainAction'), '저장하고 PC에서 이어하기').props.onClick();
  finish({ status: 'saved', photoCount: 2 }); await again;
  assert.equal(h.calls.filter((call) => call[0] === 'saveAll').length, 2);
  assert.ok(!h.calls.includes('analyze'));
});

test('M2 conversion signal blocks mobile save until every slot finishes; desktop still analyzes', async () => {
  for (const mobile of [true, false]) {
    const h = harness({ mobile });
    let tree = await h.settle();
    const group = node(tree, 'ColorImageGroup');
    if (mobile) {
      group.props.onPendingChange('base', 'Front', 1);
      group.props.onPendingChange('base', 'Back', 1);
      tree = await h.settle();
      let cta = h.expand(tree, 'InputMainAction');
      assert.equal(button(cta, '저장하고 PC에서 이어하기').props.disabled, true);
      assert.ok(text(cta).includes('사진을 불러오는 중이에요'));
      group.props.onPendingChange('base', 'Front', 0);
      tree = await h.settle();
      assert.equal(button(h.expand(tree, 'InputMainAction'), '저장하고 PC에서 이어하기').props.disabled, true);
      group.props.onPendingChange('base', 'Back', 0);
      tree = await h.settle();
      assert.equal(button(h.expand(tree, 'InputMainAction'), '저장하고 PC에서 이어하기').props.disabled, false);
    } else {
      assert.equal(typeof group.props.onPendingChange, 'function');
      await button(h.expand(tree, 'InputMainAction'), 'AI 분석하기').props.onClick();
      assert.ok(h.calls.includes('analyze'));
      assert.ok(!h.calls.some((call) => call[0] === 'saveAll'));
    }
  }
});

test('M3 failed save retries through saveAll and can close; locked has only confirmation', async () => {
  for (const status of ['failed', 'locked']) {
    let attempts = 0;
    const h = harness({ saveAll: async () => ({ status: attempts++ ? 'saved' : status, photoCount: 2 }) });
    let tree = await h.settle();
    await button(h.expand(tree, 'InputMainAction'), '저장하고 PC에서 이어하기').props.onClick();
    tree = await h.settle();
    const modal = h.expand(tree, 'MobileSaveModal');
    assert.equal(text(node(modal, 'h3')), status === 'failed' ? '사진을 다 저장하지 못했어요' : '다른 곳에서 이 상품을 이어서 작업하고 있어요');
    assert.equal(text(node(modal, 'p')), status === 'failed' ? '인터넷 연결을 확인하고 다시 시도해 주세요.' : '지금 화면의 저장을 멈췄어요.');
    if (status === 'failed') {
      assert.ok(button(modal, '닫기'));
      await button(modal, '다시 시도').props.onClick();
      tree = await h.settle();
      assert.equal(text(node(h.expand(tree, 'MobileSaveModal'), 'h3')), '사진 2장을 저장했어요');
      assert.equal(attempts, 2);
    } else {
      assert.equal(button(modal, '다시 시도'), undefined);
      button(modal, '확인').props.onClick();
      tree = await h.settle();
      assert.equal(h.expand(tree, 'MobileSaveModal'), null);
    }
  }
});

test('M2-1 restored analyzed mobile draft is read-only and requires confirmation to delete', async () => {
  const analysis = { fit: 'original', preserved: true };
  for (const mobile of [true, false]) {
    const h = harness({ mobile, analysis });
    let tree = await h.settle();
    assert.equal(node(tree, 'InputMainAction'), undefined);
    if (!mobile) { assert.ok(node(tree, 'AnalysisForm')); continue; }
    assert.equal(node(tree, 'AnalysisForm'), undefined);
    const notice = h.expand(tree, 'MobileAnalyzedNotice');
    assert.ok(text(notice).includes('이 상품은 PC에서 분석까지 마쳤어요. PC에서 이어서 진행해 주세요.'));
    const group = h.expand(tree, 'ColorImageGroup');
    assert.equal(node(group, 'AddDrop'), undefined);
    assert.equal(node(group, 'ColorSwatchPicker'), undefined);
    assert.equal(find(group, (value) => value.props.className === 'rm'), undefined);
    assert.ok(!h.calls.some((call) => call[0] === 'queue'));
    await button(notice, '지우고 새 상품 업로드').props.onClick();
    tree = await h.settle();
    const wrapper = h.expand(tree, 'DraftEntryDialog');
    const warning = h.render('MobileDraftDialog', wrapper.props);
    assert.ok(text(warning).includes('한 번에 한 상품만 저장할 수 있어요.'));
    assert.ok(!h.calls.includes('remove'));
    await button(warning, '버리고 새로 올리기').props.onClick();
    assert.deepEqual(h.calls.filter((call) => typeof call === 'string'), ['remove', 'begin']);
    assert.deepEqual(analysis, { fit: 'original', preserved: true });
  }
});

test('M6 gone lock mobile actions and desktop copy; conflict remains identical', () => {
  const h = harness();
  for (const mobile of [true, false]) {
    const calls = [];
    const tree = h.render('EditingRightsLock', { mobile, meta: { state: 'gone' },
      onDiscard: () => calls.push('discard'), onRestartLocal: () => calls.push('restart') });
    assert.equal(text(node(tree, 'h3')), mobile ? '이 상품은 다른 곳에서 마무리됐거나, 보관 기간이 지나 정리됐어요.' : '다른 곳에서 이 작업을 끝내거나 새로 시작했어요');
    const discard = button(tree, mobile ? '새 상품 업로드' : '지우고 새로 시작');
    assert.equal(discard.props.variant, mobile ? 'primary' : 'ghost');
    discard.props.onClick();
    button(tree, mobile ? '이 사진으로 다시 업로드' : '이 내용 계속 쓰기').props.onClick();
    assert.deepEqual(calls, ['discard', 'restart']);
  }
  const props = { meta: { state: 'conflict' }, onReclaim() {} };
  assert.deepEqual(h.render('EditingRightsLock', { ...props, mobile: true }), h.render('EditingRightsLock', { ...props, mobile: false }));
});

for (const mobile of [true, false]) {
  test(`M2 ${mobile ? 'mobile' : 'desktop'} add tile has one short caption`, async () => {
    const h = harness({ mobile });
    const tree = await h.settle();
    const group = h.expand(tree, 'ColorImageGroup');
    const caption = find(group, (value) => value.props.className === 'add-cap');
    assert.deepEqual(caption.props.children, ['이미지 업로드']);
    assert.ok(!text(group).includes('업로드해주세요'));
  });

  test(`M2 ${mobile ? 'mobile' : 'desktop'} needs both base Front and Back photos`, async () => {
    const photos = product();
    photos.colors[0].images.pop();
    const h = harness({ mobile, photos });
    const tree = await h.settle();
    assert.equal(node(h.expand(tree, 'InputMainAction'), 'Button').props.disabled, true);
  });
}

test('M2 actual picker reports HEIC conversion before the delayed placeholder and clears after conversion', async () => {
  let finish;
  const h = harness({ transcode: () => new Promise((resolve) => { finish = resolve; }) });
  const tree = await h.settle();
  const group = h.expand(tree, 'ColorImageGroup');
  const picker = h.render('AddDrop', node(group, 'AddDrop').props);
  node(picker, 'input').props.onChange({ target: { files: [{ name: 'photo.heic', type: 'image/heic' }], value: 'photo.heic' } });
  let cta = h.expand(h.render(), 'InputMainAction');
  assert.equal(node(cta, 'Button').props.disabled, true);
  assert.ok(text(cta).includes('사진을 불러오는 중이에요'));
  finish({ files: [], failed: [] });
  await new Promise(setImmediate);
  cta = h.expand(await h.settle(), 'InputMainAction');
  assert.equal(node(cta, 'Button').props.disabled, false);
});

test('mobile photo and swatch grids use two full-width columns without a width gate', () => {
  const css = read('src/styles/features.css');
  const mobile = css.slice(css.indexOf('.wizard.pi-handoff'), css.indexOf('@media (max-width: 560px)', css.indexOf('.wizard.pi-handoff')));
  assert.doesNotMatch(mobile, /@media|word-break/);
  assert.match(mobile, /\.wizard\.pi-handoff \{ padding-inline: var\(--sp-16\); \}/);
  assert.match(mobile, /\.pi-handoff \.surface\.pi-images \{ padding-inline: var\(--sp-16\); \}/);
  assert.match(mobile, /\.pi-handoff \.color-group \{ padding-inline: var\(--sp-12\); \}/);
  assert.match(mobile, /\.pi-handoff \.slot-well \{ border: 0; background: none; box-shadow: none; padding: 0; \}/);
  assert.match(mobile, /\.pi-handoff \.slot-well-head \{ margin-bottom: var\(--sp-8\); \}/);
  assert.match(mobile, /\.pi-handoff \.slot-tiles \{[^}]*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\); gap: var\(--sp-8\)/);
  assert.match(mobile, /\.pi-handoff \.tile \{ width: 100%; height: auto; aspect-ratio: 156 \/ 200;/);
  assert.match(mobile, /\.pi-handoff \.slot-well \.tile \{ aspect-ratio: 3 \/ 4;/);
  assert.match(mobile, /\.pi-handoff \.swatch-grid \{[^}]*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\); justify-items: stretch;[^}]*height: calc\(var\(--pi-swatch-height\) \* 2[^}]*overflow-y: auto;/);
  assert.doesNotMatch(mobile, /mask-image/);
  assert.match(mobile, /padding-right: var\(--sp-12\)/);
  assert.match(mobile, /\.pi-handoff \.swatch, \.pi-handoff \.color-name-input-wrap, \.pi-handoff \.color-name-input \{[^}]*width: 100%;/);
  assert.match(mobile, /\.pi-handoff \.color-name-input \{ min-width: 100%; max-width: 100%;/);
  assert.doesNotMatch(css, /\.pi-handoff \.tile\.add/);
});

test('header width fallbacks hide crowded controls independently of device detection', () => {
  const css = read('src/styles/app.css');
  assert.match(css, /@media \(max-width: 720px\) \{\s*\.topnav \.nav-stepper \{ display: none; \}/);
  assert.match(css, /@media \(max-width: 520px\) \{\s*\.topnav \{ gap: var\(--sp-12\); padding-inline: var\(--sp-16\); \}\s*\.topnav \.nav-links, \.topnav \.credit-badge, \.topnav \.plan-badge \{ display: none; \}\s*\.topnav \.nav-right \{ flex-shrink: 0; \}/);
  assert.match(css, /\.topnav-handoff \.nav-stepper,\s*\.topnav-handoff \.nav-links,\s*\.topnav-handoff \.credit-badge,\s*\.topnav-handoff \.plan-badge \{ display: none; \}/);
});

for (const mobile of [true, false]) {
  test(`swatch picker receives mobile=${mobile} from the input screen`, async () => {
    const h = harness({ mobile });
    const group = h.expand(await h.settle(), 'ColorImageGroup');
    assert.equal(node(group, 'ColorSwatchPicker').props.mobile, mobile);
  });
}

test('mobile library shows a PC notice without adopting or navigating', async () => {
  for (const mobile of [true, false]) {
    const h = harness({ mobile });
    const tree = await h.settle('Library');
    find(tree, (value) => value.props.className === 'lib-card').props.onClick();
    assert.deepEqual(h.calls, mobile ? [['toast', '완성본은 PC에서 열어 주세요']]
      : [['adopt', 'finished'], ['navigate', '/editor/finished']]);
  }
});

test('mobile input hides DoneGuardModal even for a persisted completed project', async () => {
  for (const mobile of [true, false]) {
    const h = harness({ mobile, doneBlocked: true });
    const tree = await h.settle();
    assert.equal(Boolean(node(tree, 'DoneGuardModal')), !mobile);
    h.cleanup();
  }
});

test('unresolved mobile save fails at 120 seconds and a late success updates the open modal', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let finish;
  const h = harness({ saveAll: () => new Promise((resolve) => { finish = resolve; }) });
  t.after(() => h.cleanup());
  let tree = await h.settle();
  const saving = button(h.expand(tree, 'InputMainAction'), '저장하고 PC에서 이어하기').props.onClick();
  t.mock.timers.tick(119999);
  tree = await h.settle();
  assert.equal(node(tree, 'MobileSaveModal').props.result.status, 'saving');
  t.mock.timers.tick(1);
  tree = await h.settle();
  assert.equal(node(tree, 'MobileSaveModal').props.result.status, 'failed');
  finish({ status: 'saved', photoCount: 2 }); await saving;
  tree = await h.settle();
  assert.equal(node(tree, 'MobileSaveModal').props.result.status, 'saved');
  button(h.expand(tree, 'MobileSaveModal'), '확인').props.onClick();
  tree = await h.settle();
  assert.equal(node(tree, 'ColorImageGroup').props.readOnly, true);
  assert.deepEqual(actionLabels(h.expand(tree, 'InputMainAction')), ['사진 고치기', '다음 상품 올리기']);
});

test('failed open modal waits for the final successful PUT after background uploads recover', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let attempts = 0, finish;
  const h = harness({ saveAll: () => ++attempts === 1 ? { status: 'failed' }
    : new Promise((resolve) => { finish = resolve; }) });
  t.after(() => h.cleanup());
  let tree = await h.settle();
  await button(h.expand(tree, 'InputMainAction'), '저장하고 PC에서 이어하기').props.onClick();
  tree = await h.settle();
  h.emitPending(false);
  tree = await h.settle();
  assert.equal(attempts, 2);
  assert.equal(node(tree, 'MobileSaveModal').props.result.status, 'failed');
  finish({ status: 'saved', photoCount: 2 });
  tree = await h.settle();
  assert.equal(node(tree, 'MobileSaveModal').props.result.status, 'saved');
  button(h.expand(tree, 'MobileSaveModal'), '확인').props.onClick();
  tree = await h.settle();
  assert.equal(node(tree, 'ColorImageGroup').props.readOnly, true);
  assert.deepEqual(actionLabels(h.expand(tree, 'InputMainAction')), ['사진 고치기', '다음 상품 올리기']);
});

test('visible saving screen retries failed uploads and checks ownership immediately', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const h = harness({ saveAll: () => new Promise(() => {}) });
  t.after(() => h.cleanup());
  const tree = await h.settle();
  button(h.expand(tree, 'InputMainAction'), '저장하고 PC에서 이어하기').props.onClick();
  await h.settle();
  h.documentEvents.visibilityState = 'hidden';
  h.documentEvents.dispatchEvent(new Event('visibilitychange'));
  assert.ok(!h.calls.includes('ownership'));
  h.documentEvents.visibilityState = 'visible';
  h.documentEvents.dispatchEvent(new Event('visibilitychange'));
  assert.ok(h.calls.includes('retryUploads'));
  assert.ok(h.calls.includes('ownership'));
});

test('closing a timed-out modal allows another save and ignores the old completion', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const finishes = [];
  const h = harness({ saveAll: () => new Promise((resolve) => finishes.push(resolve)) });
  t.after(() => h.cleanup());
  let tree = await h.settle();
  button(h.expand(tree, 'InputMainAction'), '저장하고 PC에서 이어하기').props.onClick();
  t.mock.timers.tick(120000);
  tree = await h.settle();
  button(h.expand(tree, 'MobileSaveModal'), '닫기').props.onClick();
  tree = await h.settle();
  assert.equal(node(tree, 'MobileSaveModal').props.result, null);
  button(h.expand(tree, 'InputMainAction'), '저장하고 PC에서 이어하기').props.onClick();
  finishes[0]({ status: 'saved', photoCount: 9 });
  tree = await h.settle();
  assert.equal(node(tree, 'MobileSaveModal').props.result.status, 'saving');
  finishes[1]({ status: 'saved', photoCount: 2 });
  tree = await h.settle();
  assert.equal(node(tree, 'MobileSaveModal').props.result.photoCount, 2);
});

test('gone save modal describes a finished or removed product and only offers confirmation', () => {
  const h = harness();
  const tree = h.render('MobileSaveModal', { result: { status: 'gone' } });
  assert.equal(text(node(tree, 'h3')), '이 상품은 PC에서 마무리됐거나 정리됐어요');
  assert.deepEqual(actionLabels(tree), ['확인']);
});

test('mobile empty entry never loads the persisted desktop product or analysis', async () => {
  const h = harness({ persisted: true, stagedDraft: false });
  const tree = await h.settle();
  assert.ok(h.calls.some((call) => call[0] === 'getProduct' && call[1] === null));
  assert.equal(node(tree, 'MobileAnalyzedNotice'), undefined);
  assert.ok(node(tree, 'InputMainAction'));
  h.cleanup();
});

test('desktop reports photo conversion as content before an empty-input return check', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let finish;
  const h = harness({ mobile: false, photos: { name: '', colors: [{ id: 'base', isBase: true, images: [] }] },
    transcode: () => new Promise((resolve) => { finish = resolve; }) });
  t.after(() => h.cleanup());
  let tree = await h.settle();
  assert.equal(h.content.at(-1), false);
  const group = h.expand(tree, 'ColorImageGroup');
  const picker = h.render('AddDrop', node(group, 'AddDrop').props);
  node(picker, 'input').props.onChange({ target: { files: [{ name: 'photo.heic', type: 'image/heic' }], value: '' } });
  tree = await h.settle();
  assert.equal(h.content.at(-1), true);
  finish({ files: [], failed: [] });
  await h.settle();
  assert.equal(h.content.at(-1), false);
});

for (const visible of [true, false]) {
  test(`B locks all edits, next product checks the visible slot: ${visible}`, async () => {
    const h = harness({ initialSavedMeta: { photoCount: 2 } });
    let tree = await h.settle();
    assert.ok(node(tree, 'MobileSaveStatus'));
    const group = h.expand(tree, 'ColorImageGroup');
    assert.equal(node(group, 'ColorSwatchPicker'), undefined);
    assert.equal(node(group, 'AddDrop'), undefined);
    assert.equal(find(group, (n) => n.props.className === 'rm'), undefined);
    const name = find(tree, (n) => n.type === 'input' && n.props.className === 'field');
    assert.equal(name.props.disabled, true);
    const callbacks = node(tree, 'ColorImageGroup').props;
    callbacks.onRename('다른 이름');
    callbacks.onRemove('front');
    callbacks.onAddFiles('Detail', [{ src: '/new.jpg' }]);
    name.props.onChange({ target: { value: '바뀐 이름' } });
    tree = await h.settle();
    assert.equal(find(tree, (n) => n.type === 'input' && n.props.className === 'field').props.value, '니트');
    assert.equal(node(tree, 'ColorImageGroup').props.group.images.length, 2);
    assert.ok(!h.calls.some((c) => c[0] === 'queue'));
    h.slot.get = async () => visible ? { meta: { updatedAt: 'v1', deviceLabel: 'iPhone', photoCount: 2 } } : null;
    await button(h.expand(tree, 'InputMainAction'), '다음 상품 올리기').props.onClick();
    tree = await h.settle();
    if (visible) {
      assert.equal(node(tree, 'DraftEntryDialog').props.dialog.kind, 'mobile');
      await node(tree, 'DraftEntryDialog').props.actions.keepMobile();
      tree = await h.settle();
      assert.equal(node(tree, 'ColorImageGroup').props.readOnly, true);
      assert.ok(!h.calls.includes('begin'));
      button(h.expand(tree, 'InputMainAction'), '사진 고치기').props.onClick();
      tree = await h.settle();
      assert.equal(node(tree, 'ColorImageGroup').props.readOnly, false);
      assert.ok(button(h.expand(tree, 'InputMainAction'), '저장하고 PC에서 이어하기'));
      assert.ok(text(h.expand(tree, 'MobileSaveStatus')).includes('사진이 바뀌었어요'));
    } else {
      assert.ok(h.calls.includes('begin'));
      assert.ok(!h.calls.includes('remove'));
    }
    h.cleanup();
  });
}

for (const deviceLabel of ['Windows Chrome', 'iPhone Safari', 'iPad Safari']) {
  test(`mobile conflict shows C for PC and EditingRightsLock for mobile writer: ${deviceLabel}`, async () => {
    const h = harness();
    await h.settle();
    h.calls.length = 0;
    h.emitConflict({ state: 'conflict', deviceLabel, photoCount: 2 });
    let tree = await h.settle();
    if (isMobileDraftWriter({ deviceLabel })) assert.ok(node(tree, 'EditingRightsLock'));
    else {
      assert.equal(node(tree, 'ColorImageGroup'), undefined);
      assert.equal(node(tree, 'EditingRightsLock'), undefined);
      const status = h.render('HandoffStatusPage', h.expand(tree, 'MobileDraftStatus').props);
      assert.ok(text(status).includes('PC에서 이 상품을 이어서 만들고 있어요'));
      assert.deepEqual(actionLabels(status), ['다음 상품 올리기']);
      h.documentEvents.dispatchEvent(new Event('visibilitychange'));
      tree = await h.settle();
      assert.deepEqual(h.calls, []);
      await button(status, '다음 상품 올리기').props.onClick();
      assert.equal(node(await h.settle(), 'DraftEntryDialog').props.dialog.kind, 'mobile');
      assert.ok(!h.calls.includes('remove'));
    }
    h.cleanup();
  });
}

for (const mobile of [true, false]) {
  test(`gone lock uses D only on mobile: ${mobile}`, async () => {
    const h = harness({ mobile });
    await h.settle();
    h.emitConflict({ state: 'gone' });
    h.slot.get = async () => null;
    const tree = await h.settle();
    if (mobile) {
      assert.equal(node(tree, 'EditingRightsLock'), undefined);
      assert.equal(node(tree, 'ColorImageGroup'), undefined);
      const status = h.render('HandoffStatusPage', h.expand(tree, 'MobileDraftStatus').props);
      assert.ok(text(status).includes('이 상품은 PC에서 마무리됐거나 정리됐어요'));
      await button(status, '다음 상품 올리기').props.onClick();
      assert.ok(h.calls.includes('begin'));
      assert.ok(!h.calls.includes('remove'));
    } else assert.ok(node(tree, 'EditingRightsLock'));
    h.cleanup();
  });
}

test('gone save closes into D even without a conflict notification', async () => {
  const h = harness({ saveAll: async () => ({ status: 'gone' }) });
  let tree = await h.settle();
  await button(h.expand(tree, 'InputMainAction'), '저장하고 PC에서 이어하기').props.onClick();
  tree = await h.settle();
  button(h.expand(tree, 'MobileSaveModal'), '확인').props.onClick();
  tree = await h.settle();
  assert.equal(node(tree, 'MobileDraftStatus').props.gone, true);
  assert.equal(node(tree, 'ColorImageGroup'), undefined);
  h.cleanup();
});

for (const [provider, metadata, expected, sentence] of [
  ['kakao', { full_name: '전체 이름', name: '이름' }, '전체 이름', 'PC에서도 카카오로 로그인해 주세요.'],
  ['kakao', { name: '이름' }, '이름', 'PC에서도 카카오로 로그인해 주세요.'],
  ['kakao', { nickname: '별명' }, '별명', 'PC에서도 카카오로 로그인해 주세요.'],
  ['kakao', { preferred_username: '닉네임' }, '닉네임', 'PC에서도 카카오로 로그인해 주세요.'],
  ['kakao', {}, 'seller@example.com', 'PC에서도 카카오로 로그인해 주세요.'],
  ['google', { name: '이름' }, 'seller@example.com', 'PC에서도 구글로 로그인해 주세요.'],
  ['email', {}, 'seller@example.com', 'PC에서도 이메일로 로그인해 주세요.'],
  ['unknown', {}, null, 'PC에서도 같은 계정으로 로그인해 주세요.'],
  ['toString', {}, null, 'PC에서도 같은 계정으로 로그인해 주세요.'],
  [null, {}, null, 'PC에서도 같은 계정으로 로그인해 주세요.'],
]) {
  test(`D4 account pill ${provider} ${JSON.stringify(metadata)}`, () => {
    const h = harness({ session: provider ? { user: { app_metadata: { provider }, user_metadata: metadata, email: 'seller@example.com' } } : null });
    const modal = h.render('MobileSaveModal', { result: { status: 'saved', photoCount: 2 } });
    const pill = find(modal, (n) => n.props.className === 'pill pill-soft handoff-account');
    assert.equal(pill ? text(pill) : null, expected);
    assert.equal(text(node(modal, 'p')), sentence);
  });
}

test('mobile input subtitle explains the PC handoff while desktop copy stays unchanged', async () => {
  for (const mobile of [true, false]) {
    const h = harness({ mobile });
    const head = node(await h.settle(), 'PageHead');
    assert.equal(text(head.props.sub), mobile ? '사진만 올리면 PC에서 이어서 만들어요.'
      : '사진 몇 장만으로 경험해보세요.부족한 정보는 AI 분석 후 직접 확인하고 보정할 수 있어요.');
    h.cleanup();
  }
});

test('D1 product row includes optional 56px thumbnail, name fallback and saved time', () => {
  const h = harness();
  for (const thumbnailUrl of ['/photo.jpg', null]) {
    const row = h.render('DraftProductRow', { meta: { thumbnailUrl, photoCount: 3, updatedAt: new Date().toISOString() } });
    assert.ok(text(row).includes('이름 없는 상품'));
    assert.ok(text(row).includes('사진 3장 · 방금 저장'));
    assert.equal(Boolean(node(row, 'img')), Boolean(thumbnailUrl));
    if (thumbnailUrl) assert.equal(node(row, 'img').props.width, 56);
  }
  for (const deviceLabel of ['iPhone', 'Mac Chrome']) {
    const modal = h.render('MobileDraftDialog', { dialog: { meta: { deviceLabel } }, actions: {} });
    assert.ok(text(modal).includes(deviceLabel === 'iPhone' ? 'PC에서 아직 열지 않았어요.' : 'PC에서 만드는 중이에요. 버리면 PC 화면의 작업도 사라져요.'));
    assert.deepEqual(actionLabels(modal), ['그대로 두기', '버리고 새로 올리기']);
    assert.equal(button(modal, '버리고 새로 올리기').props.variant, 'danger');
  }
});

test('handoff rows and entry thumbnails use token radius, cover and a left photo column', () => {
  const css = read('src/styles/features.css');
  assert.match(css, /\.draft-entry-source\.has-thumbnail \{ grid-template-columns: 40px minmax\(0, 1fr\)/);
  assert.match(css, /\.draft-entry-thumbnail \{[^}]*grid-column: 1;[^}]*object-fit: cover; border-radius: var\(--r-4\)/);
  assert.match(css, /\.handoff-product-row img \{[^}]*width: 56px; height: 56px;[^}]*object-fit: cover; border-radius: var\(--r-4\)/);
});

test('mobile gone notification before restoration still lands in D', async () => {
  const h = harness({ stagedDraft: false });
  await h.settle();
  h.emitConflict({ state: 'gone' });
  assert.equal(node(await h.settle(), 'MobileDraftStatus').props.gone, true);
  h.cleanup();
});

test('F6 same-browser other-tab mobile lock keeps EditingRightsLock and reclaim', async () => {
  const h = harness(); await h.settle();
  h.emitConflict({ state: 'other-tab', deviceLabel: '이 브라우저의 다른 탭' });
  const tree = await h.settle();
  assert.ok(node(tree, 'EditingRightsLock'));
  assert.equal(node(tree, 'MobileDraftStatus'), undefined);
  const lock = h.expand(tree, 'EditingRightsLock');
  assert.ok(button(lock, '이 화면에서 이어서 하기'));
  h.cleanup();
});

test('F4 desktop gone continuation recovers bytes before restarting and reports only missing photos', async () => {
  const h = harness({ mobile: false }); await h.settle();
  h.slot.recoverGonePhotos = async (snapshot) => { h.calls.push('recover'); return { ...snapshot,
    product: { ...snapshot.product, colors: [{ ...snapshot.product.colors[0], images: [snapshot.product.colors[0].images[0]] }] }, missingPhotoCount: 1 }; };
  h.slot.restartAfterGone = () => { h.calls.push('restart'); return true; };
  h.slot.flush = async () => { h.calls.push('flush'); };
  h.emitConflict({ state: 'gone' });
  const lock = node(await h.settle(), 'EditingRightsLock');
  await lock.props.onRestartLocal(); await h.settle();
  assert.ok(h.calls.indexOf('recover') < h.calls.indexOf('restart'));
  assert.ok(h.calls.some((call) => call[0] === 'toast' && call[1] === '사진 1장은 다시 골라 주세요.'));
  const queued = h.calls.filter((call) => call[0] === 'queue').at(-1)[1];
  assert.equal(queued.product.colors[0].images.length, 1);
  h.cleanup();
});

test('narrow dialog pills, balanced titles, clamped names, and saved modal spacing use scoped CSS', () => {
  const css = read('src/styles/features.css');
  const status = read('src/features/shell/HandoffStatusPage.module.css');
  assert.match(css, /\.modal-actions\.handoff-dialog-actions \{ flex-direction: column; gap: var\(--sp-8\); \}/);
  assert.match(css, /\.modal-actions\.handoff-dialog-actions \.btn \{ width: 100%; \}/);
  assert.match(css, /\.pi-save-modal\.handoff-panel \{ padding-inline: 0; \}/);
  assert.match(css, /\.handoff-account \{ font-size: 14px;/);
  assert.match(css, /-webkit-line-clamp: 2; overflow: hidden; text-wrap: balance/);
  assert.match(status, /gap: var\(--sp-8\)/);
  assert.match(status, /flex-direction: column; width: 100%/);
  assert.doesNotMatch(status, /word-break|#[0-9a-f]{3,8}\b/);
});

const priorDraftTime = '2026-10-05T01:00:00.000Z';
const priorProduct = () => ({ name: '수정 전 상품', colors: [{ id: 'base', isBase: true, images: [] }] });
function entrySlot({ token = 'owned' } = {}) {
  const remote = { meta: { hasContent: true, deviceLabel: 'Mac Chrome', photoCount: 0, updatedAt: priorDraftTime },
    payload: { product: priorProduct(), photos: [], updatedAt: priorDraftTime } };
  let staged = { payload: remote.payload };
  let paused = false;
  const calls = [];
  return {
    calls, get staged() { return staged; }, get paused() { return paused; },
    configure() {}, activate() {}, queue() {}, onConflict: () => () => {}, onPhotosPending: () => () => {},
    checkOwnership: async () => {}, consumeStaged() { const value = staged; staged = null; return value; },
    hasStaged: () => Boolean(staged), stage(value) { staged = value; },
    getIdentityEpoch: () => localIdentity.getIdentityEpoch(), getToken: () => token,
    getSyncedAt: () => priorDraftTime, getServerSyncedAt: () => priorDraftTime, getSyncedUserId: () => 'seller',
    hasPendingRemoval: () => false, hasOtherTabOwner: () => false,
    pauseEntryWrites() { paused = true; }, resumeEntryWrites() { paused = false; },
    async get() { calls.push('get'); return remote; }, async takeover() { calls.push('takeover'); return remote; },
    continueWithoutSlotCheck() { calls.push('continueUnchecked'); return true; },
  };
}

for (const delay of ['debounce', 'photo-debounce', 'photo', 'indexeddb']) {
  test(`PC edit, leave for library, and return before ${delay} save finishes keeps the latest local draft`, async (t) => {
    t.mock.timers.enable({ apis: ['setTimeout'] });
    const storage = draftStoreFixture(t, { product: priorProduct(), photos: [], updatedAt: priorDraftTime });
    let finishPhoto;
    if (delay === 'photo') t.mock.method(globalThis, 'fetch', () => new Promise((resolve) => { finishPhoto = () => resolve(new Response('new photo', { headers: { 'Content-Type': 'image/jpeg' } })); }));
    const photoUrl = URL.createObjectURL(new Blob(['new photo'], { type: 'image/jpeg' }));
    t.after(() => URL.revokeObjectURL(photoUrl));
    storage.onCleanup(() => finishPhoto?.());
    const slot = entrySlot();
    const h = harness({ mobile: false, photos: priorProduct(), draftStore: localStore, slotClient: slot,
      previewRegistry: delay === 'photo-debounce' ? (options) => createProductPhotoPreviewRegistry({ ...options, createThumbnail: async () => { throw new Error('no canvas'); } }) : null });
    t.after(() => h.cleanup());
    let tree = await h.settle('ProductInput');
    find(tree, (value) => value.type === 'input' && value.props.placeholder === '예: 소프트 골지 라운드 니트')
      .props.onChange({ target: { value: '방금 수정한 상품' } });
    if (delay.startsWith('photo')) {
      node(tree, 'ColorImageGroup').props.onAddFiles('Front', [{ id: 'new-photo', src: photoUrl, type: 'image/jpeg' }]);
    }
    await h.settle('ProductInput');
    h.unmount('ProductInput');
    h.location.pathname = '/library';
    assert.equal(storage.saved.product.name, '수정 전 상품');
    if (delay === 'indexeddb') storage.pauseWrites();
    if (['photo', 'indexeddb'].includes(delay)) {
      t.mock.timers.tick(500);
      await drainDraftTasks();
      if (delay === 'photo') assert.equal(typeof finishPhoto, 'function');
      else assert.equal(storage.waitingWrites, 1);
    }
    h.location.pathname = '/create/input';
    h.location.key = 'return-from-library';
    tree = await h.settle('ProductInputRoute');
    if (['photo', 'indexeddb'].includes(delay)) {
      assert.equal(tree, null, 'entry waits for the in-flight local save');
      assert.equal(slot.paused, true);
      assert.deepEqual(slot.calls, [], 'the older slot is not read before local persistence');
      if (delay === 'photo') finishPhoto();
      else storage.releaseWrites();
      tree = await h.settle('ProductInputRoute');
    }
    if (delay === 'photo-debounce') {
      await localStore.flushProductDraftSave();
      tree = await h.settle('ProductInputRoute');
    }
    assert.equal(tree.type, h.components.ProductInput);
    assert.equal(slot.staged.payload.product.name, '방금 수정한 상품');
    assert.equal(storage.saved.product.name, '방금 수정한 상품');
    if (delay.startsWith('photo')) {
      assert.deepEqual(slot.staged.payload.product.colors[0].images.map((image) => image.id), ['new-photo']);
      assert.equal(await slot.staged.payload.photos[0].blob.text(), 'new photo');
    }
    assert.equal(h.calls.includes('begin'), false, 'auto-open must not clear the freshly saved local draft');
    assert.equal(slot.paused, false);
    tree = await h.settle('ProductInput');
    assert.equal(find(tree, (value) => value.type === 'input' && value.props.placeholder === '예: 소프트 골지 라운드 니트').props.value, '방금 수정한 상품');
  });
}

for (const token of ['owned', null]) {
  test(`PC local save timeout releases entry checking without reading or discarding edits, token ${token}`, async (t) => {
    t.mock.timers.enable({ apis: ['setTimeout'] });
    const storage = draftStoreFixture(t, { product: priorProduct(), photos: [], updatedAt: priorDraftTime });
    const slot = entrySlot({ token });
    const h = harness({ mobile: false, photos: priorProduct(), draftStore: localStore, slotClient: slot });
    t.after(() => h.cleanup());
    let tree = await h.settle('ProductInput');
    find(tree, (value) => value.type === 'input' && value.props.placeholder === '예: 소프트 골지 라운드 니트')
      .props.onChange({ target: { value: '느린 저장 중 수정' } });
    await h.settle('ProductInput');
    h.unmount('ProductInput');
    storage.pauseWrites();
    h.location.pathname = '/library';
    h.location.pathname = '/create/input';
    tree = await h.settle('ProductInputRoute');
    t.mock.timers.tick(8000);
    tree = await h.settle('ProductInputRoute');
    assert.equal(tree.type, h.components.HandoffStatusPage);
    assert.equal(tree.props.variant, 'error');
    assert.equal(tree.props.onUpload, undefined, 'a timed-out save cannot open an older draft through unchecked entry');
    assert.deepEqual(slot.calls, []);
    assert.equal(h.calls.includes('begin'), false);
    storage.releaseWrites();
    await drainDraftTasks();
    tree.props.onRetry();
    tree = await h.settle('ProductInputRoute');
    assert.equal(tree.type, h.components.ProductInput);
    assert.equal(slot.staged.payload.product.name, '느린 저장 중 수정');
    assert.equal(storage.saved.product.name, '느린 저장 중 수정');
  });
}
