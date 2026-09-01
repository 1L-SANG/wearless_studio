/* 모델 정보 페이지 — 상단바 '모델 정보'의 목적지(/model-info). 무인증 공개다.
   내 등록 상태가 아니라 **프라이버시**다: 방문자는 미등록 모델이고, 등록 전에 가장
   알고 싶은 건 자기 상태가 아니라 내 얼굴이 어떻게 취급되는지다. */
import { LandingShell } from '../LandingShell.jsx';
import { ModelInfoSection } from '../sections/ModelInfoSection.jsx';

const TITLE = '모델 정보 — FaceMarket';
const DESCRIPTION =
  '얼굴은 생체정보라 일반 사진과 같은 규칙으로 다루지 않습니다. 무엇을 저장하고 무엇을 '
  + '저장하지 않는지, 그만두고 싶을 때 무엇을 할 수 있는지 적어 뒀어요.';

export function ModelInfoPage() {
  return (
    <LandingShell description={DESCRIPTION} title={TITLE}>
      {({ ctaLabel, onPrimary }) => (
        <ModelInfoSection ctaLabel={ctaLabel} onPrimary={onPrimary} />
      )}
    </LandingShell>
  );
}
