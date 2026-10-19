import { baseProductPhoto } from '@/lib/draftVisuals.js';
import { isAnalysisFastEntryEnabled } from '@/lib/analysisFastEntry.js';
import { createDraftFeatureRun, restoreDraftFeatureRun } from '@/lib/featureRunDraft.js';
import { getActiveFeatureRun } from '@/lib/featureRun.js';
import { analysisPhotoKey } from '@/lib/api/publicAnalysisRequest.js';
import { ClientErrorCapture } from '@/components/ClientErrorCapture.jsx';
/* =============================================================
   features/product-input — ① 제품 정보 입력 (PRD §5)
   Ported verbatim from reference/prototype/features/product-input.jsx.
   Only change: ES imports/exports; onNext → React Router navigate.
   Markup, classNames, inline styles, real file upload unchanged.
   ============================================================= */
import { mannequinQuoteConfirmation, expectedMannequinQuote } from '@/features/mannequin/quoteConfirmation.js';
import { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useLocation, useNavigate } from 'react-router-dom';
import { api, isMockMode } from '@/lib/api/index.js';
import { uid } from '@/lib/ids.js';
import { isGenerationRelevantAnalysisPatch, useAppStore } from '@/store/useAppStore.js';
import { generationWorkWarningKind } from '@/lib/generationWorkWarning.js';
import { mergeOutfitSaveResponse, outfitContextKey, syncOutfitLegacySelection } from '@/lib/outfitSelections.js';
import { mannequinGenerationCreditShortfall } from '@/lib/creditPreflight.js';
import {
  CREDIT_COSTS,
  mannequinGenerationTotal,
  mannequinRegenerationCreditText,
  normalizePlanTier,
} from '@/lib/limits.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { CreditShortfallModal } from '@/features/credits/CreditShortfallModal.jsx';
import {
  flushProductDraftSave,
  hasPendingDraft,
  loadDraft,
  clearDraftIfCurrent,
  queueProductDraftSave,
  collectDraftPhotos,
} from '@/lib/draftStore.js';
import { isMobileDevice } from '@/lib/deviceClass.js';
import { rememberMobileGuestSave, clearMobileGuestSave, claimMobileGuestSave } from '@/lib/mobileGuestSave.js';
import { readStoredAuthSession } from '@/lib/supabase.js';
import { isMobileDraftWriter } from '@/lib/draftDeviceNoun.js';
import { MobileDraftStatus } from '@/features/shell/MobileDraftDialog.jsx';
import { useDraftEntryActions } from '@/lib/useDraftEntryActions.js';
import { DraftEntryDialog } from '@/features/shell/DraftEntryDialog.jsx';
import { InputMainAction, MobileAnalyzedNotice, MobileSaveModal, MobileSaveStatus, useMobileInputSave } from './MobileInputHandoff.jsx';
import { confirmDraftPhotos } from '@/lib/draftPhotoConfirmation.js';
import { isRemoteAssetPhoto } from '@/lib/photoBytes.js';
import { isAnalysisRunning, setAnalysisRunning } from '@/lib/flowSession.js';
import {
  getUploadValidationError,
  looksLikeImageFile,
  MAX_UPLOAD_BYTES,
  toUploadableImages,
} from '@/lib/imageTranscode.js';
import {
  promoteDraftToProject,
  resetDraftSyncSingleFlight,
  retryDraftPromotion,
} from '@/lib/draftSync.js';
import {
  adoptProductPhotoPromotion,
  clearProductPhotoPromotionTask,
  NEW_PROJECT_KEY,
  startProductPhotoPromotion,
} from '@/lib/productPhotoPromotion.js';
import {
  draftSlot,
  formatDraftClock,
  formatDraftRelativeTime,
} from '@/lib/draftSlot.js';
import { Icon, Button, IconButton, ErrorState, Modal, UploadPendingTile, useToast } from '@/components/ui.jsx';
import { useDoneGuard, DoneGuardModal } from '@/features/shell/shell.jsx';
import { ProductInputHeading, ProductInputPlaceholder } from './ProductInputFrame.jsx';
import { AnalysisForm, AnalysisSkeleton, AnalysisProgress, isMatchRecommendationPatch } from '@/features/analysis/AnalysisForm.jsx';
import { normalizeAnalysisFit } from '@/lib/fitAxes.js';
import {
  analysisResponseProductPatch,
  createTrailingPatchScheduler,
  hasPatchFields,
  mergeColorMetadataWithPersistedImages,
  mergeLatestFailedAnalysisPatch,
  mergeProductOwnedAnalysisFields,
  persistAnalysisEdit,
  registerAnalysisEditSave,
  splitAnalysisEditPatch,
} from './saveRouting.js';
import { getBaseSlotUploadRoom, getPendingTileCount, PENDING_TILE_DELAY_MS } from './pendingTiles.js';
import { restoreDraftProduct } from './draftProductRestore.js';
import { photoCaption } from './photoCaption.js';
import { createProductPhotoPreviewRegistry } from './productPhotoPreviewRegistry.js';
import { autofillColorGroups } from './colorAutofill.js';
import { getSwatchScrollThumb } from './swatchScrollIndicator.js';
import {
  invalidateStoryboardEntryPrefetch,
  prefetchStoryboardAfterProductPhotos,
} from '@/features/storyboard/storyboardEntryPrefetch.js';
import { acknowledgeMannequinGenerationCancellation } from '@/features/mannequin/generationRunner.js';
import {
  invalidateStoryboardForProductPhotoEdit,
  storyboardTransitionState,
} from './storyboardTransition.js';

draftSlot.configure(api);

// human-readable file size
const fmtSize = (b) => b == null ? '' : b < 1024 ? b + ' B' : b < 1048576 ? (b / 1024).toFixed(1) + ' KB' : (b / 1048576).toFixed(1) + ' MB';

const chipWidth = (value) => [...value].reduce(
  (width, character) => width + (/[\uAC00-\uD7A3]/.test(character) ? 1 : 0.55),
  0,
).toFixed(1);

function ColorSwatchPicker({ mobile = false, swatchColors, value, name, onChange, onNameChange, disabled = false }) {
  const [nameDraft, setNameDraft] = useState(name || '');
  const [nameFocused, setNameFocused] = useState(false);
  useEffect(() => { setNameDraft(name || ''); }, [name]);

  const gridRef = useRef(null);
  const trackRef = useRef(null);
  useEffect(() => {
    if (!mobile) return;
    const grid = gridRef.current;
    const track = trackRef.current;
    if (!grid || !track) return;
    let frame = null;
    // 스크롤 중에는 React 상태 대신 표시기의 CSS 변수만 갱신한다.
    const update = () => {
      frame = null;
      const thumb = getSwatchScrollThumb(grid.clientHeight, grid.scrollHeight, grid.scrollTop);
      track.hidden = !thumb;
      if (!thumb) return;
      track.style.setProperty('--swatch-thumb-height', `${thumb.height}px`);
      track.style.setProperty('--swatch-thumb-offset', `${thumb.offset}px`);
    };
    const schedule = () => {
      if (frame === null) frame = requestAnimationFrame(update);
    };
    const observer = new ResizeObserver(schedule);
    observer.observe(grid);
    for (const chip of grid.children) observer.observe(chip);
    grid.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule);
    schedule();
    return () => {
      observer.disconnect();
      grid.removeEventListener('scroll', schedule);
      window.removeEventListener('resize', schedule);
      if (frame !== null) cancelAnimationFrame(frame);
    };
  }, [mobile, swatchColors]);

  const grid = (
      <div className="swatch-grid" ref={gridRef}>
        {swatchColors.map((s) => {
          const on = value === s.id;
          return (
            <button type="button" key={s.id} className={`swatch${on ? ' on' : ''}`}
              disabled={disabled} onClick={() => onChange(s.id)}>
              <span className="swatch-dot" style={{ background: s.hex }} />
              {s.label}
              {on && <Icon name="check" size={13} className="check" />}
            </button>
          );
        })}
        <span className="chip-input-wrap color-name-input-wrap">
          <input
            className={`chip chip-input color-name-input${nameFocused || name ? ' on' : ''}`}
            value={nameDraft}
            placeholder="직접 입력"
            disabled={disabled}
            aria-label="세부 색 이름 직접 입력"
            style={{ width: `calc(${chipWidth(nameDraft || '직접 입력')}em + 32px)` }}
            onChange={(event) => setNameDraft(event.target.value)}
            onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }}
            onFocus={() => setNameFocused(true)}
            onBlur={() => {
              setNameFocused(false);
              const nextName = nameDraft.trim();
              if (nextName === (name || '')) return;
              setNameDraft(nextName);
              onNameChange(nextName);
            }} />
        </span>
      </div>
  );

  return (
    <div className="color-pick">
      <div className="color-pick-head">
        <label className="lbl">색상 선택</label>
        <span className="hint">색 계열을 골라주세요 · 세부 색 이름(연청 등)은 직접 입력</span>
      </div>
      {mobile ? (
        <div className="swatch-scroll-area">
          {grid}
          <div className="swatch-scroll-track" ref={trackRef} hidden aria-hidden="true">
            <div className="swatch-scroll-thumb" />
          </div>
        </div>
      ) : grid}
    </div>
  );
}

// build file metas from a FileList (drag-drop / picker), capping to the room left.
// HEIC(아이폰 기본 포맷)는 여기서 JPEG 로 바꾼다 — 이 objectURL 이 미리보기·draft·업로드에
// 그대로 흘러가므로(다운스트림이 fetch(src).blob() 으로 복원) 변환 지점은 여기 한 곳이면 된다.
const filesToMetas = async (fileList, room) => {
  const selected = [...fileList];
  const uploadableCandidates = selected.filter(looksLikeImageFile);
  const skippedByType = selected
    .filter((file) => !looksLikeImageFile(file))
    .map((file) => ({ file, reason: 'not_image' }));
  const availableRoom = Math.max(0, room);
  const picked = uploadableCandidates.slice(0, availableRoom);
  const skippedByRoom = uploadableCandidates.slice(availableRoom);
  if (!picked.length) return {
    metas: [], skippedByRoom, skippedByType, skippedBySize: [], transformFailed: [],
  };
  const { files, failed: transformFailed } = await toUploadableImages(picked);
  const validFiles = [];
  const skippedBySize = [];
  for (const file of files) {
    const reason = getUploadValidationError(file);
    if (reason === 'unsupported_type') skippedByType.push({ file, reason });
    else if (reason === 'file_too_large') skippedBySize.push({ file, reason });
    else validFiles.push(file);
  }
  return {
    metas: validFiles.map((f) => ({
      src: URL.createObjectURL(f), name: f.name, size: f.size,
      type: f.type, lastModified: f.lastModified,
    })),
    skippedByRoom,
    skippedByType,
    skippedBySize,
    transformFailed,
  };
};
const fileExt = (im) => (im.type && im.type.split('/')[1] ? im.type.split('/')[1].toUpperCase() : 'IMG');

function hasRequiredDraftPhotos(product) {
  const base = (product?.colors || []).find((color) => color.isBase) || product?.colors?.[0];
  return Boolean(
    base?.images?.some((image) => image.slot === 'Front')
    && base.images.some((image) => image.slot === 'Back'),
  );
}

/* 입력 화면을 열기만 한 상태(빈 템플릿)인지, 실제로 지킬 입력이 있는지 판정.
   빈 상태를 임시저장하면 '임시저장 · 사진 0장' 팬텀 카드·잠금이 아무때나 뜬다.
   서버(_draft_slot_meta.hasContent)·mock 도 같은 기준을 쓴다. */
function draftHasContent(product, analysis) {
  return Boolean(
    analysis
    || (product && (
      (product.name || '').trim()
      || (product.colors || []).some((color) => (color.images || []).length > 0)
    )),
  );
}

// small file-meta caption shown over an uploaded image (name · size · type) — requested feature.
// caption 은 photoCaption 이 정리한 표시용 이름 — OS 임시 이름(tempImage…)이면 '앞면 사진 1'.
// 원본 파일명은 지우지 않고 tooltip 에 남긴다(셀러가 실제 파일을 되찾는 단서).
function MetaCap({ im, caption }) {
  const shown = caption || im.name || '이미지';
  return (
    <span className="img-cap">
      <span className="img-cap-name" title={im.name && im.name !== shown ? im.name : shown}>{shown}</span>
      <span className="img-cap-sub">{fmtSize(im.size)} · {fileExt(im)}</span>
    </span>
  );
}

