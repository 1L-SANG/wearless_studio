import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { transformWithEsbuild } from 'vite';
import { isMobileDraftWriter } from '../../src/lib/draftDeviceNoun.js';
import { draftSavedTime, createDraftEntryActions } from '../../src/lib/draftEntry.js';
import * as entry from '../../src/lib/draftEntry.js';
import * as flow from '../../src/lib/flowSession.js';
import * as localStore from '../../src/lib/draftStore.js';
import * as mobileGuestSave from '../../src/lib/mobileGuestSave.js';
import { restoreDraftProduct } from '../../src/features/product-input/draftProductRestore.js';
import { isRemoteAssetPhoto } from '../../src/lib/photoBytes.js';
import { draftSlot as localIdentity, localDraftMeta, createDraftSlotSync, formatDraftClock } from '../../src/lib/draftSlot.js';
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

function harness({ mobile = true, analysis = null, fastEntry = false, analyze = null, prepare = null, featureFactory = null, restoreFeature = null, saveAll, transcode, photos = product(), doneBlocked = false, persisted = false, stagedDraft = true, initialSavedMeta = null, session = { user: { id: 'seller' } }, authLoading = false, draftStore = null, slotClient = null, previewRegistry = null, autoSaveIntent = null, sellerConsentReady, slotApi, consentRetry } = {}) {
  const calls = [];
  const auth = { session, sellerConsentReady, retrySellerConsent: (userId) => { calls.push(['consent', userId]); return consentRetry ? consentRetry(userId) : Promise.resolve(false); }, loading: authLoading, openLogin: (path) => calls.push(['login', path]) };
  const content = [];
  const doneGuardChecks = [];
  const onContentChange = (value) => content.push(value);
  const documentEvents = new EventTarget();
  documentEvents.visibilityState = 'visible';
  let pendingHandler, conflictHandler, featureController, nextImageId = 0;
  const frames = new Map();
  let active, cursor, effects = [], dirty = false;
  let staged = stagedDraft ? { payload: typeof stagedDraft === 'object' ? stagedDraft : { product: photos, analysis, composeMode: 'basic' } } : null;
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
    getIdentityEpoch: () => 0, getToken: () => null, hasStaged: () => Boolean(staged),
    stage(value) { staged = value; }, pauseEntryWrites() {}, resumeEntryWrites() {},
    resetIdentity() { calls.push('reset'); }, activate() {}, queue(snapshot) { calls.push(['queue', snapshot]); },
    consumeStaged() { const next = staged; staged = null; return next; },
    get: async () => ({ meta: { photoCount: 2, updatedAt: 'v1' } }),
    getServerSyncedAt: () => 'v1', hasPendingRemoval: () => false,
    removeForNewFlow: async () => { calls.push('remove'); },
    saveAll: async (snapshot) => { calls.push(['saveAll', snapshot]); return saveAll ? saveAll(snapshot) : { status: 'saved', photoCount: 2 }; },
  };
  const bindings = {
    isAnalysisFastEntryEnabled: () => fastEntry,
    preparePublicAnalysis: prepare || (async () => ({ photos: [], photoSignature: 'signature' })), analysisPhotoKey: () => 'photos',
    getActiveFeatureRun: () => featureController,
    createDraftFeatureRun: (options) => {
      featureController = featureFactory(options);
      featureController.startAnalysis ||= async (options) => {
        await featureController.start(options);
        return featureController.requestCore(options);
      };
      return featureController;
    },
    restoreDraftFeatureRun: (draft, options) => { calls.push(['restoreFeature', draft.analysis.featureRun.stage]); return restoreFeature?.(draft, options); },
    useClientErrorCapture() {}, ClientErrorCapture: () => null, recordClientAction() {},
    getMobileGuestDraft: () => null, collectDraftPhotos: async (product) => ({ cleanProduct: product, photos: [] }),
    getMobileGuestSave: () => null, rememberMobileGuestSave: () => calls.push('intent'), clearMobileGuestSave() {}, claimMobileGuestSave: mobileGuestSave.claimMobileGuestSave, readStoredAuthSession: () => null,
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
    useAuth: () => auth,
    useDoneGuard: (enabled) => { doneGuardChecks.push(enabled); return doneBlocked; }, useToast: () => ({ push: (...args) => calls.push(['toast', ...args]) }),
    useQuery: ({ queryKey }) => ({ data: queryKey.length === 1 ? [{ id: 'finished', title: '완성본' }] : [], isLoading: false }),
    useAppStore, isMobileDevice: () => mobile, isMockMode: false, draftSlot: slot, createDraftEntryActions, isMobileDraftWriter, draftSavedTime,
    api: { getProduct: async (id) => { calls.push(['getProduct', id]); return photos; },
      getAnalysis: async () => ({ projectId: 'desktop', fit: 'regular' }), getCatalogs: async () => ({ angleSlots: ['Front', 'Back'], swatchColors: [] }),
      analyzeProduct: async (...args) => { calls.push('analyze'); return analyze ? analyze(...args) : { suggestedName: '니트' }; }, ...(slotApi || {}) },
    createProductPhotoPreviewRegistry: previewRegistry || (() => ({ sync() {}, dispose() {}, release() {}, displayUrl: (_id, src) => src })),
    createTrailingPatchScheduler: () => ({ flush() {}, schedule() {} }),
    generationWorkWarningKind: () => 'none', restoreDraftProduct: (draft) => draft.product,
    isRemoteAssetPhoto, normalizeAnalysisFit: (value) => value, isAnalysisRunning: () => false,
    queueProductDraftSave: () => {}, flushProductDraftSave: async () => {}, hasPendingDraft: () => false,
    ...(draftStore || {}),
    formatDraftRelativeTime: () => '1분 전', formatDraftClock, getBaseSlotUploadRoom, getPendingTileCount, PENDING_TILE_DELAY_MS,
    invalidateStoryboardForProductPhotoEdit() {}, invalidateStoryboardEntryPrefetch() {},
    analysisResponseProductPatch: () => ({}), autofillColorGroups: (colors) => colors,
    mergeProductOwnedAnalysisFields: (value) => value, photoCaption: () => '사진', uid: () => ++nextImageId === 1 ? 'new-photo' : `new-photo-${nextImageId}`,
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
    'ResumeChoiceModal', 'WizardCTA', 'DoneGuardModal', 'CreditShortfallModal', 'AnalysisForm', 'AnalysisProgress', 'AnalysisSkeleton']) bindings[name] = name;
  const components = new Function(...Object.keys(bindings), `${code}; return { ProductInputRoute, HandoffStatusPage, MobileDraftDialog, MobileDraftStatus, DraftProductRow, Library, ProductInput, ColorImageGroup, AddDrop, InputMainAction, MobileSaveModal, MobileSaveStatus, MobileAnalyzedNotice, EditingRightsLock, DraftEntryDialog, HangTag, AddressCopyChip };`)(...Object.values(bindings));
  function render(name = 'ProductInput', props = {}) {
    if (!frames.has(name)) frames.set(name, []);
    active = frames.get(name); cursor = 0; dirty = false;
    return components[name](name === 'ProductInput' ? { onContentChange, initialSavedMeta, autoSaveIntent, ...props } : props);
  }
  async function settle(name = 'ProductInput', props = {}) {
    let tree = render(name, props);
    for (let i = 0; i < 30; i++) {
      const batch = effects; effects = []; batch.forEach((effect) => effect());
      await new Promise(setImmediate);
      if (!dirty && !effects.length) return tree;
      tree = render(name, props);
    }
    throw new Error('did not settle');
  }
  return { calls, auth, content, doneGuardChecks, location, render, settle, components, documentEvents, slot,
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
    for (const missingSlot of ['Front', 'Back']) {
      const photos = product();
      photos.colors[0].images = photos.colors[0].images.filter((image) => image.slot !== missingSlot);
      const h = harness({ mobile, photos });
      try {
        const tree = await h.settle();
        const cta = h.expand(tree, 'InputMainAction');
        assert.equal(node(cta, 'Button').props.disabled, true);
        assert.equal(find(cta, (value) => value.props.className?.includes('wizard-cta-reason')), undefined);
      } finally { h.cleanup(); }
    }
  });
}

