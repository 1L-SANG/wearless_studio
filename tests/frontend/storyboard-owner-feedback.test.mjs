import { readStoryboardSources } from './helpers/storyboardComponents.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';

import {
  generationExampleSelectionPatch,
} from '../../src/lib/storyboardExampleSelection.js';

const storyboardSource = readStoryboardSources();
const featureStyles = readFileSync(new URL('../../src/styles/features.css', import.meta.url), 'utf8');

test('v4 콘티는 카피라이팅을 켜짐으로 저장하고 아래 바에 스위치를 두지 않는다', () => {
  assert.match(storyboardSource, /ensureV4StoryboardCopywriting\(projectId, useAppStore.getState, \{ force: true \}\)/);
  const action = readFileSync(new URL('../../src/features/storyboard/StoryboardContinue.jsx', import.meta.url), 'utf8');
  assert.doesNotMatch(action, /Toggle|copyOn|onCopyChange/);
  assert.doesNotMatch(storyboardSource, /AI가 카피를 자동으로 넣어요/);
});

test('replacing a generation example resets per-cut settings but preserves its structural recipe', () => {
  const block = {
    id: 'cut-1', sectionRole: 'styling', contentRole: 'coordination',
    cutType: 'styling', shot: 'medium', direction: 'back', colorId: 'red',
    exampleId: 'old', refScope: 'all', pose: 'walk', poseLabel: '걷기', angle: 'high',
    matchIds: ['pants'], refImages: ['mine.png'], refAssetIds: ['asset-1'],
    faceExposure: 'hide', outerClosureState: 'closed',
  };
  const result = generationExampleSelectionPatch(block, { id: 'new', direction: 'front' }, {
    clothingType: 'outer', defaultColorId: 'base', refScope: 'all',
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
    exampleId: 'new', exampleChoice: null, exampleSelectionOrigin: 'user', refScope: 'all',
    // 2026-09-23: 착용컷은 고른 예시의 방향이 카드 방향이다. 정면 예시라 sideStyle 은 떨어진다
    // — 남겨 두면 서버가 side 로 읽어 사선/옆모습이 뒤섞인다.
    direction: 'front', sideStyle: null, colorId: 'base', colorIds: [],
    angle: 'same', matchIds: [], refImages: [], refAssetIds: [], faceExposure: 'same',
    outerClosureState: 'open',
  });
});

