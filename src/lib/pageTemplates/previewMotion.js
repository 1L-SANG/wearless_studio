export function previewScrollTiming(imageHeight, viewportHeight) {
  if (!(viewportHeight > 0) || !(imageHeight > viewportHeight)) return { distance: 0, duration: 0 };
  const distance = imageHeight - viewportHeight;
  return { distance, duration: 6000 };
}

export function observePreviewMotion({ viewport, image, hovered, focused, media, makeObserver }) {
  const reduced = media('(prefers-reduced-motion: reduce)');
  const hover = media('(hover: hover) and (pointer: fine)');
  let animation;
  const refresh = () => {
    animation?.cancel();
    animation = null;
    if (reduced.matches || !(focused || hovered && hover.matches) || !image.animate) return;
    const { distance, duration } = previewScrollTiming(image.getBoundingClientRect().height, viewport.clientHeight);
    if (!distance) return;
    animation = image.animate([{ transform: 'translateY(0)' }, { transform: `translateY(-${distance}px)` }],
      { duration, endDelay: 800, easing: 'linear', fill: 'forwards' });
  };
  const observer = makeObserver?.(refresh);
  observer?.observe(viewport);
  observer?.observe(image);
  reduced.addEventListener?.('change', refresh);
  hover.addEventListener?.('change', refresh);
  refresh();
  return () => {
    animation?.cancel();
    observer?.disconnect();
    reduced.removeEventListener?.('change', refresh);
    hover.removeEventListener?.('change', refresh);
  };
}
