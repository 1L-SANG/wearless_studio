// Pure presentation and polling rules shared by the hook and local tests.
export function baseProductPhoto(product) {
  const colors = product?.colors || [];
  const base = colors.find((color) => color.isBase) || colors[0];
  return base?.images?.find((image) => String(image.slot || 'Front').toLowerCase() === 'front') || null;
}

const sameSources = (a, b) => Array.isArray(a) && Array.isArray(b)
  && a.length === b.length && a.every((id, i) => id === b[i]);
export function currentDraftVisuals(result, bindings) {
  const current = (role) => sameSources(result?.[role]?.sourceAssetIds, bindings?.[role]?.sourceAssetIds)
    && (role !== 'custom' || result[role].localId === bindings[role].localId) ? result[role] : null;
  return { product: current('product'), custom: current('custom') };
}

export function mergeDraftCustomVisual(analysis, visual) {
  if (!visual?.localId) return analysis;
  let changed = false;
  const matchClothing = analysis.matchClothing?.map((item) => {
    if (!item.isCustom || item.id !== visual.localId) return item;
    const fields = { boardUrl: visual.boardUrl || null, cutoutStatus: visual.cutoutStatus || visual.state || 'failed' };
    if (visual.thumbnailUrl) Object.assign(fields, { thumbnailUrl: visual.thumbnailUrl, thumb: visual.thumbnailUrl });
    if (Object.entries(fields).every(([key, value]) => item[key] === value)) return item;
    changed = true;
    return { ...item, ...fields };
  });
  return changed ? { ...analysis, matchClothing } : analysis;
}

export function startVisualPolling({ query, onResult, onStop, setTimer = setTimeout, clearTimer = clearTimeout,
  maxErrors = 3, maxPolls = 60 }) {
  let canceled = false; let timer; let errors = 0; let polls = 0;
  const stop = () => { canceled = true; clearTimer(timer); };
  const poll = async () => {
    if (canceled) return;
    try {
      const result = await query();
      if (canceled) return;
      onResult(result);
      const processing = result?.front?.tidyStatus === 'processing'
        || [result?.product, result?.custom].some((visual) => visual?.state === 'processing');
      if (!processing || result?.state === 'off') return;
    } catch {
      if (canceled || ++errors >= maxErrors) { if (!canceled) onStop?.(); return; }
    }
    if (++polls >= maxPolls) { onStop?.(); return; }
    timer = setTimer(poll, 5000);
  };
  void poll();
  return stop;
}
