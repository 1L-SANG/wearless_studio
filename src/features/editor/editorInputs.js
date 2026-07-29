export function clampEditorNumber(value, min = -9999, max) {
  return Math.max(min, max == null ? value : Math.min(max, value));
}

export function resolveEditorNumberDraft(nextDraft, min = -9999, max) {
  const value = Number.parseFloat(nextDraft);
  return {
    draft: nextDraft,
    value: Number.isNaN(value) ? null : clampEditorNumber(value, min, max),
  };
}
