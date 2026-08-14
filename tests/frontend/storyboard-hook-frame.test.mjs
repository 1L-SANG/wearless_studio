import test from 'node:test';
import assert from 'node:assert/strict';

import {
  DEFAULT_HOOK_STYLE,
  HOOK_FRAME_VERSION,
  HOOK_STYLES,
  HOOK_STYLE_LABELS,
  deriveHookFrame,
  hookSlotPlan,
  moodGridContent,
  unslottedHookBlocks,
} from '../../src/lib/storyboardHookFrame.js';

const block = (id, extra = {}) => ({ id, sectionRole: 'hooking', cutType: 'styling', ...extra });
const frameBlock = (id, extra = {}) => block(id, {
  hookFrameId: 'hookframe__1', hookStyle: 'pair', hookFrameVersion: 1, ...extra,
});

test('contract constants: three seller-facing styles, signature is the entry default', () => {
  assert.deepEqual(HOOK_STYLES, ['signature', 'pair', 'moodGrid']);
  assert.equal(DEFAULT_HOOK_STYLE, 'signature');
  assert.equal(HOOK_STYLE_LABELS.signature, '시그니처 컷');
  assert.equal(HOOK_STYLE_LABELS.pair, '두컷 프레임');
  assert.equal(HOOK_STYLE_LABELS.moodGrid, '네컷 프레임');
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
  // '두컷 프레임' = 미디움샷 2장 (오너 카피: "미디움샷 이미지 2개를 붙여서")
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
  assert.equal(new Set(plan.map((slot) => `${slot.cutType}/${slot.shot}`)).size, 4);
});

test('unknown style throws instead of guessing', () => {
  assert.throws(() => hookSlotPlan('carousel'), /unknown_hook_style/);
});

test('unslotted hooking blocks stay as ordinary cuts after the frame', () => {
  const blocks = [frameBlock('left'), frameBlock('right'), block('unused')];
  const frame = deriveHookFrame(blocks);
  assert.deepEqual(unslottedHookBlocks(blocks, frame).map((item) => item.id), ['unused']);
  assert.deepEqual(unslottedHookBlocks(blocks, null), blocks);
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

test('applyHookStyle(signature): benefit leads with the frame, hero stays as an ordinary cut', () => {
  const next = applyHookStyle(seedBoard(), 'signature', { colors: [{ id: 'base', isBase: true }], frameId: 'hookframe__t' });
  assert.deepEqual(next.map((block) => block.id), ['benefit', 'hero', 'styling-1']);
  const [slot, unused] = next;
  assert.equal(slot.hookStyle, 'signature');
  assert.equal(slot.hookTitleOverlay, true);
  assert.equal(slot.exampleId, 'ex-benefit');          // 틀 그대로 → 예시 보존
  assert.equal(unused.hookFrameId, undefined);
  assert.equal(unused.layoutRowId, undefined);
  assert.equal(next[2].sectionRole, 'styling');        // 다른 섹션은 그대로
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

test('applyHookStyle(moodGrid, 4 colors): four color slots in two rows, missing cuts created', () => {
  const colors = [{ id: 'base', isBase: true }, { id: 'c2' }, { id: 'c3' }, { id: 'c4' }];
  const next = applyHookStyle(seedBoard(), 'moodGrid', { colors, createBlock: makeFactory(), frameId: 'hookframe__t' });
  const slots = next.filter((block) => block.hookFrameId === 'hookframe__t');
  assert.equal(slots.length, 4);
  assert.deepEqual(slots.map((block) => block.colorId), ['base', 'c2', 'c3', 'c4']);
  assert.ok(slots.every((block) => block.cutType === 'horizon' && block.shot === 'medium'));
  assert.deepEqual([...new Set(slots.map((block) => block.layoutRowId))].length, 2);   // 2×2 = 2행
  // 기존 후킹 2컷 재사용 + 신규 2컷 생성, 다른 섹션 컷은 소모하지 않는다
  assert.deepEqual(slots.map((block) => block.id), ['benefit', 'hero', 'new-1', 'new-2']);
  assert.ok(next.some((block) => block.id === 'styling-1' && !block.hookFrameId));
});

test('applyHookStyle round-trip back to signature clears frame/row fields from ex-slots', () => {
  const colors = [{ id: 'base', isBase: true }, { id: 'c2' }];
  const grid = applyHookStyle(seedBoard(), 'moodGrid', { colors, createBlock: makeFactory(), frameId: 'hookframe__g' });
  const back = applyHookStyle(grid, 'signature', { colors, frameId: 'hookframe__s' });
  const slots = back.filter((block) => block.hookFrameId === 'hookframe__s');
  assert.equal(slots.length, 1);
  const exSlots = back.filter((block) => block.sectionRole === 'hooking' && !block.hookFrameId);
  assert.ok(exSlots.length >= 3);                       // 빠진 컷은 보관(삭제 없음)
  assert.ok(exSlots.every((block) => !block.layoutRowId && !block.hookStyle));
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
  assert.ok(slots.every((block) => block.sectionLayout === 'twoColumn'));
  assert.equal(new Set(slots.map((block) => block.layoutRowId)).size, 2);   // 2행 유지
});
