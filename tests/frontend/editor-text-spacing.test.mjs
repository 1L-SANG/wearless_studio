import test from 'node:test';
import assert from 'node:assert/strict';
import { recommendedTextSpacing, resolveTextSpacing, patchTextStyle } from '../../src/features/editor/textSpacing.js';
import { activeTextPreset } from '../../src/features/editor/presets/textPresets.js';

test('자동 간격은 글자 크기를 키울 때 줄 높이도 커지고 제목은 상대적으로 촘촘해진다', () => {
  const body = recommendedTextSpacing(17, 'Pretendard');
  const heading = recommendedTextSpacing(40, 'Pretendard');
  assert.equal(body.lineHeight, 26);
  assert.ok(heading.lineHeight > body.lineHeight);
  assert.ok(heading.lineHeight / 40 < body.lineHeight / 17);
  assert.equal(body.tracking, 0);
  assert.ok(heading.tracking < 0);
  for (let size = 9; size <= 200; size++) {
    assert.ok(recommendedTextSpacing(size).lineHeight >= recommendedTextSpacing(size - 1).lineHeight);
    assert.ok(recommendedTextSpacing(size).lineHeight >= size);
  }
});

test('자동 설정은 저장 왕복 후에도 크기 변경을 따라가고 정렬·색·서체는 유지한다', () => {
  const enabled = patchTextStyle({ size: 17, font: 'Pretendard', align: 'right', color: '#123456', lineHeight: 29 }, { spacingMode: 'auto' });
  const stored = JSON.parse(JSON.stringify(enabled));
  const changed = patchTextStyle(stored, { size: 40 });
  assert.equal(changed.spacingMode, 'auto');
  assert.equal(changed.lineHeight, recommendedTextSpacing(40).lineHeight);
  assert.equal(changed.align, 'right');
  assert.equal(changed.color, '#123456');
  assert.equal(changed.font, 'Pretendard');
  assert.deepEqual(resolveTextSpacing({ ...stored, size: 64 }), recommendedTextSpacing(64));
});

test('수동 간격은 글자 크기를 바꿔도 유지하며 0 자간도 명시 값으로 존중한다', () => {
  const manual = patchTextStyle({ size: 17, spacingMode: 'auto', lineHeight: 26, tracking: 0 }, { lineHeight: 32 });
  const enlarged = patchTextStyle(manual, { size: 40 });
  assert.equal(enlarged.spacingMode, 'manual');
  assert.equal(enlarged.lineHeight, 32);
  assert.equal(enlarged.tracking, 0);
  assert.deepEqual(resolveTextSpacing(enlarged), { lineHeight: 32, tracking: 0 });
});

test('기존 문서는 자동 설정을 켜기 전까지 기존 명시 값과 기본 행간을 유지한다', () => {
  assert.deepEqual(resolveTextSpacing({ size: 40 }), { lineHeight: 56, tracking: 0 });
  assert.deepEqual(resolveTextSpacing({ size: 17, lineHeight: 26, tracking: 1.2 }), { lineHeight: 26, tracking: 1.2 });
  assert.equal(patchTextStyle({ size: 17, lineHeight: 26 }, { weight: 600 }).lineHeight, 26);
});

test('장식 서체에는 한글 UI 서체의 음수 자간을 일괄 적용하지 않는다', () => {
  assert.equal(recommendedTextSpacing(64, 'Cormorant').tracking, 0);
  assert.equal(recommendedTextSpacing(64, 'Gowun Dodum').tracking, 0);
  assert.ok(Number.isFinite(recommendedTextSpacing(NaN).lineHeight));
});

test('간격 입력칸을 바꾸지 않고 떠나도 자동 상태가 유지된다', () => {
  const initial = patchTextStyle({ size: 40 }, { spacingMode: 'auto' });
  const blurredLine = patchTextStyle(initial, { lineHeight: initial.lineHeight });
  const blurredTracking = patchTextStyle(blurredLine, { tracking: initial.tracking });
  assert.equal(blurredTracking.spacingMode, 'auto');
  const enlarged = patchTextStyle(blurredTracking, { size: 64 });
  assert.equal(enlarged.lineHeight, recommendedTextSpacing(64).lineHeight);
});

test('기존 소제목의 반올림된 기본 행간도 기존 빠른 스타일로 판별한다', () => {
  assert.equal(activeTextPreset({ size: 26, weight: 600, color: '#0e0d14', lineHeight: 36, tracking: 0 }), 'subtitle');
});

for (const [size, lineHeight] of [[28, 39], [14, 20], [15, 21], [24, 34]]) {
  test(`행간이 없는 ${size}px 글자는 기본 행간을 ${lineHeight}px 정수로 읽는다`, () => {
    assert.deepEqual(resolveTextSpacing({ size }), { lineHeight, tracking: 0 });
  });
}

test('반올림된 기본 행간을 그대로 입력하면 상태를 유지하고 직접 바꾸면 수동 값으로 보존한다', () => {
  const unchanged = patchTextStyle({ size: 28 }, { lineHeight: 39 });
  assert.equal(unchanged.spacingMode, undefined);
  assert.equal(unchanged.lineHeight, 39);
  const manual = patchTextStyle(unchanged, { lineHeight: 40 });
  assert.equal(manual.spacingMode, 'manual');
  assert.equal(manual.lineHeight, 40);
  const enlarged = patchTextStyle(manual, { size: 40 });
  assert.equal(enlarged.spacingMode, 'manual');
  assert.equal(enlarged.lineHeight, 40);
  const automatic = patchTextStyle(enlarged, { spacingMode: 'auto' });
  assert.equal(automatic.spacingMode, 'auto');
  assert.equal(automatic.lineHeight, 52);
});

test('저장된 기본 행간의 부동소수 결과만 반올림하고 셀러가 넣은 소수는 유지한다', () => {
  assert.equal(resolveTextSpacing({ size: 28, lineHeight: 39.199999999999996 }).lineHeight, 39);
  assert.equal(resolveTextSpacing({ size: 28, lineHeight: 39.5 }).lineHeight, 39.5);
  assert.equal(resolveTextSpacing({ size: 28, lineHeight: 39.2 }).lineHeight, 39.2);
});

test('저장된 부동소수 기본 행간에 표시값을 그대로 입력해도 수동으로 바뀌지 않는다', () => {
  const unchanged = patchTextStyle({ size: 28, lineHeight: 39.199999999999996 }, { lineHeight: 39 });
  assert.equal(unchanged.spacingMode, undefined);
  assert.equal(unchanged.lineHeight, 39);
});
