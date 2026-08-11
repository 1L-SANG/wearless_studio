import test from 'node:test';
import assert from 'node:assert/strict';

import {
  getUploadValidationError,
  isHeic,
  JPEG_QUALITY,
  looksLikeImageFile,
  MAX_EDGE,
  MAX_UPLOAD_BYTES,
  renameExt,
  scaledImageDimensions,
  toUploadableImage,
  toUploadableImages,
} from '../../src/lib/imageTranscode.js';

/* ISO-BMFF 박스: [0..4]=박스 크기, [4..8]='ftyp', [8..12]=major brand */
const ftyp = (brand, { size = 24 } = {}) => {
  const head = new Uint8Array(size);
  head[3] = size;                                   // box size (big-endian, 작은 값이라 마지막 바이트만)
  head.set([0x66, 0x74, 0x79, 0x70], 4);            // 'ftyp'
  head.set([...brand].map((c) => c.charCodeAt(0)), 8);
  return new Blob([head]);
};

test('아이폰 HEIC 를 매직바이트로 알아본다 — 확장자·MIME 이 없어도', async () => {
  // iOS·일부 브라우저가 File.type 을 빈 문자열로 준다. 확장자로만 거르면 아이폰 사진이
  // 선택 단계에서 조용히 사라졌다(= 이 기능의 최초 버그).
  for (const brand of ['heic', 'heix', 'mif1', 'heim', 'hevc']) {
    assert.equal(await isHeic(ftyp(brand)), true, `${brand} 는 HEIC 로 판별돼야 한다`);
  }
});

test('HEIC 가 아닌 것은 변환 경로로 보내지 않는다', async () => {
  // JPEG(FFD8) — ftyp 박스 자체가 없다
  assert.equal(await isHeic(new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0, 0, 0, 0, 0, 0, 0, 0])])), false);
  // PNG 시그니처
  assert.equal(await isHeic(new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0])])), false);
  // ftyp 는 있지만 MP4 브랜드 — 영상이 HEIC 로 오인되면 변환에서 터진다
  assert.equal(await isHeic(ftyp('isom')), false);
  assert.equal(await isHeic(ftyp('mp42')), false);
});

test('헤더보다 짧은 파일은 HEIC 가 아니라고 본다 — 판별 실패가 업로드를 막지 않게', async () => {
  assert.equal(await isHeic(new Blob([new Uint8Array([1, 2, 3])])), false);
  assert.equal(await isHeic(new Blob([])), false);
});

test('변환본은 확장자를 jpg 로 바꿔 이름을 유지한다', () => {
  assert.equal(renameExt('IMG_1234.HEIC', 'jpg'), 'IMG_1234.jpg');
  assert.equal(renameExt('사진.heif', 'jpg'), '사진.jpg');
  assert.equal(renameExt('점.있는.이름.heic', 'jpg'), '점.있는.이름.jpg');
  assert.equal(renameExt('', 'jpg'), 'photo.jpg');   // 이름 없는 파일도 업로드 가능해야 한다
});

test('커스텀 매칭 축소는 긴 변 1600px와 최소변 400px를 함께 지킨다', () => {
  assert.deepEqual(
    scaledImageDimensions(4032, 3024, { maxEdge: 1600, minEdge: 400 }),
    { width: 1600, height: 1200 },
  );
  assert.deepEqual(
    scaledImageDimensions(8000, 500, { maxEdge: 1600, minEdge: 400 }),
    { width: 6400, height: 400 },
  );
  assert.deepEqual(
    scaledImageDimensions(399, 1200, { maxEdge: 1600, minEdge: 400 }),
    { width: 399, height: 1200 },
  );
});

test('MIME 없는 아이폰 이미지도 파일 선택 단계에서 유지한다', () => {
  assert.equal(looksLikeImageFile({ name: 'IMG_1234.HEIC', type: '' }), true);
  assert.equal(looksLikeImageFile({ name: '상품.jpg', type: 'image/jpeg' }), true);
  assert.equal(looksLikeImageFile({ name: '메모.txt', type: '' }), false);
});

