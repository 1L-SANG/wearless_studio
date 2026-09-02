/* 모델 등록 안내 페이지 — 상단바 '모델 등록'의 목적지(/register). 무인증 공개다.
   실제 등록 위저드는 /model/register(인증 필요)이고, 이 페이지 CTA 가 그리로 보낸다.
   두 경로를 헷갈리지 마라: 여기는 "몇 단계이고 무엇을 요구하는지" 를 미리 보여 주는
   설명 화면이고, 동의·신분증·얼굴 사진을 실제로 받는 곳이 아니다. */
import { LandingShell } from '../LandingShell.jsx';
import { RegisterSection } from '../sections/RegisterSection.jsx';

const TITLE = '모델 등록 — FaceMarket';
const DESCRIPTION =
  '본인 확인이 필요한 절차라 일곱 단계를 순서대로 진행합니다. 체형과 대표 이미지는 '
  + '건너뛸 수 있고, 중간에 나갔다가 이어서 할 수 있어요.';

export function RegisterPage() {
  return (
    <LandingShell description={DESCRIPTION} title={TITLE}>
      {({ ctaLabel, onPrimary }) => (
        <RegisterSection ctaLabel={ctaLabel} onPrimary={onPrimary} />
      )}
    </LandingShell>
  );
}
