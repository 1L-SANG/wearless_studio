GPT-6

# 영구 라이선스 구현 보고서

2026-09-11 작업지시서에 따라 현재 워크트리에서 구현했어요. 프론트 전체 테스트와 빌드는 통과했어요. 서버 전체 pytest는 직접 실행했지만 로컬 Postgres 접근 제한으로 105개 오류가 남아 있어 §6 전체 통과 기준을 충족했다고 보고하지 않아요.

## 제약과 지시서 차이

- 워크트리는 `.worktrees/facemarket-license-permanent`, 브랜치는 `codex/facemarket-license-permanent`예요. 커밋하지 않았고 시작과 종료 HEAD는 `658932e272aa249d00866df81dc222a9efcd2d1c`로 같아요.
- `documents/`에서는 이 보고서만 작성했어요. 작업지시서는 수정하지 않았어요. `documents/legal`에는 제 작업에서 쓰지 않았어요. 작업 도중 다른 작업의 것으로 보이는 legal 7개 파일 변경이 나타났고 되돌리거나 덮어쓰지 않았어요. 최종 diff의 legal 변경은 이번 구현의 변경 목록에서 제외해요.
- 기존 마이그레이션은 수정하거나 삭제하지 않았어요. 신규 SQL은 `alter table public.fm_licenses alter column license_valid_until drop not null;` 한 문장뿐이에요.
- **마이그레이션 파일명은 지시서와 달라요.** 요청한 `20260911120000`은 기존 `20260911120000_fm_licenses_unit_price_default_14900.sql`이 사용하고 있어 첫 전체 검사에서 버전 중복 테스트가 실패했어요. 기존 이력을 보존하고 이후 번호인 `20260911160000_fm_license_valid_until_nullable.sql`로 새 파일을 추가했어요. 최종 버전 중복 검사는 통과했어요.
- 시안 폴더가 현재 워크트리에 없어 원본 저장소의 지정 파일 3개를 같은 상대 경로로 복사한 뒤 수정했어요. 원본 저장소의 파일은 수정하지 않았어요. 이 폴더는 git ignore 대상이라 일반 git diff에 나타나지 않아요.
- NULL을 반환하는 실제 API 경로를 위해 지시서의 동작에 필요한 응답 모델도 nullable로 바꿨어요. `LicenseCard.license_valid_until`, `PublicVerifyResult.valid_until`, `ProfileLicenseView.valid_until`과 `valid_days`예요. 이를 바꾸지 않으면 정상 생성과 공개 모델 응답이 직렬화 오류를 내요.
- 유지해야 하는 VC 카드 행은 ‘사용 기간’으로 이름을 바꿨어요. 지시서 §6의 `ModelLicense.jsx`에 ‘유효기간’ 문자열이 없어야 한다는 조건도 충족해요.

## 서버와 마이그레이션 변경 파일

| 파일 | 이유 |
| --- | --- |
| `supabase/migrations/20260911160000_fm_license_valid_until_nullable.sql` | 신규 라이선스의 NULL 만료일을 저장할 수 있도록 NOT NULL만 해제해요. |
| `server/app/facemarket.py` | 요청 valid_days를 없애고 신규 INSERT에 None을 전달해요. nullable 응답과 영구 VC 날짜 문자열을 지원하며 기존 날짜 재발급은 유지해요. |
| `server/app/facemarket_admin_models.py` | 적격 조건 SQL 4곳과 프로필 응답의 날짜·일수를 null-safe로 바꿨어요. |
| `server/app/facemarket_provenance.py` | NULL 기한의 C2PA 클레임에 날짜 문자열을 전달해요. |
| `server/tests/test_facemarket_license_permanent.py` | 새 통합·단위 테스트 10개로 생성, 구클라이언트 호환, VC, SQL 4곳, 프로필, 공개 검증, C2PA를 검증해요. |
| `server/tests/test_facemarket_licenses.py` | 공통 신규 발급 요청에서 validDays를 제거해 기간 없는 생성 경로를 사용해요. |
| `server/tests/test_c2pa_signer.py` | 매니페스트 키에 영구 문자열과 기존 날짜가 모두 유지되는지 확인해요. |
| `server/tests/test_retry_pending_face_vcs.py` | 재발급 스크립트가 None과 기존 날짜를 그대로 발급 함수에 전달하는지 확인해요. |
| `server/tests/test_facemarket_model_test_cuts.py` | DB 대역의 만료 판정을 null-safe로 맞추고 공개 모델 목록에서 영구·기존 기한을 함께 검증해요. |
| `server/tests/test_facemarket_identity.py` | 카탈로그 적격 조건의 DB 대역도 NULL 만료일을 허용하도록 맞췄어요. |

