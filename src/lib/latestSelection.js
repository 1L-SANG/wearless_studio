export async function selectLatestValue({
  currentValue,
  nextValue,
  selectionState,
  confirmedKey = 'confirmedValue',
  commit,
  restore,
  onFailure,
}) {
  if (nextValue == null || Object.is(nextValue, currentValue)) return false;

  const state = selectionState?.current;
  const requestId = state ? ++state.requestId : null;
  if (state) state.pending = (state.pending || 0) + 1;

  try {
    await commit(nextValue);
    if (state) state[confirmedKey] = nextValue;
    return true;
  } catch (error) {
    const isLatest = !state || state.requestId === requestId;
    if (isLatest) {
      restore?.(state?.[confirmedKey] ?? currentValue);
      onFailure?.(error);
    }
    return false;
  } finally {
    if (state) state.pending = Math.max(0, (state.pending || 1) - 1);
  }
}
