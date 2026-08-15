/* =============================================================
   features/storyboard — ⑤ 콘티보드 (PRD §8)
   blocks 는 "서버 상태의 working copy" 패턴: 진입 시 fetch → 로컬 편집
   → 생성 CTA 에서 saveStoryboard 로 한 번에 저장 (frontend_state_model §4).
   사용자는 sectionRole(후킹/스타일링/스튜디오/의류 확인), 컷 종류와 생성예시를 다룬다.
   contentRole은 섹션·카드 위치·선택한 컷에서 정하는 내부 생성값이다.
   카피라이팅 토글은 store(copywriting) → patchProject 동기화.
   UnderlineTabs/ColorDots/MoodGuide/hexFor are exported for the editor.
   ============================================================= */
import React, { useState, useEffect, useLayoutEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api/index.js';
import { uid } from '@/lib/ids.js';
import { Placeholder } from '@/mock/placeholders.js';
import { useAppStore } from '@/store/useAppStore.js';
import { Icon, IconButton, Button, Chips, EmptyState, Modal, Toggle, useToast } from '@/components/ui.jsx';
import { PageHead, useDoneGuard, DoneGuardModal } from '@/features/shell/shell.jsx';
import { isDefaultStoryboardForMode } from '@/lib/api/shapes.js';
import { normalizeMatchIds } from '@/lib/api/matchingItems.js';
import { ensureSections, deriveSections, adoptSection, patchSection, normalizeRows, normalizeBoard } from '@/lib/sections.js';
import {
  CONTENT_ROLES,
  SECTION_ROLES,
  SECTION_ROLE_OPTIONS,
  STORYBOARD_TAXONOMY_VERSION,
  allowedCutTypeOptionsForSection,
  blockPatchForContentRole,
  cutTypeOptionsForSection,
  defaultContentRoleForSection,
  hasDetailSource,
  normalizedRecipePatch,
  poseExampleDirectionCompatible,
  sectionRoleForContentRole,
  sectionTitle,
} from '@/lib/storyboardTaxonomy.js';
import {
  assignGenerationExamples,
  generationExampleImageSources,
  hasSelectableGenerationExamples,
  isGenerationCombinationPublic,
  paginateGenerationGalleryItems,
  repeatedAllExampleVariationIds,
  selectGenerationExamples,
  storedExampleConditionStatus,
} from '@/lib/generationExamples.js';
import {
  inferStoryboardSpaceSet,
  spaceSetGroupId,
  storyboardSpaceSetsFor,
  withStoryboardSpaceSetExamples,
} from '@/lib/storyboardSpaceSetCatalog.js';
import { stripStaleSpaceSetBindings } from '@/lib/storyboardSpaceSetStaleness.js';
import { stripExampleSelectionsById, stripStaleExampleSelections } from '@/lib/storyboardExampleStaleness.js';
import {
  HOOK_STYLES,
  HOOK_STYLE_LABELS,
  adoptHookFrame,
  applyHookStyle,
  deriveHookFrame,
  hookSlotPlan,
  moodGridContent,
  stripHookFrameFields,
} from '@/lib/storyboardHookFrame.js';
import { shuffleSectionExamples } from '@/lib/storyboardExampleShuffle.js';
import { uniqueGenerationCutCount } from '@/lib/generationCutCount.js';
import { genderForClothingType } from '@/lib/productGender.js';
import {
  detachSpaceMembership,
  groupConsecutiveSpaceRuns,
  insertSpaceSet,
  moveBlockWithSpaceMembership,
  moveSpaceSetRun,
  nextSpaceSetMemberReservation,
  rekeySeparatedSpaceRuns,
  replaceSpaceSetRun,
} from '@/lib/storyboardSpaceSets.js';
import {
  consumeStoryboardEntry,
  invalidateStoryboardEntryPrefetch,
  loadStoryboardEntry,
  peekStoryboardEntry,
} from './storyboardEntryPrefetch.js';
import {
  sbLastSaved,
  sbPending,
  sbSaveIdle,
  sbSaveNow,
  sbSetSaveRepair,
  sbStable,
} from './storyboardPersistence.js';
import { renderGroups } from '@/lib/storyboardRenderGroups.js';
import { prewarmImages } from '@/lib/imagePrewarm.js';
import { spaceSetDisplayName } from '@/lib/spaceSetDisplayNames.js';
import {
  detailDirectionFromExample,
  generationExampleSelectionPatch,
  generationExampleStructuralRecipePatch,
} from '@/lib/storyboardExampleSelection.js';
import { mineImageUrl, normalizeMineImages, promoteMineImage } from '@/lib/storyboardMineImages.js';
import { requestMannequinGeneration } from '@/features/mannequin/generationRunner.js';
import { waitForAnalysisEditSave } from '@/features/product-input/saveRouting.js';
import { selectStoryboardCopywriting } from './copywritingSelection.js';
import { applyStoryboardComposeMode } from './storyboardComposeMode.js';
import { classifyStoryboardLoadError, storyboardNotFoundError } from './storyboardLoadError.js';
import { continueAfterStoryboardFlush } from './storyboardNavigation.js';
import { storyboardOverlayTop } from './storyboardOverlayTop.js';
import { bindStoryboardExitFlush, scheduleStoryboardAutosave } from './storyboardSaveLifecycle.js';
import {
  collectInitialRevealThumbnailUrls,
  waitForInitialReveal,
} from './initialRevealGate.js';


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

const withoutLayoutRow = (block) => {
  const { layoutRowId: _layoutRowId, ...single } = block;
  return single;
};

// 첫 화면 슬롯의 틀(컷 종류·샷·색상)을 직접 바꾸면 그 컷은 프레임에서 이탈한다 —
// 스타일이 틀을 정한다는 계약(스펙 §1)과 저장 표식이 어긋나지 않게 (Codex 리뷰 #5).
// 프레임 이탈은 틀이 "실제로 바뀔 때"만 — 같은 값 재클릭(예: 선택된 색상 점 다시 누르기)이
// 프레임을 해체하면 지문에 안 잡혀 훼손 보드가 기본 보드로 오판된다(Codex 리뷰 2차 #1).
const detachHookSlotOnReshape = (current, applied, merged) => (
  current.hookFrameId
  && (('cutType' in applied && applied.cutType !== current.cutType)
    || ('shot' in applied && applied.shot !== current.shot)
    || ('colorId' in applied && applied.colorId !== current.colorId))
    ? withoutLayoutRow(stripHookFrameFields(merged))
    : merged
);

const WORN_CUT_TYPES = new Set(['styling', 'horizon', 'mirror']);
const WORN_ROLE_BY_CUT_TYPE = Object.freeze({
  styling: CONTENT_ROLES.COORDINATION,
  horizon: CONTENT_ROLES.FIT,
  mirror: CONTENT_ROLES.REAL_WEAR,
});
const exampleCategoryFor = (cut) => cut === 'product' ? 'product' : (cut === 'horizon' ? 'horizon' : 'styling');
const exampleThumbFor = (catalogs, exampleId, cut) => (
  (catalogs?.genExamples || []).find((example) => example.id === exampleId)?.thumb
  || Placeholder.photo(exampleId, exampleCategoryFor(cut), 240, 320)
);
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

export function OuterClosureIcon({ state }) {
  const edge = state === 'closed'
    ? <path d="M24 18v32" />
    : state === 'partial'
      ? <path d="M18 18l6 10 6-10M24 28v22" />
      : <path d="M18 18l6 16 6-16M18 34l-3 16M30 34l3 16" />;
  return (
    <svg className="outer-closure-icon" viewBox="0 0 48 56" aria-hidden="true">
      <path d="M17 8l-9 7 5 12 4-4-2 27h18l-2-27 4 4 5-12-9-7-7 7z" />
      {edge}
      {state !== 'open' && <><circle cx="24" cy="32" r="1" /><circle cx="24" cy="39" r="1" /><circle cx="24" cy="46" r="1" /></>}
    </svg>
  );
}

function referenceFeedbackPatch(block, changes, catalogs) {
  if (!block) return changes;
  const exampleId = Object.prototype.hasOwnProperty.call(changes, 'exampleId') ? changes.exampleId : block.exampleId;
  const refScope = Object.prototype.hasOwnProperty.call(changes, 'refScope') ? changes.refScope : (block.refScope || 'all');
  // 같은 공간 묶음 컷은 서버 계약(normalize_spec)이 범위를 'pose' 로 강제 — 프론트 표시도 동일 규칙
  const spaceGroupId = Object.prototype.hasOwnProperty.call(changes, 'spaceGroupId') ? changes.spaceGroupId : block.spaceGroupId;
  const effScope = spaceGroupId ? 'pose' : refScope;
  const next = { ...changes };
  if (exampleId && effScope === 'all') {
    if (block.baseThumb == null) next.baseThumb = block.thumb;
    next.thumb = exampleThumbFor(catalogs, exampleId, changes.cutType ?? block.cutType);
  } else if (block.baseThumb != null) {
    next.thumb = block.baseThumb;
  }
  return next;
}

const CARD_DRAG_THRESHOLD_PX = 6;
const UNDO_WINDOW_MS = 10_000;
const STORYBOARD_NETWORK_ERROR_MESSAGE = '생성예시 카탈로그를 불러오지 못했어요';
const prefersReducedMotion = () => (
  typeof window !== 'undefined'
  && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
);

const initialRevealThumbnailFor = (block, catalogs) => {
  if (block.source === 'mine') return block.thumb || block.ownImages?.[0];
  const example = block.exampleId
    ? (catalogs?.genExamples || []).find((candidate) => candidate.id === block.exampleId)
    : null;
  return (example ? generationExampleImageSources(example).src : null) || block.thumb;
};

const cutNumber = (index, total) => String(index).padStart(2, '0') + '/' + String(total).padStart(2, '0');
const cutRangeLabel = (items) => {
  if (!items?.length) return '컷 없음';
  const first = items[0].index;
  const last = items[items.length - 1].index;
  return first === last ? `${first}번째 컷` : `${first}번째 ~ ${last}번째 컷`;
};

function SelectionRing() {
  return <span className="sb-selection-ring" aria-hidden="true"><i /><i /><i /><i /></span>;
}

function StoryboardInsertControl({ inTray, active, placement: forcedPlacement = null, onDragOver, onDrop, onAdd }) {
  const controlRef = useRef(null);
  const [placement, setPlacement] = useState('end');
  useLayoutEffect(() => {
    if (forcedPlacement) return undefined;
    const control = controlRef.current;
    const unit = control?.closest('.sb-grid-unit');
    const grid = unit?.parentElement;
    if (!unit || !grid) return undefined;
    const measure = () => {
      const next = unit.nextElementSibling;
      const nextIsUnit = next?.classList.contains('sb-grid-unit');
      setPlacement(nextIsUnit && Math.abs(next.offsetTop - unit.offsetTop) < 2 ? 'row' : 'end');
    };
    const observer = new ResizeObserver(measure);
    observer.observe(grid);
    measure();
    return () => observer.disconnect();
  }, [forcedPlacement]);
  const resolvedPlacement = forcedPlacement || placement;
  return (
    <span ref={controlRef}
      className={`sb-addzone ${resolvedPlacement}${inTray ? ' in-tray' : ''}${active ? ' drop-on' : ''}`}
      onDragOver={onDragOver} onDrop={onDrop}>
      <button type="button" className="sb-addzone-plus" aria-label="이 위치에 컷 추가" onClick={onAdd}>＋</button>
    </span>
  );
}

function cardLabels(block, catalogs) {
  const isProduct = block.cutType === 'product';
  const direction = isProduct
    ? (catalogs.productDirections.find((item) => item.value === block.direction)?.label || '앞면')
    : (catalogs.directions.find((item) => item.value === block.direction)?.label || '—');
  const shot = isProduct
    ? (catalogs.productShotTypes.find((item) => item.value === block.shot)?.label || '고스트샷')
    : (catalogs.shotTypes.find((item) => item.value === block.shot)?.label || '—');
  return { direction, shot, isProduct };
}

function StoryboardCaption({ block, catalogs, colorOpts, clothingType }) {
  if (block.source === 'mine') return <div className="sb-canvas-caption mine">내 사진</div>;

  const colors = ((block.colorIds && block.colorIds.length) ? block.colorIds : [block.colorId])
    .map((id) => colorOpts.find((color) => color.id === id))
    .filter(Boolean);
  const example = block.exampleId
    ? (catalogs.genExamples || []).find((item) => item.id === block.exampleId)
    : null;
  const scope = block.spaceGroupId ? 'pose' : (block.refScope || 'all');
  const { direction, shot } = cardLabels(block, catalogs);
  const scopeAll = !block.spaceGroupId && scope === 'all';
  const directionDiffers = scopeAll && !!example?.direction && !!block.direction && example.direction !== block.direction;
  const shotDiffers = scopeAll && !!example?.shot && !!block.shot && example.shot !== block.shot;
  const closureOptions = catalogs.outerClosureStates || [];
  const closure = closureOptions.find((option) => option.value === block.outerClosureState)?.label || '전체 열림';
  const showClosure = clothingType === 'outer' && WORN_CUT_TYPES.has(block.cutType);

  return (
    <div className="sb-canvas-caption">
      {/* 매칭 의류 표시는 이미지 위 오버레이(StoryboardMedia)로 옮겼다 —
          셀러가 직접 바꾼 컷에만 뜬다(2026-08-14 오너 확정). */}
      <span className="sb-caption-values">
        {block.cutType !== 'mirror' && (
          <span className={directionDiffers ? 'sb-val-changed' : undefined}>{direction}</span>
        )}
        {block.cutType !== 'mirror' && <span aria-hidden="true"> · </span>}
        <span className={shotDiffers ? 'sb-val-changed' : undefined}>{shot}</span>
        {showClosure && <span title="아우터 열림 정도"> · {closure}</span>}
      </span>
      {colors.map((color) => (
        <span key={color.id} className="sb-caption-dot" style={{ background: color.hex }} title={color.label} />
      ))}
      {colors.length > 0 && (
        <span className="sb-caption-color" title={colors.map((color) => color.label).join(', ')}>
          {colors[0].label}{colors.length > 1 ? ` 외 ${colors.length - 1}` : ''}
        </span>
      )}
    </div>
  );
}

function StoryboardCardActions({ onDuplicate, onDelete, onNudge, canNudgeUp, canNudgeDown }) {
  return (
    <span className="sb-canvas-actions" onPointerDown={(event) => event.stopPropagation()}
      onKeyDown={(event) => event.stopPropagation()}
      onClick={(event) => event.stopPropagation()}>
      {/* 드래그를 못 쓰는 경우(키보드 조작 포함)의 순서 변경 수단 — 드래그와 규칙이 같다. */}
      {onNudge && (
        <button
          type="button" title="앞으로 한 칸" aria-label="앞으로 한 칸 이동"
          disabled={!canNudgeUp} onClick={() => onNudge(-1)}
        ><Icon name="chevLeft" size={16} /></button>
      )}
      {onNudge && (
        <button
          type="button" title="뒤로 한 칸" aria-label="뒤로 한 칸 이동"
          disabled={!canNudgeDown} onClick={() => onNudge(1)}
        ><Icon name="chevRight" size={16} /></button>
      )}
      <button type="button" title="컷 복제" aria-label="컷 복제" onClick={onDuplicate}><Icon name="copy" size={15} /></button>
      <button type="button" title="컷 삭제" aria-label="컷 삭제" onClick={onDelete}><Icon name="x" size={15} /></button>
    </span>
  );
}

function StoryboardMedia({
  block, catalogs, matchClothing = null, index, total,
  showPoseVariation = false,
  onDuplicate, onDelete, onNudge, canNudgeUp, canNudgeDown,
}) {
  const missing = block.source !== 'mine' && !block.exampleId && !block.previewThumb;
  const manualEmpty = missing && block.exampleChoice === 'manual';
  const example = block.exampleId
    ? (catalogs?.genExamples || []).find((item) => item.id === block.exampleId)
    : null;
  const image = example ? generationExampleImageSources(example) : null;
  const src = block.source === 'mine'
    ? (block.thumb || block.ownImages?.[0])
    : (block.previewThumb || image?.src || block.thumb);
  // 매칭 의류 표시는 셀러가 인스펙터에서 직접 바꾼 컷에만(자동 배정 컷은 조용히) —
  // 이미지 우측 하단 오버레이(2026-08-14 오너 확정).
  const userMatch = block.source !== 'mine' && block.cutType !== 'product'
    && block.matchIdsOrigin === 'user' && Array.isArray(block.matchIds) && block.matchIds.length
    ? (matchClothing || []).find((item) => item.id === block.matchIds[0])
    : null;
  return (
    <>
      <span className="sb-canvas-number">{cutNumber(index, total)}</span>
      {block.source === 'mine' && <span className="sb-mine-badge">내 사진</span>}
      {missing ? (
        <span className={`sb-missing-body${manualEmpty ? ' manual-empty' : ''}`}>
          <span className="upload-placeholder-logo" aria-hidden="true" />
          <i>{manualEmpty
            ? '분위기 예시를 골라주세요.'
            : '이 조합의 예시를 준비하지 못했어요 — 컷 설정을 바꾸거나 직접 예시를 골라주세요'}</i>
        </span>
      ) : (
        <img src={src} srcSet={image?.srcSet} alt="" loading="lazy" decoding="async" />
      )}
      {showPoseVariation && (
        <span className="sb-pose-variation-note">약간 다른 포즈 적용</span>
      )}
      {/* 첫 화면 슬롯·미사용 배지는 제거 — 라벨 없이 컷 자체로 보여준다(2026-08-14 오너). */}
      {userMatch?.thumb && (
        <span className="sb-match-overlay" title={`매칭 의류 · ${userMatch.name || ''}`}>
          <img src={userMatch.thumb} alt="" loading="lazy" decoding="async" />
          <i>매칭</i>
        </span>
      )}
      <StoryboardCardActions
        onDuplicate={onDuplicate} onDelete={onDelete}
        onNudge={onNudge} canNudgeUp={canNudgeUp} canNudgeDown={canNudgeDown}
      />
    </>
  );
}

function CardDragSurface({ className, dragProps, onSelect, children }) {
  const pointerStart = useRef(null);
  const movedBeyondClick = useRef(false);
  const onPointerDown = (event) => {
    if (event.button !== 0) return;
    pointerStart.current = { x: event.clientX, y: event.clientY };
    movedBeyondClick.current = false;
  };
  const onPointerMove = (event) => {
    if (!pointerStart.current || movedBeyondClick.current) return;
    const dx = event.clientX - pointerStart.current.x;
    const dy = event.clientY - pointerStart.current.y;
    if (Math.hypot(dx, dy) >= CARD_DRAG_THRESHOLD_PX) movedBeyondClick.current = true;
  };
  return (
    <div
      className={className}
      role="button"
      tabIndex={0}
      draggable={dragProps?.draggable}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerCancel={() => { pointerStart.current = null; }}
      onDragStart={(event) => {
        movedBeyondClick.current = true;
        dragProps?.onDragStart?.(event);
      }}
      onDragEnd={(event) => {
        pointerStart.current = null;
        dragProps?.onDragEnd?.(event);
      }}
      onClick={(event) => {
        pointerStart.current = null;
        if (movedBeyondClick.current) {
          movedBeyondClick.current = false;
          event.preventDefault();
          return;
        }
        onSelect();
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onSelect();
        }
      }}
    >
      {children}
    </div>
  );
}

