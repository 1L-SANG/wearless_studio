import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createServer, transformWithEsbuild } from 'vite';
import { defaultAnalysisShape } from '../../src/lib/api/shapes.js';
import { isGenerationRelevantAnalysisPatch } from '../../src/lib/generationRelevance.js';
import { createFeatureRun, stripFeatureClientFields } from '../../src/lib/featureRun.js';
import { persistAnalysisEdit, registerAnalysisEditSave, mergeLatestFailedAnalysisPatch } from '../../src/features/product-input/saveRouting.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const source = read('../../src/features/analysis/AnalysisForm.jsx');
const inputSource = read('../../src/features/product-input/ProductInput.jsx');
const styles = read('../../src/styles/features.css');
const placeholder = '- 가을 출근룩으로 활용할 니트예요.\n- 소매가 길어서 키 작은 분은 접어 입으면 예뻐요.\n- 나일론 소재라서 여름에도 시원하게 착용 가능해요.';
const nodes = (tree) => Array.isArray(tree) ? tree.flatMap(nodes)
  : !tree || typeof tree !== 'object' ? [] : [tree, ...nodes(tree.props?.children)];
const deferred = () => { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return { promise, resolve }; };
const drain = async () => { for (let i = 0; i < 50; i++) await Promise.resolve(); };
let vite, mock, db, api, sync, featureDraft, features, http, supabase;
before(async () => {
  vite = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, include: [] },
    define: { 'import.meta.env.VITE_ANALYSIS_FAST_ENTRY': '"1"' },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null }, appType: 'custom', logLevel: 'silent' });
  ({ mockAdapter: mock } = await vite.ssrLoadModule('/src/lib/api/mockAdapter.js'));
  ({ DB: db } = await vite.ssrLoadModule('/src/mock/db.js'));
  ({ api } = await vite.ssrLoadModule('/src/lib/api/index.js'));
  sync = await vite.ssrLoadModule('/src/lib/draftSync.js');
  featureDraft = await vite.ssrLoadModule('/src/lib/featureRunDraft.js');
  features = await vite.ssrLoadModule('/src/lib/featureRun.js');
  ({ httpAdapter: http } = await vite.ssrLoadModule('/src/lib/api/httpAdapter.js'));
  ({ supabase } = await vite.ssrLoadModule('/src/lib/supabase.js'));
});
after(async () => { await vite?.close(); });

