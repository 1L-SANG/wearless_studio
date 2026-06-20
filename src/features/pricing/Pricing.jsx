/* =============================================================
   features/pricing — 요금제 관리 (/pricing)
   [경계] auth 에이전트는 라우트만 등록한다. 본문(요금제 카드·환불 버튼/모달 등)은
   크레딧 백엔드 에이전트가 소유 — 이 stub 을 교체/구현한다.
   ============================================================= */
export function Pricing() {
  return (
    <div className="wizard">
      <div className="surface" style={{ textAlign: 'center', padding: 48 }}>
        요금제 관리 — 준비 중 (크레딧 에이전트 구현 예정)
      </div>
    </div>
  );
}

export default Pricing;
