import { baseProductPhoto } from '../draftVisuals.js';
import { api as base } from '@/mock/api.js';
import { Placeholder } from '@/mock/placeholders.js';
import { DB } from '@/mock/db.js';
import { uid } from '@/lib/ids.js';
import { CREDIT_COSTS, editorImageCreditCost } from '@/lib/limits.js';
import { classifyEditorAssetInfo, extractEditorAssetId } from '@/lib/editorAssetInfo.js';
import { OUTER_CLOSURE_VALUES, outerClosureDisplayValue, outerClosureRange } from '@/lib/outerClosure.js';
import { isSupportedCutType } from '@/lib/storyboardTaxonomy.js';
import { mockQuery, MOCK_TIDY_KNIT } from '@/mock/outfitV18Fixtures.js';

// Mirrors ADR-0019's server recipe fields. UI-only canvas and preview fields stay out.
const RECIPE_FIELDS = [
  'sectionRole', 'contentRole', 'cutType', 'shot', 'direction', 'sideStyle', 'colorId',
  'faceExposure', 'modelId', 'matchIds', 'matchIdsOrigin', 'refAssetIds', 'refScope',
  'exampleId', 'exampleSelectionOrigin', 'outerClosureState', 'detailTargetId',
  'detailTargetOrigin', 'spaceGroupId', 'spaceVariation', 'spaceSetMemberOrder', 'horizonBackgroundMode',
];
const clone = (value) => structuredClone(value);
const error = (code, message, status = 400) => Object.assign(new Error(message), { code, status });
const hasKey = (key) => typeof key === 'string' && !!key.trim();
const recipeOf = (recipe) => recipe ? Object.fromEntries(RECIPE_FIELDS.map((key) => [key, clone(recipe[key] ?? null)])) : null;
const states = new WeakMap();
let draftVisualsScenario = null;
let productVisualsScenario;
let productVisualsScenarioVersion = 0;
function state() {
  if (!states.has(DB.project)) states.set(DB.project, { assets: new Map(), jobs: new Map(), fills: new Map(), details: new Map() });
  return states.get(DB.project);
}
const assetIdOf = (image) => image.assetId || extractEditorAssetId(image.src || image.url) || image.id || image.src || image.url;

function records(projectId) {
  const found = new Map();
  const add = (image, group, fallbackId) => {
    if (!image || (image.projectId != null && image.projectId !== projectId)) return;
    const id = assetIdOf(image) || fallbackId;
    if (!id) return;
    found.set(id, { ...found.get(id), ...image, assetId: id,
      ...(group ? { wardrobeGroup: group } : {}) });
  };
  for (const image of state().assets.values()) add(image);
  for (const [id, image] of Object.entries(DB.assets || {})) add(image, null, id);
  for (const [group, images] of Object.entries(DB.wardrobe || {})) for (const image of images) add(image, group);
  for (const block of DB.editorBlocks || []) for (const element of block.elements || []) {
    if (element.type !== 'image' || !element.src || [...found.values()].some((image) => image.src === element.src)) continue;
    // Mock demo photos have no persisted asset table. Their actual URL is a stable fallback key;
    // sourceBlockId is deliberately ignored because the photo may have been replaced.
    add({ id: element.assetId || extractEditorAssetId(element.src) || element.src,
      src: element.src, cutType: element.cutType || null, source: element.cutType ? 'generated' : 'upload' });
  }
  return found;
}

function assetInfo(image) {
  const generation = image.metadata?.generation || image.generation;
  const upload = image.source === 'upload' || image.upload || generation?.origin === 'upload';
  const recipe = upload ? null : recipeOf(generation?.recipe || image.recipe);
  const cutType = recipe?.cutType || image.cutType || image.cut_type || null;
  const groupColor = image.wardrobeGroup && image.wardrobeGroup !== 'misc' ? image.wardrobeGroup : null;
  const colorId = recipe?.colorId || image.colorId || groupColor || null;
  const origin = upload ? 'upload' : generation?.origin || image.origin || null;
  return {
    kind: classifyEditorAssetInfo({ ...image, upload, cutType, recipe, origin }), origin, cutType, colorId,
    realDerived: !!(image.realDerived || image.real_derived || image.metadata?.facemarket_real_derived),
    current: {
      colorId,
      outerClosureState: upload ? null : recipe?.outerClosureState ?? null,
      direction: upload ? null : recipe?.direction ?? null,
      detailTargetId: upload ? null : recipe?.detailTargetId ?? null,
    },
    recipe,
  };
}

