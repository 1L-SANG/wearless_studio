import { api } from './api/index.js';
import { createFeatureRun, getActiveFeatureRun, setActiveFeatureRun, stripFeatureClientFields } from './featureRun.js';
import { analysisPhotoKey, preparePublicAnalysis } from './api/publicAnalysisRequest.js';
import { loadDraft, saveFeatureRunDraft, flushProductDraftSave } from './draftStore.js';
import { clientEvents } from './clientEvents.js';
import { invalidateStoryboardEntryPrefetch } from '../features/storyboard/storyboardEntryPrefetch.js';
import { isAnalysisFastEntryEnabled } from './analysisFastEntry.js';

const promotionRuns = new Map();
const runKey = (record) => JSON.stringify([record?.runId, record?.photoSignature]);
const sameRun = (run, record) => !!run && runKey(run.getRecord()) === runKey(record);
export function getDraftFeatureRun(draft) {
  const record = draft?.analysis?.featureRun;
  if (!record) return null;
  const active = getActiveFeatureRun();
  return sameRun(active, record) ? active : promotionRuns.get(runKey(record)) || null;
}
export function claimDraftFeatureRun(draft, projectId) {
  if (isAnalysisFastEntryEnabled()) getDraftFeatureRun(draft)?.claimPromotion(projectId);
}

// 실행 수명은 입력 화면보다 길다. 화면이 사라져도 마지막 refs와 초안으로 저장을 마친다.
export function createDraftFeatureRun({ product, analysis, readProduct, readAnalysis, onAnalysis, isCurrent = () => true, promotion = false }) {
  let latest = analysis;
  let owner = null;
  let key = analysisPhotoKey(product);
  const currentAnalysis = () => readAnalysis?.() || latest;
  let run;
  run = createFeatureRun({
    readAnalysis: currentAnalysis,
    isCurrent: (record) => owner
      ? owner.runId === record.runId && owner.photoSignature === record.photoSignature
        && owner.projectId === run.getProjectId() && owner.accountGeneration === clientEvents.getAccountGeneration()
      : (promotion || getActiveFeatureRun() === run) && isCurrent()
        && analysisPhotoKey(readProduct?.() || product) === key
        && (!latest?.featureRun || latest.featureRun.runId === record.runId)
        && (!latest?.featureRun || latest.featureRun.photoSignature === record.photoSignature),
    saveAnalysis: async (patch, options) => {
      if (!options.isCurrent()) return;
      const current = currentAnalysis() || {};
      latest = { ...current, ...patch };
      const saved = await saveFeatureRunDraft(patch, { ...options,
        matchesDraft: (draft) => (!owner && !promotion) || sameRun(run, draft.analysis?.featureRun),
      });
      if (!saved) {
        if (!options.isCurrent()) return false;
        if (!owner && !promotion) throw new Error('feature_draft_save_failed');
      }
      if (!options.isCurrent()) return false;
      latest = { ...currentAnalysis(), ...patch };
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
  run.rebind = (options) => {
    ({ product, analysis: latest, readProduct, readAnalysis, onAnalysis, isCurrent = () => true } = options);
    key = analysisPhotoKey(product);
  };
  const claim = run.claimPromotion;
  run.claimPromotion = (projectId) => {
    const record = run.getRecord();
    if (owner && owner.projectId !== projectId) throw new Error('feature_project_changed');
    owner ||= { projectId, runId: record.runId, photoSignature: record.photoSignature, accountGeneration: clientEvents.getAccountGeneration() };
    claim(projectId);
    promotionRuns.set(runKey(record), run);
  };
  const freeze = run.freeze;
  run.freeze = (projectId, options) => {
    run.claimPromotion(projectId);
    return freeze(projectId, options);
  };
  return promotion ? run : setActiveFeatureRun(run);
}

export async function restoreDraftFeatureRun(draft, { promotion = false, projectId, onAnalysis, readProduct, readAnalysis, isCurrent } = {}) {
  if (!isAnalysisFastEntryEnabled() || !draft?.analysis?.featureRun) return null;
  const saved = draft.analysis.featureRun;
  const currentProduct = readProduct?.() || draft.product;
  const existing = getDraftFeatureRun(draft);
  if (existing) {
    if (!promotion) existing.rebind?.({ product: currentProduct, analysis: draft.analysis, onAnalysis, readProduct, readAnalysis, isCurrent });
    if (projectId) existing.claimPromotion(projectId);
    return existing;
  }
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
  const run = createDraftFeatureRun({ product: currentProduct, analysis: draft.analysis, onAnalysis, readProduct, readAnalysis, isCurrent, promotion });
  await run.resume(saved, options, { promotion });
  if (projectId) run.claimPromotion(projectId);
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
  let run = getDraftFeatureRun(draft);
  if (!run) {
    run = await restoreDraftFeatureRun(draft, { promotion: true, projectId });
    restoreFeatures = true;
  }
  if (!run) return { analysis: stripFeatureClientFields(draft.analysis), run: null };
  const result = restoreFeatures ? 'restored' : await run.wait();
  const analysis = run.freeze(projectId, { deadlineExceeded: result === 'deadline' });
  return { analysis: { ...stripFeatureClientFields(analysis) }, run };
}
