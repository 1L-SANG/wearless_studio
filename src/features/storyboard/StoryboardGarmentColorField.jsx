import React, { useId } from 'react';
import { garmentColorOptions } from '../../lib/garmentColors.js';
import styles from './StoryboardPanels.module.css';

export function StoryboardGarmentColorField({ product, colorOpts = [], block = {}, value, onChange, disabled = false, disabledReason = '' }) {
  const id = useId();
  const options = garmentColorOptions(product, colorOpts, block);
  const selected = options.find(option => option.id === value);
  if ((product?.colors?.length || 0) <= 1) return null;
  return <div className={`insp-sec ${styles.garmentColorField}`}>
    <span className="lbl">의류 색상 선택</span>
    <div className={styles.garmentColors} role="group" aria-label="의류 색상 선택"
      aria-describedby={disabledReason ? `${id}-disabled` : undefined}>
      {options.map((color, index) => <div className={styles.garmentColorOption} key={color.id}>
        <button type="button" className={styles.garmentColorChoice} disabled={disabled || color.disabled}
          aria-label={color.label} aria-pressed={value === color.id}
          aria-describedby={color.reason ? `${id}-${index}` : undefined}
          onClick={() => { if (!disabled && !color.disabled) onChange(color.id); }}>
          <svg className={styles.garmentSwatch} viewBox="0 0 28 28" aria-hidden="true"><circle cx="14" cy="14" r="13" fill={color.hex || 'var(--fg-3)'} /></svg>
          <span>{color.label}</span>
        </button>
        {color.reason && <span className={styles.garmentColorNote} id={`${id}-${index}`}>{color.reason}</span>}
      </div>)}
    </div>
    {disabledReason && <p className={styles.garmentColorNote} id={`${id}-disabled`}>{disabledReason}</p>}
    {selected?.source && <div className={styles.garmentSource}>
      <img src={selected.source.src} alt={`${selected.label} ${selected.sourceLabel}`} />
      <div><span>{selected.sourceLabel}</span>{selected.note && <p>{selected.note}</p>}</div>
    </div>}
    {(!selected || selected.disabled) && <p className={styles.garmentColorNote} role="status">
      {selected ? '이 색상의 원본 사진을 올려 주세요.' : '색상을 다시 골라 주세요.'}
    </p>}
    {block.detailTargetId && <p className={styles.garmentColorNote}>촬영 대상은 대표 색상 사진을 사용해요.</p>}
  </div>;
}
