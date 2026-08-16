import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  DEFAULT_HOOK_STYLE,
  HOOK_FRAME_VERSION,
  HOOK_STYLES,
  HOOK_STYLE_LABELS,
  deriveHookFrame,
  hookSlotPlan,
  moodGridContent,
} from '../../src/lib/storyboardHookFrame.js';

const block = (id, extra = {}) => ({ id, sectionRole: 'hooking', cutType: 'styling', ...extra });
const frameBlock = (id, extra = {}) => block(id, {
  hookFrameId: 'hookframe__1', hookStyle: 'pair', hookFrameVersion: 1, ...extra,
});

test('contract constants: three seller-facing styles, signature is the entry default', () => {
  assert.deepEqual(HOOK_STYLES, ['signature', 'pair', 'moodGrid']);
  assert.equal(DEFAULT_HOOK_STYLE, 'signature');
  assert.equal(HOOK_STYLE_LABELS.signature, '시그니처 컷');
  assert.equal(HOOK_STYLE_LABELS.pair, '두 컷 구성');
  assert.equal(HOOK_STYLE_LABELS.moodGrid, '네 컷 구성');
});

test('mood grid content branches by color count only — no user toggle', () => {
  assert.equal(moodGridContent([{ id: 'c1' }]), 'byCuts');
  assert.equal(moodGridContent([{ id: 'c1' }, { id: 'c2' }]), 'byColor');
  assert.equal(moodGridContent([]), 'byCuts');
  assert.equal(moodGridContent(undefined), 'byCuts');
});

test('deriveHookFrame reads the first contiguous run and its slot ids', () => {
  const blocks = [
    block('before'),
    frameBlock('left'),
    frameBlock('right'),
    block('unused'),
  ];
  assert.deepEqual(deriveHookFrame(blocks), {
    style: 'pair', frameId: 'hookframe__1', slotIds: ['left', 'right'], version: HOOK_FRAME_VERSION,
  });
});

test('deriveHookFrame returns null for boards without a frame or with a broken run', () => {
  assert.equal(deriveHookFrame([block('a'), block('b')]), null);
  assert.equal(deriveHookFrame(null), null);
  // 같은 frameId 가 비연속으로 흩어져 있으면 손상 — 프레임으로 취급하지 않는다.
  const broken = [frameBlock('a'), block('gap'), frameBlock('b')];
  assert.equal(deriveHookFrame(broken), null);
});

test('signature/pair plans reuse the default hero+benefit cuts (cut count unchanged)', () => {
  const signature = hookSlotPlan('signature');
  assert.deepEqual(signature, [
    { role: 'signature', cutType: 'horizon', shot: 'medium', titleOverlay: true },
  ]);
  // '두 컷 구성' = 미디움샷 2장 (오너 카피: "미디움샷 이미지 2개를 붙여서")
  const pair = hookSlotPlan('pair');
  assert.deepEqual(pair.map((slot) => [slot.role, slot.cutType, slot.shot]), [
    ['left', 'styling', 'medium'],
    ['right', 'horizon', 'medium'],
  ]);
});

test('four-cut frame: one slot per registered color, same cut/shot for comparability', () => {
  const colors = [{ id: 'c1' }, { id: 'c2' }, { id: 'c3' }, { id: 'c4' }];
  const plan = hookSlotPlan('moodGrid', { colors });
  assert.equal(plan.length, 4);
  assert.deepEqual(plan.map((slot) => slot.colorId), ['c1', 'c2', 'c3', 'c4']);
  assert.ok(plan.every((slot) => slot.cutType === 'horizon' && slot.shot === 'medium'));
});

test('four-cut frame: 2~3 colors always pad to four slots with base-color cuts', () => {
  const colors = [{ id: 'c1', isBase: true }, { id: 'c2' }];
  const plan = hookSlotPlan('moodGrid', { colors });
  assert.equal(plan.length, 4);
  assert.deepEqual(plan.slice(0, 2).map((slot) => slot.colorId), ['c1', 'c2']);
  assert.ok(plan.slice(2).every((slot) => slot.colorId === 'c1'));   // 남는 칸 = 기준색
  // 채움 컷은 색상 슬롯(호리존 미디움)과 겹치지 않는 다른 컷·샷
  assert.ok(plan.slice(2).every((slot) => !(slot.cutType === 'horizon' && slot.shot === 'medium')));
});

