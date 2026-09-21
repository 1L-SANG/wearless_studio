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
