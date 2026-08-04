import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { spaceSetDisplayName } from '../../src/lib/spaceSetDisplayNames.js';

const storyboardSource = readFileSync(
  new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url),
  'utf8',
);
const inspectorSource = storyboardSource.slice(
  storyboardSource.indexOf('function Inspector('),
  storyboardSource.indexOf('export function Storyboard()'),
);
const spaceSetHeaderSource = storyboardSource.slice(
  storyboardSource.indexOf('function SpaceSetInspectorHeader('),
  storyboardSource.indexOf('function SpaceSetGallery('),
);

test('selecting a space-set block omits the generation gallery, shot tabs, and my-photo tab', () => {
  assert.match(storyboardSource, /shouldRenderGenerationExampleGuide\(block\) \{\s*return !block\?\.spaceGroupId;/);
  assert.equal((inspectorSource.match(/\{shouldRenderGenerationExamples && \(/g) || []).length, 2);
  assert.match(inspectorSource, /isMine && !block\.spaceGroupId/);
  assert.match(storyboardSource, /aria-label="생성예시 갤러리"/);
  assert.match(storyboardSource, /<ShotSegment/);
  assert.match(storyboardSource, /MINE_SHOT_OPTION/);
});

test('selecting a regular block keeps the generation gallery, shot tabs, and my-photo tab', () => {
  assert.match(storyboardSource, /shouldRenderGenerationExampleGuide\(block\) \{\s*return !block\?\.spaceGroupId;/);
  assert.match(inspectorSource, /\{shouldRenderGenerationExamples && \(\s*<MoodGuide/);
  assert.match(storyboardSource, /MINE_SHOT_OPTION = Object\.freeze\(\{ value: 'mine', label: '내 이미지' \}\)/);
});

test('the space-set inspector header uses the display name rather than an internal set code', () => {
  const set = {
    id: 'set-style-women-dress-cafe-garden-attrangs-160544-root03',
    placeType: 'cafe-shop-interior',
  };
  assert.equal(spaceSetDisplayName(set), '볕 드는 카페 정원');
  assert.match(spaceSetHeaderSource, /spaceSetDisplayName\(set\)\} · \{ordinal\}번째 컷/);
  assert.doesNotMatch(spaceSetHeaderSource, /set\.id|spaceGroupId/);
});