test('v4 mock runtime preserves manually added published set members without v3 migration', async (t) => {
  const vite = await createServer({
    optimizeDeps: { noDiscovery: true, include: [] },
    configFile: false,
    plugins: [react()],
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null }, appType: 'custom', logLevel: 'silent',
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
  assert.equal(migrated.length, 3);
  assert.deepEqual(new Set(migrated.map((block) => block.shot)), new Set(['full', 'medium']));
  reseedDraft();
});

test('example clicks apply id and scope together instead of overwriting from a stale board snapshot', async () => {
  const { storyboardPhotoPanelHarness } = await import('./helpers/storyboardPhotoPanelHarness.mjs');
  const { nodes } = await import('./helpers/storyboardComponents.mjs');
  const h = await storyboardPhotoPanelHarness({ block: { id: 'a', source: 'ai', cutType: 'styling', shot: 'full', exampleId: 'old' },
    catalogs: { genExamples: [{ id: 'new', cutType: 'styling', shot: 'medium', direction: 'front' }], shotTypes: [{ value: 'full' }, { value: 'medium' }], productShotTypes: [] } });
  nodes(h.render()).find(node => node.type === 'mood-guide').props.onExampleChange('new', 'all');
  assert.equal(h.block.exampleId, 'new'); assert.equal(h.block.refScope, 'all'); assert.equal(h.block.shot, 'medium'); h.close();
});
test('the storyboard undo window groups settings, isolates section actions, pauses on hover, and exposes its count', () => {
  assert.match(storyboardSource, /UNDO_WINDOW_MS = 10_000/);
  assert.match(storyboardSource, /const before = active \? active\.before : \[\.\.\.previous\]/);
  assert.match(storyboardSource, /operationCount = \(active\?\.operationCount \|\| 0\) \+ 1/);
  assert.match(storyboardSource, /const active = !standalone && !current\?\.standalone \? current : null/);
  assert.match(storyboardSource, /previousEntry: active \? active\.previousEntry : current/);
  assert.match(storyboardSource, /onMouseEnter=/);
  assert.match(storyboardSource, /\{undoEntry\.operationCount\}가지 바꿨어요/);
});

test('owner cleanup removes place dissolve and obsolete inspector copy and stack counts', () => {
  assert.doesNotMatch(storyboardSource, /장소 세트 묶음 풀기|dissolveSpaceGroup|sb-stack-count/);
  assert.doesNotMatch(storyboardSource, /아래 설정은 이 사진에만 적용/);
});
test('location groups remain whole and additions stay at the end of the stage', async () => {
  const { packStageRows } = await import('../../src/lib/pageTemplates/storyboardStage.js');
  const blocks = [{ id: 'a', source: 'ai', spaceGroupId: 'set' }, { id: 'b', source: 'ai', spaceGroupId: 'set' }];
  const rows = packStageRows(blocks);
  assert.deepEqual(rows[0][0].blocks, blocks);
  assert.equal(rows[0].at(-1).kind, 'add');
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

test('seller photo replacement remains available in the photo panel', async () => {
  const { storyboardPhotoPanelHarness } = await import('./helpers/storyboardPhotoPanelHarness.mjs');
  const { nodes } = await import('./helpers/storyboardComponents.mjs');
  const h = await storyboardPhotoPanelHarness({ block: { id: 'a', source: 'ai', cutType: 'styling', shot: 'full' } });
  const guide = nodes(h.render()).find(node => node.type === 'mood-guide');
  guide.props.onUseMine({ url: '/seller.webp' });
  assert.equal(h.block.source, 'mine'); assert.deepEqual(h.block.ownImages, ['/seller.webp']); h.close();
});
test('uploading from the my-image tab commits the chosen image instead of saving an AI reference first', () => {
  const mineTab = storyboardSource.slice(
    storyboardSource.indexOf('function MineImageTab'),
    storyboardSource.indexOf('function SpaceSetCard'),
  );
  assert.match(mineTab, /if \(onChoose\)[^]*onChoose\(picked\);[^]*return;[^]*onImagesChange/);
});

test('v5 section movement carries complete location groups without changing their membership', async () => {
  const { seedStoryboard } = await import('../../src/lib/pageTemplates/seed.js');
  const { moveTemplateSection } = await import('../../src/lib/pageTemplates/sectionActions.js');
  const board = seedStoryboard({ clothingType: 'top', colors: [{ id: 'base', isBase: true }] }, { targetGenders: ['women'] });
  const source = board.find(block => block.spaceGroupId);
  const moved = moveTemplateSection(board, source.pageSectionKey, null);
  assert.deepEqual(moved.filter(block => block.pageSectionKey === source.pageSectionKey), board.filter(block => block.pageSectionKey === source.pageSectionKey));
});
test('v5 stage uses one terminal add tile without between-photo controls', async () => {
  const { packStageRows } = await import('../../src/lib/pageTemplates/storyboardStage.js');
  const rows = packStageRows([{ id: 'a', source: 'ai' }, { id: 'b', source: 'ai' }]);
  assert.equal(rows.flat().filter(group => group.kind === 'add').length, 1);
  assert.equal(rows.at(-1).at(-1).kind, 'add');
});
test('card captions are one-line right aligned without reference popovers', () => {
  assert.doesNotMatch(storyboardSource, /REF_SCOPE_META|sb-ref-chip|sb-ref-pop|전체 참조|포즈 변경됨/);
  assert.match(featureStyles, /\.sb-canvas-caption \{[^}]*justify-content: flex-end/);
  assert.match(featureStyles, /@container \(max-width: 175px\)/);
});

test('selected card ring blues share the seller token and preserve the other ring colors', () => {
  for (const token of ['sky', 'mauve']) {
    assert.match(featureStyles, new RegExp(`--sb-ring-${token}: var\\(--accent-blue\\)`));
  }
  for (const color of ['#7661b5', '#7b6aa8']) {
    assert.match(featureStyles, new RegExp(color));
  }
  assert.match(featureStyles, /\.sb-selection-ring \{ box-shadow: inset 0 0 0 3px var\(--sb-ring-sky\); \}/);
  assert.match(featureStyles, /\.sb-selection-ring i \{[^}]*0 0 0 3px/);
  const selectedCardRing = featureStyles.slice(featureStyles.indexOf('.sb-selection-ring {'),
    featureStyles.indexOf('@keyframes sbRingSun'));
  assert.doesNotMatch(selectedCardRing, /var\(--glow-sun\)/);
});

test('matching clothing action uses the owner-approved wording', () => {
  assert.match(storyboardSource, /매칭 의류 바꾸기/);
  assert.doesNotMatch(storyboardSource, /매칭 의류 편집/);
});
