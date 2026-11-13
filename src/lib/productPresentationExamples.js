import manifest from '../data/productPresentationExamples.json' with { type: 'json' };

// Display-only examples. Never copy these URLs into generation or stored recipes.
export function productPresentationExample(clothingType, mode, direction, available = manifest.images) {
  if (!['top', 'bottom', 'outer', 'dress'].includes(clothingType) || !['front', 'back'].includes(direction)) return null;
  const own = available[`${clothingType}_${mode}_${direction}`];
  if (own) return { src: own, face: direction, fallback: false };
  const front = direction === 'back' && available[`${clothingType}_${mode}_front`];
  return front ? { src: front, face: 'front', fallback: true } : null;
}
