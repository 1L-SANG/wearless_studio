import test from 'node:test';
import assert from 'node:assert/strict';
import profiles from '../../data/horizonSetProfiles.json' with { type: 'json' };
import { STORYBOARD_SPACE_SETS } from '../../src/lib/storyboardSpaceSetCatalog.js';
import {
  spaceSetDisplayName, spaceSetDescription, spaceSetBackgroundHint,
  spaceSetBackgroundLabel, spaceSetLightingLabel, spaceSetSupportedBackgroundModes,
} from '../../src/lib/spaceSetDisplayNames.js';

test('19 active horizon profiles plus the saved historical linen profile retain separate shooting and background labels', () => {
  assert.equal(profiles.schemaVersion, 2);
  assert.equal(Object.keys(profiles.sets).length, 20);
  const active = STORYBOARD_SPACE_SETS.filter(set => set.setType.startsWith('horizon'));
  assert.equal(active.length, 19);
  assert.ok(active.every(set => profiles.sets[set.id]));
  for (const [id, profile] of Object.entries(profiles.sets)) {
    const set = { id };
    assert.equal(spaceSetDisplayName(set), profile.shortName);
    assert.ok(profile.shortName.length <= 7);
    assert.equal(spaceSetDescription(set), profile.description);
    assert.equal(spaceSetBackgroundLabel(set), profile.backgroundLabel);
    assert.equal(spaceSetLightingLabel(set), profile.lightingLabel);
    assert.equal(spaceSetBackgroundHint(set), profile.backgroundDescription);
    assert.doesNotMatch(profile.title, /배경|그림자|회색|흰|조명|의류색|린넨|니트|스카프/);
    assert.doesNotMatch(profile.description, /배경|그림자|회색|흰 벽|조명/);
    assert.ok(profile.description.length > 15);
    assert.ok(profile.backgroundLabel && profile.lightingLabel && profile.backgroundDescription);
  }
});

test('every category supports both explicit background modes without a catalog-level active choice', () => {
  for (const id of Object.keys(profiles.sets)) {
    assert.deepEqual(spaceSetSupportedBackgroundModes({ id }), ['reference', 'garment-tone']);
    assert.equal(Object.hasOwn(profiles.sets[id], 'backgroundMode'), false);
  }
  assert.equal(spaceSetBackgroundHint({ id: 'set-unknown' }), '');
  assert.equal(spaceSetBackgroundLabel({ id: 'set-unknown' }), '');
  assert.equal(spaceSetLightingLabel({ id: 'set-unknown' }), '');
  assert.deepEqual(spaceSetSupportedBackgroundModes({ id: 'set-unknown' }), []);
});
