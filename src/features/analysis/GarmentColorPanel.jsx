import React, { useEffect, useRef, useState } from 'react';
import { AlertCircle, Check, ChevronsLeftRight, LoaderCircle } from 'lucide-react';
import { SLIDER_RANGE, SLIDER_SNAP, ZERO_SLIDERS, clampSlider, hexOf, isZeroSliders, sameSliders, toneSrgb8 } from '@/lib/garmentColorTone.js';
import styles from './GarmentColorPanel.module.css';

const AXES = [
  { key: 'chroma', name: '진하기', low: '연하게', high: '진하게' },
  { key: 'lightness', name: '밝기', low: '어둡게', high: '밝게' },
  { key: 'warmth', name: '따뜻함', low: '푸르게', high: '노랗게' },
];
const STEP = 10;
const CELLS = Array.from({ length: SLIDER_RANGE / STEP }, (_, index) => index);
const HOLD_DELAY_MS = 420;
const HOLD_REPEAT_MS = 110;
const signed = (value) => (value > 0 ? `+${value}` : value < 0 ? `−${-value}` : '0');
const cellFill = (value, index) => Math.max(0, Math.min(1, (value - index * STEP) / STEP));
/* 말 버튼 한 번: 그 방향의 다음 10 단위로. AI 값처럼 칸 사이에 있어도 한 번에 칸에 맞는다. */
const nextCell = (value, dir) => clampSlider(dir > 0 ? Math.floor(value / STEP) * STEP + STEP : Math.ceil(value / STEP) * STEP - STEP);

/* 한 항목: 양쪽 말 버튼은 한 칸씩(길게 누르면 계속), 가운데 칸 막대는 끌어서 1 단위로 오간다. */
function StepRow({ axis, value, disabled, onValue, onCommit }) {
  const latest = useRef(value);
  latest.current = value;
  const meter = useRef(null);
  const hold = useRef(null);
  const drag = useRef(false);
  const [dragging, setDragging] = useState(false);
  const stopHold = () => {
    if (!hold.current) return;
    clearTimeout(hold.current.timer); clearInterval(hold.current.repeat);
    hold.current = null;
    onCommit?.();
  };
  useEffect(() => () => { if (hold.current) { clearTimeout(hold.current.timer); clearInterval(hold.current.repeat); } }, []);
  const step = (dir) => {
    const next = nextCell(latest.current, dir);
    latest.current = next;
    onValue(next);
    if (Math.abs(next) >= SLIDER_RANGE && hold.current) { clearTimeout(hold.current.timer); clearInterval(hold.current.repeat); }
  };
  const nudge = (dir, word) => <button type="button" className={styles.nudge} tabIndex={-1} aria-label={`${axis.name} 한 칸 ${word}`}
    disabled={disabled || (dir < 0 ? value <= -SLIDER_RANGE : value >= SLIDER_RANGE)}
    onPointerDown={(event) => {
      if (event.button) return;
      stopHold(); step(dir);
      hold.current = { timer: setTimeout(() => { if (hold.current) hold.current.repeat = setInterval(() => step(dir), HOLD_REPEAT_MS); }, HOLD_DELAY_MS) };
    }}
    onPointerUp={stopHold} onPointerLeave={stopHold} onPointerCancel={stopHold}
    onClick={(event) => { if (event.detail === 0) { step(dir); onCommit?.(); } }}>{word}</button>;
  const fromPointer = (event) => {
    const rect = meter.current.getBoundingClientRect();
    const next = clampSlider(((event.clientX - rect.left) / rect.width) * 2 * SLIDER_RANGE - SLIDER_RANGE);
    onValue(Math.abs(next) <= SLIDER_SNAP ? 0 : next);
  };
  const endDrag = () => { if (!drag.current) return; drag.current = false; setDragging(false); onCommit?.(); };
  return <div className={styles.step} data-zero={value ? undefined : ''}>
    {nudge(-1, axis.low)}
    <div className={styles.stepMid}>
      <div className={styles.stepCap} aria-hidden="true"><span className={styles.stepName}>{axis.name}</span><span className={styles.stepValue}>{signed(value)}</span></div>
      <div ref={meter} className={styles.meter} role="slider" tabIndex={disabled ? -1 : 0} data-dragging={dragging ? '' : undefined} style={{ '--v': value }}
        aria-label={`${axis.name}. 왼쪽은 ${axis.low}, 오른쪽은 ${axis.high}`} aria-valuemin={-SLIDER_RANGE} aria-valuemax={SLIDER_RANGE} aria-valuenow={value}
        aria-valuetext={value ? `${value < 0 ? axis.low : axis.high} ${Math.abs(value)}` : '사진 그대로'} aria-disabled={disabled || undefined}
        onPointerDown={(event) => {
          if (disabled || event.button) return;
          event.currentTarget.setPointerCapture(event.pointerId);
          drag.current = true; setDragging(true); fromPointer(event);
        }}
        onPointerMove={(event) => { if (drag.current) fromPointer(event); }}
        onPointerUp={endDrag} onPointerCancel={endDrag} onLostPointerCapture={endDrag}
        onDoubleClick={() => { if (!disabled && value) onValue(0, { immediate: true }); }}
        onKeyDown={(event) => {
          if (disabled) return;
          const dir = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1 }[event.key];
          // 방향키는 1씩(원본 근처도 그대로), Shift와 함께면 한 칸씩.
          if (dir) onValue(event.shiftKey ? nextCell(value, dir) : clampSlider(value + dir));
          else if (event.key === 'Home') onValue(-SLIDER_RANGE);
          else if (event.key === 'End') onValue(SLIDER_RANGE);
          else return;
          event.preventDefault();
        }}
        onKeyUp={() => onCommit?.()} onBlur={() => onCommit?.()}>
        <span className={styles.cells} aria-hidden="true">
          {[...CELLS].reverse().map((index) => <i key={`low-${index}`} className={styles.low} style={{ '--f': cellFill(-value, index) }} />)}
          {CELLS.map((index) => <i key={`high-${index}`} style={{ '--f': cellFill(value, index) }} />)}
        </span>
        <span className={styles.zero} aria-hidden="true" />
        <span className={styles.mark} aria-hidden="true" />
      </div>
    </div>
    {nudge(1, axis.high)}
  </div>;
}

