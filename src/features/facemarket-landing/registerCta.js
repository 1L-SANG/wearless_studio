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

   등록(/model/register)으로 가는 문구는 **'모델 등록하기' 하나로 통일한다**(2026-09-02
   사용자 지시, #217). 진행 중이든 승인 직후든 게이트가 꺼진 첫 방문이든 같다 — 같은 버튼이
   조회 결과에 따라 늦게 글자를 바꾸는 게 오히려 어수선했고, 위저드가 열리면서 어디까지
   했는지는 그 화면이 알려 준다. 목적지가 다른 것만 다른 문구다: 지원서(모델 지원하기 ·
   다시 지원하기), 등록 상태(내 모델 정보 · 지원 상태 보기).
   ============================================================= */

const REGISTER_LABEL = '모델 등록하기';
/* 신규 방문자 지원 CTA 문구. 2026-09-03 오너 지시로 '모델 지원하기' → 얼리버드 문구.
   export 하는 이유: HeroSection 이 이 라벨과 같을 때만 얼리버드 혜택 칩을 그린다(상태
   판정을 두 벌로 만들지 않기 위해 라벨을 키로 쓴다). */
export const APPLY_LABEL = '얼리버드 지원하기';

export function registerCta(ownedModel, enrollment, { application = null, applicationRequired = true } = {}) {
  if (ownedModel?.status === 'verified') {
    return { label: '내 모델 정보', to: '/status' };
  }
  if (ownedModel || enrollment) {
    return { label: REGISTER_LABEL, to: '/model/register' };
  }
  if (applicationRequired) {
    // 지원서 여정. 진실원천은 /status(ModelHub)이고 여기선 다음 행동만 고른다.
    if (application?.status === 'under_review') return { label: '지원 상태 보기', to: '/status' };
    if (application?.status === 'approved') return { label: REGISTER_LABEL, to: '/model/register' };
    if (application?.status === 'rejected') return { label: '다시 지원하기', to: '/model/apply' };
    return { label: APPLY_LABEL, to: '/model/apply' };
  }
  return { label: REGISTER_LABEL, to: '/model/register' };
}
