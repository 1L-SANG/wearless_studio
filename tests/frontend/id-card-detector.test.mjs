/* 자동 셔터 판정. "카드가 가이드에 잘 맞았는가"를 프레임 한 장에서 판정하고,
   연속 N프레임 유지될 때만 찍는다 — 손이 지나가는 순간 찍히면 안 된다.

   판정은 순수 함수라 합성 픽셀로 테스트한다. 실제 카메라 없이도 회귀를 잡는다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { scoreFrame, createShutterGate } from '../../src/features/model/idCardDetector.js';
import { guideRectInFrame } from '../../src/features/model/idCardGeometry.js';

const W = 160, H = 120;

/* 가이드 자리에 밝은 사각형(=카드)을 그린 합성 프레임. inset 을 키우면 카드가 작아진다.

   카드 중앙에는 실제 신분증의 사진/칩처럼 더 어두운 작은 사각형을 하나 박아 둔다.
   완전히 균일한 색은 실제 카메라 프레임에서 나오지 않는다: 그 경우 카드 내부에
   밝기 차가 전혀 없어 sharp(경계 대비)가 항상 0이 되어버려, 반듯하고 잘 채운
   카드조차 "안 찍힘" 판정이 나는 비현실적인 픽셀이 된다. (처음에는 픽셀마다
   대칭 노이즈를 더했지만, 대칭 노이즈는 진폭과 무관하게 중간값 임계선을 기준으로
   화소를 정확히 반반으로 갈라 fill 을 항상 ~0.5 로 떨어뜨렸다 — 실제 카드처럼
   "밝은 배경이 대부분, 어두운 요소는 일부"인 비대칭 구조라야 fill 도 sharp 도
   현실적으로 나온다.) 사진 영역은 카드 면적의 일부만 차지해 fill 에 큰 영향이
   없고, tilt 판정에 쓰는 위/아래 10%·90% 행과도 겹치지 않게 중앙에 둔다. */
function frameWithCard({ inset = 0, tiltShift = 0 } = {}) {
  const gray = new Uint8ClampedArray(W * H).fill(30);   // 어두운 배경
  const g = guideRectInFrame(W, H);
  const photoX0 = g.x + Math.round(g.w * 0.38);
  const photoX1 = g.x + Math.round(g.w * 0.62);
  const photoY0 = g.y + Math.round(g.h * 0.35);
  const photoY1 = g.y + Math.round(g.h * 0.65);
  for (let y = g.y + inset; y < g.y + g.h - inset; y++) {
    const shift = Math.round(tiltShift * (y - g.y));
    for (let x = g.x + inset + shift; x < g.x + g.w - inset + shift; x++) {
      if (x < 0 || x >= W || y < 0 || y >= H) continue;
      const inPhoto = x >= photoX0 && x < photoX1 && y >= photoY0 && y < photoY1;
      gray[y * W + x] = inPhoto ? 80 : 220;
    }
  }
  return gray;
}

test('가이드를 채운 반듯한 카드는 ok', () => {
  const s = scoreFrame(frameWithCard(), W, H);
  assert.equal(s.ok, true, `fill=${s.fill} tilt=${s.tilt} sharp=${s.sharp}`);
});

test('카드가 너무 작으면 ok 아님 (fill 부족)', () => {
  const s = scoreFrame(frameWithCard({ inset: 18 }), W, H);
  assert.equal(s.ok, false);
});

test('빈 프레임은 ok 아님', () => {
  const s = scoreFrame(new Uint8ClampedArray(W * H).fill(30), W, H);
  assert.equal(s.ok, false);
});

test('기울어진 카드는 ok 아님', () => {
  const s = scoreFrame(frameWithCard({ tiltShift: 0.35 }), W, H);
  assert.equal(s.ok, false);
});

test('게이트는 연속 N프레임을 요구한다 — 한 프레임 튀어도 안 찍는다', () => {
  const gate = createShutterGate({ needed: 3 });
  const good = { ok: true }, bad = { ok: false };
  assert.equal(gate.push(good), false);
  assert.equal(gate.push(good), false);
  assert.equal(gate.push(bad), false, '중간에 끊기면 다시 세야 한다');
  assert.equal(gate.push(good), false);
  assert.equal(gate.push(good), false);
  assert.equal(gate.push(good), true, '연속 3프레임이면 찍는다');
});

test('reset 하면 처음부터 다시 센다', () => {
  const gate = createShutterGate({ needed: 2 });
  gate.push({ ok: true });
  gate.reset();
  assert.equal(gate.push({ ok: true }), false);
});
