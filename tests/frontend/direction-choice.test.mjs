/* 방향 칩(화면 4개) ↔ 컷 스펙(서버 3개) 변환 계약.
 *
 * 2026-09-21: 같은 direction="side" 주문이 두 가지 다른 그림이 된다.
 *   사선     얼굴 패스(LoRA)가 그린다 — DIR:side_identity, 두 눈 보임
 *   옆모습   각도 교체가 등록 실사진 머리를 붙인다 — 완전 옆모습
 * 셀러가 그걸 고를 수 있어야 해서 화면 칩을 4개로 갈랐다. 서버 계약은 셋 그대로다.
 */
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';

import { poseExampleDirectionCompatible } from '../../src/lib/storyboardTaxonomy.js';
import { generationExampleSelectionPatch } from '../../src/lib/storyboardExampleSelection.js';
import {
  groupGenerationExamplesByDirection,
  repickExampleForDirection,
  selectGenerationExamples,
} from '../../src/lib/generationExamples.js';
import {
  DIRECTION_CHOICES,
  directionChoiceFromSpec,
  specFromDirectionChoice,
} from '../../src/lib/directionChoice.js';

test('화면 칩 네 개가 카탈로그와 같다', () => {
  const db = readFileSync(new URL('../../src/mock/db.js', import.meta.url), 'utf8');
  const block = /directions: \[([\s\S]*?)\n  \]/.exec(db)?.[1];
  assert.ok(block, '카탈로그 directions 를 못 찾았다');
  const values = [...block.matchAll(/value: '([a-zA-Z]+)'/g)].map((m) => m[1]);
  assert.deepEqual(values, [...DIRECTION_CHOICES]);
});

test('서버로 나가는 값은 front/side/back 셋뿐이다', () => {
  const directions = DIRECTION_CHOICES.map((c) => specFromDirectionChoice(c).direction);
  assert.deepEqual([...new Set(directions)].sort(), ['back', 'front', 'side']);
});

test('사선과 옆모습이 같은 side 로 가되 sideStyle 로 갈린다', () => {
  assert.deepEqual(specFromDirectionChoice('threeQuarter'), { direction: 'side', sideStyle: 'threeQuarter' });
  assert.deepEqual(specFromDirectionChoice('profile'), { direction: 'side', sideStyle: 'profile' });
});

test('side 가 아닌 컷은 sideStyle 을 비운다', () => {
  // 남겨 두면 방향을 바꾼 카드에 옛 값이 따라다닌다(서버 canonicalize 와 같은 규칙).
  assert.equal(specFromDirectionChoice('front').sideStyle, null);
  assert.equal(specFromDirectionChoice('back').sideStyle, null);
});

test('모르는 값은 정면으로 떨어진다', () => {
  // ★ 조용히 사선으로 새지 않게 — 사선은 얼굴 패스, 기본은 정면이어야 한다.
  for (const bad of ['', null, undefined, 'side', 'diagonal', 'left']) {
    assert.deepEqual(specFromDirectionChoice(bad), { direction: 'front', sideStyle: null }, String(bad));
  }
});

test('왕복이 유지된다', () => {
  for (const choice of DIRECTION_CHOICES) {
    assert.equal(directionChoiceFromSpec(specFromDirectionChoice(choice)), choice, choice);
  }
});

test('sideStyle 없는 옛 side 카드는 옆모습으로 읽는다', () => {
  // 서버 기본값과 같아야 한다 — cut_generator 는 sideStyle 이 없으면 각도 교체로 보낸다.
  assert.equal(directionChoiceFromSpec({ direction: 'side' }), 'profile');
  assert.equal(directionChoiceFromSpec({ direction: 'side', sideStyle: null }), 'profile');
});

test('방향이 없는 카드는 정면으로 읽는다', () => {
  for (const block of [{}, null, undefined, { direction: 'mirror' }]) {
    assert.equal(directionChoiceFromSpec(block), 'front');
  }
});

