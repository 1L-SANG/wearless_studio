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
  assert.match(mannequinSource, /generationRelevantEditsDirty/);
  assert.match(mannequinSource, /clearGenerationRelevantEdits\(\)/);
});

test('the flag is cleared before regenerate() fires, so a re-run cannot double-bill', () => {
  // clearGenerationRelevantEdits() 가 regenerate() 호출보다 텍스트상 먼저 나와야 한다 —
  // 순서가 뒤집히면 StrictMode 이중 effect·재진입 시 유료 요청이 두 번 나갈 수 있다.
  assert.match(
    mannequinSource,
    /useAppStore\.getState\(\)\.clearGenerationRelevantEdits\(\);[\s\S]*?if \(initialCutsExistedRef\.current\) \{\s*regenerate\(\);/,
  );
});

test('adoptProject preserves the dirty flag only when acquiring identity for the same in-progress work', () => {
  // store: null projectId 에서 채택할 때만, 그리고 호출자가 명시적으로 요청했을 때만 보존한다.
  assert.match(storeSource, /preserveGenerationDirty = false/);
  assert.match(
    storeSource,
    /generationRelevantEditsDirty: preserveGenerationDirty && s\.projectId === null\s*\n\s*\? s\.generationRelevantEditsDirty\s*\n\s*: false,/,
  );
  // 게스트 편집 → 로그인 → draft sync 로 처음 project 를 얻는 두 경로(입력 화면, 로그인 복귀)는
  // 같은 작업의 연속이라 보존을 요청해야 한다.
  assert.match(productInputSource, /adoptProject\(projectId, \{ preserveGenerationDirty: true \}\)/);
  assert.match(appSource, /adoptProject\(projectId, \{ preserveGenerationDirty: true \}\)/);
  // 보관함에서 다른 project 를 여는 경로는 실제 '다른 작업' 전환이라 옵션 없이(기본값 false로)
  // 계속 초기화해야 한다 — 그렇지 않으면 무관한 project 로 신호가 샌다.
  assert.match(librarySource, /adoptProject\(it\.id\)/);
  assert.doesNotMatch(librarySource, /preserveGenerationDirty/);
});

test('the input CTA now opens the storyboard', () => {
  assert.match(productInputSource, /const goToStoryboard = async \(opts\) =>/);
  assert.doesNotMatch(productInputSource, /navigate\('\/create\/mannequin'/);
  assert.match(productInputSource, /openLogin\('\/create\/storyboard'\)/);
});

test('login return lands on the storyboard', () => {
  assert.match(appSource, /const wantsStoryboard = target === '\/create\/storyboard'/);
  assert.match(appSource, /setDest\('\/create\/storyboard'\)/);
});

test('the storyboard hands off to the mannequin, and back to input', () => {
  assert.match(storyboardSource, /const goToMannequin = async \(\) => \{/);
  assert.match(storyboardSource, /await saveNow\(projectId\);\s*\n\s*navigate\('\/create\/mannequin'\)/);
  assert.match(storyboardSource, /이전<\/button>/);
  assert.match(storyboardSource, /navigate\('\/create\/input'\)/);
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
  assert.match(librarySource, /const onNew = async \(\) => \{/);
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
  // 미확정일 땐 기존 관용구(em dash)로 표시 — 계산된 숫자(특히 0)를 보여주지 않는다.
  assert.match(mannequinSource, /aiCutCount == null \? '—' : aiCutCount \* /);
});

test('the storyboard fires mannequin generation as it loads', () => {
  assert.match(storyboardSource, /import \{ requestMannequinGeneration \} from '@\/features\/mannequin\/generationRunner\.js'/);
  // 발사는 보드 로드를 막지 않는다 — await 하면 병렬화가 사라진다.
  assert.match(storyboardSource, /void requestMannequinGeneration\(pid\)\.catch\(\(\) => \{\}\)/);
  assert.doesNotMatch(storyboardSource, /await requestMannequinGeneration/);
});

const chromeSource = read('../../src/features/shell/ChromeLayout.jsx');

test('the ribbon announces completion and stops steering', () => {
  assert.match(chromeSource, /마네킹컷 준비 완료/);
  assert.match(chromeSource, /DONE_BADGE_MS/);
  assert.doesNotMatch(chromeSource, /마네킹 화면 보기/);
  assert.doesNotMatch(chromeSource, /job-ribbon-btn/);
});

test('the done badge is scoped to the project whose job actually finished, not a bare "something ran" flag', () => {
  // beginProject/adoptProject 도 mannequinJob 을 idle 로 되돌리지만(initialMannequinJob()),
  // projectId 는 null 로 지운다. bare boolean(wasRunningRef)로 되돌리면 이 리셋도 완료로
  // 오인된다 — 반드시 '실행 중이던 프로젝트 id' 를 기억하고 idle 전환 시 그 id 와 대조해야 한다.
  assert.doesNotMatch(chromeSource, /wasRunningRef/);
  assert.match(chromeSource, /runningProjectIdRef\.current = job\.projectId/);
  assert.match(chromeSource, /job\.projectId !== runningProjectIdRef\.current/);
});