test('mood grid plan: single color falls back to four distinct cuts of the same color', () => {
  const plan = hookSlotPlan('moodGrid', { colors: [{ id: 'only' }] });
  assert.equal(plan.length, 4);
  assert.ok(plan.every((slot) => ['styling', 'horizon'].includes(slot.cutType)));
  assert.ok(plan.every((slot) => slot.colorId === undefined));   // 색상은 기준색 그대로
  // 서로 다른 네 컷 — 방향 변형까지 포함해 겹치지 않는다.
  assert.equal(new Set(plan.map((slot) => `${slot.cutType}/${slot.shot}/${slot.direction || 'front'}`)).size, 4);
});

test('slot plans skip unpublished combos via isCutAvailable (empty-tile prevention)', () => {
  // 여성 낱장은 호리존 미디움만 열려 있는 카탈로그를 흉내 — 호리존 풀샷 금지.
  const isCutAvailable = (cutType, shot) => !(cutType === 'horizon' && shot === 'full');
  const plan = hookSlotPlan('moodGrid', { colors: [{ id: 'only' }], isCutAvailable });
  assert.equal(plan.length, 4);
  assert.ok(plan.every((slot) => isCutAvailable(slot.cutType, slot.shot)), '닫힌 조합은 슬롯이 되면 안 된다');

  // 호리존이 전부 닫히면 시그니처도 스타일링 미디움으로 폴백한다.
  const noHorizon = (cutType) => cutType !== 'horizon';
  assert.deepEqual(
    hookSlotPlan('signature', { isCutAvailable: noHorizon })
      .map((slot) => [slot.cutType, slot.shot]),
    [['styling', 'medium']],
  );

  // 전 조합이 닫힌 극단에서는 원래 후보로 돌아가 빈 계획을 만들지 않는다.
  assert.equal(hookSlotPlan('pair', { isCutAvailable: () => false }).length, 2);
});

test('unknown style throws instead of guessing', () => {
  assert.throws(() => hookSlotPlan('carousel'), /unknown_hook_style/);
});

test('frame detach requires an actual value change, and availability matches the assigner', () => {
  const source = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
  // 같은 색상 점 재클릭이 프레임을 해체하면 지문에 안 잡혀 기본 보드로 오판된다(Codex 2차 #1).
  assert.match(source, /applied\.cutType !== current\.cutType/);
  assert.match(source, /applied\.shot !== current\.shot/);
  assert.match(source, /applied\.colorId !== current\.colorId/);
  // 가용성 판정은 자동 배정기와 동일해야 한다 — appendSetOnly 금지(Codex 2차 #2).
  const availability = source.slice(source.indexOf('const hookCutAvailable'), source.indexOf('async function applyHookStyleChoice'));
  assert.doesNotMatch(availability, /appendSetOnly/);
});

/* ---------- P2: 스타일 전환 엔진 ---------- */

import { adoptHookFrame, applyHookStyle } from '../../src/lib/storyboardHookFrame.js';

const aiBlock = (id, extra = {}) => ({
  id, source: 'ai', sectionId: 'sec-hook', sectionRole: 'hooking', ...extra,
});
const seedBoard = () => [
  aiBlock('hero', { contentRole: 'hero', cutType: 'styling', shot: 'full', colorId: 'base', exampleId: 'ex-hero', exampleSelectionOrigin: 'auto', refScope: 'all', baseThumb: 'hero-base.png', thumb: 'hero-ex.png' }),
  aiBlock('benefit', { contentRole: 'benefit', cutType: 'horizon', shot: 'medium', colorId: 'base', exampleId: 'ex-benefit', exampleSelectionOrigin: 'auto', refScope: 'all' }),
  { id: 'styling-1', source: 'ai', sectionId: 'sec-styling', sectionRole: 'styling', cutType: 'styling', shot: 'full' },
];
const makeFactory = () => {
  let n = 0;
  return (slot) => ({ id: `new-${n += 1}`, source: 'ai', sectionId: 'sec-hook', sectionRole: 'hooking', colorId: 'base', ...slot });
};

test('applyHookStyle(signature): benefit leads with the frame, surplus AI cut is removed', () => {
  const next = applyHookStyle(seedBoard(), 'signature', { colors: [{ id: 'base', isBase: true }], frameId: 'hookframe__t' });
  // 스타일 = 정확한 컷 구성(2026-08-14 오너 정정): 슬롯에 안 쓰인 후킹 AI 컷은 삭제된다.
  assert.deepEqual(next.map((block) => block.id), ['benefit', 'styling-1']);
  const [slot] = next;
  assert.equal(slot.hookStyle, 'signature');
  assert.equal(slot.hookTitleOverlay, true);
  assert.equal(slot.exampleId, 'ex-benefit');          // 틀 그대로 → 예시 보존
  assert.equal(next[1].sectionRole, 'styling');        // 다른 섹션은 그대로
});

