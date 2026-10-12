import { readFileSync } from 'node:fs';
import React from 'react';
import { transformWithEsbuild } from 'vite';
import { AI_MODELS } from '../../../src/features/analysis/aiModels.js';
import { isRealModelSelection, resolveSelectedModelId } from '../../../src/features/analysis/modelSelection.js';

const source = readFileSync(new URL('../../../src/features/analysis/AnalysisForm.jsx', import.meta.url), 'utf8');

// Execute the production selection effect, retaining refs across effect replays.
export function selectionEffect(a, onChange, toast) {
  const form = source.slice(source.indexOf('export function AnalysisForm'));
  const effect = [...form.matchAll(/  useEffect\(\(\) => \{[\s\S]*?\n  \}, \[[^\n]*\]\);/g)]
    .find(([code]) => code.includes('resolveSelectedModelId('))?.[0];
  if (!effect) throw new Error('AnalysisForm selection effect not found');
  const refs = [...form.matchAll(/^  const \w+Ref = useRef\((?:false|null)\);$/gm)].map(([code]) => code).join('\n');
  const visible = form.match(/  const visibleAiModels = AI_MODELS\.filter\([\s\S]*?\n  \);/)[0];
  let run;
  const bindings = {
    a, AI_MODELS, isRealModelSelection, resolveSelectedModelId, onChange, toast,
    models: [{ id: a.selectedModelId, hasActiveLicense: true }], modelsLoading: false,
    useRef: (current) => ({ current }), useEffect: (fn) => { run = fn; },
  };
  new Function(...Object.keys(bindings), `${refs}\n${visible}\n${effect}`)(...Object.values(bindings));
  return () => run();
}

// Render the actual model section without starting a browser or a dev server.
export async function modelSection(a) {
  const start = source.indexOf('      {/* 5. model select');
  const section = source.slice(start, source.indexOf('      {/* 6. match clothing', start));
  const { code } = await transformWithEsbuild(`function section() { return (<>${section}</>); }`, 'model-entry.jsx', { jsx: 'transform' });
  const changes = [];
  const bindings = {
    React, a, isRealModelSelection, visibleAiModels: AI_MODELS.filter((m) => !a.targetGenders?.[0] || m.gender === a.targetGenders[0]),
    quotePlan: 'free', extensionModelGroupLabel: () => '확장', onChange: (patch) => changes.push(patch),
    Chips: 'chips', Icon: 'icon', modelTab: 'ai', setModelTab() {}, detailFor: null,
  };
  const tree = new Function(...Object.keys(bindings), `${code}; return section();`)(...Object.values(bindings));
  return { nodes: flatten(tree), changes };
}

function flatten(node) {
  if (Array.isArray(node)) return node.flatMap(flatten);
  if (!node || typeof node !== 'object') return [];
  return [node, ...flatten(node.props?.children)];
}
