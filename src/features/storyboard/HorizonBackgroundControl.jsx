import { useState } from 'react';
import { horizonBackgroundMode } from '../../lib/horizonBackground.js';
import { effectiveHorizonBackgroundMode } from '../../lib/horizonBackgroundAvailability.js';

// availability: horizonBackgroundAvailability 결과. pending(측정 전)과 ready 는 고를 수 있고,
// unavailable(잰 결과 맞추기 어려움)은 '옷 색에 맞춤'을 끄고 이유를 보여준다.
export default function HorizonBackgroundControl({ block, disabled, onChange, memberCount, availability }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  if (block?.cutType !== 'horizon' || !block.spaceGroupId) return null;
  const requestedMode = horizonBackgroundMode(block);
  const mode = effectiveHorizonBackgroundMode(block, availability);
  const state = availability?.state;
  const garmentToneSelectable = state === 'pending' || state === 'ready';
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
          checked={mode === value} disabled={value === 'garment-tone' && !garmentToneSelectable}
          onClick={(event) => {
            if (value === 'reference' && mode === 'reference' && requestedMode === 'garment-tone') choose(value, event.currentTarget);
          }}
          onChange={(event) => choose(value, event.currentTarget)} />
        <span>{label}</span>
      </label>)}
    </div>
    {!garmentToneSelectable && availability?.reason && <p>{availability.reason}</p>}
    {mode === 'garment-tone' && state === 'ready' && <p>상품 사진에서 확인한 의류색으로 벽 색을 조정해요.</p>}
    {mode === 'garment-tone' && state === 'pending' && <p>다음 단계로 넘어갈 때 옷 색을 확인해 벽 색을 맞춰요. 맞추기 어려운 색이면 기존 배경으로 만들어요.</p>}
    <p>이 세트 {memberCount ? `${memberCount}컷` : '전체'}에 함께 적용해요.</p>
    {error && <p role="alert">{error}</p>}
  </fieldset>;
}
