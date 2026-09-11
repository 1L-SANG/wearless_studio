# FaceMarket 신분증 촬영 + 간편인증 분기 + 관리자 육안 심사 — 설계 스펙

- 작성일: 2026-09-11 (Asia/Seoul)
- 브랜치: `feat/fm-id-capture-branch` (worktree `~/devs/wearless_studio-id-capture`, origin/main 기준)
- 상태: 설계 확정 — 구현 계획은 `docs/superpowers/plans/2026-09-11-facemarket-id-capture-and-admin-review.md`

---

## 1. 왜 하는가

FaceMarket 모델 등록은 지금 **OmniOne CX `ENT_MID`(모바일 신분증) 한 가지 경로뿐**이다. 모바일 신분증을 발급받지 않은 사람은 가입 자체가 불가능하다. 실서비스로 모델을 모으려면 이 벽을 치워야 한다.

### 1.1 현재 구조 실측 (2026-09-11)

| 항목 | 실측값 | 출처 |
| --- | --- | --- |
| 라이브니스 | **꺼져 있음** (`FM_LIVENESS_ENABLED: "false"`) | `copilot/api/manifest.yml:287` |
| 유일한 얼굴 앵커 | **OACX 신분증 초상**(`data.dlphotoimage`) | `facemarket_enrollment.py:2050` |
| 업로드 사진 ↔ 앵커 임계 | 0.15 (정면·45) / 0.10 (측면) | `manifest.yml:300-306` |
| SFace 권장 동일인 기준선 | 0.363 | `FM_FACE_QC_THRESHOLD` |
| 무관 얼굴 실측 점수 | 0.05~0.10 | `manifest.yml` 주석 |
| 관리자 승인 | 지원서 심사(`FM_APPLICATION_REQUIRED: "true"`) — **얼굴 대조 앞단** | `facemarket_applications.py:805` |
| 관리자가 볼 수 있는 이미지 | 지원서 프로필 1장 + 테스트컷 **뿐** | 라우트 전수 3개 |

두 가지가 따라 나온다.

1. **임계 완화 여지가 없다.** 0.15는 무관 얼굴(0.05~0.10)과 0.05 차이다. 더 내리면 스왑 방어가 사실상 0이 된다.
2. **관리자는 지금 얼굴을 실제로 보고 있지 않다.** 등록 사진 3장·신분증 초상을 열람할 라우트가 아예 없다. "어드민이 관리하니까 기계 대조를 느슨하게" 라는 구조가 아직 존재하지 않는다.

### 1.2 간편인증만 붙이면 등록이 아예 안 된다

`ENT_SIMPLE_AUTH`(간편인증)에는 신분증 초상이 없다. 라이브니스도 꺼져 있으므로 **얼굴 앵커가 0개**가 되고, `process_enrollment_completion`이 `id_portrait_unavailable`로 fail-closed 한다. 임계 문제가 아니라 **앵커를 하나 만들어야 하는 문제**다.

---

## 2. 결정

**A + C 를 함께 간다.**

- **A — 신분증 촬영 업로드**: 간편인증 경로에서 사용자가 실물 신분증을 찍어 올린다. 그 얼굴이 SFace 앵커 자리를 대신한다.
- **C — 관리자 육안 심사를 정식 통제로 승격**: 촬영본은 위조 검증이 0이므로 기계 점수만 믿지 않는다. 사진 업로드 직후 **차단형**으로 멈추고 관리자가 실제로 얼굴을 보고 승인해야 다음으로 간다.

A 단독은 위조 신분증을 통과시키고, C 단독은 사용자가 올린 사진 두 장을 비교하는 꼴이라 "실존 인물 본인"이 성립하지 않는다. 둘이 같이 있어야 한 겹씩 맡는다.

### 2.1 확정된 세부 결정 (사용자 승인, 2026-09-11)

