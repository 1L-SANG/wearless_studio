import React from 'react';
import { createPortal } from 'react-dom';
import styles from './StoryboardLayout.module.css';

export function StoryboardContinue({ count, credits, disabled, warning, onContinue, hidden, undoEntry, undoExiting, onUndo,
  onUndoAnimationEnd, onUndoEnter, onUndoLeave, label = '다음: 마네킹컷 선택' }) {
  const bar = <div className={`sb-v5-theme ${styles.continueBar}${hidden ? ` ${styles.continueHidden}` : ''}`} aria-hidden={hidden || undefined}>
    {undoEntry && <div className={`${styles.undoInline}${undoExiting ? ` ${styles.undoExiting}` : ''}`} role="status" aria-live="polite"
      title={undoEntry.message} onAnimationEnd={onUndoAnimationEnd} onMouseEnter={onUndoEnter} onMouseLeave={onUndoLeave} onFocus={onUndoEnter} onBlur={onUndoLeave}>
      <span>{undoEntry.operationCount}가지 바꿨어요</span><span aria-hidden="true">·</span><button type="button" onClick={onUndo} disabled={hidden} tabIndex={hidden ? -1 : undefined}>되돌리기</button>
    </div>}
    <span>구성 <strong>{count}</strong>컷</span><span>{warning || <>예상 <strong>{credits}</strong>크레딧</>}</span>
    <button type="button" className={`btn btn-primary btn-glowring ${styles.continue}`} onClick={onContinue} disabled={disabled || hidden} tabIndex={hidden ? -1 : undefined}>
      {label}
    </button>
  </div>;
  // Page-enter transforms create a containing block for fixed descendants.
  // Keep the action attached to the viewport, including at the page footer.
  return typeof document !== 'undefined' && document.body ? createPortal(bar, document.body) : bar;
}
