# FaceMarket 생체등록 플로우 재배치 (신분증-먼저) — 설계

**작성일:** 2026-08-24
**브랜치:** `codex/facemarket-security-hardening` (worktree `.worktrees/facemarket-security-hardening`)
**상태:** 설계 승인 대기 → 승인 후 writing-plans 로 구현 계획 작성

## Goal

FaceMarket 실존인물 생체등록 플로우에서 **본인확인(신분증 CI)을 맨 앞으로 이동**해, 사용자가 사진·라이브니스 등 노력을 들이기 전에 신원 부적격을 fail-fast 로 걸러낸다. 더불어 얼굴 3장 단계에 **포즈 예시 일러스트**를 추가하고, 사진 뒤에 **마켓 대표이미지 업로드** 단계를 넣는다.

## Architecture (요약)

현재 등록은 `동의 → 사진3장 → 라이브니스 → (completeEnrollment: 신분증CI검증+얼굴매칭 동시) → 자산빌드 → 라이선스 → VC → 완료` 순이다. 신원검증(OACX)이 맨 끝에 있어, 신원이 부적격이어도 사용자는 사진·라이브니스를 모두 마친 뒤에야 거절당한다.

이 설계는 **신원검증을 두 조각으로 분리**한다: **본인확인(CI)** 은 맨 앞의 fail-fast 게이트로 옮기고(OACX 위젯 → 서버 `trans/{token}` CI 검증 → 저장), **얼굴매칭(SFace 1:1)** 은 라이브니스가 필요하므로 기존대로 끝에 남긴다. 신분증 초상(dlphotoimage)은 앞 단계에서 위젯이 획득해 **브라우저 메모리**에 보관하고, 매칭 단계에서 제출한다(저장·로그 0, 현행과 동일 메커니즘).

## Tech Stack

- 백엔드: FastAPI (`server/app/facemarket_enrollment.py`, `server/app/cx_identity.py`), Postgres(Supabase, PG16), psycopg async.
- 프론트: React (`src/features/model/ModelRegister.jsx`, `ModelFaceUpload.jsx`, `FaceLivenessStep.jsx`, `biometricEnrollment.js`), Vite.
- 벤더: OmniOne CX / OACX 모바일신분증 위젯(`OACX.LOAD_MODULE`), AWS Rekognition Face Liveness, SFace 1:1(`agents/face_qc.py`).

## Global Constraints (프로젝트 불변 규칙 — 모든 태스크에 적용)

- **dev 전용 우회 금지.** 모든 게이트는 전 환경 동일하게 fail-closed. QA=배포될 코드.
- **생체 바이트·초상·임베딩·랜드마크·파일명 저장·로그 금지**(§1.4). 관측 허용 = 상태 enum·사유코드·provider 만.
- **모델은 얼굴매칭 성공 후에만 바인딩**한다(중간 실패시 fm_models 행 안 생김) — 현행 원칙 유지.
- **CI(주민식별)는 서버 검증만.** 클라가 CI/이름/생년을 직접 주지 않는다. 서버가 `trans/{token}` 서버발 호출로만 확보.
- 생체등록은 현재 prod OFF(`FM_BIOMETRIC_ENROLLMENT_ENABLED=false`) — 이 재배치는 dark-launch 상태에서 이뤄지며 활성화는 별개 배포 이슈.

## 재배치 후 전체 흐름

| # | 단계 | enrollment status | 내용 | 변경 |
|---|---|---|---|---|
| 1 | 동의 | (시작 전) | 생체정보 처리 동의 | 그대로 |
| 2 | **본인확인(OACX)** | `identity_pending`(신규) | 위젯 인증 → 서버 CI 검증 = fail-fast 게이트 → CI 저장 | **신규 위치** |
| 3 | 얼굴 3장 | `photos_pending` | 정면/45/측면 + 포즈 예시 일러스트 | 위치이동+예시 |
| 4 | 대표이미지 | (비게이팅) | 마켓 노출용 사진, 표시 전용 | **신규** |
| 5 | 라이브니스 | `liveness_pending` | AWS Face Liveness | 그대로 |
| 6 | 얼굴매칭 | (completeEnrollment) | SFace 1:1: 저장초상 ↔ 라이브얼굴, 통과시 모델 바인딩 | 매칭만(CI분리) |
| 7 | 처리·자산빌드 | `processing`→`asset_building` | 3장으로 AI 모델 private 자산 생성 | 그대로 |
| 8 | 라이선스 조건 | `license_pending` | 얼굴 사용 조건 설정 | 그대로 |
| 9 | VC 발급 | `vc_pending` | OpenDID FaceLicense VC | 그대로 |
| 10 | 완료 | `passed` | 모델 활성 | 그대로 |

