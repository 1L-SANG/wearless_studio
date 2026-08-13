import { selectAnalysisComposeMode } from '../analysis/composeModeSelection.js';

export async function applyStoryboardComposeMode({
  currentMode,
  nextMode,
  projectId,
  flushBoard,
  setComposeMode,
  restoreComposeMode,
  invalidateStoryboardPrefetch,
  selectionState,
  reloadStoryboard,
  onFlushFailure,
  onPatchFailure,
}) {
  try {
    await flushBoard();
  } catch (error) {
    onFlushFailure?.(error);
    return false;
  }

  const changed = await selectAnalysisComposeMode({
    currentMode,
    nextMode,
    projectId,
    setComposeMode,
    restoreComposeMode,
    invalidateStoryboardPrefetch,
    selectionState,
    onFailure: onPatchFailure,
  });
  if (!changed) return false;

  await reloadStoryboard();
  return true;
}
