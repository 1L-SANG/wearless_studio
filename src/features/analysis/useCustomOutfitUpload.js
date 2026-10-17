import { useEffect, useRef, useState } from 'react';
import { customMatchWaitProgress } from '@/lib/smoothProgress.js';
import { complementaryOutfitSlot, setOutfitSelection } from '@/lib/outfitSelections.js';

export function useCustomOutfitUpload({ analysis, onChange, onRefresh }) {
  const [upload, setUpload] = useState(null);
  const epoch = useRef(0);
  const latest = useRef({ analysis, onChange, onRefresh, upload });
  latest.current = { analysis, onChange, onRefresh, upload };
  const urls = useRef(new Set());
  useEffect(() => () => { urls.current.forEach((url) => URL.revokeObjectURL(url)); urls.current.clear(); }, []);
  const begin = (file) => {
    if (!file) return;
    const previewSrc = URL.createObjectURL(file); urls.current.add(previewSrc);
    setUpload({ previewSrc, startedAt: performance.now(), itemId: null, resultAtMs: null, progress: 0,
      role: complementaryOutfitSlot(analysis.clothingType), epoch: epoch.current });
  };
  const receive = (nextAnalysis) => {
    const item = nextAnalysis?.matchClothing?.find((candidate) => candidate.isCustom);
    latest.current.onRefresh(nextAnalysis);
    if (!item) { setUpload(null); return; }
    setUpload((prior) => prior ? { ...prior, itemId: item.id } : null);
  };
  const fail = () => {
    const src = latest.current.upload?.previewSrc;
    if (src && urls.current.has(src)) { URL.revokeObjectURL(src); urls.current.delete(src); }
    setUpload(null);
  };
  const pendingId = upload?.itemId;
  const pendingItem = analysis.matchClothing?.find((item) => item.id === pendingId);
  useEffect(() => {
    if (!pendingItem || pendingItem.cutoutStatus === 'processing') return;
    setUpload((prior) => prior && prior.resultAtMs == null ? { ...prior, resultAtMs: performance.now() - prior.startedAt } : prior);
  }, [pendingId, pendingItem?.cutoutStatus]);
  useEffect(() => {
    if (!upload) return undefined;
    let raf;
    const tick = () => {
      const current = latest.current.upload;
      if (!current) return;
      const progress = customMatchWaitProgress({ elapsedMs: performance.now() - current.startedAt, resultAtMs: current.resultAtMs });
      if (progress >= 100) {
        const a = latest.current.analysis;
        const item = a.matchClothing?.find((candidate) => candidate.id === current.itemId);
        // 정리 도중 다른 옷을 눌렀다가 되돌아왔어도 자동 선택하지 않는다.
        if (current.epoch === epoch.current && current.role === complementaryOutfitSlot(a.clothingType) && item && item.isCompatible !== false && item.cutoutStatus !== 'failed') {
          const patch = setOutfitSelection(a, current.role, item.id);
          if (patch) latest.current.onChange(patch);
        }
        setUpload(null);
        URL.revokeObjectURL(current.previewSrc); urls.current.delete(current.previewSrc);
        return;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [!!upload]);
  // 새로고침으로 이미 정리 중인 내 옷이 들어와도 누를 수 없는 진행 타일로 복원한다.
  useEffect(() => {
    if (latest.current.upload) return;
    const item = analysis.matchClothing?.find((candidate) => candidate.isCustom && candidate.cutoutStatus === 'processing');
    if (item) setUpload({ previewSrc: item.imageUrl || item.thumb, startedAt: performance.now(), itemId: item.id,
      resultAtMs: null, progress: 0, role: complementaryOutfitSlot(analysis.clothingType), epoch: -1 });
  }, [analysis.matchClothing]);
  return { upload, begin, receive, fail, noteSelection: () => { epoch.current += 1; } };
}
