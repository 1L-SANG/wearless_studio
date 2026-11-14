import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { transformWithEsbuild } from 'vite';
import { applyPromotedMatchSelection } from '../../src/lib/customMatchPromotion.js';
import { continueStoryboardToDetailPage, loadStoryboardCreditQuote } from '../../src/features/storyboard/storyboardDetailPage.js';

const offCreationReady = { doneBlocked: false, hasEditorEntered: () => false, atomicSavingRef: { current: false },
  setDetailPageSubmitting() {}, countGenerationCuts: () => assert.fail('no saved board to count'),
  creditQuotePending: false, customMatchPromotionPending: false, customMatchPreparationFailed: false,
  setCustomMatchConfirmationOpen() {} };

const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
async function evaluate(source, bindings, result) {
  const { code } = await transformWithEsbuild(source.replace(/^export /gm, ''), 'stage-harness.jsx', { jsx: 'transform' });
  return new Function(...Object.keys(bindings), `${code}; return ${result};`)(...Object.values(bindings));
}

for (const enabled of [true, false]) {
  test(`분석 확정이 견적을 넘기는지는 스위치를 따른다: ${enabled}`, async () => {
    const source = read('src/features/analysis/AnalysisForm.jsx');
    const start = source.indexOf('  const confirmAnalysis =');
    const end = source.indexOf('\n  };', start) + 5;
    const calls = [];
    const confirm = await evaluate(source.slice(start, end), {
      isMannequinStageEnabled: () => enabled, confirming: false, onBeforeConfirm: null,
      setConfirming() {}, onConfirmingChange() {}, commitMdComment: async () => {}, persistSelectedPageStyle: async () => {},
      mannequinGenerationCost: 64, onNext: async (...args) => calls.push(args), setPageStyleError: message => assert.fail(message),
    }, 'confirmAnalysis');
    await confirm();
    assert.deepEqual(calls, enabled ? [[{ expectedQuote: { free: false, credits: 64 } }]] : [[]]);
  });

  test(`분석 확정은 끔에서 금액 강조 라벨을 쓰지 않는다: ${enabled}`, async () => {
    const source = read('src/features/analysis/AnalysisForm.jsx');
    const start = source.indexOf('  const cta = (');
    const end = source.indexOf('\n  const outfit =', start);
    const tree = await evaluate(source.slice(start, end), {
      React, Button: 'button', OutfitCreditLabel: 'CreditLabel', mannequinGenerationCost: 64,
      mannequinGenerationCtaLabel: cost => `의류정보 확정 완료 · ${cost} 크레딧`,
      isMannequinStageEnabled: () => enabled, pageStyleReady: true, confirming: false, confirmAnalysis() {},
    }, 'cta');
    const label = tree.props.children.props.children;
    if (enabled) assert.equal(label.type, 'CreditLabel');
    else assert.equal(label, '의류정보 확정 완료');
  });

  test(`상품 입력의 충전 재개와 마네킹 경고는 끔에서 사라진다: ${enabled}`, async () => {
    const source = read('src/features/product-input/ProductInput.jsx');
    const resumeStart = source.indexOf('  const [creditResume, setCreditResume]');
    const resumeEnd = source.indexOf('\n  ));', resumeStart) + 6;
    const creditResume = await evaluate(source.slice(resumeStart, resumeEnd), {
      useState: init => [init(), () => {}], isMannequinStageEnabled: () => enabled,
      location: { state: { creditResume: { action: 'storyboard', requiredCredits: 64 } } },
    }, 'creditResume');
    assert.equal(Boolean(creditResume), enabled);
    const warningStart = source.indexOf('  const generationWorkKind =');
    const warningEnd = source.indexOf('\n  useEffect', warningStart);
    const kind = await evaluate(source.slice(warningStart, warningEnd), {
      isMannequinStageEnabled: () => enabled, generationWorkWarningKind: () => 'cuts',
      hasExistingGenerationWorkRef: { current: true }, mannequinJobStatus: 'done', mannequinJobProjectId: 'p', analysisProjectId: 'p',
    }, 'generationWorkKind');
    assert.equal(kind, enabled ? 'cuts' : 'none');
  });

  test(`보관함은 선택한 마네킹이 있어도 끔에서 콘티로 잇는다: ${enabled}`, async () => {
    const source = read('src/features/library/Library.jsx');
    const code = source.slice(source.indexOf('const STAGES'), source.indexOf('export function Library'));
    const helpers = await evaluate(code, { isMannequinStageEnabled: () => enabled }, '({ stageOf, resumePath, STAGES })');
    assert.equal(helpers.STAGES[helpers.stageOf('mannequin')], enabled ? '마네킹' : '콘티');
    assert.equal(helpers.resumePath({ id: 'p', status: 'draft', selectedMannequinId: 'cut' }), enabled ? '/create/mannequin' : '/create/storyboard');
    for (const status of ['running', 'error', 'done']) assert.equal(helpers.resumePath({ id: 'p', latestJobs: { detailPage: { status } } }), '/editor/p');
  });

  test(`요금제는 끔에서 수정 혜택을 빼고 209 기준으로 센다: ${enabled}`, async () => {
    const source = read('src/features/pricing/Pricing.jsx');
    const code = source.slice(source.indexOf('// 요금 정본'), source.indexOf('export function Pricing()'));
    const pricing = await evaluate(code, {}, `pricingForMannequinStage(${enabled})`);
    assert.deepEqual([600, 1600, 2800].map(credits => Math.floor(credits / pricing.standardPageCredits)), enabled ? [2, 6, 11] : [2, 7, 13]);
    assert.equal(pricing.details.starter.features.includes('마네킹컷 1회 무료 수정 가능'), enabled);
    assert.equal(pricing.details.pro.features.includes('마네킹컷 2회 무료 수정 가능'), enabled);
    assert.ok(pricing.details.starter.features.includes('에디터 기능 제공'));
  });

  test(`마네킹 주소는 끔에서 상태를 보존해 콘티로 보낸다: ${enabled}`, async () => {
    const source = read('src/apps/seller/App.jsx');
    const start = source.indexOf('function MannequinStageRoute()');
    assert.ok(start >= 0);
    const end = source.indexOf('\n}', start) + 2;
    const state = { creditResume: { action: 'detail-page', requiredCredits: 114 } };
    const route = await evaluate(source.slice(start, end), {
      React, useLocation: () => ({ state }), isMannequinStageEnabled: () => enabled, Mannequin: 'Mannequin', Navigate: 'Navigate',
    }, 'MannequinStageRoute()');
    assert.equal(route.type, enabled ? 'Mannequin' : 'Navigate');
    if (!enabled) { assert.equal(route.props.to, '/create/storyboard'); assert.equal(route.props.state, state); assert.equal(route.props.replace, true); }
  });

  test(`상단 마네킹 알림과 띠와 도착 안내는 끔에서 붙지 않는다: ${enabled}`, async () => {
    const source = read('src/features/shell/ChromeLayout.jsx');
    const start = source.indexOf('      <TopNav />');
    const end = source.indexOf('      {/* 콘티는 데이터 준비', start);
    const tree = await evaluate(`const tree = <>${source.slice(start, end)}</>;`, {
      React, TopNav: 'TopNav', MannequinCompletionToast: 'Completion', StoryboardTransitionOverlay: 'Arrival',
      MannequinJobRibbon: 'MannequinRibbon', DetailPageJobRibbon: 'DetailRibbon',
      pathname: '/create/storyboard', transitionKey: 'arrive', isMannequinStageEnabled: () => enabled,
    }, 'tree');
    const types = node => React.Children.toArray(node?.props?.children).flatMap(child => typeof child === 'object' ? [child.type, ...types(child)] : []);
    const mounted = types(tree);
    for (const type of ['Completion', 'Arrival', 'MannequinRibbon']) assert.equal(mounted.includes(type), enabled, type);
    assert.ok(mounted.includes('DetailRibbon'));
  });

  test(`크레딧 내역 빈 설명은 끔에서 상세페이지 생성만 말한다: ${enabled}`, async () => {
    const source = read('src/features/credits/CreditsHistory.jsx').replace(/^import .*;\n/gm, '');
    const history = await evaluate(source.replace(/export default CreditsHistory;?/, ''), {
      React, useMemo: fn => fn(), useQuery: options => ({ data: [], isLoading: false, isError: false }),
      useAppStore: selector => selector({ account: { credits: 0 } }), s: {}, api: {}, seoulDateTime() {},
      isMannequinStageEnabled: () => enabled, EmptyState: 'Empty', Skeleton: 'Skeleton', ErrorState: 'Error', Icon: 'Icon',
    }, 'CreditsHistory()');
    function find(node) { if (!node || typeof node !== 'object') return null; if (node.type === 'Empty') return node; return React.Children.toArray(node.props?.children).map(find).find(Boolean); }
    assert.equal(find(history).props.desc, enabled ? '마네킹·상세페이지를 만들면 크레딧 사용 내역이 여기에 쌓여요.' : '상세페이지를 만들면 크레딧 사용 내역이 여기에 쌓여요.');
  });
}

