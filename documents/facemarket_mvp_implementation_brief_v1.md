# FaceMarket MVP 구현 지시서 v1 (2026-09-04)

> 구현 담당(Codex)이 읽는 문서. 근거는 `mockups/facemarket_screens_wireframe_20260904.html`(화면 명세), `documents/legal/00~06`(고지·동의·문안), `documents/facemarket_mvp_flow_20260903.html`(플로우 보고). 오너 결정은 전부 이 문서에 반영돼 있으므로 다른 문서와 어긋나면 **이 문서가 우선**한다.

## 0. 공통 규칙 (반드시)

- 저장소 규칙 `CLAUDE.md`를 따른다. 특히: React 훅은 로딩 early-return 위에(훅 개수 불변), 콘티보드 저장 계약과 서버는 건드리지 않음, 수집 이미지·`mockups/` 커밋 금지, 이미지 산출물 생성·교체·업로드 금지.
- 검증: 프론트 변경은 `npx vite build` 통과 + `node --test tests/frontend/facemarket-*.test.mjs` 통과. 서버 변경은 `cd server && .venv/bin/pytest -q` **전부 통과**(기존 테스트가 새 동작과 어긋나면 테스트를 새 동작에 맞게 고치되 사유를 커밋 메시지에 적는다).
- 커밋: 작업 단위마다 커밋한다. Conventional 접두사(feat/fix/refactor/test) + 요약·본문은 비개발자용 쉬운 말. **push·PR 생성은 하지 않는다.** 작업 브랜치는 현재 HEAD(`codex/facemarket`)에서 `codex/facemarket-mvp-<phase>`로 만든다.
- 숫자·문구의 단일 출처: 프론트 `src/features/facemarket-landing/facemarketTerms.js`(신설)와 서버 `server/app/facemarket_terms.py`(신설)에 아래 조건표 값을 상수로 두고, 화면·메일·API는 그 상수만 쓴다.
- 문구는 `documents/legal/04_facemarket_biometric_consent_forms_v1.md`의 체크박스 문안을 그대로 쓴다. `[대괄호]` 값은 조건표 상수로 치환한다.
- 도달 불가한 화면·죽은 라우트를 만들지 않는다. 각 단계 끝에 "어느 화면의 어느 버튼으로 도달하는지"를 커밋 본문에 적는다.

## 1. 조건표 (상수)

| 키 | 값 |
|---|---|
| MODEL_SHARE / PLATFORM_SHARE / OPS_SHARE | 0.70 / 0.20 / 0.10 |
| SETTLEMENT_DAY | 10 (매월, 전월분) |
| MIN_PAYOUT_KRW | 10000 |
| DISPUTE_WINDOW_DAYS | 30 |
| VALIDITY_OPTIONS | 365일 · 730일 · 영구(null). 기본 영구 |
| EXPIRY_NOTICE_DAYS | 30 |
| RENEWAL_SLOT_MONTHS | 6 |
| PURGE_DAYS / BACKUP_PURGE_DAYS | 30 / 90 |
| ID_FACE_RETENTION | 2차 검수 완료 즉시 파기, 최대 확정 후 30일 |
| APPLICATION_REJECT_PURGE_DAYS | 30 |
| REVIEW_SLA_DAYS | 3 (표시는 "보통 1시간 안") |
| UNIT_PRICE_DEFAULT_KRW / MIN | 10000 / 5000 |
| MONTHLY_MULTIPLIER | 2.5 (월정액 = 건당 × 2.5, 100원 단위 반올림) |
| MONTHLY_PERIOD_DAYS | 30 |
| EARLYBIRD_SEATS | 10 |
| LICENSE_ISSUE_FEE_KRW | 20000 (얼리버드 면제) — 실체는 오너 확정 전까지 화면 문구만 유지 |
| CIRCUMVENTION_MONTHS | 12 |
| APPROVAL_MODE | 'auto' 고정 (건별 승인은 스키마만 예약, UI 없음) |

## 2. Phase A — 프론트 정리 (랜딩·상단바·허브·정산) `codex/facemarket-mvp-a`

