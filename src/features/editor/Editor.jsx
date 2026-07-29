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
import { listModels } from '@/lib/api/facemarket.js';
import { uid } from '@/lib/ids.js';
import { useAppStore } from '@/store/useAppStore.js';
import { Icon, IconButton, Button, Modal, EmptyState, useToast } from '@/components/ui.jsx';
import { hexFor } from '@/features/storyboard/Storyboard.jsx';
import { AIPanel, WardrobePanel, ImagePanel, TextPanel, FramePanel, ShapePanel, LayerPanel } from '@/features/editor/EditorPanels.jsx';
import { ContentPanel } from '@/features/editor/ContentPanel.jsx';
import { InfoBlockModal } from '@/features/editor/InfoBlockModal.jsx';
import { applyInfoTemplate, applySlotFillToInfo, buildInfoBlock, carrySlotImages, defaultInfoFor, presetTypeOf } from '@/features/editor/presets/infoPresets.js';
import { SHAPE_D } from '@/features/editor/shapes.js';
import { clampDragDelta, clampElementRect, expandBlockHeights, getBlockRenderHeight } from '@/features/editor/editorGeometry.js';
import { CONTENT_ROLES, SECTION_ROLES, hasDetailSource, normalizeEditorBlockRole } from '@/lib/storyboardTaxonomy.js';

const FONT_MAP = { 'Cal Sans': 'var(--font-display)', 'Roboto Mono': 'var(--font-mono)', 'Pretendard': 'var(--font-body)', 'Cormorant': 'var(--font-serif)' };

/* 스냅 엔진 상시 on (Phase 1 정식 승격) — react-moveable 내장 snap(elementGuidelines + 캔버스 센티넬).
   DEV 게이트 제거: prod 배포에서도 동작. 옛 커스텀 snapX 는 삭제됨. */
const SNAP_SPIKE = true;

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

/* render-only element (selection + inline text edit). Manipulation handled by
   the single <Moveable> in the Editor (targets the selected element node). */
