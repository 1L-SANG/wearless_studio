import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { uniqueGenerationCutCount } from '../../src/lib/generationCutCount.js';
import scopes from '../../src/data/identityScopes.json' with { type: 'json' };

const states = ['closed', 'openToChest', 'partial', 'openToWaist', 'open'];
const block = (id, extra = {}) => ({ id, source: 'ai', sectionRole: 'studio',
  contentRole: 'fit', cutType: 'horizon', direction: 'front', shot: 'full',
  colorId: 'base', outerClosureState: 'open', ...extra });
const closure = (kind, range) => ({ version: 1, kind, range, secondary: null,
  evidenceSlots: ['Front'], note: null });

test('견적은 여밈 없는 아우터의 저장 선택을 보존하고 같은 결과를 한 장으로 센다', () => {
  const blocks = states.map(state => block(state, { outerClosureState: state }));
  const saved = structuredClone(blocks);
  assert.equal(uniqueGenerationCutCount(blocks, { clothingType: 'outer', frontClosure: closure('none', null) }), 1);
  assert.deepEqual(blocks, saved);
});

test('두 칸 아우터는 가능한 닫힘과 열림 두 장으로 견적한다', () => {
  const blocks = states.map(state => block(state, { outerClosureState: state }));
  assert.equal(uniqueGenerationCutCount(blocks, { clothingType: 'outer', frontClosure: closure('button', 'binary') }), 2);
});

test('판단 불가와 예전 분석은 다섯 가지 요청을 그대로 센다', () => {
  const blocks = states.map(state => block(state, { outerClosureState: state }));
  for (const frontClosure of [null, closure('unknown', 'graded'), closure('zipper', 'graded')]) {
    assert.equal(uniqueGenerationCutCount(blocks, { clothingType: 'outer', frontClosure }), 5);
  }
});

test('확정 GPT 요청은 여밈 판정으로 합쳐지지 않고 명시적으로 제외된 예시는 합쳐진다', () => {
  const blocks = ['closed', 'open'].map(state => block(state, { cutType: 'styling',
    shot: 'full', refScope: 'all', exampleId: 'ex_styling_men_outer_full_01', outerClosureState: state }));
  const options = { clothingType: 'outer', frontClosure: closure('none', null) };
  assert.equal(uniqueGenerationCutCount(blocks, options), 2);
  assert.equal(uniqueGenerationCutCount(blocks.map(b => ({ ...b,
    exampleId: 'ex_styling_men_bottom_full_snapshot_01' })), options), 1);
});

test('여밈이 맞춰져도 비슷한 컷 두 장은 각각 생성 장수에 포함된다', () => {
  const blocks = [block('original', { outerClosureState: 'closed' }), block('copy'),
    block('child', { similarSourceBlockId: 'original' }), block('copy-child', { similarSourceBlockId: 'copy' })];
  assert.equal(uniqueGenerationCutCount(blocks, { clothingType: 'outer', frontClosure: closure('none', null) }), 3);
});

test('확정 GPT 예외 ID는 원본 레지스트리의 명시적 제외와 같은 목록이다', () => {
  const registry = JSON.parse(readFileSync(new URL('../../server/app/data/example_assets.json', import.meta.url)));
  const excluded = Object.entries(registry.assets).filter(([, row]) => row.confirmedGptEligibleV1 === false)
    .map(([id]) => id).sort();
  assert.deepEqual(scopes.confirmedGptExcludedExampleIds, excluded);
});