test('applyHookStyle keeps seller-uploaded mine cuts in the hooking section', () => {
  const board = [
    ...seedBoard(),
  ];
  board.splice(2, 0, { id: 'mine-1', source: 'mine', sectionId: 'sec-hook', sectionRole: 'hooking', ownImages: ['m.png'] });
  const next = applyHookStyle(board, 'signature', { colors: [{ id: 'base', isBase: true }], frameId: 'hookframe__t' });
  // AI 잔여(hero)는 삭제되지만 셀러 업로드(mine)는 프레임 뒤에 남는다(오너 확정).
  assert.deepEqual(next.map((block) => block.id), ['benefit', 'mine-1', 'styling-1']);
});

test('applyHookStyle(pair): two medium slots, hero converts shot and drops its stale example', () => {
  const next = applyHookStyle(seedBoard(), 'pair', { colors: [{ id: 'base', isBase: true }], frameId: 'hookframe__t' });
  const slots = next.filter((block) => block.hookFrameId === 'hookframe__t');
  assert.deepEqual(slots.map((block) => [block.id, block.cutType, block.shot]), [
    ['hero', 'styling', 'medium'],
    ['benefit', 'horizon', 'medium'],
  ]);
  const hero = slots[0];
  assert.equal(hero.exampleId, null);                  // 샷 전환 → 예시 무효화(재배정 대상)
  assert.equal(hero.thumb, 'hero-base.png');
  assert.ok(slots.every((block) => block.sectionLayout === 'twoColumn'));
  assert.equal(new Set(slots.map((block) => block.layoutRowId)).size, 1);
  assert.equal(slots[1].exampleId, 'ex-benefit');      // 틀 유지 슬롯은 예시 보존
});

test('applyHookStyle(moodGrid, 4 colors): four color slots in one 2×2 row, missing cuts created', () => {
  const colors = [{ id: 'base', isBase: true }, { id: 'c2' }, { id: 'c3' }, { id: 'c4' }];
  const next = applyHookStyle(seedBoard(), 'moodGrid', { colors, createBlock: makeFactory(), frameId: 'hookframe__t' });
  const slots = next.filter((block) => block.hookFrameId === 'hookframe__t');
  assert.equal(slots.length, 4);
  assert.deepEqual(slots.map((block) => block.colorId), ['base', 'c2', 'c3', 'c4']);
  assert.ok(slots.every((block) => block.cutType === 'horizon' && block.shot === 'medium'));
  // 네 컷은 한 행(grid2x2) — 콘티의 붙은 2×2와 에디터 조립이 같은 뜻이 되려면 쪼개면 안 된다(오너 8/16).
  assert.equal([...new Set(slots.map((block) => block.layoutRowId))].length, 1);
  assert.ok(slots.every((block) => block.sectionLayout === 'grid2x2'));
  // 기존 후킹 2컷 재사용 + 신규 2컷 생성, 다른 섹션 컷은 소모하지 않는다
  assert.deepEqual(slots.map((block) => block.id), ['benefit', 'hero', 'new-1', 'new-2']);
  assert.ok(next.some((block) => block.id === 'styling-1' && !block.hookFrameId));
});

test('applyHookStyle round-trip back to signature leaves exactly the style cut count', () => {
  const colors = [{ id: 'base', isBase: true }, { id: 'c2' }];
  const grid = applyHookStyle(seedBoard(), 'moodGrid', { colors, createBlock: makeFactory(), frameId: 'hookframe__g' });
  assert.equal(grid.filter((block) => block.sectionRole === 'hooking').length, 4);
  const back = applyHookStyle(grid, 'signature', { colors, frameId: 'hookframe__s' });
  const slots = back.filter((block) => block.hookFrameId === 'hookframe__s');
  assert.equal(slots.length, 1);
  // 줄어드는 전환에서 남는 AI 컷은 삭제 — 후킹 섹션은 항상 스타일의 컷 구성·개수만 갖는다
  // (2026-08-14 오너: "잔여 컷 남기지 말고 그때그때 다르게 배치").
  assert.equal(back.filter((block) => block.sectionRole === 'hooking').length, 1);
  assert.ok(!slots[0].layoutRowId);
});

