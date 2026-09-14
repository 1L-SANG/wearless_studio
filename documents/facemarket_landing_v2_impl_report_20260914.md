# FaceMarket 홈 랜딩 하단 개편 구현 보고서

작성일 2026-09-14

## 바뀐 파일과 이유

- `src/features/facemarket-landing/FacemarketLanding.jsx`
  - 첫 화면 아래에 FoundingSection, RightsSection, DemoSection, 새 HowItWorksSection, 새 FaqSection, ClosingSection을 명세 순서대로 연결했다.
  - FoundingSection과 ClosingSection에 LandingShell의 CTA 라벨과 `onPrimary`를 전달했다.
- `src/features/facemarket-landing/FacemarketLanding.module.css`
  - 새 하단 섹션의 데스크톱과 폰 레이아웃을 시안 수치에 맞춰 추가했다.
  - 새 색상 표현은 `--fm-ink`, `--fm-muted`, `--fm-line`, `--fm-accent`만 사용했다.
  - 데모 그림자는 명세대로 모듈 변수 `--fm-demo-shadow`에 정의했다.
  - 신뢰 pill 스타일은 origin/main과 같은 값으로 복구하고 기존 접이식 FAQ 전용 스타일은 제거했다.
- `src/features/facemarket-landing/sections/GallerySection.jsx`
  - 신뢰 pill 세 개를 origin/main 기준으로 복구했다.
- `src/features/facemarket-landing/sections/FoundingSection.jsx`
  - 파운딩 모델 일곱 자리 중 등록 완료 세 자리, 남은 네 자리, 네 번째 지원하기 버튼을 표시한다.
- `src/features/facemarket-landing/sections/RightsSection.jsx`
  - 초상 원칙 다섯 문장을 추가하고 가격, 모델 몫, 최소 지급액, 지급일을 공용 상수에서 만들었다.
- `src/features/facemarket-landing/sections/DemoSection.jsx`
  - 등록 사진 한 장과 착용컷 세 장을 데스크톱과 폰 레이아웃으로 추가했다.
- `src/features/facemarket-landing/sections/HowItWorksSection.jsx`
  - 기존 다섯 칸 설명을 지원, 검토, 등록의 세 단계로 교체했다.
  - 검토 기한은 `REVIEW_SLA_LABEL`을 사용했다.
- `src/features/facemarket-landing/sections/FaqSection.jsx`
  - 접이식 문답 여덟 개를 항상 보이는 문답 여섯 개로 교체했다.
  - 모든 금액과 정산 수치를 공용 상수에서 만들었다.
  - 계약서와 생체정보 동의서 링크를 추가했다.
- `src/features/facemarket-landing/sections/ClosingSection.jsx`
  - 히어로 제목 클래스와 상단 CTA의 라벨 및 동작을 재사용하는 마무리 CTA를 추가했다.
- `src/features/facemarket-landing/data/foundingModels.js`
  - 파운딩 전체 인원과 등록 완료 이미지 세 장의 경로를 오너가 한 파일에서 바꿀 수 있게 분리했다.
- `src/features/facemarket-landing/data/demoCuts.js`
  - 데모의 등록 사진과 착용컷 경로를 오너가 한 파일에서 바꿀 수 있게 분리했다.
- `src/features/facemarket-landing/data/landingTiming.js`
  - 지원 시간, 등록 시간, 철회 후 삭제 기한을 홈 랜딩 전용 상수로 분리했다.
- `tests/frontend/facemarket-landing-home.test.mjs`
  - 신뢰 pill 세 개, 파운딩 완료 세 자리와 남은 네 자리 및 CTA, 초상 원칙 제목, 세 단계 시간, 항상 보이는 FAQ, 가격 상수 연동을 검증하는 테스트 여섯 개를 추가했다.
- `src/lib/host.js`
  - `/biometric-consent`를 FaceMarket 호스트 허용 경로에 추가해 문서 링크가 등록 화면으로 바뀌지 않게 했다.