test('예시 방향 호환은 side 의 하위 갈래까지 본다', () => {
  // 사선 카드가 90도 옆모습 사진의 포즈를 물려받으면 베이스가 90도로 나오고, 사선은
  // 각도 교체를 건너뛰므로 얼굴 패스가 90도 머리에 얼굴을 그린다. direction 만으로는
  // 둘이 구분되지 않는다 — 둘 다 'side' 다.
  const profile = { cutType: 'horizon', direction: 'side', sideStyle: 'profile' };
  const threeQuarter = { cutType: 'horizon', direction: 'side', sideStyle: 'threeQuarter' };
  const card = (sideStyle) => ({ cutType: 'horizon', direction: 'side', sideStyle });

  assert.equal(poseExampleDirectionCompatible(threeQuarter, card('threeQuarter')), true);
  assert.equal(poseExampleDirectionCompatible(profile, card('profile')), true);
  assert.equal(poseExampleDirectionCompatible(profile, card('threeQuarter')), false);
  assert.equal(poseExampleDirectionCompatible(threeQuarter, card('profile')), false);
});

test('sideStyle 없는 옛 side 예시는 옆모습으로 읽는다', () => {
  // 이 값이 생기기 전 발행분은 전부 완전 옆모습이었다. 서버 _side_style_of 와 같은 기본값.
  const legacy = { cutType: 'horizon', direction: 'side' };
  assert.equal(poseExampleDirectionCompatible(legacy, {
    cutType: 'horizon', direction: 'side', sideStyle: 'profile',
  }), true);
  assert.equal(poseExampleDirectionCompatible(legacy, {
    cutType: 'horizon', direction: 'side', sideStyle: 'threeQuarter',
  }), false);
  // sideStyle 을 아예 안 넘긴 호출부도 옆모습으로 본다.
  assert.equal(poseExampleDirectionCompatible(legacy, {
    cutType: 'horizon', direction: 'side',
  }), true);
});

test('front·back 은 sideStyle 과 무관하다', () => {
  for (const direction of ['front', 'back']) {
    assert.equal(poseExampleDirectionCompatible(
      { cutType: 'horizon', direction, sideStyle: null },
      { cutType: 'horizon', direction, sideStyle: 'threeQuarter' },
    ), true, direction);
  }
});

test('갤러리·자동배정은 카드와 같은 방향 가족을 앞에 둔다 — 숨기지는 않는다', () => {
  // 2026-09-22 오너: 셀러는 분위기 예시에서 아무거나 고를 수 있어야 한다. 같은 방향이 먼저 오면
  // 자동배정(앞에서 셋)도 자연히 같은 방향을 고른다.
  const ex = (id, over = {}) => ({
    id, cutType: 'horizon', shot: 'full', gender: 'men', applicableClothingTypes: ['top'],
    variants: ['all'], rank: 1, direction: 'front', sideStyle: null, thumb: `t/${id}`, ...over,
  });
  const catalog = [
    ex('front'), ex('tq', { direction: 'side', sideStyle: 'threeQuarter' }),
    ex('prof', { direction: 'side', sideStyle: 'profile' }), ex('back', { direction: 'back' }),
  ];
  const pick = (direction, sideStyle = null) => selectGenerationExamples(catalog, {
    cutType: 'horizon', shot: 'full', clothingType: 'top', gender: 'men', direction, sideStyle,
  }).map((e) => e.id);
  assert.equal(pick('front')[0], 'front');
  assert.equal(pick('side', 'threeQuarter')[0], 'tq');
  assert.equal(pick('side', 'profile')[0], 'prof');
  assert.equal(pick('back')[0], 'back');
  // sideStyle 없는 side 카드는 옆모습이다 — 서버 _side_style_of 와 같은 기본값.
  assert.equal(pick('side')[0], 'prof');
  // 나머지도 전부 남아 있다 — 필터가 아니라 정렬이다.
  assert.deepEqual([...pick('back')].sort(), ['back', 'front', 'prof', 'tq']);
});