| 결정 | 선택 | 근거 |
| --- | --- | --- |
| 신분증 촬영본 보관 범위 | **마스킹 전체본** — 주민번호 뒷자리를 가린 신분증 전면을 격리 버킷에 보관 | 관리자가 발급일·이름·사진을 함께 봐야 위조를 판별할 수 있다. 얼굴 크롭만으로는 육안 심사가 무력해진다 |
| 관리자 심사 위치 | **사진 3장 직후, 차단형 대기** (`review_pending`) | 미승인 얼굴이 자산 빌드·라이선스 파이프라인에 절대 들어가지 않는다 |
| 간편인증 배선 범위 | **이번 스펙에 포함.** 버튼 분기까지 만들고 실서비스 목표 | 모델을 실제로 모아야 한다. 해커톤 발표 때는 플래그로 `mid` 만 남긴다 |
| 신분증 촬영 순서 | **본인인증보다 먼저** | §3.2 |

---

## 3. 사용자 흐름

### 3.1 두 경로

```
[공통] 지원서 제출 → 관리자 승인 → 등록 위저드 진입 → 동의(B-1~B-6)
                                                         ↓
                                            ┌──── 인증 수단 선택 ────┐
                                            ↓                        ↓
                              [경로 M] 모바일 신분증        [경로 S] 간편인증
                                            ↓                        ↓
                                   OACX ENT_MID          ① 신분증 촬영·마스킹 업로드
                                   (초상 자동 확보)                   ↓
                                            ↓             ② OACX ENT_SIMPLE_AUTH
                                            ↓                        ↓
                                     사진 3장 업로드 ←───────────────┘
                                            ↓                        ↓
                                   (SFace enforce)          [review_pending]
                                            ↓                관리자 육안 심사
                                            ↓                        ↓ 승인
                                            └──────→ 자산 빌드 ←─────┘
                                                         ↓
                                              체형 → 사용조건 → 라이선스 발급
```

경로 M은 **현행 동작 무변경**이다. 플래그 `FM_IDENTITY_METHODS=mid` 이면 선택 화면 자체가 사라지고 지금과 똑같은 UX가 된다(발표용 롤백).

### 3.2 왜 신분증 촬영이 인증보다 먼저인가

1. **탈락을 먼저 시킨다.** 신분증이 없으면 경로 S 자체가 불가능하다. 간편인증은 건당 과금이므로 인증을 먼저 태우고 나서 신분증이 없다는 걸 알게 되면 돈과 시간을 둘 다 버린다.
2. **입력 불일치를 줄인다.** 간편인증 창은 이름·생년월일·휴대폰번호를 **사용자가 직접 입력**하게 한다(OACX 위젯 `UserFormInput`, PASS면 통신사 드롭다운까지). 신분증을 눈앞에 두고 입력하면 오타·불일치가 준다. 지원서 대조(`identity_claim_mismatch`)는 3회 실패 시 지원서가 자동 거절되므로 이 감소분이 그대로 이득이다.
3. **새로고침 내성.** 현행 `portraitRef`(프론트 메모리)는 새로고침하면 사라져 `reidentify` 스텝으로 신분증을 다시 확인해야 한다. 촬영본을 촬영 스텝에서 곧장 서버(enrollment 귀속)에 올리면 그 문제가 없다.

**대가**: 인증에 실패할 사람의 신분증을 먼저 받게 된다. §7.3의 파기 규칙이 이를 상쇄한다 — 인증 실패·취소·만료 전부에서 즉시 삭제한다.

---

## 4. 상태 머신

### 4.1 신규 상태 2개

`fm_biometric_enrollments.status` CHECK 제약에 추가한다.

| 상태 | 진입 조건 | 경로 |
| --- | --- | --- |
| `id_capture_pending` | 경로 S 등록 생성 직후 | S 전용 |
| `review_pending` | 사진 3장 업로드 + `/complete` 처리 완료 | S 전용(기본), `FM_ENROLLMENT_REVIEW=all` 이면 M도 |

### 4.2 전이

```
경로 M (현행 무변경)
  identity_pending → photos_pending → processing → asset_building
                                    → license_pending → vc_pending → passed

경로 S (신규)
  id_capture_pending → identity_pending → photos_pending → review_pending
                                    ↓(승인)
                                 processing → asset_building → … → passed
                                    ↓(거절)
                                 failed (reason='review_rejected')
```

`fm_biometric_active_per_user` partial unique index 의 상태 목록에 `id_capture_pending`, `review_pending` 을 추가한다. 빠뜨리면 심사 대기 중인 사용자가 등록을 하나 더 만들 수 있다.