### A-1 상단바
- 비로그인에도 `모델 리스트 · Digital DNA 관리 · 정산` 세 항목을 모두 보인다. 비로그인 상태에서 Digital DNA 관리·정산을 누르면 로그인 모달(`openLogin(to)`)이 열리고 로그인 후 그 경로로 간다. 파일: `src/features/facemarket-landing/LandingHeader.jsx` (`NAV_PUBLIC`/`NAV_MEMBER` 분기 제거).
- 호버 밑줄: `.navLink:hover`에 현재 활성 스타일과 같은 굵은 밑줄(`text-decoration: underline; text-decoration-thickness: 2px; text-underline-offset: 0.45rem`). 활성 항목은 그대로. 파일: `FacemarketLanding.module.css`.
- 헤더 CTA: 로그인 전이거나 지원서·모델이 없는 사용자에게만 `얼리버드 지원하기`를 보인다. 지원서가 있거나 등록 중이거나 확정된 사용자에게는 **헤더 CTA를 그리지 않는다**(Digital DNA 관리가 그 역할). `registerCta.js`의 '지원 상태 보기'·'모델 등록하기'·'내 모델 정보' 분기는 허브 안의 버튼에서만 쓰고 헤더에는 쓰지 않는다. 히어로 CTA는 헤더와 같은 규칙.

### A-2 Digital DNA 관리 (`/status`, `ModelHub.jsx`)
- 제목 "Digital DNA 관리". 온보딩 중이면 타임라인(지원 접수 → 등록 링크 → 본인확인·사진·조건·증서 → 우리 검수 → 테스트 컷 생성 → 확정 → 활동 중), 현재 칸은 파란 점, 완료는 초록. 각 칸의 라벨은 서버 상태값과 1:1(§3-1 표).
- **활동 중** 표시는 모델 상태가 `verified`(확정 승인)일 때. 거래 유무와 무관.
- 활동 중 화면: 상태 배지 · 내 트윈(대표 컷·테스트 컷, 서명 URL) · 내 규칙 카드(허용 n·제외 n·건당 가격·월정액(자동)·유효기간) + [규칙 바꾸기] · 이번 달 요약(건수·금액) + [정산 →] · [계약서·증서 보기] · 하단 [그만두기(철회)] 상시 노출.
- 허브 안의 "등록 이어가기"·"상태 보기" 류 버튼은 타임라인의 현재 칸에 붙는 하나의 행동 버튼으로 통합(예: 현재 칸이 등록이면 "등록 이어가기", 확정 대기면 "테스트 컷 확인하기"). 그 외 중복 버튼 제거.

### A-3 정산 (`/payout`, `PayoutPage.jsx`)
- 상단 숫자 3개: 이번 달 건수·금액 / 누적 / 다음 정산일(SETTLEMENT_DAY). 지급 기능이 없는 동안 "지급 준비 중 · 기록은 쌓이고 있어요" 칩.
- 표: 날짜 · 쇼핑몰 · 품목 · 썸네일 · 내 몫 금액 · 상태(활성/만료). **[증빙] 열 없음.** 월정액 건은 "월정액 · 기간(시작~끝)"으로 표시. 데이터는 `GET /v1/facemarket/settlements`.
- 계좌 등록은 이 Phase에서 만들지 않는다(문구만: "첫 지급 전에 계좌를 안내해 드려요").

### A-4 완료 화면 (`ModelRegister.jsx` done 스텝)
- 제목 "축하해요, 등록이 끝났어요". 아래에 조건 요약 카드(활동명·체형 밴드·허용 품목 수·건당 가격·월정액·유효기간·승인 방식 자동). 그 아래 한 줄: "우리가 사진을 확인한 뒤 테스트 컷을 보내드려요. 보통 1~2일 걸려요." 버튼 [Digital DNA 관리 보기]. "다음에 생기는 일" 목록은 두지 않는다.

### A-5 지원 완료 화면 (`ModelApply.jsx`)
- 접수 완료 문구 + "승인되면 메일로 등록 링크가 가요 · 보통 1시간 안" 칩 + 준비물 2줄(정부 모바일 신분증 앱, 본인 사진) + [모델 리스트 보기].

