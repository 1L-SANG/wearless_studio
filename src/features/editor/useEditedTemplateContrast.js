import { useContext, useLayoutEffect, useState, useSyncExternalStore } from 'react';
import { measureEditedTemplateContrast } from './editedTemplateContrast.js';
import { trackEditedContrast } from './editorContrastPending.js';
import { createEditorContrastStore, EditorContrastContext } from './editorContrastStore.js';

export function useEditedTemplateContrast(element, ref, { editing, preview, plated = false }) {
  const sharedStore = useContext(EditorContrastContext);
  const [localStore] = useState(createEditorContrastStore);
  const store = sharedStore || localStore;
  const measured = useSyncExternalStore(store.subscribe, () => store.get(element.id), () => null);
  const needsMeasure = !plated && element.type === 'text' && element.keepWords === false && Boolean(element.contrastFor)
    && element.style?.color === element.contrastFor.style?.color;
  const key = needsMeasure ? JSON.stringify([element.text, element.x, element.y, element.w, element.style]) : null;
  useLayoutEffect(() => {
    if (!key || editing || preview || !ref.current || store.get(element.id)?.key === key) return undefined;
    const controller = new AbortController(), node = ref.current;
    const timeout = setTimeout(() => controller.abort(), 15000);
    const work = measureEditedTemplateContrast(node, { signal: controller.signal }).then(lines => {
      if (!controller.signal.aborted && lines) store.set(element.id, { key, lines });
    }).catch(() => { /* Unavailable pixels retain the element's existing ink, never stale line positions. */ })
      .finally(() => clearTimeout(timeout));
    trackEditedContrast(node, work);
    return () => { clearTimeout(timeout); controller.abort(); };
  }, [store, element.id, key, editing, preview]);
  return key && measured?.key === key ? { ...element, contrastLines: measured.lines,
    contrastFor: { text: element.text, x: element.x, y: element.y, w: element.w, style: element.style } } : element;
}
