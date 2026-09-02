/* 정산 — 상단바 세 번째 항목의 목적지(/payout). 아직 화면이 없어 자리만 잡아 둔다.

   ⚠️ 여기에 정산 절차를 미리 적지 마라. 지금 코드가 하는 데까지는 "사용 1건(= 내 얼굴이 쓰인
   상세페이지 한 건)이 생기면 모델 몫 금액과 함께 기록을 남긴다"이고, 그 기록조차 체인 전송이
   실패하면 안 남는다(best-effort). **실제 지급 기능은 없다** — 라이선싱 페이지와 라이선스
   화면(ModelLicense.jsx)이 이미 "실제 지급 기능은 아직 준비 중이에요"라고 적고 있고,
   상단바에 '정산'이 걸린 것만으로도 이미 한 걸음 앞서간 상태다. 지급이 붙기 전에 이 페이지를
   채우면 두 화면이 서로를 부정한다. */
import { PlaceholderPage } from './PlaceholderPage.jsx';

export function PayoutPage() {
  return (
    <PlaceholderPage
      description="정산 화면은 준비 중입니다."
      heading="정산"
      title="정산 — FaceMarket"
    />
  );
}