수용 기준: 스크린샷 4장(랜딩 비로그인 상단바, 허브 온보딩 중, 허브 활동 중, 정산) 을 `mockups/codex_phase_a/`에 저장(커밋 금지 폴더). 빌드·프론트 테스트 통과.

## 3. Phase B — 서버 상태 머신·조건·증서 `codex/facemarket-mvp-b`

### B-1 등록 상태값
현재 `fm_enrollments.status`: identity_pending → photos_pending → liveness_pending → processing → asset_building → license_pending → vc_pending → (verified).
변경 후: `identity_pending → photos_pending → [liveness_pending: FM_LIVENESS_ENABLED=true일 때만] → terms_pending → vc_pending → review_pending → processing → asset_building → confirm_pending → verified`.
- 사진 통과 후 `terms_pending`으로 간다(자동 processing 진입 제거).
- 조건 저장(`POST /licenses` 또는 신설 `POST /enrollments/{id}/terms`) → `vc_pending` → VC 발급 성공 시 `review_pending`. 발급 실패는 상태를 막지 않고 `vc_status='failed'`로 표시 후 `review_pending`.
- 관리자 승인(`POST /admin/enrollments/{id}/approve`) → `processing`(기존 디스패처 그대로) → `asset_building` 완료 → `confirm_pending` + 모델에게 메일.
- 모델 확정(`POST /enrollments/{id}/confirm`) → 모델 `verified`, 라이선스 `active`. 재생성 요청(`POST /enrollments/{id}/regenerate`, 1회) → `asset_building`.
- 관리자 재촬영 요청(`POST /admin/enrollments/{id}/request-photos`, 사유·각도) → `photos_pending`. 관리자 거절 → `rejected`.
- 마이그레이션은 `server/migrations/`의 기존 방식을 따르고, 기존 행은 현재 상태를 보존한다.

### B-2 조건(사용 조건) 모델
- `fm_licenses`에 `validity_days INT NULL`(NULL=영구), `approval_mode TEXT DEFAULT 'auto'`, `monthly_price INT`(= round100(unit_price × 2.5), 저장 시 계산) 추가. `license_valid_until`은 validity_days로부터 계산, 영구면 NULL 허용(게이트·검증 페이지의 만료 판정에서 NULL=무기한).
- `PATCH /licenses/{id}/terms` 신설: allowed_use, forbidden_use, unit_price(≥5000), validity_days. 저장 시 `fm_license_term_changes`(license_id, changed_at, before JSON, after JSON, actor)에 이력. **VC 재발급 없음.** 사용 게이트는 항상 현재 DB 행을 읽는다.
- 유효기간 선택지 90/365/730 → 365/730/영구.

### B-3 VC 내용 축소
- `build_face_vc_claims`가 담는 값: `modelDid`, `licenseId`, `issuedAt`, `faceImageDigest`, `agreementVersion`(계약 문서 버전, 상수 `LICENSE_AGREEMENT_VERSION='v1'`), `consentDocVersion`. **allowedUse·forbiddenUse·unitPrice·licenseValidUntil 제거.**
- 사용 게이트(`_check_license_use` 계열)와 verify 페이지는 VC의 유효성(발급·미폐기)만 보고, 품목·기간은 DB로 판정하도록 정리. 관련 테스트(`test_facemarket_mandatory_vc*.py`, `test_facemarket_licenses.py`, `test_facemarket_vc_config.py`) 갱신.

### B-4 가격 모델(건당 + 월정액)
- 정산 행(`fm_settlements`)에 `billing_type TEXT('per_use'|'monthly')`, `period_start`, `period_end` 추가. 월정액 구매는 셀러가 한 모델에 대해 30일간 무제한 발행. 모델 몫은 두 경우 모두 0.70.
- 서버 API: `POST /licenses/{id}/monthly-passes`(셀러 인증, 결제 확인 후) → 기간 행 생성 + 정산 행 1건. 사용 게이트는 유효한 월정액 패스가 있으면 건당 청구 없이 발행. 셀러 앱 UI는 이 Phase 범위 밖(엔드포인트·테스트만).

