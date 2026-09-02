import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';

import { LANDING_MODELS } from '../../src/features/facemarket-landing/data/landingModels.js';

/* =============================================================
   랜딩 캐러셀이 도는 이미지 목록의 계약.

   프라이버시 하드룰 1 — 랜딩에는 public/models 의 **정적 가상모델**만 올라간다.
   실제 등록 모델의 얼굴은 공개 URL 을 갖지 않으므로 여기 들어올 수 없고,
   들어오면 안 된다. 목록이 순수 상수라 런타임 검증이 없으니 파일 경로 한 줄만
   바꿔도 아무 데서도 안 걸린다 — 그래서 여기서 잡는다.

   출처: 설계 스펙 §2 "가상모델 14장(women w1~w11, men m1~m3), 카드에는 번호만,
   `-face` 접미 파일과 pose/ · physique/ 는 쓰지 않는다".
   ============================================================= */

/* 허용 경로는 이 한 줄뿐이다. `-face`(얼굴 크롭)·pose/·physique/(등록 위저드
   안내용 소재)는 전부 여기서 떨어진다. */
const ALLOWED_SRC = /^\/models\/(women|men)\/[wm]\d+\.webp$/;

test('랜딩 카드는 14장이다', () => {
  // 스펙이 못 박은 수. 줄거나 늘면 카드 번호(01~14)와 고지 문구도 같이 손봐야 한다.
  assert.equal(LANDING_MODELS.length, 14);
});

test('id 가 겹치지 않는다', () => {
  // 리스트 key 로 쓰이므로 겹치면 React 가 카드를 잘못 재사용한다.
  const ids = LANDING_MODELS.map((model) => model.id);
  assert.equal(new Set(ids).size, ids.length, `id 중복: ${ids.join(', ')}`);
});

test('가상모델 정적 이미지만 쓴다 — 얼굴 크롭·pose·physique 는 못 들어온다', () => {
  for (const model of LANDING_MODELS) {
    assert.match(model.src, ALLOWED_SRC, `허용되지 않은 소재: ${model.src}`);
  }
});

test('금지된 소재 경로는 패턴에서 떨어진다', () => {
  // 위 단언이 진짜 일을 하는지 — 패턴이 느슨해지면 여기서 먼저 깨진다.
  // 실제로 public/models 에 나란히 존재하는 파일들이다.
  for (const forbidden of [
    '/models/men/m3-face.webp',
    '/models/women/w10-face.webp',
    '/models/pose/front.webp',
    '/models/physique/female/delicate_basic.webp',
  ]) {
    assert.doesNotMatch(forbidden, ALLOWED_SRC, `${forbidden} 가 통과해 버린다`);
  }
});

test('alt 는 예시라고 밝힌다', () => {
  // 화면 고지(GallerySection)와 별개로, 스크린리더에도 "실제 등록 모델이 아니다"가
  // 전달돼야 한다. 카드 메타는 번호뿐이라 alt 가 유일한 설명이다.
  for (const model of LANDING_MODELS) {
    assert.ok(model.alt.includes('예시'), `alt 에 예시 고지가 없다: ${model.alt}`);
  }
});

test('src 가 가리키는 파일이 public 에 실제로 있다', () => {
  // Vite 는 public/ 문자열 경로를 검증하지 않는다. 오타는 배포 뒤 404 로만 드러난다.
  for (const model of LANDING_MODELS) {
    assert.ok(
      existsSync(new URL(`../../public${model.src}`, import.meta.url)),
      `${model.id}: ${model.src} 가 public 에 없다`,
    );
  }
});

test('카드 메타는 번호뿐이다 — 이름·연도·평점을 지어내지 않는다', () => {
  // 이름·연도·평점 같은 건 아직 정해진 게 없어서 붙이면 지어낸 값이 실재하는 모델
  // 정보로 읽힌다. 필드가 늘면 여기서 걸리니, 정말 필요한 필드면(예: 레이아웃용
  // width/height) 이 단언을 의식적으로 같이 고쳐라 — 사람 정보는 여전히 금지다.
  for (const model of LANDING_MODELS) {
    assert.deepEqual(Object.keys(model).sort(), ['alt', 'id', 'src'], `${model.id}: 카드 메타가 늘었다`);
  }
});
