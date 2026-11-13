import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Icon } from '@/components/ui.jsx';
import styles from './StoryboardTone.module.css';

// Only the outgoing specimen is transient. The current specimen always reads
// the controlled value, so animation completion can never change a saved choice.
export function StoryboardStyleCarousel({ label, value, values, valueLabel, renderSample, onChange, disabled = false, contextKey, dotAttribute }) {
  const [transition, setTransition] = useState(null);
  const latest = useRef(value), serial = useRef(0);
  const media = useMemo(() => typeof window === 'undefined' ? null : window.matchMedia?.('(prefers-reduced-motion: reduce)'), []);
  const [reduced, setReduced] = useState(() => Boolean(media?.matches));
  useLayoutEffect(() => {
    latest.current = value;
    setTransition(previous => previous && (previous.to !== value || previous.contextKey !== contextKey) ? null : previous);
  });
  useEffect(() => {
    const change = event => { setReduced(event.matches); if (event.matches) setTransition(null); };
    media?.addEventListener?.('change', change);
    return () => media?.removeEventListener?.('change', change);
  }, [media]);
  const active = !reduced && transition?.to === value && transition.contextKey === contextKey ? transition : null;
  const move = direction => {
    if (disabled || values.length < 2) return;
    const from = latest.current;
    const index = Math.max(0, values.indexOf(from));
    const next = values[(index + direction + values.length) % values.length];
    latest.current = next;
    setTransition(media?.matches ? null : { id: ++serial.current, from: renderSample(from), to: next, direction, contextKey });
    onChange?.(next);
  };
  const onKeyDown = event => {
    if (disabled || values.length < 2 || !event.currentTarget.contains(event.target)) return;
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    move(event.key === 'ArrowLeft' ? -1 : 1);
  };
  const finish = event => {
    if (event.target !== event.currentTarget) return;
    setTransition(previous => previous?.id === active?.id ? null : previous);
  };
  return <div className={styles.picker} role="group" aria-label={label} onKeyDown={onKeyDown}>
    <div className={styles.pickerHead}>
      <span className={`lbl ${styles.label}`}>{label}</span>
      <span className={styles.dots} aria-hidden="true">
        {values.map(option => <span key={option} className={styles.dot} {...{ [dotAttribute]: option }} data-active={value === option} />)}
      </span>
    </div>
    <div className={`modal-actions ${styles.step}`}>
      <button type="button" className={styles.arrow} aria-label={`이전 ${label}`} disabled={disabled || values.length < 2} onClick={() => move(-1)}>
        <Icon name="chevLeft" size={18} />
      </button>
      <div className={styles.viewport}>
        <div key={`current-${active?.id ?? 'still'}`} className={`${styles.slide}${active ? ` ${active.direction > 0 ? styles.inRight : styles.inLeft}` : ''}`}
          data-slide="current" onAnimationEnd={finish}>{renderSample(value)}</div>
        {active && <div key={`outgoing-${active.id}`} className={`${styles.slide} ${styles.outgoing} ${active.direction > 0 ? styles.outLeft : styles.outRight}`}
          data-slide="outgoing" aria-hidden="true">{active.from}</div>}
      </div>
      <button type="button" className={styles.arrow} aria-label={`다음 ${label}`} disabled={disabled || values.length < 2} onClick={() => move(1)}>
        <Icon name="chevRight" size={18} />
      </button>
    </div>
    <span className="sr-only" aria-live="polite" aria-atomic="true">{label}: {valueLabel}, {values.indexOf(value) + 1}/{values.length}</span>
  </div>;
}
