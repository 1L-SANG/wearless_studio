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
import * as identityScope from '../../src/lib/identityScope.js';
import SCOPES from '../../src/data/identityScopes.json' with { type: 'json' };

// 컷 범위 — 실제/가상 모델이 만들 수 있는 컷을 가른다. **판정 규칙은 서버**에 있고, 이 파일은
// 서버가 내보낸 규칙을 평가만 한다. 아래 첫 테스트가 "같은 답이 나오는가"를 통째로 고정한다.

const PROFILE_BLOCK = {
  cutType: 'styling', direction: 'front', shot: 'full', refScope: 'all', pose: 'auto',
  exampleId: 'ex_styling_men_top_full_snapshot_03',
};
const STUDIO_SET = 'set_horizon_men_bottom_hatchingroom_2161_prod01';

// 2026-09-28 product 까지 열려 기본 정책(서버 REAL_ALLOWED_SECTION_ROLES)으로 막힌 섹션은 없다.
// 섹션 규칙 장치는 남아 있으니, 서버 테스트의 monkeypatch 처럼 규칙 표의 섹션 목록을
// 09-26 값(product 제외)으로 잠시 좁혀 거절 경로를 지킨다. identityScope.js 는 같은 JSON
// 모듈 객체를 평가 시점에 읽으므로 여기서 바꾼 값이 그대로 반영된다.
const NARROW_POLICY = ['hooking', 'styling', 'studio'];

function withNarrowPolicy(fn) {
  const rule = SCOPES.rules.find((r) => r.when && Array.isArray(r.when.sectionNotIn));
  assert.ok(rule, '섹션 규칙이 규칙 표에서 사라졌다');
  const original = rule.when.sectionNotIn;
  rule.when.sectionNotIn = NARROW_POLICY;
  try {
    return fn();
  } finally {
    rule.when.sectionNotIn = original;
  }
}

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

const PRODUCT_BLOCKS = [{ sectionRole: 'product', cutType: 'product' }, { cutType: 'product' },
  { cutType: 'product', shot: 'detail' }, { sectionRole: 'product', cutType: 'styling' }];

test('모든 섹션이 실제 모델에게 열려 있다 — 섹션 규칙만으로 가상 전용이 되는 컷은 없다', () => {
  // 2026-09-14 studio, 2026-09-25 styling, 2026-09-26 hooking, 2026-09-28 product 를 열었다.
  const sectionRule = SCOPES.rules.find((r) => r.when && Array.isArray(r.when.sectionNotIn));
  assert.deepEqual(sectionRule.when.sectionNotIn, ['hooking', 'styling', 'studio', 'product']);
  for (const block of [{ sectionRole: 'studio', cutType: 'horizon' },
    { sectionRole: 'styling', cutType: 'styling' }, { cutType: 'styling' }, { cutType: 'mirror' },
    { sectionRole: 'hooking', cutType: 'styling' }, { sectionRole: 'hooking', cutType: 'horizon' },
    { contentRole: 'hero', cutType: 'styling' }, ...PRODUCT_BLOCKS]) {
    assert.equal(scopeOfBlock(block), 'both', JSON.stringify(block));
  }
});

test('정책을 좁히면 열린 섹션 밖은 가상 전용이 된다 — 섹션 규칙 장치', () => {
  withNarrowPolicy(() => {
    // 착용 컷 모양이어도 섹션이 정책 밖이면 막힌다.
    for (const block of PRODUCT_BLOCKS) {
      assert.equal(scopeOfBlock(block), 'virtual', JSON.stringify(block));
    }
    assert.equal(scopeOfBlock({ sectionRole: 'studio', cutType: 'horizon' }), 'both');
  });
  // 원래 정책으로 되돌아온다.
  assert.equal(scopeOfBlock({ sectionRole: 'product', cutType: 'product' }), 'both');
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
  // hooking 섹션도 같다(2026-09-26) — 모양이 괜찮으면 통과, 확정 프로필 모양은 여전히 가상 전용.
  assert.equal(blockAllowedForModel({ ...PROFILE_BLOCK, direction: 'back', sectionRole: 'hooking' }, real), true);
  assert.equal(blockAllowedForModel({ ...PROFILE_BLOCK, sectionRole: 'hooking' }, real), false);
  // product 섹션도 열렸다(2026-09-28) — 제품 컷은 통과, 확정 프로필 모양은 여전히 가상 전용.
  assert.equal(blockAllowedForModel({ cutType: 'product' }, real), true);
  assert.equal(blockAllowedForModel({ ...PROFILE_BLOCK, direction: 'back', sectionRole: 'product' }, real), true);
  assert.equal(blockAllowedForModel({ ...PROFILE_BLOCK, sectionRole: 'product' }, real), false);
  // 정책 밖 섹션은 모양과 무관하게 막힌다(정책을 좁혀 확인).
  withNarrowPolicy(() => {
    assert.equal(blockAllowedForModel({ cutType: 'product' }, real), false);
    assert.equal(blockAllowedForModel({ ...PROFILE_BLOCK, direction: 'back', sectionRole: 'product' }, real), false);
    assert.equal(blockAllowedForModel({ cutType: 'product' }, virtual), true);
  });
});

