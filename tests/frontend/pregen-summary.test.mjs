import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { detailPageCreditCost } from '../../src/lib/limits.js';
import { detailPageGenerationCreditShortfall } from '../../src/lib/creditPreflight.js';
import { summaryEditTarget } from '../../src/features/generation-summary/summaryData.js';
import { cutFitProfileRecord } from '../../src/features/mannequin/cutFitProfile.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
function mannequinActions({ credits = 200, count = 3, save = async () => {}, onNavigate = () => {} } = {}) {
  const source = read('../../src/features/mannequin/Mannequin.jsx');
  const events = [];
  const targets = [];
  let summary = null;
  const state = { account: { credits } };
  const dependencies = {
    busy: false, submittingRef: { current: false }, regenerateState: 'idle', needsRegen: false,
    buildFitProfile: () => ({ axes: { fit: 'regular' } }),
    analysis: { fitProfile: { axes: { fit: 'regular' } } },
    setSummaryProfile: (value) => { summary = value; },
    summaryProfile: { axes: { fit: 'regular' } },
    useAppStore: { getState: () => state }, aiCutCount: count,
    summaryEditTarget, detailPageGenerationCreditShortfall, setCreditShortfall: (value) => events.push(['shortfall', value]),
    setBusy: () => {}, selected: { id: 'cut-1', fitProfileRecorded: true, fitProfile: null },
    cutFitProfileRecord, selectionFitSaveRef: { current: Promise.resolve() },
    selectMannequin: async () => events.push(['selection']),
    api: { saveAnalysis: async () => { await save(); events.push(['save']); } },
    projectId: 'project-1', setAnalysis: () => {},
    navigate: (path, options) => { events.push(['navigate', path]); targets.push({ path, ...options }); onNavigate(path); }, pushToast: (message) => events.push(['toast', message]),
  };
  const start = source.indexOf('const onCta = async () =>');
  const end = source.indexOf('const regenerateActive =', start);
  const actions = new Function(...Object.keys(dependencies),
    `${source.slice(start, end)}; return { onCta, onContinueCurrentVersion, continueWithSelectedVersion, editSummaryField: typeof editSummaryField === "undefined" ? null : editSummaryField };`)(...Object.values(dependencies));
  return { ...actions, events, targets, state, summary: () => summary };
}

test('이대로 진행은 요약만 열고 생성 화면이나 저장 요청으로 가지 않는다', async () => {
  const actions = mannequinActions();
  await actions.onCta();
  assert.ok(actions.summary(), '생성 전에 요약 확인 창을 열어야 한다');
  assert.deepEqual(actions.events, []);
});

test('현재 버전으로 진행도 최종 요약 확인을 거친다', async () => {
  const actions = mannequinActions();
  await actions.onContinueCurrentVersion();
  assert.ok(actions.summary());
  assert.deepEqual(actions.events, []);
});

test('요약 최종 확인은 현재 잔액이 부족하면 기존 안내로 보낸다', async () => {
  const actions = mannequinActions({ credits: 94 });
  await actions.continueWithSelectedVersion(null);
  assert.equal(actions.events[0][0], 'shortfall');
  assert.equal(actions.events[0][1].requiredCredits, 95);
  assert.equal(actions.events[0][1].availableCredits, 94);
  assert.equal(actions.events.length, 1);
});

test('최종 확인을 두 번 눌러도 기존 생성 경로에 한 번만 진입한다', async () => {
  let resolveSave;
  const saving = new Promise((resolve) => { resolveSave = resolve; });
  const actions = mannequinActions({ save: () => saving });
  const first = actions.continueWithSelectedVersion(null);
  const second = actions.continueWithSelectedVersion(null);
  await second;
  resolveSave();
  await first;
  assert.deepEqual(actions.events, [['selection'], ['save'], ['navigate', '/create/generating']]);
});

const summaryUrl = new URL('../../src/features/generation-summary/summaryData.js', import.meta.url);
async function summaryModule() {
  assert.ok(existsSync(summaryUrl), '재사용 요약 데이터 경계가 필요하다');
  return import(summaryUrl.href);
}

for (const count of [0, 1, 2, 3, 4, 5, 8, null]) {
  test(`${count}컷 요약 비용은 기존 버튼의 detailPageCreditCost와 같다`, async () => {
    const { buildGenerationSummary } = await summaryModule();
    const summary = buildGenerationSummary({ aiCutCount: count });
    assert.equal(summary.credits, detailPageCreditCost(count));
    if (count >= 1 && count <= 4) assert.ok(summary.minimumNote.includes('5컷'));
  });
}

