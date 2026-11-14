// Only seller text/size edits release the template's explicit line breaks.
// Keep the old object for undo; geometry-only measurements and other styles do not.
export function editedTextElement(element, patch) {
  const textChanged = element.type === 'text' && Object.hasOwn(patch, 'text') && patch.text !== element.text;
  const changed = element.type === 'text' && element.keepWords && (
    textChanged
    || patch.style && Object.hasOwn(patch.style, 'size') && patch.style.size !== element.style?.size);
  const next = { ...element, ...patch, ...(changed ? { keepWords: false } : {}) };
  if (textChanged) {
    delete next.aiDraft;
    // Even typing the original wording back must not invite a late generated replacement.
    delete next.genAutoText;
  }
  return next;
}