### 4.3 프론트 스텝 매핑

`src/features/model/biometricEnrollment.js:53` `nextEnrollmentStep()`:

```js
if (enrollment.status === 'id_capture_pending') return 'id_capture';
if (enrollment.status === 'review_pending') return 'review';
```

`review` 스텝은 폴링 없이 안내 화면만 띄운다(결과는 메일로 알린다). 사용자가 나중에 돌아오면 `GET /enrollments/current` 가 상태를 알려 준다.

---

## 5. 데이터 모델

### 5.1 마이그레이션 `supabase/migrations/2026xxxx_facemarket_id_capture_review.sql`

`fm_biometric_enrollments` 에 추가 (전부 additive·nullable):

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `identity_method` | `text` default `'mid'` | `'mid'` \| `'simple_auth'` |
| `id_document_r2_key` | `text` | 마스킹 신분증 객체 키. 경로 S 전용 |
| `id_document_type` | `text` | `'rrc'`(주민등록증) \| `'dl'`(운전면허증) \| `'passport'` \| `'arc'`(외국인등록증) |
| `id_document_uploaded_at` | `timestamptz` | |
| `id_document_purged_at` | `timestamptz` | 파기 완료 시각. non-null 이면 키는 이미 삭제됨 |
| `review_status` | `text` | `null` \| `'pending'` \| `'approved'` \| `'rejected'` |
| `reviewed_by` | `uuid` | 관리자 user_id |
| `reviewed_at` | `timestamptz` | |
| `review_reason` | `text` | 거절 사유(승인 시 null) |
| `match_scores` | `jsonb` | advisory 점수 스냅샷 — §6.2 |

`identity_contract_version`(기존 컬럼)에 경로별 계약 버전이 그대로 들어간다 — 경로 M은 `prod-dlphoto-v1`, 경로 S는 `simple-auth-v1`. **감사 추적용 신규 컬럼은 필요 없다.**

CHECK 제약·partial unique index 는 §4 대로 재생성한다(`drop constraint if exists` → `add constraint` 패턴, `20260824000000_facemarket_identity_first_reorder.sql` 선례).

### 5.2 R2 키

`server/app/r2.py` 에 추가:

```python
def enrollment_id_document_key(enrollment_id: str, ext: str) -> str:
    return f"facemarket/enrollments/{enrollment_id}/iddoc/masked.{ext}"
```

기존 `enrollment_quarantine_key`(사진 3장)와 **prefix 를 분리**한다. `_drain_photo_cleanup` 의 사진 정리 로직이 신분증을 건드리지 않게 하고, 신분증만 따로 파기·스윕할 수 있게 한다.

버킷은 `R2_FACE_BUCKET`(`wearless-face`, 공개 도메인 미연결) 그대로. 신분증은 생체 PII 와 동급으로 취급한다.

---

## 6. 매칭·심사 정책

### 6.1 경로별 차이

| | 경로 M (모바일 신분증) | 경로 S (간편인증 + 촬영) |
| --- | --- | --- |
| 앵커 출처 | OACX `dlphotoimage` (정부 VC, 서명검증됨) | 사용자 촬영본에서 YuNet 얼굴 크롭 |
| SFace | **enforce** — 임계 미달 시 `face_match_failed` 종료 (현행) | **advisory** — 점수만 계산·저장, 차단하지 않음 |
| 차단 주체 | 기계 | **사람(관리자)** |
| 얼굴 미검출 | `face_match_failed` | **차단** — 심사할 대상이 없으므로 재촬영 요구 (`id_face_not_detected`) |

**경로 S에서 SFace를 enforce 하지 않는 이유**: 촬영본은 위조 가능하므로 점수가 높아도 안전을 보장하지 않고, 촬영 품질(조명·각도·코팅 반사) 편차 때문에 낮아도 본인일 수 있다. 어느 방향으로도 기계 판정이 신뢰구간을 못 만든다. 그래서 점수는 관리자에게 **정보로** 넘기고 판정은 사람이 한다. 이것이 C 안의 내용이다.

### 6.2 `match_scores` 스냅샷

