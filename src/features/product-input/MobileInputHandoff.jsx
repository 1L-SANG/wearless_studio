import { useRef, useState } from 'react';
import { Button, Icon, Modal, UploadPendingTile } from '@/components/ui.jsx';
import { WizardCTA } from '@/features/shell/shell.jsx';
import { HangTag } from '@/features/shell/HangTag.jsx';
import { AddressCopyChip } from '@/features/shell/AddressCopyChip.jsx';
import { draftSlot } from '@/lib/draftSlot.js';
import s from '@/components/NotFound.module.css';

export function useMobileInputSave(product) {
  const [result, setResult] = useState(null);
  const [saved, setSaved] = useState(null);
  const saving = useRef(false);
  const save = async (snapshot) => {
    if (saving.current) return;
    saving.current = true;
    setResult({ status: 'saving' });
    try {
      const next = await draftSlot.saveAll(snapshot);
      setResult({ ...next, product: snapshot.product });
    } catch {
      setResult({ status: 'failed' });
    } finally {
      saving.current = false;
    }
  };
  return {
    result, save, saving: result?.status === 'saving',
    close: () => {
      if (saving.current) return;
      if (result?.status === 'saved') setSaved({ product: result.product, photoCount: result.photoCount });
      setResult(null);
    },
    banner: saved ? { changed: saved.product !== product, photoCount: saved.photoCount } : null,
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

export function InputMainAction({ mobile, canDone, pending, saving, disabledReason, onSave, onAnalyze }) {
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
  if (!result) return null;
  const { status, photoCount } = result;
  const copy = {
    saving: ['사진을 저장하고 있어요', '다 저장될 때까지 이 화면을 닫지 말아 주세요.'],
    saved: [`사진 ${photoCount}장을 저장했어요`, 'PC에서 같은 계정으로 로그인하면 바로 이어져요.'],
    failed: ['사진을 다 저장하지 못했어요', '인터넷 연결을 확인하고 다시 시도해 주세요.'],
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
        <h3>{copy[0]}</h3>
        <p>{copy[1]}</p>
        {status === 'saved' ? <>
          <AddressCopyChip />
          <Button variant="primary" block onClick={onClose}>확인</Button>
        </> : status === 'saving' ? <UploadPendingTile className="pi-save-progress" /> : (
          <div className="handoff-actions">
            {status === 'failed' && <Button variant="primary" onClick={onRetry}>다시 시도</Button>}
            <Button variant="ghost" onClick={onClose}>{status === 'failed' ? '닫기' : '확인'}</Button>
          </div>
        )}
      </div>
    </Modal>
  );
}