const additionalFrontReason = '추가한 색상의 정면 사진을 올리거나 색상을 지워 주세요';
const additionalGroup = (tree) => find(tree, (value) => value.type?.name === 'ColorImageGroup'
  && value.props.group.id === 'extra');
const ctaReason = (tree) => find(tree, (value) => value.props.className?.includes('wizard-cta-reason'));
const productWithAdditionalColor = (images = []) => {
  const photos = product();
  photos.colors.push({ id: 'extra', isBase: false, images });
  return photos;
};

for (const mobile of [true, false]) {
  const device = mobile ? 'mobile' : 'desktop';

  test(`G10 ${device} blocks an additional color without Front and shows the blue reason`, async () => {
    for (const images of [[], [{ id: 'extra-back', slot: 'Back', src: '/extra-back.jpg' }]]) {
      const h = harness({ mobile, photos: productWithAdditionalColor(images) });
      try {
        const cta = h.expand(await h.settle(), 'InputMainAction');
        assert.equal(node(cta, 'Button').props.disabled, true);
        assert.equal(text(ctaReason(cta)), additionalFrontReason);
        assert.equal(ctaReason(cta).props.className, 'wizard-cta-reason is-link');
      } finally { h.cleanup(); }
    }
  });

  test(`G10 ${device} adding the additional Front enables the main action`, async () => {
    const h = harness({ mobile, photos: productWithAdditionalColor() });
    try {
      const tree = await h.settle();
      assert.equal(node(h.expand(tree, 'InputMainAction'), 'Button').props.disabled, true);
      additionalGroup(tree).props.onAddFiles('Front', [{ src: '/extra-front.jpg' }]);
      const cta = h.expand(await h.settle(), 'InputMainAction');
      assert.equal(node(cta, 'Button').props.disabled, false);
      assert.equal(ctaReason(cta), undefined);
    } finally { h.cleanup(); }
  });

  test(`G10 ${device} deleting an empty additional color enables the main action`, async () => {
    const h = harness({ mobile, photos: productWithAdditionalColor() });
    try {
      const tree = await h.settle();
      assert.equal(node(h.expand(tree, 'InputMainAction'), 'Button').props.disabled, true);
      additionalGroup(tree).props.onRemoveGroup();
      const cta = h.expand(await h.settle(), 'InputMainAction');
      assert.equal(node(cta, 'Button').props.disabled, false);
      assert.equal(ctaReason(cta), undefined);
    } finally { h.cleanup(); }
  });

  test(`G10 ${device} pending additional Front blocks completion and suppresses upload instructions`, async () => {
    const h = harness({ mobile, photos: productWithAdditionalColor() });
    try {
      const group = additionalGroup(await h.settle());
      group.props.onPendingChange('extra', 'Front', 1);
      let cta = h.expand(await h.settle(), 'InputMainAction');
      assert.equal(node(cta, 'Button').props.disabled, true);
      assert.ok(!text(cta).includes(additionalFrontReason));
      if (mobile) {
        assert.equal(text(ctaReason(cta)), '사진을 불러오는 중이에요');
        assert.equal(ctaReason(cta).props.className, 'wizard-cta-reason');
      } else assert.equal(ctaReason(cta), undefined);
      group.props.onPendingChange('extra', 'Front', 0);
      cta = h.expand(await h.settle(), 'InputMainAction');
      assert.equal(node(cta, 'Button').props.disabled, true);
      assert.equal(text(ctaReason(cta)), additionalFrontReason);
    } finally { h.cleanup(); }
  });

  test(`G10 ${device} preserves loading priority before additional-color guidance`, async () => {
    const h = harness({ mobile, authLoading: true, photos: productWithAdditionalColor() });
    try {
      const tree = await h.settle();
      let cta = h.expand(tree, 'InputMainAction');
      assert.equal(node(cta, 'Button').props.disabled, true);
      assert.equal(text(ctaReason(cta)), '로그인 상태를 확인하고 있어요.');
      assert.equal(ctaReason(cta).props.className, 'wizard-cta-reason');
      const group = node(tree, 'ColorImageGroup');
      group.props.onPendingChange('base', 'Front', 1);
      cta = h.expand(await h.settle(), 'InputMainAction');
      assert.equal(node(cta, 'Button').props.disabled, true);
      assert.equal(text(ctaReason(cta)), mobile ? '사진을 불러오는 중이에요' : '로그인 상태를 확인하고 있어요.');
      assert.equal(ctaReason(cta).props.className, 'wizard-cta-reason');
      group.props.onPendingChange('base', 'Front', 0);
      cta = h.expand(await h.settle(), 'InputMainAction');
      assert.equal(node(cta, 'Button').props.disabled, true);
      assert.equal(text(ctaReason(cta)), '로그인 상태를 확인하고 있어요.');
      assert.equal(ctaReason(cta).props.className, 'wizard-cta-reason');
    } finally { h.cleanup(); }
  });
}

test('G10 mobile keeps its photo-loading reason ahead of additional-color instructions', async () => {
  const h = harness({ photos: productWithAdditionalColor() });
  try {
    node(await h.settle(), 'ColorImageGroup').props.onPendingChange('base', 'Back', 1);
    const cta = h.expand(await h.settle(), 'InputMainAction');
    assert.equal(node(cta, 'Button').props.disabled, true);
    assert.equal(text(ctaReason(cta)), '사진을 불러오는 중이에요');
    assert.equal(ctaReason(cta).props.className, 'wizard-cta-reason');
  } finally { h.cleanup(); }
});

