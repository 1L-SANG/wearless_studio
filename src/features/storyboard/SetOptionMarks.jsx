import React from 'react';
import { ChoiceReason } from './StoryboardChoiceControls.jsx';
import styles from './StoryboardChoices.module.css';

export function OuterLockIcon({ closed = false }) {
  return <svg className={styles.optionIcon} width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" fill={closed ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round">
    {closed ? <><path d="m8 3 4 2 4-2 4 5-3 3-1-1v11H8V10l-1 1-3-3z" /><path d="M12 5v16" className={styles.coatSeam} /></>
      : <><path d="m8 3 3 2-2 16H7V10l-1 2-3-3zM16 3l-3 2 2 16h2V10l1 2 3-3z" /><path d="m8 3 3 2-3 5m8-7-3 2 3 5" /></>}
  </svg>;
}

export function ToneIcon({ color, dots = false }) {
  const fill = color?.hex || 'var(--fg-3)';
  return dots ? <svg className={styles.toneDots} width="20" height="16" viewBox="0 0 20 16" aria-hidden="true">
    <circle cx="13.5" cy="8" r="6.5" fill={fill} /><circle cx="13.5" cy="8" r="6.5" fill="var(--bg-1)" opacity=".72" />
    <circle cx="6.5" cy="8" r="6.5" fill={fill} stroke="var(--bg-1)" strokeWidth="1.5" />
  </svg> : <svg className={styles.optionIcon} width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
    <circle cx="8" cy="8" r="8" fill={fill} /><path d="M2.34 13.66A8 8 0 0 0 13.66 2.34Z" fill="var(--bg-1)" opacity=".72" />
  </svg>;
}

export function SetHeadMarks({ state, horizon }) {
  const background = horizon && state.backgroundOn, outer = state.outerVisible && state.outerClosed;
  const label = [background && '배경색 맞춤', outer && '아우터 잠금'].filter(Boolean).join(', ');
  return label ? <ChoiceReason reason={label}><span className={styles.headMarks} tabIndex={0} aria-label={label}>
    {background && <ToneIcon color={state.color} dots />}{outer && <OuterLockIcon closed />}
  </span></ChoiceReason> : null;
}

export function SetCaptionTone({ color }) {
  return <ChoiceReason reason="배경색 맞춤"><span className={styles.headMarks} tabIndex={0} aria-label="배경색 맞춤">
    <ToneIcon color={color} dots />
  </span></ChoiceReason>;
}
