import { useEffect, useRef, useState } from 'react';
import { frameGalleryPhoto, galleryImageRect, GALLERY_FRAME, setCaptureFrameStyle } from './idGalleryFraming.js';
import s from './ModelRegister.module.css';

export default function IdGalleryFit({ imageUrl, onCaptured, onGallery }) {
  const [loaded, setLoaded] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const imageRef = useRef(null), viewportRef = useRef(null), dragRef = useRef(null);
  const active = useRef(true), inFlight = useRef(false);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    const viewport = viewportRef.current, image = imageRef.current;
    if (!viewport) return;
    setCaptureFrameStyle(viewport, GALLERY_FRAME.width, GALLERY_FRAME.height);
    if (!loaded || !image) return;
    const rect = galleryImageRect(image.naturalWidth, image.naturalHeight, zoom, offset);
    for (const [key, value] of Object.entries(rect)) image.style.setProperty(`--image-${key}`, `${value / (['x', 'w'].includes(key) ? GALLERY_FRAME.width : GALLERY_FRAME.height) * 100}%`);
  }, [loaded, zoom, offset]);
  const capture = async () => {
    if (!loaded || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError('');
    try {
      const blob = await frameGalleryPhoto(document.createElement('canvas'), document.createElement('canvas'), imageRef.current, zoom, offset);
      if (active.current) onCaptured(blob);
    } catch { if (active.current) setError('사진을 저장하지 못했어요. 다시 골라 주세요.'); }
    finally { inFlight.current = false; if (active.current) setBusy(false); }
  };
  return <>
    <div className={s.idCameraStage}>
      <div className={`${s.idCameraViewport} ${s.idGalleryViewport}`} ref={viewportRef}
        onPointerDown={event => {
          if (!loaded || busy) return;
          event.currentTarget.setPointerCapture?.(event.pointerId);
          dragRef.current = { x: event.clientX, y: event.clientY, offset, width: event.currentTarget.getBoundingClientRect().width };
        }}
        onPointerMove={event => {
          const drag = dragRef.current;
          if (!drag?.width || busy) return;
          const scale = GALLERY_FRAME.width / drag.width;
          setOffset({ x: drag.offset.x + (event.clientX - drag.x) * scale, y: drag.offset.y + (event.clientY - drag.y) * scale });
        }}
        onPointerUp={() => { dragRef.current = null; }} onPointerCancel={() => { dragRef.current = null; }}
        tabIndex={0} role="group" aria-label="신분증 사진 위치 조절" onKeyDown={event => {
          const delta = { ArrowLeft: [-20, 0], ArrowRight: [20, 0], ArrowUp: [0, -20], ArrowDown: [0, 20] }[event.key];
          if (!delta || busy) return;
          event.preventDefault(); setOffset(current => ({ x: current.x + delta[0], y: current.y + delta[1] }));
        }}>
        <img className={s.idGalleryImage} ref={imageRef} src={imageUrl} alt="선택한 신분증" draggable={false} onLoad={() => setLoaded(true)} onError={() => { setLoaded(false); setError('사진을 열지 못했어요. 다른 사진을 골라 주세요.'); }} />
        <div className={s.idCameraGuide} aria-hidden="true" />
      </div>
    </div>
    <div className={s.idCameraHint}>
      <p>사진을 끌어서 신분증을 네모 안에 맞춰 주세요.</p>
      <p className={s.idCameraSubHint}>주민등록번호 뒷자리는 가리고 올려도 돼요.</p>
      <label className={s.idZoom}>사진 크기<input type="range" min="0.5" max="4" step="0.01" value={zoom} disabled={busy || !loaded} onChange={event => setZoom(Number(event.target.value))} /></label>
      {error && <p className={s.idCaptureError} role="alert">{error}</p>}
    </div>
    <div className={`${s.idCameraControls} ${s.idReviewControls}`}>
      <button type="button" className={s.secondary} disabled={busy} onClick={onGallery}>앨범</button>
      <button type="button" className={s.primary} disabled={busy || !loaded} onClick={capture}>{busy ? '준비하고 있어요' : '이 사진 쓰기'}</button>
      <span aria-hidden="true" />
    </div>
  </>;
}
