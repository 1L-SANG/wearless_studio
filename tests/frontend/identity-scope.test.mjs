import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  IDENTITY_SCOPE_CASES,
  blockAllowedForModel,
  filterExamplesForModel,
  filterSpaceSetsForModel,
  identityKindOf,
  blocksForModel,
  rejectionOfBlock,
  rejectionOfSection,
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

test('studio·styling 섹션 밖은 전부 가상 전용 — 실제 모델은 스튜디오·스타일링 컷만 만든다', () => {
  // 2026-09-14 사용자 결정으로 studio 를 열었고, 2026-09-25 결정으로 styling 을 더했다.
  for (const block of [{ sectionRole: 'studio', cutType: 'horizon' },
    { sectionRole: 'styling', cutType: 'styling' }, { cutType: 'styling' }, { cutType: 'mirror' }]) {
    assert.equal(scopeOfBlock(block), 'both', JSON.stringify(block));
  }
  // hooking·product 는 여전히 막힌다.
  for (const block of [{ sectionRole: 'hooking', cutType: 'styling' },
    { sectionRole: 'hooking', cutType: 'horizon' },
    { sectionRole: 'product', cutType: 'product' }, { cutType: 'product' }]) {
    assert.equal(scopeOfBlock(block), 'virtual', JSON.stringify(block));
  }
});

test('그 밖의 컷은 both — 모르는 입력도 막지 않는다', () => {
  // 섹션을 알 수 없는 입력은 막지 않는다(서버 identity_scope.studio_only_block 과 같은 규칙).
  assert.equal(scopeOfBlock({ ...PROFILE_BLOCK, direction: 'back', sectionRole: 'studio' }), 'both');
  assert.equal(scopeOfBlock(null), 'both');
  assert.equal(scopeOfSpaceSet('set-no-such'), 'both');
});

test('실제 모델은 가상 전용 컷을 만들 수 없다', () => {
  const real = identityKindOf(true);
  const virtual = identityKindOf(false);
  assert.equal(blockAllowedForModel(PROFILE_BLOCK, real), false);
  assert.equal(blockAllowedForModel(PROFILE_BLOCK, virtual), true);
  // studio 섹션 안이면 컷 모양으로 갈린다 — 확정 프로필 모양만 가상 전용.
  const studio = { sectionRole: 'studio', cutType: 'horizon', direction: 'front', shot: 'full' };
  assert.equal(blockAllowedForModel(studio, real), true);
  assert.equal(blockAllowedForModel({ ...studio, spaceGroupId: `ssg1__${STUDIO_SET}__sg_1` }, real), false);
  // styling 섹션도 같다(2026-09-25) — 모양이 괜찮으면 통과, 확정 프로필 모양은 여전히 가상 전용.
  const styling = { sectionRole: 'styling', cutType: 'styling', direction: 'front', shot: 'full' };
  assert.equal(blockAllowedForModel(styling, real), true);
  assert.equal(blockAllowedForModel({ ...PROFILE_BLOCK, direction: 'back' }, real), true);
  assert.equal(blockAllowedForModel({ ...PROFILE_BLOCK, sectionRole: 'styling' }, real), false);
  // 그 밖의 섹션(hooking·product)은 모양과 무관하게 막힌다
  assert.equal(blockAllowedForModel({ cutType: 'product' }, real), false);
  assert.equal(blockAllowedForModel({ ...PROFILE_BLOCK, direction: 'back', sectionRole: 'hooking' }, real), false);
});

test('막힌 이유가 셀러에게 갈린다 — 문구는 서버 규칙 표에서 온다', () => {
  const real = identityKindOf(true);
  const outside = rejectionOfBlock({ sectionRole: 'hooking', cutType: 'styling' }, real);
  assert.equal(outside.code, 'real_model_studio_only');
  assert.match(outside.message, /스튜디오/);
  assert.match(outside.message, /스타일링/);
  assert.equal(rejectionOfSection('studio', real), null);
  assert.equal(rejectionOfSection('styling', real), null);   // 2026-09-25 열림
  assert.equal(rejectionOfSection('hooking', real).code, 'real_model_studio_only');
  assert.equal(rejectionOfSection('product', real).code, 'real_model_studio_only');
  assert.equal(rejectionOfSection('product', identityKindOf(false)), null);
  // styling 섹션의 확정 프로필 예시는 섹션이 아니라 예시 때문에 막힌다 — 섹션 사유(코드)가
  // 없으니 카드는 일반 문구('이 모델로는 만들 수 없는 컷')로 떨어진다.
  const profileInStyling = { ...PROFILE_BLOCK, sectionRole: 'styling' };
  assert.equal(blockAllowedForModel(profileInStyling, real), false);
  assert.equal(rejectionOfBlock(profileInStyling, real), null);
});

test('견적은 실제로 생성될 컷만 센다', () => {
  const blocks = [
    { source: 'ai', sectionRole: 'studio', cutType: 'horizon' },
    { source: 'ai', sectionRole: 'styling', cutType: 'styling' },
    { source: 'ai', sectionRole: 'hooking', cutType: 'styling' },
    { source: 'mine' },
  ];
  assert.equal(blocksForModel(blocks, identityKindOf(true)).length, 3);   // studio + styling + 내 이미지
  assert.equal(blocksForModel(blocks, identityKindOf(false)).length, 4);
  assert.equal(blocksForModel(blocks, null).length, 4);                   // 모르면 안 뺀다
});

test('선택지에서도 빠진다 — 갤러리·자동 구성 공용 필터', () => {
  const examples = [{ id: PROFILE_BLOCK.exampleId }, { id: 'ex_product_top_ghost_01' }];
  const shape = { cutType: 'styling', direction: 'front', shot: 'full', refScope: 'all', pose: 'auto' };
  assert.equal(filterExamplesForModel(examples, 'real', shape).length, 0);
  assert.equal(filterExamplesForModel(examples, 'virtual', shape).length, 2);
  // studio 섹션 안에서는 컷 모양이 판정에 들어간다(범위는 여전히 모양에 달려 있다).
  const studioShape = { ...shape, cutType: 'horizon', sectionRole: 'studio' };
  assert.equal(filterExamplesForModel(examples, 'real', studioShape).length, 2);

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
  assert.match(board, /outOfScope=\{block\.source === 'ai' && !blockAllowedForModel\(block, identityKind\)/);
  // 막힌 이유가 둘로 갈린다 — 카드 문구도 그걸 따라간다(스튜디오 전용 vs 가상 전용 예시).
  assert.match(board, /rejectionOfBlock\(block, identityKind\)/);
  assert.match(board, /rejectionOfSection\(sectionRole, identityKind\)/);
  // 견적도 서버 예약과 같은 수를 본다.
  assert.match(board, /uniqueGenerationCutCount\(blocksForModel\(blocks, identityKind\)\)/);

  const placement = readFileSync(new URL('../../src/lib/storyboardEntryPlacement.js', import.meta.url), 'utf8');
  assert.match(placement, /filterSpaceSetsForModel\(/);
  const examples = readFileSync(new URL('../../src/lib/generationExamples.js', import.meta.url), 'utf8');
  assert.match(examples, /filterExamplesForModel\(/);
});
