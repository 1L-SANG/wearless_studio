import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

const mannequinSource = read('../../src/features/mannequin/Mannequin.jsx');
const productInputSource = read('../../src/features/product-input/ProductInput.jsx');
const storeSource = read('../../src/store/useAppStore.js');
const appSource = read('../../src/App.jsx');
const librarySource = read('../../src/features/library/Library.jsx');
const storyboardSource = read('../../src/features/storyboard/Storyboard.jsx');
const shellSource = read('../../src/features/shell/shell.jsx');

test('the regeneration signal travels in the store, not in router state', () => {
  // 입력 → 콘티 → 마네킹 사이에 화면이 하나 끼면 route state 는 증발한다.
  assert.doesNotMatch(productInputSource, /refreshForEdits/);
  assert.doesNotMatch(mannequinSource, /location\.state\?\.refreshForEdits/);
  assert.match(storeSource, /generationRelevantEditsDirty:/);
  assert.match(mannequinSource, /readGenerationRelevantEditsRevision/);
  assert.match(mannequinSource, /clearGenerationRelevantEdits\(/);
});

test('the mannequin wires edit refreshes through the guarded behavior runner', () => {
  // 실제 성공/실패·중복 실행 계약은 mannequin-generation-runner.test.mjs가 동작으로 검증한다.
  assert.match(
    mannequinSource,
    /runGenerationRelevantEditsRefresh\(\{[\s\S]*?handledRef: refreshForEditsHandledRef,[\s\S]*?regenerate: \(onSucceeded\)[\s\S]*?clearDirty:/,
  );
  assert.match(mannequinSource, /onGenerationSucceeded\(\);\s*\n\s*if \(!runIsCurrent\(runId\)\) return true/);
  assert.match(
    mannequinSource,
    /const reportSuccess = \(\) => \{[\s\S]*?clearGenerationRelevantEdits\(generationProjectId, dirtyRevision\)[\s\S]*?onGenerationSucceeded\(\)/,
  );
  assert.match(mannequinSource, /markGenerationRelevantEditsAttempt\([\s\S]*?regenerateBaselineRef\.current/);
  assert.match(mannequinSource, /const retryGeneration = \(\) => regenerate\(/);
  assert.match(mannequinSource, /if \(needsRegen\) \{ regenerate\(\); return; \}/);
  assert.match(storeSource, /clearGenerationRelevantEdits\(projectId = get\(\)\.projectId, expectedRevision\)/);
  assert.match(storeSource, /if \(cleared && get\(\)\.projectId === projectId\) set\(\{ generationRelevantEditsDirty: false \}\)/);
});

test('adoptProject preserves the dirty flag only when acquiring identity for the same in-progress work', () => {
  // store: null projectId 에서 채택할 때만, 그리고 호출자가 명시적으로 요청했을 때만 보존한다.
  assert.match(storeSource, /preserveGenerationDirty = false/);
  // sameWorkContinuation = "같은 작업이 신원을 얻는 경로" 판정 — dirty 보존과 composeMode 보존이
  // 같은 판정을 공유한다(둘 다 게스트 구간의 상태를 로그인 너머로 잇는 일이라서).
  assert.match(storeSource, /const sameWorkContinuation = preserveGenerationDirty && current\.projectId === null/);
  assert.match(storeSource, /const preserveDirty = sameWorkContinuation && current\.generationRelevantEditsDirty/);
  assert.match(storeSource, /adoptGenerationRelevantEdits\(projectId, \{ preserveDirty \}\)/);
  // 게스트 편집 → 로그인 → draft sync 로 처음 project 를 얻는 두 경로(입력 화면, 로그인 복귀)는
  // 같은 작업의 연속이라 보존을 요청해야 한다.
  assert.match(productInputSource, /adoptProject\(projectId, \{ preserveGenerationDirty: true \}\)/);
  assert.match(appSource, /adoptProject\(projectId, \{ preserveGenerationDirty: true \}\)/);
  // 보관함에서 다른 project 를 여는 경로는 실제 '다른 작업' 전환이라 옵션 없이(기본값 false로)
  // 계속 초기화해야 한다 — 그렇지 않으면 무관한 project 로 신호가 샌다.
  assert.match(librarySource, /adoptProject\(it\.id\)/);
  assert.doesNotMatch(librarySource, /preserveGenerationDirty/);
});

test('starting an isolated input flow does not delete another project\'s scoped dirty marker', () => {
  const beginProjectSource = storeSource.slice(
    storeSource.indexOf('async beginProject()'),
    storeSource.indexOf('async ensureProject()'),
  );
  assert.match(beginProjectSource, /generationRelevantEditsDirty: false/);
  assert.doesNotMatch(beginProjectSource, /clearGenerationRelevantEditsSession/);
});

test('the input CTA now opens the storyboard', () => {
  assert.match(productInputSource, /const goToStoryboard = async \(opts\) =>/);
  assert.doesNotMatch(productInputSource, /navigate\('\/create\/mannequin'/);
  assert.match(productInputSource, /openLogin\('\/create\/storyboard'\)/);
});

test('the input CTA proceeds without a generation-start acknowledgement modal', () => {
  const gate = productInputSource.slice(
    productInputSource.indexOf('const goToStoryboard = async (opts) =>'),
    productInputSource.indexOf('const queueAnalysisPatch ='),
  );
  assert.doesNotMatch(productInputSource, /generationStartAck|generationStartOpen|ackGenerationStart/);
  assert.doesNotMatch(productInputSource, /마네킹컷을 만들기 시작해요/);
  assert.match(gate, /if \(!guardMannequinCredits\(\)\) return;/);
  assert.match(gate, /inputConsistency && !consistencyAck && !force/);
  assert.match(gate, /promoteDraftToProject\(draft\)[\s\S]*?confirmProductInfo\(projectId\)/);
  assert.match(gate, /showMannequinTransition: true/);
});

test('all color mutations share the debounced existing analysis-save queue', () => {
  const handlers = productInputSource.slice(
    productInputSource.indexOf('const editColors ='),
    productInputSource.indexOf('// 필수 판정은 기준 색상 기준'),
  );
  assert.match(handlers, /mergeColorMetadataWithPersistedImages\(/);
  assert.match(handlers, /invalidateStoryboardEntryPrefetch\(analysisProjectId\)/);
  assert.match(handlers, /colorSaveSchedulerRef\.current\.schedule\(\{ colors: persistedColors \}\)/);
  for (const name of ['renameColor', 'setColor', 'addColor', 'removeColor']) {
    assert.match(handlers, new RegExp(`const ${name} = [^;]*editColors`));
  }
  assert.match(productInputSource, /colorSaveSchedulerRef\.current\.flush\(\);\s*\n\s*\/\/ 직전 입력 이벤트/);
  assert.match(productInputSource, /queueAnalysisPatch[\s\S]*?persistAnalysisEdit\(api, analysisProjectId, patch\)/);
  assert.match(productInputSource, /persistedColorsRef\.current = p\.colors \|\| \[\]/);
  assert.match(productInputSource, /persistedColorsRef\.current = savedProduct\?\.colors \|\| patch\.colors/);
  assert.match(productInputSource, /registerAnalysisEditSave\(analysisProjectId, analysisSaveChainRef\.current\)/);
  const storyboardLoad = storyboardSource.slice(
    storyboardSource.indexOf('await useAppStore.getState().loadProject()'),
    storyboardSource.indexOf('await sbSaveIdle()'),
  );
  assert.match(storyboardLoad, /await waitForAnalysisEditSave\(pid\)/);
  assert.ok(
    storyboardLoad.indexOf('await waitForAnalysisEditSave(pid)')
      < storyboardLoad.indexOf('requestMannequinGeneration(pid)'),
  );
});

test('login return lands on the storyboard', () => {
  assert.match(appSource, /const wantsStoryboard = target === '\/create\/storyboard'/);
  assert.match(appSource, /setDest\('\/create\/storyboard'\)/);
});

test('the storyboard hands off to the mannequin without reopening confirmed input', () => {
  assert.match(storyboardSource, /const goToMannequin = async \(\) => \{/);
  // 저장 실패는 조용히 삼켜지지 않는다 — 공용 handoff helper가 서버 메시지를 toast로 보여주고
  // navigate 를 건너뛴다
  // (2026-08 QA: 콘티 재배치로 저장 실패가 실제로 도달 가능해져, '다음'이 아무 반응 없이
  // 죽는 문제가 생겼다). 성공 시에만 saveNow 뒤에 마네킹으로 이동한다.
  assert.match(
    storyboardSource,
    /await continueAfterStoryboardFlush\(\{[\s\S]*?flush: \(\) => saveNow\(projectId\),[\s\S]*?navigate: \(\) => navigate\('\/create\/mannequin'\),[\s\S]*?onFailure: \(message\) => toast\.push\(message\)/,
  );
  assert.doesNotMatch(storyboardSource, /이전<\/button>/);
  assert.doesNotMatch(storyboardSource, /navigate\('\/create\/input'\)/);
  assert.doesNotMatch(storyboardSource, /navigate\('\/create\/generating'\)/);
});

test('the mannequin is the last stop before generation', () => {
  assert.match(mannequinSource, /navigate\('\/create\/generating'\)/);
  assert.doesNotMatch(mannequinSource, /navigate\('\/create\/storyboard'\)/);
});

test('a running job no longer yanks the user onto the mannequin screen', () => {
  assert.doesNotMatch(shellSource, /mannequinJob\?\.status === 'running'/);
  assert.match(shellSource, /resumePath \|\| '\/create\/storyboard'/);
});

test('the library "새로 만들기" is no longer hijacked by another project\'s running job', () => {
  // Task 6 wires the storyboard to fire generation on entry — from that point a job can
  // be running while the user is still on /create/storyboard, and this hijack would have
  // bounced a fresh "새로 만들기" click straight into /create/mannequin, past goToMannequin's
  // own validation gate.
  assert.doesNotMatch(librarySource, /mannequinJob/);
  assert.doesNotMatch(librarySource, /navigate\('\/create\/mannequin'\)/);
  assert.match(librarySource, /const onNew = \(\) => navigate\('\/create\/input'\)/);
  assert.doesNotMatch(librarySource, /beginProject/);
});

test('the mannequin CTA cannot mistake a failed storyboard fetch for zero AI cuts', () => {
  // getStoryboard 실패를 [] 로 뭉개면 "AI 컷 0장"과 "조회 실패"가 구분되지 않아, 크레딧
  // 소비 직전 CTA 가 '0 크레딧'(=무료로 읽힘)을 보여줄 수 있다. null 로 남겨 구분한다.
  assert.doesNotMatch(mannequinSource, /getStoryboard\(pid\)\.catch\(\(\) => \[\]\)/);
  assert.match(mannequinSource, /getStoryboard\(pid\)\.catch\(\(\) => null\)/);
  assert.match(
    mannequinSource,
    /setAiCutCount\(Array\.isArray\(nextStoryboard\) \? nextStoryboard\.filter\(\(b\) => b\.source !== 'mine'\)\.length : null\)/,
  );
  // 조회 실패를 0원으로 표시하지 않으면서, 정상 조회된 컷 수에는 단가를 곱해 CTA에 보여준다.
  assert.match(
    mannequinSource,
    /aiCutCount == null \? '—' : aiCutCount \* CREDIT_COSTS\.storyboardPerCut/,
  );
  assert.match(
    mannequinSource,
    /detailPageGenerationCreditShortfall\(\s*useAppStore\.getState\(\)\.account,\s*aiCutCount,\s*\)/,
  );
});

test('the storyboard fires mannequin generation as it loads', () => {
  assert.match(storyboardSource, /import \{ requestMannequinGeneration \} from '@\/features\/mannequin\/generationRunner\.js'/);
  // 발사는 보드 로드를 막지 않는다 — await 하면 병렬화가 사라진다.
  assert.match(storyboardSource, /void requestMannequinGeneration\(pid\)\.catch\(\(\) => \{\}\)/);
  assert.doesNotMatch(storyboardSource, /await requestMannequinGeneration/);
});

const chromeSource = read('../../src/features/shell/ChromeLayout.jsx');
const mannequinRibbonStart = chromeSource.indexOf('function MannequinJobRibbon()');
const detailPageRibbonStart = chromeSource.indexOf('function DetailPageJobRibbon()');
const chromeLayoutStart = chromeSource.indexOf('export function ChromeLayout()');
assert.ok(
  mannequinRibbonStart >= 0
    && detailPageRibbonStart > mannequinRibbonStart
    && chromeLayoutStart > detailPageRibbonStart,
);
const mannequinRibbonSource = chromeSource.slice(mannequinRibbonStart, detailPageRibbonStart);
const detailPageRibbonSource = chromeSource.slice(detailPageRibbonStart, chromeLayoutStart);

test('the transition overlay replaces the duplicate completion badge and the ribbon stops steering', () => {
  assert.doesNotMatch(mannequinRibbonSource, /마네킹컷 준비 완료/);
  assert.doesNotMatch(chromeSource, /DONE_BADGE_MS/);
  assert.match(chromeSource, /의류 구현 진행중/);
  assert.match(chromeSource, /setTimeout\(\(\) => setVisible\(false\), 4725\)/);
  assert.doesNotMatch(mannequinRibbonSource, /마네킹 화면 보기/);
  assert.doesNotMatch(mannequinRibbonSource, /job-ribbon-btn/);
  assert.match(detailPageRibbonSource, /job-ribbon-btn/);
  assert.match(detailPageRibbonSource, /생성 화면 보기/);
});

test('the removed completion badge leaves no stale running-state tracker behind', () => {
  assert.doesNotMatch(chromeSource, /wasRunningRef/);
  assert.doesNotMatch(chromeSource, /runningProjectIdRef/);
  // idle 이면 리본이 남지 않아야 한다는 게 요지. 진행바 작업에서 표시 조건을 visible 로
  // 모으면서 문장 형태만 바뀌었고(rAF 루프를 숨김 상태에서 멈추려고), 불변식은 그대로다.
  assert.match(mannequinRibbonSource, /job\.status !== 'idle'/);
  assert.match(mannequinRibbonSource, /if \(!visible\) return null/);
});