function normalizedBody(request) {
  const { idempotencyKey, onJobStarted, onProgress, preserveFailureMessage: _preserveFailureMessage, ...body } = request;
  if (body.mode === 'vary' && body.variationMode === 'closure'
    && OUTER_CLOSURE_VALUES.includes(body.outerClosureState)) {
    body.outerClosureState = outerClosureDisplayValue(body.outerClosureState, outerClosureRange(DB.analysis?.frontClosure));
  }
  if (body.source?.src && extractEditorAssetId(body.source.src)) {
    body.source = { ...body.source, src: `/v1/assets/${encodeURIComponent(extractEditorAssetId(body.source.src))}/file` };
  }
  return body;
}
function fingerprint(body) {
  const canonical = (value) => {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort()
      .filter((key) => !key.startsWith('_') && value[key] !== undefined && typeof value[key] !== 'function')
      .map((key) => [key, canonical(value[key])]));
    return value;
  };
  return JSON.stringify(canonical(body));
}
const jobKey = (projectId, key) => JSON.stringify([projectId, key]);
function existingJob(projectId, body, key) {
  if (!hasKey(key)) return null;
  const entry = state().jobs.get(jobKey(projectId, key));
  if (entry && entry.fingerprint !== fingerprint(body)) {
    throw error('idempotency_key_reused', '같은 요청 번호로 다른 내용이 왔어요. 다시 눌러 주세요.', 409);
  }
  return entry;
}
function join(entry, request) {
  if (!entry.done) {
    entry.listeners.push(request);
    if (entry.started) request.onJobStarted?.(entry.id, entry.contextFill && clone(entry.contextFill));
  }
  return entry.promise.then(clone);
}
function submit(projectId, body, request, run, fillKey) {
  const current = state();
  const entry = { id: uid(fillKey ? 'fill' : 'editor'), fingerprint: fingerprint(body), listeners: [],
    started: !fillKey, done: false, contextFill: null };
  if (hasKey(request.idempotencyKey)) current.jobs.set(jobKey(projectId, request.idempotencyKey), entry);
  if (fillKey) current.fills.set(fillKey, entry);
  const onProgress = (progress) => entry.listeners.forEach((listener) => listener.onProgress?.(progress));
  const onJobStarted = (_id, decision) => {
    entry.started = true;
    entry.contextFill = decision;
    entry.listeners.forEach((listener) => listener.onJobStarted?.(entry.id, clone(decision)));
  };
  entry.promise = Promise.resolve().then(() => run({ onProgress, onJobStarted })).finally(() => {
    entry.done = true;
    entry.listeners = [];
    if (fillKey && current.fills.get(fillKey) === entry) current.fills.delete(fillKey);
  });
  return join(entry, request);
}

function recordResult(projectId, result, recipe, origin, source = null) {
  const image = result.data;
  image.assetId = assetIdOf(image);
  // The placeholder palette is finite, so equal SVG bytes must not collapse asset identity.
  if (image.src?.startsWith('data:')) image.src = `${image.src.split('#')[0]}#${image.assetId}`;
  image.metadata = { ...image.metadata, generation: { version: 1, origin,
    autoSelected: origin === 'context_fill', sourceAssetId: source?.assetId || null,
    recipeFrom: source ? 'anchor' : 'self', recipe: recipeOf(recipe) } };
  if (source && assetInfo(source).realDerived) image.metadata.facemarket_real_derived = true;
  const stored = Object.values(DB.wardrobe).flat().find((item) => item.id === image.id);
  if (stored) Object.assign(stored, clone(image));
  state().assets.set(image.assetId, { ...clone(image), projectId });
  return { ...result, creditsCharged: result.creditsCharged ?? CREDIT_COSTS.editorImage };
}

