/* =============================================================
   facemarket-landing/registerCta.js
   등록 상태 → CTA 문구·경로. 상태 라벨은 ModelHub 와 같은 어휘를 쓴다
   (model: pending · reverification_required · verified).

   조회 실패는 여기서 다루지 않는다 — 호출부가 null 을 넘기면 기본값인
   '모델 등록하기'가 나온다. 랜딩이 조회 결과를 기다리다 비어 있으면 안 된다.

   등록으로 가는 문구는 **'모델 등록하기' 하나로 통일한다**(2026-09-02 사용자 지시).
   전에는 진행 중이면 '이어서 등록하기', 처음이면 '모델 등록 시작'으로 갈렸는데, 같은
   버튼이 조회 결과에 따라 늦게 글자를 바꾸는 게 오히려 어수선했다. 목적지는 어차피
   /model/register 하나이고, 위저드가 열리면서 어디까지 했는지는 그 화면이 알려 준다.
   등록을 마친 사람만 다른 문구(내 모델 정보)로 갈라진다 — 그건 목적지도 다르다. */

const REGISTER_LABEL = '모델 등록하기';

export function registerCta(ownedModel, enrollment) {
  if (ownedModel?.status === 'verified') {
    return { label: '내 모델 정보', to: '/model' };
  }
  // 진행 중이든 처음이든 같은 문구·같은 목적지다(머리말 참고).
  void enrollment;
  return { label: REGISTER_LABEL, to: '/model/register' };
}
