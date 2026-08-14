# 커스텀 매칭 의류 누끼(배경 제거) 설계

작성일: 2026-08-13
브랜치: `feat/matching-cutout` (origin/main `f95f2e8` 기준)

## 문제

분석 화면의 매칭 의류 카드는 두 종류를 나란히 보여준다.

- **시드 카탈로그 62개** (`seed/matching/*.png`): 1024², 회색 스튜디오 flat-lay. 깔끔하다.
- **커스텀 업로드** (셀러가 올린 내 옷): `thumbnail_asset_id` = 업로드 원본 첫 장.
  실측한 실물은 **매장 행거 사진** — 비스듬한 각도에 배경으로 다른 옷 4벌·신발·가방이
  같이 찍혀 있다.

그래서 커스텀만 화면에서 튄다. 그리고 커스텀 매칭의 마네킹 생성 입력(`image_asset_id` =
`garment_grid` 4분할 합성)에도 그 배경 옷들이 그대로 들어가, 생성 결과에 배경 옷이 섞일
위험이 있다.

## 목표

셀러가 커스텀 매칭 의류를 업로드하면 SAM2 로 배경을 제거(누끼)해서, 화면 카드도 생성
입력도 시드처럼 깨끗한 흰 배경 컷으로 보이게 한다.

## 결정 사항 (확정)

| 항목 | 결정 | 근거 |
|---|---|---|
| 스코프 | **썸네일 + 생성입력 둘 다** 누끼 | 화면 정돈 + 배경 옷이 생성에 섞이는 것 방지 |
| 배경 | 흰색 합성, 불투명 저장 | 카드 CSS 가 바뀌어도 일관. 시드(옅은 회색 ≈232)와 미세하게 밝지만 무시 가능 수준 |
| 대상 | 신규 커스텀 업로드만 | 기존 소급 없음 (별도 배치 불필요) |
| 등록 UX | 등록·선택은 즉시. 카드는 "누끼 처리 중" 로딩 → 준비되면 자동 교체 | 원본(지저분)을 아예 안 보여준다. SAM 추론 ~25s 라 "즉시"는 물리적 불가 |
| 실패 | 원본 유지, 매칭 자체는 안 막음 | 캐노니컬 컷아웃과 같은 fail-open |
| 잡 | 신규 무과금 `matching_cutout` | `sam_preprocess` 패턴 복제 |

## 아키텍처

기존 부품 재사용이 핵심이다. 새 아키텍처가 아니라 배선이다.

```
커스텀 매칭 등록 (POST .../analysis/custom-match-item)
   ↓ 기존 흐름: 원본 저장 + garment_grid 합성 + insert_custom_matching_item
   ↓ 커밋 후 (별도 트랜잭션, 예외 삼킴)
matching_cutout 잡 enqueue → 즉시 응답 (카드 = 처리 중)
   ↓ 워커 (백그라운드)
각 업로드 원본 → sam_client.segment_garment (투명 RGBA 컷아웃)
   ↓
흰 배경 합성 → 불투명 PNG → R2 (derived 경로)
   ↓
누끼본들로 garment_grid 재합성 (생성 입력용)
   ↓
matching_items 의 thumbnail_asset_id / image_asset_id 를 누끼본으로 스왑
   ↓ (프론트)
매칭 상태 폴링이 "ready" 감지 → 카드 이미지 교체
```

### 재사용하는 기존 부품

- `sam_client.segment_garment(settings, {view: r2_key})` — 이미 투명 RGBA 컷아웃을 R2 에
  써서 키를 돌려준다. R2 키 화이트리스트에 `users/.../uploads/` 이미 허용됨.
- `garment_grid.compose_garment_grid(images)` — 1~4장 합성. 누끼본으로 재호출.
- `sam_preprocess_job` — 무과금·fail-open·finalize_uncharged_job 잡 패턴의 선례.
- `_enqueue_...` 커밋 후 별도 트랜잭션 + 예외 삼킴 패턴 (tone editor / base fidelity).

### 신규 코드

- `server/app/workers/matching_cutout_job.py` — 워커. 원본→누끼→흰배경합성→grid재합성→
  asset 스왑. 실패 시 원본 유지.
- `server/app/services/matching_cutout.py` — 흰배경 합성 + 파생 asset 기록 헬퍼.
- 라우트 `add_custom_match_item` 끝(커밋 후)에 enqueue 훅.
- 프론트: 커스텀 카드 상태 = 처리 중/준비됨 구분 + 폴링 + 안내 문구.
- 마이그레이션: `jobs_kind_check` 에 `matching_cutout` 추가.

## 컴포넌트별 책임

