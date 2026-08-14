/* =============================================================
   features/editor/Editor.jsx — ⑦ 상세페이지 에디터 (PRD §10)
   Structure/markup/classNames ported verbatim from the prototype
   (reference/prototype/features/editor.jsx). The element manipulation
   ENGINE is swapped to react-moveable (drag/resize/rotate/snap) and
   crop to react-easy-crop, mapped onto the same Element {x,y,w,h,rotate}
   model + patchElById. Everything else (blocks, panels, mini-preview,
   layers, undo/redo, frames, download/preview) keeps prototype logic.
   ============================================================= */
import { useState, useEffect, useLayoutEffect, useRef, useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import Moveable from 'react-moveable';
import QRCode from 'qrcode';
import { api, isMockMode } from '@/lib/api/index.js';
import { getJobSettlement } from '@/lib/api/facemarket.js';
import { buildEditorBlocksFromStoryboard } from '@/mock/db.js';
import { alignSkeletonToServer, canSafelyMergeServerBlocks, decorateGenBlocks, fillGenBlocks, mergeServerBlocks } from '@/lib/editorWaitSkeleton.js';
import { clearEditorWaitDraft, loadEditorWaitDraft, saveEditorWaitDraft } from '@/lib/editorWaitDraft.js';
import { listModels } from '@/lib/api/facemarket.js';
import { uid } from '@/lib/ids.js';
import { useAppStore } from '@/store/useAppStore.js';
import { Icon, IconButton, Button, Modal, EmptyState, useToast } from '@/components/ui.jsx';
import { exampleGenderFromAnalysis, hexFor } from '@/features/storyboard/Storyboard.jsx';
import { AIPanel, WardrobePanel, ImagePanel, TextPanel, FramePanel, ShapePanel, LayerPanel } from '@/features/editor/EditorPanels.jsx';
import { InfoBlockModal } from '@/features/editor/InfoBlockModal.jsx';
import { applyInfoTemplate, applySlotFillToInfo, buildInfoBlock, carrySlotImages, defaultInfoFor, ensureShippingReturnsBlock, fillFeatureCopy, isRepeatablePreset, needsDefaultTemplate, presetTypeOf } from '@/features/editor/presets/infoPresets.js';
import { SHAPE_D } from '@/features/editor/shapes.js';
import { blockHeightFromBottom, clampDragDelta, clampElementRect, expandBlockHeights, getBlockRenderHeight, pointMissesTextLines } from '@/features/editor/editorGeometry.js';
import { EDITOR_FRAME_DRAG_TYPE, EDITOR_INFO_PRESET_DRAG_TYPE, acceptsEditorBlockInsert, findImageDropSlot, pendingImageImportTarget, placeImageInBlock, viewportPointToBlock } from '@/features/editor/editorImageDrop.js';
import { DEFAULT_BUBBLE_RADIUS, DEFAULT_BUBBLE_STROKE, DEFAULT_BUBBLE_STROKE_WIDTH, FRAME_LIBRARY_ITEMS, WARDROBE_IMAGE_MIME, buildFrameBlock, buildImageBlock, buildObjectPreset, colorWithOpacity, decodeWardrobeImage, objectPresetInitialSelectionIds, upgradeLegacyKiwiTemplateBlocks } from '@/features/editor/editorLibrary.js';
import { bubbleTextWidth, fitBubbleToText, isSpeechBubbleElement, patchSelectedBubbleAppearance, speechBubbleFitOptions } from '@/features/editor/editorBubbleFit.js';
import { imageResizeRect, lineHitStrokeWidth, resizePolicyForElement, shouldShowRotationHandle, speechBubblePath, stripPhotoBlockTextElements } from '@/features/editor/editorAppearance.js';
import { isWardrobeImageUsed, mergeEditorImagesIntoWardrobe } from '@/features/editor/editorWardrobe.js';
import { isEditorDeleteKey, isEditorGrayWorkspaceTarget, normalizeEditorSelectionGroups, removeSelectedBlock, removeSelectedElements, selectableElementBelowBlankText, selectionIdsForElement, selectionIdsInsideMarquee, shouldClearEditorSelection, shouldPassGroupDragArea, shouldPreserveMultiSelectionOnPointerDown, shouldStartTextOnlyDrag } from '@/features/editor/editorSelection.js';
import { getUploadValidationError, looksLikeImageFile, toUploadableImage } from '@/lib/imageTranscode.js';
import { CONTENT_ROLES, SECTION_ROLES, normalizeEditorBlockRole } from '@/lib/storyboardTaxonomy.js';
import { withStoryboardSpaceSetExamples } from '@/lib/storyboardSpaceSetCatalog.js';

const FONT_MAP = { 'Cal Sans': 'var(--font-display)', 'Roboto Mono': 'var(--font-mono)', 'Pretendard': 'var(--font-body)', 'Cormorant': 'var(--font-serif)' };

/* 스냅 엔진 상시 on (Phase 1 정식 승격) — react-moveable 내장 snap(elementGuidelines + 캔버스 센티넬).
   DEV 게이트 제거: prod 배포에서도 동작. 옛 커스텀 snapX 는 삭제됨. */
const SNAP_SPIKE = true;

// FaceMarket 정산 영수증(장면③) — 404 = '정산 없음' 확정 신호라 즉시 종료(비 FM 경로 지연 방지).
async function tryGetReceipt(jobId) {
  for (let i = 0; i < 3; i++) {
    try { return await getJobSettlement(jobId); }
    catch (e) {
      if (e?.status === 404) return null;
      await new Promise((r) => setTimeout(r, 1000));
    }
  }
  return null;
}
const wonFmt = (n) => `₩${Number(n || 0).toLocaleString('ko-KR')}`;

function pickEditorImageFile() {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*,.heic,.heif,.hif';
    input.onchange = () => resolve(input.files?.[0] || null);
    input.oncancel = () => resolve(null);
    input.click();
  });
}

function readImageDimensions(file) {
  return new Promise((resolve) => {
    const src = URL.createObjectURL(file);
    const image = new Image();
    const finish = (dimensions) => { URL.revokeObjectURL(src); resolve(dimensions); };
    image.onload = () => finish({ width: image.naturalWidth, height: image.naturalHeight });
    image.onerror = () => finish({ width: null, height: null });
    image.src = src;
  });
}

function waitForImageSource(src, timeoutMs = 12000) {
  return new Promise((resolve, reject) => {
    if (!src) {
      reject(new Error('업로드한 이미지 주소를 확인하지 못했어요.'));
      return;
    }
    const image = new Image();
    let settled = false;
    const finish = (error) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      image.onload = null;
      image.onerror = null;
      if (error) reject(error);
      else resolve();
    };
    const timer = window.setTimeout(() => finish(new Error('이미지를 불러오는 데 시간이 오래 걸리고 있어요. 다시 시도해 주세요.')), timeoutMs);
    image.onload = () => finish();
    image.onerror = () => finish(new Error('업로드한 이미지를 불러오지 못했어요. 다시 시도해 주세요.'));
    image.src = src;
    if (image.complete && image.naturalWidth > 0) finish();
  });
}

async function getFinalEditorBlocks(projectId) {
  let lastError;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const blocks = await api.getEditorBlocks(projectId);
      if (!Array.isArray(blocks)) throw new Error('완성된 상세페이지 형식이 올바르지 않아요.');
      return blocks;
    } catch (error) {
      lastError = error;
      if (attempt < 2) await new Promise((resolve) => setTimeout(resolve, 800));
    }
  }
  throw lastError || new Error('완성된 상세페이지를 불러오지 못했어요.');
}

/* 라이선스 검증 배지 QR (제안서 step03 "& DID 서명 첨부") — 라이선스가 잠긴 상세페이지의
   ai-notice 블록에만 백엔드가 넣는 'license-verify' 요소를 렌더한다. QR 내용은 스캔 대상이
   외부 폰이라 반드시 절대 URL: `{현재 origin}/verify/{licenseId}`. 심사위원이 찍으면 무인증
   공개 검증 페이지로 이동해 "이 얼굴이 검증된 실제 모델이고 라이선스가 유효함"을 즉석 확인한다.
   QR 이 싣는 건 licenseId(공개 검증용 능력토큰)뿐 — 얼굴·digest·CI·생년월일은 담기지 않는다.
   이 요소는 컴플라이언스 산출물이라 선택/이동 대상이 아니다(데이터 요소 아님, 표시 전용). */
function LicenseVerifyEl({ el, base }) {
  const [qr, setQr] = useState(null);
  const licenseId = el.licenseId;
  const verifyUrl = licenseId ? `${window.location.origin}/verify/${licenseId}` : '';
  useEffect(() => {
    if (!verifyUrl) return;
    let alive = true;
    QRCode.toDataURL(verifyUrl, { width: 320, margin: 1, errorCorrectionLevel: 'M' })
      .then((u) => { if (alive) setQr(u); })
      .catch(() => { /* QR 생성 실패 — 배지 텍스트는 별도 요소라 그대로 남는다 */ });
    return () => { alive = false; };
  }, [verifyUrl]);
  return (
    <div className="el el-verify" style={{ ...base, cursor: 'default', pointerEvents: 'none' }}>
      <div className="ev-card">
        {qr
          ? <img className="ev-qr" src={qr} alt="라이선스 검증 QR 코드" draggable={false} />
          : <div className="ev-qr-skel" />}
        <div className="ev-hint">스캔하면 라이선스 검증 페이지로 이동해요</div>
      </div>
    </div>
  );
}

function editableText(node) {
  return String(node?.innerText ?? node?.textContent ?? '').replace(/\n$/, '');
}

function naturalTextWidth(node, value) {
  const doc = node?.ownerDocument;
  if (!doc) return 0;
  const canvas = doc.createElement('canvas');
  const context = canvas.getContext('2d');
  if (!context) return node.scrollWidth || 0;
  const computed = doc.defaultView.getComputedStyle(node);
  context.font = computed.font;
  const tracking = Number.parseFloat(computed.letterSpacing) || 0;
  return Math.max(0, ...String(value || '').split('\n').map((line) => (
    context.measureText(line || ' ').width + Math.max(0, line.length - 1) * tracking
  )));
}

function ImageDropGuide({ scale, filled, width, height, rotate = 0 }) {
  const safeScale = scale || 1;
  const badgeWidth = Math.max(76, Math.min(148, (Number(width) || 240) * safeScale - 16));
  const compact = Math.min((Number(width) || 240) * safeScale, (Number(height) || 240) * safeScale) < 132;
  return (
    <div className="image-drop-guide" style={{ '--drop-inv': 1 / safeScale, '--drop-counter-rotate': `${-rotate}deg` }} aria-hidden="true">
      <span className="image-drop-guide-arrow">↓</span>
      <div className={`image-drop-guide-content${compact ? ' compact' : ''}`} style={{ width: badgeWidth }}>
        <span className="image-drop-guide-icon"><Icon name="imagePlus" size={compact ? 18 : 22} /></span>
        <strong>이 프레임에 {filled ? '교체' : '넣기'}</strong>
        {!compact && <span>여기에 사진이 들어가요</span>}
      </div>
    </div>
  );
}

/* render-only element (selection + inline text edit). Manipulation handled by
   the single <Moveable> in the Editor (targets the selected element node). */
