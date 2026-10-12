export function getSwatchScrollThumb(visibleHeight, scrollHeight, scrollTop) {
  if (visibleHeight <= 0 || scrollHeight <= visibleHeight) return null;
  const height = Math.min(visibleHeight, Math.max(24, visibleHeight * visibleHeight / scrollHeight));
  const progress = Math.min(1, Math.max(0, scrollTop / (scrollHeight - visibleHeight)));
  return { height, offset: progress * (visibleHeight - height) };
}
