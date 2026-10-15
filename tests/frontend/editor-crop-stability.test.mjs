import test from 'node:test';
import assert from 'node:assert/strict';
import * as crop from '../../src/features/editor/editorCrop.js';

const photo = { id: 'p', src: '/photo', type: 'image', x: 510, y: 50, w: 430, h: 580 };
const start = () => crop.beginEditorCrop('b', photo, { width: 430, height: 580 });

test('비율을 여러 번 비교해도 같은 3:4 영역으로 돌아온다', () => {
  let c = crop.ratioEditorCrop(start(), 3 / 4);
  for (let i = 0; i < 4; i++) {
    c = crop.ratioEditorCrop(c, 16 / 9);
    c = crop.ratioEditorCrop(c, 3 / 4);
    assert.equal(c.fw, 430);
    assert.ok(Math.abs(c.fh - 573.3333333333334) < .001);
    assert.equal(c.fx, 510);
    assert.ok(Math.abs(c.fy - 53.3333333333333) < .001);
  }
});

test('사용자가 손잡이로 바꾼 크기는 이후 비율 비교의 새 기준이 된다', () => {
  let c = crop.ratioEditorCrop(start(), 1);
  c = crop.dragEditorCrop(c, 'frame', 'se', -130, -130);
  c = crop.ratioEditorCrop(c, 16 / 9);
  c = crop.ratioEditorCrop(c, 1);
  assert.equal(c.fw, 300);
  assert.equal(c.fh, 300);
});

test('사진을 확대하고 자르기 영역을 옮긴 뒤에도 비율 전환이 사진 밖을 노출하지 않는다', () => {
  let c = crop.ratioEditorCrop(start(), 1);
  c = crop.zoomEditorCrop(c, 2);
  c = crop.dragEditorCrop(c, 'range', '', 1000, 1000);
  for (const ratio of [16 / 9, 3 / 4, 1, 3 / 4]) {
    c = crop.ratioEditorCrop(c, ratio);
    assert.ok(c.ox >= 0 && c.oy >= 0);
    assert.ok(c.ox + c.fw <= c.iw + .001 && c.oy + c.fh <= c.ih + .001);
    assert.ok(Math.abs(c.fw / c.fh - ratio) < .001);
  }
  assert.equal(c.fw, 430);
});

const viewport = { left: 320, top: 60, right: 1057, bottom: 720 };
for (const item of [
  { name: '오른쪽 열', frame: { left: 692, top: 100, bottom: 332 }, size: { width: 422, height: 34 }, want: { left: 627, top: 348 } },
  { name: '아래 공간 부족', frame: { left: 500, top: 300, bottom: 700 }, size: { width: 422, height: 34 }, want: { left: 500, top: 250 } },
  { name: '위아래로 큰 사진', frame: { left: 100, top: -300, bottom: 900 }, size: { width: 422, height: 34 }, want: { left: 328, top: 678 } },
  { name: '스크롤로 사라진 사진', frame: { left: 500, top: -500, bottom: -100 }, size: { width: 422, height: 34 }, want: { left: 500, top: 68 } },
  { name: '줄바꿈된 도구막대', frame: { left: 692, top: 100, bottom: 332 }, size: { width: 280, height: 76 }, viewport: { left: 320, top: 60, right: 616, bottom: 720 }, want: { left: 328, top: 348 } },
]) {
  test(`${item.name}에서도 자르기 도구막대 전체가 작업 영역 안에 남는다`, () => {
    assert.equal(typeof crop.cropToolbarPosition, 'function');
    assert.deepEqual(crop.cropToolbarPosition(item.frame, item.viewport || viewport, item.size), item.want);
  });
}
