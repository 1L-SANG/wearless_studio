import { createContext } from 'react';

// Each mounted editor owns this cache. Nothing here is written to saved blocks.
export const EditorContrastContext = createContext(null);

export function createEditorContrastStore() {
  const entries = new Map(), listeners = new Set();
  return {
    get: id => entries.get(id) ?? null,
    set(id, entry) {
      entries.set(id, entry);
      listeners.forEach(listener => listener());
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