test('마네킹을 건너뛰면 저장되어 돌아온 컷 수로 잔액을 보고 화면에서는 옷 색을 재지 않는다', async () => {
  const source = read('src/features/storyboard/Storyboard.jsx');
  const start = source.indexOf('  const goToMannequin =');
  const code = source.slice(start, source.indexOf('\n  const runs =', start));
  for (const [savedCount, credits, failure, outcome] of [
    [6, 95, false, ['shortfall', 114]], [5, 95, false, '/create/generating'],
    [6, 114, false, '/create/generating'], [5, 114, true, 'failure'],
  ]) {
    const cuts = n => Array.from({ length: n }, (_, id) => ({ id, source: 'ai', contentRole: 'benefit', cutType: 'styling' }));
    const saved = cuts(savedCount);
    const calls = [];
    const go = await evaluate(code, {
      ...offCreationReady, detailPageEntryLocked: { current: false }, mannequinStageEnabled: false, blocks: cuts(5),
      productSectionNeedingSelection: () => null, unsupportedAiBlock: null, continueStoryboardToDetailPage,
      detailPageSubmitRef: { current: false },
      styleSaves: { flush: async () => calls.push('style') }, styleValueRef: { current: {} },
      ensureV4StoryboardCopywriting: async () => calls.push('copy'), projectId: 'p',
      useAppStore: { getState: () => ({ projectId: 'p', account: { credits } }) },
      saveNow: async () => { calls.push('board'); if (failure) throw Error('offline'); return saved; },
      latestBlocks: { current: cuts(7) },
      countGenerationCuts: snapshot => { assert.equal(snapshot, saved); return snapshot.length; },
      needsGarmentColorMeasurement: () => true, api: { measureGarmentColors: async () => calls.push('measure') },
      navigate: path => calls.push(path), toast: { push: () => calls.push('failure') },
      setCreditShortfall: shortfall => calls.push(['shortfall', shortfall.requiredCredits]), generationCutCount: 5,
      detailPageLifetimeRef: { current: {} }, detailPageQuote: null, catalogs: { analysis: { selectedModelId: 'mA' } },
    }, 'goToMannequin');
    await go();
    assert.deepEqual(calls, ['style', 'copy', 'board', outcome]);
  }
});