function editPlan(projectId, body) {
  const all = records(projectId);
  const sourceId = extractEditorAssetId(body.source?.src);
  const source = all.get(sourceId) || [...all.values()].find((image) => image.src === body.source?.src || image.url === body.source?.src);
  const info = source && assetInfo(source);
  if (!info || !['worn', 'upload'].includes(info.kind)) {
    throw error('edit_source_unsupported', '이 사진에서는 색상과 열림 정도를 바꿀 수 없어요.');
  }
  const recipe = recipeOf(info.recipe || {});
  if (body.variationMode === 'color') {
    const color = DB.product.colors?.find((item) => item.id === body.colorId);
    if (!color) throw error('color_unknown', '상품 색상을 다시 선택해 주세요.');
    if (!color.images?.some((image) => image.src || image.assetId || image.id)) {
      throw error('no_selected_color_images', '이 색 상품 사진이 없어요.', 409);
    }
    if (info.current.colorId === body.colorId) throw error('edit_same_value', '지금 사진과 같은 값이에요.');
    recipe.colorId = body.colorId;
  } else {
    const range = outerClosureRange(DB.analysis?.frontClosure);
    if (DB.product.clothingType !== 'outer' || range === 'hidden') {
      throw error('closure_unsupported', '이 옷에서는 열림 정도를 바꿀 수 없어요.');
    }
    if (!OUTER_CLOSURE_VALUES.includes(body.outerClosureState)) throw error('outer_closure_invalid', '열림 정도를 다시 선택해 주세요.');
    if (info.current.outerClosureState != null
      && outerClosureDisplayValue(info.current.outerClosureState, range) === body.outerClosureState) {
      throw error('edit_same_value', '지금 사진과 같은 값이에요.');
    }
    recipe.outerClosureState = body.outerClosureState;
  }
  return { source, info, recipe };
}

