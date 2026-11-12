import { useLayoutEffect, useState } from 'react';
import { measureEditedTemplateContrast } from './editedTemplateContrast.js';
import { trackEditedContrast } from './editorContrastPending.js';

export function useEditedTemplateContrast(element, ref, { editing, preview }) {
  const [measured, setMeasured] = useState(null);
  const needsMeasure = element.type === 'text' && element.keepWords === false && Boolean(element.contrastFor)
    && element.style?.color === element.contrastFor.style?.color;
  const key = needsMeasure ? JSON.stringify([element.text, element.x, element.y, element.w, element.style]) : null;
  useLayoutEffect(() => {
    if (!key || editing || preview || !ref.current) return undefined;
    const controller = new AbortController(), node = ref.current;
    const timeout = setTimeout(() => controller.abort(), 15000);
    const work = measureEditedTemplateContrast(node, { signal: controller.signal }).then(lines => {
      if (!controller.signal.aborted && lines) setMeasured({ key, lines });
    }).catch(() => { /* Unavailable pixels retain the element's existing ink, never stale line positions. */ })
      .finally(() => clearTimeout(timeout));
    trackEditedContrast(node, work);
    return () => { clearTimeout(timeout); controller.abort(); };
  }, [key, editing, preview]);
  return key && measured?.key === key ? { ...element, contrastLines: measured.lines,
    contrastFor: { text: element.text, x: element.x, y: element.y, w: element.w, style: element.style } } : element;
}
