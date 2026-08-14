/* =============================================================
   features/product-input — ① 제품 정보 입력 (PRD §5)
   Ported verbatim from reference/prototype/features/product-input.jsx.
   Only change: ES imports/exports; onNext → React Router navigate.
   Markup, classNames, inline styles, real file upload unchanged.
   ============================================================= */
import { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useLocation, useNavigate } from 'react-router-dom';
import { api, isMockMode } from '@/lib/api/index.js';
import { uid } from '@/lib/ids.js';
import { isGenerationRelevantAnalysisPatch, useAppStore } from '@/store/useAppStore.js';
import { generationWorkWarningKind } from '@/lib/generationWorkWarning.js';
import { mannequinGenerationCreditShortfall } from '@/lib/creditPreflight.js';
import { CREDIT_COSTS } from '@/lib/limits.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { CreditShortfallModal } from '@/features/credits/CreditShortfallModal.jsx';
import {
  clearDraft,
  flushProductDraftSave,
  hasPendingDraft,
  loadDraft,
  queueProductDraftSave,
} from '@/lib/draftStore.js';
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
  draftSlot,
  formatDraftClock,
  formatDraftRelativeTime,
} from '@/lib/draftSlot.js';
import { Icon, Button, IconButton, ErrorState, Modal, useToast } from '@/components/ui.jsx';
import { PageHead, WizardCTA, useDoneGuard, DoneGuardModal } from '@/features/shell/shell.jsx';
import { AnalysisForm, AnalysisSkeleton, AnalysisProgress, isMatchRecommendationPatch } from '@/features/analysis/AnalysisForm.jsx';
import {
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
import { createProductPhotoPreviewRegistry } from './productPhotoPreviewRegistry.js';
import {
  invalidateStoryboardEntryPrefetch,
  prefetchStoryboardEntry,
} from '@/features/storyboard/storyboardEntryPrefetch.js';
import { acknowledgeMannequinGenerationCancellation } from '@/features/mannequin/generationRunner.js';

draftSlot.configure(api);

// human-readable file size
const fmtSize = (b) => b == null ? '' : b < 1024 ? b + ' B' : b < 1048576 ? (b / 1024).toFixed(1) + ' KB' : (b / 1048576).toFixed(1) + ' MB';

function ColorSwatchPicker({ swatchColors, value, onChange }) {
  return (
    <div className="color-pick">
      <div className="color-pick-head">
        <label className="lbl">색상 선택</label>
        <span className="hint">이 색상의 이름을 골라주세요</span>
      </div>
      <div className="swatch-grid">
        {swatchColors.map((s) => {
          const on = value === s.id;
          return (
            <button key={s.id} className={`swatch${on ? ' on' : ''}`} onClick={() => onChange(s.id)}>
              <span className="swatch-dot" style={{ background: s.hex }} />
              {s.label}
              {on && <Icon name="check" size={13} className="check" />}
            </button>
          );
        })}
      </div>
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

function restoreDraftProduct(draft) {
  if (!draft?.product) return null;
  const urlById = {};
  for (const photo of draft.photos || []) {
    try { urlById[photo.imageId] = URL.createObjectURL(photo.blob); } catch { /* skip */ }
  }
  return {
    ...draft.product,
    colors: (draft.product.colors || []).map((color) => ({
      ...color,
      images: (color.images || []).map((image) => ({
        ...image,
        src: urlById[image.id] || image.src,
      })),
    })),
  };
}

function hasRequiredDraftPhotos(product) {
  const base = (product?.colors || []).find((color) => color.isBase) || product?.colors?.[0];
  return Boolean(
    base?.images?.some((image) => image.slot === 'Front')
    && base.images.some((image) => image.slot === 'Back'),
  );
}

// small file-meta caption shown over an uploaded image (name · size · type) — requested feature
function MetaCap({ im }) {
  return (
    <span className="img-cap">
      <span className="img-cap-name" title={im.name}>{im.name || '이미지'}</span>
      <span className="img-cap-sub">{fmtSize(im.size)} · {fileExt(im)}</span>
    </span>
  );
}

// add target that ALSO accepts drag-drop + click-to-pick (keeps original .tile.add / .up-empty styles)
function AddDrop({ className, slot, room, onAddFiles, onPendingChange, children }) {
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
  return (
    <div className={`tile upload-placeholder${small ? ' sm' : ''}`} aria-hidden="true">
      <span className="upload-placeholder-logo" />
    </div>
  );
}

function ProductPhotoPreview({ image, displayUrl }) {
  const src = displayUrl(image.id, image.src);
  return src
    ? <img src={src} alt="" decoding="async" onError={(e) => { e.currentTarget.style.opacity = 0; }} />
    : <span className="product-photo-preview-pending" aria-hidden="true" />;
}

function ColorImageGroup({ group, catalogs, swatchColors, onAddFiles, onRemove, onRename, onRemoveGroup, onPickColor, displayUrl, photosLocked = false }) {
  const base = group.isBase;
  const used = group.images.length;
  const [pendingBySlot, setPendingBySlot] = useState({});
  const setSlotPending = (slot, count) => {
    setPendingBySlot((current) => {
      if (count) return { ...current, [slot]: count };
      if (!current[slot]) return current;
      const next = { ...current };
      delete next[slot];
      return next;
    });
  };
  const chosen = (swatchColors || []).find((s) => s.id === group.swatchId);
  // color indicator (dot + label); gray "색상 미정" until a swatch is picked
  const colorInd = (
    <span className="color-ind" title={chosen ? chosen.label : '색상 미정'}>
      <span className={`color-ind-dot${chosen ? '' : ' undecided'}`} style={{ background: chosen ? chosen.hex : '#d4d4d8' }} />
      <span className={`color-ind-label${chosen ? '' : ' undecided'}`}>{chosen ? chosen.label : '색상 미정'}</span>
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
        {imgs.map((im) => (
          <div className={`tile${small ? ' sm' : ''}`} key={im.id}>
            <ProductPhotoPreview image={im} displayUrl={displayUrl} />
            {!photosLocked && <button className="rm" aria-label="내가 업로드한 의류 사진 삭제" onClick={() => onRemove(im.id)}><Icon name="x" size={12} /></button>}
            <MetaCap im={im} />
          </div>
        ))}
        {Array.from({ length: pendingCount }, (_, index) => (
          <PendingTile key={`${s}-pending-${index}`} small={small} />
        ))}
        {!photosLocked && (
          <AddDrop className={`tile add${small ? ' sm' : ''}`} slot={s} room={room}
            onAddFiles={onAddFiles} onPendingChange={setSlotPending}>
            {base && room <= 0 ? (
              <span className="add-limit">최대 6장까지 이미지를 업로드할 수 있습니다.</span>
            ) : (
              <>
                <span className="add-ico"><Icon name="imagePlus" size={small ? 24 : 26} /></span>
                <span className="add-cap"><span>이미지를</span><span>업로드해주세요</span></span>
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
      <div className="slot-well-head"><span className="swh-label">{slotLabel(s)}{(s === 'Front' || s === 'Back') && <span className="req-star">*</span>}</span></div>
      {tiles(s, true)}
    </div>
  );
  return (
    <div className="color-group">
      {!base && (
        <div className="color-group-head">
          <div className="ttl">
            <span className="color-swatch" style={{ background: chosen ? chosen.hex : '#e9e7ec' }} />
            <div className="sec-title" style={{ fontSize: 15 }}>{chosen ? chosen.label : group.name || '색상'}</div>
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
          {group.images.map((im) => (
            <div className="tile" key={im.id}>
              <ProductPhotoPreview image={im} displayUrl={displayUrl} />
              {!photosLocked && <button className="rm" aria-label="내가 업로드한 의류 사진 삭제" onClick={() => onRemove(im.id)}><Icon name="x" size={12} /></button>}
              <MetaCap im={im} />
            </div>
          ))}
          {Array.from({ length: getPendingTileCount(pendingBySlot.Front, 3 - used) }, (_, index) => (
            <PendingTile key={`Front-pending-${index}`} />
          ))}
          {!photosLocked && (
            <AddDrop className="tile add" slot="Front" room={3 - used}
              onAddFiles={onAddFiles} onPendingChange={setSlotPending}>
              <Icon name="plus" size={16} />{used === 0 ? '정면 필수' : '추가'}
            </AddDrop>
          )}
        </div>
      )}

      {used > 0 && (
        <ColorSwatchPicker swatchColors={swatchColors} value={group.swatchId} onChange={onPickColor} />
      )}

      {base && <p className="cap-note">앞면·뒷면 필수 · 현재 {used}장 / 최대 6장</p>}
      {!base && <p className="cap-note">정면 사진 필수 · 색상당 최대 3장 · 현재 {used}장</p>}
    </div>
  );
}

function EditingRightsLock({ meta, onReclaim, onRestartLocal, onDiscard }) {
  const gone = meta?.state === 'gone';
  return (
    <div className="draft-slot-lock" role="alertdialog" aria-modal="true">
      <div className="draft-slot-lock-card">
        <Icon name="lock" size={28} />
        {gone ? (
          <>
            <h3>임시저장이 다른 곳에서 마무리됐어요</h3>
            <p>이 탭에 남아 있는 입력은 자동으로 다시 저장하지 않았어요.<br />이어갈 내용을 직접 골라주세요.</p>
            <div className="modal-actions">
              <Button variant="ghost" onClick={onDiscard}>이 내용 버리고 새로 시작</Button>
              <Button variant="primary" onClick={onRestartLocal}>이 탭 내용으로 다시 저장</Button>
            </div>
          </>
        ) : (
          <>
            <h3>다른 탭 또는 기기에서 이어서 작업 중이에요</h3>
            <p>
              {formatDraftRelativeTime(meta?.updatedAt)} · {meta?.deviceLabel || '다른 탭 또는 기기'}
              <br />내용이 섞이지 않도록 이 화면의 저장을 멈췄어요.
            </p>
            <Button variant="primary" onClick={onReclaim}>이 탭에서 계속하기</Button>
          </>
        )}
      </div>
    </div>
  );
}

export function ProductInput() {
  const navigate = useNavigate();
  const location = useLocation();
  const [product, setProduct] = useState(null);
  const [catalogs, setCatalogs] = useState(null);
  const [loadError, setLoadError] = useState('');
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [phase, setPhase] = useState('input');   // input | analyzing | done
  // 분석 결과 도착 신호 — 화면 전환은 대기 연출(AnalysisProgress)이 잔여 단계를 완주한 뒤
  // onFinished 로 수행한다 (애니메이션 끝 ≈ 전환, 2026-07-13 A안 결정).
  const [analysisReady, setAnalysisReady] = useState(false);
  const [analysis, setAnalysis] = useState(null);
  const [analysisProjectId, setAnalysisProjectId] = useState(null);
  const composeMode = useAppStore((s) => s.composeMode);
  const [expanded, setExpanded] = useState(false);
  // AG-IC 입력 사진 동일성 경고. 서버가 warn 모드일 때만 내려온다(off 면 undefined).
  // 업로드 순간이 아니라 **생성으로 넘어가는 버튼**에서 한 번 띄운다 — 사진을 고르는 도중에
  // 끼어들면 아직 다 올리지도 않은 셀러를 방해한다.
  const [inputConsistency, setInputConsistency] = useState(null);
  const [consistencyAck, setConsistencyAck] = useState(false);   // '계속 진행' 누른 뒤 재차 막지 않는다
  const [consistencyOpen, setConsistencyOpen] = useState(false);
  // 성별·의류 종류 등 생성 관련 필드를 바꾸면 콘티/마네킹의 기존 작업이 무효화된다 — 적용을
  // 보류하고 대가를 먼저 보여준다. 확정 전엔 화면·서버 어느 쪽에도 반영하지 않는다(취소=무해).
  const [pendingRelevantPatch, setPendingRelevantPatch] = useState(null);
  const [cancellingRelevantPatch, setCancellingRelevantPatch] = useState(false);
  const [creditShortfall, setCreditShortfall] = useState(null);
  const [creditResume, setCreditResume] = useState(() => (
    location.state?.creditResume?.action === 'storyboard' ? location.state.creditResume : null
  ));
  const [slotLock, setSlotLock] = useState(null);
  const [reclaimChoiceOpen, setReclaimChoiceOpen] = useState(false);
  const [promotionLocked, setPromotionLocked] = useState(false);
  const { session, loading: authLoading, openLogin } = useAuth();
  const slotEnabled = Boolean(session) || isMockMode;
  const doneBlocked = useDoneGuard();   // 생성 완료 후 초안 재진입 제한 (PRD §10.17)
  const toast = useToast();
  const pushToast = toast.push;
  const [, refreshPhotoPreviews] = useState(0);
  const photoPreviewRegistryRef = useRef(null);
  const latestLocalUpdatedAtRef = useRef(null);
  const latestProductRef = useRef(product);
  const latestAnalysisRef = useRef(analysis);
  const latestComposeModeRef = useRef(composeMode);
  const promotionRunRef = useRef(0);
  const mountedRef = useRef(false);
  latestProductRef.current = product;
  latestAnalysisRef.current = analysis;
  latestComposeModeRef.current = composeMode;
  if (!photoPreviewRegistryRef.current) {
    photoPreviewRegistryRef.current = createProductPhotoPreviewRegistry({
      onChange: () => refreshPhotoPreviews((version) => version + 1),
    });
  }

  useEffect(() => draftSlot.onConflict((meta) => {
    setSlotLock(meta);
    if (!meta) setReclaimChoiceOpen(false);
  }), []);

  useEffect(() => {
    if (slotEnabled) draftSlot.activate();
  }, [slotEnabled]);

  useEffect(() => {
    if (!product) return;
    const localUpdatedAt = new Date().toISOString();
    latestLocalUpdatedAtRef.current = localUpdatedAt;
    queueProductDraftSave(product, analysis, composeMode, localUpdatedAt);
    if (slotEnabled) {
      draftSlot.queue({ product, analysis, composeMode, localUpdatedAt });
    }
  }, [analysis, composeMode, product, slotEnabled]);

  useEffect(() => {
    if (!slotEnabled) return;
    let lastCheckAt = 0;
    const check = () => {
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

  useEffect(() => () => photoPreviewRegistryRef.current.dispose(), []);
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
    return () => window.removeEventListener('beforeunload', blockUnload);
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
    // 클릭 순간의 loadAccount 캐시만 읽는다. 비로그인·아직 계정을 못 불러온 상태는 과차단하지
    // 않고 통과시키며, 실제 잔액 정합성은 기존 서버 402 방어선이 계속 책임진다.
    const account = session ? useAppStore.getState().account : null;
    const shortfall = mannequinGenerationCreditShortfall(account);
    if (!shortfall) return true;
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
    if (!analysisProjectId || phase !== 'done') return;
    if (storyboardPrefetchProjectRef.current === analysisProjectId) return;
    storyboardPrefetchProjectRef.current = analysisProjectId;
    void prefetchStoryboardEntry(analysisProjectId);
  }, [analysisProjectId, phase]);

  useEffect(() => {
    if (!analysisProjectId || phase !== 'done') return;
    if (mannequinWorkCheckProjectRef.current === analysisProjectId) return;
    mannequinWorkCheckProjectRef.current = analysisProjectId;
    let alive = true;
    api.getMannequins(analysisProjectId).then((cuts) => {
      if (alive) hasExistingGenerationWorkRef.current = (cuts || []).length > 0;
    }).catch(() => { /* 조회 실패는 경고를 생략한다 — 방해가 안전보다 나쁘다 */ });
    return () => { alive = false; };
  }, [analysisProjectId, phase]);
  // force: 경고 모달에서 '계속 진행'을 누른 경로. setState 는 비동기라 ack 상태를 기다릴 수
  // 없어 인자로 넘긴다. onNext 콜백이 이벤트 객체를 넘겨도 force 는 undefined 라 안전하다.
  const goToStoryboard = async (opts) => {
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
        if ((isMatchRecommendationPatch(retryPatch) || 'matchClothing' in retryPatch) && savedAnalysis) {
          setAnalysis((current) => ({ ...current, matchClothing: savedAnalysis.matchClothing }));
        }
      }
      if (analysisSaveErrorRef.current) throw analysisSaveErrorRef.current;
      latestSnapshot = {
        product: latestProductRef.current,
        analysis: latestAnalysisRef.current,
        composeMode: latestComposeModeRef.current,
        localUpdatedAt: latestLocalUpdatedAtRef.current || new Date().toISOString(),
      };
      queueProductDraftSave(
        latestSnapshot.product,
        latestSnapshot.analysis,
        latestSnapshot.composeMode,
        latestSnapshot.localUpdatedAt,
      );
      const { failed = 0 } = await flushProductDraftSave() || {};
      if (failed) toast.push(`일부 사진(${failed}장)을 임시 저장하지 못했어요.`, { icon: 'alertTri' });
      if (session || isMockMode) {
        const draft = await loadDraft();
        if (!draft?.product) throw new Error('저장된 입력 내용을 다시 불러오지 못했어요. 다시 시도해 주세요.');
        await draftSlot.flush();
        // 프로젝트를 만들기 전에 서버 잠금 안에서 active token을 소비한다. 여기서 409면
        // 작업권을 잃은 기기는 승격 자체를 시작하지 않는다.
        await draftSlot.remove();
        const { projectId } = await promoteDraftToProject(draft);
        promotedProjectId = projectId;
        if (!isCurrentRun()) return;
        // 게스트로 편집(재생성 신호 dirty)한 뒤 세션이 생겨 여기서 처음 project 를 얻는 경로 —
        // '다른 작업으로 전환'이 아니라 같은 작업이 신원을 얻는 것뿐이라 신호를 지우면 안 된다.
        useAppStore.getState().adoptProject(projectId, { preserveGenerationDirty: true });
        setAnalysisProjectId(projectId);
        useAppStore.getState().confirmProductInfo(projectId);
        await clearDraft().then(() => { resetDraftSyncSingleFlight(); }).catch(() => {});
        navigate('/create/storyboard', { state: { showMannequinTransition: true } });
        return;
      }
      openLogin('/create/storyboard');
    } catch (error) {
      if (promotedProjectId) {
        retryDraftPromotion(promotedProjectId);
      }
      draftSlot.resume();
      if (latestSnapshot && isCurrentRun()) draftSlot.queue(latestSnapshot);
      toast.push(error?.message || '입력 내용을 서버에 저장하지 못했어요. 잠시 후 다시 시도해 주세요.', { icon: 'alert' });
    } finally {
      redirectingRef.current = false;
      if (isCurrentRun()) setFlowPromotionLocked(false);
    }
  };

  const queueAnalysisPatch = (patch) => {
    // 후보 목록은 서버 소유 — 추천 갱신 패치뿐 아니라 선택 토글 응답도
    // 서버 머지 결과로 동기화해 묵은 후보가 로컬에 남지 않게 한다.
    const syncMatch = isMatchRecommendationPatch(patch) || 'matchClothing' in patch;
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
        if (syncMatch) setAnalysis((a) => ({ ...a, matchClothing: savedAnalysis.matchClothing }));
      })
      .catch((error) => {
        failedAnalysisPatchRef.current = mergeLatestFailedAnalysisPatch(
          failedAnalysisPatchRef.current,
          patch,
          latestAnalysisPatchRef.current,
        );
        analysisSaveErrorRef.current = error;
        toast.push(error?.message || '분석 수정 내용을 저장하지 못했어요.', { icon: 'alertTri' });
      });
    registerAnalysisEditSave(analysisProjectId, analysisSaveChainRef.current);
  };
  analysisPatchQueueRef.current = queueAnalysisPatch;

  // 분석 폼의 편집 하나를 실제로 화면·서버에 반영한다. 생성 관련 필드(성별·의류 종류 등)를
  // 바꿀 때 기존 작업이 있으면 이 함수를 곧장 부르지 않고 경고 모달의 확정을 거친다 — 취소하면
  // 아예 호출되지 않으므로 화면·서버 어디에도 흔적이 남지 않는다.
  const applyAnalysisPatch = (patch) => {
    if (isGenerationRelevantAnalysisPatch(patch)) {
      useAppStore.getState().markGenerationRelevantEdits();
    }
    const { productPatch } = splitAnalysisEditPatch(patch);
    setProduct((p) => (hasPatchFields(productPatch) ? { ...p, ...productPatch } : p));
    setAnalysis((a) => ({ ...a, ...patch }));
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

  // 생성 관련 필드 편집 요청 — 기존 작업(마네킹 컷이 있거나, 지금 생성이 도는 중)이 있으면
  // 바로 적용하지 않고 대가를 먼저 보여준다. 없으면(새 프로젝트의 첫 분석 검토) 잃을 게
  // 없으니 그대로 적용해 방해하지 않는다.
  const onAnalysisFormChange = (patch) => {
    if (isGenerationRelevantAnalysisPatch(patch) && generationWorkKind !== 'none') {
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
      toast.push(error?.message || '마네킹컷 생성을 취소하지 못했어요. 잠시 후 다시 시도해 주세요.', { icon: 'alert' });
    } finally {
      cancellingRelevantPatchRef.current = false;
      setCancellingRelevantPatch(false);
    }
  };

  const applyDraftPayload = (payload) => {
    if (!payload?.product) return false;
    const restored = restoreDraftProduct(payload);
    persistedColorsRef.current = restored.colors || [];
    setProduct(restored);
    setAnalysis(payload.analysis || null);
    useAppStore.getState().restoreComposeMode(payload.composeMode);
    setAnalysisProjectId(null);
    setPhase(payload.analysis && hasRequiredDraftPhotos(restored) ? 'done' : 'input');
    return true;
  };

  const chooseRemoteContent = async () => {
    try {
      const takeover = await draftSlot.takeover();
      if (!takeover?.payload) throw new Error('다른 기기의 임시저장을 불러오지 못했어요.');
      applyDraftPayload(takeover.payload);
      setReclaimChoiceOpen(false);
      setSlotLock(null);
    } catch (error) {
      toast.push(error?.message || '작업을 이어받지 못했어요. 잠시 후 다시 시도해 주세요.', { icon: 'alert' });
    }
  };

  const chooseLocalContent = async () => {
    try {
      await draftSlot.takeover();
      setReclaimChoiceOpen(false);
      setSlotLock(null);
      if (product) {
        const localUpdatedAt = latestLocalUpdatedAtRef.current || new Date().toISOString();
        draftSlot.queue({ product, analysis, composeMode, localUpdatedAt });
        await draftSlot.flush();
      }
    } catch (error) {
      toast.push(error?.message || '이 기기의 작업을 이어받지 못했어요.', { icon: 'alert' });
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
      if (!draftSlot.restartAfterGone()) return;
      setSlotLock(null);
      if (product) {
        const localUpdatedAt = latestLocalUpdatedAtRef.current || new Date().toISOString();
        draftSlot.queue({ product, analysis, composeMode, localUpdatedAt });
        await draftSlot.flush();
      }
    } catch (error) {
      toast.push(error?.message || '이 탭의 내용을 다시 저장하지 못했어요.', { icon: 'alert' });
    }
  };

  const startOver = async () => {
    setConsistencyOpen(false);
    if (slotEnabled) {
      try {
        await draftSlot.removeForNewFlow();
      } catch (error) {
        toast.push(error?.message || '임시저장을 정리하지 못했어요. 잠시 후 다시 시도해 주세요.', { icon: 'alert' });
        return;
      }
    }
    await useAppStore.getState().beginProject();
    navigate('/create/input', { replace: true });
  };

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
      const editingProjectId = projectPersisted && currentProjectId ? currentProjectId : null;
      const [p, c, existingAnalysis] = await Promise.all([
        api.getProduct(editingProjectId),
        api.getCatalogs(),
        editingProjectId ? api.getAnalysis(editingProjectId) : Promise.resolve(null),
      ]);
      if (!alive) return;
      setCatalogs(c);
      const staged = draftSlot.consumeStaged();
      if (staged?.payload && applyDraftPayload(staged.payload)) return;
      persistedColorsRef.current = p.colors || [];
      const analysisWasRunning = editingProjectId && isAnalysisRunning(editingProjectId);

      // 같은 탭에서 마네킹/후속 단계로 갔다가 input 으로 돌아온 경우에는 현재 프로젝트를
      // 편집한다. cold input 은 라우트 계층이 먼저 beginProject 해서 여기까지 stale id가 오지 않는다.
      // getAnalysis 는 미저장이어도 {projectId} 봉투를 돌려주므로 truthy — payload 실존 여부로
      // 판정해야 분석이 실패한 프로젝트가 빈 '고스트' 분석 폼으로 뜨는 걸 막는다(F3 진입로).
      if (editingProjectId && existingAnalysis && Object.keys(existingAnalysis).length > 1
          && (!analysisWasRunning || !isMockMode)) {
        setAnalysisRunning(editingProjectId, false);
        setProduct(p);
        setAnalysis(mergeProductOwnedAnalysisFields(existingAnalysis, p));
        // 저장분에서 경고를 복원한다 — 이게 없으면 새로고침·재진입한 탭에서만 게이트가
        // 사라져 그대로 통과한다(분석 직후 탭에서는 멀쩡히 뜨므로 재현이 헷갈린다).
        setInputConsistency(existingAnalysis.inputConsistency || null);
        setAnalysisProjectId(editingProjectId);
        setPhase('done');
        return;
      }

      // 분석 요청 직후 새로고침된 탭은 서버 상품(영속 asset URL 포함)을 먼저 보여주고,
      // 같은 analyze 호출로 활성 job 에 합류한다. 완료된 분석이 방금 저장됐다면 위 분기가 맡는다.
      if (analysisWasRunning) {
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
          const analyzedProductPatch = splitAnalysisEditPatch(a).productPatch;
          const finalName = (recoveredProduct.name && recoveredProduct.name.trim()) || a.suggestedName || '새 상품';
          const nextProduct = { ...recoveredProduct, name: finalName, ...analyzedProductPatch };
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
          toast.push(error?.message || '진행 중인 분석에 다시 연결하지 못했어요. 다시 시도해 주세요.', { icon: 'alert' });
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
        const restored = restoreDraftProduct(draft);
        persistedColorsRef.current = restored.colors || [];
        setProduct(restored);
        useAppStore.getState().restoreComposeMode(draft.composeMode);
        // 분석 결과 복원 → 분석 폼(done)으로 바로. 단 필수 사진(앞면·뒷면)이 추출 실패로
        // 빠졌으면 입력 단계로 둬서 필수 검증이 재업로드를 강제하게 한다(검증 우회 방지).
        // 판정은 product 메타데이터가 아니라 실제 저장된 photo blob(photos[]) 기준이고,
        // 입력 게이트와 동일하게 **기준 색상**의 사진만 인정한다 — 추가 색상 Front 가
        // 기준 색상 Front 유실을 가리면 안 된다 (2026-08-07 Codex 리뷰 P2).
        const draftColors = draft.product?.colors || [];
        const draftBase = draftColors.find((c) => c.isBase) || draftColors[0];
        const restoredHasRequired = !!draftBase
          && (draft.photos || []).some((p) => p.colorId === draftBase.id && p.slot === 'Front')
          && (draft.photos || []).some((p) => p.colorId === draftBase.id && p.slot === 'Back');
        if (draft.analysis && restoredHasRequired) { setAnalysis(draft.analysis); setPhase('done'); }
        return;
      }

      const fresh = { ...p, name: '', colors: [{ ...p.colors[0], swatchId: undefined, images: [] }] };
      setProduct(fresh);
    })().catch((error) => {
      if (alive) setLoadError(error?.message || '입력 화면을 불러오지 못했어요. 다시 시도해 주세요.');
    });
    return () => { alive = false; };
  }, [loadAttempt]);

  if (loadError) return (
    <div className="wizard">
      {editingRightsLock}
      {doneBlocked && <DoneGuardModal />}
      <div className="surface">
        <ErrorState desc={loadError} onRetry={() => setLoadAttempt((n) => n + 1)} />
      </div>
    </div>
  );
  if (!product || !catalogs) return (
    <div className="wizard" aria-busy="true" aria-label="입력 화면 불러오는 중">
      {editingRightsLock}
      {doneBlocked && <DoneGuardModal />}
    </div>
  );

  const set = (patch) => setProduct((p) => ({ ...p, ...patch }));
  // add real uploaded files (drag-drop / picker) with name/size/type meta (PRD §5.5)
  const addImageFiles = (colorId, slot, metas) => setProduct((p) => ({ ...p, colors: p.colors.map((c) => c.id === colorId ? { ...c, images: [...c.images, ...metas.map((m) => ({ id: uid('img'), slot, label: slot, ...m }))] } : c) }));
  const removeImage = (colorId, imgId) => {
    photoPreviewRegistryRef.current.release(imgId);
    setProduct((p) => ({ ...p, colors: p.colors.map((c) => c.id === colorId ? { ...c, images: c.images.filter((im) => im.id !== imgId) } : c) }));
  };
  const editColors = (change) => {
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

  // 필수 판정은 기준 색상 기준 — AI가 소비하는 것이 기준 색상 이미지라서(스펙 §4).
  const baseColor = product.colors.find((c) => c.isBase) || product.colors[0];
  const hasFront = !!baseColor?.images.some((im) => im.slot === 'Front');
  const hasBack = !!baseColor?.images.some((im) => im.slot === 'Back');
  const hasName = !!(product.name && product.name.trim());
  const canDone = hasFront && hasBack && phase === 'input' && !authLoading && !slotLock;
  const disabledReason = !hasFront && !hasBack
    ? '앞면·뒷면 사진이 각 1장 필요해요'
    : !hasFront
      ? '앞면 사진이 필요해요'
      : !hasBack
        ? '뒷면 사진이 필요해요'
        : authLoading
          ? '로그인 상태를 확인하고 있어요.'
          : '';
  const locked = phase !== 'input';
  // AI 분석하기 → analyze inline (skeleton below) → fill analysis form below
  const submit = async () => {
    if (authLoading) return;
    setAnalysisReady(false);
    setPhase('analyzing');
    window.scrollTo({ top: 0, behavior: 'smooth' });
    try {
      queueProductDraftSave(product, analysis, composeMode);
      await flushProductDraftSave();
      // 로그인 여부와 무관하게 확정 전 분석은 publicAnalyze 경로다. 로그인 세션의 Bearer는
      // optional_user 레이트리밋 우선권에만 쓰이며, 보관함 project는 아직 만들지 않는다.
      setAnalysisProjectId(null);
      const enteredName = (product.name && product.name.trim()) ? product.name.trim() : null;
      const a = await api.analyzeProduct(null, { product });
      const analyzedProductPatch = splitAnalysisEditPatch(a).productPatch;
      // 상품명이 비어 있으면 AI가 임의로 지어준다 → 요약 카드에 표시됨 + 서버에도 반영
      const finalName = enteredName || a.suggestedName || '새 상품';
      const nextProduct = {
        ...product,
        name: finalName,
        ...analyzedProductPatch,
      };
      persistedColorsRef.current = nextProduct.colors || [];
      setProduct(nextProduct);
      setAnalysis(mergeProductOwnedAnalysisFields(a, nextProduct));
      // 사진 묶음이 바뀌었을 수 있으므로 이전 판정의 확인 상태는 버린다.
      setInputConsistency(a.inputConsistency || null);
      setConsistencyAck(false);
      // 즉시 전환하지 않는다 — 대기 연출이 잔여 단계를 빠르게 완주한 뒤 onFinished 에서 전환.
      setAnalysisReady(true);
    } catch (e) {
      // http 모드에서 분석 실패(네트워크·서버 에러)해도 스피너에 고착되지 않게 — 입력으로 복귀 + 안내.
      setPhase('input');
      toast.push(e?.message || '분석에 실패했어요. 잠시 후 다시 시도해 주세요.', { icon: 'alert' });
    }
  };

  const nameCard = (
    <div className="surface">
      <div className="sec-head">
        <div><div className="sec-title">상품명 <span className="pi-optional-label">(선택 — 비우면 AI가 지어드려요)</span></div></div>
      </div>
      <input className="field" value={product.name} placeholder="예: 소프트 골지 라운드 니트"
        disabled={phase === 'analyzing'} onChange={(e) => {
          const name = e.target.value;
          set({ name });
          if (phase === 'done') colorSaveSchedulerRef.current.schedule({ name });
        }} />
    </div>
  );

  const allUploaded = product.colors.flatMap((c) => c.images);
  const imgCount = product.colors[0] ? product.colors[0].images.length : 0;
  const images = (
    <div className="surface pi-images">
      <div className="sec-head">
        <div className="ttl" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div className="sec-title" style={{ whiteSpace: 'nowrap' }}>상품 이미지</div>
          <span className="pill pill-soft">{imgCount}장</span>
        </div>
      </div>
      <div className="sec-sub" style={{ marginTop: -6, marginBottom: 16 }}>각도별로 한 장 이상 올리면 더 정확한 상세페이지가 만들어져요. 앞면·뒷면은 필수예요 — 뒷면이 없으면 뒷모습 컷을 만들 수 없어요.</div>
      {product.colors.map((c) => (
        <ColorImageGroup key={c.id} group={c} catalogs={catalogs} swatchColors={catalogs.swatchColors}
          onAddFiles={(slot, metas) => addImageFiles(c.id, slot, metas)} onRemove={(id) => removeImage(c.id, id)}
          onRename={(n) => renameColor(c.id, n)} onRemoveGroup={() => removeColor(c.id)} onPickColor={(sid) => setColor(c.id, sid)}
          displayUrl={(imageId, originalUrl) => photoPreviewRegistryRef.current.displayUrl(imageId, originalUrl)}
          photosLocked={phase === 'done'} />
      ))}
      {!locked && (
        <div style={{ marginTop: 16 }}>
          <Button variant="quiet" icon="plus" onClick={addColor} disabled={product.colors.length >= 3}>색상 추가</Button>
          {product.colors.length >= 3 && <p className="hint" style={{ marginTop: 8 }}>색상은 최대 3개까지 추가할 수 있어요.</p>}
        </div>
      )}
    </div>
  );

  // 입력 섹션: 상품명 + 이미지를 한 카드로
  const inputSection = <div className="merged-card">{nameCard}{images}</div>;

  const wide = phase !== 'input';

  // after AI analysis starts, the input collapses into a compact summary above the analysis
  const allImages = product.colors.flatMap((c) => c.images);
  const colorCount = product.colors.filter((c) => c.images.length).length;
  const summaryCard = (
    <div className="surface pi-summary">
      <div className="pi-summary-row">
        <div className="pi-summary-thumbs">
          {allImages.slice(0, 5).map((im) => {
            const src = photoPreviewRegistryRef.current.displayUrl(im.id, im.src);
            return src ? <img key={im.id} src={src} alt="" decoding="async" /> : null;
          })}
          {allImages.length > 5 && <span className="more">+{allImages.length - 5}</span>}
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
    <div className={`wizard${wide ? ' wide' : ''}`}>
      {/* 확정 대기는 '잠금 경고'가 아니라 '이미 시작된 페이지 전환'으로 보여준다(2026-08-14 사용자
          지적 — 흰 잠금 카드 → 콘티보드의 어두운 전환 오버레이가 연달아 떠 이질적이었다).
          도착 화면(ChromeLayout 의 storyboard-transition-overlay)과 같은 시각 언어라 확정→도착이
          한 번의 전환으로 읽힌다. 입력 차단(전체 덮음)·beforeunload 가드는 종전과 동일하다. */}
      {/* 확정 대기는 어두운 베일만 — 로고·문구는 도착 화면에서 한 번에 나타난다(2026-08-14 사용자
          결정: 로고가 대기·도착에 두 번 뜨면 끊겨 보인다). 베일이 도착 오버레이와 같은 톤이라
          화면이 바뀌어도 배경은 이어진 것처럼 읽힌다. 낭독은 aria-label 로 유지. */}
      {promotionLocked && createPortal((
        <div className="input-promotion-transition" role="status" aria-live="polite"
          aria-label="상세페이지 구성으로 넘어가는 중" />
      ), document.body)}
      {editingRightsLock}
      {reclaimChoiceOpen && slotLock && (
        <Modal onClose={() => setReclaimChoiceOpen(false)}>
          <h3>어느 내용을 이어갈까요?</h3>
          <p>두 기기의 저장 시각을 확인하고 기준으로 사용할 내용을 골라주세요.</p>
          <div className="draft-source-options">
            <Button variant="ghost" onClick={chooseLocalContent}>
              이 기기 내용 ({formatDraftClock(latestLocalUpdatedAtRef.current)})
            </Button>
            <Button variant="primary" onClick={chooseRemoteContent}>
              다른 기기 내용 ({formatDraftClock(slotLock.updatedAt)})
            </Button>
          </div>
        </Modal>
      )}
      {doneBlocked && <DoneGuardModal />}
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
      <PageHead
        title="의류 이미지를 올려주세요"
        sub={<>사진 몇장만으로 경험해보세요.<br />부족한 정보는 AI 분석 후 직접 확인하고 보정할 수 있어요.</>}
      />

      {phase === 'input' ? inputSection : summaryCard}

      {/* 경고를 CTA 모달에만 걸면 그 버튼을 누르기 전까지 화면에 아무 흔적이 없어, 셀러 눈에는
          "다른 옷을 넣었는데 아무 일도 안 일어난" 것으로 보인다(2026-07-31 실측). 분석 직후
          바로 보이는 배너를 함께 둔다 — 모달은 진행 직전 마지막 확인용으로 남긴다. */}
      {inputConsistency && (
        <div className="surface" style={{ marginTop: 12, borderColor: '#f0b429', background: '#fffaf0' }}>
          <div className="sec-title" style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 15 }}>
            <Icon name="alertTri" size={17} /> 다른 옷이 섞인 것 같아요
          </div>
          <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
            {inputConsistency.offending.map((o) => (
              <li key={o.index} style={{ marginTop: 4 }}>
                <b>{(catalogs.angleLabels && catalogs.angleLabels[o.slot]) || o.slot}</b> 사진 — {o.reason}
              </li>
            ))}
          </ul>
          <p className="hint" style={{ marginTop: 8 }}>
            잘못 올린 사진이면 처음부터 다시 시작해주세요. 맞다면 그대로 진행해도 괜찮아요.
          </p>
        </div>
      )}

      {phase === 'input' && (
        <>
          <WizardCTA>
            {disabledReason && <span className="wizard-cta-reason">{disabledReason}</span>}
            <Button variant="primary" size="lg" icon="check" disabled={!canDone} onClick={submit}>AI 분석하기</Button>
          </WizardCTA>
        </>
      )}

      <div className="af-anchor" />
      {phase === 'analyzing' && (
        <>
          <AnalysisProgress
            photoSrc={product.colors?.[0]?.images?.[0]?.src}
            done={analysisReady}
            onFinished={() => { setPhase('done'); toast.push('AI 분석을 완료했어요', { icon: 'sparkles' }); }} />
          <AnalysisSkeleton />
        </>
      )}
      {phase === 'done' && (
        <div className="pi-reveal">
          <AnalysisForm inline analysis={analysis} catalogs={catalogs}
            projectId={analysisProjectId}
            onAnalysisReplace={setAnalysis}
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
              <p>지금 만들던 마네킹컷 생성이 취소돼요. 취소된 생성의 크레딧(2)도 차감되고, 새로 만들 때 2크레딧이 더 들어요.</p>
            </>
          ) : (
            <>
              <h3>바꾸면 마네킹 컷을 다시 만들어야 해요</h3>
              <p>마네킹 컷이 다시 만들어져요 · {CREDIT_COSTS.mannequinGenerate} 크레딧. 콘티에서 고른 촬영 세트도 다시 골라야 해요.</p>
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
