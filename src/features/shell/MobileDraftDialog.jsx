import { Modal, Button } from '@/components/ui.jsx';
import { DraftProductRow } from './DraftProductRow.jsx';
import { HandoffStatusPage } from './HandoffStatusPage.jsx';
import { isMobileDraftWriter } from '@/lib/draftDeviceNoun.js';

export function MobileDraftDialog({ dialog, actions, busy }) {
  return <Modal onClose={busy ? undefined : actions.keepMobile}>
    <h3 className="handoff-title">저장된 상품이 있어요</h3>
    <DraftProductRow meta={dialog.meta} />
    <p>{isMobileDraftWriter(dialog.meta) ? 'PC에서 아직 열지 않았어요.'
      : 'PC에서 만드는 중이에요. 버리면 PC 화면의 작업도 사라져요.'}</p>
    <p>한 번에 한 상품만 저장할 수 있어요.</p>
    <div className="modal-actions handoff-actions handoff-dialog-actions">
      <Button variant="primary" disabled={busy} onClick={actions.keepMobile}>그대로 두기</Button>
      <Button variant="danger" disabled={busy} onClick={actions.confirm}>버리고 새로 올리기</Button>
    </div>
  </Modal>;
}

export function MobileDraftStatus({ meta, gone = false, onNext, busy, onSlotChange }) {
  return <HandoffStatusPage variant={gone ? 'gone' : 'pc'} meta={meta} busy={busy} onNext={onNext} onSlotChange={onSlotChange} />;
}
