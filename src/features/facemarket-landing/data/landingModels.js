/* =============================================================
   facemarket-landing/data/landingModels.js
   랜딩 캐러셀이 도는 이미지 목록.

   전부 public/models 의 **가상 모델**이다. 실제 등록 모델의 얼굴은 여기
   들어올 수 없다 — 얼굴은 공개 URL 을 갖지 않는다(프라이버시 하드룰 1).
   화면에도 예시라는 고지가 함께 붙는다(GallerySection).

   카드에 붙는 건 번호뿐이다. 이름·연도·평점 같은 메타는 아직 정해지지 않았고,
   지어내면 실재하는 모델 정보로 읽힌다.
   ============================================================= */

const FILES = [
  'women/w1.webp',
  'women/w2.webp',
  'women/w3.webp',
  'women/w4.webp',
  'women/w5.webp',
  'women/w6.webp',
  'women/w7.webp',
  'women/w8.webp',
  'women/w9.webp',
  'women/w10.webp',
  'women/w11.webp',
  'men/m1.webp',
  'men/m2.webp',
  'men/m3.webp',
];

export const LANDING_MODELS = Object.freeze(
  FILES.map((file, index) => {
    const id = file.replace(/^.*\//, '').replace(/\.webp$/, '');
    const number = String(index + 1).padStart(2, '0');
    return Object.freeze({
      id,
      src: `/models/${file}`,
      alt: `가상 모델 예시 이미지 ${number}`,
    });
  }),
);