test('G10 desktop only suppresses additional-color instructions for that color Front conversion', async () => {
  const h = harness({ mobile: false, photos: productWithAdditionalColor() });
  try {
    const group = additionalGroup(await h.settle());
    for (const [groupId, slot] of [['base', 'Front'], ['extra', 'Back']]) {
      group.props.onPendingChange(groupId, slot, 1);
      const cta = h.expand(await h.settle(), 'InputMainAction');
      assert.equal(node(cta, 'Button').props.disabled, true);
      assert.equal(text(ctaReason(cta)), additionalFrontReason);
      assert.equal(ctaReason(cta).props.className, 'wizard-cta-reason is-link');
      group.props.onPendingChange(groupId, slot, 0);
    }
  } finally { h.cleanup(); }
});

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


test('빠른 핵심 저장을 기다리는 동안 옛 분석으로 상품 초안을 예약하지 않는다', async () => {
  let coreResolve, saveResolve;
  const core = new Promise((yes) => { coreResolve = yes; });
  const saved = new Promise((yes) => { saveResolve = yes; });
  const snapshots = [];
  const h = harness({ mobile: false, fastEntry: true, analyze: () => core,
    draftStore: { queueProductDraftSave: (product, analysis) => snapshots.push({ product, analysis }) },
    featureFactory: ({ onAnalysis, onProduct, readProduct }) => {
      let record;
      return { async start(options) { record = { runId: options.runId, photoSignature: options.photoSignature, stage: 'requested', coreReceived: false }; onAnalysis({ featureRun: record }); },
        async requestCore() { const analysis = await core; await saved; record = { ...record, coreReceived: true }; onAnalysis({ ...analysis, featureRun: record }); onProduct(readProduct()); return analysis; }, invalidate() {} };
    } });
  const tree = await h.settle(); const submitting = button(h.expand(tree, 'InputMainAction'), 'AI 분석하기').props.onClick();
  await h.settle();
  coreResolve({ suggestedName: '니트', sellingPoints: ['예비 특징'], aiSuggestedPoints: ['예비 특징'] });
  await h.settle();
  assert.equal(snapshots.some((snapshot) => snapshot.analysis?.featureRun?.coreReceived === false && !snapshot.analysis.sellingPoints), false);
  saveResolve(); await submitting; const completed = await h.settle();
  node(completed, 'AnalysisProgress').props.onFinished();
  assert.equal(node(await h.settle(), 'AnalysisForm').props.analysis.sellingPoints[0], '예비 특징'); h.cleanup();
});

test('고른 로컬 초안도 특징 실행을 복원하고 핵심 수신 전에는 분석 폼을 열지 않는다', async () => {
  let restoreCalls = 0;
  const h = harness({ mobile: false, fastEntry: true, analysis: { featureRun: { stage: 'requested', coreReceived: false, runId: 'restore_run_123' } },
    restoreFeature: async () => { restoreCalls++; return { getRecord: () => ({ coreReceived: false }) }; } });
  const tree = await h.settle();
  assert.equal(restoreCalls, 1); assert.ok(node(tree, 'AnalysisProgress')); assert.equal(node(tree, 'AnalysisForm'), undefined); h.cleanup();
});
for (const coreReceived of [false, true]) test(`핵심 분석 복원은 사진 일치 경고를 다시 표시한다 (이미 수신 ${coreReceived})`, async () => {
  const warning = { offending: [{ slot: 'Back', reason: '다른 의류' }] };
  const analysis = { featureRun: { stage: 'requested', coreReceived, runId: 'restore_run_123' },
    ...(coreReceived ? { inputConsistency: warning } : {}) };
  const h = harness({ mobile: false, fastEntry: true, analysis,
    restoreFeature: async (draft, options) => {
      const next = { ...draft.analysis, inputConsistency: warning, featureRun: { ...draft.analysis.featureRun, coreReceived: true } };
      if (!coreReceived) options.onAnalysis(next);
      return { getRecord: () => next.featureRun, getAnalysis: () => next };
    } });
  const tree = await h.settle();
  assert.ok(text(tree).includes('다른 옷이 섞인 것 같아요'));
  assert.ok(node(tree, 'AnalysisForm')); h.cleanup();
});
test('입력 화면을 떠나도 조정자가 시작한 핵심 요청과 결과 저장은 이어진다', async () => {
  let resolve, coreCalls = 0, settled = null;
  const core = new Promise((yes) => { resolve = yes; });
  const h = harness({ mobile: false, fastEntry: true,
    featureFactory: ({ onAnalysis, onProduct, readProduct }) => {
      let record;
      return {
        async start(options) { record = { runId: options.runId, photoSignature: options.photoSignature, coreReceived: false, stage: 'requested' }; onAnalysis({ featureRun: record }); },
        async requestCore() {
          coreCalls++;
          const result = await core;
          settled = { ...result, featureRun: { ...record, coreReceived: true } };
          onAnalysis(settled); onProduct({ ...readProduct(), name: result.suggestedName });
          return settled;
        }, invalidate() {},
      };
    } });
  const tree = await h.settle();
  const submitting = button(h.expand(tree, 'InputMainAction'), 'AI 분석하기').props.onClick();
  await h.settle(); h.unmount('ProductInput');
  resolve({ suggestedName: '화면 밖에서 분석한 상품', sellingPoints: ['핵심 특징'], aiSuggestedPoints: ['핵심 특징'] });
  await submitting;
  assert.equal(coreCalls, 1); assert.equal(settled.featureRun.coreReceived, true);
  assert.deepEqual(settled.sellingPoints, ['핵심 특징']);
  assert.equal(h.calls.includes('analyze'), false);
  assert.equal(h.calls.some((call) => call[0] === 'toast'), false);
  h.cleanup();
});
test('사진 준비 중 돌아와도 입력 버튼 대신 같은 분석의 대기 화면을 이어간다', async () => {
  let release, controller, bindings, preparing, coreCalls = 0;
  const prepared = new Promise((yes) => { release = yes; });
  let saved = { product: product(), analysis: null, photos: [{ colorId: 'base', slot: 'Front' }, { colorId: 'base', slot: 'Back' }] };
  const h = harness({ mobile: false, fastEntry: true, prepare: () => prepared,
    draftStore: { hasPendingDraft: () => true, loadDraft: async () => saved,
      queueProductDraftSave: (product, analysis) => { saved = { ...saved, product, analysis }; } },
    featureFactory: (options) => {
      bindings = options;
      controller = {
        async start() {},
        async requestCore() { coreCalls++; return { sellingPoints: ['특징'] }; },
        startAnalysis(options) {
          const record = { runId: options.runId, photoSignature: null, stage: 'preparing', coreReceived: false };
          saved = { ...saved, analysis: { featureRun: record } }; bindings.onAnalysis(saved.analysis);
          preparing = prepared.then(() => {
            coreCalls++; const analysis = { sellingPoints: ['특징'], aiSuggestedPoints: ['특징'], featureRun: { ...record, stage: 'requested', coreReceived: true } };
            saved = { ...saved, analysis }; bindings.onAnalysis(analysis); return analysis;
          });
          return preparing;
        }, getRecord: () => saved.analysis.featureRun, getAnalysis: () => saved.analysis, invalidate() {},
      }; return controller;
    },
    restoreFeature: async (_draft, options) => { bindings = options; await preparing; return controller; },
  });
  const tree = await h.settle();
  const submitting = button(h.expand(tree, 'InputMainAction'), 'AI 분석하기').props.onClick();
  await h.settle(); h.unmount('ProductInput');
  const returned = await h.settle();
  const stillAnalyzing = Boolean(node(returned, 'AnalysisProgress'));
  release({ photos: [], photoSignature: 'signature' }); await submitting; const done = await h.settle();
  assert.equal(stillAnalyzing, true); assert.equal(coreCalls, 1); assert.ok(node(done, 'AnalysisForm'));
  h.cleanup();
});

