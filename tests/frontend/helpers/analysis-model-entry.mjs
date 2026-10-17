import { readFileSync } from 'node:fs';
import { outfitHarness, nodes as allNodes } from './outfitHarness.mjs';
import { extensionModelFee } from '../../../src/lib/limits.js';
import { outfitModelCredit } from '../../../src/lib/outfitPresentation.js';
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
  const h = await outfitHarness();
  const priorRaf = globalThis.requestAnimationFrame;
  const priorCancel = globalThis.cancelAnimationFrame;
  globalThis.requestAnimationFrame = () => 0;
  globalThis.cancelAnimationFrame = () => {};
  try {
    const { OutfitSelection } = await h.load('/src/features/analysis/OutfitSelection.jsx');
    const { OutfitTile } = await h.load('/src/features/analysis/OutfitCatalog.jsx');
    const changes = [];
    const props = { analysis: { ...a, clothingType: 'dress' },
      models: AI_MODELS.filter(model => model.gender === a.targetGenders[0]),
      modelCredit: model => model.tier === 'basic' ? '무료' : outfitModelCredit(extensionModelFee('free', model.id)),
      onChange: patch => changes.push(patch) };
    let tree = h.render(OutfitSelection, props);
    allNodes(tree).find(node => node.props['aria-controls'] === 'outfit-panel-model').props.onClick();
    tree = h.render(OutfitSelection, props);
    const tiles = allNodes(tree).filter(node => node.type === OutfitTile);
    const more = allNodes(tree).find(node => node.type?.name === 'OutfitMore');
    return { nodes: tiles.flatMap(node => allNodes(OutfitTile(node.props))), changes, allItems: more.props.items };
  } finally {
    await h.close(); globalThis.requestAnimationFrame = priorRaf; globalThis.cancelAnimationFrame = priorCancel;
  }
}
