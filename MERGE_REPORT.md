# 머지 보고서 — `feat/fm-id-capture-branch` ← `origin/main`

워크트리: `/Users/nojeong-un/devs/wearless_studio-id-capture`
백업: `backup/pre-merge-2` (머지 시작 전 우리 쪽 HEAD)
베이스: `7ee62637` / 우리: `0ae0b5c2` / 저쪽: `74f3637a` (#280·#285·#287 등)

---

## 1. 파일별 해소 결정

### 컨트롤러가 이미 해소한 3개 (재작업 안 함, 단 1건 버그 수정)

| 파일 | 결정 |
|---|---|
| `server/app/config.py` | 양쪽 설정 모두 유지 — 로딩 확인 완료(검증 5) |
| `src/apps/admin/App.jsx` | 양쪽 라우트 모두 유지 |
| `src/features/admin/AdminShell.jsx` | 양쪽 nav 항목 유지 — **다만 `lucide-react` import 문이 두 줄 그대로 붙어 있어 `pnpm build` 가 "symbol already declared" 5건으로 죽었다.** 한 줄로 합쳤다(`Banknote, Camera, FileText, Flag, IdCard, LayoutDashboard, ShieldCheck, Users`). 컨트롤러가 경고한 "함정 1"(양쪽을 그냥 이어붙이면 깨진다)이 여기서 한 번 더 실현됐다. |

### `src/lib/api/facemarket.js` (4 블록)

- **import 블록** → 우리 것. 우리 쪽이 상위집합(`DEVICE_REJECTED_EVENT` + 상대경로 이유 주석).
- **`_authFetch` 주석 블록** → 우리 것(저쪽은 빈 블록).
- **`adminFetchApplicationPhotoUrl` 본문** → 우리 것(`_gatedImageUrl(path, message)` 직접 호출).
- **네 번째 블록** → **양쪽 다 유지**. 우리 admin-enrollment API 5종(`adminListEnrollments`/`adminEnrollmentCard`/`adminApproveEnrollment`/`adminRejectEnrollment`/`adminFetchGatedImageUrl`)을 그대로 두고, main 의 `adminApplicationProfileImage(imageUri)` 를 `_gatedImageUrl(imageUri, '사진을 불러오지 못했어요.')` 로 **위임**하도록 고쳐 넣었다.

  이유: main 의 원본은 `if (!res.ok) throw new Error(...)` 라 403(기기 게이트 거절)과 404(파기됨)를 구분하지 않고 `DEVICE_REJECTED_EVENT` 도 안 쏜다. 프로덕션 기기 게이트가 켜진 상태에서 관리자 심사 이미지는 403 이 나는데, 그걸 화면이 "파기됨"으로 읽는다(최종리뷰 C4). 위임으로 바꿔 main 의 다른 호출자(`AdminSubmissionDetails.jsx` 의 `SubmissionPhoto`)도 같은 구분을 얻는다.

  호출부 확인: `AdminApplications.jsx:58`(`adminFetchApplicationPhotoUrl`), `AdminSubmissionDetails.jsx:59`(둘 다), `AdminEnrollmentReview.jsx:139·178`. 인자 형태 변화 없음 — `adminApplicationProfileImage` 는 여전히 `(imageUri)` 단항, 두 번째 인자를 무시하던 기존 호출도 그대로 동작한다. `admin-applications-parity.test.mjs` 의 스텁(`adminApplicationProfileImage: path => ...`)도 동일.

### `server/app/facemarket_enrollment.py` (13 블록)

대부분 "양쪽이 서로 다른 것을 더했다" → 합집합. 특이 사항만 적는다.

1. **`BIOMETRIC_CONSENT_VERSION`** → **main 값 `2026-09-v1` 채택** (+ main 의 `OVERSEAS_NOTICE_VERSION`).
   우리 쪽은 이 상수를 `2026-08-v2` 로 **되돌려** 두었고, 그 이유는 "2026-09-v1 동의 문구가 아직 화면에 없다" 였다. 그런데 main 이 그 사이에 그 문구를 실제로 내보냈다 — `CONSENT_DOCUMENTS`(`public/legal/biometric-consent`, `overseas-transfer`, 둘 다 version `2026-09-v1`)와 위저드의 `termsConsent` 요구가 함께 나갔다. 되돌림의 전제가 사라졌으므로 main 을 따랐다. 상수 앞의 경고 주석(카탈로그가 비는 사고)은 살려 두었다.
2. **`CreateEnrollmentBody` / `EnrollmentView` / 두 SELECT / 뷰 생성** → 합집합(우리 `identity_method`·`review_status`·`id_document_r2_key`, main 의 `terms_consent_version`·`overseas_consent_version`·`photo_revision`·`license_*`·`photo_count`).
3. **`create_enrollment` 검증** → 양쪽 다. main 의 추가 동의 강제 + `overseas_version` 계산을 앞에, 우리 인증수단 화이트리스트(`identity_method_unavailable` 409)와 `initial_status` 결정을 뒤에.
4. **INSERT** → 우리 2분기(mid / simple_auth) 구조를 유지하고, **main 이 늘린 동의 버전 두 컬럼을 두 분기 모두에** 넣었다(바인드 파라미터 8개로 통일). `on conflict ... where status in (...)` 9-state 목록은 우리 것 유지 — `test_facemarket_id_capture_migration.py::test_active_status_lists_match_migration_index` 가 마이그레이션 인덱스와 바이트 단위로 대조한다.
5. **완료-체크 SELECT** → 합집합(`photo_revision` + `identity_method`, `id_document_r2_key`). `test_completion_select_projects_every_column_read` 가 "읽는 컬럼은 전부 투영한다"를 강제하므로 셋 다 필요.
6. **신분증 초상 파싱** → **main 의 `if settings.fm_face_match_enabled:` 게이트 안에 우리 경로 분기를 넣었다.** `method` 판정과 `match_snapshot` 초기값은 게이트 **밖**으로 뺐다 — 얼굴 매칭이 꺼져 있어도 `simple_auth` 는 여전히 사람 심사로 가야 하기 때문이다. 매칭이 꺼진 회차의 스냅샷은 `{"policyVersion", "anchor", "faceMatch": "disabled"}` 로 남겨 심사자가 "점수 없음"과 "점수 0"을 구분할 수 있게 했다.
7. **매칭 스냅샷의 `thresholds`** → 우리 원본은 `for angle in ANGLES` 였는데 **main 이 `ANGLES` 를 `LEGACY_ANGLES` 로 개명하고 슬롯을 18장으로 늘렸다**(컨트롤러가 경고한 "함정 2"). 상수 이름을 갈아 끼우는 대신 **실제로 매칭을 시도한 각도**(`for angle, _ in photo_items`)로 바꿨다 — 3각도 레거시 등록에서는 결과가 동일하고(기존 단언 `{"front":0.15,"angle45":0.15,"side":0.10}` 그대로 통과), 18슬롯 등록에서는 스냅샷이 거짓말을 하지 않는다.
8. **완료 tail** → 우리 `bind_model_and_enqueue_asset_build` 공용 추출을 유지하고, **main 이 그 사이에 인라인 tail 에 더한 `photo_revision` 처리를 helper 안으로 포팅했다**:
   - `revalidating_photos = row.get("photo_revision", 0) > 0` + 다른 모델로의 재바인딩 차단(`identity_recovery_required`)
   - 재검증 회차에는 `fm_identity_verifications` INSERT 생략(같은 `cx_tx_id` 재삽입 = 유니크 위반)
   - 자산빌드 잡 payload 에 `"photoRevision"` 추가
   이 포팅을 빼먹었으면 main 의 사진 재검증 기능이 우리 브랜치에서만 조용히 죽었을 것이다(트랩 2의 정확한 사례).

### 나머지

| 파일 | 결정 |
|---|---|
| `documents/facemarket_apply_faq.md` · `src/features/facemarket-landing/applyStartFaq.js` | 한 문단을 **합성**했다(두 파일이 바이트 단위로 같아야 `facemarket-apply-start.test.mjs` 가 통과). main 의 사실(사진 **18장**, 증서가 **철회 전까지 영구**)을 취하고, 우리 간편인증 경로 설명(신분증 직접 촬영·주민번호 뒷자리 마스킹·심사 종료 시 즉시 파기·7일 상한·심사 대기 시간)을 덧붙였다. 우리 쪽에 있던 "정면/45도/측면 + 추가 20장"과 "유효기간을 정하고"는 main 에 의해 사실이 아니게 되었으므로 버렸다. |
| `documents/legal/00_facemarket_legal_notice_map_v1.md` | 합성. main 의 **B-2 = 동의가 아니라 고지**(체크 없음) 프레이밍을 취하고, 우리 **B-3a/B-3b 경로별 신분증 보관** 분리를 얹었다. |
| `tools/legal_publish.py` | main 구조 + 우리 것 재적용. `manifest.append(...)` 는 우리 per-slug `version`/`iso_date`(`DOC_REVISIONS`)를 쓰고, 그 뒤에 main 의 `manifest += CONSENT_DOCUMENTS` 를 붙였다. (`DOC_REVISIONS`·`DEFAULT_VERSION`·`_korean_date`·`CONSENT_DOCUMENTS` 정의부는 충돌 없이 자동 병합돼 있었다.) |
| `tests/frontend/legal-publish.test.mjs` | 버전 검사는 `revised` 맵 하나로 합쳤다(`privacy-model`=v1.2/2026-09-12, 동의문서 2종=2026-09-v1/2026-09-11, 나머지 v1.1/2026-09-11). 드리프트 검사는 **main 쪽을 채택**했다 — 우리 것과 같은 검사에 `public/llms.txt` 비교와 손으로 관리하는 동의문서 2종 예외 처리까지 있어 상위집합이다. 우리 "게시본에 간편인증 고지가 실제로 들어 있는가" 테스트(정본과 게시본이 **함께** 비어도 드리프트 검사는 조용하다는 사각지대를 메우는 것)는 그대로 살렸고, main 의 기간형 권리 제거 테스트도 살렸다. 재발행 결과는 커밋된 `public/legal` 과 바이트 단위로 일치(diff 없음) 확인. |
| `server/tests/test_facemarket_biometric_enrollment.py` | 완료-체크 투영은 **우리 것**(`completion_check_columns()` 로 프로덕션 SQL 에서 컬럼을 뽑음 — 손으로 적으면 "코드는 읽는데 SELECT 엔 없는" 컬럼을 가짜 커서가 채워 줘 프로덕션에서만 죽는다, C1). `EnrollmentView` 필드 집합은 합집합. |
| `tests/frontend/facemarket-biometric-enrollment.test.mjs` | 우리 것(저쪽은 빈 블록) — 우리 admin-enrollment API 테스트 5개 유지. |

---

## 2. `ModelRegister.jsx` 포팅 (라인 머지 아님)

main 이 1355줄 → 375줄로 재구성했다(화면은 `RegisterScreens.jsx`, 상태 복원은 `registerSlots.js`, OACX 위젯은 `src/lib/api/facemarketIdentityWidget.js` 로 분리). **main 의 375줄 파일을 베이스로 새로 쓰고** 우리 추가분을 재적용했다.

| 우리 기능 | 새 구조에서의 자리 |
|---|---|
| `method` 스텝 | 렌더 체인에 `} else if (step === 'method')` 추가 → `IdentityMethodStep`. 수단이 하나면 그 화면을 아예 안 거친다(`chooseMethod = IDENTITY_METHODS.length > 1`) — `FM_IDENTITY_METHODS=mid` 롤백 경로에 클릭이 늘지 않는다. |
| `id_capture` 스텝 | `} else if (step === 'id_capture')` → `IdDocumentStep`(`onUploaded`/`onStale` 둘 다 `finishIdDocument`, 409 복구). |
| `review` 스텝 | `} else if (step === 'review')` → 대기 화면 + `refreshReview`(수동 새로고침) + `cancelReview`(취소 탈출구) + 메일 통지·5일 기한 문구. |
| 위젯 분기 | **`facemarketIdentityWidget.js` 로 이전**: `runIdentityWidget({ identityMethod, signal })` 가 `CX_AUTH_CONFIG_URL` + `{signType:'ENT_SIMPLE_AUTH', compareCI:false, isBirth:true}` 와 `CX_CONFIG_URL` + `ENT_MID`/`useConvertor` 를 가른다. 설정 URL 이 없는데 간편인증이면 **위젯을 열기 전에** 던지는 가드도 같이 옮겼다. 등록 화면은 `record?.identityMethod \|\| 'mid'` 를 넘긴다. |
| `startEnrollment(identityMethod)` | 그대로. `...(identityMethod && identityMethod !== 'mid' ? { identityMethod } : {})` 로 mid 요청 바디는 오늘과 동일. 간편인증 설정 부재는 `createEnrollment` **앞에서** 막는다. |
| 동의 버튼 라우팅 | `action: identityPending ? () => runIdentity() : chooseMethod ? () => setStep('method') : () => startEnrollment(IDENTITY_METHODS[0])` |
| 경로별 문구 | main 의 문구는 "신분증 인증"으로 고정이었다. `isSimpleAuthEnrollment` 로 `verb` 를 갈라 버튼 라벨이 실제로 열리는 창(PASS·카카오·네이버)과 어긋나지 않게 했다. |
| 상태 → 화면 복원 | `registerSlots.restoreRegisterScreen` 에 `id_capture_pending → id_capture`, `review_pending → review` 를 추가하고, main 이 `review_pending` 을 `done` 으로 보내던 줄을 `passed` 만 남겼다. **main 쪽 그대로 두면 심사 대기 중인 사용자가 증서도 없이 "축하해요, 등록이 끝났어요" 화면을 본다.** |
| `handleMethodPick` | `useCallback(..., [])` 로 참조를 고정하되 **최신 `startEnrollment` 는 ref 로 부른다**. main 의 `startEnrollment` 는 `consents` 를 읽으므로 클로저를 그냥 굳히면 첫 렌더의 `[false,false]` 를 영원히 보고 아무 일도 안 일어난다(우리 원본에는 없던 위험 — main 이 동의 검사를 그 함수 안으로 옮겼기 때문). |

**CSS**: main 의 위저드 재설계가 `stepHead`·`stateTitle`·`medallion`·`uploadZone`·`backLink` 등 15개 클래스를 `ModelRegister.module.css` 에서 걷어냈는데 `IdDocumentStep.jsx` 는 그걸 계속 쓴다(CSS Modules 는 없는 클래스에 `undefined` 를 줘서 조용히 스타일만 사라진다). 머지 전 파일에서 해당 규칙 33개를 뽑아 파일 끝에 표식과 함께 되살렸다.

---

## 3. 보존하지 못한 것

1. **클라이언트의 신분증 초상 릴레이(`portraitRef` · `idPhotoHex` · `reidentify` 화면 · `useConvertor: true`)**
   main 이 `FM_FACE_MATCH_ENABLED`(기본 **false**)를 도입하면서 `/complete` 는 `sessionId` 만 보내고, 위젯의 `useConvertor` 도 `false` 로 껐다. 우리 브랜치가 지키려던 "간편인증은 초상 없이도 진행돼야 한다"는 요구는 이제 **양쪽 경로 모두 초상을 안 쓰므로 구조적으로 충족**된다 — 그래서 `enrollment?.identityMethod !== 'simple_auth' && !portraitRef.current` 가드와 `finishMatch` 의 `idPhotoHex` 생략은 되살릴 대상이 없어졌다. 대신 "클라가 `dlphotoimage`/`portraitRef`/`idPhotoHex` 를 더 이상 다루지 않는다"를 테스트로 잠갔다.
   **후속 주의**: 나중에 `FM_FACE_MATCH_ENABLED=true` 로 켜면 **mid 경로에는 초상 소스가 없다**(클라가 안 보내고 `useConvertor` 도 꺼져 있다). 이건 main 이 만든 상태이고 이 머지가 새로 만든 문제는 아니지만, 켜기 전에 위 3가지를 함께 되살려야 한다.

2. **`BIOMETRIC_CONSENT_VERSION` 되돌림(`2026-08-v2`)**
   위 1-①의 이유로 포기했다. 대가는 실재한다 — `2026-08-v2` 로 기록된 기존 등록은 `_CURRENT_CARD_ELIGIBILITY` 에서 빠지고 백필 마이그레이션은 없다. main 이 이미 내린 결정이라 머지에서 뒤집지 않았다.

3. **마이그레이션 파일명 `20260911140000_facemarket_id_capture_review.sql`**
   main 의 `20260911140000_fm_payout_accounts.sql` 과 버전이 정면 충돌해서(`test_supabase_migration_versions_are_unique` 가 잡았다) **우리 것을** `20260912000000_facemarket_id_capture_review.sql` 로 옮겼다(main 은 이미 배포됨). 참조 2곳(`server/tests/test_facemarket_id_capture_migration.py`, 계획 문서)도 같이 고쳤다. 아직 적용되지 않은 마이그레이션이라 이름 변경이 안전하다.

---

## 4. 고친 테스트 (그리고 이유)

| 테스트 | 변경 | 종류 |
|---|---|---|
| `server/tests/test_facemarket_admin_review.py` (모듈 상수) | `facemarket_enrollment.ANGLES` → `.LEGACY_ANGLES` | main 이 상수를 개명했다 |
| `server/tests/test_facemarket_admin_review.py::_setup` | `fm_face_match_enabled=True`, `fm_photo_slots=("face01","face03","face05")`, `fm_required_slot_count=3` 추가 | main 이 얼굴 매칭을 기본 off 로, 필수 사진을 18장으로 바꿨다. 이 파일의 검증 대상(advisory 점수)이 매칭 결과라 명시적으로 켜야 하고, `/complete` 하나만 보는 파일이라 슬롯 요구를 자산 소스 3개로 좁혔다(레거시 3각도 행이 `resolve_photo_rows` 를 통해 그 3슬롯을 채운다) |
| `server/tests/test_facemarket_biometric_enrollment.py` (FakeCursor 투영) | `COMPLETION_COLUMN_DEFAULTS = {"photo_revision": 0}` 추가 | 우리 "프로덕션 SQL 에서 컬럼을 뽑는" 투영이 main 의 손수 적은 기본값(`row.get("photo_revision", 0)`)을 잃어 `None > 0` TypeError 가 났다. 컬럼 **목록**은 계속 프로덕션에서 뽑고(C1 보호 유지) **기본값만** 스키마(`integer not null default 0`)를 따르게 했다. 이 하나가 전체 스위트를 멈춰 세우던 교착(`test_cancel_wins_before_completion_finalization_without_resurrection` 무한 대기)의 원인이었다 |
| `server/tests/conftest.py` (simple_auth INSERT 가짜) | 파라미터 언패킹 6개 → 8개, 행에 `terms_consent_version`·`overseas_consent_version`·`photo_revision` 추가 | main 이 INSERT 에 동의 버전 두 컬럼을 더했다 |
| `server/tests/test_facemarket_identity_method.py::test_accepts_new_and_previous_consent_versions` | `BIOMETRIC_CONSENT_VERSION == "2026-08-v2"` → `"2026-09-v1"` | main 이 상수를 올렸다(위 3-②) |
| `server/tests/test_facemarket_identity.py::test_catalog_eligibility_survives_for_the_literal_shipped_consent_version` | 심는 리터럴 `2026-08-v2` → `2026-09-v1` | 같은 이유. 이 테스트의 요점(상수가 아니라 **리터럴**을 심어 다음 범프를 먼저 터뜨린다)은 그대로다 |
| `tests/frontend/facemarket-biometric-enrollment.test.mjs::identity step runs OACX widget...` | `completeEnrollment` 가 `idPhotoHex` 를 싣는다 → **안 싣는다** | main 이 초상 릴레이를 제거했다(위 3-①) |
| `tests/frontend/facemarket-id-capture.test.mjs` — `runCxWidget` 관련 2개 | `ModelRegister.jsx` 대신 `facemarketIdentityWidget.js` 를 대조. `useConvertor: true`·`dlphotoimage` 단언 삭제 | 위젯 호출이 그 모듈로 이사했고, `useConvertor` 는 main 이 껐다 |
| 〃 — 라이브니스 `portraitRef` 가드 / `finishMatch` `idPhotoHex` 2개 | 삭제하고 "클라가 초상을 다루지 않는다" 단언으로 대체 | 위 3-① |
| 〃 — 동의 버튼 라우팅 / `handleMethodPick` / `finishIdDocument` / `review` 스텝 / 문구 분기 5개 | 새 구조의 실제 텍스트로 재조준(렌더 체인 `} else if (step === '…')`, `screen.step`, `chooseMethod`, `startEnrollmentRef`) | 포팅에 맞춘 재조준. 잡으려는 회귀는 전부 동일하고, `restoreRegisterScreen` 의 두 상태 매핑 단언을 새로 추가해 보호를 오히려 넓혔다 |

---

## 5. 검증 결과 (5/5 green)

**1) 서버 테스트**
```
$ cd .../server && .venv/bin/python -m pytest tests/ -q --ignore=tests/test_personalization.py | tail -3
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
4998 passed, 77 skipped, 391 warnings in 86.71s (0:01:26)
```

