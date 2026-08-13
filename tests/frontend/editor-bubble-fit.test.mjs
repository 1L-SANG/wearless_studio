import assert from 'node:assert/strict';
import test from 'node:test';

import {
  bubbleTextWidth,
  fitBubbleToText,
  mergeSpeechBubbleElements,
  patchSelectedBubbleAppearance,
} from '../../src/features/editor/editorBubbleFit.js';

test('speech bubble follows short copy immediately while keeping its left anchor and padding', () => {
  const bubble = {
    id: 'bubble', type: 'text', shape: 'bubble', x: 100, y: 80, w: 380, h: 104,
    text: '짧은 질문',
    bubbleFit: { minWidth: 160, maxWidth: 620, padX: 24, padTop: 22, padBottom: 42, anchor: 'left' },
  };

  assert.equal(bubbleTextWidth(bubble, 112), 116);
  assert.deepEqual(fitBubbleToText(bubble, { naturalWidth: 112, renderedHeight: 29 }), {
    elementPatch: { x: 100, y: 80, w: 164, h: 93 },
    textWidth: 116,
    textHeight: 29,
  });
});

test('legacy minimum widths no longer delay responsive growth', () => {
  const bubble = {
    id: 'bubble', type: 'text', shape: 'bubble', x: 40, y: 30, w: 320, h: 100,
    bubbleFit: { minWidth: 220, maxWidth: 560, padX: 24, padTop: 20, padBottom: 38, anchor: 'left' },
  };

  assert.equal(bubbleTextWidth(bubble, 36), 40);
  assert.equal(fitBubbleToText(bubble, { naturalWidth: 36, renderedHeight: 29 }).elementPatch.w, 88);
});

test('right-hand speech bubble grows up to its limit without moving its right edge', () => {
  const bubble = {
    id: 'bubble', type: 'text', shape: 'bubble', x: 210, y: 192, w: 520, h: 142, flipX: true,
    text: '긴 답변',
    bubbleFit: { minWidth: 200, maxWidth: 604, padX: 30, padTop: 26, padBottom: 50, anchor: 'right' },
  };

  assert.equal(bubbleTextWidth(bubble, 900), 604);
  assert.deepEqual(fitBubbleToText(bubble, { naturalWidth: 900, renderedHeight: 58 }), {
    elementPatch: { x: 66, y: 192, w: 664, h: 134 },
    textWidth: 604,
    textHeight: 58,
  });
});

test('legacy shape and copy layers merge into one editable speech-bubble element', () => {
  const elements = [
    { id: 'bubble', type: 'shape', shape: 'bubble', groupId: 'qa', bubblePairId: 'pair', x: 20, y: 20, w: 300, h: 90, fill: '#fff', stroke: '#111' },
    {
      id: 'copy', type: 'text', groupId: 'qa', bubblePairId: 'pair', x: 40, y: 40, w: 260, h: 30,
      text: '한 요소가 됩니다', style: { size: 20 },
      bubbleFit: { minWidth: 160, maxWidth: 600, padX: 20, padTop: 20, padBottom: 40, anchor: 'left' },
    },
    { id: 'keep', type: 'text', x: 10, y: 150, w: 200, h: 30, text: '일반 텍스트' },
  ];

  const merged = mergeSpeechBubbleElements(elements);
  assert.equal(merged.length, 2);
  assert.deepEqual(merged[0], {
    id: 'bubble', type: 'text', shape: 'bubble', groupId: 'qa', x: 20, y: 20, w: 300, h: 90,
    fill: '#fff', stroke: '#111', strokeWidth: 1, radius: 45, text: '한 요소가 됩니다', style: { size: 20 },
    bubbleFit: { minWidth: 0, maxWidth: 600, padX: 20, padTop: 20, padBottom: 40, anchor: 'left' },
  });
  assert.equal(merged[1], elements[2], 'unrelated elements retain their identity');
  assert.equal(mergeSpeechBubbleElements(merged), merged, 'migration is idempotent');
});

test('legacy speech bubbles without border data receive the subtle default border', () => {
  const merged = mergeSpeechBubbleElements([
    { id: 'bubble', type: 'shape', shape: 'bubble', bubblePairId: 'pair', x: 20, y: 20, w: 300, h: 90, fill: '#fff' },
    { id: 'copy', type: 'text', bubblePairId: 'pair', x: 40, y: 40, w: 260, h: 30, text: '질문', style: { size: 20 } },
  ]);

  assert.equal(merged[0].stroke, '#b9b9be');
  assert.equal(merged[0].strokeWidth, 1);
});

test('bubble appearance controls update every selected bubble without touching normal text', () => {
  const blocks = [{ id: 'faq', elements: [
    { id: 'question', type: 'text', shape: 'bubble', fill: '#fff' },
    { id: 'answer', type: 'text', shape: 'bubble', fill: '#dcecff' },
    { id: 'copy', type: 'text', text: '그대로' },
  ] }];

  assert.deepEqual(
    patchSelectedBubbleAppearance(blocks, ['question', 'answer', 'copy'], { stroke: '#FF0000', strokeWidth: 2.5 }),
    [{ id: 'faq', elements: [
      { id: 'question', type: 'text', shape: 'bubble', fill: '#fff', stroke: '#FF0000', strokeWidth: 2.5 },
      { id: 'answer', type: 'text', shape: 'bubble', fill: '#dcecff', stroke: '#FF0000', strokeWidth: 2.5 },
      { id: 'copy', type: 'text', text: '그대로' },
    ] }],
  );
});
