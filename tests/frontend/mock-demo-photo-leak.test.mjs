/* 목 데모 사진이 실제 흐름으로 새는 경로를 막는다 (2026-08-17 사고).

   http 모드의 비로그인 입력 흐름은 서버 projectId 가 없어서 상품 저장·조회를 mock 어댑터에
   위임한다(api/index.js PUBLIC_INPUT). 그런데 그 mock DB 에는 데모 시드 상품이 들어 있고,
   그 사진은 SVG 플레이스홀더(data:image/svg+xml)다. 이게 "서버에 저장된 상품 사진" 자리로
   들어오면:

     ① 색상 저장 패치의 images 로 실려 나가고(mergeColorMetadataWithPersistedImages)
     ② 임시저장 페이로드에 그대로 실리고(blob: 이 아니라 걸러지지 않음)
     ③ 복원되면 화면의 상품 사진이 되어 데모 실루엣이 보이고
     ④ 확정에서 mime image/svg+xml 로 업로드돼 서버가 400 으로 거부한다
        ("지원하지 않는 이미지 형식입니다." — 셀러는 jpg 를 올렸는데도 진행이 막힌다)

   실제 사고 재현: 21:15 프로젝트는 업로드 0건·이미지 0장으로 남고, 같은 옷으로 새 상세페이지를
   만들면(=draft 초기화) 정상 동작했다. */

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { isUploadablePhotoMime, isUploadablePhotoSrc } from '../../src/lib/imageTranscode.js';
import { withoutDemoPhotos } from '../../src/lib/api/guestProductTemplate.js';
import { mergeColorMetadataWithPersistedImages } from '../../src/features/product-input/saveRouting.js';
import { restoreDraftProduct } from '../../src/features/product-input/draftProductRestore.js';
import { collectDraftPhotos } from '../../src/lib/draftStore.js';

const PLACEHOLDER = "data:image/svg+xml;utf8,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%3E%3C/svg%3E";
const demoColors = () => ([
  {
    id: 'col1',
    name: '블랙',
    swatchId: 'black',
    isBase: true,
    isMain: true,
    monotone: true,
    // 데모 시드의 사진 — type 필드가 아예 없다(그래서 mime 이 blob.type 으로 굳는다).
    images: [
      { id: 'seed-front', slot: 'Front', label: 'Front', src: PLACEHOLDER },
      { id: 'seed-back', slot: 'Back', label: 'Back', src: PLACEHOLDER },
    ],
  },
  { id: 'col2', name: '아이보리', swatchId: 'ivory', images: [{ id: 'seed-c2', slot: 'Front', src: PLACEHOLDER }] },
]);

// ── 판정 ──────────────────────────────────────────────────────────────────────

test('업로드 가능한 mime 은 서버 화이트리스트와 같은 집합이다', () => {
  for (const mime of ['image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/avif']) {
    assert.equal(isUploadablePhotoMime(mime), true, mime);
  }
  for (const mime of ['image/svg+xml', 'image/heic', 'image', '', null, undefined,
    'text/html', 'application/json', 'image/jpg']) {
    assert.equal(isUploadablePhotoMime(mime), false, String(mime));
  }
});

test('셀러 사진의 src 는 blob: 또는 http(s): 뿐 — data: 는 플레이스홀더다', () => {
  assert.equal(isUploadablePhotoSrc('blob:http://x/abc'), true);
  assert.equal(isUploadablePhotoSrc('https://api.wearless.kr/v1/assets/a/file'), true);
  assert.equal(isUploadablePhotoSrc('/v1/assets/a/file'), true);
  assert.equal(isUploadablePhotoSrc(PLACEHOLDER), false);
  assert.equal(isUploadablePhotoSrc('data:image/png;base64,AAAA'), false);
  assert.equal(isUploadablePhotoSrc(''), false);
  assert.equal(isUploadablePhotoSrc(undefined), false);
});

// ── ① 경계: 게스트에게 데모 사진을 건네지 않는다 ──────────────────────────────

