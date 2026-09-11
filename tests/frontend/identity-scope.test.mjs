import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  IDENTITY_SCOPE_CASES,
  blockAllowedForModel,
  filterExamplesForModel,
  filterSpaceSetsForModel,
  identityKindOf,
  scopeOfBlock,
  scopeOfSpaceSet,
} from '../../src/lib/identityScope.js';

// 컷 범위 — 실제/가상 모델이 만들 수 있는 컷을 가른다. **판정 규칙은 서버**에 있고, 이 파일은
// 서버가 내보낸 규칙을 평가만 한다. 아래 첫 테스트가 "같은 답이 나오는가"를 통째로 고정한다.

const PROFILE_BLOCK = {
  cutType: 'styling', direction: 'front', shot: 'full', refScope: 'all', pose: 'auto',
  exampleId: 'ex_styling_men_top_full_snapshot_03',
};
const STUDIO_SET = 'set_horizon_men_bottom_hatchingroom_2161_prod01';

test('서버가 계산한 모든 대조 케이스를 그대로 재현한다', () => {
  assert.ok(IDENTITY_SCOPE_CASES.length > 100, '대조 케이스가 비었다');
  const mismatches = IDENTITY_SCOPE_CASES
    .filter((item) => scopeOfBlock(item.block) !== item.scope)
    .slice(0, 3);
  assert.deepEqual(mismatches, []);
});

test('확정 프로필을 요구하는 컷과 스튜디오 공간세트는 가상 전용', () => {
  assert.equal(scopeOfBlock(PROFILE_BLOCK), 'virtual');
  assert.equal(scopeOfSpaceSet(STUDIO_SET), 'virtual');
});

test('그 밖의 컷은 both — 모르는 입력도 막지 않는다', () => {
  assert.equal(scopeOfBlock({ ...PROFILE_BLOCK, direction: 'back' }), 'both');
  assert.equal(scopeOfBlock({ cutType: 'product' }), 'both');
  assert.equal(scopeOfBlock(null), 'both');
  assert.equal(scopeOfSpaceSet('set-no-such'), 'both');
});

test('실제 모델은 가상 전용 컷을 만들 수 없다', () => {
  const real = identityKindOf(true);
  const virtual = identityKindOf(false);
  assert.equal(blockAllowedForModel(PROFILE_BLOCK, real), false);
  assert.equal(blockAllowedForModel(PROFILE_BLOCK, virtual), true);
  assert.equal(blockAllowedForModel({ ...PROFILE_BLOCK, direction: 'back' }, real), true);
  assert.equal(blockAllowedForModel({ cutType: 'product' }, real), true);
});

test('선택지에서도 빠진다 — 갤러리·자동 구성 공용 필터', () => {
  const examples = [{ id: PROFILE_BLOCK.exampleId }, { id: 'ex_product_top_ghost_01' }];
  const shape = { cutType: 'styling', direction: 'front', shot: 'full', refScope: 'all', pose: 'auto' };
  assert.equal(filterExamplesForModel(examples, 'real', shape).length, 0);
  assert.equal(filterExamplesForModel(examples, 'virtual', shape).length, 2);
  // 뒷모습 컷이면 실제 모델도 고를 수 있다(범위는 컷 모양에 달려 있다).
  assert.equal(filterExamplesForModel(examples, 'real', { ...shape, direction: 'back' }).length, 2);

  const sets = [{ id: STUDIO_SET }, { id: 'set-01-indoor' }];
  assert.deepEqual(filterSpaceSetsForModel(sets, 'real').map((x) => x.id), ['set-01-indoor']);
  assert.equal(filterSpaceSetsForModel(sets, 'virtual').length, 2);
  // 모델 종류를 모르면 아무것도 빼지 않는다(로딩 중 화면이 비지 않게).
  assert.equal(filterExamplesForModel(examples, null, shape).length, 2);
  assert.equal(filterSpaceSetsForModel(sets, null).length, 2);
});

test('콘티보드가 범위를 실제로 쓴다(배선 고정)', () => {
  const board = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
  assert.match(board, /identityKind/);
  assert.match(board, /filterSpaceSetsForModel\(/);
  assert.match(board, /filterExamplesForModel\(/);
  assert.match(board, /이 모델로는 만들 수 없는 컷/);
  assert.match(board, /outOfScope=\{block\.source === 'ai' && !blockAllowedForModel\(block, identityKind\)\}/);

  const placement = readFileSync(new URL('../../src/lib/storyboardEntryPlacement.js', import.meta.url), 'utf8');
  assert.match(placement, /filterSpaceSetsForModel\(/);
  const examples = readFileSync(new URL('../../src/lib/generationExamples.js', import.meta.url), 'utf8');
  assert.match(examples, /filterExamplesForModel\(/);
});
