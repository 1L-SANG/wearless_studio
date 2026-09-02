/* =============================================================
   facemarket-landing/registerCta.js
   등록 상태 → 상단바 CTA 문구·경로. 상태 라벨은 ModelHub 와 같은 어휘를 쓴다
   (model: pending · reverification_required · verified / application: under_review ·
   approved · rejected · cancelled).

   2026-09-02 사용자 지시: "모델 지원 버튼 누르면 바로 모델 지원 페이지". 지원서 게이트
   (applicationRequired)가 켜져 있으면 신규 방문자의 CTA 는 허브를 거치지 않고 곧장
   /model/apply 다. 게이트가 꺼져 있으면 종전처럼 /model/register 로 간다.

   조회 실패는 여기서 다루지 않는다 — 호출부가 null 을 넘기면 기본값이 나온다. 랜딩이
   조회 결과를 기다리다 비어 있으면 안 된다. applicationRequired 의 기본은 **true** —
   제품 방향이 지원서 플로우라, 설정 조회가 늦거나 실패해도 '모델 지원하기'가 먼저 뜬다.
   ============================================================= */

export function registerCta(ownedModel, enrollment, { application = null, applicationRequired = true } = {}) {
  if (ownedModel?.status === 'verified') {
    return { label: '내 모델 정보', to: '/status' };
  }
  if (ownedModel || enrollment) {
    return { label: '이어서 등록하기', to: '/model/register' };
  }
  if (applicationRequired) {
    // 지원서 여정. 진실원천은 /status(ModelHub)이고 여기선 다음 행동만 고른다.
    if (application?.status === 'under_review') return { label: '지원 상태 보기', to: '/status' };
    if (application?.status === 'approved') return { label: '모델 등록 계속하기', to: '/model/register' };
    if (application?.status === 'rejected') return { label: '다시 지원하기', to: '/model/apply' };
    return { label: '모델 지원하기', to: '/model/apply' };
  }
  return { label: '모델 등록 시작', to: '/model/register' };
}
