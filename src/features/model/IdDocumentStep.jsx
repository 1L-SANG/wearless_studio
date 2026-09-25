/* 상태 순서: phase, imageUrl, capturedBlob, busy, localError, imageLoaded.
   카메라와 앨범 모두 단말에서 직접 가린 사진만 확인 후 올려요. */
import { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import { uploadIdDocument } from '@/lib/api/facemarket.js';
import { toUploadableImage } from '../../lib/imageTranscode.js';
import IdCameraCapture from './IdCameraCapture.jsx';
import IdGalleryFit from './IdGalleryFit.jsx';
import IdMaskEditor from './IdMaskEditor.jsx';
import { ID_DOCUMENT_TYPES } from './idDocumentMasking.js';
import s from './ModelRegister.module.css';

const ACCEPTED_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/heic', 'image/heif'];
const ACCEPT_ATTR = `${ACCEPTED_IMAGE_TYPES.join(',')},.heic,.heif`;
const CAMERA_UNAVAILABLE_MESSAGES = {
  permission: '카메라 권한이 없어서 촬영할 수 없어요. 사진을 선택해서 올려 주세요.',
  unsupported: '이 브라우저에서는 카메라 촬영을 지원하지 않아요. 사진을 선택해서 올려 주세요.',
};

export default function IdDocumentStep({ enrollmentId, onUploaded, onStale }) {
  const [phase, setPhase] = useState('camera');
  const [imageUrl, setImageUrl] = useState(null);
  const [capturedBlob, setCapturedBlob] = useState(null);
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState('');
  const [imageLoaded, setImageLoaded] = useState(false);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [maskRegion, setMaskRegion] = useState(null);
  const [maskConfirmed, setMaskConfirmed] = useState(false);
  const inputRef = useRef(null), sheetRef = useRef(null), mounted = useRef(true);
  const inFlight = useRef(false), busyRef = useRef(false), retakeRef = useRef(null);
  busyRef.current = busy;
  const sheetOpen = phase !== 'choose';
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { if (imageUrl) return () => URL.revokeObjectURL(imageUrl); }, [imageUrl]);
  const clearMasked = () => { setCapturedBlob(null); setPreviewUrl(null); setMaskRegion(null); setMaskConfirmed(false); setImageLoaded(false); };
  const discard = next => { setImageUrl(null); clearMasked(); setLocalError(''); setPhase(next); };
  useEffect(() => {
    if (!sheetOpen || typeof document === 'undefined') return undefined;
    const previousFocus = document.activeElement;
    const previousOverflow = document.documentElement.style.overflow;
    document.documentElement.style.overflow = 'hidden';
    const onKey = event => {
      if (event.key === 'Escape' && !busyRef.current) { event.preventDefault(); discard('choose'); }
      if (event.key !== 'Tab') return;
      const nodes = [...(sheetRef.current?.querySelectorAll('button:not(:disabled), input:not(:disabled), [tabindex="0"]') || [])];
      if (!nodes.length) { event.preventDefault(); sheetRef.current?.focus?.({ preventScroll: true }); return; }
      const first = nodes[0], last = nodes.at(-1);
      if (!sheetRef.current?.contains(document.activeElement) || document.activeElement === sheetRef.current) {
        event.preventDefault(); (event.shiftKey ? last : first)?.focus(); return;
      }
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.documentElement.style.overflow = previousOverflow;
      document.removeEventListener('keydown', onKey);
      previousFocus?.focus?.({ preventScroll: true });
    };
  }, [sheetOpen]);
  useEffect(() => {
    sheetRef.current?.querySelector('button')?.focus?.({ preventScroll: true });
  }, [phase]);
  useEffect(() => { if (busy) sheetRef.current?.focus?.({ preventScroll: true }); }, [busy]);
  useEffect(() => { if (previewUrl) return () => URL.revokeObjectURL(previewUrl); }, [previewUrl]);
  const captured = blob => {
    if (!blob?.size) { setLocalError('사진을 저장하지 못했어요. 다시 찍어 주세요.'); return; }
    clearMasked(); setImageUrl(URL.createObjectURL(blob)); setLocalError(''); setPhase('mask');
  };
  const masked = (blob, region) => {
    if (!blob?.size || !region) { setLocalError('가린 사진을 만들지 못했어요. 다시 시도해 주세요.'); return; }
    setCapturedBlob(blob); setPreviewUrl(URL.createObjectURL(blob)); setMaskRegion(region);
    setMaskConfirmed(false); setImageLoaded(false); setLocalError(''); setPhase('review');
  };
  const openGallery = () => {
    if (inFlight.current) return;
    discard('choose');
    inputRef.current?.click();
  };
  const pickFile = async event => {
    const file = event.target.files?.[0]; event.target.value = '';
    if (!file || inFlight.current) return;
    if (file.type && !ACCEPTED_IMAGE_TYPES.includes(file.type) && !/\.hei[cf]$/i.test(file.name || '')) {
      setLocalError('JPG, PNG, WebP 또는 아이폰 사진을 골라 주세요.'); return;
    }
    inFlight.current = true; setBusy(true); setLocalError('');
    try {
      const converted = await toUploadableImage(file);
      if (!mounted.current) return;
      setCapturedBlob(null); setImageUrl(URL.createObjectURL(converted)); setPhase('fit');
    } catch (error) { if (mounted.current) setLocalError(error.message || '사진을 열지 못했어요. 다시 골라 주세요.'); }
    finally { inFlight.current = false; if (mounted.current) setBusy(false); }
  };
  const upload = async () => {
    if (phase !== 'review' || !capturedBlob?.size || !maskRegion || !maskConfirmed || !imageLoaded || !enrollmentId || inFlight.current) return;
    inFlight.current = true; setBusy(true); setLocalError('');
    try {
      const current = await uploadIdDocument(enrollmentId, { file: capturedBlob, documentType: ID_DOCUMENT_TYPES[0].value, maskedConfirmed: maskConfirmed, maskRegion });
      if (mounted.current) await onUploaded?.(current);
    } catch (error) {
      if (!mounted.current) return;
      if (error?.status === 409) {
        try { await onStale?.(error); return; }
        catch (refreshError) { error = refreshError; }
        if (!mounted.current) return;
      }
      setLocalError(error?.code === 'id_face_not_detected' ? '신분증 얼굴이 보이게 다시 찍어 주세요.' : error?.message || '신분증 업로드에 실패했어요. 잠시 후 다시 시도해 주세요.');
      if (error?.code === 'id_face_not_detected') retakeRef.current?.focus?.({ preventScroll: true });
    } finally { inFlight.current = false; if (mounted.current) setBusy(false); }
  };
  return <>
    <input ref={inputRef} className={s.fileInput} type="file" accept={ACCEPT_ATTR} aria-label="앨범에서 신분증 사진 선택" disabled={busy} onChange={pickFile} />
    {!sheetOpen && <div className={s.idChoose}>
      {localError && <p role="alert" className={s.error}>{localError}</p>}
      {busy && <p role="status">사진을 준비하고 있어요.</p>}
      <button type="button" className={s.primary} disabled={busy} onClick={() => discard('camera')}>카메라로 촬영</button>
      <button type="button" className={s.secondary} disabled={busy} onClick={openGallery}>앨범에서 사진 선택</button>
    </div>}
    {sheetOpen && <div className={s.idCameraSheet} role="dialog" tabIndex={-1} aria-modal="true" aria-labelledby="id-camera-title" ref={sheetRef}>
      <div className={s.idCameraHeader}>
        <button type="button" className={s.idClose} aria-label="촬영 닫기" disabled={busy} onClick={() => discard('choose')}><X size={24} aria-hidden="true" /></button>
        <h2 id="id-camera-title">{phase === 'mask' ? '주민번호 가리기' : phase === 'review' ? '가린 사진 확인' : '주민등록증 촬영'}</h2><span />
      </div>
      {phase === 'camera' && <IdCameraCapture busy={busy} onCaptured={captured} onGallery={openGallery} onUnavailable={reason => { discard('choose'); setLocalError(CAMERA_UNAVAILABLE_MESSAGES[reason] || CAMERA_UNAVAILABLE_MESSAGES.unsupported); }} />}
      {phase === 'fit' && <IdGalleryFit key={imageUrl} imageUrl={imageUrl} onCaptured={captured} onGallery={openGallery} />}
      {phase === 'mask' && <IdMaskEditor key={imageUrl} imageUrl={imageUrl} onMasked={masked} onRetake={() => discard('camera')} />}
      {phase === 'review' && <>
        <div className={s.idCameraStage}><img className={s.idReviewImage} src={previewUrl} alt="주민등록번호를 가린 신분증" onLoad={() => setImageLoaded(true)} onError={() => { setImageLoaded(false); setLocalError('사진을 열지 못했어요. 다시 찍어 주세요.'); }} /></div>
        <div className={s.idCameraHint}><label className={s.idMaskConfirmation} htmlFor="id-mask-confirmed"><input id="id-mask-confirmed" type="checkbox" checked={maskConfirmed} disabled={busy || !imageLoaded} onChange={event => setMaskConfirmed(event.target.checked)} /><span>주민등록번호 뒤 7자리가 모두 가려졌고, 이름과 얼굴 사진은 보여요.</span></label></div>
        <div className={`${s.idCameraControls} ${s.idFinalControls}`}>
          <button type="button" className={s.secondary} ref={retakeRef} disabled={busy} onClick={() => discard('camera')}>다시 찍기</button>
          <button type="button" className={s.secondary} disabled={busy} onClick={() => { clearMasked(); setLocalError(''); setPhase('mask'); }}>다시 가리기</button>
          <button type="button" className={s.primary} disabled={busy || !imageLoaded || !maskConfirmed} onClick={upload}>{busy ? '올리는 중이에요' : '이 사진으로 확인 요청'}</button>
          <button type="button" className={s.secondary} disabled={busy} onClick={() => discard('choose')}>삭제</button>
        </div>
      </>}
      {localError && <p className={s.idSheetError} role="alert">{localError}</p>}
    </div>}
  </>;
}
