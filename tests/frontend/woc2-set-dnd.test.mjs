import test from 'node:test';
import assert from 'node:assert/strict';
import { addTemplateSection } from '../../src/lib/pageTemplates/sectionActions.js';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { validateStoryboardV4 } from '../../src/lib/pageTemplates/validateStoryboard.js';
import { STORYBOARD_SPACE_SETS, STORYBOARD_SPACE_SET_EXAMPLES, spaceSetIdFromGroupId } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { uniqueGenerationCutCount } from '../../src/lib/generationCutCount.js';
import { nextUnusedSpaceSetMember } from '../../src/lib/storyboardSpaceSets.js';

const product = { clothingType: 'top', colors: [{ id: 'base', isBase: true }] };
const analysis = { targetGenders: ['women'], pageStyle: defaultPageStyle() };
const examples = STORYBOARD_SPACE_SET_EXAMPLES;
const exampleFor = id => examples.find(e => e.id === id);
const addedPhotos = (before, after) => after.filter(b => !before.some(old => old.id === b.id) && b.source === 'ai');

// The break: the ordinary-first branch can select a released member as its
// companion, then discard its set/pose authority before the first save.
test('D204 a manual ordinary example drop gives its published companion singleton membership immediately', () => {
  const set = STORYBOARD_SPACE_SETS.find(s => s.setType === 'styling' && s.gender === 'women'
    && s.applicableClothingTypes.includes('top') && s.placeType === 'park-garden');
  const member = set.members.find(m => m.shot === 'medium');
  const companion = exampleFor(member.exampleId);
  const ordinary = { id: 'ordinary-park-front', cutType: 'styling', shot: 'full', direction: 'front',
    gender: 'women', applicableClothingTypes: ['top'], placeType: 'park-garden', variants: ['all'], thumb: '/ordinary.webp' };
  const before = seedStoryboard(product, analysis);
  const snapshot = JSON.stringify(before);
  const after = addTemplateSection(before, product, analysis, 'wear', 'styling',
    { example: ordinary, gender: 'women', catalog: [ordinary, companion] });
  const added = addedPhotos(before, after);
  assert.equal(added.length, 2);
  assert.deepEqual(added.map(b => b.exampleId), [ordinary.id, companion.id]);
  assert.equal(added[0].spaceGroupId, undefined);
  assert.equal(spaceSetIdFromGroupId(added[1].spaceGroupId), set.id);
  assert.equal(added[1].spaceSetMemberOrder, member.order);
  assert.equal(added[1].refScope, 'pose');
  assert.equal(added[1].setSelectionOrigin, 'user');
  assert.equal(added[1].exampleSelectionOrigin, 'user');
  assert.equal(nextUnusedSpaceSetMember(set, [added[1]]).exampleId, set.members[0].exampleId);
  assert.equal(JSON.stringify(before), snapshot, 'the undo snapshot remains exact');
  validateStoryboardV4(after);
});

test('D204 a published styling drop keeps the selected member first and a coherent same-set pair', () => {
  const set = STORYBOARD_SPACE_SETS.find(s => s.setType === 'styling' && s.gender === 'women'
    && s.applicableClothingTypes.includes('top') && s.members.some(m => m.shot === 'medium'));
  const member = set.members.find(m => m.shot === 'medium');
  const chosen = exampleFor(member.exampleId);
  const before = seedStoryboard(product, analysis);
  const after = addTemplateSection(before, product, analysis, 'wear', 'styling',
    { example: chosen, gender: 'women', catalog: examples });
  const added = addedPhotos(before, after);
  assert.equal(added.length, 2);
  assert.equal(added[0].exampleId, chosen.id);
  assert.equal(added[0].shot, chosen.shot);
  assert.equal(added[0].direction, chosen.direction);
  assert.equal(added[0].spaceGroupId, added[1].spaceGroupId);
  assert.equal(spaceSetIdFromGroupId(added[0].spaceGroupId), set.id);
  assert.ok(added.every(b => b.exampleSelectionOrigin === 'user' && b.setSelectionOrigin === 'user'),
    'a manual drop must remain protected from automatic section shuffle');
  assert.notEqual(added[0].spaceSetMemberOrder, added[1].spaceSetMemberOrder);
  assert.equal(uniqueGenerationCutCount(added), 2);
  validateStoryboardV4(after);
});

// The break: wear/fit currently ignores context.example and generates an
// unrelated seeded rotation instead of the photograph the seller dropped.
test('D204 a complete studio example drop preserves the exact picked photo first and exactly three published inputs', () => {
  const set = STORYBOARD_SPACE_SETS.find(s => s.setType === 'horizon-sequence' && s.gender === 'women'
    && s.applicableClothingTypes.includes('top') && s.members.some(m => m.direction === 'back'));
  const member = set.members.find(m => m.direction === 'back');
  const chosen = exampleFor(member.exampleId);
  const before = seedStoryboard(product, analysis);
  const snapshot = JSON.stringify(before);
  const after = addTemplateSection(before, product, analysis, 'wear', 'fit',
    { example: chosen, gender: 'women', catalog: examples });
  const added = addedPhotos(before, after);
  assert.equal(added.length, 3);
  assert.equal(added[0].exampleId, chosen.id);
  assert.equal(added[0].shot, chosen.shot);
  assert.equal(added[0].direction, chosen.direction);
  for (const b of added) {
    const actual = exampleFor(b.exampleId);
    assert.ok(actual, 'every input is a real published example');
    assert.equal(b.cutType, actual.cutType);
    assert.equal(b.shot, actual.shot);
    assert.equal(b.direction, actual.direction);
    assert.equal(spaceSetIdFromGroupId(b.spaceGroupId), actual.spaceSetId);
    assert.equal(b.refScope, 'all');
    assert.equal(b.horizonBackgroundMode, 'reference');
  }
  assert.equal(new Set(added.map(b => b.spaceGroupId)).size, 3);
  assert.equal(uniqueGenerationCutCount(added), 3);
  assert.equal(JSON.stringify(before), snapshot);
  validateStoryboardV4(after);
});

