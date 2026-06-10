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
import { useNavigate } from 'react-router-dom';
import Moveable from 'react-moveable';
import { api } from '@/lib/api/index.js';
import { DB } from '@/mock/db.js';
import { Icon, IconButton, Button, Modal, EmptyState, useToast } from '@/components/ui.jsx';
import { hexFor } from '@/features/storyboard/Storyboard.jsx';
import { AIPanel, WardrobePanel, ImagePanel, TextPanel, FramePanel, ShapePanel, LayerPanel } from '@/features/editor/EditorPanels.jsx';

const FONT_MAP = { 'Cal Sans': 'var(--font-display)', 'Roboto Mono': 'var(--font-mono)', 'Pretendard': 'var(--font-body)' };

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
      if (preview) return <div className="el el-slot" style={base} />;
      return (
        <div {...common} className={cls('el-slot')} style={base}>
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

function CanvasBlock({ block, scale, selectedBlockId, selEls, onSelectBlock, onSelectEl, onElPatch, onAddImage, onOpenLayers, onObjectDrop, onReshape, onMove, onAddEmpty, onDelete, onDownload, editEl, onEdit, crop, onCropDrag, onCropStart, onCropCommit, idx }) {
  const contentBottom = block.elements.reduce((m, e) => Math.max(m, (e.y || 0) + (e.h || 40)), 0);
  const blockH = block.h || Math.max(220, contentBottom + 50);
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
  const end = () => { setDragId(null); setLineAt(null); };
  return (
    <div className="ed-right">
      <div className="mini-head">상세페이지 · 드래그로 순서 변경</div>
      {blocks.map((b, i) => (
        <div key={b.id} style={{ display: 'contents' }}>
          <div className={`mini-dropline${lineAt === i ? ' on' : ''}`} />
          <div className={`mini-block${selectedBlockId === b.id ? ' on' : ''}${dragId === b.id ? ' dragging' : ''}`}
            draggable onClick={() => onJump(b.id)}
            onDragStart={(e) => { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/mini', b.id); setDragId(b.id); }}
            onDragEnd={end}
            onDragOver={(e) => { if (dragId) { e.preventDefault(); const r = e.currentTarget.getBoundingClientRect(); setLineAt(e.clientY > r.top + r.height / 2 ? i + 1 : i); } }}
            onDrop={(e) => { e.preventDefault(); if (!dragId) return; const from = blocks.findIndex((x) => x.id === dragId); let to = lineAt == null ? i : lineAt; if (from < to) to--; to = Math.max(0, Math.min(blocks.length - 1, to)); if (from > -1 && from !== to) onReorder(from, to); end(); }}>
            <div className="mini-canvas" style={{ background: b.bg, aspectRatio: `1000 / ${b.h || 660}` }}>
              {b.elements.filter((e) => e.type === 'image' && e.src).map((e) => {
                const bh = b.h || 660;
                return <img key={e.id} src={e.src} style={{ left: (e.x / 1000) * 100 + '%', top: (e.y / bh) * 100 + '%', width: (e.w / 1000) * 100 + '%', height: (e.h / bh) * 100 + '%' }} alt="" draggable={false} />;
              })}
            </div>
          </div>
        </div>
      ))}
      <div className={`mini-dropline${lineAt === blocks.length ? ' on' : ''}`} />
    </div>
  );
}

const hexForCol = (col) => hexFor(col);

/* rotate 정규화: 무한 누적(-1080° 등) 방지 — 항상 (-180, 180] 로 저장·표시 */
const normDeg = (d) => { let n = ((d % 360) + 360) % 360; return n > 180 ? n - 360 : n; };

export function Editor() {
  const navigate = useNavigate();
  const [blocks, setBlocks] = useState(null);
  const [wardrobe, setWardrobe] = useState(null);
  const [catalogs, setCatalogs] = useState(null);
  const [account, setAccount] = useState(null);
  const [colorOpts, setColorOpts] = useState([]);
  const [productName, setProductName] = useState('');
  const [tab, setTab] = useState('ai');
  const [selBlock, setSelBlock] = useState(null);
  const [selEl, setSelEl] = useState(null);
  const [selEls, setSelEls] = useState([]);
  const [scale, setScale] = useState(0.4);
  const [rightHidden, setRightHidden] = useState(false);
  const [preview, setPreview] = useState(false);
  const [download, setDownload] = useState(false);
  const [dlFormat, setDlFormat] = useState('long');
  const [backWarn, setBackWarn] = useState(false);
  const [genDot, setGenDot] = useState('none');
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
    Promise.all([api.getEditorBlocks(), api.getWardrobe(), api.getCatalogs(), api.getAccount(), api.getProduct()])
      .then(([b, w, c, a, p]) => {
        const withH = b.map((blk) => ({ ...blk, h: blk.h || Math.max(220, blk.elements.reduce((m, e) => Math.max(m, (e.y || 0) + (e.h || 40)), 0) + 50) }));
        setBlocks(withH); setWardrobe(w); setCatalogs(c); setAccount(a); setSelBlock(withH[0]?.id);
        setProductName(p.name || '제목 없는 상세페이지');
        const opts = (p.colors || []).filter((col) => col.images.length || col.isBase).map((col) => ({ id: col.id, label: col.name || '색상', hex: hexForCol(col) }));
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
  // dev-only QA hook: drive gestures via moveable.request() (real pointer pipeline)
  useEffect(() => { if (import.meta.env.DEV) window.__mv = moveableRef; }, []);

  if (!blocks || !catalogs) return <div className="editor"><div style={{ margin: 'auto' }}><Icon name="loader" size={26} className="spin" /></div></div>;

  const selectedElObj = blocks.flatMap((b) => b.elements).find((e) => e.id === selEl);
  const visibleBlock = () => selBlock || blocks[0]?.id;
  const elById = (id) => blocks.flatMap((b) => b.elements).find((e) => e.id === id);

  const selectEl = (blockId, el, additive, keepTab) => {
    if (cropping) commitCrop();   // 크롭 중 다른 요소 클릭 → 크롭 확정 후 선택 (런타임 호출이라 TDZ 무관)
    setSelBlock(blockId); setSelEl(el.id);
    setSelEls((cur) => additive ? (cur.includes(el.id) ? cur.filter((x) => x !== el.id) : [...cur, el.id]) : [el.id]);
    if (!keepTab) setTab(el.type === 'text' ? 'text' : 'image');
  };
  const clearSel = () => { setSelEl(null); setSelEls([]); };
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
  const addEmpty = (idx) => setBlocks((bs) => { const n = [...bs]; const nb = { id: DB.uid('b'), name: '빈 블록', kind: 'info', bg: '#ffffff', h: 300, elements: [] }; n.splice(idx + 1, 0, nb); return n; });
  const deleteBlock = (id) => { setBlocks((bs) => bs.filter((b) => b.id !== id)); toast.push('블록을 삭제했어요'); };
  const addFrame = (f, idx) => {
    const nb = { id: DB.uid('b'), name: f.label, kind: 'info', bg: '#ffffff', h: 580, elements:
      Array.from({ length: f.cols }).map((_, i) => ({ id: DB.uid('el'), type: 'image', x: 40 + i * (920 / f.cols), y: 60, w: 920 / f.cols - 20, h: 460, radius: 10 })) };
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
      ? { id: DB.uid('el'), type: 'line', shape: shapeId, x, y, w: 240, h: 24 }
      : { id: DB.uid('el'), type: 'shape', shape: shapeId, x, y, w: 140, h: 140 };
    setBlocks((bs) => bs.map((b) => b.id === target ? { ...b, elements: [...b.elements, el] } : b));
    selectEl(target, el); toast.push('오브젝트를 추가했어요');
  };
  const insertImage = (im) => {
    const bId = visibleBlock();
    const el = { id: DB.uid('el'), type: 'image', x: 250, y: 80, w: 500, h: 560, src: im.src, radius: 12 };
    setBlocks((bs) => bs.map((b) => b.id === bId ? { ...b, elements: [...b.elements, el] } : b));
    toast.push('이미지를 캔버스에 삽입했어요');
  };
  const requestSlotImage = (blockId, el) => { setPendingSlot({ blockId, elId: el.id }); setTab('wardrobe'); };
  const wardrobeInsert = (im) => {
    if (pendingSlot) { patchElById(pendingSlot.blockId, pendingSlot.elId, { src: im.src }); setPendingSlot(null); setTab('image'); toast.push('빈 칸에 이미지를 넣었어요'); }
    else insertImage(im);
  };
  const deleteWardrobeImages = (ids) => {
    setWardrobe((w) => { const nw = {}; for (const [g, arr] of Object.entries(w)) { const f = arr.filter((im) => !ids.includes(im.id)); if (f.length) nw[g] = f; } return nw; });
    toast.push(`${ids.length}개 이미지를 의류 목록에서 삭제했어요`, { icon: 'trash' });
  };
  const generateImage = async ({ group }) => {
    const loadingId = DB.uid('w');
    setWardrobe((w) => ({ ...w, [group]: [...(w[group] || []), { id: loadingId, loading: true }] }));
    setGenDot('busy'); toast.push('이미지를 생성하는 중이에요', { icon: 'sparkles' });
    const img = await api.generateImage({ group });
    setWardrobe((w) => ({ ...w, [group]: w[group].map((x) => x.id === loadingId ? img : x) }));
    setGenDot('done'); toast.push('이미지 생성을 완료했어요', { icon: 'check' });
    setAccount((a) => ({ ...a, credits: a.credits - 1 }));
  };
  const varyImage = () => { setTab('ai'); toast.push('현재 컷 변형으로 이동했어요', { icon: 'wand' }); };
  const jumpTo = (id) => { setSelBlock(id); setSelEl(null); setSelEls([]);
    const idx = blocks.findIndex((b) => b.id === id);
    const wrap = wrapRef.current; if (!wrap) return;
    const target = wrap.querySelectorAll('.canvas-block')[idx];
    if (target) { const wr = wrap.getBoundingClientRect(); const tr = target.getBoundingClientRect(); wrap.scrollTo({ top: wrap.scrollTop + (tr.top - wr.top) - 40, behavior: 'smooth' }); } };
  const addText = (bId) => {
    const id = bId || visibleBlock();
    const el = { id: DB.uid('el'), type: 'text', x: 120, y: 80, w: 420, h: 60, text: '텍스트를 입력하세요', style: { font: 'Pretendard', size: 32, weight: 500, color: '#0e0d14' } };
    setBlocks((bs) => bs.map((b) => b.id === id ? { ...b, elements: [...b.elements, el] } : b));
    selectEl(id, el); setTab('text'); toast.push('텍스트를 추가했어요');
  };
  const undo = () => { const h = hist.current; if (!h.past.length) { toast.push('되돌릴 작업이 없어요'); return; } const snap = h.past.pop(); h.future.push(prevBlocks.current); fromHistory.current = true; clearSel(); setBlocks(snap); toast.push('실행 취소', { icon: 'undo' }); };
  const redo = () => { const h = hist.current; if (!h.future.length) { toast.push('다시 실행할 작업이 없어요'); return; } const snap = h.future.pop(); h.past.push(prevBlocks.current); fromHistory.current = true; clearSel(); setBlocks(snap); toast.push('다시 실행', { icon: 'redo' }); };
  const save = () => toast.push('저장했어요', { icon: 'check' });
  /* kb.current 는 crop 핸들러 정의 뒤(아래)에서 채운다 — TDZ 방지 */

  /* ---- react-moveable → Element {x,y,w,h,rotate}.
     좌표: rootContainer가 캔버스 scale을 행렬로 접어 넣음 → 델타/크기는 LOCAL 도착.
     적용: 제스처 중에는 e.target.style 에만 라이브로 쓰고(liveRef), End 에서 한 번
     상태를 커밋한다 — 매 프레임 setState→컨트롤박스 재생성이 리사이즈 제스처를
     죽이던 되먹임 루프 차단 (드래그는 타깃 노드에 붙어 살아남던 비대칭). ---- */
  const blockIdOf = (elId) => (blocks.find((b) => b.elements.some((e) => e.id === elId)) || {}).id;
  const snapX = (nx, w) => { const W = 1000, s = 10, targets = [40, (W - w) / 2, W - 40 - w]; for (const t of targets) { if (Math.abs(nx - t) < s) return t; } return nx; };
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
    let nx = st.x + beforeTranslate[0]; let ny = st.y + beforeTranslate[1];
    if (selEls.length === 1) nx = snapX(nx, st.w || 0);
    target.style.left = nx + 'px'; target.style.top = ny + 'px';
    liveRef.current[elId] = { x: Math.round(nx), y: Math.round(ny) };
  };
  const onMvResizeStart = () => { gesturing.current = true; liveRef.current = {}; const id = selEls[0]; const e = elById(id); dragSnap.current = e ? { [id]: { x: e.x, y: e.y, w: e.w, h: e.h } } : null; };
  const liveResize = (target, width, height, drag) => {
    const elId = target.dataset.elid;
    const st = dragSnap.current && dragSnap.current[elId]; if (!st) return;
    const w = Math.max(24, width); const h = Math.max(24, height);
    const nx = st.x + (drag?.beforeTranslate?.[0] || 0); const ny = st.y + (drag?.beforeTranslate?.[1] || 0);
    target.style.left = nx + 'px'; target.style.top = ny + 'px';
    target.style.width = w + 'px'; target.style.height = h + 'px';
    liveRef.current[elId] = { x: Math.round(nx), y: Math.round(ny), w: Math.round(w), h: Math.round(h) };
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
    { id: 'ai', icon: 'sparkles', label: 'AI', dot: true },
    { id: 'wardrobe', icon: 'shirt', label: '의류' },
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
      case 'ai': return <AIPanel catalogs={catalogs} account={account} colorOpts={colorOpts} selectedEl={selectedElObj} onGenerate={generateImage} onVary={() => toast.push('변형 이미지를 생성했어요', { icon: 'wand' })} />;
      case 'wardrobe': return <WardrobePanel wardrobe={wardrobe} colorOpts={colorOpts} pendingSlot={pendingSlot} onInsert={wardrobeInsert} onDeleteSelected={deleteWardrobeImages} onUpload={async () => { const src = await api.pickAnyImage(); setWardrobe((w) => ({ ...w, '기타': [...(w['기타'] || []), { id: DB.uid('w'), src }] })); toast.push('이미지를 업로드했어요'); }} onVaryImage={varyImage} />;
      case 'image': return <ImagePanel el={selectedElObj} onChange={patchEl} onLayer={layerEl} lock={lockRatio} onLock={setLockRatio} onCrop={(el) => startCrop(blockIdOf(el.id), el)} />;
      case 'frame': return <FramePanel catalogs={catalogs} onAdd={addFrame} onDragStart={() => setFrameDragging(true)} onDragEnd={() => setFrameDragging(false)} />;
      case 'text': return <TextPanel el={selectedElObj} catalogs={catalogs} onChange={patchEl} onLayer={layerEl} onAddText={() => addText()} />;
      case 'shape': return <ShapePanel catalogs={catalogs} onAdd={addShape} block={(selEls.length === 0 && selBlock) ? blocks.find((b) => b.id === selBlock) : null} onBgChange={changeBg} />;
      default: return null;
    }
  };

  const single = selEls.length === 1 && !editEl;
  const group = selEls.length > 1 && !editEl;

  return (
    <div className="editor">
      {/* toolbar */}
      <div className="ed-toolbar">
        <button className="ed-tool" onClick={() => navigate('/library')} title="보관함으로" style={{ flexDirection: 'row', gap: 6 }}>
          <span className="brand" style={{ fontSize: 17 }}>wearless</span>
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

        <div className="ed-canvas-wrap" ref={wrapRef}
          onClick={(e) => { if (e.target.closest && e.target.closest('.moveable-control-box')) return; if (cropping) { commitCrop(); return; } clearSel(); }}
          onScroll={() => moveableRef.current?.updateRect()}
          onMouseMove={(e) => { const g = !e.target.closest('.canvas-block'); setHoverGray((v) => v === g ? v : g); }}
          onMouseLeave={() => setHoverGray(false)}>
          <div className={`zoom-float${hoverGray ? ' show' : ''}`}>
            <div className="zoom-pill" onClick={(e) => e.stopPropagation()} onMouseMove={(e) => e.stopPropagation()}>
              <button onClick={() => setScale((s) => Math.max(0.1, +(s - 0.1).toFixed(2)))}><Icon name="minus" size={15} /></button>
              <span>{Math.round(scale * 100)}%</span>
              <button onClick={() => setScale((s) => Math.min(2, +(s + 0.1).toFixed(2)))}><Icon name="plus" size={15} /></button>
            </div>
          </div>
          {rightHidden && <div style={{ position: 'absolute', right: 10, top: 10, zIndex: 3 }}><IconButton name="layout" size="sm" onClick={() => setRightHidden(false)} /></div>}
          {/* CSS `zoom` is invisible to react-moveable (it only reads the transform
              matrix) — scale via transform instead. transform doesn't take layout
              space, so a spacer reserves the SCALED dimensions for scrolling. */}
          <div style={{ position: 'relative', width: 1000 * scale, height: canvasH * scale, margin: '40px auto' }}>
          <div className={`ed-canvas${frameDragging ? ' frame-dragging' : ''}`} ref={canvasRef}
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
                  onCropDrag={cropDrag} onCropStart={startCrop} onCropCommit={commitCrop}
                  onSelectBlock={(id) => { setSelBlock(id); clearSel(); setTab('shape'); }} onSelectEl={selectEl}
                  onElPatch={patchElById} onAddImage={requestSlotImage} onOpenLayers={(id) => { setLayerFloat(id); setLayerPos(null); }}
                  onObjectDrop={(bid, type, id, ev) => addShape(type, id, bid, ev)} onReshape={reshapeBlock}
                  onMove={moveBlock} onAddEmpty={addEmpty} onDelete={deleteBlock}
                  onDownload={() => toast.push('이 블록을 PNG로 저장했어요', { icon: 'download' })} />
              </div>
            ))}
            <div className="canvas-droprow" onDragOver={(e) => { if (e.dataTransfer.types.includes('text/frame')) { e.preventDefault(); setFrameOver(blocks.length); } }}
              onDragLeave={() => setFrameOver((o) => o === blocks.length ? null : o)} onDrop={(e) => onFrameDrop(e, blocks.length)}>
              <div className={`canvas-dropline${frameOver === blocks.length ? ' on' : ''}`} />
            </div>

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
              renderDirections={['nw', 'n', 'ne', 'w', 'e', 'sw', 's', 'se']}
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
              <div key={b.id} style={{ position: 'relative', height: b.h || 660, background: b.bg, overflow: 'hidden', boxSizing: 'border-box' }}>
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
    </div>
  );
}

export default Editor;
