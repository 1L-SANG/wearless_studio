# FaceMarket 모델 테스트컷 확인 게이트 설계

## 목표

VC 발급과 모델 공개를 분리한다. VC 발급 뒤 모델은 `pending`으로 남고, 관리자가 비공개 테스트컷을 전송한 뒤 모델이 대표 컷과 공개 품질에 동의해야만 `verified`가 된다.

## 상태와 데이터

- `fm_models.status`에 `awaiting_confirm`을 추가한다.
- `confirm_requested_at`, `confirmed_at`, `confirm_consent_version`, `redo_count`를 모델 행에 기록한다.
- `fm_model_test_cuts`는 모델별 원본 컷의 비공개 R2 키와 MIME, 순서, 승인 여부를 보관한다. RLS를 켜고 직접 접근 정책은 두지 않아 백엔드 service role만 원장을 읽고 쓴다.
- VC와 enrollment는 정상 발급/완료 상태를 유지하되 모델 카탈로그 사용 가능 여부는 오직 `fm_models.status='verified'`가 연다.

## 서버 흐름

1. VC 발급 성공은 라이선스와 enrollment만 활성/완료하고 모델에는 DID만 기록한다. 모델 상태는 `pending` 또는 재검증 상태를 유지한다.
2. 관리자는 1~6장의 이미지(모델 전체 최대 6장)를 얼굴 전용 R2에 올린다. API 응답에는 키 대신 인증 이미지 URI만 포함한다.
3. 관리자가 보내기를 누르면 컷 존재를 잠금 안에서 재확인하고 `awaiting_confirm`과 요청 시각을 커밋한다. 메일은 커밋 뒤 best-effort로 보낸다.
4. 모델은 본인 소유 행으로만 컷을 조회한다. 승인 시 선택 컷 원본을 읽고 긴 변 1024px WebP로 축소해 일반 R2 카탈로그 경로에 저장한 뒤, 잠금 안에서 선택 컷 승인·동의 시각/버전·커버 키·`verified`를 원자 반영한다.
5. 다시 만들기는 `awaiting_confirm`에서 한 번만 가능하다. 첫 요청은 `pending`, `redo_count=1`로 바꾸고 두 번째는 409 `redo_limit`이다.
6. 확정된 승인 원본은 법적 확인 증거이므로 관리자 삭제를 409로 막는다.

## 프론트 흐름

- 관리자 앱은 공통 상단 탭으로 지원서와 모델을 구분한다. 모델 카드는 상태·등록 상태·컷 수를 먼저 보여주고, 펼치면 인증 fetch로 받은 썸네일과 업로드/삭제/전송 동작을 제공한다.
- `/model/confirm`은 소유 모델이면 접근 가능하다. 큰 컷 선택, 정확한 동의 문구, 공개/재생성 CTA를 한 흐름으로 제공한다.
- 모델 허브는 `awaiting_confirm`을 확인 CTA로, `pending && redoCount>0`을 재생성 중 상태로 표시한다.

## UI 참조 잠금

- 주 참조: `AdminApplications`의 기능 중심 카드, 상태 배지, `useToast`/`ErrorState` 처리.
- 주 참조: `ModelHub`의 FaceMarket 타임라인과 옅은 파랑 행동 패널.
- 보조 규칙: 기존 토큰, 의미 있는 상태색, 클릭 가능한 전체 라디오/체크박스 라벨, `:focus-visible`, 고정 이미지 비율.
- 새 색·장식·가짜 그래픽을 만들지 않는다. 테스트컷 이미지가 화면의 주 시각 요소다.

## 검증

- 서버 계약은 실패 테스트부터 추가한다: VC 뒤 pending, confirm 뒤 verified/cover/동의 기록, redo 1회, awaiting_confirm 카탈로그·런타임 차단, 관리자 403, R2 키 비노출.
- 프론트 상태 매핑 Node 테스트, 전체 `pytest -q`, `npx vite build`, 브라우저 스모크를 실행한다.
- 기존 미커밋 파일과 `documents/legal/**`는 변경하지 않고 커밋도 만들지 않는다.
