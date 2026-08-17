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
const spaceSetCardSource = storyboardSource.slice(
  storyboardSource.indexOf('function SpaceSetCard('),
  storyboardSource.indexOf('function SpaceSetInspectorHeader('),
);
const spaceSetHeaderSource = storyboardSource.slice(
  storyboardSource.indexOf('function SpaceSetInspectorHeader('),
  storyboardSource.indexOf('function SpaceSetGallery('),
);

test('selecting a space-set block omits the generation gallery, shot tabs, and my-photo tab', () => {
  assert.match(storyboardSource, /shouldRenderGenerationExampleGuide\(block\) \{\s*return !block\?\.spaceGroupId;/);
  // 갤러리 게이트는 두 곳(대기 레시피 / 일반). 시그니처 슬롯은 전용 갤러리를 쓰므로
  // 일반 쪽 게이트에 !isSignatureSlot 이 붙는다 — 두 게이트 모두 여전히 존재해야 한다.
  assert.equal((inspectorSource.match(/\{shouldRenderGenerationExamples && /g) || []).length, 2);
  assert.match(inspectorSource, /\{shouldRenderGenerationExamples && !isSignatureSlot && \(/);
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

test('selecting a space-set block renders the shared card with its display name', () => {
  const set = {
    id: 'set-style-women-dress-cafe-garden-attrangs-160544-root03',
    placeType: 'cafe-shop-interior',
  };
  assert.equal(spaceSetDisplayName(set), '볕 드는 카페 정원');
  assert.match(spaceSetHeaderSource, /<SpaceSetCard set=\{set\} interactive=\{false\} currentCutOrdinal=\{ordinal\} \/>/);
  assert.match(spaceSetCardSource, /<strong>\{spaceSetDisplayName\(set\)\}<\/strong>/);
  assert.doesNotMatch(spaceSetCardSource, /<strong>\{set\.(?:id|name)\}/);
});

test('the current space-set card shows which member cut is selected', () => {
  assert.match(spaceSetCardSource, /현재 선택 · \{currentCutOrdinal\}번째 컷/);
  assert.match(spaceSetHeaderSource, /currentCutOrdinal=\{ordinal\}/);
});

test('the inspector space-set card and its thumbnails have no replacement click handler', () => {
  const staticVariantSource = spaceSetCardSource.slice(
    spaceSetCardSource.indexOf('if (!interactive)'),
    spaceSetCardSource.indexOf('return (', spaceSetCardSource.indexOf('if (!interactive)')),
  );
  const thumbnailSource = spaceSetCardSource.slice(
    spaceSetCardSource.indexOf('<span className="sb-set-polaroids"'),
    spaceSetCardSource.indexOf('<strong>{spaceSetDisplayName(set)}</strong>'),
  );
  assert.match(staticVariantSource, /return <div className=\{className\}>\{content\}<\/div>/);
  assert.doesNotMatch(staticVariantSource, /onClick|onKeyDown/);
  assert.doesNotMatch(thumbnailSource, /onClick|onKeyDown/);
});
