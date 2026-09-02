/* 모델 둘러보기 — 상단바 첫 항목의 목적지(/models). 무인증 공개다.

   ⚠️ 지금 보이는 건 **가상 모델 예시**다(browseModels.js). 실데이터를 붙일 때
   등록된 모델의 얼굴을 목록으로 걸 수 없다 — PRD §10 하드룰 1: 얼굴 사진은 공개 주소를
   갖지 않고, 권한이 확인된 요청에만 그때그때 열린다. 예외는 모델이 직접 올린 대표 이미지
   하나뿐이고 그것도 1시간짜리 서명 주소다. 그러니 실제로 걸 수 있는 건 대표 이미지를 올린
   모델로 한정한 목록이고, '누가 등록했다'는 사실 자체를 공개할지부터 결정해야 한다. */
import { LandingShell } from '../LandingShell.jsx';
import { BrowseSection } from '../sections/BrowseSection.jsx';

const TITLE = '모델 둘러보기 — FaceMarket';
const DESCRIPTION =
  '얼굴과 신체 사이즈, 그리고 그 얼굴을 어떤 조건으로 쓸 수 있는지 함께 봅니다. '
  + '지금 보이는 목록은 전부 가상 모델 예시입니다.';

export function ModelsPage() {
  return (
    <LandingShell description={DESCRIPTION} title={TITLE}>
      {() => <BrowseSection />}
    </LandingShell>
  );
}