```json
{
  "policyVersion": "prod-2026-08",
  "anchor": "id_document_crop",
  "thresholds": { "front": 0.15, "angle45": 0.15, "side": 0.10 },
  "scores": { "front": 0.31, "angle45": 0.27, "side": 0.11 },
  "belowThreshold": [],
  "computedAt": "2026-09-11T12:34:56Z"
}
```

`no_face_detected` 로 건너뛴 각도는 `scores` 에서 빠지고 `skipped: ["side"]` 로 기록한다(현행 매칭 루프가 측면을 건너뛰는 것과 같은 규칙 — `facemarket_enrollment.py:2088`).

### 6.3 관리자 심사 카드에 실리는 것

1. **신분증 마스킹 전체본** (`kind=id_document`)
2. **등록 사진 3장** (`front` / `angle45` / `side`)
3. **지원서 정보** — 이름·생년월일·프로필 사진 (`fm_model_applications` 조인)
4. **자동 대조 결과** — `match_scores`, 임계 미달 각도는 빨간 배지
5. **CI 대조 결과** — 지원서 이름·생년월일 ↔ 인증 결과(이미 `verify_enrollment_identity` 가 수행). `identity_mismatch_count` 노출
6. **마스킹 확인 체크** — §7.2

승인 / 거절(사유 선택) 두 버튼. 두 동작 모두 `admin_guard.write_audit` 으로 감사 기록을 남긴다(`action='enrollment_review_approve'|'enrollment_review_reject'`, `target_type='enrollment'`).

---

## 7. 개인정보·법무

### 7.1 수집 최소화 원칙 대비

현행 정책(`documents/legal/03_facemarket_privacy_policy_v1.md:72`)은 *"회사는 신분증 원본 이미지를 저장하지 않고 신분증 번호를 받지 않습니다"* 다. 경로 S는 이 문장을 **부분적으로 깬다**. 개정 없이 출시하면 안 된다.

### 7.2 마스킹 — 클라이언트 수행, 서버 검증 불가

주민등록번호는 법령 근거 없이 수집할 수 없다. 신분증 전면에는 번호가 찍혀 있다.

**방식**: 클라이언트에서 사용자가 직접 가린다.
1. 신분증 종류 선택 → 종류별 기본 마스킹 박스를 오버레이로 제시
2. 사용자가 드래그로 위치·크기 조정
3. 전송 전 **캔버스에 실제 픽셀을 덮어쓴다**. 원본은 업로드하지 않는다
4. "가렸음" 확인 체크 필수 (`maskedConfirmed: true`)

**한계를 명시한다**: 서버는 마스킹 여부를 검증할 수 없다(OCR 자동 마스킹은 비범위). 그래서 관리자 심사 카드에 **마스킹 확인** 항목을 두고, 미마스킹이 발견되면 관리자가 즉시 거절 → 객체 삭제 → 재제출을 요구한다. 자동 OCR 마스킹은 v2 과제로 남긴다.

### 7.3 파기 규칙 (fail-safe 3겹)

| 시점 | 동작 |
| --- | --- |
| 관리자 승인 / 거절 | 같은 트랜잭션 커밋 직후 R2 객체 삭제 + `id_document_purged_at` 기록 |
| enrollment 취소 / 실패 / 만료 | 사진 정리 경로(`_drain_photo_cleanup` 패턴)에 신분증 삭제를 함께 태운다 |
| 배치 스윕 (안전망) | `r2.list_prefix_aged("facemarket/enrollments/", older_than_seconds=7*86400)` 로 `iddoc/` 잔존 객체 삭제. 앞의 두 경로가 실패해도 7일이 상한 |

`id_document_purged_at` 이 non-null 인데 객체가 남아 있으면 스윕이 정리한다. 반대로 객체가 없는데 키가 남아 있으면 관리자 이미지 라우트는 404를 돌려준다.

### 7.4 개정해야 하는 문서

