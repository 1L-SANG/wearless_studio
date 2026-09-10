# FaceMarket 모델 지원 플로우 구현 보고 (2026-09-10)

## 1. 바꾼 파일 목록과 각 파일 한 줄

총 40개입니다. 사용자가 제공한 구현 지시서 파일은 수정하지 않았습니다.

| 파일 | 변경 |
| --- | --- |
| [documents/facemarket_apply_flow_impl_report_20260910.md](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/documents/facemarket_apply_flow_impl_report_20260910.md) | 지시서 §9 형식의 변경 목록, 검증 결과, 도달 경로와 후속 작업을 기록했습니다. |
| [server/app/facemarket_applications.py](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/server/app/facemarket_applications.py) | v3 제출 검증, 선택 지역과 몸무게, 다섯 확인 항목, 저장과 조회 응답을 연결했습니다. |
| [server/app/main.py](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/server/app/main.py) | 지원서 제출의 요청 형식 오류만 HTTP 400으로 반환합니다. |
| [server/app/services/biometric_purge.py](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/server/app/services/biometric_purge.py) | 계정 삭제 시 새 몸무게 필드도 지우며 구버전 DB의 기존 파기는 유지합니다. |
| [server/tests/test_facemarket_application_form_v3.py](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/server/tests/test_facemarket_application_form_v3.py) | 지역 생략, 몸무게 저장과 조회, 필수값과 확인 항목, 실제 HTTP 응답, 파기, 마이그레이션을 검증합니다. |
| [src/apps/facemarket/App.jsx](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/apps/facemarket/App.jsx) | 공개 지원 시작 라우트 /apply를 추가했습니다. |
| [src/features/admin/AdminApplications.jsx](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/features/admin/AdminApplications.jsx) | 지역과 카테고리 대신 몸무게와 에이전시 경험을 표시합니다. |
| [src/features/auth/AuthProvider.jsx](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/features/auth/AuthProvider.jsx) | 명시적인 개발 mock 모드의 FaceMarket에서만 가상 계정으로 화면을 확인할 수 있게 했습니다. |
| [src/features/facemarket-landing/FacemarketLanding.module.css](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/features/facemarket-landing/FacemarketLanding.module.css) | 네 메뉴 간격, 흰 지원 화면 셸, 768px 근처의 상단 버튼 공간을 조정했습니다. |
| [src/features/facemarket-landing/LandingHeader.jsx](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/features/facemarket-landing/LandingHeader.jsx) | 지원 화면에서 비로그인 사용자의 로그인 버튼과 현재 경로 복귀를 제공합니다. |
| [src/features/facemarket-landing/LandingShell.jsx](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/features/facemarket-landing/LandingShell.jsx) | 공개 CTA는 /apply로 이동하고, 지원과 상태 화면은 시안에 맞는 흰 셸을 사용합니다. |
| [src/features/facemarket-landing/applyStartFaq.js](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/features/facemarket-landing/applyStartFaq.js) | 정본 FAQ 10개와 공통 가격, 정산 상수를 연결했습니다. |
| [src/features/facemarket-landing/facemarketRootTarget.js](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/features/facemarket-landing/facemarketRootTarget.js) | 모델 지원을 첫 공개 메뉴로 추가하고 /apply를 복귀 경로로 허용합니다. |
| [src/features/facemarket-landing/facemarketTerms.js](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/features/facemarket-landing/facemarketTerms.js) | REVIEW_SLA_LABEL을 24시간 이내로 변경했습니다. |
| [src/features/facemarket-landing/pages/ApplyStartPage.jsx](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/features/facemarket-landing/pages/ApplyStartPage.jsx) | 지원 시작 소개, 얼굴 4장, 혜택, 자격, 키보드 말풍선과 FAQ를 구현했습니다. |
| [src/features/facemarket-landing/pages/ApplyStartPage.module.css](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/features/facemarket-landing/pages/ApplyStartPage.module.css) | 1280px 두 칸, 390px 한 칸, 개별 FAQ 카드의 반응형 스타일입니다. |
| [src/features/facemarket-landing/pages/StatusPage.jsx](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/features/facemarket-landing/pages/StatusPage.jsx) | 흰 상태 화면 셸과 로그인 계정 이메일을 허브에 연결했습니다. |
| [src/features/facemarket-landing/registerCta.js](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/features/facemarket-landing/registerCta.js) | landing scope의 신규 지원 목적지만 /apply로 변경했습니다. |
| [src/features/facemarket-shell/FacemarketModelLayout.jsx](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/features/facemarket-shell/FacemarketModelLayout.jsx) | 지원서와 완료 화면에서 공통 푸터를 숨겼습니다. |
| [src/features/facemarket-shell/FacemarketModelLayout.module.css](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/features/facemarket-shell/FacemarketModelLayout.module.css) | 지원서 라우트의 배경을 흰색으로 지정합니다. |
| [src/features/model/ModelApply.jsx](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/features/model/ModelApply.jsx) | 기본 정보, 프로필, 확인의 세 단계와 접수 완료, 고치기 복귀, 새 payload를 구현했습니다. |
| [src/features/model/ModelApply.module.css](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/features/model/ModelApply.module.css) | 위저드 트랙, 입력 폭, 사진 슬롯, 확인 목록, 하단 바와 완료 화면 스타일입니다. |
| [src/features/model/ModelHub.jsx](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/features/model/ModelHub.jsx) | 상태별 제목, 현재 단계 설명, 행동 카드, 취소와 미승인 사유를 구현했습니다. |
| [src/features/model/ModelPersonalization.module.css](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/features/model/ModelPersonalization.module.css) | 다섯 단계의 연결선, 파란 강조, 그라데이션 행동 카드와 미승인 화면 스타일입니다. |
| [src/features/model/birthdateInput.js](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/features/model/birthdateInput.js) | 숫자 입력, 자동 포커스, 백스페이스, 붙여넣기, 달력 날짜와 성인 여부를 판정합니다. |
| [src/features/model/modelHubState.js](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/features/model/modelHubState.js) | 모든 지원과 등록 상태를 새 다섯 단계의 index와 다음 행동에 매핑합니다. |
| [src/lib/api/facemarket.js](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/lib/api/facemarket.js) | 개발 mock 모드의 지원서, 업로드, 취소와 허브 조회를 목 자료로 연결합니다. |
| [src/lib/host.js](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/lib/host.js) | 도메인 가드가 새 /apply 화면을 등록 화면으로 돌려보내지 않도록 허용했습니다. |
| [src/mock/facemarket.js](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/mock/facemarket.js) | 신규 지원과 검토 중, 승인, 미승인 자료 및 사진 제출과 취소 상태를 제공합니다. |
| [src/styles/tokens.css](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/src/styles/tokens.css) | FAQ의 14px 라운드와 사진 안내 체크 색 토큰을 추가했습니다. |
| [supabase/migrations/20260910180000_facemarket_application_form_v3.sql](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/supabase/migrations/20260910180000_facemarket_application_form_v3.sql) | region의 NOT NULL을 해제하고 범위 제약이 있는 weight_kg를 추가합니다. |
| [tests/frontend/adult-consent.test.mjs](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/tests/frontend/adult-consent.test.mjs) | 만 19세 확인 문구의 새 정본을 검증합니다. |
| [tests/frontend/facemarket-apply-mock.test.mjs](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/tests/frontend/facemarket-apply-mock.test.mjs) | 목 상태와 실제 개발 API 어댑터의 HTTP 없는 제출, 조회, 취소를 검증합니다. |
| [tests/frontend/facemarket-apply-start.test.mjs](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/tests/frontend/facemarket-apply-start.test.mjs) | 공개 첫 메뉴, 라우트와 정본 FAQ 10개의 제목과 답을 검증합니다. |
| [tests/frontend/facemarket-biometric-enrollment.test.mjs](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/tests/frontend/facemarket-biometric-enrollment.test.mjs) | 세 단계 버튼 조건, 사진 업로드, 새 payload, 완료 화면, 고치기, 재지원과 시작 화면 렌더를 검증합니다. |
| [tests/frontend/facemarket-birthdate-input.test.mjs](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/tests/frontend/facemarket-birthdate-input.test.mjs) | 생년월일 자동 넘김, 삭제, 붙여넣기와 유효성을 10개 테스트로 검증합니다. |
| [tests/frontend/facemarket-landing-routing.test.mjs](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/tests/frontend/facemarket-landing-routing.test.mjs) | 네 메뉴와 공개 지원 경로의 기존 라우팅 계약을 갱신했습니다. |
| [tests/frontend/facemarket-model-hub.test.mjs](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/tests/frontend/facemarket-model-hub.test.mjs) | 다섯 단계 배열과 상태별 다음 행동을 검증합니다. |
| [tests/frontend/facemarket-register-cta.test.mjs](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/tests/frontend/facemarket-register-cta.test.mjs) | 랜딩 신규 지원 CTA의 /apply 목적지를 검증합니다. |
| [tests/frontend/host-route-boundary.test.mjs](/Users/daily/Documents/wearless_studio/.worktrees/facemarket-apply/tests/frontend/host-route-boundary.test.mjs) | /apply가 FaceMarket 도메인에서 유지되는지 검증합니다. |