// 기존 analysis 테스트처럼 실제 JSX와 훅을 실행하고 브라우저만 대신한다.
async function form(initial = '', onChange = () => {}, onNext = async () => {}) {
  const stateStart = source.indexOf('  const [mdCommentDraft,');
  const sectionStart = source.indexOf('      {/* MD 코멘트 */}');
  assert.ok(stateStart >= 0 && sectionStart >= 0, 'MD 코멘트 초안과 섹션이 있어야 한다');
  const state = source.slice(stateStart, source.indexOf('  const [ccDraft,', stateStart));
  const section = source.slice(sectionStart, source.indexOf('      {/* 5. model select', sectionStart));
  const confirm = source.slice(source.indexOf('  const confirmAnalysis ='), source.indexOf('  const [customMatchOpen,'));
  const placeholderDeclaration = source.match(/^const MD_COMMENT_PLACEHOLDER = .+;$/m)[0];
  const { code } = await transformWithEsbuild(`${placeholderDeclaration}\nfunction render(a) { ${state}\n${confirm}\nreturn (<>${section}</>); }`, 'md-comment.jsx', { jsx: 'transform' });
  const hooks = []; let cursor = 0, effects = [], analysis = { mdComment: initial }, tree;
  const bindings = {
    React: { Fragment: 'fragment', createElement: (type, props, ...children) => ({ type, props: { ...props, children } }) },
    useState(initial) { const index = cursor++; if (!(index in hooks)) hooks[index] = typeof initial === 'function' ? initial() : initial;
      return [hooks[index], (next) => { hooks[index] = typeof next === 'function' ? next(hooks[index]) : next; }]; },
    useRef(current) { return hooks[cursor++] ||= { current }; },
    useEffect(effect, deps) { const index = cursor++, previous = hooks[index];
      if (!previous || deps.some((value, i) => !Object.is(value, previous.deps[i]))) {
        hooks[index] = { deps }; effects.push(effect);
      } },
    onChange, onNext, confirming: false, setConfirming() {}, onConfirmingChange() {},
    composeModeSaveRef: { current: Promise.resolve() }, mannequinGenerationCost: 0,
  };
  const render = new Function(...Object.keys(bindings), `${code}; return render;`)(...Object.values(bindings));
  const show = (next = analysis) => { analysis = next; cursor = 0; tree = render(analysis);
    effects.splice(0).forEach((effect) => effect()); cursor = 0; tree = render(analysis); return tree; };
  const textarea = () => nodes(tree).find((node) => node.type === 'textarea');
  show();
  // 확정 함수도 같은 초안 훅의 클로저를 사용하도록 렌더 반환값에 함께 담는다.
  const { code: withConfirm } = await transformWithEsbuild(`${placeholderDeclaration}\nfunction render(a) { ${state}\n${confirm}\nreturn { tree: (<>${section}</>), confirmAnalysis }; }`, 'md-confirm.jsx', { jsx: 'transform' });
  const renderConfirm = new Function(...Object.keys(bindings), `${withConfirm}; return render;`)(...Object.values(bindings));
  return { show, textarea, focus() { textarea().props.onFocus(); },
    type(value) { textarea().props.onChange({ target: { value } }); show(); },
    blur() { const result = textarea().props.onBlur(); show(); return result; },
    confirm() { cursor = 0; return renderConfirm(analysis).confirmAnalysis(); },
    field() { return nodes(tree).find((node) => node.props.className?.startsWith('md-field')); },
    count() { return nodes(tree).find((node) => node.props.className === 'md-count').props.children.join(''); },
    hint() { return nodes(tree).some((node) => node.props.className === 'hint md-hint'); },
  };
}