export const colorSourceSrgb = (color, source) => color?.sourceSrgb || source?.median || null;
export const colorSwatch = (color, source) => {
  const base = colorSourceSrgb(color, source);
  return base ? hexOf(toneSrgb8(base, color?.sliders || ZERO_SLIDERS)) : null;
};

/* 누끼 미리보기를 캔버스 칸 안에 맞춰 그린다(object-fit: contain과 같은 배치). */
export function ToneCanvas({ source, sliders, scale = 1, className, width, height, label }) {
  const ref = useRef(null);
  const work = useRef(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas || !source?.preview) return undefined;
    const frame = requestAnimationFrame(() => {
      const { preview } = source;
      if (!work.current || work.current.width !== preview.width || work.current.height !== preview.height) {
        const offscreen = document.createElement('canvas');
        offscreen.width = preview.width; offscreen.height = preview.height;
        const context = offscreen.getContext('2d');
        work.current = { canvas: offscreen, context, image: context.createImageData(preview.width, preview.height), width: preview.width, height: preview.height };
      }
      preview.render(sliders || ZERO_SLIDERS, work.current.image);
      work.current.context.putImageData(work.current.image, 0, 0);
      const context = canvas.getContext('2d');
      const fit = Math.min(canvas.width / preview.width, canvas.height / preview.height) * scale;
      const w = preview.width * fit, h = preview.height * fit;
      context.clearRect(0, 0, canvas.width, canvas.height);
      context.drawImage(work.current.canvas, (canvas.width - w) / 2, (canvas.height - h) / 2, w, h);
    });
    return () => cancelAnimationFrame(frame);
  }, [source, sliders?.chroma, sliders?.lightness, sliders?.warmth, scale, width, height]);
  return <canvas ref={ref} className={className} width={width} height={height} role={label ? 'img' : undefined} aria-label={label} aria-hidden={label ? undefined : true} />;
}

function Compare({ source, sliders }) {
  const [split, setSplit] = useState(50);
  const box = useRef(null);
  const dragging = useRef(false);
  const move = (event) => {
    const rect = box.current.getBoundingClientRect();
    setSplit(Math.max(4, Math.min(96, ((event.clientX - rect.left) / rect.width) * 100)));
  };
  return <>
    <div ref={box} className={styles.compare} style={{ '--split': `${split}%` }}
      onPointerDown={(event) => { dragging.current = true; event.currentTarget.setPointerCapture(event.pointerId); move(event); }}
      onPointerMove={(event) => { if (dragging.current) move(event); }}
      onPointerUp={() => { dragging.current = false; }} onPointerCancel={() => { dragging.current = false; }}>
      <ToneCanvas source={source} sliders={ZERO_SLIDERS} scale={0.82} className={styles.before} width={640} height={480} />
      <ToneCanvas source={source} sliders={sliders} scale={0.82} className={styles.after} width={640} height={480} />
      <span className={styles.divider} aria-hidden="true" />
      <span className={styles.handle} role="slider" tabIndex={0} aria-label="Before와 After 비교 위치" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(split)}
        onKeyDown={(event) => {
          if (event.key === 'ArrowLeft') { setSplit((value) => Math.max(4, value - 5)); event.preventDefault(); }
          if (event.key === 'ArrowRight') { setSplit((value) => Math.min(96, value + 5)); event.preventDefault(); }
        }}><ChevronsLeftRight size={18} aria-hidden="true" /></span>
    </div>
    <div className={styles.caption} aria-hidden="true"><span>Before</span><span className={styles.now}>After</span></div>
  </>;
}

