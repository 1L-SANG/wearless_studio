import { readFileSync } from 'node:fs';
import { transformWithEsbuild } from 'vite';
import React from 'react';
import { storyboardPositions, storyboardPositionId, isHiddenStoryboardKind, positionSwapReason, storyboardPositionSwapReason } from '../../../src/lib/pageTemplates/storyboardPositions.js';
import { wearUsageForBlock } from '../../../src/lib/pageTemplates/slots.js';
import { automaticSectionInsertionKey } from '../../../src/lib/pageTemplates/sectionPlacement.js';
import { colorDisplayName } from '../../../src/lib/colorwayMatching.js';
import { isTextOnlySection, visibleSectionRuns } from '../../../src/lib/pageTemplates/sectionRuns.js';
import * as signature from '../../../src/lib/signatureCutPool.js';
import * as photoChoices from '../../../src/lib/pageTemplates/storyboardPhotoChoices.js';
import * as gallery from '../../../src/lib/pageTemplates/storyboardGallery.js';
import { assembleStoryboardPreview } from '../../../src/features/storyboard/storyboardPreview.js';
import { stageAdditions } from '../../../src/lib/pageTemplates/stageAdditions.js';
import { pairedPhotoSlot } from '../../../src/lib/pageTemplates/pairedDesigns.js';
import { reconcileSectionDependencies } from '../../../src/lib/pageTemplates/sectionDependencies.js';
import { baseColorOf, resolvePageTokens } from '../../../src/lib/pageTemplates/tokens.js';
const extraBindings = { storyboardPositions, storyboardPositionId, isHiddenStoryboardKind, positionSwapReason, storyboardPositionSwapReason,
  wearUsageForBlock, automaticSectionInsertionKey, colorDisplayName, isTextOnlySection, visibleSectionRuns,
  ...signature, ...gallery, ...photoChoices, assembleStoryboardPreview, stageAdditions, pairedPhotoSlot, reconcileSectionDependencies, baseColorOf, resolvePageTokens,
  StoryboardPaleSlot: 'pale-slot', StoryboardStackPopup: 'stack-popup',
  ProductPresentationPicker: 'product-presentations', PhotoChoiceFilters: 'photo-filters', PhotoFraming: 'photo-framing', PhotoChoicePager: 'photo-pages', ExampleThumb: 'example-thumb' };

export const componentStyles = new Proxy({}, { get: (_target, key) => String(key) });
export const nodes = node => Array.isArray(node) ? node.flatMap(nodes)
  : !node || typeof node !== 'object' ? [] : [node, ...nodes(node.props?.children)];
export const textOf = node => node == null || typeof node === 'boolean' ? '' : Array.isArray(node) ? node.map(textOf).join('')
  : typeof node === 'object' ? textOf(node.props?.children) : String(node);
export const readStoryboard = file => readFileSync(new URL(`../../../src/features/storyboard/${file}`, import.meta.url), 'utf8');
export const readStoryboardSources = () => ['Storyboard.jsx', 'StoryboardPhotoPanel.jsx', 'StoryboardExampleGallery.jsx',
  'StoryboardControls.jsx', 'StoryboardContinue.jsx', 'StoryboardStage.jsx', 'StoryboardPageList.jsx', 'StoryboardDrawer.jsx', 'StoryboardSpaceSetPanel.jsx']
  .map(readStoryboard).join('\n');

// Execute the actual JSX and callbacks with hook storage supplied by the test.
// This never starts a browser, server, fetch, or image request.
export async function loadStoryboardComponent(file, name, bindings = {}) {
  bindings = { ...extraBindings, ...bindings };
  const source = readStoryboard(file).replace(/^import[\s\S]*?from ['"][^'"]+['"];\s*/gm, '')
    .replace(/^export default [^;]+;\s*/gm, '')
    .replace(/^export \{[^}]+\} from [^;]+;\s*/gm, '').replace(/\bexport (?=(function|const))/g, '');
  const { code } = await transformWithEsbuild(source, file, { jsx: 'transform' });
  return new Function('React', 'styles', ...Object.keys(bindings), `${code}; return ${name};`)(React, componentStyles, ...Object.values(bindings));
}

export function hookHarness() {
  let index = 0, effects = [];
  const slots = [], cleanups = [];
  const same = (left, right) => left && right && left.length === right.length && left.every((v, i) => Object.is(v, right[i]));
  const hooks = {
    useState(initial) { const i = index++; if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial;
      return [slots[i], value => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }]; },
    useRef(initial) { const i = index++; return slots[i] ||= { current: initial }; },
    useMemo(fn, deps) { const i = index++; if (!same(slots[i]?.deps, deps)) slots[i] = { value: fn(), deps }; return slots[i].value; },
    useEffect(fn, deps) { const i = index++; if (!same(slots[i], deps)) { slots[i] = deps; effects.push(() => { cleanups[i]?.(); cleanups[i] = fn(); }); } },
  };
  return { hooks, render(component, props, attach) { index = 0; effects = []; const tree = component(props); attach?.(tree); effects.forEach(fn => fn()); return tree; },
    close() { cleanups.forEach(fn => fn?.()); } };
}
