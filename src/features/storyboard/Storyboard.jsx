import { HangTag } from '../shell/HangTag.jsx';
import notFoundStyles from '@/components/NotFound.module.css';
import { addTemplateSection, sectionRemovalImpact } from '../../lib/pageTemplates/sectionActions.js';

import { createLateDetailCatalog } from './storyboardLateDetails.js';
import { isAnalysisFastEntryEnabled } from '@/lib/analysisFastEntry.js';
import { isMannequinStageEnabled } from '@/lib/mannequinStage.js';
import { getActiveFeatureRun, isFeatureRunPending } from '@/lib/featureRun.js';
import { canPlanSimilarCut, createSimilarCutBlock, inheritSimilarCutRecipes, reconcileSimilarCutMutation } from '@/lib/storyboardSimilarCuts.js';
import { ClientErrorCapture } from '@/components/ClientErrorCapture.jsx';

/* =============================================================
   features/storyboard — ⑤ 콘티보드 (PRD §8)
   blocks 는 "서버 상태의 working copy" 패턴: 진입 시 fetch → 로컬 편집
   → 생성 CTA 에서 saveStoryboard 로 한 번에 저장 (frontend_state_model §4).
   사용자는 sectionRole(후킹/스타일링/스튜디오/의류 확인), 컷 종류와 생성예시를 다룬다.
   contentRole은 섹션·카드 위치·선택한 컷에서 정하는 내부 생성값이다.
   v4 카피라이팅은 늘 켜고 store → patchProject 직렬 저장을 기다린다.
   UnderlineTabs/ColorDots/MoodGuide/hexFor are exported for the editor.
   ============================================================= */
import { mannequinQuoteConfirmation, mannequinQuoteLabel } from '@/features/mannequin/quoteConfirmation.js';
import React, { useState, useEffect, useLayoutEffect, useRef, useCallback, useMemo } from 'react';

import { useLocation, useNavigate } from 'react-router-dom';
import { api } from '@/lib/api/index.js';

import { CATALOG_DRAG_MIME } from '@/lib/storyboardCatalogDrag.js';
import { reconcileHorizonGroupBackground } from '@/lib/horizonBackgroundAvailability.js';
import { needsGarmentColorMeasurement, updateHorizonGroupBackground } from '@/lib/horizonBackground.js';
import { preserveDetailTargetBinding, isProductDetail, detailTargetPresentation } from '@/lib/detailRecommendations.js';
import { referenceFeedbackPatch } from '../../lib/storyboardExampleFeedback.js';
import { uid } from '@/lib/ids.js';

import { useAppStore } from '@/store/useAppStore.js';
import { Icon, Button, Modal, ProgressBar, useToast } from '@/components/ui.jsx';
import { CreditShortfallModal } from '@/features/credits/CreditShortfallModal.jsx';
import { useDoneGuard, DoneGuardModal } from '@/features/shell/shell.jsx';
import { hasEditorEntered } from '@/lib/editorEntered.js';
import { normalizeMatchIds } from '@/lib/api/matchingItems.js';
import { ensureSections, normalizeBoard } from '@/lib/sections.js';
import { hasDetailSource } from '@/lib/storyboardTaxonomy.js';
import { assignGenerationExamples, generationExampleImageSources } from '@/lib/generationExamples.js';
import { inferStoryboardSpaceSet, spaceSetGroupId, withStoryboardSpaceSetExamples } from '@/lib/storyboardSpaceSetCatalog.js';
import { stripStaleSpaceSetBindings } from '@/lib/storyboardSpaceSetStaleness.js';
import { stripExampleSelectionsById, stripStaleExampleSelections } from '@/lib/storyboardExampleStaleness.js';
import { blocksForModel, firstUnsupportedAiBlock, identityKindOf } from '@/lib/identityScope.js';
import { isRealModelSelection } from '@/features/analysis/modelSelection.js';
import { uniqueGenerationCutCount } from '@/lib/generationCutCount.js';
import { CREDIT_COSTS, crossedBelowDetailPageMinimum, detailPageCreditCost } from '@/lib/limits.js';
import { genderForClothingType } from '@/lib/productGender.js';
import { detachSpaceMembership, insertSpaceSet, rekeySeparatedSpaceRuns, replaceSpaceSetRun } from '@/lib/storyboardSpaceSets.js';
import { consumeStoryboardEntry, loadStoryboardEntry, peekStoryboardEntry } from './storyboardEntryPrefetch.js';
import { sbLastSaved, sbPending, sbSaveIdle, sbSaveNow, sbSetSaveRepair, sbSetConflictHandler, sbStable } from './storyboardPersistence.js';
import { setStoryboardEntryNotice, storyboardEntryState, STORYBOARD_REVIEW_MESSAGE } from '@/lib/pageTemplates/entryNotices.js';
import { storyboardWorkCopies, restoreWorkCopyOnce } from '@/lib/conflictWorkCopy.js';
import { renderGroups } from '@/lib/storyboardRenderGroups.js';
import { prewarmImages } from '@/lib/imagePrewarm.js';

import { requestMannequinGeneration } from '@/features/mannequin/generationRunner.js';
import { retryInitialGeneration } from '@/features/mannequin/initialGenerationSession.js';
import { getProductPhotoPromotionTask, resumeProductPhotoPromotionForStoryboard, retryProductPhotoPromotionFromDraft, subscribeProductPhotoPromotion } from '@/lib/productPhotoPromotion.js';
import { waitForAnalysisEditSave } from '@/features/product-input/saveRouting.js';
import { ensureV4StoryboardCopywriting } from './v4Copywriting.js';
import { classifyStoryboardLoadError, storyboardNotFoundError } from './storyboardLoadError.js';
import { buildColorOpts, visibleColorOpts } from '@/lib/colorOpts.js';
import { continueAfterStoryboardFlush } from './storyboardNavigation.js';
import { continueStoryboardToDetailPage, loadStoryboardCreditQuote, storyboardContinueLabel,
  storyboardCreditResume, storyboardDetailPageCredits } from './storyboardDetailPage.js';

import { shouldReuseInitialStoryboardEntry } from './storyboardEntryReuse.js';

import { bindStoryboardExitFlush, scheduleStoryboardAutosave } from './storyboardSaveLifecycle.js';
import { collectInitialRevealThumbnailUrls, waitForInitialReveal } from './initialRevealGate.js';
import { applyPromotedMatchSelection, getCustomMatchPromotionTask, onCustomMatchPromotionFailure, subscribeCustomMatchPromotion } from '@/lib/customMatchPromotion.js';

import { sectionRuns, visibleSectionRuns } from '../../lib/pageTemplates/sectionRuns.js';
import { moveTemplateSection, changeTemplateSectionDesign } from '../../lib/pageTemplates/sectionActions.js';
import { removeStoryboardPhoto, patchStoryboardPhoto, canReplaceStoryboardSet } from '../../lib/pageTemplates/photoActions.js';
import { reconcileSectionDependencies } from '../../lib/pageTemplates/sectionDependencies.js';
import { addStageAddition, removePairedPhoto, stageAdditions } from '../../lib/pageTemplates/stageAdditions.js';
import { pairedPhotoSlot } from '../../lib/pageTemplates/pairedDesigns.js';
import { wornCropRegions } from '../../lib/pageTemplates/seed.js';
import { usedGalleryExampleIds } from '../../lib/pageTemplates/storyboardGallery.js';
import { templateSectionName, wearUsageForBlock } from '../../lib/pageTemplates/slots.js';
import { StoryboardPageList, NEW_SECTION_DRAG_MIME } from './StoryboardPageList.jsx';
import { StoryboardStage } from './StoryboardStage.jsx';
import { StoryboardDrawer } from './StoryboardDrawer.jsx';
import { StoryboardSectionPicker } from './StoryboardSectionPicker.jsx';
import { sectionDesignChoices } from '../../lib/pageTemplates/designChoices.js';
import { StoryboardPhotoPanel } from './StoryboardPhotoPanel.jsx';
import { StoryboardSpaceSetPanel } from './StoryboardSpaceSetPanel.jsx';
import { StoryboardContinue } from './StoryboardContinue.jsx';
import { assembleStoryboardPreview } from './storyboardPreview.js';
import { useDetailPreviewDimensions } from './useDetailPreviewDimensions.js';
import { useStoryboardShopName } from './useStoryboardShopName.js';
import { StoryboardStyleControls } from './StoryboardStyleControls.jsx';
import { createStoryboardStyleSaveQueue, saveStoryboardPageStyle } from './storyboardPageStyle.js';
import { analysisPageStyle, selectFontStyle, selectColorModeStyle } from '../../lib/pageTemplates/pageStyle.js';
import { baseColorOf, resolvePageTokens } from '../../lib/pageTemplates/tokens.js';
import { productSectionNeedingSelection } from '../../lib/pageTemplates/productPresentation.js';
import { useStoryboardTheme } from './useStoryboardTheme.js';
import { automaticSectionInsertionKey } from '../../lib/pageTemplates/sectionPlacement.js';
import { swapStoryboardPositions } from '../../lib/pageTemplates/storyboardPositions.js';
import { StoryboardBackdrop } from './StoryboardBackdrop.jsx';
import { StoryboardRemoveDialog } from './StoryboardRemoveDialog.jsx';
import { captureSectionPreview, flySectionPreview } from './sectionFlight.js';
import styles from './StoryboardLayout.module.css';
export { UnderlineTabs, ColorDots } from './StoryboardControls.jsx';
export { MoodGuide } from './StoryboardExampleGallery.jsx';
export { shouldRenderGenerationExampleGuide } from './StoryboardPhotoPanel.jsx';

const COLOR_HEX = {
  white: '#ffffff', ivory: '#f3eee1', beige: '#d8c4a3', brown: '#7a5230', black: '#15141a',
  gray: '#9a9aa1', navy: '#1f2a44', blue: '#2a5db0', green: '#3f7a4f', red: '#c0392b', pink: '#e3a7b8', yellow: '#e7c75c',
  purple: '#7d5ba6',
  '블랙': '#15141a', '아이보리': '#f3eee1', '화이트': '#ffffff', '베이지': '#d8c4a3',
};
export const hexFor = (c) => COLOR_HEX[c.swatchId] || COLOR_HEX[c.name] || '#d8d6dc';

const undoLabelForPatch = (patch) => {
  const keys = new Set(Object.keys(patch || {}));
  const labels = [];
  if (keys.has('colorId') || keys.has('colorIds')) labels.push('색상');
  if (keys.has('shot')) labels.push('샷');
  if (keys.has('direction')) labels.push('방향');
  if (keys.has('exampleId') || keys.has('refScope')) labels.push('참조');
  if (keys.has('matchIds')) labels.push('매칭 의류');
  if (keys.has('outerClosureState')) labels.push('아우터 열림');
  if (keys.has('source') || keys.has('ownImages')) labels.push('이미지');
  return labels.length === 1 ? labels[0] : '설정';
};

export function exampleGenderFromAnalysis(analysis, catalogs, clothingType) {
  if (clothingType === 'dress') return genderForClothingType(clothingType, []);
  const allowed = new Set(['women', 'men']);
  const modelId = analysis?.selectedModelId || analysis?.selected_model_id;
  const models = [...(catalogs?.models || []), ...(analysis?.models || [])];
  const modelGender = models.find((model) => model.id === modelId)?.gender;
  if (allowed.has(modelGender)) return modelGender;
  const fitGender = analysis?.fitProfile?.gender;
  if (allowed.has(fitGender)) return fitGender;
  const targets = (analysis?.targetGenders || []).filter((value) => allowed.has(value));
  return targets.length === 1 ? targets[0] : null;
}

const UNDO_WINDOW_MS = 10_000;
const STORYBOARD_NETWORK_ERROR_MESSAGE = '생성예시 카탈로그를 불러오지 못했어요';
const prefersReducedMotion = () => (
  typeof window !== 'undefined'
  && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
);

const initialRevealThumbnailFor = (block, catalogs) => {
  if (block.source === 'mine') return block.thumb || block.ownImages?.[0];
  if (isProductDetail(block)) return detailTargetPresentation(block, catalogs?.detailProduct, catalogs?.detailRecommendations)?.src;
  const example = block.exampleId
    ? (catalogs?.genExamples || []).find((candidate) => candidate.id === block.exampleId)
    : null;
  return (example ? generationExampleImageSources(example).src : null) || block.thumb;
};