## 프론트 변경 파일

| 파일 | 이유 |
| --- | --- |
| `src/features/model/ModelLicense.jsx` | 기간 선택 상수, 상태, 03 단계, 전송 필드를 제거했어요. VC 카드의 ‘사용 기간’ 행은 null일 때 ‘철회 시까지’를 표시해요. |
| `src/lib/api/facemarket.js` | createLicense 인자와 요청 body에서 validDays를 제거하고 공개 검증 validUntil의 null 가능성을 주석에 적었어요. |
| `src/features/model/modelProfilePreview.js` | null, undefined, 비유한 값은 ‘철회 시까지’를 반환하고 3650일 이상을 영구로 바꾸는 분기를 제거했어요. 양의 숫자는 기존 연수와 일수 표기를 유지해요. |
| `src/features/facemarket-landing/data/publicModels.js` | 공개 모델 어댑터의 기간과 종료일 포매터가 null을 ‘철회 시까지’로 반환해요. |
| `src/features/verify/PublicVerify.jsx` | 공개 라이선스 검증 날짜 행에서 null을 ‘철회 시까지’로 표시해요. |
| `src/features/verify/PublicVerifyPublication.jsx` | 공개 배포본 검증 날짜 행을 null이어도 유지하고 ‘철회 시까지’로 표시해요. |
| `src/features/admin/AdminModels.jsx` | 관리자 모델 상세의 라이선스 종료일이 null이면 ‘철회 시까지’로 표시해요. |
| `src/features/analysis/AnalysisForm.jsx` | 셀러의 실제 모델 상세 창에서 null이면 사라지던 기한 행을 유지하고 ‘철회 시까지’로 표시해요. 지시서 §4.4 셀러 상세 창 조건에 해당해요. |
| `src/features/facemarket-landing/facemarketTerms.js` | 공통 조건표의 null 라벨을 ‘철회 시까지’로 바꿨어요. |
| `src/features/facemarket-landing/applyStartFaq.js` | FAQ에서 유효기간을 선택한다는 안내를 삭제했어요. |
| `src/features/facemarket-landing/sections/IntroSection.jsx` | 주석에서 유효기간과 validDays 입력 설명을 삭제했어요. |
| `tests/frontend/facemarket-license-permanent.test.mjs` | 신규 6개 테스트로 기간 선택과 전송 필드 제거, null 라벨, 정산 상태, 셀러 실제 상세 컴포넌트의 기간 행을 확인해요. |
| `tests/frontend/facemarket-biometric-enrollment.test.mjs` | 기간 없는 발급 body와 허브, 완료 화면을 검증하고 VC 카드 및 두 공개 검증 화면의 null과 기존 KST 날짜 렌더를 확인해요. |
| `tests/frontend/facemarket-model-profile-preview.test.mjs` | 3650일은 10년, 4000일은 4000일, null은 ‘철회 시까지’라는 새 계약을 확인해요. |
| `tests/frontend/facemarket-public-models.test.mjs` | 공개 모델의 null 기간과 종료일 기대값을 바꿨어요. |
| `tests/frontend/facemarket-model-hub.test.mjs` | 공통 조건표의 null 라벨 기대값을 바꿨어요. |
| `tests/frontend/facemarket-confirm-unavailable.test.mjs` | 영구 라이선스에서도 프로필 확정 버튼이 활성이고 ‘철회 시까지’가 보이는지 검증해요. |
| `tests/frontend/facemarket-apply-start.test.mjs` | 이전 FAQ 문서를 기준으로 하는 비교에서 최신 지시서가 대체한 기간 선택 안내 한 문장만 반영해요. FAQ 문서는 수정하지 않았어요. |


## 시안 변경 파일

