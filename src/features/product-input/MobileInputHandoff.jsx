import { useEffect, useRef, useState } from 'react';
import { Button, Icon, Modal, UploadPendingTile } from '@/components/ui.jsx';
import { WizardCTA } from '@/features/shell/shell.jsx';
import { HangTag } from '@/features/shell/HangTag.jsx';
import { AddressCopyChip } from '@/features/shell/AddressCopyChip.jsx';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { draftSlot } from '@/lib/draftSlot.js';
import s from '@/components/NotFound.module.css';

export function useMobileInputSave(product, initialSavedMeta) {
  const [result, setResult] = useState(null);
  const [saved, setSaved] = useState(() => initialSavedMeta ? { photoCount: initialSavedMeta.photoCount } : null);
  const [readOnly, setReadOnly] = useState(Boolean(initialSavedMeta));
  const [gone, setGone] = useState(false);
  const [editing, setEditing] = useState(false);
  const active = useRef(null);
  const publish = (attempt, next) => {
    if (active.current !== attempt) return;
    attempt.status = next.status;
    setResult({ ...next, product: attempt.snapshot.product });
  };
  const sync = async (attempt) => {
    if (attempt.inFlight || active.current !== attempt) return;
    attempt.inFlight = true;
    try {
      publish(attempt, await draftSlot.saveAll(attempt.snapshot));
    } catch {
      publish(attempt, { status: 'failed' });
    } finally {
      attempt.inFlight = false;
      clearTimeout(attempt.timer);
    }
  };
  const save = (snapshot) => {
    if (active.current?.status === 'saving') return;
    clearTimeout(active.current?.timer);
    const attempt = { snapshot, status: 'saving', inFlight: false };
    active.current = attempt;
    setResult({ status: 'saving' });
    // 안내만 실패로 바꾼다. 사진 재시도와 늦은 최종 저장 응답은 계속 기다린다.
    attempt.timer = setTimeout(() => publish(attempt, { status: 'failed' }), 120000);
    return sync(attempt);
  };
  useEffect(() => {
    const unsubscribe = draftSlot.onPhotosPending((pending) => {
      const attempt = active.current;
      if (pending || attempt?.status !== 'failed' || attempt.inFlight) return;
      // 사진 완료 신호만으로 성공을 알리지 않고 최신 스냅샷의 PUT 성공을 확인한다.
      void sync(attempt);
    });
    const onVisible = () => {
      const attempt = active.current;
      if (document.visibilityState !== 'visible' || !['saving', 'failed'].includes(attempt?.status)) return;
      void draftSlot.checkOwnership().catch(() => {});
      draftSlot.retryFailedUploads();
      if (attempt.status === 'failed') void sync(attempt);
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      unsubscribe();
      document.removeEventListener('visibilitychange', onVisible);
      clearTimeout(active.current?.timer);
      active.current = null;
    };
  }, []);
  return {
    result, save, readOnly, gone, saving: result?.status === 'saving',
    edit: () => { setReadOnly(false); setEditing(true); },
    close: () => {
      if (active.current?.status === 'saving') return;
      clearTimeout(active.current?.timer);
      active.current = null;
      if (result?.status === 'saved') {
        setSaved({ product: result.product, photoCount: result.photoCount });
        setReadOnly(true);
        setEditing(false);
      }
      if (result?.status === 'gone') setGone(true);
      setResult(null);
    },
    banner: saved ? { changed: editing || (!readOnly && saved.product !== product), photoCount: saved.photoCount } : null,
  };
}

export function MobileSaveStatus({ changed, photoCount }) {
  return (
    <div className={`pi-save-status${changed ? ' pi-save-status-changed' : ''}`} role="status">
      <span className="pi-save-status-icon" aria-hidden="true"><Icon name={changed ? 'refresh' : 'check'} size={18} /></span>
      <div className="pi-save-status-copy">
        <p className="pi-save-status-title">{changed ? '사진이 바뀌었어요' : `사진 ${photoCount}장을 저장했어요`}</p>
        <p className="pi-save-status-sub">{changed ? '아래 버튼을 다시 눌러 저장해 주세요.'
          : 'PC에서 ai.wearless.kr로 이어서 만들어요.'}</p>
      </div>
    </div>
  );
}

