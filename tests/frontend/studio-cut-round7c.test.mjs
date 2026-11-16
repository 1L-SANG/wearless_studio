import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { packStageRows } from '../../src/lib/pageTemplates/storyboardStage.js';

const set = (n, groupId = 'ssg1__set__a') => Array.from({ length: n }, (_, i) => ({ id: `b${i}`, source: 'ai', colorId: 'c1', spaceGroupId: groupId }));

test('a set spanning two rows marks only its final chunk as last', () => {
  const rows = packStageRows(set(6), { add: false });
  const chunks = rows.flat().filter(group => group.kind === 'space');
  assert.deepEqual(chunks.map(chunk => [chunk.blocks.length, chunk.continued, chunk.last]), [[4, false, false], [2, true, true]]);
});

test('a one-row set keeps its change button chunk', () => {
  const chunks = packStageRows(set(3), { add: false }).flat().filter(group => group.kind === 'space');
  assert.deepEqual(chunks.map(chunk => [chunk.continued, chunk.last]), [[false, true]]);
});

test('stage renders the set change button only on the last chunk while keeping the set name on every row', () => {
  const source = readFileSync(new URL('../../src/features/storyboard/StoryboardStage.jsx', import.meta.url), 'utf8');
  assert.match(source, /\{group\.last !== false && <button type="button" className=\{`\$\{styles\.pill\}/);
  assert.match(source, /<strong>\{spaceSetDisplayName\(inferStoryboardSpaceSet\(group\.blocks\[0\]\.spaceGroupId\)\)\}<\/strong>/);
});