| 파일 | 이유 |
| --- | --- |
| `mockups/facemarket_register_v3_20260910/v3c_paged.html` | 기간 선택 상태·UI·이벤트·안내를 제거하고 철회 시까지 유효하다고 표시해요. 조건 안의 기간 선택만 있었으므로 상위 본인확인, 사진, 조건, 증서 4단계 번호와 전이는 연속으로 유지해요. |
| `mockups/facemarket_register_v3_20260910/mypage.html` | expiring 상태, 노랑 띠, 연장하기와 선택 패널 항목을 제거하고 기간 행에 철회 시까지를 표시해요. paused와 revoked는 유지해요. |
| `mockups/facemarket_register_v3_20260910/_qa/verify_mypage.mjs` | 삭제된 만료 상태와 노랑 띠 검사를 제거하고 상태 조합을 14개, 화면·패널 포함 56조합으로 맞췄어요. |
| `documents/facemarket_license_permanent_impl_report_20260911.md` | 변경 근거와 실제 검사 결과 및 확인 필요 항목을 기록해요. |

## 만료 게이트 전수 확인

`server/app` 전체와 `server/scripts` 전체에서 `license_valid_until`, `valid_until`, `licenseValidUntil`, `validUntil`을 검색하고 비교 연산과 호출부를 확인했어요. 아래는 수정한 SQL 전부예요.

| 위치 | 경로 | 결과 |
| --- | --- | --- |
| `server/app/facemarket_admin_models.py:217` | 모델 확인용 프로필과 공개 모델 목록 `_load_model_profiles` | `(l.license_valid_until is null or l.license_valid_until > now())`로 수정했어요. |
| `server/app/facemarket_admin_models.py:298` | 모델 테스트컷 조회의 활성 라이선스 판정 | 같은 NULL 허용 조건으로 수정했어요. |
| `server/app/facemarket_admin_models.py:400` | 관리자 테스트컷의 발송 준비 여부 | 같은 NULL 허용 조건으로 수정했어요. |
| `server/app/facemarket_admin_models.py:909` | 발송과 확정에서 쓰는 `_ensure_active_license` | 같은 NULL 허용 조건으로 수정했어요. |

나머지 검색 결과도 확인했어요.

- `server/app/facemarket.py:374`: 공통 카탈로그 조건은 이미 NULL을 허용해요.
- `server/app/facemarket.py:2395`: `_is_expired`는 None이면 False예요. 공개 검증과 실제 사용 게이트에서 공통으로 사용해요. 기존 날짜와 expired 상태는 유지해요.
- `server/app/facemarket.py:1465`: 공개 API `validUntil`은 None을 그대로 반환해요. 신규 통합 테스트가 HTTP 200, `validUntil: null`, `valid: true`, `status: active`를 확인했어요.
- `server/app/facemarket_admin.py:364`: 관리자 응답은 이미 None을 지원해 변경하지 않았어요. 별도 SQL 만료 비교는 없어요.
- `server/app/facemarket_provenance.py:515`: 배포본 공개 검증도 `_is_expired`를 사용해요. `:535`의 `licenseValidUntil`은 None을 그대로 반환해요. 별도 SQL 만료 비교는 없어요.
- `server/app/services/c2pa_signer.py:53`, `:94`: 입력 날짜 문자열을 `licenseValidUntil` 키에 전달할 뿐 만료 게이트가 없어요. 다른 `services` 파일에도 직접 만료일 참조가 없어요.
- `server/app/workers`: 해당 날짜 필드의 직접 참조나 추가 만료 비교를 찾지 못했어요.
- `server/scripts/retry_pending_face_vcs.py:66`, `:97`: 저장된 기한을 조회해서 발급 함수에 그대로 넘겨요. 추가 만료 게이트는 없어요.

## DB, VC와 공개 응답의 구분