**2) 프런트엔드 테스트**
```
$ pnpm test:frontend | tail -8
ℹ tests 1667
ℹ suites 0
ℹ pass 1667
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 22317.556958
```

**3) 빌드**
```
$ pnpm build | tail -5
(!) Some chunks are larger than 500 kB after minification. Consider:
- Using dynamic import() to code-split the application
- Use build.rollupOptions.output.manualChunks to improve chunking: ...
- Adjust chunk size limit for this warning via build.chunkSizeWarningLimit.
✓ built in 5.51s
```

**4) 충돌 마커**
```
$ grep -rn '^<<<<<<<\|^>>>>>>>\|^=======$' --include='*.py' --include='*.js' --include='*.jsx' --include='*.md' src/ server/ tools/ documents/ tests/
grep exit=1 (매치 없음)
```

**5) 플래그 기본 off** (`env -i` — 환경변수 없이 `load_settings()`)
```
fm_identity_methods = ('mid',)
fm_enrollment_review = 'simple_auth_only'
fm_oacx_simple_auth_contract = 'disabled'
```
`fm_identity_methods` 가 `('mid',)` 이므로 `simple_auth` 는 서버가 409(`identity_method_unavailable`)로 막고, `fm_oacx_simple_auth_contract='disabled'` 로 계약도 꺼져 있다. `fm_enrollment_review='simple_auth_only'` 는 그 경로에만 작용하므로 mid-only 배포에서는 아무도 심사 대기로 가지 않는다. 프런트도 `VITE_FM_IDENTITY_METHODS` 없으면 `['mid']` 라 선택 화면 자체가 없다.
