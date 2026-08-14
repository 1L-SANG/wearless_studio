import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { selectStoryboardCopywriting } from '../../src/features/storyboard/copywritingSelection.js';
import {
  classifyStoryboardLoadError,
  storyboardNotFoundError,
} from '../../src/features/storyboard/storyboardLoadError.js';
import { storyboardOverlayTop } from '../../src/features/storyboard/storyboardOverlayTop.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const storyboardSource = read('../../src/features/storyboard/Storyboard.jsx');
const uiSource = read('../../src/components/ui.jsx');
const storeSource = read('../../src/store/useAppStore.js');
const featureStyles = read('../../src/styles/features.css');
const appStyles = read('../../src/styles/app.css');

test('1-02 missing examples use the owner-approved explanatory copy', () => {
  assert.match(
    storyboardSource,
    /이 조합의 예시를 준비하지 못했어요 — 컷 설정을 바꾸거나 직접 예시를 골라주세요/,
  );
  assert.doesNotMatch(storyboardSource, /카드를 열어 다시 시도/);
});

test('1-09 copywriting uses a native keyboard-operable switch and returns its PATCH promise', () => {
  const toggle = uiSource.slice(
    uiSource.indexOf('export function Toggle'),
    uiSource.indexOf('export function ProgressBar'),
  );
  assert.match(toggle, /<button[\s\S]*?type="button"[\s\S]*?role="switch"[\s\S]*?aria-checked=\{on\}[\s\S]*?aria-label=\{label\}/);
  assert.doesNotMatch(toggle, /<div/);
  assert.match(appStyles, /\.tg:focus-visible \{ outline: 2px solid var\(--focus\)/);

  const setter = storeSource.slice(
    storeSource.indexOf('setCopywriting(copywriting)'),
    storeSource.indexOf('/** 서버 응답'),
  );
  assert.match(setter, /copywritingPatchChain = copywritingPatchChain[\s\S]*?api\.patchProject\(projectId, \{ copywriting \}\)/);
  assert.match(setter, /return copywritingPatchChain/);
  assert.match(setter, /restoreCopywriting\(copywriting\)/);
});

test('1-09 only the latest copywriting failure rolls back and emits one toast', async () => {
  const selectionState = { current: { requestId: 0, pending: 0, confirmedValue: true } };
  const pending = [];
  const restored = [];
  const failures = [];
  const setCopywriting = (value) => new Promise((resolve, reject) => {
    pending.push({ value, resolve, reject });
  });
  const shared = {
    setCopywriting,
    restoreCopywriting: (value) => restored.push(value),
    selectionState,
    onFailure: (error) => failures.push(error.message),
  };

  const first = selectStoryboardCopywriting({
    ...shared, currentValue: true, nextValue: false,
  });
  const latest = selectStoryboardCopywriting({
    ...shared, currentValue: false, nextValue: true,
  });
  pending[0].resolve();
  await first;
  pending[1].reject(new Error('latest failed'));
  assert.equal(await latest, false);

  assert.deepEqual(restored, [false]);
  assert.deepEqual(failures, ['latest failed']);

  const retry = selectStoryboardCopywriting({
    ...shared, currentValue: false, nextValue: true,
  });
  pending[2].resolve();
  assert.equal(await retry, true);
});

test('1-10 card actions use a non-overflowing 2 by 2 grid with 44px hit targets', () => {
  const actions = featureStyles.slice(
    featureStyles.indexOf('.sb-canvas-actions {'),
    featureStyles.indexOf('.sb-cutcard.missing'),
  );
  assert.match(actions, /grid-template-columns: repeat\(2, 44px\)/);
  assert.match(actions, /gap: 2px/);
  assert.match(actions, /\.sb-canvas-actions button \{[\s\S]*?width: 44px;[\s\S]*?height: 44px/);
  assert.match(actions, /button::before \{[\s\S]*?width: 24px;[\s\S]*?height: 24px/);
  assert.ok((44 * 2) + 2 <= 136, 'the action grid must fit the smallest 136px card');
});

test('1-11 credit cost remains a separate visible label at 899px and 560px', () => {
  const actionbar = storyboardSource.slice(
    storyboardSource.indexOf('<div className="sb-actionbar">'),
    storyboardSource.indexOf('</div>\n  );', storyboardSource.indexOf('<div className="sb-actionbar">')),
  );
  assert.match(actionbar, /<div className="sb-ab-count">[\s\S]*?<\/div>\s*<span className="sb-ab-cost">생성/);
  const actionbarCssStart = featureStyles.indexOf('.sb-actionbar {');
  const compact = featureStyles.slice(
    featureStyles.indexOf('@media (max-width: 900px)', actionbarCssStart),
    featureStyles.indexOf('@media (max-width: 640px)', actionbarCssStart),
  );
  assert.match(compact, /\.sb-ab-count \{ display: none; \}/);
  assert.doesNotMatch(compact, /\.sb-ab-cost \{[^}]*display: none/);
  assert.match(compact, /\.sb-ab-cost \{ margin-left: auto; \}/);
});

test('1-12 classifies 404 separately from retryable network errors', () => {
  assert.deepEqual(storyboardNotFoundError(), {
    kind: 'notFound', message: '작업을 찾을 수 없어요',
  });
  assert.deepEqual(classifyStoryboardLoadError({ status: 404 }), {
    kind: 'notFound', message: '작업을 찾을 수 없어요',
  });
  assert.deepEqual(classifyStoryboardLoadError(new Error('offline')), {
    kind: 'network', message: '생성예시 카탈로그를 불러오지 못했어요',
  });

  const loadErrorView = storyboardSource.slice(
    storyboardSource.indexOf('if (loadError) return'),
    storyboardSource.indexOf('if (shouldRenderStoryboardLoadingFrame'),
  );
  assert.match(loadErrorView, /loadError\.kind === 'notFound'[\s\S]*?보관함으로 이동[\s\S]*?: \([\s\S]*?다시 시도/);
});

test('N1 collapsed decks use the same card width and 3 by 4 ratio as expanded cards', () => {
  const stack = featureStyles.slice(
    featureStyles.indexOf('.sb-stack {'),
    featureStyles.indexOf('.sb-stack-cut {'),
  );
  assert.match(stack, /width: var\(--sb-card-w\)/);
  assert.match(stack, /aspect-ratio: 3 \/ 4/);
  assert.doesNotMatch(stack, /width: 200px|height: 258px/);
});

test('N3 undo top shares the measured topnav and ribbon-stack offset', () => {
  assert.equal(storyboardOverlayTop(62, 0), 72);
  assert.equal(storyboardOverlayTop(62, 28), 100);
  assert.equal(storyboardOverlayTop(62, 56), 128);
  assert.match(storyboardSource, /querySelector\('\.job-ribbon-stack'\)[\s\S]*?storyboardOverlayTop\(topnavHeight, ribbonHeight\)/);
  assert.match(storyboardSource, /resizeObserver\.observe\(element\)/);
  assert.match(storyboardSource, /className=\{`sb-undo-bar[\s\S]*?style=\{\{ top: `\$\{inspectorTop\}px` \}\}/);
});

test('N9 undo text is compact and dismissal fades before DOM removal', () => {
  assert.match(storyboardSource, /const message = `\$\{operationCount\}건 변경`/);
  assert.doesNotMatch(storyboardSource, /생성예시를 바꾸며 방향·색상 등 설정을 초기화했어요/);

  const dismiss = storyboardSource.slice(
    storyboardSource.indexOf('const dismissUndo ='),
    storyboardSource.indexOf('const scheduleUndoDismiss'),
  );
  assert.match(dismiss, /if \(prefersReducedMotion\(\)\) finishUndoDismiss\(\);\s*\n\s*else setUndoExiting\(true\)/);
  assert.doesNotMatch(dismiss, /setUndoEntry\(null\)/);
  assert.match(storyboardSource, /onAnimationEnd=\{\(event\) => \{[\s\S]*?event\.target === event\.currentTarget && undoExiting[\s\S]*?finishUndoDismiss\(\)/);
  assert.match(featureStyles, /\.sb-undo-bar\.exiting \{[\s\S]*?animation: sb-undo-out \.18s ease-in both/);
  assert.match(featureStyles, /prefers-reduced-motion: reduce[\s\S]*?\.sb-undo-bar, \.sb-undo-bar\.exiting \{ animation: none; \}/);
});