for (const mobile of [false, true]) test(`빠른 진입 분석 전 사진 추가는 로컬과 서버 초안 저장을 예약한다, 휴대폰 ${mobile}`, async (t) => {
  const storage = draftStoreFixture(t);
  const photo = new Blob(['added photo'], { type: 'image/jpeg' });
  const src = URL.createObjectURL(photo);
  t.after(() => URL.revokeObjectURL(src));
  const photos = { name: '', colors: [{ id: 'base', isBase: true, images: [] }] };
  const h = harness({ mobile, fastEntry: true, photos, draftStore: localStore });
  t.after(() => h.cleanup());
  const tree = await h.settle();
  node(tree, 'ColorImageGroup').props.onAddFiles('Front', [{ id: 'new-photo', src, type: 'image/jpeg' }]);
  await h.settle();
  await localStore.flushProductDraftSave();
  assert.equal(storage.saved?.photos.length, 1);
  assert.equal(await storage.saved.photos[0].blob.text(), 'added photo');
  assert.equal(storage.saved.analysis, null);
  const queued = h.calls.filter((call) => call[0] === 'queue').at(-1)?.[1];
  assert.equal(queued?.product.colors[0].images[0].id, 'new-photo');
  assert.equal(queued.analysis, null);
});

test('특징 실행이 저장한 같은 분석 객체는 자동저장이 다시 예약하지 않는다', async (t) => {
  const local = [];
  const h = harness({ mobile: false, fastEntry: true,
    draftStore: { queueProductDraftSave: (...args) => local.push(args) },
    featureFactory: ({ onAnalysis }) => ({
      async start(options) { onAnalysis({ featureRun: { runId: options.runId, photoSignature: options.photoSignature, coreReceived: false, stage: 'requested' } }); },
      async requestCore() { const analysis = { suggestedName: '니트' }; onAnalysis({ ...analysis, featureRun: { coreReceived: true, stage: 'applied' } }); return analysis; }, invalidate() {},
    }) });
  t.after(() => h.cleanup());
  const tree = await h.settle();
  const before = h.calls.filter((call) => call[0] === 'queue').length;
  await button(h.expand(tree, 'InputMainAction'), 'AI 분석하기').props.onClick();
  await h.settle();
  assert.equal(h.calls.filter((call) => call[0] === 'queue').length, before);
  assert.equal(local.some(([, analysis]) => analysis?.featureRun?.stage === 'applied'), false);
});

for (const stagedDraft of [true, false]) for (const coreReceived of [false, true]) {
  test(`스위치 꺼짐 초안 복원은 핵심 수신 여부로 입력과 분석을 구분한다, staged ${stagedDraft}, core ${coreReceived}`, async (t) => {
    const analysis = { featureRun: { stage: 'requested', coreReceived, runId: 'restore_run_123' },
      ...(coreReceived ? { sellingPoints: ['완료 특징'], aiSuggestedPoints: ['완료 특징'] } : {}) };
    const photos = product();
    const h = harness({ mobile: false, fastEntry: false, analysis, stagedDraft,
      draftStore: { hasPendingDraft: () => true, loadDraft: async () => ({ product: photos, analysis, photos: [
        { colorId: 'base', slot: 'Front' }, { colorId: 'base', slot: 'Back' },
      ] }) } });
    t.after(() => h.cleanup());
    const tree = await h.settle();
    assert.equal(Boolean(node(tree, 'AnalysisForm')), coreReceived);
    assert.equal(node(tree, 'AnalysisProgress'), undefined);
    if (coreReceived) assert.deepEqual(node(tree, 'AnalysisForm').props.analysis.sellingPoints, ['완료 특징']);
    else {
      assert.equal(node(tree, 'ColorImageGroup').props.group.images.length, 2);
      assert.ok(button(h.expand(tree, 'InputMainAction'), 'AI 분석하기'));
    }
    assert.equal(h.calls.some((call) => call[0] === 'restoreFeature'), false);
  });
}


test('G2/G3 guest phone keeps edits local and flushes before opening input login', async () => {
  let release;
  const h = harness({ session: null, draftStore: {
    queueProductDraftSave: () => h.calls.push('local'),
    flushProductDraftSave: () => new Promise((resolve) => { release = resolve; }),
  } });
  let tree = await h.settle();
  assert.equal(node(tree, 'ColorImageGroup').props.group.images.length, 2);
  assert.equal(h.calls.filter((call) => call[0] === 'queue').length, 0);
  const saving = button(h.expand(tree, 'InputMainAction'), '저장하고 PC에서 이어하기').props.onClick();
  await new Promise(setImmediate);
  assert.equal(h.calls.some((call) => call[0] === 'login'), false);
  assert.equal(typeof release, 'function', 'save tap must flush the local draft before login');
  release({ saved: 2, failed: 0 }); await saving;
  assert.deepEqual(h.calls.slice(-2), ['intent', ['login', '/create/input']]);
  assert.equal(h.calls.some((call) => call[0] === 'saveAll' || call === 'analyze'), false);
  tree = await h.settle();
  assert.equal(node(tree, 'ColorImageGroup').props.group.images.length, 2);
  const again = button(h.expand(tree, 'InputMainAction'), '저장하고 PC에서 이어하기').props.onClick();
  await new Promise(setImmediate); release({ saved: 2, failed: 0 }); await again;
  assert.equal(h.calls.filter((call) => call[0] === 'login').length, 2);
  h.cleanup();
});

