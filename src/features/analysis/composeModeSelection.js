export function estimateComposeModeCredits(count, perCut) {
  if (count == null || count === '') return null;
  return String(count)
    .split('~')
    .map((value) => Number(value) * perCut)
    .join('~');
}

export async function selectAnalysisComposeMode({
  currentMode,
  nextMode,
  projectId,
  setComposeMode,
  invalidateStoryboardPrefetch,
}) {
  if (!nextMode || nextMode === currentMode) return false;
  invalidateStoryboardPrefetch(projectId);
  await setComposeMode(nextMode);
  return true;
}
