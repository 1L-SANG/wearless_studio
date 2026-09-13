# 작업지시서: 라이선스 유효기간 선택 폐지 (영구 기본, 철회로만 종료)

작성 2026-09-11 (Claude). 실행 Codex. 워크트리 `.worktrees/facemarket-license-permanent`, 브랜치 `codex/facemarket-license-permanent`(origin/main 658932e2 기준).

## 0. 제약
- 커밋 금지. 완료 후 Claude 가 diff 리뷰와 테스트를 돌리고 커밋한다.
- `documents/legal/**` 는 건드리지 않는다(Claude 담당). 이 지시서와 보고서 외 `documents/` 수정 금지.
- 마이그레이션은 append-only. 기존 파일 수정 금지, 새 파일만 추가.
- 샌드박스에서 포트 바인딩과 로컬 Postgres 접속이 안 된다. 스크린샷과 DB 물리 확인은 Claude 가 한다.
- 줄표(—) 금지, "A → B" 표기 금지, 해요체. 답 맨 앞에 실행 모델명.
- 요청받지 않은 리팩터링 금지. 바뀐 줄이 전부 아래 항목으로 추적돼야 한다.

## 1. 결정 (오너 2026-09-11 확정)
- 모델은 라이선스 유효기간을 고르지 않는다. 라이선스는 **영구**이고, 끝내려면 **철회**만 있다.
- DB `fm_licenses.license_valid_until` 은 새 라이선스에서 **NULL** 이다. 컬럼은 남긴다(기존 행 이력).
- 증서(VC) 클레임 `licenseValidUntil` 은 키를 유지하고 값은 **`"9999-12-31"`** 로 보낸다. 외부 OpenDID 발급 서버의 facelicense 스키마가 날짜 문자열을 기대할 수 있어서 키를 빼거나 한글을 넣지 않는다(`services/fm-holder/.../IssueVcService.java:239` 는 null 이면 클레임을 빼는데, 발급자 쪽 필수 여부를 확인할 수 없다). C2PA 매니페스트의 `licenseValidUntil` 도 같은 값 `"9999-12-31"`.
- 화면과 공개 검증 API 에서는 만료일이 없으면 **"철회 시까지"** 로 보여준다. 기존 행(날짜 있음)은 지금처럼 날짜를 보여준다.
- "잠시 멈춤"(노출 일시정지)은 이번 범위가 아니다. 마이페이지 시안에 있는 건 그대로 둔다.

## 2. DB
새 파일 `supabase/migrations/20260911120000_fm_license_valid_until_nullable.sql`:
```sql
alter table public.fm_licenses alter column license_valid_until drop not null;
```
그 밖의 스키마 변경 없음.

## 3. 서버 (`server/app`)
1. `facemarket.py`
   - `CreateLicenseRequest.valid_days` 필드 삭제. CamelModel 은 extra 를 forbid 하지 않으므로 옛 클라이언트가 보내도 무시된다. 확인해서 보고서에 적을 것.
   - 라이선스 INSERT 에서 `license_valid_until = NULL`. `valid_until = now + valid_days` 계산 삭제.
   - `build_face_vc_claims(...)`: `valid_until` 이 None 이면 `"licenseValidUntil": "9999-12-31"`. 기존 행 재발급 경로(`retry_pending_face_vcs` 등)에서 날짜가 있으면 지금처럼 KST 날짜.
   - `issue_face_vc(...)` 호출부 두 곳(약 1154, 2121줄)에서 None 을 넘길 수 있게.
2. **만료 게이트 null-safe.** `license_valid_until > now()` 를 그대로 쓰는 곳은 영구 라이선스를 전부 탈락시킨다. 전수 grep 해서 `(l.license_valid_until is null or l.license_valid_until > now())` 로 바꾼다. 현재 확인된 곳: `facemarket_admin_models.py` 217, 298, 400, 909줄. `facemarket.py:370` 은 이미 null-safe. 다른 파일(`facemarket_provenance.py`, `facemarket_admin.py`, `services/*.py`, `workers/*.py`)도 grep 으로 확인해 보고서에 목록을 적을 것.
3. `facemarket_provenance.py` 389줄: `license_valid_until=str(lic.get("license_valid_until") or "")` 은 None 이면 `"9999-12-31"`. 535줄 `licenseValidUntil` 응답은 None 그대로(프론트가 "철회 시까지"로 그린다).
4. `facemarket_admin.py` 364줄은 이미 None 처리됨. 확인만.
5. 공개 검증 API(`/verify/:licenseId` 계열)의 `validUntil` 은 None 을 그대로 내려준다. `expired` 판정 로직이 None 을 만료로 보지 않는지 확인.
6. 상태값 `'expired'` 체크 제약과 관련 코드는 지우지 않는다(기존 행 이력).

## 4. 프론트 (`src`)
1. `src/features/model/ModelLicense.jsx`
   - `VALIDITY` 상수, `validDays` 상태, `TERM_STEPS.validity`, 유효기간 선택 UI(03 단계) 삭제. 조건 단계는 허용 품목(01)과 가격(02)만 남는다. 번호는 01, 02.
   - `createLicense` 호출에서 `validDays` 제거.
   - `VcCard` 의 유효기간 행: `validUntil` 이 null 이면 "철회 시까지".
