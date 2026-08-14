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