test('저장 중에는 하단 되돌리기와 생성 버튼을 사용할 수 없다', async () => {
  const source = read('src/features/storyboard/StoryboardContinue.jsx').replace(/^import .*;\n/gm, '');
  const { StoryboardContinue } = await evaluate(source, { React, styles: {} }, '({ StoryboardContinue })');
  const children = React.Children.toArray(StoryboardContinue({ submitting: true, undoEntry: {}, count: 5, credits: 95 }).props.children);
  const undo = React.Children.toArray(children[0].props.children).find(node => node.type === 'button');
  assert.equal(undo.props.disabled, true);
  assert.equal(children.at(-1).props.disabled, true);
});

for (const [enabled, saved, done, expected] of [
  [true, false, false, [['mannequin', 'p']]],
  [false, false, false, ['quote', 'save']],
  [false, true, false, ['quote']],
  [false, true, true, []],
]) test(`콘티 진입에서 사진 정착과 서버 저장 상태를 확인한다: ${enabled}, ${saved}, ${done}`, async () => {
  const source = read('src/features/storyboard/Storyboard.jsx');
  const start = source.indexOf('  useEffect(() => {\n    // 사진 업로드 정착');
  const end = source.indexOf('  /* 보드 썸네일 선캐싱', start);
  assert.ok(start >= 0 && end > start);
  const code = source.slice(start, end).replace('    (async () => {', '    taskState.promise = (async () => {');
  let finishPhotos;
  const photosReady = new Promise(resolve => { finishPhotos = resolve; });
  const calls = [], taskState = {}, board = [{ id: 'first', taxonomyVersion: 4 }];
  const prepared = { blocks: [...board], normalized: false, assignment: { changed: false, assignedIds: [], protectedIds: [] },
    catalogs: {}, matchClothing: [], storyboardSeedContext: { colors: [] }, colorOpts: [], detailColorOpts: [] };
  const bindings = { ...offCreationReady, detailPageEntryLocked: { current: false }, taskState, mannequinStageEnabled: enabled, loadRetry: 0, useEffect: effect => effect(),
    pidRef: { current: 'p' }, useAppStore: { getState: () => ({ projectId: 'p', loadProject: async () => {} }) },
    setLoadError: error => { if (error) assert.fail(error.message); }, navigate: () => assert.fail('unexpected route'),
    waitForAnalysisEditSave: async () => {},
    resumeProductPhotoPromotionForStoryboard: async () => { await photosReady; calls.push('photos'); return { promotionObserved: false, recoveryAttempted: false }; },
    getProductPhotoPromotionTask: () => null, getCustomMatchPromotionTask: () => null, sbSaveIdle: async () => {},
    consumeStoryboardEntry: async () => { calls.push('board'); return [board]; }, loadStoryboardEntry: async () => assert.fail('entry already loaded'),
    api: { storyboardEntryNotice: () => null, getCreditQuote: async () => { calls.push('quote'); return { detailPageGenerate: { extensionModelFee: 19 } }; } },
    loadStoryboardCreditQuote, storyboardEntryState: () => ({ saved, done }), setCreditQuote() {}, setEntryNotice() {}, isAnalysisFastEntryEnabled: () => false,
    requestMannequinGeneration: async (...args) => calls.push(['mannequin', ...args]), setMannequinFailure: error => assert.fail(error),
    sbPending: new Map(), sbLastSaved: new Map(), sbStable: JSON.stringify, toast: { push: message => assert.fail(message) },
    shouldReuseInitialStoryboardEntry: () => false, initialEntryRef: { current: null }, prepareStoryboardEntry: () => prepared,
    promotedMatchClothingRef: { current: { projectId: null } },
    lateDetailCatalogRef: { current: { merge: (_pid, catalogs) => catalogs } },
    sbSaveNow: async (id, snapshot) => { assert.equal(id, 'p'); assert.equal(snapshot(), prepared.blocks); calls.push('save'); },
  };
  for (const name of ['setBlocks', 'setCatalogs', 'setMatchClothing', 'setClothingType', 'setExampleGender', 'setIdentityKind', 'setHasDetailImage', 'setDetailColorOpts', 'setColorOpts', 'setStoryboardSeedContext']) bindings[name] = () => {};
  await evaluate(code, bindings, 'undefined');
  await Promise.resolve();
  assert.deepEqual(calls, [], '사진 준비 중에는 보드 읽기와 생성 및 저장이 없어야 한다');
  finishPhotos();
  await taskState.promise;
  assert.deepEqual(calls, ['photos', 'board', ...expected]);
});

