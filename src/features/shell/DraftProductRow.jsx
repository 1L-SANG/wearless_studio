import { draftSavedTime } from '@/lib/draftEntry.js';

export function DraftProductRow({ meta }) {
  return <div className="handoff-product-row">
    {meta?.thumbnailUrl && <img src={meta.thumbnailUrl} width={56} height={56} alt="" />}
    <div>
      <strong>{meta?.productName || '이름 없는 상품'}</strong>
      <p>사진 {meta?.photoCount || 0}장 · {draftSavedTime(meta?.updatedAt)}</p>
    </div>
  </div>;
}
