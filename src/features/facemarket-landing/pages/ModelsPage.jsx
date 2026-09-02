/* 모델 둘러보기 — 상단바 첫 항목의 목적지(/models). 아직 화면이 없어 자리만 잡아 둔다.

   ⚠️ 이 페이지를 실제로 만들 때 **등록된 모델의 얼굴을 목록으로 걸 수 없다.** PRD §10 의
   프라이버시 하드룰 1: 얼굴 사진은 공개 주소를 갖지 않는다(권한이 확인된 요청에만 그때그때
   열린다). 예외는 모델이 직접 올린 대표 이미지 하나뿐이고 그것도 1시간짜리 서명 주소다.
   그러니 여기 놓을 수 있는 건 (a) 지금 홈 캐러셀이 쓰는 가상 모델 예시이거나, (b) 대표
   이미지를 올린 모델에 한정한 목록이다. 등록 사실 자체가 노출되는 것도 결정이 필요하다. */
import { PlaceholderPage } from './PlaceholderPage.jsx';

export function ModelsPage() {
  return (
    <PlaceholderPage
      description="모델 둘러보기 화면은 준비 중입니다."
      heading="모델 둘러보기"
      title="모델 둘러보기 — FaceMarket"
    />
  );
}
