import assert from 'node:assert/strict';
import test from 'node:test';

import { STORYBOARD_SPACE_SETS } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { mappedSpaceSetCount, spaceSetDisplayName } from '../../src/lib/spaceSetDisplayNames.js';

test('every released shooting set has a code-free Korean display name', () => {
  // Preparation-only sets also have reviewed names; every published set still needs coverage.
  assert.ok(mappedSpaceSetCount() >= STORYBOARD_SPACE_SETS.length);
  for (const set of STORYBOARD_SPACE_SETS) {
    const displayName = spaceSetDisplayName(set);
    assert.match(displayName, /[가-힣]/, set.id);
    assert.doesNotMatch(displayName, /[A-Za-z0-9_]/, set.id);
    // A newly published catalog may already contain the same approved short name.
    // Internal catalog text must still never override the reviewed display mapping.
    assert.equal(spaceSetDisplayName({ ...set, name: 'INTERNAL_123_PASS' }), displayName);
    // Horizon names describe shooting; background and lighting have their own rows.
    const maxLength = set.setType.startsWith('horizon') ? 26 : 11;
    assert.ok([...displayName].length <= maxLength, `${set.id}: ${displayName}`);
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
  assert.equal(spaceSetDisplayName({ id: 'unknown' }), '실내 장소 세트');
});
