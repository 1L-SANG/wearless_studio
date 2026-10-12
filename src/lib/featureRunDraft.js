import { api } from './api/index.js';
import { createFeatureRun, getActiveFeatureRun, setActiveFeatureRun, stripFeatureClientFields } from './featureRun.js';
import { analysisPhotoKey, preparePublicAnalysis } from './api/publicAnalysisRequest.js';
import { loadDraft, queueProductDraftSave, saveFeatureRunDraft, flushProductDraftSave } from './draftStore.js';
import { clientEvents } from './clientEvents.js';
import { invalidateStoryboardEntryPrefetch } from '../features/storyboard/storyboardEntryPrefetch.js';
import { isAnalysisFastEntryEnabled } from './analysisFastEntry.js';
import { analysisResponseProductPatch, mergeProductOwnedAnalysisFields } from '../features/product-input/saveRouting.js';
import { autofillColorGroups } from '../features/product-input/colorAutofill.js';
import { draftSlot } from './draftSlot.js';

const promotionRuns = new Map();
const runKey = (record) => JSON.stringify([record?.runId, record?.photoSignature]);
const sameRun = (run, record) => !!run && runKey(run.getRecord()) === runKey(record);
const withCurrentImages = (metadata, current) => ({ ...metadata, colors: (metadata.colors || []).map((color) => ({ ...color,
  images: current?.colors?.find((item) => item.id === color.id)?.images || color.images })) });
export function getDraftFeatureRun(draft) {
  const record = draft?.analysis?.featureRun;
  if (!record) return null;
  const active = getActiveFeatureRun();
  const preparing = record.stage === 'preparing' && record.runId === active?.getRecord()?.runId;
  const run = sameRun(active, record) || preparing ? active : promotionRuns.get(runKey(record)) || null;
  return run?.isCurrentRuntime?.() === false ? null : run;
}
export function claimDraftFeatureRun(draft, projectId) {
  if (isAnalysisFastEntryEnabled()) getDraftFeatureRun(draft)?.claimPromotion(projectId);
}