| 파일 | 내용 |
| --- | --- |
| `documents/legal/03_facemarket_privacy_policy_v1.md` §52·§72 | 경로 S의 신분증 촬영본 수집·보관 목적·보관 기간(심사 종료 즉시, 최대 7일)·마스킹 고지 추가 |
| `documents/legal/00_facemarket_legal_notice_map_v1.md` B-3 | "신분증 얼굴 대조 보관" 고지를 경로별로 분리 서술 |
| `documents/facemarket_apply_faq.md` | "한국 모바일 신분증으로 본인확인" → 두 경로 안내로 교체 |
| 동의 버전 | `ACCEPTED_CONSENT_VERSIONS`(`facemarket_enrollment.py:38`)에 `2026-09-v1` 추가, 프론트 `CONSENT_VERSION` 갱신. 기존 `2026-08-v2` 는 남겨 진행 중 등록을 깨지 않는다 |

> 이 스펙은 기술 설계다. 법률 의견서가 아니다. 출시 전 개인정보 보호책임자 검토가 필요하다.

---

## 8. API 계약

### 8.1 신규·변경 라우트

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| `POST` | `/v1/facemarket/enrollments` | **변경** — body 에 `identityMethod: "mid" \| "simple_auth"` 추가(기본 `"mid"`). `simple_auth` 면 시작 상태가 `id_capture_pending` |
| `POST` | `/v1/facemarket/enrollments/{id}/id-document` | **신규** — multipart. `file`, `documentType`, `maskedConfirmed`. `id_capture_pending` 에서만 허용 |
| `POST` | `/v1/facemarket/enrollments/{id}/identity` | **변경** — `identity_method` 에 따라 계약 어댑터 분기 |
| `POST` | `/v1/facemarket/enrollments/{id}/complete` | **변경** — 경로 S면 `review_pending` 으로 전이하고 자산 빌드를 시작하지 않음 |
| `GET` | `/v1/facemarket/admin/enrollments` | **신규** — 심사 큐. `?review=pending\|approved\|rejected` |
| `GET` | `/v1/facemarket/admin/enrollments/{id}` | **신규** — 심사 카드(§6.3) |
| `GET` | `/v1/facemarket/admin/enrollments/{id}/images/{kind}` | **신규** — `kind`: `id_document` \| `front` \| `angle45` \| `side`. `Cache-Control: private, no-store` |
| `POST` | `/v1/facemarket/admin/enrollments/{id}/approve` | **신규** |
| `POST` | `/v1/facemarket/admin/enrollments/{id}/reject` | **신규** — body `{ reason }` |

관리자 라우트는 전부 `_require_admin(conn, user_id, request)` 를 탄다(기기 게이트 포함 — `ADMIN_DEVICE_GATE=enforce` 가동 중). 이미지 라우트는 `facemarket_applications.py:941` `admin_application_photo` 패턴을 그대로 따른다.

### 8.2 간편인증 계약 어댑터

`server/app/cx_identity.py` 에 추가:

```python
SIMPLE_AUTH_CONTRACT = OacxBiometricContract(
    version="simple-auth-v1",
    birth_path=("birth",),
    portrait_encoding="hex",     # 미사용 — 경로 S는 초상을 OACX에서 받지 않는다
    max_portrait_bytes=0,
)

async def fetch_simple_auth_identity(base_url: str, token: str) -> dict: ...
```

`get_oacx_biometric_contract(settings, *, method: str)` 로 시그니처를 확장해 `method='simple_auth'` 일 때 위 계약을 돌려준다. `FM_OACX_SIMPLE_AUTH_CONTRACT=disabled`(기본)면 `OacxBiometricError("oacx_contract_unavailable")` 를 던진다 — 스키마 확정 전에는 호출 자체가 불가능하다.

`verify_enrollment_identity` 는 이미 **`ci`·`name`·`birth` 세 개만** 사용한다(초상을 보지 않는다). 어댑터가 이 셋을 채워 주면 검증·지원서 대조·CI 중복 차단이 전부 그대로 동작한다.

---

## 9. 플래그·롤백

| 환경변수 | 기본 | 값 | 효과 |
| --- | --- | --- | --- |
| `FM_IDENTITY_METHODS` | `mid` | `mid` \| `mid,simple_auth` \| `simple_auth` | 노출할 인증 수단. `mid` 면 선택 화면이 사라지고 **현행 UX 그대로** |
| `FM_ENROLLMENT_REVIEW` | `simple_auth_only` | `off` \| `simple_auth_only` \| `all` | 관리자 심사 적용 범위 |
| `FM_OACX_SIMPLE_AUTH_CONTRACT` | `disabled` | `disabled` \| `simple-auth-v1` | 간편인증 계약 활성 |
| `VITE_CX_AUTH_CONFIG_URL` | — | 라온 발급 auth config URL | 간편인증 위젯 설정 |
| `VITE_FM_IDENTITY_METHODS` | `mid` | 서버와 동일 | 프론트 버튼 분기 |

