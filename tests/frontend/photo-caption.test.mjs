import test from 'node:test';
import assert from 'node:assert/strict';

import { isMeaninglessPhotoName, photoCaption } from '../../src/features/product-input/photoCaption.js';

test('macOS 드래그·붙여넣기 임시 이름을 임시로 판정한다', () => {
  for (const name of [
    'tempImage6mNIRA.jpg', 'tempimage.png', 'tempImage-2.jpeg',
    'image.png', 'image (1).png', 'image_2.jpg', 'unnamed.jpg', 'photo.jpg',
    '스크린샷 2026-08-15 오후 3.45.12.png', 'Screenshot 2026-08-15 at 15.45.png',
    'Screen Shot 2026-08-15.png', 'clipboard.png', 'pasted image 3.png',
    '', null, undefined, '   ',
  ]) assert.equal(isMeaninglessPhotoName(name), true, `임시로 봐야 함: ${name}`);
});

test('사람이 붙인 이름과 카메라 기본명은 살린다', () => {
  for (const name of [
    '니트_앞면.jpg', 'IMG_1234.HEIC', 'DSC_0099.jpg', '골지니트-뒷면-디테일.png',
    'front.jpg', 'imagenary-knit.jpg', 'photobooth-01.jpg',
  ]) assert.equal(isMeaninglessPhotoName(name), false, `살려야 함: ${name}`);
});

test('임시 이름은 슬롯 라벨 + 순번으로 바꾼다', () => {
  assert.equal(photoCaption('tempImage6mNIRA.jpg', '앞면', 0), '앞면 사진 1');
  assert.equal(photoCaption('image.png', '뒷면 디테일', 2), '뒷면 디테일 사진 3');
  assert.equal(photoCaption('', '앞면', 1), '앞면 사진 2');
});

test('슬롯 라벨이 없으면 순번만, 사람 이름은 그대로', () => {
  assert.equal(photoCaption('tempImage.jpg', '', 0), '사진 1');
  assert.equal(photoCaption('tempImage.jpg', null, 4), '사진 5');
  assert.equal(photoCaption('니트_앞면.jpg', '앞면', 0), '니트_앞면.jpg');
});

test('잘못된 순번에도 1 이상으로 떨어진다', () => {
  assert.equal(photoCaption('image.png', '앞면', -3), '앞면 사진 1');
  assert.equal(photoCaption('image.png', '앞면', NaN), '앞면 사진 1');
  assert.equal(photoCaption('image.png', '앞면', 1.7), '앞면 사진 2');
});