test('각 고치기는 분석 또는 콘티로 같은 프로젝트를 지정한다', async () => {
  const { summaryEditTarget, isSummaryAnalysisEdit } = await summaryModule();
  for (const field of ['category', 'fit', 'materials', 'features', 'model', 'matching']) {
    const target = summaryEditTarget(field, 'project-1');
    assert.equal(target.path, '/create/input');
    assert.equal(isSummaryAnalysisEdit(target.state, { projectId: 'project-1', projectPersisted: true }), true);
    assert.equal(isSummaryAnalysisEdit(target.state, { projectId: 'project-2', projectPersisted: true }), false);
  }
  for (const field of ['cuts', 'copywriting']) assert.equal(summaryEditTarget(field, 'project-1').path, '/create/storyboard');
});

test('요약은 선택 버전의 핏, 소재, 모델, 선택한 매칭과 컷 종류를 보여준다', async () => {
  const { buildGenerationSummary } = await summaryModule();
  const summary = buildGenerationSummary({
    analysis: { clothingType: 'top', customCategory: '골지 니트', fit: 'regular',
      fitProfile: { category: 'top', gender: 'women', axes: { fit: 'over' } },
      materials: [{ name: '면', ratio: 100 }], sellingPoints: ['부드러운 촉감'], selectedModelId: 'mF',
      matchClothing: [{ id: 'a', name: '팬츠', selected: true }, { id: 'b', name: '스커트', selected: false }] },
    catalogs: { clothingTypes: [{ value: 'top', label: '상의' }] },
    blocks: [{ source: 'ai', cutType: 'styling' }, { source: 'mine' }], aiCutCount: 1, copywriting: true,
  });
  assert.match(summary.category, /골지 니트/);
  assert.match(summary.fit, /오버/);
  assert.equal(summary.materials, '면 100%');
  assert.equal(summary.model, '하린');
  assert.equal(summary.matching, '팬츠');
  assert.equal(summary.cutCount, 2);
  assert.match(summary.cuts, /스타일링 1/);
  assert.match(summary.cuts, /내 사진 1/);
  assert.equal(summary.copywriting, '켜짐');
});

test('분석 수정 진입은 기존 프로젝트를 유지하고 저장 후 콘티로 돌아간다', () => {
  const app = read('../../src/apps/seller/App.jsx');
  const input = read('../../src/features/product-input/ProductInput.jsx');
  assert.match(app, /isSummaryAnalysisEdit\(locationState, useAppStore\.getState\(\)\)/);
  const gate = input.slice(input.indexOf('const goToStoryboard = async'), input.indexOf('const queueAnalysisPatch ='));
  assert.match(gate, /summaryEditing/);
  assert.match(gate, /await analysisSaveChainRef\.current/);
  assert.match(gate, /navigate\('\/create\/storyboard'\)/);
});

test('사진 그대로인 선택 버전은 과거 분석 핏을 요약에 재사용하지 않는다', async () => {
  const { buildGenerationSummary } = await summaryModule();
  const summary = buildGenerationSummary({ analysis: { clothingType: 'top', fit: 'regular', fitProfile: null } });
  assert.equal(summary.fit, '사진 그대로');
});

test('하의는 프로필 없는 이전 분석에서도 팬츠 축으로 핏을 표시한다', async () => {
  const { buildGenerationSummary } = await summaryModule();
  const summary = buildGenerationSummary({ analysis: { clothingType: 'bottom', subCategory: 'slacks', fit: 'wide' } });
  assert.equal(summary.fit, '와이드');
});

test('기장만 조정한 버전도 비어 있는 핏 축을 과거 분석값으로 채우지 않는다', async () => {
  const { buildGenerationSummary } = await summaryModule();
  const summary = buildGenerationSummary({ analysis: { clothingType: 'top', fit: 'regular',
    fitProfile: { category: 'top', gender: 'women', axes: { fit: null, length: 'crop' } } } });
  assert.equal(summary.fit, '사진 그대로');
});


test('로그인 복원 후 checking 재판정에서도 같은 프로젝트의 요약 수정을 유지한다', async () => {
  const { isSummaryAnalysisEdit } = await summaryModule();
  const source = read('../../src/apps/seller/App.jsx');
  const routeStart = source.indexOf('function ProductInputRoute()');
  const start = source.indexOf("if (entryDecision !== 'checking') return;", routeStart);
  const end = source.indexOf('if (draftSlot.getToken())', start);
  const results = [];
  const evaluateGate = new Function('entryDecision', 'isMockMode', 'authLoading', 'locationState',
    'useAppStore', 'isSummaryAnalysisEdit', 'setEntryDecision', 'draftSlot', source.slice(start, end));
  evaluateGate('checking', false, false, { summaryEditProjectId: 'project-1', summaryEditField: 'materials' },
    { getState: () => ({ projectId: 'project-1', projectPersisted: true }) }, isSummaryAnalysisEdit,
    (value) => results.push(value), { resumeEntryWrites() {} });
  assert.deepEqual(results, ['continue']);
});