function CanvasElement({ el, blockId, selected, selectionCount = 0, editing, scale, preview, onSelect, onSelectBlock, onPickBelowText, onPatch, onTextCommit, onMultiDragStart, onTextDragStart, onObjectGroupDragStart, onAddImage, onDropImage, onDropImageFiles, onEdit, onCropStart }) {
  const ref = useRef(null);
  const textRef = useRef(null);
  const deferredPick = useRef(false);
  const pendingBubbleFit = useRef(null);
  const [imageDropOver, setImageDropOver] = useState(false);

  /* 글자 근처를 눌렀는지, 상자 안의 먼 빈 곳을 눌렀는지 가른다 — 판정은 viewport
     좌표의 최소 hit 여유를 포함한다. 편집 중이거나 라이브러리 완성형인 요소는 상자 전체를 살려 둔다. */
  const missesGlyphs = (e) => {
    if (el.type !== 'text' || el.shape === 'bubble' || el.fullTextHitArea || editing) return false;
    // Composite objects use the entire text box as their hit target. Exact-glyph
    // hit testing made speech-bubble text look selectable while dropping the group.
    if (el.groupId && el.libraryItemId) return false;
    const node = ref.current;
    if (!node) return false;
    const range = document.createRange();
    range.selectNodeContents(node);
    return pointMissesTextLines([...range.getClientRects()], e.clientX, e.clientY);
  };

  const pick = (e) => {
    if (preview) return;
    if (el.locked) return;
    if (missesGlyphs(e)) {
      e.stopPropagation();
      const elementBelow = onPickBelowText?.(e, el);
      if (elementBelow) {
        onSelect(elementBelow, e.shiftKey);
        return;
      }
      if (!onSelectBlock) return;
      onSelectBlock();
      return;
    }
    e.stopPropagation();
    // 직접 만든 일반 글자는 마퀴 다중선택 상태여도 자기 레이어만 잡는다.
    // 오브젝트 라이브러리의 글자는 아래 완성형 그룹 이동 경로를 사용한다.
    if (shouldStartTextOnlyDrag(el, e.shiftKey)) {
      // 드래그 임계값(4px)에 도달하기 전에 브라우저의 네이티브 글자 선택이 먼저
      // 시작되면 같은 동작이 될 때도, 안 될 때도 있었다. 편집 진입은 더블클릭이
      // 담당하므로 일반 선택 상태에서는 기본 글자 선택을 즉시 막는다.
      e.preventDefault();
      window.getSelection?.()?.removeAllRanges();
      deferredPick.current = false;
      onSelect(el, false);
      onTextDragStart?.(e, el);
      return;
    }
    // 처음 누른 완성형 오브젝트도 같은 포인터 제스처로 바로 움직여야 한다. 아직
    // 선택되지 않은 요소에는 Moveable이 붙어 있지 않으므로 첫 제스처를 캔버스가
    // 이어 받아 프리셋의 배경·선·글자를 함께 이동한다.
    if (!selected && !e.shiftKey && onObjectGroupDragStart?.(e, el)) {
      deferredPick.current = false;
      onSelect(el, false);
      return;
    }
    deferredPick.current = shouldPreserveMultiSelectionOnPointerDown({
      selected,
      selectionCount,
      additive: e.shiftKey,
    });
    if (deferredPick.current) {
      onMultiDragStart?.(e, () => { deferredPick.current = false; });
      return;
    }
    onSelect(el, e.shiftKey);
  };

  const finishClick = (e) => {
    e.stopPropagation();
    if (!deferredPick.current) return;
    deferredPick.current = false;
    onSelect(el, false);
  };

  const previewBubbleSize = useCallback((node) => {
    if (!node || !isSpeechBubbleElement(el)) return null;
    const value = editableText(node);
    const naturalWidth = naturalTextWidth(node, value);
    const width = bubbleTextWidth(el, naturalWidth);
    node.style.width = width + 'px';
    node.style.height = 'auto';
    const fit = fitBubbleToText(el, {
      naturalWidth,
      renderedHeight: node.scrollHeight,
    });
    if (!fit) return null;
    if (ref.current) {
      ref.current.style.left = fit.elementPatch.x + 'px';
      ref.current.style.top = fit.elementPatch.y + 'px';
      ref.current.style.width = fit.elementPatch.w + 'px';
      ref.current.style.height = fit.elementPatch.h + 'px';
    }
    node.style.width = fit.textWidth + 'px';
    pendingBubbleFit.current = fit;
    return fit;
  }, [el]);

  // 프리셋의 임시 w/h 를 한 프레임 먼저 보여 주면 말풍선이 뒤늦게 커지거나 줄어든다.
  // 첫 paint 전 실제 글자 폭·줄바꿈 높이를 재서 캔버스 정본까지 한 번에 맞춘다.
  useLayoutEffect(() => {
    if (el.hidden || editing || !isSpeechBubbleElement(el)) return;
    const nextFit = previewBubbleSize(textRef.current);
    if (!nextFit || preview || !onPatch) return;
    const patch = nextFit.elementPatch;
    const changed = ['x', 'y', 'w', 'h'].some((key) => Math.round(Number(el[key] || 0)) !== patch[key]);
    if (changed) onPatch(blockId, el.id, patch);
  }, [blockId, editing, el, onPatch, preview, previewBubbleSize]);

  if (el.hidden) return null;

  const base = {
    left: el.x, top: el.y, width: el.w, height: el.h,
    transform: el.rotate ? `rotate(${el.rotate}deg)` : undefined, opacity: el.opacity ?? 1,
    pointerEvents: el.locked ? 'none' : undefined,
  };
  const cls = (extra) => `el${extra ? ' ' + extra : ''}${selected ? ' on' : ''}${selected && selectionCount > 1 ? ' multi-selected' : ''}`;
  const common = { ref, 'data-elid': el.id, onPointerDown: pick, onClick: finishClick };
  const imageDropProps = preview || el.type !== 'image' ? {} : {
    onDragOver: (e) => {
      const types = [...e.dataTransfer.types];
      if (!types.includes(WARDROBE_IMAGE_MIME) && !types.includes('Files')) return;
      e.preventDefault(); e.stopPropagation(); e.dataTransfer.dropEffect = 'copy'; setImageDropOver(true);
    },
    onDragLeave: (e) => {
      if (e.currentTarget.contains(e.relatedTarget)) return;
      setImageDropOver(false);
    },
    onDrop: (e) => {
      const image = decodeWardrobeImage(e.dataTransfer.getData(WARDROBE_IMAGE_MIME));
      const files = [...(e.dataTransfer.files || [])];
      if (!image && !files.length) return;
      e.preventDefault(); e.stopPropagation(); setImageDropOver(false);
      if (image) onDropImage?.(image);
      else onDropImageFiles?.(files);
    },
  };

  if (el.type === 'template-overlay') {
    return (
      <div className="el el-template-overlay" style={base} aria-hidden="true">
        <img src={el.src} alt="" draggable={false} />
      </div>
    );
  }

  if (el.type === 'image') {
    const imageFrameStyle = el.stroke && el.stroke !== 'none' ? {
      border: `${el.strokeWidth || 2}px ${el.dash || 'solid'} ${el.stroke}`,
      boxSizing: 'border-box',
      overflow: 'hidden',
    } : {};
    if (!el.src && el.genPending) {
      // 생성 대기/진행/실패 타일 — 입력 페이지 자리표시 언어(회색+로고). 선택·조작 불가
      // (이미지 내용 잠금), 호버 시 콘티에서 고른 예시가 잠깐 비친다(상시 노출 금지 결정).
      return (
        <div data-elid={el.id} className={`el el-slot ed-genwait ${el.genPending}`}
          style={{ ...base, borderRadius: el.radius, cursor: 'not-allowed' }}
          onClick={(e) => e.stopPropagation()} onPointerDown={(e) => e.stopPropagation()}>
          {el.genExample && <img className="ed-genwait-ex" src={el.genExample} alt="" draggable={false} />}
          <span className="ed-genwait-logo" aria-hidden="true" />
          {el.genPending === 'live' && <span className="ed-genwait-shim" aria-hidden="true" />}
          {el.genPending === 'live' && <span className="ed-genwait-tag">생성 중</span>}
          {el.genPending === 'failed'
            ? <span className="ed-genwait-fail">이 컷은 만들지 못했어요<br /><small>크레딧 미차감</small></span>
            : <span className="ed-genwait-hint">{el.genExample ? '콘티 예시 · ' : ''}생성 중 · 아직 편집할 수 없어요</span>}
        </div>
      );
    }
    if (!el.src) {
      const inv = 1 / (scale || 1);
      const compactSlot = Math.min(el.w * (scale || 1), el.h * (scale || 1)) < 120;
      // 빈 슬롯도 radius 를 따른다 — 특징 포인트의 원형 사진 슬롯이 원으로 보이게
      const slotBase = { ...base, borderRadius: el.radius, ...imageFrameStyle };
      if (preview) return <div className="el el-slot" style={slotBase} />;
      return (
        <div {...common} {...imageDropProps} className={cls(`el-slot${el.checkerboard ? ' checkerboard' : ''}${imageDropOver ? ' image-drop-over' : ''}`)} style={slotBase}>
          <button className={`slot-add${compactSlot ? ' compact' : ''}`} style={{ transform: `scale(${inv})` }}
            aria-label="이 프레임에 사진 넣기" title="이 프레임에 사진 넣기"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => { e.stopPropagation(); onAddImage && onAddImage(el); }}>
            <Icon name="imagePlus" size={compactSlot ? 22 : 28} />
            {!compactSlot && <span>여기에 사진 넣기</span>}
          </button>
          {imageDropOver && <ImageDropGuide scale={scale} filled={false} width={el.w} height={el.h} rotate={el.rotate} />}
        </div>
      );
    }
    return (
      <div {...common} {...imageDropProps} className={cls(imageDropOver ? 'image-drop-over' : '')} style={{ ...base, borderRadius: el.radius, ...imageFrameStyle }}
        onDoubleClick={preview ? undefined : (e) => { e.stopPropagation(); onCropStart && onCropStart(el); }}>
        {el.crop ? (
          /* 커밋된 인라인 크롭: 프레임(overflow hidden) 안에 원본을 -ox,-oy 오프셋으로 */
          <div className="el-cropped" style={{ borderRadius: el.radius }}>
            <img src={el.src} alt="" draggable={false} style={{ left: -el.crop.ox, top: -el.crop.oy, width: el.crop.iw, height: el.crop.ih }} />
          </div>
        ) : (
          <img src={el.src} alt="" style={{ borderRadius: el.radius, objectFit: el.fit || 'cover' }} draggable={false} />
        )}
        {imageDropOver && <ImageDropGuide scale={scale} filled width={el.w} height={el.h} rotate={el.rotate} />}
      </div>
    );
  }
  if (el.type === 'text') {
    const s = el.style || {};
    const isBubbleText = isSpeechBubbleElement(el);
    const lines = (el.text || '').split('\n');
    const display = (!s.list || s.list === 'none') ? el.text
      : lines.map((ln, i) => (s.list === 'ordered' ? `${i + 1}. ` : '• ') + ln).join('\n');
    const hasBg = s.bg && s.bg !== 'none';
    if (isBubbleText) {
      const fit = speechBubbleFitOptions(el);
      const fill = colorWithOpacity(el.fill || '#ffffff', el.fillOpacity ?? 1);
      const stroke = el.stroke === 'none' ? 'none' : (el.stroke || DEFAULT_BUBBLE_STROKE);
      const strokeWidth = stroke === 'none' ? 0 : (el.strokeWidth ?? DEFAULT_BUBBLE_STROKE_WIDTH);
      const radius = el.radius ?? DEFAULT_BUBBLE_RADIUS;
      const bubblePath = speechBubblePath({ width: el.w, height: el.h, radius });
      return (
        <div {...common} className={cls(`el-speech-bubble${editing ? ' editing' : ''}`)} style={{ ...base, overflow: 'visible' }}
          onDoubleClick={preview ? undefined : (e) => {
            e.stopPropagation(); pendingBubbleFit.current = null; onEdit(el.id);
            setTimeout(() => textRef.current && textRef.current.focus(), 0);
          }}>
          <svg width="100%" height="100%" viewBox={`0 0 ${Math.max(1, el.w)} ${Math.max(1, el.h)}`} preserveAspectRatio="none"
            aria-hidden="true" style={{ position: 'absolute', inset: 0, display: 'block', overflow: 'visible', pointerEvents: 'none' }}>
            <path d={bubblePath} fill={fill} stroke={stroke} strokeWidth={strokeWidth} strokeLinejoin="round" vectorEffect="non-scaling-stroke"
              transform={el.flipX ? `translate(${el.w} 0) scale(-1 1)` : undefined} />
          </svg>
          <div ref={textRef} className="el-speech-bubble-copy" style={{
            position: 'absolute', left: fit.padX, top: fit.padTop,
            width: Math.max(1, el.w - fit.padX * 2), height: 'auto',
            fontFamily: FONT_MAP[s.font] || 'var(--font-body)', fontSize: s.size, fontWeight: s.weight || 400,
            color: s.color || '#0e0d14', letterSpacing: s.tracking, textAlign: s.align || 'left',
            lineHeight: s.lineHeight ? s.lineHeight + 'px' : 1.4, whiteSpace: 'pre-wrap',
            overflowWrap: 'anywhere', opacity: s.opacity ?? 1,
            background: hasBg ? s.bg : undefined, padding: hasBg ? '2px 8px' : undefined,
            borderRadius: hasBg ? 4 : undefined, fontStyle: s.italic ? 'italic' : 'normal',
            textDecoration: [s.underline && 'underline', s.strike && 'line-through'].filter(Boolean).join(' ') || 'none',
            outline: 'none', pointerEvents: editing ? 'auto' : 'none',
          }}
          contentEditable={editing} suppressContentEditableWarning
          onPointerDown={(e) => { if (editing) e.stopPropagation(); }}
          onClick={(e) => e.stopPropagation()}
          onInput={(e) => { if (editing) previewBubbleSize(e.currentTarget); }}
          onBlur={(e) => {
            const value = editableText(e.currentTarget);
            const nextFit = pendingBubbleFit.current || previewBubbleSize(e.currentTarget);
            pendingBubbleFit.current = null;
            onEdit(null);
            if (nextFit && onTextCommit) onTextCommit(blockId, el.id, value, nextFit.elementPatch);
            else onPatch(blockId, el.id, { text: value });
          }}>
            {editing ? el.text : display}
          </div>
        </div>
      );
    }
    return (
      <div ref={ref} data-elid={el.id} className={cls(`el-text${editing ? ' editing' : ''}`)} style={{ ...base, height: 'auto',
        fontFamily: FONT_MAP[s.font] || 'var(--font-body)', fontSize: s.size, fontWeight: s.weight || 400,
        color: s.color || '#0e0d14', letterSpacing: s.tracking, textAlign: s.align || 'left',
        lineHeight: s.lineHeight ? s.lineHeight + 'px' : 1.4, whiteSpace: 'pre-wrap', opacity: (el.opacity ?? 1) * (s.opacity ?? 1),
        background: hasBg ? s.bg : undefined, padding: hasBg ? '2px 8px' : undefined, borderRadius: hasBg ? 4 : undefined,
        fontStyle: s.italic ? 'italic' : 'normal',
        textDecoration: [s.underline && 'underline', s.strike && 'line-through'].filter(Boolean).join(' ') || 'none' }}
        onPointerDown={(e) => { if (!editing) pick(e); }}
        /* pick 이 이미 요소든 블록이든 골라 놨다 — 어느 쪽이든 이 클릭은 캔버스 바닥까지
           가면 안 된다. 거기 onClick 이 선택을 지운다. */
        onClick={finishClick}
        onDoubleClick={(e) => { e.stopPropagation(); pendingBubbleFit.current = null; onEdit(el.id); setTimeout(() => ref.current && ref.current.focus(), 0); }}
        contentEditable={editing} suppressContentEditableWarning
        onBlur={(e) => {
          const value = editableText(e.currentTarget);
          onEdit(null);
          onPatch(blockId, el.id, { text: value });
        }}>
        {editing ? el.text : display}</div>
    );
  }
  if (el.type === 'license-verify') {
    return <LicenseVerifyEl el={el} base={base} />;
  }
  // shape / line
  let inner = null;
  const fill = el.fill || '#0e0d14';
  const sc = el.stroke && el.stroke !== 'none' ? el.stroke : null;
  const sw = sc ? (el.strokeWidth || 2) : 0;
  if (el.shape === 'circle') inner = <div style={{ width: '100%', height: '100%', borderRadius: '50%', background: fill, boxShadow: sc ? `inset 0 0 0 ${sw}px ${sc}` : undefined }} />;
  else if (el.shape === 'rect') inner = <div style={{ width: '100%', height: '100%', borderRadius: el.radius || 0, background: fill, boxShadow: sc ? `inset 0 0 0 ${sw}px ${sc}` : undefined }} />;
  else if (el.shape === 'triangle') inner = (
    <svg width="100%" height="100%" viewBox={`0 0 ${el.w} ${el.h}`} preserveAspectRatio="none" style={{ display: 'block', overflow: 'visible' }}>
      <polygon points={`${el.w / 2},${sw / 2 || 0} ${el.w - (sw / 2 || 0)},${el.h - (sw / 2 || 0)} ${sw / 2 || 0},${el.h - (sw / 2 || 0)}`} fill={fill} stroke={sc || 'none'} strokeWidth={sw} strokeLinejoin="round" />
    </svg>
  );
  else if (SHAPE_D[el.shape]) inner = (
    <svg width="100%" height="100%" viewBox="0 0 100 100" preserveAspectRatio="none" style={{ display: 'block', overflow: 'visible' }}>
      <path d={SHAPE_D[el.shape]} fill={fill} stroke={sc || 'none'} strokeWidth={sw} strokeLinejoin="round" vectorEffect="non-scaling-stroke"
        transform={el.flipX ? 'translate(100 0) scale(-1 1)' : undefined} />
    </svg>
  );
  else {
    const my = el.h / 2; const lc = el.stroke && el.stroke !== 'none' ? el.stroke : (el.fill || '#0e0d14'); const lw = el.strokeWidth || 2.5;
    const hitWidth = lineHitStrokeWidth(lw, scale);
    const dashMap = { dotted: '3 5', dashed: '12 9', solid: '' };
    const da = dashMap[el.dash || 'solid'] || undefined;
    inner = (
      <svg width="100%" height="100%" viewBox={`0 0 ${el.w} ${el.h}`} style={{ overflow: 'visible', display: 'block' }}>
        <line x1={0} y1={my} x2={el.w} y2={my} stroke="transparent" strokeWidth={hitWidth}
          strokeLinecap="round" pointerEvents="stroke" />
        <line x1={el.shape === 'arrow-l' ? 12 : 0} y1={my} x2={el.shape === 'arrow-r' ? el.w - 12 : el.w} y2={my} stroke={lc} strokeWidth={lw} strokeLinecap="round" strokeDasharray={da} />
        {el.shape === 'arrow-l' && <polyline points={`14,${my - 8} 2,${my} 14,${my + 8}`} fill="none" stroke={lc} strokeWidth={lw} strokeLinecap="round" strokeLinejoin="round" />}
        {el.shape === 'arrow-r' && <polyline points={`${el.w - 14},${my - 8} ${el.w - 2},${my} ${el.w - 14},${my + 8}`} fill="none" stroke={lc} strokeWidth={lw} strokeLinecap="round" strokeLinejoin="round" />}
      </svg>
    );
  }
  return <div {...common} className={cls()} style={base}>{inner}</div>;
}

const IMAGE_IMPORT_COPY = {
  preparing: ['이미지를 불러오고 있어요', '사진을 편집기에 맞게 준비 중이에요'],
  uploading: ['이미지를 불러오고 있어요', '사진을 안전하게 업로드하고 있어요'],
  placing: ['이미지를 배치하고 있어요', '거의 다 됐어요. 잠시만 기다려 주세요'],
  done: ['이미지를 불러왔어요', '드롭한 위치에 배치했어요'],
  error: ['이미지를 불러오지 못했어요', '파일을 확인하고 다시 끌어 놓아 주세요'],
};

function ImageImportWait({ item, scale }) {
  const phase = IMAGE_IMPORT_COPY[item.phase] ? item.phase : 'preparing';
  const [title, detail] = IMAGE_IMPORT_COPY[phase];
  const inv = Math.min(2.5, 1 / (scale || 1));
  const contentWidth = Math.max(76, Math.min(156, item.w * (scale || 1) - 16));
  const compact = Math.min(item.w * (scale || 1), item.h * (scale || 1)) < 132;
  const compactTitle = {
    preparing: '사진 준비 중',
    uploading: '업로드 중',
    placing: '프레임 적용 중',
    done: '완료',
    error: '불러오기 실패',
  }[phase];
  const iconName = phase === 'done' ? 'check' : phase === 'error' ? 'x' : 'loader';

  return (
    <div className={`ed-uploadwait ${phase}`} role="status" aria-live="polite" aria-label={compact ? compactTitle : title}
      style={{ left: item.x, top: item.y, width: item.w, height: item.h, borderRadius: item.radius || 12, transform: item.rotate ? `rotate(${item.rotate}deg)` : undefined, '--upload-counter-rotate': `${-(item.rotate || 0)}deg` }}
      onPointerDown={(e) => { e.preventDefault(); e.stopPropagation(); }}
      onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); }}
      onDrop={(e) => { e.preventDefault(); e.stopPropagation(); }}>
      <span className="ed-uploadwait-shim" aria-hidden="true" />
      <div className={`ed-uploadwait-content${compact ? ' compact' : ''}`} style={{ width: contentWidth, transform: `rotate(var(--upload-counter-rotate)) scale(${inv})` }}>
        <span className="ed-uploadwait-icon"><Icon name={iconName} size={20} className={phase === 'preparing' || phase === 'uploading' || phase === 'placing' ? 'spin' : ''} /></span>
        <strong>{compact ? compactTitle : title}</strong>
        {!compact && <span>{detail}</span>}
        {phase !== 'done' && phase !== 'error' && <span className="ed-uploadwait-track" aria-hidden="true"><i /></span>}
      </div>
    </div>
  );
}

function CanvasBlock({ block, scale, imageImports, selectedBlockId, selEls, onSelectBlock, onSelectEl, onElPatch, onTextCommit, onMultiDragStart, onTextDragStart, onObjectGroupDragStart, shouldSuppressBlankClick, onAddImage, onDropImage, onDropBlockImage, onDropImageFiles, onOpenLayers, onObjectDrop, onReshape, onMove, onAddEmpty, onDelete, onDownload, onEditInfo, editEl, onEdit, crop, onCropDrag, onCropStart, onCropCommit, onCropCancel, onCropReset, idx }) {
  // 블록 높이는 콘텐츠보다 작아지지 않는다 — 이미지를 블록보다 크게 리사이즈하면 블록도 따라 커져 클립 방지.
  // (기존: block.h 있으면 고정 → 이미지 키워도 block-clip 이 잘라 "안 커보이던" 버그)
  const blockH = getBlockRenderHeight(block);
  // 블록은 요소 선택의 좌표계로 계속 활성 상태를 유지하되, 파란 선택 링과 높이 손잡이는
  // 블록 자체만 고른 경우에만 보인다. 다중 요소 선택을 부모 블록 선택처럼 보이게 하지 않는다.
  const blockActive = selectedBlockId === block.id;
  const blockSelected = blockActive && (!selEls || selEls.length === 0);
  const [objOver, setObjOver] = useState(false);

  const pickBelowBlankText = (event, currentElement) => {
    const candidateIds = [];
    const glyphHitIds = [];
    const seen = new Set();
    for (const hit of document.elementsFromPoint(event.clientX, event.clientY)) {
      const node = hit.closest?.('[data-elid]');
      const id = node?.dataset.elid;
      if (!id || seen.has(id)) continue;
      seen.add(id); candidateIds.push(id);
      const candidate = block.elements.find((element) => element.id === id);
      const normalText = candidate?.type === 'text' && candidate.shape !== 'bubble' && !candidate.fullTextHitArea
        && !(candidate.groupId && candidate.libraryItemId);
      if (!normalText) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      if (!pointMissesTextLines([...range.getClientRects()], event.clientX, event.clientY)) glyphHitIds.push(id);
    }
    return selectableElementBelowBlankText(block.elements, currentElement.id, candidateIds, glyphHitIds);
  };

  const resizeFromBottom = (e) => {
    e.stopPropagation(); e.preventDefault();
    if (e.button != null && e.button !== 0) return;
    const sy = e.clientY, startH = blockH;
    const move = (ev) => {
      onReshape(block.id, { h: blockHeightFromBottom(startH, ev.clientY - sy, scale) });
    };
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); document.body.style.userSelect = ''; };
    document.body.style.userSelect = 'none';
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
  };

  return (
    <div className={`canvas-block${blockSelected ? ' on' : ''}${objOver ? ' obj-over' : ''}`}
      data-blockid={block.id}
      /* 고른 뒤 전파를 멈춘다 — 캔버스 바닥의 onClick 이 매 클릭마다 선택을 지우므로,
         멈추지 않으면 누르는 동안만 잡혔다가 손을 떼는 순간 풀린다. */
      onClick={(e) => {
        if (e.target !== e.currentTarget && !e.target.classList.contains('block-clip')) return;
        e.stopPropagation();
        if (shouldSuppressBlankClick?.()) return;
        onSelectBlock(block.id);
      }}
      style={{ background: colorWithOpacity(block.bg, block.bgOpacity), height: blockH, '--inv': 1 / (scale || 1) }}
      onDragOver={(e) => {
        const types = [...e.dataTransfer.types];
        if (!types.some((type) => type === 'text/object' || type === WARDROBE_IMAGE_MIME || type === 'Files')) return;
        e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; setObjOver(true);
      }}
      onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setObjOver(false); }}
      onDrop={(e) => {
        const draggedImage = decodeWardrobeImage(e.dataTransfer.getData(WARDROBE_IMAGE_MIME));
        const files = [...(e.dataTransfer.files || [])];
        const objectData = e.dataTransfer.getData('text/object');
        if (!draggedImage && !files.length && !objectData) return;
        e.preventDefault(); setObjOver(false);
        const rect = e.currentTarget.getBoundingClientRect();
        const point = viewportPointToBlock({ clientX: e.clientX, clientY: e.clientY, blockLeft: rect.left, blockTop: rect.top, scale });
        if (draggedImage) onDropBlockImage(block.id, draggedImage, point);
        else if (files.length) onDropImageFiles(block.id, files, point);
        else { const [type, id] = objectData.split(':'); onObjectDrop(block.id, type, id, e); }
      }}>
      <div className="block-clip">
        {block.elements.map((el) => (
          (crop && crop.elId === el.id) ? null : (
            <CanvasElement key={el.id} el={el} blockId={block.id} scale={scale} preview={false}
              selected={selEls && selEls.includes(el.id)} selectionCount={selEls?.length || 0} editing={editEl === el.id}
              onSelect={(e, additive) => onSelectEl(block.id, e, additive)}
              onSelectBlock={() => onSelectBlock(block.id)} onPickBelowText={pickBelowBlankText}
              onPatch={onElPatch} onTextCommit={onTextCommit}
              onMultiDragStart={(event, onDragStarted) => onMultiDragStart?.(block.id, event, onDragStarted)}
              onTextDragStart={(event, element) => onTextDragStart?.(block.id, element, event)}
              onObjectGroupDragStart={(event, element) => onObjectGroupDragStart?.(block.id, element, event)}
              onAddImage={(elm) => onAddImage(block.id, elm)} onEdit={onEdit}
              onDropImage={(image) => onDropImage(block.id, el.id, image)}
              onDropImageFiles={(files) => onDropImageFiles(block.id, files, null, el.id)}
              onCropStart={(elm) => onCropStart && onCropStart(block.id, elm)} />
          )
        ))}
        {(imageImports || []).map((item) => <ImageImportWait key={item.id} item={item} scale={scale} />)}
        {/* 인라인 크롭 오버레이 — 고스트(원본 전체) + 밝은 프레임(8핸들), 밖은 딤.
            딤 영역(레이어 자신) 클릭 = "빈 곳 클릭" → 크롭 확정 */}
        {crop && (
          <div className="crop-layer" onClick={(e) => { e.stopPropagation(); if (e.target === e.currentTarget) onCropCommit && onCropCommit(); }}>
            <div className="crop-ghost" style={{ left: crop.fx - crop.ox, top: crop.fy - crop.oy, width: crop.iw, height: crop.ih }}
              onPointerDown={(e) => onCropDrag(e, 'img')}>
              <img src={crop.src} alt="" draggable={false} />
            </div>
            <div className="crop-frame" style={{ left: crop.fx, top: crop.fy, width: crop.fw, height: crop.fh, borderRadius: crop.radius }}
              onPointerDown={(e) => onCropDrag(e, 'img')}>
              <div className="crop-frame-image">
                <img src={crop.src} alt="" draggable={false} style={{ left: -crop.ox, top: -crop.oy, width: crop.iw, height: crop.ih }} />
              </div>
              {['nw', 'n', 'ne', 'w', 'e', 'sw', 's', 'se'].map((d) => (
                <span key={d} className={`crop-h ch-${d}`} onPointerDown={(e) => onCropDrag(e, 'frame', d)} />
              ))}
            </div>
            {/* 포토샵식 마칭앤츠 테두리 + 3분할 그리드 — 프레임 밖(비클립) 형제로 렌더해 stroke 안 잘림, pointer-events none */}
            <svg className="crop-svg" style={{ left: crop.fx, top: crop.fy, width: crop.fw, height: crop.fh }}
              viewBox={`0 0 ${Math.max(1, crop.fw)} ${Math.max(1, crop.fh)}`} preserveAspectRatio="none">
              <g className="crop-thirds">
                <line x1={crop.fw / 3} y1={0} x2={crop.fw / 3} y2={crop.fh} />
                <line x1={crop.fw * 2 / 3} y1={0} x2={crop.fw * 2 / 3} y2={crop.fh} />
                <line x1={0} y1={crop.fh / 3} x2={crop.fw} y2={crop.fh / 3} />
                <line x1={0} y1={crop.fh * 2 / 3} x2={crop.fw} y2={crop.fh * 2 / 3} />
              </g>
              <rect className="crop-ant ant-w" x={0} y={0} width={crop.fw} height={crop.fh} />
              <rect className="crop-ant ant-b" x={0} y={0} width={crop.fw} height={crop.fh} />
            </svg>
          </div>
        )}
      </div>
      {crop && (
        <div className="crop-bar" style={{ left: crop.fx, top: crop.fy + crop.fh }} onPointerDown={(e) => e.stopPropagation()} onClick={(e) => e.stopPropagation()}>
          <button className="crop-reset" onClick={(e) => { e.stopPropagation(); onCropReset && onCropReset(); }}>원본</button>
          <button type="button" className="crop-apply" aria-label="크롭 적용" title="크롭 적용" onClick={(e) => { e.stopPropagation(); onCropCommit && onCropCommit(); }}><Icon name="check" size={15} /></button>
          <button type="button" className="crop-cancel" aria-label="크롭 취소" title="크롭 취소" onClick={(e) => { e.stopPropagation(); onCropCancel && onCropCancel(); }}><Icon name="x" size={15} /></button>
        </div>
      )}
      {blockSelected && (
        /* 손잡이를 눌렀다 뗀 클릭도 캔버스 바닥으로 새면 방금 잡은 블록이 풀린다 */
        <span className="blk-resize" onPointerDown={resizeFromBottom} onClick={(e) => e.stopPropagation()} title="아래로 높이 조절"><span className="pill-bar" /></span>
      )}
      <div className="quick" onClick={(e) => e.stopPropagation()}>
        <IconButton name="chevUp" onClick={() => onMove(idx, -1)} title="위로" />
        <IconButton name="chevDown" onClick={() => onMove(idx, 1)} title="아래로" />
        <IconButton name="plus" onClick={() => onAddEmpty(idx)} title="빈 블록 추가" />
        {/* presetTypeOf 로 게이트 — 갓 조립된 size/care auto 블록은 info 가 없어도 폼 편집 대상이다 */}
        {onEditInfo && presetTypeOf(block) && <IconButton name="pencil" onClick={() => onEditInfo(block)} title="내용 수정" />}
        <IconButton name="layers" onClick={() => onOpenLayers(block.id)} title="레이어" />
        <IconButton name="download" onClick={() => onDownload(block)} title="이 블록 다운로드" />
        <IconButton name="trash" onClick={() => onDelete(block.id)} title="블록 삭제" />
      </div>
    </div>
  );
}