test('콘티 저장 중 프로젝트와 계정과 화면 수명이 바뀌면 새 작업을 시작하지 않는다', async () => {
  const source = read('src/features/storyboard/Storyboard.jsx');
  const code = source.slice(source.indexOf('  const goToMannequin ='), source.indexOf('\n  const runs =', source.indexOf('  const goToMannequin =')));
  for (const boundary of ['project', 'generation', 'user', 'unmount']) {
    const state = { projectId: 'p', projectGeneration: 1, userId: 'seller', account: { credits: 0 } };
    const lifetime = { current: {} }, calls = [];
    let release;
    const saved = new Promise(resolve => { release = resolve; });
    const go = await evaluate(code, {
      ...offCreationReady, detailPageEntryLocked: { current: false }, mannequinStageEnabled: false, blocks: [{ source: 'mine' }], productSectionNeedingSelection: () => null,
      unsupportedAiBlock: null, continueStoryboardToDetailPage, detailPageSubmitRef: { current: false }, detailPageLifetimeRef: lifetime,
      styleSaves: { flush: async () => {} }, styleValueRef: { current: {} }, projectId: 'p',
      useAppStore: { getState: () => state }, ensureV4StoryboardCopywriting: async () => {}, saveNow: () => saved,
      latestBlocks: { current: [] }, needsGarmentColorMeasurement: () => true,
      api: { measureGarmentColors: async () => calls.push('measure') },
      navigate: path => calls.push(path), toast: { push: message => calls.push(message) },
      setCreditShortfall: () => calls.push('shortfall'), generationCutCount: 5, detailPageQuote: null,
      catalogs: { analysis: { selectedModelId: 'mF' } },
    }, 'goToMannequin');
    const pending = go();
    await new Promise(setImmediate);
    if (boundary === 'project') state.projectId = 'other';
    if (boundary === 'generation') state.projectGeneration++;
    if (boundary === 'user') state.userId = 'other';
    if (boundary === 'unmount') lifetime.current = null;
    release();
    await pending;
    assert.deepEqual(calls, [], boundary);
  }
});