export const mockAdapter = {
  ...base,
  // 목 에디터는 리비전을 관리하지 않으므로 조회와 저장 기준 모두 모름(null)으로 둔다.
  async peekEditorRevision() { return null; },
  editorRevisionOf() { return null; },
  async pollJob(jobId, { onProgress, intervalMs = 1200, timeoutMs = 90000 } = {}) {
    const start = Date.now();
    for (;;) {
      const job = await base.getMallRegistration(jobId);
      onProgress?.(job.progress);
      if (job.status === 'done') return job;
      if (['error', 'cancelled'].includes(job.status)) throw Object.assign(new Error(job.errorMessage || '등록 상태를 확인해 주세요'), { code: job.code, jobId });
      if (Date.now() - start > timeoutMs) throw Object.assign(new Error('아직 진행 중이에요'), { code: 'job_timeout', jobId });
      await new Promise((resolve) => setTimeout(resolve, intervalMs));
    }
  },
  setDraftVisualsScenario(scenario) { draftVisualsScenario = scenario ? clone(scenario) : null; },
  async prepareDraftVisuals(token, body) {
    if (!draftVisualsScenario) return { state: 'off', jobId: null, sourceAssetIds: body.sourceAssetIds };
    const slot = await base.getDraftSlot(token, { full: true });
    if (!slot?.holdsToken) throw error('token_mismatch', '작업권을 다시 확인해 주세요.', 409);
    const sources = body.role === 'product' ? [baseProductPhoto(slot.payload.product)?.id]
      : slot.payload.customMatch?.localId === body.localId ? slot.payload.customMatch.sources.map((source) => source.id) : [];
    if (JSON.stringify(sources) !== JSON.stringify(body.sourceAssetIds)) throw error('source_changed', '사진이 바뀌었어요.', 404);
    const key = JSON.stringify([body.role, body.sourceAssetIds]);
    const records = state().draftVisuals ||= new Map();
    if (!records.has(key)) records.set(key, { ...clone(body), jobId: uid('draft-visual'), startedAt: Date.now() });
    return this.draftVisualPresentation(records.get(key));
  },
  draftVisualPresentation(record) {
    const scenario = draftVisualsScenario || { status: 'off' };
    const status = scenario.status === 'processing' && scenario.settleTo
      && Date.now() - record.startedAt >= (scenario.delayMs ?? 7000) ? scenario.settleTo : scenario.status || 'ready';
    const url = scenario.tidyUrl || MOCK_TIDY_KNIT;
    return { state: status, jobId: record.jobId, sourceAssetIds: [...record.sourceAssetIds],
      ...(record.role === 'product' ? { front: { tidyStatus: status, tidyUrl: status === 'ready' ? url : null,
        cutoutStatus: status, cutoutUrl: null, width: 768, height: 1024 } }
        : { localId: record.localId, boardUrl: status === 'ready' ? url : null,
          thumbnailUrl: status === 'ready' ? url : null, cutoutStatus: status }) };
  },
  async getDraftVisuals(token) {
    if (!draftVisualsScenario) return { state: 'off', product: null, custom: null };
    const slot = await base.getDraftSlot(token, { full: true });
    if (!slot?.holdsToken) throw error('token_mismatch', '작업권을 다시 확인해 주세요.', 409);
    const result = { state: 'ready', product: null, custom: null };
    for (const record of state().draftVisuals?.values() || []) {
      const ids = record.role === 'product' ? [baseProductPhoto(slot.payload.product)?.id]
        : slot.payload.customMatch?.localId === record.localId ? slot.payload.customMatch.sources.map((source) => source.id) : [];
      if (JSON.stringify(ids) === JSON.stringify(record.sourceAssetIds)) result[record.role] = this.draftVisualPresentation(record);
    }
    return result;
  },
  async promoteDraftVisuals({ jobId }) { return { joined: true, jobId }; },
  setProductVisualsScenario(scenario) {
    productVisualsScenario = scenario === undefined ? undefined : scenario ? clone(scenario) : null;
    productVisualsScenarioVersion++;
  },
  async getProductVisuals(_projectId) {
    const image = Placeholder.product('product-tidy-front', 768, 1024);
    const queryStatus = mockQuery().get('tidyDemo');
    const scenario = productVisualsScenario === undefined
      ? ['processing', 'failed', 'off', 'ready'].includes(queryStatus)
        ? { status: queryStatus, ...(queryStatus === 'processing' ? { settleTo: 'ready', delayMs: 7000 } : {}) }
        : null
      : productVisualsScenario;
    if (!scenario) return { front: { tidyStatus: 'ready', tidyUrl: image, cutoutStatus: 'ready',
      cutoutUrl: image, width: 768, height: 1024 } };
    const key = JSON.stringify([productVisualsScenarioVersion, scenario]);
    if (state().productVisuals?.key !== key) state().productVisuals = { key, startedAt: Date.now() };
    const status = scenario.status === 'processing' && scenario.settleTo
      && Date.now() - state().productVisuals.startedAt >= (scenario.delayMs ?? 7000)
      ? scenario.settleTo : scenario.status || 'ready';
    const cutoutUrl = Object.hasOwn(scenario, 'cutoutUrl') ? scenario.cutoutUrl : image;
    const tidyUrl = Object.hasOwn(scenario, 'tidyUrl') ? scenario.tidyUrl : MOCK_TIDY_KNIT;
    return { front: { tidyStatus: status, tidyUrl: status === 'ready' ? tidyUrl : null,
      cutoutStatus: cutoutUrl ? 'ready' : 'off', cutoutUrl, width: 768, height: 1024 } };
  },
  async analyzeProduct(projectId, options = {}) {
    const result = await base.analyzeProduct(projectId, options);
    if (options.parts !== 'core') return result;
    const { detailRecommendations: _details, detailRecommendationsHandoff: _handoff, ...core } = result;
    return { ...core, pointsTicket: { runId: options.runId, points: core.aiSuggestedPoints || [], signature: 'mock-points-ticket' } };
  },
  async analyzePublicFeatures({ runId }) {
    const scale = Number(import.meta.env?.MOCK_API_LATENCY_SCALE ?? globalThis.process?.env?.MOCK_API_LATENCY_SCALE ?? 1);
    await new Promise((resolve) => setTimeout(resolve, 6000 * scale));
    const points = ['부드러운 원단과 촘촘한 짜임', '단정한 라운드넥'];
    const details = { ...clone(DB.analysis.detailRecommendations || {}), version: 1, status: 'ready', candidates: clone(DB.analysis.detailRecommendations?.candidates || []) };
    return { points: { status: 'ready', items: points },
      detailRecommendations: details,
      detailRecommendationsHandoff: { signature: 'mock-detail-handoff', contract: details },
      featureTicket: { runId, points, signature: 'mock-feature-ticket' } };
  },
  async promoteDetailRecommendations(projectId, handoff) {
    const details = handoff?.contract;
    if (!details || handoff.signature !== 'mock-detail-handoff') throw error('invalid_analysis_handoff', '추천 내용을 확인하지 못했어요.');
    const previous = state().details.get(projectId);
    if (previous && JSON.stringify(previous) !== JSON.stringify(details)) throw error('analysis_handoff_conflict', '이미 저장된 추천이 있어요.', 409);
    state().details.set(projectId, clone(details));
    DB.analysis.detailRecommendations = clone(details);
    return clone(details);
  },
  async mergePublicPoints({ pointsTicket, featureTicket }) {
    return { decisions: featureTicket.points.map((_, late) => late < pointsTicket.points.length
      ? { late, action: 'refine', early: late } : { late, action: 'new', early: null }) };
  },
  async getWardrobe(projectId) {
    const wardrobe = await base.getWardrobe(projectId);
    for (const images of Object.values(wardrobe)) for (const image of images) image.assetId = assetIdOf(image);
    return wardrobe;
  },
  async getEditorAssetInfo(projectId, assetIds) {
    if (!Array.isArray(assetIds) || assetIds.length > 60
      || assetIds.some((id) => typeof id !== 'string' || !id.trim())) {
      throw error('asset_ids_invalid', '사진 정보를 다시 확인해 주세요.');
    }
    const all = records(projectId);
    const items = {};
    for (const id of new Set(assetIds)) {
      const image = all.get(id) || [...all.values()].find((record) => record.src === id || record.url === id);
      if (image) Object.defineProperty(items, id, { value: assetInfo(image), enumerable: true, configurable: true });
    }
    return { items };
  },
  async uploadPhoto(projectId, photo, options) {
    const result = await base.uploadPhoto(projectId, photo, options);
    state().assets.set(result.assetId, { id: result.assetId, assetId: result.assetId,
      src: result.url, source: 'upload', projectId });
    return result;
  },
  async uploadAsset(file, { projectId } = {}) {
    const result = await base.uploadAsset(file, { projectId });
    state().assets.set(result.id, { ...result, assetId: result.id, source: 'upload', projectId });
    return result;
  },
  async pickAnyImage(projectId) {
    const result = await base.pickAnyImage(projectId);
    let src = typeof result === 'string' ? result : result.url || result.src;
    const assetId = typeof result === 'object' ? assetIdOf(result) : uid('upload');
    if (src?.startsWith('data:')) src = `${src.split('#')[0]}#${assetId}`;
    state().assets.set(assetId, { assetId, src, source: 'upload', projectId });
    return typeof result === 'string' ? src : { assetId, url: src };
  },
  async generateImage(projectId, request = {}) {
    if (request.mode === 'fill') return this.fillEmptySlot(projectId, request);
    const body = normalizedBody(request);
    const isEdit = body.mode === 'vary' && ['color', 'closure'].includes(body.variationMode);
    if (isEdit && !hasKey(request.idempotencyKey)) {
      throw Object.assign(error('idempotency_key_required', '요청 번호가 없어요. 다시 눌러 주세요.'), { requestNotSent: true });
    }
    const prior = existingJob(projectId, body, request.idempotencyKey);
    if (prior) return join(prior, request);
    const all = records(projectId);
    const source = all.get(extractEditorAssetId(body.source?.src))
      || [...all.values()].find((image) => image.src === body.source?.src || image.url === body.source?.src);
    const info = source && assetInfo(source);
    const requestedCutType = body.mode === 'vary'
      ? info?.kind === 'upload' ? null : info?.cutType || body.source?.cutType
      : body.cutType;
    if (requestedCutType != null && !isSupportedCutType(requestedCutType)) {
      throw Object.assign(error('unknown_cut_type', '지원하지 않는 컷 종류예요. 새 이미지를 선택해 주세요.'), { requestNotSent: true });
    }
    const plan = isEdit ? editPlan(projectId, body) : null;
    return submit(projectId, body, request, async (callbacks) => {
      if (plan) {
        const colorId = body.variationMode === 'color' ? body.colorId : plan.source.wardrobeGroup || plan.info.colorId || 'misc';
        const cutType = plan.info.kind === 'upload' ? 'styling' : plan.info.cutType || 'styling';
        const result = await base.generateImage(projectId, { mode: 'new', cutType, colorId, ...callbacks },
          { creditCost: editorImageCreditCost(body) });
        return recordResult(projectId, result, plan.recipe, `editor_${body.variationMode}`, plan.source);
      }
      const isSimilar = body.mode === 'vary' && body.variationMode === 'similar';
      let result;
      if (isSimilar && ['worn', 'upload'].includes(info?.kind)) {
        if ('changes' in body && (!Array.isArray(body.changes) || body.changes.length)) {
          throw error('vary_unsupported', '이 사진은 비슷한 컷만 만들 수 있어요.');
        }
        const rootId = source.similarCut?.rootAssetId || source.assetId;
        const root = all.get(rootId) || source;
        const rootInfo = assetInfo(root);
        const cutType = rootInfo.kind === 'upload' ? 'styling' : rootInfo.cutType || 'styling';
        const colorId = root.wardrobeGroup || assetInfo(root).colorId || source.wardrobeGroup || 'misc';
        result = await base.generateImage(projectId, { mode: 'new', cutType, colorId, ...callbacks },
          { creditCost: editorImageCreditCost(body) });
        const actions = [['relax-arms', '팔의 힘을 풀고 자연스럽게 서기'], ['glance-side', '시선을 살짝 옆으로 옮기기'], ['soft-smile', '고개를 살짝 기울이며 부드럽게 웃기']];
        const previous = [...all.values()].filter((image) => image.similarCut?.rootAssetId === rootId);
        const [actionKey, actionSummary] = actions[previous.length % actions.length];
        result.data.similarCut = { version: 1, rootAssetId: rootId, sourceAssetId: source.assetId,
          plan: { actionKey, actionSummary, instructions: actionSummary, preserve: ['인물', '옷', '장소'],
            avoid: previous.slice(-2).map((image) => image.similarCut.plan.actionSummary) } };
      } else result = await base.generateImage(projectId, { ...body, ...callbacks });
      const retry = body.retryBlockId && DB.storyboard?.find((block) => block.id === body.retryBlockId);
      const recipe = body.mode === 'vary' ? info?.recipe || null : recipeOf(retry || body);
      const origin = isSimilar ? 'editor_similar' : body.mode === 'vary' ? 'editor_reshoot' : retry ? 'editor_retry' : 'editor_new';
      return recordResult(projectId, result, recipe, origin, body.mode === 'vary' ? source : null);
    });
  },
  async fillEmptySlot(projectId, request = {}) {
    const { slot, idempotencyKey } = request;
    if (!hasKey(idempotencyKey)) throw Object.assign(error('fill_slot_invalid', '이 칸을 찾지 못했어요.'), { requestNotSent: true });
    const body = { mode: 'fill', slot };
    const prior = existingJob(projectId, body, idempotencyKey);
    if (prior) return join(prior, request);
    const fillKey = JSON.stringify([projectId, slot?.blockId, slot?.elementId]);
    const active = state().fills.get(fillKey);
    if (active) {
      state().jobs.set(jobKey(projectId, idempotencyKey), active);
      return join(active, request);
    }
    return submit(projectId, body, request, async (callbacks) => {
      const result = await base.fillEmptySlot(projectId, { slot, idempotencyKey, ...callbacks });
      return recordResult(projectId, result, { cutType: result.data.cutType, colorId: result.contextFill.colorId }, 'context_fill');
    }, fillKey);
  },
};
