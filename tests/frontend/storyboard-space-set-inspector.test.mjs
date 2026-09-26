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

test('a selected set member uses a compact cut header instead of the large set card', () => {
  assert.doesNotMatch(spaceSetHeaderSource, /<SpaceSetCard/);
  assert.match(spaceSetHeaderSource, /sb-space-current-thumb/);
  assert.match(spaceSetHeaderSource, /세트 설정/);
  assert.match(spaceSetHeaderSource, /세트 공통/);
  assert.doesNotMatch(inspectorSource, /<HorizonBackgroundControl/);
  assert.match(inspectorSource, /label className="lbl">방향/);
  assert.match(inspectorSource, /label className="lbl">대상 색상/);
});

test('set gallery guide says what a set click does and that a single cut can be dragged in', () => {
  const gallerySource = storyboardSource.slice(storyboardSource.indexOf('function SpaceSetGallery('));
  // 세트 변경 화면에서 누르면 교체되고, 추가 화면에서 누르면 추가된다. 한 컷은 미리보기에서 끌어 온다.
  assert.match(gallerySource, /replacing \? '세트를 눌러서 바꾸거나 하나의 컷만 드래그해서 추가해보세요\.' : '세트를 눌러서 추가하거나 하나의 컷만 드래그해서 추가해보세요\.'/);
});
