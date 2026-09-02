# TODOS

## FaceMarket

### 30일 PII 익명화 sweep 구현 (데모 후 첫 후속 PR)

**What:** 거절(application_rejected)·취소(cancelled) 30일 경과 지원서의 PII 필드 익명화 + R2 프로필 사진 삭제를 기존 terminal-enrollment sweep에 추가.

**Why:** 2026-09-02 CEO 리뷰에서 정책은 확정(결정 3A)했으나 구현은 해커톤 크리티컬 패스 밖으로 연기(9/21 전엔 30일 경과 행이 없어 발화 불가). 잊으면 프라이버시 약속이 공약이 됨 — 기존 시스템은 해시·마스킹·연도만 저장하는 설계라 지원서 평문 PII 방치는 설계 모순.

**Context:** 계획서 `~/.gstack/projects/1L-SANG-wearless_studio/ceo-plans/2026-09-02-facemarket-application-renewal.md`의 3A 결정 참조. 대상 = `fm_model_applications`의 PII 필드 + R2 프로필 사진(application id 귀속 키). 재지원 프리필은 30일 내에만 사진 포함 가능하다는 결정과 정렬. ⚠️ 코덱스 지적: 기존 sweep은 AI dispatcher에 종속(R2·AI provider 없으면 미기동, `server/app/main.py:193`) — privacy 잡은 dispatcher 가용성과 무관하게 독립 기동해야 함.

**Effort:** S
**Priority:** P1
**Depends on:** FaceMarket 지원서·검토 리뉴얼 머지

### body-type matrix ↔ DB CHECK 불일치 픽스

**What:** `facemarket_physique.py:27`의 body-type matrix 값과 `20260828000000_facemarket_model_physique.sql:21`의 DB CHECK 제약이 불일치 — 코드가 허용하는 값을 DB가 거부(또는 역). 정합화.

**Why:** 2026-09-02 코덱스 외부 리뷰가 발견한 기존 버그. 컴카드 스펙 확장(리뉴얼 T10)이 이 경로를 다시 밟기 전에 수정해야 안전.

**Context:** CEO 리뷰 계획서 traps 참조. 실제 어긋난 값 목록 확인 후 CHECK 재생성 or 코드 상수 수정 중 택일.

**Effort:** S
**Priority:** P2
**Depends on:** None

### 카테고리 → 셀러 카탈로그 연동

**What:** 지원서에서 수집한 카테고리(Fashion/Commercial/Fitness/LifeStyle)를 셀러 카탈로그에 필터·뱃지로 노출.

**Why:** 리뉴얼에서 카테고리는 수집만 하고 노출은 스킵됨(2026-09-02 체리픽 D4.2). 연동하면 "지원서가 마켓을 구조화한다"는 루프가 완성되고 셀러 모델 탐색 UX가 개선됨.

**Context:** 지원서 카테고리는 다중 선택. 셀러 카탈로그 화면(모델 대표 이미지 노출 — PR #203/#207 참조)에 필터 UI + 모델 카드 뱃지 추가. 백엔드는 카탈로그 쿼리에 카테고리 조건 추가만.

**Effort:** M
**Priority:** P3
**Depends on:** FaceMarket 지원서·검토 리뉴얼 머지
