/* =============================================================
   features/editor.jsx — ⑦ 상세페이지 수정 페이지 (PRD §11)
   Working: tab switch, per-tab left panel, 1000px canvas @40%,
   block/element selection, block move/add/delete, block bg color,
   auto info blocks (size/care/AI notice), mini preview + drag reorder,
   AI generate (real mock), wardrobe insert, frame add + drag-insert,
   shape add, element drag-move/resize/rotate, snap-align, layer order,
   multi-select (shift-click) group move + delete, inline text edit,
   download modal, preview overlay, back-restriction guard.
   Mockup-only: real crop, server save/Undo·Redo, file download.
   ============================================================= */
const { useState, useEffect, useRef } = React;

const FONT_MAP = { 'Cal Sans': 'var(--font-display)', 'Roboto Mono': 'var(--font-mono)', 'Pretendard': 'var(--font-body)' };

/* a single draggable / resizable / rotatable element inside a block.
   pointer deltas are divided by `scale` to map screen px → canvas px (canvas uses zoom).
   selected = in the selection set; showHandles = sole selection (resize/rotate only on one). */
function CanvasElement({ el, blockId, selected, showHandles, isMulti, scale, preview, onSelect, onPatch, onAddImage, onGroupStart, onGroupMove, onGroupEnd }) {
  const ref = useRef(null);
  const [editing, setEditing] = useState(false);
  if (el.hidden) return null;   // 레이어에서 숨긴 요소는 렌더하지 않음

  const drag = (e, mode, corner) => {
    if (e.button != null && e.button !== 0) return;
    if (el.locked) return;       // 잠긴 요소는 이동/리사이즈/선택 불가 (레이어 패널에서 해제)
    e.stopPropagation();
    const group = mode === 'move' && isMulti;          // 다중 선택 멤버를 끌면 그룹 이동
    if (!group && mode === 'move') onSelect(el, e.shiftKey);  // 단일: 누르는 즉시 선택
    else if (mode !== 'move') onSelect(el, false);            // 리사이즈/회전: 단일 기준
    const sx = e.clientX, sy = e.clientY;
    const o = { x: el.x, y: el.y, w: el.w, h: el.h, rotate: el.rotate || 0 };
    const r = ref.current.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const a0 = Math.atan2(sy - cy, sx - cx);
    let moved = false;
    if (group) onGroupStart && onGroupStart();
    const move = (ev) => {
      moved = true;
      const dx = (ev.clientX - sx) / scale, dy = (ev.clientY - sy) / scale;
      if (group) { onGroupMove && onGroupMove(dx, dy); return; }
      if (mode === 'move') {
        let nx = o.x + dx, ny = o.y + dy;
        // 자석 정렬: 블록 왼쪽(40) · 가운데 · 오른쪽(960)에 스냅 (PRD §11.17)
        const W = 1000, snap = 10;
        const targets = [40, (W - o.w) / 2, W - 40 - o.w];
        for (const t of targets) { if (Math.abs(nx - t) < snap) { nx = t; break; } }
        onPatch(blockId, el.id, { x: Math.round(nx), y: Math.round(ny) });
      }
      else if (mode === 'resize') {
        let nx = o.x, ny = o.y, nw = o.w, nh = o.h;
        if (corner.includes('r')) nw = Math.max(24, o.w + dx);
        if (corner.includes('l')) { nw = Math.max(24, o.w - dx); nx = o.x + (o.w - nw); }
        if (corner.includes('b')) nh = Math.max(24, o.h + dy);
        if (corner.includes('t')) { nh = Math.max(24, o.h - dy); ny = o.y + (o.h - nh); }
        onPatch(blockId, el.id, { x: Math.round(nx), y: Math.round(ny), w: Math.round(nw), h: Math.round(nh) });
      } else if (mode === 'rotate') {
        const ang = Math.atan2(ev.clientY - cy, ev.clientX - cx) - a0;
        let deg = Math.round(o.rotate + ang * 180 / Math.PI);
        // rotation snapping: 0·90·180·270(±360) 근처 ±7°에서 딱 맞춤
        const norm = ((deg % 360) + 360) % 360;
        const snap = [0, 90, 180, 270, 360].find((t) => Math.abs(norm - t) <= 7);
        if (snap != null) deg += (snap % 360) - norm;
        onPatch(blockId, el.id, { rotate: deg });
      }
    };
    const up = () => {
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up);
      document.body.style.userSelect = '';
      if (group) { onGroupEnd && onGroupEnd(); if (!moved) onSelect(el, e.shiftKey); }  // 드래그 없이 클릭 → 선택
    };
    document.body.style.userSelect = 'none';
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
  };

  const base = { left: el.x, top: el.y, width: el.w, height: el.h,
    transform: el.rotate ? `rotate(${el.rotate}deg)` : undefined, opacity: el.opacity ?? 1,
    pointerEvents: el.locked ? 'none' : undefined };
  const handles = showHandles && !editing && (
    <>
      <span className="rot" onPointerDown={(e) => drag(e, 'rotate')}><Icon name="rotate" size={13} /></span>
      {/* edge handles — drag any side to resize that side (common-editor behavior, #8).
          side classes are eg-* so the left handle never collides with the element's own .el class */}
      <span className="edge eg-t" onPointerDown={(e) => drag(e, 'resize', 't')} />
      <span className="edge eg-b" onPointerDown={(e) => drag(e, 'resize', 'b')} />
      <span className="edge eg-l" onPointerDown={(e) => drag(e, 'resize', 'l')} />
      <span className="edge eg-r" onPointerDown={(e) => drag(e, 'resize', 'r')} />
      <span className="hdl tl" onPointerDown={(e) => drag(e, 'resize', 'tl')} />
      <span className="hdl tr" onPointerDown={(e) => drag(e, 'resize', 'tr')} />
      <span className="hdl bl" onPointerDown={(e) => drag(e, 'resize', 'bl')} />
      <span className="hdl br" onPointerDown={(e) => drag(e, 'resize', 'br')} />
    </>
  );

  if (el.type === 'image') {
    // 빈 이미지 칸(프레임 슬롯) — '이미지 추가' 버튼, 클릭 시 의류 탭으로 (item 3)
    if (!el.src) {
      const inv = 1 / (scale || 1);
      if (preview) return <div className="el el-slot" style={base} />;
      return (
        <div ref={ref} className={`el el-slot${selected ? ' on' : ''}`} style={base}
          onPointerDown={(e) => drag(e, 'move')} onClick={(e) => e.stopPropagation()}>
          <button className="slot-add" style={{ transform: `scale(${inv})` }}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => { e.stopPropagation(); onAddImage && onAddImage(el); }}>
            <Icon name="plus" size={20} /><span>이미지 추가</span>
          </button>{handles}
        </div>
      );
    }
    return (
      <div ref={ref} className={`el${selected ? ' on' : ''}`} style={base}
        onPointerDown={(e) => drag(e, 'move')} onClick={(e) => e.stopPropagation()}>
        <img src={el.src} alt="" style={{ borderRadius: el.radius }} draggable={false} />{handles}
      </div>
    );
  }
  if (el.type === 'text') {
    const s = el.style || {};
    // 말머리(list) 적용: 편집 중엔 원문, 보기 중엔 줄마다 마커
    const lines = (el.text || '').split('\n');
    const display = (!s.list || s.list === 'none') ? el.text
      : lines.map((ln, i) => (s.list === 'ordered' ? `${i + 1}. ` : '• ') + ln).join('\n');
    const hasBg = s.bg && s.bg !== 'none';
    return (
      <div ref={ref} className={`el el-text${selected ? ' on' : ''}${editing ? ' editing' : ''}`} style={{ ...base, height: 'auto',
        fontFamily: FONT_MAP[s.font] || 'var(--font-body)', fontSize: s.size, fontWeight: s.weight || 400,
        color: s.color || '#0e0d14', letterSpacing: s.tracking, textAlign: s.align || 'left',
        lineHeight: s.lineHeight ? s.lineHeight + 'px' : 1.4, whiteSpace: 'pre-wrap', opacity: (el.opacity ?? 1) * (s.opacity ?? 1),
        background: hasBg ? s.bg : undefined, padding: hasBg ? '2px 8px' : undefined, borderRadius: hasBg ? 4 : undefined,
        fontStyle: s.italic ? 'italic' : 'normal',
        textDecoration: [s.underline && 'underline', s.strike && 'line-through'].filter(Boolean).join(' ') || 'none' }}
        onPointerDown={(e) => { if (!editing) drag(e, 'move'); }}
        onClick={(e) => e.stopPropagation()}
        onDoubleClick={(e) => { e.stopPropagation(); setEditing(true); setTimeout(() => ref.current && ref.current.focus(), 0); }}
        contentEditable={editing} suppressContentEditableWarning
        onBlur={(e) => { setEditing(false); onPatch(blockId, el.id, { text: e.currentTarget.textContent }); }}>
        {editing ? el.text : display}{handles}</div>
    );
  }
  // shape / line — fill / stroke editable (Figma-style 채움/테두리)
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
    // line / arrow — render the actual ←, —, → so it matches the 오브젝트 탭 (#6)
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
  return <div ref={ref} className={`el${selected ? ' on' : ''}`} style={base}
    onPointerDown={(e) => drag(e, 'move')} onClick={(e) => e.stopPropagation()}>{inner}{handles}</div>;
}

