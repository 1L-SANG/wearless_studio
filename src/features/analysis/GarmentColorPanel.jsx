import React, { forwardRef, useEffect, useRef, useState } from 'react';
import { AlertCircle, ChevronsLeftRight, LoaderCircle } from 'lucide-react';
import { ZERO_SLIDERS, hexOf, isZeroSliders, sameSliders, sliderTrack, toneSrgb8 } from '@/lib/garmentColorTone.js';
import styles from './GarmentColorPanel.module.css';

const SLIDERS = [
  { key: 'chroma', name: '진하기', label: '진하기. 왼쪽은 연하게, 오른쪽은 진하게' },
  { key: 'lightness', name: '밝기', label: '밝기. 왼쪽은 어둡게, 오른쪽은 밝게' },
  { key: 'warmth', name: '따뜻함', label: '따뜻함. 왼쪽은 푸르게, 오른쪽은 노랗게' },
];

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

export const GarmentColorPanel = forwardRef(function GarmentColorPanel({
  colors, currentId, onSelect, sources, onSliders, onDone, drawer, style,
}, ref) {
  const current = colors.find((color) => color.colorId === currentId) || colors[0];
  const source = current ? sources[current.colorId] : null;
  const sliders = current?.sliders || ZERO_SLIDERS;
  const unavailable = current?.status === 'unavailable';
  const processing = !unavailable && !source?.preview;
  const state = unavailable ? 'unavailable' : processing ? 'processing' : 'ready';
  const base = colorSourceSrgb(current, source);
  const chips = useRef(null);
  const pick = (colorId) => onSelect(colorId);
  return <aside ref={ref} id="garment-color-panel" className={`${styles.panel} ${drawer ? styles.drawer : styles.push}`} style={style} data-state={state}
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
    {state === 'unavailable'
      ? current?.photo && <img className={styles.photo} src={current.photo} alt="올린 원본 사진" />
      : <Compare source={source} sliders={sliders} />}
    {state !== 'unavailable' && <>
      <div className={styles.adjustHead}><h3>색 조절</h3>
        <div className={styles.tools}>
          <button type="button" disabled={processing || !current?.auto} onClick={() => onSliders(current.colorId, current.auto, { immediate: true })}>AI 자동</button>
          <button type="button" disabled={processing || isZeroSliders(sliders)} onClick={() => onSliders(current.colorId, ZERO_SLIDERS, { immediate: true })}>원래대로</button>
        </div>
      </div>
      {!processing && !current?.auto && <p className={styles.note} aria-live="polite">배경으로 조명을 알 수 없어 AI 자동을 쓸 수 없어요.</p>}
      <div className={styles.sliders}>
        {SLIDERS.map(({ key, name, label }) => <label key={key} className={styles.slider}>
          <span className={styles.sliderName}>{name}</span>
          <input type="range" min={-100} max={100} step={1} value={sliders[key]} aria-label={label} disabled={processing}
            style={base ? { '--track': sliderTrack(base, sliders, key) } : undefined}
            onChange={(event) => onSliders(current.colorId, { ...sliders, [key]: Number(event.target.value) })}
            onDoubleClick={() => { if (sliders[key]) onSliders(current.colorId, { ...sliders, [key]: 0 }, { immediate: true }); }} />
        </label>)}
      </div>
      {current?.auto && sameSliders(sliders, current.auto) && <span className="sr-only" role="status">AI 자동 값으로 맞췄어요</span>}
    </>}
  </aside>;
});
