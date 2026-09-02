/* 라이선스 페이지 — 상단바 '라이선스'의 목적지(/license). 무인증 공개다.
   옛 주소 /licensing 은 App.jsx 에서 여기로 넘긴다. */
import { LandingShell } from '../LandingShell.jsx';
import { LicensingSection } from '../sections/LicensingSection.jsx';

const TITLE = '라이선스 — FaceMarket';
/* 홈 설명과 같은 눈금이다. 지급(payout)·사용 내역 화면이 없는 지금 "정산받는다"는
   못 쓴다(LicensingSection.jsx 헤더 주석). '해지'로 쓰고 목적어를 붙인다. */
const DESCRIPTION =
  '어떤 품목에 건당 얼마로 얼마 동안 쓸 수 있는지 본인이 정하고, 그 조건이 서명된 '
  + '자격증명으로 발급됩니다. QR 하나로 누구나 확인할 수 있고 언제든 해지할 수 있어요.';

export function LicensingPage() {
  return (
    <LandingShell description={DESCRIPTION} title={TITLE}>
      {({ ctaLabel, onPrimary }) => (
        <LicensingSection ctaLabel={ctaLabel} onPrimary={onPrimary} />
      )}
    </LandingShell>
  );
}