test('같은 방향이 0장이어도 갤러리는 비지 않는다', () => {
  const ex = (id, over = {}) => ({
    id, cutType: 'horizon', shot: 'full', gender: 'men', applicableClothingTypes: ['top'],
    variants: ['all'], rank: 1, direction: 'front', sideStyle: null, thumb: `t/${id}`, ...over,
  });
  const catalog = [ex('a'), ex('b', { direction: 'back' })];
  const shown = selectGenerationExamples(catalog, {
    cutType: 'horizon', shot: 'full', clothingType: 'top', gender: 'men', direction: 'side', sideStyle: 'threeQuarter',
  });
  assert.deepEqual(shown.map((e) => e.id), ['a', 'b']);
});

test('방향을 바꾸면 자동배정 예시를 새 방향으로 갈아 끼우고, 셀러가 고른 예시는 둔다', () => {
  const ex = (id, over = {}) => ({
    id, cutType: 'horizon', shot: 'full', gender: 'men', applicableClothingTypes: ['top'],
    variants: ['all'], rank: 1, direction: 'front', sideStyle: null, thumb: `t/${id}`, ...over,
  });
  const catalog = [ex('front'), ex('tq', { direction: 'side', sideStyle: 'threeQuarter' })];
  const base = { id: 'blk', source: 'ai', cutType: 'horizon', shot: 'full', direction: 'front', exampleId: 'front', thumb: 't/front' };
  const opts = { clothingType: 'top', gender: 'men', direction: 'side', sideStyle: 'threeQuarter' };

  const auto = repickExampleForDirection({ ...base, exampleSelectionOrigin: 'auto' }, catalog, opts);
  assert.equal(auto.exampleId, 'tq');
  assert.equal(auto.thumb, 't/tq');
  assert.equal(auto.baseThumb, 't/front');

  // 이미 맞는 예시면 손대지 않는다.
  assert.equal(repickExampleForDirection({ ...base, exampleSelectionOrigin: 'auto', exampleId: 'tq' }, catalog, opts), null);
  // 셀러가 직접 고른 예시는 그대로 — 그건 "변경됨" 표시가 맞는 자리다.
  assert.equal(repickExampleForDirection({ ...base, exampleSelectionOrigin: 'user' }, catalog, opts), null);
  // 장소세트 멤버는 세트가 정한 자리라 건드리지 않는다.
  assert.equal(repickExampleForDirection({ ...base, exampleSelectionOrigin: 'auto', spaceGroupId: 'g1' }, catalog, opts), null);
});

test('갤러리는 방향 묶음으로 잘린다 — 카드 방향이 첫 묶음, 나머지도 뒤에 남는다', () => {
  // 2026-09-22 오너: "정면·사선·옆모습·뒷면별로 실제 사진 나오게 하고 거기서 고를 수 있게."
  // 정렬만 해서는 화면이 그대로라 방향이 보이지 않았다 — 묶음마다 이름을 붙인다.
  const ex = (id, over = {}) => ({
    id, cutType: 'horizon', shot: 'full', gender: 'men', applicableClothingTypes: ['top'],
    variants: ['all'], rank: 1, direction: 'front', sideStyle: null, thumb: `t/${id}`, ...over,
  });
  const list = [
    ex('f1'), ex('f2'), ex('tq', { direction: 'side', sideStyle: 'threeQuarter' }),
    ex('prof', { direction: 'side', sideStyle: 'profile' }), ex('back', { direction: 'back' }),
    ex('none', { direction: null }),
  ];
  const sections = groupGenerationExamplesByDirection(list, { direction: 'side', sideStyle: 'threeQuarter' });
  assert.deepEqual(sections.map((section) => section.label), ['사선', '정면', '옆모습', '뒷면', '기타']);
  assert.deepEqual(sections[0].examples.map((e) => e.id), ['tq']);
  assert.deepEqual(sections[1].examples.map((e) => e.id), ['f1', 'f2']);
  // 라벨이 안 붙은 예시도 숨기지 않는다 — 마지막 '기타' 묶음에 남는다.
  assert.deepEqual(sections.at(-1).examples.map((e) => e.id), ['none']);
  // 전체 장수는 그대로 — 묶는 것이지 거르는 것이 아니다.
  assert.equal(sections.reduce((sum, section) => sum + section.examples.length, 0), list.length);
});