## 2. §8 검증 결과

| 항목 | 실행 명령 | 결과 |
| --- | --- | --- |
| 빌드 | `npx vite build` | 통과. 4,815개 모듈 변환, 30.89초, 종료 코드 0. 기존 큰 청크 경고가 있습니다. |
| 프론트 전체 | `node --test tests/frontend/*.test.mjs` | 1,316 tests, 1,316 pass, 0 fail, 0 skip, 27.25초, 종료 코드 0. |
| 서버 전체 | `cd server && .venv/bin/pytest -q` | 4,013 passed, 24 skipped, 105 errors, 391 warnings, 62.94초. 전체 통과는 아닙니다. |
| 서버 변경 관련 | `cd server && .venv/bin/pytest -q tests/test_facemarket_application_form_v3.py tests/test_facemarket_application_hardening.py tests/test_biometric_purge.py` | 88 passed, 3 skipped. 신규 v3 파일 자체는 26개입니다. |
| 목 모드 어댑터 | `node --test tests/frontend/facemarket-apply-mock.test.mjs` | 3 passed. 실제 API facade가 HTTP 없이 제출, 상태 조회, 취소하는 것을 검증했습니다. |
| 목 개발 서버 | `pnpm dev:mock --host 127.0.0.1 --port 5188` | `listen EPERM 127.0.0.1:5188`로 실행 차단. 브라우저 렌더 확인은 남았습니다. |
| 파일 검사 | `git diff --check`, 기존 마이그레이션 diff, 코드와 테스트의 이전 SLA 검색 | 공백 오류 0, 기존 SQL 변경 0, `보통 1시간 안` 검색 결과 0. |