test('게스트 상품 템플릿은 데모 사진을 들고 오지 않는다', () => {
  const template = withoutDemoPhotos({ name: '소프트 골지 라운드 니트', colors: demoColors() });
  assert.deepEqual(template.colors.map((c) => c.images), [[], []]);
  // 색상 메타(스와치 후보·기준색 표식)는 템플릿의 쓸모라 그대로 남는다.
  assert.equal(template.colors[0].id, 'col1');
  assert.equal(template.colors[0].isBase, true);
});

test('실제 서버 상품은 손대지 않는다 — 사진이 사라지면 그게 더 큰 사고다', () => {
  const real = {
    colors: [{
      id: 'col1',
      images: [{ id: 'a1', slot: 'Front', src: 'https://api.wearless.kr/v1/assets/a1/file', type: 'image/jpeg' }],
    }],
  };
  assert.deepEqual(withoutDemoPhotos(real), real);
});

// ── ② 저장 패치로 새지 않는다 ────────────────────────────────────────────────

test('색상 저장 패치는 플레이스홀더 사진을 실어 보내지 않는다', () => {
  const edited = [{ id: 'col1', name: '퍼플', swatchId: 'purple', isBase: true }];
  const merged = mergeColorMetadataWithPersistedImages(demoColors(), edited);
  assert.equal(merged[0].name, '퍼플');
  assert.equal(merged[0].swatchId, 'purple');
  assert.deepEqual(merged[0].images, [], '데모 플레이스홀더가 저장 패치에 실리면 안 된다');
});

test('서버에 이미 있는 사진은 색상 저장 패치에 그대로 유지된다', () => {
  const persisted = [{
    id: 'col1',
    images: [
      { id: 'a1', slot: 'Front', src: 'https://api.wearless.kr/v1/assets/a1/file', type: 'image/jpeg' },
      { id: 'ph', slot: 'Back', src: PLACEHOLDER },
    ],
  }];
  const merged = mergeColorMetadataWithPersistedImages(persisted, [{ id: 'col1', name: '화이트' }]);
  assert.deepEqual(merged[0].images.map((im) => im.id), ['a1'], '진짜 사진만 남는다');
});

// ── ③ 복원된 화면에 데모 사진이 뜨지 않는다 ──────────────────────────────────

test('복원은 blob 이 살아난 사진만 상품에 남긴다', () => {
  const draft = {
    product: {
      colors: [{
        id: 'col1',
        images: [
          { id: 'live', slot: 'Front', src: 'blob:dead' },
          { id: 'placeholder', slot: 'Back', src: PLACEHOLDER },
          { id: 'uploaded', slot: 'Detail', src: 'https://api.wearless.kr/v1/assets/u/file' },
        ],
      }],
    },
    photos: [{ imageId: 'live', blob: 'BLOB' }],
  };
  const restored = restoreDraftProduct(draft, { createObjectUrl: () => 'blob:fresh' });
  assert.deepEqual(
    restored.colors[0].images.map((im) => [im.id, im.src]),
    [['live', 'blob:fresh'], ['uploaded', 'https://api.wearless.kr/v1/assets/u/file']],
    '데모 플레이스홀더는 복원 대상이 아니다',
  );
});

test('복원할 blob 이 없고 src 도 죽은 objectURL 이면 그 사진은 버린다', () => {
  const draft = {
    product: { colors: [{ id: 'col1', images: [{ id: 'gone', slot: 'Front', src: 'blob:dead' }] }] },
    photos: [],
  };
  const restored = restoreDraftProduct(draft, { createObjectUrl: () => 'blob:fresh' });
  assert.deepEqual(restored.colors[0].images, [], '죽은 objectURL 을 정상 사진인 척 복원하면 안 된다');
});

// ── ④ 업로드 경계: 올릴 수 없는 사진은 draft 에 담지 않는다 ──────────────────

