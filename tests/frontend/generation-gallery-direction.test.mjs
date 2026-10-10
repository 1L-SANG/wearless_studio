import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { poseExampleDirectionCompatible } from '../../src/lib/storyboardTaxonomy.js';
import { generationExampleSelectionPatch } from '../../src/lib/storyboardExampleSelection.js';
import { selectGenerationExamples } from '../../src/lib/generationExamples.js';

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
  assert.doesNotMatch(moodGuide, /sb-expage-label/);
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

test('갤러리는 방향 분류 없이 예시 사진 목록을 보여준다', () => {
  // 2026-10-10 오너 결정: 방향 분류 없이 3×2 넘기기를 복원한다.
  const board = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
  const moodGuide = board.slice(board.indexOf('function MoodGuide'), board.indexOf('function Inspector'));
  assert.match(moodGuide, /galleryPage/);
  assert.match(moodGuide, /sb-expage-hit/);
  assert.match(moodGuide, /paginateGenerationGalleryItems/);
  assert.doesNotMatch(moodGuide, /sb-exsection|sb-expage-label/);
  assert.match(moodGuide, /paginateGenerationGalleryItems\(examples\)/);
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

test('제품 디테일은 고른 원본 면을 유지하고 사선 표현을 만들지 않는다', () => {
  const patch = generationExampleSelectionPatch(
    { id: 'b', cutType: 'product', shot: 'detail', direction: 'front' },
    { id: 'd', cutType: 'product', shot: 'detail', direction: 'back' },
  ).patch;
  assert.equal(patch.direction, undefined);
  assert.equal(patch.sideStyle, undefined, '제품컷에는 sideStyle 을 심지 않는다');
});
