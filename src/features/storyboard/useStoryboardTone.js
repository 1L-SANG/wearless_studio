import { useEffect, useMemo, useRef, useState } from 'react';
import { baseColorOf, isNeutralPageSwatch, resolvePageTokens } from '../../lib/pageTemplates/tokens.js';

// Display state only. The sole persistence call is the explicit select callback.
export function useStoryboardTone({ projectId, pageStyle, product, sectionCount, onChange, resetKey = 0, timing = globalThis }) {
  const swatch = baseColorOf(product).swatchId;
  const available = !isNeutralPageSwatch(swatch);
  const selectedMode = resolvePageTokens(pageStyle, swatch).colorMode;
  const key = JSON.stringify([projectId, pageStyle?.templateId, swatch, resetKey]);
  const [preview, setPreview] = useState(null), [waveState, setWave] = useState(null);
  const timers = useRef([]), serial = useRef(0), context = useRef(key);
  const latest = useRef({ key, observed: selectedMode, mode: selectedMode });
  context.current = key;
  if (latest.current.key !== key || latest.current.observed !== selectedMode) latest.current = { key, observed: selectedMode, mode: selectedMode };
  const media = useMemo(() => timing.matchMedia?.('(prefers-reduced-motion: reduce)'), [timing]);
  const clear = () => { timers.current.forEach(id => timing.clearTimeout(id)); timers.current = []; serial.current++; };
  useEffect(() => { setPreview(null); setWave(null); return () => clear(); }, [key, timing]);
  useEffect(() => {
    const change = event => { if (event.matches) { clear(); setWave(null); } };
    media?.addEventListener?.('change', change);
    return () => media?.removeEventListener?.('change', change);
  }, [media, timing]);
  const wave = waveState?.key === key && available ? waveState : null;
  const previewing = preview?.key === key && (preview.pointer || preview.focus);
  return {
    available, mode: available && previewing ? 'point' : wave?.toMode || selectedMode, wave,
    beginPreview(source) {
      if (!available) return;
      clear(); setWave(null);
      setPreview(previous => ({ ...(previous?.key === key ? previous : {}), key, [source]: true }));
    },
    endPreview(source) { setPreview(previous => previous?.key === key ? { ...previous, [source]: false } : null); },
    select(mode) {
      if (!available || !['mono', 'point'].includes(mode)) return;
      const fromMode = latest.current.mode;
      latest.current = { ...latest.current, mode };
      clear(); setPreview(null); setWave(null); onChange(mode);
      if (mode === fromMode || media?.matches || !sectionCount) return;
      const id = serial.current;
      setWave({ key, fromMode, toMode: mode, revealed: 0 });
      for (let i = 0; i < sectionCount; i++) timers.current.push(timing.setTimeout(() => {
        if (context.current === key && serial.current === id) setWave(previous => previous && { ...previous, revealed: i + 1 });
      }, 100 + Math.round(i * 400 / Math.max(1, sectionCount - 1))));
      timers.current.push(timing.setTimeout(() => {
        if (context.current === key && serial.current === id) { timers.current = []; setWave(null); }
      }, 800));
    },
  };
}
