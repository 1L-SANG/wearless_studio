import { pointPhotosForRun } from '../lib/pageTemplates/assemble.js';

// The mock generation endpoints persist the same split field as the worker.
// Keep this out of skeleton/preview assembly, which must never create copy.
export function withMockPartDescriptions(storyboard, analysis, copywriting) {
  if (!copywriting) return analysis;
  const candidates = analysis.detailRecommendations?.candidates || [];
  const runs = [];
  for (const card of storyboard) {
    if (runs.at(-1)?.[0].pageSectionKey !== card.pageSectionKey) runs.push([]);
    runs.at(-1).push(card);
  }
  const labels = new Set();
  for (const run of runs) {
    if (run[0].pageSectionKind !== 'points') continue;
    for (const card of pointPhotosForRun(run, storyboard)) {
      const label = card.pointItem?.kind === 'wornCrop' ? card.pointItem.label
        : card.detailTargetId != null ? candidates.find(item => String(item.id) === String(card.detailTargetId))?.label : null;
      if (typeof label === 'string' && label.trim()) labels.add(label);
    }
  }
  if (!labels.size) return analysis;
  const merged = new Map((analysis.partDescriptionCopy || [])
    .filter(item => typeof item?.point === 'string' && item.desc).map(item => [item.point, item.desc]));
  for (const label of labels) merged.set(label, `${label} 디테일을 확인할 수 있습니다.`);
  return { ...analysis, partDescriptionCopy: [...merged].map(([point, desc]) => ({ point, desc })) };
}
