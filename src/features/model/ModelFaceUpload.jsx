/* 얼굴 3장 업로드 + 동기 품질검사. 기본 개인화 API와 생체 enrollment adapter를
   같은 슬롯 UI로 처리하며 순서는 front → angle45 → side로 고정한다. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, ErrorState, Icon, useToast } from '@/components/ui.jsx';
import {
  deleteFacePhoto, fetchFacePhotoUrl, getStatus, listFacePhotos, uploadFacePhoto,
} from '@/lib/api/personalization.js';
import { toUploadableImage } from '@/lib/imageTranscode.js';
import { ENROLLMENT_ANGLES } from './biometricEnrollment.js';
import s from './ModelPersonalization.module.css';

const personalizationPhotoApi = {
  async load() {
    const [status, result] = await Promise.all([getStatus(), listFacePhotos()]);
    return {
      photos: result.photos || [],
      blocked: (status.blockers || []).some((blocker) => blocker.code === 'consent_missing')
        ? '업로드 전에 필수 동의를 먼저 완료해주세요.'
        : null,
    };
  },
  upload: uploadFacePhoto,
  remove: deleteFacePhoto,
  fetchUrl: fetchFacePhotoUrl,
};

// api-spec §3.2 qc_reason 카피(ux-flow §3.2 와 단일 소스) — 서버가 보낸 reasons 배열을 매핑한다.
const QC_COPY = {
  occlusion: '얼굴이 가려져 있어요. 얼굴 전체가 보이게 다시 찍어주세요.',
  low_resolution: '사진이 흐리거나 작아요. 더 선명한 사진으로 올려주세요.',
  multiple_faces: '사진에 여러 명이 있어요. 본인만 나온 사진으로 올려주세요.',
  angle_mismatch: '선택한 각도와 달라요. 안내에 맞춰 정면/측면/45도로 찍어주세요.',
};

function SlotCard({ index, angle, label, guide, exampleImage, slot, onPicked, onDelete, checking, queued, locked, fetchUrl, localUrl }) {
  const fileRef = useRef(null);
  const [url, setUrl] = useState(null);
  const passed = slot?.qcStatus === 'passed';

  useEffect(() => {
    let alive = true;
    let u;
    if (passed && slot?.imageUri && fetchUrl) {
      fetchUrl(slot.imageUri)
        .then((v) => { if (!alive) { URL.revokeObjectURL(v); return; } u = v; setUrl(v); })
        .catch(() => { /* 표시 실패 — 플레이스홀더 유지 */ });
    } else {
      setUrl(null);
    }
    return () => { alive = false; if (u) URL.revokeObjectURL(u); };
  }, [fetchUrl, passed, slot?.imageUri]);

  const disabled = checking || queued || locked;
  const stateLabel = queued ? '대기 중' : checking ? '검사 중' : passed ? '확인 완료' : '사진 필요';
  // 서버 프리뷰가 있으면 그것(재방문에도 남는 정본), 없으면 방금 올린 파일의 로컬 프리뷰.
  // 생체등록 경로는 격리 사진을 내주는 라우트가 없어 로컬 프리뷰가 유일한 확인 수단이다.
  const shownUrl = url || localUrl || null;

  return (
    <div className={`${s.slotCard}${passed ? ` ${s.slotCardDone}` : ''}`}>
      <div className={s.slotHeader}>
        <div className={s.slotTitleGroup}>
          <span className={s.slotIndex}>{String(index + 1).padStart(2, '0')}</span>
          <span className={s.slotLabel}>{label}</span>
        </div>
        <span className={`${s.slotState}${passed ? ` ${s.slotStateDone}` : ''}`} aria-live="polite">
          {stateLabel}
        </span>
      </div>

      <div className={s.slotMedia}>
        <button type="button" className={`${s.slotUpload}${shownUrl ? ' ' + s.slotHas : ''}`}
          onClick={() => !disabled && fileRef.current?.click()} disabled={disabled}
          aria-label={shownUrl ? `${label} 사진 바꾸기` : `${label} 사진 올리기`}>
          {shownUrl ? <img src={shownUrl} alt={`${label} 얼굴`} /> : passed ? (
            <div className={s.slotEmpty}>
              <span className={s.slotUploadIcon}><Icon name="check" size={19} /></span>
              <span className={s.slotUploadTitle}>{label} 업로드 완료</span>
              <span className={s.slotUploadHint}>클릭해서 바꾸기</span>
            </div>
          ) : (
            <div className={s.slotEmpty}>
              <span className={s.slotUploadIcon}><Icon name="upload" size={19} /></span>
              <span className={s.slotUploadTitle}>{label} 사진 선택</span>
              <span className={s.slotUploadHint}>클릭해서 업로드</span>
            </div>
          )}
          {(checking || queued) && <div className={s.slotBusy}>{checking ? '품질 확인 중…' : '대기 중…'}</div>}
        </button>
        {passed && (
          <button type="button" className={s.slotDel} onClick={() => onDelete(angle)}
            title={`${label} 사진 삭제`} aria-label={`${label} 사진 삭제`} disabled={disabled}>
            <Icon name="x" size={14} />
          </button>
        )}
      </div>
      <input ref={fileRef} type="file" accept="image/*,.heic,.heif,.hif" hidden
        onChange={(e) => { const f = e.target.files?.[0]; if (f) onPicked(angle, f); e.target.value = ''; }} />
      {exampleImage && <img className={s.slotExample} src={exampleImage} alt="" aria-hidden="true" />}
      <p className={s.slotGuide}>{guide}</p>
      {slot?.lastFail && (
        <div className={s.slotFail}>
          <div>{slot.lastFail.message}</div>
          {slot.lastFail.reasons?.length > 0 && (
            <ul>{slot.lastFail.reasons.map((r) => <li key={r}>{QC_COPY[r] || r}</li>)}</ul>
          )}
        </div>
      )}
    </div>
  );
}