test('막힌 이유가 셀러에게 갈린다 — 문구는 서버 규칙 표에서 온다', () => {
  const real = identityKindOf(true);
  assert.equal(rejectionOfBlock({ sectionRole: 'product', cutType: 'product' }, real), null);   // 2026-09-28 열림
  assert.equal(rejectionOfBlock({ sectionRole: 'hooking', cutType: 'styling' }, real), null);   // 2026-09-26 열림
  assert.equal(rejectionOfSection('studio', real), null);
  assert.equal(rejectionOfSection('styling', real), null);   // 2026-09-25 열림
  assert.equal(rejectionOfSection('hooking', real), null);   // 2026-09-26 열림
  assert.equal(rejectionOfSection('product', real), null);   // 2026-09-28 열림
  withNarrowPolicy(() => {
    const outside = rejectionOfBlock({ sectionRole: 'product', cutType: 'product' }, real);
    assert.equal(outside.code, 'real_model_studio_only');
    assert.equal(outside.message, '실제 모델로는 만들 수 없는 섹션이에요.');
    assert.equal(rejectionOfSection('product', real).code, 'real_model_studio_only');
    assert.equal(rejectionOfSection('product', identityKindOf(false)), null);
    assert.equal(rejectionOfSection('studio', real), null);
  });
  // styling 섹션의 확정 프로필 예시는 섹션이 아니라 예시 때문에 막힌다 — 섹션 사유(코드)가
  // 없으니 카드는 일반 문구('이 모델로는 만들 수 없는 컷')로 떨어진다.
  const profileInStyling = { ...PROFILE_BLOCK, sectionRole: 'styling' };
  assert.equal(blockAllowedForModel(profileInStyling, real), false);
  assert.equal(rejectionOfBlock(profileInStyling, real), null);
});

test('견적은 실제로 생성될 컷만 센다', () => {
  const blocks = [
    { source: 'ai', sectionRole: 'hooking', cutType: 'styling' },
    { source: 'ai', sectionRole: 'studio', cutType: 'horizon' },
    { source: 'ai', sectionRole: 'styling', cutType: 'styling' },
    { source: 'ai', sectionRole: 'product', cutType: 'product' },
    { source: 'mine' },
  ];
  // 2026-09-28 product 까지 열려 실제 모델도 전부 센다.
  assert.equal(blocksForModel(blocks, identityKindOf(true)).length, 5);
  assert.equal(blocksForModel(blocks, identityKindOf(false)).length, 5);
  assert.equal(blocksForModel(blocks, null).length, 5);                   // 모르면 안 뺀다
  // 가상 전용 예시(확정 프로필)는 여전히 실제 모델 견적에서 빠진다.
  assert.equal(blocksForModel([...blocks, { source: 'ai', ...PROFILE_BLOCK }], identityKindOf(true)).length, 5);
  // 정책을 좁히면 막힌 섹션의 컷이 빠진다.
  withNarrowPolicy(() => {
    assert.equal(blocksForModel(blocks, identityKindOf(true)).length, 4);
    assert.equal(blocksForModel(blocks, identityKindOf(false)).length, 5);
  });
});

test('지원하지 않는 AI 컷이 있으면 다음 단계로 넘기기 전에 그 컷을 찾는다', () => {
  assert.equal(typeof identityScope.firstUnsupportedAiBlock, 'function');
  const blocks = [
    { id: 'mine', source: 'mine', sectionRole: 'hooking' },
    { id: 'valid', source: 'ai', sectionRole: 'studio', cutType: 'horizon' },
    { id: 'blocked', source: 'ai', sectionRole: 'product', cutType: 'product' },
  ];
  // 2026-09-28 product 가 열려 기본 정책에서는 막히는 컷이 없다.
  assert.equal(identityScope.firstUnsupportedAiBlock(blocks, identityKindOf(true)), null);
  // 가상 전용 예시(확정 프로필)는 여전히 걸린다.
  const withProfile = [...blocks, { id: 'profile', source: 'ai', ...PROFILE_BLOCK }];
  assert.equal(identityScope.firstUnsupportedAiBlock(withProfile, identityKindOf(true))?.id, 'profile');
  assert.equal(identityScope.firstUnsupportedAiBlock(withProfile, identityKindOf(false)), null);
  // 정책을 좁히면 막힌 섹션의 컷을 찾는다.
  withNarrowPolicy(() => {
    assert.equal(identityScope.firstUnsupportedAiBlock(blocks, identityKindOf(true))?.id, 'blocked');
    assert.equal(identityScope.firstUnsupportedAiBlock(blocks, identityKindOf(false)), null);
    assert.equal(identityScope.firstUnsupportedAiBlock(blocks, null), null);
  });
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
