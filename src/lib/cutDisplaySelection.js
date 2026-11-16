import { cutTypeDisplay } from './storyboardTaxonomy.js';
import { selectGenerationExamples } from './generationExamples.js';
import { filterExamplesForModel } from './identityScope.js';

export function cutDisplayCandidates(catalog, { cutType, studioStyle, shots, clothingType, gender, identityKind, direction, sideStyle }) {
  return filterExamplesForModel(shots.flatMap(shot => selectGenerationExamples(catalog, {
    cutType, studioStyle, shot, clothingType, gender, direction, sideStyle,
    includeSetOnly: cutType !== 'product', limit: catalog?.length || 0,
  })), identityKind);
}

export function studioStyleOptions(catalog, context) {
  return cutTypeDisplay('horizon').studioOptions.map(option => ({
    ...option,
    disabled: !cutDisplayCandidates(catalog, { ...context, studioStyle: option.value,
      cutType: option.value === 'minimal' ? 'styling' : 'horizon' }).length,
    disabledReason: '이 조건은 아직 서비스에 공개되지 않았어요',
  }));
}

// No display state is persisted. Minimal must acquire an eligible example in the same update.
export function cutDisplaySelection(block, { value, studioStyle = 'horizon' }, { catalog, preferredShot, ...context }) {
  const minimal = value === 'horizon' && studioStyle === 'minimal';
  const cutType = minimal ? 'styling' : value;
  const candidates = cutDisplayCandidates(catalog, { ...context, cutType, studioStyle: minimal ? 'minimal' : null });
  const preferred = preferredShot || block.shot;
  const shot = candidates.some(example => example.shot === preferred) ? preferred
    : candidates[0]?.shot || context.shots[0];
  const previous = candidates.find(example => example.id === block.exampleId);
  const selected = previous || (minimal ? candidates.find(example => example.shot === shot) : null);
  if (minimal && !selected) return null;
  return { cutType, shot: selected?.shot || shot, exampleId: selected?.id || null };
}