function StoryboardCard({
  item, total, catalogs, colorOpts, matchClothing, clothingType,
  selected, locked, cardDrag, onSelect, onDuplicate, onDelete, addControl,
  onNudge, canNudgeUp, canNudgeDown, microVariationIds,
}) {
  const { block, index } = item;
  const missing = block.source !== 'mine' && !block.exampleId;
  const manualEmpty = missing && block.exampleChoice === 'manual';
  return (
    <div className={'sb-canvas-card' + (locked ? ' locked' : '')}>
      <div className="sb-card-media">
        <CardDragSurface
          className={'sb-cutcard' + (selected ? ' selected' : '') + (missing ? ' missing' : '') + (manualEmpty ? ' manual-empty' : '')}
          dragProps={cardDrag}
          onSelect={onSelect}
        >
          <StoryboardMedia
            block={block}
            catalogs={catalogs}
            colorOpts={colorOpts}
            matchClothing={matchClothing}
            index={index}
            total={total}
            showPoseVariation={microVariationIds?.has(block.id)}
            onDuplicate={onDuplicate}
            onDelete={onDelete}
            onNudge={onNudge}
            canNudgeUp={canNudgeUp}
            canNudgeDown={canNudgeDown}
          />
          {selected && <SelectionRing />}
        </CardDragSurface>
        {addControl}
      </div>
      <StoryboardCaption
        block={block}
        catalogs={catalogs}
        colorOpts={colorOpts}
        matchClothing={matchClothing}
        clothingType={clothingType}
      />
    </div>
  );
}

function StoryboardFrame({
  items, total, catalogs, colorOpts, matchClothing, clothingType,
  selectedId, locked, dragFor, onSelect, onDuplicate, onDelete, addControl,
  microVariationIds,
}) {
  const colorway = items.every((item) => (
    item.block.colorwayGroupId
    && item.block.colorwayGroupId === items[0].block.colorwayGroupId
  ));
  const colorwayName = colorOpts.find((color) => color.id === items[0].block.colorId)?.label || '색상';
  return (
    <div className={'sb-frame' + (colorway ? ' colorway-set' : '')}>
      <div className="sb-frame-media">
        {/* 프레임 라벨은 색상 세트만 남긴다 — '한 프레임 구성'류 일반 라벨은 전부 제거
            (2026-08-15 오너: "이런 거 다 빼라"). */}
        {colorway && (
          <span className="sb-frame-tag">{`색상 세트 · ${colorwayName} · 풀샷 + 미디움샷`}</span>
        )}
        <div className="sb-frame-box">
          {items.map((item) => {
            const missing = item.block.source !== 'mine' && !item.block.exampleId;
            const manualEmpty = missing && item.block.exampleChoice === 'manual';
            return (
              <CardDragSurface
                key={item.block.id}
                className={'sb-frame-half' + (item.block.id === selectedId ? ' selected' : '') + (missing ? ' missing' : '') + (manualEmpty ? ' manual-empty' : '') + (locked && item.block.id !== selectedId ? ' locked' : '')}
                dragProps={dragFor(item.block.id)}
                onSelect={() => onSelect(item.block.id)}
              >
                <StoryboardMedia
                  block={item.block}
                  catalogs={catalogs}
                  colorOpts={colorOpts}
                  matchClothing={matchClothing}
                  index={item.index}
                  total={total}
                  showPoseVariation={microVariationIds?.has(item.block.id)}
                        onDuplicate={() => onDuplicate(item.block.id)}
                  onDelete={() => onDelete(item.block.id)}
                />
                {item.block.id === selectedId && <SelectionRing />}
              </CardDragSurface>
            );
          })}
        </div>
        {addControl}
      </div>
      <div className="sb-frame-captions">
        {items.map((item) => (
          <StoryboardCaption
            key={item.block.id}
            block={item.block}
            catalogs={catalogs}
            colorOpts={colorOpts}
            matchClothing={matchClothing}
            clothingType={clothingType}
          />
        ))}
      </div>
    </div>
  );
}

function StoryboardStack({ group, total, catalogs, onOpen }) {
  const previews = group.items.slice(0, 3);
  return (
    <div className="sb-stack-wrap">
      <button type="button" className={'sb-stack' + (previews.length ? '' : ' empty')} onClick={onOpen}>
        {previews.length ? previews.map((item, stackIndex) => {
          const example = item.block.exampleId
            ? (catalogs?.genExamples || []).find((candidate) => candidate.id === item.block.exampleId)
            : null;
          const image = example ? generationExampleImageSources(example) : null;
          return (
            <span key={item.block.id} className="sb-stack-cut">
              {stackIndex === 0 && <span className="sb-canvas-number">{cutNumber(item.index, total)}</span>}
              <img src={item.block.previewThumb || image?.src || item.block.thumb || item.block.ownImages?.[0]}
                srcSet={image?.srcSet} alt="" loading="lazy" decoding="async" />
            </span>
          );
        }) : <span className="sb-stack-empty">＋ 컷 추가</span>}
      </button>
    </div>
  );
}

const blockPreviewSrc = (block, catalogs) => {
  const example = block.exampleId
    ? (catalogs?.genExamples || []).find((candidate) => candidate.id === block.exampleId)
    : null;
  const image = example ? generationExampleImageSources(example) : null;
  return block.previewThumb || image?.src || block.thumb || block.ownImages?.[0];
};

/* 후킹 접힘 예외(스펙 2026-08-14 §3) — 스택 대신 흰 페이지 시트 위에 첫 화면 실형태.
   클릭 = 섹션 펼침. 스타일 변경은 펼친 뒤 컷 인스펙터 상단에서. */
function StoryboardHookSheet({ frame, slots, total, catalogs, colorOpts, productName, baseColorId, onOpen }) {
  return (
    <div className="sb-hooksheet-wrap">
      <button
        type="button"
        className={`sb-hooksheet style-${frame.style}`}
        onClick={onOpen}
        aria-label="후킹 섹션 펼치기"
      >
        <span className="sb-hooksheet-box">
          {slots.map(({ block, index }) => {
            const color = colorOpts.find((option) => option.id === block.colorId);
            return (
              <span key={block.id} className="sb-hooksheet-tile">
                <span className="sb-canvas-number">{cutNumber(index, total)}</span>
                <img src={blockPreviewSrc(block, catalogs)} alt="" loading="lazy" decoding="async" />
                {frame.style === 'signature' && block.hookTitleOverlay && productName && (
                  <span className="sb-hooksheet-title"><i>{productName}</i></span>
                )}
                {frame.style === 'moodGrid' && color && (
                  <span className="sb-hooksheet-color"><i style={{ background: color.hex }} />{color.label}</span>
                )}
                {frame.style === 'moodGrid' && baseColorId && block.colorId
                  && block.colorId !== baseColorId && (
                  <span className="sb-hooksheet-gen">자동 생성</span>
                )}
              </span>
            );
          })}
        </span>
      </button>
    </div>
  );
}

const ShuffleIcon = (
  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
    strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <polyline points="16 3 21 3 21 8" /><line x1="4" y1="20" x2="21" y2="3" />
    <polyline points="21 16 21 21 16 21" /><line x1="15" y1="15" x2="21" y2="21" /><line x1="4" y1="4" x2="9" y2="9" />
  </svg>
);

function frameUnits(items) {
  const units = [];
  for (let index = 0; index < items.length;) {
    const rowId = items[index].block.layoutRowId;
    if (rowId) {
      let end = index + 1;
      while (end < items.length && items[end].block.layoutRowId === rowId) end += 1;
      if (end - index === 2) {
        units.push({ kind: 'frame', items: items.slice(index, end) });
        index = end;
        continue;
      }
    }
    units.push({ kind: 'card', items: [items[index]] });
    index += 1;
  }
  return units;
}

function canvasUnits(items) {
  const units = [];
  for (let index = 0; index < items.length;) {
    const spaceGroupId = items[index].block.spaceGroupId;
    if (spaceGroupId) {
      let end = index + 1;
      while (end < items.length && items[end].block.spaceGroupId === spaceGroupId
        && items[end].block.sectionId === items[index].block.sectionId) end += 1;
      units.push({ kind: 'spaceRun', spaceGroupId, items: items.slice(index, end) });
      index = end;
      continue;
    }
    let end = index + 1;
    while (end < items.length && !items[end].block.spaceGroupId) end += 1;
    units.push(...frameUnits(items.slice(index, end)));
    index = end;
  }
  return units;
}

const nextSeparatedSpaceGroupId = (setId) => spaceSetGroupId(setId, uid('sg'));
const ensureContiguousSpaceRuns = (blocks) => (
  rekeySeparatedSpaceRuns(blocks, nextSeparatedSpaceGroupId)
);
const normalizeStoryboardMutation = (blocks) => ensureContiguousSpaceRuns(normalizeBoard(blocks));

const SECTION_ORDER = new Map(SECTION_ROLE_OPTIONS.map((option, index) => [option.value, index]));

/* 섹션은 블록이 0장이 되어도 사라지지 않는다. 빈 밴드의 start는 다음 섹션
   첫 블록 앞(없으면 맨 끝)이라 그 자리에서 새 블록을 다시 만들 수 있다. */
function deriveFixedSections(blocks) {
  const runs = deriveSections(blocks);
  return SECTION_ROLE_OPTIONS.flatMap((option, order) => {
    const existing = runs.filter((section) => section.role === option.value);
    if (existing.length) return existing;
    const nextIndex = blocks.findIndex((block) => (SECTION_ORDER.get(block.sectionRole) ?? 99) > order);
    return [{
      id: `empty:${option.value}`,
      title: option.label,
      role: option.value,
      layout: 'stack',
      custom: false,
      samePlace: false,
      spaceGroupId: null,
      start: nextIndex < 0 ? blocks.length : nextIndex,
      items: [],
    }];
  });
}

function dragGroupFor(blocks, id) {
  const index = blocks.findIndex((b) => b.id === id);
  if (index < 0) return null;
  const block = blocks[index];
  if (!block.layoutRowId) return { indexes: [index], ids: [id], items: [block] };
  const indexes = [];
  blocks.forEach((candidate, i) => {
    if (candidate.layoutRowId === block.layoutRowId && candidate.sectionId === block.sectionId) indexes.push(i);
  });
  const contiguous = indexes.length > 1 && indexes.every((value, i) => i === 0 || value === indexes[i - 1] + 1);
  if (!contiguous) return { indexes: [index], ids: [id], items: [block] };
  return { indexes, ids: indexes.map((i) => blocks[i].id), items: indexes.map((i) => blocks[i]) };
}

/* underline tab navigation for 컷 종류 — sliding indicator */
export function UnderlineTabs({ options, value, onChange }) {
  const ref = React.useRef(null);
  const [line, setLine] = React.useState({ left: 0, width: 0 });
  React.useEffect(() => {
    const el = ref.current; if (!el) return;
    const active = el.querySelector('.utab.on');
    if (active) setLine({ left: active.offsetLeft, width: active.offsetWidth });
  }, [value]);
  // initial measure after first paint
  React.useEffect(() => {
    const el = ref.current; if (!el) return;
    requestAnimationFrame(() => { const a = el.querySelector('.utab.on'); if (a) setLine({ left: a.offsetLeft, width: a.offsetWidth }); });
  }, []);
  return (
    <div className="utabs" ref={ref} style={{ '--ul-left': line.left + 'px', '--ul-width': line.width + 'px' }}>
      {options.map((o) => (
        <button key={o.value} disabled={o.disabled}
          title={o.disabled ? (o.disabledReason === false ? undefined : (o.disabledReason || '이 조건은 아직 서비스에 공개되지 않았어요')) : undefined}
          className={`utab${value === o.value ? ' on' : ''}`} onClick={() => onChange(o.value)}>{o.label}</button>
      ))}
    </div>
  );
}

/* 대상 색상 — colored circles only (from product input) */
export function ColorDots({ colorOpts, value, onChange }) {
  return (
    <div className="color-dots">
      {colorOpts.map((c) => (
        <button key={c.id} className={`color-dot${value === c.id ? ' on' : ''}`} title={c.label} onClick={() => onChange(c.id)}>
          <span className="cd-fill" style={{ background: c.hex }} />
        </button>
      ))}
    </div>
  );
}

function ExampleThumb({ example }) {
  const [attempt, setAttempt] = useState(0);
  const [failed, setFailed] = useState(false);
  useEffect(() => { setAttempt(0); setFailed(false); }, [example?.thumb]);
  if (failed) return (
    <span className="sb-exthumb-error">썸네일을 불러오지 못했어요
      <span role="button" tabIndex={0} onClick={(event) => {
        event.stopPropagation(); setAttempt((value) => value + 1); setFailed(false);
      }} onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault(); event.stopPropagation(); setAttempt((value) => value + 1); setFailed(false);
        }
      }}>다시 시도</span>
    </span>
  );
  const separator = String(example?.thumb || '').includes('?') ? '&' : '?';
  return <img src={`${example.thumb}${attempt ? `${separator}retry=${attempt}` : ''}`} alt="" onError={() => setFailed(true)} />;
}

function ShotSegment({
  options, value, onChange, cut, clothingType, gender, isOptionPublished = null,
}) {
  return (
    <div className="seg sb-shot-seg" data-count={options.length}
      data-idx={Math.max(0, options.findIndex((option) => option.value === value))}
      aria-label={cut === 'product' ? '제품컷 형식' : '샷 종류'}>
      {options.map((option) => {
        const published = !option.disabled && (isOptionPublished
          ? isOptionPublished(option.value)
          : isGenerationCombinationPublic({
            cutType: cut, shot: option.value, clothingType, gender,
          }));
        return (
          <button key={option.value} type="button" className={value === option.value ? 'on' : ''}
            disabled={!published} aria-pressed={value === option.value}
            title={!published ? '이 조건은 아직 서비스에 공개되지 않았어요' : undefined}
            onClick={() => onChange(option.value)}>{option.label}</button>
        );
      })}
    </div>
  );
}

const MINE_SHOT_OPTION = Object.freeze({ value: 'mine', label: '내 이미지' });

function MineImageTab({ images = [], onImagesChange, onChoose, onPickImage }) {
  const upload = async () => {
    const picked = await onPickImage?.();
    if (!picked) return;
    if (onChoose) {
      onChoose(picked);
      return;
    }
    onImagesChange?.([...images, picked]);
  };
  return (
    <div className="sb-mine-tab">
      <p>가지고 있는 이미지를 그대로 넣어요. AI 생성 옵션은 적용되지 않습니다.</p>
      {images.length > 0 && (
        <div className="sb-mine-grid">
          {images.map((image, index) => {
            const src = image?.url || image;
            return (
              <span key={`${src}:${index}`} className={`sb-excell up${onChoose ? ' usable' : ''}`}
                role={onChoose ? 'button' : undefined} tabIndex={onChoose ? 0 : undefined}
                title={onChoose ? '이 이미지로 바꾸기' : undefined}
                onClick={onChoose ? () => onChoose(image) : undefined}
                onKeyDown={onChoose ? (event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault(); onChoose(image);
                  }
                } : undefined}>
                <img src={src} alt="" /><span className="upb">내 사진</span>
                <button type="button" className="rm" aria-label="내 이미지 삭제"
                  onClick={(event) => {
                    event.stopPropagation();
                    onImagesChange?.(images.filter((_, itemIndex) => itemIndex !== index));
                  }}><Icon name="x" size={11} /></button>
              </span>
            );
          })}
        </div>
      )}
      <button type="button" className="ref-upload" onClick={upload}>
        <Icon name="upload" size={16} />로컬에서 이미지 업로드
      </button>
    </div>
  );
}

function SpaceSetCard({
  set,
  interactive = true,
  currentCutOrdinal = null,
  onChoose,
  onPreviewOpen,
  onPreviewClose,
}) {
  const content = (
    <>
      <span className="sb-set-polaroids" aria-hidden="true"
        style={{ '--set-member-count': set?.members?.length || 0 }}>
        {(set?.members || []).map((member, index) => (
          <span key={member.exampleId || `${member.direction}:${member.shot}:${index}`} className={`sb-set-polaroid p${index + 1}`}>
            {member.thumb
              ? <img src={member.thumb} alt="" />
              : <span className={member.shot === 'medium' ? 'figure medium' : 'figure'} />}
          </span>
        ))}
      </span>
      <strong>{spaceSetDisplayName(set)}</strong>
      <small>{set?.compositionLabel}</small>
      {Number.isInteger(currentCutOrdinal) && (
        <span className="sb-set-current-cut">현재 선택 · {currentCutOrdinal}번째 컷</span>
      )}
    </>
  );
  const className = `sb-set-card ${interactive ? 'is-interactive' : 'is-static'} tone-${set?.tone || 'neutral'}`;
  if (!interactive) return <div className={className}>{content}</div>;
  return (
    <button type="button" className={className}
      onMouseEnter={(event) => onPreviewOpen?.(set, event.currentTarget)}
      onMouseLeave={onPreviewClose}
      onFocus={(event) => onPreviewOpen?.(set, event.currentTarget, 0)}
      onBlur={onPreviewClose}
      onClick={() => onChoose?.(set)}>
      {content}
    </button>
  );
}

function SpaceSetInspectorHeader({ set, siblings, block, onChangeSet }) {
  const siblingIndex = siblings.findIndex((sibling) => sibling.id === block.id);
  const ordinal = Number.isInteger(block.spaceSetMemberOrder)
    ? block.spaceSetMemberOrder
    : siblingIndex + 1;
  return (
    <div className="sb-space-inspector-context">
      <SpaceSetCard set={set} interactive={false} currentCutOrdinal={ordinal} />
      <button type="button" className="sb-space-set-change" onClick={onChangeSet}>장소 세트 변경</button>
    </div>
  );
}

