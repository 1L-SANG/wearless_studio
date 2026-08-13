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
    id: 'cut-1', sectionRole: 'styling', contentRole: 'coordination',
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
    sectionRole: 'styling', contentRole: 'coordination', cutType: 'styling', shot: 'medium',
    exampleId: 'new', exampleChoice: null, exampleSelectionOrigin: 'user', refScope: 'pose',
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

test('owner cleanup removes place dissolve and obsolete inspector copy and stack counts', () => {
  assert.doesNotMatch(storyboardSource, /장소 세트 묶음 풀기|dissolveSpaceGroup/);
  assert.doesNotMatch(storyboardSource, /새 컷의 예시를 먼저 골라주세요/);
  assert.doesNotMatch(storyboardSource, /sb-stack-count/);
  assert.match(storyboardSource, /cutRangeLabel\(group\.items\)/);
});

test('shooting place picker uses column-aware larger previews and no tray place label', () => {
  assert.match(storyboardSource, /const width = 316/);
  assert.match(storyboardSource, /itemIndex % 2 === 0 \? leftSide : rightSide/);
  assert.doesNotMatch(storyboardSource, /<strong>\{label\}<\/strong>|traySeqLabels|normalizePlaceType/);
  assert.match(storyboardSource, /className="sb-tray-swap"[^]*장소 세트 변경/);
});

test('gallery controls sit below a full-width grid and hover uses compositor properties', () => {
  assert.match(featureStyles, /\.sb-exgallery \{[^}]*padding-inline: 0/);
  assert.match(featureStyles, /\.sb-excontrols \{[^}]*justify-content: center/);
  assert.match(featureStyles, /\.sb-excell img \{[^}]*transition: transform \.16s, opacity \.16s/);
  assert.doesNotMatch(featureStyles, /\.sb-excell[^}]*transition:[^;}]*(filter|box-shadow)/);
  assert.doesNotMatch(featureStyles, /\.sb-cutcard\.selected \{[^}]*outline:/);
  assert.match(featureStyles, /\.sb-selection-ring i \{[^}]*will-change: opacity/);
  assert.match(featureStyles, /@keyframes sbRingSky \{[^}]*opacity:/);
  assert.match(featureStyles, /prefers-reduced-motion: reduce[^]*\.sb-selection-ring i \{ animation: none/);
});

test('my images live only in the shot tab flow', () => {
  assert.match(storyboardSource, /MINE_SHOT_OPTION[^]*value: 'mine'[^]*label: '내 이미지'/);
  assert.equal((storyboardSource.match(/내 이미지 업로드/g) || []).length, 1);
  assert.doesNotMatch(storyboardSource, /내 이미지 추가|mine-add-solo|onImgDrag|dragMine|insertMineAt/);
  assert.doesNotMatch(storyboardSource, /MINE_SHOT_OPTION, disabled: inSpace/);
  assert.doesNotMatch(storyboardSource, /if \(isMine\) \{\s*return/);
  assert.match(storyboardSource, /applied\.source === 'mine'[^]*detachSpaceMembership\(withoutLayoutRow\(updated\)\)/);
  assert.match(storyboardSource, /next = ensureContiguousSpaceRuns\(next\)/);
});

test('uploading from the my-image tab commits the chosen image instead of saving an AI reference first', () => {
  const mineTab = storyboardSource.slice(
    storyboardSource.indexOf('function MineImageTab'),
    storyboardSource.indexOf('function SpaceSetCard'),
  );
  assert.match(mineTab, /if \(onChoose\)[^]*onChoose\(picked\);[^]*return;[^]*onImagesChange/);
});

test('a place-set member can cross official sections and detaches through the shared move path', () => {
  const moveHandler = storyboardSource.slice(
    storyboardSource.indexOf('const applySingleMove'),
    storyboardSource.indexOf('const nudgeBlock'),
  );
  assert.doesNotMatch(moveHandler, /장소 세트 묶음을 푼 뒤/);
  assert.match(moveHandler, /moveBlockWithSpaceMembership\(current, id, targetSectionEnd/);
});

test('between-cut controls are centered only in a measured same-row gap', () => {
  assert.match(storyboardSource, /Math\.abs\(next\.offsetTop - unit\.offsetTop\) < 2 \? 'row' : 'end'/);
  assert.match(featureStyles, /\.sb-addzone \{[^}]*right: -18px;[^}]*width: 18px/);
  assert.match(featureStyles, /\.sb-addzone\.end \{[^}]*display: grid;[^}]*right: -24px;[^}]*width: 24px/);
});

test('card captions are one-line right aligned without reference popovers', () => {
  assert.doesNotMatch(storyboardSource, /REF_SCOPE_META|sb-ref-chip|sb-ref-pop|전체 참조|포즈 변경됨/);
  assert.match(featureStyles, /\.sb-canvas-caption \{[^}]*justify-content: flex-end/);
  assert.match(featureStyles, /@container \(max-width: 175px\)/);
});

test('selected card rings use four persistent high-contrast colors', () => {
  for (const color of ['#2f83b5', '#7661b5', '#7b6aa8', '#3971a7']) {
    assert.match(featureStyles, new RegExp(color));
  }
  assert.match(featureStyles, /\.sb-selection-ring \{ box-shadow: inset 0 0 0 3px var\(--sb-ring-sky\); \}/);
  assert.match(featureStyles, /\.sb-selection-ring i \{[^}]*0 0 0 3px/);
  assert.doesNotMatch(featureStyles, /sbRingSun[^]*var\(--glow-sun\)/);
});

test('matching clothing action uses the owner-approved wording', () => {
  assert.match(storyboardSource, /매칭 의류 바꾸기/);
  assert.doesNotMatch(storyboardSource, /매칭 의류 편집/);
});