test('draft 사진 수집은 올릴 수 없는 사진을 담지 않고 실패로 센다', async () => {
  const product = {
    colors: [{
      id: 'col1',
      images: [
        { id: 'real', slot: 'Front', src: 'blob:real', name: 'a.jpg', type: 'image/jpeg' },
        { id: 'placeholder', slot: 'Back', src: PLACEHOLDER },
      ],
    }],
  };
  const fetchBlob = async (src) => (src === PLACEHOLDER
    ? { type: 'image/svg+xml', size: 120 }
    : { type: 'image/jpeg', size: 2000 });

  const { photos, cleanProduct, failed } = await collectDraftPhotos(product, fetchBlob);
  assert.deepEqual(photos.map((p) => p.imageId), ['real']);
  assert.deepEqual(photos.map((p) => p.mime), ['image/jpeg']);
  assert.equal(failed, 1, '올릴 수 없는 사진은 조용히 사라지지 않고 실패로 보고된다');
  assert.deepEqual(cleanProduct.colors[0].images.map((im) => im.id), ['real']);
});

test('mime 을 못 믿는 사진은 blob 의 실제 타입으로 고친다', async () => {
  const product = {
    colors: [{
      id: 'col1',
      images: [
        // filesToMetas 폴백이 남긴 잘못된 MIME('image') — 서버 화이트리스트에 없다.
        { id: 'bad-type', slot: 'Front', src: 'blob:a', type: 'image' },
        { id: 'no-type', slot: 'Back', src: 'blob:b' },
      ],
    }],
  };
  const { photos, failed } = await collectDraftPhotos(
    product, async () => ({ type: 'image/png', size: 10 }),
  );
  assert.deepEqual(photos.map((p) => p.mime), ['image/png', 'image/png']);
  assert.equal(failed, 0);
});

test('blob 을 읽지 못한 사진은 종전처럼 실패로 집계된다', async () => {
  const product = { colors: [{ id: 'col1', images: [{ id: 'dead', slot: 'Front', src: 'blob:dead' }] }] };
  const { photos, failed } = await collectDraftPhotos(product, async () => {
    throw new Error('revoked');
  });
  assert.deepEqual(photos, []);
  assert.equal(failed, 1);
});


// ── ⑤ 승격(확정)은 마지막 문지기다 ───────────────────────────────────────────

test('임시저장 페이로드는 올릴 수 없는 사진을 싣지 않는다', () => {
  const src = readFileSync(new URL('../../src/lib/draftSlot.js', import.meta.url), 'utf8');
  const payloadFor = src.slice(src.indexOf('const payloadFor'), src.indexOf('photosPending,\n      includedUploadIds'));
  assert.match(payloadFor, /isPlaceholderPhotoSrc\(image\.src\)/,
    'blob: 이 아니라는 이유만으로 통과시키면 data: 플레이스홀더가 그대로 실린다');
});

test('승격 업로드는 올릴 수 없는 mime 을 서버로 보내지 않는다', () => {
  const src = readFileSync(new URL('../../src/lib/draftSync.js', import.meta.url), 'utf8');
  assert.match(src, /isUploadablePhotoMime\(p\?\.mime\)/);
  // 사진 하나 때문에 확정 전체가 서버 400 으로 죽는 경로를 남기지 않는다.
  assert.ok(src.indexOf('uploadable.map') > src.indexOf('isUploadablePhotoMime'));
});

test('게스트 구간의 상품 조회는 경계에서 데모 사진을 잘라낸다', () => {
  const src = readFileSync(new URL('../../src/lib/api/index.js', import.meta.url), 'utf8');
  assert.match(src, /withoutDemoPhotos\(await mockAdapter\.getProduct\(projectId/);
});

test('게스트는 서버 저장 색상 기준선을 갖지 않는다', () => {
  const src = readFileSync(new URL('../../src/features/product-input/ProductInput.jsx', import.meta.url), 'utf8');
  assert.match(src, /persistedColorsRef\.current = editingProjectId \? \(p\.colors \|\| \[\]\) : \[\]/);
});
