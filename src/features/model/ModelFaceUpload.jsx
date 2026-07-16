/* =============================================================
   features/model — ②③ 얼굴 3장 업로드 + 동기 품질검사
   (/model/face)
   슬롯(front/side/angle45)당 1장, 업로드는 동기 QC 응답을 그대로 반영한다
   (api-spec §3.2). 통과 슬롯은 게이트(fetchFacePhotoUrl)로만 표시 — 공개
   URL 금지(§1.4). 불합격은 사유코드별 재업로드 안내를 보여준다.

   embedded 모드 — step02 라이선스 여정(/model/license)의 2단계로 재사용
   (ModelConsent 와 동일 패턴). 라이선스 얼굴은 여기 통과한 **front 슬롯**을
   그대로 참조하므로(서버 _resolve_profile_face), 이 화면이 곧 라이선스 얼굴
   등록이다 — 3장이 다 통과해야(프로필 ready) 발급이 열린다.
   ============================================================= */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, ErrorState, Icon, useToast } from '@/components/ui.jsx';
import {
  deleteFacePhoto, fetchFacePhotoUrl, getStatus, listFacePhotos, uploadFacePhoto,
} from '@/lib/api/personalization.js';
import s from './ModelPersonalization.module.css';

const ANGLES = [
  { value: 'front', label: '정면', guide: '정면을 바라보고 얼굴 전체가 나오게 찍어주세요.' },
  { value: 'side', label: '측면', guide: '고개를 완전히 옆으로 돌려 옆모습 윤곽이 보이게 찍어주세요. 왼쪽·오른쪽 편한 방향으로요.' },
  { value: 'angle45', label: '45도', guide: '정면에서 살짝만 돌려, 정면과 옆모습 사이 각도로 찍어주세요.' },
];

// api-spec §3.2 qc_reason 카피(ux-flow §3.2 와 단일 소스) — 서버가 보낸 reasons 배열을 매핑한다.
const QC_COPY = {
  occlusion: '얼굴이 가려져 있어요. 얼굴 전체가 보이게 다시 찍어주세요.',
  low_resolution: '사진이 흐리거나 작아요. 더 선명한 사진으로 올려주세요.',
  multiple_faces: '사진에 여러 명이 있어요. 본인만 나온 사진으로 올려주세요.',
  angle_mismatch: '선택한 각도와 달라요. 안내에 맞춰 정면/측면/45도로 찍어주세요.',
};