// 실행 수명은 입력 화면보다 길다. 화면이 사라져도 마지막 refs와 초안으로 저장을 마친다.
export function createDraftFeatureRun({ product, analysis, readProduct, readAnalysis, onAnalysis, onProduct, swatchColors = [], isCurrent = () => true, promotion = false }) {
  let latest = analysis;
  let owner = null;
  const accountGeneration = clientEvents.getAccountGeneration(), identityEpoch = draftSlot.getIdentityEpoch();
  const currentRuntime = () => accountGeneration === clientEvents.getAccountGeneration()
    && identityEpoch === draftSlot.getIdentityEpoch();
  let key = analysisPhotoKey(product);
  const currentAnalysis = () => readAnalysis?.() || latest;
  let run, coreRequest = null, preparing = null;
  const currentRun = (record) => owner
      ? owner.runId === record.runId && owner.photoSignature === record.photoSignature
        && owner.projectId === run.getProjectId() && owner.accountGeneration === clientEvents.getAccountGeneration()
      : currentRuntime() && (promotion || getActiveFeatureRun() === run) && isCurrent()
        && analysisPhotoKey(readProduct?.() || product) === key
        && (!latest?.featureRun || latest.featureRun.runId === record.runId)
        && (!latest?.featureRun || latest.featureRun.photoSignature === record.photoSignature);
  run = createFeatureRun({
    readAnalysis: currentAnalysis,
    isCurrent: currentRun,
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
  run.isCurrentRuntime = currentRuntime;
  run.hasCoreRequest = () => !!coreRequest || !!preparing;
  const requestCore = (options) => {
    if (run.getRecord()?.coreReceived) return Promise.resolve(currentAnalysis());
    if (coreRequest) return coreRequest;
    const record = run.getRecord();
    const current = () => !!record && currentRun(record) && !run.getRecord()?.coreFailed
      && !['committing', 'committed'].includes(run.getRecord()?.stage);
    if (!current()) return Promise.resolve(null);
    coreRequest = (async () => {
      const core = await api.analyzeProduct(null, { ...options, parts: 'core', runId: record.runId, isCurrent: current });
      if (!core || !current()) return null;
      const source = readProduct?.() || product;
      const nextProduct = { ...source, name: source.name?.trim() || core.suggestedName || '새 상품',
        ...analysisResponseProductPatch(core), colors: autofillColorGroups(source.colors, core.swatchSuggestions, swatchColors) };
      const saved = await saveFeatureRunDraft({}, { product: nextProduct, isCurrent: current,
        matchesDraft: (draft) => sameRun(run, draft.analysis?.featureRun) });
      if (!current()) return null;
      if (!saved) throw new Error('feature_draft_save_failed');
      product = withCurrentImages(nextProduct, readProduct?.() || product);
      const analysis = await run.receiveCore(mergeProductOwnedAnalysisFields(core, nextProduct));
      if (analysis && current()) {
        product = withCurrentImages(product, readProduct?.() || product);
        onProduct?.(product);
      }
      return analysis;
    })().catch(async (error) => {
      if (!current()) return null;
      await run.failCore();
      throw error;
    }).finally(() => { coreRequest = null; });
    return coreRequest;
  };
  run.requestCore = (options) => preparing || requestCore(options);
  run.startAnalysis = ({ runId, startedAt, composeMode = 'basic' }) => {
    if (preparing) return preparing;
    const record = run.prepare({ runId, startedAt });
    latest = { featureRun: record, dismissedAiPoints: [] };
    queueProductDraftSave(readProduct?.() || product, latest, composeMode);
    onAnalysis?.(latest);
    preparing = (async () => {
      await flushProductDraftSave();
      if (!currentRun(record)) return null;
      const draft = await loadDraft();
      if (!currentRun(record)) return null;
      const source = readProduct?.() || product;
      const options = await preparePublicAnalysis(source, { fetchBlob: async (src) => {
        const photo = source.colors.flatMap((color) => color.images || []).find((item) => item.src === src);
        const blob = draft?.photos?.find((item) => item.imageId === photo?.id)?.blob;
        if (!blob) throw new Error('feature_photo_missing');
        return blob;
      } });
      if (!currentRun(record)) return null;
      latest = { ...currentAnalysis(), featureRun: { ...record, photoSignature: options.photoSignature } };
      await run.start({ ...options, product: source, runId, startedAt });
      return requestCore({ ...options, product: source });
    })().catch(async (error) => {
      if (!currentRun(run.getRecord())) return null;
      if (!run.getRecord()?.coreFailed) await run.failCore();
      throw error;
    }).finally(() => { preparing = null; });
    return preparing;
  };
  run.rebind = (options) => {
    const completed = run.getRecord()?.coreReceived && !options.analysis?.featureRun?.coreReceived;
    const completedAnalysis = latest, completedProduct = product;
    ({ product, analysis: latest, readProduct, readAnalysis, onAnalysis, onProduct, swatchColors = [], isCurrent = () => true } = options);
    if (latest?.featureRun?.stage === 'preparing' && run.getRecord()?.stage !== 'preparing') {
      latest = { ...latest, featureRun: run.getRecord() };
    }
    if (completed) {
      latest = completedAnalysis;
      product = withCurrentImages(completedProduct, product);
    }
    key = analysisPhotoKey(product);
    if (completed && currentRun(run.getRecord())) { onAnalysis?.(latest); onProduct?.(product); }
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

export async function restoreDraftFeatureRun(draft, { promotion = false, projectId, onAnalysis, onProduct, swatchColors, readProduct, readAnalysis, isCurrent } = {}) {
  if (!isAnalysisFastEntryEnabled() || !draft?.analysis?.featureRun) return null;
  const saved = draft.analysis.featureRun;
  if (saved.coreFailed && !saved.coreReceived) return null;
  const currentProduct = readProduct?.() || draft.product;
  const existing = getDraftFeatureRun(draft);
  if (existing) {
    if (!promotion) existing.rebind?.({ product: currentProduct, analysis: draft.analysis, onAnalysis, onProduct, swatchColors, readProduct, readAnalysis, isCurrent });
    if (projectId) existing.claimPromotion(projectId);
    if (promotion || existing.getRecord()?.coreReceived) return existing;
    if (existing.getRecord()?.coreFailed) return null;
    if (existing.hasCoreRequest?.()) { await existing.requestCore(); return existing; }
  }
  if (saved.stage === 'preparing') {
    if (promotion) return null;
    const run = existing || createDraftFeatureRun({ product: currentProduct, analysis: draft.analysis, onAnalysis, onProduct, swatchColors, readProduct, readAnalysis, isCurrent });
    await run.startAnalysis({ runId: saved.runId, startedAt: saved.startedAt, composeMode: draft.composeMode });
    return run;
  }
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
  const run = existing || createDraftFeatureRun({ product: currentProduct, analysis: draft.analysis, onAnalysis, onProduct, swatchColors, readProduct, readAnalysis, isCurrent, promotion });
  if (!existing) await run.resume(saved, options, { promotion });
  if (projectId) run.claimPromotion(projectId);
  if (!promotion && !saved.coreReceived) {
    await run.requestCore({ ...options, product: draft.product });
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
