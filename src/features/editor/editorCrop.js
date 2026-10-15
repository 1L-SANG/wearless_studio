// The saved crop remains {ox, oy, iw, ih}; only this transient draft has a range.
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

export function beginEditorCrop(blockId, el, natural = {}) {
  let fx = el.x, fy = el.y, fw = el.w, fh = el.h;
  let iw = fw, ih = fh;
  const ratio = natural.width / natural.height;
  if (!el.crop && ratio > 0 && el.fit !== 'fill') {
    iw = el.fit === 'contain' ? Math.min(fw, fh * ratio) : Math.max(fw, fh * ratio);
    ih = iw / ratio;
    if (el.fit === 'contain') { fx += (fw - iw) / 2; fy += (fh - ih) / 2; fw = iw; fh = ih; }
  }
  const geometry = { fx, fy, fw, fh, ...(el.crop || { ox: (iw - fw) / 2, oy: (ih - fh) / 2, iw, ih }) };
  return { blockId, elId: el.id, src: el.src, radius: el.radius || 0, ...geometry, initial: geometry };
}

export function resizedPhotoPatch(el, w, h) {
  const patch = { w, h };
  if (el.crop && el.w > 0 && el.h > 0) patch.crop = {
    ox: Math.round(el.crop.ox * w / el.w), oy: Math.round(el.crop.oy * h / el.h),
    iw: Math.round(el.crop.iw * w / el.w), ih: Math.round(el.crop.ih * h / el.h),
  };
  return patch;
}

export function dragEditorCrop(c, mode, dir, dx, dy) {
  let { fx, fy, fw, fh, ox, oy } = c;
  if (mode === 'img') {
    ox = clamp(c.ox - dx, 0, Math.max(0, c.iw - fw));
    oy = clamp(c.oy - dy, 0, Math.max(0, c.ih - fh));
  } else if (mode === 'range') {
    const mx = clamp(dx, -ox, c.iw - fw - ox), my = clamp(dy, -oy, c.ih - fh - oy);
    fx += mx; fy += my; ox += mx; oy += my;
  } else {
    if (dir.includes('e')) fw = clamp(c.fw + dx, 24, c.iw - c.ox);
    if (dir.includes('s')) fh = clamp(c.fh + dy, 24, c.ih - c.oy);
    if (dir.includes('w')) { const d = clamp(dx, -c.ox, c.fw - 24); fx += d; fw -= d; ox += d; }
    if (dir.includes('n')) { const d = clamp(dy, -c.oy, c.fh - 24); fy += d; fh -= d; oy += d; }
    if (c.ratio > 0) {
      const r = c.ratio, horizontal = /[ew]/.test(dir), vertical = /[ns]/.test(dir);
      const maxW = horizontal ? (dir.includes('w') ? c.ox + c.fw : c.iw - c.ox)
        : 2 * Math.min(c.ox + c.fw / 2, c.iw - c.ox - c.fw / 2);
      const maxH = vertical ? (dir.includes('n') ? c.oy + c.fh : c.ih - c.oy)
        : 2 * Math.min(c.oy + c.fh / 2, c.ih - c.oy - c.fh / 2);
      const wanted = horizontal && (!vertical || Math.abs(fw - c.fw) >= Math.abs((fh - c.fh) * r)) ? fw : fh * r;
      fw = clamp(wanted, Math.max(24, 24 * r), Math.min(maxW, maxH * r)); fh = fw / r;
      fx = horizontal ? (dir.includes('w') ? c.fx + c.fw - fw : c.fx) : c.fx + (c.fw - fw) / 2;
      fy = vertical ? (dir.includes('n') ? c.fy + c.fh - fh : c.fy) : c.fy + (c.fh - fh) / 2;
      ox = c.ox + fx - c.fx; oy = c.oy + fy - c.fy;
    }
  }
  // A deliberate handle resize establishes a new basis; preset changes do not.
  return { ...c, fx, fy, fw, fh, ox, oy,
    ...(c.ratioBase && mode === 'frame' && (fw !== c.fw || fh !== c.fh) ? { ratioBase: { fw, fh } } : {}) };
}

export function ratioEditorCrop(c, ratio) {
  if (!(ratio > 0)) return { ...c, ratio: null };
  const ratioBase = c.ratioBase || { fw: c.fw, fh: c.fh };
  const fw = Math.min(ratioBase.fw, ratioBase.fh * ratio, c.iw, c.ih * ratio), fh = fw / ratio;
  const ox = clamp(c.ox + (c.fw - fw) / 2, 0, c.iw - fw);
  const oy = clamp(c.oy + (c.fh - fh) / 2, 0, c.ih - fh);
  return { ...c, fx: c.fx + ox - c.ox, fy: c.fy + oy - c.oy, fw, fh, ox, oy, ratio, ratioBase };
}

// Screen coordinates: keep every control reachable, including after scrolling.
export function cropToolbarPosition(frame, viewport, size) {
  const margin = 8, gap = 16;
  const left = clamp(frame.left, viewport.left + margin, Math.max(viewport.left + margin, viewport.right - size.width - margin));
  const below = frame.bottom + gap, above = frame.top - size.height - gap;
  const preferred = below + size.height <= viewport.bottom - margin ? below
    : above >= viewport.top + margin ? above : below;
  const top = clamp(preferred, viewport.top + margin, Math.max(viewport.top + margin, viewport.bottom - size.height - margin));
  return { left, top };
}

export function zoomEditorCrop(c, factor) {
  const ratio = c.ih / c.iw;
  const iw = clamp(c.iw * factor, Math.max(c.fw, c.fh / ratio), Math.max(c.fw, c.fh / ratio) * 12);
  const ih = iw * ratio;
  const ox = clamp((c.ox + c.fw / 2) / c.iw * iw - c.fw / 2, 0, iw - c.fw);
  const oy = clamp((c.oy + c.fh / 2) / c.ih * ih - c.fh / 2, 0, ih - c.fh);
  return { ...c, iw, ih, ox, oy };
}

export function editorCropPatch(c) {
  return { x: Math.round(c.fx), y: Math.round(c.fy), w: Math.round(c.fw), h: Math.round(c.fh),
    radius: Math.min(c.radius || 0, Math.round(Math.min(c.fw, c.fh) / 2)),
    crop: { ox: Math.round(c.ox), oy: Math.round(c.oy), iw: Math.round(c.iw), ih: Math.round(c.ih) } };
}

export function applyEditorCrop(blocks, c) {
  if (!c) return blocks;
  if (c.initial && Object.keys(c.initial).every(key => Math.abs(c[key] - c.initial[key]) < 0.001)) return blocks;
  const block = blocks.find(b => b.id === c.blockId);
  const el = block?.elements.find(e => e.id === c.elId);
  if (!el || el.src !== c.src || el.locked || el.hidden || el.genPending) return blocks;
  const patch = editorCropPatch(c);
  const previousCrop = el.crop || { ox: 0, oy: 0, iw: el.w, ih: el.h };
  if (['x', 'y', 'w', 'h'].every(key => el[key] === patch[key])
    && Object.keys(previousCrop).every(key => previousCrop[key] === patch.crop[key])) return blocks;
  return blocks.map(b => b !== block ? b : { ...b, elements: b.elements.map(e => e !== el ? e : { ...e, ...patch }) });
}