function SlotCard({ angle, label, guide, slot, onPicked, onDelete, checking, locked }) {
  const fileRef = useRef(null);
  const [url, setUrl] = useState(null);
  const passed = slot?.qcStatus === 'passed';

  useEffect(() => {
    let alive = true;
    let u;
    if (passed && slot?.imageUri) {
      fetchFacePhotoUrl(slot.imageUri)
        .then((v) => { if (!alive) { URL.revokeObjectURL(v); return; } u = v; setUrl(v); })
        .catch(() => { /* 표시 실패 — 플레이스홀더 유지 */ });
    } else {
      setUrl(null);
    }
    return () => { alive = false; if (u) URL.revokeObjectURL(u); };
  }, [passed, slot?.imageUri]);

  const disabled = checking || locked;

  return (
    <div className={s.slotCard}>
      <button type="button" className={`${s.slotUpload}${passed ? ' ' + s.slotHas : ''}`}
        onClick={() => !disabled && fileRef.current?.click()} disabled={disabled}>
        {passed && url ? (
          <>
            <img src={url} alt={`${label} 얼굴`} />
            <span className={s.slotBadge}>등록됨</span>
            <button type="button" className={s.slotDel} onClick={(e) => { e.stopPropagation(); onDelete(angle); }} title="삭제">
              <Icon name="x" size={13} />
            </button>
          </>
        ) : (
          <div className={s.slotEmpty}>
            <Icon name="upload" size={20} />
            <span>{label} 사진 올리기</span>
          </div>
        )}
        {checking && <div className={s.slotBusy}>검사 중…</div>}
      </button>
      <input ref={fileRef} type="file" accept="image/*" hidden
        onChange={(e) => { const f = e.target.files?.[0]; if (f) onPicked(angle, f); e.target.value = ''; }} />
      <div className={s.slotLabel}>{label}</div>
      <div className={s.slotGuide}>{guide}</div>
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

export function ModelFaceUpload({ embedded = false, onDone }) {
  const navigate = useNavigate();
  const { push } = useToast();
  const [phase, setPhase] = useState('loading'); // loading|ready|error
  const [slots, setSlots] = useState({});          // angle -> {qcStatus, qcReasons, imageUri, uploadedAt, lastFail}
  const [busyAngle, setBusyAngle] = useState(null);
  const [blocked, setBlocked] = useState(null);    // 동의 미완료·미성년 등 전제조건 미충족 안내

  const load = useCallback(async () => {
    setPhase('loading');
    try {
      const status = await getStatus();
      setBlocked((status.blockers || []).some((b) => b.code === 'consent_missing')
        ? '업로드 전에 필수 동의를 먼저 완료해주세요.' : null);
      const r = await listFacePhotos();
      const map = {};
      (r.photos || []).forEach((p) => { map[p.angle] = p; });
      setSlots(map);
      setPhase('ready');
    } catch (e) {
      push?.(e.message, { icon: 'alertCircle' });
      setPhase('error');
    }
  }, [push]);

  useEffect(() => { load(); }, [load]);

  const onPicked = async (angle, file) => {
    if (!file.type.startsWith('image/')) { push?.('이미지 파일만 올릴 수 있어요.', { icon: 'alertCircle' }); return; }
    setBusyAngle(angle);
    setSlots((m) => ({ ...m, [angle]: { ...(m[angle] || {}), lastFail: null } }));
    try {
      const res = await uploadFacePhoto({ angle, fileBlob: file, filename: file.name });
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
      setBusyAngle(null);
    }
  };

  const onDelete = async (angle) => {
    if (!window.confirm('이 사진을 삭제할까요?')) return;
    try {
      await deleteFacePhoto(angle);
      setSlots((m) => ({ ...m, [angle]: { angle, qcStatus: 'none', qcReasons: [], imageUri: null, uploadedAt: null } }));
      push?.('삭제했어요.', { icon: 'check' });
    } catch (e) {
      push?.(e.message || '삭제에 실패했어요.', { icon: 'alertCircle' });
    }
  };

  const Wrap = ({ children }) => (embedded ? <>{children}</> : <div className="wizard">{children}</div>);

  if (phase === 'loading') return <Wrap><div className="surface">불러오는 중…</div></Wrap>;
  if (phase === 'error') return <Wrap><div className="surface"><ErrorState desc="얼굴 사진 정보를 불러오지 못했어요." onRetry={load} /></div></Wrap>;

  const completeCount = ANGLES.filter((a) => slots[a.value]?.qcStatus === 'passed').length;

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
          {ANGLES.map((a) => (
            <SlotCard key={a.value} angle={a.value} label={a.label} guide={a.guide}
              slot={slots[a.value]} onPicked={onPicked} onDelete={onDelete}
              checking={busyAngle === a.value} locked={!!blocked || (busyAngle && busyAngle !== a.value)} />
          ))}
        </div>
        <p className="hint" style={{ marginTop: 16 }}>{completeCount}/3장 완료</p>
        <div className={s.banner} style={{ marginTop: 14 }}>
          <Icon name="lock" size={15} />
          <span>얼굴 사진은 비공개로 저장되고, 본인 확인 후 내 모델 생성에만 사용돼요.</span>
        </div>
        {/* 라이선스 여정에선 3장 전부 QC 통과해야 프로필이 ready 가 되고 발급이 열린다 —
            덜 채운 채 다음으로 보내면 마지막에 400 으로 되돌아오므로 여기서 막는다.
            단독 라우트는 종전대로 언제든 다음 단계로 진행 가능(회귀 0). */}
        <Button variant="primary" block iconRight="arrowRight" style={{ marginTop: 18 }}
          disabled={embedded && completeCount < 3}
          onClick={() => { if (onDone) onDone(); else navigate('/model/body'); }}>
          {embedded
            ? (completeCount < 3 ? `${3 - completeCount}장 더 올려주세요` : '다음 · 신체 정보')
            : '다음 · 신체 정보 입력'}
        </Button>
      </div>
    </Wrap>
  );
}

export default ModelFaceUpload;
