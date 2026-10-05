import { Modal, Button } from '@/components/ui.jsx';

export function DraftEntryDialog({ dialog, actions, busy }) {
  if (!dialog) return null;
  return (
    <Modal onClose={busy ? undefined : actions.cancel}>
      <h3>{dialog.kind === 'pending' ? '사진을 저장하고 있어요'
        : dialog.partial ? `지금 있는 사진 ${dialog.meta.photoCount || 0}장으로 열기` : '계속할까요?'}</h3>
      <p>{dialog.message}</p>
      <div className="modal-actions">
        {dialog.kind === 'pending' ? (
          <>
            <Button disabled={busy} variant="ghost" onClick={actions.openPartial}>지금 있는 사진 {dialog.meta.photoCount || 0}장으로 열기</Button>
            <Button disabled={busy} variant="primary" onClick={actions.refreshRemote}>다시 확인</Button>
          </>
        ) : (
          <>
            <Button disabled={busy} variant="ghost" onClick={actions.cancel}>취소</Button>
            <Button disabled={busy} variant="primary" onClick={actions.confirm}>지우고 계속</Button>
          </>
        )}
      </div>
    </Modal>
  );
}
