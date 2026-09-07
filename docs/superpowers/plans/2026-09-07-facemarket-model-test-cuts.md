# FaceMarket 모델 테스트컷 확인 게이트 구현 계획

> **For agentic workers:** 이 계획은 현재 세션에서 순서대로 실행한다. 사용자의 명시적 지시에 따라 커밋 단계는 없다.

**Goal:** 관리자가 전송한 비공개 테스트컷을 모델이 직접 확인·승인하기 전에는 셀러가 모델을 선택하거나 사용할 수 없게 한다.

**Architecture:** 기존 FaceMarket 라우터 옆에 테스트컷 전용 모듈을 두고, DB 상태와 비공개/일반 R2 사이의 승격을 한 곳에서 관리한다. 기존 카탈로그와 라이선스 런타임은 `verified` 조건을 그대로 단일 게이트로 사용한다.

**Tech Stack:** FastAPI, psycopg, Cloudflare R2, Pillow, Vite React, CSS Modules

**Spec:** `docs/superpowers/specs/2026-09-07-facemarket-model-test-cuts-design.md`

## Global Constraints

- `supabase/migrations`는 append-only이며 새 마이그레이션만 추가한다.
- 테스트컷 원본은 `r2_face`에만 저장하고 API 응답/로그에 R2 키를 노출하지 않는다.
- 확정 전 모델 상태는 `verified`가 아니어야 한다.
- 모델 확인 동의 문구와 설명문은 사용자 요구 문안을 정확히 사용한다.
- React 훅은 모든 early return 위에 둔다.
- 다른 세션의 미커밋 파일과 `documents/legal/**`를 수정하지 않는다.
- 커밋하지 않는다.

---

### Task 1: 상태·마이그레이션 계약

**Files:**
- Create: `supabase/migrations/20260907120000_fm_model_test_cuts.sql`
- Create: `server/tests/test_facemarket_model_test_cuts_migration.py`

- [ ] 실패 테스트로 상태 값, 컬럼, 테스트컷 테이블/FK/RLS/인덱스를 고정한다.
- [ ] 테스트가 새 파일 부재 또는 계약 누락으로 실패하는지 확인한다.
- [ ] 순방향 마이그레이션을 추가하고 해당 테스트를 통과시킨다.

### Task 2: VC 발급 뒤 공개 차단

**Files:**
- Modify: `server/tests/test_facemarket_licenses.py`
- Modify: `server/app/facemarket.py`

- [ ] 기존 VC 성공 테스트 기대값을 `pending`으로 바꾸고 실패를 확인한다.
- [ ] 모델 상태 갱신은 제거하되 DID 기록과 CAS 검증은 유지한다.
- [ ] `awaiting_confirm`이 카탈로그와 `verify_license_local`에서 거절되는 테스트를 추가해 통과시킨다.

### Task 3: 관리자·모델 테스트컷 API

**Files:**
- Create: `server/tests/test_facemarket_model_test_cuts.py`
- Create: `server/app/facemarket_admin_models.py`
- Modify: `server/app/main.py`
- Modify: `server/app/facemarket_notify.py`
- Modify: `server/app/r2.py`

- [ ] 관리자 403, 업로드 수/MIME/키 비노출, 목록/삭제/전송의 실패 테스트를 작성한다.
- [ ] 모델 소유 조회, 승인 후 커버·동의·verified, redo 1회 제한의 실패 테스트를 작성한다.
- [ ] 전용 라우터, R2 키 헬퍼, Pillow 축소, post-commit Resend 메일을 최소 구현한다.
- [ ] 선택 테스트를 반복 실행해 모두 통과시킨다.

### Task 4: 프론트 API와 상태 매핑

**Files:**
- Modify: `src/lib/api/facemarket.js`
- Modify: `src/features/model/modelHubState.js`
- Modify: `tests/frontend/facemarket-model-hub.test.mjs`

- [ ] `awaiting_confirm`과 redo 상태의 기대 매핑을 먼저 추가해 실패를 확인한다.
- [ ] 관리자/모델 API 함수와 인증 이미지 object URL 헬퍼를 추가한다.
- [ ] 허브 매핑을 구현하고 Node 테스트를 통과시킨다.

### Task 5: 관리자와 모델 UI

**Files:**
- Modify: `src/apps/admin/App.jsx`
- Create: `src/apps/admin/App.module.css`
- Create: `src/features/admin/AdminModels.jsx`
- Create: `src/features/admin/AdminModels.module.css`
- Modify: `src/apps/facemarket/modelSectionRoutes.jsx`
- Create: `src/features/model/ModelConfirm.jsx`
- Create: `src/features/model/ModelConfirm.module.css`
- Modify: `src/features/model/ModelHub.jsx`
- Modify: `src/features/model/ModelPersonalization.module.css`

- [ ] 관리자 상단 탭, 모델 카드/컷 업로드·삭제·전송 상태를 기존 패턴으로 구현한다.
- [ ] 모델 확인 화면의 선택/동의/확정/redo 상태를 구현한다.
- [ ] 허브 상단 CTA와 재생성 중 안내를 연결한다.
- [ ] 프론트 테스트와 Vite 빌드로 문법·번들 경계를 확인한다.

### Task 6: 전체 검증과 보고

**Files:**
- Create: `deliverables/codex/T1_report_2026-09-07.md`

- [ ] `cd server && .venv/bin/pytest -q`를 새로 실행해 실패 0을 확인한다.
- [ ] `npx vite build`를 새로 실행해 exit 0을 확인한다.
- [ ] 실제 라우트 진입과 핵심 화면을 브라우저로 스모크 확인한다.
- [ ] diff를 요구사항·금지 파일 목록과 대조하고 독립 리뷰 결과를 반영한다.
- [ ] 변경 파일, 마이그레이션, 검증 결과, 남은 일을 보고서에 적는다.
