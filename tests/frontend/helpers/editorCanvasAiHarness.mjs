import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { extractEditorAssetId } from '../../../src/lib/editorAssetInfo.js';
import { buildFailedCutRetry } from '../../../src/features/editor/failedCutRetry.js';
import { buildCanvasAiMenu, buildCanvasAiRequest } from '../../../src/features/editor/canvasAiActions.js';
import { createCanvasAiJobController } from '../../../src/features/editor/canvasAiJobs.js';
import { resolveCanvasAiSubmission } from '../../../src/features/editor/canvasAiPoll.js';
import { canFillCanvasAiSlot, canvasAiSourceDimensions } from '../../../src/features/editor/canvasAiApply.js';

const source = readFileSync(new URL('../../../src/features/editor/useCanvasAi.js', import.meta.url), 'utf8')
  .replace(/^import .*;\n/gm, '').replace('export function useCanvasAi', 'function useCanvasAi');

// Run the real hook's request and result boundaries without a browser or paid API.
// React state is a fixed selected-photo snapshot; the real controller owns each job.
export function canvasAiHookHarness({ blocks, selectedId, wardrobe = {}, storyboard = [],
  product = null, analysis = {}, clothingType = 'top', assetRecipe = null, api = {}, handlers = {} }) {
  const scheduled = new Map();
  let timerId = 0;
  const completions = new Map();
  let finish;
  const element = blocks.flatMap((block) => block.elements || []).find((item) => item.id === selectedId);
  const image = Object.values(wardrobe).flat().find((item) => item.src === element?.src);
  const assetId = extractEditorAssetId(element?.src) || image?.assetId || image?.id || element?.src;
  const handlerRef = { current: { ...handlers, onFinished(job) {
    handlers.onFinished?.(job);
    finish?.(job);
  } } };
  const scope = {
    useEffect: (setup) => setup(), useMemo: (factory) => factory(), useRef: (value) => ({ current: value }),
    useState: (initial) => [initial && 'loading' in initial
      ? { id: assetId, loading: false, info: assetRecipe } : initial, () => {}],
    api: { getEditorAssetInfo: async () => ({ items: {} }), ...api }, isMockMode: true,
    extractEditorAssetId, buildFailedCutRetry, buildCanvasAiMenu, buildCanvasAiRequest, canvasAiSourceDimensions, resolveCanvasAiSubmission,
    createCanvasAiJobController: (options) => createCanvasAiJobController({ ...options,
      createKey: randomUUID,
      schedule: (callback) => { const id = ++timerId; scheduled.set(id, callback); return id; },
      cancelSchedule: (id) => scheduled.delete(id),
    }),
  };
  const useCanvasAi = new Function(...Object.keys(scope), `${source}; return useCanvasAi;`)(...Object.values(scope));
  const hook = useCanvasAi({ projectId: 'project', blocks, wardrobe, storyboard, selectedId,
    product, analysis, clothingType, handlers: handlerRef });
  const start = (action, value) => {
    hook.start(action, value);
    const job = hook.controller.get(selectedId);
    if (!job) return null;
    if (!completions.has(job)) completions.set(job, new Promise((resolve) => { finish = resolve; }));
    return job;
  };
  const run = async () => {
    const job = hook.controller.get(selectedId);
    if (!job) return null;
    const callbacks = [...scheduled.values()]; scheduled.clear();
    callbacks.forEach((callback) => callback());
    await completions.get(job);
    return job;
  };
  return { hook, start, run, scheduled, handlers: handlerRef };
}

export function editorCanvasAiHandlers(overrides) {
  const editor = readFileSync(new URL('../../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
  const start = editor.indexOf('  canvasAiHandlers.current = {');
  const end = editor.indexOf('  const replaceCanvasAiResult =', start);
  const canvasAiHandlers = { current: null };
  const scope = { canvasAiHandlers, genActiveRef: { current: false }, projectId: 'project',
    persistable: (value) => value, uid: () => 'loading', setWardrobe() {},
    genCount: { current: 0 }, genDot: 'none', canvasAiDotRestore: { current: 'none' }, setGenDot() {}, dropLoadingTile() {}, placeGeneratedImage() {}, syncCredits() {},
    canFillCanvasAiSlot, lastPush: { current: 0 }, ...overrides };
  new Function(...Object.keys(scope), editor.slice(start, end))(...Object.values(scope));
  return canvasAiHandlers.current;
}
