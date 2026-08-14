import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { mergeEditorImagesIntoWardrobe } from '../../src/features/editor/editorWardrobe.js';

const editorSource = readFileSync(fileURLToPath(new URL('../../src/features/editor/Editor.jsx', import.meta.url)), 'utf8');
const panelSource = readFileSync(fileURLToPath(new URL('../../src/features/editor/EditorPanels.jsx', import.meta.url)), 'utf8');

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

test('final asset URLs replace expired generation previews instead of doubling the wardrobe', () => {
  const storyboard = [
    { id: 'shot-black-1', colorId: 'black' },
    { id: 'shot-black-2', colorId: 'black' },
  ];
  const previewBlocks = [{
    id: 'generation-preview',
    elements: [
      { id: 'photo-1', type: 'image', src: 'https://provider.example/expired-1.jpg', sourceBlockId: 'shot-black-1' },
      { id: 'photo-2', type: 'image', src: 'https://provider.example/expired-2.jpg', sourceBlockId: 'shot-black-2' },
    ],
  }];
  const previewWardrobe = mergeEditorImagesIntoWardrobe({
    wardrobe: {},
    blocks: previewBlocks,
    storyboard,
    colorIds: ['black'],
  });

  const finalBlocks = [{
    id: 'generation-preview',
    elements: [
      { id: 'photo-1', type: 'image', src: 'https://api.wearless.kr/v1/assets/stable-1/file', sourceBlockId: 'shot-black-1' },
      { id: 'photo-2', type: 'image', src: 'https://api.wearless.kr/v1/assets/stable-2/file', sourceBlockId: 'shot-black-2' },
    ],
  }];
  const finalized = mergeEditorImagesIntoWardrobe({
    wardrobe: previewWardrobe,
    blocks: finalBlocks,
    storyboard,
    colorIds: ['black'],
  });

  assert.deepEqual(finalized.black.map((image) => image.src), [
    'https://api.wearless.kr/v1/assets/stable-1/file',
    'https://api.wearless.kr/v1/assets/stable-2/file',
  ]);
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

test('direct wardrobe uploads keep the wardrobe tab and expose their loading state', () => {
  const insertImage = editorSource.slice(
    editorSource.indexOf('const insertImage ='),
    editorSource.indexOf('const requestSlotImage ='),
  );
  const uploadEditorImage = editorSource.slice(
    editorSource.indexOf('const uploadEditorImage ='),
    editorSource.indexOf('const dropImageFiles ='),
  );

  assert.match(insertImage, /keepTab = false/);
  assert.match(insertImage, /selectEl\(target, el, false, keepTab\)/);
  assert.match(uploadEditorImage, /const isWardrobeUpload = !placement\?\.blockId && !importId/);
  assert.match(uploadEditorImage, /setWardrobeUploadLoading\(true\)/);
  assert.match(uploadEditorImage, /wardrobeInsert\(image, \{ keepTab: true \}\)/);
  assert.match(uploadEditorImage, /finally \{\s*if \(isWardrobeUpload\) setWardrobeUploadLoading\(false\)/);
  assert.match(panelSource, /role="status" aria-live="polite"/);
  assert.match(panelSource, /의류 이미지를 불러오는 중이에요/);
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
