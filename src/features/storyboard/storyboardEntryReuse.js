export function shouldReuseInitialStoryboardEntry({
  usePending,
  promotionObserved,
  initialEntry,
  projectId,
  entry,
}) {
  return !usePending
    && !promotionObserved
    && initialEntry?.projectId === projectId
    && initialEntry?.raw === entry;
}
