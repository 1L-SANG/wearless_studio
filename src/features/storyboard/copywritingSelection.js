import { selectLatestValue } from '../../lib/latestSelection.js';

export function selectStoryboardCopywriting({
  currentValue,
  nextValue,
  setCopywriting,
  restoreCopywriting,
  selectionState,
  onFailure,
}) {
  return selectLatestValue({
    currentValue,
    nextValue,
    selectionState,
    commit: setCopywriting,
    restore: restoreCopywriting,
    onFailure,
  });
}