**발표(해커톤 심사) 모드**: `FM_IDENTITY_METHODS=mid` 한 줄. 버튼 분기·신분증 촬영·심사 대기가 전부 사라지고 지금과 동일한 모바일 신분증 데모가 된다. 배포 없이 env 만 바꾸면 된다.

**전면 롤백**: 위 4개를 기본값으로 되돌린다. 마이그레이션은 additive 라 되돌릴 필요가 없다(신규 컬럼은 `identity_method='mid'` 기본값으로 무해하게 남는다).

---

## 10. 미검증 가정 — 실거래 전 확정 필요

`FM_OACX_SIMPLE_AUTH_CONTRACT=disabled` 인 동안에는 아래 중 무엇이 틀려도 프로덕션에 영향이 없다. 켜기 전에 전부 확정한다.

| # | 가정 | 확인 방법 |
| --- | --- | --- |
| 1 | `ENT_SIMPLE_AUTH` 결과에 `ci` 가 포함된다 | 라온 서면 답변 + 실거래 1회 |
| 2 | 결과 취득이 `trans/{token}` 으로 된다 (아니면 `authen/result` / `parse_token`) | 실거래 1회. config.auth.json 은 `/oacx/api/v1.5/authen/request` + `/authen/status` 를 쓴다 |
| 3 | `v1.5_enc` userInfo 복호화 규격 | 라온 문서 |
| 4 | 우리 서비스용 auth config 발급 조건·과금 | 라온 협의 |

**실측으로 이미 확인된 것**(2026-09-11):
- `POST /oacx/api/v1.0/provider/list` 바디 `{serviceId, signType}` 로 signType 별 인증사 필터링이 동작한다.
- `ENT_SIMPLE_AUTH` 활성 인증사 13곳 — 통신사패스(PASS)·카카오지갑·네이버·삼성패스·토스×2·신한·KB·NH·하나·우리·IBK·뱅크샐러드.
- `ENT_MID` 는 위젯이 `setSortCategory(["mid"])` 로 강제하므로 **한 창에서 두 수단을 같이 띄울 수 없다** → 버튼 2개로 위젯을 두 번 호출하는 분기여야 한다.

---

## 11. 비범위

- **OCR 자동 마스킹** — v2. 이번엔 클라이언트 수동 마스킹 + 관리자 확인
- **신분증 진위확인 API**(행안부 주민등록증 / 경찰청 운전면허) — 별도 계약·비용. 이번엔 관리자 육안이 대신한다
- **라이브니스 재가동** — `manifest.yml:285` 에 "라이브프레임 0.25 vs 신분증초상 0.46 실측, 애드온이라 제거" 기록이 있다. 되돌리면 그 불안정성이 복귀한다
- **SFace 임계 재캘리브** — 경로 M 임계는 그대로 둔다. 경로 S는 advisory 라 임계가 차단에 쓰이지 않는다
- **간편인증 실거래 검증** — 라온 auth config 수령 후 별도 작업

---

## 12. 열린 리스크

| 리스크 | 영향 | 완화 |
| --- | --- | --- |
| 관리자 심사가 병목 | 등록 처리 지연 | `FM_ENROLLMENT_REVIEW=off` 로 즉시 해제 가능. 큐 길이를 대시보드에 노출 |
| 마스킹 미이행 신분증 업로드 | 주민번호가 서버에 들어옴 | 관리자 즉시 거절 + 객체 삭제. 7일 배치 스윕이 상한 |
| 위조 신분증이 육안을 통과 | 실존인물 보증 훼손 | 지원서 대조(CI ↔ 이름·생년월일) 가 동시에 걸린다. 위조 신분증으로 간편인증까지 통과하려면 타인 명의 인증수단도 필요 |
| 간편인증 스키마가 예상과 다름 | 경로 S 불가 | 어댑터 격리 + 기본 `disabled`. 경로 M은 무영향 |