test('최종 변환본은 서버와 같은 MIME 화이트리스트와 25MB 상한으로 검사한다', () => {
  assert.equal(getUploadValidationError(new File(['x'], '상품.jpg', { type: 'image/jpeg' })), null);
  assert.equal(getUploadValidationError(new File(['x'], '상품.svg', { type: 'image/svg+xml' })), 'unsupported_type');
  assert.equal(getUploadValidationError(new File([], '빈사진.jpg', { type: 'image/jpeg' })), 'file_too_large');
  assert.equal(getUploadValidationError({ type: 'image/png', size: MAX_UPLOAD_BYTES + 1 }), 'file_too_large');
});

test('이미지 변환은 한 번 실패하면 자동으로 한 번만 재시도한다', async () => {
  const originalCreateImageBitmap = globalThis.createImageBitmap;
  let calls = 0;
  globalThis.createImageBitmap = async () => {
    calls += 1;
    if (calls === 1) throw new Error('일시적인 디코드 시간이 제한을 초과했어요');
    return { width: 100, height: 100, close: () => {} };
  };
  try {
    const input = new File(['image'], '상품.jpg', { type: 'image/jpeg' });
    const result = await toUploadableImages([input]);
    assert.deepEqual(result, { files: [input], failed: [] });
    assert.equal(calls, 2);
  } finally {
    if (originalCreateImageBitmap === undefined) delete globalThis.createImageBitmap;
    else globalThis.createImageBitmap = originalCreateImageBitmap;
  }
});

test('커스텀 옵션은 원본 JPEG/PNG도 1600px JPEG로 한 번 재인코딩한다', async () => {
  const originalCreateImageBitmap = globalThis.createImageBitmap;
  const originalDocument = globalThis.document;
  let drawn;
  let closed = false;
  globalThis.createImageBitmap = async () => ({
    width: 4032, height: 3024, close: () => { closed = true; },
  });
  globalThis.document = {
    createElement: () => ({
      width: 0,
      height: 0,
      getContext: () => ({ drawImage: (...args) => { drawn = args; } }),
      toBlob: (resolve, type) => resolve(new Blob(['jpeg'], { type })),
    }),
  };
  try {
    const input = new File([new Uint8Array([0xff, 0xd8, 0xff])], '상품.png', {
      type: 'image/png', lastModified: 123,
    });
    const output = await toUploadableImage(input, {
      maxEdge: 1600, minEdge: 400, forceJpeg: true,
    });
    assert.equal(output.name, '상품.jpg');
    assert.equal(output.type, 'image/jpeg');
    assert.deepEqual(drawn.slice(1), [0, 0, 1600, 1200]);
    assert.equal(closed, true);
  } finally {
    if (originalCreateImageBitmap === undefined) delete globalThis.createImageBitmap;
    else globalThis.createImageBitmap = originalCreateImageBitmap;
    if (originalDocument === undefined) delete globalThis.document;
    else globalThis.document = originalDocument;
  }
});

test('여러 장 변환은 이미 취소됐으면 다음 파일을 시작하지 않는다', async () => {
  const controller = new AbortController();
  controller.abort();
  const result = await toUploadableImages(
    [new File(['x'], '상품.jpg', { type: 'image/jpeg' })],
    { maxEdge: 1600, forceJpeg: true },
    { signal: controller.signal },
  );
  assert.deepEqual(result, { files: [], failed: [] });
});

