/* 카메라와 앨범 모두 단말에서 직접 가린 사진을 제출한다. */
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
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState('');
  const inputRef = useRef(null), sheetRef = useRef(null), mounted = useRef(true);
  const inFlight = useRef(false), busyRef = useRef(false);
  busyRef.current = busy;
  const sheetOpen = phase !== 'choose';
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { if (imageUrl) return () => URL.revokeObjectURL(imageUrl); }, [imageUrl]);
  const discard = next => { setImageUrl(null); setLocalError(''); setPhase(next); };
  useEffect(() => {
    if (!sheetOpen || typeof document === 'undefined') return undefined;
    const previousFocus = document.activeElement;
    const previousOverflow = document.documentElement.style.overflow;
    document.documentElement.style.overflow = 'hidden';
    // iOS 하단 주소창과 키보드가 차지하는 영역을 제외한 실제 보이는 높이를 따른다.
    const viewport = globalThis.window?.visualViewport;
    const fitVisibleViewport = () => {
      const height = viewport?.height || globalThis.window?.innerHeight;
      if (!height) return;
      sheetRef.current?.style?.setProperty('--id-visible-height', `${height}px`);
      sheetRef.current?.style?.setProperty('--id-visible-top', `${viewport?.offsetTop || 0}px`);
    };
    fitVisibleViewport();
    viewport?.addEventListener('resize', fitVisibleViewport);
    viewport?.addEventListener('scroll', fitVisibleViewport);
    globalThis.window?.addEventListener?.('resize', fitVisibleViewport);
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
      viewport?.removeEventListener('resize', fitVisibleViewport);
      viewport?.removeEventListener('scroll', fitVisibleViewport);
      globalThis.window?.removeEventListener?.('resize', fitVisibleViewport);
      previousFocus?.focus?.({ preventScroll: true });
    };
  }, [sheetOpen]);
  useEffect(() => {
    sheetRef.current?.querySelector('button')?.focus?.({ preventScroll: true });
  }, [phase]);
  useEffect(() => { if (busy) sheetRef.current?.focus?.({ preventScroll: true }); }, [busy]);
  const captured = blob => {
    if (!blob?.size) { setLocalError('사진을 저장하지 못했어요. 다시 찍어 주세요.'); return; }
    setImageUrl(URL.createObjectURL(blob)); setLocalError(''); setPhase('mask');
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
      setImageUrl(URL.createObjectURL(converted)); setPhase('fit');
    } catch (error) { if (mounted.current) setLocalError(error.message || '사진을 열지 못했어요. 다시 골라 주세요.'); }
    finally { inFlight.current = false; if (mounted.current) setBusy(false); }
  };
  const submitMasked = async (blob, maskRegion) => {
    if (phase !== 'mask' || !blob?.size || !maskRegion || !enrollmentId || inFlight.current) return;
    inFlight.current = true; setBusy(true); setLocalError('');
    try {
      const current = await uploadIdDocument(enrollmentId, { file: blob, documentType: ID_DOCUMENT_TYPES[0].value, maskedConfirmed: true, maskRegion });
      if (mounted.current) await onUploaded?.(current);
    } catch (error) {
      if (!mounted.current) return;
      if (error?.status === 409) {
        try { await onStale?.(error); return; }
        catch (refreshError) { error = refreshError; }
        if (!mounted.current) return;
      }
      throw error;
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
        <h2 id="id-camera-title">{phase === 'mask' ? '주민번호 가리기' : '주민등록증 촬영'}</h2><span />
      </div>
      {phase === 'camera' && <IdCameraCapture busy={busy} onCaptured={captured} onGallery={openGallery} onUnavailable={reason => { discard('choose'); setLocalError(CAMERA_UNAVAILABLE_MESSAGES[reason] || CAMERA_UNAVAILABLE_MESSAGES.unsupported); }} />}
      {phase === 'fit' && <IdGalleryFit key={imageUrl} imageUrl={imageUrl} onCaptured={captured} onGallery={openGallery} />}
      {phase === 'mask' && <IdMaskEditor key={imageUrl} imageUrl={imageUrl} onSubmit={submitMasked} onRetake={() => discard('camera')} />}
      {localError && <p className={s.idSheetError} role="alert">{localError}</p>}
    </div>}
  </>;
}