for (const provider of ['email', 'google', 'kakao']) {
  test(`G4 ${provider} restored input automatically saves once and reaches D3/D4`, async () => {
    const h = harness({ autoSaveIntent: { id: 'pending' }, session: { user: {
      id: 'seller', email: 'seller@example.test', app_metadata: { provider }, user_metadata: { name: '셀러' },
    } } });
    let tree = await h.settle();
    assert.equal(h.calls.filter((call) => call[0] === 'saveAll').length, 1);
    assert.equal(node(tree, 'ColorImageGroup').props.group.images.length, 2);
    const modal = h.expand(tree, 'MobileSaveModal');
    assert.equal(text(node(modal, 'h3')), '사진 2장을 저장했어요');
    assert.ok(text(modal).includes(provider === 'kakao' ? '셀러' : 'seller@example.test'));
    button(modal, '확인').props.onClick(); tree = await h.settle();
    assert.equal(node(tree, 'ColorImageGroup').props.readOnly, true);
    assert.equal(h.calls.filter((call) => call[0] === 'saveAll').length, 1);
    h.cleanup();
  });
}

for (const reenter of [false, true]) {
  test(`R4 email return with an invisible locked slot opens the reclaim chooser without saving again (reenter=${reenter})`, async (t) => {
    const draft = { product: product(), photos: [], updatedAt: '2026-10-15T12:26:00.000Z', ownerUserId: 'seller' };
    draftStoreFixture(t, draft);
    const stored = new Map();
    const client = createDraftSlotSync({ setTimer: setImmediate, clearTimer: clearImmediate, storage: {
      getItem: (key) => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value), removeItem: (key) => stored.delete(key),
    } });
    client.setUserId('seller');
    t.after(() => client.dispose());
    const remote = { holdsToken: false, meta: { hasContent: false, photosPending: true,
      updatedAt: '2026-10-15T12:01:00.000Z', deviceLabel: 'iPhone Safari', photoCount: 0 },
      payload: { product: { ...product(), colors: [] } } };
    let saves = 0, takeovers = 0;
    const puts = [];
    const saveAll = client.saveAll.bind(client);
    client.saveAll = (snapshot) => { saves++; return saveAll(snapshot); };
    const intent = { id: 'r4-locked', draftUpdatedAt: draft.updatedAt };
    const h = harness({ stagedDraft: false, slotClient: client, autoSaveIntent: intent,
      session: { user: { id: 'seller', app_metadata: { provider: 'email' } } },
      slotApi: {
        getDraftSlot: async () => remote,
        putDraftSlot: async (body) => {
          puts.push(body);
          if (body.token !== 'taken') throw Object.assign(new Error('locked'), { status: 409, code: 'token_mismatch', meta: remote.meta });
          return { token: 'taken', meta: { updatedAt: 'saved' } };
        },
        takeoverDraftSlot: async () => { takeovers++; return { ...remote, token: 'taken' }; },
      },
      draftStore: { ...localStore, getMobileGuestSave: () => intent },
    });
    t.after(() => h.cleanup());
    const route = await h.settle('ProductInputRoute');
    assert.equal(route.props.autoSaveIntent.id, intent.id);
    let tree = await h.settle('ProductInput', route.props);
    await drainDraftTasks(); tree = await h.settle('ProductInput', route.props);
    assert.equal(node(tree, 'MobileSaveModal').props.result.status, 'locked');
    assert.equal(saves, 1);
    button(h.expand(tree, 'MobileSaveModal'), '확인').props.onClick();
    tree = await h.settle('ProductInput', route.props);
    if (reenter) {
      h.unmount('ProductInput');
      await drainDraftTasks();
      tree = await h.settle('ProductInput', route.props);
    }
    button(h.expand(tree, 'EditingRightsLock'), '이 화면에서 이어서 하기').props.onClick();
    tree = await h.settle('ProductInput', route.props);
    assert.ok(find(tree, (value) => value.type === 'h3' && text(value) === '어느 내용으로 이어갈까요?'));
    assert.ok(actionLabels(tree).some((label) => label.startsWith('이 화면의 내용 (')));
    assert.ok(actionLabels(tree).some((label) => label.startsWith('다른 기기의 내용 (')));
    assert.ok(!actionLabels(tree).some((label) => label.includes('--:--')));
    assert.equal(node(tree, 'ColorImageGroup').props.group.images.length, 2);
    assert.equal(node(tree, 'MobileSaveModal').props.result, null);
    assert.equal(saves, 1);
    assert.equal(takeovers, 0);
    const writesBeforeChoice = puts.length;
    h.documentEvents.dispatchEvent(new Event('visibilitychange'));
    await drainDraftTasks(); tree = await h.settle('ProductInput', route.props);
    assert.equal(puts.length, writesBeforeChoice);
    assert.equal(saves, 1);
    const localChoice = find(tree, (value) => value.type === 'Button' && text(value).startsWith('이 화면의 내용 ('));
    await localChoice.props.onClick();
    tree = await h.settle('ProductInput', route.props);
    assert.equal(takeovers, 1);
    assert.equal(puts.at(-1).token, 'taken');
    assert.equal(puts.at(-1).payload.product.colors[0].images.length, 2);
    assert.equal(node(tree, 'EditingRightsLock'), undefined);
    assert.equal(node(tree, 'MobileSaveModal').props.result, null);
    assert.equal(saves, 1);
  });
}

for (const status of ['failed', 'locked']) {
  test(`R4 login save intent is not retried by pending or visibility events (${status})`, async (t) => {
    const h = harness({ autoSaveIntent: { id: `r4-${status}` }, saveAll: async () => ({ status }) });
    t.after(() => h.cleanup());
    let tree = await h.settle();
    assert.equal(node(tree, 'MobileSaveModal').props.result.status, status);
    h.emitPending(false);
    h.documentEvents.dispatchEvent(new Event('visibilitychange'));
    tree = await h.settle();
    assert.equal(h.calls.filter((call) => call[0] === 'saveAll').length, 1);
    assert.equal(node(tree, 'MobileSaveModal').props.result.status, status);
    button(h.expand(tree, 'MobileSaveModal'), status === 'failed' ? '닫기' : '확인').props.onClick();
    await h.settle();
    h.unmount('ProductInput');
    h.slot.stage({ payload: { product: product(), updatedAt: '2026-10-15T12:26:00.000Z' } });
    tree = await h.settle();
    assert.equal(h.calls.filter((call) => call[0] === 'saveAll').length, 1);
    assert.equal(node(tree, 'MobileSaveModal').props.result, null);
    await button(h.expand(tree, 'InputMainAction'), '저장하고 PC에서 이어하기').props.onClick();
    assert.equal(h.calls.filter((call) => call[0] === 'saveAll').length, 2);
  });
}


