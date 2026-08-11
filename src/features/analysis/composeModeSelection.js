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
  restoreComposeMode,
  invalidateStoryboardPrefetch,
  selectionState,
  onFailure,
}) {
  if (!nextMode || nextMode === currentMode) return false;
  const requestId = selectionState ? ++selectionState.current.requestId : null;
  invalidateStoryboardPrefetch(projectId);
  try {
    await setComposeMode(nextMode);
    if (selectionState) selectionState.current.confirmedMode = nextMode;
    return true;
  } catch (error) {
    const isLatest = !selectionState || selectionState.current.requestId === requestId;
    if (isLatest) {
      restoreComposeMode?.(selectionState?.current.confirmedMode ?? currentMode);
      onFailure?.(error);
    }
    return false;
  }
}