test('방향 묶음은 sideStyle 없는 side 를 옆모습으로 읽는다 — 서버 기본값과 같다', () => {
  const ex = (id, over = {}) => ({
    id, cutType: 'horizon', shot: 'full', gender: 'men', applicableClothingTypes: ['top'],
    variants: ['all'], rank: 1, direction: 'front', sideStyle: null, thumb: `t/${id}`, ...over,
  });
  const sections = groupGenerationExamplesByDirection(
    [ex('side_unlabelled', { direction: 'side' })], { direction: 'side', sideStyle: null },
  );
  assert.deepEqual(sections.map((section) => section.label), ['옆모습']);
});

test('방향이 없는 컷(제품)은 묶음이 하나뿐이라 라벨이 붙지 않는다', () => {
  const ex = (id) => ({
    id, cutType: 'product', shot: 'detail', gender: null, applicableClothingTypes: ['top'],
    variants: ['all'], rank: 1, direction: 'front', sideStyle: null, thumb: `t/${id}`,
  });
  const sections = groupGenerationExamplesByDirection([ex('p1'), ex('p2')], {});
  assert.equal(sections.length, 1);
});

test('분위기 예시 칸은 잠기지 않는다 — 회색 비활성·not-allowed 가 없다', () => {
  // 2026-09-22 오너: 포즈 자산이 없거나 방향이 달라도 셀러가 고를 수 있어야 한다.
  // 회색으로 덮으면 왜 못 고르는지 알 수 없었고, 실제로는 눌리는 칸도 있어 더 헷갈렸다.
  const board = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
  const moodGuide = board.slice(board.indexOf('function MoodGuide'), board.indexOf('function Inspector'));
  assert.doesNotMatch(moodGuide, /disabled=\{poseUnavailable\}/);
  assert.doesNotMatch(moodGuide, /sb-excell\$\{[^}]*unavailable/);
  assert.doesNotMatch(moodGuide, /moodonly/, '정면이 아닌 방향이라고 갤러리를 흐리게 덮지 않는다');
  const css = readFileSync(new URL('../../src/styles/features.css', import.meta.url), 'utf8');
  assert.doesNotMatch(css, /\.sb-exgallery\.moodonly/);
  assert.doesNotMatch(css, /\.sb-excell\.unavailable/);
  // 묶음 이름 자리는 있어야 한다.
  assert.match(moodGuide, /sb-expage-label/);
  assert.match(css, /\.sb-expage-label/);
});

test('스튜디오 갤러리는 같은 방향에 낱개가 있으면 세트 멤버를 빼서 같은 사진을 두 번 안 보여준다', () => {
  // 2026-09-22 오너 실측: 남성 미디움 뒷면 칸에 새 낱개 예시와, 그 예시를 뽑을 때 베이스로 쓴
  // 공간 묶음 멤버가 나란히 떴다 — 같은 사람·같은 옷·같은 포즈라 두 장이 똑같아 보였다.
  const ex = (id, over = {}) => ({
    id, cutType: 'horizon', shot: 'medium', gender: 'men', applicableClothingTypes: ['top'],
    variants: ['all'], rank: 1, direction: 'front', sideStyle: null, thumb: `t/${id}`, ...over,
  });
  const catalog = [
    ex('flat_front'), ex('flat_back', { direction: 'back', rank: 2 }),
    ex('set_front', { setOnly: true }), ex('set_back', { direction: 'back', setOnly: true }),
    ex('set_profile', { direction: 'side', setOnly: true }),
  ];
  const shown = selectGenerationExamples(catalog, {
    cutType: 'horizon', shot: 'medium', clothingType: 'top', gender: 'men',
    direction: 'front', sideStyle: null, appendSetOnly: true,
  }).map((e) => e.id);
  // 정면·뒷면은 낱개가 덮으므로 세트 멤버가 빠지고, 낱개가 없는 옆모습은 그대로 남는다.
  assert.deepEqual(shown, ['flat_front', 'flat_back', 'set_profile']);
});

