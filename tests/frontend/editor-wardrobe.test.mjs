import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { isWardrobeImageUsed, mergeEditorImagesIntoWardrobe } from '../../src/features/editor/editorWardrobe.js';

const editorSource = readFileSync(fileURLToPath(new URL('../../src/features/editor/Editor.jsx', import.meta.url)), 'utf8');
const panelSource = readFileSync(fileURLToPath(new URL('../../src/features/editor/EditorPanels.jsx', import.meta.url)), 'utf8');
const styleSource = readFileSync(fileURLToPath(new URL('../../src/styles/features.css', import.meta.url)), 'utf8');

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

test('wardrobe images are protected when the canvas uses the same id or source URL', () => {
  const blocks = [{
    id: 'block',
    elements: [
      { id: 'same-id', type: 'image', src: '/different-source.png' },
      { id: 'copied-element', type: 'image', src: '/same-source.png' },
      { id: 'text', type: 'text', text: '/same-source.png' },
    ],
  }];

  assert.equal(isWardrobeImageUsed(blocks, { id: 'same-id', src: '/unused.png' }), true);
  assert.equal(isWardrobeImageUsed(blocks, { id: 'wardrobe-source', src: '/same-source.png' }), true);
  assert.equal(isWardrobeImageUsed(blocks, { id: 'unused', src: '/unused.png' }), false);
});

test('wardrobe uses direct trash actions and blocks deletion for photos used in the editor', () => {
  const wardrobePanel = panelSource.slice(
    panelSource.indexOf('export function WardrobePanel'),
    panelSource.indexOf('/* ---------- 이미지 props'),
  );

  // 삭제는 앱 공통 관례대로 우측 위 X(오너 8/15) — 휴지통 아이콘·좌상단 배치는 폐기.
  assert.match(wardrobePanel, /className=\{`ward-rm\$\{used \? ' disabled' : ''\}`\}/);
  assert.match(wardrobePanel, /<Icon name="x" size=\{12\} \/>/);
  assert.doesNotMatch(wardrobePanel, /ward-trash|name="trash"/);
  // 우측 위는 '사진 고르기' 체크와 자리가 겹친다 — 그 모드에서는 삭제를 감춰 오클릭을 막는다.
  assert.match(styleSource, /\.ward-cell\.select-target \.ward-rm \{ display: none; \}/);
  assert.match(styleSource, /\.ward-cell \.ward-rm \{[^}]*top: 4px; right: 4px;/);
  assert.match(wardrobePanel, /aria-disabled=\{used\}/);
  assert.match(wardrobePanel, /onDeleteImage\(im\)/);
  assert.doesNotMatch(wardrobePanel, /ward-check|onDeleteSelected|toggleSel|ward-delbar/);
  assert.match(editorSource, /isWardrobeImageUsed\(latestBlocks\.current \|\| blocks, image\)/);
  assert.match(editorSource, /현재 에디팅에 사용 중인 사진은 삭제할 수 없어요/);
});

test('editor loading renders never gain an extra hook after data arrives', () => {
  const loadingReturn = editorSource.indexOf('if (!blocks || !catalogs) return');
  assert.ok(loadingReturn > 0);
  assert.doesNotMatch(
    editorSource.slice(loadingReturn),
    /\buse(?:State|Effect|LayoutEffect|Memo|Callback|Ref|Context|Reducer)\s*\(/,
  );
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


test('생성 중·지연 타일은 블록이 바뀌어도 살아남는다 — 사라지면 셀러가 다시 생성해 이중 결제', () => {
  const wardrobe = {
    c1: [
      { id: 'done', src: '/a.png' },
      { id: 'loading', loading: true },
      { id: 'slow', loading: true, slow: true },
    ],
  };
  const merged = mergeEditorImagesIntoWardrobe({ wardrobe, blocks: [], colorIds: ['c1'] });
  assert.deepEqual(merged.c1.map((image) => image.id), ['done', 'loading', 'slow']);
  assert.equal(merged.c1[2].slow, true, "'조금 더 걸려요' 표시가 유지된다");
  // src 도 표식도 없는 찌꺼기는 예전처럼 걸러낸다.
  const junk = mergeEditorImagesIntoWardrobe({ wardrobe: { c1: [{ id: 'ghost' }] }, blocks: [], colorIds: ['c1'] });
  assert.equal(junk.c1, undefined);
});

test('같은 대기 타일이 두 번 들어와도 한 번만 남는다', () => {
  const wardrobe = { c1: [{ id: 'loading', loading: true }, { id: 'loading', loading: true }] };
  const merged = mergeEditorImagesIntoWardrobe({ wardrobe, blocks: [], colorIds: ['c1'] });
  assert.equal(merged.c1.length, 1);
});