function MiniPreview({ blocks, selectedBlockId, onJump, onReorder }) {
  const [dragId, setDragId] = useState(null);
  const [lineAt, setLineAt] = useState(null);
  // 실제 내용 축소 렌더용 썸네일 폭 — 첫 mini-canvas 마운트 시 측정 (패널 폭 75% 파생)
  const [thumbW, setThumbW] = useState(0);
  const measureRef = useCallback((node) => { if (node) setThumbW(node.clientWidth); }, []);
  const end = () => { setDragId(null); setLineAt(null); };
  return (
    <div className="ed-right">
      <div className="mini-head">상세페이지 · 드래그로 순서 변경</div>
      {blocks.map((b, i) => {
        const blockH = getBlockRenderHeight(b);
        return (
        <div key={b.id} style={{ display: 'contents' }}>
          <div className={`mini-dropline${lineAt === i ? ' on' : ''}`} />
          <div className={`mini-block${selectedBlockId === b.id ? ' on' : ''}${dragId === b.id ? ' dragging' : ''}`}
            draggable onClick={() => onJump(b.id)}
            onDragStart={(e) => { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/mini', b.id); setDragId(b.id); }}
            onDragEnd={end}
            onDragOver={(e) => { if (dragId) { e.preventDefault(); const r = e.currentTarget.getBoundingClientRect(); setLineAt(e.clientY > r.top + r.height / 2 ? i + 1 : i); } }}
            onDrop={(e) => { e.preventDefault(); if (!dragId) return; const from = blocks.findIndex((x) => x.id === dragId); let to = lineAt == null ? i : lineAt; if (from < to) to--; to = Math.max(0, Math.min(blocks.length - 1, to)); if (from > -1 && from !== to) onReorder(from, to); end(); }}>
            <div className="mini-canvas" style={{ background: colorWithOpacity(b.bg, b.bgOpacity), aspectRatio: `1000 / ${blockH}` }} title={b.name}
              ref={i === 0 ? measureRef : undefined}>
              {/* 실제 내용 축소 렌더 — 1000px 블록을 그대로 그려 scale 로 줄인다.
                  글자·표·도형이 캔버스와 동일하게 보인다 (CanvasElement preview 재사용, hidden 자동 제외) */}
              <div style={{ position: 'absolute', top: 0, left: 0, width: 1000, height: blockH,
                transform: `scale(${(thumbW || 140) / 1000})`, transformOrigin: 'top left', pointerEvents: 'none' }}>
                {b.elements.map((el) => (
                  <CanvasElement key={el.id} el={el} preview selected={false} onSelect={() => {}} onEdit={() => {}} />
                ))}
              </div>
            </div>
          </div>
        </div>
        );
      })}
      <div className={`mini-dropline${lineAt === blocks.length ? ' on' : ''}`} />
    </div>
  );
}

const hexForCol = (col) => hexFor(col);

/* 프로젝트가 실제 사용 중인 모델(마네킹/분석 단계 선택, analysis.selectedModelId 정본).
   FaceMarket 실존 모델 우선. ⚠️ faceThumbUri 는 인증 게이트 URL(공개 아님) — 문서에
   저장하면 <img> 401 로 깨진다. 저장 가능한 공개 coverImageUrl 만 쓴다. */
function resolveSelectedModel(analysis, fmModels, catalogs) {
  const id = analysis?.selectedModelId;
  if (!id) return null;
  const fm = (fmModels || []).find((m) => m.id === id);
  if (fm) return { name: fm.displayName || '실제 모델', thumb: fm.coverImageUrl || null };
  const cat = ((catalogs && catalogs.models) || []).find((m) => m.id === id);
  if (cat) return { name: cat.name, thumb: cat.thumb || null };
  const am = ((analysis && analysis.models) || []).find((m) => m.id === id);
  return am ? { name: am.name, thumb: am.thumb || null } : null;
}

/* 정보 블록 프리셋 프리필 컨텍스트 — 마운트(자동 템플릿)와 렌더(폼)가 같은 정의를 쓴다 */
function buildInfoCtx({ productName, clothingType, catalogs, product, analysis, colorOpts, fmModels }) {
  return {
    productName,
    clothingType,
    measurementSchema: catalogs?.measurementSchema,
    measurementLabels: catalogs?.measurementLabels,
    measurements: product?.measurements,
    materials: analysis?.materials,
    sellingPoints: (analysis?.sellingPoints?.length ? analysis.sellingPoints : analysis?.aiSuggestedPoints) || [],
    featureCopy: analysis?.featureCopy || [],
    fit: analysis?.fit,
    fits: catalogs?.fits,
    colorLabels: (colorOpts || []).map((o) => o.label),
    selectedModel: resolveSelectedModel(analysis, fmModels, catalogs),
  };
}

/* rotate 정규화: 무한 누적(-1080° 등) 방지 — 항상 (-180, 180] 로 저장·표시 */
const normDeg = (d) => { let n = ((d % 360) + 360) % 360; return n > 180 ? n - 360 : n; };

export function Editor() {
  const navigate = useNavigate();
  const { id: projectId } = useParams();             // /editor/:id = projectId (계약 §2 — mock 은 무시)
  const account = useAppStore((s) => s.account);     // 크레딧 단일 표시 소스 (frontend_state_model §6)
  const syncCredits = useAppStore((s) => s.syncCredits);
  const [blocks, setBlocksState] = useState(null);
  // 이탈 플러시용 최신 blocks — effect([blocks])가 아니라 setBlocks 와 "동기"로
  // 갱신한다. 편집 커밋과 라우트 이탈이 같은 배치에 겹치면(텍스트 blur 직후
  // 보관함 클릭 등) 언마운트가 effect 보다 먼저라 ref 가 한 단계 묵게 되어
  // 마지막 편집이 유실되던 구멍의 수정.
  const latestBlocks = useRef(null);
  const pendingGenerationDraft = useRef(false);
  const setBlocks = useCallback((u) => setBlocksState((prev) => {
    const source = typeof u === 'function' ? u(prev) : u;
    const next = normalizeEditorSelectionGroups(expandBlockHeights(upgradeLegacyKiwiTemplateBlocks(source, uid)));
    latestBlocks.current = next;
    return next;
  }), []);
  const [wardrobe, setWardrobe] = useState(null);
  const wardrobeContext = useRef({ storyboard: [], colorIds: [], fallbackColorId: null });
  const [varyTarget, setVaryTarget] = useState(null); // 의류 탭 'AI 편집'으로 지정한 변형 대상 { id } — 캔버스 선택이 바뀌면 해제
  const [catalogs, setCatalogs] = useState(null);
  const [fmModels, setFmModels] = useState(null); // FaceMarket 검증 모델(실존) — http 모드에서만 로드, 실패=null(가상 폴백)
  const [colorOpts, setColorOpts] = useState([]);
  const [detailColorOpts, setDetailColorOpts] = useState([]);
  const [clothingType, setClothingType] = useState('top'); // 샷 필터 아이콘·예시 크롭용 (계약 §3.1)
  const [matchClothing, setMatchClothing] = useState([]); // AI 탭 '매칭 의류 바꾸기' — 콘티보드와 같은 목록 (계약 §6)
  const [productName, setProductName] = useState('');
  const [product, setProduct] = useState(null);   // 실측(measurements) 등 — 정보 블록 프리필 (PRD §10.14)
  const [analysis, setAnalysis] = useState(null); // targetGenders·materials·fit·sellingPoints — 추천 배지·프리필 전용
  const [infoModal, setInfoModal] = useState(null); // { type, blockId|null, initialInfo }
  const [tab, setTab] = useState('ai');
  const [selBlock, setSelBlock] = useState(null);
  const [selEl, setSelEl] = useState(null);
  const [selEls, setSelEls] = useState([]);
  const [scale, setScale] = useState(0.4);
  const [spaceDown, setSpaceDown] = useState(false); // space-드래그 팬 모드 (Phase 4)
  const [rightHidden, setRightHidden] = useState(false);
  const [preview, setPreview] = useState(false);
  // 이어보기 — 블록 사이 간격·카드 그림자를 없애 실제 상세페이지처럼 붙여 본다(편집은 그대로 가능).
  const [stitched, setStitched] = useState(false);
  // 블록 강조 표시 여부 — 캔버스 빈 곳을 클릭하면 꺼진다. selBlock 자체는 삽입 대상으로 계속 쓰므로 건드리지 않는다.
  const [blockFocused, setBlockFocused] = useState(false);
  const [download, setDownload] = useState(false);
  /* ---- 에디터 통합 대기(editor_wait_dev_spec v2) — 생성이 도는 동안 같은 캔버스에서 편집.
     잡 수명·이벤트는 store.detailPageJob 소유, 여기는 구독·채움·완료 병합만. ---- */
  const dpJob = useAppStore((s) => s.detailPageJob);
  const [genActive, setGenActive] = useState(false);
  const genActiveRef = useRef(false);
  const genMergedRef = useRef(false);
  const [genFinalizeError, setGenFinalizeError] = useState('');
  const [genFinalizeAttempt, setGenFinalizeAttempt] = useState(0);
  const [genReceipt, setGenReceipt] = useState(null);
  const [genNotif, setGenNotif] = useState(
    typeof Notification !== 'undefined' ? Notification.permission : 'unsupported');
  const [dlFormat, setDlFormat] = useState('long');
  const [backWarn, setBackWarn] = useState(false);
  const [genDot, setGenDot] = useState('none');
  const genCount = useRef(0); // 동시 생성 수 — 주황(busy) 점은 마지막 생성이 끝날 때까지 유지
  const [frameOver, setFrameOver] = useState(null);
  const [frameDragging, setFrameDragging] = useState(false);
  const [pendingSlot, setPendingSlot] = useState(null);
  const [imageImports, setImageImports] = useState([]);
  const [wardrobeUploadLoading, setWardrobeUploadLoading] = useState(false);
  const [hoverGray, setHoverGray] = useState(false);
  const [layerFloat, setLayerFloat] = useState(null);
  const [layerPos, setLayerPos] = useState(null);
  const [editEl, setEditEl] = useState(null);     // text element being inline-edited
  // inline crop mode (Figma식): { blockId, elId, src, radius, fx,fy,fw,fh, ox,oy,iw,ih }
  // frame = 보이는 창(fx..fh, 블록 좌표), image drawn at frame-relative -ox,-oy size iw×ih
  const [cropping, setCropping] = useState(null);
  const [lockRatio, setLockRatio] = useState(true); // 이미지 패널 자물쇠 = moveable keepRatio
  const [mvTargets, setMvTargets] = useState([]);  // DOM nodes for react-moveable
  const [mvGuides, setMvGuides] = useState([]);    // Phase0 스파이크: elementGuidelines 소스(형제 요소+센티넬), effect-수집(identity 안정)
  const [marquee, setMarquee] = useState(null);    // wrapper 좌표계의 Figma식 드래그 다중선택 박스
  const dragSnap = useRef(null);                   // start coords during a moveable gesture
  const dragBlockHeight = useRef(null);            // 시작 시 부모 하단 — 요소가 닿으면 더 내려가지 않는다
  const gesturing = useRef(false);                 // moveable 제스처 진행 중 — 상태 커밋/updateRect 금지
  const liveRef = useRef({});                      // elId → 라이브 적용값 (gesture end에 한 번 커밋)
  const toast = useToast();
  const wrapRef = useRef(null);
  const canvasRef = useRef(null);                  // unscaled-layout canvas (transform: scale)
  const moveableRef = useRef(null);                // for updateRect() on selection/layout change
  const pointerGroupRectFrame = useRef(null);      // direct child drag 중 Moveable 그룹 박스 동기화 RAF
  const suppressMarqueeClick = useRef(false);      // pointerup 직후 합성 click이 블록 선택으로 덮는 것 방지
  const [canvasH, setCanvasH] = useState(0);       // unscaled canvas height → scaled spacer
  const hist = useRef({ past: [], future: [] });
  const prevBlocks = useRef(null);
  const fromHistory = useRef(false);
  const lastPush = useRef(0);

  const syncPointerGroupSelectionBounds = useCallback(() => {
    if (pointerGroupRectFrame.current != null) return;
    pointerGroupRectFrame.current = window.requestAnimationFrame(() => {
      pointerGroupRectFrame.current = null;
      moveableRef.current?.updateRect();
    });
  }, []);

  useEffect(() => () => {
    if (pointerGroupRectFrame.current != null) window.cancelAnimationFrame(pointerGroupRectFrame.current);
  }, []);

  useEffect(() => { genActiveRef.current = genActive; }, [genActive]);

  useEffect(() => {
    const enteringJob = useAppStore.getState().detailPageJob;
    const savedDraft = loadEditorWaitDraft(projectId);
    const resumingGeneration = Boolean(enteringJob.projectId === projectId
      && (enteringJob.status === 'running'
        || (enteringJob.status === 'error' && enteringJob.jobId && savedDraft)));
    if (resumingGeneration) useAppStore.getState().startDetailPageGeneration(projectId);
    pendingGenerationDraft.current = Boolean(savedDraft);
    // 에디터는 앱 크롬 밖에서 열린다 — account 는 store 캐시를 직접 로드 (단일 소스)
    Promise.all([api.getEditorBlocks(projectId), api.getWardrobe(projectId), api.getCatalogs(), useAppStore.getState().loadAccount(), api.getProduct(projectId),
      // 실존 모델 카탈로그 — mock 모드는 서버가 없으니 스킵, 실패는 null(AIPanel 이 가상모델 폴백)
      isMockMode ? Promise.resolve(null) : listModels().catch(() => null),
      // 분석 컨텍스트 — 정보 블록 프리필·추천 배지 전용(실패해도 에디터는 뜬다)
      api.getAnalysis(projectId).catch(() => null),
      // 생성 중 진입이면 스켈레톤·예시 썸네일의 근거(콘티) — 실패해도 에디터는 뜬다
      api.getStoryboard(projectId).catch(() => []),
      // AI 탭 '매칭 의류 바꾸기' 목록 — 콘티보드와 동일 소스, 실패해도 에디터는 뜬다
      api.getMatchClothing(projectId).catch(() => [])])
      .then(([b, w, c, _a, p, fm, an, sb, mc]) => {
        const hydratedCatalogs = withStoryboardSpaceSetExamples(c);
        let withH = b.map((blk) => normalizeEditorBlockRole(blk));
        const allColorOpts = (p.colors || []).map((col) => ({ id: col.id, label: col.name || '색상', hex: hexForCol(col) }));
        const opts = allColorOpts.filter((_option, index) => (p.colors[index].images || []).length || p.colors[index].isBase);
        wardrobeContext.current = {
          storyboard: sb || [],
          colorIds: allColorOpts.map((option) => option.id),
          fallbackColorId: opts[0]?.id || allColorOpts[0]?.id || null,
        };
        // 생성 직후 기본 문서(옛 자동 size/care 블록만 있고 info 블록 없음)면
        // 기본 정보 템플릿을 자동으로 구성한다 — 수동 '템플릿 추가' 버튼 대체(2026-07-29 결정).
        const dj = useAppStore.getState().detailPageJob;
        const genMode = resumingGeneration || (dj.status === 'running' && dj.projectId === projectId);
        if (genMode) {
          // 생성 중 진입 — 저장된 blocks(구 데모 시드·빈 값) 대신 콘티 스켈레톤이 캔버스가 된다.
          // 컷·카피는 store 이벤트가 채우고, 완료 시 mergeServerBlocks 로 같은 화면에서 확정.
          const cw = useAppStore.getState().copywriting;
          const exThumb = {};
          for (const blk of sb || []) {
            if (blk?.exampleId) {
              const ex = (hydratedCatalogs?.genExamples || []).find((g) => g.id === blk.exampleId);
              if (ex?.thumb) exThumb[blk.id] = ex.thumb;
            }
          }
          const skeleton = decorateGenBlocks(
            alignSkeletonToServer(buildEditorBlocksFromStoryboard(sb || [], p, cw), cw), dj, exThumb);
          // 생성 중 임시 작업본은 서버 완성본과 분리한다. 재진입 시 문구뿐 아니라 배치·추가 요소도
          // 그대로 복원하고, 이후 재수신되는 생성 이벤트가 새 이미지/카피만 채운다.
          withH = savedDraft || skeleton;
          genActiveRef.current = true;
          setGenActive(true);
        } else {
          const ctx = buildInfoCtx({ productName: p.name || '', clothingType: p.clothingType || 'top', catalogs: hydratedCatalogs, product: p, analysis: an, colorOpts: opts, fmModels: fm });
          if (needsDefaultTemplate(withH)) {
            withH = applyInfoTemplate(withH, ctx).blocks;
            toast.push('기본 정보 템플릿으로 구성했어요 — 사이즈·케어·고시 내용을 채워주세요', { icon: 'check' });
          }
          if (savedDraft) withH = mergeServerBlocks(savedDraft, withH);
          withH = ensureShippingReturnsBlock(withH, ctx);
        }
        withH = upgradeLegacyKiwiTemplateBlocks(stripPhotoBlockTextElements(withH), uid);
        setBlocks(withH);
        setWardrobe(mergeEditorImagesIntoWardrobe({ wardrobe: w, blocks: withH, ...wardrobeContext.current }));
        setCatalogs(hydratedCatalogs); setFmModels(fm); setSelBlock(withH[0]?.id);
        setProductName(p.name || '제목 없는 상세페이지');
        setClothingType(p.clothingType || 'top');
        setMatchClothing(Array.isArray(mc) ? mc : []);
        setProduct(p); setAnalysis(an);
        setDetailColorOpts(allColorOpts.length ? allColorOpts : [{ id: 'col1', label: '기본', hex: '#15141a' }]);
        setColorOpts(opts.length ? opts : [{ id: 'col1', label: '기본', hex: '#15141a' }]);
      });
  }, []);

  // 상세페이지 생성 사진은 editor_blocks 안에만 저장되는 경로도 있다. 블록이 생성·복원될
  // 때마다 서버 의류함과 합쳐 누락 없이 색상별로 보여주고, 직접 업로드 출처는 기타로 모은다.
  useEffect(() => {
    if (!blocks || !wardrobe) return;
    setWardrobe((current) => mergeEditorImagesIntoWardrobe({
      wardrobe: current,
      blocks,
      ...wardrobeContext.current,
    }));
    // wardrobe 는 이 effect가 갱신하므로 의존성에서 제외한다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [blocks]);

  /* 생성 이벤트 → 캔버스 채움. 셀러가 손댄 것(src 있는 슬롯·genAutoText 와 달라진 텍스트)은
     fillGenBlocks 가 건드리지 않는다(셀러 편집 승리). history 는 쌓지 않는다(자동 채움은
     되돌리기 대상이 아님 — setBlocks 는 명시 액션에서만 push). */
  useEffect(() => {
    if (!genActive) return;
    setBlocks((bs) => (bs ? fillGenBlocks(bs, dpJob) : bs));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [genActive, dpJob.cuts, dpJob.copy, dpJob.live, dpJob.failedCuts]);

  /* 완료 — 화면 전환 없이 같은 캔버스에서 확정(오너 결정 #4): 서버 조립본에서 안정 src·
     미편집 카피·AI 고지만 병합하고 저장을 연다. FaceMarket 영수증은 있으면 모달로. */
  useEffect(() => {
    if (!genActive || dpJob.status !== 'done' || genMergedRef.current) return;
    genMergedRef.current = true;
    setGenFinalizeError('');
    let cancelled = false;
    (async () => {
      try {
        const server = await getFinalEditorBlocks(projectId);
        if (cancelled) return;
        // 생성 잡이 진행 중에 analysis.featureCopy 를 쓴다. 마운트 때 받아 둔 스냅샷은
        // 그 쓰기보다 앞서므로, 정보 템플릿을 깔기 전에 다시 읽어야 특징 포인트 설명이 채워진다.
        // 루프는 두 번 돌 수 있으니 네트워크 호출은 루프 밖에 둔다.
        const freshAnalysis = await api.getAnalysis(projectId).catch(() => null);
        if (cancelled) return;
        if (freshAnalysis) setAnalysis(freshAnalysis);
        let restoredServerLayout = false;
        // 서버 완성본의 안정 이미지 주소를 현재 임시 작업본에 합친 뒤 직접 저장한다.
        // 저장 요청 도중 사용자가 한 번 더 편집했다면 최신 ref로 다시 합쳐 저장하여 덮어쓰지 않는다.
        for (;;) {
          const current = latestBlocks.current || [];
          restoredServerLayout ||= !canSafelyMergeServerBlocks(current, server);
          let merged = stripPhotoBlockTextElements(mergeServerBlocks(current, server));
          // 이 이펙트의 클로저가 붙든 analysis 는 여전히 마운트 스냅샷이라 위에서 다시 읽은 값을 쓴다.
          const ctx = buildInfoCtx({ productName, clothingType, catalogs, product, analysis: freshAnalysis || analysis, colorOpts, fmModels });
          if (needsDefaultTemplate(merged)) {
            merged = applyInfoTemplate(merged, ctx).blocks;
          }
          merged = ensureShippingReturnsBlock(merged, ctx);
          merged = normalizeEditorSelectionGroups(expandBlockHeights(upgradeLegacyKiwiTemplateBlocks(stripPhotoBlockTextElements(merged), uid)));
          latestBlocks.current = merged;
          setBlocksState(merged);
          await api.saveEditorBlocks(projectId, merged);
          if (cancelled) return;
          if (latestBlocks.current === merged) break;
        }
        clearEditorWaitDraft(projectId);
        pendingGenerationDraft.current = false;
        genActiveRef.current = false;
        setGenActive(false);
        toast.push(restoredServerLayout
          ? '일부 대기 화면을 복원할 수 없어 서버 완성본을 안전하게 불러왔어요'
          : '상세페이지가 완성됐어요 — 편집 내용까지 저장했어요', { icon: 'check' });
        if (dpJob.jobId) {
          const r = await tryGetReceipt(dpJob.jobId);
          if (!cancelled && r) setGenReceipt(r);
        }
      } catch (error) {
        if (cancelled) return;
        genMergedRef.current = false;
        setGenFinalizeError(error?.message || '완성본을 불러오지 못했어요.');
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [genActive, dpJob.status, genFinalizeAttempt]);

  // history (rapid bursts within 350ms coalesce)
  useEffect(() => {
    if (blocks == null) return;
    if (prevBlocks.current == null) { prevBlocks.current = blocks; return; }
    if (fromHistory.current) { fromHistory.current = false; prevBlocks.current = blocks; return; }
    const now = Date.now();
    if (now - lastPush.current > 350) { hist.current.past.push(prevBlocks.current); if (hist.current.past.length > 80) hist.current.past.shift(); hist.current.future = []; }
    lastPush.current = now; prevBlocks.current = blocks;
  }, [blocks]);

  // 자동 저장 — 생성 중에는 브라우저 임시 작업본, 완료 뒤에는 서버 완성본으로 분리한다.
  // 이 구분이 서버 조립 완료와 미완성 스켈레톤 PUT의 덮어쓰기 경합을 막는다.
  const saveTimer = useRef(null);
  useEffect(() => {
    if (blocks == null) return;
    clearTimeout(saveTimer.current);
    if (genActive) {
      saveTimer.current = setTimeout(() => {
        saveEditorWaitDraft(projectId, latestBlocks.current);
        pendingGenerationDraft.current = true;
      }, 300);
      return;
    }
    saveTimer.current = setTimeout(() => {
      api.saveEditorBlocks(projectId, latestBlocks.current).then(() => {
        if (pendingGenerationDraft.current) {
          clearEditorWaitDraft(projectId);
          pendingGenerationDraft.current = false;
        }
      }).catch(() => {});
    }, 1500);
  }, [blocks, genActive, projectId]);
  useEffect(() => () => {
    clearTimeout(saveTimer.current);
    if (!latestBlocks.current) return;
    if (genActiveRef.current) saveEditorWaitDraft(projectId, latestBlocks.current);
    else api.saveEditorBlocks(projectId, latestBlocks.current);
  }, [projectId]);

  // Delete/Backspace removes the most specific selection: selected elements
  // first, or the whole top-level block when the block itself is selected.
  useEffect(() => {
    const h = (e) => {
      const deleteElements = selEls.length > 0;
      const deleteBlockSelection = !deleteElements && blockFocused && Boolean(selBlock);
      if (isEditorDeleteKey(e) && (deleteElements || deleteBlockSelection)) {
        e.preventDefault();
        if (deleteElements) {
          setBlocks((bs) => removeSelectedElements(bs, selEls));
          setSelEl(null); setSelEls([]); setBlockFocused(false);
          toast.push(`${selEls.length > 1 ? selEls.length + '개 요소를' : '요소를'} 삭제했어요`, { icon: 'trash' });
        } else {
          setBlocks((bs) => removeSelectedBlock(bs, selBlock));
          setSelEl(null); setSelEls([]); setSelBlock(null); setBlockFocused(false);
          toast.push('블록 전체를 삭제했어요', { icon: 'trash' });
        }
      }
      // 방향키 nudge — 1px, Shift=10px. 타이핑/크롭 중 제외. drag 와 동일 clamp([0,1000-w]·y≥0). 연타는 350ms 히스토리 창으로 1 undo.
      if (selEls.length && e.key.startsWith('Arrow')) {
        const t = e.target;
        if (/input|textarea/i.test(t.tagName) || t.isContentEditable || kb.current.croppingOn) return;
        const step = e.shiftKey ? 10 : 1;
        const dx = e.key === 'ArrowLeft' ? -step : e.key === 'ArrowRight' ? step : 0;
        const dy = e.key === 'ArrowUp' ? -step : e.key === 'ArrowDown' ? step : 0;
        if (!dx && !dy) return;
        e.preventDefault();
        setBlocks((bs) => {
          const selectedBlock = bs.find((block) => block.elements.some((el) => selEls.includes(el.id)));
          const snapshot = Object.fromEntries((selectedBlock?.elements || []).filter((el) => selEls.includes(el.id)).map((el) => [el.id, el]));
          const [moveX, moveY] = clampDragDelta(snapshot, [dx, dy], selectedBlock ? getBlockRenderHeight(selectedBlock) : undefined);
          return bs.map((b) => ({ ...b, elements: b.elements.map((el) => (selEls.includes(el.id)
            ? { ...el, x: (el.x || 0) + moveX, y: (el.y || 0) + moveY } : el)) }));
        });
      }
    };
    window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h);
  }, [selEls, selBlock, blockFocused]);

  const kb = useRef({});
  useEffect(() => {
    const onKey = (e) => {
      const t = e.target;
      const typing = /input|textarea/i.test(t.tagName) || t.isContentEditable;
      const mod = e.ctrlKey || e.metaKey;
      // inline crop mode: Enter = 확정, Esc = 취소 (PRD §10.10 인라인 크롭)
      if (kb.current.croppingOn) {
        if (e.key === 'Enter') { e.preventDefault(); kb.current.cropCommit?.(); return; }
        if (e.key === 'Escape') { e.preventDefault(); kb.current.cropCancel?.(); return; }
      }
      if (mod && (e.key === 'z' || e.key === 'Z')) { e.preventDefault(); e.shiftKey ? kb.current.redo?.() : kb.current.undo?.(); return; }
      if (mod && (e.key === 'y' || e.key === 'Y')) { e.preventDefault(); kb.current.redo?.(); return; }
      if (mod && (e.key === 's' || e.key === 'S')) { e.preventDefault(); kb.current.save?.(); return; }
      if (!mod && !typing && e.key === '[' && kb.current.hasSel) { e.preventDefault(); kb.current.layer?.('down'); return; }
      if (!mod && !typing && e.key === ']' && kb.current.hasSel) { e.preventDefault(); kb.current.layer?.('up'); return; }
      if (!mod && !typing && (e.key === 't' || e.key === 'T' || e.key === 'ㅅ') && kb.current.canAddText) { e.preventDefault(); kb.current.addText?.(); }
    };
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey);
  }, []);

  // 크롭 중 휠 = 사진 확대/축소(프레임 고정, 중앙 기준). Ctrl+휠(캔버스 줌)은 그대로 둔다.
  // 로직을 effect 안에 인라인으로 둬 early-return 뒤 선언(TDZ)에 의존하지 않게 한다.
  useEffect(() => {
    if (!cropping) return;
    const wrap = wrapRef.current; if (!wrap) return;
    const onWheel = (e) => {
      if (e.ctrlKey || e.metaKey) return;
      if (!e.target.closest || !e.target.closest('.crop-layer')) return;
      e.preventDefault(); e.stopPropagation();
      const factor = e.deltaY < 0 ? 1.08 : 1 / 1.08;
      setCropping((c) => {
        if (!c || !c.iw || !c.ih) return c;
        const ratio = c.ih / c.iw;
        const minIw = Math.max(c.fw, c.fh / ratio);          // 프레임보다 작아지지 않게(빈틈 방지)
        const niw = Math.min(c.fw * 12, Math.max(minIw, c.iw * factor));
        const nih = niw * ratio;
        const cx = (c.ox + c.fw / 2) / c.iw, cy = (c.oy + c.fh / 2) / c.ih;  // 프레임 중앙의 사진 지점 유지
        const ox = Math.min(Math.max(0, cx * niw - c.fw / 2), Math.max(0, niw - c.fw));
        const oy = Math.min(Math.max(0, cy * nih - c.fh / 2), Math.max(0, nih - c.fh));
        return { ...c, iw: niw, ih: nih, ox, oy };
      });
    };
    wrap.addEventListener('wheel', onWheel, { passive: false });
    return () => wrap.removeEventListener('wheel', onWheel);
  }, [cropping]);

  // Phase 4 — space-드래그 팬 모드 토글. **early-return 앞**에 둬야 hook 개수 안정(blank 크래시 방지).
  useEffect(() => {
    const isType = (t) => /input|textarea/i.test(t.tagName) || t.isContentEditable || t.tagName === 'BUTTON';
    const down = (e) => { if (e.code === 'Space' && !isType(e.target)) { e.preventDefault(); setSpaceDown(true); } };
    const up = (e) => { if (e.code === 'Space') setSpaceDown(false); };
    const blur = () => setSpaceDown(false);
    window.addEventListener('keydown', down); window.addEventListener('keyup', up); window.addEventListener('blur', blur);
    return () => { window.removeEventListener('keydown', down); window.removeEventListener('keyup', up); window.removeEventListener('blur', blur); };
  }, []);

  // Ctrl/Cmd + wheel → zoom in 10% steps
  useEffect(() => {
    const wrap = wrapRef.current; if (!wrap) return;
    const onWheel = (e) => { if (!(e.ctrlKey || e.metaKey)) return; e.preventDefault(); setScale((s) => { const ns = e.deltaY < 0 ? s + 0.1 : s - 0.1; return Math.min(2, Math.max(0.1, +ns.toFixed(2))); }); };
    wrap.addEventListener('wheel', onWheel, { passive: false });
    return () => wrap.removeEventListener('wheel', onWheel);
  }, [!!blocks && !!catalogs]);

  // keep react-moveable bound to the current selection's DOM nodes
  useEffect(() => {
    if (!blocks || preview) { setMvTargets([]); return; }
    const wrap = wrapRef.current; if (!wrap) { setMvTargets([]); return; }
    const ids = editEl ? selEls.filter((id) => id !== editEl) : selEls;
    const nodes = ids.map((id) => wrap.querySelector(`[data-elid="${id}"]`)).filter(Boolean);
    setMvTargets(nodes);
  }, [selEls, blocks, scale, tab, preview, editEl, layerFloat]);

  // Phase0 스파이크: elementGuidelines 소스 = 선택 요소가 속한 블록의 형제 요소 노드 + 캔버스 센티넬.
  // mvTargets 와 동일한 effect+state 패턴(렌더-타임 DOM 쿼리 금지) — deps 는 제스처-안정이라
  // 드래그 중 배열 identity 가 안 변함(prop-churn 재렌더로 리사이즈 죽는 것 방지, critic M1).
  useEffect(() => {
    if (!SNAP_SPIKE || !blocks || preview) { setMvGuides([]); return; }
    const wrap = wrapRef.current; if (!wrap) { setMvGuides([]); return; }
    // 스냅 걸림 범위 = 선택 요소가 있는 페이지(블록) + 바로 위/아래 페이지만. 먼 페이지엔 안 걸린다.
    const selIdx = new Set();
    blocks.forEach((b, i) => { if (b.elements.some((e) => selEls.includes(e.id))) selIdx.add(i); });
    const inRange = new Set();
    selIdx.forEach((i) => { inRange.add(i - 1); inRange.add(i); inRange.add(i + 1); });
    const targetSet = new Set(selEls);
    const sib = [];
    blocks.forEach((b, i) => { if (!inRange.has(i)) return; b.elements.forEach((el) => {
      if (targetSet.has(el.id) || el.hidden || el.locked) return;
      const n = wrap.querySelector(`[data-elid="${el.id}"]`); if (n) sib.push(n);
    }); });
    // 센티넬(캔버스 세로선)은 세로 가이드만 방출 — 전체높이라 top/bottom/middle 수평선까지 뿜던 잡음 제거(critic side-effect).
    // 한 페이지(블록) 내 가운데 정렬 — 선택 블록의 클립 영역을 center/middle 가이드로(요소 중앙이 페이지 중앙 x·y 에 오면 스냅).
    const blockNodes = wrap.querySelectorAll('.canvas-block');
    const centerGuides = [];
    selIdx.forEach((i) => { const clip = blockNodes[i]?.querySelector('.block-clip'); if (clip) centerGuides.push({ element: clip, center: true, middle: true }); });
    const sentinels = Array.from(wrap.querySelectorAll('[data-snap-sentinel]')).map((el) => ({ element: el, left: true, center: true, right: true }));
    setMvGuides([...sib, ...centerGuides, ...sentinels]);
  }, [selEls, blocks, scale, tab, preview, mvTargets]);

  // transform: scale doesn't take layout space — measure the unscaled canvas
  // height so the spacer can reserve the SCALED scroll area (zoom-equivalent)
  useLayoutEffect(() => {
    const el = canvasRef.current; if (!el) return;
    const update = () => setCanvasH(el.offsetHeight);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [!!blocks]);

  // selection/zoom/layout changed → recompute the moveable control-box rect.
  // NEVER during a gesture: mid-gesture updateRect/재바인딩이 컨트롤박스 핸들을
  // 재생성해 리사이즈 제스처를 죽인다(드래그는 타깃 노드에 붙어 살아남는 비대칭).
  useEffect(() => { if (!gesturing.current) moveableRef.current?.updateRect(); }, [blocks, scale, selEls, canvasH, rightHidden, mvTargets]);
  // 파란 완료 점 — 의류 탭을 확인하는 순간 사라진다 (주황 busy 점은 생성이 끝날 때까지 유지)
  useEffect(() => { if (tab === 'wardrobe' && genDot === 'done') setGenDot('none'); }, [tab, genDot]);
  // dev-only QA hook: drive gestures via moveable.request() (real pointer pipeline)
  useEffect(() => { if (import.meta.env.DEV) window.__mv = moveableRef; }, []);
  // Phase0 스파이크 QA 훅: 콘솔에서 window.__spike.zoom(0.4) 후 요소 선택 → __spike.resize(120,120)
  // 로 리사이즈 파이프라인을 스크립트 구동(생존 검증). __spike.guides() = 현재 가이드 노드 수.
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    window.__spike = {
      resize: (dw = 120, dh = 120) => moveableRef.current?.request('resizable', { deltaWidth: dw, deltaHeight: dh, isInstant: true }),
      zoom: (s) => setScale(Math.min(2, Math.max(0.1, +(+s).toFixed(2)))),
      guides: () => mvGuides.length,
    };
  });

  if (!blocks || !catalogs) return <div className="editor"><div style={{ margin: 'auto' }}><Icon name="loader" size={26} className="spin" /></div></div>;

  const selectedElObj = blocks.flatMap((b) => b.elements).find((e) => e.id === selEl);
  const visibleBlock = () => selBlock || blocks[0]?.id;
  const elById = (id) => blocks.flatMap((b) => b.elements).find((e) => e.id === id);

  const selectEl = (blockId, el, additive, keepTab) => {
    if (cropping) commitCrop();   // 크롭 중 다른 요소 클릭 → 크롭 확정 후 선택 (런타임 호출이라 TDZ 무관)
    setVaryTarget(null);          // 캔버스 선택이 바뀌면 'AI 편집' 지정 대상은 해제
    // 요소를 고르는 것도 그 블록을 잡은 것이다 — 이걸 안 켜면 블록 테두리·빠른 도구가
    // 안 뜨고, 블록 배경을 정확히 눌러야만 보이는 상태로 돌아간다.
    setSelBlock(blockId); setBlockFocused(true); setSelEl(el.id);
    const block = blocks.find((item) => item.id === blockId);
    const pickedIds = selectionIdsForElement(block?.elements, el);
    setSelEls((cur) => {
      if (!additive) return pickedIds;
      const allPicked = pickedIds.every((id) => cur.includes(id));
      return allPicked ? cur.filter((id) => !pickedIds.includes(id)) : [...new Set([...cur, ...pickedIds])];
    });
    if (!keepTab) setTab(el.type === 'text' ? 'text' : 'image');
  };
  const clearSel = () => { setSelEl(null); setSelEls([]); setVaryTarget(null); };
  const patchEl = (patch) => setBlocks((bs) => bs.map((b) => ({ ...b, elements: b.elements.map((e) => e.id === selEl ? { ...e, ...patch } : e) })));
  const patchBubbleAppearance = (patch) => setBlocks((bs) => patchSelectedBubbleAppearance(bs, selEls.length ? selEls : [selEl], patch));
  const patchElById = (blockId, elId, patch) => setBlocks((bs) => bs.map((b) => b.id === blockId ? { ...b, elements: b.elements.map((e) => e.id === elId ? { ...e, ...patch } : e) } : b));
  const commitBubbleText = (blockId, elementId, value, elementPatch) => setBlocks((bs) => bs.map((b) => {
    if (b.id !== blockId) return b;
    return {
      ...b,
      elements: b.elements.map((element) => element.id === elementId
        ? { ...element, text: value, ...elementPatch }
        : element),
    };
  }));
  const changeBg = (blockId, patch) => setBlocks((bs) => bs.map((b) => b.id === blockId ? { ...b, ...(typeof patch === 'string' ? { bg: patch } : patch) } : b));
  const reshapeBlock = (blockId, { h, shiftEls }) => setBlocks((bs) => bs.map((b) => {
    if (b.id !== blockId) return b;
    const els = shiftEls ? b.elements.map((e) => shiftEls[e.id] != null ? { ...e, y: shiftEls[e.id] } : e) : b.elements;
    return { ...b, h, elements: els };
  }));
  const reorderBlock = (from, to) => setBlocks((bs) => { const n = [...bs]; const [it] = n.splice(from, 1); n.splice(to, 0, it); return n; });
  const layerEl = (dir) => setBlocks((bs) => bs.map((b) => {
    const i = b.elements.findIndex((e) => e.id === selEl); if (i < 0) return b;
    const els = [...b.elements]; const [it] = els.splice(i, 1);
    const j = dir === 'front' ? els.length : dir === 'back' ? 0 : dir === 'up' ? Math.min(els.length, i + 1) : Math.max(0, i - 1);
    els.splice(j, 0, it); return { ...b, elements: els };
  }));
  const reorderLayer = (blockId, fromId, toId) => setBlocks((bs) => bs.map((b) => {
    if (b.id !== blockId) return b;
    const els = [...b.elements];
    const fi = els.findIndex((e) => e.id === fromId); if (fi < 0) return b;
    const [it] = els.splice(fi, 1);
    const ti = els.findIndex((e) => e.id === toId); if (ti < 0) return b;
    els.splice(fi < ti ? ti + 1 : ti, 0, it);
    return { ...b, elements: els };
  }));
  const toggleElField = (blockId, elId, field) => setBlocks((bs) => bs.map((b) => b.id === blockId
    ? { ...b, elements: b.elements.map((e) => e.id === elId ? { ...e, [field]: !e[field] } : e) } : b));
  const moveBlock = (idx, dir) => setBlocks((bs) => { const n = [...bs]; const j = idx + dir; if (j < 0 || j >= n.length) return n; [n[idx], n[j]] = [n[j], n[idx]]; return n; });
  const addEmpty = (idx) => setBlocks((bs) => { const n = [...bs]; const nb = { id: uid('b'), name: '직접 구성', kind: SECTION_ROLES.STYLING, contentRole: CONTENT_ROLES.CUSTOM, bg: '#ffffff', h: 300, elements: [] }; n.splice(idx + 1, 0, nb); return n; });
  const deleteBlock = (id) => { setBlocks((bs) => bs.filter((b) => b.id !== id)); toast.push('블록을 삭제했어요'); };
  const addFrame = (f, idx) => {
    const nb = buildFrameBlock(f, uid);
    setBlocks((bs) => { const n = [...bs]; n.splice(idx == null ? n.length : idx, 0, nb); return n; });
    setSelBlock(nb.id); setBlockFocused(true); setSelEl(null); setSelEls([]);
    toast.push(`${f.label} 프레임을 새 블록으로 추가했어요`);
  };
  const addImageBlock = (image, idx) => {
    const nb = buildImageBlock(image, uid);
    setBlocks((bs) => { const n = [...bs]; n.splice(idx == null ? n.length : idx, 0, nb); return n; });
    const element = nb.elements[0];
    setSelBlock(nb.id); setBlockFocused(true); setSelEl(element.id); setSelEls([element.id]); setTab('image');
    toast.push('이미지를 새 블록으로 추가했어요', { icon: 'image' });
  };
  const onCanvasInsertDrop = (e, idx) => {
    e.preventDefault(); setFrameOver(null); setFrameDragging(false);
    const image = decodeWardrobeImage(e.dataTransfer.getData(WARDROBE_IMAGE_MIME));
    if (image) { addImageBlock(image, idx); return; }
    const presetType = e.dataTransfer.getData(EDITOR_INFO_PRESET_DRAG_TYPE);
    if (presetType) { addInfoPresetBlock(presetType, idx); return; }
    const id = e.dataTransfer.getData(EDITOR_FRAME_DRAG_TYPE); if (!id) return;
    const f = FRAME_LIBRARY_ITEMS.find((x) => x.id === id); if (f) addFrame(f, idx);
  };
  const addShape = (type, shapeId, bId, dropEvent) => {
    const target = bId || visibleBlock();
    let x = type === 'line' ? 380 : type === 'preset' ? 185 : 430;
    let y = type === 'line' ? 300 : type === 'preset' ? 220 : 250;
    if (dropEvent && wrapRef.current) {
      const blockEl = wrapRef.current.querySelectorAll('.canvas-block')[blocks.findIndex((b) => b.id === target)];
      if (blockEl) {
        const r = blockEl.getBoundingClientRect();
        x = Math.round((dropEvent.clientX - r.left) / scale - (type === 'line' ? 120 : type === 'preset' ? 250 : 70));
        y = Math.round((dropEvent.clientY - r.top) / scale - (type === 'line' ? 12 : type === 'preset' ? 80 : 70));
      }
    }
    if (type === 'preset') {
      const elements = buildObjectPreset(shapeId, { x: Math.max(0, x), y: Math.max(0, y), idFn: uid });
      const initialSelection = objectPresetInitialSelectionIds(shapeId, elements);
      setBlocks((bs) => bs.map((b) => b.id === target ? { ...b, elements: [...b.elements, ...elements] } : b));
      setSelBlock(target); setBlockFocused(true); setSelEl(initialSelection[0]); setSelEls(initialSelection);
      toast.push(shapeId === 'qa-bubbles' ? '독립된 말풍선 두 개를 추가했어요' : '추천 오브젝트를 묶음으로 추가했어요');
      return;
    }
    const el = type === 'line'
      ? { id: uid('el'), type: 'line', shape: shapeId, x, y, w: 240, h: 24 }
      : shapeId === 'bubble'
        ? {
          id: uid('el'), type: 'text', shape: 'bubble', x, y, w: 320, h: 100,
          text: '내용을 입력하세요', style: { size: 20, weight: 500, color: '#000000', lineHeight: 29 },
          fill: '#FFFFFF', stroke: '#000000', strokeWidth: 2, radius: 28,
          bubbleFit: { maxWidth: 560, padX: 24, padTop: 20, padBottom: 38, anchor: 'left' },
        }
      : { id: uid('el'), type: 'shape', shape: shapeId, x, y, w: 140, h: 140,
      };
    setBlocks((bs) => bs.map((b) => b.id === target ? { ...b, elements: [...b.elements, el] } : b));
    selectEl(target, el); toast.push('오브젝트를 추가했어요');
  };
  const setSlotImage = (blockId, elId, image) => {
    setBlocks((bs) => bs.map((b) => (b.id === blockId ? applySlotFillToInfo(b, elId, image || { src: null, cutType: null }) : b)));
  };
  const insertImage = (im, { blockId, point, slotId, keepTab = false } = {}) => {
    const target = blockId || visibleBlock();
    const targetBlock = (latestBlocks.current || blocks).find((block) => block.id === target);
    if (!targetBlock || !im?.src) return;

    const slot = slotId
      ? targetBlock.elements.find((element) => element.id === slotId && element.type === 'image' && element.frameSlot)
      : findImageDropSlot(targetBlock.elements, point);
    if (slot) {
      const filled = {
        ...slot,
        src: im.src,
        cutType: im.cutType || null,
        ...(im.userUploaded ? { userUploaded: true } : {}),
        ...(im.wardrobeGroup ? { wardrobeGroup: im.wardrobeGroup } : {}),
      };
      setSlotImage(target, slot.id, filled);
      selectEl(target, filled, false, keepTab);
      toast.push('프레임에 이미지를 넣었어요', { icon: 'image' });
      return;
    }

    const geometry = placeImageInBlock({
      blockHeight: getBlockRenderHeight(targetBlock),
      imageWidth: im.width || im.w,
      imageHeight: im.height || im.h,
      dropX: point?.x,
      dropY: point?.y,
    });
    const el = {
      id: uid('el'), type: 'image', ...geometry, src: im.src, radius: 12,
      ...(im.cutType ? { cutType: im.cutType } : {}),
      ...(im.userUploaded ? { userUploaded: true } : {}),
      ...(im.wardrobeGroup ? { wardrobeGroup: im.wardrobeGroup } : {}),
    };
    setBlocks((bs) => bs.map((block) => block.id === target ? { ...block, elements: [...block.elements, el] } : block));
    selectEl(target, el, false, keepTab);
    toast.push('이미지를 캔버스에 삽입했어요');
  };
  const requestSlotImage = (blockId, el) => { setPendingSlot({ blockId, elId: el.id }); setTab('wardrobe'); };
  const dropSlotImage = (blockId, elId, image) => {
    setSlotImage(blockId, elId, image);
    toast.push('프레임에 이미지를 넣었어요', { icon: 'image' });
  };
  const wardrobeInsert = (im, { keepTab = false } = {}) => {
    if (pendingSlot) {
      // 정보 블록 슬롯이면 info(폼 정본)에도 동기화 — 재생성 때 사진-포인트 연결 유지
      setSlotImage(pendingSlot.blockId, pendingSlot.elId, {
        src: im.src,
        cutType: im.cutType || null,
        userUploaded: Boolean(im.userUploaded),
        wardrobeGroup: im.wardrobeGroup || null,
      });
      setPendingSlot(null);
      if (!keepTab) setTab('image');
      toast.push('빈 칸에 이미지를 넣었어요');
    } else insertImage(im, { keepTab });
  };
  const patchImageImport = (id, patch) => {
    if (!id) return;
    setImageImports((current) => current.map((item) => item.id === id ? { ...item, ...patch } : item));
  };
  const finishImageImport = (id, delay) => {
    if (!id) return;
    window.setTimeout(() => setImageImports((current) => current.filter((item) => item.id !== id)), delay);
  };
  const uploadEditorImage = async (file, placement, importId = null) => {
    if (!looksLikeImageFile(file)) {
      toast.push('JPG, PNG, WebP 또는 HEIC 사진을 선택해 주세요.', { icon: 'x' });
      return;
    }
    const isWardrobeUpload = !placement?.blockId && !importId;
    if (isWardrobeUpload) setWardrobeUploadLoading(true);
    try {
      const prepared = await toUploadableImage(file);
      const validation = getUploadValidationError(prepared);
      if (validation === 'file_too_large') throw new Error('사진은 25MB 이하만 업로드할 수 있어요.');
      if (validation) throw new Error('지원하지 않는 이미지 형식이에요.');
      const dimensions = await readImageDimensions(prepared);
      if (importId) {
        const targetBlock = (latestBlocks.current || blocks).find((block) => block.id === placement?.blockId);
        const nextRect = !placement?.slotId && targetBlock ? placeImageInBlock({
          blockHeight: getBlockRenderHeight(targetBlock),
          imageWidth: dimensions.width,
          imageHeight: dimensions.height,
          dropX: placement?.point?.x,
          dropY: placement?.point?.y,
        }) : null;
        patchImageImport(importId, { phase: 'uploading', ...(nextRect || {}) });
      }
      const uploaded = await api.uploadPhoto(projectId, {
        filename: prepared.name || file.name || 'editor-image.jpg',
        mime: prepared.type || 'image/jpeg',
        blob: prepared,
      });
      const image = {
        id: uploaded.assetId || uid('w'),
        src: uploaded.url,
        ...dimensions,
        userUploaded: true,
        wardrobeGroup: 'misc',
      };
      patchImageImport(importId, { phase: 'placing' });
      await waitForImageSource(uploaded.url);
      setWardrobe((current) => ({ ...current, misc: [...(current.misc || []), image] }));
      if (placement?.blockId) insertImage(image, placement);
      else wardrobeInsert(image, { keepTab: true });
      patchImageImport(importId, { phase: 'done' });
      finishImageImport(importId, 420);
    } catch (error) {
      patchImageImport(importId, { phase: 'error' });
      finishImageImport(importId, 1600);
      toast.push(error?.message || '사진을 업로드하지 못했어요. 다시 시도해 주세요.', { icon: 'x' });
    } finally {
      if (isWardrobeUpload) setWardrobeUploadLoading(false);
    }
  };
  const dropImageFiles = (blockId, files, point, slotId = null) => {
    const file = files.find(looksLikeImageFile);
    if (!file) {
      toast.push('이미지 파일을 끌어 놓아 주세요.', { icon: 'x' });
      return;
    }
    const targetBlock = (latestBlocks.current || blocks).find((block) => block.id === blockId);
    if (!targetBlock) return;
    const target = pendingImageImportTarget({
      elements: targetBlock.elements,
      blockHeight: getBlockRenderHeight(targetBlock),
      point,
      slotId,
    });
    if (target.slotId && imageImports.some((item) => item.blockId === blockId && item.slotId === target.slotId)) {
      toast.push('이 프레임에서 이미 이미지를 불러오고 있어요.', { icon: 'image' });
      return;
    }
    const importId = uid('upload');
    setImageImports((current) => [...current, {
      id: importId,
      blockId,
      name: file.name || '이미지',
      phase: 'preparing',
      ...target,
    }]);
    void uploadEditorImage(file, { blockId, point, slotId: target.slotId }, importId);
  };
  const pickAndInsertImage = async () => {
    const file = await pickEditorImageFile();
    if (file) await uploadEditorImage(file);
  };
  // fresh = 새로 생성된 컷의 4색 glow 하이라이트 — 사용자가 본 뒤(애니메이션 종료) 해제
  const freshSeen = (id) => setWardrobe((w) => { const nw = {}; for (const [g, arr] of Object.entries(w)) nw[g] = arr.map((x) => x.id === id && x.fresh ? { ...x, fresh: false } : x); return nw; });
  const wardrobeImageInUse = (image) => (
    isWardrobeImageUsed(latestBlocks.current || blocks, image)
  );
  const deleteWardrobeImage = (image) => {
    if (wardrobeImageInUse(image)) {
      toast.push('현재 에디팅에 사용 중인 사진은 삭제할 수 없어요', { icon: 'alertTri' });
      return;
    }
    setWardrobe((current) => {
      const next = {};
      for (const [group, images] of Object.entries(current || {})) {
        const filtered = images.filter((candidate) => candidate.id !== image.id);
        if (filtered.length) next[group] = filtered;
      }
      return next;
    });
    if (varyTarget?.id === image.id) setVaryTarget(null);
    toast.push('이미지를 의류 목록에서 삭제했어요', { icon: 'trash' });
  };
  // req = NewCutRequest 필드 전체 (계약 §6) — 방향·샷·모델·예시 선택이 생성에 그대로 반영되어야 한다
  const generateImage = async (req) => {
    const group = req.colorId || 'misc';             // wardrobe 그룹 키 = colorId | 'misc' (계약 §3.6)
    const loadingId = uid('w');
    setWardrobe((w) => ({ ...w, [group]: [...(w[group] || []), { id: loadingId, loading: true }] }));
    genCount.current += 1; setGenDot('busy'); toast.push('이미지를 생성하는 중이에요', { icon: 'sparkles' });
    try {
      const { data: img, credits } = await api.generateImage(projectId, { mode: 'new', ...req, colorId: group });
      setWardrobe((w) => ({ ...w, [group]: w[group].map((x) => x.id === loadingId ? { ...img, wardrobeGroup: group, fresh: true } : x) }));
      toast.push('이미지 생성을 완료했어요', { icon: 'check' });
      syncCredits(credits);                          // 차감은 서버 책임 — 봉투 잔액만 반영 (계약 §6)
    } catch (e) {
      // 실서버 생성 실패 = 재생성 루프의 정상 경로 — 로딩 타일 제거 + 재시도 안내 (ADR-0004)
      setWardrobe((w) => ({ ...w, [group]: (w[group] || []).filter((x) => x.id !== loadingId) }));
      toast.push(e?.message || '이미지 생성에 실패했어요. 다시 시도해 주세요.', { icon: 'x' });
    } finally {
      genCount.current -= 1; setGenDot(genCount.current > 0 ? 'busy' : 'done');
    }
  };
  // 현재 이미지 수정 — 누적된 변경(chips)을 적용해 생성. 원본의 색상 그룹을 그대로
  // 이어 받아 의류 탭에서 같은 상품 컬러끼리 모여 보이게 한다.
  const varyGenerate = async ({ source, changes, refBg, refBgAssetId }) => {
    const group = varyTarget?.group || source?.wardrobeGroup || 'misc';
    const loadingId = uid('w');
    setWardrobe((w) => ({ ...w, [group]: [...(w[group] || []), { id: loadingId, loading: true }] }));
    setTab('wardrobe');
    genCount.current += 1; setGenDot('busy');
    toast.push(changes.length ? `${changes.length}개 변경을 적용한 컷을 생성하는 중이에요` : '비슷한 컷을 생성하는 중이에요', { icon: 'sparkles' });
    const { data: img, credits } = await api.generateImage(projectId, { mode: 'vary', source, changes, refBg, refBgAssetId });
    setWardrobe((w) => ({ ...w, [group]: w[group].map((x) => x.id === loadingId ? { ...img, wardrobeGroup: group, fresh: true } : x) }));
    genCount.current -= 1; setGenDot(genCount.current > 0 ? 'busy' : 'done'); toast.push('이미지 생성을 완료했어요', { icon: 'check' });
    syncCredits(credits);
    return img;
  };
  const varyImage = (im) => {
    setVaryTarget(im?.id ? { id: im.id, group: im.wardrobeGroup || 'misc' } : null); // 클릭한 의류 이미지가 변형 대상 — 이미지별 독립 상태
    setTab('ai'); toast.push('현재 이미지 수정으로 이동했어요', { icon: 'wand' });
  };
  // 변형 대상 결정 — 'AI 편집' 지정이 있으면 그 의류 이미지, 없으면 선택된 캔버스 이미지
  const varySource = (() => {
    if (varyTarget) {
      const im = Object.values(wardrobe || {}).flat().find((x) => x.id === varyTarget.id);
      return im ? { id: im.id, src: im.src, cutType: im.cutType || null, wardrobeGroup: im.wardrobeGroup || varyTarget.group } : null;
    }
    return selectedElObj && selectedElObj.type === 'image'
      ? { id: selectedElObj.id, src: selectedElObj.src, cutType: selectedElObj.cutType || null, wardrobeGroup: selectedElObj.wardrobeGroup || null }
      : null;
  })();
  const setVaryCutType = (t) => {
    if (varyTarget) setWardrobe((w) => { const nw = {}; for (const [g, arr] of Object.entries(w)) nw[g] = arr.map((x) => x.id === varyTarget.id ? { ...x, cutType: t } : x); return nw; });
    else patchEl({ cutType: t });
  };
  // setBlockFocused 없이 selBlock 만 세우면 오른쪽 목록만 켜지고 캔버스는 조용하다 —
  // 캔버스 강조는 blockFocused 로 게이팅되고 그건 캔버스 클릭에서만 켜졌다.
  const jumpTo = (id) => { setSelBlock(id); setBlockFocused(true); setSelEl(null); setSelEls([]);
    const idx = blocks.findIndex((b) => b.id === id);
    const wrap = wrapRef.current; if (!wrap) return;
    const target = wrap.querySelectorAll('.canvas-block')[idx];
    if (target) { const wr = wrap.getBoundingClientRect(); const tr = target.getBoundingClientRect(); wrap.scrollTo({ top: wrap.scrollTop + (tr.top - wr.top) - 40, behavior: 'smooth' }); } };
  const addText = (bId, preset) => {
    const id = bId || visibleBlock();
    // preset 'garment' = AI 컷의 뭉갠 프린트 글자를 정확한 글자로 덮는 오버레이. 프린트는 보통
    // 크고 굵고 가운데라 기본값을 다르게 준다(색은 셀러가 원본 프린트에 맞게 조절).
    const garment = preset === 'garment';
    const el = garment
      ? { id: uid('el'), type: 'text', x: 90, y: 70, w: 480, h: 60, text: '옷 글자 입력', style: { font: 'Pretendard', size: 44, weight: 700, color: '#0e0d14', align: 'center' } }
      : { id: uid('el'), type: 'text', x: 120, y: 80, w: 420, h: 60, text: '텍스트를 입력하세요', style: { font: 'Pretendard', size: 32, weight: 500, color: '#0e0d14' } };
    setBlocks((bs) => bs.map((b) => b.id === id ? { ...b, elements: [...b.elements, el] } : b));
    selectEl(id, el); setTab('text');
    toast.push(garment ? '옷 글자를 추가했어요 — 뭉갠 글자 위로 옮겨 덮으세요' : '텍스트를 추가했어요');
  };
  /* ---- 정보 블록 (PRD §10.14 `내용 추가`) — infoPresets 빌더로 폼→블록 생성 ---- */
  const targetGenders = (analysis && analysis.targetGenders) || [];
  const recommendGender = targetGenders.length
    ? (targetGenders.every((g) => g === 'men') ? 'men' : 'women')
    : null;
  const infoCtx = buildInfoCtx({ productName, clothingType, catalogs, product, analysis, colorOpts, fmModels });
  // 특징 포인트는 폼을 열 때 빈 설명을 analysis.featureCopy 로 다시 채운다 — 생성 잡의 쓰기보다
  // 앞선 스냅샷으로 블록이 지어졌으면 그대로 두면 영구히 빈칸이다(정보 템플릿은 재적용되지 않는다).
  const seedInfo = (type, info) => (type === 'feature_icons' ? fillFeatureCopy(info, infoCtx) : info);
  // 'AI 문구 불러오기' — 서버가 강조특징마다 한 줄을 쓰고 analysis.featureCopy 에 합쳐 저장한다.
  // 여기서도 상태를 갱신해 두면 이 세션에서 블록을 새로 넣을 때 바로 프리필된다.
  const draftFeatureCopy = async () => {
    const items = await api.draftFeatureCopy(projectId);
    if (items && items.length) {
      setAnalysis((a) => {
        const merged = new Map((a?.featureCopy || []).map((c) => [c.point, c.desc]));
        items.forEach((c) => merged.set(c.point, c.desc));
        return { ...(a || {}), featureCopy: [...merged].map(([point, desc]) => ({ point, desc })) };
      });
    }
    return items;
  };
  const openInfoPreset = (type) => {
    // size/care 는 자동 블록 제자리 강화, info 는 같은 infoType 이 있으면 그 블록을 수정한다(중복 방지).
    // 단 repeatable 프리셋(특징 포인트)은 누를 때마다 새 블록 — 상세페이지는 DETAIL POINT 를 여러 벌 쓴다.
    const kind = type === 'size_table' ? 'size' : type === 'care' ? 'care' : null;
    const existing = isRepeatablePreset(type) ? null
      : (kind ? blocks.find((b) => b.kind === kind) : blocks.find((b) => presetTypeOf(b) === type));
    // 두 번째 특징 포인트 블록은 앞 블록이 쓰지 않은 강조특징부터 채운다 — 같은 포인트가
    // 두 섹션에 나란히 뜨는 것보다, 남은 특징을 이어 받는 쪽이 실제로 쓰는 순서다.
    const ctx = (isRepeatablePreset(type) && type === 'feature_icons')
      ? { ...infoCtx, sellingPoints: unusedSellingPoints() }
      : infoCtx;
    setInfoModal({ type, blockId: existing ? existing.id : null, initialInfo: seedInfo(type, existing?.info || defaultInfoFor(type, ctx)) });
  };
  const unusedSellingPoints = () => {
    const used = new Set(blocks.filter((b) => presetTypeOf(b) === 'feature_icons')
      .flatMap((b) => ((b.info && b.info.items) || []).map((it) => it.title).filter(Boolean)));
    const all = infoCtx.sellingPoints || [];
    const left = all.filter((p) => !used.has(p));
    return left.length ? left : all;   // 다 썼으면 처음부터 — 빈 폼을 주는 것보다 낫다
  };
  const addInfoPresetBlock = (type, idx) => {
    const kind = type === 'size_table' ? 'size' : type === 'care' ? 'care' : null;
    const existing = isRepeatablePreset(type) ? null
      : (kind ? blocks.find((block) => block.kind === kind) : blocks.find((block) => presetTypeOf(block) === type));
    if (existing) {
      jumpTo(existing.id);
      toast.push(`${existing.name} 블록은 이미 있어요`, { icon: 'check' });
      return;
    }
    const ctx = (isRepeatablePreset(type) && type === 'feature_icons')
      ? { ...infoCtx, sellingPoints: unusedSellingPoints() }
      : infoCtx;
    const built = buildInfoBlock(type, seedInfo(type, defaultInfoFor(type, ctx)), ctx);
    setBlocks((current) => {
      const next = [...current];
      next.splice(idx == null ? next.length : idx, 0, built);
      return next;
    });
    setSelBlock(built.id); setBlockFocused(true); setSelEl(null); setSelEls([]);
    toast.push(`${built.name} 블록을 바로 추가했어요`, { icon: 'check' });
  };
  const openInfoEdit = (block) => {
    const type = presetTypeOf(block);
    if (!type) return;
    setInfoModal({ type, blockId: block.id, initialInfo: seedInfo(type, block.info || defaultInfoFor(type, infoCtx)) });
  };
  const submitInfo = (info) => {
    const m = infoModal; if (!m) return;
    const built = buildInfoBlock(m.type, info, infoCtx);
    if (m.blockId) {
      // 슬롯에 채워 둔 사진(실측도·특징 포인트)은 재생성 후에도 이월한다
      setBlocks((bs) => bs.map((b) => (b.id === m.blockId ? { ...carrySlotImages(b.elements, built), id: b.id } : b)));
      setSelBlock(m.blockId);
      toast.push('내용을 업데이트했어요', { icon: 'check' });
    } else {
      setBlocks((bs) => {
        const n = [...bs];
        const idx = n.findIndex((b) => b.id === selBlock);
        n.splice(idx >= 0 ? idx + 1 : n.length, 0, built);
        return n;
      });
      setSelBlock(built.id);
      toast.push(`${built.name} 블록을 추가했어요`, { icon: 'check' });
    }
    setInfoModal(null);
  };
  const undo = () => { const h = hist.current; if (!h.past.length) { toast.push('되돌릴 작업이 없어요'); return; } const snap = h.past.pop(); h.future.push(prevBlocks.current); fromHistory.current = true; clearSel(); setBlocks(snap); toast.push('실행 취소', { icon: 'undo' }); };
  const redo = () => { const h = hist.current; if (!h.future.length) { toast.push('다시 실행할 작업이 없어요'); return; } const snap = h.future.pop(); h.past.push(prevBlocks.current); fromHistory.current = true; clearSel(); setBlocks(snap); toast.push('다시 실행', { icon: 'redo' }); };
  const save = async () => {
    if (genActive) {
      saveEditorWaitDraft(projectId, latestBlocks.current || blocks);
      pendingGenerationDraft.current = true;
      toast.push('생성 중 작업을 임시 저장했어요 — 나갔다 와도 이어서 볼 수 있어요');
      return;
    }
    await api.saveEditorBlocks(projectId, blocks);
    if (pendingGenerationDraft.current) {
      clearEditorWaitDraft(projectId);
      pendingGenerationDraft.current = false;
    }
    toast.push('저장했어요', { icon: 'check' });
  };
  // 이탈 직전 플러시 — 인라인 편집 중 텍스트는 blur/언마운트에 기대지 않고
  // DOM 에서 직접 읽어 합쳐 저장한다. (프로그램적 내비게이션은 blur 가 없고,
  // blur 가 있어도 언마운트 배치에선 setState 커밋이 보장되지 않는 두 구멍 커버)
  const flushExit = () => {
    let bs = latestBlocks.current;
    if (editEl && wrapRef.current && bs) {
      const node = wrapRef.current.querySelector(`[data-elid="${editEl}"]`);
      if (node) {
        const text = node.textContent;
        bs = bs.map((b) => ({ ...b, elements: b.elements.map((el) => el.id === editEl ? { ...el, text } : el) }));
        latestBlocks.current = bs;
      }
    }
    clearTimeout(saveTimer.current);
    // 생성 중 이탈 — 미완성 화면은 서버 완성본 자리에 PUT하지 않고 브라우저 임시 작업본으로
    // 저장한다. 진행 잡은 store/서버가 계속 돌며 재진입 때 이 작업본 위로 이벤트를 재생한다.
    if (genActive) {
      if (bs) saveEditorWaitDraft(projectId, bs);
      pendingGenerationDraft.current = Boolean(bs);
      return;
    }
    if (bs) api.saveEditorBlocks(projectId, bs);
  };
  const discardGenerationAndReturnToStoryboard = () => {
    clearTimeout(saveTimer.current);
    clearEditorWaitDraft(projectId);
    pendingGenerationDraft.current = false;
    genActiveRef.current = false;
    useAppStore.getState().resetDetailPageJob();
    navigate('/create/storyboard');
  };
  /* kb.current 는 crop 핸들러 정의 뒤(아래)에서 채운다 — TDZ 방지 */

  /* ---- react-moveable → Element {x,y,w,h,rotate}.
     좌표: rootContainer가 캔버스 scale을 행렬로 접어 넣음 → 델타/크기는 LOCAL 도착.
     적용: 제스처 중에는 e.target.style 에만 라이브로 쓰고(liveRef), End 에서 한 번
     상태를 커밋한다 — 매 프레임 setState→컨트롤박스 재생성이 리사이즈 제스처를
     죽이던 되먹임 루프 차단 (드래그는 타깃 노드에 붙어 살아남던 비대칭). ---- */
  const blockIdOf = (elId) => (blocks.find((b) => b.elements.some((e) => e.id === elId)) || {}).id;
  // snapX 제거됨(promote) — moveable 내장 스냅(snappable + elementGuidelines)이 대체.
  const snapDeg = (n) => { for (const t of [0, 90, 180, 270]) { const diff = ((n - t + 540) % 360) - 180; if (Math.abs(diff) <= 7) return normDeg(t); } return n; };
  const commitLive = () => {
    const lv = liveRef.current; liveRef.current = {};
    if (!Object.keys(lv).length) return;
    setBlocks((bs) => bs.map((b) => ({ ...b, elements: b.elements.map((el) => {
      const v = lv[el.id]; if (!v) return el;
      const next = { ...el, ...v };
      // 크롭된 이미지를 리사이즈하면 크롭 창도 비례 스케일 (Figma 동일)
      if (v.w != null && el.crop && el.w && el.h) {
        const kx = v.w / el.w, ky = v.h / el.h;
        next.crop = { ox: Math.round(el.crop.ox * kx), oy: Math.round(el.crop.oy * ky), iw: Math.round(el.crop.iw * kx), ih: Math.round(el.crop.ih * ky) };
      }
      return next;
    }) })));
  };
  const onMvDragStart = () => {
    gesturing.current = true; liveRef.current = {};
    const selectedBlock = blocks.find((block) => block.elements.some((element) => selEls.includes(element.id)));
    dragBlockHeight.current = selectedBlock ? getBlockRenderHeight(selectedBlock) : null;
    const o = {};
    selEls.forEach((id) => { const e = elById(id); if (e) o[id] = { x: e.x, y: e.y, w: e.w, h: e.h }; });
    dragSnap.current = o;
  };
  const onMvGestureEnd = () => { gesturing.current = false; commitLive(); dragBlockHeight.current = null; };
  const liveDrag = (target, beforeTranslate) => {
    const elId = target.dataset.elid;
    const st = dragSnap.current && dragSnap.current[elId]; if (!st) return;
    const [dx, dy] = clampDragDelta(dragSnap.current, beforeTranslate, dragBlockHeight.current);
    const nx = st.x + dx; const ny = st.y + dy;  // moveable 내장 스냅으로 beforeTranslate 는 이미 스냅된 값
    // 캔버스 밖으로 넘어가지 않게 clamp — 왼쪽 끝에서 x=0 flush(overshoot 방지), 오른쪽은 1000-w, 위(y<0)도 막음.
    // block-clip 이 어차피 넘친 부분을 자르므로 손실 없음. ("맨 왼쪽 끌면 몇 px 더 넘어가던" 문제 해결)
    target.style.left = nx + 'px'; target.style.top = ny + 'px';
    liveRef.current[elId] = { x: Math.round(nx), y: Math.round(ny) };
  };
  const onMvResizeStart = () => { gesturing.current = true; liveRef.current = {}; dragBlockHeight.current = null; const id = selEls[0]; const e = elById(id); dragSnap.current = e ? { [id]: { x: e.x, y: e.y, w: e.w, h: e.h } } : null; };
  const startPointerSelectionDrag = (block, ids, event, { threshold = 0, onDragStarted } = {}) => {
    const selected = block.elements.filter((candidate) => ids.includes(candidate.id));
    if (selected.length < 1) return false;
    const start = Object.fromEntries(selected.map((candidate) => [candidate.id, {
      x: candidate.x, y: candidate.y, w: candidate.w, h: candidate.h,
    }]));
    const startClientX = event.clientX;
    const startClientY = event.clientY;
    const pointerTarget = event.currentTarget;
    const pointerId = event.pointerId;
    const blockHeight = getBlockRenderHeight(block);
    const nodes = Object.fromEntries(ids.map((id) => [id, wrapRef.current?.querySelector(`[data-elid="${id}"]`)]));
    let active = false;
    let latest = null;
    pointerTarget?.setPointerCapture?.(pointerId);

    const move = (pointerEvent) => {
      const clientDx = pointerEvent.clientX - startClientX;
      const clientDy = pointerEvent.clientY - startClientY;
      if (!active && Math.hypot(clientDx, clientDy) < threshold) return;
      if (!active) {
        active = true;
        onDragStarted?.();
        document.body.style.userSelect = 'none';
      }
      pointerEvent.preventDefault();
      const [dx, dy] = clampDragDelta(start, [clientDx / (scale || 1), clientDy / (scale || 1)], blockHeight);
      latest = Object.fromEntries(ids.map((id) => [id, {
        x: Math.round(start[id].x + dx),
        y: Math.round(start[id].y + dy),
      }]));
      ids.forEach((id) => {
        const node = nodes[id];
        if (!node) return;
        node.style.left = latest[id].x + 'px';
        node.style.top = latest[id].y + 'px';
      });
      // 직접 선택 요소를 잡아 끄는 경로는 Moveable 이벤트를 거치지 않는다. DOM 요소만
      // 이동시키면 바깥 그룹 박스가 출발점에 남으므로, 같은 프레임에 함께 갱신한다.
      syncPointerGroupSelectionBounds();
    };
    const end = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', end);
      window.removeEventListener('pointercancel', end);
      if (pointerTarget?.hasPointerCapture?.(pointerId)) pointerTarget.releasePointerCapture(pointerId);
      document.body.style.userSelect = '';
      if (pointerGroupRectFrame.current != null) {
        window.cancelAnimationFrame(pointerGroupRectFrame.current);
        pointerGroupRectFrame.current = null;
      }
      if (!active || !latest) return;
      setBlocks((current) => current.map((item) => item.id === block.id ? {
        ...item,
        elements: item.elements.map((candidate) => latest[candidate.id]
          ? { ...candidate, ...latest[candidate.id] }
          : candidate),
      } : item));
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
    window.addEventListener('pointercancel', end);
    return true;
  };
  const startSelectedGroupDrag = (blockId, event, onDragStarted) => {
    const block = blocks.find((item) => item.id === blockId);
    if (!block) return false;
    const blockIds = new Set(block.elements.map((element) => element.id));
    const ids = selEls.filter((id) => blockIds.has(id));
    return startPointerSelectionDrag(block, ids, event, { threshold: 4, onDragStarted });
  };
  const startTextOnlyDrag = (blockId, element, event) => {
    const block = blocks.find((item) => item.id === blockId);
    if (!block || element?.type !== 'text' || element?.shape === 'bubble') return false;
    return startPointerSelectionDrag(block, [element.id], event, { threshold: 4 });
  };
  const startUnselectedObjectGroupDrag = (blockId, element, event) => {
    const block = blocks.find((item) => item.id === blockId);
    if (!block) return false;
    const ids = selectionIdsForElement(block.elements, element);
    const selected = block.elements.filter((candidate) => ids.includes(candidate.id));
    const isUnifiedBubble = element?.type === 'text' && element?.shape === 'bubble';
    if (selected.length < 2 && !isUnifiedBubble) return false;
    return startPointerSelectionDrag(block, ids, event);
  };
  const liveResize = (target, width, height, drag) => {
    const elId = target.dataset.elid;
    const st = dragSnap.current && dragSnap.current[elId]; if (!st) return;
    const elNow = elById(elId);
    const imgNode = target.querySelector('img');
    const proposed = imageResizeRect({
      start: st,
      width,
      height,
      beforeTranslate: drag?.beforeTranslate,
    });
    const { x: nx, y: ny, w: nw, h: nh } = proposed;
    const rect = clampElementRect(nx, ny, nw, nh);
    target.style.left = rect.x + 'px'; target.style.top = rect.y + 'px';
    target.style.width = rect.w + 'px'; target.style.height = rect.h + 'px';
    // 크롭된 이미지는 안쪽 원본(<img> 인라인 px)도 같은 배율로 라이브 스케일 —
    // 안 하면 틀만 커지고 사진은 제자리라 "사진이 같이 안 커지는" 것처럼 보인다.
    // (gesture end 의 commitLive 가 crop{ox,oy,iw,ih} 을 같은 비율로 커밋해 이어짐)
    if (elNow && elNow.crop && imgNode && st.w && st.h) {
      const kx = rect.w / st.w, ky = rect.h / st.h;
      imgNode.style.width = (elNow.crop.iw * kx) + 'px';
      imgNode.style.height = (elNow.crop.ih * ky) + 'px';
      imgNode.style.left = (-elNow.crop.ox * kx) + 'px';
      imgNode.style.top = (-elNow.crop.oy * ky) + 'px';
    }
    liveRef.current[elId] = { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.w), h: Math.round(rect.h) };
  };
  const liveRotate = (target, rotation) => {
    const elId = target.dataset.elid;
    const deg = Math.round(snapDeg(normDeg(rotation)));   // 무한 누적 방지: 항상 (-180,180]
    target.style.transform = deg ? `rotate(${deg}deg)` : '';
    liveRef.current[elId] = { rotate: deg };
  };

  /* ---- inline crop (Figma식, PRD §10.10) — 모달 없이 블록 안에서 ---- */
  const startCrop = (blockId, el) => {
    if (!el || el.type !== 'image' || !el.src) return;
    const c = el.crop || { ox: 0, oy: 0, iw: el.w, ih: el.h };
    clearSel();                                   // moveable 박스 → 크롭 핸들로 전환
    setCropping({ blockId, elId: el.id, src: el.src, radius: el.radius || 0,
      fx: el.x, fy: el.y, fw: el.w, fh: el.h, ...c });
  };
  const commitCrop = () => {
    setCropping((c) => {
      if (c) patchElById(c.blockId, c.elId, {
        x: Math.round(c.fx), y: Math.round(c.fy), w: Math.round(c.fw), h: Math.round(c.fh),
        crop: { ox: Math.round(c.ox), oy: Math.round(c.oy), iw: Math.round(c.iw), ih: Math.round(c.ih) },
      });
      return null;
    });
  };
  const cancelCrop = () => setCropping(null);
  // 크롭 리셋 — 프레임을 원본 이미지 전체로 되돌린다(자른 것 원위치, 오프셋 0).
  const resetCrop = () => setCropping((c) => (c ? { ...c, fx: c.fx - c.ox, fy: c.fy - c.oy, fw: c.iw, fh: c.ih, ox: 0, oy: 0 } : c));
  // 크롭 핸들·내부 이미지 드래그 — 자체 포인터 핸들러 (리사이즈와 동일하게 /scale 환산)
  const cropDrag = (e, mode, dir) => {
    if (e.button != null && e.button !== 0) return;
    e.stopPropagation(); e.preventDefault();
    const sx = e.clientX, sy = e.clientY;
    const c0 = { ...cropping };
    const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
    const move = (ev) => {
      const dx = (ev.clientX - sx) / (scale || 1), dy = (ev.clientY - sy) / (scale || 1);
      setCropping((c) => {
        if (!c) return c;
        let { fx, fy, fw, fh, ox, oy } = c0;
        const { iw, ih } = c0;
        if (mode === 'img') {                 // 내부 이미지 위치 조정 (프레임 고정)
          ox = clamp(c0.ox - dx, 0, Math.max(0, iw - fw)); oy = clamp(c0.oy - dy, 0, Math.max(0, ih - fh));
        } else {                              // 프레임 8방향 핸들 (이미지는 캔버스에 고정)
          if (dir.includes('e')) fw = clamp(c0.fw + dx, 24, iw - c0.ox);
          if (dir.includes('s')) fh = clamp(c0.fh + dy, 24, ih - c0.oy);
          if (dir.includes('w')) { const d = clamp(dx, -c0.ox, c0.fw - 24); fx = c0.fx + d; fw = c0.fw - d; ox = c0.ox + d; }
          if (dir.includes('n')) { const d = clamp(dy, -c0.oy, c0.fh - 24); fy = c0.fy + d; fh = c0.fh - d; oy = c0.oy + d; }
        }
        return { ...c, fx, fy, fw, fh, ox, oy };
      });
    };
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); document.body.style.userSelect = ''; };
    document.body.style.userSelect = 'none';
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
  };

  // keep latest action handlers for the global keyboard effect (incl. crop keys)
  kb.current = { undo, redo, save, addText, canAddText: selEls.length === 0 && !!selBlock, layer: layerEl, hasSel: !!selEl,
    croppingOn: !!cropping, cropCommit: commitCrop, cropCancel: cancelCrop };

  const TOOLS = [
    { id: 'ai', icon: 'sparkles', label: 'AI' },
    { id: 'wardrobe', icon: 'shirt', label: '의류', dot: true }, // 생성 점 표시는 결과가 쌓이는 의류 탭에
    { id: 'image', icon: 'image', label: '이미지' },
    { id: 'frame', icon: 'layout', label: '프레임' },
    { id: 'text', icon: 'type', label: '텍스트' },
    { id: 'shape', icon: 'shapes', label: '오브젝트' },
  ];
  const panelTitle = (() => {
    if (tab === 'image' && selectedElObj) {
      if (selectedElObj.type === 'shape') return '도형';
      if (selectedElObj.type === 'line') return '선';
      return '이미지';
    }
    return TOOLS.find((t) => t.id === tab)?.label;
  })();

  const renderPanel = () => {
    switch (tab) {
      case 'ai': return <AIPanel catalogs={catalogs} fmModels={fmModels} account={account} colorOpts={colorOpts} detailColorOpts={detailColorOpts} clothingType={clothingType} matchClothing={matchClothing} exampleGender={exampleGenderFromAnalysis(analysis, catalogs, clothingType)} varySource={varySource} onGenerate={generateImage} onVaryGenerate={varyGenerate} onPickRef={() => api.pickRefImage(projectId)} onPickMoodRef={() => api.pickRefImage(projectId)} onSetCutType={setVaryCutType} />;
      case 'wardrobe': return <WardrobePanel wardrobe={wardrobe} colorOpts={detailColorOpts} pendingSlot={pendingSlot} uploading={wardrobeUploadLoading} onInsert={wardrobeInsert} onDeleteImage={deleteWardrobeImage} isImageUsed={wardrobeImageInUse} onUpload={pickAndInsertImage} onVaryImage={varyImage} onFreshSeen={freshSeen}
        onImageDragStart={() => setFrameDragging(true)} onImageDragEnd={() => { setFrameDragging(false); setFrameOver(null); }} />;
      case 'image': return <ImagePanel el={selectedElObj} onChange={patchEl} onLayer={layerEl} lock={lockRatio} onLock={setLockRatio}
        onCrop={(el) => startCrop(blockIdOf(el.id), el)} onCropReset={() => patchEl({ crop: undefined })}
        onReplace={(el) => requestSlotImage(blockIdOf(el.id), el)}
        onRemove={(el) => { setSlotImage(blockIdOf(el.id), el.id, { src: null, cutType: null }); toast.push('이미지를 프레임에서 빼냈어요'); }}
        onVary={varyImage} />;
      case 'frame': return (
        <FramePanel onAdd={addFrame} recommendGender={recommendGender} onPickInfo={openInfoPreset}
          onDragStart={() => setFrameDragging(true)} onDragEnd={() => { setFrameDragging(false); setFrameOver(null); }} />
      );
      case 'text': return <TextPanel el={selectedElObj} catalogs={catalogs} onChange={patchEl} onBubbleAppearanceChange={patchBubbleAppearance} onLayer={layerEl} onAddText={() => addText()} onAddGarmentText={() => addText(undefined, 'garment')} />;
      case 'shape': return <ShapePanel catalogs={catalogs} onAdd={addShape} block={(selEls.length === 0 && selBlock) ? blocks.find((b) => b.id === selBlock) : null} onBgChange={changeBg} />;
      default: return null;
    }
  };

  // Phase 4 — space-드래그 팬 핸들러 (keydown/up effect 는 early-return 앞에 위치, Rules of Hooks)
  const startPan = (e) => {
    e.preventDefault(); e.stopPropagation();
    const wrap = wrapRef.current; if (!wrap) return;
    const sx = e.clientX, sy = e.clientY, sl = wrap.scrollLeft, st = wrap.scrollTop;
    const move = (ev) => { wrap.scrollLeft = sl - (ev.clientX - sx); wrap.scrollTop = st - (ev.clientY - sy); };
    const upp = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', upp); };
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', upp);
  };
  const consumeMarqueeClick = () => {
    if (!suppressMarqueeClick.current) return false;
    suppressMarqueeClick.current = false;
    return true;
  };
  const startMarqueeSelection = (event) => {
    if (spaceDown || preview || event.button !== 0) return;
    const wrap = wrapRef.current;
    if (!wrap) return;
    const target = event.target;
    // Figma처럼 프레임 밖 회색 작업영역에서도 시작한다. 다만 실제 요소·조작 UI를
    // 누른 제스처는 기존 선택/이동/크롭 동작에 맡긴다.
    if (!(target instanceof Element) || target.closest([
      '[data-elid]', '.moveable-control-box', '.align-bar', '.block-tools',
      '.crop-layer', 'button', 'input', 'textarea', 'select', '[contenteditable="true"]',
    ].join(','))) return;

    const startClientX = event.clientX;
    const startClientY = event.clientY;
    const additive = event.shiftKey;
    const baseSelection = additive ? [...selEls] : [];
    const blockNodes = [...wrap.querySelectorAll('.ed-canvas .canvas-block')];
    let active = false;
    let latestIds = baseSelection;
    let latestBlockId = selBlock;
    document.body.style.userSelect = 'none';

    const pointInWrap = (clientX, clientY) => {
      const rect = wrap.getBoundingClientRect();
      return {
        x: clientX - rect.left + wrap.scrollLeft,
        y: clientY - rect.top + wrap.scrollTop,
      };
    };
    const startPoint = pointInWrap(startClientX, startClientY);
    const move = (pointerEvent) => {
      const distance = Math.hypot(pointerEvent.clientX - startClientX, pointerEvent.clientY - startClientY);
      if (!active && distance < 4) return;
      if (!active) {
        active = true;
        suppressMarqueeClick.current = true;
      }
      pointerEvent.preventDefault();
      const left = Math.min(startClientX, pointerEvent.clientX);
      const top = Math.min(startClientY, pointerEvent.clientY);
      const marqueeRect = {
        left,
        top,
        right: Math.max(startClientX, pointerEvent.clientX),
        bottom: Math.max(startClientY, pointerEvent.clientY),
      };
      const hitsByBlock = blocks.map((block, index) => {
        const blockNode = blockNodes[index];
        const elementRects = new Map();
        block.elements.forEach((element) => {
          const node = blockNode?.querySelector(`[data-elid="${element.id}"]`);
          if (node) elementRects.set(element.id, node.getBoundingClientRect());
        });
        return {
          blockId: block.id,
          ids: selectionIdsInsideMarquee(block.elements, elementRects, marqueeRect),
        };
      });
      const hits = hitsByBlock.flatMap((entry) => entry.ids);
      latestIds = additive ? [...new Set([...baseSelection, ...hits])] : hits;
      latestBlockId = hitsByBlock.find((entry) => entry.ids.length)?.blockId
        || (additive && latestIds.length ? selBlock : null);
      const currentPoint = pointInWrap(pointerEvent.clientX, pointerEvent.clientY);
      setMarquee({
        left: Math.min(startPoint.x, currentPoint.x),
        top: Math.min(startPoint.y, currentPoint.y),
        width: Math.abs(currentPoint.x - startPoint.x),
        height: Math.abs(currentPoint.y - startPoint.y),
      });
      setSelBlock(latestBlockId);
      setBlockFocused(Boolean(latestBlockId));
      setSelEls(latestIds);
      setSelEl(latestIds[0] || null);
    };
    const end = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', end);
      window.removeEventListener('pointercancel', end);
      document.body.style.userSelect = '';
      setMarquee(null);
      if (!active) return;
      setSelBlock(latestBlockId);
      setBlockFocused(Boolean(latestBlockId));
      setSelEls(latestIds);
      setSelEl(latestIds[0] || null);
      setTimeout(() => { suppressMarqueeClick.current = false; }, 0);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
    window.addEventListener('pointercancel', end);
  };
  const fitToScreen = () => { const wrap = wrapRef.current; if (!wrap) return; setScale(Math.min(2, Math.max(0.1, +((wrap.clientWidth - 80) / 1000).toFixed(2)))); };
  const single = selEls.length === 1 && !editEl;
  const group = selEls.length > 1 && !editEl;
  const passGroupDragArea = group && shouldPassGroupDragArea(selEls.map((id) => elById(id)));
  const resizePolicy = resizePolicyForElement(selectedElObj, lockRatio);
  const showRotationHandle = single && shouldShowRotationHandle(selectedElObj);
  const autoHeightTextTarget = single && selectedElObj?.type === 'text' && selectedElObj.shape !== 'bubble';
  // 정렬·분배(Phase 3b) — 다중선택이 "한 블록"일 때만(좌표가 블록-상대라 cross-block 정렬 무의미).
  const groupBlockId = (() => {
    if (!group) return null;
    const bids = new Set();
    blocks.forEach((b) => b.elements.forEach((e) => { if (selEls.includes(e.id)) bids.add(b.id); }));
    return bids.size === 1 ? [...bids][0] : null;
  })();
  const alignEls = (mode) => {
    if (!groupBlockId) return;
    setBlocks((bs) => bs.map((b) => {
      if (b.id !== groupBlockId) return b;
      const sel = b.elements.filter((e) => selEls.includes(e.id));
      if (sel.length < 2) return b;
      const minX = Math.min(...sel.map((e) => e.x)), maxX = Math.max(...sel.map((e) => e.x + e.w));
      const minY = Math.min(...sel.map((e) => e.y)), maxY = Math.max(...sel.map((e) => e.y + e.h));
      const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
      const mv = (e) => {
        let { x, y } = e;
        if (mode === 'left') x = minX; else if (mode === 'centerH') x = Math.round(cx - e.w / 2); else if (mode === 'right') x = maxX - e.w;
        else if (mode === 'top') y = minY; else if (mode === 'middleV') y = Math.round(cy - e.h / 2); else if (mode === 'bottom') y = maxY - e.h;
        return { ...e, x, y };
      };
      return { ...b, elements: b.elements.map((e) => (selEls.includes(e.id) ? mv(e) : e)) };
    }));
  };
  const distributeEls = (axis) => {
    if (!groupBlockId) return;
    setBlocks((bs) => bs.map((b) => {
      if (b.id !== groupBlockId) return b;
      const sel = b.elements.filter((e) => selEls.includes(e.id));
      if (sel.length < 3) return b;
      const k = axis === 'h' ? 'x' : 'y', d = axis === 'h' ? 'w' : 'h';
      const sorted = [...sel].sort((a, c) => a[k] - c[k]);
      const start = sorted[0][k], last = sorted[sorted.length - 1];
      const totalSize = sorted.reduce((s, e) => s + e[d], 0);
      const gap = (last[k] + last[d] - start - totalSize) / (sorted.length - 1);
      const pos = {}; let cur = start;
      sorted.forEach((e) => { pos[e.id] = Math.round(cur); cur += e[d] + gap; });
      return { ...b, elements: b.elements.map((e) => (pos[e.id] != null ? { ...e, [k]: pos[e.id] } : e)) };
    }));
  };
  const genFailed = dpJob.status === 'error' || Boolean(genFinalizeError);
  const genFailureMessage = genFinalizeError || dpJob.errorMessage || '생성을 끝내지 못했어요';

  return (
    <div className="editor">
      {/* toolbar */}
      <div className="ed-toolbar">
        <button className="ed-tool" onClick={() => { flushExit(); navigate('/library'); }} title="보관함으로" style={{ flexDirection: 'row', gap: 6 }}>
          <span className="brand">
            <img className="brand-logo" src="/assets/brand/logo.svg" alt="" />
            <img className="brand-wordmark" src="/assets/brand/wordmark.png" alt="Wearless" />
          </span>
        </button>
        <div className="ed-divider" />
        <div className="ed-toolgroup">
          {TOOLS.map((t) => (
            <button key={t.id} className={`ed-tool${tab === t.id ? ' on' : ''}`} onClick={() => setTab(t.id)}>
              <span className="dotwrap"><Icon name={t.icon} size={22} />
                {t.dot && genDot !== 'none' && <span className="dot" style={{ position: 'absolute', top: -2, right: -3, background: genDot === 'busy' ? '#e6b800' : 'var(--link)', boxShadow: '0 0 0 1.5px #fff' }} />}
              </span>{t.label}
            </button>
          ))}
        </div>
        <div className="ed-doc-name" title={productName}>{productName}</div>
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          <button className="ed-tool compact" onClick={undo} title="실행 취소 (Ctrl+Z)"><Icon name="undo" size={19} />Undo</button>
          <button className="ed-tool compact" onClick={redo} title="다시 실행 (Shift+Ctrl+Z)"><Icon name="redo" size={19} />Redo</button>
          <button className={`ed-tool compact${stitched ? ' on' : ''}`} onClick={() => setStitched((v) => !v)}
            title={stitched ? '블록을 떨어뜨려 편집하기 편하게 봅니다' : '블록을 붙여 실제 상세페이지처럼 봅니다 (편집 그대로 가능)'}>
            <Icon name={stitched ? 'layers' : 'layout'} size={19} />{stitched ? '떼어보기' : '이어보기'}
          </button>
          <Button variant="ghost" size="sm" icon="eye" onClick={() => setPreview(true)}>미리보기</Button>
          <Button variant="ghost" size="sm" icon="save" onClick={save}>저장</Button>
          <Button variant="primary" size="sm" icon="download" disabled={genActive}
            onClick={() => setDownload(true)}>{genActive ? '생성 중…' : '다운로드'}</Button>
        </div>
      </div>

      {/* 생성 진행 리본 — 에디터 통합 대기. 같은 캔버스에서 편집하며 기다린다. */}
      {((genActive && dpJob.status !== 'blocked')
          || (dpJob.projectId === projectId && dpJob.status === 'error')) && (
        <div className={`ed-genbar${genFailed ? ' error' : ''}`} role="status" aria-live="polite">
          <Icon name={genFailed ? 'alertTri' : 'loader'} size={14}
            className={genFailed ? '' : 'spin'} />
          <span className="ed-genbar-msg">
            {genFailed ? genFailureMessage
              : dpJob.cutsTotal
                ? <>사진을 만들어 채우는 중 · <b>{dpJob.cutsDone}/{dpJob.cutsTotal}</b>컷</>
                : '상세페이지를 만들고 있어요'}
          </span>
          {!genFailed && (
            <span className="ed-genbar-track" aria-hidden="true"><i style={{ width: `${dpJob.progress}%` }} /></span>
          )}
          <span className="ed-genbar-side">
            {genFailed ? (
              <>
                {genFinalizeError ? (
                  <Button size="sm" variant="primary" onClick={() => setGenFinalizeAttempt((n) => n + 1)}>
                    완성본 다시 불러오기
                  </Button>
                ) : (
                  <Button size="sm" variant="primary" onClick={() => {
                    genMergedRef.current = false;
                    useAppStore.getState().resetDetailPageJob();
                    useAppStore.getState().startDetailPageGeneration(projectId);
                    genActiveRef.current = true;
                    setGenActive(true);
                  }}>다시 시도</Button>
                )}
                <Button size="sm" variant="ghost" onClick={discardGenerationAndReturnToStoryboard}>콘티로</Button>
              </>
            ) : (
              <>
                <span className="ed-genbar-hint">지금도 문구·배치를 고칠 수 있어요 · 창을 닫아도 계속 만들어져요</span>
                {genNotif === 'default' && (
                  <button type="button" className="ed-genbar-notify"
                    onClick={async () => setGenNotif(await Notification.requestPermission())}>완료되면 알림 받기</button>
                )}
                {genNotif === 'granted' && <span className="ed-genbar-on"><Icon name="check" size={11} />알림 켜짐</span>}
              </>
            )}
          </span>
        </div>
      )}

      {/* FaceMarket 차단(409) — 생성이 라이선스 게이트에서 멈춤 */}
      {dpJob.projectId === projectId && dpJob.status === 'blocked' && (
        <div className="ed-genoverlay">
          <div className="surface fm-blocked">
            <div className="fm-blocked-icon"><Icon name="alertCircle" size={28} /></div>
            <p className="fm-blocked-msg">{dpJob.errorMessage}</p>
            <p className="fm-blocked-hint">다른 모델을 선택하거나 라이선스 상태를 확인한 뒤 다시 시도해 주세요.</p>
            <Button variant="primary" block onClick={discardGenerationAndReturnToStoryboard}>콘티로 돌아가기</Button>
          </div>
        </div>
      )}
      {/* FaceMarket 온체인 정산 영수증(장면③) — 완료 병합 후 모달로 */}
      {genReceipt && (
        <div className="ed-genoverlay" onClick={() => setGenReceipt(null)}>
          <div className="surface fm-receipt" onClick={(e) => e.stopPropagation()}>
            <div className="fm-receipt-head">
              <span className="fm-receipt-badge"><Icon name="check" size={13} />정산 완료</span>
              <span className="fm-receipt-total">{wonFmt(genReceipt.totalAmount)}</span>
            </div>
            <div className="fm-split">
              <div className="fm-split-row"><span>모델 정산</span><span>{wonFmt(genReceipt.modelAmount)}</span></div>
              <div className="fm-split-row"><span>플랫폼</span><span>{wonFmt(genReceipt.platformAmount)}</span></div>
              <div className="fm-split-row"><span>운영</span><span>{wonFmt(genReceipt.opsAmount)}</span></div>
            </div>
            <Button variant="primary" block onClick={() => setGenReceipt(null)}>확인하고 편집 계속하기</Button>
          </div>
        </div>
      )}

      {/* body */}
      <div className="ed-body" style={{ '--lcol': '320px', '--rcol': rightHidden ? '0px' : '208px' }}>
        <div className="ed-left">
          <div style={{ marginBottom: 14 }}><span className="panel-h" style={{ marginBottom: 0 }}>{panelTitle}</span></div>
          {renderPanel()}
        </div>

        <div className={`ed-canvas-wrap${spaceDown ? ' panning' : ''}`} ref={wrapRef}
          onPointerDown={(e) => { if (spaceDown) startPan(e); else startMarqueeSelection(e); }}
          onClick={(e) => { if (consumeMarqueeClick() || spaceDown || !shouldClearEditorSelection(e.target)) return; if (cropping) { commitCrop(); return; } clearSel(); setBlockFocused(false); }}
          onScroll={() => moveableRef.current?.updateRect()}
          onMouseMove={(e) => { const g = isEditorGrayWorkspaceTarget(e.target); setHoverGray((v) => v === g ? v : g); }}
          onMouseLeave={() => setHoverGray(false)}>
          <div className={`zoom-float${hoverGray ? ' show' : ''}`}>
            <div className="zoom-pill" onClick={(e) => e.stopPropagation()} onMouseMove={(e) => e.stopPropagation()}>
              <button onClick={() => setScale((s) => Math.max(0.1, +(s - 0.1).toFixed(2)))}><Icon name="minus" size={15} /></button>
              <span>{Math.round(scale * 100)}%</span>
              <button onClick={() => setScale((s) => Math.min(2, +(s + 0.1).toFixed(2)))}><Icon name="plus" size={15} /></button>
              <span className="zoom-div" />
              <button className="zoom-fit" onClick={fitToScreen} title="화면 너비에 맞춤">맞춤</button>
            </div>
          </div>
          {rightHidden && <div style={{ position: 'absolute', right: 10, top: 10, zIndex: 3 }}><IconButton name="layout" size="sm" onClick={() => setRightHidden(false)} /></div>}
          {marquee && <div className="selection-marquee" style={marquee} aria-hidden="true" />}
          {groupBlockId && (
            <div className="align-bar" onPointerDown={(e) => e.stopPropagation()} onClick={(e) => e.stopPropagation()}>
              <button aria-label="왼쪽 정렬" title="왼쪽 정렬" onClick={() => alignEls('left')}>⇤</button>
              <button aria-label="가로 가운데 정렬" title="가로 가운데 정렬" onClick={() => alignEls('centerH')}>⇔</button>
              <button aria-label="오른쪽 정렬" title="오른쪽 정렬" onClick={() => alignEls('right')}>⇥</button>
              <span className="align-sep" />
              <button aria-label="위 정렬" title="위 정렬" onClick={() => alignEls('top')}>⤒</button>
              <button aria-label="세로 가운데 정렬" title="세로 가운데 정렬" onClick={() => alignEls('middleV')}>⇕</button>
              <button aria-label="아래 정렬" title="아래 정렬" onClick={() => alignEls('bottom')}>⤓</button>
              <span className="align-sep" />
              <button aria-label="가로 균등 분배" title="가로 균등 분배 (3개+)" onClick={() => distributeEls('h')} disabled={selEls.length < 3}>⇿</button>
              <button aria-label="세로 균등 분배" title="세로 균등 분배 (3개+)" onClick={() => distributeEls('v')} disabled={selEls.length < 3}>⇳</button>
            </div>
          )}
          {/* CSS `zoom` is invisible to react-moveable (it only reads the transform
              matrix) — scale via transform instead. transform doesn't take layout
              space, so a spacer reserves the SCALED dimensions for scrolling. */}
          <div style={{ position: 'relative', width: 1000 * scale, height: canvasH * scale, margin: '40px auto' }}>
          <div className={`ed-canvas${frameDragging ? ' frame-dragging' : ''}${stitched ? ' stitched' : ''}`} ref={canvasRef}
            style={{ transform: `scale(${scale})`, transformOrigin: 'top left', position: 'absolute', top: 0, left: 0, margin: 0, '--canvas-inv': 1 / (scale || 1) }}>
            {blocks.map((b, i) => (
              <div key={b.id} style={{ display: 'contents' }}>
                <div className="canvas-droprow" onDragOver={(e) => { if (acceptsEditorBlockInsert(e.dataTransfer.types)) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; setFrameOver(i); } }}
                  onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setFrameOver((o) => o === i ? null : o); }} onDrop={(e) => onCanvasInsertDrop(e, i)}>
                  <div className={`canvas-dropline${frameOver === i ? ' on' : ''}`} />
                </div>
                <CanvasBlock block={b} scale={scale} idx={i} imageImports={imageImports.filter((item) => item.blockId === b.id)}
                  selectedBlockId={blockFocused ? selBlock : null} selEls={selEls} editEl={editEl} onEdit={setEditEl}
                  crop={cropping && cropping.blockId === b.id ? cropping : null}
                  onCropDrag={cropDrag} onCropStart={startCrop} onCropCommit={commitCrop} onCropCancel={cancelCrop} onCropReset={resetCrop}
                  onSelectBlock={(id) => { setSelBlock(id); setBlockFocused(true); clearSel(); setTab('shape'); }} onSelectEl={selectEl}
                  onElPatch={patchElById} onTextCommit={commitBubbleText} onMultiDragStart={startSelectedGroupDrag}
                  onTextDragStart={startTextOnlyDrag}
                  onObjectGroupDragStart={startUnselectedObjectGroupDrag}
                  shouldSuppressBlankClick={consumeMarqueeClick}
                  onAddImage={requestSlotImage} onDropImage={dropSlotImage}
                  onDropBlockImage={(blockId, image, point) => insertImage(image, { blockId, point })} onDropImageFiles={dropImageFiles}
                  onOpenLayers={(id) => { setLayerFloat(id); setLayerPos(null); }}
                  onObjectDrop={(bid, type, id, ev) => addShape(type, id, bid, ev)} onReshape={reshapeBlock}
                  onMove={moveBlock} onAddEmpty={addEmpty} onDelete={deleteBlock} onEditInfo={openInfoEdit}
                  onDownload={() => toast.push('이 블록을 PNG로 저장했어요', { icon: 'download' })} />
              </div>
            ))}
            <div className="canvas-droprow" onDragOver={(e) => { if (acceptsEditorBlockInsert(e.dataTransfer.types)) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; setFrameOver(blocks.length); } }}
              onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setFrameOver((o) => o === blocks.length ? null : o); }} onDrop={(e) => onCanvasInsertDrop(e, blocks.length)}>
              <div className={`canvas-dropline${frameOver === blocks.length ? ' on' : ''}`} />
            </div>
            {/* Phase0 스파이크: 캔버스 세로 센티넬(좌40/중앙500/우960) — elementGuidelines 소스.
                zero-width·투명, .ed-canvas(언스케일 좌표) 안이라 x 는 언스케일 px. */}
            {SNAP_SPIKE && [40, 500, 960].map((x) => (
              <div key={`snap-sentinel-${x}`} data-snap-sentinel style={{ position: 'absolute', left: x, top: 0, width: 0, height: canvasH || 4000, pointerEvents: 'none', opacity: 0 }} />
            ))}

          </div>
          </div>

          {/* react-moveable — rendered OUTSIDE the scaled canvas (a scaled ancestor
              would shrink the control box itself, pinning it to the top-left);
              rootContainer = the untransformed scroll wrapper so the canvas scale
              is folded into moveable's coordinate math */}
          {mvTargets.length > 0 && (
            <Moveable
              ref={moveableRef}
              className={autoHeightTextTarget ? 'moveable-auto-text' : undefined}
              target={mvTargets}
              rootContainer={wrapRef.current}
              // 일반 합성 오브젝트는 자식 레이어 선택을 위해 투과한다. 완성된 말풍선끼리의
              // 그룹은 드래그 영역이 포인터를 직접 받아야 선택 후 바로 이동할 수 있다.
              passDragArea={passGroupDragArea}
              preventClickEventOnDrag
              draggable
              resizable={single}
              rotatable={showRotationHandle}
              keepRatio={resizePolicy.keepRatio}
              {...(SNAP_SPIKE ? {
                snappable: true,
                elementGuidelines: mvGuides,
                snapDirections: { top: true, left: true, bottom: true, right: true, center: true, middle: true },
                elementSnapDirections: { top: true, left: true, bottom: true, right: true, center: true, middle: true },
                snapGap: true,
                snapHorizontalThreshold: 8,
                snapVerticalThreshold: 8,
                isDisplaySnapDigit: false,   // 스냅 거리 숫자(px) 표시 안 함 — 가이드선만
              } : {})}
              renderDirections={resizePolicy.directions}
              origin={false}
              throttleDrag={0}
              throttleResize={0}
              throttleRotate={0}
              onDragStart={onMvDragStart}
              onDrag={(e) => liveDrag(e.target, e.beforeTranslate)}
              onDragEnd={onMvGestureEnd}
              onDragGroupStart={onMvDragStart}
              onDragGroup={(e) => e.events.forEach((ev) => liveDrag(ev.target, ev.beforeTranslate))}
              onDragGroupEnd={onMvGestureEnd}
              onResizeStart={onMvResizeStart}
              onResize={(e) => liveResize(e.target, e.width, e.height, e.drag)}
              onResizeEnd={onMvGestureEnd}
              onRotateStart={onMvDragStart}
              onRotate={(e) => liveRotate(e.target, e.rotation)}
              onRotateEnd={onMvGestureEnd}
            />
          )}
        </div>

        {!rightHidden && <MiniPreview blocks={blocks} selectedBlockId={selBlock} onJump={jumpTo} onReorder={reorderBlock} />}

        {layerFloat && blocks.find((b) => b.id === layerFloat) && (
          <div className="layer-float" style={layerPos ? { left: layerPos.x, top: layerPos.y, right: 'auto' } : undefined}>
            <div className="lf-head">
              <Icon name="gripV" size={14} className="lf-grip" /><Icon name="layers" size={15} /><span>레이어</span>
              <IconButton name="x" size="sm" onClick={() => setLayerFloat(null)} />
            </div>
            <div className="lf-body">
              <LayerPanel embedded block={blocks.find((b) => b.id === layerFloat)} selEls={selEls}
                onSelect={(bid, el) => selectEl(bid, el, false, true)} onReorder={reorderLayer} onToggle={toggleElField} />
            </div>
          </div>
        )}
      </div>

      {/* preview overlay */}
      {preview && (
        <div className="preview-full">
          <div className="preview-close"><IconButton name="x" onClick={() => setPreview(false)} /></div>
          <div className="preview-sheet">
            {blocks.map((b) => (
              <div key={b.id} style={{ position: 'relative', height: getBlockRenderHeight(b), background: colorWithOpacity(b.bg, b.bgOpacity), overflow: 'hidden', boxSizing: 'border-box' }}>
                {b.elements.map((el) => <CanvasElement key={el.id} el={el} preview selected={false} onSelect={() => {}} onEdit={() => {}} />)}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* download modal */}
      {download && (
        <Modal onClose={() => setDownload(false)} wide>
          <div className="dl-modal">
            <div className="dl-head">
              <div className="dl-eyebrow">다운로드</div>
              <h3 className="dl-title">상세페이지를 내보내기</h3>
              <p className="dl-sub">형식과 해상도를 고르면 바로 저장돼요.</p>
            </div>
            <div className="dl-opts">
              {catalogs.downloadOptions.map((o) => {
                const on = dlFormat === o.id;
                return (
                  <button key={o.id} className={`dl-opt${on ? ' on' : ''}`} onClick={() => setDlFormat(o.id)}>
                    <span className="dl-opt-ico"><Icon name={o.id === 'zip' ? 'layers' : 'image'} size={20} /></span>
                    <span className="dl-opt-meta">
                      <span className="dl-opt-title">{o.title}</span>
                      <span className="dl-opt-desc">{o.desc}</span>
                    </span>
                    <span className={`dl-radio${on ? ' on' : ''}`}>{on && <Icon name="check" size={13} />}</span>
                  </button>
                );
              })}
            </div>
            <div className="dl-foot">
              <Button variant="quiet" onClick={() => setDownload(false)}>취소</Button>
              <Button variant="primary" icon="download" onClick={() => { setDownload(false); toast.push('다운로드를 시작했어요', { icon: 'download' }); }}>다운로드</Button>
            </div>
          </div>
        </Modal>
      )}

      {backWarn && (
        <Modal onClose={() => setBackWarn(false)}>
          <h3>초안 단계로 돌아갈 수 없어요</h3>
          <p>이미 생성이 완료된 상세페이지입니다. 필요한 컷은 이 페이지에서 추가하거나 수정해주세요.</p>
          <div className="modal-actions"><Button variant="primary" onClick={() => setBackWarn(false)}>확인</Button></div>
        </Modal>
      )}

      {/* 정보 블록 입력 폼 (PRD §10.14) */}
      {infoModal && (
        <InfoBlockModal type={infoModal.type} initialInfo={infoModal.initialInfo} ctx={infoCtx}
          wardrobe={wardrobe} colorOpts={colorOpts}
          editing={!!infoModal.blockId} onClose={() => setInfoModal(null)} onSubmit={submitInfo}
          onDraftCopy={draftFeatureCopy} />
      )}
    </div>
  );
}

export default Editor;