const nextSeparatedSpaceGroupId = (setId) => spaceSetGroupId(setId, uid('sg'));
const ensureContiguousSpaceRuns = (blocks) => (
  rekeySeparatedSpaceRuns(blocks, nextSeparatedSpaceGroupId)
);
const normalizeStoryboardMutation = (blocks) => ensureContiguousSpaceRuns(inheritSimilarCutRecipes(normalizeBoard(blocks)));

function usePhotoUploadProgress(projectId) {
  const [state, setState] = useState(null);
  useEffect(() => {
    if (!projectId) { setState(null); return undefined; }
    return subscribeProductPhotoPromotion(projectId, setState);
  }, [projectId]);
  if (!state || state.status !== 'pending' || !state.total) return null;
  return state;
}

function useFeaturePreparation(projectId) {
  const [pending, setPending] = useState(false);
  useEffect(() => {
    if (!projectId || !isAnalysisFastEntryEnabled()) return undefined;
    const run = getActiveFeatureRun();
    if (!run) return undefined;
    return run.subscribe((record) => setPending(isFeatureRunPending(record)));
  }, [projectId]);
  return pending && getProductPhotoPromotionTask(projectId)?.status === 'pending';
}

export function StoryboardLoadingState({ photoUpload = null, featurePending = false }) {
  // 빈 div 의 aria-label 은 낭독되지 않을 수 있다 — 숨긴 텍스트 + status 로 알린다(리뷰 반영)
  const label = featurePending ? '옷 특징 정리 중' : photoUpload
    ? `사진 ${photoUpload.total}장 중 ${photoUpload.done}장 올렸어요`
    : '콘티보드를 불러오는 중이에요';
  return (
    <div role="status" aria-busy="true">
      <span className="sr-only">{label}</span>
      {featurePending && <p className="sb-feature-preparation">옷 특징 정리 중</p>}
      {photoUpload && (
        <div className="sb-upload-progress">
          <ProgressBar
            label={label}
            value={(photoUpload.done / photoUpload.total) * 100}
          />
        </div>
      )}
    </div>
  );
}

function prepareStoryboardEntry(
  [board, rawCatalogs, matchClothing, product, analysis],
  sourceBlocks = board,
  projectId = 'default',
) {
  const p = product;
  const a = analysis;
  const hydratedCatalogs = { ...withStoryboardSpaceSetExamples(rawCatalogs),
    detailProduct: p, detailRecommendations: a?.detailRecommendations, garmentColorEvidence: a?.garmentColorEvidence,
    frontClosure: a?.frontClosure, analysis: a };
  const hasDetailImage = hasDetailSource(p);
  const clothingType = p.clothingType || 'top';
  const exampleGender = exampleGenderFromAnalysis(
    a,
    hydratedCatalogs,
    p.clothingType,
  );
  const sectionedBlocks = ensureSections(sourceBlocks, { hasDetailImage }).map((block) => ({
    ...block,
    ...(block.source === 'ai' ? referenceFeedbackPatch(block, {}, hydratedCatalogs) : {}),
  }));
  // 서버(mannequin.select_base_gender)와 같은 규칙으로 판정한 성별 — 저장된 카드가 물고 있는
  // 공간 세트 바인딩이 "지금의 분석" 기준으로 여전히 저장 가능한지 서버와 같은 눈으로 본다.
  // 입력에서 성별·의류 종류를 바꾼 뒤 콘티로 오면(이전 버튼 재배치), 안 맞는 세트가 낀 카드가
  // 매 저장마다 space_set_gender_mismatch 등으로 400 나던 것을 — 여기서 바인딩만 떼어 되돌린다.
  const boundGender = genderForClothingType(clothingType, a?.targetGenders);
  const adoptedBlocks = sectionedBlocks;
  const spaceSetRepairedBlocks = stripStaleSpaceSetBindings(adoptedBlocks, {
    gender: boundGender,
    clothingType,
  });
  // 서버가 저장 시 도는 두 번째(상호 배타) 검증 — spaceGroupId 로 안 묶인 낱개 예시(일반
  // 생성예시든, 세트 단품을 참고용으로 고른 것이든)의 성별/의류 종류/컷 종류를 같은 카탈로그
  // 로 본다. 같은 이유로 매 저장마다 example_gender_mismatch 등 400 나던 카드를 여기서 되돌린다.
  const exampleRepairedBlocks = stripStaleExampleSelections(spaceSetRepairedBlocks, hydratedCatalogs.genExamples, {
    gender: boundGender,
    clothingType,
  });
  // 구 저장분에는 매칭 의류가 두 벌 들어 있을 수 있다. 현재 단일 선택 계약에 맞춰
  // 첫 선택만 남겨, 콘티에서 다시 고르지 않아도 이후 편집·저장이 같은 규칙을 쓰게 한다.
  const matchRepairedBlocks = exampleRepairedBlocks.map((block) => (
    Array.isArray(block.matchIds) && block.matchIds.length > 1
      ? { ...block, matchIds: normalizeMatchIds(block.matchIds) }
      : block
  ));
  const normalizedBlocks = ensureContiguousSpaceRuns(reconcileSectionDependencies(sourceBlocks, matchRepairedBlocks, p));
  const normalized = sbStable(normalizedBlocks) !== sbStable(sourceBlocks);
  // 방금 바인딩/예시를 뗀 카드만 boundGender(서버가 실제로 검증하는 성별)로 먼저 채운다.
  // 일반 자동배정(exampleGender, 바로 아래)은 실존 모델 픽처럼 targetGenders 와 일부러
  // 갈릴 수 있는 신호까지 우선한다 — 그 신호로 되채우면 방금 뗀 카드가 다시 같은 이유로
  // 낡아 저장이 또 400 날 수 있어, 이 카드들만은 서버가 볼 값을 그대로 쓴다. 두 참조를
  // index 별로 비교해 이번에 뗀 카드만 골라낸다(strip 함수는 안 바뀐 블록을 원본 참조 그대로
  // 돌려주므로 참조 비교만으로 충분하다).
  const repairedIds = exampleRepairedBlocks
    .filter((block, index) => block !== adoptedBlocks[index])
    .map((block) => block.id);
  // 이 모델로 만들 수 없는 예시는 배정 후보에서 빠진다(판정은 서버 규칙 표).
  const entryIdentityKind = identityKindOf(isRealModelSelection(
    a?.selectedModelId || a?.selected_model_id));
  const repairedAssignment = repairedIds.length
    ? assignGenerationExamples(normalizedBlocks, {
      catalog: hydratedCatalogs.genExamples,
      projectId, product: p, analysis: a,
      gender: boundGender,
      onlyBlockIds: repairedIds,
      identityKind: entryIdentityKind,
    })
    : { blocks: normalizedBlocks };
  // 나머지(원래부터 비어 있던 카드 등)는 기존 그대로 exampleGender 로 채운다 — 방금 boundGender
  // 로 채운 카드는 이미 exampleId+origin='auto' 를 갖고 있어 이 호출이 다시 건드리지 않는다.
  const assignment = assignGenerationExamples(repairedAssignment.blocks, {
    catalog: hydratedCatalogs.genExamples,
    projectId, product: p, analysis: a,
    gender: exampleGender,
    identityKind: entryIdentityKind,
  });
  const withSignature = assignment.blocks;

  const allColorOpts = buildColorOpts(p.colors, hydratedCatalogs, hexFor);
  const colorOpts = visibleColorOpts(allColorOpts, p.colors);
  const fallbackColor = [{ id: 'col1', label: '기본', hex: '#15141a' }];

  return {
    blocks: withSignature,
    catalogs: hydratedCatalogs,
    matchClothing,
    clothingType,
    exampleGender,
    identityKind: entryIdentityKind,
    hasDetailImage,
    productName: p.name || '',
    colorOpts: colorOpts.length ? colorOpts : fallbackColor,
    detailColorOpts: allColorOpts.length ? allColorOpts : fallbackColor,
    storyboardSeedContext: {
      colors: p.colors || [],
      targetGenders: a?.targetGenders || [],
      subCategory: a?.subCategory || null,
      customCategory: a?.customCategory || null,
      matchClothing: matchClothing || [],
      detailRecommendations: a?.detailRecommendations,
      sellingPoints: a?.sellingPoints || [],
    },
    normalized,
    assignment,
  };
}

