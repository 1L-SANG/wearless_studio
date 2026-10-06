import { Modal, Button } from '@/components/ui.jsx';
import { MobileDraftDialog } from './MobileDraftDialog.jsx';

export function DraftEntryDialog({ dialog, actions, busy }) {
  if (!dialog) return null;
  if (dialog.kind === 'mobile') return <MobileDraftDialog dialog={dialog} actions={actions} busy={busy} />;
  return (
    <Modal onClose={busy ? undefined : actions.cancel}>
      <h3 className="handoff-title">{dialog.kind === 'pending' ? '사진을 저장하고 있어요'
        : dialog.partial ? '이대로 열기' : '계속할까요?'}</h3>
      <p>{dialog.message}</p>
      {(dialog.kind === 'pending' || dialog.partial) && <p>사진 {dialog.meta.photoCount || 0}장</p>}
      {dialog.kind === 'pending' && <p>{dialog.savedAt}</p>}
      <div className="modal-actions handoff-dialog-actions">
        {dialog.kind === 'pending' ? (
          <>
            <Button disabled={busy} variant="primary" onClick={actions.refreshRemote}>다시 확인</Button>
            <Button disabled={busy} variant="ghost" onClick={actions.openPartial}>이대로 열기</Button>
          </>
        ) : (
          <>
            <Button disabled={busy} variant="primary" onClick={actions.confirm}>{dialog.partial ? '이대로 열기' : '지우고 계속'}</Button>
            <Button disabled={busy} variant="ghost" onClick={actions.cancel}>취소</Button>
          </>
        )}
      </div>
    </Modal>
  );
}