**스코프 = 1~6. 7~10 은 손대지 않는다.**

## 상세 설계

### A. 상태머신 + 마이그레이션

- `fm_biometric_enrollments.status` 에 **`identity_pending`** 추가하고 등록 시작 기본값을 `photos_pending` → `identity_pending` 로 변경.
- **DB CHECK 제약 갱신 필요**: `supabase/migrations/20260821010100_facemarket_biometric_runtime.sql:19-21` 의 `check (status in (...))` 에 `identity_pending` 추가. 같은 파일 line 84 부근 partial index 의 `where status in (...)` 목록도 함께 갱신. → **신규 additive 마이그레이션 파일**(기존 파일 수정 아님)로 제약을 drop/재생성.
- `server/app/facemarket_enrollment.py` 내 status 목록을 하드코딩한 SQL 리터럴 다수(예: line 597, 709, 724 의 IN/CASE 절)에 `identity_pending` 반영. (후속: 이 중복 상수를 한 곳으로 모으는 정리도 계획에 포함 검토.)
- 프론트 `nextEnrollmentStep`(`biometricEnrollment.js`): `identity_pending → 'identity'` 매핑 추가. `ENROLLMENT_STEPS` = `['consent','identity','photos','profile','liveness','processing','terms','done']`.

### B. 본인확인(CI) 게이트 — 신규 엔드포인트

- `create_enrollment`(`facemarket_enrollment.py:637`): 동의 기록 후 status = `identity_pending` 로 시작.
- **신규** `POST /v1/facemarket/enrollments/{id}/identity`, body `{ token }`:
  - 서버: `cx_identity.fetch_trans(settings.cx_trans_base_url, token)` → `parse_oacx_biometric_evidence(trans, contract)` 로 CI/이름/생년/txId 파싱(현재 `process_enrollment_completion:1693-1695` 에 있는 로직을 이 엔드포인트로 이동).
  - **중복/유효성 검사(fail-fast)**: 동일 CI 로 이미 활성/검증된 모델이 있으면 여기서 거절. (현재 completeEnrollment 후반의 CI 바인딩 단계에 있는 dedup 을 여기로 당김 — 단, 바인딩 자체는 매칭 성공 후로 유지, C 참조.)
  - 통과 시: 검증된 CI 증거(ci_hash, name_masked, birth, txId 등)를 **enrollment 행에 pending 으로 저장** → status `identity_pending → photos_pending`.
  - 초상은 이 요청에 **포함하지 않는다**(브라우저 메모리 보관, D 참조).
- **CI 증거 저장 위치**: `fm_biometric_enrollments` 에 컬럼 추가(ci_hash, name_masked, birth_year, tx_id 등) 또는 pending `fm_identity_verifications` 행. → **마이그레이션 필요**(additive 컬럼). 매칭 성공 후 이 값을 fm_models 바인딩에 사용.

### C. completeEnrollment 단순화 — 얼굴매칭만

- `CompleteEnrollmentBody`(`facemarket_enrollment.py:172-178`): `token` 제거(더 이상 필요 없음 — CI 는 B 에서 검증됨, 토큰은 어차피 ~5분 만료). `sessionId` + `id_photo_hex`(초상) 유지.
- `process_enrollment_completion`(1657): `fetch_trans`/`parse_oacx_biometric_evidence`(1693-1695) 호출 제거 → **B 에서 저장한 CI 증거를 읽어 사용**. `_assert_match(one_to_one_similarity(portrait, liveness.reference_image), ...)`(1716) 및 각 사진 버퍼 매칭(1721)은 **그대로**.
- 매칭 통과 후 fm_models 바인딩 + `fm_identity_verifications` 확정은 **저장된 CI 증거로** 수행(모델 생성은 여전히 매칭 성공 후에만).

### D. 초상 보관 + 재개(resumability)

