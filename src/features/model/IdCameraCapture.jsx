/* 신분증은 사용자가 직접 촬영해요. 안내 틀과 실제 프레임의 좌표를 맞춰요. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { setCaptureFrameStyle } from './idGalleryFraming.js';
import { captureFrameBlob } from './idDocumentMasking.js';
import s from './ModelRegister.module.css';

export default function IdCameraCapture({ onCaptured, onUnavailable, busy, onGallery }) {
  const videoRef = useRef(null);
  const shotRef = useRef(null);
  const streamRef = useRef(null);
  const capturingRef = useRef(false);
  const mountedRef = useRef(true);
  const busyRef = useRef(busy);
  const [ready, setReady] = useState(false);
  const [frameSize, setFrameSize] = useState(null);
  const [captureError, setCaptureError] = useState(false);
  const onCapturedRef = useRef(onCaptured);
  useEffect(() => { onCapturedRef.current = onCaptured; });
  const onUnavailableRef = useRef(onUnavailable);
  useEffect(() => { onUnavailableRef.current = onUnavailable; });
  useEffect(() => { busyRef.current = busy; });
  useEffect(() => {
    let cancelled = false;
    const video = videoRef.current;
    const captureFrameSize = () => {
      if (video?.videoWidth && video?.videoHeight) {
        setFrameSize({ width: video.videoWidth, height: video.videoHeight });
      }
    };
    video?.addEventListener('loadedmetadata', captureFrameSize);
    video?.addEventListener('resize', captureFrameSize);
    (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 } },
          audio: false,
        });
        if (cancelled) { stream.getTracks().forEach((t) => t.stop()); return; }
        streamRef.current = stream;
        if (video) {
          video.srcObject = stream;
          await video.play();
          if (cancelled) return;
          captureFrameSize();
        }
        if (cancelled) return;
        setReady(true);
      } catch (error) {
        if (cancelled) return;
        onUnavailableRef.current?.(error?.name === 'NotAllowedError' ? 'permission' : 'unsupported');
      }
    })();
    return () => {
      cancelled = true;
      video?.removeEventListener('loadedmetadata', captureFrameSize);
      video?.removeEventListener('resize', captureFrameSize);
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    };
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);
  const capture = useCallback(() => {
    const video = videoRef.current;
    const canvas = shotRef.current;
    if (!video || !canvas || capturingRef.current || busyRef.current) return;
    const width = video.videoWidth;
    const height = video.videoHeight;
    if (!width || !height) return;
    capturingRef.current = true;
    setCaptureError(false);
    try {
      captureFrameBlob(canvas, video, width, height)
        .then((blob) => {
          if (!mountedRef.current) return;
          onCapturedRef.current?.(blob);
        })
        .catch(() => {
          if (mountedRef.current) setCaptureError(true);
        })
        .finally(() => { capturingRef.current = false; });
    } catch {
      capturingRef.current = false;
      if (mountedRef.current) setCaptureError(true);
    }
  }, []);

  const viewportRef = useRef(null);
  const shutterRef = useRef(null);
  useEffect(() => { if (ready) shutterRef.current?.focus?.({ preventScroll: true }); }, [ready]);
  useEffect(() => {
    if (frameSize && viewportRef.current) setCaptureFrameStyle(viewportRef.current, frameSize.width, frameSize.height);
  }, [frameSize]);

  return <>
    <div className={s.idCameraStage}>
      <div className={s.idCameraViewport} ref={viewportRef}>
        <video ref={videoRef} playsInline muted className={s.idCameraVideo} />
        {frameSize && <div className={s.idCameraGuide} aria-hidden="true" />}
      </div>
      <canvas ref={shotRef} hidden />
    </div>
    <div className={s.idCameraHint}>
      <p>주민등록증을 네모 안에 맞추고 촬영해 주세요.</p>
      <p className={s.idCameraSubHint}>촬영 후 화면을 터치해서 뒷자리를 가릴 가림막 박스를 만들어 주세요.</p>
      {captureError && <p className={s.idCaptureError} role="alert">사진을 저장하지 못했어요. 다시 찍어 주세요.</p>}
    </div>
    <div className={s.idCameraControls}>
      <button type="button" className={s.secondary} disabled={busy} onClick={onGallery}>앨범</button>
      <button type="button" className={s.idShutter} ref={shutterRef} aria-label="촬영하기" disabled={busy || !ready} onClick={capture} autoFocus />
      <span aria-hidden="true" />
    </div>
  </>;
}
