/* 신분증 업로드 파일 형식 게이트.

   2026-09-14 프로덕션: 맥 사진앱에서 고른 HEIC 를 올리자 미리보기가 깨진 이미지(얇은 띠)로
   뜨고, 아무 안내도 없이 "확인 요청" 버튼만 영영 비활성이었다. 원인 셋이 겹쳤다:
     · accept="image/*" 가 브라우저가 못 그리는 HEIC 까지 고르게 뒀다
     · 고른 직후 형식 검증이 없어 서버까지 갔다 거부되기 전에 알 수 없었다
     · <img> 에 onError 가 없어 로드 실패가 화면에 드러나지 않았다(onLoad 만 있어
       imageLoaded 가 false 로 남고 제출 버튼이 계속 잠긴다)

   서버가 받는 건 {jpeg, png, webp} 뿐이다(facemarket_id_document.ALLOWED_ID_MIME).
   앞단이 그걸 그대로 반영해야 한다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (p) => readFileSync(fileURLToPath(new URL(p, root)), 'utf8');
const step = () => read('src/features/model/IdDocumentStep.jsx');

test('accept 은 서버가 받는 형식만 — image/* 로 열어두지 않는다', () => {
  const source = step();
  assert.ok(!/accept=["']image\/\*["']/.test(source), 'image/* 는 HEIC 까지 통과시킨다');
  for (const mime of ['image/jpeg', 'image/png', 'image/webp']) {
    assert.ok(source.includes(mime), `${mime} 가 accept 에 있어야 한다`);
  }
});

test('로드 실패를 화면에 드러낸다 (onError)', () => {
  assert.match(step(), /onError=/, '깨진 이미지는 안내 없이 두면 사용자가 원인을 알 수 없다');
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
