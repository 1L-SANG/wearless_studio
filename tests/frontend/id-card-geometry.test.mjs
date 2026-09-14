/* 신분증 규격 좌표. 카드가 가이드를 채우도록 찍게 하면 주민등록번호 위치가
   계산으로 나온다 — 사용자가 박스를 끌 필요가 없고, 서버가 그 자리를 검사할 수 있다.
   이 모듈이 틀리면 마스크가 엉뚱한 데 찍히므로(=주민번호 노출) 좌표를 테스트로 굳힌다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { CARD_ASPECT, RRN_REGION, guideRectInFrame, rrnRectInFrame } from '../../src/features/model/idCardGeometry.js';

test('카드 비율은 ISO/IEC 7810 ID-1 (85.6 × 53.98mm)', () => {
  assert.ok(Math.abs(CARD_ASPECT - 85.6 / 53.98) < 1e-9);
});

test('가이드는 프레임 안에 들어가고 카드 비율을 지킨다', () => {
  for (const [w, h] of [[1080, 1920], [1920, 1080], [800, 800]]) {
    const g = guideRectInFrame(w, h);
    assert.ok(g.x >= 0 && g.y >= 0, '프레임 밖으로 나가면 안 된다');
    assert.ok(g.x + g.w <= w && g.y + g.h <= h);
    assert.ok(Math.abs(g.w / g.h - CARD_ASPECT) < 0.01, `비율이 카드와 달라졌다: ${g.w}/${g.h}`);
  }
});

test('주민번호 영역은 가이드 안에 완전히 들어간다', () => {
  const g = guideRectInFrame(1080, 1920);
  const r = rrnRectInFrame(1080, 1920);
  assert.ok(r.x >= g.x && r.y >= g.y, '가이드 왼쪽/위를 벗어났다');
  assert.ok(r.x + r.w <= g.x + g.w && r.y + r.h <= g.y + g.h, '가이드 오른쪽/아래를 벗어났다');
});

test('주민번호 영역 비율은 카드 아래쪽 번호 줄을 덮는다', () => {
  // 주민등록증의 번호는 이름 아래 한 줄이다. 가로로 넉넉히, 세로로는 그 줄만.
  assert.ok(RRN_REGION.yr > 0.4 && RRN_REGION.yr < 0.8, '세로 위치가 카드 중하단이어야 한다');
  assert.ok(RRN_REGION.wr > 0.4, '번호 전체를 덮을 만큼 가로가 넓어야 한다');
  assert.ok(RRN_REGION.hr > 0.08 && RRN_REGION.hr < 0.3, '한 줄 높이여야 한다');
});

test('fill 을 줄이면 가이드가 작아지고 주민번호 영역도 같이 줄어든다', () => {
  const big = rrnRectInFrame(1080, 1920, 0.9);
  const small = rrnRectInFrame(1080, 1920, 0.6);
  assert.ok(small.w < big.w && small.h < big.h);
});