export function InputMainAction({ mobile, canDone, pending, saving, disabledReason, onSave, onAnalyze, saved, onEdit, onNext, busy }) {
  if (mobile && saved) return <WizardCTA>
    <div className="handoff-actions">
      <Button variant="ghost" disabled={busy} onClick={onEdit}>사진 고치기</Button>
      <Button variant="primary" disabled={busy} onClick={onNext}>다음 상품 올리기</Button>
    </div>
  </WizardCTA>;
  const reason = mobile && pending ? '사진을 불러오는 중이에요' : disabledReason;
  return (
    <WizardCTA>
      {reason && <span className="wizard-cta-reason">{reason}</span>}
      <Button variant="primary" size="lg" icon="check"
        disabled={!canDone || (mobile && (pending || saving))} onClick={mobile ? onSave : onAnalyze}>
        {mobile ? '저장하고 PC에서 이어하기' : 'AI 분석하기'}
      </Button>
    </WizardCTA>
  );
}

export function MobileAnalyzedNotice({ onNew, busy }) {
  return (
    <div className="surface handoff-panel pi-handoff-notice">
      <p>이 상품은 PC에서 분석까지 마쳤어요. PC에서 이어서 진행해 주세요.</p>
      <div className="handoff-actions">
        <Button variant="ghost" disabled={busy} onClick={onNew}>지우고 새 상품 업로드</Button>
      </div>
    </div>
  );
}

export function MobileSaveModal({ result, onRetry, onClose }) {
  const { session } = useAuth();
  const user = session?.user;
  const provider = user?.app_metadata?.provider;
  const providerName = provider === 'kakao' ? '카카오' : provider === 'google' ? '구글' : provider === 'email' ? '이메일' : null;
  const metadata = user?.user_metadata;
  const identity = provider === 'kakao'
    ? metadata?.full_name || metadata?.name || metadata?.nickname || metadata?.preferred_username || user?.email
    : user?.email;
  const account = providerName && identity ? `${providerName} · ${identity}` : null;
  if (!result) return null;
  const { status, photoCount } = result;
  const copy = {
    saving: ['사진을 저장하고 있어요', '다 저장될 때까지 이 화면을 닫지 말아 주세요.'],
    saved: [`사진 ${photoCount}장을 저장했어요`, account ? 'PC에서도 이 계정으로 로그인해 주세요.' : 'PC에서도 같은 계정으로 로그인해 주세요.'],
    failed: ['사진을 다 저장하지 못했어요', '인터넷 연결을 확인하고 다시 시도해 주세요.'],
    gone: ['이 상품은 PC에서 마무리됐거나 정리됐어요', '지금 화면의 저장을 멈췄어요.'],
    locked: ['다른 곳에서 이 상품을 이어서 작업하고 있어요', '지금 화면의 저장을 멈췄어요.'],
  }[status];
  return (
    <Modal onClose={status === 'saving' ? undefined : onClose}>
      <div className={`handoff-panel pi-save-modal pi-save-${status}`} role="status" aria-live="polite">
        {status === 'saved' && <HangTag className="pi-save-tag" label={`사진 ${photoCount}장 저장됨이라고 적힌 옷 태그`}
          footer={<text className={`${s.mono} ${s.mutedFill}`} x="140" y="187" textAnchor="middle" fontSize="7.6" letterSpacing="1.6">KEEP 7 DAYS</text>}>
          <text className={`${s.display} ${s.inkFill}`} x="140" y="146" textAnchor="middle" fontSize="46">{photoCount}</text>
          <text className={`${s.mono} ${s.accentFill}`} x="140" y="164" textAnchor="middle" fontSize="7.6" letterSpacing="1.6">PHOTOS SAVED</text>
        </HangTag>}
        <h3 className="handoff-title">{copy[0]}</h3>
        {status === 'saved' && account && <span className="pill pill-soft handoff-account">{account}</span>}
        <p>{copy[1]}</p>
        {status === 'saved' && result.missingPhotoCount > 0 && <p>사진 {result.missingPhotoCount}장은 다시 골라 주세요.</p>}
        {status === 'saved' ? <>
          <AddressCopyChip />
          <Button variant="primary" block onClick={onClose}>확인</Button>
        </> : status === 'saving' ? <UploadPendingTile className="pi-save-progress" /> : (
          <div className="handoff-actions modal-actions">
            {status === 'failed' && <Button variant="primary" onClick={onRetry}>다시 시도</Button>}
            <Button variant="ghost" onClick={onClose}>{status === 'failed' ? '닫기' : '확인'}</Button>
          </div>
        )}
      </div>
    </Modal>
  );
}
