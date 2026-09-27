const EDGE = 96;
const MAX_SPEED = 300; // px/second: deliberate placement, not a fast page jump.

export function edgeScrollVelocity(y, height) {
  if (!Number.isFinite(y) || y < 0 || y > height) return 0;
  const zone = Math.min(EDGE, height / 3);
  if (y < zone) return -MAX_SPEED * ((zone - y) / zone) ** 2;
  if (y > height - zone) return MAX_SPEED * ((y - height + zone) / zone) ** 2;
  return 0;
}

// Attach only while a Wearless card/set is being dragged. No external-file scroll.
export function attachStoryboardDragScroll(win = window) {
  const doc = win.document;
  let frame = null;
  let lastTime = null;
  let velocity = 0;
  let ended = false;
  const pause = () => {
    if (frame !== null) win.cancelAnimationFrame(frame);
    frame = null; lastTime = null; velocity = 0;
  };
  const stop = () => { ended = true; pause(); };
  const tick = (time) => {
    if (ended || !velocity) { frame = null; return; }
    if (lastTime !== null) win.scrollBy({ top: velocity * Math.min(time - lastTime, 48) / 1000, behavior: 'instant' });
    lastTime = time;
    frame = win.requestAnimationFrame(tick);
  };
  const track = (event) => {
    if (ended) return;
    velocity = edgeScrollVelocity(event.clientY, win.innerHeight);
    if (event.clientX < 0 || event.clientX > win.innerWidth) velocity = 0;
    if (!velocity) { pause(); return; }
    if (frame === null) frame = win.requestAnimationFrame(tick);
  };
  const leave = (event) => {
    if (!event.relatedTarget && (event.clientX <= 0 || event.clientX >= win.innerWidth
      || event.clientY <= 0 || event.clientY >= win.innerHeight)) pause();
  };
  const wheel = (event) => {
    if (ended || event.ctrlKey || !event.deltaY) return;
    event.preventDefault();
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? win.innerHeight : 1;
    win.scrollBy({ top: event.deltaY * unit, behavior: 'instant' });
  };
  const key = (event) => { if (event.key === 'Escape') stop(); };
  const hidden = () => { if (doc.hidden) stop(); };
  doc.addEventListener('dragover', track, true);
  doc.addEventListener('dragleave', leave, true);
  doc.addEventListener('drop', stop, true);
  doc.addEventListener('dragend', stop, true);
  doc.addEventListener('wheel', wheel, { capture: true, passive: false });
  doc.addEventListener('keydown', key, true);
  doc.addEventListener('visibilitychange', hidden);
  win.addEventListener('blur', stop);
  return () => {
    stop();
    doc.removeEventListener('dragover', track, true);
    doc.removeEventListener('dragleave', leave, true);
    doc.removeEventListener('drop', stop, true);
    doc.removeEventListener('dragend', stop, true);
    doc.removeEventListener('wheel', wheel, true);
    doc.removeEventListener('keydown', key, true);
    doc.removeEventListener('visibilitychange', hidden);
    win.removeEventListener('blur', stop);
  };
}