### matching_cutout_job (워커)
- 입력: `{projectId, sourceAssetIds, matchingItemId}` (커밋된 커스텀 아이템 식별자)
- SAM 미설정/실패/재시도 소진 → 원본 유지하고 `skipped`/`failed` 로 종결(raise 안 함).
- 각 원본 컷아웃 → 흰배경 합성 → grid 재합성 → asset 스왑을 **원자적 커밋**으로.
  스왑 전 실패는 원본 그대로 남는다(부분 상태 없음).
- 무과금: `credits_reserved=0`, Gemini/VLM 호출 없음.

### matching_cutout 서비스
- `flatten_on_white(rgba_png) -> opaque_png` — 투명 컷 위에 흰 배경.
- 파생 asset 신원: 소스 해시 + 누끼 알고리즘 버전 기반(재시도 시 중복 방지).

### 프론트
- 커스텀 매칭 아이템에 `cutoutStatus` (처리 중 / 준비됨 / 실패) 반영.
- 처리 중: 스켈레톤 + "이미지 업로드됐어요! 지금 배경 정리 중이에요" 안내.
- 폴링으로 준비 감지 → 카드 이미지 교체. 새로고침 불필요.
- 실패: 원본 이미지로 표시(조용히). 매칭 선택은 내내 가능.

## 멱등·상태

- 잡 멱등키: `{project}:matching_cutout:{matchingItemId}:{알고리즘버전}`.
- 커스텀 아이템은 프로젝트당 하나(`get_custom_matching_item` 이 이미 보장). 잡도 그에 준함.
- **상태 노출: 새 API 를 만들지 않는다.** `repo.list_active_matching_items` 조회 결과에
  `cutoutStatus`(처리 중/준비됨/실패)를 실어서, 프론트가 이미 쓰는
  `refreshMatchClothing` 폴링 경로가 그대로 감지하게 한다. 상태의 출처는 누끼 파생
  asset 의 존재 여부 + 메타(원본 asset 이 아직 걸려 있으면 처리 중).

## 품질 게이트 (구현 중 필수)

⚠️ 캐노니컬 selector(`select_garment_mask`)는 "중앙 최대 solid 후보"를 고른다. flat-lay
상품 사진에 맞춰 검증됐다. **매장 행거 사진(배경 옷들·비스듬 각도)은 다른 분포**라
selector 가 배경 옷을 고르거나 옷을 잘못 자를 수 있다.

→ 배선 전에 **실제 커스텀 업로드 샘플 코퍼스로 시각 검증**한다(톤 에디터 때와 동일 규율:
숫자 지표가 아니라 오버레이를 눈으로 본다). 통과 못 하면 그 사실을 보고하고 대안을 논의한다
(예: 커스텀만 다른 프롬프트 전략, 또는 누끼 결과 신뢰도 게이트).

## 하지 않는 것 (YAGNI / 범위 밖)

- 기존 커스텀 아이템 소급 배치.
- 시드 카탈로그 재처리(이미 깨끗).
- 누끼 품질을 높이려 새 세그멘테이션 모델 도입 — 기존 SAM2 로 검증, 안 되면 보고.
- 매칭 하의 QC(바지 정체성 판정) — **별개 작업**, 별도 브랜치·PR·세션에서 진행.

## 마이그레이션·배포

- 마이그레이션: `jobs_kind_check` 에 `matching_cutout` 한 줄 추가 + 활성 유니크 인덱스
  제외 목록(프로젝트당 하나라 제외 불필요할 수 있음 — 확인).
- 배포 순서(둘 다 바뀌면): 마이그레이션 → (SAM 무변경이라 sam2 재배포 불필요) → api →
  프론트. SAM 서비스 코드는 안 건드린다.
- 이 브랜치에서 배포는 하지 않는다.

## 건드리지 않는 것

- SAM 캐노니컬 세그멘테이션(`segmentation.py`, `sam2-grid8-v2`), 알고리즘·버전·selector.
- 톤 에디터, untuck 예산 구조.
- 크레딧 정책.
- 매칭 추천 랭킹 로직(`matching.py`) — 이미지 파이프라인만 바꾼다.

## 테스트 계획

- 워커: enqueue 는 커밋 후, 무과금, 실패 시 원본 유지·매칭 안 막음, 멱등, Gemini/VLM 미호출.
- 서비스: `flatten_on_white` 가 불투명·소스 크기, 파생 신원 결정론.
- 마이그레이션: 신규 kind 가 `jobs_kind_check` 에 포함.
- 프론트: 처리 중 상태 렌더, 준비 시 교체, 실패 시 원본 폴백.
- 시각 QA: 커스텀 업로드 샘플 코퍼스 오버레이 눈으로 검수(품질 게이트).