function CanvasBlock({ block, scale, selectedBlockId, selEls, primaryEl, onSelectBlock, onSelectEl, onElPatch, onAddImage, onOpenLayers, onGroupStart, onGroupMove, onGroupEnd, onObjectDrop, onReshape, onMove, onAddEmpty, onDelete, onDownload, idx, total }) {
  const contentBottom = block.elements.reduce((m, e) => Math.max(m, (e.y || 0) + (e.h || 40)), 0);
  // block height is FIXED (stored on the block) — element drag/resize never changes it (#10)
  const blockH = block.h || Math.max(220, contentBottom + 50);
  const blockSelected = selectedBlockId === block.id && (!selEls || selEls.length === 0);
  const isMulti = selEls && selEls.length > 1;
  const [objOver, setObjOver] = useState(false);

  // drag the top/bottom pill to change ONLY this block's height (#11)
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
        const delta = nh - startH;                 // grow upward → push content down so it stays anchored
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
      {/* elements live inside a clip so anything past the frame is cropped (#10) */}
      <div className="block-clip">
        {block.elements.map((el) => (
          <CanvasElement key={el.id} el={el} blockId={block.id} scale={scale}
            selected={selEls && selEls.includes(el.id)} showHandles={selEls && selEls.length === 1 && selEls[0] === el.id} isMulti={isMulti}
            onSelect={(e, additive) => onSelectEl(block.id, e, additive)} onPatch={onElPatch}
            onAddImage={(elm) => onAddImage(block.id, elm)}
            onGroupStart={onGroupStart} onGroupMove={onGroupMove} onGroupEnd={onGroupEnd} />
        ))}
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
  const [lineAt, setLineAt] = useState(null); // insertion index 0..n (blue line between blocks)
  const end = () => { setDragId(null); setLineAt(null); };
  return (
    <div className="ed-right">
      <div className="mini-head">상세페이지 · 드래그로 순서 변경</div>
      {blocks.map((b, i) => (
        <React.Fragment key={b.id}>
          <div className={`mini-dropline${lineAt === i ? ' on' : ''}`} />
          <div className={`mini-block${selectedBlockId === b.id ? ' on' : ''}${dragId === b.id ? ' dragging' : ''}`}
            draggable onClick={() => onJump(b.id)}
            onDragStart={(e) => { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/mini', b.id); setDragId(b.id); }}
            onDragEnd={end}
            onDragOver={(e) => { if (dragId) { e.preventDefault(); const r = e.currentTarget.getBoundingClientRect(); setLineAt(e.clientY > r.top + r.height / 2 ? i + 1 : i); } }}
            onDrop={(e) => { e.preventDefault(); if (!dragId) return; const from = blocks.findIndex((x) => x.id === dragId); let to = lineAt == null ? i : lineAt; if (from < to) to--; to = Math.max(0, Math.min(blocks.length - 1, to)); if (from > -1 && from !== to) onReorder(from, to); end(); }}>
            {/* 실제 블록 비율(1000 × b.h)을 그대로 축소해 보여준다 */}
            <div className="mini-canvas" style={{ background: b.bg, aspectRatio: `1000 / ${b.h || 660}` }}>
              {b.elements.filter((e) => e.type === 'image' && e.src).map((e) => {
                const bh = b.h || 660;
                return <img key={e.id} src={e.src} style={{ left: (e.x / 1000) * 100 + '%', top: (e.y / bh) * 100 + '%', width: (e.w / 1000) * 100 + '%', height: (e.h / bh) * 100 + '%' }} alt="" draggable={false} />;
              })}
            </div>
          </div>
        </React.Fragment>
      ))}
      <div className={`mini-dropline${lineAt === blocks.length ? ' on' : ''}`} />
    </div>
  );
}

function Editor({ onExit }) {
  const [blocks, setBlocks] = useState(null);
  const [wardrobe, setWardrobe] = useState(null);
  const [catalogs, setCatalogs] = useState(null);
  const [account, setAccount] = useState(null);
  const [colorOpts, setColorOpts] = useState([]);
  const [productName, setProductName] = useState('');
  const [tab, setTab] = useState('ai');
  const [selBlock, setSelBlock] = useState(null);
  const [selEl, setSelEl] = useState(null);          // 기준(primary) 선택 — 우측 패널 표시용
  const [selEls, setSelEls] = useState([]);           // 선택 집합 (다중 선택, PRD §11.15)
  const [scale, setScale] = useState(0.4);
  const [leftHidden, setLeftHidden] = useState(false);
  const [rightHidden, setRightHidden] = useState(false);
  const [preview, setPreview] = useState(false);
  const [download, setDownload] = useState(false);
  const [dlFormat, setDlFormat] = useState('long');  // 다운로드 형식 선택
  const [backWarn, setBackWarn] = useState(false);
  const [genDot, setGenDot] = useState('none'); // none|busy|done
  const [frameOver, setFrameOver] = useState(null); // frame drag-insert target index
  const [frameDragging, setFrameDragging] = useState(false); // 프레임 드래그 중이면 드롭존을 넓힘
  const [pendingSlot, setPendingSlot] = useState(null); // {blockId, elId} — 의류 탭에서 채울 빈 칸
  const [hoverGray, setHoverGray] = useState(false);    // 캔버스 회색 빈 영역 호버 → 줌 표시 (item 4)
  const [layerFloat, setLayerFloat] = useState(null);   // 레이어 플로팅 패널이 열린 블록 id (item 5)
  const [layerPos, setLayerPos] = useState(null);       // 드래그로 옮긴 패널 위치 {x,y}
  const startLayerDrag = (e) => {
    if (e.target.closest('button')) return;             // X 버튼 클릭은 제외
    e.preventDefault();
    const panel = e.currentTarget.closest('.layer-float');
    const body = e.currentTarget.closest('.ed-body') || document.body;
    const pr = panel.getBoundingClientRect(), br = body.getBoundingClientRect();
    const offX = e.clientX - pr.left, offY = e.clientY - pr.top;
    const move = (ev) => {
      const x = Math.max(8, Math.min(br.width - pr.width - 8, ev.clientX - br.left - offX));
      const y = Math.max(8, Math.min(br.height - 44, ev.clientY - br.top - offY));
      setLayerPos({ x, y });
    };
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); document.body.style.userSelect = ''; };
    document.body.style.userSelect = 'none';
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
  };
  const toast = useToast();
  const wrapRef = useRef(null);
  const dragOrigins = useRef(null);   // group-move 시작 좌표 스냅샷
  // undo/redo history (rapid bursts within 350ms coalesce into one step)
  const hist = useRef({ past: [], future: [] });
  const prevBlocks = useRef(null);
  const fromHistory = useRef(false);
  const lastPush = useRef(0);

  useEffect(() => {
    Promise.all([api.getEditorBlocks(), api.getWardrobe(), api.getCatalogs(), api.getAccount(), api.getProduct()])
      .then(([b, w, c, a, p]) => {
        // give every block a FIXED initial height (so element drag/resize never grows it, #10)
        const withH = b.map((blk) => ({ ...blk, h: blk.h || Math.max(220, blk.elements.reduce((m, e) => Math.max(m, (e.y || 0) + (e.h || 40)), 0) + 50) }));
        setBlocks(withH); setWardrobe(w); setCatalogs(c); setAccount(a); setSelBlock(withH[0]?.id);
        setProductName(p.name || '제목 없는 상세페이지');
        // 색상 원은 입력 페이지에서 고른 색상만 나열 (콘티보드와 동일 규칙)
        const opts = (p.colors || []).filter((col) => col.images.length || col.isBase).map((col) => ({ id: col.id, label: col.name || '색상', hex: hexFor(col) }));
        setColorOpts(opts.length ? opts : [{ id: 'col1', label: '기본', hex: '#15141a' }]);
      });
  }, []);
  // autosave (mock)
  useEffect(() => { const t = setInterval(() => {}, 60000); return () => clearInterval(t); }, []);
  // track blocks history for undo/redo (skip changes that came from undo/redo itself)
  useEffect(() => {
    if (blocks == null) return;
    if (prevBlocks.current == null) { prevBlocks.current = blocks; return; }
    if (fromHistory.current) { fromHistory.current = false; prevBlocks.current = blocks; return; }
    const now = Date.now();
    if (now - lastPush.current > 350) {
      hist.current.past.push(prevBlocks.current);
      if (hist.current.past.length > 80) hist.current.past.shift();
      hist.current.future = [];
    }
    lastPush.current = now;
    prevBlocks.current = blocks;
  }, [blocks]);
  // delete key removes the whole selection (multi-select, PRD §11.15)
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

  // global shortcuts: Ctrl+Z / Shift+Ctrl+Z (undo/redo), Ctrl+S (save), T (add text)
  const kb = useRef({});
  useEffect(() => {
    const onKey = (e) => {
      const t = e.target;
      const typing = /input|textarea/i.test(t.tagName) || t.isContentEditable;
      const mod = e.ctrlKey || e.metaKey;
      if (mod && (e.key === 'z' || e.key === 'Z')) { e.preventDefault(); e.shiftKey ? kb.current.redo?.() : kb.current.undo?.(); return; }
      if (mod && (e.key === 'y' || e.key === 'Y')) { e.preventDefault(); kb.current.redo?.(); return; }
      if (mod && (e.key === 's' || e.key === 'S')) { e.preventDefault(); kb.current.save?.(); return; }
      // [ = 레이어 뒤로, ] = 레이어 앞으로 (선택 요소가 있을 때)
      if (!mod && !typing && e.key === '[' && kb.current.hasSel) { e.preventDefault(); kb.current.layer?.('down'); return; }
      if (!mod && !typing && e.key === ']' && kb.current.hasSel) { e.preventDefault(); kb.current.layer?.('up'); return; }
      if (!mod && !typing && (e.key === 't' || e.key === 'T' || e.key === 'ㅅ') && kb.current.canAddText) { e.preventDefault(); kb.current.addText?.(); }
    };
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey);
  }, []);
  // Ctrl/Cmd + wheel → zoom in 10% steps (PRD: 40% 기준 10% 단위 확대·축소)
  useEffect(() => {
    const wrap = wrapRef.current; if (!wrap) return;
    const onWheel = (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      setScale((s) => { const ns = e.deltaY < 0 ? s + 0.1 : s - 0.1; return Math.min(2, Math.max(0.1, +ns.toFixed(2))); });
    };
    wrap.addEventListener('wheel', onWheel, { passive: false });
    return () => wrap.removeEventListener('wheel', onWheel);
  }, [!!blocks && !!catalogs]);

  if (!blocks || !catalogs) return <div className="editor"><div style={{ margin: 'auto' }}><Icon name="loader" size={26} className="spin" /></div></div>;

  const selectedElObj = blocks.flatMap((b) => b.elements).find((e) => e.id === selEl);
  const visibleBlock = () => selBlock || blocks[0]?.id;

  const selectEl = (blockId, el, additive, keepTab) => {
    setSelBlock(blockId); setSelEl(el.id);
    setSelEls((cur) => additive ? (cur.includes(el.id) ? cur.filter((x) => x !== el.id) : [...cur, el.id]) : [el.id]);
    // 도형·선도 이미지처럼 취급 → 이미지 탭에서 동일 조절 (#5).
    // keepTab=true면 탭 전환 안 함 (레이어 패널에서 선택할 때)
    if (!keepTab) setTab(el.type === 'text' ? 'text' : 'image');
  };
  const clearSel = () => { setSelEl(null); setSelEls([]); };
  // group move (다중 선택 동시 이동) — origins snapshot on start, apply delta to all
  const groupStart = () => { const o = {}; blocks.forEach((b) => b.elements.forEach((e) => { if (selEls.includes(e.id)) o[e.id] = { x: e.x, y: e.y }; })); dragOrigins.current = o; };
  const groupMove = (dx, dy) => { const o = dragOrigins.current; if (!o) return; setBlocks((bs) => bs.map((b) => ({ ...b, elements: b.elements.map((e) => o[e.id] ? { ...e, x: Math.round(o[e.id].x + dx), y: Math.round(o[e.id].y + dy) } : e) }))); };
  const groupEnd = () => { dragOrigins.current = null; };
  const patchEl = (patch) => setBlocks((bs) => bs.map((b) => ({ ...b, elements: b.elements.map((e) => e.id === selEl ? { ...e, ...patch } : e) })));
  // patch a specific element (used by canvas drag/resize/rotate + inline text edit)
  const patchElById = (blockId, elId, patch) => setBlocks((bs) => bs.map((b) => b.id === blockId ? { ...b, elements: b.elements.map((e) => e.id === elId ? { ...e, ...patch } : e) } : b));
  // block background color (PRD §11.15)
  const changeBg = (blockId, color) => setBlocks((bs) => bs.map((b) => b.id === blockId ? { ...b, bg: color } : b));
  // resize a block's fixed height via the top/bottom pills (#11); top resize also shifts elements
  const reshapeBlock = (blockId, { h, shiftEls }) => setBlocks((bs) => bs.map((b) => {
    if (b.id !== blockId) return b;
    const els = shiftEls ? b.elements.map((e) => shiftEls[e.id] != null ? { ...e, y: shiftEls[e.id] } : e) : b.elements;
    return { ...b, h, elements: els };
  }));
  // mini-preview drag reorder (PRD §11.5)
  const reorderBlock = (from, to) => setBlocks((bs) => { const n = [...bs]; const [it] = n.splice(from, 1); n.splice(to, 0, it); return n; });
  // layer order for the selected element (PRD §11.15) — array order = paint order
  const layerEl = (dir) => setBlocks((bs) => bs.map((b) => {
    const i = b.elements.findIndex((e) => e.id === selEl); if (i < 0) return b;
    const els = [...b.elements]; const [it] = els.splice(i, 1);
    const j = dir === 'front' ? els.length : dir === 'back' ? 0 : dir === 'up' ? Math.min(els.length, i + 1) : Math.max(0, i - 1);
    els.splice(j, 0, it); return { ...b, elements: els };
  }));
  // layer panel: drag-reorder one element to another's stacking slot (PRD §14 P2 레이어 패널)
  const reorderLayer = (blockId, fromId, toId) => setBlocks((bs) => bs.map((b) => {
    if (b.id !== blockId) return b;
    const els = [...b.elements];
    const fi = els.findIndex((e) => e.id === fromId); if (fi < 0) return b;
    const [it] = els.splice(fi, 1);
    const ti = els.findIndex((e) => e.id === toId); if (ti < 0) return b;
    // dropped ONTO target row → take target's slot (target shifts toward back)
    els.splice(fi < ti ? ti + 1 : ti, 0, it);
    return { ...b, elements: els };
  }));
  // layer panel: toggle element visibility / lock
  const toggleElField = (blockId, elId, field) => setBlocks((bs) => bs.map((b) => b.id === blockId
    ? { ...b, elements: b.elements.map((e) => e.id === elId ? { ...e, [field]: !e[field] } : e) } : b));
  const moveBlock = (idx, dir) => setBlocks((bs) => { const n = [...bs]; const j = idx + dir; if (j < 0 || j >= n.length) return n; [n[idx], n[j]] = [n[j], n[idx]]; return n; });
  const addEmpty = (idx) => setBlocks((bs) => { const n = [...bs]; const nb = { id: DB.uid('b'), name: '빈 블록', kind: 'info', bg: '#ffffff', h: 300, elements: [] }; n.splice(idx + 1, 0, nb); return n; });
  const deleteBlock = (id) => { setBlocks((bs) => bs.filter((b) => b.id !== id)); toast.push('블록을 삭제했어요'); };
  const addFrame = (f, idx) => {
    // 프레임 내 이미지 칸은 '빈 칸'으로 추가 — 각 칸의 '이미지 추가'로 의류를 채운다 (item 3)
    const nb = { id: DB.uid('b'), name: f.label, kind: 'info', bg: '#ffffff', h: 580, elements:
      Array.from({ length: f.cols }).map((_, i) => ({ id: DB.uid('el'), type: 'image', x: 40 + i * (920 / f.cols), y: 60, w: 920 / f.cols - 20, h: 460, radius: 10 })) };
    setBlocks((bs) => { const n = [...bs]; n.splice(idx == null ? n.length : idx, 0, nb); return n; });
    toast.push(`${f.label} 프레임을 새 블록으로 추가했어요`);
  };
  // drop a frame (dragged from the 프레임 탭) between blocks → new block at that index (PRD §11.13)
  const onFrameDrop = (e, idx) => {
    e.preventDefault(); setFrameOver(null); setFrameDragging(false);
    const id = e.dataTransfer.getData('text/frame'); if (!id) return;
    const f = catalogs.frames.find((x) => x.id === id); if (f) addFrame(f, idx);
  };
  const addShape = (type, shapeId, bId, dropEvent) => {
    const target = bId || visibleBlock();
    // dropped onto a block → place near the cursor; otherwise block center
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
  // a frame's empty image slot asks for a garment → open 의류 탭 with this slot pending (item 3)
  const requestSlotImage = (blockId, el) => { setPendingSlot({ blockId, elId: el.id }); setTab('wardrobe'); };
  // 의류 탭에서 이미지 클릭 — 빈 칸이 대기 중이면 그 칸을 채우고, 아니면 새로 삽입
  const wardrobeInsert = (im) => {
    if (pendingSlot) {
      patchElById(pendingSlot.blockId, pendingSlot.elId, { src: im.src });
      setPendingSlot(null); setTab('image');
      toast.push('빈 칸에 이미지를 넣었어요');
    } else insertImage(im);
  };
  // 의류 목록에서만 삭제 (캔버스에 들어간 이미지는 유지, PRD §11.10)
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
  const varyImage = (im) => { setSelEl(im.id ? selEl : selEl); setTab('ai'); toast.push('현재 컷 변형으로 이동했어요', { icon: 'wand' }); };
  const jumpTo = (id) => { setSelBlock(id); setSelEl(null); setSelEls([]);
    const idx = blocks.findIndex((b) => b.id === id);
    const wrap = wrapRef.current; if (!wrap) return;
    const target = wrap.querySelectorAll('.canvas-block')[idx];
    if (target) { const wr = wrap.getBoundingClientRect(); const tr = target.getBoundingClientRect(); wrap.scrollTo({ top: wrap.scrollTop + (tr.top - wr.top) - 40, behavior: 'smooth' }); } };

  // add a text element to the given (or visible) block — toolbar button + 'T' shortcut
  const addText = (bId) => {
    const id = bId || visibleBlock();
    const el = { id: DB.uid('el'), type: 'text', x: 120, y: 80, w: 420, h: 60, text: '텍스트를 입력하세요',
      style: { font: 'Pretendard', size: 32, weight: 500, color: '#0e0d14' } };
    setBlocks((bs) => bs.map((b) => b.id === id ? { ...b, elements: [...b.elements, el] } : b));
    selectEl(id, el); setTab('text');
    toast.push('텍스트를 추가했어요');
  };
  // undo / redo (PRD §11.18)
  const undo = () => {
    const h = hist.current; if (!h.past.length) { toast.push('되돌릴 작업이 없어요'); return; }
    const snap = h.past.pop(); h.future.push(prevBlocks.current);
    fromHistory.current = true; clearSel(); setBlocks(snap); toast.push('실행 취소', { icon: 'undo' });
  };
  const redo = () => {
    const h = hist.current; if (!h.future.length) { toast.push('다시 실행할 작업이 없어요'); return; }
    const snap = h.future.pop(); h.past.push(prevBlocks.current);
    fromHistory.current = true; clearSel(); setBlocks(snap); toast.push('다시 실행', { icon: 'redo' });
  };
  const save = () => toast.push('저장했어요', { icon: 'check' });
  // keep latest action handlers + T-availability for the keyboard effect
  kb.current = { undo, redo, save, addText, canAddText: selEls.length === 0 && !!selBlock, layer: layerEl, hasSel: !!selEl };

  const TOOLS = [
    { id: 'ai', icon: 'sparkles', label: 'AI', dot: true },
    { id: 'wardrobe', icon: 'shirt', label: '의류' },
    { id: 'image', icon: 'image', label: '이미지' },
    { id: 'frame', icon: 'layout', label: '프레임' },
    { id: 'text', icon: 'type', label: '텍스트' },
    { id: 'shape', icon: 'shapes', label: '오브젝트' },
  ];
  // 선택 요소에 맞춰 좌측 패널 제목을 바꾼다 (보완: 도형/선을 '이미지'로 오인하지 않게)
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
      case 'image': return <ImagePanel el={selectedElObj} onChange={patchEl} onLayer={layerEl} />;
      case 'frame': return <FramePanel catalogs={catalogs} onAdd={addFrame} onDragStart={() => setFrameDragging(true)} onDragEnd={() => setFrameDragging(false)} />;
      case 'text': return <TextPanel el={selectedElObj} catalogs={catalogs} onChange={patchEl} onLayer={layerEl} onAddText={() => addText()} />;
      case 'shape': return <ShapePanel catalogs={catalogs} onAdd={addShape}
        block={(selEls.length === 0 && selBlock) ? blocks.find((b) => b.id === selBlock) : null} onBgChange={changeBg} />;
      default: return null;
    }
  };

  return (
    <div className="editor">
      {/* toolbar */}
      <div className="ed-toolbar">
        <button className="ed-tool" onClick={onExit} title="보관함으로" style={{ flexDirection: 'row', gap: 6 }}>
          <span className="brand" style={{ fontSize: 17 }}>wearless</span>
        </button>
        <div className="ed-divider" />
        <div className="ed-toolgroup">
          {TOOLS.map((t) => (
            <button key={t.id} className={`ed-tool${tab === t.id ? ' on' : ''}`} onClick={() => setTab(t.id)}>
              <span className="dotwrap"><Icon name={t.icon} size={19} />
                {t.dot && genDot !== 'none' && <span className="dot" style={{ position: 'absolute', top: -2, right: -3, background: genDot === 'busy' ? '#e6b800' : 'var(--link)', boxShadow: '0 0 0 1.5px #fff' }} />}
              </span>{t.label}
            </button>
          ))}
        </div>
        {/* 편집 중인 제품명 — 툴바 중앙 */}
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
      <div className="ed-body" style={{ '--lcol': leftHidden ? '0px' : '320px', '--rcol': rightHidden ? '0px' : '208px' }}>
        <div className={`ed-left${leftHidden ? ' hidden' : ''}`}>
          <div style={{ marginBottom: 14 }}>
            <span className="panel-h" style={{ marginBottom: 0 }}>{panelTitle}</span>
          </div>
          {renderPanel()}
        </div>

        <div className="ed-canvas-wrap" ref={wrapRef} onClick={() => clearSel()}
          onMouseMove={(e) => { const g = !e.target.closest('.canvas-block'); setHoverGray((v) => v === g ? v : g); }}
          onMouseLeave={() => setHoverGray(false)}>
          {/* 줌 표시 — 회색 빈 영역에 마우스가 있을 때만, 캔버스 우측 위에 sticky (item 4) */}
          <div className={`zoom-float${hoverGray ? ' show' : ''}`}>
            <div className="zoom-pill" onClick={(e) => e.stopPropagation()} onMouseMove={(e) => e.stopPropagation()}>
              <button onClick={() => setScale((s) => Math.max(0.1, +(s - 0.1).toFixed(2)))}><Icon name="minus" size={15} /></button>
              <span>{Math.round(scale * 100)}%</span>
              <button onClick={() => setScale((s) => Math.min(2, +(s + 0.1).toFixed(2)))}><Icon name="plus" size={15} /></button>
            </div>
          </div>
          {rightHidden && <div style={{ position: 'absolute', right: 10, top: 10, zIndex: 3 }}><IconButton name="layout" size="sm" onClick={() => setRightHidden(false)} /></div>}
          <div className={`ed-canvas${frameDragging ? ' frame-dragging' : ''}`} style={{ zoom: scale }}>
            {blocks.map((b, i) => (
              <React.Fragment key={b.id}>
                <div className="canvas-droprow" onDragOver={(e) => { if (e.dataTransfer.types.includes('text/frame')) { e.preventDefault(); setFrameOver(i); } }}
                  onDragLeave={() => setFrameOver((o) => o === i ? null : o)} onDrop={(e) => onFrameDrop(e, i)}>
                  <div className={`canvas-dropline${frameOver === i ? ' on' : ''}`} />
                </div>
                <CanvasBlock block={b} scale={scale} idx={i} total={blocks.length}
                  selectedBlockId={selBlock} selEls={selEls}
                  onSelectBlock={(id) => { setSelBlock(id); clearSel(); setTab('shape'); }} onSelectEl={selectEl}
                  onElPatch={patchElById} onAddImage={requestSlotImage} onOpenLayers={(id) => { setLayerFloat(id); setLayerPos(null); }}
                  onGroupStart={groupStart} onGroupMove={groupMove} onGroupEnd={groupEnd}
                  onObjectDrop={(bid, type, id, ev) => addShape(type, id, bid, ev)}
                  onReshape={reshapeBlock}
                  onMove={moveBlock} onAddEmpty={addEmpty} onDelete={deleteBlock}
                  onDownload={() => toast.push('이 블록을 PNG로 저장했어요', { icon: 'download' })} />
              </React.Fragment>
            ))}
            <div className="canvas-droprow" onDragOver={(e) => { if (e.dataTransfer.types.includes('text/frame')) { e.preventDefault(); setFrameOver(blocks.length); } }}
              onDragLeave={() => setFrameOver((o) => o === blocks.length ? null : o)} onDrop={(e) => onFrameDrop(e, blocks.length)}>
              <div className={`canvas-dropline${frameOver === blocks.length ? ' on' : ''}`} />
            </div>
          </div>
        </div>

        {!rightHidden && <MiniPreview blocks={blocks} selectedBlockId={selBlock} onJump={jumpTo} onReorder={reorderBlock} />}

        {/* 블록 퀵액션의 레이어 버튼 → 우측에 레이어 플로팅 패널 (item 5). 헤더를 홀드해 위치 이동 */}
        {layerFloat && blocks.find((b) => b.id === layerFloat) && (
          <div className="layer-float" style={layerPos ? { left: layerPos.x, top: layerPos.y, right: 'auto' } : undefined}>
            <div className="lf-head" onPointerDown={startLayerDrag}>
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

      {/* preview overlay (11.20) */}
      {preview && (
        <div className="preview-full">
          <div className="preview-close"><IconButton name="x" onClick={() => setPreview(false)} /></div>
          <div className="preview-sheet">
            {/* 모든 블록을 실제 높이 그대로 이어 붙여 하나의 상세페이지로 보여준다 */}
            {blocks.map((b) => (
              <div key={b.id} style={{ position: 'relative', height: b.h || 660, background: b.bg, overflow: 'hidden', boxSizing: 'border-box' }}>
                {b.elements.map((el) => <CanvasElement key={el.id} el={el} preview selected={false} onSelect={() => {}} />)}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* download modal (11.21) — 심플·고급 리디자인 */}
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

window.Editor = Editor;