const { spawnSync } = await import('node:child_process');
const { patchStoryboardPhoto } = await import('../../src/lib/pageTemplates/photoActions.js');
const { generationExampleSelectionPatch } = await import('../../src/lib/storyboardExampleSelection.js');
const { createSpaceSetMembers, rekeySeparatedSpaceRuns } = await import('../../src/lib/storyboardSpaceSets.js');
const { spaceSetGroupId } = await import('../../src/lib/storyboardSpaceSetCatalog.js');
const { createSimilarCutBlock } = await import('../../src/lib/storyboardSimilarCuts.js');

test('D204 manual drops, same-set replacement, split rekey and similar followers survive the real API validators', () => {
  const sets = STORYBOARD_SPACE_SETS.filter(s => s.setType === 'styling' && s.gender === 'women'
    && s.applicableClothingTypes.includes('top'));
  const before = seedStoryboard(product, analysis);
  const oldSnapshot = JSON.stringify(before);
  const selected = exampleFor(sets[0].members[1].exampleId);
  const styling = addTemplateSection(before, product, analysis, 'wear', 'styling',
    { example: selected, gender: 'women', catalog: examples });
  const studioSet = STORYBOARD_SPACE_SETS.find(s => s.setType === 'horizon-sequence' && s.gender === 'women'
    && s.applicableClothingTypes.includes('top'));
  const studioExample = exampleFor(studioSet.members.find(m => m.direction === 'back').exampleId);
  const studio = addTemplateSection(before, product, analysis, 'wear', 'fit',
    { example: studioExample, gender: 'women', catalog: examples });
  const host = before.find(b => b.pageSectionUsage === 'styling');
  const group = spaceSetGroupId(sets[0].id, 'api-original');
  const members = createSpaceSetMembers(sets[0], host, { spaceGroupId: group,
    makeId: (_member, index) => `api-source-${index}` });
  const originalRun = [members[0], members[1], createSimilarCutBlock(members[1], 'api-similar'), members[2]];
  const full = before.flatMap(b => b.id === host.id ? originalRun : b.pageSectionKey === host.pageSectionKey ? [] : [b]);
  const snapshot = JSON.stringify(full);
  const same = patchStoryboardPhoto(full, members[1].id,
    generationExampleSelectionPatch(members[1], exampleFor(sets[0].members[0].exampleId)).patch, product);
  assert.equal(same.find(b => b.id === members[1].id).spaceGroupId, group);
  assert.equal(same.find(b => b.id === 'api-similar').exampleId, sets[0].members[0].exampleId);
  assert.equal(nextUnusedSpaceSetMember(sets[0], same.filter(b => b.spaceGroupId === group)).exampleId,
    sets[0].members[1].exampleId);
  const different = patchStoryboardPhoto(full, members[1].id,
    generationExampleSelectionPatch(members[1], exampleFor(sets[1].members[1].exampleId)).patch, product);
  const split = rekeySeparatedSpaceRuns(different, id => spaceSetGroupId(id, 'api-tail'));
  assert.notEqual(split.find(b => b.id === members[2].id).spaceGroupId, group);
  assert.equal(split.find(b => b.id === 'api-similar').spaceGroupId,
    split.find(b => b.id === members[1].id).spaceGroupId);
  assert.equal(uniqueGenerationCutCount(same), uniqueGenerationCutCount(full));
  assert.equal(uniqueGenerationCutCount(split), uniqueGenerationCutCount(full));
  assert.equal(JSON.stringify(full), snapshot);
  assert.equal(JSON.stringify(before), oldSnapshot);
  const flows = [styling, studio, same, split];
  flows.forEach(validateStoryboardV4);
  const result = spawnSync('server/.venv/bin/python', ['-c', `
import json,sys
sys.path.insert(0,'server')
from app.agents import content_roles, cut_generator, space_set_assets, confirmed_gpt_runtime
out=[]
for blocks in json.load(sys.stdin):
 content_roles.validate_storyboard_v4(blocks)
 normalized=content_roles.canonicalize_storyboard(blocks)
 space_set_assets.bind_storyboard_space_sets(normalized, clothing_type='top', gender='women')
 rows=[]
 for b in normalized:
  if b.get('spaceGroupId'):
   spec=cut_generator.normalize_spec(b, clothing_type='top')
   rows.append(dict(id=b['id'],exampleId=b['exampleId'],spaceGroupId=b['spaceGroupId'],shot=spec['shot'],direction=spec['direction'],scope=spec['refScope'],confirmed=confirmed_gpt_runtime.profile_requested(spec)))
 out.append(rows)
print(json.dumps(out))
`], { input: JSON.stringify(flows), encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  const summaries = JSON.parse(result.stdout);
  assert.equal(summaries.length, 4);
  summaries.forEach((rows, index) => rows.forEach(row => {
    const original = flows[index].find(b => b.id === row.id);
    assert.equal(row.exampleId, original.exampleId);
    assert.equal(row.spaceGroupId, original.spaceGroupId);
    assert.equal(row.shot, original.shot);
    assert.equal(row.direction, original.direction);
    assert.equal(row.scope, original.cutType === 'styling' ? 'pose' : 'all');
    assert.equal(row.confirmed, false);
  }));
});