서버 오류 105개는 모두 기존 `tests/test_personalization.py`의 설정 단계에서 로컬 PostgreSQL `127.0.0.1:54322` 접속이 `Operation not permitted`로 거부되어 발생했습니다. 테스트를 삭제하거나 건너뛰도록 바꾸지 않았습니다. 허용된 환경에서 같은 전체 명령을 다시 실행해야 합니다.

브라우저 스모크는 지시서대로 오너 담당입니다. 390px과 1280px, 768px 근처 상단바, 실제 생년월일 자동 포커스와 백스페이스는 브라우저에서 확인해야 합니다. 코드 리뷰와 컴포넌트 테스트가 이 시각 검증을 대신하지 않습니다.

목 모드에서 직접 확인할 주소는 다음과 같습니다. Vite 기본 포트 5173 기준이며 각 주소를 새로 열면 해당 자료로 시작합니다.

- 지원 시작: `http://localhost:5173/apply?facemarket=1&fmMock=new`
- 빈 지원서: `http://localhost:5173/model/apply?facemarket=1&fmMock=new`
- 검토 중: `http://localhost:5173/status?facemarket=1&fmMock=review`
- 승인: `http://localhost:5173/status?facemarket=1&fmMock=approved`
- 미승인: `http://localhost:5173/status?facemarket=1&fmMock=rejected`
- 비로그인 시작 화면은 지원 시작 주소 끝에 `&mockAuth=anonymous`를 붙입니다.

목 인증과 자료는 `DEV`이면서 `VITE_API_MODE=mock`일 때만 사용합니다. 프로덕션 산출물에서 가상 계정과 자료 키가 포함되지 않는 것도 확인했습니다.

최종 코드 리뷰와 상단바 후속 재검토는 승인됐으며, 확인된 중요 결함 두 건인 HTTP 응답과 계정 삭제 시 몸무게 잔존을 모두 수정했습니다. 이 승인은 위의 DB 테스트와 브라우저 검증 미완료를 대체하지 않습니다.

## 3. 각 화면의 도달 경로

