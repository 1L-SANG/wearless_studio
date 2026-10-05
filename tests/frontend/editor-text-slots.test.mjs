import assert from 'node:assert/strict';
import test from 'node:test';

import * as textSlots from '../../src/lib/editorTextSlots.js';

test('44px 캡션은 20자까지 한 줄, 21자부터 두 줄 높이를 쓴다', () => {
  assert.equal(textSlots.captionHeight('가'.repeat(20)), 60);
  assert.equal(textSlots.captionHeight('가'.repeat(21)), 120);
});

test('60px 시그니처 제목은 단어 경계에서 최대 두 줄로 배치한다', () => {
  assert.deepEqual(
    textSlots.hookTitleLayout('프리미엄 울 블렌드 오버핏 케이블 라운드 니트'),
    {
      text: '프리미엄 울 블렌드\n오버핏 케이블 라운드 니트',
      lines: 2,
      h: 144,
    },
  );
});

test('시그니처 제목은 편집 중이 아닐 때만 두 줄 clamp를 적용한다', () => {
  assert.deepEqual(textSlots.textLineClampStyle?.({ maxLines: 2 }, false), {
    display: '-webkit-box',
    WebkitBoxOrient: 'vertical',
    WebkitLineClamp: 2,
    overflow: 'hidden',
  });
  assert.deepEqual(textSlots.textLineClampStyle?.({ maxLines: 2 }, true), {});
});
