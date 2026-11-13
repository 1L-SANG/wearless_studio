import { readFileSync } from 'node:fs';
import { createEditorContrastStore, EditorContrastContext } from '../../../src/features/editor/editorContrastStore.js';
import { trackEditedContrast } from '../../../src/features/editor/editorContrastPending.js';

export function loadEditedContrastHook(harness, measure, store = null) {
  const source = readFileSync('src/features/editor/useEditedTemplateContrast.js', 'utf8')
    .replace(/^import .*;\n/gm, '').replace('export function', 'function');
  const bindings = {
    useContext: () => store, useLayoutEffect: harness.hooks.useEffect, useState: harness.hooks.useState,
    useSyncExternalStore(subscribe, getSnapshot) {
      const [, update] = harness.hooks.useState(0);
      harness.hooks.useEffect(() => subscribe(() => update(value => value + 1)), [subscribe]);
      return getSnapshot();
    },
    createEditorContrastStore, EditorContrastContext, measureEditedTemplateContrast: measure, trackEditedContrast,
  };
  return new Function(...Object.keys(bindings), `${source}; return useEditedTemplateContrast;`)(...Object.values(bindings));
}