export function ModelFaceUpload({
  embedded = false,
  onDone,
  photoApi = personalizationPhotoApi,
  angles = ENROLLMENT_ANGLES,
  nextLabel = '다음 · 신체 정보',
}) {
  const navigate = useNavigate();
  const { push } = useToast();
  const [phase, setPhase] = useState('loading'); // loading|ready|error
  const [slots, setSlots] = useState({});          // angle -> {qcStatus, qcReasons, imageUri, uploadedAt, lastFail}
  // angle -> objectURL. 생체등록 경로는 서버가 격리 사진을 안 내주므로(EnrollmentPhotoView 에
  // 이미지 참조 없음) 방금 올린 파일로 프리뷰를 만들어 "내가 뭘 올렸는지"를 보여준다.
  // 이 세션 동안만 유효 — 새로고침하면 사라지고 '업로드 완료' 표시로 돌아간다.
  const [previews, setPreviews] = useState({});
  const previewsRef = useRef({});                  // 회수(revoke)용 최신 맵 — 언마운트 정리에 쓴다
  const [slotBusy, setSlotBusy] = useState({});    // angle -> 'queued' | 'checking' (슬롯별 진행 — 다른 슬롯을 잠그지 않는다)
  const uploadQueueRef = useRef(Promise.resolve()); // 업로드 직렬화(백엔드 photo-fence 는 enrollment 스코프라 동시요청 금지)
  const [blocked, setBlocked] = useState(null);    // 동의 미완료·미성년 등 전제조건 미충족 안내

  // 프리뷰 교체/해제. revoke 는 setState 업데이터 밖에서 한다 — 업데이터는 순수해야 하고
  // (StrictMode 는 이중 호출한다) 그 안에서 revoke 하면 화면에 떠 있는 URL 을 죽일 수 있다.
  const putPreview = useCallback((angle, url) => {
    const previous = previewsRef.current[angle];
    const next = { ...previewsRef.current };
    if (url) next[angle] = url; else delete next[angle];
    previewsRef.current = next;
    setPreviews(next);
    if (previous && previous !== url) URL.revokeObjectURL(previous);
  }, []);

  // 언마운트 — 남은 objectURL 회수(누수 금지).
  useEffect(() => () => {
    Object.values(previewsRef.current).forEach((u) => URL.revokeObjectURL(u));
    previewsRef.current = {};
  }, []);

  const load = useCallback(async () => {
    setPhase('loading');
    try {
      const r = await photoApi.load();
      setBlocked(r.blocked || null);
      const map = {};
      (r.photos || []).forEach((p) => { map[p.angle] = p; });
      setSlots(map);
      setPhase('ready');
    } catch (e) {
      push?.(e.message, { icon: 'alertCircle' });
      setPhase('error');
    }
  }, [photoApi, push]);

  useEffect(() => { load(); }, [load]);

  const onPicked = (angle, picked) => {
    // 아이폰 HEIC 는 File.type 이 비어 오기도 한다 — type 만 보고 막으면 아이폰 사진이 전부 거절된다.
    const looksImage = picked.type ? picked.type.startsWith('image/') : /\.(hei[cf]|hif|jpe?g|png|webp)$/i.test(picked.name || '');
    if (!looksImage) { push?.('이미지 파일만 올릴 수 있어요.', { icon: 'alertCircle' }); return; }
    // 고른 즉시 이 슬롯을 '대기 중'으로 표시(다른 슬롯은 안 잠금 — 탭이 무시되지 않게).
    setSlotBusy((m) => ({ ...m, [angle]: 'queued' }));
    setSlots((m) => ({ ...m, [angle]: { ...(m[angle] || {}), lastFail: null } }));
    // 실제 업로드는 큐에 직렬로(백엔드 fence 가 enrollment 스코프라 동시요청 금지). 순번이 되면 '검사 중'.
    uploadQueueRef.current = uploadQueueRef.current.then(async () => {
      setSlotBusy((m) => ({ ...m, [angle]: 'checking' }));
      try {
        // HEIC → JPEG(+긴 변 축소). 서버·QC(SFace)가 HEIC 를 못 읽으므로 업로드 전에 바꾼다.
        let file;
        try {
          file = await toUploadableImage(picked);
        } catch {
          push?.('이 사진은 불러오지 못했어요. JPG·PNG 로 저장해 올려주세요.', { icon: 'alertCircle' });
          return;
        }
        // 변환본으로 프리뷰를 만든다 — 아이폰 HEIC 원본은 브라우저가 못 그린다.
        // QC 결과와 무관하게 먼저 건다: 떨어져도 "내가 뭘 올렸는지" 보고 다시 찍을 수 있어야 한다.
        putPreview(angle, URL.createObjectURL(file));
        const res = await photoApi.upload({ angle, fileBlob: file, filename: file.name });
        setSlots((m) => ({ ...m, [angle]: res }));
        push?.('사진이 등록됐어요.', { icon: 'check' });
      } catch (e) {
        if (e.code === 'consent_required' || e.code === 'minor_blocked') {
          setBlocked(e.message);
        } else {
          setSlots((m) => ({
            ...m,
            [angle]: { ...(m[angle] || { angle, qcStatus: 'none', qcReasons: [], imageUri: null }), lastFail: { message: e.message, reasons: e.reasons || [] } },
          }));
          push?.(e.message || '업로드에 실패했어요.', { icon: 'alertCircle' });
        }
      } finally {
        setSlotBusy((m) => { const next = { ...m }; delete next[angle]; return next; });
      }
    });
  };

  const onDelete = async (angle) => {
    if (!window.confirm('이 사진을 삭제할까요?')) return;
    try {
      await photoApi.remove(angle);
      putPreview(angle, null);
      setSlots((m) => ({ ...m, [angle]: { angle, qcStatus: 'none', qcReasons: [], imageUri: null, uploadedAt: null } }));
      push?.('삭제했어요.', { icon: 'check' });
    } catch (e) {
      push?.(e.message || '삭제에 실패했어요.', { icon: 'alertCircle' });
    }
  };

  const Wrap = ({ children }) => (embedded ? <>{children}</> : <div className="wizard">{children}</div>);

  if (phase === 'loading') return <Wrap><div className="surface">불러오는 중…</div></Wrap>;
  if (phase === 'error') return <Wrap><div className="surface"><ErrorState desc="얼굴 사진 정보를 불러오지 못했어요." onRetry={load} /></div></Wrap>;

  const completeCount = angles.filter((a) => slots[a.value]?.qcStatus === 'passed').length;
  const canContinue = completeCount === angles.length && Object.keys(slotBusy).length === 0 && !blocked;

  return (
    <Wrap>
      {!embedded && (
        <div className="page-head">
          <h1>얼굴 3장을 올려주세요</h1>
          <p>조명이 밝고 배경이 단순한 곳에서, 가리는 것 없이 본인 1인만 나오게 찍어주세요.</p>
        </div>
      )}

      {blocked && (
        <div className={`${s.banner} ${s.bannerWarn}`}>
          <Icon name="alertTri" size={16} /><span>{blocked}</span>
        </div>
      )}

      <div className="surface">
        <div className={s.slotGrid}>
          {angles.map((a, index) => (
            <SlotCard key={a.value} index={index} angle={a.value} label={a.label} guide={a.guide}
              exampleImage={a.exampleImage}
              slot={slots[a.value]} onPicked={onPicked} onDelete={onDelete}
              checking={slotBusy[a.value] === 'checking'} queued={slotBusy[a.value] === 'queued'}
              locked={!!blocked}
              fetchUrl={photoApi.fetchUrl} localUrl={previews[a.value]} />
          ))}
        </div>
        <p className="hint" style={{ marginTop: 16 }}>{completeCount}/3장 품질 확인 완료</p>
        <div className={s.banner} style={{ marginTop: 14 }}>
          <Icon name="lock" size={15} />
          <span>얼굴 사진은 비공개로 저장되고, 본인 확인 후 내 모델 생성에만 사용돼요.</span>
        </div>
        {/* 단독 화면과 라이선스 여정 모두 세 슬롯의 동기 QC 통과가 전제다.
            버튼은 항상 보여주되 검사 완료 전에는 비활성화해 다음 조건을 분명히 알린다. */}
        <Button variant="primary" block iconRight="arrowRight" style={{ marginTop: 18 }}
          disabled={!canContinue}
          onClick={() => { if (onDone) onDone(); else navigate('/model/body'); }}>
          {embedded ? nextLabel : '다음 · 신체 정보 입력'}
        </Button>
      </div>
    </Wrap>
  );
}

export default ModelFaceUpload;
