import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { buildFailedCutRetry } from '../../src/features/editor/failedCutRetry.js';

test('실패한 시그니처 슬롯은 원래 콘티의 생성 정본을 그대로 재사용한다', () => {
  const storyboard = [{
    id: 'blk-signature', source: 'ai', title: '첫 장면', contentRole: 'hero',
    colorId: 'white', cutType: 'horizon', direction: 'front', shot: 'medium',
    faceExposure: 'same', pose: 'auto', exampleId: 'sig_women_01', modelId: 'model-a',
    matchIds: ['matching-a'], refScope: 'all',
    thumb: '/assets/signature/thumb/sig_women_01.webp',
    refImages: [{ assetId: 'mood-a', url: 'blob:display-only' }],
    sectionId: 'must-not-leak',
  }];

  const retry = buildFailedCutRetry(storyboard, 'blk-signature');
  assert.equal(retry.signature, true);
  assert.equal(retry.thumb, '/assets/signature/thumb/sig_women_01.webp');
  assert.deepEqual(retry.request, {
    mode: 'new', contentRole: 'hero', colorId: 'white', cutType: 'horizon', direction: 'front',
    shot: 'medium', faceExposure: 'same', pose: 'auto', exampleId: 'sig_women_01',
    modelId: 'model-a', matchIds: ['matching-a'], refScope: 'all', refAssetIds: ['mood-a'],
  });
  assert.equal('sectionId' in retry.request, false);
});

test('AI가 아닌 슬롯이나 원본 콘티가 없는 슬롯은 재생성하지 않는다', () => {
  assert.equal(buildFailedCutRetry([], 'missing'), null);
  assert.equal(buildFailedCutRetry([{ id: 'mine', source: 'mine', cutType: null }], 'mine'), null);
});

test('저장 콘티를 바꾸지 않고 분석의 2슬롯 모델을 컷별 재시도에 복원한다', () => {
  const storyboard = [
    { id: 'h', source: 'ai', cutType: 'horizon', shot: 'full' },
    { id: 's', source: 'ai', cutType: 'styling', shot: 'full' },
    { id: 'm', source: 'ai', cutType: 'mirror', shot: 'full' },
    { id: 'p', source: 'ai', cutType: 'product', shot: 'ghost' },
  ];
  const realSelection = {
    selectedModelId: '11111111-1111-1111-1111-111111111111',
    stylingModelId: 'mA',
  };

  assert.equal(buildFailedCutRetry(storyboard, 'h', realSelection).request.modelId,
    realSelection.selectedModelId);
  assert.equal(buildFailedCutRetry(storyboard, 's', realSelection).request.modelId, 'mA');
  assert.equal(buildFailedCutRetry(storyboard, 'm', realSelection).request.modelId, 'mA');
  assert.equal(buildFailedCutRetry(storyboard, 'p', realSelection).request.modelId, null);
  assert.equal(buildFailedCutRetry(storyboard, 's', {
    selectedModelId: 'mB', stylingModelId: null,
  }).request.modelId, 'mB');
});

test('에디터는 실패 슬롯에서 일반 새 이미지 폼 대신 전용 재시도를 노출한다', () => {
  const editor = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
  const panels = readFileSync(new URL('../../src/features/editor/EditorPanels.jsx', import.meta.url), 'utf8');
  assert.match(editor, /buildFailedCutRetry\([\s\S]*wardrobeContext\.current\.storyboard,[\s\S]*pendingSlot\.sourceBlockId,[\s\S]*analysis,[\s\S]*\)/);
  assert.match(editor, /stylingModelPatchForAnalysis\(an, AI_MODELS\)/);
  assert.match(editor, /onRetryFailedCut=\{retryFailedCut\}/);
  assert.match(panels, /if \(failedCutRetry\)/);
  assert.match(panels, /시그니처 컷 다시 만들기/);
});