test('adoptHookFrame promotes a legacy opening row to a pair frame, leaves others alone', () => {
  const opening = [
    aiBlock('hero', { contentRole: 'hero', cutType: 'styling', shot: 'medium', layoutRowId: 'row__opening__sec-hook', sectionLayout: 'twoColumn' }),
    aiBlock('benefit', { contentRole: 'benefit', cutType: 'horizon', shot: 'medium', layoutRowId: 'row__opening__sec-hook', sectionLayout: 'twoColumn' }),
  ];
  const adopted = adoptHookFrame(opening);
  assert.equal(adopted.changed, true);
  assert.ok(adopted.blocks.every((block) => block.hookStyle === 'pair'));
  assert.equal(adopted.blocks[0].hookFrameId, adopted.blocks[1].hookFrameId);

  const already = adoptHookFrame(adopted.blocks);
  assert.equal(already.changed, false);

  const legacyVertical = seedBoard();                   // 오프닝 행 없는 구형 세로 보드
  assert.equal(adoptHookFrame(legacyVertical).changed, false);
});

test('board normalization keeps hook-frame rows regardless of section cut counts', async () => {
  const { normalizeBoard, ensureSections } = await import('../../src/lib/sections.js');
  let n = 0;
  const mk = (id, extra = {}) => ({
    id, sectionId: 'sec-hook', sectionRole: 'hooking', source: 'ai',
    cutType: 'horizon', shot: 'medium', colorId: 'base', taxonomyVersion: 3, contentRole: 'benefit', ...extra,
  });
  const board = [
    mk('h1', { contentRole: 'hero', hookFrameId: 'hf', hookStyle: 'signature', hookFrameVersion: 1, hookTitleOverlay: true, hookSlotRole: 'signature' }),
    mk('h2', { cutType: 'styling', shot: 'full' }),
  ];
  const colors = [{ id: 'base', isBase: true }, { id: 'c2' }, { id: 'c3' }, { id: 'c4' }];
  const grid = applyHookStyle(board, 'moodGrid', {
    colors, createBlock: (slot) => mk(`new-${n += 1}`, slot), frameId: 'hf2',
  });
  const normalized = normalizeBoard(ensureSections(grid));
  const slots = normalized.filter((block) => block.hookFrameId === 'hf2');
  assert.equal(slots.length, 4);
  assert.ok(slots.every((block) => block.sectionLayout === 'grid2x2'));
  assert.equal(new Set(slots.map((block) => block.layoutRowId)).size, 1);   // 4칸 1행 유지
});

test('style transition keeps user-pinned examples in matching slots (Codex review #2)', () => {
  const board = [
    aiBlock('hero', { contentRole: 'hero', cutType: 'horizon', shot: 'medium', colorId: 'base', exampleId: 'ex-user', exampleSelectionOrigin: 'user', refScope: 'all' }),
    aiBlock('benefit', { contentRole: 'benefit', cutType: 'styling', shot: 'full', colorId: 'base', exampleId: 'ex-auto', exampleSelectionOrigin: 'auto', refScope: 'all' }),
  ];
  // pair 의 오른쪽 슬롯(호리존 미디움)은 고정 컷과 틀이 같다 — 고정 선택이 그대로 살아남아야 한다.
  const next = applyHookStyle(board, 'pair', { colors: [{ id: 'base', isBase: true }], frameId: 'hookframe__t' });
  const right = next.find((block) => block.hookSlotRole === 'right');
  assert.equal(right.id, 'hero');
  assert.equal(right.exampleId, 'ex-user');
  assert.equal(right.exampleSelectionOrigin, 'user');
  // 틀 전환이 필요한 왼쪽 슬롯은 auto 컷이 소모된다(고정 보호).
  const left = next.find((block) => block.hookSlotRole === 'left');
  assert.equal(left.id, 'benefit');
});

test('section shuffle skips space sets holding a user-pinned member (Codex review #3)', async () => {
  const { shuffleSectionExamples } = await import('../../src/lib/storyboardExampleShuffle.js');
  const { STORYBOARD_SPACE_SET_EXAMPLES } = await import('../../src/lib/storyboardSpaceSetCatalog.js');
  const member = STORYBOARD_SPACE_SET_EXAMPLES.find((item) => item.gender === 'women');
  const setBlocks = [0, 1].map((index) => ({
    id: `set-${index}`, sectionId: 'sec-style', sectionRole: 'styling', source: 'ai',
    cutType: member.cutType, shot: 'full',
    spaceGroupId: 'ssg1__someset__i1', spaceSetMemberOrder: index + 1,
    setSelectionOrigin: 'auto', refScope: 'pose',
    exampleId: member.id,
    exampleSelectionOrigin: index === 0 ? 'user' : 'auto',   // 멤버 한 컷 고정
  }));
  const next = shuffleSectionExamples(setBlocks, {
    sectionId: 'sec-style',
    catalog: [],
    product: { clothingType: member.applicableClothingTypes[0] },
    gender: 'women',
    rotation: 1,
  });
  assert.equal(next, setBlocks);   // 고정 멤버 보유 세트는 통째로 제외 — 무변경
});
