import test from 'node:test';
import assert from 'node:assert/strict';
import changes from '../fixtures/verified-example-tags-20261106.json' with { type: 'json' };
import examples from '../../src/data/genExamples.json' with { type: 'json' };
import { selectGenerationExamples } from '../../src/lib/generationExamples.js';
import { STORYBOARD_SPACE_SETS, STORYBOARD_SPACE_SET_EXAMPLES } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { createSpaceSetMembers } from '../../src/lib/storyboardSpaceSets.js';

const restrictions = changes.filter(change => change.excludeClothingTypes);
const correctedMembers = changes.filter(change => !change.excludeClothingTypes);

test('pocket poses cannot be selected for the excluded garments and remain available for tops', () => {
  const select = (example, clothingType) => selectGenerationExamples(examples, {
    cutType: 'horizon', shot: example.shot, gender: 'women', clothingType, limit: examples.length,
  }).some(item => item.id === example.id);
  for (const change of restrictions) {
    const example = examples.find(item => item.id === change.id);
    assert.ok(example, change.label);
    for (const clothing of change.excludeClothingTypes) assert.equal(select(example, clothing), false, `${change.label}/${clothing}`);
    for (const clothing of ['top', 'outer']) assert.equal(select(example, clothing), true, `${change.label}/${clothing}`);
  }
});

test('verified directions and crops reach both gallery examples and new storyboard blocks', () => {
  for (const change of correctedMembers) {
    const group = STORYBOARD_SPACE_SETS.find(set => set.members.some(member => member.exampleId === change.id));
    assert.ok(group, `set was lost during normalization: ${change.label}`);
    const blocks = createSpaceSetMembers(group, { sectionRole: group.setType === 'styling' ? 'styling' : 'studio' }, {
      spaceGroupId: `ssg1__${group.id}__verified`,
    });
    const block = blocks.find(item => item.exampleId === change.id);
    const galleryExample = STORYBOARD_SPACE_SET_EXAMPLES.find(item => item.id === change.id);
    for (const field of ['direction', 'shot']) {
      if (change[field]) {
        assert.equal(block[field], change[field], `${change.label}/block/${field}`);
        assert.equal(galleryExample[field], change[field], `${change.label}/gallery/${field}`);
      }
    }
  }
});
