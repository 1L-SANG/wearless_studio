/* 등록 얼굴 사진은 **원본 바이트 그대로** 올린다 (2026-09-15).

   이전에는 얼굴 칸도 셀러 상품 사진용 함수(toUploadableImage)를 그대로 썼다 — 긴 변 4000px,
   JPEG 0.85 재인코딩. 그 규칙은 "생성 비용"을 줄이려고 만든 것이고, 얼굴 등록 사진은 곧
   LoRA 학습셋이라 같은 규칙을 먹이면 48MP 원본이 12MP 손실본으로 학습에 들어간다.
   v7 은 48MP 원본을 무손실 PNG 로 바꿔 학습했다.

   여기서 고정하는 것:
     1. 얼굴 업로드 경로는 toUploadableImage 를 **부르지 않는다**.
     2. 미리보기는 따로 만들고(toPreviewImage) **업로드하지 않는다**.
     3. 셀러 상품 사진 경로(ProductInput·AnalysisForm·Editor)의 변환은 그대로다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');

const FACE_UPLOAD_FILES = [
  'src/features/model/ModelRegister.jsx',
  'src/features/model/ModelFaceUpload.jsx',
];
const SELLER_FILES = [
  'src/features/product-input/ProductInput.jsx',
  'src/features/analysis/AnalysisForm.jsx',
  'src/features/editor/Editor.jsx',
];

test('얼굴 등록 경로는 상품 사진용 축소·재인코딩을 쓰지 않는다', () => {
  for (const path of FACE_UPLOAD_FILES) {
    const source = read(path);
    assert.ok(
      !/toUploadableImages?\s*\(/.test(source),
      `${path} 가 아직 toUploadableImage 로 얼굴을 재인코딩한다 — 그만큼이 학습에서 영영 사라진다`,
    );
    assert.match(source, /toPreviewImage/, `${path} 에 미리보기 경로가 없다`);
  }
});

test('업로드에 넘기는 건 사용자가 고른 파일 자체다', () => {
  // 미리보기 결과(preview)를 업로드에 넘기면 축소본이 학습셋이 된다 — 바뀐 게 없는 것처럼
  // 보이는 가장 위험한 회귀라, 호출 인자를 문자열로 못박는다.
  const register = read('src/features/model/ModelRegister.jsx');
  assert.match(register, /uploadEnrollmentPhoto\(\{[^}]*fileBlob:\s*file[,\s}]/);
  const upload = read('src/features/model/ModelFaceUpload.jsx');
  assert.match(upload, /photoApi\.upload\(\{\s*angle,\s*fileBlob:\s*file,/);
  assert.match(upload, /const file = picked;/);
});

test('셀러 상품 사진 경로의 변환은 그대로다', () => {
  for (const path of SELLER_FILES) {
    assert.match(read(path), /toUploadableImages?\b/, `${path} 의 상품 사진 변환이 사라졌다`);
  }
});

test('미리보기 함수는 업로드 규칙과 다른 상수를 쓴다', () => {
  const source = read('src/lib/imageTranscode.js');
  assert.match(source, /export const PREVIEW_MAX_EDGE = 1024;/);
  // 상품 사진 상수는 손대지 않는다(4000px · 0.85 · 25MB).
  assert.match(source, /export const MAX_EDGE = 4000;/);
  assert.match(source, /export const JPEG_QUALITY = 0\.85;/);
  assert.match(source, /export const MAX_UPLOAD_BYTES = 25 \* 1024 \* 1024;/);
});

test('미리보기 실패가 업로드를 막지 않는다', () => {
  const source = read('src/lib/imageTranscode.js');
  const body = source.slice(source.indexOf('export async function toPreviewImage'));
  assert.match(body.slice(0, body.indexOf('\n}')), /catch\s*\{\s*\n\s*return null;/);
});

test('서버 얼굴 업로드 상한·형식이 프런트 설명과 같다', () => {
  const server = read('server/app/facemarket_enrollment.py');
  assert.match(server, /MAX_FACE_BYTES = 40 \* 1024 \* 1024/);
  for (const mime of ['image/heic', 'image/heif']) {
    assert.ok(server.includes(`"${mime}"`), `서버가 ${mime} 를 안 받는다`);
  }
});