test('확인 전 POST는 0건이며 두 번 확인과 라우트 중복 진입에도 생성 POST는 1건이다', async (t) => {
  const { createServer } = await import('vite');
  const root = new URL('../..', import.meta.url).pathname;
  let requests = 0;
  let finishRequest;
  const pendingRequest = new Promise((resolve) => { finishRequest = resolve; });
  const key = '__pregenSummaryApi';
  globalThis[key] = {
    startDetailPage: async () => { requests++; await pendingRequest; return { data: {}, credits: 105 }; },
  };
  const vite = await createServer({ configFile: false, root, logLevel: 'silent', appType: 'custom',
    cacheDir: '/tmp/pregen-summary-test-vite', resolve: { alias: { '@': `${root}/src` } }, server: { middlewareMode: true, watch: null, hmr: false },
    plugins: [{ name: 'pregen-network-boundary', enforce: 'pre', resolveId(id) {
      if (id.endsWith('/lib/api/index.js')) return '\0pregen-api';
      if (id.endsWith('/lib/api/httpAdapter.js')) return '\0pregen-http';
    }, load(id) {
      if (id === '\0pregen-api') return `export const api = globalThis.${key};`;
      if (id === '\0pregen-http') return 'export const resetAnalysisCache = () => {};';
    } }],
  });
  t.after(async () => { delete globalThis[key]; await vite.close(); });
  const { useAppStore } = await vite.ssrLoadModule('/src/store/useAppStore.js');
  const runs = [];
  const actions = mannequinActions({ onNavigate(path) {
    if (path !== '/create/generating') return;
    // 같은 라우트가 재마운트돼도 기존 store 중복 방지 규칙을 사용한다.
    runs.push(useAppStore.getState().startDetailPageGeneration('project-1'));
    runs.push(useAppStore.getState().startDetailPageGeneration('project-1'));
  } });
  await actions.onCta();
  assert.equal(requests, 0);
  await Promise.all([actions.continueWithSelectedVersion(null), actions.continueWithSelectedVersion(null)]);
  assert.equal(requests, 1);
  finishRequest();
  await Promise.all(runs);
  assert.equal(useAppStore.getState().detailPageJob.status, 'done');
});

test('분석 수정 복귀는 저장 완료를 기다리며 현재 프로젝트를 새로 승격하지 않는다', async () => {
  const source = read('../../src/features/product-input/ProductInput.jsx');
  const start = source.indexOf('const goToStoryboard = async (opts) =>');
  const end = source.indexOf('const queueAnalysisPatch =', start);
  let finishSave;
  const save = new Promise((resolve) => { finishSave = resolve; });
  const events = [];
  const dependencies = { mobile: false, summaryEditing: true, inputConsistency: null,
    redirectingRef: {current:false}, promotionRunRef: {current:0}, mountedRef: {current:true},
    useAppStore: {getState:()=>({projectId:'kept-project',projectGeneration:1})},
    setFlowPromotionLocked: ()=>{}, colorSaveSchedulerRef:{current:{flush(){}}},
    analysisSaveChainRef:{current:save}, failedAnalysisPatchRef:{current:null}, analysisSaveErrorRef:{current:null},
    navigate:path=>events.push([path,'kept-project']),
  };
  const go = new Function(...Object.keys(dependencies), `${source.slice(start,end)}; return goToStoryboard;`)(...Object.values(dependencies));
  const result = go();
  await Promise.resolve();
  assert.deepEqual(events, []);
  finishSave(); await result;
  assert.deepEqual(events, [['/create/storyboard','kept-project']]);
});

test('요약 핏 수정은 상의 선택 버전과 하의 컷 축을 같은 값으로 편집한다', async () => {
  const { summaryFitEditor, summaryFitPatch } = await summaryModule();
  assert.equal(typeof summaryFitEditor, 'function');
  assert.equal(typeof summaryFitPatch, 'function');
  const top = { clothingType:'top', targetGenders:['women'], fit:'regular',
    fitProfile:{category:'top',gender:'women',axes:{fit:'over',length:'crop'},version:2} };
  assert.equal(summaryFitEditor(top).value,'over');
  const pants = { clothingType:'bottom', subCategory:'slacks', targetGenders:['women'], fit:null,
    fitProfile:{category:'pants',gender:'women',axes:{cut:'wide',length:'full'},version:2} };
  assert.equal(summaryFitEditor(pants).axis,'cut');
  assert.equal(summaryFitEditor(pants).value,'wide');
  assert.ok(summaryFitEditor(pants).options.some(item=>item.value==='straight'));
  const patch=summaryFitPatch(pants,'straight');
  assert.equal(patch.fitProfile.axes.cut,'straight');
  assert.equal(patch.fitProfile.axes.length,'full');
  assert.equal(patch.fitProfile.source,'seller');
  assert.equal(buildGenerationSummaryLabel({...pants,...patch}, await summaryModule()), '일자');
});
function buildGenerationSummaryLabel(analysis, module) { return module.buildGenerationSummary({analysis}).fit; }