- `server/app/models.py:23`의 CamelModel에는 extra forbid 설정이 없어요. `CreateLicenseRequest`에서 valid_days를 없앤 뒤 Pydantic의 기본 extra ignore 동작으로 옛 `validDays: 365`를 무시해요. 새 테스트가 요청 모델의 필드 제거와 실제 HTTP 생성 결과를 모두 확인해요.
- 신규 생성의 `server/app/facemarket.py:1065`는 None이고 INSERT 10번째 파라미터도 None이에요. 기존 pending 라이선스의 재시도와 동시 생성 충돌 복구는 `:1093`, `:1128`에서 기존 저장 날짜를 유지해요.
- VC의 `server/app/facemarket.py:2061`은 None일 때 **`"licenseValidUntil": "9999-12-31"`**을 만들어요. 키를 생략하지 않고 한글도 넣지 않아요. 실제 발급 호출 `:1153` 및 내부 클레임 생성 `:2117`에서 None을 전달할 수 있어요.
- 날짜 있는 기존 VC는 `_kst_date_str`을 그대로 사용해요. UTC `2027-09-07T23:00:00Z`가 KST 날짜 `2027-09-08`로 발급되는 테스트도 통과했어요.
- C2PA는 `server/app/facemarket_provenance.py:389`에서 같은 문자열 `"9999-12-31"`을 전달해요. 실제 sign 라우트가 조립해 서명기에 넘기는 매니페스트까지 테스트했어요. 기존 날짜는 기존 문자열 표현을 유지해요.
- 화면용 API 날짜에는 위 VC 문자열을 주입하지 않아요. DB의 NULL은 API에서 null이고 화면이 ‘철회 시까지’로 그려요.

## 화면 위치와 도달 경로

| 화면 | 도달 경로와 근거 | 현재 값 |
| --- | --- | --- |
| 라이선스 발급 조건 | 등록 과정에서 ‘VC 발급 하러 가기’ (`src/features/model/ModelRegister.jsx:797`), `/model/license` 라우트 (`src/apps/facemarket/modelSectionRoutes.jsx:145`) | 허용 품목 01 (`ModelLicense.jsx:444`)과 플랫폼 표준가 02 (`ModelLicense.jsx:475`)만 있어요. 이전 03 기간 선택은 없어졌어요. |
| 모델 VC 카드 | `/status`의 ‘계약서·증서 보기’ (`src/features/model/ModelHub.jsx:202`)를 눌러 `/model/license`로 들어가요. | `src/features/model/ModelLicense.jsx:300`의 ‘사용 기간’ 행에서 null은 ‘철회 시까지’예요. 기존 날짜는 연월과 KST 날짜를 유지해요. |
| Digital DNA 관리 | 상단 관리 메뉴의 `/status` (`src/apps/facemarket/App.jsx:72`), 로그인된 방문자는 ModelHub를 렌더해요 (`src/features/facemarket-landing/pages/StatusPage.jsx:43`). | 활동 중 대시보드 `src/features/model/ModelHub.jsx:233`의 유효기간은 null일 때 ‘철회 시까지’예요. |
| 모델 프로필 확정 | 관리 화면에서 ‘이미지 선택하기’ (`src/features/model/modelHubState.js:64`)로 `/model/confirm`에 들어가요. | `src/features/model/ModelConfirm.jsx:147`의 유효기간은 null일 때 ‘철회 시까지’예요. |
| 등록 완료 | 라이선스 발급 성공 시 `/model/register`에 completionSummary를 전달해요 (`src/features/model/ModelLicense.jsx:598`). | `src/features/model/ModelRegister.jsx:770`의 유효기간은 null일 때 ‘철회 시까지’예요. |
| 라이선스 공개 검증 | 관리 화면의 검증 링크 (`src/features/model/ModelHub.jsx:215`) 또는 VC 카드 ‘QR 보기’ (`src/features/model/ModelLicense.jsx:343`), `/verify/:licenseId` 라우트 (`src/apps/facemarket/App.jsx:109`)로 열어요. | `src/features/verify/PublicVerify.jsx:112`에서 null은 ‘철회 시까지’, 날짜는 KST 날짜와 ‘까지’예요. |
| 배포본 공개 검증 | 외부 배포본 검증 주소 `/verify/p/:publicationId`로 직접 들어가요 (`src/apps/facemarket/App.jsx:112`, `src/apps/seller/App.jsx:672`). 앱 내부 별도 진입 버튼은 없어요. | `src/features/verify/PublicVerifyPublication.jsx:108`에서 null이어도 행을 유지하며 ‘철회 시까지’를 표시해요. |
| 관리자 모델 상세 | 관리자 앱 `/models` (`src/apps/admin/App.jsx:38`)에서 모델 행을 선택해요 (`src/features/admin/AdminModels.jsx:445`). 상세 패널은 484줄에서 열려요. | `src/features/admin/AdminModels.jsx:334`의 라이선스 종료일은 null일 때 ‘철회 시까지’예요. |
| 셀러 실제 모델 상세 | 셀러 `/create/input` (`src/apps/seller/App.jsx:648`)의 상품 분석 입력 화면은 AnalysisForm을 렌더해요 (`src/features/product-input/ProductInput.jsx:1449`). ‘실제 모델’ 탭 (`src/features/analysis/AnalysisForm.jsx:1278`)에서 활성 모델 카드 (`AnalysisForm.jsx:1334`)를 눌러 상세 창을 열어요. | `src/features/analysis/AnalysisForm.jsx:119`의 기한 행은 null일 때 ‘철회 시까지’예요. 기존 날짜는 KST로 유지해요. |
| 공개 모델 둘러보기 | `/models` (`src/apps/facemarket/App.jsx:69`)에서 카드 (`src/features/facemarket-landing/sections/BrowseSection.jsx:92`)를 누르면 상세 창을 열어요 (122줄). | 카드와 상세 창에는 현재 기한 줄이 없어요. `ModelDetailDialog.jsx:147`도 오너 지시로 기한을 표시하지 않는다고 명시해요. 어댑터 값만 null이면 ‘철회 시까지’로 맞췄어요. |