test('첫 파일 변환 중 취소하면 완료 결과를 버리고 다음 파일을 시작하지 않는다', async () => {
  const originalCreateImageBitmap = globalThis.createImageBitmap;
  const originalDocument = globalThis.document;
  const controller = new AbortController();
  let bitmapCalls = 0;
  let releaseBitmap;
  globalThis.createImageBitmap = async () => {
    bitmapCalls += 1;
    await new Promise((resolve) => { releaseBitmap = resolve; });
    return { width: 4032, height: 3024, close: () => {} };
  };
  globalThis.document = {
    createElement: () => ({
      width: 0,
      height: 0,
      getContext: () => ({ drawImage: () => {} }),
      toBlob: (resolve, type) => resolve(new Blob(['jpeg'], { type })),
    }),
  };
  try {
    const files = [
      new File(['a'], '첫째.jpg', { type: 'image/jpeg' }),
      new File(['b'], '둘째.jpg', { type: 'image/jpeg' }),
    ];
    const pending = toUploadableImages(
      files, { maxEdge: 1600, forceJpeg: true }, { signal: controller.signal },
    );
    while (!releaseBitmap) await new Promise((resolve) => setImmediate(resolve));
    controller.abort();
    releaseBitmap();
    assert.deepEqual(await pending, { files: [], failed: [] });
    assert.equal(bitmapCalls, 1);
  } finally {
    if (originalCreateImageBitmap === undefined) delete globalThis.createImageBitmap;
    else globalThis.createImageBitmap = originalCreateImageBitmap;
    if (originalDocument === undefined) delete globalThis.document;
    else globalThis.document = originalDocument;
  }
});

test('이미지 디코드 지연은 제한 시간 내 실패 처리돼 업로드 준비가 멈추지 않는다', async () => {
  const originalCreateImageBitmap = globalThis.createImageBitmap;
  const originalDocument = globalThis.document;
  globalThis.createImageBitmap = () => new Promise(() => {});
  globalThis.document = {
    createElement: () => ({
      width: 0,
      height: 0,
      getContext: () => ({ drawImage: () => {} }),
      toBlob: (resolve, type) => {
        resolve(new Blob(['jpeg'], { type }));
      },
    }),
  };
  try {
    const result = await toUploadableImages([
      new File(['x'], '상품.jpg', { type: 'image/jpeg' }),
    ], { maxEdge: 1600, minEdge: 400, forceJpeg: true, timeoutMs: 20 });
    assert.equal(result.files.length, 0);
    assert.equal(result.failed.length, 1);
    assert.match(result.failed[0].reason, /이미지를 디코드하는 데/);
  } finally {
    if (originalCreateImageBitmap === undefined) delete globalThis.createImageBitmap;
    else globalThis.createImageBitmap = originalCreateImageBitmap;
    if (originalDocument === undefined) delete globalThis.document;
    else globalThis.document = originalDocument;
  }
});

test('JPEG 인코딩 콜백이 멈추면 제한 시간 내 실패 처리돼 무한 대기가 안 생긴다', async () => {
  const originalCreateImageBitmap = globalThis.createImageBitmap;
  const originalDocument = globalThis.document;
  globalThis.createImageBitmap = async () => ({ width: 4032, height: 3024, close: () => {} });
  globalThis.document = {
    createElement: () => ({
      width: 0,
      height: 0,
      getContext: () => ({ drawImage: () => {} }),
      toBlob: () => {},
    }),
  };
  try {
    const result = await toUploadableImages([
      new File(['x'], '상품.jpg', { type: 'image/jpeg' }),
    ], { maxEdge: 1600, minEdge: 400, forceJpeg: true, timeoutMs: 20 });
    assert.equal(result.files.length, 0);
    assert.equal(result.failed.length, 1);
    assert.match(result.failed[0].reason, /이미지 인코딩이 20ms 내에 끝나지/);
  } finally {
    if (originalCreateImageBitmap === undefined) delete globalThis.createImageBitmap;
    else globalThis.createImageBitmap = originalCreateImageBitmap;
    if (originalDocument === undefined) delete globalThis.document;
    else globalThis.document = originalDocument;
  }
});

test('축소 상한은 2K 출력보다 크되 업로드 상한(25MB) 안에 들도록 잡혀 있다', () => {
  // 서버는 업로드 원본을 리사이즈 없이 Gemini 로 보낸다(InlineImage=원본 바이트).
  // 상한이 커지면 생성 요청마다 비용·지연으로 전가되므로 이 두 값이 계약이다.
  assert.ok(MAX_EDGE >= 2500, '마네킹 출력 2K(≈2500px)보다 작으면 디테일이 손해다');
  assert.ok(MAX_EDGE <= 4000, '4000px 를 넘기면 JPEG 가 상한을 위협한다');
  assert.ok(JPEG_QUALITY >= 0.8 && JPEG_QUALITY < 1, '과압축·무압축 둘 다 피한다');
});