### B-5 관리자 큐 API
- `GET /admin/enrollments?status=review_pending|confirm_pending|...` 카드 목록: 신분증 얼굴(서명 URL, 1시간) · 등록 사진 · 지원서 사진 · 자동 매칭 점수 · 성인 여부 · VC 발급 여부 · 조건 요약.
- 승인·재촬영·거절 엔드포인트(B-1). 신분증 얼굴 사진은 **승인 또는 거절 시 즉시 파기**(기존 purge 유틸 재사용), 최대 확정 후 30일.

### B-6 알림
- `facemarket_notify.py`에 메일 3종 추가: 검수 완료·테스트 컷 확인 요청(confirm_pending), 입점 완료(verified), 파기 완료(purge 끝). 광고성 표시 없음(서비스 통지).

수용 기준: pytest 전부 통과, 상태 전이 테스트(각 전이·역전이 금지) 추가, 마이그레이션 up/down 테스트.

## 4. Phase C — 위저드 4단계·확정 화면·콘솔 탭 `codex/facemarket-mvp-c` (Phase B 이후)

- 위저드(`ModelRegister.jsx`)를 B 시안 4단계로: ① 동의와 본인확인(B-0·B-1·B-2·B-3·B-4·B-5·B-6 문안, 끝까지 스크롤 활성화, 개별 체크, 사전 체크 없음, 동의 유형 6종 서버 기록) ② 사진(장수 설정값 `FM_ENROLL_PHOTO_COUNT`, 기본 3) ③ 프로필(체형은 전신 사진 없으면 필수) ④ 사용 조건(허용/제외 품목 ×, 건당 가격 → 월정액 자동 표시, 유효기간 365/730/영구, 상단 고정 문장) → [라이선스 발급] → 완료(A-4). 체형·대표 사진 별도 스텝 제거.
- 확정 화면 `/model/confirm`: 테스트 컷 6장 · 셀러에게 보이는 프로필 미리보기 · [이 모습으로 공개하기] · [다시 만들어 주세요(사유)] · "확정 전 미공개" 문장.
- 관리자 콘솔(`AdminApplications.jsx`)에 "2차 검수" 탭: B-5 카드 + [같은 사람 · 트윈 만들기] [이 각도만 다시 요청] [신원 불일치 · 종료]. 상태별 묶음(대기가 위).
- 규칙 바꾸기: `/model/license`를 편집 모드로(B-2 PATCH), 상단 고정 "변경은 새 사용 건부터", 변경 이력 표시.
- 철회 화면 문안을 `04` G-3으로 교체.

## 5. Phase D — 법률 페이지·동의 기록 (오너 값 입력 후)

- `/terms /privacy /license-agreement /seller-terms /answers` 라우트: `documents/legal/01~06`의 마크다운을 빌드 시 읽어 렌더(`[대괄호]`가 남아 있으면 빌드 실패). 하단 사업자 정보·링크.
- 지원서 A-3·A-4 체크, 로그인 모달 B-0, 셀러 앱 I-1·I-2·I-3.

## 6. Phase E — 출처 4층 (Phase B 이후, 서버)

- ① `fm_settlements`/발행 기록에 `phash` 저장(`imagehash`), ④ `trustmark`로 발행 직전 짧은 ID 삽입, ② `c2pa-python`으로 매니페스트(AI 생성 표시·라이선스 번호·조건 버전·검증 URL) 서명(자체 서명 인증서, 키는 KMS/시크릿), 검증 페이지에 "이미지 업로드로 확인"(워터마크 → 지문 순). 유료 인증서·적합성 프로그램은 쓰지 않는다.

## 7. 도달 경로 요약

| 화면 | 도달 |
|---|---|
| Digital DNA 관리 | 상단바 (비로그인은 로그인 모달 경유) |
| 정산 | 상단바 |
| 위저드 | 승인 메일 링크 → 로그인 → 허브 타임라인 [등록 이어가기] |
| 확정 화면 | confirm_pending 메일 링크 → 허브 타임라인 [테스트 컷 확인하기] |
| 규칙 바꾸기 | 허브 규칙 카드 [규칙 바꾸기] |
| 철회 | 허브 하단 [그만두기] |
| 콘솔 2차 탭 | 관리자 콘솔 탭 |

