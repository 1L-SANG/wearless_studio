import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { ensureV4StoryboardCopywriting } from '../../src/features/storyboard/v4Copywriting.js';
import {
  classifyStoryboardLoadError,
  storyboardNotFoundError,
} from '../../src/features/storyboard/storyboardLoadError.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const storyboardSource = read('../../src/features/storyboard/Storyboard.jsx');
const uiSource = read('../../src/components/ui.jsx');
const storeSource = read('../../src/store/useAppStore.js');
const featureStyles = read('../../src/styles/features.css');
const appStyles = read('../../src/styles/app.css');

test('1-02 missing examples use the owner-approved explanatory copy', async () => {
  const { storyboardStage, stagePhoto } = await import('./helpers/storyboardStageHarness.mjs');
  const { textOf } = await import('./helpers/storyboardComponents.mjs');
  const tree = await storyboardStage([stagePhoto('missing', { exampleId: null, thumb: null })]);
  assert.ok(textOf(tree).includes('사진 설정을 확인해주세요'));
});
test('legacy copywriting preferences retain serialized PATCH promises and accessible shared toggles', () => {
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

test('v4 always enables copywriting and shares the pending server patch before continuing', async () => {
  let finish;
  const patches = [];
  const state = { projectId: 'copy-enabled', copywriting: false,
    setCopywriting(value) { state.copywriting = value; patches.push(value); return new Promise(resolve => { finish = resolve; }); },
    restoreCopywriting(value) { state.copywriting = value; } };
  const first = ensureV4StoryboardCopywriting(state.projectId, () => state);
  await Promise.resolve();
  const nextStep = ensureV4StoryboardCopywriting(state.projectId, () => state);
  assert.equal(first, nextStep); assert.deepEqual(patches, [true]);
  let saved = false; nextStep.then(() => { saved = true; });
  await Promise.resolve(); assert.equal(saved, false);
  finish(); await first; assert.equal(saved, true);
  await ensureV4StoryboardCopywriting(state.projectId, () => state);
  assert.deepEqual(patches, [true]);
});

test('v4 copywriting failures restore the local value and can be retried', async () => {
  let fail = true;
  const patches = [];
  const state = { projectId: 'copy-retry', copywriting: false,
    async setCopywriting(value) { state.copywriting = value; patches.push(value); if (fail) throw new Error('save failed'); },
    restoreCopywriting(value) { state.copywriting = value; } };
  await assert.rejects(ensureV4StoryboardCopywriting(state.projectId, () => state), /save failed/);
  assert.equal(state.copywriting, false);
  fail = false; await ensureV4StoryboardCopywriting(state.projectId, () => state);
  assert.equal(state.copywriting, true); assert.deepEqual(patches, [true, true]);
});

test('v4 entry confirms true on the server even when the restored local flag is already true', async () => {
  const patches = [];
  const state = { projectId: 'copy-restore', copywriting: true,
    async setCopywriting(value) { patches.push(value); }, restoreCopywriting() {} };
  await ensureV4StoryboardCopywriting(state.projectId, () => state, { force: true });
  assert.deepEqual(patches, [true]);
});

test('a failed entry confirmation must retry before leaving even if the local flag was true', async () => {
  let fail = true;
  const patches = [];
  const state = { projectId: 'copy-unconfirmed', copywriting: true,
    async setCopywriting(value) { patches.push(value); if (fail) throw new Error('not confirmed'); }, restoreCopywriting() {} };
  await assert.rejects(ensureV4StoryboardCopywriting(state.projectId, () => state, { force: true }), /not confirmed/);
  fail = false;
  await ensureV4StoryboardCopywriting(state.projectId, () => state);
  assert.deepEqual(patches, [true, true]);
});

test('an old project copywriting failure cannot roll back another project', async () => {
  let reject, state = { projectId: 'old-copy', copywriting: false,
    setCopywriting() { return new Promise((_resolve, fail) => { reject = fail; }); },
    restoreCopywriting() { assert.fail('changed the current project'); } };
  const saving = ensureV4StoryboardCopywriting(state.projectId, () => state);
  await Promise.resolve(); state = { projectId: 'new-copy', copywriting: true };
  reject(new Error('old failed')); await assert.rejects(saving, /old failed/);
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

test('v5 floating action keeps the cut count and credit estimate alongside the next-step action', async () => {
  const { loadStoryboardComponent, textOf } = await import('./helpers/storyboardComponents.mjs');
  const Continue = await loadStoryboardComponent('StoryboardContinue.jsx', 'StoryboardContinue', { Toggle: 'toggle' });
  const tree = Continue({ count: 7, credits: 88, copyOn: true });
  const text = textOf(tree); assert.ok(text.includes('구성 7컷')); assert.ok(text.includes('예상 88크레딧')); assert.ok(text.includes('다음: 마네킹컷 선택'));
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

  const loadErrorStart = storyboardSource.indexOf('if (loadError) return');
  const loadErrorView = storyboardSource.slice(
    loadErrorStart,
    storyboardSource.indexOf('if (!blocks || !catalogs)', loadErrorStart),
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

test('v5 overlay starts twelve pixels from the viewport top with a 520px width', () => {
  const css = readFileSync(new URL('../../src/features/storyboard/StoryboardPanels.module.css', import.meta.url), 'utf8');
  assert.match(css, /\.drawer[^}]*top: var\(--sp-12\)/);
  assert.match(css, /width: min\(520px, 40vw\)/);
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
  assert.match(storyboardSource, /onUndoAnimationEnd=\{event => \{[\s\S]*?event\.target === event\.currentTarget && undoExiting[\s\S]*?finishUndoDismiss\(\)/);
  assert.match(featureStyles, /\.sb-undo-bar\.exiting \{[\s\S]*?animation: sb-undo-out \.18s ease-in both/);
  assert.match(featureStyles, /prefers-reduced-motion: reduce[\s\S]*?\.sb-undo-bar, \.sb-undo-bar\.exiting \{ animation: none; \}/);
});
