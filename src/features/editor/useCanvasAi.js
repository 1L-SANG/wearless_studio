import { isGifImage } from '../../lib/gif/gifMedia.js';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api, isMockMode } from '../../lib/api/index.js';
import { extractEditorAssetId } from '../../lib/editorAssetInfo.js';
import { buildFailedCutRetry } from './failedCutRetry.js';
import { buildCanvasAiMenu, buildCanvasAiRequest } from './canvasAiActions.js';
import { createCanvasAiJobController } from './canvasAiJobs.js';
import { resolveCanvasAiSubmission } from './canvasAiPoll.js';
import { canvasAiSourceDimensions } from './canvasAiApply.js';

// 메뉴가 닫히거나 선택이 바뀌어도 사진별 작업과 접수 번호는 에디터가 보관한다.
export function useCanvasAi({ projectId, blocks, wardrobe, storyboard, selectedId,
  product, analysis, clothingType, colorOpts, handlers }) {
  const latest = useRef(null);
  latest.current = { projectId, blocks, wardrobe, storyboard, product, analysis, clothingType, handlers };
  const mounted = useRef(false);
  const cache = useMemo(() => new Map(), [projectId]);
  const [assetState, setAssetState] = useState({ id: null, loading: false, info: null });
  const [jobs, setJobs] = useState(new Map());
  const controller = useMemo(() => createCanvasAiJobController({
    onQueued: (job) => { if (mounted.current && latest.current.projectId === projectId) latest.current.handlers.current.onQueued?.(job); },
    onCancelled: (job) => { if (mounted.current && latest.current.projectId === projectId) latest.current.handlers.current.onCancelled?.(job); },
    onStarted: (job) => { if (mounted.current && latest.current.projectId === projectId) latest.current.handlers.current.onStarted?.(job); },
    onFinished: (job) => { if (mounted.current && latest.current.projectId === projectId) latest.current.handlers.current.onFinished?.(job); },
    onResult: (job, response) => {
      if (!mounted.current || latest.current.projectId !== projectId) return;
      latest.current.handlers.current.onResult?.(job, response);
      if (job.isSlot) controller.dismiss(job.key);
      const image = response?.data;
      const id = image?.assetId || extractEditorAssetId(image?.src)
        || (isMockMode ? image?.id : null);
      if (id && !cache.has(id)) {
        const pending = api.getEditorAssetInfo(projectId, [id])
          .then(({ items }) => items?.[id] || null);
        cache.set(id, pending);
        pending.catch(() => cache.delete(id));
      }
    },
    onError: (job, error) => { if (mounted.current && latest.current.projectId === projectId) latest.current.handlers.current.onError?.(job, error); },
  }), [projectId, cache]);
  useEffect(() => {
    mounted.current = true;
    setJobs(new Map(controller.jobs));
    const unsubscribe = controller.subscribe(setJobs);
    return () => { mounted.current = false; unsubscribe(); controller.dispose(); };
  }, [controller]);

  const block = (blocks || []).find((item) => item.elements?.some((el) => el.id === selectedId));
  const element = block?.elements?.find((el) => el.id === selectedId);
  const wardrobeImage = Object.entries(wardrobe || {}).flatMap(([group, images]) =>
    images.map((image) => ({ ...image, wardrobeGroup: group })))
    .find((image) => image.src && image.src === element?.src);
  const assetId = isGifImage(element) ? null : (extractEditorAssetId(element?.src) || wardrobeImage?.assetId
    || (isMockMode && element?.src ? wardrobeImage?.id || element.src : null));
  useEffect(() => {
    let alive = true;
    if (!assetId) {
      setAssetState({ id: null, loading: false, info: null });
      return () => { alive = false; };
    }
    setAssetState({ id: assetId, loading: true, info: null });
    if (!cache.has(assetId)) {
      cache.set(assetId, api.getEditorAssetInfo(projectId, [assetId])
        .then(({ items }) => items?.[assetId] || null));
    }
    cache.get(assetId).then((info) => {
      if (alive) setAssetState({ id: assetId, loading: false, info });
    }).catch((error) => {
      cache.delete(assetId);
      if (alive) setAssetState({ id: assetId, loading: false, info: null, error });
    });
    return () => { alive = false; };
  }, [assetId, cache, projectId]);

  const info = assetState.id === assetId ? assetState.info : null;
  const retry = element && !element.src ? buildFailedCutRetry(storyboard || [],
    element.sourceBlockId, analysis, product, { editorBlocks: blocks || [], failure: element }) : null;
  const menu = buildCanvasAiMenu({ block, element, storyboard: storyboard || [],
    assetRecipe: info, product, clothingType, colorOpts, frontClosure: analysis?.frontClosure,
    detailRecommendations: analysis?.detailRecommendations, failedCutRetry: retry });
  const start = (action, value, natural = null) => {
    if (!block || !element || isGifImage(element) || (element.src && (!info || assetState.loading))) return;
    const request = buildCanvasAiRequest({ action, value,
      source: { ...menu.source, wardrobeGroup: wardrobeImage?.wardrobeGroup || menu.source?.wardrobeGroup },
      assetRecipe: info, product, slot: { blockId: block.id, elementId: element.id }, failedCutRetry: retry,
      detailRecommendations: analysis?.detailRecommendations });
    if (!request && !retry?.fillImage) return;
    const isSlot = !element.src;
    const group = request?.colorId || info?.current?.colorId || info?.colorId
      || wardrobeImage?.wardrobeGroup || 'misc';
    const dimensions = canvasAiSourceDimensions(element, wardrobeImage, natural);
    const meta = { blockId: block.id, elementId: element.id, sourceSrc: element.src || null,
      sourceAssetId: assetId, sourceWidth: dimensions.width, sourceHeight: dimensions.height,
      action, value, request, group, isSlot, loadingId: null };
    controller.start(element.id, meta, async ({ idempotencyKey, onJobStarted, request: retainedRequest }) => {
      const originalRequest = retainedRequest || request;
      let acceptedId = null;
      const accepted = (id, contextFill) => { acceptedId = id; onJobStarted(id, contextFill); };
      if (originalRequest?.mode === 'fill' || (isSlot && originalRequest?.mode === 'vary')) {
        try { await latest.current.handlers.current.saveBeforeRequest?.(); }
        catch (error) { error.requestNotSent = true; throw error; }
      }
      if (retry?.fillImage && action === 'retry') return { data: retry.fillImage };
      return resolveCanvasAiSubmission(() => originalRequest.mode === 'fill'
        ? api.fillEmptySlot(projectId, { slot: originalRequest.slot, idempotencyKey, onJobStarted: accepted, preserveFailureMessage: true })
        : api.generateImage(projectId, { ...originalRequest, idempotencyKey, onJobStarted: accepted, preserveFailureMessage: true }), {
        getJob: (id) => api.getJob(id), acceptedJobId: () => acceptedId,
        active: () => mounted.current && latest.current.projectId === projectId,
      });
    });
  };
  return { menu, block, element, info, jobs, controller, start,
    loading: Boolean(element?.src && assetId && (assetState.id !== assetId || assetState.loading)),
    assetError: assetState.id === assetId ? assetState.error : null };
}
