/* 관리자 등록 심사 — 대조 점수 표시 계산. JSX 없는 .js 로 떼어 둔 이유는 node 테스트가
   번들러 없이 직접 import 해서 돌리기 위해서다(AdminDashboard 의 sparklineMath.js 와
   같은 패턴, tests/frontend 참고).

   SFace 코사인은 동일인도 0.2~0.4 대에 걸친다. 0.31 을 "31%" 로만 보여 주면 관리자가
   "69% 다르다"로 읽어 멀쩡한 본인을 거절한다 — 그래서 백분율과 기준선(임계값)을 항상
   같이 낸다. 배지(badge/tone)는 참고 정보일 뿐이다: 위조 신분증도 진짜 얼굴이 찍혀
   있어 점수가 높게 나오는 게 정상이고, 진짜 신분증도 코팅 반사광 한 번에 점수가 낮게
   나올 수 있다 — 그래서 이 결과로 승인/거절 버튼을 절대 막지 않는다(AdminEnrollmentReview.jsx
   의 마스킹 체크박스만 승인을 막는다).

   score == null 은 "점수가 낮다"가 아니라 "그 각도에서 얼굴을 아예 못 찾았다"
   (match_scores.skipped)는 뜻이라 danger 와 다른 muted 톤으로 분리한다 — 둘을 같은
   빨간 배지로 뭉치면 관리자가 "위조 의심"과 "정면 검출기가 45도 사진을 못 읽었을
   뿐"을 구분할 수 없다. */
export function scoreRow(angle, score, threshold) {
  if (score == null) return { angle, label: '– 대조 안 됨', tone: 'muted' };
  const ratio = score / threshold;
  const tone = score < threshold ? 'danger' : ratio >= 2 ? 'ok' : 'warn';
  const badge = score < threshold ? '✗ 미달' : ratio >= 2 ? '✓ 통과' : '△ 아슬';
  return {
    angle,
    percent: `${Math.round(score * 100)}%`,
    baseline: `기준 ${Math.round(threshold * 100)}%`,
    multiple: `기준의 ${ratio.toFixed(1)}배`,
    badge,
    tone,
  };
}
