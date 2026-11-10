import React from 'react';
import styles from './StoryboardChoices.module.css';
import { getTemplate } from '../../lib/pageTemplates/catalog.js';

export const STORYBOARD_THEMES = Object.freeze([
  { value: 'serif', label: '매거진' }, { value: 'boldGothic', label: '볼드' }, { value: 'template', label: '다정' },
]);

export function StoryboardThemePicker({ value = 'template', templateId = 'emo', onChange, disabled = false, inline = false }) {
  const template = getTemplate(templateId) || getTemplate('emo');
  const themes = STORYBOARD_THEMES.map(theme => theme.value === 'template' ? { ...theme, label: template.id === 'show' ? '기본' : '다정' } : theme);
  const specimen = (role, text) => {
    const font = template.fonts[role];
    return <svg className={role === 'labelEn' ? styles.templateEnglish : styles.templateKorean} width="96" height={role === 'labelEn' ? '22' : '20'} aria-hidden="true">
      <text x="0" y="50%" dominantBaseline="central" fontFamily={font.font} fontWeight={font.weight} fontStyle={font.italic ? 'italic' : 'normal'}
        letterSpacing={`${font.trackingEm}em`} fill="currentColor">{font.uppercase ? text.toUpperCase() : text}</text>
    </svg>;
  };
  return <div className={`${styles.themePicker}${inline ? ` ${styles.inlineThemes}` : ''}`}>
    <span className={`lbl ${styles.themeLabel}`}>글자 스타일</span>
    <div className={styles.themes} role="group" aria-label="글자 스타일">
    {themes.map(theme => <button type="button" key={theme.value} disabled={disabled} title={theme.label}
      aria-label={theme.label} aria-pressed={value === theme.value} data-font-style={theme.value}
      className={`${styles.themeChoice}${value === theme.value ? ` ${styles.themeSelected}` : ''}`}
      onClick={() => onChange(theme.value)}>
      <span className={styles.themeSample} aria-hidden="true">
        {theme.value === 'template' ? <>{specimen('labelEn', 'wearless')}{specimen('titleKo', '웨어리스')}</>
          : <><span className={styles.themeEnglish}>wearless</span><span className={styles.themeKorean}>웨어리스</span></>}
      </span>
    </button>)}
    </div>
  </div>;
}
