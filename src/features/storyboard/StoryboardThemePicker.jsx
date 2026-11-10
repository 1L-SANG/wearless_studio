import React from 'react';
import styles from './StoryboardChoices.module.css';

export const STORYBOARD_THEMES = Object.freeze([
  { value: 'serif', label: '매거진' }, { value: 'boldGothic', label: '볼드' }, { value: 'template', label: '다정' },
]);

export function StoryboardThemePicker({ value = 'template', onChange, disabled = false }) {
  return <div className={styles.themes} role="group" aria-label="글씨 테마">
    {STORYBOARD_THEMES.map(theme => <button type="button" key={theme.value} disabled={disabled} title={theme.label}
      aria-label={theme.label} aria-pressed={value === theme.value} data-font-style={theme.value}
      className={`${styles.themeChoice}${value === theme.value ? ` ${styles.themeSelected}` : ''}`}
      onClick={() => onChange(theme.value)}>
      <span className={styles.themeBar} aria-hidden="true" /><span className={styles.themeSample} aria-hidden="true">
        <span className={styles.themeEnglish}>wearless</span><span className={styles.themeKorean}>웨어리스</span>
      </span>
    </button>)}
  </div>;
}