test('콘티에서 견적이나 내 옷 반영을 기다리는 동안 생성 요청을 시작하지 않는다', async () => {
  const source = read('src/features/storyboard/Storyboard.jsx');
  const start = source.indexOf('  const goToMannequin =');
  const code = source.slice(start, source.indexOf('\n  const runs =', start));
  for (const gate of ['creditQuotePending', 'customMatchPromotionPending', 'doneBlocked', 'atomicSavingRef']) {
    let starts = 0;
    const go = await evaluate(code, {
      ...offCreationReady, detailPageEntryLocked: { current: false }, [gate]: gate === 'atomicSavingRef' ? { current: true } : true,
      mannequinStageEnabled: false, blocks: [{ source: 'mine' }], productSectionNeedingSelection: () => null,
      unsupportedAiBlock: null, projectId: 'p', detailPageLifetimeRef: { current: {} }, detailPageSubmitRef: { current: false },
      useAppStore: { getState: () => ({ projectId: 'p' }) }, latestBlocks: { current: [] },
      continueStoryboardToDetailPage: async () => { starts++; }, setCreditShortfall() {}, navigate() {},
      generationCutCount: 5, detailPageQuote: null, catalogs: { analysis: {} },
    }, 'goToMannequin');
    await go();
    assert.equal(starts, 0, gate);
  }
});

test('내 옷 반영에 실패한 콘티는 셀러가 그대로 만들기를 고른 뒤에만 생성한다', async () => {
  const source = read('src/features/storyboard/Storyboard.jsx');
  const start = source.indexOf('  const goToMannequin =');
  const code = source.slice(start, source.indexOf('\n  const runs =', start));
  const actions = [];
  const go = await evaluate(code, {
    ...offCreationReady, customMatchPreparationFailed: true, detailPageEntryLocked: { current: false },
    setCustomMatchConfirmationOpen: value => actions.push(['confirm', value]), mannequinStageEnabled: false,
    blocks: [{ source: 'mine' }], productSectionNeedingSelection: () => null, unsupportedAiBlock: null, projectId: 'p',
    detailPageLifetimeRef: { current: {} }, detailPageSubmitRef: { current: false },
    useAppStore: { getState: () => ({ projectId: 'p' }) }, latestBlocks: { current: [] },
    continueStoryboardToDetailPage: async () => actions.push('start'), setCreditShortfall() {}, navigate() {},
    generationCutCount: 5, detailPageQuote: null, catalogs: { analysis: {} },
  }, 'goToMannequin');
  await go();
  assert.deepEqual(actions, [['confirm', true]]);
  await go({ allowMissingCustomMatch: true });
  assert.deepEqual(actions, [['confirm', true], 'start']);
});