test('G4 signup waits for consent before guest photos reach the slot', async () => {
  const h = harness({ autoSaveIntent: { id: 'signup' }, sellerConsentReady: false });
  let tree = await h.settle();
  assert.equal(h.calls.filter((call) => call[0] === 'saveAll' || call[0] === 'queue').length, 0);
  h.auth.sellerConsentReady = true; tree = await h.settle();
  assert.equal(h.calls.filter((call) => call[0] === 'saveAll').length, 1);
  assert.equal(h.expand(tree, 'MobileSaveModal').props.children[0].props.role, 'status');
  h.cleanup();
});


test('G2 guest reload restores real IndexedDB photo bytes without any server work', async (t) => {
  const storage = draftStoreFixture(t);
  localIdentity.setUserId(null);
  const urls = ['front', 'back'].map((body) => URL.createObjectURL(new Blob([body], { type: 'image/jpeg' })));
  t.after(() => urls.forEach((url) => URL.revokeObjectURL(url)));
  const photos = product(); photos.colors[0].images = [];
  const restoredUrls = [];
  const draftStore = { ...localStore,
    restoreDraftProduct: (draft) => restoreDraftProduct(draft, { createObjectUrl(blob) {
      const url = URL.createObjectURL(blob); restoredUrls.push(url); return url;
    } }),
  };
  t.after(() => restoredUrls.forEach((url) => URL.revokeObjectURL(url)));
  const h = harness({ session: null, photos, stagedDraft: false, draftStore });
  t.after(() => h.cleanup());
  let tree = await h.settle();
  for (let i = 0; i < 2; i++) node(tree, 'ColorImageGroup').props.onAddFiles(['Front', 'Back'][i], [{ src: urls[i], type: 'image/jpeg' }]);
  tree = await h.settle(); await localStore.flushProductDraftSave();
  assert.equal(storage.saved.photos.length, 2);
  assert.equal(storage.saved.ownerUserId, null);
  assert.deepEqual(await Promise.all(storage.saved.photos.map((p) => p.blob.text())), ['front', 'back']);
  h.unmount('ProductInput'); await drainDraftTasks();
  urls.forEach((url) => URL.revokeObjectURL(url));
  tree = await h.settle();
  const images = node(tree, 'ColorImageGroup').props.group.images;
  assert.equal(images.length, 2);
  assert.deepEqual(await Promise.all(images.map((image) => fetch(image.src).then((r) => r.text()))), ['front', 'back']);
  assert.ok(h.doneGuardChecks.every((enabled) => enabled === false));
  assert.equal(h.calls.some((call) => call[0] === 'queue' || call[0] === 'saveAll' || call === 'analyze'), false);
  assert.ok(h.calls.filter((call) => call[0] === 'getProduct').every((call) => call[1] === null));
});


for (const sameTab of [true, false]) {
  test(`G4/G6 fresh login mount restores IDB bytes and completes real saveAll once (sameTab=${sameTab})`, async (t) => {
    const savedProduct = product();
    savedProduct.colors[0].images.forEach((image) => { image.src = `blob:expired-${image.id}`; image.type = 'image/jpeg'; });
    const storage = draftStoreFixture(t, { product: savedProduct, photos: ['front', 'back'].map((id) => ({
      imageId: id, colorId: 'base', slot: id === 'front' ? 'Front' : 'Back',
      blob: new Blob([id], { type: 'image/jpeg' }), mime: 'image/jpeg', filename: id,
    })), updatedAt: 'guest', ownerUserId: null });
    const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
    const shared = new Map();
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
      getItem: (key) => shared.get(key) ?? null, setItem: (key, value) => shared.set(key, value), removeItem: (key) => shared.delete(key),
    } });
    t.after(() => { if (originalStorage) Object.defineProperty(globalThis, 'localStorage', originalStorage); else delete globalThis.localStorage; });
    const intent = mobileGuestSave.rememberMobileGuestSave('guest');
    if (!sameTab) sessionStorage.removeItem('wl_draftPending');
    const client = createDraftSlotSync({ setTimer: setImmediate, clearTimer: clearImmediate });
    client.setUserId('seller');
    t.after(() => client.dispose());
    const puts = [], uploads = [], urls = [];
    let saves = 0;
    const realSaveAll = client.saveAll.bind(client);
    client.saveAll = (snapshot) => { saves++; return realSaveAll(snapshot); };
    const h = harness({ session: { user: { id: 'seller', last_sign_in_at: new Date(intent.createdAt + (sameTab ? -3000 : 1000)).toISOString() } },
      stagedDraft: false, autoSaveIntent: intent, slotClient: client,
      slotApi: {
        getDraftSlot: async () => null,
        uploadDraftSlotPhoto: async ({ blob }) => { uploads.push(await blob.text()); return { assetId: `asset-${uploads.length}`, url: `https://photos.test/${uploads.length}.jpg` }; },
        putDraftSlot: async (body) => { puts.push(body); return { token: 'owned', meta: { updatedAt: 'saved' } }; },
      },
      draftStore: { ...localStore,
        getMobileGuestSave: (session) => mobileGuestSave.getMobileGuestSave(session, { mobile: true }),
        clearMobileGuestSave: mobileGuestSave.clearMobileGuestSave,
        restoreDraftProduct: (draft) => restoreDraftProduct(draft, { createObjectUrl(blob) { const url = URL.createObjectURL(blob); urls.push(url); return url; } }),
      },
    });
    t.after(() => { h.cleanup(); urls.forEach((url) => URL.revokeObjectURL(url)); });
    const route = await h.settle('ProductInputRoute');
    assert.equal(route.props.autoSaveIntent.id, intent.id);
    let tree = await h.settle(); await drainDraftTasks(); tree = await h.settle();
    assert.equal(saves, 1);
    assert.deepEqual(uploads.sort(), ['back', 'front']);
    assert.equal(puts.at(-1).photosPending, false);
    assert.equal(puts.at(-1).payload.product.colors[0].images.length, 2);
    const modal = h.expand(tree, 'MobileSaveModal');
    assert.equal(text(node(modal, 'h3')), '사진 2장을 저장했어요');
    assert.equal(shared.has('wl_mobileGuestSave'), false);
    button(modal, '확인').props.onClick(); tree = await h.settle();
    assert.equal(node(tree, 'ColorImageGroup').props.readOnly, true);
    assert.equal(storage.saved.photos.length, 2);
    assert.equal(saves, 1);
  });
}

