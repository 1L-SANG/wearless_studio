import { useEffect, useRef, useState } from 'react';
import { buildMaskedBlob, clampMaskRatio } from './idDocumentMasking.js';
import s from './ModelRegister.module.css';

export default function IdMaskEditor({ imageUrl, onMasked, onRetake }) {
  const [loaded, setLoaded] = useState(false);
  const [mask, setMask] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const imageRef = useRef(null), viewportRef = useRef(null), maskRef = useRef(null);
  const dragRef = useRef(null), active = useRef(true), inFlight = useRef(false);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    if (!mask || !maskRef.current) return;
    for (const [key, value] of Object.entries(mask)) maskRef.current.style.setProperty(`--mask-${key}`, `${value * 100}%`);
  }, [mask]);
  const startDrag = (event, mode) => {
    if (!loaded || busy) return;
    event.preventDefault(); event.stopPropagation();
    const box = viewportRef.current.getBoundingClientRect();
    if (!box.width || !box.height) return;
    const chosen = mode === 'place' ? clampMaskRatio({
      xr: (event.clientX - box.left) / box.width - (mask?.wr || .3) / 2,
      yr: (event.clientY - box.top) / box.height - (mask?.hr || .06) / 2,
      wr: mask?.wr || .3, hr: mask?.hr || .06,
    }) : mask;
    if (!chosen) return;
    setMask(chosen);
    event.currentTarget.setPointerCapture?.(event.pointerId);
    dragRef.current = { mode, x: event.clientX, y: event.clientY, box, mask: chosen };
  };
  const move = event => {
    const drag = dragRef.current;
    if (!drag || busy) return;
    const dx = (event.clientX - drag.x) / drag.box.width;
    const dy = (event.clientY - drag.y) / drag.box.height;
    setMask(clampMaskRatio(drag.mode === 'resize'
      ? { ...drag.mask, wr: drag.mask.wr + dx, hr: drag.mask.hr + dy }
      : { ...drag.mask, xr: drag.mask.xr + dx, yr: drag.mask.yr + dy }));
  };
  const apply = async () => {
    if (!loaded || !mask || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError('');
    try {
      const blob = await buildMaskedBlob(document.createElement('canvas'), imageRef.current, mask);
      if (!blob?.size) throw new Error('가린 사진을 저장하지 못했어요. 다시 시도해 주세요.');
      if (active.current) onMasked(blob, mask);
    } catch (failure) { if (active.current) setError(failure.message || '가린 사진을 만들지 못했어요.'); }
    finally { inFlight.current = false; if (active.current) setBusy(false); }
  };
  return <>
    <div className={s.idCameraStage}>
      <div className={`${s.idCameraViewport} ${s.idMaskViewport}`} ref={viewportRef}
        onPointerDown={event => startDrag(event, 'place')} onPointerMove={move}
        onPointerUp={() => { dragRef.current = null; }} onPointerCancel={() => { dragRef.current = null; }}
        tabIndex={0} role="group" aria-label="주민등록번호 가릴 위치 지정" onKeyDown={event => {
          if (!loaded || busy) return;
          if (!mask && ['Enter', ' '].includes(event.key)) {
            event.preventDefault(); setMask({ xr: .35, yr: .47, wr: .3, hr: .06 }); return;
          }
          const delta = { ArrowLeft: [-.01, 0], ArrowRight: [.01, 0], ArrowUp: [0, -.01], ArrowDown: [0, .01] }[event.key];
          if (!mask || !delta) return;
          event.preventDefault(); setMask(clampMaskRatio({ ...mask, xr: mask.xr + delta[0], yr: mask.yr + delta[1] }));
        }}>
        <img ref={imageRef} src={imageUrl} className={s.idMaskImage} alt="주민등록번호를 가릴 사진" draggable={false}
          onLoad={() => {
            const image = imageRef.current;
            if (!image?.naturalWidth || !image?.naturalHeight) return;
            viewportRef.current.style.setProperty('--frame-aspect', `${image.naturalWidth} / ${image.naturalHeight}`);
            viewportRef.current.style.setProperty('--frame-ratio', String(image.naturalWidth / image.naturalHeight));
            setLoaded(true);
          }} onError={() => { setLoaded(false); setError('사진을 열지 못했어요. 다시 찍어 주세요.'); }} />
        {loaded && mask && <div className={s.idManualMask} ref={maskRef} onPointerDown={event => startDrag(event, 'move')}>
          <span className={s.idMaskHandle} aria-hidden="true" onPointerDown={event => startDrag(event, 'resize')} />
        </div>}
      </div>
    </div>
    <div className={s.idCameraHint}>
      <p>{mask ? '검은 박스를 움직이고 크기를 조절해 뒤 7자리를 모두 가려 주세요.' : '주민등록번호 뒤 7자리를 눌러 가릴 위치를 정해 주세요.'}</p>
      <p className={s.idCameraSubHint}>이름과 얼굴 사진은 보이게 남겨 주세요. 사진은 아직 전송되지 않았어요.</p>
      <div className={`${s.idMaskSliders} ${mask ? '' : s.idMaskSlidersEmpty}`} aria-hidden={!mask}>
        <label className={s.idZoom}>가림 폭<input type="range" min=".02" max="1" step=".01" value={mask?.wr || .3} disabled={busy || !mask} onChange={e => mask && setMask(clampMaskRatio({ ...mask, wr: Number(e.target.value) }))} /></label>
        <label className={s.idZoom}>가림 높이<input type="range" min=".02" max="1" step=".01" value={mask?.hr || .06} disabled={busy || !mask} onChange={e => mask && setMask(clampMaskRatio({ ...mask, hr: Number(e.target.value) }))} /></label>
      </div>
      {error && <p role="alert" className={s.idCaptureError}>{error}</p>}
    </div>
    <div className={`${s.idCameraControls} ${s.idMaskControls}`}>
      <button type="button" className={s.secondary} disabled={busy} onClick={onRetake}>다시 찍기</button>
      <button type="button" className={s.primary} disabled={busy || !loaded || !mask} onClick={apply}>{busy ? '가리는 중이에요' : '가린 결과 확인'}</button>
    </div>
  </>;
}
