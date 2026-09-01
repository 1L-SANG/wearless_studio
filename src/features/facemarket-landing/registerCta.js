/* =============================================================
   facemarket-landing/registerCta.js
   등록 상태 → CTA 문구·경로. 상태 라벨은 ModelHub 와 같은 어휘를 쓴다
   (model: pending · reverification_required · verified).

   조회 실패는 여기서 다루지 않는다 — 호출부가 null 을 넘기면 기본값인
   '모델 등록 시작'이 나온다. 랜딩이 조회 결과를 기다리다 비어 있으면 안 된다.
   ============================================================= */

export function registerCta(ownedModel, enrollment) {
  if (ownedModel?.status === 'verified') {
    return { label: '내 모델 정보', to: '/model' };
  }
  if (ownedModel || enrollment) {
    return { label: '이어서 등록하기', to: '/model/register' };
  }
  return { label: '모델 등록 시작', to: '/model/register' };
}
