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
  let owner = null;
  const key = analysisPhotoKey(product);
  let run;
  run = createFeatureRun({
    readAnalysis: () => readAnalysis?.() || latest,
    isCurrent: (record) => owner
      ? owner.runId === record.runId && owner.photoSignature === record.photoSignature
        && owner.projectId === run.getProjectId() && owner.accountGeneration === clientEvents.getAccountGeneration()
      : getActiveFeatureRun() === run && isCurrent()
        && analysisPhotoKey(readProduct?.() || product) === key
        && (!latest?.featureRun || latest.featureRun.runId === record.runId)
        && (!latest?.featureRun || latest.featureRun.photoSignature === record.photoSignature),
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
      messageCode: code, context: { projectId: run?.getProjectId() }, error: Object.assign(new Error(code), { code }), blocking: false }),
  });
  const freeze = run.freeze;
  run.freeze = (projectId, options) => {
    const record = run.getRecord();
    if (owner && owner.projectId !== projectId) throw new Error('feature_project_changed');
    owner ||= { projectId, runId: record.runId, photoSignature: record.photoSignature, accountGeneration: clientEvents.getAccountGeneration() };
    return freeze(projectId, options);
  };
  return setActiveFeatureRun(run);
}

export async function restoreDraftFeatureRun(draft, { promotion = false, onAnalysis, readProduct, readAnalysis, isCurrent } = {}) {
  if (!isAnalysisFastEntryEnabled() || !draft?.analysis?.featureRun) return null;
  const saved = draft.analysis.featureRun;
  const existing = getActiveFeatureRun();
  if (existing?.getRecord()?.runId === saved.runId && existing.getRecord().photoSignature === saved.photoSignature) return existing;
  const currentProduct = readProduct?.() || draft.product;
  const key = analysisPhotoKey(currentProduct);
  let options = {};
  if (['requested', 'received', 'decided'].includes(saved.stage) || !saved.coreReceived) {
    const colors = draft.product?.colors || [];
    const product = { ...draft.product, colors: colors.map((color) => ({ ...color, images: (color.images || []).map((photo) => ({ ...photo,
      blob: draft.photos?.find((stored) => stored.imageId === photo.id)?.blob })) })) };
    try {
      options = await preparePublicAnalysis(product, { fetchBlob: async (src) => {
        const photo = product.colors.flatMap((color) => color.images).find((item) => item.src === src);
        if (photo?.blob) return photo.blob;
        throw new Error('feature_photo_missing');
      } });
    } catch { return null; }
    if (options.photoSignature !== saved.photoSignature) return null;
    options.product = draft.product;
  }
  const run = createDraftFeatureRun({ product: currentProduct, analysis: draft.analysis, onAnalysis, readProduct, readAnalysis, isCurrent });
  await run.resume(saved, options, { promotion });
  if (!promotion && !saved.coreReceived) {
    const core = await api.analyzeProduct(null, { ...options, product: draft.product, parts: 'core', runId: saved.runId,
      isCurrent: () => getActiveFeatureRun() === run && analysisPhotoKey(readProduct?.() || draft.product) === key });
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
