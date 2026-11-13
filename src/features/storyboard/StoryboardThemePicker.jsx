import React from 'react';
import { StoryboardStyleCarousel } from './StoryboardStyleCarousel.jsx';
import styles from './StoryboardTone.module.css';
import { pageTemplatesCatalog } from '../../lib/pageTemplates/catalog.js';
import { resolvePageTokens } from '../../lib/pageTemplates/tokens.js';

const STYLE_LABELS = { serif: '매거진', boldGothic: '볼드' };

export function StoryboardThemePicker({ value = 'template', templateId = 'emo', onChange, disabled = false }) {
  const values = Object.keys(pageTemplatesCatalog.fontStyles);
  const tokens = resolvePageTokens({ templateId, fontStyle: value });
  const labelOf = style => style === 'template' ? tokens.template.id === 'show' ? '기본' : '다정'
    : pageTemplatesCatalog.fontStyleLabels?.[style] || STYLE_LABELS[style] || style;
  const renderSample = style => {
    const sample = resolvePageTokens({ templateId, fontStyle: style });
    const specimen = (role, text) => {
      const font = sample.fonts[role];
      return <svg className={role === 'labelEn' ? styles.themeEnglish : styles.themeKorean} width="100%" height={role === 'labelEn' ? '22' : '20'} aria-hidden="true">
        <text x="50%" y="50%" textAnchor="middle" dominantBaseline="central" fontFamily={font.font} fontWeight={font.weight} fontStyle={font.italic ? 'italic' : 'normal'}
          letterSpacing={`${font.trackingEm}em`} fill="currentColor">{font.uppercase ? text.toUpperCase() : text}</text>
      </svg>;
    };
    return <span className={styles.sample} role="img" title={labelOf(style)} aria-label={labelOf(style)} data-font-style={style}>
      {specimen('labelEn', 'wearless')}{specimen('titleKo', '웨어리스')}
    </span>;
  };
  return <StoryboardStyleCarousel label="글자 스타일" value={tokens.fontStyle} values={values} valueLabel={labelOf(tokens.fontStyle)}
    renderSample={renderSample} onChange={onChange} disabled={disabled} contextKey={tokens.template.id} dotAttribute="data-font-style-dot" />;
}
