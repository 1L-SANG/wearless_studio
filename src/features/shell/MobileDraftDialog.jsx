import { Modal, Button } from '@/components/ui.jsx';
import { draftSavedTime } from '@/lib/draftEntry.js';
import { isMobileDraftWriter } from '@/lib/draftDeviceNoun.js';

export function DraftProductRow({ meta }) {
  return <div className="handoff-product-row">
    {meta?.thumbnailUrl && <img src={meta.thumbnailUrl} width={56} height={56} alt="" />}
    <div>
      <strong>{meta?.productName || '이름 없는 상품'}</strong>
      <p>사진 {meta?.photoCount || 0}장 · {draftSavedTime(meta?.updatedAt)}</p>
    </div>
  </div>;
}

export function MobileDraftDialog({ dialog, actions, busy }) {
  return <Modal onClose={busy ? undefined : actions.keepMobile}>
    <h3 className="handoff-title">저장된 상품이 있어요</h3>
    <DraftProductRow meta={dialog.meta} />
    <p>{isMobileDraftWriter(dialog.meta) ? 'PC에서 아직 열지 않았어요.'
      : 'PC에서 만드는 중이에요. 버리면 PC 화면의 작업도 사라져요.'}</p>
    <p>한 번에 한 상품만 저장할 수 있어요.</p>
    <div className="modal-actions handoff-actions">
      <Button variant="primary" disabled={busy} onClick={actions.keepMobile}>그대로 두기</Button>
      <Button variant="danger" disabled={busy} onClick={actions.confirm}>버리고 새로 올리기</Button>
    </div>
  </Modal>;
}

export function MobileDraftStatus({ meta, gone = false, onNext, busy }) {
  return <div className="surface handoff-panel">
    {!gone && <DraftProductRow meta={meta} />}
    <h3 className="handoff-title">{gone ? '이 상품은 PC에서 마무리됐거나 정리됐어요' : 'PC에서 이 상품을 이어서 만들고 있어요'}</h3>
    <div className="handoff-actions">
      <Button variant="primary" disabled={busy} onClick={onNext}>다음 상품 올리기</Button>
    </div>
  </div>;
}