// add target that ALSO accepts drag-drop + click-to-pick (keeps original .tile.add / .up-empty styles)
function AddDrop({ className, slot, room, onAddFiles, onPendingChange, immediatePending = false, children }) {
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);   // HEIC 변환 중 — 큰 사진은 1~2초 걸린다
  const inputRef = useRef(null);
  const pendingTimerRef = useRef(null);
  const mountedRef = useRef(true);
  const toast = useToast();
  const disabled = room <= 0 || busy;
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      clearTimeout(pendingTimerRef.current);
    };
  }, []);
  const take = async (fileList) => {
    const selected = [...fileList];
    const skippedByType = selected.filter((file) => !looksLikeImageFile(file));
    if (skippedByType.length) {
      toast.push(`이미지 파일만 올릴 수 있어요 (${skippedByType.map((file) => file.name || '이 파일').join(', ')})`,
        { icon: 'alertTri' });
    }
    const pendingCount = getPendingTileCount(selected, room);
    setBusy(true);
    clearTimeout(pendingTimerRef.current);
    if (pendingCount && immediatePending) onPendingChange(slot, pendingCount);
    if (pendingCount) {
      pendingTimerRef.current = setTimeout(() => {
        if (mountedRef.current) onPendingChange(slot, pendingCount);
      }, PENDING_TILE_DELAY_MS);
    }
    try {
      const {
        metas, skippedByRoom, skippedByType: typeFailures, skippedBySize, transformFailed,
      } = await filesToMetas(selected, room);
      if (metas.length) onAddFiles(slot, metas);
      if (skippedByRoom.length) {
        toast.push(`남은 자리는 ${Math.max(0, room)}장이에요. ${skippedByRoom.map((file) => file.name || '이 사진').join(', ')}은(는) 추가하지 못했어요.`,
          { icon: 'alertTri' });
      }
      for (const { file, reason } of typeFailures) {
        if (reason === 'not_image') continue; // 선택 직후 이미 안내했다.
        toast.push(`${file.name || '이 사진'}: 지원하지 않는 이미지 형식이에요. JPG·PNG·WEBP·GIF·AVIF로 저장해 다시 올려주세요.`,
          { icon: 'alertTri' });
      }
      for (const { file } of skippedBySize) {
        const message = file.size > MAX_UPLOAD_BYTES
          ? `${file.name || '이 사진'}: 파일 용량이 25MB를 넘어요.`
          : `${file.name || '이 사진'}: 빈 파일은 올릴 수 없어요.`;
        toast.push(message, { icon: 'alertTri' });
      }
      for (const _failed of transformFailed) {
        toast.push('이 사진을 불러오지 못했어요. JPG로 저장해 다시 올려주세요', { icon: 'alertTri' });
      }
    } finally {
      clearTimeout(pendingTimerRef.current);
      if (mountedRef.current) {
        onPendingChange(slot, 0);
        setBusy(false);
      }
    }
  };
  return (
    <button type="button" className={`${className}${over ? ' over' : ''}${busy ? ' is-busy' : ''}`} disabled={disabled}
      onClick={() => inputRef.current && inputRef.current.click()}
      onDragOver={(e) => { e.preventDefault(); if (!disabled) setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); if (!disabled) take(e.dataTransfer.files); }}>
      {/* .heic/.heif 를 명시 — 일부 브라우저·OS 는 image/* 만으로는 HEIC 를 선택지에 안 띄운다 */}
      <input ref={inputRef} type="file" accept="image/*,.heic,.heif,.hif" multiple hidden
        onChange={(e) => { take(e.target.files); e.target.value = ''; }} />
      {children}
    </button>
  );
}

function PendingTile({ small }) {
  return <UploadPendingTile className={`tile${small ? ' sm' : ''}`} />;
}

function ProductPhotoPreview({ image, displayUrl }) {
  const src = displayUrl(image.id, image.src);
  return src
    ? <img src={src} alt="" decoding="async" onError={(e) => { e.currentTarget.style.opacity = 0; }} />
    : <span className="product-photo-preview-pending" aria-hidden="true" />;
}

function ColorImageGroup({ mobile = false, group, catalogs, swatchColors, onAddFiles, onRemove, onRename, onRemoveGroup, onPickColor, displayUrl, photosLocked = false, readOnly = false, onPendingChange }) {
  const base = group.isBase;
  const used = group.images.length;
  const [pendingBySlot, setPendingBySlot] = useState({});
  useEffect(() => () => onPendingChange?.(group.id, null, 0), []);
  const setSlotPending = (slot, count) => {
    onPendingChange?.(group.id, slot, count);
    setPendingBySlot((current) => {
      if (count) return { ...current, [slot]: count };
      if (!current[slot]) return current;
      const next = { ...current };
      delete next[slot];
      return next;
    });
  };
  const chosen = (swatchColors || []).find((s) => s.id === group.swatchId);
  const colorLabel = group.name?.trim() || chosen?.label || '색상 미정';
  // 점은 스와치 계열만 반영한다. 세부 이름만 있으면 회색 '색상 미정' 상태를 유지한다.
  const colorInd = (
    <span className="color-ind" title={colorLabel}>
      <span className={`color-ind-dot${chosen ? '' : ' undecided'}`} style={{ background: chosen ? chosen.hex : '#d4d4d8' }} />
      <span className={`color-ind-label${colorLabel === '색상 미정' ? ' undecided' : ''}`}>{colorLabel}</span>
    </span>
  );
  const slotLabel = (s) => (catalogs.angleLabels && catalogs.angleLabels[s]) || s;
  const MAX = 6;
  const tiles = (s, small) => {
    const imgs = group.images.filter((im) => im.slot === s);
    const room = base ? getBaseSlotUploadRoom(group.images, s, MAX) : MAX - used;
    const pendingCount = getPendingTileCount(pendingBySlot[s], room);
    return (
      <div className="slot-tiles">
        {imgs.map((im, imageIndex) => (
          <div className={`tile${small ? ' sm' : ''}`} key={im.id}>
            <ProductPhotoPreview image={im} displayUrl={displayUrl} />
            {!photosLocked && <button className="rm" aria-label="내가 업로드한 의류 사진 삭제" onClick={() => onRemove(im.id)}><Icon name="x" size={12} /></button>}
            <MetaCap im={im} caption={photoCaption(im.name, slotLabel(s), imageIndex)} />
          </div>
        ))}
        {Array.from({ length: pendingCount }, (_, index) => (
          <PendingTile key={`${s}-pending-${index}`} small={small} />
        ))}
        {!photosLocked && (
          <AddDrop className={`tile add${small ? ' sm' : ''}`} slot={s} room={room}
            onAddFiles={onAddFiles} onPendingChange={setSlotPending} immediatePending={Boolean(onPendingChange)}>
            {base && room <= 0 ? (
              <span className="add-limit">최대 6장까지 이미지를 업로드할 수 있습니다.</span>
            ) : (
              <>
                <span className="add-ico"><Icon name="imagePlus" size={small ? 24 : 26} /></span>
                <span className="add-cap">이미지 업로드</span>
              </>
            )}
          </AddDrop>
        )}
      </div>
    );
  };
  // 2×2 angle "wells" — all four angles at a glance, images stack inside each
  const wellSlot = (s) => (
    <div className="slot-well" key={s}>
      <div className="slot-well-head"><span className="swh-label">{slotLabel(s)}</span><span className={`ds-field-status${s === 'Front' || s === 'Back' ? ' required' : ''}`}>{s === 'Front' || s === 'Back' ? '필수' : '선택'}</span></div>
      {tiles(s, true)}
    </div>
  );
  return (
    <div className="color-group">
      {!base && (
        <div className="color-group-head">
          <div className="ttl">
            <span className="color-swatch" style={{ background: chosen ? chosen.hex : '#e9e7ec' }} />
            <div className="sec-title" style={{ fontSize: 15 }}>{colorLabel}</div>
          </div>
          {!photosLocked && <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <IconButton name="trash" size="sm" onClick={onRemoveGroup} title="색상 삭제" />
          </div>}
        </div>
      )}

      {base ? (
        <>
          <div className="color-bar">{colorInd}</div>
          <div className="slot-wells">{catalogs.angleSlots.map(wellSlot)}</div>
        </>
      ) : (
        <div className="slot-tiles">
          {group.images.map((im, imageIndex) => (
            <div className="tile" key={im.id}>
              <ProductPhotoPreview image={im} displayUrl={displayUrl} />
              {!photosLocked && <button className="rm" aria-label="내가 업로드한 의류 사진 삭제" onClick={() => onRemove(im.id)}><Icon name="x" size={12} /></button>}
              <MetaCap im={im} caption={photoCaption(im.name, slotLabel(im.slot), imageIndex)} />
            </div>
          ))}
          {Array.from({ length: getPendingTileCount(pendingBySlot.Front, 3 - used) }, (_, index) => (
            <PendingTile key={`Front-pending-${index}`} />
          ))}
          {!photosLocked && (
            <AddDrop className="tile add" slot="Front" room={3 - used}
              onAddFiles={onAddFiles} onPendingChange={setSlotPending} immediatePending={Boolean(onPendingChange)}>
              <Icon name="plus" size={16} />{used === 0 ? '정면 필수' : '추가'}
            </AddDrop>
          )}
        </div>
      )}

      {used > 0 && !readOnly && (
        <ColorSwatchPicker mobile={mobile} swatchColors={swatchColors} value={group.swatchId} name={group.name}
          onChange={onPickColor} onNameChange={onRename} />
      )}

      {/* 개수 안내는 '상품 이미지' 제목 옆 pill 로 옮겼다(2026-08-14 사용자 결정) — base 중복 제거 */}
      {!base && <p className="cap-note">정면 사진 필수 · 색상당 최대 3장 · 현재 {used}장</p>}
    </div>
  );
}

function EditingRightsLock({ meta, onReclaim, onRestartLocal, onDiscard, mobile = isMobileDevice() }) {
  const gone = meta?.state === 'gone';
  return (
    <div className="draft-slot-lock" role="alertdialog" aria-modal="true">
      <div className="draft-slot-lock-card">
        <Icon name="lock" size={28} />
        {gone ? (
          <>
            <h3>{mobile ? '이 상품은 다른 곳에서 마무리됐거나, 보관 기간이 지나 정리됐어요.' : '다른 곳에서 이 작업을 끝내거나 새로 시작했어요'}</h3>
            <p>지금 화면에 남아 있는 내용을 계속 쓸지 골라주세요.</p>
            <div className="modal-actions">
              <Button variant={mobile ? "primary" : "ghost"} onClick={onDiscard}>{mobile ? '새 상품 업로드' : '지우고 새로 시작'}</Button>
              <Button variant={mobile ? "ghost" : "primary"} onClick={onRestartLocal}>{mobile ? '이 사진으로 다시 업로드' : '이 내용 계속 쓰기'}</Button>
            </div>
          </>
        ) : (
          <>
            <h3>다른 곳에서 같은 작업을 하고 있어요</h3>
            <p>
              {formatDraftRelativeTime(meta?.updatedAt)} · {meta?.deviceLabel || '다른 탭 또는 기기'}에서 저장했어요.
              <br />내용이 서로 엉키지 않게 이 화면의 자동 저장을 잠시 멈췄어요.
            </p>
            <Button variant="primary" onClick={onReclaim}>이 화면에서 이어서 하기</Button>
          </>
        )}
      </div>
    </div>
  );
}

