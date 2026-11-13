import React, { useId } from 'react';
import { garmentColorOptions } from '../lib/garmentColors.js';
import styles from './GarmentColorField.module.css';

export function GarmentColorField({ product, colorOpts = [], block = {}, value, onChange }) {
  const id = useId();
  const options = garmentColorOptions(product, colorOpts, block);
  const selected = options.find(option => option.id === value);
  return (
    <div className={styles.field}>
      <div className={styles.options} role="group" aria-label="대상 색상">
        {options.map((color, index) => (
          <div className={styles.option} key={color.id}>
            <button type="button" className={styles.choice} disabled={color.disabled}
              aria-label={color.label} aria-pressed={value === color.id}
              aria-describedby={color.reason ? `${id}-${index}` : undefined}
              onClick={() => { if (!color.disabled) onChange(color.id); }}>
              <span className={`${styles.swatch}${value === color.id ? ` ${styles.selected}` : ''}`}>
                <svg viewBox="0 0 28 28" aria-hidden="true"><circle cx="14" cy="14" r="14" fill={color.hex || 'var(--fg-3)'} /></svg>
              </span>
              <span className={styles.name}>{color.label}</span>
            </button>
            {color.reason && <span className={styles.reason} id={`${id}-${index}`}>{color.reason}</span>}
          </div>
        ))}
      </div>
      {selected?.source && <div className={styles.source}>
        <img src={selected.source.src} alt={`${selected.label} ${selected.sourceLabel}`} />
        <div><span>{selected.sourceLabel}</span>{selected.note && <p>{selected.note}</p>}</div>
      </div>}
      {(!selected || selected.disabled) && <p className={styles.message} role="status">
        {selected ? '이 색상의 원본 사진을 올려 주세요.' : '색상을 다시 골라 주세요.'}
      </p>}
      {block.detailTargetId && <p className={styles.message}>촬영 대상은 대표 색상 사진을 사용해요.</p>}
    </div>
  );
}
