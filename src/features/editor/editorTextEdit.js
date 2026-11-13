// Only seller text/size edits release the template's explicit line breaks.
// Keep the old object for undo; geometry-only measurements and other styles do not.
export function editedTextElement(element, patch) {
  const changed = element.type === 'text' && element.keepWords && (
    Object.hasOwn(patch, 'text') && patch.text !== element.text
    || patch.style && Object.hasOwn(patch.style, 'size') && patch.style.size !== element.style?.size);
  return { ...element, ...patch, ...(changed ? { keepWords: false } : {}) };
}