export function Storyboard({ toastOverride = null } = {}) {
  const navigate = useNavigate();
  const location = useLocation();
  const mannequinStageEnabled = isMannequinStageEnabled();
  const initialEntryRef = useRef(undefined);
  if (initialEntryRef.current === undefined) {
    const initialProjectId = useAppStore.getState().projectId;
    const prefetched = initialProjectId && !sbPending.has(initialProjectId)
      ? peekStoryboardEntry(initialProjectId)
      : null;
    initialEntryRef.current = prefetched ? {
      projectId: initialProjectId,
      raw: prefetched,
      prepared: prepareStoryboardEntry(prefetched, undefined, initialProjectId),
    } : null;
  }
  const initialEntry = initialEntryRef.current?.prepared;
  const [blocks, setRawBlocks] = useState(() => initialEntry?.blocks || null);
  const setBlocks = useCallback((update) => setRawBlocks((previous) =>
    reconcileSimilarCutMutation(previous, typeof update === 'function' ? update(previous) : update)
  ), []);
  const [catalogs, setCatalogs] = useState(() => initialEntry?.catalogs || null);
  const [matchClothing, setMatchClothing] = useState(() => initialEntry?.matchClothing || null);
  const [customMatchPromotionPending, setCustomMatchPromotionPending] = useState(() => {
    const initialProjectId = useAppStore.getState().projectId;
    const task = getCustomMatchPromotionTask(initialProjectId);
    return mannequinStageEnabled ? task?.status === 'pending' : Boolean(task);
  });
  const [colorOpts, setColorOpts] = useState(() => initialEntry?.colorOpts || []);
  const [detailColorOpts, setDetailColorOpts] = useState(() => initialEntry?.detailColorOpts || []);
  const [clothingType, setClothingType] = useState(() => initialEntry?.clothingType || 'top'); // 샷 필터 아이콘·예시 크롭용 (상의=위/하의=아래)
  const [exampleGender, setExampleGender] = useState(() => initialEntry?.exampleGender || null);
  // 선택한 모델 종류(real|virtual) — 이 콘티에서 고를 수 있는 컷의 범위를 정한다.
  const [identityKind, setIdentityKind] = useState(() => initialEntry?.identityKind || null);
  const [hasDetailImage, setHasDetailImage] = useState(() => initialEntry?.hasDetailImage || false);
  const [autosaveFailed, setAutosaveFailed] = useState(false);
  const [entryNotice, setEntryNotice] = useState(() => api.storyboardEntryNotice?.(useAppStore.getState().projectId));
  const [storyboardSeedContext, setStoryboardSeedContext] = useState(() => initialEntry?.storyboardSeedContext || ({
    colors: [],
    targetGenders: [],
    matchClothing: [],
  }));
  const [selectedId, setSelectedId] = useState(null);
  const [selectedViewId, setSelectedViewId] = useState(null);
  const [highlightedPosition, setHighlightedPosition] = useState(null);
  const [pendingRemoval, setPendingRemoval] = useState(null);
  const [swapNotice, setSwapNotice] = useState(null);
  const photoDragUntil = useRef(0);
  const flightCleanup = useRef(null);
  useEffect(() => () => flightCleanup.current?.(), []);
  const [drawer, setDrawer] = useState(null);
  const [activeSectionKey, setActiveSectionKey] = useState(null);
  const [overview, setOverview] = useState(false);
  const [dragExampleId, setDragExampleId] = useState(null);
  const [draggingSectionChoice, setDraggingSectionChoice] = useState(false);
  const [setPicker, setSetPicker] = useState(null);
  const [setPickerError, setSetPickerError] = useState(null);
  const [insertedSectionKey, setInsertedSectionKey] = useState(null);
  const sectionRefs = useRef(new Map());
  const designFocusId = drawer?.mode === 'design' ? blocks?.find(block => block.pageSectionKey === drawer.runKey)?.pageDesignId : null;
  useEffect(() => {
    if (drawer?.mode !== 'design' || !blocks) return undefined;
    if (!designFocusId) { setDrawer(null); return undefined; }
    setActiveSectionKey(drawer.runKey); setOverview(false);
    const frame = requestAnimationFrame(() => sectionRefs.current.get(drawer.runKey)?.scrollIntoView({ block: 'nearest', inline: 'nearest' }));
    return () => cancelAnimationFrame(frame);
  }, [drawer?.mode, drawer?.runKey, designFocusId]);
  const [loadError, setLoadError] = useState(null);
  const [loadRetry, setLoadRetry] = useState(0);
  const [mannequinFailure, setMannequinFailure] = useState(null);
  const mannequinRetryRef = useRef(false);
  const [atomicSaving, setAtomicSaving] = useState(false);
  const [undoEntry, setUndoEntry] = useState(null);
  const [undoExiting, setUndoExiting] = useState(false);
  const [initialBoardRevealed, setInitialBoardRevealed] = useState(() => prefersReducedMotion());
  const atomicSavingRef = useRef(false);
  const atomicRetryRef = useRef(null);
  const directSaveSnapshots = useRef(new WeakSet());
  const undoEntryRef = useRef(null);
  const undoTimerRef = useRef(null);
  const undoHoveredRef = useRef(false);
  const leaveDesignUndoRef = useRef(null);
  const designUndoVisible = drawer?.mode === 'design' && Boolean(undoEntry && !undoExiting);
  useEffect(() => {
    // A removed pane/status cannot emit mouseleave or blur. Resume the shared
    // expiry path before the next change, without resetting a still-visible hover.
    if (!designUndoVisible && undoHoveredRef.current) leaveDesignUndoRef.current?.();
  }, [designUndoVisible]);
  const defaultToast = useToast();
  const toast = toastOverride || defaultToast;
  const pushToast = toast.push;
  const countGenerationCuts = snapshot => uniqueGenerationCutCount(blocksForModel(snapshot, identityKind), {
    clothingType, frontClosure: catalogs?.frontClosure,
  });
  const generationCutCount = blocks == null ? null : countGenerationCuts(blocks);
  const unsupportedAiBlock = firstUnsupportedAiBlock(blocks, identityKind);
  const previousGenerationCutCount = useRef(null);
  useEffect(() => {
    if (generationCutCount == null) return;
    if (previousGenerationCutCount.current != null && crossedBelowDetailPageMinimum(
      previousGenerationCutCount.current, generationCutCount, CREDIT_COSTS.storyboardMinCuts,
    )) {
      pushToast(`AI 컷이 ${CREDIT_COSTS.storyboardMinCuts}컷보다 적으면 ${CREDIT_COSTS.storyboardMinCuts}컷 기준(${detailPageCreditCost(1)}크레딧)으로 차감돼요. ${CREDIT_COSTS.storyboardMinCuts}컷까지는 같은 값이에요.`);
    }
    previousGenerationCutCount.current = generationCutCount;
  }, [generationCutCount, pushToast]);
  const customMatchPromotionExpectedRef = useRef(location.state?.customMatchPromotionStarted === true);
  const customMatchPromotionHandledRef = useRef(new Set());
  const promotedMatchClothingRef = useRef({ projectId: null, items: null });
  const storyboardSeedContextRef = useRef(storyboardSeedContext);
  const projectId = useAppStore((s) => s.projectId);
  const account = useAppStore((s) => s.account);
  const [creditQuote, setCreditQuote] = useState(null);
  const detailPageQuote = creditQuote?.projectId === projectId ? creditQuote.quote : null;
  const creditQuotePending = !mannequinStageEnabled && creditQuote?.projectId !== projectId;
  const [customMatchPreparationFailed, setCustomMatchPreparationFailed] = useState(false);
  const [customMatchConfirmationOpen, setCustomMatchConfirmationOpen] = useState(false);
  const [creditShortfall, setCreditShortfall] = useState(null);
  const [creditResume, setCreditResume] = useState(() => storyboardCreditResume(location.state, mannequinStageEnabled));
  const detailPageSubmitRef = useMemo(() => ({ current: false }), [projectId]);
  const [detailPageSubmitting, setDetailPageSubmitting] = useState(false);
  const detailPageEntryLocked = useRef(!mannequinStageEnabled && Boolean(storyboardEntryState(initialEntryRef.current?.raw?.[0])?.done));
  const detailPageLifetimeRef = useRef(null);
  useEffect(() => {
    const lifetime = {};
    detailPageLifetimeRef.current = lifetime;
    setDetailPageSubmitting(false);
    return () => { if (detailPageLifetimeRef.current === lifetime) detailPageLifetimeRef.current = null; };
  }, [projectId]);
  const shopName = useStoryboardShopName(blocks);
  const doneBlocked = useDoneGuard();   // 생성 완료 후 초안 재진입 제한 (PRD §10.17)

  // 입력 화면이 시작한 완료 프라미스를 직접 구독한다. 서버를 반복 조회하지 않고 완료 시 한 번만
  // 최신 매칭 목록을 읽어 준비 타일을 실제 내 옷으로 자동 교체한다.
  useEffect(() => {
    if (!projectId) return undefined;
    let active = true;
    let observedTask = null;
    if (!mannequinStageEnabled) setCustomMatchPreparationFailed(false);
    const unsubscribe = subscribeCustomMatchPromotion(projectId, (task) => {
      if (!active) return;
      if (mannequinStageEnabled || !task) setCustomMatchPromotionPending(task?.status === 'pending');
      if (!task || task === observedTask) return;
      observedTask = task;
      if (!mannequinStageEnabled) {
        setCustomMatchPromotionPending(true);
        setCustomMatchPreparationFailed(false);
      }
      void task.promise.then(async (result) => {
        if (!active || (!mannequinStageEnabled && task !== observedTask)) return;
        if (mannequinStageEnabled) setCustomMatchPromotionPending(false);
        // 실패 알림은 태스크 층(onCustomMatchPromotionFailure)이 화면과 무관하게 띄운다 —
        // 셀러가 콘티를 곧바로 떠나도 안내가 사라지지 않게(리뷰 지적).
        if (result?.attempted && !result.promoted) {
          if (!mannequinStageEnabled) setCustomMatchPreparationFailed(true);
          return;
        }
        if (!result?.attempted) return;
        try {
          const refreshed = mannequinStageEnabled
            ? await api.getMatchClothing(projectId)
            : (await api.getAnalysis(projectId)).matchClothing;
          if (!active || (!mannequinStageEnabled && task !== observedTask)) return;
          const nextMatchClothing = refreshed || [];
          if (!mannequinStageEnabled && !nextMatchClothing.some(item => item.isCustom)) {
            setCustomMatchPreparationFailed(true);
            return;
          }
          promotedMatchClothingRef.current = { projectId, items: nextMatchClothing };
          setMatchClothing(nextMatchClothing);
          setStoryboardSeedContext((current) => ({ ...current, matchClothing: nextMatchClothing }));
          setBlocks((current) => applyPromotedMatchSelection(
            current,
            storyboardSeedContextRef.current.colors,
            nextMatchClothing,
          ));
        } catch {
          if (!active || (!mannequinStageEnabled && task !== observedTask)) return;
          if (mannequinStageEnabled) {
            // 완료 후 목록을 못 읽으면 기존 콘티 로드 경로를 재시도한다.
            setLoadRetry((current) => current + 1);
          } else {
            setCustomMatchPreparationFailed(true);
          }
        }
      }).catch(() => {
        if (active && !mannequinStageEnabled && task === observedTask) setCustomMatchPreparationFailed(true);
      }).finally(() => {
        if (active && (mannequinStageEnabled || task === observedTask)) setCustomMatchPromotionPending(false);
      });
    });
    return () => { active = false; unsubscribe(); };
  }, [projectId, pushToast]);

  // 승격 실패 안내 — 어느 화면이 떠 있든 프로젝트당 한 번. 콘티보드 언마운트로 유실되지 않는다.
  useEffect(() => onCustomMatchPromotionFailure(() => {
    pushToast('내 옷을 등록하지 못했어요. 다시 올리려면 새로 시작해 주세요.', { icon: 'alertCircle' });
  }), [pushToast]);

  // 새로고침·이탈로 메모리 프라미스가 유실된 경우 새 백엔드 인프라를 만들지 않는다. 이미 서버
  // 목록에 내 옷이 없다면 기존 실패 안내로 수렴해 새 작업에서 다시 올리도록 알려준다.
  useEffect(() => {
    if (!customMatchPromotionExpectedRef.current || !projectId || matchClothing === null) return;
    if (getCustomMatchPromotionTask(projectId)) return;
    if ((matchClothing || []).some((item) => item.isCustom)) return;
    if (customMatchPromotionHandledRef.current.has(projectId)) return;
    customMatchPromotionHandledRef.current.add(projectId);
    if (!mannequinStageEnabled) setCustomMatchPreparationFailed(true);
    pushToast('내 옷을 등록하지 못했어요. 다시 올리려면 새로 시작해 주세요.', { icon: 'alertCircle' });
  }, [matchClothing, projectId, pushToast]);

  useEffect(() => () => clearTimeout(undoTimerRef.current), []);
  useEffect(() => {
    if (!insertedSectionKey) return undefined;
    const section = sectionRefs.current.get(insertedSectionKey);
    if (!section) return undefined;
    const frame = requestAnimationFrame(() => section.scrollIntoView({
      block: 'nearest', behavior: 'instant',
    }));
    const timer = setTimeout(() => setInsertedSectionKey(null), prefersReducedMotion() ? 1000 : 1520);
    return () => { cancelAnimationFrame(frame); clearTimeout(timer); };
  }, [insertedSectionKey]);
  useLayoutEffect(() => { storyboardSeedContextRef.current = storyboardSeedContext; }, [storyboardSeedContext]);
  const v4CopyReady = entryNotice !== 'legacyGenerated' && Boolean(blocks?.length) && blocks.every(block => block.taxonomyVersion === 4);
  useStoryboardTheme(v4CopyReady);
  useEffect(() => {
    if (!v4CopyReady || (!mannequinStageEnabled && (detailPageEntryLocked.current || hasEditorEntered(projectId)))) return;
    void ensureV4StoryboardCopywriting(projectId, useAppStore.getState, { force: true }).catch(error => {
      pushToast('카피라이팅 설정을 저장하지 못했어요. 다시 시도해 주세요.', {
        variant: 'error', error, kind: 'save_error', actionName: 'save', messageCode: 'copywriting_save_failed', blocking: true });
    });
  }, [projectId, v4CopyReady, pushToast]);

  // 분석에서 넘어올 때 이전 화면의 스크롤이 남아 보드 중간부터 보이던 문제 — 진입 시 최상단.
  useLayoutEffect(() => { window.scrollTo(0, 0); }, []);

  const initialRevealReady = blocks !== null && catalogs !== null;
  useEffect(() => {
    if (!initialRevealReady) return undefined;
    if (prefersReducedMotion()) {
      setInitialBoardRevealed(true);
      return undefined;
    }

    let active = true;
    const urls = collectInitialRevealThumbnailUrls(
      renderGroups(blocks),
      (block) => initialRevealThumbnailFor(block, catalogs),
    );
    void waitForInitialReveal(urls).then(() => {
      if (active) setInitialBoardRevealed(true);
    });
    return () => { active = false; };
  }, [initialRevealReady]);

  // 훅은 로딩 early-return 위에 둔다(CLAUDE.md — 훅 개수 불변).
  const photoUploadProgress = usePhotoUploadProgress(projectId);
  const featurePreparationPending = useFeaturePreparation(projectId);
  const lateDetailCatalogRef = useRef(null);
  lateDetailCatalogRef.current ||= createLateDetailCatalog();
  useEffect(() => {
    if (!projectId || !isAnalysisFastEntryEnabled()) return undefined;
    const run = getActiveFeatureRun();
    if (!run) return undefined;
    let active = true;
    let revision = 0;
    const unsubscribe = run.subscribe((record) => {
      if (!active || run.getProjectId() !== projectId || !(record?.lateDetailRevision > revision)) return;
      revision = record.lateDetailRevision;
      void api.getAnalysis(projectId).then((analysis) => {
        if (!active || useAppStore.getState().projectId !== projectId) return;
        lateDetailCatalogRef.current.remember(projectId, analysis.detailRecommendations);
        setCatalogs((current) => lateDetailCatalogRef.current.merge(projectId, current));
      }).catch(() => {});
    });
    return () => { active = false; unsubscribe(); };
  }, [projectId]);

  useEffect(() => {
    // 사진 업로드 정착을 여기서 기다리므로(수십 초 가능) 언마운트 후 setState 를 막는 플래그가
    // 필요하다. 종전에는 즉시 끝나는 로드라 없어도 무방했다.
    let active = true;
    (async () => {
      let productPhotoPromotionObserved = false;
      let productPhotoRecoveryAttempted = false;
      const requestedProjectId = pidRef.current || useAppStore.getState().projectId;
      try {
        setLoadError(null);
        await useAppStore.getState().loadProject();
        const pid = useAppStore.getState().projectId;
        if (!pid) {
          if (requestedProjectId) setLoadError(storyboardNotFoundError());
          else navigate('/create/input', { replace: true });  // 콜드 진입(복원 불가) → 입력
          return;
        }
        pidRef.current = pid;   // 이 인스턴스의 저장 대상 고정 (프로젝트 경계)
        // ProductInput의 이탈 cleanup이 마지막 색상 PATCH를 막 시작했을 수 있다. 같은 project의
        // 저장만 기다린 뒤 생성/콘티 GET을 시작해, 빠른 브라우저 뒤로가기에서도 옛 색을 읽지 않는다.
        await waitForAnalysisEditSave(pid);
        // 확정 CTA 는 프로젝트 신원까지만 기다리고 넘어온다 — 상품 사진 업로드·저장은 여기서
        // 정착한다. 보드 시드가 상품 색상을 읽으므로(shapes.defaultStoryboard) 저장 전에 읽으면
        // 빈 상품으로 시드된다. 기다리는 동안 화면은 진행률을 보여준다(아래 photoUpload 상태).
        const photoPreparation = await resumeProductPhotoPromotionForStoryboard(pid);
        productPhotoPromotionObserved = photoPreparation.promotionObserved;
        productPhotoRecoveryAttempted = photoPreparation.recoveryAttempted;
        if (!active) return;
        // 업로드가 실패로 끝났으면 보드를 읽지 않는다 — 사진 없는 상품으로 시드된 잘못된 보드가
        // 굳는다. 복구는 **여기서** 한다: 실패 시점은 이미 확정 뒤라 입력 화면이 봉인돼 있어
        // (App.jsx entryDecision 'redirect' → 재진입 고집 시 start-new 가 draft 삭제)
        // 뒤로 보내는 안내는 갈 수 없는 곳을 가리킨다. 같은 draft 로 승격을 다시 돌리고,
        // 그래도 실패면 기존 '다시 시도' 오류 화면(loadRetry 루프)에 태운다.
        if (getProductPhotoPromotionTask(pid)?.status === 'failed') {
          const recovered = productPhotoRecoveryAttempted
            ? false
            : await retryProductPhotoPromotionFromDraft(pid);
          if (!active) return;
          if (!recovered) {
            setLoadError({
              kind: 'photoUpload',
              message: '사진 업로드를 끝내지 못했어요. 네트워크를 확인한 뒤 다시 시도해 주세요.',
            });
            return;
          }
        }
        // 마네킹컷 생성은 콘티 로드와 병렬로 돌리되, 재료인 내 옷 승격만은 먼저 정착시킨다.
        // fail-open 결과도 resolve되므로 등록 실패가 마네킹 생성을 막지는 않는다.
        const customMatchTask = getCustomMatchPromotionTask(pid);
        const customMatchReady = customMatchTask?.promise.catch(() => null) || Promise.resolve();
        await sbSaveIdle();     // 직전 인스턴스의 비행 중 저장(이탈 플러시)이 착지한 뒤에 읽는다 — 스테일 로드 방지
        const entry = await consumeStoryboardEntry(pid) || await loadStoryboardEntry(pid);
        if (!mannequinStageEnabled && (!active || useAppStore.getState().projectId !== pid)) return;
        const [board] = entry;
        const persistedEntry = !mannequinStageEnabled && storyboardEntryState(board);
        const entryLocked = !mannequinStageEnabled && (persistedEntry?.done || hasEditorEntered(pid));
        if (!mannequinStageEnabled && active) detailPageEntryLocked.current = Boolean(entryLocked);
        const needsInitialSave = !mannequinStageEnabled && persistedEntry?.saved === false;
        const notice = api.storyboardEntryNotice?.(pid);
        if (active) setEntryNotice(notice);
        if (mannequinStageEnabled && notice !== 'rebuilt' && notice !== 'legacyGenerated') {
          void customMatchReady.then(() => isAnalysisFastEntryEnabled()
            ? (active ? requestMannequinGeneration(pid, { isActive: () => active }) : null)
            : requestMannequinGeneration(pid)).catch((error) => {
            if (active) setMannequinFailure(error?.message || '서버 연결이 불안정해요. 잠시 후 다시 시도해 주세요.');
          });
        }

        // 직전 이탈 저장 실패분 복원 — 단, "서버가 우리가 마지막으로 알던 상태 그대로"일 때만.
        // 서버가 변했다면 다른 탭/기기의 더 새로운 저장이므로 보관분을 폐기하고 서버본을 따른다(침묵 덮어쓰기 금지).
        let pending = sbPending.get(pid);
        const baseline = sbLastSaved.get(pid);
        // 1순위: 보관분이 서버와 내용 동일 = '실패'로 기록됐지만 실제로 착지했던 저장(응답 유실).
        //        기준선 일치 여부와 무관하게 최우선 정리 — 안 하면 불필요한 복원·재저장 루프에 빠진다.
        if (pending && sbStable(board) === sbStable(pending)) { sbPending.delete(pid); pending = null; }
        const serverUnchanged = baseline != null && sbStable(board) === sbStable(baseline);
        const usePending = !!pending && serverUnchanged;
        if (pending && !usePending) {
          // 진짜 충돌(서버가 보관분·기준선과 다른 제3의 내용) — 폐기하되 침묵하지 않는다
          sbPending.delete(pid);
          toast.push('다른 곳에서 저장된 최신 콘티를 불러왔어요 — 이전에 저장 못 한 변경은 반영되지 않았어요');
        }
        if (notice === 'rebuilt' || needsInitialSave) sbLastSaved.delete(pid); // 화면에서 만든 보드는 아직 서버 저장본이 아니다.
        else if (!usePending) sbLastSaved.set(pid, board);   // 이번 로드의 서버 상태를 기준선으로 기록
        const reuseInitialEntry = shouldReuseInitialStoryboardEntry({
          usePending,
          promotionObserved: productPhotoPromotionObserved,
          initialEntry: initialEntryRef.current,
          projectId: pid,
          entry,
        });
        const prepared = reuseInitialEntry
          ? initialEntryRef.current.prepared
          : prepareStoryboardEntry(entry, usePending ? pending : board, pid);
        const promotedMatchClothing = promotedMatchClothingRef.current.projectId === pid
          ? promotedMatchClothingRef.current.items
          : null;
        const initBlocks = promotedMatchClothing
          ? applyPromotedMatchSelection(
            prepared.blocks,
            prepared.storyboardSeedContext.colors,
            promotedMatchClothing,
          )
          : prepared.blocks;
        const promotionChangedBlocks = initBlocks !== prepared.blocks;

        if (!reuseInitialEntry) {
          setBlocks(initBlocks);
          setCatalogs(lateDetailCatalogRef.current.merge(pid, prepared.catalogs));
          setMatchClothing(promotedMatchClothing || prepared.matchClothing);
          setClothingType(prepared.clothingType);
          setExampleGender(prepared.exampleGender);
          setIdentityKind(prepared.identityKind || null);
          setHasDetailImage(prepared.hasDetailImage);
          setDetailColorOpts(prepared.detailColorOpts);
          setColorOpts(prepared.colorOpts);
          setStoryboardSeedContext(promotedMatchClothing
            ? { ...prepared.storyboardSeedContext, matchClothing: promotedMatchClothing }
            : prepared.storyboardSeedContext);
        }
        // 승격은 사진 뒤에 분석도 저장한다. 그 정착을 기다린 이 자리에서만 모델 견적을 읽는다.
        if (!mannequinStageEnabled && active && !entryLocked) {
          void loadStoryboardCreditQuote({ mannequinStageEnabled, projectId: pid, getCreditQuote: api.getCreditQuote })
            .then(quote => {
              if (active && useAppStore.getState().projectId === pid) setCreditQuote({ projectId: pid, quote });
            });
        }
        if (!entryLocked && notice !== 'legacyGenerated' && notice !== 'rebuilt' && (prepared.normalized || prepared.assignment.changed
          || usePending || promotionChangedBlocks || needsInitialSave)) {
          const autoAssignmentOnly = (needsInitialSave || prepared.assignment.assignedIds.length > 0)
            && prepared.assignment.protectedIds.length === 0 && !prepared.normalized
            && !usePending && !promotionChangedBlocks;
          try {
            await sbSaveNow(pid, () => (
              !mannequinStageEnabled && hasEditorEntered(pid) ? null : initBlocks
            ), { autoAssignment: autoAssignmentOnly, skipUnchanged: !mannequinStageEnabled });
          } catch {
            // 초기 정규화·자동 배정도 background save다. persistence scheduler가 조용히 재시도한다.
          }
        }
      } catch (error) {
        if (active) setLoadError(classifyStoryboardLoadError(error, STORYBOARD_NETWORK_ERROR_MESSAGE));
      }
    })();
    return () => { active = false; };
  }, [loadRetry]);
  /* 보드 썸네일 선캐싱 — 트리거 ① 보드 내용이 확정/변경될 때마다(진입·컷 추가·예시 교체).
     이미 데운 URL은 모듈 캐시가 걸러내므로 재실행이 중복 요청을 만들지 않는다. */
  useEffect(() => {
    if (!blocks || !catalogs) return undefined;
    const blockImages = blocks.flatMap((block) => [
      block.exampleId
        // 시그니처 컷은 카탈로그에 없다 — 조회가 비면 block.thumb 로 떨어져야 첫 화면 카드가
        // 늦게 뜨지 않는다(이 카드가 보드에서 가장 먼저 보이는 자리다).
        ? (generationExampleImageSources(
          (catalogs.genExamples || []).find((example) => example.id === block.exampleId),
        ).prewarm || block.thumb)
        : block.thumb,
      block.ownImages?.[0],
    ]);
    const spaceSetThumbs = blocks.flatMap((block) => (
      inferStoryboardSpaceSet(block.spaceGroupId)?.members
        .map((member) => member.thumb || member.thumbUrl) || []
    ));
    return prewarmImages([...blockImages, ...spaceSetThumbs]);
  }, [blocks, catalogs]);

  // 평상시엔 10초 단위로 느긋하게 저장하고, 이탈 시에는 아래 lifecycle flush가 즉시 보강한다.
  const saveTimer = useRef(null);
  const latestBlocks = useRef(null);
  const pidRef = useRef(null);   // 이 인스턴스가 로드한 프로젝트 — 플러시가 스토어의 "현재" id(새 프로젝트로 바뀌었을 수 있음)를 쓰지 않게 고정
  const conflictNoticeProject = useRef(null);
  const showStoryboardConflict = (pid, copy) => {
    const restore = restoreWorkCopyOnce(async () => {
      if (pidRef.current !== pid) return;
      const kept = copy.blocks;
      directSaveSnapshots.current.add(kept);
      latestBlocks.current = kept;
      setBlocks(kept);
      try {
        if (api.storyboardEntryNotice?.(pid) !== 'rebuilt') await sbSaveNow(pid, () => kept, { restoreConflict: true });
        storyboardWorkCopies.clear(pid, copy);
        setAutosaveFailed(false);
      } catch (error) { setAutosaveFailed(error); }
    });
    pushToast('다른 곳에서 먼저 저장해서 최신 내용을 불러왔어요.', { action: {
      label: '이 화면 내용으로 되돌리기',
      onClick: () => { if (!detailPageSubmitRef.current) restore(); },
    } });
  };
  useEffect(() => {
    sbSetConflictHandler((pid, copy, server) => {
      if (pidRef.current !== pid) return;
      clearTimeout(saveTimer.current);
      const kept = storyboardWorkCopies.keep(pid, { blocks: latestBlocks.current || copy.blocks });
      latestBlocks.current = server;
      directSaveSnapshots.current.add(server);
      setBlocks(server);
      setAutosaveFailed(false);
      conflictNoticeProject.current = pid;
      showStoryboardConflict(pid, kept);
    });
    return () => sbSetConflictHandler(null);
  }, [pushToast]);
  useEffect(() => {
    if (!blocks || pidRef.current !== projectId || conflictNoticeProject.current === projectId) return;
    const copy = storyboardWorkCopies.read(projectId);
    if (!copy) return;
    conflictNoticeProject.current = projectId;
    showStoryboardConflict(projectId, copy);
  }, [blocks, projectId, pushToast]);
  const flushLatest = (pid, options = {}) => {
    clearTimeout(saveTimer.current);
    saveTimer.current = null;
    if (!mannequinStageEnabled && (detailPageEntryLocked.current || hasEditorEntered(pid))) return Promise.resolve();
    if (api.storyboardEntryNotice?.(pid) === 'legacyGenerated') return Promise.resolve();
    if (api.storyboardEntryNotice?.(pid) === 'rebuilt' && !options.confirmRebuilt) return Promise.resolve();
    const { confirmRebuilt: _confirmRebuilt, ...saveOptions } = options;
    if (!mannequinStageEnabled) saveOptions.skipUnchanged = true;
    return sbSaveNow(pid, () => latestBlocks.current, saveOptions);
  };
  const saveNow = async (pid) => {
    const saved = await flushLatest(pid, { confirmRebuilt: true });
    if (api.storyboardEntryNotice?.(pid) === 'rebuilt') {
      setStoryboardEntryNotice(pid, null);
      setEntryNotice(null);
    }
    return saved;
  };
  // 저장이 다시 성공하면 배너를 거둔다.
  const retryAutosave = () => flushLatest(projectId)
    .then(() => setAutosaveFailed(false))
    .catch((error) => setAutosaveFailed(error?.code === 'revision_conflict' ? false : error));
  useLayoutEffect(() => {
    latestBlocks.current = blocks;
  }, [blocks]);
  const sbSkipFirstSave = useRef(true);
  useEffect(() => {
    if (blocks == null || !projectId) return;
    if (sbSkipFirstSave.current) { sbSkipFirstSave.current = false; return; }  // 최초 로드분은 저장 생략(불필요 dirty 방지)
    if (directSaveSnapshots.current.has(blocks)) {
      directSaveSnapshots.current.delete(blocks);
      return;
    }
    if (entryNotice === 'legacyGenerated' || entryNotice === 'rebuilt') return undefined;
    return scheduleStoryboardAutosave(saveTimer, () => {
      // 자동저장 실패를 침묵시키지 않는다 — 서버가 4xx 로 거절하면 재전송도 없어서
      // 그 세션의 저장이 통째로 죽는데 화면엔 아무 표시가 없었다(자체 리뷰 high 의 2차 피해).
      flushLatest(projectId)
        .then(() => setAutosaveFailed(false))     // 다시 성공하면 배너를 거둔다(오경보 방지)
        .catch((error) => setAutosaveFailed(error?.code === 'revision_conflict' ? false : error));
    });
  }, [blocks, projectId]);
  // hidden/pagehide/unmount 모두 같은 latest snapshot을 keepalive로 직렬 체인에 enqueue한다.
  // 겹쳐 발화해도 sbLastSaved identity 비교가 뒤따르는 중복 PUT을 흡수한다.
  useEffect(() => bindStoryboardExitFlush({
    getProjectId: () => pidRef.current,
    flushLatest,
  }), []);
  // 저장이 4xx로 거절되면 persistence가 이 훅으로 스냅샷 복구를 요청한다 — 진입 정규화와
  // 같은 낡은 선택 제거에 더해, 서버가 meta.exampleId 로 지목한 선택(발행 회전 직후
  // 클라이언트 카탈로그로는 유효해 보이는 스큐 케이스)을 걷어내고 재배정해 즉시 재저장한다.
  // 같은 스냅샷을 그대로 다시 보내는 맹목 재시도는 4xx에선 영원히 같은 답이라 하지 않는다.
  const saveRepairContext = useRef(null);
  useEffect(() => {
    saveRepairContext.current = {
      catalogs,
      clothingType,
      targetGenders: storyboardSeedContext.targetGenders,
      identityKind,
    };
  }, [catalogs, clothingType, storyboardSeedContext, identityKind]);
  useEffect(() => {
    sbSetSaveRepair((pid, snapshot, error) => {
      const ctx = saveRepairContext.current;
      if (pid !== pidRef.current || !ctx?.catalogs || !Array.isArray(snapshot)) return null;
      const staleFamily = new Set([
        'unknown_example_id', 'example_not_applicable', 'example_cut_mismatch', 'example_gender_mismatch',
      ]);
      if (!staleFamily.has(error?.code)) return null;
      const gender = genderForClothingType(ctx.clothingType, ctx.targetGenders);
      let next = stripStaleSpaceSetBindings(snapshot, { gender, clothingType: ctx.clothingType });
      next = stripStaleExampleSelections(next, ctx.catalogs.genExamples, { gender, clothingType: ctx.clothingType });
      const rejectedId = error?.meta?.exampleId;
      if (rejectedId) next = stripExampleSelectionsById(next, rejectedId);
      if (next === snapshot) return null;   // 고칠 것을 못 찾음 — 무한 재저장 방지
      next = assignGenerationExamples(next, {
        catalog: ctx.catalogs.genExamples,
        product: { clothingType: ctx.clothingType },
        analysis: ctx.catalogs.analysis,
        gender,
        identityKind: ctx.identityKind || null,
      }).blocks;
      // 화면도 복구본으로 교체 — 이후 편집·자동 저장이 오염본을 다시 보내지 않게.
      if (latestBlocks.current === snapshot) {
        directSaveSnapshots.current.add(next);
        setBlocks(next);
      }
      return next;
    });
    return () => sbSetSaveRepair(null);
  }, []);
  const detailPreviewBlocks = useMemo(() => [...(blocks || []), ...sectionRuns(blocks || []).filter(run => run.kind === 'points')
    .flatMap(run => stageAdditions(blocks, run.key, { product: catalogs?.detailProduct, analysis: catalogs?.analysis }))
    .flatMap(item => item.block ? [item.block] : [])], [blocks, catalogs]);
  const detailPreviewDimensions = useDetailPreviewDimensions(detailPreviewBlocks, catalogs);
  const previewCatalogs = useMemo(() => catalogs && ({ ...catalogs, detailPreviewDimensions,
    shopName }), [catalogs, detailPreviewDimensions, shopName]);
  const styleSaves = useMemo(() => createStoryboardStyleSaveQueue(style => saveStoryboardPageStyle(projectId, style), { projectId }), [projectId]);
  const styleRequest = useRef(0);
  const selectedPageStyle = analysisPageStyle(catalogs?.analysis?.pageStyle, blocks);
  const styleValueRef = useRef(selectedPageStyle);
  useLayoutEffect(() => { styleValueRef.current = selectedPageStyle; styleSaves.seed(selectedPageStyle); }, [projectId, selectedPageStyle, styleSaves]);
  const [pageStyleError, setPageStyleError] = useState('');
  useEffect(() => { styleRequest.current++; setPageStyleError(''); }, [projectId]);
  const changePageStyle = changes => {
    const next = changes.fontStyle ? selectFontStyle(styleValueRef.current, changes.fontStyle)
      : selectColorModeStyle(styleValueRef.current, changes.colorMode);
    styleValueRef.current = next;
    const request = ++styleRequest.current;
    setPageStyleError('');
    setCatalogs(current => ({ ...current, analysis: { ...current.analysis, pageStyle: next } }));
    // Prefetched controls are interactive before pidRef binds the board loader.
    // Use the live project boundary for style replies, including the first failure.
    void styleSaves.save(next).then(() => {
      if (request !== styleRequest.current || projectId !== useAppStore.getState().projectId) return;
      const saved = styleSaves.confirmed();
      styleValueRef.current = saved;
      setCatalogs(current => ({ ...current, analysis: { ...current.analysis, pageStyle: saved } }));
    }).catch(() => {
      if (request !== styleRequest.current || projectId !== useAppStore.getState().projectId) return;
      const saved = styleSaves.confirmed();
      styleValueRef.current = saved;
      setCatalogs(current => ({ ...current, analysis: { ...current.analysis, pageStyle: saved } }));
      setPageStyleError('페이지 스타일을 저장하지 못했어요. 다시 골라 주세요.');
    });
  };
  const tone = resolvePageTokens(selectedPageStyle, baseColorOf(catalogs?.detailProduct).swatchId);
  const pagePreview = useMemo(() => {
    if (!blocks || !catalogs || entryNotice === 'legacyGenerated') return [];
    return assembleStoryboardPreview({ storyboard: blocks, product: catalogs.detailProduct,
      analysis: catalogs.analysis, pageStyle: selectedPageStyle, catalogs: previewCatalogs });
  }, [blocks, catalogs, previewCatalogs, entryNotice]);
  if (loadError) return (
    <div className="wizard wide"><div className="surface sb-load-error">
      <div>{loadError.message}</div>
      {loadError.kind === 'notFound' ? (
        <button type="button" className="btn btn-primary" onClick={() => navigate('/library')}>보관함으로 이동</button>
      ) : (
        <button type="button" className="btn btn-primary" onClick={() => setLoadRetry((value) => value + 1)}>다시 시도</button>
      )}
    </div></div>
  );
  // 옛 방식으로 이미 만든 페이지: 404와 같은 옷 태그 그림 가족으로 빈 화면을 채우고, 갈 곳 두 개를 준다.
  if (entryNotice === 'legacyGenerated') return <section className={notFoundStyles.page} aria-labelledby="sb-legacy-title">
    <HangTag label="OPEN IN EDITOR 라고 적힌 옷 태그">
      <g className={notFoundStyles.stroke}>
        <rect x="118" y="114" width="44" height="30" rx="3" />
        <path d="M126 124 H154 M126 131 H148 M126 138 H140" />
      </g>
      <text className={`${notFoundStyles.mono} ${notFoundStyles.accentFill}`} x="140" y="164" textAnchor="middle" fontSize="7.6" letterSpacing="1.6">OPEN IN EDITOR</text>
    </HangTag>
    <p className={notFoundStyles.eyebrow}>EARLIER VERSION</p>
    <h1 id="sb-legacy-title" className={notFoundStyles.title}>예전 방식으로 만든 페이지예요</h1>
    <p className={notFoundStyles.desc}>에디터에서 이어서 고칠 수 있어요.</p>
    <div className={`${notFoundStyles.actions} modal-actions sb-legacy-actions`}>
      <button type="button" className="btn btn-primary btn-block" onClick={() => navigate(`/editor/${projectId}`)}>에디터 열기</button>
      <button type="button" className="btn btn-block" onClick={() => navigate('/library')}>보관함으로</button>
    </div>
  </section>;
  if (!blocks || !catalogs) return <StoryboardLoadingState photoUpload={photoUploadProgress} featurePending={featurePreparationPending} />;

  const selected = blocks.find((b) => b.id === selectedId);
  const finishUndoDismiss = () => {
    setUndoExiting(false);
    setUndoEntry(null);
  };
  const dismissUndo = () => {
    clearTimeout(undoTimerRef.current);
    if (!undoEntryRef.current) return;
    undoEntryRef.current = null;
    if (prefersReducedMotion()) finishUndoDismiss();
    else setUndoExiting(true);
  };
  const scheduleUndoDismiss = (entry, delay = UNDO_WINDOW_MS) => {
    clearTimeout(undoTimerRef.current);
    entry.remainingMs = delay;
    if (undoHoveredRef.current) {
      entry.deadline = null;
      return;
    }
    entry.deadline = Date.now() + delay;
    undoTimerRef.current = setTimeout(() => {
      if (undoEntryRef.current === entry) dismissUndo();
    }, delay);
  };
  const showUndo = (previous, next, { blockId, label = '설정', standalone = false, message: undoMessage = null } = {}) => {
    if (!previous || !next || previous === next) return;
    setUndoExiting(false);
    const now = Date.now();
    const current = undoEntryRef.current;
    const active = !standalone && !current?.standalone ? current : null;
    const before = active ? active.before : [...previous];
    const labels = active ? [...new Set([...active.labels, label])] : [label];
    const operationCount = (active?.operationCount || 0) + 1;
    const message = `${operationCount}건 변경`;
    const entry = {
      before, after: next, blockId, labels, operationCount, updatedAt: now,
      message: undoMessage || message, standalone,
      previousEntry: active ? active.previousEntry : current,
    };
    undoEntryRef.current = entry;
    setUndoEntry(entry);
    scheduleUndoDismiss(entry);
  };
  const clearUndoForSnapshot = (snapshot) => {
    if (undoEntryRef.current?.after === snapshot) dismissUndo();
  };
  const undoLatest = () => {
    if (detailPageSubmitRef.current) return;
    const entry = undoEntryRef.current;
    if (!entry) return;
    setBlocks(entry.before);
    if (setPicker?.mode === 'replace') {
      const anchor = blocks.find((block) => block.spaceGroupId === setPicker.spaceGroupId);
      const restored = entry.before.find((block) => block.id === anchor?.id);
      if (restored?.spaceGroupId) setSetPicker((current) => current ? { ...current, spaceGroupId: restored.spaceGroupId } : current);
      else { setSetPicker(null); setDrawer(null); setSelectedId(null); }
    }
    setInsertedSectionKey(null);
    if (entry.previousEntry) {
      undoEntryRef.current = entry.previousEntry;
      setUndoExiting(false);
      setUndoEntry(entry.previousEntry);
      scheduleUndoDismiss(entry.previousEntry);
    } else dismissUndo();
    toast.push('변경을 되돌렸어요', { icon: 'undo' });
  };
  const enterUndo = () => {
    undoHoveredRef.current = true;
    const entry = undoEntryRef.current;
    if (!entry) return;
    entry.remainingMs = entry.deadline == null ? entry.remainingMs || UNDO_WINDOW_MS : Math.max(1, entry.deadline - Date.now());
    entry.deadline = null; clearTimeout(undoTimerRef.current);
  };
  const leaveUndo = () => { undoHoveredRef.current = false; const entry = undoEntryRef.current; if (entry) scheduleUndoDismiss(entry, entry.remainingMs || UNDO_WINDOW_MS); };
  leaveDesignUndoRef.current = leaveUndo;
  const patch = (id, changes, { undoLabel = null } = {}) => {
    if (atomicSavingRef.current) return;
    const previous = blocks;
    const current = previous.find((block) => block.id === id);
    if (!current) return;
    const applied = preserveDetailTargetBinding(current, typeof changes === 'function' ? changes(current) : changes);
    let next = patchStoryboardPhoto(previous, id, applied, catalogs.detailProduct);
    next = ensureContiguousSpaceRuns(next);
    // 내부 생성 레시피가 복구돼 placeholder가 생성 대상이 되면 레이아웃 배타 규칙을 다시 적용한다.
    if (applied && 'cutType' in applied && applied.cutType && !current.cutType) next = normalizeStoryboardMutation(next);
    setBlocks(next);
    showUndo(previous, next, { blockId: id, label: undoLabel || undoLabelForPatch(applied) });
  };
  const atomicPatch = async (id, changes, { retryAtomic = false, pickerOwnsError = false, undoLabel = null } = {}) => {
    if (atomicSavingRef.current) throw new Error('storyboard_atomic_save_in_progress');
    atomicSavingRef.current = true;
    setAtomicSaving(true);
    const previous = blocks;
    changes = preserveDetailTargetBinding(previous.find((block) => block.id === id), changes);
    const next = normalizeStoryboardMutation(patchStoryboardPhoto(previous, id, changes, catalogs.detailProduct));
    showUndo(previous, next, { blockId: id, label: undoLabel || undoLabelForPatch(changes) });
    directSaveSnapshots.current.add(next);
    setBlocks(next);
    try {
      if (api.storyboardEntryNotice?.(projectId) !== 'rebuilt') await sbSaveNow(projectId, () => next);
      atomicRetryRef.current = null;
    } catch (error) {
      if (error?.code === 'revision_conflict') throw error;
      if (sbPending.get(projectId) === next) sbPending.delete(projectId);
      atomicRetryRef.current = retryAtomic ? { previous, next } : null;
      directSaveSnapshots.current.add(previous);
      setBlocks(previous);
      clearUndoForSnapshot(next);
      throw error;
    } finally {
      atomicSavingRef.current = false;
      setAtomicSaving(false);
    }
  };
  const atomicBoardChange = async (buildNext, { nextSelectedId = null, undoBlockId = null, undoLabel = '설정' } = {}) => {
    if (atomicSavingRef.current) throw new Error('storyboard_atomic_save_in_progress');
    atomicSavingRef.current = true;
    setAtomicSaving(true);
    const previous = blocks;
    const built = buildNext(previous);
    const reconciled = reconcileSectionDependencies(previous, built, catalogs.detailProduct,
      { cropRegions: wornCropRegions[clothingType] || wornCropRegions.top });
    const next = reconcileSimilarCutMutation(previous, normalizeStoryboardMutation(reconciled));
    showUndo(previous, next, { blockId: undoBlockId || nextSelectedId || 'multi', label: undoLabel });
    directSaveSnapshots.current.add(next);
    setBlocks(next);
    try {
      if (api.storyboardEntryNotice?.(projectId) !== 'rebuilt') await sbSaveNow(projectId, () => next);
      atomicRetryRef.current = null;
      if (nextSelectedId) {
        setSelectedId(nextSelectedId);
      }
      return next;
    } catch (error) {
      if (error?.code === 'revision_conflict') throw error;
      if (sbPending.get(projectId) === next) sbPending.delete(projectId);
      atomicRetryRef.current = null;
      directSaveSnapshots.current.add(previous);
      setBlocks(previous);
      clearUndoForSnapshot(next);
      throw error;
    } finally {
      atomicSavingRef.current = false;
      setAtomicSaving(false);
    }
  };
  const closeInspector = () => {
    if (atomicSavingRef.current) return;
    setDrawer(null); setSetPicker(null); setSetPickerError(null); setSelectedId(null); setSelectedViewId(null);
  };
  const selectCard = (id, viewId = null, displaySectionKey = null) => {
    if (atomicSavingRef.current || Date.now() < photoDragUntil.current) return;
    const block = blocks.find(item => item.id === id);
    if (!block) return;
    setSelectedId(id); setSelectedViewId(viewId); setActiveSectionKey(displaySectionKey || block.pageSectionKey); setOverview(false);
    setSetPicker(null); setSetPickerError(null); setDrawer({ mode: 'photo' });
  };
  const openSetPicker = picker => {
    if (atomicSavingRef.current) return;
    setSetPickerError(null); setSetPicker(picker); setDrawer({ mode: 'set' });
  };
  const addSimilar = id => {
    const picked = blocks.find(block => block.id === id);
    const source = picked?.similarSourceBlockId ? blocks.find(block => block.id === picked.similarSourceBlockId) : picked;
    if (!canPlanSimilarCut(source) || blocks.filter(block => block.pageSectionKey === source.pageSectionKey && block.source !== 'text').length >= 12) return;
    const child = createSimilarCutBlock(source, uid('blk'));
    let end = blocks.indexOf(source);
    blocks.forEach((block, index) => { if (block.similarSourceBlockId === source.id) end = Math.max(end, index); });
    const next = [...blocks]; next.splice(end + 1, 0, child);
    const normalized = normalizeStoryboardMutation(next);
    setBlocks(normalized); showUndo(blocks, normalized, { blockId: child.id, label: '비슷한 컷 추가' });
    setSelectedId(child.id);
  };
  const remove = id => {
    if (atomicSavingRef.current) return;
    const next = removeStoryboardPhoto(blocks, id, catalogs.detailProduct);
    if (next === blocks) return;
    setBlocks(next); showUndo(blocks, next, { blockId: id, label: '컷 빼기' });
    if (blocks.some(block => block.similarSourceBlockId === id)) toast.push('원본과 연결된 비슷한 컷을 함께 삭제했어요');
    if (!next.some(block => block.id === selectedId)) closeInspector();
  };
  const addOptionalPhoto = async item => {
    if (atomicSavingRef.current || !activeRun) return;
    try {
      await atomicBoardChange(current => addStageAddition(current, activeRun.key, item,
        { product: catalogs.detailProduct, analysis: catalogs.analysis }), { undoLabel: `${item.label} 넣기` });
    } catch { toast.push('컷을 저장하지 못했어요. 다시 시도해주세요.'); }
  };
  const removeOptionalPhoto = async key => {
    if (atomicSavingRef.current) return;
    try {
      await atomicBoardChange(current => removePairedPhoto(current, key, catalogs.detailProduct), { undoLabel: '컷 빼기' });
      closeInspector();
    } catch { toast.push('컷을 저장하지 못했어요. 다시 시도해주세요.'); }
  };
  const chooseSpaceSet = async set => {
    if (!setPicker || atomicSavingRef.current) return;
    const run = sectionRuns(blocks).find(item => item.sectionId === setPicker.targetSid);
    if (!run) return;
    if (setPicker.mode !== 'replace' && (run.kind !== 'wear' || wearUsageForBlock(run.blocks.find(block => block.source !== 'text') || run.blocks[0]) !== 'styling')) return;
    const currentMembers = setPicker.spaceGroupId ? blocks.filter(block => block.spaceGroupId === setPicker.spaceGroupId) : [];
    if (setPicker.mode === 'replace' ? !canReplaceStoryboardSet(blocks, setPicker.spaceGroupId, set)
      : run.blocks.filter(block => block.source !== 'text').length + set.members.length > 12) {
      setSetPickerError('이 세트를 넣으면 12컷을 넘어요. 컷을 먼저 빼주세요.'); return;
    }
    if (setPicker.mode === 'replace' && inferStoryboardSpaceSet(setPicker.spaceGroupId)?.id === set.id) return;
    const groupId = spaceSetGroupId(set.id, uid('sg'));
    const ids = set.members.map((_member, index) => currentMembers.filter(block => !block.similarSourceBlockId)[index]?.id || uid('blk'));
    let removedSimilarCount = 0;
    setSetPickerError(null);
    try {
      await atomicBoardChange(current => {
        const host = run.blocks.find(block => block.source === 'ai') || run.blocks[0];
        const next = setPicker.mode === 'replace'
          ? replaceSpaceSetRun(current, setPicker.spaceGroupId, set, { spaceGroupId: groupId, makeId: (_member, index) => ids[index], setSelectionOrigin: 'user' })
          : insertSpaceSet(current, run.end, set, host, { spaceGroupId: groupId, makeId: (_member, index) => ids[index], setSelectionOrigin: 'user' });
        removedSimilarCount = current.filter(block => block.similarSourceBlockId && !next.some(item => item.id === block.id)).length;
        return reconcileHorizonGroupBackground(next, groupId, catalogs.detailProduct, catalogs.garmentColorEvidence, set.id);
      }, { nextSelectedId: ids[0], undoLabel: setPicker.mode === 'replace' ? '장소 바꾸기' : '장소 세트 추가' });
      setSetPicker({ ...setPicker, mode: 'replace', spaceGroupId: groupId });
      if (removedSimilarCount) toast.push(`원본과 연결된 비슷한 컷 ${removedSimilarCount}컷도 함께 정리했어요`);
    } catch { setSetPickerError('장소 세트를 저장하지 못했어요. 다시 시도해주세요.'); }
  };
  const locked = false;
  const commitSectionRemoval = (key, impact) => {
    const previous = blocks, next = impact.nextBlocks;
    if (next === previous) return;
    setPendingRemoval(null); setBlocks(next);
    const name = templateSectionName(previous.find(block => block.pageSectionKey === key));
    showUndo(previous, next, { label: '섹션 빼기', standalone: true, message: `${name} 섹션을 뺐어요` });
    if (activeSectionKey === key || drawer?.mode === 'design' || !next.some(block => block.id === selectedId)) closeInspector();
  };
  const removeSection = key => {
    if (locked || atomicSavingRef.current) return;
    const impact = sectionRemovalImpact(blocks, key, { product: catalogs.detailProduct });
    if (impact.nextBlocks === blocks) return;
    if (impact.confirmationRequired) setPendingRemoval({ key, previous: blocks, impact });
    else commitSectionRemoval(key, impact);
  };
  const addSection = (kind, usage, designId, beforeSectionKey = automaticSectionInsertionKey(blocks, kind, usage), example = null, flightSource = null) => {
    if (locked || atomicSavingRef.current) return;
    const flight = captureSectionPreview(flightSource);
    const previous = blocks;
    const next = addTemplateSection(previous, catalogs.detailProduct, catalogs.analysis || {}, kind, usage,
      { projectId, identityKind, gender: exampleGender, catalog: catalogs.genExamples, beforeSectionKey, designId, example });
    if (next === previous) return;
    const added = next.filter(block => !previous.some(old => old.id === block.id));
    const assigned = assignGenerationExamples(next, { catalog: catalogs.genExamples, product: catalogs.detailProduct,
      analysis: catalogs.analysis, gender: exampleGender, identityKind, projectId, onlyBlockIds: added.map(block => block.id) }).blocks;
    setBlocks(assigned);
    const run = added[0];
    const name = templateSectionName(run);
    showUndo(previous, assigned, { label: '섹션 추가', standalone: true,
      message: `${name} 섹션을 넣었어요` });
    setInsertedSectionKey(run.pageSectionKey); setActiveSectionKey(run.pageSectionKey); setOverview(false); setSelectedId(null); setSelectedViewId(null);
    setSetPicker(null); setDrawer({ mode: 'add' });
    if (flightSource) requestAnimationFrame(() => {
      const row = sectionRefs.current.get(run.pageSectionKey);
      row?.scrollIntoView({ block: 'nearest' });
      flightCleanup.current?.(); flightCleanup.current = flySectionPreview(flight, row?.querySelector('[role="button"]'));
    });
  };
  const moveSection = (key, beforeKey, focusAction = 'handle') => {
    if (atomicSavingRef.current) return;
    const next = moveTemplateSection(blocks, key, beforeKey);
    if (next === blocks) return;
    setBlocks(next); showUndo(blocks, next, { label: '섹션 이동', standalone: true, message: '섹션 순서를 바꿨어요' });
    requestAnimationFrame(() => {
      const row = sectionRefs.current.get(key);
      row?.querySelector(`[data-move-action="${focusAction}"]`)?.focus({ preventScroll: true });
      row?.scrollIntoView({ block: 'nearest' });
    });
  };
  const changeDesign = designId => {
    const key = drawer?.runKey;
    const next = changeTemplateSectionDesign(blocks, key, designId, { product: catalogs.detailProduct });
    if (next === blocks) return;
    setBlocks(next); showUndo(blocks, next, { label: '섹션 디자인', standalone: true, message: '섹션 디자인을 바꿨어요' });
  };
  const selectSection = key => {
    const run = sectionRuns(blocks).find(item => item.key === key);
    if (!run) return;
    setActiveSectionKey(key); setOverview(false); setSelectedId(null); setSelectedViewId(null); setHighlightedPosition(null); setSetPicker(null);
    if (drawer?.mode === 'design' && sectionDesignChoices(run, { storyboard: blocks, product: catalogs.detailProduct }).length > 1) setDrawer({ mode: 'design', runKey: key });
    else setDrawer(null);
  };
  const swapPosition = (from, to) => {
    if (atomicSavingRef.current) return;
    const next = swapStoryboardPositions(blocks, from, to);
    if (next === blocks) return;
    setSwapNotice(null); setSelectedViewId(null); setBlocks(next);
    showUndo(blocks, next, { label: '사진 자리', standalone: true, message: '사진 자리를 바꿨어요' });
  };
  const dropSectionContent = (transfer, beforeKey) => {
    setDragExampleId(null); setDraggingSectionChoice(false);
    const payload = transfer.getData(NEW_SECTION_DRAG_MIME);
    if (payload) {
      try { const entry = JSON.parse(payload); addSection(entry.kind, entry.usage, entry.designId, beforeKey); } catch { /* foreign drag */ }
      return;
    }
    let exampleId = transfer.getData('text/example-id');
    if (!exampleId) { try { exampleId = JSON.parse(transfer.getData(CATALOG_DRAG_MIME)).exampleId; } catch { /* foreign drag */ } }
    const example = catalogs.genExamples.find(item => item.id === exampleId);
    if (example && ['styling', 'horizon'].includes(example.cutType)) addSection('wear', example.cutType === 'horizon' ? 'fit' : 'styling', undefined, beforeKey, example);
  };
  async function retryAtomicSave() {
    let atomicRetry = atomicRetryRef.current;
    if (atomicRetry && latestBlocks.current !== atomicRetry.previous) {
      atomicRetryRef.current = null;
      atomicRetry = null;
    }
    if (!atomicRetry || atomicSavingRef.current) return false;
    atomicSavingRef.current = true;
    setAtomicSaving(true);
    directSaveSnapshots.current.add(atomicRetry.next);
    setBlocks(atomicRetry.next);
    try {
      if (api.storyboardEntryNotice?.(projectId) !== 'rebuilt') await sbSaveNow(projectId, () => atomicRetry.next);
      atomicRetryRef.current = null;
      showUndo(atomicRetry.previous, atomicRetry.next, { blockId: 'retry', label: '설정' });
      return true;
    } catch (error) {
      if (error?.code === 'revision_conflict') throw error;
      if (sbPending.get(projectId) === atomicRetry.next) sbPending.delete(projectId);
      directSaveSnapshots.current.add(atomicRetry.previous);
      setBlocks(atomicRetry.previous);
      throw error;
    } finally {
      atomicSavingRef.current = false;
      setAtomicSaving(false);
    }
  }
  const goToMannequin = async ({ allowMissingCustomMatch = false } = {}) => {
    if (!mannequinStageEnabled && (doneBlocked || detailPageEntryLocked.current || hasEditorEntered(projectId)
      || creditQuotePending || customMatchPromotionPending || atomicSavingRef.current || detailPageSubmitRef.current)) return;
    // 방어: UI disabled 와 별개로 함수 자체도 게이트 — 다른 호출 경로가 생겨도 미설정 블록 생성 불가
    if (blocks.length === 0) return;
    const missingPresentation = productSectionNeedingSelection(blocks);
    if (missingPresentation) {
      setActiveSectionKey(missingPresentation.key); setSelectedId(missingPresentation.ids[0]); setDrawer({ mode: 'photo' });
      toast.push('제품컷의 촬영 방식을 골라 주세요.'); return;
    }
    if (blocks.some((b) => b.source === 'ai' && (!b.contentRole || !b.cutType))) { toast.push('생성 설정을 준비하지 못한 이미지가 있어요'); return; }
    if (unsupportedAiBlock) {
      toast.push('이 모델로 만들 수 없는 AI 컷이 있어요. 표시된 컷을 삭제하거나 연출을 바꿔주세요.');
      return;
    }
    if (!mannequinStageEnabled) {
      if (customMatchPreparationFailed && !allowMissingCustomMatch) {
        setCustomMatchConfirmationOpen(true);
        return;
      }
      const lifetime = detailPageLifetimeRef.current;
      const { projectGeneration, userId } = useAppStore.getState();
      const isCurrent = () => {
        const state = useAppStore.getState();
        return Boolean(lifetime && detailPageLifetimeRef.current === lifetime && state.projectId === projectId
          && state.projectGeneration === projectGeneration && state.userId === userId
          && !detailPageEntryLocked.current && !hasEditorEntered(projectId));
      };
      setDetailPageSubmitting(true);
      try {
        await continueStoryboardToDetailPage({
          submission: detailPageSubmitRef,
          isCurrent,
          flush: async () => {
            await styleSaves.flush(() => styleValueRef.current);
            if (!isCurrent()) return;
            await ensureV4StoryboardCopywriting(projectId, useAppStore.getState);
            if (!isCurrent()) return;
            const saved = await saveNow(projectId);
            if (isCurrent() && !Array.isArray(saved)) throw new Error('저장된 콘티를 확인하지 못했어요.');
            return saved;
          },
          onFailure: (message, error) => toast.push(message, { variant: 'error', error,
            kind: 'save_error', actionName: 'save', messageCode: 'storyboard_save_failed', blocking: true }),
          account: () => useAppStore.getState().account,
          countGenerationCuts, quote: detailPageQuote, selectedModelId: catalogs.analysis?.selectedModelId,
          onShortfall: setCreditShortfall, navigate,
        });
      } finally {
        if (!detailPageSubmitRef.current && detailPageLifetimeRef.current === lifetime) setDetailPageSubmitting(false);
      }
      return;
    }
    // 생성 입력은 서버가 저장된 콘티에서 읽는다 — 다음 단계로 넘기기 전에 반드시 저장.
    // 같은 직렬 체인 경유: 비행 중 자동저장 뒤에 줄서서 최신 스냅샷이 마지막에 반영됨을 보장.
    await continueAfterStoryboardFlush({
      flush: async () => { await styleSaves.flush(() => styleValueRef.current); await ensureV4StoryboardCopywriting(projectId, useAppStore.getState); return saveNow(projectId); },
      navigate: () => navigate('/create/mannequin'),
      onFailure: (message, error) => toast.push(message, { variant: 'error', error,
        kind: 'save_error', actionName: 'save', messageCode: 'storyboard_save_failed', blocking: true }),
      // 시그니처 또는 호리존 세트의 배경에 필요하면 저장된 콘티 기준으로 옷 색 확인을 맡긴다. 기다리지 않는다.
      afterFlush: () => {
        if (needsGarmentColorMeasurement(latestBlocks.current || blocks)) void api.measureGarmentColors(projectId).catch(() => {});
      },
    });
  };

  const runs = sectionRuns(blocks), visibleRuns = visibleSectionRuns(blocks);
  const activeRun = visibleRuns.find(run => run.key === activeSectionKey) || visibleRuns[0];
  const photoBlocks = blocks.filter(block => block.source !== 'text');
  const photoOrdinals = new Map(photoBlocks.map((block, index) => [block.id, index + 1]));
  const page = pagePreview;
  const selectedSpaceContext = selected?.spaceGroupId ? {
    siblings: blocks.filter(block => block.spaceGroupId === selected.spaceGroupId),
    set: inferStoryboardSpaceSet(selected.spaceGroupId),
  } : null;
  const pickerRun = runs.find(run => run.sectionId === setPicker?.targetSid);
  const pickerMembers = setPicker?.spaceGroupId ? blocks.filter(block => block.spaceGroupId === setPicker.spaceGroupId) : [];
  const usedExampleIds = usedGalleryExampleIds(blocks, selectedId);
  const interactionBusy = atomicSaving || detailPageSubmitting;
  return <div className={`wizard wide sb-page sb-v5-theme sb-content-enter sb-initial-reveal ${styles.page}${drawer ? ` ${styles.drawerOpen}` : ''}${drawer?.mode === 'design' ? ` ${styles.designDrawerOpen}` : ''}${initialBoardRevealed ? ' is-revealed' : ''}${interactionBusy ? ' is-atomic-saving' : ''}`}
    aria-busy={!initialBoardRevealed || interactionBusy || undefined}
    onClickCapture={interactionBusy ? event => { event.preventDefault(); event.stopPropagation(); } : undefined}
    onKeyDownCapture={interactionBusy ? event => { if (!['Tab', 'Escape'].includes(event.key)) { event.preventDefault(); event.stopPropagation(); } } : undefined}
    onDragStartCapture={interactionBusy ? event => { event.preventDefault(); event.stopPropagation(); } : undefined}
    onDropCapture={detailPageSubmitting ? event => { event.preventDefault(); event.stopPropagation(); } : undefined}>
    <StoryboardBackdrop />
    {pendingRemoval && <StoryboardRemoveDialog name={templateSectionName(blocks.find(block => block.pageSectionKey === pendingRemoval.key))}
      consequence={pendingRemoval.impact.consequenceText} onCancel={() => setPendingRemoval(null)} onConfirm={() => {
        const impact = pendingRemoval.previous === blocks ? pendingRemoval.impact : sectionRemovalImpact(blocks, pendingRemoval.key, { product: catalogs.detailProduct });
        commitSectionRemoval(pendingRemoval.key, impact);
      }} />}
    {(doneBlocked || detailPageEntryLocked.current) && <DoneGuardModal />}
    {customMatchConfirmationOpen && !doneBlocked && !detailPageEntryLocked.current && <Modal onClose={() => setCustomMatchConfirmationOpen(false)}>
      <h3>내 옷을 콘티에 반영하지 못했어요</h3>
      <p>이대로 진행하면 고른 옷이 결과에 빠질 수 있어요.</p>
      <div className="modal-actions">
        <Button variant="ghost" onClick={() => setCustomMatchConfirmationOpen(false)}>콘티 확인</Button>
        <Button variant="primary" onClick={() => {
          setCustomMatchConfirmationOpen(false);
          goToMannequin({ allowMissingCustomMatch: true });
        }}>지금 코디로 만들기</Button>
      </div>
    </Modal>}
    {!doneBlocked && !detailPageEntryLocked.current && creditShortfall && <CreditShortfallModal shortfall={creditShortfall} action="detail-page" onClose={() => setCreditShortfall(null)} />}
    {!doneBlocked && !detailPageEntryLocked.current && creditResume && <Modal onClose={() => {
      setCreditResume(null);
      navigate(location.pathname, { replace: true, state: null });
    }}>
      <h3>이어서 진행할까요? · {creditResume.requiredCredits}크레딧</h3>
      <p>충전 전 멈춘 상세페이지 생성을 다시 확인해 주세요.</p>
      <div className="modal-actions">
        <Button variant="ghost" onClick={() => {
          setCreditResume(null);
          navigate(location.pathname, { replace: true, state: null });
        }}>나중에</Button>
        <Button variant="primary" disabled={creditQuotePending || customMatchPromotionPending} onClick={() => {
          setCreditResume(null);
          navigate(location.pathname, { replace: true, state: null });
          goToMannequin();
        }}>이어서 진행</Button>
      </div>
    </Modal>}
    <div className={`page-head ${styles.pageHeadWithTheme}`}><div className={styles.pageHeadingCopy}><h1>상세페이지 초안 구성</h1>
        <p className={styles.introCopy}>예시 이미지들로 상세페이지 초안이 구성됐어요. 원하는 느낌으로 구성해보세요.</p></div>
      <div className={styles.styleControlsWrap}><StoryboardStyleControls key={projectId} pageStyle={selectedPageStyle} product={catalogs.detailProduct} disabled={interactionBusy}
        onFontChange={fontStyle => changePageStyle({ fontStyle })} onToneChange={colorMode => changePageStyle({ colorMode })} />
        {pageStyleError && <p role="alert">{pageStyleError}</p>}</div>
    </div>
    {mannequinStageEnabled && mannequinFailure && <div className="sec-head" role="status">
      <p className="sec-sub handoff-title">{mannequinFailure}</p>
      <Button variant="ghost" size="sm" onClick={async () => {
        if (mannequinRetryRef.current) return;
        mannequinRetryRef.current = true; retryInitialGeneration(projectId); setMannequinFailure(null);
        try { await requestMannequinGeneration(projectId, { confirmed: true }); }
        catch (error) { setMannequinFailure(error?.message || '서버 연결이 불안정해요. 잠시 후 다시 시도해 주세요.'); }
        finally { mannequinRetryRef.current = false; }
      }}>마네킹 다시 만들기{mannequinQuoteConfirmation.pending(projectId)
        ? ` · ${mannequinQuoteLabel(mannequinQuoteConfirmation.pending(projectId).quote)}` : ''}</Button>
    </div>}
    {entryNotice === 'rebuilt' && <p role="status" className="sb-entry-notice">{STORYBOARD_REVIEW_MESSAGE}</p>}
    {autosaveFailed && <div className="sb-save-error sb-autosave-error" role="alert">
      <ClientErrorCapture point="storyboardSave" error={autosaveFailed instanceof Error ? autosaveFailed : undefined} projectId={projectId} />
      <span>변경 내용을 저장하지 못했어요. 잠시 뒤 다시 시도해주세요.</span><button type="button" onClick={retryAutosave}>다시 저장</button>
    </div>}
    <div className={`${styles.layout}${overview ? ` ${styles.overviewLayout}` : ''}`}>
      {!overview && <StoryboardPageList runs={visibleRuns} page={page} activeKey={activeRun?.key}
        busy={interactionBusy} highlightedPosition={highlightedPosition} selectedSourceId={selectedId} selectedPositionId={selectedViewId} insertedKey={insertedSectionKey}
        onPositionSelect={(key, position) => selectCard(position.sourceBlockId, position.viewId, key)}
        draggingExample={Boolean(dragExampleId || draggingSectionChoice)} onSelect={selectSection}
        onAdd={() => { setSelectedId(null); setDrawer({ mode: 'add' }); }}
        onRemove={removeSection} onMove={moveSection} onDropContent={dropSectionContent}
        onOverview={() => { closeInspector(); setOverview(true); }} sectionRefs={sectionRefs} />}
      <StoryboardStage run={activeRun} number={visibleRuns.findIndex(run => run.key === activeRun?.key) + 1}
        toneColor={tone.colorMode === 'point' ? tone.colors.accentBg : null}
        blocks={blocks} page={page} catalogs={previewCatalogs} colorOpts={detailColorOpts} photoOrdinals={photoOrdinals} selectedId={selectedId} matchClothing={matchClothing || []} identityKind={identityKind}
        selectedViewId={selectedViewId} selectedGroupId={drawer?.mode === 'set' && setPicker?.mode === 'replace' ? setPicker.spaceGroupId : null}
        dimOthers={drawer?.mode === 'photo' || drawer?.mode === 'set' && setPicker?.mode === 'replace'} busy={interactionBusy} canDesign={sectionDesignChoices(activeRun, { storyboard: blocks, product: catalogs.detailProduct }).length > 1}
        onHoverPosition={setHighlightedPosition} onSwapPosition={swapPosition} onSwapBlocked={setSwapNotice}
        onPhotoDragStart={() => { photoDragUntil.current = Infinity; }} onPhotoDragEnd={() => { photoDragUntil.current = Date.now() + 200; }}
        onDesign={() => setDrawer({ mode: 'design', runKey: activeRun.key })} onSelectPhoto={selectCard}
        onRemovePhoto={remove} onRemovePairedPhoto={removeOptionalPhoto} onAddAddition={addOptionalPhoto} onSimilar={addSimilar}
        onAddSet={() => openSetPicker({ mode: 'add', targetSid: activeRun.sectionId, targetRole: activeRun.usage === 'fit' ? 'studio' : 'styling' })}
        onReplaceSet={block => { setSelectedId(block.id); openSetPicker({ mode: 'replace', spaceGroupId: block.spaceGroupId,
          targetSid: block.sectionId, targetRole: block.cutType === 'horizon' ? 'studio' : 'styling' }); }}
        overview={overview} onLeaveOverview={() => {
          setOverview(false);
          requestAnimationFrame(() => sectionRefs.current.get(activeRun?.key)?.querySelector('[aria-current="true"]')?.focus({ preventScroll: true }));
        }} onSelectSection={selectSection} />
    </div>
    {swapNotice && <p role="status" className={styles.swapNotice}>{swapNotice}</p>}
    {drawer && <StoryboardDrawer mode={drawer.mode} busy={interactionBusy} onClose={closeInspector}
      title={drawer.mode === 'design' ? `${templateSectionName(blocks.find(block => block.pageSectionKey === drawer.runKey))} 디자인` : undefined}
      focusKey={`${drawer.mode}:${drawer.runKey || ''}:${drawer.beforeSectionKey || ''}:${selectedId || ''}`}>
      {['add', 'design'].includes(drawer.mode) ? <StoryboardSectionPicker mode={drawer.mode} runKey={drawer.runKey}
        blocks={blocks} catalogs={previewCatalogs} gender={exampleGender} identityKind={identityKind} busy={interactionBusy}
        undoEntry={undoEntry} undoExiting={undoExiting} onUndo={undoLatest} onUndoEnter={enterUndo} onUndoLeave={leaveUndo}
        onChoose={(kind, usage, designId, source) => drawer.mode === 'design' ? changeDesign(designId)
          : addSection(kind, usage, designId, automaticSectionInsertionKey(blocks, kind, usage), null, source)}
        onDragChange={setDraggingSectionChoice} />
        : drawer.mode === 'set' ? <StoryboardSpaceSetPanel mode={setPicker?.mode} error={setPickerError} onChoose={chooseSpaceSet}
          projectId={projectId}
          gender={exampleGender} clothingType={clothingType} identityKind={identityKind} busy={interactionBusy}
          usage={pickerRun?.usage} cutType={pickerMembers[0]?.cutType || pickerRun?.blocks.find(block => block.source === 'ai')?.cutType}
          currentMembers={pickerMembers} remaining={12 - (pickerRun?.blocks.filter(block => block.source !== 'text').length || 0)} onExampleDrag={setDragExampleId}
          canChooseSet={set => setPicker?.mode !== 'replace' || canReplaceStoryboardSet(blocks, setPicker.spaceGroupId, set)} />
          : selected?.similarSourceBlockId ? <div className={styles.similarPanel}>
            <h2>비슷한 컷</h2><p>기준 사진이 완성되면 같은 촬영의 다른 순간으로 만들어요.</p>
            <button type="button" onClick={() => selectCard(selected.similarSourceBlockId)}>기준 컷 {photoOrdinals.get(selected.similarSourceBlockId)}번 설정 보기</button>
          </div> : <StoryboardPhotoPanel key={selectedId} block={selected} blocks={blocks} busy={interactionBusy} catalogs={catalogs} colorOpts={colorOpts} detailColorOpts={detailColorOpts}
            fixedProduct={activeRun?.kind === 'first' && pairedPhotoSlot(activeRun.designId) === 'ghost'
              && (selectedViewId?.includes(':first.ghost:') || selected?.slotKey === 'first.ghost')}
            clothingType={clothingType} subCategory={storyboardSeedContext.subCategory} customCategory={storyboardSeedContext.customCategory}
            exampleGender={exampleGender} identityKind={identityKind} hasDetailImage={hasDetailImage} projectId={projectId}
            onChange={(patchValue, options) => patch(selectedId, patchValue, options)} onAtomicChange={(patchValue, options) => atomicPatch(selectedId, patchValue, options)}
            onRetryAtomicSave={retryAtomicSave} matchClothing={matchClothing} customMatchPromotionPending={customMatchPromotionPending}
            spaceContext={selectedSpaceContext} usedExampleIds={usedExampleIds} onExampleDrag={setDragExampleId}
            onChangeSpaceSet={() => openSetPicker({ mode: 'replace', spaceGroupId: selected.spaceGroupId,
              targetSid: selected.sectionId, targetRole: selected.cutType === 'horizon' ? 'studio' : 'styling' })}
            onChangeSpaceBackground={mode => atomicBoardChange(current => updateHorizonGroupBackground(current, selected.spaceGroupId, mode), { undoBlockId: selected.id, undoLabel: '세트 배경' })} />}
    </StoryboardDrawer>}
    <StoryboardContinue hidden={Boolean(drawer || (!mannequinStageEnabled
      && (doneBlocked || detailPageEntryLocked.current || creditShortfall || creditResume || customMatchConfirmationOpen)))} count={photoBlocks.length}
      label={storyboardContinueLabel(mannequinStageEnabled)}
      credits={mannequinStageEnabled ? detailPageCreditCost(generationCutCount) : storyboardDetailPageCredits(generationCutCount,
        { quote: detailPageQuote, plan: account?.plan, selectedModelId: catalogs.analysis?.selectedModelId })}
      undoEntry={undoEntry} undoExiting={undoExiting} onUndo={undoLatest} submitting={detailPageSubmitting}
      onUndoAnimationEnd={event => { if (event.target === event.currentTarget && undoExiting) finishUndoDismiss(); }}
      onUndoEnter={enterUndo} onUndoLeave={leaveUndo}
      onContinue={goToMannequin} warning={unsupportedAiBlock ? 'AI 컷 설정 확인 필요'
        : customMatchPromotionPending && !mannequinStageEnabled ? '내 옷을 콘티에 반영하고 있어요'
        : creditQuotePending ? '크레딧을 확인하고 있어요' : null}
      disabled={creditQuotePending || (!mannequinStageEnabled && (atomicSaving || customMatchPromotionPending)) || !photoBlocks.length
        || Boolean(unsupportedAiBlock) || blocks.some(block => block.source === 'ai' && (!block.contentRole || !block.cutType))} />
  </div>;
}

export default Storyboard;
