import React from 'react';
import { Icon } from '@/components/ui.jsx';
import { baseColorOf, isNeutralPageSwatch, resolvePageTokens } from '../../lib/pageTemplates/tokens.js';
import { StoryboardThemePicker } from './StoryboardThemePicker.jsx';
import styles from './StoryboardTone.module.css';

export function StoryboardStyleControls({ pageStyle, product, disabled, onFontChange, onToneChange, onTonePreview, onTonePreviewEnd }) {
  const swatch = baseColorOf(product).swatchId;
  const tokens = resolvePageTokens(pageStyle, swatch);
  const point = resolvePageTokens({ ...pageStyle, colorMode: 'point' }, swatch).colors;
  return <div className={styles.controls}>
    <StoryboardThemePicker templateId={tokens.template.id} value={tokens.fontStyle} onChange={onFontChange} disabled={disabled} inline />
    {!isNeutralPageSwatch(swatch) && <>
      <span className={`lbl ${styles.label}`}>페이지 색감</span>
      <div className={styles.tones} role="group" aria-label="페이지 색감">
        {[['mono', '기본'], ['point', '의류 색감에 맞추기']].map(([mode, label]) => <button key={mode} type="button"
          aria-label={label} aria-pressed={tokens.colorMode === mode} disabled={disabled}
          className={`${styles.choice}${tokens.colorMode === mode ? ` ${styles.selected}` : ''}`}
          onPointerEnter={event => { if (!disabled && mode === 'point' && event.pointerType !== 'touch') onTonePreview?.('pointer'); }}
          onPointerLeave={() => onTonePreviewEnd?.('pointer')}
          onMouseEnter={() => { if (!disabled && mode === 'point') onTonePreview?.('pointer'); }}
          onMouseLeave={() => onTonePreviewEnd?.('pointer')}
          onFocus={() => { if (!disabled && mode === 'point') onTonePreview?.('focus'); }}
          onBlur={() => onTonePreviewEnd?.('focus')}
          onClick={() => onToneChange?.(mode)}>
          <svg className={styles.surface} width="100%" height="100%" aria-hidden="true"><rect width="100%" height="100%" fill={mode === 'point' ? point.accentBg : 'var(--bg-1)'} /></svg>
          <svg className={styles.copy} width="100%" height="20" aria-hidden="true"><text x="50%" y="50%" dominantBaseline="central" textAnchor="middle"
            fill={mode === 'point' ? point.accentInk : 'var(--fg-1)'}>{label}</text></svg>
          {tokens.colorMode === mode && <span className={styles.check} aria-hidden="true"><Icon name="check" size={11} /></span>}
        </button>)}
      </div>
    </>}
  </div>;
}