## 8. Phase C 변경 요약 (2026-09-11)

이 절은 위 초기 계획에서 달라진 현재 구현을 정리해요. Phase C의 화면과 설정은 이 절을 기준으로 읽으면 돼요.

- 증서 유효기간은 선택 없이 영구(철회 전까지)로 고정하고, 등록 위저드와 마이페이지에서 유효기간 선택과 만료 표시를 제거해요(2026-09-11 오너 결정).
- C1에서는 `/status` 마이페이지에 지원과 등록 진행 상황, 수익, 사용 기록과 신고, 사용 조건, 활동 일시 중단을 모았어요. 수익 조회가 실패해도 나머지 화면은 사용할 수 있어요. 옛 `/payout` 주소는 `/status#earnings`로 이동해요.
- C2에서는 등록을 본인확인, 사진, 조건, 증서의 네 단계로 바꿨어요. 사진은 얼굴 8장, 상반신 5장, 전신 5장으로 총 18장이에요. 옛 각도 이름은 서버에서 당분간 받아요. 신분증 초상은 기본 등록 경로에서 받지 않고 얼굴 대조도 기본으로 실행하지 않아요. 체형은 키 구간 없이 제출할 수 있고, 사용 조건은 최소 한 가지를 유지하며 유효기간은 기본 영구예요.
- 가격 안내 기준은 건당 14,900원이에요. 월 이용권은 49,900원으로 월 10건을 포함하고, 초과분은 건당 7,900원이에요. 모델 몫은 70%예요. 모델이 직접 가격을 입력하지 않으며 서버는 라이선스 발급에 `FM_STANDARD_UNIT_PRICE` 값을 적용해요. 이 요약이 월 이용권 구매 기능의 구현 완료를 뜻하지는 않아요.
- C3에서는 관리자 콘솔의 `사용 신고` 메뉴와 `/usage-reports` 화면에서 신고 목록을 확인하고 `open`과 `closed` 상태를 변경할 수 있어요. 상태 변경은 관리자 권한을 확인하고 감사 로그에 이전 값과 새 값을 같은 트랜잭션으로 남겨요. 종결 처리는 신고 상태만 바꾸며 라이선스 취소나 정산 변경을 실행하지 않아요. 목록을 불러오거나 상태를 저장하지 못하면 화면에서 다시 시도할 수 있어요.

| 관리자 API | 하는 일 |
|---|---|
| `GET /v1/facemarket/admin/usage-reports` | `status` 필터와 `cursor`로 최신순 목록을 조회해요. `limit`은 기본 100건이며 1건부터 200건까지예요. 응답은 `items`와 `nextCursor`예요. |
| `PATCH /v1/facemarket/admin/usage-reports/{report_id}` | 본문의 `status`를 `open` 또는 `closed`로 변경해요. |

| 설정 | 기본 선언값 | 적용 내용 |
|---|---|---|
| `FM_FACE_MATCH_ENABLED` | `false` | 기본 등록 경로의 얼굴 대조를 꺼요. |
| `FM_STANDARD_UNIT_PRICE` | `14900` | 서버가 라이선스에 채우는 건당 표준가예요. |
| `FM_USAGE_REPORT_TO_EMAIL` | `contact@wearless.kr` | 회사 정보와 개인정보 처리방침에 명시된 문의 주소를 사용해요. |

세 설정은 `copilot/api/manifest.yml`과 `server/.env.example`에 함께 선언해요. 수신 주소의 근거는 `src/lib/companyInfo.json`과 `documents/legal/11_wearless_privacy_policy_v1.md`의 확정 연락처예요. 신고 상태 제약은 새 전진 마이그레이션 `20260911130000_facemarket_usage_report_status.sql`로 확장해요. 기존 마이그레이션과 소유자 조회 정책은 유지해요. 운영 DB 적용과 배포, 메일 실발송 확인은 별도로 진행해요.

브라우저 스모크: 미실행.