test('분석 기본 shape와 목 DB는 빈 MD 코멘트를 가진다', () => {
  assert.equal(defaultAnalysisShape().mdComment, '');
  assert.equal(db.analysis.mdComment, '');
});
test('목 조회와 목 분석은 과거 데이터의 누락된 MD 코멘트를 빈 값으로 채운다', async (t) => {
  const original = db.analysis; db.analysis = { ...original }; delete db.analysis.mdComment;
  t.after(() => { db.analysis = original; });
  assert.equal((await mock.getAnalysis(null)).mdComment, '');
  assert.equal((await mock.analyzeProduct(null)).mdComment, '');
});
test('입력 중 저장하지 않고 blur에서 공백을 뺀 메모를 한 번 저장한다', async () => {
  const patches = [], h = await form('', (patch) => patches.push(patch));
  h.focus(); h.type('  가'); h.type('  가을 니트  ');
  assert.deepEqual(patches, []);
  h.blur(); h.blur();
  assert.deepEqual(patches, [{ mdComment: '가을 니트' }]);
  assert.equal(h.textarea().props.value, '가을 니트');
});
test('변경 없음과 앞뒤 공백만 바뀐 경우에는 저장하지 않고 지우기는 빈 문자열로 저장한다', async () => {
  const patches = [], h = await form('가을 니트', (patch) => patches.push(patch));
  h.focus(); h.blur(); h.focus(); h.type('  가을 니트  '); h.blur();
  assert.deepEqual(patches, []);
  h.focus(); h.type(' '); h.blur(); assert.deepEqual(patches, [{ mdComment: '' }]);
});
test('확정은 포커스 중인 초안을 기존 분석 저장 대기열에 먼저 넣고 저장이 끝나야 이동한다', async () => {
  const blocked = deferred(), events = [], analysisSaveChainRef = { current: Promise.resolve() };
  const bindings = { analysisSaveChainRef, analysisProjectId: 'md-project',
    latestAnalysisPatchRef: { current: {} }, failedAnalysisPatchRef: { current: null }, analysisSaveErrorRef: { current: null },
    persistedColorsRef: { current: [] }, setAnalysis() {}, toast: { push() {} },
    isMatchRecommendationPatch: () => false, mergeLatestFailedAnalysisPatch, registerAnalysisEditSave, persistAnalysisEdit,
    api: { saveAnalysis: async (_id, patch) => { events.push(['saving', patch]); await blocked.promise; events.push('saved'); return patch; } } };
  const queueCode = inputSource.slice(inputSource.indexOf('  const queueAnalysisPatch ='), inputSource.indexOf('  analysisPatchQueueRef.current ='));
  const queue = new Function(...Object.keys(bindings), `${queueCode}; return queueAnalysisPatch;`)(...Object.values(bindings));
  const waitCode = inputSource.slice(inputSource.indexOf('      colorSaveSchedulerRef.current.flush();'), inputSource.indexOf('      if (failedAnalysisPatchRef.current)'));
  assert.match(waitCode, /await analysisSaveChainRef.current/);
  const next = new Function('analysisSaveChainRef', 'colorSaveSchedulerRef', `return async () => { ${waitCode} };`)(analysisSaveChainRef, { current: { flush() {} } });
  const h = await form('', queue, async () => { await next(); events.push('next'); });
  h.focus(); h.type('  먼저 저장  '); const confirming = h.confirm(); await drain();
  assert.deepEqual(events, [['saving', { mdComment: '먼저 저장' }]]);
  h.blur(); blocked.resolve(); await confirming;
  assert.deepEqual(events, [['saving', { mdComment: '먼저 저장' }], 'saved', 'next']);
});
test('확정은 onChange가 돌려준 저장 Promise도 기다린다', async () => {
  const blocked = deferred(); let next = false;
  const h = await form('', () => blocked.promise, async () => { next = true; });
  h.focus(); h.type('메모'); const confirming = h.confirm(); await drain(); assert.equal(next, false);
  blocked.resolve(); await confirming; assert.equal(next, true);
});
test('다른 경로의 분석 갱신은 쓰는 중인 초안을 보존하고 blur 후에는 저장값을 따른다', async () => {
  const patches = [], h = await form('저장값', (patch) => patches.push(patch));
  h.focus(); h.type('내 초안'); h.show({ mdComment: '서버 갱신', sellingPoints: ['다듬은 특징'] });
  assert.equal(h.textarea().props.value, '내 초안'); h.blur();
  assert.deepEqual(patches, [{ mdComment: '내 초안' }]);
  h.show({ mdComment: '재조회' }); assert.equal(h.textarea().props.value, '재조회');
});
test('포커스만 하고 쓰지 않았으면 갱신된 저장값을 되돌려 저장하지 않는다', async () => {
  const patches = [], h = await form('원래', (patch) => patches.push(patch));
  h.focus(); h.show({ mdComment: '새 저장값' }); h.blur();
  assert.deepEqual(patches, []); assert.equal(h.textarea().props.value, '새 저장값');
});
test('안내 문구 없이 글자 수, 접근성 이름, 세 줄 예시를 제공한다', async () => {
  const h = await form(); assert.equal(h.hint(), false); assert.equal(h.count(), '0/300');
  assert.equal(h.textarea().props['aria-label'], 'MD 코멘트');
  assert.equal(h.textarea().props.maxLength, 300); assert.equal(h.textarea().props.rows, 3);
  assert.equal(h.textarea().props.placeholder, placeholder);
  h.focus(); h.type('가을 니트'); assert.equal(h.hint(), false); assert.equal(h.count(), '5/300');
  assert.match(h.field().props.className, /is-filled/);
  h.type('  '); assert.equal(h.hint(), false); assert.equal(h.count(), '2/300');
});
test('칸 안 여백과 글자 수를 눌러도 textarea로 포커스를 옮긴다', async () => {
  const h = await form(); let focused = 0;
  h.textarea().props.ref.current = { focus() { focused++; } };
  const field = h.field(); field.props.onMouseDown({ target: {}, preventDefault() {} });
  assert.equal(focused, 1);
});
test('시안의 링과 제목 굵기, CSS 자동 높이를 쓰고 새 인라인 스타일과 word-break는 없다', async () => {
  const h = await form(); const tree = h.show();
  assert.equal(nodes(tree).some((node) => node.props.style), false);
  assert.match(source, /md-latin">MD<\/span>/);
  const css = styles.slice(styles.indexOf('.md-title'), styles.indexOf('.model-grid'));
  assert.match(css, /Cal Sans.*합성/);
  assert.match(css, /\.md-title \.md-latin \{ font-weight: 400/);
  assert.match(css, /var\(--elev-inset\), 0 0 0 1px var\(--ring\)/);
  assert.match(css, /0 0 0 1\.5px #8fbfee/);
  assert.match(css, /resize: none/); assert.match(css, /line-height: 1\.6/);
  assert.match(css, /content: attr\(data-value\)/); assert.match(css, /white-space: pre-wrap/);
  assert.doesNotMatch(css, /word-break:/);
});
test('자동 높이용 숨은 글자는 textarea와 같은 안쪽 폭으로 줄바꿈한다', () => {
  const shared = styles.match(/\.md-input::after, \.md-field textarea \{([^}]+)\}/)[1];
  // 전역 * 리셋은 ::after에 적용되지 않으므로 좌우 padding 32px도 같은 폭에 포함한다.
  assert.match(shared, /box-sizing: border-box/);
  assert.match(shared, /width: 100%/); assert.match(shared, /padding: 14px 16px 4px/);
});
test('빠른 진입의 특징 다듬기는 최신 MD 코멘트를 보존한다', async () => {
  let analysis = { mdComment: '내 판매 메모', sellingPoints: ['예비 특징'], aiSuggestedPoints: ['예비 특징'] };
  const run = createFeatureRun({ readAnalysis: () => analysis, saveAnalysis: async (patch) => { analysis = { ...analysis, ...patch }; },
    requestFeatures: async () => ({ points: { status: 'ready', items: ['다듬은 특징'] }, featureTicket: 'feature' }),
    requestMerge: async () => ({ decisions: [{ late: 0, action: 'refine', early: 0 }] }) });
  await run.start({ runId: 'md_test_123', photoSignature: 'sig' });
  await run.receiveCore({ sellingPoints: ['예비 특징'], aiSuggestedPoints: ['예비 특징'], pointsTicket: 'core' });
  analysis.mdComment = '최신 메모'; await drain(); await run.wait();
  assert.deepEqual(analysis.sellingPoints, ['다듬은 특징']); assert.equal(analysis.mdComment, '최신 메모');
  assert.equal(stripFeatureClientFields(analysis).mdComment, '최신 메모');
  assert.match(read('../../src/lib/featureRunDraft.js'), /latest = \{ \.\.\.currentAnalysis\(\), \.\.\.patch \}/);
});
for (const fast of [false, true]) test(`공개 분석 메모는 로그인 승격 때 저장된다, 빠른 진입 ${fast}`, async (t) => {
  const original = {}; const saved = [];
  const replacement = { createProject: async () => ({ id: `md-promote-${fast}` }), saveProduct: async () => {},
    saveAnalysis: async (_id, value) => saved.push(value), patchProject: async () => {}, getCustomMatchDraft: () => null };
  for (const [key, value] of Object.entries(replacement)) { original[key] = api[key]; api[key] = value; }
  t.after(() => { Object.assign(api, original); sync.resetDraftSyncSingleFlight(); features.setActiveFeatureRun(null); });
  let analysis = { mdComment: '로그인 전 판매 메모', sellingPoints: ['셀러 특징'], aiSuggestedPoints: ['셀러 특징'] };
  if (fast) {
    const run = features.createFeatureRun({ readAnalysis: () => analysis, saveAnalysis: async (patch) => { analysis = { ...analysis, ...patch }; } });
    await run.resume({ runId: 'md_promotion_123', photoSignature: 'sig', stage: 'applied', coreReceived: true });
    analysis.featureRun = run.getRecord(); features.setActiveFeatureRun(run);
    assert.equal(featureDraft.getDraftFeatureRun({ analysis }), run);
  }
  await sync.promoteDraftToProject({ product: { name: '옷', colors: [] }, analysis, photos: [], composeMode: 'basic' });
  assert.equal(saved.length, 1); assert.equal(saved[0].mdComment, '로그인 전 판매 메모');
});
test('MD 코멘트 PATCH는 매칭 갱신 없이 보낸 키만 저장한다', async (t) => {
  const originalFetch = globalThis.fetch, originalSession = supabase.auth.getSession, calls = [];
  supabase.auth.getSession = async () => ({ data: { session: { access_token: 'test' } } });
  globalThis.fetch = async (url, options) => { calls.push([url, options]); return { ok: true, status: 200, headers: new Headers(), json: async () => ({ mdComment: '저장' }) }; };
  t.after(() => { globalThis.fetch = originalFetch; supabase.auth.getSession = originalSession; });
  await http.saveAnalysis('md-http', { mdComment: '저장' });
  assert.equal(calls.length, 1); assert.match(calls[0][0], /analysis\?merge=1$/);
  assert.deepEqual(JSON.parse(calls[0][1].body), { mdComment: '저장' });
});
for (const fast of [false, true]) test(`사진 재분석은 MD 코멘트를 다른 셀러 편집값과 같은 기본값으로 바꾼다, 빠른 진입 ${fast}`, async (t) => {
  const originalFetch = globalThis.fetch, originalSession = supabase.auth.getSession;
  const calls = [];
  supabase.auth.getSession = async () => ({ data: { session: null } });
  globalThis.fetch = async (url, options) => {
    calls.push([url, options]);
    return { ok: true, status: 200, headers: new Headers(), json: async () => ({ data: {
      clothingType: 'top', aiSuggestedPoints: ['새 특징'], mdComment: 'AI가 임의로 보낸 메모', sellingPoints: ['예전 특징'],
    } }) };
  };
  t.after(() => { globalThis.fetch = originalFetch; supabase.auth.getSession = originalSession; });
  const fresh = await http.publicAnalyze({ name: '니트', colors: [] }, {
    photos: [{ slot: 'Front', blob: new Blob(['photo'], { type: 'image/jpeg' }) }],
    ...(fast ? { parts: 'core', runId: 'md_reanalysis_123' } : {}),
  });
  assert.equal(fresh.mdComment, ''); assert.deepEqual(fresh.sellingPoints, []);
  assert.deepEqual(fresh.aiSuggestedPoints, ['새 특징']);
  assert.equal(calls.length, 1); assert.equal(calls[0][1].body.has('mdComment'), false);
  assert.match(inputSource, /setAnalysis\(coreAnalysis\)/);
  assert.match(inputSource, /latestAnalysisRef.current = null;[\s\S]*?featureRun.startAnalysis/);
});
test('MD 코멘트는 콘티 시드와 생성 관련 편집 판정에 영향을 주지 않는다', async (t) => {
  const original = structuredClone(db.analysis), storyboard = db.storyboard;
  t.after(() => { db.analysis = original; });
  await mock.saveAnalysis(null, { mdComment: '메모' });
  assert.equal(db.storyboard, storyboard);
  assert.equal(isGenerationRelevantAnalysisPatch({ mdComment: '메모' }), false);
  const routing = read('../../src/features/product-input/saveRouting.js');
  assert.doesNotMatch(routing.match(/const STORYBOARD_SEED_PATCH_KEYS[^;]+/)[0], /mdComment/);
});
