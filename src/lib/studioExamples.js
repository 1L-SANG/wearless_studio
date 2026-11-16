import examples from '../data/genExamples.json' with { type: 'json' };
import spaceSets from '../data/storyboardSpaceSets.json' with { type: 'json' };

const RELEASE_EXAMPLES = new Map([
  ...examples.map(example => [example.id, example]),
  ...spaceSets.sets.flatMap(set => set.members.map(member => [member.exampleId, member])),
]);

export const isMinimalExample = example => example?.cutType === 'styling' && example.studioStyle === 'minimal';

export function isMinimalExampleId(exampleId, catalog = null) {
  return isMinimalExample(catalog ? catalog.find(example => example.id === exampleId) : RELEASE_EXAMPLES.get(exampleId));
}

// A set's display category is derived from every member, never from its name or place type.
export function studioStyleForSet(members) {
  const minimal = (members || []).filter(isMinimalExample).length;
  if (minimal && minimal !== members.length) throw new Error('mixed_studio_style_set');
  return minimal ? 'minimal' : null;
}
