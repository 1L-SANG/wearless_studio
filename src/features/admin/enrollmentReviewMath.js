/* 관리자 등록 심사 — 대조 점수 표시 계산. JSX 없는 .js 로 떼어 둔 이유는 node 테스트가
   번들러 없이 직접 import 해서 돌리기 위해서다(AdminDashboard 의 sparklineMath.js 와
   같은 패턴, tests/frontend 참고).

   SFace 코사인은 동일인도 0.2~0.4 대에 걸친다. 0.31 을 "31%" 로만 보여 주면 관리자가
   "69% 다르다"로 읽어 멀쩡한 본인을 거절한다 — 그래서 백분율과 기준선(임계값)을 항상
   같이 낸다. 배지(badge/tone)는 참고 정보일 뿐이다: 위조 신분증도 진짜 얼굴이 찍혀
   있어 점수가 높게 나오는 게 정상이고, 진짜 신분증도 코팅 반사광 한 번에 점수가 낮게
   나올 수 있다 — 그래서 이 결과로 승인/거절 버튼을 절대 막지 않는다(AdminEnrollmentReview.jsx
   의 동일인 확인 체크와 등록 단계로 승인을 결정한다).

   score == null 은 "점수가 낮다"가 아니라 "그 각도에서 얼굴을 아예 못 찾았다"
   (match_scores.skipped)는 뜻이라 danger 와 다른 muted 톤으로 분리한다 — 둘을 같은
   빨간 배지로 뭉치면 관리자가 "위조 의심"과 "정면 검출기가 45도 사진을 못 읽었을
   뿐"을 구분할 수 없다. */
export function scoreRow(angle, score, threshold) {
  // threshold 도 없으면(오늘 백엔드는 항상 세 각도 전부 채우지만, 방어적으로) 비율을
  // 못 구한다 — NaN%/NaN배를 심사자 화면에 내는 대신 muted 로 낮춘다(fix round 1, minor).
  if (score == null || threshold == null) return { angle, label: '– 대조 안 됨', tone: 'muted' };
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

/* 거절 사유 확정값. 'other' 를 고르면 자유 입력을, 그 외엔 프리셋 라벨을 그대로 쓴다.
   자유 입력은 반드시 trim 한다 — 안 하면 스페이스만 입력해도 "   " 는 truthy 라
   !finalReason 가드(빈 사유 금지)를 통과해, 빈 것이나 다름없는 사유가 그대로 누군가의
   거절 기록에 남는다(fix round 1, IMPORTANT). 컴포넌트의 disabled 조건이 이 함수의
   반환값만 보게 해서, trim 여부를 소스텍스트 정규식이 아니라 실제 입력값으로 잠근다. */
export function finalRejectReason(presetValue, freeText, presetLabel) {
  if (presetValue === 'other') return (freeText || '').trim();
  return presetLabel || '';
}

/* 이미지 fetch 실패를 심사자에게 어떻게 말할 것인가. 403(관리자 기기 미승인)을
   "파기됨"으로 그리면 **존재하는 증거를 없다고 믿게 만든다** — 기기 게이트가 enforce 로
   켜진 뒤 심사 화면의 모든 이미지가 실제로 그렇게 보였다(최종리뷰 C4). 상태 코드별로
   문구를 가르고, 알 수 없는 실패는 "파기"라고 단정하지 않는다. */
export function imageFailureLabel(status, kind) {
  if (status === 403) return '권한 없음 (기기 미승인)';
  if (status === 404) return kind === 'id_document' ? '볼 수 없음 (파기됨)' : '볼 수 없음 (없음)';
  return '불러오지 못했어요';
}


export function reviewActions(card) {
  const identityCleared = card.reviewStatus == null || card.reviewStatus === 'approved';
  const canApproveIdentity = card.reviewStatus === 'pending'
    && ['review_pending', 'vc_pending'].includes(card.status);
  return {
    identityCleared,
    canApproveIdentity,
    approvalLabel: card.status === 'review_pending' ? '승인' : '승인하고 증서 발급',
    approvalHint: ['asset_building', 'license_pending'].includes(card.status)
      ? '등록자가 사용 조건을 마치면 승인할 수 있어요.' : '',
  };
}