test('스냅(스타일링)은 장소가 다 다르므로 세트 멤버를 빼지 않는다', () => {
  const ex = (id, over = {}) => ({
    id, cutType: 'styling', shot: 'full', gender: 'women', applicableClothingTypes: ['top'],
    variants: ['all'], rank: 1, direction: 'front', sideStyle: null, thumb: `t/${id}`, ...over,
  });
  const catalog = [ex('flat_front'), ex('set_front', { setOnly: true })];
  const shown = selectGenerationExamples(catalog, {
    cutType: 'styling', shot: 'full', clothingType: 'top', gender: 'women',
    direction: 'front', sideStyle: null, appendSetOnly: true,
  }).map((e) => e.id);
  assert.deepEqual(shown, ['flat_front', 'set_front']);
});

test('갤러리는 방향 묶음을 한 화면에 쌓는다 — 페이지 넘김·점 네비가 없다', () => {
  // 2026-09-22 오너: "분류했으면 여러 창 옮기게 하지 말고 하나만 있으면 되잖아".
  const board = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
  const moodGuide = board.slice(board.indexOf('function MoodGuide'), board.indexOf('function Inspector'));
  assert.doesNotMatch(moodGuide, /galleryPage/);
  assert.doesNotMatch(moodGuide, /sb-expage-hit/);
  assert.doesNotMatch(moodGuide, /paginateGenerationGalleryItems/);
  assert.match(moodGuide, /sb-exsection/);
  // 한 방향이 길어져 다른 방향을 밀어내지 않게 묶음마다 6장으로 자른다.
  assert.match(moodGuide, /section\.examples\.slice\(0, 6\)/);
});

test('예시를 고르면 카드 방향이 그 예시의 방향이 된다 — 첫 선택도, 교체도', () => {
  // 2026-09-23 운영 실측: 셀러가 사선·옆모습·뒷면 예시를 골랐는데 13블록 전부
  // direction=front 로 저장돼 생성이 정면으로만 나갔다. 첫 선택 경로에 방향 패치가
  // 없었고, 교체 경로에는 sideStyle 이 빠져 사선이 옆모습(90도)으로 읽혔다.
  const ex = (id, direction, sideStyle = null) => ({
    id, cutType: 'horizon', shot: 'full', gender: 'men', direction, sideStyle,
    applicableClothingTypes: ['top'], variants: ['all'],
  });
  const card = (over = {}) => ({
    id: 'blk', source: 'ai', cutType: 'horizon', shot: 'full', direction: 'front', sideStyle: null, ...over,
  });
  const pick = (block, example) => generationExampleSelectionPatch(block, example).patch;

  const firstTq = pick(card(), ex('tq', 'side', 'threeQuarter'));
  assert.equal(firstTq.direction, 'side');
  assert.equal(firstTq.sideStyle, 'threeQuarter');

  const firstProfile = pick(card(), ex('pr', 'side', 'profile'));
  assert.equal(firstProfile.sideStyle, 'profile');

  const firstBack = pick(card(), ex('bk', 'back'));
  assert.equal(firstBack.direction, 'back');
  assert.equal(firstBack.sideStyle, null);

  // 교체도 같다 — 사선으로 갈아타면 sideStyle 이 따라붙는다.
  const swapped = pick(card({ exampleId: 'front1' }), ex('tq', 'side', 'threeQuarter'));
  assert.equal(swapped.direction, 'side');
  assert.equal(swapped.sideStyle, 'threeQuarter');

  // 사선 카드에서 정면 예시로 되돌리면 sideStyle 이 떨어진다 — 남으면 서버가 side 로 읽는다.
  const back = pick(card({ exampleId: 'tq', direction: 'side', sideStyle: 'threeQuarter' }), ex('fr', 'front'));
  assert.equal(back.direction, 'front');
  assert.equal(back.sideStyle, null);
});

test('제품 디테일 방향 규칙은 그대로다 — 예시 라벨이 정본, sideStyle 개념 없음', () => {
  const patch = generationExampleSelectionPatch(
    { id: 'b', cutType: 'product', shot: 'detail', direction: 'front' },
    { id: 'd', cutType: 'product', shot: 'detail', direction: 'back' },
  ).patch;
  assert.equal(patch.direction, 'back');
  assert.equal(patch.sideStyle, undefined, '제품컷에는 sideStyle 을 심지 않는다');
});
