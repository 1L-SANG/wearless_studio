import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';

import {
  estimateComposeModeCredits,
  selectAnalysisComposeMode,
} from '../../src/features/analysis/composeModeSelection.js';
import { applyStoryboardComposeMode } from '../../src/features/storyboard/storyboardComposeMode.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const analysisSource = read('../../src/features/analysis/AnalysisForm.jsx');
const analysisStyles = read('../../src/styles/features.css');
const selectionSource = read('../../src/features/analysis/composeModeSelection.js');
const storyboardSource = read('../../src/features/storyboard/Storyboard.jsx');

test('the analysis confirmation CTA owns the catalog-backed split compose menu', () => {
  const ctaSource = analysisSource.slice(
    analysisSource.indexOf('const cta ='),
    analysisSource.indexOf('if (inline)'),
  );

  assert.match(analysisSource, /const composeModes = catalogs\?\.composeModes \|\| \[\]/);
  assert.match(analysisSource, /selectedComposeModeLabel = selectedComposeMode[\s\S]*?selectedComposeMode\.count/);
  assert.match(ctaSource, /className=\{`af-cta-split\$\{composeModeOpen \? ' open' : ''\}`\}/);
  assert.match(ctaSource, /aria-haspopup="listbox" aria-expanded=\{composeModeOpen\}/);
  assert.match(ctaSource, /role="listbox"[\s\S]*?role="option" aria-selected=\{composeMode === mode\.value\}/);
  assert.match(ctaSource, /\{mode\.desc\} · \{mode\.count\}컷/);
  // 열리면 '선택된' 옵션부터 포커스하고 화살표로 이동한다 (리뷰 P2 반영, 2026-08-12)
  assert.match(analysisSource, /aria-selected'\) === 'true'\) \|\| options\(\)\[0\]\)\?\.focus\(\)/);
  assert.match(analysisSource, /ArrowDown' && event\.key !== 'ArrowUp'/);
  // 옵션 선택으로 닫힐 때도 트리거로 포커스 복귀 — 닫힌 aria-hidden 안에 포커스가 남지 않게
  assert.match(analysisSource, /setComposeModeOpen\(false\);\s*\n\s*composeModeTriggerRef\.current\?\.focus\(\);\s*\n\s*changeComposeMode\(mode\.value\)/);
  assert.match(analysisSource, /event\.key === 'Escape'[\s\S]*?composeModeTriggerRef\.current\?\.focus\(\)/);
  assert.match(analysisSource, /document\.addEventListener\('pointerdown', closeOnOutsideClick\)/);
  assert.doesNotMatch(analysisSource, /composeModeCredits|CREDIT_COSTS\.storyboardPerCut/);
  assert.doesNotMatch(ctaSource, /af-vol|af-cta-note|af-cta-actions/);
  assert.doesNotMatch(analysisStyles, /\.af-vol|\.af-cta-note|\.af-cta-actions/);
  assert.match(analysisStyles, /\.af-compose-popover \{[\s\S]*?left: 0;[\s\S]*?bottom: calc\(100% \+ 8px\)/);
  // 팝오버는 왼칸 위에만 머문다 — 폭 상한이 없으면 확정 버튼 위까지 뻗는다 (2026-08-12)
  assert.match(analysisStyles, /\.af-compose-popover \{[\s\S]*?min-width: 240px; max-width: 264px/);
  assert.match(analysisStyles, /opacity: 0; transform: translateY\(6px\); pointer-events: none/);
  assert.match(analysisSource, /await composeModeSaveRef\.current;[\s\S]*?await onNext\(\);/);
  assert.match(ctaSource, /disabled=\{composeModeSaving \|\| confirming\}[\s\S]*?onClick=\{confirmAnalysis\}/);
});

test('only the latest failed compose-mode request rolls the UI back', async () => {
  const state = { current: { requestId: 0, confirmedMode: 'basic' } };
  const pending = [];
  const restored = [];
  const failures = [];
  const setComposeMode = (mode) => new Promise((resolve, reject) => {
    pending.push({ mode, resolve, reject });
  });
  const shared = {
    projectId: 'project-1',
    setComposeMode,
    restoreComposeMode: (mode) => restored.push(mode),
    invalidateStoryboardPrefetch: () => {},
    selectionState: state,
    onFailure: (error) => failures.push(error.message),
  };

  const first = selectAnalysisComposeMode({ ...shared, currentMode: 'basic', nextMode: 'extended' });
  const second = selectAnalysisComposeMode({ ...shared, currentMode: 'extended', nextMode: 'basic' });
  pending[0].resolve();
  await first;
  pending[1].reject(new Error('latest failed'));
  await second;

  assert.deepEqual(restored, ['extended']);
  assert.deepEqual(failures, ['latest failed']);
});

test('a stale failure is ignored and the failed value can be retried', async () => {
  const state = { current: { requestId: 0, confirmedMode: 'basic' } };
  const pending = [];
  const restored = [];
  const setComposeMode = (mode) => new Promise((resolve, reject) => {
    pending.push({ mode, resolve, reject });
  });
  const shared = {
    projectId: 'project-1',
    setComposeMode,
    restoreComposeMode: (mode) => restored.push(mode),
    invalidateStoryboardPrefetch: () => {},
    selectionState: state,
    onFailure: () => restored.push('toast'),
  };

  const stale = selectAnalysisComposeMode({ ...shared, currentMode: 'basic', nextMode: 'extended' });
  const latest = selectAnalysisComposeMode({ ...shared, currentMode: 'extended', nextMode: 'basic' });
  pending[1].resolve();
  await latest;
  pending[0].reject(new Error('stale failed'));
  await stale;
  assert.deepEqual(restored, []);

  const retry = selectAnalysisComposeMode({ ...shared, currentMode: 'basic', nextMode: 'extended' });
  pending[2].resolve();
  assert.equal(await retry, true);
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

test('the storyboard shows one segmented basic/extended picker and blocks changes on an edited board', () => {
  assert.doesNotMatch(storyboardSource, /ComposeModePicker/);
  assert.equal(
    existsSync(new URL('../../src/features/storyboard/ComposeModePicker.jsx', import.meta.url)),
    false,
  );
  assert.doesNotMatch(storyboardSource, /사진 양 <strong>|예상 \{currentMode\.count\}컷|>변경<\/button>/);
  assert.match(storyboardSource, /className="sb-compose-segment" role="group" aria-label="사진 양"/);
  assert.match(storyboardSource, /aria-pressed=\{selected\}/);
  assert.match(storyboardSource, /직접 수정한 콘티에는 적용되지 않아요/);
  assert.match(storyboardSource, /disabled=\{applying \|\| \(!selected && !canApply\)\}/);
  assert.match(storyboardSource, /isDefaultStoryboardForMode\([\s\S]*?composeModeSeed\.colors[\s\S]*?targetGenders: composeModeSeed\.targetGenders/);
  assert.match(storyboardSource, /await onApply\(nextMode\)/);
  assert.match(storyboardSource, /onApply=\{onComposeModeApply\}/);
});

test('storyboard compose changes flush, patch, then reload in that exact order', async () => {
  const calls = [];
  const changed = await applyStoryboardComposeMode({
    currentMode: 'basic',
    nextMode: 'extended',
    projectId: 'project-1',
    flushBoard: async () => calls.push('flush'),
    setComposeMode: async () => calls.push('patch'),
    restoreComposeMode: () => calls.push('restore'),
    invalidateStoryboardPrefetch: () => calls.push('invalidate'),
    reloadStoryboard: async () => calls.push('reload'),
  });

  assert.equal(changed, true);
  assert.deepEqual(calls, ['flush', 'invalidate', 'patch', 'reload']);
});

test('a flush failure keeps the compose modal retryable and skips patch and reload', async () => {
  const calls = [];
  const changed = await applyStoryboardComposeMode({
    currentMode: 'basic',
    nextMode: 'extended',
    projectId: 'project-1',
    flushBoard: async () => { calls.push('flush'); throw new Error('offline'); },
    setComposeMode: async () => calls.push('patch'),
    restoreComposeMode: () => calls.push('restore'),
    invalidateStoryboardPrefetch: () => calls.push('invalidate'),
    reloadStoryboard: async () => calls.push('reload'),
    onFlushFailure: () => calls.push('flush-error'),
  });

  assert.equal(changed, false);
  assert.deepEqual(calls, ['flush', 'flush-error']);
});

const storeSource = read('../../src/store/useAppStore.js');

test('pre-confirmation compose picks stay local and promotion owns the first server write', () => {
  // 분석 페이지는 공개라 로그인 여부와 무관하게 확정 전에는 PATCH를 보내지 않는다.
  const setter = storeSource.slice(
    storeSource.indexOf('setComposeMode(composeMode)'),
    storeSource.indexOf('setCopywriting'),
  );
  assert.match(setter, /if \(!projectId \|\| !get\(\)\.productInfoConfirmed\) return composeModePatchChain;/);

  // 승격 함수가 product/analysis와 함께 composeMode를 저장하므로 adoptProject는 선택만 보존하고
  // 별도의 중복 PATCH를 만들지 않는다.
  const adopt = storeSource.slice(
    storeSource.indexOf('adoptProject(projectId'),
    storeSource.indexOf('setResumePath'),
  );
  assert.match(adopt, /composeMode: sameWorkContinuation \? current\.composeMode/);
  assert.doesNotMatch(adopt, /adoptedComposeMode/);
  const promotion = read('../../src/lib/draftSync.js');
  assert.match(promotion, /api\.patchProject\(projectId, \{[\s\S]*?composeMode: draft\.composeMode/);
});

test('the storyboard opens at the top of the page', () => {
  // 분석 → 콘티 전환 시 이전 화면 스크롤이 남아 보드 중간부터 보이던 문제의 회귀 방지.
  assert.match(storyboardSource, /useLayoutEffect\(\(\) => \{ window\.scrollTo\(0, 0\); \}, \[\]\);/);
});
