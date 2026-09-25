import { detailRecommendationMessage, detailTargetPatch, detailTargetPresentation, selectableDetailTargets } from '../../lib/detailRecommendations.js';

export function DetailTargetPicker({ block, product, recommendations, onChange }) {
  const targets = selectableDetailTargets(recommendations);
  const presentation = detailTargetPresentation(block, product, recommendations);
  return (
    <div className="insp-sec detail-target-picker">
      <label className="lbl" htmlFor={`detail-target-${block.id || 'new'}`}>촬영 대상</label>
      {presentation?.src && <img className="detail-target-image" src={presentation.src} alt={`내 상품 원본 · ${presentation.label}`} />}
      <p className="hint">내 상품 원본 · 촬영 대상: {presentation?.label}</p>
      <select id={`detail-target-${block.id || 'new'}`} value={block.detailTargetId || ''}
        onChange={(event) => onChange({ ...detailTargetPatch(targets.find((candidate) => candidate.id === event.target.value)),
          ...(event.target.value ? { colorId: (product?.colors?.find((color) => color.isBase) || product?.colors?.[0])?.id || block.colorId } : {}),
        })}>
        <option value="">원본에서 디테일 촬영</option>
        {block.detailTargetId && !targets.some((target) => target.id === block.detailTargetId)
          && <option value={block.detailTargetId} disabled>선택한 대상을 확인할 수 없어요</option>}
        {targets.map((target) => <option key={target.id} value={target.id}>{target.label} · {target.direction === 'back' ? '뒷면' : '앞면'}</option>)}
      </select>
      <p className="hint">{presentation?.target?.reason || (targets.length ? '상품에서 보여줄 부위를 고르세요. 아래 예시는 촬영 연출만 참고해요.' : detailRecommendationMessage(recommendations))}</p>
    </div>
  );
}