export function GarmentColorPanel({ panelRef, colors, currentId, onSelect, onRetry, sources, onSliders, onCommit, onDone, drawer, style }) {
  const current = colors.find((color) => color.colorId === currentId) || colors[0];
  const source = current ? sources[current.colorId] : null;
  const sliders = current?.sliders || ZERO_SLIDERS;
  const unavailable = current?.status === 'unavailable';
  const failed = current?.status === 'error';
  const processing = !unavailable && !failed && !source?.preview;
  const state = unavailable ? 'unavailable' : failed ? 'error' : processing ? 'processing' : 'ready';
  const chips = useRef(null);
  const latest = useRef(sliders);
  latest.current = sliders;
  const autoApplied = Boolean(current?.auto) && !isZeroSliders(current.auto) && sameSliders(sliders, current.auto);
  // 길게 누르기는 다음 그리기 전에 또 부를 수 있어 마지막으로 보낸 값 위에 쌓는다.
  const setAxis = (key, value, options) => {
    const next = { ...latest.current, [key]: value };
    latest.current = next;
    onSliders(current.colorId, next, options);
  };
  const pick = (colorId) => onSelect(colorId);
  const reset = <button type="button" disabled={processing || isZeroSliders(sliders)} onClick={() => onSliders(current.colorId, ZERO_SLIDERS, { immediate: true })}>원래대로</button>;
  return <aside ref={panelRef} id="garment-color-panel" className={`${styles.panel} ${drawer ? styles.drawer : styles.push}`} style={style} data-state={state}
    tabIndex={-1} aria-labelledby="garment-color-title" role={drawer ? 'dialog' : undefined} aria-modal={drawer ? true : undefined}>
    <div className={styles.head}>
      <div><h2 id="garment-color-title">내 옷 색 맞추기</h2><p>실물과 같은지 확인해 주세요.</p></div>
      <button type="button" className={styles.done} onClick={onDone}>완료</button>
    </div>
    {colors.length > 1 && <div ref={chips} className={styles.chips} role="radiogroup" aria-label="색상 옵션"
      onKeyDown={(event) => {
        if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
        const index = colors.findIndex((color) => color.colorId === current.colorId);
        const next = colors[(index + (['ArrowRight', 'ArrowDown'].includes(event.key) ? 1 : colors.length - 1)) % colors.length];
        pick(next.colorId);
        requestAnimationFrame(() => chips.current?.querySelector(`[data-color-id="${CSS.escape(next.colorId)}"]`)?.focus());
        event.preventDefault();
      }}>
      {colors.map((color) => {
        const on = color.colorId === current.colorId; const swatch = colorSwatch(color, sources[color.colorId]);
        return <button key={color.colorId} type="button" className={styles.chip} role="radio" aria-checked={on} tabIndex={on ? 0 : -1}
          data-color-id={color.colorId} onClick={() => pick(color.colorId)}>
          <span className={styles.dot} style={swatch ? { '--swatch': swatch } : undefined} />{color.name}
        </button>;
      })}
    </div>}
    {state === 'processing' && <p className={styles.message} role="status"><LoaderCircle size={16} className={styles.spin} aria-hidden="true" />사진을 정리하는 중이에요.</p>}
    {state === 'unavailable' && <p className={styles.message} role="status"><AlertCircle size={16} aria-hidden="true" />옷 부분을 못 찾았어요. 사진 그대로 만들어요.</p>}
    {state === 'error' && <p className={styles.message} role="status"><AlertCircle size={16} aria-hidden="true" /><span>사진을 불러오지 못했어요.</span>
      <button type="button" className={styles.retry} onClick={() => onRetry?.(current.colorId)}>다시 시도</button></p>}
    {state === 'unavailable' || state === 'error'
      ? current?.photo && <img className={styles.photo} src={current.photo} alt="올린 원본 사진" />
      : <Compare source={source} sliders={sliders} />}
    {(state === 'unavailable' || state === 'error') && !isZeroSliders(sliders) && <div className={styles.adjustHead}>
      <p className={styles.savedNote}>전에 맞춘 색이 저장돼 있어요.</p><div className={styles.tools}>{reset}</div></div>}
    {state !== 'unavailable' && state !== 'error' && <>
      <div className={styles.adjustHead}><h3>색 조절</h3>
        <div className={styles.tools}>
          <button type="button" disabled={processing || !current?.auto} data-applied={autoApplied || undefined} onClick={() => onSliders(current.colorId, current.auto, { immediate: true })}>{autoApplied && <Check size={14} aria-hidden="true" />}AI 자동</button>
          {reset}
        </div>
      </div>
      {!processing && !current?.auto && <p className={styles.note} aria-live="polite">배경으로 조명을 알 수 없어 AI 자동을 쓸 수 없어요.</p>}
      <div className={styles.steps}>
        {AXES.map((axis) => <StepRow key={axis.key} axis={axis} value={sliders[axis.key]} disabled={processing}
          onValue={(value, options) => setAxis(axis.key, value, options)} onCommit={onCommit} />)}
      </div>
      {autoApplied && <span className="sr-only" role="status">AI 자동 값으로 맞췄어요</span>}
    </>}
  </aside>;
}