function CanvasElement({ el, blockId, selected, editing, scale, preview, onSelect, onPatch, onAddImage, onEdit, onCropStart }) {
  const ref = useRef(null);
  if (el.hidden) return null;

  const pick = (e) => {
    if (preview) return;
    if (el.locked) return;
    e.stopPropagation();
    onSelect(el, e.shiftKey);
  };

  const base = {
    left: el.x, top: el.y, width: el.w, height: el.h,
    transform: el.rotate ? `rotate(${el.rotate}deg)` : undefined, opacity: el.opacity ?? 1,
    pointerEvents: el.locked ? 'none' : undefined,
  };
  const cls = (extra) => `el${extra ? ' ' + extra : ''}${selected ? ' on' : ''}`;
  const common = { ref, 'data-elid': el.id, onPointerDown: pick, onClick: (e) => e.stopPropagation() };

  if (el.type === 'image') {
    if (!el.src) {
      const inv = 1 / (scale || 1);
      // 빈 슬롯도 radius 를 따른다 — 특징 포인트의 원형 사진 슬롯이 원으로 보이게
      const slotBase = { ...base, borderRadius: el.radius };
      if (preview) return <div className="el el-slot" style={slotBase} />;
      return (
        <div {...common} className={cls('el-slot')} style={slotBase}>
          <button className="slot-add" style={{ transform: `scale(${inv})` }}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => { e.stopPropagation(); onAddImage && onAddImage(el); }}>
            <Icon name="plus" size={20} /><span>이미지 추가</span>
          </button>
        </div>
      );
    }
    return (
      <div {...common} className={cls()} style={base}
        onDoubleClick={preview ? undefined : (e) => { e.stopPropagation(); onCropStart && onCropStart(el); }}>
        {el.crop ? (
          /* 커밋된 인라인 크롭: 프레임(overflow hidden) 안에 원본을 -ox,-oy 오프셋으로 */
          <div className="el-cropped" style={{ borderRadius: el.radius }}>
            <img src={el.src} alt="" draggable={false} style={{ left: -el.crop.ox, top: -el.crop.oy, width: el.crop.iw, height: el.crop.ih }} />
          </div>
        ) : (
          <img src={el.src} alt="" style={{ borderRadius: el.radius }} draggable={false} />
        )}
      </div>
    );
  }
  if (el.type === 'text') {
    const s = el.style || {};
    const lines = (el.text || '').split('\n');
    const display = (!s.list || s.list === 'none') ? el.text
      : lines.map((ln, i) => (s.list === 'ordered' ? `${i + 1}. ` : '• ') + ln).join('\n');
    const hasBg = s.bg && s.bg !== 'none';
    return (
      <div ref={ref} data-elid={el.id} className={`el el-text${selected ? ' on' : ''}${editing ? ' editing' : ''}`} style={{ ...base, height: 'auto',
        fontFamily: FONT_MAP[s.font] || 'var(--font-body)', fontSize: s.size, fontWeight: s.weight || 400,
        color: s.color || '#0e0d14', letterSpacing: s.tracking, textAlign: s.align || 'left',
        lineHeight: s.lineHeight ? s.lineHeight + 'px' : 1.4, whiteSpace: 'pre-wrap', opacity: (el.opacity ?? 1) * (s.opacity ?? 1),
        background: hasBg ? s.bg : undefined, padding: hasBg ? '2px 8px' : undefined, borderRadius: hasBg ? 4 : undefined,
        fontStyle: s.italic ? 'italic' : 'normal',
        textDecoration: [s.underline && 'underline', s.strike && 'line-through'].filter(Boolean).join(' ') || 'none' }}
        onPointerDown={(e) => { if (!editing) pick(e); }}
        onClick={(e) => e.stopPropagation()}
        onDoubleClick={(e) => { e.stopPropagation(); onEdit(el.id); setTimeout(() => ref.current && ref.current.focus(), 0); }}
        contentEditable={editing} suppressContentEditableWarning
        onBlur={(e) => { onEdit(null); onPatch(blockId, el.id, { text: e.currentTarget.textContent }); }}>
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
      <path d={SHAPE_D[el.shape]} fill={fill} stroke={sc || 'none'} strokeWidth={sw} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
  else {
    const my = el.h / 2; const lc = el.stroke && el.stroke !== 'none' ? el.stroke : (el.fill || '#0e0d14'); const lw = el.strokeWidth || 2.5;
    const dashMap = { dotted: '3 5', dashed: '12 9', solid: '' };
    const da = dashMap[el.dash || 'solid'] || undefined;
    inner = (
      <svg width="100%" height="100%" viewBox={`0 0 ${el.w} ${el.h}`} style={{ overflow: 'visible', display: 'block' }}>
        <line x1={el.shape === 'arrow-l' ? 12 : 0} y1={my} x2={el.shape === 'arrow-r' ? el.w - 12 : el.w} y2={my} stroke={lc} strokeWidth={lw} strokeLinecap="round" strokeDasharray={da} />
        {el.shape === 'arrow-l' && <polyline points={`14,${my - 8} 2,${my} 14,${my + 8}`} fill="none" stroke={lc} strokeWidth={lw} strokeLinecap="round" strokeLinejoin="round" />}
        {el.shape === 'arrow-r' && <polyline points={`${el.w - 14},${my - 8} ${el.w - 2},${my} ${el.w - 14},${my + 8}`} fill="none" stroke={lc} strokeWidth={lw} strokeLinecap="round" strokeLinejoin="round" />}
      </svg>
    );
  }
  return <div {...common} className={cls()} style={base}>{inner}</div>;
}

function CanvasBlock({ block, scale, selectedBlockId, selEls, onSelectBlock, onSelectEl, onElPatch, onAddImage, onOpenLayers, onObjectDrop, onReshape, onMove, onAddEmpty, onDelete, onDownload, onEditInfo, editEl, onEdit, crop, onCropDrag, onCropStart, onCropCommit, onCropReset, idx }) {
  // 블록 높이는 콘텐츠보다 작아지지 않는다 — 이미지를 블록보다 크게 리사이즈하면 블록도 따라 커져 클립 방지.
  // (기존: block.h 있으면 고정 → 이미지 키워도 block-clip 이 잘라 "안 커보이던" 버그)
  const blockH = getBlockRenderHeight(block);
  const blockSelected = selectedBlockId === block.id && (!selEls || selEls.length === 0);
  const [objOver, setObjOver] = useState(false);

  const resize = (e, side) => {
    e.stopPropagation(); e.preventDefault();
    if (e.button != null && e.button !== 0) return;
    const sy = e.clientY, startH = blockH;
    const startEls = block.elements.map((el) => ({ id: el.id, y: el.y }));
    const move = (ev) => {
      const dy = (ev.clientY - sy) / (scale || 1);
      if (side === 'bottom') { onReshape(block.id, { h: Math.max(120, Math.round(startH + dy)) }); }
      else {
        const nh = Math.max(120, Math.round(startH - dy));
        const delta = nh - startH;
        const shiftEls = {};
        startEls.forEach((s) => { shiftEls[s.id] = Math.round(s.y + delta); });
        onReshape(block.id, { h: nh, shiftEls });
      }
    };
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); document.body.style.userSelect = ''; };
    document.body.style.userSelect = 'none';
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
  };

  return (
    <div className={`canvas-block${blockSelected ? ' on' : ''}${objOver ? ' obj-over' : ''}`}
      onClick={(e) => { if (e.target === e.currentTarget || e.target.classList.contains('block-clip')) onSelectBlock(block.id); }}
      style={{ background: block.bg, height: blockH, '--inv': 1 / (scale || 1) }}
      onDragOver={(e) => { if (e.dataTransfer.types.includes('text/object')) { e.preventDefault(); setObjOver(true); } }}
      onDragLeave={() => setObjOver(false)}
      onDrop={(e) => { const d = e.dataTransfer.getData('text/object'); if (d) { e.preventDefault(); setObjOver(false); const [type, id] = d.split(':'); onObjectDrop(block.id, type, id, e); } }}>
      <div className="block-clip">
        {block.elements.map((el) => (
          (crop && crop.elId === el.id) ? null : (
            <CanvasElement key={el.id} el={el} blockId={block.id} scale={scale} preview={false}
              selected={selEls && selEls.includes(el.id)} editing={editEl === el.id}
              onSelect={(e, additive) => onSelectEl(block.id, e, additive)} onPatch={onElPatch}
              onAddImage={(elm) => onAddImage(block.id, elm)} onEdit={onEdit}
              onCropStart={(elm) => onCropStart && onCropStart(block.id, elm)} />
          )
        ))}
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
              <img src={crop.src} alt="" draggable={false} style={{ left: -crop.ox, top: -crop.oy, width: crop.iw, height: crop.ih }} />
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
            <div className="crop-bar" style={{ left: crop.fx, top: crop.fy + crop.fh }} onPointerDown={(e) => e.stopPropagation()} onClick={(e) => e.stopPropagation()}>
              <button className="crop-reset" onClick={(e) => { e.stopPropagation(); onCropReset && onCropReset(); }}>원본</button>
              <span className="crop-hint">안쪽 드래그 사진 이동 · 휠 확대 · 모서리 영역 조절</span>
              <span className="crop-hint quiet">Enter 확정 · Esc 취소</span>
            </div>
          </div>
        )}
      </div>
      {blockSelected && (
        <>
          <span className="blk-resize top" onPointerDown={(e) => resize(e, 'top')} title="위로 높이 조절"><span className="pill-bar" /></span>
          <span className="blk-resize bottom" onPointerDown={(e) => resize(e, 'bottom')} title="아래로 높이 조절"><span className="pill-bar" /></span>
        </>
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
            <div className="mini-canvas" style={{ background: b.bg, aspectRatio: `1000 / ${blockH}` }} title={b.name}
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
  const setBlocks = useCallback((u) => setBlocksState((prev) => {
    const next = expandBlockHeights(typeof u === 'function' ? u(prev) : u);
    latestBlocks.current = next;
    return next;
  }), []);
  const [wardrobe, setWardrobe] = useState(null);
  const [varyTarget, setVaryTarget] = useState(null); // 의류 탭 'AI 편집'으로 지정한 변형 대상 { id } — 캔버스 선택이 바뀌면 해제
  const [catalogs, setCatalogs] = useState(null);
  const [fmModels, setFmModels] = useState(null); // FaceMarket 검증 모델(실존) — http 모드에서만 로드, 실패=null(가상 폴백)
  const [colorOpts, setColorOpts] = useState([]);
  const [detailColorOpts, setDetailColorOpts] = useState([]);
  const [clothingType, setClothingType] = useState('top'); // 샷 필터 아이콘·예시 크롭용 (계약 §3.1)
  const [hasDetailImage, setHasDetailImage] = useState(false);
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
  const [download, setDownload] = useState(false);
  const [dlFormat, setDlFormat] = useState('long');
  const [backWarn, setBackWarn] = useState(false);
  const [genDot, setGenDot] = useState('none');
  const genCount = useRef(0); // 동시 생성 수 — 주황(busy) 점은 마지막 생성이 끝날 때까지 유지
  const [frameOver, setFrameOver] = useState(null);
  const [frameDragging, setFrameDragging] = useState(false);
  const [pendingSlot, setPendingSlot] = useState(null);
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
  const dragSnap = useRef(null);                   // start coords during a moveable gesture
  const gesturing = useRef(false);                 // moveable 제스처 진행 중 — 상태 커밋/updateRect 금지
  const liveRef = useRef({});                      // elId → 라이브 적용값 (gesture end에 한 번 커밋)
  const toast = useToast();
  const wrapRef = useRef(null);
  const canvasRef = useRef(null);                  // unscaled-layout canvas (transform: scale)
  const moveableRef = useRef(null);                // for updateRect() on selection/layout change
  const [canvasH, setCanvasH] = useState(0);       // unscaled canvas height → scaled spacer
  const hist = useRef({ past: [], future: [] });
  const prevBlocks = useRef(null);
  const fromHistory = useRef(false);
  const lastPush = useRef(0);

  useEffect(() => {
    // 에디터는 앱 크롬 밖에서 열린다 — account 는 store 캐시를 직접 로드 (단일 소스)
    Promise.all([api.getEditorBlocks(projectId), api.getWardrobe(projectId), api.getCatalogs(), useAppStore.getState().loadAccount(), api.getProduct(projectId),
      // 실존 모델 카탈로그 — mock 모드는 서버가 없으니 스킵, 실패는 null(AIPanel 이 가상모델 폴백)
      isMockMode ? Promise.resolve(null) : listModels().catch(() => null),
      // 분석 컨텍스트 — 정보 블록 프리필·추천 배지 전용(실패해도 에디터는 뜬다)
      api.getAnalysis(projectId).catch(() => null)])
      .then(([b, w, c, _a, p, fm, an]) => {
        const withH = b.map((blk) => normalizeEditorBlockRole(blk));
        setBlocks(withH); setWardrobe(w); setCatalogs(c); setFmModels(fm); setSelBlock(withH[0]?.id);
        setProductName(p.name || '제목 없는 상세페이지');
        setClothingType(p.clothingType || 'top');
        setHasDetailImage(hasDetailSource(p));
        setProduct(p); setAnalysis(an);
        const allColorOpts = (p.colors || []).map((col) => ({ id: col.id, label: col.name || '색상', hex: hexForCol(col) }));
        const opts = allColorOpts.filter((_option, index) => (p.colors[index].images || []).length || p.colors[index].isBase);
        setDetailColorOpts(allColorOpts.length ? allColorOpts : [{ id: 'col1', label: '기본', hex: '#15141a' }]);
        setColorOpts(opts.length ? opts : [{ id: 'col1', label: '기본', hex: '#15141a' }]);
      });
  }, []);

  // history (rapid bursts within 350ms coalesce)
  useEffect(() => {
    if (blocks == null) return;
    if (prevBlocks.current == null) { prevBlocks.current = blocks; return; }
    if (fromHistory.current) { fromHistory.current = false; prevBlocks.current = blocks; return; }
    const now = Date.now();
    if (now - lastPush.current > 350) { hist.current.past.push(prevBlocks.current); if (hist.current.past.length > 80) hist.current.past.shift(); hist.current.future = []; }
    lastPush.current = now; prevBlocks.current = blocks;
  }, [blocks]);

  // 자동 저장 — 변경 1.5s 디바운스 + 이탈 시 플러시 (frontend_state_model §8 P1-6).
  // 첫 로드 직후 1회는 방금 불러온 동일 데이터 재기록이라 무해.
  const saveTimer = useRef(null);
  useEffect(() => {
    if (blocks == null) return;
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => { api.saveEditorBlocks(projectId, latestBlocks.current); }, 1500);
  }, [blocks]);
  useEffect(() => () => {
    clearTimeout(saveTimer.current);
    if (latestBlocks.current) api.saveEditorBlocks(projectId, latestBlocks.current);
  }, []);

  // delete key removes selection
  useEffect(() => {
    const h = (e) => {
      if ((e.key === 'Delete' || e.key === 'Backspace') && selEls.length) {
        const t = e.target;
        if (/input|textarea/i.test(t.tagName) || t.isContentEditable) return;
        e.preventDefault();
        setBlocks((bs) => bs.map((b) => ({ ...b, elements: b.elements.filter((el) => !selEls.includes(el.id)) })));
        setSelEl(null); setSelEls([]);
        toast.push(`${selEls.length > 1 ? selEls.length + '개 요소를' : '요소를'} 삭제했어요`, { icon: 'trash' });
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
          const snapshot = Object.fromEntries(bs.flatMap((b) => b.elements.filter((el) => selEls.includes(el.id)).map((el) => [el.id, el])));
          const [moveX, moveY] = clampDragDelta(snapshot, [dx, dy]);
          return bs.map((b) => ({ ...b, elements: b.elements.map((el) => (selEls.includes(el.id)
            ? { ...el, x: (el.x || 0) + moveX, y: (el.y || 0) + moveY } : el)) }));
        });
      }
    };
    window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h);
  }, [selEls]);

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
    setSelBlock(blockId); setSelEl(el.id);
    setSelEls((cur) => additive ? (cur.includes(el.id) ? cur.filter((x) => x !== el.id) : [...cur, el.id]) : [el.id]);
    if (!keepTab) setTab(el.type === 'text' ? 'text' : 'image');
  };
  const clearSel = () => { setSelEl(null); setSelEls([]); setVaryTarget(null); };
  const patchEl = (patch) => setBlocks((bs) => bs.map((b) => ({ ...b, elements: b.elements.map((e) => e.id === selEl ? { ...e, ...patch } : e) })));
  const patchElById = (blockId, elId, patch) => setBlocks((bs) => bs.map((b) => b.id === blockId ? { ...b, elements: b.elements.map((e) => e.id === elId ? { ...e, ...patch } : e) } : b));
  const changeBg = (blockId, color) => setBlocks((bs) => bs.map((b) => b.id === blockId ? { ...b, bg: color } : b));
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
  const addEmpty = (idx) => setBlocks((bs) => { const n = [...bs]; const nb = { id: uid('b'), name: '직접 구성', kind: SECTION_ROLES.FIT, contentRole: CONTENT_ROLES.CUSTOM, bg: '#ffffff', h: 300, elements: [] }; n.splice(idx + 1, 0, nb); return n; });
  const deleteBlock = (id) => { setBlocks((bs) => bs.filter((b) => b.id !== id)); toast.push('블록을 삭제했어요'); };
  const addFrame = (f, idx) => {
    const nb = { id: uid('b'), name: f.label, kind: SECTION_ROLES.FIT, contentRole: CONTENT_ROLES.CUSTOM, bg: '#ffffff', h: 580, elements:
      Array.from({ length: f.cols }).map((_, i) => ({ id: uid('el'), type: 'image', x: 40 + i * (920 / f.cols), y: 60, w: 920 / f.cols - 20, h: 460, radius: 10 })) };
    setBlocks((bs) => { const n = [...bs]; n.splice(idx == null ? n.length : idx, 0, nb); return n; });
    toast.push(`${f.label} 프레임을 새 블록으로 추가했어요`);
  };
  const onFrameDrop = (e, idx) => {
    e.preventDefault(); setFrameOver(null); setFrameDragging(false);
    const id = e.dataTransfer.getData('text/frame'); if (!id) return;
    const f = catalogs.frames.find((x) => x.id === id); if (f) addFrame(f, idx);
  };
  const addShape = (type, shapeId, bId, dropEvent) => {
    const target = bId || visibleBlock();
    let x = type === 'line' ? 380 : 430, y = type === 'line' ? 300 : 250;
    if (dropEvent && wrapRef.current) {
      const blockEl = wrapRef.current.querySelectorAll('.canvas-block')[blocks.findIndex((b) => b.id === target)];
      if (blockEl) { const r = blockEl.getBoundingClientRect(); x = Math.round((dropEvent.clientX - r.left) / scale - (type === 'line' ? 120 : 70)); y = Math.round((dropEvent.clientY - r.top) / scale - (type === 'line' ? 12 : 70)); }
    }
    const el = type === 'line'
      ? { id: uid('el'), type: 'line', shape: shapeId, x, y, w: 240, h: 24 }
      : { id: uid('el'), type: 'shape', shape: shapeId, x, y, w: 140, h: 140 };
    setBlocks((bs) => bs.map((b) => b.id === target ? { ...b, elements: [...b.elements, el] } : b));
    selectEl(target, el); toast.push('오브젝트를 추가했어요');
  };
  const insertImage = (im) => {
    const bId = visibleBlock();
    const el = { id: uid('el'), type: 'image', x: 250, y: 80, w: 500, h: 560, src: im.src, radius: 12, ...(im.cutType ? { cutType: im.cutType } : {}) };
    setBlocks((bs) => bs.map((b) => b.id === bId ? { ...b, elements: [...b.elements, el] } : b));
    toast.push('이미지를 캔버스에 삽입했어요');
  };
  const requestSlotImage = (blockId, el) => { setPendingSlot({ blockId, elId: el.id }); setTab('wardrobe'); };
  const wardrobeInsert = (im) => {
    if (pendingSlot) {
      // 정보 블록 슬롯이면 info(폼 정본)에도 동기화 — 재생성 때 사진-포인트 연결 유지
      setBlocks((bs) => bs.map((b) => (b.id === pendingSlot.blockId ? applySlotFillToInfo(b, pendingSlot.elId, { src: im.src, cutType: im.cutType || null }) : b)));
      setPendingSlot(null); setTab('image'); toast.push('빈 칸에 이미지를 넣었어요');
    } else insertImage(im);
  };
  // fresh = 새로 생성된 컷의 4색 glow 하이라이트 — 사용자가 본 뒤(애니메이션 종료) 해제
  const freshSeen = (id) => setWardrobe((w) => { const nw = {}; for (const [g, arr] of Object.entries(w)) nw[g] = arr.map((x) => x.id === id && x.fresh ? { ...x, fresh: false } : x); return nw; });
  const deleteWardrobeImages = (ids) => {
    setWardrobe((w) => { const nw = {}; for (const [g, arr] of Object.entries(w)) { const f = arr.filter((im) => !ids.includes(im.id)); if (f.length) nw[g] = f; } return nw; });
    toast.push(`${ids.length}개 이미지를 의류 목록에서 삭제했어요`, { icon: 'trash' });
  };
  // req = NewCutRequest 필드 전체 (계약 §6) — 방향·샷·모델·예시 선택이 생성에 그대로 반영되어야 한다
  const generateImage = async (req) => {
    const group = req.colorId || 'misc';             // wardrobe 그룹 키 = colorId | 'misc' (계약 §3.6)
    const loadingId = uid('w');
    setWardrobe((w) => ({ ...w, [group]: [...(w[group] || []), { id: loadingId, loading: true }] }));
    genCount.current += 1; setGenDot('busy'); toast.push('이미지를 생성하는 중이에요', { icon: 'sparkles' });
    try {
      const { data: img, credits } = await api.generateImage(projectId, { mode: 'new', ...req, colorId: group });
      setWardrobe((w) => ({ ...w, [group]: w[group].map((x) => x.id === loadingId ? { ...img, fresh: true } : x) }));
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
  // 현재 이미지 수정 — 누적된 변경(chips)을 적용해 생성. 생성 즉시 의류 탭으로 이동해
  // '기타' 그룹의 로딩 셀을 보여준다 (PRD §10.8: 새 이미지는 의류 탭에 추가).
  const varyGenerate = async ({ source, changes, refBg, refBgAssetId }) => {
    const loadingId = uid('w');
    setWardrobe((w) => ({ ...w, misc: [...(w.misc || []), { id: loadingId, loading: true }] }));
    setTab('wardrobe');
    genCount.current += 1; setGenDot('busy');
    toast.push(changes.length ? `${changes.length}개 변경을 적용한 컷을 생성하는 중이에요` : '비슷한 컷을 생성하는 중이에요', { icon: 'sparkles' });
    const { data: img, credits } = await api.generateImage(projectId, { mode: 'vary', source, changes, refBg, refBgAssetId });
    setWardrobe((w) => ({ ...w, misc: w.misc.map((x) => x.id === loadingId ? { ...img, fresh: true } : x) }));
    genCount.current -= 1; setGenDot(genCount.current > 0 ? 'busy' : 'done'); toast.push('이미지 생성을 완료했어요', { icon: 'check' });
    syncCredits(credits);
    return img;
  };
  const varyImage = (im) => {
    setVaryTarget(im?.id ? { id: im.id } : null); // 클릭한 의류 이미지가 변형 대상 — 이미지별 독립 상태
    setTab('ai'); toast.push('현재 이미지 수정으로 이동했어요', { icon: 'wand' });
  };
  // 변형 대상 결정 — 'AI 편집' 지정이 있으면 그 의류 이미지, 없으면 선택된 캔버스 이미지
  const varySource = (() => {
    if (varyTarget) {
      const im = Object.values(wardrobe || {}).flat().find((x) => x.id === varyTarget.id);
      return im ? { id: im.id, src: im.src, cutType: im.cutType || null } : null;
    }
    return selectedElObj && selectedElObj.type === 'image'
      ? { id: selectedElObj.id, src: selectedElObj.src, cutType: selectedElObj.cutType || null }
      : null;
  })();
  const setVaryCutType = (t) => {
    if (varyTarget) setWardrobe((w) => { const nw = {}; for (const [g, arr] of Object.entries(w)) nw[g] = arr.map((x) => x.id === varyTarget.id ? { ...x, cutType: t } : x); return nw; });
    else patchEl({ cutType: t });
  };
  const jumpTo = (id) => { setSelBlock(id); setSelEl(null); setSelEls([]);
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
  // 프로젝트가 실제 사용 중인 모델(마네킹/분석 단계에서 고른 것, analysis.selectedModelId 정본)
  // — 모델 정보 프리셋 프리필. FaceMarket 실존 모델 우선.
  // ⚠️ faceThumbUri 는 인증 게이트 URL(공개 URL 아님) — 문서에 저장하면 <img> 가 401 로
  // 깨지고 저장본에도 박제된다. 저장 가능한 공개 coverImageUrl 만 쓰고, 없으면 빈 슬롯
  // (원형 칸에서 '이미지 추가'로 채움).
  const selectedModel = (() => {
    const id = analysis?.selectedModelId;
    if (!id) return null;
    const fm = (fmModels || []).find((m) => m.id === id);
    if (fm) return { name: fm.displayName || '실제 모델', thumb: fm.coverImageUrl || null };
    const cat = ((catalogs && catalogs.models) || []).find((m) => m.id === id);
    if (cat) return { name: cat.name, thumb: cat.thumb || null };
    const am = ((analysis && analysis.models) || []).find((m) => m.id === id);
    return am ? { name: am.name, thumb: am.thumb || null } : null;
  })();
  const infoCtx = {
    productName,
    clothingType,
    measurementSchema: catalogs?.measurementSchema,
    measurementLabels: catalogs?.measurementLabels,
    measurements: product?.measurements,
    materials: analysis?.materials,
    sellingPoints: (analysis?.sellingPoints?.length ? analysis.sellingPoints : analysis?.aiSuggestedPoints) || [],
    fit: analysis?.fit,
    fits: catalogs?.fits,
    colorLabels: colorOpts.map((o) => o.label),
    selectedModel,
  };
  const openInfoPreset = (type) => {
    // size/care 는 자동 블록 제자리 강화, info 는 같은 infoType 이 있으면 그 블록을 수정한다(중복 방지)
    const kind = type === 'size_table' ? 'size' : type === 'care' ? 'care' : null;
    const existing = kind ? blocks.find((b) => b.kind === kind) : blocks.find((b) => presetTypeOf(b) === type);
    setInfoModal({ type, blockId: existing ? existing.id : null, initialInfo: existing?.info || defaultInfoFor(type, infoCtx) });
  };
  const openInfoEdit = (block) => {
    const type = presetTypeOf(block);
    if (!type) return;
    setInfoModal({ type, blockId: block.id, initialInfo: block.info || defaultInfoFor(type, infoCtx) });
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
  const applyTemplate = () => {
    const res = applyInfoTemplate(blocks, infoCtx);
    setBlocks(res.blocks);
    setSelBlock(res.blocks[0]?.id);
    toast.push(`기본 정보 템플릿을 적용했어요 — ${res.inserted.length}개 구성${res.skipped.length ? ` · 이미 있는 ${res.skipped.length}개는 건너뜀` : ''}`, { icon: 'check' });
  };
  const undo = () => { const h = hist.current; if (!h.past.length) { toast.push('되돌릴 작업이 없어요'); return; } const snap = h.past.pop(); h.future.push(prevBlocks.current); fromHistory.current = true; clearSel(); setBlocks(snap); toast.push('실행 취소', { icon: 'undo' }); };
  const redo = () => { const h = hist.current; if (!h.future.length) { toast.push('다시 실행할 작업이 없어요'); return; } const snap = h.future.pop(); h.past.push(prevBlocks.current); fromHistory.current = true; clearSel(); setBlocks(snap); toast.push('다시 실행', { icon: 'redo' }); };
  const save = async () => { await api.saveEditorBlocks(projectId, blocks); toast.push('저장했어요', { icon: 'check' }); };
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
    if (bs) api.saveEditorBlocks(projectId, bs);
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
  const onMvDragStart = () => { gesturing.current = true; liveRef.current = {}; const o = {}; selEls.forEach((id) => { const e = elById(id); if (e) o[id] = { x: e.x, y: e.y, w: e.w }; }); dragSnap.current = o; };
  const onMvGestureEnd = () => { gesturing.current = false; commitLive(); };
  const liveDrag = (target, beforeTranslate) => {
    const elId = target.dataset.elid;
    const st = dragSnap.current && dragSnap.current[elId]; if (!st) return;
    const [dx, dy] = clampDragDelta(dragSnap.current, beforeTranslate);
    const nx = st.x + dx; const ny = st.y + dy;  // moveable 내장 스냅으로 beforeTranslate 는 이미 스냅된 값
    // 캔버스 밖으로 넘어가지 않게 clamp — 왼쪽 끝에서 x=0 flush(overshoot 방지), 오른쪽은 1000-w, 위(y<0)도 막음.
    // block-clip 이 어차피 넘친 부분을 자르므로 손실 없음. ("맨 왼쪽 끌면 몇 px 더 넘어가던" 문제 해결)
    target.style.left = nx + 'px'; target.style.top = ny + 'px';
    liveRef.current[elId] = { x: Math.round(nx), y: Math.round(ny) };
  };
  const onMvResizeStart = () => { gesturing.current = true; liveRef.current = {}; const id = selEls[0]; const e = elById(id); dragSnap.current = e ? { [id]: { x: e.x, y: e.y, w: e.w, h: e.h } } : null; };
  const liveResize = (target, width, height, drag) => {
    const elId = target.dataset.elid;
    const st = dragSnap.current && dragSnap.current[elId]; if (!st) return;
    let nx = st.x + (drag?.beforeTranslate?.[0] || 0);
    let ny = st.y + (drag?.beforeTranslate?.[1] || 0);
    let nw = width, nh = height;
    // 이미지(크롭 안 한 것)는 원본 사진 비율을 따라간다 — 리사이즈로 잘리거나 늘어나지 않게.
    // 위쪽 핸들로 끌 땐 아래 변을 고정해 높이 보정이 튀지 않도록 y 를 되맞춘다.
    const elNow = elById(elId);
    const imgNode = target.querySelector('img');
    if (elNow && elNow.type === 'image' && !elNow.crop && imgNode && imgNode.naturalWidth && imgNode.naturalHeight) {
      nh = Math.max(24, Math.round(nw * imgNode.naturalHeight / imgNode.naturalWidth));
      if ((drag?.beforeTranslate?.[1] || 0) !== 0) ny = st.y + st.h - nh;
    }
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
      case 'ai': return <AIPanel catalogs={catalogs} fmModels={fmModels} account={account} colorOpts={colorOpts} detailColorOpts={detailColorOpts} clothingType={clothingType} hasDetailImage={hasDetailImage} varySource={varySource} onGenerate={generateImage} onVaryGenerate={varyGenerate} onPickRef={() => api.pickRefImage(projectId)} onPickMoodRef={() => api.pickRefImage(projectId)} onSetCutType={setVaryCutType} />;
      case 'wardrobe': return <WardrobePanel wardrobe={wardrobe} colorOpts={colorOpts} pendingSlot={pendingSlot} onInsert={wardrobeInsert} onDeleteSelected={deleteWardrobeImages} onUpload={async () => { const src = await api.pickAnyImage(); setWardrobe((w) => ({ ...w, misc: [...(w.misc || []), { id: uid('w'), src }] })); toast.push('이미지를 업로드했어요'); }} onVaryImage={varyImage} onFreshSeen={freshSeen} />;
      case 'image': return <ImagePanel el={selectedElObj} onChange={patchEl} onLayer={layerEl} lock={lockRatio} onLock={setLockRatio} onCrop={(el) => startCrop(blockIdOf(el.id), el)} onVary={varyImage} />;
      case 'frame': return (
        <>
          <FramePanel catalogs={catalogs} onAdd={addFrame} onDragStart={() => setFrameDragging(true)} onDragEnd={() => setFrameDragging(false)} />
          {/* 내용 프리셋 — 프레임 탭에 통합 (별도 탭 없음) */}
          <div style={{ marginTop: 22 }}>
            <ContentPanel recommendGender={recommendGender} onApplyTemplate={applyTemplate} onPick={openInfoPreset} />
          </div>
        </>
      );
      case 'text': return <TextPanel el={selectedElObj} catalogs={catalogs} onChange={patchEl} onLayer={layerEl} onAddText={() => addText()} onAddGarmentText={() => addText(undefined, 'garment')} />;
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
  const fitToScreen = () => { const wrap = wrapRef.current; if (!wrap) return; setScale(Math.min(2, Math.max(0.1, +((wrap.clientWidth - 80) / 1000).toFixed(2)))); };
  const single = selEls.length === 1 && !editEl;
  const group = selEls.length > 1 && !editEl;
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
          <Button variant="primary" size="sm" icon="download" onClick={() => setDownload(true)}>다운로드</Button>
        </div>
      </div>

      {/* body */}
      <div className="ed-body" style={{ '--lcol': '320px', '--rcol': rightHidden ? '0px' : '208px' }}>
        <div className="ed-left">
          <div style={{ marginBottom: 14 }}><span className="panel-h" style={{ marginBottom: 0 }}>{panelTitle}</span></div>
          {renderPanel()}
        </div>

        <div className={`ed-canvas-wrap${spaceDown ? ' panning' : ''}`} ref={wrapRef}
          onPointerDown={(e) => { if (spaceDown) startPan(e); }}
          onClick={(e) => { if (spaceDown) return; if (e.target.closest && e.target.closest('.moveable-control-box')) return; if (cropping) { commitCrop(); return; } clearSel(); }}
          onScroll={() => moveableRef.current?.updateRect()}
          onMouseMove={(e) => { const g = !e.target.closest('.canvas-block'); setHoverGray((v) => v === g ? v : g); }}
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
            style={{ transform: `scale(${scale})`, transformOrigin: 'top left', position: 'absolute', top: 0, left: 0, margin: 0 }}>
            {blocks.map((b, i) => (
              <div key={b.id} style={{ display: 'contents' }}>
                <div className="canvas-droprow" onDragOver={(e) => { if (e.dataTransfer.types.includes('text/frame')) { e.preventDefault(); setFrameOver(i); } }}
                  onDragLeave={() => setFrameOver((o) => o === i ? null : o)} onDrop={(e) => onFrameDrop(e, i)}>
                  <div className={`canvas-dropline${frameOver === i ? ' on' : ''}`} />
                </div>
                <CanvasBlock block={b} scale={scale} idx={i}
                  selectedBlockId={selBlock} selEls={selEls} editEl={editEl} onEdit={setEditEl}
                  crop={cropping && cropping.blockId === b.id ? cropping : null}
                  onCropDrag={cropDrag} onCropStart={startCrop} onCropCommit={commitCrop} onCropReset={resetCrop}
                  onSelectBlock={(id) => { setSelBlock(id); clearSel(); setTab('shape'); }} onSelectEl={selectEl}
                  onElPatch={patchElById} onAddImage={requestSlotImage} onOpenLayers={(id) => { setLayerFloat(id); setLayerPos(null); }}
                  onObjectDrop={(bid, type, id, ev) => addShape(type, id, bid, ev)} onReshape={reshapeBlock}
                  onMove={moveBlock} onAddEmpty={addEmpty} onDelete={deleteBlock} onEditInfo={openInfoEdit}
                  onDownload={() => toast.push('이 블록을 PNG로 저장했어요', { icon: 'download' })} />
              </div>
            ))}
            <div className="canvas-droprow" onDragOver={(e) => { if (e.dataTransfer.types.includes('text/frame')) { e.preventDefault(); setFrameOver(blocks.length); } }}
              onDragLeave={() => setFrameOver((o) => o === blocks.length ? null : o)} onDrop={(e) => onFrameDrop(e, blocks.length)}>
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
              target={mvTargets}
              rootContainer={wrapRef.current}
              draggable
              resizable={single}
              rotatable={single}
              keepRatio={lockRatio}
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
              renderDirections={lockRatio ? ['nw', 'ne', 'sw', 'se'] : ['nw', 'n', 'ne', 'w', 'e', 'sw', 's', 'se']}
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
              <div key={b.id} style={{ position: 'relative', height: getBlockRenderHeight(b), background: b.bg, overflow: 'hidden', boxSizing: 'border-box' }}>
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
          editing={!!infoModal.blockId} onClose={() => setInfoModal(null)} onSubmit={submitInfo} />
      )}
    </div>
  );
}

export default Editor;