test('고치기는 직전 선택 버전 저장을 기다리고 그 버전의 핏을 분석으로 넘긴다', async () => {
  let finishSave;
  const saving = new Promise(resolve=>{finishSave=resolve;});
  const actions=mannequinActions({credits:0,save:()=>saving});
  assert.equal(typeof actions.editSummaryField,'function');
  const pending=actions.editSummaryField('fit');
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(actions.events,[['selection']]);
  finishSave(); await pending;
  assert.deepEqual(actions.events,[['selection'],['save'],['navigate','/create/input']]);
  assert.deepEqual(actions.targets,[{path:'/create/input',state:{summaryEditProjectId:'project-1',summaryEditField:'fit'}}]);
});

test('시각 요약은 선택 모델과 매칭 사진, 종류별 컷 수를 데이터로 제공한다', async () => {
  const { buildGenerationSummary } = await summaryModule();
  const summary = buildGenerationSummary({analysis:{selectedModelId:'mF',matchClothing:[{id:'a',name:'팬츠',thumb:'/pants.webp',selected:true}]},
    blocks:[{cutType:'styling',source:'ai'},{cutType:'product',shot:'detail',source:'ai'}],aiCutCount:2});
  assert.equal(summary.modelThumb,'/models/women/w3.webp');
  assert.equal(summary.matchingThumb,'/pants.webp');
  assert.deepEqual(summary.cutTypes.map(({label,count})=>({label,count})),[{label:'스타일링',count:1},{label:'디테일',count:1}]);
});

test('핏을 고친 뒤 마네킹에 돌아오면 선택 컷의 과거 핏 대신 저장한 새 핏으로 갱신한다', async () => {
  const { runGenerationRelevantEditsRefresh } = await import('../../src/features/mannequin/generationRunnerCore.js');
  const source=read('../../src/features/mannequin/Mannequin.jsx');
  const start=source.lastIndexOf('useEffect(() => {',source.indexOf('const refreshProjectId = projectId;'));
  const end=source.indexOf('}, [phase]);',start)+'}, [phase]);'.length;
  const saved={category:'pants',gender:'women',axes:{cut:'straight'},source:'seller',version:2};
  const profiles=[];
  const deps={phase:'ready',projectId:'project-1',refreshForEditsHandledRef:{current:false},
    initialCutsExistedRef:{current:true},analysisEditsProfileRef:{current:saved},
    readGenerationRelevantEditsRevision:()=>1,runGenerationRelevantEditsRefresh,
    isGenerationRelevantEditsTerminalFailure:()=>false,useAppStore:{getState:()=>({clearGenerationRelevantEdits(){}})},
    regenerate:async(profile,onSucceeded)=>{profiles.push(profile);onSucceeded();return true;},useEffect:fn=>fn()};
  new Function(...Object.keys(deps),source.slice(start,end))(...Object.values(deps));
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(profiles,[saved]);
});

for (const savedProfile of [null, {category:'top',gender:'women',axes:{},source:'auto',version:2}]) {
  test(`사진 그대로에서 매칭만 고쳐도 빈 핏을 과거 분석값으로 채우지 않는다: ${savedProfile === null ? 'null' : '빈 축'}`, () => {
    const source=read('../../src/features/mannequin/Mannequin.jsx');
    const start=source.indexOf('const draft = createFitProfileDraft(nextProduct, nextAnalysis, nextMainMatchingItem);');
    const end=source.indexOf('setStepState(',start);
    const ref={current:null};
    const deps={nextProduct:{clothingType:'top'},nextAnalysis:{fit:'semi_over',fitProfile:savedProfile},nextMainMatchingItem:null,
      analysisEditsProfileRef:ref,setFitProfileDraft:()=>{},createFitProfileDraft:()=>({category:'top',gender:'women',axes:{fit:'semi_over'},source:'auto',version:2})};
    new Function(...Object.keys(deps),source.slice(start,end))(...Object.values(deps));
    assert.notEqual(ref.current?.axes?.fit,'semi_over');
    if(savedProfile) assert.equal(ref.current,savedProfile);
  });
}
