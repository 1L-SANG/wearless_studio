# FaceMarket 협찬 MVP 실행 계획

**목표:** 기존 화면 안에서 선택형 의류 협찬 설정을 저장하고 참여 모델 검색과 출시 알림 신청을 제공해요.
**정본:** 원본 저장소 documents/facemarket_sponsorship_impl_brief_20260922.md와 mockups/facemarket_sponsorship_uiux_20260922/index.html After 화면이에요. 사용자가 이 브리프 실행과 MVP 내 가독성 개선을 요청했어요.
**구조:** fm_models에 프로필 필드를 추가하고 초상 라이선스와 분리된 PATCH로 저장해요. 관심 신청만 별도 테이블에 기록해요. 실제 협찬 거래는 만들지 않아요.
**환경:** Vite, React, CSS Modules, FastAPI, Postgres예요.

## 제약과 판단

- 지정 워크트리 .worktrees/facemarket-sponsorship, codex/facemarket-sponsorship에서 origin/main 기준으로 작업해요. 원본 미커밋 파일은 건드리지 않아요.
- 화면은 기존 --fm-* 토큰과 구성, After 시안을 기준으로 해요. 이미지 변경, 팔로워순 정렬, 요청·배송·검수 흐름을 만들지 않아요.
- 문구는 해요체예요. 화살표 나열과 줄표를 새로 넣지 않아요.
- 라이선스 API와 증서 클레임, 정산을 변경하지 않아요. 별도 법무 지시서는 이번 실행 범위가 아니에요.
- 결정: 준비 중인 요청 기능을 오해하지 않도록 협찬 설명 근처에 '협찬 요청 기능은 준비 중이에요. 지금은 참여 설정만 저장해요.'를 추가해요. 확정된 메인 문구와 세 가지 게시 규칙은 유지해요.
- 결정: 프로필 PATCH가 기존에 없으므로 models/{id}/sponsorship을 추가해요. VC 조건 API를 재사용하면 협찬과 얼굴 권리가 섞이므로 피해야 해요.
- 결정: on은 완성된 계정·팔로워·사이즈 정보만 저장해요. off는 저장된 정보를 지우지 않고 타인에게 노출하지 않아요.
- 결정: interest GET을 POST와 함께 두어 재방문해도 알림 신청 상태가 정확히 보여요. 계정 ID는 서버 인증에서만 얻어요.
- 실행은 독립된 서버와 랜딩 부분을 병렬로 작업하고 부모가 프로필·카탈로그 연결과 통합 검증을 맡아요. 커밋은 부모가 검증된 단위별로 해요. push, PR, DB 적용은 하지 않아요.

## 인터페이스

PATCH /v1/facemarket/models/{id}/sponsorship
입력: sponsorshipEnabled, instagramHandle, instagramFollowers, sizeTop, sizeBottomWaist 중 변경 필드예요.
응답: id와 위 필드, instagramFollowersReportedAt예요. 기준일은 서버가 정해요.
POST/GET /v1/facemarket/models/{id}/sponsorship-interest
응답: { interested: boolean }예요. 로그인 사용자만 신청할 수 있어요.

## 작업과 검증

- [ ] 서버: 소유자 저장 권한, on 필수값·허용 사이즈, off 보존·공개 마스킹, 알림 중복·비공개 대상 차단 테스트를 먼저 실패시키고 구현해요. 기존 fm_models에 append-only 마이그레이션과 interest RLS를 추가해요.
- [ ] 랜딩: 홈 부제·새 섹션·공개 앵커·FAQ와 지원 시작 내용, 지원서 SNS 도움말을 반영해요. 금액은 기존 가격·배분 상수를 사용해요.
- [ ] 설정: 공용 선택 카드와 입력 검증을 만들고 사용 조건 단계에서 증서 발급 전에 별도 저장해요. 마이페이지에서도 저장·off·오류·복원을 확인해요.
- [ ] 카탈로그: 공개 응답 어댑터, 실제 모델 배지, 협찬 필터, 상세 정보와 로그인 후 알림 신청을 연결해요. 가상 모델에는 표시하지 않아요.
- [ ] 통합: pnpm build, node --test tests/frontend/facemarket-*.test.mjs, 전체 서버 pytest를 실행해요. 변경 없는 기존 실패가 있다면 기준 실행과 비교해요.
- [ ] 화면: 홈 새 섹션, 지원 시작, 위저드 on·off, 모델 카드, 상세를 실제 브라우저에서 확인하고 mockups/codex_sponsorship/에 캡처해요. 캡처는 커밋하지 않아요.
- [ ] 변경 전체를 독립적으로 리뷰하고 중요한 오류를 수정한 뒤 커밋과 남은 배포 절차를 보고해요.

## 초기 검증

- 프론트 FaceMarket 기준 실행: 472개 통과, 실패 0개예요.
- 서버 기준 실행: 진행 중이에요. .scratch/sponsorship/baseline-server.log에 기록해요.