- 초상(dlphotoimage HEX)은 **본인확인(2) 단계 위젯 콜백에서 획득 → React ref(메모리) 보관 → 매칭(6) 에서 제출**. 저장·로그 0(현행 `ModelRegister.jsx:310-313` 주석 규칙 유지).
- **트레이드오프**: 초상이 3~5단계 동안 브라우저 메모리에 상주(현행은 맨 끝이라 잠깐). 저장은 안 하므로 데이터 보존 리스크는 "보관 시간 증가"뿐(census 크리티컬 #2 수준은 불변, 악화 아님).
- **새로고침/리마운트 시 초상 유실** → 매칭 불가. 처리: `identity_pending` 이후 상태인데 메모리 초상이 없으면, 사용자를 **본인확인(2) 단계로 되돌려 OACX 재인증**(토큰 단회성이라 재발급). 조용한 실패 금지 — 명시 안내.

### E. 얼굴 3장 예시 일러스트

- `ENROLLMENT_ANGLES`(`biometricEnrollment.js`) 각 항목에 `example`(이미지 경로) 필드 추가. `ModelFaceUpload.jsx` 슬롯에 예시 이미지 렌더.
- **에셋 제작 필요**: 정면/45도/측면 **포즈 일러스트/실루엣**(실제 사람 얼굴 미사용 — 안전). 3개 SVG/PNG. (구현 태스크에서 생성.)

### F. 대표이미지 (비게이팅 모델 속성)

- 생체 상태머신에 넣지 않는다. **신규** `POST /v1/facemarket/enrollments/{id}/profile-image`(또는 모델 스코프) 로 표시전용 이미지 저장(모델 레코드 속성). 가벼운 검증(이미지 타입/크기)만, 생체 QC·SFace 없음.
- 프론트 `profile` 스텝: 사진3장(3) 뒤 라이브니스(5) 앞. **건너뛰기 허용**. 나중에 "모델 관리"에서도 설정 가능(후속 가능, 이 스펙은 등록 중 업로드까지).
- 저장 위치: R2 공개/표시용 버킷 또는 기존 모델 자산 경로 규칙 따름(구현시 확정).

## Error handling

- **본인확인 실패**(CI 무효·중복·trans 오류): `identity` 단계에서 4xx 로 명시 거절, 사진 진행 차단(fail-fast). 재시도 가능.
- **토큰 만료/유실**: B 는 즉시 검증하므로 앞단에선 문제없음. 매칭(6) 은 토큰 미사용이라 만료 무관.
- **새로고침 후 초상 유실**: D 처리(본인확인 재인증 유도).
- **부분 실패 원자성**: CI 저장(B)과 모델 바인딩(C)은 분리 — B 성공 후 사용자가 이탈해도 fm_models 는 안 생김(바인딩은 매칭 후). 미완 enrollment 는 기존 취소/만료 경로로 정리.

## Testing 전략

- 백엔드 단위/통합(pytest, `server/tests/test_facemarket_biometric_enrollment.py` 확장):
  - `identity` 엔드포인트: 유효 CI → `photos_pending` 전이; 무효/중복 CI → 거절, 사진 차단; trans 실패 → 명시 에러.
  - `create_enrollment` → 시작 status `identity_pending`.
  - `complete_enrollment`: 토큰 없이 sessionId+portraitHex 로 매칭만 수행; CI 는 저장값 사용; 매칭 실패 → 모델 미생성.
  - 마이그레이션: 신규 status CHECK 제약 additive·PG16 안전(기존 status-apply 테스트 패턴).
- 프론트(node --test, `tests/frontend/`): `nextEnrollmentStep(identity_pending)==='identity'`; `ENROLLMENT_STEPS` 순서; 예시 이미지 슬롯 렌더; 대표이미지 스텝 건너뛰기.
- 회귀: 기존 생체등록 스위트 그린 유지(7~10 미변경 확인).

## Out of scope (별도 추적)

1. **census 크리티컬 #2 — 서버측 초상 재fetch**: 벤더 매뉴얼 확인 결과 OACX 는 초상을 위젯(클라)에서만 내주고 result 단계는 단회성이라 서버 재fetch 불가. faceAccessToken 으로 초상 조회하는 API 는 매뉴얼에 없음. → **벤더 질문거리**로 분리(서버 result 호출/submit-only 위젯 모드 지원 여부).
2. **속옷/노출 카테고리 제외**: 실존인물 얼굴 + 속옷 = 딥페이크·NCII 위험. 의류 전문 브랜드 정책. **다른 subsystem(생성/카테고리 정책)** — 이 재배치 다음 별도 조사·설계.
3. **prod VC 발급(9단계)**: prod OpenDID 홀더(Server3) 미배포 → 현재 VC no-op. 별도 배포 이슈(Phase-2 계획서).
4. **배포 crash-loop(deploy-gate #1)**: manifest 미싱크. 별도.

## Open questions / risks

- CI 증거를 enrollment 컬럼 추가 vs pending identity_verifications 행 — 구현 계획에서 확정(기존 스키마 패턴 따름).
- 대표이미지 저장 버킷/경로 — 기존 모델 자산 규칙 확인 후 확정.
- status 상수 중복 정리(SQL 리터럴 다수) — 이번에 함께 정리할지, 최소변경만 할지 계획에서 결정.
