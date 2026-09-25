// 서버 지원 형식과 아이폰 사진 변환 경로를 확인해요.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (p) => readFileSync(fileURLToPath(new URL(p, root)), 'utf8');
const step = () => read('src/features/model/IdDocumentStep.jsx');

test('accept은 지원 형식을 명시하고 아이폰 사진은 변환해서 받아요', () => {
  const source = step();
  assert.match(source, /image\/heic/);
  assert.match(source, /image\/heif/);
  assert.match(source, /await toUploadableImage\(file\)/);
  assert.ok(!/accept=["']image\/\*["']/.test(source), 'image/* 는 HEIC 까지 통과시킨다');
  for (const mime of ['image/jpeg', 'image/png', 'image/webp']) {
    assert.ok(source.includes(mime), `${mime} 가 accept 에 있어야 한다`);
  }
});

test('로드 실패를 화면에 드러낸다 (onError)', () => {
  for (const component of ['IdGalleryFit', 'IdMaskEditor']) {
    assert.match(read(`src/features/model/${component}.jsx`), /onError=/,
      `${component}의 깨진 이미지는 안내 없이 두면 사용자가 원인을 알 수 없다`);
  }
});

test('서버 허용 목록과 클라이언트 목록이 같다', () => {
  const server = read('server/app/facemarket_id_document.py');
  const listed = [...server.matchAll(/"(image\/[a-z]+)"/g)].map((m) => m[1]).sort();
  const client = [...step().matchAll(/image\/(?:jpeg|png|webp)/g)].map((m) => m[0]);
  for (const mime of listed) {
    assert.ok(client.includes(mime), `서버는 ${mime} 를 받는데 클라이언트가 안 건다`);
  }
});

test('고른 직후 형식을 검증한다 — 서버까지 갔다 거부되기 전에', () => {
  const source = step();
  assert.match(source, /ACCEPTED_IMAGE_TYPES|isAcceptedImage/, '형식 검증 지점이 있어야 한다');
});
