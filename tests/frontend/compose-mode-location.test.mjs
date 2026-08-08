import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';

import {
  estimateComposeModeCredits,
  selectAnalysisComposeMode,
} from '../../src/features/analysis/composeModeSelection.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const analysisSource = read('../../src/features/analysis/AnalysisForm.jsx');
const selectionSource = read('../../src/features/analysis/composeModeSelection.js');
const storyboardSource = read('../../src/features/storyboard/Storyboard.jsx');

test('the analysis confirmation CTA owns catalog-backed non-deselectable compose chips', () => {
  const ctaSource = analysisSource.slice(
    analysisSource.indexOf('const cta ='),
    analysisSource.indexOf('if (inline)'),
  );

  assert.match(analysisSource, /const composeModes = catalogs\?\.composeModes \|\| \[\]/);
  assert.match(analysisSource, /label: `\$\{mode\.label\} · \$\{mode\.count\}컷`/);
  assert.match(ctaSource, /<Chips[\s\S]*?className="af-vol"[\s\S]*?allowDeselect=\{false\}/);
  assert.match(ctaSource, /상세페이지 생성은 콘티 컷 수만큼이에요[\s\S]*?composeModeCredits/);
  assert.match(analysisSource, /CREDIT_COSTS\.storyboardPerCut/);
  assert.match(analysisSource, /await composeModeSaveRef\.current;\s*\n\s*onNext\(\);/);
  assert.match(ctaSource, /disabled=\{composeModeSaving\}[\s\S]*?onClick=\{confirmAnalysis\}/);
});

test('analysis mode changes invalidate the warmed storyboard before using the existing store setter', async () => {
  const calls = [];
  const changed = await selectAnalysisComposeMode({
    currentMode: 'basic',
    nextMode: 'extended',
    projectId: 'project-1',
    invalidateStoryboardPrefetch: (projectId) => calls.push(`invalidate:${projectId}`),
    setComposeMode: async (mode) => calls.push(`set:${mode}`),
  });

  assert.equal(changed, true);
  assert.deepEqual(calls, ['invalidate:project-1', 'set:extended']);

  calls.length = 0;
  assert.equal(await selectAnalysisComposeMode({
    currentMode: 'extended',
    nextMode: 'extended',
    projectId: 'project-1',
    invalidateStoryboardPrefetch: () => calls.push('invalidate'),
    setComposeMode: async () => calls.push('set'),
  }), false);
  assert.deepEqual(calls, []);
  assert.doesNotMatch(selectionSource, /markGenerationRelevantEdits/);
});

test('compose-mode credit estimates scale both ends of the catalog count', () => {
  assert.equal(estimateComposeModeCredits('13', 1), '13');
  assert.equal(estimateComposeModeCredits('14~33', 1), '14~33');
  assert.equal(estimateComposeModeCredits('13', 2), '26');
  assert.equal(estimateComposeModeCredits('14~33', 2), '28~66');
});

test('the storyboard shows a summary and blocks applying a mode to an edited board', () => {
  assert.doesNotMatch(storyboardSource, /ComposeModePicker/);
  assert.equal(
    existsSync(new URL('../../src/features/storyboard/ComposeModePicker.jsx', import.meta.url)),
    false,
  );
  assert.match(
    storyboardSource,
    /사진 양 <strong>\{currentMode\.label\}<\/strong> · 예상 \{currentMode\.count\}컷/,
  );
  assert.match(storyboardSource, /직접 수정한 콘티에는 적용되지 않아요/);
  assert.match(storyboardSource, /disabled=\{!canApply \|\| draftMode === value \|\| applying\}/);
  assert.match(storyboardSource, /isDefaultStoryboardForMode\([\s\S]*?composeModeSeed\.colors[\s\S]*?targetGenders: composeModeSeed\.targetGenders/);
  assert.match(storyboardSource, /await setComposeMode\(nextMode\);[\s\S]*?await onComposeModeChange\(nextMode\);/);
});

const storeSource = read('../../src/store/useAppStore.js');

test('guest compose picks stay local until the project gets its server identity', () => {
  // 분석 페이지는 공개라 비로그인(projectId=null)에서도 칩이 눌린다. 가드가 없으면
  // /v1/projects/null 로 PATCH 가 나간다 — 콘티보드(로그인 전용)에 있던 시절엔 불가능했던 경로.
  const setter = storeSource.slice(
    storeSource.indexOf('setComposeMode(composeMode)'),
    storeSource.indexOf('setCopywriting'),
  );
  assert.match(setter, /if \(!projectId\) return composeModePatchChain;/);

  // 로그인 채택(같은 작업의 연속)이 initialFlow 스프레드로 선택을 basic 으로 되돌리면
  // 게스트의 확장형 선택이 조용히 사라진다 — 보존 + 서버 수렴 둘 다 있어야 한다.
  const adopt = storeSource.slice(
    storeSource.indexOf('adoptProject(projectId'),
    storeSource.indexOf('setResumePath'),
  );
  assert.match(adopt, /composeMode: sameWorkContinuation \? current\.composeMode/);
  assert.match(adopt, /patchProject\(projectId, \{ composeMode: adoptedComposeMode \}\)/);
});

test('the storyboard opens at the top of the page', () => {
  // 분석 → 콘티 전환 시 이전 화면 스크롤이 남아 보드 중간부터 보이던 문제의 회귀 방지.
  assert.match(storyboardSource, /useLayoutEffect\(\(\) => \{ window\.scrollTo\(0, 0\); \}, \[\]\);/);
});
