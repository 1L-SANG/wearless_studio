/* =============================================================
   features/credits — 크레딧 사용 내역 (/credits/history)
   [경계] auth 에이전트는 라우트만 등록한다. 본문(사용 내역·환불 등)은
   크레딧 백엔드 에이전트가 소유 — 이 stub 을 교체/구현한다.
   ============================================================= */
export function CreditsHistory() {
  return (
    <div className="wizard">
      <div className="surface" style={{ textAlign: 'center', padding: 48 }}>
        크레딧 사용 내역 — 준비 중 (크레딧 에이전트 구현 예정)
      </div>
    </div>
  );
}

export default CreditsHistory;