2. `src/lib/api/facemarket.js` 352~356줄: `validDays` 매개변수와 body 필드 삭제. 402줄 주석의 응답 설명은 유지(validUntil 은 null 가능이라고 한 줄 덧붙임).
3. `src/features/model/modelProfilePreview.js` `validityLabel`: 입력이 null/undefined/비유한이면 `'철회 시까지'`. 3650 이상 `'영구'` 분기는 지운다(더 이상 생기지 않는 값). 숫자 일수는 기존대로.
4. `src/features/facemarket-landing/data/publicModels.js` `formatValidity`, `formatValidUntil`: null 이면 `'철회 시까지'`. 셀러용 카드나 상세 창에서 기한 줄을 그리고 있으면 그 줄이 어디에 보이는지 보고서에 적고, 값은 "철회 시까지"로 통일한다(줄 삭제는 하지 않는다).
5. `src/features/facemarket-landing/payoutData.js` 70~73줄: `validUntil` 이 없으면 만료 아님으로 처리.
6. `src/features/verify/PublicVerify.jsx` 121줄, `PublicVerifyPublication.jsx` 118줄: null 이면 "철회 시까지"(`까지` 접미 중복 주의). `expired` 문구는 유지.
7. `src/features/admin/AdminModels.jsx` 334줄: null 이면 "철회 시까지".
8. `src/features/model/ModelHub.jsx` 234줄, `ModelConfirm.jsx` 147줄, `ModelRegister.jsx` 770줄: `validityLabel` 을 거치므로 3번으로 해결되는지 확인.
9. `src/features/facemarket-landing/facemarketTerms.js` 26줄 `'영구'` 는 `'철회 시까지'` 로.
10. `src/features/facemarket-landing/applyStartFaq.js` 28줄 문장에서 "조건과 유효기간을 정하고" 를 "조건을 정하고" 로.
11. `src/features/facemarket-landing/sections/IntroSection.jsx` 18~19줄 주석에서 유효기간, validDays 언급 삭제.
12. 목 데이터(`src/mock/**`)에 `validDays`, `validUntil` 이 있으면 null 로.

## 5. 시안 (`mockups/facemarket_register_v3_20260910/`, 커밋되지 않는 폴더)
- `v3c_paged.html`: 유효기간 단계와 관련 문구 삭제. 단계 번호 재정렬.
- `mypage.html`: `flag=expiring` 상태, 노랑 띠, "연장하기" 버튼, 옵션 패널의 expiring 항목 삭제. `paused`, `revoked` 는 그대로.
- `_qa/verify_mypage.mjs` 가 expiring 을 검사하면 같이 정리.

## 6. 테스트
- 서버: 아래 파일들에서 `valid_days`, `valid_until`, `licenseValidUntil` 을 쓰는 테스트를 새 동작에 맞춘다.
  `test_facemarket_licenses.py`, `test_facemarket_mandatory_vc.py`, `test_facemarket_mandatory_vc_migration.py`, `test_c2pa_signer.py`, `test_admin_models.py`, `test_retry_pending_face_vcs.py`, `test_biometric_purge.py`, `test_detail_page_identity_source.py`, `test_facemarket_identity.py`, `test_facemarket_contact_email.py`, `test_facemarket_seller_loop.py`, `test_facemarket_publications.py`, `test_kst_timezone.py`, `test_facemarket_cutover.py`, `test_detail_page_license_face.py`, `test_facemarket_publication_verify.py`, `test_facemarket_provenance_migration.py`, `test_facemarket_model_test_cuts.py`, `test_cut_input_authority.py`.
- 새 서버 테스트 `server/tests/test_facemarket_license_permanent.py`:
  1. 라이선스 생성 요청에 `validDays` 가 없어도 201 이고 INSERT 파라미터의 `license_valid_until` 이 None.
  2. 옛 클라이언트가 `validDays: 365` 를 보내도 무시되고 None.
  3. `build_face_vc_claims(valid_until=None)` 이 `"9999-12-31"`, 날짜가 있으면 KST 날짜.
  4. `facemarket_admin_models.py` 의 적격 조건 SQL 문자열에 `license_valid_until is null or` 가 들어 있다(4곳 모두).
  5. 공개 검증 응답이 `validUntil: null` 을 만료로 판정하지 않는다.
- 프론트: `tests/frontend/facemarket-biometric-enrollment.test.mjs`, `facemarket-model-profile-preview.test.mjs`, `facemarket-public-models.test.mjs`, `facemarket-model-hub.test.mjs`, `facemarket-confirm-unavailable.test.mjs`, `datetime-seoul.test.mjs` 를 새 동작에 맞춘다.
- 새 프론트 테스트 `tests/frontend/facemarket-license-permanent.test.mjs`:
  1. `ModelLicense.jsx` 소스에 `VALIDITY`, `validDays`, `유효기간` 문자열이 없다.
  2. `api/facemarket.js` 의 `createLicense` body 에 `validDays` 가 없다.
  3. `validityLabel(null)`, `validityLabel(undefined)` 이 `'철회 시까지'`.
  4. `formatValidity(null)`, `formatValidUntil(null)` 이 `'철회 시까지'`.
- 통과 기준: `cd server && .venv/bin/pytest -q` 전부 통과, `pnpm test:frontend` 전부 통과, `npx vite build` 성공.

## 7. 보고서
`documents/facemarket_license_permanent_impl_report_20260911.md` 에 적는다.
- 바뀐 파일 목록과 한 줄 이유.
- null-safe 로 고친 게이트 SQL 위치 전부(파일:줄).
- 유효기간이 화면에 보이던 자리 전부와 지금 무엇을 보여주는지(도달 경로: 어느 화면의 어느 조작).
- 테스트 결과 숫자(pytest passed/skipped, node pass/fail, build).
- 확신이 없는 곳(예: 발급 서버 스키마가 `9999-12-31` 을 받는지는 실서버에서만 확인 가능)을 "확인 필요"로 남긴다.
