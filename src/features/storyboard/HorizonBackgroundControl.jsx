import { useState } from 'react';
import { horizonBackgroundMode } from '../../lib/horizonBackground.js';
import { effectiveHorizonBackgroundMode } from '../../lib/horizonBackgroundAvailability.js';

export default function HorizonBackgroundControl({ block, disabled, onChange, memberCount,
  garmentToneAvailable = false, garmentToneUnavailableReason = '현재 상품의 색상 근거를 확인한 뒤 사용할 수 있어요.' }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  if (block?.cutType !== 'horizon' || !block.spaceGroupId) return null;
  const requestedMode = horizonBackgroundMode(block);
  const mode = effectiveHorizonBackgroundMode(block, { available: garmentToneAvailable });
  async function choose(value, input) {
    if (value === requestedMode || saving || disabled) return;
    setSaving(true);
    setError('');
    try { await onChange(value); }
    catch { setError('배경 설정을 저장하지 못했어요. 다시 선택해 주세요.'); }
    finally {
      setSaving(false);
      requestAnimationFrame(() => { if (input?.isConnected) input.focus({ preventScroll: true }); });
    }
  }
  return <fieldset className="sb-horizon-background" disabled={disabled || saving}>
    <legend>이 세트의 배경</legend>
    <div className="sb-horizon-background-options">
      {[
        ['reference', '기존 배경'],
        ['garment-tone', '옷 색에 맞춤'],
      ].map(([value, label]) => <label key={value} data-selected={mode === value}>
        <input type="radio" name={`horizon-background-${block.spaceGroupId}`} value={value}
          checked={mode === value} disabled={value === 'garment-tone' && !garmentToneAvailable}
          onClick={(event) => {
            if (value === 'reference' && mode === 'reference' && requestedMode === 'garment-tone') choose(value, event.currentTarget);
          }}
          onChange={(event) => choose(value, event.currentTarget)} />
        <span>{label}</span>
      </label>)}
    </div>
    {!garmentToneAvailable && <p>{garmentToneUnavailableReason}</p>}
    {mode === 'garment-tone' && garmentToneAvailable && <p>상품 사진에서 확인한 의류색으로 벽 색을 조정해요.</p>}
    <p>이 세트 {memberCount ? `${memberCount}컷` : '전체'}에 함께 적용해요.</p>
    {error && <p role="alert">{error}</p>}
  </fieldset>;
}
