import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { transformWithEsbuild } from 'vite';
import { createDraftEntryActions } from '../../src/lib/draftEntry.js';
import { getBaseSlotUploadRoom, getPendingTileCount, PENDING_TILE_DELAY_MS } from '../../src/features/product-input/pendingTiles.js';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const clean = (source) => source.replace(/^import [\s\S]*?;\n/gm, '').replace(/^export default .*;$/gm, '').replace(/^export /gm, '');
const { code } = await transformWithEsbuild([
  'src/features/product-input/ProductInput.jsx', 'src/features/product-input/MobileInputHandoff.jsx',
  'src/features/shell/HangTag.jsx', 'src/features/shell/AddressCopyChip.jsx',
  'src/lib/useDraftEntryActions.js', 'src/features/shell/DraftEntryDialog.jsx',
].map((path) => clean(read(path))).join('\n'), 'MobileInput.jsx', { jsx: 'transform' });
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

function harness({ mobile = true, analysis = null, saveAll, transcode, photos = product() } = {}) {
  const calls = [];
  const frames = new Map();
  let active, cursor, effects = [], dirty = false;
  let staged = { payload: { product: photos, analysis, composeMode: 'basic' } };
  const state = { composeMode: 'basic', mannequinJob: {}, projectGeneration: 0,
    restoreComposeMode() {}, async beginProject() { calls.push('begin'); }, setInputPromotionLocked() {} };
  const useAppStore = (select) => select(state);
  useAppStore.getState = () => state;
  const slot = {
    configure() {}, onConflict: () => () => {}, activate() {}, queue(snapshot) { calls.push(['queue', snapshot]); },
    consumeStaged() { const next = staged; staged = null; return next; },
    get: async () => ({ meta: { photoCount: 2, updatedAt: 'v1' } }),
    getServerSyncedAt: () => 'v1', hasPendingRemoval: () => false,
    removeForNewFlow: async () => { calls.push('remove'); },
    saveAll: async (snapshot) => { calls.push(['saveAll', snapshot]); return saveAll ? saveAll(snapshot) : { status: 'saved', photoCount: 2 }; },
  };
  const bindings = {
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
    useNavigate: () => (path) => calls.push(['navigate', path]), useLocation: () => ({ state: null }),
    useAuth: () => ({ session: { user: { id: 'seller' } }, loading: false }),
    useDoneGuard: () => false, useToast: () => ({ push: (...args) => calls.push(['toast', ...args]) }),
    useAppStore, isMobileDevice: () => mobile, isMockMode: false, draftSlot: slot, createDraftEntryActions,
    api: { getProduct: async () => photos, getCatalogs: async () => ({ angleSlots: ['Front', 'Back'], swatchColors: [] }),
      analyzeProduct: async () => { calls.push('analyze'); return { suggestedName: '니트' }; } },
    createProductPhotoPreviewRegistry: () => ({ sync() {}, dispose() {}, release() {}, displayUrl: (_id, src) => src }),
    createTrailingPatchScheduler: () => ({ flush() {}, schedule() {} }),
    generationWorkWarningKind: () => 'none', restoreDraftProduct: (draft) => draft.product,
    normalizeAnalysisFit: (value) => value, isAnalysisRunning: () => false,
    queueProductDraftSave: () => {}, flushProductDraftSave: async () => {}, hasPendingDraft: () => false,
    formatDraftRelativeTime: () => '1분 전', getBaseSlotUploadRoom, getPendingTileCount, PENDING_TILE_DELAY_MS,
    invalidateStoryboardForProductPhotoEdit() {}, invalidateStoryboardEntryPrefetch() {},
    analysisResponseProductPatch: () => ({}), autofillColorGroups: (colors) => colors,
    mergeProductOwnedAnalysisFields: (value) => value, photoCaption: () => '사진', uid: () => 'new-photo',
    looksLikeImageFile: () => true,
    toUploadableImages: transcode || (async () => ({ files: [], failed: [] })),
    getUploadValidationError: () => null, MAX_UPLOAD_BYTES: 25000000,
    window: { addEventListener() {}, removeEventListener() {}, scrollTo() {}, location: { origin: 'https://ai.wearless.kr', host: 'ai.wearless.kr' } },
    document: { addEventListener() {}, removeEventListener() {} },
    copyHandoffAddress: async (pushToast) => pushToast('주소를 복사했어요'),
    s: new Proxy({}, { get: (_, key) => key }),
  };
  for (const name of ['Button', 'Modal', 'Icon', 'IconButton', 'ErrorState', 'UploadPendingTile', 'PageHead',
    'WizardCTA', 'DoneGuardModal', 'CreditShortfallModal', 'AnalysisForm', 'AnalysisProgress', 'AnalysisSkeleton']) bindings[name] = name;
  const components = new Function(...Object.keys(bindings), `${code}; return { ProductInput, ColorImageGroup, AddDrop, InputMainAction, MobileSaveModal, MobileSaveStatus, MobileAnalyzedNotice, EditingRightsLock, DraftEntryDialog, HangTag, AddressCopyChip };`)(...Object.values(bindings));
  function render(name = 'ProductInput', props = {}) {
    if (!frames.has(name)) frames.set(name, []);
    active = frames.get(name); cursor = 0; dirty = false;
    return components[name](props);
  }
  async function settle() {
    let tree = render();
    for (let i = 0; i < 30; i++) {
      const batch = effects; effects = []; batch.forEach((effect) => effect());
      await new Promise(setImmediate);
      if (!dirty && !effects.length) return tree;
      tree = render();
    }
    throw new Error('did not settle');
  }
  return { calls, render, settle, components,
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
  assert.equal(text(node(saved, 'p')), 'PC에서 같은 계정으로 로그인하면 바로 이어져요.');
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
      assert.equal(group.props.onPendingChange, undefined);
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
    const warning = h.expand(tree, 'DraftEntryDialog');
    assert.ok(text(warning).includes('업로드한 사진 2장이 지워져요. 한 번에 한 상품만 저장할 수 있어요.'));
    assert.ok(!h.calls.includes('remove'));
    await button(warning, '지우고 계속').props.onClick();
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
  const mobile = css.slice(css.indexOf('.wizard.pi-handoff'), css.lastIndexOf('@media (max-width: 560px)'));
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
