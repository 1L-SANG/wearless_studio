import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui.jsx';
import { getAiDraftSectionNames, formatAiDraftSectionNames } from './editorAiDrafts.js';
import styles from './EditorDownloadModal.module.css';

// The modal's mount owns consent, so retrying keeps it and reopening clears it.
export function useAiDraftConfirmation(blocks, actions) {
  const accepted = useRef(false);
  const [pendingAction, setPendingAction] = useState(null);
  const sectionNames = getAiDraftSectionNames(blocks);
  const request = (action) => {
    if (!accepted.current && sectionNames.length) setPendingAction(action);
    else actions[action]();
  };
  const confirm = () => {
    accepted.current = true;
    setPendingAction(null);
    actions[pendingAction]();
  };
  return { sectionNames, confirming: pendingAction !== null, pendingAction, request, confirm };
}

export function EditorAiDraftConfirmation({ sectionNames, onCancel, onConfirm, busy, confirmLabel = '그대로 다운로드' }) {
  const root = useRef(null);
  useEffect(() => { root.current?.querySelector('button')?.focus(); }, []);
  return (
    <div className={styles.draftConfirmation} ref={root}>
      <p className={styles.draftMessage} role="status">{formatAiDraftSectionNames(sectionNames)}에 AI 초안이 그대로 있어요</p>
      <div className={`modal-actions ${styles.actions}`}>
        <Button variant="ghost" className={styles.cancel} disabled={busy} onClick={onCancel}>돌아가서 고치기</Button>
        <Button variant="primary" disabled={busy} onClick={onConfirm}>{confirmLabel}</Button>
      </div>
    </div>
  );
}
