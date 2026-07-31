import test from 'node:test';
import assert from 'node:assert/strict';

import { isHeic, renameExt, MAX_EDGE, JPEG_QUALITY } from '../../src/lib/imageTranscode.js';

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

test('축소 상한은 2K 출력보다 크되 업로드 상한(25MB) 안에 들도록 잡혀 있다', () => {
  // 서버는 업로드 원본을 리사이즈 없이 Gemini 로 보낸다(InlineImage=원본 바이트).
  // 상한이 커지면 생성 요청마다 비용·지연으로 전가되므로 이 두 값이 계약이다.
  assert.ok(MAX_EDGE >= 2500, '마네킹 출력 2K(≈2500px)보다 작으면 디테일이 손해다');
  assert.ok(MAX_EDGE <= 4000, '4000px 를 넘기면 JPEG 가 상한을 위협한다');
  assert.ok(JPEG_QUALITY >= 0.8 && JPEG_QUALITY < 1, '과압축·무압축 둘 다 피한다');
});