function SpaceSetGallery({ mode, error, onChoose, onClose, gender, clothingType }) {
  const replacing = mode === 'replace';
  const spaceSets = storyboardSpaceSetsFor({ gender, clothingType });
  const [preview, setPreview] = useState(null);
  const previewTimer = useRef(null);
  useEffect(() => () => clearTimeout(previewTimer.current), []);
  const closePreview = () => {
    clearTimeout(previewTimer.current);
    setPreview(null);
  };
  const openPreview = (set, anchor, delay = 200) => {
    clearTimeout(previewTimer.current);
    previewTimer.current = setTimeout(() => {
      const rect = anchor.getBoundingClientRect();
      const width = 316;
      const height = 58 + Math.ceil(set.members.length / 3) * 124;
      const gap = 12;
      const leftSide = rect.left - width - gap;
      const rightSide = rect.right + gap;
      const itemIndex = [...anchor.parentElement.children].indexOf(anchor);
      const preferred = itemIndex % 2 === 0 ? leftSide : rightSide;
      const fallback = itemIndex % 2 === 0 ? rightSide : leftSide;
      const fits = (value) => value >= 8 && value + width <= window.innerWidth - 8;
      const left = fits(preferred)
        ? preferred
        : Math.min(Math.max(8, fallback), window.innerWidth - width - 8);
      const top = Math.min(
        Math.max(8, rect.top),
        Math.max(8, window.innerHeight - height - 8),
      );
      setPreview({ set, left, top, width });
    }, delay);
  };
  return (
    <div className="surface inspector sb-set-picker">
      <div className="sb-set-picker-head">
        <div>
          <div className="sec-title">{replacing ? '장소 세트 변경' : '장소 세트 추가'}</div>
          <p>{replacing
            ? '고르면 공간과 구성 컷 전체가 한 번에 바뀌어요.'
            : '장소 세트 카드 하나에 공간과 어울리는 컷 구성이 함께 들어 있어요.'}</p>
        </div>
        <button type="button" className="sb-set-picker-close" onClick={onClose} aria-label="장소 세트 갤러리 닫기"><Icon name="x" size={16} /></button>
      </div>
      <div className="sb-set-grid">
        {spaceSets.map((set) => (
          <SpaceSetCard key={set.id} set={set} onChoose={onChoose}
            onPreviewOpen={openPreview} onPreviewClose={closePreview} />
        ))}
        {!spaceSets.length && <div className="sb-set-empty">이 상품에 맞는 장소 세트를 준비 중이에요.</div>}
      </div>
      {error && <div className="sb-save-error">{error}</div>}
      {preview && createPortal(
        <div className="sb-set-preview" role="tooltip"
          style={{ left: preview.left, top: preview.top, width: preview.width }}>
          <strong>{spaceSetDisplayName(preview.set)}</strong>
          <span>{preview.set.compositionLabel}</span>
          <div className="sb-set-preview-grid">
            {preview.set.members.map((member, index) => (
              <figure key={member.exampleId || index}>
                {member.thumb ? <img src={member.thumb} alt="" /> : <span />}
                <figcaption>{index + 1}번째 컷</figcaption>
              </figure>
            ))}
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}


export function shouldRenderGenerationExampleGuide(block) {
  return !block?.spaceGroupId;
}

/* 분위기 예시 — 갤러리가 주인공 (B+C안 확정, ADR-0004):
   · 샷 종류 = 갤러리 헤더 세그먼트 (설정과 같은 shot 필드를 바꾼다)
   · 생성예시 셀 선택 = 촬영 연출만 참고 — 예시 속 옷·신발·액세서리는 제외하고 exampleId로 생성 입력에 포함
   · 내 사진(refImages) = 샷 종류의 '내 이미지' 탭에서 업로드·선택
   · 카드가 사이드/뒷면이어도 선택한 예시의 전체 연출을 참고하되, 카드의 촬영 방향은 유지
   refs/exampleId 는 제어형 — 콘티는 블록이, 에디터 AI 패널은 패널 상태가 소유 (계약 §3.4/§6). */
export function MoodGuide({ catalogs, cut, blockCutType = cut, direction, shot, onShotChange, shotOptions = null, clothingType = 'top', gender = null, exampleId, onExampleChange, onExampleDrag = null, refs = [], onRefsChange, onPickRef, refScope = 'all', onUseMine = null, includeMirrorExamples = false }) {
  const galleryCut = cut === 'mirror' ? 'styling' : cut;
  const shotOpts = shotOptions || (cut === 'product' ? catalogs.productShotTypes
    : catalogs.shotTypes);
  const shotVal = shotOpts.some((s) => s.value === shot) ? shot : shotOpts[0].value;
  const examples = React.useMemo(() => selectGenerationExamples(catalogs.genExamples, {
    cutType: galleryCut,
    shot: shotVal,
    clothingType,
    gender,
    direction,
    appendSetOnly: cut !== 'product',
    appendMirror: includeMirrorExamples && galleryCut === 'styling',
  }), [catalogs.genExamples, cut, galleryCut, shotVal, clothingType, gender, direction, includeMirrorExamples]);
  const selectedExample = (catalogs.genExamples || []).find((example) => example.id === exampleId) || null;
  const moodOnly = (cut === 'styling' || cut === 'horizon') && !!direction && direction !== 'front';
  const conditionStatus = !exampleId ? null : storedExampleConditionStatus(selectedExample, {
    cutType: galleryCut, blockCutType, clothingType, gender,
    includeMirror: includeMirrorExamples && galleryCut === 'styling',
  });
  const selectedPoseCompatible = (selectedExample?.variants || []).includes('pose')
    && poseExampleDirectionCompatible(selectedExample, {
      cutType: selectedExample?.cutType || cut,
      direction,
    });
  const selectedStatus = conditionStatus === 'valid'
    && refScope === 'pose' && !selectedPoseCompatible
    ? 'changed' : conditionStatus;
  const galleryRef = useRef(null);
  const [galleryPage, setGalleryPage] = useState(0);
  const [mineTab, setMineTab] = useState(false);
  const galleryPageCount = Math.max(1, Math.ceil(examples.length / 6));
  const scrollToGalleryPage = (page, behavior = 'smooth') => {
    const targetPage = Math.max(0, Math.min(page, galleryPageCount - 1));
    const element = galleryRef.current?.querySelectorAll('.sb-expage')[targetPage];
    if (element) galleryRef.current.scrollTo({ left: element.offsetLeft, behavior });
    setGalleryPage(targetPage);
  };
  useEffect(() => {
    scrollToGalleryPage(0, 'auto');
  }, [cut, shotVal, clothingType, gender, direction]);
  useEffect(() => {
    if (galleryPage >= galleryPageCount) scrollToGalleryPage(galleryPageCount - 1, 'auto');
  }, [galleryPage, galleryPageCount]);
  const poseDirectionReason = (example) => {
    const label = { front: '정면', back: '뒷면', side: '사이드' }[example?.direction] || '다른 방향';
    return `이 예시의 포즈는 ${label} 전용이에요`;
  };
  const selectFirstAvailable = () => {
    const first = refScope === 'pose'
      ? examples.find((example) => (example.variants || []).includes('pose')
        && poseExampleDirectionCompatible(example, { cutType: example.cutType || cut, direction }))
      : examples[0];
    if (first) onExampleChange?.(first.id, refScope);
  };
  const renderExampleCell = (example) => {
    const on = exampleId === example.id;
    const variants = Array.isArray(example.variants) ? example.variants : [];
    const poseCompatible = poseExampleDirectionCompatible(example, {
      cutType: example.cutType || cut,
      direction,
    });
    const poseRequired = refScope === 'pose';
    const poseUnavailable = poseRequired && (!variants.includes('pose') || !poseCompatible);
    const poseUnavailableReason = !variants.includes('pose')
      ? '이 예시는 아직 포즈 전용 자산이 없어요'
      : poseDirectionReason(example);
    const pick = (scope) => {
      if (!onExampleChange || !variants.includes(scope)) return;
      if (scope === 'pose' && !poseCompatible) return;
      onExampleChange(example.id, scope);
    };
    const defaultScope = cut === 'product' || (moodOnly && !poseRequired) ? 'all'
      : variants.includes(refScope || 'all')
        && ((refScope || 'all') !== 'pose' || poseCompatible)
        ? (refScope || 'all') : 'all';
    return (
      <button key={example.id} type="button"
        draggable={Boolean(onExampleDrag)}
        onDragStart={(event) => {
          if (!onExampleDrag) return;
          event.dataTransfer.effectAllowed = 'copy';
          event.dataTransfer.setData('text/example-id', example.id);
          onExampleDrag(example.id);
        }}
        onDragEnd={() => onExampleDrag?.(null)}
        disabled={poseUnavailable}
        title={poseUnavailable ? poseUnavailableReason : undefined}
        className={`sb-excell${on ? ' sel' : ''}${poseUnavailable ? ' unavailable' : ''}`}
        onClick={() => pick(defaultScope)}>
        <ExampleThumb example={example} />
        {on && <span className="ck"><Icon name="check" size={11} /></span>}
        {/* MVP 이후 재도입 — 포즈 탭 형태 검토. refScope 필드와 선택 기본값 로직은 유지한다. */}
      </button>
    );
  };
  const exampleCells = examples.map(renderExampleCell);
  const galleryItems = exampleCells;
  const galleryPages = paginateGenerationGalleryItems(galleryItems);
  const pickReference = async () => {
    if (!onRefsChange) return;
    return onPickRef
      ? onPickRef()
      : api.pickRefImage(useAppStore.getState().projectId);
  };
  const updateGalleryPageFromScroll = () => {
    const pages = [...(galleryRef.current?.querySelectorAll('.sb-expage') || [])];
    if (!pages.length) return;
    const left = galleryRef.current.scrollLeft;
    let closest = 0;
    pages.forEach((page, index) => {
      if (Math.abs(page.offsetLeft - left) < Math.abs(pages[closest].offsetLeft - left)) closest = index;
    });
    setGalleryPage(closest);
  };
  return (
    <div className="insp-sec">
      <div className="sb-exhead">
        <label className="lbl">{cut === 'product' ? '생성 예시' : '분위기 예시'}</label>
        {onShotChange
          ? <ShotSegment options={(cut === 'styling' || cut === 'horizon') ? [
            ...shotOpts, { ...MINE_SHOT_OPTION, disabled: !onRefsChange || !onUseMine },
          ] : shotOpts} value={mineTab ? 'mine' : shotVal} onChange={(value) => {
            if (value === 'mine') setMineTab(true);
            else { setMineTab(false); onShotChange(value); }
          }}
            cut={cut} clothingType={clothingType} gender={gender}
            isOptionPublished={cut !== 'product' ? (candidateShot) => candidateShot === 'mine' || hasSelectableGenerationExamples(
              catalogs.genExamples,
              {
                cutType: galleryCut,
                shot: candidateShot,
                clothingType,
                gender,
                direction,
                appendSetOnly: true,
                appendMirror: includeMirrorExamples && galleryCut === 'styling',
              },
            ) : null} />
          : <span className="sb-exhint">내 사진은 이 프로젝트에서만</span>}
      </div>
      {mineTab ? (
        <MineImageTab images={refs} onImagesChange={onRefsChange} onChoose={onUseMine}
          onPickImage={pickReference} />
      ) : <>
      {exampleId && selectedStatus !== 'valid' && (
        <div className="sb-current-example has-error">
          <div className="sb-example-error">{selectedExample ? '조건이 바뀌어 예시를 다시 골라주세요' : '저장된 예시를 불러오지 못했어요'}</div>
          {examples.length > 0 && (
            <button type="button" className="sb-example-retry" onClick={selectFirstAvailable}>다시 선택</button>
          )}
        </div>
      )}
      <div className={`sb-exgallery${moodOnly ? ' moodonly' : ''}`}
        role="region" aria-label="생성예시 갤러리" tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === 'ArrowLeft') {
            event.preventDefault(); scrollToGalleryPage(galleryPage - 1);
          } else if (event.key === 'ArrowRight') {
            event.preventDefault(); scrollToGalleryPage(galleryPage + 1);
          }
        }}>
        <div ref={galleryRef} className="sb-exgrid"
          onScroll={updateGalleryPageFromScroll}
          onWheel={(event) => {
            if (galleryPageCount <= 1 || Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
            event.preventDefault();
            scrollToGalleryPage(galleryPage + (event.deltaY > 0 ? 1 : -1));
          }}>
          {galleryPages.map((pageItems, pageIndex) => (
            <div className="sb-expage" key={`page:${pageIndex}`}>
              {pageItems}
              {!galleryItems.length && (
                <div className="sb-exempty">
                  {isGenerationCombinationPublic({ cutType: cut, shot: shotVal, clothingType, gender })
                    ? '이 조건의 생성예시를 불러오지 못했어요' : '이 조건은 아직 서비스에 공개되지 않았어요'}
                  <button type="button" onClick={() => globalThis.location?.reload()}>다시 시도</button>
                </div>
              )}
            </div>
          ))}
        </div>
        {galleryPageCount > 1 && (
          <div className="sb-excontrols">
            <button type="button" className="sb-expage-hit prev" aria-label="이전 예시 페이지"
              disabled={galleryPage === 0} onClick={() => scrollToGalleryPage(galleryPage - 1)}>‹</button>
            <div className="sb-expages" aria-label={`${galleryPageCount}페이지 중 ${galleryPage + 1}페이지`}>
              {galleryPages.map((_page, index) => (
                <button type="button" key={index} className={index === galleryPage ? 'on' : ''}
                  aria-label={`${index + 1}페이지`} aria-current={index === galleryPage ? 'page' : undefined}
                  onClick={() => scrollToGalleryPage(index)} />
              ))}
            </div>
            <button type="button" className="sb-expage-hit next" aria-label="다음 예시 페이지"
              disabled={galleryPage === galleryPageCount - 1} onClick={() => scrollToGalleryPage(galleryPage + 1)}>›</button>
          </div>
        )}
      </div>
      </>}
    </div>
  );
}

function Inspector({ block, catalogs, colorOpts, detailColorOpts, clothingType, exampleGender, hasDetailImage, projectId, onChange, onAtomicChange, onRetryAtomicSave, requestedRecipe, onCancelRequestedRecipe, matchClothing, spaceContext, onChangeSpaceSet, onAddMine, onExampleDrag, hookStyle = null }) {
  const [matchOpen, setMatchOpen] = useState(false);
  const [pendingRecipe, setPendingRecipe] = useState(null);
  const [pendingChoice, setPendingChoice] = useState(null);
  const [pendingError, setPendingError] = useState(null);
  const [pendingSaving, setPendingSaving] = useState(false);
  const [pickerSaveError, setPickerSaveError] = useState(null);
  const [pickerSaving, setPickerSaving] = useState(false);
  const [emptyMineOpen, setEmptyMineOpen] = useState(false);
  const [emptyMineImages, setEmptyMineImages] = useState([]);
  useEffect(() => { setMatchOpen(false); }, [block?.id]);
  useEffect(() => {
    // block과 requestedRecipe가 둘 다 null이면 undefined===undefined로 참이 되어
    // null.cutType을 읽다 죽는다(같은 카드 재클릭=선택 해제 시 백지 화면의 원인, 2026-07-29)
    setPendingRecipe(requestedRecipe && block && requestedRecipe.blockId === block.id
      ? { cutType: requestedRecipe.cutType, shot: requestedRecipe.shot } : null);
    setPendingChoice(null); setPendingError(null); setPendingSaving(false);
    setPickerSaveError(null); setPickerSaving(false);
  }, [block?.id, requestedRecipe?.blockId, requestedRecipe?.cutType, requestedRecipe?.shot]);

  if (!block && !emptyMineOpen) return (
    <div className="surface inspector empty-insp">
      <EmptyState icon="layout" title="블록을 선택해 수정하세요" desc="좌측에서 수정하고싶은 카드를 선택하거나 아래 버튼으로 내 이미지를 추가하세요." />
      <button className="mine-add-big" onClick={() => setEmptyMineOpen(true)}><Icon name="upload" size={20} />내 이미지 업로드</button>
    </div>
  );

  if (!block) return (
    <div className="surface inspector">
      <div className="sb-exhead">
        <label className="lbl">샷 종류</label>
        <ShotSegment options={catalogs.shotTypes.map((option) => ({ ...option, disabled: true })).concat(MINE_SHOT_OPTION)}
          value="mine" onChange={() => {}} cut="styling" clothingType={clothingType} gender={exampleGender}
          isOptionPublished={(value) => value === 'mine'} />
      </div>
      <MineImageTab images={emptyMineImages} onImagesChange={setEmptyMineImages}
        onPickImage={() => api.pickAnyImage(projectId)} onChoose={(image) => onAddMine(mineImageUrl(image))} />
    </div>
  );

  const closureOptions = catalogs.outerClosureStates || [];
  const isMine = block.source === 'mine';
  const isProduct = block.cutType === 'product';
  const isMirror = block.cutType === 'mirror';
  const isDetail = block.contentRole === CONTENT_ROLES.DETAIL;
  const shouldRenderGenerationExamples = shouldRenderGenerationExampleGuide(block);
  const effectiveSectionRole = requestedRecipe?.sectionRole || block.sectionRole;
  const pendingInSpace = !!block.spaceGroupId && !requestedRecipe;
  // 디테일 샷 상시 제공(2026-08-07 개편) — 디테일 사진이 없어도 서버가 원본 구조 확대로 생성
  const productShotOptions = catalogs.productShotTypes;
  const hasSelectableExamples = (cutType, shot) => hasSelectableGenerationExamples(
    catalogs.genExamples,
    {
      cutType,
      shot,
      clothingType,
      gender: exampleGender,
      appendSetOnly: cutType !== 'product',
    },
  );
  const cutTypeOptions = cutTypeOptionsForSection(effectiveSectionRole).map((option) => {
    const shots = option.value === 'product'
      ? productShotOptions.map((item) => item.value)
      : catalogs.shotTypes.map((item) => item.value);
    return {
      ...option,
      disabled: !shots.some((shot) => hasSelectableExamples(option.value, shot)),
    };
  });
  const onCutTypeChange = (cutType) => {
    if (block.cutType === cutType) { setPendingRecipe(null); return; }
    const availableShots = cutType === 'product' ? productShotOptions : catalogs.shotTypes;
    const shot = availableShots.find((option) => option.value === block.shot
      && hasSelectableExamples(cutType, option.value))?.value
      || availableShots.find((option) => hasSelectableExamples(cutType, option.value))?.value
      || block.shot;
    setPendingError(null);
    setPendingChoice(null);
    setPendingRecipe({ cutType, shot });
  };
  const onShotChange = (shot) => {
    if (block.cutType === 'product') {
      if (block.shot === shot) { setPendingRecipe(null); return; }
      setPendingError(null);
      setPendingChoice(null);
      setPendingRecipe({ cutType: 'product', shot });
      return;
    }
    onChange((current) => {
      if (!current.spaceGroupId || !current.exampleId) {
        return {
          shot,
          exampleSelectionOrigin: current.exampleId ? 'user' : null,
        };
      }
      return { shot, refScope: 'pose', exampleSelectionOrigin: 'user' };
    });
  };
  const commitPendingRecipe = async (exampleId) => {
    if (!pendingRecipe || pendingSaving) return;
    const example = (catalogs.genExamples || []).find((item) => item.id === exampleId);
    if (!example) return;
    const current = block;
    const nextRole = pendingRecipe.cutType === 'product'
      ? (pendingRecipe.shot === 'detail' ? CONTENT_ROLES.DETAIL : CONTENT_ROLES.PRODUCT_OVERVIEW)
      : [SECTION_ROLES.STYLING, SECTION_ROLES.STUDIO].includes(effectiveSectionRole)
        ? WORN_ROLE_BY_CUT_TYPE[pendingRecipe.cutType]
        : [CONTENT_ROLES.HERO, CONTENT_ROLES.BENEFIT].includes(current.contentRole)
          ? current.contentRole : defaultContentRoleForSection(effectiveSectionRole);
    const baseRecipePatch = normalizedRecipePatch({
      ...current,
      source: 'ai',
      sectionRole: effectiveSectionRole,
      cutType: pendingRecipe.cutType,
      shot: pendingRecipe.shot,
    }, nextRole, { hasDetailImage });
    const recipePatch = {
      ...baseRecipePatch,
      ...generationExampleStructuralRecipePatch({ ...current, ...baseRecipePatch }, example),
    };
    const nextColorOpts = nextRole === CONTENT_ROLES.DETAIL ? detailColorOpts : colorOpts;
    const colorId = nextColorOpts.some((color) => color.id === current.colorId)
      ? current.colorId : nextColorOpts[0]?.id;
    const changes = referenceFeedbackPatch(current, {
      ...recipePatch,
      source: 'ai',
      shot: recipePatch.shot,
      colorId,
      pose: 'auto',
      poseLabel: 'AI 자동',
      angle: 'same',
      exampleId,
      baseThumb: current.baseThumb ?? current.thumb,
      exampleSelectionOrigin: 'user',
      refScope: pendingInSpace ? 'pose' : 'all',
      outerClosureState: clothingType === 'outer' && WORN_CUT_TYPES.has(recipePatch.cutType)
        ? (closureOptions.some((option) => option.value === current.outerClosureState)
          ? current.outerClosureState : 'open')
        : null,
      ...(recipePatch.cutType === 'product' ? { matchIds: [], faceExposure: null } : {}),
      // 샷 전환 확정도 공통 규칙 적용 — 뒷면 고스트→디테일 전환 시 이전 back 이
      // 숨은 상태로 남아 BackDetail 근거로 새어 나가는 것을 막는다(Codex 리뷰 P1).
      ...(recipePatch.cutType === 'product' && recipePatch.shot === 'detail'
        ? { direction: detailDirectionFromExample(example) } : {}),
    }, catalogs);
    setPendingChoice(exampleId);
    setPendingSaving(true);
    setPendingError(null);
    try {
      await onAtomicChange(changes, { pickerOwnsError: true });
      setPendingRecipe(null);
      setPendingChoice(null);
    } catch {
      setPendingError('변경 내용을 저장하지 못했어요');
    } finally {
      setPendingSaving(false);
    }
  };
  const generationExamplePatch = (current, example, scope) => {
    const selected = generationExampleSelectionPatch(current, example, {
      clothingType,
      defaultColorId: (isDetail ? detailColorOpts : colorOpts)[0]?.id,
      refScope: scope,
    });
    return {
      changes: referenceFeedbackPatch(current, {
        ...selected.patch,
        baseThumb: current.baseThumb ?? current.thumb,
      }, catalogs),
      settingsReset: selected.settingsReset,
    };
  };
  const onGenerationExampleChange = (exampleId, scope) => {
    const example = (catalogs.genExamples || []).find((item) => item.id === exampleId);
    if (!example) return undefined;
    if (block.spaceGroupId) {
      const selected = generationExamplePatch(block, example, scope);
      setPickerSaveError(null);
      setPickerSaving(true);
      return onAtomicChange(selected.changes, {
        retryAtomic: true,
        pickerOwnsError: true,
        undoLabel: selected.settingsReset ? '예시·설정 초기화' : '참조',
      }).catch(() => {
        setPickerSaveError('변경 내용을 저장하지 못했어요');
      }).finally(() => setPickerSaving(false));
    }
    const selected = generationExamplePatch(block, example, scope);
    onChange(selected.changes, {
      undoLabel: selected.settingsReset ? '예시·설정 초기화' : '참조',
    });
    return undefined;
  };
  const onDirectionChange = (direction) => onChange((current) => {
    if (!current.exampleId) return { direction };
    if (!current.spaceGroupId && current.refScope !== 'pose') return { direction };
    const example = (catalogs.genExamples || []).find((item) => item.id === current.exampleId);
    const compatible = (example?.variants || []).includes('pose')
      && poseExampleDirectionCompatible(example, {
        cutType: current.cutType,
        direction,
      });
    if (compatible) {
      return {
        direction,
        ...(current.spaceGroupId ? { refScope: 'pose' } : {}),
      };
    }
    // 방향이 예시 포즈와 안 맞아도 생성예시는 유지한다(2026-08-15 오너 — 예시를 비워
    // "준비하지 못했어요" 빈 카드를 만들지 않는다). 캡션의 방향 표시만 바뀌고,
    // 서버는 방향 비호환 시 포즈 권한만 내려놓고 생성한다(reference_direction_compatible).
    return {
      direction,
      ...(current.spaceGroupId ? {} : { refScope: 'all' }),
    };
  });
  const showOuterClosure = clothingType === 'outer' && block.source === 'ai' && WORN_CUT_TYPES.has(block.cutType);
  const outerClosureState = closureOptions.some((option) => option.value === block.outerClosureState) ? block.outerClosureState : 'open';
  return (
    <div className="surface inspector">
      {hookStyle && <HookStyleSection {...hookStyle} />}
      {isMine && !block.spaceGroupId ? (
        <>
          <div className="sb-exhead">
            <label className="lbl">샷 종류</label>
            <ShotSegment options={catalogs.shotTypes.map((option) => ({ ...option, disabled: true })).concat(MINE_SHOT_OPTION)}
              value="mine" onChange={() => {}} cut="styling" clothingType={clothingType} gender={exampleGender}
              isOptionPublished={(value) => value === 'mine'} />
          </div>
          <MineImageTab images={block.ownImages || []}
            onImagesChange={(images) => {
              const nextImages = normalizeMineImages(images);
              onChange({ ownImages: nextImages, thumb: nextImages[0] || null });
            }}
            onPickImage={() => api.pickAnyImage(projectId)} onChoose={(selectedImage) => {
              const nextImages = promoteMineImage(block.ownImages, selectedImage);
              onChange({ ownImages: nextImages, thumb: nextImages[0] || null });
            }} />
        </>
      ) : <>
      {block.spaceGroupId && (
        <SpaceSetInspectorHeader set={spaceContext?.set} siblings={spaceContext?.siblings || []}
          block={block} onChangeSet={onChangeSpaceSet} />
      )}
      {!block.cutType ? (
        <>
          <div className="insp-empty-hint"><Icon name="info" size={15} />이 이미지의 생성 설정을 준비하지 못했어요. 블록을 취소하고 다시 추가해주세요.</div>
        </>
      ) : (
        <>
      {/* 세트 멤버는 컷 종류가 세트에 고정 — 잠금 표시 대신 아예 숨긴다(2026-08-15 오너). */}
      {!spaceContext && (
        <div className="insp-sec">
          <div className="sb-cut-label-row"><label className="lbl">컷 종류</label></div>
          <UnderlineTabs
            options={cutTypeOptions}
            value={pendingRecipe?.cutType || (isMirror ? 'styling' : block.cutType)}
            onChange={onCutTypeChange} />
        </div>
      )}

      {pendingRecipe ? (
        <div className="sb-pending-recipe">
          {requestedRecipe && <button type="button" className="insp-cancel-new" onClick={() => {
            setPendingRecipe(null);
            onCancelRequestedRecipe?.();
          }}>섹션 이동 취소</button>}
          {shouldRenderGenerationExamples && (
            <MoodGuide catalogs={catalogs} cut={pendingRecipe.cutType} blockCutType={block.cutType}
              direction={pendingRecipe.cutType === 'mirror' ? null : block.direction} shot={pendingRecipe.shot}
              shotOptions={pendingRecipe.cutType === 'product' ? productShotOptions : null}
              onShotChange={(shot) => setPendingRecipe((current) => ({ ...current, shot }))}
              clothingType={clothingType} gender={exampleGender}
              includeMirrorExamples={effectiveSectionRole === SECTION_ROLES.STYLING || isMirror}
              exampleId={pendingChoice} onExampleChange={commitPendingRecipe} onExampleDrag={onExampleDrag}
              refScope={pendingInSpace ? 'pose' : 'all'} />
          )}
          {pendingError && <div className="sb-save-error">{pendingError}
            <button type="button" disabled={!pendingChoice || pendingSaving}
              onClick={() => commitPendingRecipe(pendingChoice)}>다시 시도</button>
          </div>}
        </div>
      ) : (
        <>
          {shouldRenderGenerationExamples && (
            <MoodGuide onUseMine={(ref) => onChange({
              source: 'mine', title: '내 이미지', cutType: null, contentRole: CONTENT_ROLES.CUSTOM,
              ownImages: [ref?.url || ref], thumb: ref?.url || ref,
              exampleId: null, exampleSelectionOrigin: null, refScope: null,
              refImages: [], refAssetIds: [],
              spaceGroupId: null, spaceVariation: null,
            })} catalogs={catalogs} cut={block.cutType} blockCutType={block.cutType}
              direction={block.direction} shot={block.shot}
              shotOptions={isProduct ? productShotOptions : null}
              onShotChange={onShotChange} clothingType={clothingType} gender={exampleGender}
              includeMirrorExamples={effectiveSectionRole === SECTION_ROLES.STYLING || isMirror}
              exampleId={block.exampleId || null}
              onExampleChange={onGenerationExampleChange}
              onExampleDrag={onExampleDrag}
              refScope={block.refScope || 'all'}
              refs={(block.refImages || []).map((value, index) => ({ url: value?.url || value, assetId: value?.assetId || (block.refAssetIds || [])[index] }))}
              onRefsChange={(references) => onChange({
                refImages: references.map((value) => value?.url || value),
                refAssetIds: references.map((value) => value?.assetId).filter(Boolean),
              })} />
          )}
          {pickerSaveError && <div className="sb-save-error">{pickerSaveError}
            <button type="button" disabled={pickerSaving} onClick={async () => {
              setPickerSaving(true);
              try {
                const retried = await onRetryAtomicSave();
                if (retried) setPickerSaveError(null);
              } catch {
                setPickerSaveError('변경 내용을 저장하지 못했어요');
              } finally {
                setPickerSaving(false);
              }
            }}>다시 시도</button>
          </div>}
        </>
      )}

      {/* 방향 — mirror 생성 레시피는 방향 개념 없음 (ADR-0004).
          디테일 컷도 숨김 — 방향은 셀러가 고르지 않고 선택한 생성예시의 direction 라벨이
          내부적으로 결정한다(2026-08-07 오너 결정, generationExampleSelectionPatch). */}
      {!isMirror && !isDetail && (
        <div className="insp-sec" style={{ marginBottom: 12 }}><label className="lbl">방향</label>
          <Chips options={isProduct ? catalogs.productDirections : catalogs.directions}
            value={(isProduct ? catalogs.productDirections : catalogs.directions).some((d) => d.value === block.direction) ? block.direction : 'front'}
            onChange={onDirectionChange} /></div>
      )}

      {showOuterClosure && (
        <div className="insp-sec outer-closure-field">
          <div className="lbl" id={`outer-closure-label-${block.id}`}>아우터 열림 정도</div>
          <div className="outer-closure-options" role="radiogroup" aria-labelledby={`outer-closure-label-${block.id}`}>
            {closureOptions.map((option) => {
              const on = outerClosureState === option.value;
              return (
                <label key={option.value} className={`outer-closure-option${on ? ' on' : ''}`}>
                  <input type="radio" name={`outer-closure-${block.id}`} value={option.value}
                    checked={on} onChange={() => onChange({ outerClosureState: option.value })} />
                  <OuterClosureIcon state={option.value} />
                  <span>{option.label}</span>
                </label>
              );
            })}
          </div>
          <p className="outer-closure-hint">이 컷에서 아우터의 앞부분을 얼마나 열지 정해요.</p>
        </div>
      )}

      <div className="insp-sec"><label className="lbl">대상 색상</label>
        <ColorDots colorOpts={isDetail ? detailColorOpts : colorOpts}
          value={block.colorId} onChange={(v) => onChange({ colorId: v })} /></div>

      {/* 매칭 의류가 없으면 편집 패널이 빈 화면이 되므로 진입 자체를 막는다.
          세트 멤버(spaceGroupId)는 세트 연출이 정본이라 매칭 편집도 숨긴다(2026-08-15 오너). */}
      {WORN_CUT_TYPES.has(block.cutType) && !block.spaceGroupId
        && Array.isArray(matchClothing) && matchClothing.length > 0 && (
        <>
          <button className={`insp-detail-btn${matchOpen ? ' open' : ''}`} onClick={() => setMatchOpen((v) => !v)}>
            <Icon name="settings" size={17} />매칭 의류 바꾸기
          </button>
          {matchOpen && (
            <div className="sb-match-inline">
              <div className="match-grid">
                {matchClothing.map((m) => {
                  const on = (block.matchIds || []).includes(m.id);
                  return (
                    <button key={m.id} className={`match-cell${on ? ' on' : ''}`} aria-pressed={on}
                      /* origin 'user' = 셀러가 직접 바꾼 매칭 — 카드 우측 하단 오버레이 표시 조건 */
                      onClick={() => onChange({ matchIds: on ? [] : [m.id], matchIdsOrigin: 'user' })}>
                      <img src={m.thumb} alt={m.name} /><span className="ml">{m.name}{on && <Icon name="check" size={12} />}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}

      {/* 추가 옵션(얼굴 노출·앵글)은 필드와 생성 로직을 유지하고 MVP 이후 재도입한다. */}
        </>
      )}
      </>}

    </div>
  );
}

function StoryboardLoadingState() {
  // 빈 div 의 aria-label 은 낭독되지 않을 수 있다 — 숨긴 텍스트 + status 로 알린다(리뷰 반영)
  return (
    <div role="status" aria-busy="true">
      <span className="sr-only">콘티보드를 불러오는 중이에요</span>
    </div>
  );
}

function prepareStoryboardEntry([board, rawCatalogs, matchClothing, product, analysis], sourceBlocks = board) {
  const p = product;
  const a = analysis;
  const hydratedCatalogs = withStoryboardSpaceSetExamples(rawCatalogs);
  const hasDetailImage = hasDetailSource(p);
  const clothingType = p.clothingType || 'top';
  const exampleGender = exampleGenderFromAnalysis(
    a,
    hydratedCatalogs,
    p.clothingType,
  );
  const sectionedBlocks = ensureSections(sourceBlocks, { hasDetailImage }).map((block) => ({
    ...block,
    ...referenceFeedbackPatch(block, {}, hydratedCatalogs),
  }));
  // 서버(mannequin.select_base_gender)와 같은 규칙으로 판정한 성별 — 저장된 카드가 물고 있는
  // 공간 세트 바인딩이 "지금의 분석" 기준으로 여전히 저장 가능한지 서버와 같은 눈으로 본다.
  // 입력에서 성별·의류 종류를 바꾼 뒤 콘티로 오면(이전 버튼 재배치), 안 맞는 세트가 낀 카드가
  // 매 저장마다 space_set_gender_mismatch 등으로 400 나던 것을 — 여기서 바인딩만 떼어 되돌린다.
  const boundGender = genderForClothingType(clothingType, a?.targetGenders);
  // 구 '오프닝 2단 행' 보드는 두컷 프레임의 전신 — 진입 1회, 프레임 표식만 승격한다
  // (컷·순서 불변, 스펙 2026-08-14 §2). 그 밖의 구형 보드는 프레임 없음 = 스택 폴백.
  const adoptedBlocks = adoptHookFrame(sectionedBlocks).blocks;
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
  const normalizedBlocks = ensureContiguousSpaceRuns(matchRepairedBlocks);
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
  const repairedAssignment = repairedIds.length
    ? assignGenerationExamples(normalizedBlocks, {
      catalog: hydratedCatalogs.genExamples,
      product: p,
      gender: boundGender,
      onlyBlockIds: repairedIds,
    })
    : { blocks: normalizedBlocks };
  // 나머지(원래부터 비어 있던 카드 등)는 기존 그대로 exampleGender 로 채운다 — 방금 boundGender
  // 로 채운 카드는 이미 exampleId+origin='auto' 를 갖고 있어 이 호출이 다시 건드리지 않는다.
  const assignment = assignGenerationExamples(repairedAssignment.blocks, {
    catalog: hydratedCatalogs.genExamples,
    product: p,
    gender: exampleGender,
  });
  const allColorOpts = (p.colors || []).map((color, index) => ({
    id: color.id,
    label: hydratedCatalogs.swatchColors.find((swatch) => swatch.id === color.swatchId)?.label
      || color.name?.trim()
      || `색상 ${index + 1}`,
    hex: hexFor(color),
  }));
  const colorOpts = allColorOpts.filter((_option, index) => (
    (p.colors[index].images || []).length || p.colors[index].isBase
  ));
  const fallbackColor = [{ id: 'col1', label: '기본', hex: '#15141a' }];

  return {
    blocks: assignment.blocks,
    catalogs: hydratedCatalogs,
    matchClothing,
    clothingType,
    exampleGender,
    hasDetailImage,
    productName: p.name || '',
    colorOpts: colorOpts.length ? colorOpts : fallbackColor,
    detailColorOpts: allColorOpts.length ? allColorOpts : fallbackColor,
    composeModeSeed: {
      colors: p.colors || [],
      targetGenders: a?.targetGenders || [],
      matchClothing: matchClothing || [],
    },
    normalized,
    assignment,
  };
}

function ComposeModeSegment({ modes, value, canApply, onApply, onError }) {
  const [applying, setApplying] = useState(false);
  if (!modes?.length) return null;

  const selectMode = async (nextMode) => {
    if (!canApply || !nextMode || nextMode === value || applying) return;
    setApplying(true);
    try {
      await onApply(nextMode);
    } catch {
      onError();
    } finally {
      setApplying(false);
    }
  };

  return (
    <div className="sb-compose-segment" role="group" aria-label="사진 양" aria-busy={applying || undefined}>
      {modes.map((mode) => {
        const selected = mode.value === value;
        return (
          <button key={mode.value} type="button" className={selected ? 'on' : ''}
            aria-pressed={selected}
            disabled={applying || (!selected && !canApply)}
            title={!selected && !canApply ? '직접 수정한 콘티에는 적용되지 않아요' : undefined}
            onClick={() => selectMode(mode.value)}>
            {mode.label}
          </button>
        );
      })}
    </div>
  );
}

/* '첫 화면 스타일'(스펙 §3, 2026-08-14 오너 확정) — 상시 우측 패널이 아니라 후킹 섹션
   컷을 클릭했을 때 그 컷 인스펙터의 맨 위에 뜬다. 미리보기를 누르면 스타일 3종이 펼쳐진다. */
const HOOK_STYLE_DESCRIPTIONS = {
  signature: '의류 위주로 확대한 이미지 중간에 제품명을 강조해서 넣었어요.',
  pair: '의류 위주로 표현된 미디움샷 이미지 2개를 붙여서 보여줘요.',
  moodGridByColor: '여러 분위기의 이미지를 색상별로 한번에 보여줘요.',
  moodGridByCuts: '같은 색의 서로 다른 네 컷을 모아 무드를 풍성하게 보여줘요.',
};
const hookStyleDescription = (style, colors) => (
  style === 'moodGrid'
    ? HOOK_STYLE_DESCRIPTIONS[moodGridContent(colors) === 'byColor' ? 'moodGridByColor' : 'moodGridByCuts']
    : HOOK_STYLE_DESCRIPTIONS[style]
);

function HookStyleSection({
  frame, blocks, catalogs, colors, productName, saving, error, onSelectStyle,
  clothingType, gender, isCutAvailable,
}) {
  const [open, setOpen] = useState(false);
  const slots = frame.slotIds
    .map((slotId) => blocks.find((block) => block.id === slotId))
    .filter(Boolean);
  const previewSrc = slots.map((block) => blockPreviewSrc(block, catalogs));
  // 스타일 카드 3종의 대표 이미지 = 발행 카탈로그에서 스타일 슬롯 사양대로 고정 선정
  // (2026-08-14 오너: 현재 보드 컷 대신 스타일 차이가 잘 보이는 고정 이미지).
  // 카탈로그 순위 1번을 쓰므로 발행이 갈리지 않는 한 항상 같은 그림이다.
  const representativeThumb = (cutType, shot) => {
    const example = selectGenerationExamples(catalogs?.genExamples || [], {
      cutType, shot, clothingType, gender, appendSetOnly: cutType !== 'product',
    })[0];
    return example ? generationExampleImageSources(example).src : previewSrc[0];
  };
  const thumbFor = (style) => hookSlotPlan(style, { colors, isCutAvailable })
    .map((slot) => representativeThumb(slot.cutType, slot.shot));
  return (
    <div className="insp-sec sb-hookpanel">
      <label className="lbl">첫 화면 스타일</label>
      <button
        type="button"
        className="sb-hookpanel-live"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className={`sb-hookpanel-sheet style-${frame.style}`}>
          {slots.map((block, index) => (
            <span key={block.id} className="sb-hookpanel-tile">
              <img src={previewSrc[index]} alt="" loading="lazy" decoding="async" />
              {frame.style === 'signature' && productName && (
                <span className="sb-hooksheet-title small"><i>{productName}</i></span>
              )}
            </span>
          ))}
        </span>
        <span className="sb-hookpanel-desc">
          <b>{HOOK_STYLE_LABELS[frame.style]}</b>
          <span>{hookStyleDescription(frame.style, colors)}</span>
          <em>{open ? '스타일 목록 접기' : '미리보기를 눌러 스타일 바꾸기'}</em>
        </span>
      </button>
      {open && (
      <div className="sb-hookpanel-cards" role="group" aria-label="첫 화면 스타일">
        {HOOK_STYLES.map((style) => (
          <button
            key={style}
            type="button"
            className={'sb-hookpanel-card' + (style === frame.style ? ' on' : '')}
            aria-pressed={style === frame.style}
            disabled={saving}
            onClick={() => { onSelectStyle(style); setOpen(false); }}   /* 고르면 목록 접힘(2026-08-15 오너) */
          >
            <span className={`sb-hookpanel-thumb style-${style}`}>
              {thumbFor(style).map((src, index) => (
                <i key={index} style={{ backgroundImage: src ? `url("${src}")` : undefined }} />
              ))}
            </span>
            <span className="sb-hookpanel-name">{HOOK_STYLE_LABELS[style]}</span>
          </button>
        ))}
      </div>
      )}
      {error && <div className="sb-save-error">{error}</div>}
    </div>
  );
}

export function Storyboard() {
  const navigate = useNavigate();
  const initialEntryRef = useRef(undefined);
  if (initialEntryRef.current === undefined) {
    const initialProjectId = useAppStore.getState().projectId;
    const prefetched = initialProjectId && !sbPending.has(initialProjectId)
      ? peekStoryboardEntry(initialProjectId)
      : null;
    initialEntryRef.current = prefetched ? {
      projectId: initialProjectId,
      raw: prefetched,
      prepared: prepareStoryboardEntry(prefetched),
    } : null;
  }
  const initialEntry = initialEntryRef.current?.prepared;
  const [blocks, setBlocks] = useState(() => initialEntry?.blocks || null);
  const [catalogs, setCatalogs] = useState(() => initialEntry?.catalogs || null);
  const [matchClothing, setMatchClothing] = useState(() => initialEntry?.matchClothing || null);
  const [colorOpts, setColorOpts] = useState(() => initialEntry?.colorOpts || []);
  const [detailColorOpts, setDetailColorOpts] = useState(() => initialEntry?.detailColorOpts || []);
  const [clothingType, setClothingType] = useState(() => initialEntry?.clothingType || 'top'); // 샷 필터 아이콘·예시 크롭용 (상의=위/하의=아래)
  const [exampleGender, setExampleGender] = useState(() => initialEntry?.exampleGender || null);
  const [hasDetailImage, setHasDetailImage] = useState(() => initialEntry?.hasDetailImage || false);
  const [productName, setProductName] = useState(() => initialEntry?.productName || '');
  const [hookStyleSaving, setHookStyleSaving] = useState(false);
  const [hookStyleError, setHookStyleError] = useState(null);
  const shuffleTickRef = useRef(0);
  const [composeModeSeed, setComposeModeSeed] = useState(() => initialEntry?.composeModeSeed || ({
    colors: [],
    targetGenders: [],
    matchClothing: [],
  }));
  const [selectedId, setSelectedId] = useState(null);
  const [splitOpen, setSplitOpen] = useState(false); // 한 번이라도 카드를 열면 좌/우 분할 유지
  const [dragId, setDragId] = useState(null);
  const [dragSpaceGroupId, setDragSpaceGroupId] = useState(null);
  const [dragOver, setDragOver] = useState(null);
  const [dragOverSec, setDragOverSec] = useState(null); // 호버 중인 드롭 대상 섹션 — 하이라이트와 드롭이 같은 신호를 쓴다
  const [dragOverSpaceGroupId, setDragOverSpaceGroupId] = useState(null);
  const [dragExampleId, setDragExampleId] = useState(null);
  const [setPicker, setSetPicker] = useState(null);
  const [setPickerError, setSetPickerError] = useState(null);
  const [openGroupKeys, setOpenGroupKeys] = useState([]); // 펼쳐 둔 렌더 그룹들 (다중 허용 · UI 전용)
  const [loadError, setLoadError] = useState(null);
  const [loadRetry, setLoadRetry] = useState(0);
  const [pendingSectionMove, setPendingSectionMove] = useState(null);
  const [atomicSaving, setAtomicSaving] = useState(false);
  const [undoEntry, setUndoEntry] = useState(null);
  const [undoExiting, setUndoExiting] = useState(false);
  const [inspectorTop, setInspectorTop] = useState(70);
  const [initialBoardRevealed, setInitialBoardRevealed] = useState(() => prefersReducedMotion());
  const microVariationIds = repeatedAllExampleVariationIds(
    blocks,
    catalogs?.genExamples,
  );
  const atomicSavingRef = useRef(false);
  const atomicRetryRef = useRef(null);
  const directSaveSnapshots = useRef(new WeakSet());
  const undoEntryRef = useRef(null);
  const undoTimerRef = useRef(null);
  const undoHoveredRef = useRef(false);
  const newSeq = useRef(0);
  const cardRefs = useRef(new Map());
  const setPickerScrollY = useRef(null);
  const toast = useToast();
  // 카피라이팅 토글 = 플로우 선택값 (store → patchProject 동기화, ADR-0002)
  const projectId = useAppStore((s) => s.projectId);
  const composeMode = useAppStore((s) => s.composeMode);
  const setComposeMode = useAppStore((s) => s.setComposeMode);
  const restoreComposeMode = useAppStore((s) => s.restoreComposeMode);
  const copyOn = useAppStore((s) => s.copywriting);
  const setCopyOn = useAppStore((s) => s.setCopywriting);
  const restoreCopyOn = useAppStore((s) => s.restoreCopywriting);
  const composeModeSelectionRef = useRef({ requestId: 0, pending: 0, confirmedMode: composeMode });
  const copywritingSelectionRef = useRef({ requestId: 0, pending: 0, confirmedValue: copyOn });
  const doneBlocked = useDoneGuard();   // 생성 완료 후 초안 재진입 제한 (PRD §10.17)

  useEffect(() => () => clearTimeout(undoTimerRef.current), []);
  useEffect(() => {
    if (!composeModeSelectionRef.current.pending) {
      composeModeSelectionRef.current.confirmedMode = composeMode;
    }
    if (!copywritingSelectionRef.current.pending) {
      copywritingSelectionRef.current.confirmedValue = copyOn;
    }
  }, [composeMode, copyOn]);

  // 분석에서 넘어올 때 이전 화면의 스크롤이 남아 보드 중간부터 보이던 문제 — 진입 시 최상단.
  // 세트픽커의 스크롤 복원(setPickerScrollY)은 마운트가 아니라 상태 변경에 반응하므로 충돌 없음.
  useLayoutEffect(() => { window.scrollTo(0, 0); }, []);

  useLayoutEffect(() => {
    const observed = new Set();
    const measure = () => {
      const topnav = document.querySelector('.topnav');
      // 리본이 복수(마네킹+상세페이지)일 수 있어 스택 전체 높이를 잰다(codex F8)
      const ribbon = document.querySelector('.job-ribbon-stack') || document.querySelector('.job-ribbon');
      const topnavHeight = topnav?.getBoundingClientRect().height || 0;
      const ribbonHeight = ribbon?.getBoundingClientRect().height || 0;
      setInspectorTop(storyboardOverlayTop(topnavHeight, ribbonHeight));
      [topnav, ribbon].filter(Boolean).forEach((element) => {
        if (observed.has(element)) return;
        observed.add(element);
        resizeObserver.observe(element);
      });
    };
    const resizeObserver = new ResizeObserver(measure);
    const mutationObserver = new MutationObserver(measure);
    const shell = document.querySelector('.app-shell');
    if (shell) mutationObserver.observe(shell, { childList: true });
    window.addEventListener('resize', measure);
    measure();
    return () => {
      resizeObserver.disconnect();
      mutationObserver.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, []);

  useLayoutEffect(() => {
    if (setPickerScrollY.current == null) return;
    window.scrollTo({ top: setPickerScrollY.current, behavior: 'instant' });
    setPickerScrollY.current = null;
  }, [setPicker]);

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

  useEffect(() => {
    (async () => {
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
        // 마네킹컷 생성은 오래 걸린다 — 사용자가 콘티를 짜는 동안 백그라운드로 돌린다.
        // await 하지 않는다: 보드 로드가 생성 완료를 기다리면 병렬화가 사라진다. 실패는 리본과
        // 마네킹 화면이 각각 보고하므로 여기선 삼킨다. 중복 호출은 러너와 서버가 함께 흡수한다.
        void requestMannequinGeneration(pid).catch(() => {});
        await sbSaveIdle();     // 직전 인스턴스의 비행 중 저장(이탈 플러시)이 착지한 뒤에 읽는다 — 스테일 로드 방지
        const entry = await consumeStoryboardEntry(pid) || await loadStoryboardEntry(pid);
        const [board] = entry;

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
        if (!usePending) sbLastSaved.set(pid, board);   // 이번 로드의 서버 상태를 기준선으로 기록
        const reuseInitialEntry = !usePending
          && initialEntryRef.current?.projectId === pid
          && initialEntryRef.current?.raw === entry;
        const prepared = reuseInitialEntry
          ? initialEntryRef.current.prepared
          : prepareStoryboardEntry(entry, usePending ? pending : board);
        const initBlocks = prepared.blocks;
        setOpenGroupKeys([]);
        if (!reuseInitialEntry) {
          setBlocks(initBlocks);
          setCatalogs(prepared.catalogs);
          setMatchClothing(prepared.matchClothing);
          setClothingType(prepared.clothingType);
          setExampleGender(prepared.exampleGender);
          setHasDetailImage(prepared.hasDetailImage);
          setDetailColorOpts(prepared.detailColorOpts);
          setColorOpts(prepared.colorOpts);
          setComposeModeSeed(prepared.composeModeSeed);
          setProductName(prepared.productName);
        }
        if (prepared.normalized || prepared.assignment.changed || usePending) {
          const autoAssignmentOnly = prepared.assignment.assignedIds.length > 0
            && prepared.assignment.protectedIds.length === 0 && !prepared.normalized && !usePending;
          try {
            await sbSaveNow(pid, () => initBlocks, { autoAssignment: autoAssignmentOnly });
          } catch {
            // 초기 정규화·자동 배정도 background save다. persistence scheduler가 조용히 재시도한다.
          }
        }
      } catch (error) {
        setLoadError(classifyStoryboardLoadError(error, STORYBOARD_NETWORK_ERROR_MESSAGE));
      }
    })();
  }, [loadRetry]);
  /* 보드 썸네일 선캐싱 — 트리거 ① 보드 내용이 확정/변경될 때마다(진입·컷 추가·예시 교체).
     이미 데운 URL은 모듈 캐시가 걸러내므로 재실행이 중복 요청을 만들지 않는다. */
  useEffect(() => {
    if (!blocks || !catalogs) return undefined;
    const blockImages = blocks.flatMap((block) => [
      block.exampleId
        ? generationExampleImageSources(
          (catalogs.genExamples || []).find((example) => example.id === block.exampleId),
        ).prewarm
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
  const flushLatest = (pid, options = {}) => {
    clearTimeout(saveTimer.current);
    saveTimer.current = null;
    return sbSaveNow(pid, () => latestBlocks.current, options);
  };
  const saveNow = (pid) => flushLatest(pid);
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
    return scheduleStoryboardAutosave(saveTimer, () => {
      flushLatest(projectId).catch(() => {});
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
      targetGenders: composeModeSeed.targetGenders,
    };
  }, [catalogs, clothingType, composeModeSeed]);
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
        gender,
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
  // 컬러 비교 자격 상실(색 통일·시리즈 편입 등) 시 즉시 세로로 강등 — 무효 레이아웃이 저장·조립에 남지 않게.
  // 주의: 훅은 아래 로딩 early-return 위에 있어야 한다 (훅 개수 불변 규칙).
  // autoDemoteTrail: 이 effect 가 만든 배열 → 원본 계보. 삭제-undo 의 "변경 없음" 판정이
  // 자동 강등(계보 위)과 사용자 레이아웃 변경(계보 밖)을 필드가 아니라 **행위자**로 구분하게 한다.
  const autoDemoteTrail = useRef(new WeakMap());
  useEffect(() => {
    if (!blocks) return;
    for (const s of deriveSections(blocks)) {
      if (s.layout !== 'colorCompare') continue;
      const cset = new Set(s.items.filter(({ b }) => b.source !== 'mine' && b.colorId).map(({ b }) => b.colorId));
      if (s.samePlace || cset.size < 2) {
        setBlocks((bs) => {
          const next = patchSection(bs, s.id, { sectionLayout: 'stack' });
          autoDemoteTrail.current.set(next, bs);
          return next;
        });
        return;
      }
    }
  }, [blocks]);
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
  if (!blocks || !catalogs) return <StoryboardLoadingState />;

  const composeModeApplies = isDefaultStoryboardForMode(
    blocks,
    composeModeSeed.colors,
    composeMode,
    {
      projectId,
      clothingType,
      targetGenders: composeModeSeed.targetGenders,
      matchClothing: composeModeSeed.matchClothing,
    },
  );

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
  const showUndo = (previous, next, { blockId, label = '설정' } = {}) => {
    if (!previous || !next || previous === next) return;
    setUndoExiting(false);
    const now = Date.now();
    const active = undoEntryRef.current;
    const before = active ? active.before : [...previous];
    const labels = active ? [...new Set([...active.labels, label])] : [label];
    const operationCount = (active?.operationCount || 0) + 1;
    const message = `${operationCount}건 변경`;
    const entry = {
      before, after: next, blockId, labels, operationCount, updatedAt: now, message,
    };
    undoEntryRef.current = entry;
    setUndoEntry(entry);
    scheduleUndoDismiss(entry);
  };
  const clearUndoForSnapshot = (snapshot) => {
    if (undoEntryRef.current?.after === snapshot) dismissUndo();
  };
  const undoLatest = () => {
    const entry = undoEntryRef.current;
    if (!entry) return;
    setBlocks(entry.before);
    dismissUndo();
    toast.push('변경을 되돌렸어요', { icon: 'undo' });
  };
  const patch = (id, changes, { undoLabel = null } = {}) => {
    if (atomicSavingRef.current) return;
    const previous = blocks;
    const current = previous.find((block) => block.id === id);
    if (!current) return;
    const applied = typeof changes === 'function' ? changes(current) : changes;
    const oldRowId = current.layoutRowId;
    let next = previous.map((block) => {
      if (block.id === id) {
        const updated = detachHookSlotOnReshape(current, applied, { ...block, ...applied });
        return applied.source === 'mine'
          ? detachSpaceMembership(withoutLayoutRow(updated))
          : updated;
      }
      // 내 이미지로 전환한 컷은 행에 남을 수 없으므로 기존 행도 함께 해제한다.
      return applied.source === 'mine' && oldRowId && block.layoutRowId === oldRowId ? withoutLayoutRow(block) : block;
    });
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
    const move = pendingSectionMove?.blockId === id ? pendingSectionMove : null;
    let staged = previous;
    if (move) {
      const from = staged.findIndex((block) => block.id === id);
      if (from >= 0) {
        const movedRowId = staged[from].layoutRowId;
        const moved = moveBlockWithSpaceMembership(staged, id, move.index, {
          targetSpaceGroupId: null,
          nextGroupId: nextSeparatedSpaceGroupId,
        });
        const dissolved = movedRowId
          ? moved.map((block) => block.layoutRowId === movedRowId ? withoutLayoutRow(block) : block)
          : moved;
        staged = adoptSection(dissolved, id, move.targetSid, move.targetRole);
      }
    }
    const next = normalizeStoryboardMutation(staged.map((block) => (
      block.id === id
        ? detachHookSlotOnReshape(block, changes, { ...block, ...changes })
        : block
    )));
    showUndo(previous, next, { blockId: id, label: undoLabel || undoLabelForPatch(changes) });
    directSaveSnapshots.current.add(next);
    setBlocks(next);
    try {
      await sbSaveNow(projectId, () => next);
      if (move) setPendingSectionMove(null);
      atomicRetryRef.current = null;
    } catch (error) {
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
    const next = normalizeStoryboardMutation(built);
    showUndo(previous, next, { blockId: undoBlockId || nextSelectedId || 'multi', label: undoLabel });
    directSaveSnapshots.current.add(next);
    setBlocks(next);
    try {
      await sbSaveNow(projectId, () => next);
      atomicRetryRef.current = null;
      if (nextSelectedId) {
        setSelectedId(nextSelectedId);
        setSplitOpen(true);
      }
      return next;
    } catch (error) {
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
  const finishEdit = () => { setPendingSectionMove(null); setSelectedId(null); };
  const selectCard = (id) => {
    if (atomicSavingRef.current) return;
    setSetPicker(null); setSetPickerError(null);
    if (selectedId === id) { finishEdit(); return; }      // click again → deselect
    setPendingSectionMove(null);
    setSelectedId(id); setSplitOpen(true);
  };
  const duplicate = (id) => {
    dismissUndo();
    setBlocks((bs) => {
      const i = bs.findIndex((b) => b.id === id); if (i < 0) return bs;
      const group = dragGroupFor(bs, id);
      // 첫 화면 프레임 슬롯의 복제본은 표식 없이 '구성 미사용' 일반 컷이 된다
      // (슬롯 수는 스타일이 정한다 — 스펙 §1, Codex 리뷰 #4).
      const copy = { ...stripHookFrameFields(withoutLayoutRow(bs[i])), id: uid('blk') };
      const n = [...bs];
      // 행 안에 복제본을 끼워 넣어 기존 행의 연속성을 깨지 않도록 행 바로 뒤에 단일 컷으로 둔다.
      n.splice((group?.indexes[group.indexes.length - 1] ?? i) + 1, 0, copy);
      // 컷 수가 변한 섹션의 레이아웃 위생 — 삽입·이동 경로와 동일 규칙 (예: 2컷 twoColumn 에 복제 → 강등/재배치)
      return normalizeStoryboardMutation(n);
    });
  };
  const remove = (id) => {
    dismissUndo();
    const idx = blocks.findIndex((b) => b.id === id); const removed = blocks[idx];
    const rowId = removed?.layoutRowId;
    const undoBlock = removed ? withoutLayoutRow(removed) : removed;
    // undo 정본 = 삭제 전 보드 통짜 스냅샷 — normalizeBoard 가 삭제 시 레이아웃을 강등하므로
    // 재삽입+재정규화만으론 원래 레이아웃·행 구성이 복원되지 않는다(addBlock 취소와 동일 패턴).
    const preDelete = blocks;
    let postDelete = null;   // 삭제 직후 상태 — identity 가 그대로일 때만 통짜 복원 유효
    setBlocks((bs) => {
      postDelete = normalizeStoryboardMutation((bs.filter((b) => b.id !== id).map((b) => {
        // 삭제 규칙: 한 멤버가 사라지면 남은 파트너 전원의 행 id를 내려 모두 일반 단일 카드로 돌린다.
        // normalizeBoard: 컷 수가 줄어든 섹션의 레이아웃 위생(예: 3컷 threeColumn 에서 1개 삭제 → 스테일 레이아웃 해소)
        return rowId && b.layoutRowId === rowId ? withoutLayoutRow(b) : b;
      })));
      return postDelete;
    });
    if (selectedId === id) finishEdit();
    toast.push('블록을 삭제했어요', { undo: () => setBlocks((bs) => {
      if (!undoBlock) return bs;
      // "삭제 직후 그대로" 판정 = 현재 보드가 postDelete 이거나 자동 강등 effect 가 postDelete
      // 로부터 만들어 온 계보(autoDemoteTrail) 위에 있을 때만 — 사용자 조작(레이아웃 칩 포함)은
      // 계보에 없으므로 자동으로 폴백. 판정되면 스냅샷 통짜 복원(레이아웃·행·소속까지 원복,
      // 복원 후 effect 가 preDelete 기준으로 재평가).
      let cur = bs, unchanged = false;
      for (let hop = 0; cur && hop < 8; hop += 1) {
        if (cur === postDelete) { unchanged = true; break; }
        cur = autoDemoteTrail.current.get(cur);
      }
      if (unchanged) return preDelete;
      // 그 사이 실제 조작이 있었으면 폴백: 재삽입 후 이웃 섹션 재채택 + 공통 위생
      const n = [...bs]; n.splice(Math.min(idx, n.length), 0, undoBlock);
      return normalizeStoryboardMutation(adoptSection(n, undoBlock.id));
    }) });
  };
  // 이동 후 adoptSection — 섹션 경계를 넘으면 이웃 섹션을 채택하고 대상 섹션을 '직접 구성' 처리
  const addBlock = async (idx, targetSid, targetRole = null, targetSpaceGroupId = null, targetRenderGroupKey = null, requestedExample = null) => {
    dismissUndo();
    const reservation = requestedExample && typeof requestedExample === 'object'
      ? requestedExample : null;
    const reservedSpaceMember = reservation?.member || null;
    const droppedExampleId = reservedSpaceMember?.exampleId || requestedExample;
    const targetHost = blocks.find((b) => b.sectionId === targetSid);
    const host = targetHost || (!targetRole ? blocks[Math.max(0, Math.min(idx - 1, blocks.length - 1))] : null);
    const sectionRole = targetRole || host?.sectionRole || SECTION_ROLES.HOOKING;
    const droppedExample = droppedExampleId
      ? (catalogs.genExamples || []).find((example) => example.id === droppedExampleId)
      : null;
    // 예약 멤버 또는 사용자가 드롭한 포즈 예시가 있을 때만 새 컷을 세트에 가입시킨다.
    // 예약이 소진된 내부 plus는 저장 가능한 일반 manual 컷으로 경계를 나눈다.
    const effectiveSpaceGroupId = targetSpaceGroupId && (reservedSpaceMember || droppedExample)
      ? targetSpaceGroupId
      : null;
    if (droppedExampleId && !droppedExample) {
      toast.push('이 생성예시를 찾지 못했어요');
      return;
    }
    const droppedCutType = droppedExample?.cutType;
    if (!reservation && droppedCutType === 'mirror' && sectionRole !== SECTION_ROLES.STYLING) {
      toast.push('거울컷은 스타일링 섹션에만 추가할 수 있어요');
      return;
    }
    if (droppedCutType && !allowedCutTypeOptionsForSection(sectionRole).some((option) => option.value === droppedCutType)) {
      toast.push('이 섹션에는 해당 생성예시를 추가할 수 없어요');
      return;
    }
    if (targetSpaceGroupId && droppedExample && (!(droppedExample.variants || []).includes('pose')
      || !poseExampleDirectionCompatible(droppedExample, {
        cutType: droppedCutType,
        direction: droppedExample.direction,
      }))) {
      toast.push('이 장소 세트에는 포즈 참조가 가능한 예시만 넣을 수 있어요');
      return;
    }
    newSeq.current += 1;
    // 역할 칩 없이도 모든 내부 생성법을 계속 추가할 수 있게, 삽입 지점과
    // 가장 가까운 같은 섹션의 AI 이미지가 쓰는 생성법을 물려받는다.
    const isAdjacentCandidate = (block) => block
      && block.source !== 'mine'
      && (!targetSid || block.sectionId === targetSid)
      && sectionRoleForContentRole(block.contentRole) === sectionRole;
    let adjacent = null;
    for (let distance = 1; distance <= blocks.length && !adjacent; distance += 1) {
      const before = blocks[idx - distance];
      const after = blocks[idx + distance - 1];
      adjacent = [before, after].find(isAdjacentCandidate) || null;
    }
    const renderPurpose = targetRenderGroupKey === 'studio' ? CONTENT_ROLES.FIT
      : targetRenderGroupKey === 'styling' ? CONTENT_ROLES.COORDINATION
        : targetRenderGroupKey === 'product' ? CONTENT_ROLES.PRODUCT_OVERVIEW
          : null;
    const firstPurpose = renderPurpose || adjacent?.contentRole || defaultContentRoleForSection(sectionRole);
    const purposePatch = blockPatchForContentRole(null, firstPurpose, { clothingType });
    const insertionRecipe = droppedExample ? {
      ...purposePatch,
      cutType: droppedCutType || purposePatch.cutType,
      direction: droppedExample.direction ?? purposePatch.direction,
      shot: droppedExample.shot || purposePatch.shot,
    } : purposePatch;
    const initialColorOpts = firstPurpose === CONTENT_ROLES.DETAIL ? detailColorOpts : colorOpts;
    // 내부 역할은 현재 섹션의 안전한 기본값으로 시작하고, normalizeBoard가
    // 핵심 장점 첫 카드의 hero 여부를 실제 카드 순서에 맞춰 다시 확정한다.
    const nb = { id: uid('blk'), sectionRole, taxonomyVersion: STORYBOARD_TAXONOMY_VERSION, colorId: initialColorOpts[0]?.id || 'col1',
      pose: 'auto', matchIds: [], faceExposure: 'same', angle: 'same', refImages: [], refAssetIds: [],
      ...(!droppedExample ? { exampleChoice: 'manual' } : {}),
      ...insertionRecipe,
      thumb: Placeholder.photo('new' + Date.now(), insertionRecipe.cutType === 'product' ? 'product' : insertionRecipe.cutType === 'horizon' ? 'horizon' : 'styling', 240, 320), poseThumb: Placeholder.pose('stand'), poseLabel: 'AI 자동' };
    const m = [...blocks]; m.splice(idx, 0, nb);
      let out = adoptSection(m, nb.id, targetSid, sectionRole);             // 이웃/명시된 섹션 소속으로 삽입
      // 빈 보드 등 이웃이 없으면 기본 섹션 부여 — 무소속(unsupported state) 블록 방지
      out = out.map((b) => b.id === nb.id && !b.sectionId
        ? { ...b, sectionId: uid('sec'), sectionTitle: sectionTitle(sectionRole), sectionLayout: 'stack' } : b);
      // 같은 공간 시리즈 섹션에의 '추가'는 명시 액션 = 시리즈 가입 — adoptSection 의 자동가입 금지 규칙은
      // '이동'용이다. 미가입 상태로 두면 deriveSections 가 시리즈를 해제해 SPACE 연속성·포즈 범위 계약이 깨진다.
      {
        const sid = out.find((b) => b.id === nb.id)?.sectionId;
        const peers = out.filter((b) => b.id !== nb.id && b.sectionId === sid);
        const explicitGroup = effectiveSpaceGroupId
          ? peers.find((block) => block.spaceGroupId === effectiveSpaceGroupId)
          : null;
        const g = explicitGroup;
        if (g) out = out.map((b) => (b.id === nb.id
          ? { ...b, spaceGroupId: g.spaceGroupId, spaceVariation: g.spaceVariation ?? 'subtle', refScope: 'pose' } : b));
      }
      out = normalizeStoryboardMutation(out);   // 행 위생 + 분리된 공간 run 재키 (삽입 경로 공통 규칙)
    const next = droppedExample
      ? out.map((block) => block.id === nb.id ? {
        ...block,
        ...referenceFeedbackPatch(block, {
          exampleId: droppedExample.id,
          baseThumb: block.baseThumb ?? block.thumb,
          exampleSelectionOrigin: 'user',
          refScope: effectiveSpaceGroupId ? 'pose' : 'all',
        }, catalogs),
        ...(reservation?.blockPatch || {}),
      } : block)
      : assignGenerationExamples(out, {
        catalog: catalogs.genExamples,
        product: { clothingType },
        gender: exampleGender,
        onlyBlockIds: [nb.id],
      }).blocks;
    const serverValidNext = ensureContiguousSpaceRuns(next);
    directSaveSnapshots.current.add(serverValidNext);
    setBlocks(serverValidNext);
    try {
      await sbSaveNow(projectId, () => serverValidNext);
    } catch {
      // 일반 컷 추가 저장은 background 경로다. 보드는 유지하고 scheduler가 조용히 재시도한다.
    }
    setSelectedId(nb.id); setSplitOpen(true);
    toast.push(reservedSpaceMember ? '준비된 컷을 추가했어요'
      : droppedExample ? '생성예시를 새 컷으로 추가했어요' : '블록을 추가했어요', { icon: 'plus' });
  };
  const mineBlock = (src, n) => ({
    id: uid('blk'), sectionRole: SECTION_ROLES.HOOKING, contentRole: CONTENT_ROLES.CUSTOM, taxonomyVersion: STORYBOARD_TAXONOMY_VERSION,
    title: '내 이미지', source: 'mine', cutType: null, colorId: colorOpts[0]?.id || 'col1',
    ownImages: [src], thumb: src, pose: 'auto', matchIds: [], faceExposure: 'same', angle: 'same', refImages: [], refAssetIds: [],
    poseThumb: Placeholder.pose('stand'), poseLabel: '-',
  });
  const addMineBlock = async (srcOrIndex = null) => {
    const picked = typeof srcOrIndex === 'string' ? srcOrIndex : await api.pickAnyImage(projectId);
    const src = mineImageUrl(picked);
    if (!src) return;
    dismissUndo();
    const idx = typeof srcOrIndex === 'number' ? srcOrIndex : null;
    const nb = mineBlock(src, (newSeq.current += 1));
    // adoptSection — 화면(즉시)과 재진입(ensureSections 상속)이 같은 소속이 되도록 삽입 시점에 확정
    setBlocks((bs) => {
      const m = [...bs]; m.splice(idx == null ? m.length : idx, 0, nb);
      const adopted = adoptSection(m, nb.id);
      const sectioned = adopted.find((b) => b.id === nb.id)?.sectionId ? adopted : ensureSections(adopted);
      return normalizeStoryboardMutation(sectioned);
    });
    setSelectedId(nb.id); setSplitOpen(true);
    toast.push('내 이미지 블록을 추가했어요', { icon: 'plus' });
  };
  // drag-to-reorder blocks (with drop indicator)
  const onDragStart = (id) => (e) => {
    dismissUndo();
    e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/blk', id);
    // 고스트 = 그립 점이 아니라 블록 카드 전체 — 잡은 지점 기준 오프셋 유지
    const node = cardRefs.current.get(id);
    if (node && e.dataTransfer.setDragImage) {
      const r = node.getBoundingClientRect();
      e.dataTransfer.setDragImage(node, Math.max(0, e.clientX - r.left), Math.max(0, e.clientY - r.top));
    }
    setDragId(id);
  };
  const onSpaceDragStart = (spaceGroupId) => (e) => {
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/space-group', spaceGroupId);
    setDragSpaceGroupId(spaceGroupId);
  };
  const onDragEnd = () => {
    setDragId(null); setDragSpaceGroupId(null); setDragOver(null); setDragOverSec(null); setDragOverSpaceGroupId(null);
  };
  const renderKeyForBlockId = (id) => renderGroups(blocks)
    .find((group) => group.items.some((item) => item.block.id === id))?.key;
  const onDropAt = (idx, targetSid, targetRole = null, targetSpaceGroupId = null, targetGroupKey = null) => (e) => {
    e.preventDefault();
    e.stopPropagation();
    const exampleId = e.dataTransfer.getData('text/example-id') || dragExampleId;
    const draggedGroup = e.dataTransfer.getData('text/space-group') || dragSpaceGroupId;
    setDragOver(null); setDragOverSec(null); setDragOverSpaceGroupId(null);
    if (draggedGroup) {
      setDragSpaceGroupId(null);
      if (targetSpaceGroupId) return;
      const members = blocks.filter((block) => block.spaceGroupId === draggedGroup);
      if (targetGroupKey && renderKeyForBlockId(members[0]?.id) !== targetGroupKey) {
        toast.push('같은 그룹 안에서만 순서를 바꿀 수 있어요');
        return;
      }
      const memberIds = new Set(members.map((member) => member.id));
      const allAllowed = !targetRole || members.every((block) => allowedCutTypeOptionsForSection(targetRole)
        .some((option) => option.value === block.cutType));
      if (!allAllowed) { toast.push('이 섹션에는 장소 세트 구성을 그대로 옮길 수 없어요'); return; }
      setBlocks((current) => {
        let moved = moveSpaceSetRun(current, draggedGroup, idx);
        for (const member of members) moved = adoptSection(moved, member.id, targetSid, targetRole);
        return normalizeStoryboardMutation(moved.map((block) => memberIds.has(block.id)
          ? { ...block, spaceGroupId: draggedGroup, spaceVariation: block.spaceVariation || 'subtle', refScope: 'pose' }
          : block));
      });
      return;
    }
    if (exampleId) {
      setDragExampleId(null);
      addBlock(idx, targetSid, targetRole, targetSpaceGroupId, targetGroupKey, exampleId);
      return;
    }
    const id = e.dataTransfer.getData('text/blk') || dragId; setDragId(null); if (!id) return;
    applySingleMove({ id, idx, targetSid, targetRole, targetSpaceGroupId, targetGroupKey });
  };

  /* 단일 블록 이동의 공통 경로 — 드래그(onDropAt)와 위/아래 버튼(nudgeBlock)이 함께 쓴다.
     그룹 제한·섹션 이동 시 예시 재선택·세트 소속 승계 규칙이 두 경로에서 동일해야 한다. */
  const applySingleMove = ({ id, idx, targetSid, targetRole = null, targetSpaceGroupId = null, targetGroupKey = null }) => {
    const moving = blocks.find((block) => block.id === id);
    const currentGroupKey = moving ? renderKeyForBlockId(id) : null;
    const crossedRenderGroup = !!targetGroupKey && currentGroupKey !== targetGroupKey;
    // 다른 섹션으로 옮길 때는 사용자가 어느 드롭라인을 가리켰든 그 섹션의 맨 뒤가 목적지다.
    // 같은 섹션 안에서는 가리킨 위치를 그대로 써 세밀한 재정렬을 유지한다.
    const targetGroupEnd = crossedRenderGroup
      ? (renderGroups(blocks).find((group) => group.key === targetGroupKey)?.items.at(-1)?.index ?? idx)
      : null;
    const targetSectionEnd = targetGroupEnd ?? (targetSid && moving?.sectionId !== targetSid
      ? blocks.reduce((end, block, index) => block.sectionId === targetSid ? index + 1 : end, idx)
      : idx);
    const targetContentRole = targetGroupKey === 'studio' ? CONTENT_ROLES.FIT
      : targetGroupKey === 'styling' ? CONTENT_ROLES.COORDINATION
        : targetGroupKey === 'product' ? CONTENT_ROLES.PRODUCT_OVERVIEW
          : targetGroupKey === 'hooking' ? CONTENT_ROLES.BENEFIT : null;
    const renderGroupRecipe = crossedRenderGroup && moving?.source !== 'mine' && targetContentRole
      ? blockPatchForContentRole(moving, targetContentRole, { clothingType })
      : null;
    const cutAllowed = !targetRole || moving?.source === 'mine'
      || allowedCutTypeOptionsForSection(targetRole).some((option) => option.value === moving?.cutType);
    if (moving && (!cutAllowed || (renderGroupRecipe && renderGroupRecipe.cutType !== moving.cutType))) {
      const fallbackRole = targetContentRole || defaultContentRoleForSection(targetRole);
      const targetRecipe = renderGroupRecipe || blockPatchForContentRole(moving, fallbackRole, { clothingType });
      setPendingSectionMove({
        blockId: id, index: targetSectionEnd, targetSid, targetRole,
        cutType: targetRecipe.cutType, shot: targetRecipe.shot, sectionRole: targetRole,
      });
      setSelectedId(id); setSplitOpen(true);
      toast.push('새 섹션에 맞는 컷 예시를 먼저 골라주세요');
      return;
    }
    setBlocks((current) => {
      const moved = moveBlockWithSpaceMembership(current, id, targetSectionEnd, {
        targetSpaceGroupId,
        nextGroupId: nextSeparatedSpaceGroupId,
      });
      const adopted = adoptSection(moved, id, targetSid, targetRole);
      return normalizeStoryboardMutation(adopted);
    });
  };

  /* 위/아래 한 칸 이동 — 드래그를 쓸 수 없는 경우(키보드 조작 포함)의 순서 변경 수단.
     이웃 블록의 섹션·세트를 목적지로 삼아 applySingleMove 에 위임하므로 규칙이 드래그와 같다. */
  const nudgeBlock = (id, delta) => {
    const from = blocks.findIndex((block) => block.id === id);
    if (from < 0) return;
    const to = from + delta;
    if (to < 0 || to >= blocks.length) return;
    const neighbor = blocks[to];
    // moveBlockWithSpaceMembership 은 targetIndex 를 '원본 배열 기준'으로 받아 자기 자신이
    // 빠진 만큼 스스로 보정한다(storyboardSpaceSets.js:101). 아래로 한 칸은 to+1 이어야 한다.
    applySingleMove({
      id,
      idx: delta > 0 ? to + 1 : to,
      targetSid: neighbor.sectionId,
      targetRole: neighbor.sectionRole || null,
      targetSpaceGroupId: neighbor.spaceGroupId || null,
      targetGroupKey: renderKeyForBlockId(neighbor.id),
    });
  };

  /* 렌더 그룹 아코디언 (UI 전용) */
  // 다중 열기 — 한 섹션을 펼쳐도 이미 펼친 섹션은 그대로 둔다(생성 전 전체 점검 동선).
  const toggleRenderGroup = (key) => setOpenGroupKeys((current) => (
    current.includes(key) ? current.filter((k) => k !== key) : [...current, key]
  ));
  const openRenderGroup = (key) => setOpenGroupKeys((current) => (
    current.includes(key) ? current : [...current, key]
  ));
  /* 섹션 레이아웃 변경 — 멤버 전체 patch + 직접 구성 표시 */
  const setSecLayout = (sec, v) => {
    // 활성 칩도 다시 적용할 수 있어야 layoutRowId 없는 레거시 보드를 명시적으로 마이그레이션할 수 있다.
    setBlocks((bs) => patchSection(bs, sec.id, { sectionLayout: v, sectionCustom: true }));
  };
  const openSetPicker = (picker) => {
    setPickerScrollY.current = window.scrollY;
    setSetPickerError(null);
    setSetPicker(picker);
    setSplitOpen(true);
  };
  const chooseSpaceSet = async (set) => {
    if (!setPicker) return;
    setSetPickerError(null);
    const groupId = spaceSetGroupId(set.id, uid('sg'));
    try {
      if (setPicker.mode === 'replace') {
        const currentMembers = blocks.filter((block) => block.spaceGroupId === setPicker.spaceGroupId);
        const newIds = set.members.map((_member, index) => currentMembers[index]?.id || uid('blk'));
        await atomicBoardChange((current) => {
          const replaced = replaceSpaceSetRun(current, setPicker.spaceGroupId, set, {
            spaceGroupId: groupId,
            makeId: (_member, index) => newIds[index],
            setSelectionOrigin: 'user',
          });
          const normalized = normalizeBoard(replaced);
          return assignGenerationExamples(normalized, {
            catalog: catalogs.genExamples,
            product: { clothingType },
            gender: exampleGender,
            onlyBlockIds: newIds,
          }).blocks;
        }, { nextSelectedId: newIds[0] });
      } else {
        const memberIds = set.members.map(() => uid('blk'));
        const memberIdSet = new Set(memberIds);
        const targetHost = blocks.find((block) => block.sectionId === setPicker.targetSid)
          || blocks[Math.max(0, Math.min(setPicker.index - 1, blocks.length - 1))];
        const template = targetHost || {
          sectionRole: setPicker.targetRole,
          taxonomyVersion: STORYBOARD_TAXONOMY_VERSION,
          colorId: colorOpts[0]?.id || 'col1',
          matchIds: [], faceExposure: 'same', angle: 'same', refImages: [], refAssetIds: [],
          thumb: Placeholder.photo('new-set', 'styling', 240, 320),
          poseThumb: Placeholder.pose('stand'),
        };
        await atomicBoardChange((current) => {
          let inserted = insertSpaceSet(current, setPicker.index, set, template, {
            spaceGroupId: groupId,
            makeId: (_member, index) => memberIds[index],
            setSelectionOrigin: 'user',
          });
          for (const id of memberIds) inserted = adoptSection(inserted, id, setPicker.targetSid, setPicker.targetRole);
          inserted = inserted.map((block) => memberIdSet.has(block.id) ? {
            ...block, spaceGroupId: groupId, spaceVariation: set.spaceVariation || 'subtle', refScope: 'pose',
          } : block);
          const normalized = normalizeBoard(inserted);
          return assignGenerationExamples(normalized, {
            catalog: catalogs.genExamples,
            product: { clothingType },
            gender: exampleGender,
            onlyBlockIds: memberIds,
          }).blocks;
        }, { nextSelectedId: memberIds[0] });
      }
      setSetPicker(null);
      toast.push(setPicker.mode === 'replace' ? '장소 세트를 변경했어요' : '장소 세트를 추가했어요', { icon: 'plus' });
    } catch {
      setSetPickerError('장소 세트를 저장하지 못했어요. 다시 시도해주세요.');
    }
  };
  const locked = false;
  const boardGroups = renderGroups(blocks);

  const sections = deriveFixedSections(blocks);
  const draggedSpaceBlock = dragSpaceGroupId ? blocks.find((block) => block.spaceGroupId === dragSpaceGroupId) : null;
  const draggedSpaceGroupKey = draggedSpaceBlock ? renderKeyForBlockId(draggedSpaceBlock.id) : null;

  const sectionForGroup = (group) => {
    const sectionId = group.items[0]?.block.sectionId;
    if (sectionId) {
      const exact = sections.find((section) => section.id === sectionId);
      if (exact) return exact;
    }
    const role = {
      hooking: SECTION_ROLES.HOOKING,
      styling: SECTION_ROLES.STYLING,
      studio: SECTION_ROLES.STUDIO,
      product: SECTION_ROLES.PRODUCT,
    }[group.key];
    return sections.find((section) => section.role === role) || {
      id: 'empty:' + role,
      role,
      layout: 'stack',
      items: [],
      start: group.items[0]?.index ? group.items[0].index - 1 : blocks.length,
    };
  };

  // 첫 화면 프레임(후킹) — 스타일 패널·접힘 시트·슬롯 배지가 같은 파생을 본다.
  // 주의: 이 지점은 로딩 early-return 아래라 훅 사용 금지(훅 개수 불변 규칙) — 순수 파생만.
  const hookFrame = deriveHookFrame(blocks || []);
  const boundGenderNow = exampleGender
    || genderForClothingType(clothingType, composeModeSeed.targetGenders);
  // 발행된 조합만 슬롯으로 — 닫힌 조합으로 컷을 만들면 예시가 배정되지 않아 빈 칸이 된다
  // (2026-08-14 '이미지 사라짐' 원인). 판정은 **자동 배정기(candidatesForBlock)와 동일**해야
  // 한다 — appendSetOnly 를 켜면 세트 전용 조합(예: 남성 호리존 미디움)을 가용으로 오판해
  // 배정기가 못 채우는 빈 컷이 생긴다(Codex 리뷰 2차 #2).
  const hookCutAvailable = (cutType, shot) => selectGenerationExamples(
    catalogs?.genExamples || [],
    { cutType, shot, clothingType, gender: boundGenderNow },
  ).length > 0;

  // 스타일 전환(스펙 §2 전환 엔진) — 컷 재사용·부족분 생성·잔여 AI 컷 삭제·예시 재배정 후 즉시 저장.
  async function applyHookStyleChoice(style) {
    if (locked || hookStyleSaving || !hookFrame) return;
    // 같은 스타일 재선택은 프레임이 온전할 때만 무시한다 — 슬롯 컷을 삭제해 프레임이
    // 모자라진 보드는 재적용으로 복구한다(Codex 리뷰 2차 #5: 카드 삭제 후 복구 불가).
    if (style === hookFrame.style) {
      const planLength = hookSlotPlan(style, {
        colors: composeModeSeed.colors || [], isCutAvailable: hookCutAvailable,
      }).length;
      if (hookFrame.slotIds.length >= planLength) return;
    }
    const previous = blocks;
    const template = previous.find((block) => block.id === hookFrame.slotIds[0])
      || previous.find((block) => block.sectionRole === SECTION_ROLES.HOOKING && block.source !== 'mine');
    if (!template) return;
    const colors = composeModeSeed.colors || [];
    const baseColorId = (colors.find((color) => color.isBase) || colors[0])?.id || template.colorId;
    const createBlock = (slot) => ({
      id: uid('blk'),
      sectionId: template.sectionId,
      sectionRole: SECTION_ROLES.HOOKING,
      contentRole: CONTENT_ROLES.BENEFIT,
      taxonomyVersion: STORYBOARD_TAXONOMY_VERSION,
      title: template.title,
      source: 'ai',
      cutType: slot.cutType,
      direction: slot.direction || 'front',
      shot: slot.shot,
      colorId: slot.colorId || baseColorId,
      pose: 'auto',
      poseLabel: 'AI 자동',
      poseThumb: template.poseThumb,
      matchIds: [...(template.matchIds || [])],
      faceExposure: 'same',
      angle: 'same',
      refImages: [],
      thumb: template.baseThumb || template.thumb,
    });
    setHookStyleSaving(true);
    setHookStyleError(null);
    try {
      let next = applyHookStyle(previous, style, {
        colors, createBlock, isCutAvailable: hookCutAvailable,
      });
      next = normalizeStoryboardMutation(next);
      next = assignGenerationExamples(next, {
        catalog: catalogs.genExamples,
        product: { clothingType, colors },
        gender: boundGenderNow,
      }).blocks;
      directSaveSnapshots.current.add(next);
      setBlocks(next);
      const nextFrame = deriveHookFrame(next);
      // 선택 컷은 전환 후에도 유지한다(인스펙터·스타일 목록이 그대로 남게) —
      // 보드에서 사라진 경우에만 첫 슬롯으로 넘어간다.
      setSelectedId((current) => (
        next.some((block) => block.id === current) ? current : (nextFrame?.slotIds[0] ?? null)
      ));
      await sbSaveNow(pidRef.current, () => next);
      if (sbPending.get(pidRef.current) === next) sbPending.delete(pidRef.current);
    } catch {
      setHookStyleError('스타일을 저장하지 못했어요');
    } finally {
      setHookStyleSaving(false);
    }
  }

  // 예시 셔플(스펙 §4, 2026-08-15 축소) — 후킹 섹션(재추첨)과 장소세트(세트 교체)에만.
  // 저장은 기존 자동 저장 흐름을 탄다.
  const runShuffle = (group, options = {}) => {
    if (locked || !catalogs) return;
    const section = sectionForGroup(group);
    const previous = blocks;
    shuffleTickRef.current += 1;
    const next = shuffleSectionExamples(previous, {
      sectionId: section.id,
      catalog: catalogs.genExamples,
      product: { clothingType, colors: composeModeSeed.colors },
      gender: boundGenderNow,
      rotation: shuffleTickRef.current,
      uid,
      ...options,
    });
    if (next === previous) {
      toast.push('바꿀 수 있는 예시가 없어요 — 모두 직접 고른 컷이에요');
      return;
    }
    const normalized = ensureContiguousSpaceRuns(next);
    setBlocks(normalized);
    showUndo(previous, normalized, {
      blockId: section.items[0]?.b?.id || null,
      label: '예시 셔플',
    });
  };
  const shuffleSection = (group) => runShuffle(group);
  const shuffleSpaceSet = (group, spaceGroupId) => runShuffle(group, { onlySpaceGroupId: spaceGroupId });

  const insertControl = (
    idx,
    group,
    targetSpaceGroupId = null,
    requestedExample = null,
    placement = null,
  ) => {
    const section = sectionForGroup(group);
    const canAcceptDrag = (!draggedSpaceGroupKey || draggedSpaceGroupKey === group.key)
      && !(dragSpaceGroupId && targetSpaceGroupId);
    const lineOn = dragOver === idx && dragOverSec === section.id
      && dragOverSpaceGroupId === targetSpaceGroupId;
    return (
      <StoryboardInsertControl
        inTray={!!targetSpaceGroupId}
        active={lineOn}
        placement={placement}
        onDragOver={(event) => {
          if (!(dragId || dragExampleId || dragSpaceGroupId) || !canAcceptDrag) return;
          event.preventDefault();
          event.stopPropagation();
          setDragOver(idx);
          setDragOverSec(section.id);
          setDragOverSpaceGroupId(targetSpaceGroupId);
        }}
        onDrop={onDropAt(idx, section.id, section.role, targetSpaceGroupId, group.key)}
        onAdd={(event) => {
            event.stopPropagation();
            addBlock(idx, section.id, section.role, targetSpaceGroupId, group.key, requestedExample);
          }}
      />
    );
  };

  const layoutControlForGroup = (group) => {
    const section = sectionForGroup(group);
    const aiItems = section.items.filter(({ b }) => b.source !== 'mine');
    if ((import.meta.env.VITE_API_MODE ?? 'mock') === 'http' || aiItems.length < 2) return null;
    const colorSet = new Set(aiItems.filter(({ b }) => b.colorId).map(({ b }) => b.colorId));
    const comparisonAllowed = !section.samePlace && colorSet.size >= 2;
    const offered = aiItems.length === 2 ? 'twoColumn'
      : aiItems.length === 3 ? 'threeColumn'
        : aiItems.length === 4 ? 'grid2x2'
          : aiItems.length >= 5 ? 'twoColumn' : null;
    return (
      <div className="sb-group-layout" aria-label="레이아웃 설정">
        <span>레이아웃 설정</span>
        <button type="button" className={section.layout === 'stack' ? 'on' : undefined}
          onClick={() => setSecLayout(section, 'stack')}>세로 1열</button>
        {offered && (
          <button type="button" className={section.layout === offered ? 'on' : undefined}
            onClick={() => setSecLayout(section, offered)}>
            {offered === 'twoColumn' ? '2단' : offered === 'threeColumn' ? '3단' : '2×2단'}
          </button>
        )}
        {(comparisonAllowed || section.layout === 'colorCompare') && (
          <button type="button" className={section.layout === 'colorCompare' ? 'on' : undefined}
            onClick={() => setSecLayout(section, 'colorCompare')}>컬러 비교</button>
        )}
      </div>
    );
  };

  const registerUnitRef = (node, items) => {
    items.forEach((item) => {
      if (node) cardRefs.current.set(item.block.id, node);
      else cardRefs.current.delete(item.block.id);
    });
  };

  const renderUnit = (unit, group, targetSpaceGroupId = null, reservation = null) => {
    const section = sectionForGroup(group);
    const lastItem = unit.items[unit.items.length - 1];
    // 세트 안 추가는 예약된 예비 멤버가 남아 있을 때만 — 예비 소진 뒤 일반 컷을 세트에
    // 넣는 추가 존은 제공하지 않는다(섹션 추가와 중복, 2026-08-14 오너 결정).
    const addControl = targetSpaceGroupId && !reservation
      ? null
      : insertControl(lastItem.index, group, targetSpaceGroupId, reservation);
    if (unit.kind === 'frame') {
      return (
        <div
          key={'frame:' + unit.items[0].block.layoutRowId}
          ref={(node) => registerUnitRef(node, unit.items)}
          className={'sb-grid-unit sb-frame-unit sb-drag' + (unit.items.some((item) => item.block.id === dragId) ? ' dragging' : '')}
        >
          <StoryboardFrame
            items={unit.items}
            total={blocks.length}
            catalogs={catalogs}
            colorOpts={colorOpts}
            matchClothing={matchClothing}
            clothingType={clothingType}
            selectedId={selectedId}
            locked={locked}
            dragFor={(id) => ({ draggable: true, onDragStart: onDragStart(id), onDragEnd })}
            onSelect={selectCard}
            onDuplicate={duplicate}
            onDelete={remove}
            addControl={addControl}
            microVariationIds={microVariationIds}
          />
        </div>
      );
    }

    const item = unit.items[0];
    const block = item.block;
    // 이동 가능 범위는 자기가 속한 렌더 그룹 안 — 그룹 밖으로 나가는 이동은 드래그와 동일하게 막힌다.
    const groupPos = group ? group.items.findIndex((entry) => entry.block.id === block.id) : -1;
    const inGroup = groupPos >= 0;
    return (
      <div
        key={block.id}
        ref={(node) => registerUnitRef(node, [item])}
        className={'sb-grid-unit sb-drag' + (block.id === dragId ? ' dragging' : '')}
      >
        <StoryboardCard
          item={item}
          total={blocks.length}
          catalogs={catalogs}
          colorOpts={colorOpts}
          matchClothing={matchClothing}
          clothingType={clothingType}
          selected={block.id === selectedId}
          locked={locked && block.id !== selectedId}
          cardDrag={{ draggable: true, onDragStart: onDragStart(block.id), onDragEnd }}
          onSelect={() => selectCard(block.id)}
          onDuplicate={() => duplicate(block.id)}
          onDelete={() => remove(block.id)}
          addControl={addControl}
          onNudge={inGroup ? ((delta) => nudgeBlock(block.id, delta)) : undefined}
          canNudgeUp={inGroup && groupPos > 0}
          canNudgeDown={inGroup && groupPos < group.items.length - 1}
          microVariationIds={microVariationIds}
          microVariationIds={microVariationIds}
        />
      </div>
    );
  };

  const renderSpaceRun = (unit, group) => {
    const set = inferStoryboardSpaceSet(unit.spaceGroupId);
    const reservation = nextSpaceSetMemberReservation(set, unit.items.map((item) => item.block));
    const lastItem = unit.items[unit.items.length - 1];
    return (
      <div key={'spaceRun:' + unit.spaceGroupId + ':' + unit.items[0].block.id} className="sb-tray">
        <div
          className="sb-tray-head"
          draggable
          onDragStart={onSpaceDragStart(unit.spaceGroupId)}
          onDragEnd={onDragEnd}
        >
          {/* 세트 이름('햇살 드는 시장 골목' 등) 라벨은 표시하지 않는다(2026-08-15 오너). */}
          <button type="button" className="sb-tray-swap" onClick={(event) => {
            event.stopPropagation();
            const first = unit.items[0].block;
            setSelectedId(first.id);
            openSetPicker({ mode: 'replace', spaceGroupId: unit.spaceGroupId });
          }}>장소 세트 변경</button>
        </div>
        <div className="sb-tray-grid">
          {frameUnits(unit.items).map((spaceUnit) => (
            renderUnit(spaceUnit, group, unit.spaceGroupId, reservation)
          ))}
          {/* 예비 멤버 카드(구 UI 복원, 2026-08-15 오너 스크린샷) — 다음 세트 컷을 희미한
              실제 사진으로 보여주고 눌러서 추가. 예비 소진 시 세트 안 추가 수단은 사라진다. */}
          {reservation && (
            <div className="sb-grid-unit">
              <button
                type="button"
                className="sb-reserve-card"
                disabled={locked}
                onClick={() => {
                  const section = sectionForGroup(group);
                  addBlock(lastItem.index, section.id, section.role, unit.spaceGroupId, group.key, reservation);
                }}
              >
                {reservation.member?.thumb && (
                  <img src={reservation.member.thumb} alt="" loading="lazy" decoding="async" />
                )}
                <span className="sb-reserve-label"><b>＋</b>이 컷 추가</span>
              </button>
            </div>
          )}
        </div>
        {/* 예시 셔플 — 장소세트 단위(이 세트만 교체), 마지막 카드 우측 아래(2026-08-15 오너). */}
        <div className="sb-shuffle-row end">
          <button
            type="button"
            className="sb-shuffle-btn"
            disabled={locked}
            onClick={(event) => { event.stopPropagation(); shuffleSpaceSet(group, unit.spaceGroupId); }}
          >
            {ShuffleIcon}예시 셔플
          </button>
        </div>
        {insertControl(lastItem.index, group, null, null, 'end')}
      </div>
    );
  };

  const allGroupKeys = boardGroups.map((group) => group.key);
  const allOpen = allGroupKeys.length > 0 && allGroupKeys.every((key) => openGroupKeys.includes(key));

  const list = (
    <div className="sb-canvas-board">
      <div className="sb-board-tools">
        {/* 구성컷 수는 사진 양(기본형/확장형) 바로 위 — 같은 결정의 맥락으로 묶는다(2026-08-14 오너). */}
        <div className="sb-board-lead">
          <div className="sb-count-head">
            구성컷: <strong>{blocks.length}</strong>개
          </div>
          <ComposeModeSegment
            modes={catalogs?.composeModes || []}
            value={composeMode}
            canApply={composeModeApplies}
            onApply={onComposeModeApply}
            onError={onComposeModeError}
          />
        </div>
        <button
          type="button"
          className="sb-board-tool"
          onClick={() => setOpenGroupKeys(allOpen ? [] : allGroupKeys)}
        >
          {allOpen ? '전체 접기' : '전체 펼치기'}
        </button>
      </div>
      {boardGroups.map((group, groupIndex) => {
        const open = openGroupKeys.includes(group.key);
        const range = cutRangeLabel(group.items);
        const groupSection = sectionForGroup(group);
        return (
          <section
            key={group.key}
            className={'sb-deck' + (open ? ' open' : '') + (dragOverSec === groupSection.id ? ' hot' : '')}
            style={{ '--reveal-order': Math.min(groupIndex, 6) }}
            onPointerEnter={() => {
              if (open || !catalogs) return;   // 펼치기 직전 신호 — 아직 안 데운 것만 앞당겨 받는다
              prewarmImages(group.items.flatMap(({ block }) => [
                block.previewThumb || (block.exampleId
                  ? generationExampleImageSources(
                    (catalogs.genExamples || []).find((example) => example.id === block.exampleId),
                  ).prewarm
                  : block.thumb),
                block.ownImages?.[0],
              ]), { concurrency: 6 });
            }}
          >
            <button
              type="button"
              className="sb-deck-header"
              aria-expanded={open}
              onClick={() => toggleRenderGroup(group.key)}
            >
              <span className="sb-deck-chevron" aria-hidden="true"><Icon name="chevDown" size={18} /></span>
              <span className="sb-deck-index">{group.title}</span>
              <span className="sb-deck-label">{group.label}</span>
              <span className="sb-deck-range">{range}</span>
            </button>
            <div className="sb-stack-collapse">
              <div>
                {(() => {
                  // 후킹 접힘 예외(스펙 §3) — 프레임이 있으면 스택 대신 흰 페이지 시트.
                  const hookSlots = hookFrame && groupSection.role === SECTION_ROLES.HOOKING
                    ? hookFrame.slotIds
                      .map((slotId) => group.items.find((item) => item.block.id === slotId))
                      .filter(Boolean)
                      .map((item) => ({ block: item.block, index: item.index }))
                    : [];
                  return hookSlots.length ? (
                    <StoryboardHookSheet
                      frame={hookFrame}
                      slots={hookSlots}
                      total={blocks.length}
                      catalogs={catalogs}
                      colorOpts={colorOpts}
                      productName={productName}
                      baseColorId={(composeModeSeed.colors.find((color) => color.isBase)
                        || composeModeSeed.colors[0])?.id || null}
                      onOpen={() => openRenderGroup(group.key)}
                    />
                  ) : (
                    <StoryboardStack group={group} total={blocks.length} catalogs={catalogs}
                      onOpen={() => openRenderGroup(group.key)} />
                  );
                })()}
              </div>
            </div>
            <div className="sb-deck-collapse">
              <div>
                {/* 레이아웃 설정 UI는 MVP 이후 재도입한다. sectionLayout 로직과 저장 필드는 유지한다. */}
                <div className="sb-canvas-grid">
                  {canvasUnits(group.items).map((unit) => (
                    unit.kind === 'spaceRun' ? renderSpaceRun(unit, group) : renderUnit(unit, group)
                  ))}
                  {!group.items.length && insertControl(groupSection.start, group, null, null, 'empty')}
                  {/* 개별 컷 추가 — 점선 카드(구 UI, 2026-08-15 오너 스크린샷). 후킹 섹션은
                      스타일이 컷 구성을 지배하므로 여기서 컷을 추가하지 않는다. */}
                  {group.items.length > 0 && groupSection.role !== SECTION_ROLES.HOOKING && (
                    <div className="sb-grid-unit">
                      <button
                        type="button"
                        className="sb-addcard"
                        disabled={locked}
                        onClick={() => addBlock(
                          group.items[group.items.length - 1].index,
                          groupSection.id,
                          groupSection.role,
                          null,
                          group.key,
                        )}
                      >
                        ＋ 컷 추가
                      </button>
                    </div>
                  )}
                </div>
                {/* 예시 셔플 — 섹션1(후킹)에만, 마지막 카드 우측 아래(2026-08-15 오너).
                    장소세트는 renderSpaceRun 안에서 세트 단위로 붙는다. */}
                {group.items.length > 0 && groupSection.role === SECTION_ROLES.HOOKING && (
                  <div className="sb-shuffle-row end">
                    <button
                      type="button"
                      className="sb-shuffle-btn"
                      disabled={locked}
                      onClick={(event) => { event.stopPropagation(); shuffleSection(group); }}
                    >
                      {ShuffleIcon}예시 셔플
                    </button>
                  </div>
                )}
              </div>
            </div>
          </section>
        );
      })}
    </div>
  );


  const selectedSpaceRun = selected?.spaceGroupId
    ? groupConsecutiveSpaceRuns(blocks).find((run) => run.kind === 'space'
      && run.items.some((block) => block.id === selected.id)) : null;
  const selectedSpaceSiblings = selectedSpaceRun?.items || [];
  const selectedSpaceContext = selectedSpaceRun ? {
    siblings: selectedSpaceSiblings,
    set: inferStoryboardSpaceSet(selectedSpaceRun.spaceGroupId),
  } : null;
  // '첫 화면 스타일'은 후킹 섹션 AI 컷의 인스펙터에만 얹는다(상시 패널 없음 — 오너 확정).
  const hookStyleSection = hookFrame && selected
    && selected.sectionRole === SECTION_ROLES.HOOKING && selected.source !== 'mine'
    ? {
      frame: hookFrame,
      blocks,
      catalogs,
      colors: composeModeSeed.colors,
      productName,
      saving: hookStyleSaving,
      error: hookStyleError,
      onSelectStyle: applyHookStyleChoice,
      clothingType,
      gender: boundGenderNow,
      isCutAvailable: hookCutAvailable,
    } : null;
  const inspector = setPicker ? (
    <SpaceSetGallery mode={setPicker.mode} error={setPickerError} onChoose={chooseSpaceSet}
      gender={exampleGender} clothingType={clothingType}
      onClose={() => { setSetPicker(null); setSetPickerError(null); }} />
  ) : <Inspector key={selectedId} block={selected} catalogs={catalogs} colorOpts={colorOpts} detailColorOpts={detailColorOpts} clothingType={clothingType} exampleGender={exampleGender} hasDetailImage={hasDetailImage} projectId={projectId}
    hookStyle={hookStyleSection}
    onChange={(p, options) => patch(selectedId, p, options)} onAtomicChange={(p, options) => atomicPatch(selectedId, p, options)} onRetryAtomicSave={retryAtomicSave} requestedRecipe={pendingSectionMove}
    onCancelRequestedRecipe={() => setPendingSectionMove(null)} matchClothing={matchClothing}
    spaceContext={selectedSpaceContext}
    onChangeSpaceSet={() => {
      if (selected?.spaceGroupId) openSetPicker({ mode: 'replace', spaceGroupId: selected.spaceGroupId });
    }}
    onAddMine={addMineBlock}
    onExampleDrag={(value) => {
      setDragExampleId(value);
      if (value == null) { setDragOver(null); setDragOverSec(null); setDragOverSpaceGroupId(null); }
    }}
    />;

  const cutCount = blocks.length;
  const body = (
    <div className={'sb-canvas-shell' + (splitOpen ? ' inspector-open' : '')}
      style={{ '--sb-inspector-top': `${inspectorTop}px` }}>
      <div className="sb-canvas-main">
        {list}
      </div>
      {splitOpen && <div className="insp-col">{inspector}</div>}
    </div>
  );
  // 크레딧은 AI 생성 컷에만 — 내 이미지 블록은 생성 작업이 없어 제외 (계약 §6)
  const aiCount = blocks.filter((b) => b.source !== 'mine').length;
  const mineCount = cutCount - aiCount;
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
      await sbSaveNow(projectId, () => atomicRetry.next);
      atomicRetryRef.current = null;
      showUndo(atomicRetry.previous, atomicRetry.next, { blockId: 'retry', label: '설정' });
      return true;
    } catch (error) {
      if (sbPending.get(projectId) === atomicRetry.next) sbPending.delete(projectId);
      directSaveSnapshots.current.add(atomicRetry.previous);
      setBlocks(atomicRetry.previous);
      throw error;
    } finally {
      atomicSavingRef.current = false;
      setAtomicSaving(false);
    }
  }
  function onComposeModeError() {
    toast.push('사진 양 선택을 저장하지 못했어요. 다시 선택해 주세요.');
  }
  // 사진 양 변경 계약: 현재 보드 flush → composeMode PATCH → 성공 시에만 reload.
  // helper가 false를 반환하면 현재 분할 선택 상태를 그대로 유지한다.
  function onComposeModeApply(nextMode) {
    return applyStoryboardComposeMode({
      currentMode: composeMode,
      nextMode,
      projectId,
      flushBoard: () => saveNow(projectId),
      setComposeMode,
      restoreComposeMode,
      invalidateStoryboardPrefetch: invalidateStoryboardEntryPrefetch,
      selectionState: composeModeSelectionRef,
      reloadStoryboard: () => {
        setLoadRetry((n) => n + 1);
      },
      onFlushFailure: onComposeModeError,
      onPatchFailure: onComposeModeError,
    });
  }
  const onCopywritingChange = (nextValue) => {
    void selectStoryboardCopywriting({
      currentValue: useAppStore.getState().copywriting,
      nextValue,
      setCopywriting: setCopyOn,
      restoreCopywriting: restoreCopyOn,
      selectionState: copywritingSelectionRef,
      onFailure: () => toast.push('카피라이팅 설정을 저장하지 못했어요. 다시 시도해 주세요.'),
    });
  };
  const goToMannequin = async () => {
    // 방어: UI disabled 와 별개로 함수 자체도 게이트 — 다른 호출 경로가 생겨도 미설정 블록 생성 불가
    if (blocks.length === 0) return;
    if (blocks.some((b) => b.source !== 'mine' && (!b.contentRole || !b.cutType))) { toast.push('생성 설정을 준비하지 못한 이미지가 있어요'); return; }
    // 생성 입력은 서버가 저장된 콘티에서 읽는다 — 다음 단계로 넘기기 전에 반드시 저장.
    // 같은 직렬 체인 경유: 비행 중 자동저장 뒤에 줄서서 최신 스냅샷이 마지막에 반영됨을 보장.
    await continueAfterStoryboardFlush({
      flush: () => saveNow(projectId),
      navigate: () => navigate('/create/mannequin'),
      onFailure: (message) => toast.push(message),
    });
  };
  return (
    <div className={`wizard wide sb-page sb-content-enter sb-initial-reveal${initialBoardRevealed ? ' is-revealed' : ''}${atomicSaving ? ' is-atomic-saving' : ''}`}
      aria-busy={!initialBoardRevealed || atomicSaving || undefined}
      onClickCapture={atomicSaving ? (event) => { event.preventDefault(); event.stopPropagation(); } : undefined}
      onDragStartCapture={atomicSaving ? (event) => { event.preventDefault(); event.stopPropagation(); } : undefined}>
      {/* 완료 가드는 게이트를 기다리지 않는다 — 기다리면 잠금 없이 보드가 활성화되는 창이 생긴다(리뷰 P1) */}
      {doneBlocked && <DoneGuardModal />}
      <PageHead title="상세페이지 초안 구성" sub="지금 보이는 이미지들은 예시입니다. 느낌만을 보고 필요한 컷은 수정하며 상세페이지를 생성해보세요." />
      {undoEntry && (
        <div className={`sb-undo-bar${undoExiting ? ' exiting' : ''}`} role="status" aria-live="polite"
          style={{ top: `${inspectorTop}px` }}
          onAnimationEnd={(event) => {
            if (event.target === event.currentTarget && undoExiting) finishUndoDismiss();
          }}
          onMouseEnter={() => {
            undoHoveredRef.current = true;
            const entry = undoEntryRef.current;
            if (!entry) return;
            entry.remainingMs = entry.deadline == null
              ? (entry.remainingMs || UNDO_WINDOW_MS)
              : Math.max(1, entry.deadline - Date.now());
            entry.deadline = null;
            clearTimeout(undoTimerRef.current);
          }}
          onMouseLeave={() => {
            undoHoveredRef.current = false;
            const entry = undoEntryRef.current;
            if (entry) scheduleUndoDismiss(entry, entry.remainingMs || UNDO_WINDOW_MS);
          }}>
          <span>{undoEntry.message}</span>
          <button type="button" onClick={undoLatest}><Icon name="undo" size={15} />{undoEntry.operationCount}건 되돌리기</button>
        </div>
      )}
      {body}

      {/* document-flow bottom action bar */}
      <div className="sb-actionbar">
        <div className="sb-ab-inner">
          <div className="sb-ab-count">
            AI 생성 {aiCount}컷 · 셀러 사진 {mineCount}컷
          </div>
          <span className="sb-ab-cost">생성 {uniqueGenerationCutCount(blocks) * (catalogs.creditCosts?.storyboardPerCut ?? 1)} 크레딧</span>
          <div className="sb-ab-copy">
            <Toggle on={copyOn} onChange={onCopywritingChange} label="카피라이팅" />
            <div><div className="sec-title" style={{ fontSize: 14 }}>카피라이팅 {copyOn ? 'ON' : 'OFF'}</div>
              <div className="hint" style={{ marginTop: 1 }}>AI가 카피를 자동으로 넣어요</div></div>
          </div>
          <button className="btn btn-primary btn-lg sb-ab-go btn-glowring" onClick={goToMannequin}
            disabled={blocks.length === 0 || blocks.some((b) => b.source !== 'mine' && (!b.contentRole || !b.cutType))}
            title={blocks.length === 0 ? '컷을 1개 이상 구성해주세요'
              : blocks.some((b) => b.source !== 'mine' && (!b.contentRole || !b.cutType)) ? '생성 설정을 준비하지 못한 이미지가 있어요' : undefined}>
            다음 · 마네킹컷 확인하기 <Icon name="arrowRight" size={17} />
          </button>
        </div>
      </div>
    </div>
  );
}

export default Storyboard;