ModelLicense.jsx에는 지시서의 소스 검사 조건대로 `VALIDITY`, `validDays`, `유효기간` 문자열이 전혀 없어요. 유지해야 하는 VC 카드 행의 라벨은 ‘사용 기간’으로 바꿨어요.


시안에서는 `v3c_paged.html?step=3`의 조건 화면에서 기간 선택이 없어졌고, `step=4c`의 실패 요약과 `step=done`의 완료 안내는 철회 시까지 유효하다고 표시해요. `mypage.html?mode=active`의 사용 조건 기간 행은 ‘철회 시까지’이고, 옵션 패널에 만료 임박 항목이 없어요. 일시정지와 철회 상태의 조작은 유지해요.

## 지정 파일 중 수정하지 않은 곳

- `src/features/facemarket-landing/payoutData.js:72`는 날짜가 있을 때만 만료를 비교하므로 이미 null-safe예요. 새 회귀 테스트가 영구 active, revoked, 기존 날짜 만료를 확인해요.
- `ModelHub.jsx`, `ModelConfirm.jsx`, `ModelRegister.jsx`는 공통 validityLabel 변경으로 해결되어 별도로 수정하지 않았어요. 실제 화면 렌더 테스트를 보강했어요.
- `src/mock/**`에는 validDays와 validUntil 검색 결과가 없어요.
- `tests/frontend/datetime-seoul.test.mjs`는 날짜 포매터의 기존 KST 계약이 그대로여서 수정하지 않았고 전체 실행에서 통과했어요.
- `publicModels.js`의 기존 숫자 일수 이력 표현은 보존했어요. `modelProfilePreview.js`의 3650 이상 ‘영구’ 분기만 지시대로 제거했어요. 공개 둘러보기 카드와 상세 창은 원래 기간 행이 없어요.
- §6에서 열거한 서버 테스트 19개 파일을 모두 검색하고 실행했어요. 위 변경 목록에 없는 파일들은 기존 날짜 이력·만료 판정·마이그레이션 이력 테스트이거나 이미 None을 사용하는 경로라 유지했어요. 예를 들어 `test_kst_timezone.py`의 기존 KST 날짜 회귀, `test_facemarket_seller_loop.py`의 과거 기한 차단, 기존 마이그레이션 테스트는 그대로예요.

## 직접 실행한 검사 결과

