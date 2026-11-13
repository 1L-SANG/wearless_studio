import styles from './StoryboardLayout.module.css';

// A transient DOM clone uses the Web Animations API, never React style props
// or persisted layout values. Reduced motion and unavailable APIs insert at once.
export function captureSectionPreview(source) {
  return source ? { node: source.cloneNode(true), rect: source.getBoundingClientRect() } : null;
}

export function flySectionPreview(source, target) {
  if (!source || !target || typeof document === 'undefined' || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
  const start = source.rect, end = target.getBoundingClientRect();
  if (![start.width, start.height, end.width, end.height].every(value => Number.isFinite(value) && value > 0)) return;
  const clone = source.node;
  clone.className = `sb-v5-theme ${styles.sectionFlight}`;
  clone.setAttribute('aria-hidden', 'true'); clone.removeAttribute('data-section-flight-source');
  if (typeof clone.animate !== 'function') return;
  document.body.appendChild(clone);
  const tokens = typeof getComputedStyle === 'function' ? getComputedStyle(clone) : null;
  const duration = Number.parseFloat(tokens?.getPropertyValue('--sb-flight-duration')) || 520;
  const easing = tokens?.getPropertyValue('--sb-ease').trim() || 'cubic-bezier(.2,.8,.2,1)';
  const animation = clone.animate([
    { transform: `translate(${start.left}px, ${start.top}px) scale(${start.width / 64}, ${start.height / 84})`, opacity: .85 },
    { transform: `translate(${end.left}px, ${end.top}px) scale(${end.width / 64}, ${end.height / 84})`, opacity: .3 },
  ], { duration, easing, fill: 'forwards' });
  animation.finished.then(() => clone.remove(), () => clone.remove());
  return () => { animation.cancel(); clone.remove(); };
}
