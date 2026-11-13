import React from 'react';
import { baseColorOf, isNeutralPageSwatch, resolvePageTokens } from '../../lib/pageTemplates/tokens.js';
import { StoryboardThemePicker } from './StoryboardThemePicker.jsx';
import { StoryboardStyleCarousel } from './StoryboardStyleCarousel.jsx';
import styles from './StoryboardTone.module.css';

const TONE_LABELS = { mono: '기본', point: '의류 색감' };
const TONE_VALUES = Object.keys(TONE_LABELS);

export function StoryboardStyleControls({ pageStyle, product, disabled, onFontChange, onToneChange }) {
  const swatch = baseColorOf(product).swatchId;
  const tokens = resolvePageTokens(pageStyle, swatch);
  const renderTone = mode => {
    const colors = resolvePageTokens({ ...pageStyle, colorMode: mode }, swatch).colors;
    return <span className={styles.sample} role="img" title={TONE_LABELS[mode]} aria-label={TONE_LABELS[mode]} data-color-mode={mode}>
      <svg className={styles.swatches} viewBox="0 0 60 22" width="60" height="22" aria-hidden="true">
        {[colors.ink, colors.accentInk, colors.accentBg].map((fill, index) => <circle key={index} cx={10 + index * 20} cy="11" r="7" fill={fill} />)}
      </svg>
      <span className={styles.toneName}>{TONE_LABELS[mode]}</span>
    </span>;
  };
  return <div className={styles.controls}>
    <StoryboardThemePicker templateId={tokens.template.id} value={tokens.fontStyle} onChange={onFontChange} disabled={disabled} />
    {!isNeutralPageSwatch(swatch) && <StoryboardStyleCarousel label="페이지 색감" value={tokens.colorMode} values={TONE_VALUES}
      valueLabel={TONE_LABELS[tokens.colorMode]} renderSample={renderTone} onChange={onToneChange} disabled={disabled}
      contextKey={`${tokens.template.id}:${swatch}`} dotAttribute="data-color-mode-dot" />}
  </div>;
}