- `tests/frontend/host-route-boundary.test.mjs`
  - FaceMarket 호스트에서 `/biometric-consent`가 현재 경로를 유지하는지 단언했다.
- `public/models/founding/mosaic4.webp`
  - 등록 완료 모델이 세 명으로 확정되어 사용하지 않는 네 번째 이미지를 제거했다. 원본은 `/tmp/facemarket-landing-v2-mosaic4.webp`에서 복구할 수 있다.
- `documents/facemarket_landing_v2_impl_report_20260914.md`
  - 구현과 검증 결과를 기록했다.

## 지운 CSS 클래스

- `.faqList`
- `.faqItem`
- `.faqQuestion`
- `.faqAnswer`

`.trustPills`는 오너의 갱신 지시에 따라 삭제하지 않고 origin/main 그대로 복구했다. `.rail`, `.railStep`, `.railNumber`, `.railLabel`, `.railNote`는 HowItWorksSection에서 사용을 제거했지만 CSS에서는 지우지 않았다. 홈 밖의 `/register` 화면에 있는 `RegisterSection.jsx`가 같은 클래스를 계속 사용하므로 삭제하면 해당 화면이 깨진다.

## 동의서 경로 확인 결과

`src/apps/facemarket/App.jsx`에 생체정보 동의서 공개 라우트 `/biometric-consent`와 문서 slug `biometric-consent`가 등록돼 있어 FAQ의 동의서 링크에 `/biometric-consent`를 사용했다. 갱신 지시에 따라 `src/lib/host.js`의 `FACEMARKET_ROUTES`에도 같은 경로를 추가했다. `host-route-boundary.test.mjs`에서 FaceMarket 호스트의 경로 판정 결과가 `null`인지 확인해 `/model/register` 리다이렉트가 발생하지 않도록 검증했다.

## 테스트와 빌드

- RED 확인: `node --test tests/frontend/facemarket-landing-home.test.mjs`
  - 구현 전 6개 실패를 확인했다.
- 랜딩 전용 재검증: `node --test tests/frontend/facemarket-landing-home.test.mjs`
  - 6개 통과, 0개 실패했다.
- 오너 수정 RED 확인: `node --test tests/frontend/facemarket-landing-home.test.mjs tests/frontend/host-route-boundary.test.mjs`
  - 수정 전 12개 중 3개 실패를 확인했다. 실패 항목은 신뢰 pill 부재, 등록 완료 이미지 네 장, `/biometric-consent` 리다이렉트였다.
- 오너 수정 통합 재검증: `node --test tests/frontend/facemarket-landing-home.test.mjs tests/frontend/host-route-boundary.test.mjs`
  - 12개 통과, 0개 실패했다.
- 전체 프런트엔드: `pnpm test:frontend`
  - 1,755개 통과, 0개 실패했다.
  - 샌드박스가 상위 공유 `node_modules`의 Vite 캐시 쓰기를 막아, 의존성은 그대로 공유하고 Vite 캐시만 워크트리 안에 만드는 임시 링크 구성을 사용했다. 실행 뒤 원래 `node_modules` 심볼릭 링크를 복원했다.
  - 일부 기존 테스트에서 포트 개방 권한 경고가 출력됐지만 테스트 실패로 처리되지 않았다.
- 프로덕션 빌드: `npx vite build`
  - 4,877개 모듈 변환 후 성공했다.
  - 기존 동적 import와 큰 청크 경고가 출력됐지만 빌드 종료 코드는 0이었다.

## 확신이 없는 것

- 지시대로 이미지 파일을 열지 않았고 스크린샷도 찍지 않았다. 이미지 경로의 파일 존재와 코드 속성은 확인했지만 실제 크롭과 파운딩 배지의 밝기, 반응형 시각 결과는 Claude의 스크린샷 검토가 필요하다.
