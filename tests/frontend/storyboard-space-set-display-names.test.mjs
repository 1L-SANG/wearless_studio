import assert from 'node:assert/strict';
import test from 'node:test';

import { STORYBOARD_SPACE_SETS } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { mappedSpaceSetCount, spaceSetDisplayName } from '../../src/lib/spaceSetDisplayNames.js';

test('every released shooting set has a code-free Korean display name', () => {
  assert.equal(mappedSpaceSetCount(), STORYBOARD_SPACE_SETS.length);
  for (const set of STORYBOARD_SPACE_SETS) {
    const displayName = spaceSetDisplayName(set);
    assert.match(displayName, /[가-힣]/, set.id);
    assert.doesNotMatch(displayName, /[A-Za-z0-9_]/, set.id);
    assert.notEqual(displayName, set.name, `raw catalog name leaked for ${set.id}`);
  }
});

test('an unmapped set falls back by place without exposing its id or internal name', () => {
  const unknown = {
    id: '06-cream-cheese-26667',
    name: '06 cream cheese 26667 PASS',
    placeType: 'urban-building-exterior',
    setType: 'styling',
  };
  assert.equal(spaceSetDisplayName(unknown), '도시적인 건물 외벽');
  assert.doesNotMatch(spaceSetDisplayName(unknown), /cream|26667|PASS/i);
  assert.equal(spaceSetDisplayName({ id: 'unknown' }), '실내 촬영 세트');
});