test('콘티의 충전 및 편집 잠금 안내는 하단 생성 버튼을 숨긴다', async () => {
  const source = read('src/features/storyboard/Storyboard.jsx');
  const start = source.indexOf('<StoryboardContinue hidden');
  const code = source.slice(start, source.indexOf('/>', start) + 2);
  for (const condition of ['doneBlocked', 'creditShortfall', 'creditResume', 'customMatchConfirmationOpen']) {
    const node = await evaluate(`const footer = (${code});`, {
      React, StoryboardContinue: 'Footer', drawer: null, photoBlocks: [{}], mannequinStageEnabled: false,
      detailPageSubmitting: false, atomicSaving: false,
      doneBlocked: false, creditShortfall: null, creditResume: null, customMatchConfirmationOpen: false,
      detailPageEntryLocked: { current: false }, creditQuotePending: false, customMatchPromotionPending: false,
      [condition]: true, storyboardContinueLabel: () => '', detailPageCreditCost: () => 0,
      storyboardDetailPageCredits: () => 0, generationCutCount: 0, detailPageQuote: null, account: null,
      catalogs: { analysis: {} }, undoEntry: null, undoExiting: false, undoLatest() {}, enterUndo() {}, leaveUndo() {},
      goToMannequin() {}, unsupportedAiBlock: null, blocks: [{ source: 'mine' }],
    }, 'footer');
    assert.equal(node.props.hidden, true, condition);
  }
});


test('끔 상태 내 옷 준비 안내는 등록 후 새 분석을 읽고 콘티에 반영할 때까지 유지된다', async () => {
  const source = read('src/features/storyboard/Storyboard.jsx');
  const start = source.indexOf('  useEffect(() => {', source.indexOf('// 입력 화면이 시작한 완료 프라미스'));
  const code = source.slice(start, source.indexOf('  // 승격 실패 안내', start));
  let notify, finishPromotion, finishRefresh;
  const calls = [];
  const registration = new Promise(resolve => { finishPromotion = resolve; });
  const analysis = new Promise(resolve => { finishRefresh = resolve; });
  const colors = [{ id: 'base', isBase: true }];
  let blocks = [{ id: 'card', colorId: 'base', source: 'ai', cutType: 'styling', matchIds: [] }];
  await evaluate(code, {
    projectId: 'p', pushToast() {}, mannequinStageEnabled: false, useEffect: effect => effect(),
    subscribeCustomMatchPromotion(_projectId, callback) { notify = callback; },
    setCustomMatchPromotionPending: pending => calls.push(['pending', pending]),
    setCustomMatchPreparationFailed: failed => calls.push(['failed', failed]),
    api: { getAnalysis: () => analysis, getMatchClothing: () => assert.fail('cached selection') },
    promotedMatchClothingRef: { current: null }, storyboardSeedContextRef: { current: { colors } },
    applyPromotedMatchSelection, setMatchClothing() {}, setStoryboardSeedContext() {},
    setBlocks(update) { blocks = update(blocks); calls.push(['setBlocks', blocks[0].matchIds]); },
  }, 'undefined');
  const task = { status: 'pending', promise: registration };
  notify(task);
  task.status = 'settled';
  notify(task);
  finishPromotion({ attempted: true, promoted: true });
  await new Promise(setImmediate);
  assert.deepEqual(calls, [['failed', false], ['pending', true], ['failed', false]]);
  finishRefresh({ matchClothing: [{ id: 'own', isCustom: true, selected: true }] });
  await new Promise(setImmediate);
  assert.deepEqual(calls, [['failed', false], ['pending', true], ['failed', false], ['setBlocks', ['own']], ['pending', false]]);
});
