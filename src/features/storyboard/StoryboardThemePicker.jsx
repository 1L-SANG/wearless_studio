import React from 'react';
import { Icon } from '@/components/ui.jsx';
import styles from './StoryboardChoices.module.css';
import { pageTemplatesCatalog } from '../../lib/pageTemplates/catalog.js';
import { resolvePageTokens } from '../../lib/pageTemplates/tokens.js';

const STYLE_LABELS = { serif: '매거진', boldGothic: '볼드' };

export function StoryboardThemePicker({ value = 'template', templateId = 'emo', onChange, disabled = false, inline = false }) {
  const values = Object.keys(pageTemplatesCatalog.fontStyles);
  const tokens = resolvePageTokens({ templateId, fontStyle: value });
  const currentIndex = values.indexOf(tokens.fontStyle);
  const label = tokens.fontStyle === 'template' ? tokens.template.id === 'show' ? '기본' : '다정'
    : pageTemplatesCatalog.fontStyleLabels?.[tokens.fontStyle] || STYLE_LABELS[tokens.fontStyle] || tokens.fontStyle;
  const move = direction => {
    if (disabled || values.length < 2) return;
    onChange?.(values[(currentIndex + direction + values.length) % values.length]);
  };
  const onKeyDown = event => {
    if (disabled || values.length < 2 || !event.currentTarget.contains(event.target)) return;
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    move(event.key === 'ArrowLeft' ? -1 : 1);
  };
  const specimen = (role, text) => {
    const font = tokens.fonts[role];
    return <svg className={role === 'labelEn' ? styles.themeEnglish : styles.themeKorean} width="100%" height={role === 'labelEn' ? '22' : '20'} aria-hidden="true">
      <text x="50%" y="50%" textAnchor="middle" dominantBaseline="central" fontFamily={font.font} fontWeight={font.weight} fontStyle={font.italic ? 'italic' : 'normal'}
        letterSpacing={`${font.trackingEm}em`} fill="currentColor">{font.uppercase ? text.toUpperCase() : text}</text>
    </svg>;
  };
  return <div className={`${styles.themePicker}${inline ? ` ${styles.inlineThemes}` : ''}`}>
    <span className={`lbl ${styles.themeLabel}`}>글자 스타일</span>
    <div className={styles.themes} role="group" aria-label="글자 스타일" onKeyDown={onKeyDown}>
      <button type="button" className={styles.themeArrow} aria-label="이전 글자 스타일" disabled={disabled || values.length < 2} onClick={() => move(-1)}>
        <Icon name="chevLeft" size={18} />
      </button>
      <span className={styles.themeSample} role="img" title={label} aria-label={label} data-font-style={tokens.fontStyle}>
        {specimen('labelEn', 'wearless')}{specimen('titleKo', '웨어리스')}
      </span>
      <span className={styles.themeDots} aria-hidden="true">
        {values.map(style => <span key={style} className={styles.themeDot} data-font-style-dot={style} data-active={tokens.fontStyle === style} />)}
      </span>
      <button type="button" className={styles.themeArrow} aria-label="다음 글자 스타일" disabled={disabled || values.length < 2} onClick={() => move(1)}>
        <Icon name="chevRight" size={18} />
      </button>
    </div>
  </div>;
}