export function ProductInput({ onContentChange, initialSavedMeta, onFreshStart, autoSaveIntent } = {}) {
  const navigate = useNavigate();
  const mobile = isMobileDevice();
  const location = useLocation();
  const [product, setProduct] = useState(null);
  const [catalogs, setCatalogs] = useState(null);
  const [loadOriginalError, setLoadOriginalError] = useState(null);
  const [loadError, setLoadError] = useState('');
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [phase, setPhase] = useState('input');   // input | analyzing | done
  // 분석 결과 도착 신호 — 화면 전환은 대기 연출(AnalysisProgress)이 잔여 단계를 완주한 뒤
  // onFinished 로 수행한다 (애니메이션 끝 ≈ 전환, 2026-07-13 A안 결정).
  const [analysisReady, setAnalysisReady] = useState(false);
  const [analysis, setAnalysis] = useState(null);
  const handoff = useMobileInputSave(product, initialSavedMeta);
  const mobileReadOnly = mobile && (phase === 'done' || handoff.readOnly);
  const [pendingConversions, setPendingConversions] = useState({});
  const hasPendingConversions = Object.values(pendingConversions).some(Boolean);
  const reportPending = (groupId, slot, count) => setPendingConversions((current) => {
    const next = { ...current };
    if (slot == null) {
      for (const key of Object.keys(next)) if (key.startsWith(`${groupId}:`)) delete next[key];
    } else if (count) next[`${groupId}:${slot}`] = count;
    else delete next[`${groupId}:${slot}`];
    return next;
  });
  const [analysisProjectId, setAnalysisProjectId] = useState(null);
  const composeMode = useAppStore((s) => s.composeMode);
  const visualsEnabled = useAppStore((s) => s.account?.draftVisualsEnabled === true);
  const [expanded, setExpanded] = useState(false);
  // AG-IC 입력 사진 동일성 경고. 서버가 warn 모드일 때만 내려온다(off 면 undefined).
  // 업로드 순간이 아니라 **생성으로 넘어가는 버튼**에서 한 번 띄운다 — 사진을 고르는 도중에
  // 끼어들면 아직 다 올리지도 않은 셀러를 방해한다.
  const [inputConsistency, setInputConsistency] = useState(null);
  const [consistencyAck, setConsistencyAck] = useState(false);   // '계속 진행' 누른 뒤 재차 막지 않는다
  const [consistencyOpen, setConsistencyOpen] = useState(false);
  const [photoFailure, setPhotoFailure] = useState(null);
  // 성별·의류 종류 등 생성 관련 필드를 바꾸면 콘티/마네킹의 기존 작업이 무효화된다 — 적용을
  // 보류하고 대가를 먼저 보여준다. 확정 전엔 화면·서버 어느 쪽에도 반영하지 않는다(취소=무해).
  const [pendingRelevantPatch, setPendingRelevantPatch] = useState(null);
  const [cancellingRelevantPatch, setCancellingRelevantPatch] = useState(false);
  const [creditShortfall, setCreditShortfall] = useState(null);
  const [creditQuote, setCreditQuote] = useState(null);
  const [creditResume, setCreditResume] = useState(() => (
    location.state?.creditResume?.action === 'storyboard' ? location.state.creditResume : null
  ));
  const [slotLock, setSlotLock] = useState(null);
  const [reclaimChoiceOpen, setReclaimChoiceOpen] = useState(false);
  const [promotionLocked, setPromotionLocked] = useState(false);
  const { session, loading: authLoading, openLogin, sellerConsentReady, retrySellerConsent } = useAuth();
  const [preparingLogin, setPreparingLogin] = useState(false);
  const loginSaveRef = useRef(false);
  const loginDraftRef = useRef(null);
  const visualsRequestedRef = useRef(false);
  const draftFront = baseProductPhoto(product);
  useEffect(() => {
    if (visualsEnabled && session && visualsRequestedRef.current === session.user?.id && !mobile) {
      void draftSlot.prepareProductVisuals(product).catch(() => {});
    }
  }, [draftFront?.id, draftFront?.src, session?.user?.id, visualsEnabled]);
  const slotEnabled = Boolean(session) || isMockMode;
  const doneBlocked = useDoneGuard(!mobile || slotEnabled);   // 생성 완료 후 초안 재진입 제한 (PRD §10.17)
  const toast = useToast();
  const pushToast = toast.push;
  const { actions: entryActions, dialog: entryDialog, busy: entryBusy } = useDraftEntryActions({
    beginProject: (options) => useAppStore.getState().beginProject(options), mobile,
    onOpened: (meta) => { onFreshStart?.(meta); navigate('/create/input', { replace: true }); },
    onKept: (meta) => setSlotLock(meta),
    onStarted: () => { onFreshStart?.(); navigate('/create/input', { replace: true }); },
    onError: pushToast,
  });
  const [, refreshPhotoPreviews] = useState(0);
  const photoPreviewRegistryRef = useRef(null);
  const latestLocalUpdatedAtRef = useRef(null);
  const draftHadContentRef = useRef(false);   // 이 화면에서 실제 입력이 있었는지 — 빈 템플릿 임시저장 방지
  const latestProductRef = useRef(product);
  const latestAnalysisRef = useRef(analysis);
  const featureSavedAnalysisRef = useRef(null);
  const latestComposeModeRef = useRef(composeMode);
  const promotionRunRef = useRef(0);
  const entryUserRef = useRef(null);
  entryUserRef.current = session?.user?.id || null;
  const mountedRef = useRef(false);
  latestProductRef.current = product;
  latestAnalysisRef.current = analysis;
  latestComposeModeRef.current = composeMode;
  if (!photoPreviewRegistryRef.current) {
    photoPreviewRegistryRef.current = createProductPhotoPreviewRegistry({
      onChange: () => refreshPhotoPreviews((version) => version + 1),
    });
  }

  useEffect(() => {
    onContentChange?.(!product || draftHasContent(product, analysis) || hasPendingConversions);
  }, [product, analysis, hasPendingConversions, onContentChange]);

  useEffect(() => draftSlot.onConflict((meta) => {
    if (!mobile && meta?.state === 'gone'
        && !draftHasContent(latestProductRef.current, latestAnalysisRef.current)) {
      // 이 화면에 지킬 입력이 없다 — 고를 것도 없으니 잠금 화면 대신 조용히 새 저장 상태로 되돌린다.
      draftSlot.restartAfterGone();
      return;
    }
    setSlotLock(meta);
    if (!meta) setReclaimChoiceOpen(false);
  }), []);

  useEffect(() => draftSlot.onVisuals?.(() => {
    if (!session || mobile || session.user?.id !== draftSlot.getUserId() || !latestProductRef.current) return;
    queueProductDraftSave(latestProductRef.current, latestAnalysisRef.current, latestComposeModeRef.current);
  }), [session?.user?.id, mobile]);

  useEffect(() => {
    if (slotEnabled) draftSlot.activate();
  }, [slotEnabled]);

  useEffect(() => {
    if (!product || mobileReadOnly || (mobile && (entryBusy || slotLock || handoff.gone))) return;
    if (isAnalysisFastEntryEnabled() && featureSavedAnalysisRef.current !== null && featureSavedAnalysisRef.current === analysis) {
      featureSavedAnalysisRef.current = null;
      return;
    }
    const hasContent = draftHasContent(product, analysis);
    // 구경만 한 빈 화면은 임시저장을 만들지 않는다. 단 내용이 있다가 다 지워진 상태는
    // '비움'도 최신 상태이므로 계속 저장한다(안 하면 지운 사진이 복원에서 되살아난다).
    if (!hasContent && !draftHadContentRef.current) return;
    draftHadContentRef.current = draftHadContentRef.current || hasContent;
    const localUpdatedAt = new Date().toISOString();
    latestLocalUpdatedAtRef.current = localUpdatedAt;
    queueProductDraftSave(product, analysis, composeMode, localUpdatedAt);
    if (slotEnabled && !(mobile && session && sellerConsentReady === false)) {
      draftSlot.queue({ product, analysis, composeMode, localUpdatedAt });
    }
  }, [analysis, composeMode, product, slotEnabled, mobileReadOnly, mobile, slotLock, handoff.gone, entryBusy, session, sellerConsentReady]);

  useEffect(() => {
    if (!slotEnabled) return;
    let lastCheckAt = 0;
    const check = () => {
      if (!draftSlot.getToken()) return;
      const now = Date.now();
      if (now - lastCheckAt < 60000) return;
      lastCheckAt = now;
      void draftSlot.checkOwnership().catch(() => {});
    };
    window.addEventListener('focus', check);
    return () => window.removeEventListener('focus', check);
  }, [slotEnabled]);

  useEffect(() => {
    const hasFiles = (event) => Array.from(event.dataTransfer?.types || []).includes('Files');
    const preventFileNavigation = (event) => {
      if (hasFiles(event)) event.preventDefault();
    };
    const handleDocumentDrop = (event) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      if (!event.target?.closest?.('.tile.add')) {
        pushToast('사진은 점선 칸에 올려주세요', { icon: 'alertTri' });
      }
    };
    document.addEventListener('dragover', preventFileNavigation, true);
    document.addEventListener('drop', handleDocumentDrop, true);
    return () => {
      document.removeEventListener('dragover', preventFileNavigation, true);
      document.removeEventListener('drop', handleDocumentDrop, true);
    };
  }, [pushToast]);

  useEffect(() => {
    const images = product?.colors?.flatMap((color) => color.images || []) || [];
    photoPreviewRegistryRef.current.sync(images);
  }, [product]);

  useEffect(() => {
    const registry = photoPreviewRegistryRef.current;
    const userId = entryUserRef.current;
    const identityEpoch = draftSlot.getIdentityEpoch();
    // 입력 화면을 떠나도 저장 중인 사진 원본은 로컬 저장이 끝날 때까지 유지한다.
    return () => {
      void Promise.allSettled([flushProductDraftSave({ userId, identityEpoch }), loginDraftRef.current])
        .finally(() => registry.dispose());
    };
  }, []);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      promotionRunRef.current += 1;
      useAppStore.getState().setInputPromotionLocked(false);
    };
  }, []);

  useEffect(() => {
    if (!promotionLocked) return undefined;
    const blockUnload = (event) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', blockUnload);
    // 베일은 포인터만 막는다 — Shift+Tab 으로 뒤 폼 편집·헤더 Enter 로 SPA 이탈이 가능했다(P1).
    // #root 만 잠그면 body 로 포털된 모달(커스텀 의류 등)이 살아남아 승격 스냅샷 밖 편집이
    // 가능하다(P1 후속) — 베일·토스트를 뺀 body 직계 전부를 inert 로 잠근다.
    const spared = (el) => el.classList?.contains('input-promotion-transition')
      || el.classList?.contains('toast-host');
    const locked = [...document.body.children].filter(
      (el) => !spared(el) && !el.hasAttribute('inert'),
    );
    locked.forEach((el) => el.setAttribute('inert', ''));
    return () => {
      window.removeEventListener('beforeunload', blockUnload);
      locked.forEach((el) => el.removeAttribute('inert'));
    };
  }, [promotionLocked]);

  const setFlowPromotionLocked = (locked) => {
    setPromotionLocked(Boolean(locked));
    useAppStore.getState().setInputPromotionLocked(locked);
  };

  // 분석 CTA — 마네킹부터는 로그인 필요. 서버 분석을 마친 로그인 사용자는 바로 이동한다.
  // 로컬 분석 결과는 먼저 IndexedDB 에 보관한다. 미로그인이면 로그인 모달을 띄우고, 이미
  // 로그인한 상태(로그인 복귀 후 동기화 실패·다른 탭 로그인 포함)면 여기서 백엔드 동기화를
  // 재시도한 뒤 이동한다. analysisProjectId 를 로그인 여부의 대용값으로 쓰지 않는다.
  const redirectingRef = useRef(false);
  const analysisSaveChainRef = useRef(Promise.resolve());
  const analysisSaveErrorRef = useRef(null);
  const failedAnalysisPatchRef = useRef(null);
  const latestAnalysisPatchRef = useRef({});
  const persistedColorsRef = useRef([]);
  const analysisPatchQueueRef = useRef(null);
  const colorSaveSchedulerRef = useRef(null);
  const storyboardPrefetchProjectRef = useRef(null);
  const mannequinWorkCheckProjectRef = useRef(null);
  const cancellingRelevantPatchRef = useRef(false);
  const pendingRelevantWorkKindRef = useRef('none');
  if (!colorSaveSchedulerRef.current) {
    colorSaveSchedulerRef.current = createTrailingPatchScheduler({
      commit: (patch) => analysisPatchQueueRef.current?.(patch),
    });
  }

  const guardMannequinCredits = () => {
    // 클릭 순간의 계정으로 잔액과 요금을 함께 계산한다. 늦게 불러온 요금제를 이전 렌더의
    // 요금과 섞지 않는다. 비로그인·아직 계정을 못 불러온 상태는 과차단하지
    // 않고 통과시키며, 실제 잔액 정합성은 기존 서버 402 방어선이 계속 책임진다.
    const cachedAccount = session ? useAppStore.getState().account : null;
    const plan = normalizePlanTier(cachedAccount?.plan);
    const currentQuote = creditQuote?.plan === plan
      && creditQuote?.mannequinGenerate?.selectedModelId === analysis?.selectedModelId
      ? creditQuote : null;
    const requiredCredits = generationWorkKind === 'cuts'
      ? (currentQuote?.mannequinRegenerate?.nextCost ?? CREDIT_COSTS.mannequinGenerate)
      : (currentQuote?.mannequinGenerate?.total
        ?? mannequinGenerationTotal(plan, analysis?.selectedModelId));
    const shortfall = mannequinGenerationCreditShortfall(cachedAccount, requiredCredits);
    if (!shortfall) {
      if (generationWorkKind === 'cuts' && analysisProjectId) {
        mannequinQuoteConfirmation.remember(analysisProjectId, 'regenerate', expectedMannequinQuote(requiredCredits));
      }
      return true;
    }
    setCreditShortfall(shortfall);
    return false;
  };

  // 콘티 이동은 아래에서 명시적으로 flush한다. 브라우저 뒤로가기처럼 cleanup을 기다릴 수 없는
  // 이탈도 보류 저장을 시작하고, 콘티 쪽 프로젝트별 저장 barrier가 성공한 PATCH와 GET을 직렬화한다.
  useEffect(() => () => colorSaveSchedulerRef.current?.flush(), []);
  // 마네킹 컷이 이미 만들어져 있는가 — 성별·의류 종류를 바꾸는 경고를 띄울지 판정하는 신호.
  // 콘티(getStoryboard)는 저장분이 없으면 화면이 매번 기본 시드를 만들어 돌려주므로("보드가
  // 있다"가 늘 참이 되어 못 쓴다) — 실제로 셀러/시스템이 만든, 유료 산출물인 마네킹 컷의 존재로
  // 판정한다. 이 흐름(입력→콘티→마네킹)에서 컷은 콘티 진입 시 백그라운드로 생성되므로, 컷이
  // 있다는 것은 곧 콘티(따라서 그 안의 세트 선택)도 이미 거쳤다는 뜻 — 두 비용을 함께 경고해도 된다.
  const hasExistingGenerationWorkRef = useRef(false);
  // 컷이 아직 0장이어도 "이 프로젝트의 마네킹 생성이 지금 돌고 있다"면 같은 경고 대상이다 —
  // job 이 끝나면 방금 바꾼 값이 아니라 옛 선택으로 만든 유료 컷이 도착하고, 마네킹 화면의
  // dirty 플래그가 그걸 또 한 번 유료로 다시 만든다(두 번 과금). 완료를 기다렸다가 컷의 존재로
  // 판정하면 이미 늦으므로, 진행률처럼 시시각각 바뀌는 이 신호는 ref 가 아니라 store 구독으로
  // 읽는다 — 리렌더를 타야 이 화면에 머무는 동안의 실제 상태 변화(러너가 job 시작을 알리거나
  // 완료로 정리되는 순간)를 놓치지 않는다. status/projectId 만 구독해 progress 틱마다
  // 리렌더하지 않는다(불필요한 리렌더 방지).
  const mannequinJobStatus = useAppStore((s) => s.mannequinJob.status);
  const mannequinJobProjectId = useAppStore((s) => s.mannequinJob.projectId);

  // 분석 결과를 사용자가 검토하는 동안 다음 화면(콘티)을 미리 데운다. analysisProjectId 는
  // submit() 시작 시점(사진 업로드·저장·분석보다 먼저)에 이미 잡히므로 그것만으로는 이르다 —
  // 콘티 시드가 읽는 필드(colors·clothingType·targetGenders)가 전부 서버에 반영된 뒤인
  // phase==='done'(기존 프로젝트 재진입·초안 동기화·최초 분석 완료 세 경로 모두 이 시점엔
  // 관련 저장이 끝나 있다)까지 함께 기다린다.
  useEffect(() => {
    if (mobile) return;
    if (!analysisProjectId || phase !== 'done') return;
    if (storyboardPrefetchProjectRef.current === analysisProjectId) return;
    storyboardPrefetchProjectRef.current = analysisProjectId;
    let alive = true;
    // 사진 업로드·상품 저장이 아직 돌고 있으면 기다린다. 콘티 시드는 상품 색상을 읽으므로
    // (shapes.defaultStoryboard) 저장 전에 데우면 **빈 상품으로 시드된 보드**가 캐시되고,
    // 콘티보드는 그 캐시를 그대로 즉시 표시한다 — 잘못된 보드가 굳는다(2026-08-18).
    void prefetchStoryboardAfterProductPhotos(analysisProjectId, {
      isActive: () => alive,
    });
    return () => { alive = false; };
  }, [analysisProjectId, phase, mobile]);

  useEffect(() => {
    if (mobile) return;
    if (!analysisProjectId || phase !== 'done') return;
    if (mannequinWorkCheckProjectRef.current === analysisProjectId) return;
    mannequinWorkCheckProjectRef.current = analysisProjectId;
    let alive = true;
    api.getMannequins(analysisProjectId).then((cuts) => {
      if (alive) hasExistingGenerationWorkRef.current = (cuts || []).length > 0;
    }).catch(() => { /* 조회 실패는 경고를 생략한다 — 방해가 안전보다 나쁘다 */ });
    return () => { alive = false; };
  }, [analysisProjectId, phase, mobile]);
  // force: 경고 모달에서 '계속 진행'을 누른 경로. setState 는 비동기라 ack 상태를 기다릴 수
  // 없어 인자로 넘긴다. onNext 콜백이 이벤트 객체를 넘겨도 force 는 undefined 라 안전하다.
  const goToStoryboard = async (opts) => {
    if (mobile) return;
    if (opts?.expectedQuote) mannequinQuoteConfirmation.remember('draft', 'generate', opts.expectedQuote);
    const force = opts?.force === true;   // null·이벤트 객체로 불려도 안전하게
    if (redirectingRef.current) return; // 더블클릭/재진입 가드 (blob 추출 await 중)
    if (!guardMannequinCredits()) return;
    // 다른 옷이 섞였을 수 있다는 경고 — 생성에 들어가기 직전 한 번만. 확인하면 그대로 진행한다
    // (차단이 아니다. 판정이 틀렸을 때 셀러가 갇히면 경고가 없느니만 못하다).
    if (inputConsistency && !consistencyAck && !force) {
      setConsistencyOpen(true);
      return;
    }
    redirectingRef.current = true;
    const runId = ++promotionRunRef.current;
    const projectGeneration = useAppStore.getState().projectGeneration;
    const isCurrentRun = () => mountedRef.current
      && promotionRunRef.current === runId
      && useAppStore.getState().projectGeneration === projectGeneration;
    setFlowPromotionLocked(true);
    let promotedProjectId = null;
    let latestSnapshot = null;
    try {
      colorSaveSchedulerRef.current.flush();
      // 직전 입력 이벤트의 PATCH가 getAnalysis보다 늦게 도착하는 레이스를 막는다. 모든 분석 저장을
      // 입력 순서대로 직렬화하고, 확정은 현재 큐까지만 기다린 뒤 이동/재생성을 시작한다.
      await analysisSaveChainRef.current;
      if (failedAnalysisPatchRef.current) {
        const retryPatch = failedAnalysisPatchRef.current;
        const { analysis: savedAnalysis } = await persistAnalysisEdit(api, analysisProjectId, retryPatch);
        failedAnalysisPatchRef.current = null;
        analysisSaveErrorRef.current = null;
        if ((isMatchRecommendationPatch(retryPatch) || 'matchClothing' in retryPatch || 'outfitSelections' in retryPatch) && savedAnalysis) {
          const merged = mergeOutfitSaveResponse(latestAnalysisRef.current, savedAnalysis);
          latestAnalysisRef.current = merged;
          setAnalysis(merged);
        }
      }
      if (analysisSaveErrorRef.current) throw analysisSaveErrorRef.current;
      latestSnapshot = {
        product: latestProductRef.current,
        analysis: latestAnalysisRef.current,
        composeMode: latestComposeModeRef.current,
        localUpdatedAt: latestLocalUpdatedAtRef.current || new Date().toISOString(),
      };
      await confirmDraftPhotos({
        product: latestSnapshot.product,
        omitIds: opts?.omitPhotoIds || [],
        onProductChange: (nextProduct) => {
          latestProductRef.current = nextProduct;
          setProduct(nextProduct);
          latestSnapshot.product = nextProduct;
          latestSnapshot.localUpdatedAt = new Date().toISOString();
          latestLocalUpdatedAtRef.current = latestSnapshot.localUpdatedAt;
        },
        save: async (nextProduct) => {
          queueProductDraftSave(
            nextProduct,
            latestSnapshot.analysis,
            latestSnapshot.composeMode,
            latestSnapshot.localUpdatedAt,
          );
          return flushProductDraftSave();
        },
        onFailure: (failure) => {
          if (isCurrentRun()) setPhotoFailure(failure);
        },
        confirm: async () => {
          if (!isCurrentRun()) return;
          if (session || isMockMode) {
            const draft = await loadDraft();
            if (!draft?.product) throw new Error('저장된 입력 내용을 다시 불러오지 못했어요. 다시 시도해 주세요.');
            await draftSlot.settleVisualPreparations();
            if (!isCurrentRun()) return;
            await draftSlot.flush();
            if (!isCurrentRun()) return;
            draft.visuals = draftSlot.getVisualHandoff(draft.product, draft.customMatch?.localId);
            // 프로젝트를 만들기 전에 서버 잠금 안에서 active token을 소비한다. 여기서 409면
            // 작업권을 잃은 기기는 승격 자체를 시작하지 않는다.
            await draftSlot.remove({ forPromotion: true });
            // CTA 는 **프로젝트 신원까지만** 기다린다. 사진 업로드·상품 저장은 그 뒤로 계속 돌고,
            // 콘티보드가 그 프라미스를 구독해 진행률을 보여주고 마네킹 생성 전에 정착을 기다린다.
            // 이 대기를 CTA 에 두면 사진 용량에 정비례해 화면이 멈춘다(실측 44~211초, 2026-08-17).
            const promotedDraftRevision = draft.updatedAt;
            // 버려진 이전 시도(전환 전 실패·이탈)의 임시 키 task 가 pending 으로 남아 있으면
            // startProductPhotoPromotion 이 그걸 돌려줘 이번 run 이 아예 실행되지 않는다.
            // 동시 실행은 redirectingRef 가 이미 막으므로, 여기 남아 있는 것은 항상 잔재다.
            clearProductPhotoPromotionTask(NEW_PROJECT_KEY);
            let promotion;
            const projectId = await new Promise((resolve, reject) => {
              let settled = false;
              const task = startProductPhotoPromotion(
                NEW_PROJECT_KEY,
                ({ onPhotoProgress }) => promoteDraftToProject(draft, {
                  onProjectReady: (id) => {
                    if (settled) return;
                    settled = true;
                    // 진행률 task 를 실제 projectId 로 옮겨 붙인다, 콘티보드는 그 키로 구독한다.
                    adoptProductPhotoPromotion(NEW_PROJECT_KEY, id);
                    resolve(id);
                  },
                  onPhotoProgress,
                }),
                { draftSlotSnapshot: latestSnapshot },
              );
              promotion = task;
              task.promise.then((value) => {
                if (!settled) { settled = true; resolve(value?.projectId); }
              }, (error) => {
                if (!settled) { settled = true; reject(error); }
              });
            });
            promotedProjectId = projectId;
            if (!isCurrentRun()) return;
            mannequinQuoteConfirmation.remember(projectId, 'generate', mannequinQuoteConfirmation.seen(projectId, 'generate')
              || mannequinQuoteConfirmation.seen('draft', 'generate') || opts?.expectedQuote
              || expectedMannequinQuote(creditQuote?.mannequinGenerate?.total
                ?? mannequinGenerationTotal(useAppStore.getState().account?.plan, latestSnapshot.analysis?.selectedModelId)));
            // 여기부터는 CTA 비대기 구간이다. 내 옷 승격도 이미 시작됐고 콘티보드가 그 프라미스를
            // 구독해 준비 상태·완료 반영·실패 안내를 맡는다.
            // 게스트로 편집(재생성 신호 dirty)한 뒤 세션이 생겨 여기서 처음 project 를 얻는 경로,
            // '다른 작업으로 전환'이 아니라 같은 작업이 신원을 얻는 것뿐이라 신호를 지우면 안 된다.
            useAppStore.getState().adoptProject(projectId, { preserveGenerationDirty: true });
            setAnalysisProjectId(projectId);
            useAppStore.getState().confirmProductInfo(projectId);
            navigate('/create/storyboard', {
              state: storyboardTransitionState(draft),
            });
            // 로컬 draft 는 **업로드가 끝난 뒤에** 지운다, 업로드 중 탭이 닫히면 그 draft 가 유일한
            // 사진 원본이라, 먼저 지우면 재시도할 재료가 없어진다.
            void promotion.promise
              .then(async () => {
                // 두 정리는 관심사가 다르다. draft 삭제는 "그 사이 새 입력이 없을 때만"이지만,
                // single-flight 신원 정리는 이 승격이 끝나면 언제나 해야 한다, 조건을 묶어 두면
                // 새 입력이 생긴 경우에 앞 projectId 가 남아 다음 상품이 거기에 덮어써진다.
                await clearDraftIfCurrent(promotedDraftRevision);
                resetDraftSyncSingleFlight();
              })
              .catch(() => {});
            return;
          }
          openLogin('/create/storyboard');
        },
      });
    } catch (error) {
      if (promotedProjectId) {
        retryDraftPromotion(promotedProjectId);
      }
      draftSlot.resume();
      if (latestSnapshot && isCurrentRun()) draftSlot.queue(latestSnapshot);
      toast.push(error?.message || '입력 내용을 서버에 저장하지 못했어요. 잠시 후 다시 시도해 주세요.', { icon: 'alertCircle', variant: 'error', error: error, kind: 'toast_error', actionName: 'save', messageCode: 'action_failed', blocking: true });
    } finally {
      redirectingRef.current = false;
      if (isCurrentRun()) setFlowPromotionLocked(false);
    }
  };

  const queueAnalysisPatch = (patch) => {
    // 후보 목록은 서버 소유 — 추천 갱신 패치뿐 아니라 선택 토글 응답도
    // 서버 머지 결과로 동기화해 묵은 후보가 로컬에 남지 않게 한다.
    const syncMatch = isMatchRecommendationPatch(patch) || 'matchClothing' in patch || 'outfitSelections' in patch;
    const requestContextKey = outfitContextKey(latestAnalysisRef.current, latestProductRef.current);
    latestAnalysisPatchRef.current = { ...latestAnalysisPatchRef.current, ...patch };
    if (failedAnalysisPatchRef.current) {
      failedAnalysisPatchRef.current = mergeLatestFailedAnalysisPatch(
        failedAnalysisPatchRef.current,
        patch,
        latestAnalysisPatchRef.current,
      );
    }
    analysisSaveChainRef.current = analysisSaveChainRef.current
      .then(() => persistAnalysisEdit(api, analysisProjectId, patch))
      .then(({ analysis: savedAnalysis, product: savedProduct }) => {
        if (!failedAnalysisPatchRef.current) analysisSaveErrorRef.current = null;
        if ('colors' in patch) persistedColorsRef.current = savedProduct?.colors || patch.colors;
        if (!savedAnalysis) return;
        if (syncMatch) {
          const current = latestAnalysisRef.current;
          const merged = mergeOutfitSaveResponse(current, savedAnalysis, {
            requestContextKey, product: latestProductRef.current,
          });
          if (isGenerationRelevantAnalysisPatch({ outfitSelections: merged?.outfitSelections }, current)) {
            useAppStore.getState().markGenerationRelevantEdits();
          }
          latestAnalysisRef.current = merged;
          setAnalysis(merged);
        }
      })
      .catch((error) => {
        failedAnalysisPatchRef.current = mergeLatestFailedAnalysisPatch(
          failedAnalysisPatchRef.current,
          patch,
          latestAnalysisPatchRef.current,
        );
        analysisSaveErrorRef.current = error;
        toast.push(error?.message || '분석 수정 내용을 저장하지 못했어요.', { icon: 'alertTri', variant: 'error', error: error, kind: 'toast_error', actionName: 'save', messageCode: 'action_failed', blocking: true });
      });
    registerAnalysisEditSave(analysisProjectId, analysisSaveChainRef.current);
  };
  analysisPatchQueueRef.current = queueAnalysisPatch;

  // AG-01은 제안만 한다. 실제 Product 반영은 셀러가 비워 둔 필드만
  // 채우고, 일반 색상 편집과 같은 trailing scheduler를 통해 저장한다.
  const withAutofilledColors = (sourceProduct, analyzed) => {
    const nextColors = autofillColorGroups(
      sourceProduct?.colors || [],
      analyzed?.swatchSuggestions || [],
      catalogs?.swatchColors || [],
    );
    if (nextColors === sourceProduct?.colors) return sourceProduct;
    const persistedColors = mergeColorMetadataWithPersistedImages(
      persistedColorsRef.current,
      nextColors,
    );
    colorSaveSchedulerRef.current.schedule({ colors: persistedColors });
    return { ...sourceProduct, colors: nextColors };
  };

  // 분석 폼의 편집 하나를 실제로 화면·서버에 반영한다. 생성 관련 필드(성별·의류 종류 등)를
  // 바꿀 때 기존 작업이 있으면 이 함수를 곧장 부르지 않고 경고 모달의 확정을 거친다 — 취소하면
  // 아예 호출되지 않으므로 화면·서버 어디에도 흔적이 남지 않는다.
  const applyAnalysisPatch = (patch) => {
    if (isGenerationRelevantAnalysisPatch(patch, latestAnalysisRef.current)) {
      useAppStore.getState().markGenerationRelevantEdits();
    }
    const { productPatch } = splitAnalysisEditPatch(patch);
    if (hasPatchFields(productPatch)) {
      latestProductRef.current = { ...latestProductRef.current, ...productPatch };
      setProduct(latestProductRef.current);
    }
    latestAnalysisRef.current = syncOutfitLegacySelection({ ...latestAnalysisRef.current, ...patch });
    setAnalysis(latestAnalysisRef.current);
    queueAnalysisPatch(patch);
  };

  // 'cuts' = 컷이 이미 있음(다시 만들어야 함) · 'running' = 컷은 없지만 이 프로젝트의 생성이
  // 지금 돌고 있음(끝나면 방금 바꾼 값이 아니라 옛 선택으로 완성됨) · 'none' = 잃을 게 없음.
  const generationWorkKind = generationWorkWarningKind({
    cutsExist: hasExistingGenerationWorkRef.current,
    jobStatus: mannequinJobStatus,
    jobProjectId: mannequinJobProjectId,
    projectId: analysisProjectId,
  });
  useEffect(() => {
    if (mobile) return;
    if (!analysisProjectId || phase !== 'done') return undefined;
    let alive = true;
    api.getCreditQuote(analysisProjectId, { selectedModelId: analysis?.selectedModelId })
      .then((quote) => { if (alive) setCreditQuote(quote); })
      .catch(() => { /* 즉시 계산값과 서버 생성 요청의 402 판정을 유지한다. */ });
    return () => { alive = false; };
  }, [analysis?.selectedModelId, analysisProjectId, phase, mobile]);

  // 생성 관련 필드 편집 요청 — 기존 작업(마네킹 컷이 있거나, 지금 생성이 도는 중)이 있으면
  // 바로 적용하지 않고 대가를 먼저 보여준다. 없으면(새 프로젝트의 첫 분석 검토) 잃을 게
  // 없으니 그대로 적용해 방해하지 않는다.
  const onAnalysisFormChange = (patch) => {
    if (isGenerationRelevantAnalysisPatch(patch, latestAnalysisRef.current) && generationWorkKind !== 'none') {
      pendingRelevantWorkKindRef.current = generationWorkKind;
      setPendingRelevantPatch(patch);
      return;
    }
    applyAnalysisPatch(patch);
  };

  const confirmRunningRelevantPatch = async () => {
    if (cancellingRelevantPatchRef.current || !pendingRelevantPatch) return;
    if (!guardMannequinCredits()) return;
    cancellingRelevantPatchRef.current = true;
    setCancellingRelevantPatch(true);
    const patch = pendingRelevantPatch;
    try {
      const { credits } = await api.cancelMannequinGeneration(analysisProjectId);
      useAppStore.getState().syncCredits(credits);
      acknowledgeMannequinGenerationCancellation(analysisProjectId);
      setPendingRelevantPatch(null);
      applyAnalysisPatch(patch);
    } catch (error) {
      toast.push(error?.message || '마네킹컷 생성을 취소하지 못했어요. 잠시 후 다시 시도해 주세요.', { icon: 'alertCircle' });
    } finally {
      cancellingRelevantPatchRef.current = false;
      setCancellingRelevantPatch(false);
    }
  };

  const resumeFeatureAnalysis = async (payload) => {
    if (!isAnalysisFastEntryEnabled() || mobile || !payload.analysis?.featureRun) return;
    const restoredGeneration = useAppStore.getState().projectGeneration;
    const restoredRun = await restoreDraftFeatureRun(payload, {
      readProduct: () => latestProductRef.current, readAnalysis: () => latestAnalysisRef.current,
      swatchColors: catalogs?.swatchColors,
      isCurrent: () => useAppStore.getState().projectGeneration === restoredGeneration,
      onProduct: (next) => {
        latestProductRef.current = next; persistedColorsRef.current = next.colors || [];
        if (mountedRef.current) setProduct(next);
      },
      onAnalysis: (next) => {
        latestAnalysisRef.current = next; featureSavedAnalysisRef.current = next;
        if (mountedRef.current) setAnalysis(next);
      },
    });
    if (!mountedRef.current || useAppStore.getState().projectGeneration !== restoredGeneration) return;
    if (restoredRun?.getRecord().coreReceived) {
      const restoredAnalysis = restoredRun.getAnalysis?.() || latestAnalysisRef.current;
      setInputConsistency(restoredAnalysis?.inputConsistency || null);
      setConsistencyAck(false);
      setPhase('done');
    }
    else if (!restoredRun) setPhase('input');
  };

  const applyDraftPayload = (payload) => {
    if (!payload?.product) return false;
    if (mobile && autoSaveIntent) latestLocalUpdatedAtRef.current = payload.updatedAt || autoSaveIntent.draftUpdatedAt;
    const restored = restoreDraftProduct(payload);
    draftSlot.adoptVisualHandoff?.(payload, restored);
    persistedColorsRef.current = restored.colors || [];
    latestProductRef.current = restored;
    latestAnalysisRef.current = (mobile ? payload.analysis : normalizeAnalysisFit(payload.analysis)) || null;
    void api.restoreInputDraft(restored, latestAnalysisRef.current, payload.customMatch);
    setProduct(restored);
    setAnalysis((mobile ? payload.analysis : normalizeAnalysisFit(payload.analysis)) || null);
    useAppStore.getState().restoreComposeMode(payload.composeMode);
    setAnalysisProjectId(null);
    const canRestoreAnalysis = payload.analysis && (mobile || hasRequiredDraftPhotos(restored));
    const awaitingCore = payload.analysis?.featureRun?.coreReceived === false;
    setPhase(canRestoreAnalysis ? (awaitingCore ? (isAnalysisFastEntryEnabled() ? 'analyzing' : 'input') : 'done') : 'input');
    if (canRestoreAnalysis && isAnalysisFastEntryEnabled()) {
      void resumeFeatureAnalysis(payload).catch((error) => {
        if (mountedRef.current) { setPhase('input'); toast.push('분석을 이어서 불러오지 못했어요. 다시 시도해 주세요.', { icon: 'alertCircle', error }); }
      });
    }
    return true;
  };

  const chooseRemoteContent = async () => {
    const userId = entryUserRef.current;
    const epoch = draftSlot.getIdentityEpoch();
    const isCurrent = () => userId === entryUserRef.current && epoch === draftSlot.getIdentityEpoch();
    try {
      const slot = await draftSlot.get();
      if (!isCurrent()) return;
      if (!slot) throw new Error('저장돼 있던 내용이 다른 곳에서 이미 정리됐어요.');
      if (mobile && !isMobileDraftWriter(slot.meta)) { setSlotLock(slot.meta); return; }
      const takeover = await draftSlot.takeover({ isCurrent });
      if (!isCurrent()) return;
      if (!takeover?.payload) throw new Error('다른 기기에서 저장한 내용을 불러오지 못했어요.');
      applyDraftPayload(takeover.payload);
      setReclaimChoiceOpen(false);
      setSlotLock(null);
    } catch (error) {
      if (!isCurrent()) return;
      toast.push(error?.message || '내용을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.', { icon: 'alertCircle', variant: 'error', error: error, kind: 'ui_error', actionName: 'restore', messageCode: 'action_failed', blocking: true });
    }
  };

  const chooseLocalContent = async () => {
    const userId = entryUserRef.current;
    const epoch = draftSlot.getIdentityEpoch();
    const isCurrent = () => userId === entryUserRef.current && epoch === draftSlot.getIdentityEpoch();
    try {
      const slot = await draftSlot.get();
      if (!isCurrent()) return;
      if (mobile && slot && !isMobileDraftWriter(slot.meta)) { setSlotLock(slot.meta); return; }
      await draftSlot.takeover({ isCurrent });
      if (!isCurrent()) return;
      setReclaimChoiceOpen(false);
      setSlotLock(null);
      if (product) {
        const localUpdatedAt = latestLocalUpdatedAtRef.current || new Date().toISOString();
        draftSlot.queue({ product, analysis, composeMode, localUpdatedAt });
        await draftSlot.flush();
        if (!isCurrent()) return;
      }
    } catch (error) {
      if (!isCurrent()) return;
      toast.push(error?.message || '이 화면의 내용으로 이어가지 못했어요. 잠시 후 다시 시도해 주세요.', { icon: 'alertCircle', variant: 'error', error: error, kind: 'toast_error', actionName: 'save', messageCode: 'action_failed', blocking: true });
    }
  };

  const reclaimEditingRights = () => {
    if (draftSlot.hasUnsyncedChanges(latestLocalUpdatedAtRef.current)) {
      setReclaimChoiceOpen(true);
      return;
    }
    void chooseRemoteContent();
  };

  const restartGoneWithLocal = async () => {
    try {
      const epoch = draftSlot.getIdentityEpoch();
      const snapshot = product ? await draftSlot.recoverGonePhotos({ product, analysis, composeMode,
        localUpdatedAt: latestLocalUpdatedAtRef.current || new Date().toISOString() }) : null;
      if (epoch !== draftSlot.getIdentityEpoch()) return;
      if (!draftSlot.restartAfterGone()) return;
      setSlotLock(null);
      if (snapshot) {
        setProduct(snapshot.product);
        if (snapshot.missingPhotoCount) toast.push(`사진 ${snapshot.missingPhotoCount}장은 다시 골라 주세요.`, { icon: 'alertTri' });
        draftSlot.queue(snapshot);
        await draftSlot.flush();
      }
    } catch (error) {
      toast.push(error?.message || '내용을 다시 저장하지 못했어요. 잠시 후 다시 시도해 주세요.', { icon: 'alertCircle', variant: 'error', error: error, kind: 'toast_error', actionName: 'save', messageCode: 'action_failed', blocking: true });
    }
  };

  const startOver = async () => {
    setConsistencyOpen(false);
    await entryActions.startNew({ enabled: slotEnabled });
  };

  // 기준 색상 앞면과 뒷면, 추가 색상 정면을 필수로 받는다(ADR-0017).
  const baseColor = product?.colors.find((c) => c.isBase) || product?.colors[0];
  const hasFront = !!baseColor?.images.some((im) => im.slot === 'Front');
  const hasBack = !!baseColor?.images.some((im) => im.slot === 'Back');
  const additionalColorsMissingFront = (product?.colors || []).filter((color) => color !== baseColor
    && !color.isBase && !color.images.some((image) => image.slot === 'Front'));
  const canDone = hasFront && hasBack && !additionalColorsMissingFront.length
    && phase === 'input' && !authLoading && !slotLock;
  const canSaveForPC = canDone && !hasPendingConversions && !mobileReadOnly;
  const saveForPC = async ({ automatic = false } = {}) => {
    if (!canSaveForPC || loginSaveRef.current) return;
    const snapshot = { product, analysis, composeMode,
      localUpdatedAt: latestLocalUpdatedAtRef.current || new Date().toISOString() };
    if (slotEnabled) {
      const save = () => {
        if (autoSaveIntent) {
          const claimed = claimMobileGuestSave(autoSaveIntent);
          if (automatic && !claimed) return;
        }
        return handoff.save(snapshot, { autoRetry: !automatic });
      };
      if (sellerConsentReady !== false) return save();
      loginSaveRef.current = true;
      setPreparingLogin(true);
      const userId = entryUserRef.current;
      const epoch = draftSlot.getIdentityEpoch();
      const current = () => mountedRef.current && userId === entryUserRef.current && epoch === draftSlot.getIdentityEpoch();
      try {
        if (!await retrySellerConsent(userId) || !current()) return;
        return save();
      } catch {
        if (current()) toast.push('서버 연결이 불안정해요. 잠시 후 다시 시도해 주세요.', { icon: 'alertCircle' });
      } finally {
        loginSaveRef.current = false;
        if (mountedRef.current) setPreparingLogin(false);
      }
      return;
    }
    loginSaveRef.current = true;
    setPreparingLogin(true);
    const epoch = draftSlot.getIdentityEpoch();
    const saveTappedAt = Date.now();
    const excludedUserId = readStoredAuthSession()?.user?.id || null;
    let localSaved = false;
    try {
      try {
        queueProductDraftSave(product, analysis, composeMode, snapshot.localUpdatedAt);
        const stored = await flushProductDraftSave({ timeoutMs: 8000 });
        localSaved = !stored?.failed;
      } catch { /* 로컬 보관 실패가 로그인을 막지 않게 한다. */ }
      if (!mountedRef.current || epoch !== draftSlot.getIdentityEpoch()) return;
      if (!localSaved) {
        // Blob을 IndexedDB에 못 쓰는 브라우저도 이메일 로그인 동안에는 사진을 유지한다.
        loginDraftRef.current = collectDraftPhotos(product).then(({ photos, cleanProduct }) => ({
          ownerUserId: null, product: cleanProduct, photos, analysis, composeMode, updatedAt: snapshot.localUpdatedAt,
        }));
      }
      rememberMobileGuestSave(snapshot.localUpdatedAt, excludedUserId, saveTappedAt, localSaved ? null : loginDraftRef.current);
      if (!localSaved) toast.push('이 브라우저는 사진을 잠시 보관하지 못해요. 구글이나 카카오로 로그인하면 다시 골라 주세요.', { icon: 'alertCircle' });
      openLogin('/create/input');
    } finally {
      loginSaveRef.current = false;
      if (mountedRef.current) setPreparingLogin(false);
    }
  };

  useEffect(() => {
    if (!mobile || !session || preparingLogin || sellerConsentReady === false || !autoSaveIntent || !canSaveForPC
        || reclaimChoiceOpen || handoff.result || autoSaveIntent.saveStarted) return;
    void saveForPC({ automatic: true });
  }, [mobile, session, preparingLogin, sellerConsentReady, autoSaveIntent, canSaveForPC, reclaimChoiceOpen, handoff.result]);

  useEffect(() => {
    if (mobile && autoSaveIntent && handoff.result?.status === 'saved') clearMobileGuestSave(autoSaveIntent.id);
  }, [mobile, autoSaveIntent, handoff.result?.status]);

  const editingRightsLock = slotLock && (
    <EditingRightsLock
      meta={slotLock}
      onReclaim={reclaimEditingRights}
      onRestartLocal={restartGoneWithLocal}
      onDiscard={startOver}
    />
  );

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoadError('');
      // cold input 은 라우트 계층이 stale flow 를 먼저 비운다. 같은 탭에서 돌아온 input 만
      // 현재 project 를 읽고, project 가 없으면 null 계약의 클라이언트 시드 템플릿을 쓴다.
      const { projectId: currentProjectId, projectPersisted } = useAppStore.getState();
      const editingProjectId = !mobile && projectPersisted && currentProjectId ? currentProjectId : null;
      const [p, c, existingAnalysis] = await Promise.all([
        api.getProduct(editingProjectId),
        api.getCatalogs(),
        editingProjectId ? api.getAnalysis(editingProjectId) : Promise.resolve(null),
      ]);
      if (!alive) return;
      setCatalogs(c);
      const staged = draftSlot.consumeStaged();
      if (staged?.payload && applyDraftPayload(staged.payload)) return;
      // 이 ref 는 "서버에 저장된 상품 색상"이다 — 색상명·스와치만 저장할 때 그 저장본의
      // images 를 그대로 유지하는 데 쓴다. 게스트(서버 프로젝트 없음)에겐 그런 저장본이
      // 아예 없으므로 비워 둔다. 예전엔 getProduct(null) 이 돌려준 **목 데모 시드**의 색상이
      // 여기 들어와, 데모의 SVG 플레이스홀더 사진이 저장 패치·임시저장을 타고 셀러의 상품
      // 사진 자리까지 흘러갔다(2026-08-17 사고).
      persistedColorsRef.current = editingProjectId ? (p.colors || []) : [];
      const analysisWasRunning = editingProjectId && isAnalysisRunning(editingProjectId);

      // 같은 탭에서 마네킹/후속 단계로 갔다가 input 으로 돌아온 경우에는 현재 프로젝트를
      // 편집한다. cold input 은 라우트 계층이 먼저 beginProject 해서 여기까지 stale id가 오지 않는다.
      // getAnalysis 는 미저장이어도 {projectId} 봉투를 돌려주므로 truthy — payload 실존 여부로
      // 판정해야 분석이 실패한 프로젝트가 빈 '고스트' 분석 폼으로 뜨는 걸 막는다(F3 진입로).
      if (editingProjectId && existingAnalysis && Object.keys(existingAnalysis).length > 1
          && (!analysisWasRunning || !isMockMode)) {
        setAnalysisRunning(editingProjectId, false);
        const nextProduct = mobile ? p : withAutofilledColors(p, existingAnalysis);
        persistedColorsRef.current = nextProduct.colors || [];
        setProduct(nextProduct);
        setAnalysis(mobile ? existingAnalysis : mergeProductOwnedAnalysisFields(existingAnalysis, nextProduct));
        // 저장분에서 경고를 복원한다 — 이게 없으면 새로고침·재진입한 탭에서만 게이트가
        // 사라져 그대로 통과한다(분석 직후 탭에서는 멀쩡히 뜨므로 재현이 헷갈린다).
        setInputConsistency(existingAnalysis.inputConsistency || null);
        setAnalysisProjectId(editingProjectId);
        setPhase('done');
        return;
      }

      // 분석 요청 직후 새로고침된 탭은 서버 상품(영속 asset URL 포함)을 먼저 보여주고,
      // 같은 analyze 호출로 활성 job 에 합류한다. 완료된 분석이 방금 저장됐다면 위 분기가 맡는다.
      if (analysisWasRunning && !mobile) {
        // mock 은 메모리 DB도 새로 로드되므로 서버 역할의 seed만으로는 방금 올린 blob을
        // 복구할 수 없다. 같은 탭 IndexedDB draft를 사진 소스로 사용하되 getProduct 호출은 유지한다.
        const mockDraft = isMockMode && hasPendingDraft()
          ? await loadDraft().catch(() => null)
          : null;
        const recoveredProduct = restoreDraftProduct(mockDraft) || p;
        setProduct(recoveredProduct);
        setAnalysisProjectId(editingProjectId);
        setPhase('analyzing');
        try {
          const a = await api.analyzeProduct(editingProjectId, {});
          if (!alive) return;
          const analyzedProductPatch = analysisResponseProductPatch(a);
          // 분석 대기 중에 입력된 셀러 값도 시작 스냅샷으로 되돌리지 않게
          // 가장 최신 Product를 기준으로 빈칸만 채운다.
          const currentProduct = latestProductRef.current || recoveredProduct;
          const finalName = (currentProduct.name && currentProduct.name.trim()) || a.suggestedName || '새 상품';
          const nextProduct = withAutofilledColors(
            { ...currentProduct, name: finalName, ...analyzedProductPatch },
            a,
          );
          if (hasPatchFields(analyzedProductPatch)) {
            await api.saveProduct(editingProjectId, analyzedProductPatch);
          }
          if (!recoveredProduct.name?.trim()) await api.saveProduct(editingProjectId, { name: finalName });
          if (!alive) return;
          persistedColorsRef.current = nextProduct.colors || [];
          setProduct(nextProduct);
          setAnalysis(mergeProductOwnedAnalysisFields(a, nextProduct));
          setInputConsistency(a.inputConsistency || null);
          setConsistencyAck(false);
          setAnalysisRunning(editingProjectId, false);
          setAnalysisReady(true);
        } catch (error) {
          if (!alive) return;
          setAnalysisRunning(editingProjectId, false);
          setPhase('input');
          toast.push(error?.message || '진행 중인 분석에 다시 연결하지 못했어요. 다시 시도해 주세요.', { icon: 'alertCircle', variant: 'error', error: error, kind: 'analysis_error', actionName: 'analyze', messageCode: 'analysis_failed', blocking: true });
        }
        return;
      }

      // 로그인 실패/취소/브라우저 뒤로가기(카카오→←뒤로→구글)·새로고침으로 입력 화면에 돌아오면
      // 페이지가 새로고침돼 입력이 사라진다 → 리다이렉트 직전 저장해 둔 draft(입력+분석)를 복원한다
      // (사진 blob→objectURL 재생성, imageId 매칭). 단 '이 탭 세션'에 저장한 경우만
      // (hasPendingDraft=sessionStorage) — 같은 탭은 복원되고, 공용 브라우저의 다른 사용자
      // (다른 탭/세션)에겐 복원되지 않아 입력이 누출되지 않는다. draft 는 새 제작·로그아웃 때 정리.
      const draft = hasPendingDraft() ? await loadDraft().catch(() => null) : null;
      if (!alive) return;
      if (draft?.product) {
        if (mobile && autoSaveIntent) latestLocalUpdatedAtRef.current = draft.updatedAt || autoSaveIntent.draftUpdatedAt;
        const restored = restoreDraftProduct(draft);
        draftSlot.adoptVisualHandoff?.(draft, restored);
        persistedColorsRef.current = restored.colors || [];
        setProduct(restored);
        useAppStore.getState().restoreComposeMode(draft.composeMode);
        // 원격 주소는 로컬 바이트가 없어도 복원한다. 확정할 때 다시 수집하고 실패하면 막는다.
        // 로컬 사진은 저장된 blob이 있어야 필수 사진으로 인정한다.
        const draftColors = draft.product?.colors || [];
        const draftBase = draftColors.find((c) => c.isBase) || draftColors[0];
        const restoredHasRequired = !!draftBase
          && ['Front', 'Back'].every((slot) =>
            (draft.photos || []).some((p) => p.colorId === draftBase.id && p.slot === slot)
            || (draftBase.images || []).some((image) => image.slot === slot && isRemoteAssetPhoto(image.src)));
        if (draft.analysis && (mobile || restoredHasRequired)) {
          latestProductRef.current = restored;
          latestAnalysisRef.current = draft.analysis;
          void api.restoreInputDraft(restored, draft.analysis, draft.customMatch);
          setAnalysis(draft.analysis);
          const awaitingCore = draft.analysis.featureRun?.coreReceived === false;
          setPhase(awaitingCore ? (isAnalysisFastEntryEnabled() ? 'analyzing' : 'input') : 'done');
          await resumeFeatureAnalysis(draft);
        }
        return;
      }

      // 색상 이름도 비운다 — 시드 템플릿의 '블랙'이 새 제작으로 새면 스와치를 다른 색으로
      // 골라도 이름만 '블랙'으로 남아 원과 이름이 어긋난다(오너 8/15 지적의 오염원).
      const fresh = {
        ...p,
        name: '',
        colors: [{ ...p.colors[0], name: '', swatchId: undefined, images: [] }],
      };
      setProduct(fresh);
    })().catch((error) => {
      if (alive) { setLoadOriginalError(error); setLoadError(error?.message || '입력 화면을 불러오지 못했어요. 다시 시도해 주세요.'); }
    });
    return () => { alive = false; };
  }, [loadAttempt]);

  const nextMobile = () => entryActions.nextMobile({ enabled: slotEnabled });
  if (mobile && (handoff.gone || slotLock?.state === 'gone'
      || (slotLock && !isMobileDraftWriter(slotLock)))) return (
    <>
      <MobileDraftStatus meta={slotLock} gone={handoff.gone || slotLock?.state === 'gone'}
        busy={entryBusy} onNext={nextMobile} onSlotChange={(slot) => {
          if (!slot || slot.meta?.hasContent === false) setSlotLock({ state: 'gone' });
          else if (isMobileDraftWriter(slot.meta)) entryActions.showMobile(slot.meta, { entry: true });
          else setSlotLock(slot.meta);
        }} />
      <MobileSaveModal result={handoff.result} onClose={handoff.close} />
      <DraftEntryDialog dialog={entryDialog} actions={entryActions} busy={entryBusy} />
    </>
  );

  if (loadError) return (
    <div className="wizard">
      {editingRightsLock}
      {!mobile && doneBlocked && <DoneGuardModal />}
      <div className="surface">
        <ErrorState error={loadOriginalError} desc={loadError} onRetry={() => setLoadAttempt((n) => n + 1)} />
      </div>
    </div>
  );
  if (!product || !catalogs) return (
    <ProductInputPlaceholder mobile={mobile}>
      {editingRightsLock}
      {!mobile && doneBlocked && <DoneGuardModal />}
    </ProductInputPlaceholder>
  );

  const mobileEditingLocked = mobile && (mobileReadOnly || handoff.saving || preparingLogin);
  const set = (patch) => { if (!mobileEditingLocked) setProduct((p) => ({ ...p, ...patch })); };
  // add real uploaded files (drag-drop / picker) with name/size/type meta (PRD §5.5)
  const addImageFiles = (colorId, slot, metas) => {
    if (mobileEditingLocked) return;
    invalidateStoryboardForProductPhotoEdit(analysisProjectId, invalidateStoryboardEntryPrefetch);
    setProduct((p) => ({ ...p, colors: p.colors.map((c) => c.id === colorId ? { ...c, images: [...c.images, ...metas.map((m) => ({ id: uid('img'), slot, label: slot, ...m }))] } : c) }));
  };
  const removeImage = (colorId, imgId) => {
    if (mobileEditingLocked) return;
    photoPreviewRegistryRef.current.release(imgId);
    invalidateStoryboardForProductPhotoEdit(analysisProjectId, invalidateStoryboardEntryPrefetch);
    setProduct((p) => ({ ...p, colors: p.colors.map((c) => c.id === colorId ? { ...c, images: c.images.filter((im) => im.id !== imgId) } : c) }));
  };
  const editColors = (change) => {
    if (mobileEditingLocked) return;
    const colors = change(product.colors);
    if (colors === product.colors) return;
    setProduct((p) => ({ ...p, colors }));
    if (phase === 'done') {
      setAnalysis((a) => ({ ...a, colors }));
      const persistedColors = mergeColorMetadataWithPersistedImages(
        persistedColorsRef.current,
        colors,
      );
      // 저장 타이머보다 먼저 캐시를 닫아, 브라우저 뒤로가기가 묵은 콘티 시드를 즉시 읽지 않게 한다.
      if (analysisProjectId) invalidateStoryboardEntryPrefetch(analysisProjectId);
      // 스와치 연타는 화면에 즉시 보이되, 마지막 colors 패치 하나만 기존 저장 큐로 보낸다.
      colorSaveSchedulerRef.current.schedule({ colors: persistedColors });
    }
  };
  const renameColor = (colorId, name) => editColors((colors) => (
    colors.map((c) => c.id === colorId ? { ...c, name } : c)
  ));
  const setColor = (colorId, swatchId) => editColors((colors) => (
    colors.map((c) => c.id === colorId ? { ...c, swatchId } : c)
  ));
  const addColor = () => editColors((colors) => colors.length >= 3
    ? colors
    : [...colors, { id: uid('col'), name: '', isBase: false, images: [] }]);
  const removeColor = (colorId) => editColors((colors) => {
    colors.find((color) => color.id === colorId)?.images.forEach((image) => {
      photoPreviewRegistryRef.current.release(image.id);
    });
    return colors.filter((c) => c.id !== colorId);
  });

  const hasPendingAdditionalFront = additionalColorsMissingFront.some((color) =>
    pendingConversions[`${color.id}:Front`]);
  const hasName = !!(product.name && product.name.trim());
  // 추가 색상 정면 안내만 CTA 사진 요구 문구의 예외다(ADR-0017 2026-10-11).
  const disabledReasonIsLink = !authLoading && additionalColorsMissingFront.length > 0
    && !hasPendingAdditionalFront;
  const disabledReason = authLoading ? '로그인 상태를 확인하고 있어요.'
    : disabledReasonIsLink ? '추가한 색상의 정면 사진을 올리거나 색상을 지워 주세요' : '';
  const locked = phase !== 'input' || mobileEditingLocked;
  // AI 분석하기 → analyze inline (skeleton below) → fill analysis form below
  const submit = async () => {
    if (authLoading || mobile) return;
    if (session && !mobile) {
      visualsRequestedRef.current = session.user?.id;
      void draftSlot.prepareProductVisuals(product).catch(() => {});
    }
    const startedAt = Date.now();
    const analysisGeneration = useAppStore.getState().projectGeneration;
    let featureRun = null;
    setAnalysisReady(false);
    setPhase('analyzing');
    window.scrollTo({ top: 0, behavior: 'smooth' });
    try {
      if (!isAnalysisFastEntryEnabled()) {
        queueProductDraftSave(product, analysis, composeMode);
        await flushProductDraftSave();
      }
      // 로그인 여부와 무관하게 확정 전 분석은 publicAnalyze 경로다. 로그인 세션의 Bearer는
      // optional_user 레이트리밋 우선권에만 쓰이며, 보관함 project는 아직 만들지 않는다.
      setAnalysisProjectId(null);
      const enteredName = (product.name && product.name.trim()) ? product.name.trim() : null;
      let a;
      if (isAnalysisFastEntryEnabled()) {
        const key = analysisPhotoKey(product);
        featureRun = createDraftFeatureRun({ product, analysis: null,
          readProduct: () => latestProductRef.current,
          readAnalysis: () => latestAnalysisRef.current,
          swatchColors: catalogs?.swatchColors,
          isCurrent: () => useAppStore.getState().projectGeneration === analysisGeneration,
          onProduct: (next) => {
            latestProductRef.current = next; persistedColorsRef.current = next.colors || [];
            if (mountedRef.current) setProduct(next);
          },
          onAnalysis: (next) => {
            latestAnalysisRef.current = next;
            featureSavedAnalysisRef.current = next;
            if (mountedRef.current) setAnalysis(next);
          },
        });
        const runId = crypto.randomUUID();
        latestAnalysisRef.current = null;
        a = await featureRun.startAnalysis({ runId, startedAt, composeMode });
        if (!a || !mountedRef.current || getActiveFeatureRun() !== featureRun
          || useAppStore.getState().projectGeneration !== analysisGeneration
          || analysisPhotoKey(latestProductRef.current) !== key) return;
        setInputConsistency(a.inputConsistency || null);
        setConsistencyAck(false);
        setAnalysisReady(true);
        return;
      } else {
        a = await api.analyzeProduct(null, { product });
      }
      const analyzedProductPatch = analysisResponseProductPatch(a);
      // 상품명이 비어 있으면 AI가 임의로 지어준다 → 요약 카드에 표시됨 + 서버에도 반영
      const currentProduct = latestProductRef.current || product;
      const finalName = enteredName || a.suggestedName || '새 상품';
      const nextProduct = withAutofilledColors({
        ...currentProduct,
        name: finalName,
        ...analyzedProductPatch,
      }, a);
      persistedColorsRef.current = nextProduct.colors || [];
      setProduct(nextProduct);
      latestProductRef.current = nextProduct;
      const coreAnalysis = mergeProductOwnedAnalysisFields(a, nextProduct);
      setAnalysis(coreAnalysis);
      // 사진 묶음이 바뀌었을 수 있으므로 이전 판정의 확인 상태는 버린다.
      setInputConsistency(a.inputConsistency || null);
      setConsistencyAck(false);
      // 즉시 전환하지 않는다 — 대기 연출이 잔여 단계를 빠르게 완주한 뒤 onFinished 에서 전환.
      setAnalysisReady(true);
    } catch (e) {
      featureRun?.invalidate();
      if (featureRun && (!mountedRef.current || getActiveFeatureRun() !== featureRun
          || useAppStore.getState().projectGeneration !== analysisGeneration)) return;
      // http 모드에서 분석 실패(네트워크·서버 에러)해도 스피너에 고착되지 않게 — 입력으로 복귀 + 안내.
      setPhase('input');
      toast.push(e?.message || '서버 연결이 불안정해요. 잠시 후 다시 시도해 주세요.', { icon: 'alertCircle', variant: 'error', error: e, kind: 'analysis_error', actionName: 'analyze', messageCode: 'analysis_failed', blocking: true });
    }
  };

  const nameCard = (
    <div className="surface">
      <div className="sec-head">
        <div><label className="sec-title" htmlFor="product-name">상품명 <span className="ds-optional">선택</span></label></div>
      </div>
      <div className="sec-sub" style={{ marginTop: -6, marginBottom: 12 }}>안 쓰시면 AI가 알아서 작성해요.</div>
      <input id="product-name" className="field" value={product.name} placeholder="예: 소프트 골지 라운드 니트"
        disabled={phase === 'analyzing' || mobileEditingLocked} onChange={(e) => {
          const name = e.target.value;
          set({ name });
          if (phase === 'done') colorSaveSchedulerRef.current.schedule({ name });
        }} />
    </div>
  );

  const imgCount = product.colors[0] ? product.colors[0].images.length : 0;
  const images = (
    <div className="surface pi-images">
      <div className="sec-head">
        <div className="ttl" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div className="sec-title" style={{ whiteSpace: 'nowrap' }}>상품 이미지</div>
          <span className="pill pill-soft">현재 {imgCount}장 / 최대 6장</span>
        </div>
      </div>
      <div className="sec-sub" style={{ marginTop: -6, marginBottom: 16, lineHeight: 1.75 }}>앞면, 뒷면은 필수예요.<br />디테일란에 의류의 특징적인 부분들을 찍어서 업로드해주시면 훨씬 정확해져요.</div>
      {product.colors.map((c) => (
        <ColorImageGroup key={c.id} mobile={mobile} group={c} catalogs={catalogs} swatchColors={catalogs.swatchColors}
          onAddFiles={(slot, metas) => addImageFiles(c.id, slot, metas)} onRemove={(id) => removeImage(c.id, id)}
          onRename={(n) => renameColor(c.id, n)} onRemoveGroup={() => removeColor(c.id)} onPickColor={(sid) => setColor(c.id, sid)}
          displayUrl={(imageId, originalUrl) => photoPreviewRegistryRef.current.displayUrl(imageId, originalUrl)}
          photosLocked={phase === 'done' || mobileEditingLocked} readOnly={mobileEditingLocked}
          onPendingChange={reportPending} />
      ))}
      {!locked && (
        <div style={{ marginTop: 16 }}>
          <Button variant="quiet" icon="plus" className="pi-add-color" onClick={addColor} disabled={product.colors.length >= 3}>색상 추가</Button>
          {product.colors.length >= 3 && <p className="hint" style={{ marginTop: 8 }}>색상은 최대 3개까지 추가할 수 있어요.</p>}
        </div>
      )}
    </div>
  );

  // 입력 섹션: 상품명 + 이미지를 한 카드로
  const inputSection = <div className="merged-card">{nameCard}{images}</div>;

  const wide = !mobile && phase !== 'input';

  // after AI analysis starts, the input collapses into a compact summary above the analysis
  const allImages = product.colors.flatMap((c) => c.images);
  const colorCount = product.colors.filter((c) => c.images.length).length;
  const summaryCard = (
    <div className="surface pi-summary">
      <div className="pi-summary-row">
        <div className="pi-summary-thumbs">
          {allImages.slice(0, 3).map((im) => {
            const src = photoPreviewRegistryRef.current.displayUrl(im.id, im.src);
            return src ? <img key={im.id} src={src} alt="" decoding="async" /> : null;
          })}
        </div>
        <div className="pi-summary-meta">
          <div className="sec-title" style={{ fontSize: 15 }}>{product.name || '상품 이미지'}</div>
          <div className="hint" style={{ marginTop: 3 }}>이미지 {allImages.length}장 · 색상 {colorCount || 1}</div>
        </div>
        <button className="btn btn-quiet btn-sm" onClick={() => setExpanded((e) => !e)}>
          {expanded ? '접기' : '펼치기'}<Icon name={expanded ? 'chevUp' : 'chevDown'} size={15} />
        </button>
      </div>
      {expanded && <div className="pi-summary-body">{inputSection}</div>}
    </div>
  );

  return (
    <div className={`wizard${wide ? ' wide' : ''}${mobile ? ' pi-handoff' : ''}`}>
      {/* 확정 대기는 '잠금 경고'가 아니라 '이미 시작된 페이지 전환'으로 보여준다(2026-08-14 사용자
          지적 — 흰 잠금 카드 → 콘티보드의 어두운 전환 오버레이가 연달아 떠 이질적이었다).
          도착 화면(ChromeLayout 의 storyboard-transition-overlay)과 같은 시각 언어라 확정→도착이
          한 번의 전환으로 읽힌다. 입력 차단(전체 덮음)·beforeunload 가드는 종전과 동일하다. */}
      {/* 확정 대기는 어두운 베일만 — 로고·문구는 도착 화면에서 한 번에 나타난다(2026-08-14 사용자
          결정: 로고가 대기·도착에 두 번 뜨면 끊겨 보인다). 베일이 도착 오버레이와 같은 톤이라
          화면이 바뀌어도 배경은 이어진 것처럼 읽힌다. 낭독은 aria-label 로 유지. */}
      {promotionLocked && createPortal((
        <div className="input-promotion-transition" role="status" aria-live="polite">
          {/* 일부 스크린리더는 status 의 aria-label 을 무시한다 — 숨긴 텍스트 노드가 호환성이 높다 */}
          <span className="sr-only">상세페이지 구성으로 넘어가고 있어요</span>
        </div>
      ), document.body)}
      {!handoff.result && editingRightsLock}
      {mobile && <MobileSaveModal result={handoff.result} onRetry={saveForPC} onClose={handoff.close} />}
      <DraftEntryDialog dialog={entryDialog} actions={entryActions} busy={entryBusy} />
      {reclaimChoiceOpen && slotLock && (
        <Modal onClose={() => setReclaimChoiceOpen(false)}>
          <h3>어느 내용으로 이어갈까요?</h3>
          <p>저장 시간을 보고 이어갈 내용을 골라주세요. 고르지 않은 쪽은 덮어써요.</p>
          <div className="draft-source-options">
            <Button variant="ghost" onClick={chooseLocalContent}>
              이 화면의 내용 ({formatDraftClock(latestLocalUpdatedAtRef.current)} 저장)
            </Button>
            <Button variant="primary" onClick={chooseRemoteContent}>
              다른 기기의 내용 ({formatDraftClock(slotLock.updatedAt)} 저장)
            </Button>
          </div>
        </Modal>
      )}
      {photoFailure && (
        <Modal onClose={() => setPhotoFailure(null)}>
          <ClientErrorCapture point="photoRestore" error={photoFailure.error} blocking={!photoFailure.canOmit} projectId={analysisProjectId} />
          <h3>사진 {photoFailure.failed}장을 불러오지 못했어요.</h3>
          <p>{photoFailure.removed
            ? `사진 ${photoFailure.failed}장이 정리돼 다시 받을 수 없어요. 휴대폰에서 다시 올려 주세요.`
            : <>인터넷 연결을 확인하고 다시 시도해 주세요.{!photoFailure.canOmit && ' 분석에 쓴 사진이라 빼고 진행할 수 없어요.'}</>}</p>
          <div className="modal-actions">
            {photoFailure.canOmit && (
              <Button variant="ghost" onClick={() => {
                const omitPhotoIds = photoFailure.failedIds;
                setPhotoFailure(null);
                goToStoryboard({ force: true, omitPhotoIds });
              }}>이 사진을 빼고 계속</Button>
            )}
            <Button variant="primary" onClick={() => {
              setPhotoFailure(null);
              goToStoryboard({ force: true });
            }}>다시 시도</Button>
          </div>
        </Modal>
      )}
      {!mobile && doneBlocked && <DoneGuardModal />}
      {creditShortfall && (
        <CreditShortfallModal
          shortfall={creditShortfall}
          action="storyboard"
          onClose={() => setCreditShortfall(null)}
        />
      )}
      {creditResume && (
        <Modal onClose={() => {
          setCreditResume(null);
          navigate(location.pathname, { replace: true, state: null });
        }}>
          <h3>이어서 진행할까요? · {creditResume.requiredCredits}크레딧</h3>
          <p>충전 전 멈춘 작업이에요. 크레딧 사용을 다시 확인해 주세요.</p>
          <div className="modal-actions">
            <Button variant="ghost" onClick={() => {
              setCreditResume(null);
              navigate(location.pathname, { replace: true, state: null });
            }}>나중에</Button>
            <Button variant="primary" onClick={() => {
              setCreditResume(null);
              navigate(location.pathname, { replace: true, state: null });
              goToStoryboard();
            }}>이어서 진행</Button>
          </div>
        </Modal>
      )}
      {consistencyOpen && inputConsistency && (
        <Modal onClose={() => setConsistencyOpen(false)}>
          <h3>사진을 한 번만 확인해 주세요</h3>
          <p>올려주신 사진 중 다른 옷으로 보이는 게 있어요. 이대로 만들면 결과물이 어색해질 수 있어요.</p>
          <ul style={{ margin: '12px 0 0', paddingLeft: 18 }}>
            {inputConsistency.offending.map((o) => (
              <li key={o.index} style={{ marginTop: 4 }}>
                <b>{(catalogs.angleLabels && catalogs.angleLabels[o.slot]) || o.slot}</b> 사진 — {o.reason}
              </li>
            ))}
          </ul>
          <div className="modal-actions">
            {/* 판정이 틀렸을 수 있다 — 진행 경로는 항상 열려 있어야 한다 */}
            <Button variant="ghost" onClick={() => {
              setConsistencyAck(true);
              setConsistencyOpen(false);
              goToStoryboard({ force: true });
            }}>이대로 진행</Button>
            <Button variant="primary" onClick={startOver}>처음부터 다시 하기</Button>
          </div>
        </Modal>
      )}
      <ProductInputHeading mobile={mobile} />

      {mobile && phase === 'done' && !handoff.readOnly && <MobileAnalyzedNotice busy={entryBusy} onNew={nextMobile} />}
      {mobile && handoff.banner && <MobileSaveStatus {...handoff.banner} />}
      {mobile || phase === 'input' ? inputSection : summaryCard}

      {/* 경고를 CTA 모달에만 걸면 그 버튼을 누르기 전까지 화면에 아무 흔적이 없어, 셀러 눈에는
          "다른 옷을 넣었는데 아무 일도 안 일어난" 것으로 보인다(2026-07-31 실측). 분석 직후
          바로 보이는 배너를 함께 둔다 — 모달은 진행 직전 마지막 확인용으로 남긴다. */}
      {/* 판정 근거(슬롯별 상세)는 배너에서 뺀다 — 셀러에게 필요한 건 "무엇을 하면 되는가"뿐
          (2026-08-14 사용자 결정). 상세 근거는 진행 직전 확인 모달에만 남긴다. */}
      {!mobile && inputConsistency && (
        <div className="surface" style={{ marginTop: 12, borderColor: '#f0b429', background: '#fffaf0' }}>
          <div className="sec-title" style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 15 }}>
            <Icon name="alertTri" size={17} /> 다른 옷이 섞인 것 같아요
          </div>
          <p style={{ margin: '8px 0 0', fontSize: 13.5, color: 'var(--fg-2)' }}>
            의류를 잘못 올렸다면 처음부터 시작해주세요. 맞다면 그대로 진행해도 괜찮아요.
          </p>
          <p style={{ margin: '6px 0 0', fontSize: 14.5, fontWeight: 650, color: '#8a6116' }}>
            지금 상태에선 의류구현이 제대로 안될 수 있습니다.
          </p>
        </div>
      )}

      {(phase === 'input' || (mobile && handoff.readOnly)) && (
        <InputMainAction mobile={mobile} canDone={canDone} pending={hasPendingConversions}
          saved={handoff.readOnly} onEdit={() => { handoff.edit(); setPhase('input'); }} onNext={nextMobile} busy={entryBusy}
          saving={handoff.saving || preparingLogin} disabledReason={disabledReason}
          disabledReasonIsLink={disabledReasonIsLink} onSave={saveForPC} onAnalyze={submit} />
      )}

      <div className="af-anchor" />
      {!mobile && phase === 'analyzing' && (
        <>
          <AnalysisProgress
            photoSrc={product.colors?.[0]?.images?.[0]?.src}
            done={analysisReady}
            onFinished={() => { setPhase('done'); toast.push('AI 분석을 완료했어요', { icon: 'sparkles' }); }} />
          <AnalysisSkeleton />
        </>
      )}
      {!mobile && phase === 'done' && (
        <div className="pi-reveal">
          <AnalysisForm inline analysis={analysis} catalogs={catalogs} product={product}
            projectId={analysisProjectId}
            onAnalysisReplace={(next) => {
              const resolved = typeof next === 'function' ? next(latestAnalysisRef.current) : next;
              latestAnalysisRef.current = resolved;
              setAnalysis(resolved);
            }}
            onChange={onAnalysisFormChange}
            onConfirmingChange={setFlowPromotionLocked}
            onNext={goToStoryboard} />
        </div>
      )}
      {pendingRelevantPatch && !creditShortfall && (
        <Modal onClose={() => {
          if (!cancellingRelevantPatchRef.current) setPendingRelevantPatch(null);
        }}>
          {pendingRelevantWorkKindRef.current === 'running' ? (
            <>
              <h3>바꾸면 지금 만들고 있는 마네킹 컷을 버려요</h3>
              <p>지금 만들던 마네킹컷 생성이 취소돼요. 취소된 생성의 크레딧({CREDIT_COSTS.mannequinGenerate})도 차감되고, 새로 만들 때 {CREDIT_COSTS.mannequinGenerate}크레딧이 더 들어요.</p>
            </>
          ) : (
            <>
              <h3>바꾸면 마네킹 컷을 다시 만들어야 해요</h3>
              <p>마네킹 컷이 다시 만들어져요 · {mannequinRegenerationCreditText(creditQuote?.mannequinRegenerate)}. 콘티에서 고른 촬영 세트도 다시 골라야 해요.</p>
            </>
          )}
          <div className="modal-actions">
            {pendingRelevantWorkKindRef.current === 'running' ? (
              <Button variant="ghost" disabled={cancellingRelevantPatch}
                onClick={confirmRunningRelevantPatch}>그대로 바꿀게요</Button>
            ) : (
              <Button variant="ghost" onClick={() => {
                if (!guardMannequinCredits()) return;
                const patch = pendingRelevantPatch;
                setPendingRelevantPatch(null);
                applyAnalysisPatch(patch);
              }}>그대로 바꿀게요</Button>
            )}
            <Button variant="primary" disabled={cancellingRelevantPatch}
              onClick={() => setPendingRelevantPatch(null)}>취소</Button>
          </div>
        </Modal>
      )}
    </div>
  );
}

export default ProductInput;
