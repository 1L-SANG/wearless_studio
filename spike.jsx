/* Phase 0 스냅 스파이크 격리 픽스처 — Editor 의 실제 조건(transform:scale 캔버스 +
   rootContainer=언스케일 wrapper + 센티넬 + moveable snap props)을 auth/데이터 없이 재현.
   Editor.jsx 스파이크 하니스와 동일한 moveable 설정을 쓴다. */
import React, { useState, useEffect, useRef } from 'react';
import { createRoot } from 'react-dom/client';
import Moveable from 'react-moveable';

const SENTINELS = [40, 500, 960];
const CANVAS_H = 1400;

function Fixture() {
  const [scale, setScale] = useState(0.4);
  const [boxes, setBoxes] = useState([
    { id: 'a', x: 200, y: 180, w: 220, h: 150 },
    { id: 'b', x: 620, y: 430, w: 170, h: 130 },
  ]);
  const [sel, setSel] = useState('b');
  const [ratio, setRatio] = useState(false); // keepRatio 토글 — 실 에디터 기본은 true
  const [snap, setSnap] = useState(true);     // snappable 토글 (원인 격리용)
  const [guides, setGuides] = useState([]);
  const [tick, setTick] = useState(0); // 첫 마운트 후 target 재조회 강제
  const wrapRef = useRef(null);
  const mvRef = useRef(null);
  const live = useRef({});

  // effect-수집 guides (형제 박스 + 센티넬), identity 안정
  useEffect(() => {
    const wrap = wrapRef.current; if (!wrap) return;
    const sib = boxes.filter((b) => b.id !== sel).map((b) => wrap.querySelector(`[data-elid="${b.id}"]`)).filter(Boolean);
    const sents = Array.from(wrap.querySelectorAll('[data-snap-sentinel]'));
    setGuides([...sib, ...sents]);
  }, [sel, boxes, scale, tick]);

  useEffect(() => { setTick(1); }, []);

  const box = (id) => boxes.find((b) => b.id === id);
  const patch = (id, p) => setBoxes((bs) => bs.map((b) => (b.id === id ? { ...b, ...p } : b)));
  const target = wrapRef.current?.querySelector(`[data-elid="${sel}"]`) || null;

  useEffect(() => {
    window.__spike = {
      zoom: (s) => setScale(Math.min(2, Math.max(0.1, +(+s).toFixed(2)))),
      select: (id) => setSel(id),
      ratio: (v) => setRatio(!!v),
      snap: (v) => setSnap(!!v),
      guides: () => guides.length,
      lines: () => document.querySelectorAll('.moveable-line.moveable-bold, .moveable-line.moveable-dashed').length,
      pos: (id) => box(id),
      reset: () => setBoxes([{ id: 'a', x: 140, y: 120, w: 200, h: 140 }, { id: 'b', x: 470, y: 300, w: 150, h: 110 }]),
    };
  });

  return (
    <div ref={wrapRef} style={{ position: 'relative', width: '100vw', height: '100vh', overflow: 'auto', background: '#f3f3f5' }}>
      <div style={{ position: 'relative', width: 1000 * scale, height: CANVAS_H * scale, margin: '60px auto' }}>
        <div className="ed-canvas" style={{ transform: `scale(${scale})`, transformOrigin: 'top left', position: 'absolute', top: 0, left: 0, width: 1000, height: CANVAS_H, background: '#fff', boxShadow: '0 0 0 1px #ddd' }}>
          {boxes.map((b) => (
            <div key={b.id} data-elid={b.id} onMouseDown={() => setSel(b.id)}
              style={{ position: 'absolute', left: b.x, top: b.y, width: b.w, height: b.h, background: b.id === sel ? '#bcd8f5' : '#e6a6a6', border: '1px solid #333', boxSizing: 'border-box' }} />
          ))}
          {SENTINELS.map((x) => (
            <div key={x} data-snap-sentinel style={{ position: 'absolute', left: x, top: 0, width: 0, height: CANVAS_H, pointerEvents: 'none', opacity: 0 }} />
          ))}
        </div>
      </div>
      {target && (
        <Moveable ref={mvRef} target={target} rootContainer={wrapRef.current}
          draggable resizable keepRatio={ratio} origin={false} snappable={snap}
          renderDirections={['nw', 'n', 'ne', 'w', 'e', 'sw', 's', 'se']}
          throttleDrag={0} throttleResize={0}
          elementGuidelines={guides}
          snapDirections={{ top: true, left: true, bottom: true, right: true, center: true, middle: true }}
          elementSnapDirections={{ top: true, left: true, bottom: true, right: true, center: true, middle: true }}
          snapGap snapHorizontalThreshold={6} snapVerticalThreshold={6} isDisplaySnapDigit
          onDrag={(e) => {
            const s = box(sel);
            e.target.style.left = (s.x + e.beforeTranslate[0]) + 'px';
            e.target.style.top = (s.y + e.beforeTranslate[1]) + 'px';
            live.current = { left: parseFloat(e.target.style.left), top: parseFloat(e.target.style.top) };
          }}
          onDragEnd={() => { if (live.current.left != null) patch(sel, { x: Math.round(live.current.left), y: Math.round(live.current.top) }); live.current = {}; }}
          onResize={(e) => {
            const s = box(sel);
            e.target.style.width = e.width + 'px';
            e.target.style.height = e.height + 'px';
            e.target.style.left = (s.x + (e.drag.beforeTranslate[0] || 0)) + 'px';
            e.target.style.top = (s.y + (e.drag.beforeTranslate[1] || 0)) + 'px';
            live.current = { w: e.width, h: e.height, left: parseFloat(e.target.style.left), top: parseFloat(e.target.style.top) };
          }}
          onResizeEnd={() => { if (live.current.w != null) patch(sel, { w: Math.round(live.current.w), h: Math.round(live.current.h), x: Math.round(live.current.left), y: Math.round(live.current.top) }); live.current = {}; }}
        />
      )}
    </div>
  );
}
createRoot(document.getElementById('root')).render(<Fixture />);
