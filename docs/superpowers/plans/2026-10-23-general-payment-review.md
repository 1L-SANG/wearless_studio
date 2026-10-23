# 일반 단건결제 심사 준비 구현계획

## 1. 심사 호스트 계약

- `src/lib/host.js`에 `pay.wearless.kr` 정확 일치 판정과 프로덕션 문서 호스트 등록을 추가한다.
- `tests/frontend/pg-review-host.test.mjs`에 정기결제·일반결제 호스트 분리 검사를 먼저 추가해 실패를 확인한다.
- `copilot/api/manifest.yml`에 API CORS 오리진을 추가하고 `tests/frontend/cors-origins-cover-hosts.test.mjs`로 검증한다.
- `vercel.json`에 `pay.wearless.kr` noindex 헤더를 추가한다.

## 2. 단건결제 전용 가격 화면

- `src/lib/tossKeys.js`에서 일반결제 심사 호스트에만 `TOPUP_ENABLED`를 켜고 계좌이체를 끈다.
- 구독 노출 여부를 별도 상수로 제공해 다른 호스트 동작을 보존한다.
- `src/features/pricing/Pricing.jsx`에서 일반결제 심사 호스트는 구독 카드를 숨기고 단건 상품 두 개와 일반결제 버튼만 표시한다.
- 월간 자동결제 안내가 단건결제 화면에 나오지 않도록 한다.
- `tests/frontend/pg-review-host.test.mjs`에서 세 호스트의 완성 HTML을 비교한다.

## 3. 루트 진입과 회귀 방지

- `src/apps/seller/App.jsx`의 루트 기본 목적지를 일반결제 심사 호스트에서 `/pricing?tab=topup`으로 지정한다.
- 기존 로그인 복귀와 초안 승격 로직은 변경하지 않는다.
- 호스트별 루트 목적지 순수 함수를 두고 단위검사한다.

## 4. 배포와 운영 확인

- 관련 프론트 테스트, 전체 프론트 테스트와 빌드를 실행한다.
- PR을 생성하고 CI 통과 후 main에 머지한다.
- Vercel 프로젝트에 `pay.wearless.kr` 도메인을 연결한다.
- 운영 화면에서 상품, 푸터, 로그인, 토스 일반결제창을 확인한다.

## 5. 제출자료

- 일반결제 가이드 구성에 맞는 별도 PPT를 만든다.
- 가맹점 정보, 푸터, 환불정책, 로그인, 단건 상품 선택, 일반결제창 순서로 캡처한다.
- 필수질문 회신 초안과 휴대폰결제 `확인 Y, 신청 N` 답변을 작성한다.
- 전자계약 신청서의 실제 활성 필수서류는 사용자가 로그인해 업로드한다.
