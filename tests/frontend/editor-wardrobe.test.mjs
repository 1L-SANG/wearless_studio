import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { mergeEditorImagesIntoWardrobe } from '../../src/features/editor/editorWardrobe.js';

const editorSource = readFileSync(fileURLToPath(new URL('../../src/features/editor/Editor.jsx', import.meta.url)), 'utf8');

test('generated editor photos are merged into their product color groups', () => {
  const wardrobe = {
    black: [{ id: 'existing', src: '/existing-black.png' }],
    misc: [{ id: 'existing-upload', src: '/existing-upload.png' }],
  };
  const blocks = [{
    id: 'generated-block',
    elements: [
      { id: 'black-photo', type: 'image', src: '/generated-black.png', sourceBlockId: 'shot-black', cutType: 'hero' },
      { id: 'ivory-photo', type: 'image', src: '/generated-ivory.png', sourceBlockId: 'shot-ivory', cutType: 'detail' },
      { id: 'duplicate', type: 'image', src: '/existing-black.png', sourceBlockId: 'shot-black' },
    ],
  }];
  const storyboard = [
    { id: 'shot-black', colorId: 'black' },
    { id: 'shot-ivory', colorId: 'ivory' },
  ];

  const merged = mergeEditorImagesIntoWardrobe({
    wardrobe,
    blocks,
    storyboard,
    colorIds: ['black', 'ivory'],
    fallbackColorId: 'black',
  });

  assert.deepEqual(merged.black.map((image) => image.src), ['/existing-black.png', '/generated-black.png']);
  assert.deepEqual(merged.ivory.map((image) => image.src), ['/generated-ivory.png']);
  assert.deepEqual(merged.misc.map((image) => image.src), ['/existing-upload.png']);
});

test('direct uploads are collected under misc without treating arbitrary placed photos as uploads', () => {
  const blocks = [
    {
      id: 'uploaded-block',
      contentRole: 'custom',
      name: '내 이미지',
      elements: [
        { id: 'upload', type: 'image', src: '/mine.png' },
        { id: 'storyboard-upload', type: 'image', src: '/mine-from-storyboard.png', sourceBlockId: 'mine-shot' },
      ],
    },
    {
      id: 'frame-block',
      contentRole: 'custom',
      elements: [
        { id: 'marked-upload', type: 'image', src: '/uploaded-into-frame.png', userUploaded: true },
        { id: 'library-photo', type: 'image', src: '/placed-from-library.png' },
      ],
    },
  ];

  const merged = mergeEditorImagesIntoWardrobe({
    wardrobe: {},
    blocks,
    storyboard: [{ id: 'mine-shot', source: 'mine', colorId: 'black' }],
  });

  assert.deepEqual(merged.misc.map((image) => image.src), ['/mine.png', '/mine-from-storyboard.png', '/uploaded-into-frame.png']);
});

test('crop controls keep original and expose mouse confirm/cancel without instruction pills', () => {
  assert.match(editorSource, /className="crop-reset"[\s\S]{0,220}>원본<\/button>/);
  assert.match(editorSource, /className="crop-apply"/);
  assert.match(editorSource, /className="crop-cancel"/);
  assert.match(editorSource, /onCropCancel/);
  assert.doesNotMatch(editorSource, /안쪽 드래그 사진 이동/);
  assert.doesNotMatch(editorSource, /Enter 확정 · Esc 취소/);
});

test('crop controls render outside the clipped block contents', () => {
  assert.match(editorSource, /<\/div>\s*\{crop && \(\s*<div className="crop-bar"/);
});