| 화면 | 어느 화면의 어느 버튼으로 도달하는지 |
| --- | --- |
| 지원 시작 `/apply` | 상단바의 `모델 지원`, 랜딩 히어로의 `얼리버드 지원하기`, 지원 없는 관리 화면의 `얼리버드 지원하기` |
| 지원서 기본 정보 | 지원 시작의 `지원서 쓰기`, 미승인 관리 화면의 `다시 지원하기`. 비로그인은 기존 가드가 로그인 후 `/model/apply`로 복귀시킵니다. |
| 지원서 프로필 | 기본 정보의 `다음`, 확인 화면 프로필 묶음의 `고치기` |
| 지원서 확인 | 프로필의 `다음`. 확인에서 `고치기`로 돌아간 단계의 `다음`도 바로 확인으로 돌아옵니다. |
| 접수 완료 | 확인 화면에서 다섯 항목에 표시한 뒤 `지원 완료` |
| 검토 중 관리 `/status` | 접수 완료의 `지원 상태 보기`, 상단바의 `Digital DNA 관리`. 현재 지원서가 검토 중이면 표시됩니다. |
| 승인된 관리 `/status` | 상단바의 `Digital DNA 관리`, 승인 메일 링크. 현재 지원서가 승인되면 표시됩니다. |
| 미승인 관리 `/status` | 상단바의 `Digital DNA 관리`. 검토 결과가 미승인이면 사유와 `다시 지원하기`가 표시됩니다. |
| 등록 `/model/register` | 승인 상태의 행동 카드 `등록 시작하기`, 등록 도중 같은 카드의 `등록 이어가기` |
| 조건과 증서 `/model/license` | 해당 등록 단계가 남았을 때 행동 카드의 `조건·증서 이어가기` |
| 최종 검토 | 등록 제출 후 상단바 `Digital DNA 관리`. 현재 단계 설명과 `검토 상태 새로고침`을 제공합니다. |
| 이미지 선택 `/model/confirm` | 프로필 이미지 확정 단계 행동 카드의 `이미지 선택하기` |
| 개인정보 전문 `/privacy` | 확인 화면 다섯 번째 체크 항목의 `개인정보 처리 약관` 링크로 새 탭에 표시 |
| 관리자 지원서 상세 | 관리자 셸의 `지원서 검토` 메뉴에서 `/applications` |

`지원 취소`는 검토 중 여정 아래 오른쪽 밑줄 버튼이며 기존 `cancelApplication`을 실행합니다. 기존 활동 중 대시보드는 유지했습니다.

## 4. 지시와 다르게 푼 것과 이유

- 기획과 필드 계약의 편차는 없습니다. 추가로 필요한 연결부를 수정했습니다.
- `/apply`는 앱 라우트만 추가하면 도메인 가드가 등록 화면으로 돌려보내므로 `src/lib/host.js`에도 허용했습니다. 랜딩 CTA도 기존 로그인 선행 실행을 바꿔 공개 시작 화면에 직접 도달하게 했습니다.
- `LandingShell`과 모델 공통 레이아웃에 시안용 흰 배경과 푸터 숨김을 적용했습니다. 지원과 상태 화면의 상단바에서는 중복 지원 CTA를 빼고 로그인 또는 로그아웃을 표시합니다. 768px부터 1023px까지는 랜딩의 보조 상단 지원 버튼을 숨기며 본문 CTA는 유지합니다.
- `www.` 형태의 링크 예시는 기존 서버가 그대로 받지 못하므로 제출 시 생략된 `https://`를 보완합니다.
- 기존 전역 요청 오류 응답이 422여서, 필수 필드 누락 시 400이라는 지시를 지키기 위해 지원서 제출 API에만 400을 적용했습니다.
- 새 몸무게가 계정 삭제 뒤 남지 않게 기존 개인정보 파기 경로도 보완했습니다. 구버전 DB에서는 기존 파기가 계속됩니다.
- 목 자료만 있던 것이 아니라 FaceMarket API가 실서버 전용이었으므로 개발 모드 전용 어댑터와 가상 인증도 연결했습니다. 실제 인증 가드 코드는 수정하지 않았습니다.
- `CLAUDE.md`가 워크트리에 없어 원본 저장소 `/Users/daily/Documents/wearless_studio/CLAUDE.md`를 읽었습니다.
- 공유 `node_modules` 링크의 임시 캐시 쓰기가 차단되어 검증 중 워크트리 안에서 캐시를 썼습니다. 검증 후 원래 공유 링크로 복원했습니다. 패키지 내용과 의존성 파일은 수정하지 않았습니다.

## 5. 지시한 쪽이 결정해야 할 것

새로 결정할 제품 사항은 없습니다. 남은 작업은 권한이 허용된 환경의 서버 전체 테스트와 오너 브라우저 스모크입니다. 새 SQL 파일은 작성만 했으며 실제 DB에 적용하지 않았습니다.

git add, 커밋, 푸시는 하지 않았습니다. 기존 마이그레이션과 mockups는 수정하지 않았고, mockups 파일을 복사하지 않았습니다.

쉬운 설명: 로그인 전에 지원 내용을 읽고, 세 단계로 지원서를 보낸 뒤 관리 화면에서 다음 할 일을 확인할 수 있게 연결했습니다. 자동 테스트와 빌드는 통과한 범위를 명시했으며, 이 환경에서 막힌 DB 검사와 실제 화면 확인은 남아 있습니다.
