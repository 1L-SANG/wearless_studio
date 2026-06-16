import { lazy } from 'react';

let editorModulePromise;

export function preloadEditor() {
  editorModulePromise ||= import('@/features/editor/Editor.jsx');
  return editorModulePromise;
}

export const LazyEditor = lazy(preloadEditor);
