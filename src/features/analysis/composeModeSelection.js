import { selectLatestValue } from '../../lib/latestSelection.js';

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
  invalidateStoryboardPrefetch(projectId);
  return selectLatestValue({
    currentValue: currentMode,
    nextValue: nextMode,
    selectionState,
    confirmedKey: 'confirmedMode',
    commit: setComposeMode,
    restore: restoreComposeMode,
    onFailure,
  });
}
