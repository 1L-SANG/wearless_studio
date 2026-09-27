// Both catalog columns share the inspector's right dock. Never move the canvas.
export function setPreviewPlacement({ panel, viewportWidth, viewportHeight }) {
  const gap = 12, dockGap = 20, minimumWidth = 120;
  const top = Math.max(gap, panel.top);
  const height = Math.max(80, Math.min(panel.bottom - panel.top, viewportHeight - top - gap));
  const rightRoom = viewportWidth - panel.right - dockGap - gap;
  if (rightRoom >= minimumWidth) return { left: panel.right + dockGap, top, width: Math.min(180, rightRoom), height, placement: 'right' };
  const leftRoom = panel.left - dockGap - gap;
  if (leftRoom >= minimumWidth) { const width = Math.min(180, leftRoom); return { left: panel.left - dockGap - width, top, width, height, placement: 'left' }; }
  const above = panel.top - gap * 2, below = viewportHeight - panel.bottom - gap * 2;
  const placement = Math.max(above, below) >= 160 ? (above > below ? 'top' : 'bottom') : 'compact';
  const stripHeight = placement === 'compact' ? Math.min(252, viewportHeight - gap * 2) : Math.min(280, placement === 'top' ? above : below);
  const width = Math.min(560, viewportWidth - gap * 2);
  const left = Math.max(gap, Math.min(panel.left, viewportWidth - width - gap));
  return { left, top: placement === 'top' ? panel.top - gap - stripHeight : placement === 'bottom' ? panel.bottom + gap : viewportHeight - gap - stripHeight, width, height: stripHeight, placement };
}
