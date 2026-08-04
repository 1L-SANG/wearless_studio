import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';

import { generationExampleSelectionPatch } from '../../src/lib/storyboardExampleSelection.js';

const storyboardSource = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
const featureStyles = readFileSync(new URL('../../src/styles/features.css', import.meta.url), 'utf8');

test('replacing a generation example resets per-cut settings but preserves its structural recipe', () => {
  const block = {
    id: 'cut-1', sectionRole: 'fit', contentRole: 'coordination',
    cutType: 'styling', shot: 'medium', direction: 'back', colorId: 'red',
    exampleId: 'old', refScope: 'all', pose: 'walk', poseLabel: '걷기', angle: 'high',
    matchIds: ['pants'], refImages: ['mine.png'], refAssetIds: ['asset-1'],
    faceExposure: 'hide', outerClosureState: 'closed',
  };
  const result = generationExampleSelectionPatch(block, { id: 'new', direction: 'front' }, {
    clothingType: 'outer', defaultColorId: 'base', refScope: 'pose',
  });

  assert.equal(result.settingsReset, true);
  assert.deepEqual({
    sectionRole: block.sectionRole,
    contentRole: block.contentRole,
    cutType: block.cutType,
    shot: block.shot,
    ...result.patch,
  }, {
    sectionRole: 'fit', contentRole: 'coordination', cutType: 'styling', shot: 'medium',
    exampleId: 'new', exampleSelectionOrigin: 'user', refScope: 'pose',
    direction: 'front', colorId: 'base', colorIds: [], pose: 'auto', poseLabel: 'AI 자동',
    angle: 'same', matchIds: [], refImages: [], refAssetIds: [], faceExposure: 'same',
    outerClosureState: 'open',
  });
});

test('the mock API runtime migrates an HMR-stale three-member styling seed on read', async (t) => {
  const vite = await createServer({
    configFile: false,
    plugins: [react()],
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true }, appType: 'custom', logLevel: 'silent',
  });
  t.after(() => vite.close());
  const { DB, reseedDraft } = await vite.ssrLoadModule('/src/mock/db.js');
  const { api } = await vite.ssrLoadModule('/src/mock/api.js');
  const { inferStoryboardSpaceSet } = await vite.ssrLoadModule('/src/lib/storyboardSpaceSetCatalog.js');

  const initialGroupId = DB.storyboard.find((block) => block.cutType === 'styling' && block.spaceGroupId).spaceGroupId;
  const run = DB.storyboard.filter((block) => block.spaceGroupId === initialGroupId);
  assert.equal(run.length, 2, 'fresh mock runtime seed must already use two entry members');
  const set = inferStoryboardSpaceSet(initialGroupId);
  const omitted = set.members.find((member) => !run.some((block) => block.spaceSetMemberOrder === member.order));
  assert.ok(omitted, 'fixture needs the legacy third member');
  const lastIndex = DB.storyboard.findLastIndex((block) => block.spaceGroupId === initialGroupId);
  DB.storyboard.splice(lastIndex + 1, 0, {
    ...run[0], id: 'legacy-third', shot: omitted.shot, direction: omitted.direction,
    exampleId: omitted.exampleId, thumb: omitted.thumb, spaceSetMemberOrder: omitted.order,
  });

  const loaded = await api.getStoryboard(DB.project.id);
  const migrated = loaded.filter((block) => block.spaceGroupId === initialGroupId);
  assert.equal(migrated.length, 2);
  assert.deepEqual(new Set(migrated.map((block) => block.shot)), new Set(['full', 'medium']));
  reseedDraft();
});

test('example clicks apply id and scope together instead of overwriting from a stale board snapshot', () => {
  const pickHandler = storyboardSource.slice(
    storyboardSource.indexOf('const pick = (scope)'),
    storyboardSource.indexOf('const defaultScope'),
  );
  assert.match(pickHandler, /onExampleChange\(example\.id, scope\)/);
  const activeGuide = storyboardSource.slice(
    storyboardSource.indexOf('<MoodGuide onUseMine='),
    storyboardSource.indexOf('{\/\* 방향'),
  );
  assert.doesNotMatch(activeGuide, /onRefScopeChange=/);
});

test('the storyboard undo window groups every active change, pauses on hover, and exposes its count', () => {
  assert.match(storyboardSource, /UNDO_WINDOW_MS = 10_000/);
  assert.match(storyboardSource, /const before = active \? active\.before : \[\.\.\.previous\]/);
  assert.match(storyboardSource, /operationCount = \(active\?\.operationCount \|\| 0\) \+ 1/);
  assert.match(storyboardSource, /onMouseEnter=/);
  assert.match(storyboardSource, /\{undoEntry\.operationCount\}건 되돌리기/);
});

test('owner cleanup keeps one tray dissolve action and removes obsolete inspector copy and stack counts', () => {
  assert.equal((storyboardSource.match(/세트 전체 풀기/g) || []).length, 1);
  assert.doesNotMatch(storyboardSource, /새 컷의 예시를 먼저 골라주세요/);
  assert.doesNotMatch(storyboardSource, /sb-stack-count/);
  assert.match(storyboardSource, /cutRangeLabel\(group\.items\)/);
});

test('gallery arrows use reserved lanes and selected rings animate only compositor opacity', () => {
  assert.match(featureStyles, /\.sb-exgallery \{[^}]*padding-inline: 38px/);
  assert.match(featureStyles, /\.sb-expage-hit\.next \{ right: 0/);
  assert.doesNotMatch(featureStyles, /\.sb-cutcard\.selected \{[^}]*outline:/);
  assert.match(featureStyles, /\.sb-selection-ring i \{[^}]*will-change: opacity/);
  assert.match(featureStyles, /@keyframes sbRingSky \{[^}]*opacity:/);
  assert.match(featureStyles, /prefers-reduced-motion: reduce[^]*\.sb-selection-ring i \{ animation: none/);
});

test('between-cut controls are centered only in a measured same-row gap', () => {
  assert.match(storyboardSource, /Math\.abs\(next\.offsetTop - unit\.offsetTop\) < 2 \? 'row' : 'end'/);
  assert.match(featureStyles, /\.sb-addzone \{[^}]*right: -18px;[^}]*width: 18px/);
  assert.match(featureStyles, /\.sb-addzone\.end \{ display: none; \}/);
});