test('G2 a guest never restores or reads a signed-in account draft after session expiry', async (t) => {
  draftStoreFixture(t, { product: product(), photos: [], ownerUserId: 'old-account', updatedAt: 'old' });
  localIdentity.setUserId(null);
  const h = harness({ session: null, stagedDraft: false, photos: product(), draftStore: localStore });
  t.after(() => h.cleanup());
  const tree = await h.settle();
  assert.equal(node(tree, 'ColorImageGroup').props.group.images.length, 0);
  assert.equal(h.calls.some((call) => call[0] === 'queue' || call[0] === 'saveAll'), false);
});

test('R2 G3 a local save timeout keeps phone photos and opens login with the storage warning', async () => {
  const h = harness({ session: null, draftStore: { flushProductDraftSave: async () => { throw new Error('draft_save_timeout'); } } });
  let tree = await h.settle();
  await button(h.expand(tree, 'InputMainAction'), '저장하고 PC에서 이어하기').props.onClick();
  assert.equal(h.calls.find((call) => call[0] === 'toast')[1], '이 브라우저는 사진을 잠시 보관하지 못해요. 구글이나 카카오로 로그인하면 다시 골라 주세요.');
  assert.equal(h.calls.filter((call) => call[0] === 'login').length, 1);
  assert.equal(h.calls.some((call) => call[0] === 'saveAll'), false);
  tree = await h.settle(); assert.equal(node(tree, 'ColorImageGroup').props.group.images.length, 2);
  h.cleanup();
});


for (const reason of ['DataCloneError', 'draft_save_timeout', 'partial']) {
  test(`R2 failed local save still opens login and warns before redirect choices: ${reason}`, async (t) => {
    const h = harness({ session: null, draftStore: { flushProductDraftSave: async () => {
      if (reason === 'partial') return { saved: 1, failed: 1 };
      throw new Error(reason);
    } } });
    t.after(() => h.cleanup());
    const tree = await h.settle();
    await button(h.expand(tree, 'InputMainAction'), '저장하고 PC에서 이어하기').props.onClick();
    const warning = h.calls.findIndex((call) => call[0] === 'toast');
    const login = h.calls.findIndex((call) => call[0] === 'login');
    assert.ok(warning >= 0 && login > warning);
    assert.equal(h.calls[warning][1], '이 브라우저는 사진을 잠시 보관하지 못해요. 구글이나 카카오로 로그인하면 다시 골라 주세요.');
    assert.equal(h.calls[login][1], '/create/input');
    assert.equal(h.calls.filter((call) => call[0] === 'toast').length, 1);
    assert.equal(h.calls.some((call) => call[0] === 'queue' || call[0] === 'saveAll'), false);
  });
}

for (const [reason, signup, conflict = false] of [['DataCloneError', false], ['DataCloneError', true], ['timeout', false], ['DataCloneError', false, true]]) {
  test(`R2 IndexedDB ${reason} survives the guest remount and saves from memory (signup=${signup}, conflict=${conflict})`, async (t) => {
    if (reason === 'timeout') t.mock.timers.enable({ apis: ['setTimeout'] });
    const fixture = draftStoreFixture(t); localIdentity.setUserId(null);
    if (reason === 'timeout') fixture.pauseWrites();
    else fixture.failWrites(new DOMException('Blob storage unavailable', 'DataCloneError'));
    const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
    const values = new Map();
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
      getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key),
    } });
    t.after(() => { mobileGuestSave.clearMobileGuestSave(); if (original) Object.defineProperty(globalThis, 'localStorage', original); else delete globalThis.localStorage; });
    const p = product();
    const photos = ['front', 'back'].map((id) => ({ imageId: id, colorId: 'base', slot: id === 'front' ? 'Front' : 'Back', mime: 'image/jpeg', blob: new Blob([id], { type: 'image/jpeg' }) }));
    const urls = [];
    const uploadedBytes = [];
    let resolveSaved; const saved = new Promise((resolve) => { resolveSaved = resolve; });
    const h = harness({ session: null, sellerConsentReady: false, stagedDraft: { product: p, photos },
      previewRegistry: (options) => createProductPhotoPreviewRegistry({ ...options, createThumbnail: async () => { throw new Error('no canvas'); } }),
      saveAll: async (snapshot) => {
        for (const image of snapshot.product.colors[0].images) uploadedBytes.push(await fetch(image.src).then((res) => res.text()));
        resolveSaved();
        return { status: 'saved', photoCount: 2 };
      },
      draftStore: { ...localStore, ...mobileGuestSave,
        getMobileGuestSave: (session) => mobileGuestSave.getMobileGuestSave(session, { mobile: true }),
        getMobileGuestDraft: (intent) => mobileGuestSave.getMobileGuestDraft?.(intent),
        restoreDraftProduct: (draft) => restoreDraftProduct(draft, { createObjectUrl(blob) { const url = URL.createObjectURL(blob); urls.push(url); return url; } }),
      },
    });
    t.after(() => { h.cleanup(); urls.forEach((url) => URL.revokeObjectURL(url)); });
    h.slot.get = async () => conflict ? { meta: { hasContent: true, deviceLabel: 'Windows Chrome', updatedAt: 'v1', photoCount: 1 } } : null;
    h.slot.takeover = async () => ({ token: 'owned' });
    let tree = await h.settle();
    const saving = button(h.expand(tree, 'InputMainAction'), '저장하고 PC에서 이어하기').props.onClick();
    if (reason === 'timeout') {
      await drainDraftTasks(); assert.equal(fixture.waitingWrites, 1);
      t.mock.timers.tick(8000);
    }
    await saving;
    assert.equal(h.calls.filter((call) => call[0] === 'login').length, 1);
    assert.equal(fixture.saved, null);
    h.unmount('ProductInput');
    const intent = mobileGuestSave.getMobileGuestSave(null, { mobile: true });
    const memory = await mobileGuestSave.getMobileGuestDraft(intent);
    assert.deepEqual(await Promise.all(memory.photos.map((photo) => photo.blob.text())), ['front', 'back']);
    localIdentity.setUserId('seller');
    h.auth.session = { user: { id: 'seller', email: 'seller@test.example', last_sign_in_at: new Date().toISOString(), app_metadata: { provider: 'email' } } };
    h.auth.sellerConsentReady = !signup;
    let route = await h.settle('ProductInputRoute');
    if (conflict) {
      assert.equal(route.props.dialog.kind, 'mobile');
      assert.equal(h.calls.filter((call) => call[0] === 'saveAll').length, 0);
      assert.equal((await mobileGuestSave.getMobileGuestDraft(intent)).photos.length, 2);
      await route.props.actions.confirm(); route = await h.settle('ProductInputRoute');
    }
    assert.equal(route.type, h.components.ProductInput);
    assert.ok(route.props.autoSaveIntent);
    tree = await h.settle('ProductInput', route.props);
    assert.equal(node(tree, 'ColorImageGroup').props.group.images.length, 2);
    if (signup) {
      assert.equal(h.calls.filter((call) => call[0] === 'saveAll').length, 0);
      h.auth.sellerConsentReady = true;
      tree = await h.settle('ProductInput', route.props);
    }
    assert.equal(h.calls.filter((call) => call[0] === 'saveAll').length, 1);
    await saved; tree = await h.settle('ProductInput', route.props);
    assert.deepEqual(uploadedBytes, ['front', 'back']);
    assert.equal(text(node(h.expand(tree, 'MobileSaveModal'), 'h3')), '사진 2장을 저장했어요');
  });
}