| 검사 | 실제 결과 |
| --- | --- |
| 수정 전 새 서버 테스트 | 7 failed, 3 passed예요. 신규 저장 기한, VC, SQL 게이트, 응답 모델, C2PA의 기존 동작에서 실패하는 것을 확인했어요. |
| 수정 후 `cd server && .venv/bin/pytest -q tests/test_facemarket_license_permanent.py` | 10 passed, 0 skipped, 1 warning이에요. |
| §6 서버 지정 19개 파일, 새 permanent 파일, `test_migration_versions.py` | 549 passed, 9 skipped, 1 warning, exit 0이에요. |
| 최종 `cd server && .venv/bin/pytest -q` | **4667 passed, 63 skipped, 105 errors, 391 warnings, exit 1**이에요. 일반 assertion 실패는 0개지만 전체 통과는 아니에요. |
| 최종 `pnpm test:frontend` | **1402 pass, 0 fail, 0 skipped, 0 cancelled, exit 0**이에요. |
| 최종 `npx vite build` | **성공, exit 0**, Vite 6.4.3, 7.16초예요. 500 kB 초과 청크 경고는 남아 있어요. |
| `node mockups/facemarket_register_v3_20260910/_qa/verify_mypage.mjs` | 정적·VM 검사 1936 passed, 0 failed, 0 blocked, 1 미실행이에요. 브라우저 검사는 실행하지 않았어요. |
| `git diff --check -- server src tests supabase` | exit 0이에요. |
| 독립 코드 리뷰 | 수정해야 할 실질적인 결함을 찾지 못했어요. 리뷰 담당자가 신규 서버 10개와 프론트 6개도 별도 실행해 통과했어요. |

서버 전체의 105개 오류는 전부 `tests/test_personalization.py`의 공통 DB fixture에서 발생했어요. 로컬 `127.0.0.1:54322` 접속이 `Operation not permitted`로 차단됐어요. 이 테스트들을 지우거나 강제 skip하지 않았어요. 63 skipped는 원래 테스트의 실행 조건에 따른 실제 집계예요.

첫 전체 실행에는 버전 충돌 외에 `test_face_render_bundle.py::test_bundle_is_reproducible`도 실패했어요. 해당 번들 코드나 테스트를 수정하지 않았으며, 최종 전체 실행에서는 통과했어요. 첫 실행 결과는 4662 passed, 63 skipped, 2 failed, 105 errors였어요. 이 일시적 실패도 숨기지 않고 기록해요.

처음 프론트와 빌드는 `node_modules`가 워크트리 밖 공용 디렉터리의 심볼릭 링크라 Vite 캐시와 `.vite-temp` 쓰기가 EPERM으로 실패했어요. 중간 진단에서 임시 Node hook 및 `--configLoader runner`를 사용했지만, 위 최종 결과는 그 옵션 없이 지정 명령 그대로 실행한 결과예요. 워크트리의 ignored `node_modules`를 로컬 디렉터리로 구성하고 각 패키지를 기존 공용 패키지에 연결해 `.vite`와 `.vite-temp`만 현재 워크트리에 생성하도록 했어요. 패키지·lockfile·Vite 제품 설정은 바꾸지 않았어요. 원래 링크 값은 `/tmp/facemarket-permanent-checks/node-modules-link.txt`에 남겼어요.

실행 로그는 `/tmp/facemarket-permanent-checks/`의 `pytest-final.log`, `pytest-targeted.log`, `frontend-final.log`, `vite-build-final.log`, `mockup-qa.log`에 있어요. 원인 분석에 사용한 첫 실행 로그와 grep 결과도 같은 폴더에 있어요.

## 확인 필요

- **서버 전체 통과는 확인 필요예요.** 샌드박스 밖에서 로컬 Postgres에 접속할 수 있는 환경으로 동일한 전체 명령을 다시 실행해야 해요.
- **DB 적용과 물리 확인이 필요해요.** 새 마이그레이션을 실제 DB에 적용하지 않았어요. 컬럼 null 허용과 신규 행 NULL 저장은 Claude가 물리 DB에서 확인해야 해요.
- **실제 OpenDID 발급 서버의 `9999-12-31` 수용 여부는 확인 필요예요.** 이번에는 전송 payload와 키·값을 테스트했고 실서버 발급을 실행하지 않았어요.
- **실제 C2PA 서명 파일의 외부 검증과 화면 스크린샷은 확인 필요예요.** 라우트·매니페스트 테스트와 빌드는 통과했지만 실배포 파일과 브라우저 화면은 확인하지 않았어요.
- **기존 FAQ 정본 문서의 기간 선택 안내가 남아 있어요.** `documents/facemarket_apply_faq.md`는 문서 수정 금지 범위라 유지했고 최신 지시서의 한 문장만 제품과 테스트 기대값에 반영했어요.
- **시안 인계는 파일 복사가 필요해요.** 수정한 ignored 시안 3개는 현재 워크트리에 있어요. 일반 tracked diff만 리뷰하면 누락되므로 Claude가 원본 저장소에 반영할 때 함께 확인해야 해요.
