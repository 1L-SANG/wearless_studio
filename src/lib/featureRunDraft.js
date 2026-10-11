import { api } from './api/index.js';
import { createFeatureRun, getActiveFeatureRun, setActiveFeatureRun, stripFeatureClientFields } from './featureRun.js';
import { analysisPhotoKey, preparePublicAnalysis } from './api/publicAnalysisRequest.js';
import { loadDraft, saveFeatureRunDraft, flushProductDraftSave } from './draftStore.js';
import { clientEvents } from './clientEvents.js';
import { invalidateStoryboardEntryPrefetch } from '../features/storyboard/storyboardEntryPrefetch.js';
import { isAnalysisFastEntryEnabled } from './analysisFastEntry.js';

// 실행 수명은 입력 화면보다 길다. 화면이 사라져도 마지막 refs와 초안으로 저장을 마친다.
export function createDraftFeatureRun({ product, analysis, readProduct, readAnalysis, onAnalysis, isCurrent = () => true }) {
  let latest = analysis;
  let ownsProject = false;
  const key = analysisPhotoKey(product);
  let run;
  run = createFeatureRun({
    readAnalysis: () => readAnalysis?.() || latest,
    isCurrent: (record) => getActiveFeatureRun() === run && isCurrent()
      && (ownsProject || (analysisPhotoKey(readProduct?.() || product) === key
        && (!latest?.featureRun || latest.featureRun.runId === record.runId)
        && (!latest?.featureRun || latest.featureRun.photoSignature === record.photoSignature))),
    saveAnalysis: async (patch, options) => {
      if (!options.isCurrent()) return;
      const current = readAnalysis?.() || latest || {};
      latest = { ...current, ...patch };
      const saved = await saveFeatureRunDraft(patch, options);
      if (!saved) {
        if (!options.isCurrent()) return false;
        throw new Error('feature_draft_save_failed');
      }
      if (!options.isCurrent()) return false;
      latest = { ...(readAnalysis?.() || latest), ...patch };
      onAnalysis?.(latest);
      return true;
    },
    requestFeatures: api.analyzePublicFeatures,
    requestMerge: api.mergePublicPoints,
    onLateDetails: async (projectId, handoff) => {
      try { await api.promoteDetailRecommendations(projectId, handoff); }
      catch (error) { if (error?.status !== 409 && error?.code !== 'invalid_analysis_handoff') throw error; }
      invalidateStoryboardEntryPrefetch(projectId);
    },
    onEvent: (code) => clientEvents.report({ kind: 'analysis_error', action: 'analyze',
      messageCode: code, error: Object.assign(new Error(code), { code }), blocking: false }),
  });
  const freeze = run.freeze;
  run.freeze = (projectId, options) => { ownsProject = true; return freeze(projectId, options); };
  return setActiveFeatureRun(run);
}

export async function restoreDraftFeatureRun(draft, { promotion = false, onAnalysis, readProduct, readAnalysis, isCurrent } = {}) {
  if (!isAnalysisFastEntryEnabled() || !draft?.analysis?.featureRun) return null;
  const saved = draft.analysis.featureRun;
  const existing = getActiveFeatureRun();
  if (existing?.getRecord()?.runId === saved.runId && existing.getRecord().photoSignature === saved.photoSignature) return existing;
  const run = createDraftFeatureRun({ product: draft.product, analysis: draft.analysis, onAnalysis, readProduct, readAnalysis, isCurrent });
  let options = {};
  if (!promotion && (saved.stage === 'requested' || !saved.coreReceived)) {
    const colors = draft.product?.colors || [];
    const product = { ...draft.product, colors: colors.map((color) => ({ ...color, images: (color.images || []).map((photo) => ({ ...photo,
      blob: draft.photos?.find((stored) => stored.imageId === photo.id)?.blob })) })) };
    options = await preparePublicAnalysis(product, { fetchBlob: async (src) => {
      const photo = product.colors.flatMap((color) => color.images).find((item) => item.src === src);
      if (photo?.blob) return photo.blob;
      throw new Error('feature_photo_missing');
    } });
    if (options.photoSignature !== saved.photoSignature) { run.invalidate(); return null; }
    options.product = draft.product;
  }
  await run.resume(saved, options, { promotion });
  if (!promotion && !saved.coreReceived) {
    const core = await api.analyzeProduct(null, { ...options, product: draft.product, parts: 'core', runId: saved.runId,
      isCurrent: () => getActiveFeatureRun() === run && analysisPhotoKey(readProduct?.() || draft.product) === analysisPhotoKey(draft.product) });
    if (core) await run.receiveCore(core);
  }
  return run;
}

export async function waitForFeaturesBeforeLogin() {
  if (!isAnalysisFastEntryEnabled()) return;
  const run = getActiveFeatureRun();
  if (run) await run.wait();
  await flushProductDraftSave();
}


export async function settleDraftFeatureRun(draft, projectId, { restoreFeatures = false } = {}) {
  if (!isAnalysisFastEntryEnabled() || !draft.analysis?.featureRun) return { analysis: draft.analysis, run: null };
  const saved = draft.analysis.featureRun;
  let run = getActiveFeatureRun();
  if (run?.getRecord()?.runId !== saved.runId || run.getRecord().photoSignature !== saved.photoSignature) {
    run = await restoreDraftFeatureRun(draft, { promotion: true });
    restoreFeatures = true;
  }
  if (!run) return { analysis: stripFeatureClientFields(draft.analysis), run: null };
  const result = restoreFeatures ? 'restored' : await run.wait();
  const analysis = run.freeze(projectId, { deadlineExceeded: result === 'deadline' });
  return { analysis: { ...stripFeatureClientFields(analysis) }, run };
}