for (const mobile of [true, false]) {
  test(`R2 signed-in input keeps the existing completed-project guard (mobile=${mobile})`, async (t) => {
    const h = harness({ mobile }); t.after(() => h.cleanup());
    await h.settle(); assert.ok(h.doneGuardChecks.every((enabled) => enabled === true));
  });
}

test('R2 an automatic IndexedDB Blob rejection stays retryable without an unhandled rejection', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const fixture = draftStoreFixture(t); localIdentity.setUserId(null);
  fixture.failWrites(new DOMException('Blob storage unavailable', 'DataCloneError'));
  const photos = product(); photos.colors[0].images = [];
  const h = harness({ session: null, photos, draftStore: localStore });
  t.after(() => h.cleanup());
  await h.settle();
  t.mock.timers.tick(500); await drainDraftTasks();
  await assert.rejects(localStore.flushProductDraftSave(), { name: 'DataCloneError' });
});


for (const automatic of [false, true]) {
test(`R3 unknown consent retries on save, reports failure, and saves only after confirmed retry (auto=${automatic})`, async (t) => {
  let failure = true;
  const h = harness({ sellerConsentReady: false, autoSaveIntent: automatic ? { id: 'retry' } : null, consentRetry: async () => {
    if (failure) throw new Error('offline');
    h.auth.sellerConsentReady = true; return true;
  } });
  t.after(() => h.cleanup());
  let tree = await h.settle();
  await button(h.expand(tree, 'InputMainAction'), '저장하고 PC에서 이어하기').props.onClick();
  assert.equal(h.calls.filter((call) => call[0] === 'consent').length, 1);
  assert.equal(h.calls.find((call) => call[0] === 'toast')?.[1], '서버 연결이 불안정해요. 잠시 후 다시 시도해 주세요.');
  assert.equal(h.calls.some((call) => call[0] === 'queue' || call[0] === 'saveAll'), false);
  failure = false;
  await button(h.expand(tree, 'InputMainAction'), '저장하고 PC에서 이어하기').props.onClick();
  tree = await h.settle();
  assert.equal(h.calls.filter((call) => call[0] === 'consent').length, 2);
  assert.equal(h.calls.filter((call) => call[0] === 'saveAll').length, 1);
  assert.equal(text(node(h.expand(tree, 'MobileSaveModal'), 'h3')), '사진 2장을 저장했어요');
});
}

test('R3 signed-in phone also blocks background uploads while consent is unknown', async (t) => {
  const h = harness({ sellerConsentReady: false }); t.after(() => h.cleanup());
  await h.settle();
  assert.equal(h.calls.some((call) => call[0] === 'queue' || call[0] === 'saveAll'), false);
});

for (const blocked of ['sessionStorage', 'both']) {
  test(`R3 blocked storage opens plain guest input (${blocked})`, async (t) => {
    for (const key of blocked === 'both' ? ['sessionStorage', 'localStorage'] : ['sessionStorage']) {
      const original = Object.getOwnPropertyDescriptor(globalThis, key);
      Object.defineProperty(globalThis, key, { configurable: true, get() { throw new DOMException('blocked', 'SecurityError'); } });
      t.after(() => { if (original) Object.defineProperty(globalThis, key, original); else delete globalThis[key]; });
    }
    assert.equal(mobileGuestSave.getMobileGuestSave(null, { mobile: true }), null);
    const h = harness({ session: null, stagedDraft: false, draftStore: { hasPendingDraft: localStore.hasPendingDraft } });
    t.after(() => h.cleanup());
    const tree = await h.settle();
    assert.ok(node(tree, 'ColorImageGroup'));
    assert.equal(node(tree, 'ColorImageGroup').props.group.images.length, 0);
    assert.equal(h.calls.some((call) => call[0] === 'queue' || call[0] === 'saveAll'), false);
  });
}

for (const failBinding of [false, true]) {
  test(`R3 D1 keep for A never offers the phone draft to B (binding write fails=${failBinding})`, async (t) => {
    const fixture = draftStoreFixture(t, { updatedAt: 'guest-version', ownerUserId: null, product: product(), photos: [] });
    localIdentity.setUserId('A');
    if (failBinding) fixture.failWrites(new DOMException('read only', 'QuotaExceededError'));
    const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
    const shared = new Map();
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
      getItem: (key) => shared.get(key) ?? null, setItem: (key, value) => shared.set(key, value), removeItem: (key) => shared.delete(key),
    } });
    t.after(() => { mobileGuestSave.clearMobileGuestSave(); mobileGuestSave.clearMobileGuestDraftOwner?.(); if (original) Object.defineProperty(globalThis, 'localStorage', original); else delete globalThis.localStorage; });
    const intent = mobileGuestSave.rememberMobileGuestSave('guest-version');
    let remote = { meta: { deviceLabel: 'Windows Chrome', hasContent: true, updatedAt: 'pc-v1', photoCount: 2 } };
    const h = harness({ stagedDraft: false, session: { user: { id: 'A', last_sign_in_at: new Date(intent.createdAt).toISOString() } },
      draftStore: { ...localStore, ...mobileGuestSave, getMobileGuestSave: (session) => mobileGuestSave.getMobileGuestSave(session, { mobile: true }) },
    });
    t.after(() => h.cleanup());
    h.slot.get = async () => remote;
    let tree = await h.settle('ProductInputRoute');
    assert.equal(tree.props.dialog.kind, 'mobile');
    await tree.props.actions.keepMobile(); await h.settle('ProductInputRoute');
    assert.equal(shared.has('wl_mobileGuestSave'), false);
    remote = null; localIdentity.setUserId('B');
    h.auth.session = { user: { id: 'B' } };
    tree = await h.settle('ProductInputRoute');
    assert.equal(tree.type, h.components.ProductInput);
    assert.ok(!tree.props.autoSaveIntent);
    tree = await h.settle('ProductInput', tree.props);
    assert.equal(node(tree, 'ColorImageGroup').props.group.images.length, 0);
    assert.equal(h.calls.some((call) => call[0] === 'saveAll'), false);
  });
}
