# Wearless — 구현 현황·할 일 (TODO / Status)

> 역할: **설계 문서(PRD·계약·상태모델·파이프라인)에서 "해야 할 일 / 진행 상태 / 코드가 아직 계약을 못 따라간 갭"을 한곳에 모은 문서.**
> 설계 문서들은 "무엇이 맞는가(정체성·계약)"에 집중하고, "무엇이 아직 안 됐는가"는 여기서 추적한다.
> 갱신 방식: 작업을 끝낼 때마다 각 설계 문서를 고치지 않고 **이 문서만 주기적으로 갱신**한다.
> 정책 미확정(크레딧 단가·환불 등)과 단계별 실행 로드맵은 중복하지 않고 `backend_integration_plan.md` §10(로드맵)·§11(오픈 이슈)을 가리킨다.
> 최종 갱신: 2026-06-14

---

## 1. 코드 ↔ 계약 동기화 갭 (마이그레이션 TODO)

> "문서가 맞고 코드가 아직 못 따라간" 항목. 문서가 정본이다(`common_data_contract.md` §5 적용 방식).
> ✅ = 반영 완료(기록), 🔶 = 남음, 🆕 = 2026-06-14 결정으로 새로 추가.

### 완료 (기록용)

- ✅ `project` 신설(`createProject`/`getProject`/`patchProject`), `/editor/:id`를 projectId로 사용.
- ✅ 컷 토큰 통일(`cutType: styling|horizon|product` + `source: ai|mine`), `daily`·`studio` 제거.
- ✅ 한국어 저장값 영문화(조정 enum, `MeasurementKey`, `subCategories {value,label}` 등) + 라벨 파생.
- ✅ `saveStoryboard` 생성 CTA에서 호출 후 이동.
- ✅ `generateDetailPage`가 저장된 콘티 기반 생성(`buildEditorBlocksFromStoryboard`).
- ✅ 크레딧 봉투(`{ data, credits }`) 5종 + `syncCredits` 단일화(에디터 로컬 account 제거).
- ✅ 제품컷 옵션 카탈로그화(`productDirections`/`productShotTypes`).
- ✅ 콘티 '내 레퍼런스' 영속(`block.refImages`) + 에디터 `NewCutRequest.refImages` 입력.
- ✅ `lib/types.js`가 계약 미러링, `ProjectSummary`(`blockCount`·ISO `updatedAt`), id 생성기 `lib/ids.js` 이관.
- ✅ `saveEditorBlocks` — 저장 버튼 + 1.5s 디바운스 자동 저장 + 이탈 시 플러시(세션 내 재진입 유지).

### 남음

> 각 항목 끝의 **→ Phase N**은 그 갭을 처리할 단계. 백엔드 에이전트는 해당 Phase 착수 시, 그 Phase가 건드리는 함수의 갭을 **함께** 처리한다(별도 "코드 정리 Phase" 없음). `독립`은 백엔드와 무관해 아무 때나 가능.

- 🔶 **Product/Analysis 소유권 일원화** — `clothingType`·`measurements`·`measurementsUnknown`을 **Product 단일 소유**로. 현재 프론트는 이 필드들을 analysis 작업본에 두고 `saveAnalysis`가 product에 미러링하는 과도기 규칙(`src/mock/api.js` saveAnalysis). 최종은 analysis 레코드에서 제거. (구 계약 §7-4) **→ Phase 3**
- 🔶 **화면의 `Placeholder` 직접 import 정리** — 콘티 새 블록 썸네일·분위기 예시. 분위기 예시의 운영자 시드 전환과 함께. (구 계약 §7-9 잔여) **→ Phase 3~ (분위기 예시 시드)**
- 🔶 **죽은 코드 정리** — `AnalysisForm.jsx`의 미사용 `Analysis` 라우트 컴포넌트(구 시그니처 `analyzeProduct({})`·`saveAnalysis(null,…)` 호출 포함), `colorIds` 잔존 읽기, `catalogs.backgrounds`·`extendedColorPriority`(소비처 0). db 재작성 시 제거. (구 계약 §7-13) **→ Phase 7 (또는 수시)**
- 🔶 **TanStack Query 도입** — 백엔드 연동과 함께. 서버 상태를 Query 캐시로, store의 account/catalogs 캐시 제거. (구 상태모델 §8-7, `03_기술스택_결정서.md` §3) **→ Phase 1**

### 신규 (2026-06-14 결정)

- 🆕 **`matchClothing` → `matchCandidates`(+`matchSelections`) 코드 리네임** — 설계 문서는 이미 계약 이름으로 통일됨. 코드(`src/lib/types.js`, `src/mock/*`, `AnalysisForm.jsx`, `Mannequin.jsx`, `Storyboard.jsx`)는 아직 `matchClothing`+`.selected`/`.selOrder` 사용. 계약 §3.2 shape로 이관. **→ Phase 3** (분석 어댑터가 계약 shape로 응답하면 프론트가 안 깨지려면 동시 필수)
- 🆕 **plan 토큰 `basic`/`plus`/`seller`** — 계약 §3.7/§4는 소문자 토큰 + 라벨(Basic/Plus/Seller)로 갱신됨. 코드 동기화 필요: `src/lib/types.js`(`@property {'Free'|'Pro'|'Team'} plan`), `src/mock/db.js`(`account.plan: 'Pro'`), `shell.jsx` 표시(토큰→라벨). **→ Phase 1** (`/me`가 plan 반환)
- 🆕 **세탁 안내 = 에디터 자동 블록(규칙 기반)** — `Analysis.washCare` 필드와 `draftWashCare` API 제거. 분석 화면엔 이미 미렌더(`AnalysisForm.jsx`의 `draftWash`는 죽은 코드). 세탁 안내 내용은 **의류 종류별 대표 소재 프리셋 + 애매하면 기본 세탁방침**으로 M-02(page-assembler)가 생성(AI 카피 아님). 코드 정리: `draftWashCare`(mock api), `washCare`(types/Analysis), `draftWash`(AnalysisForm) 제거. **→ Phase 3** (washCare/draftWashCare 제거) · 자동블록 생성은 **Phase 4~6**
- 🆕 **`getMatchClothing` 최종 제거(과도기 함수)** — 현재 마네킹·콘티 화면이 실제 호출(`Mannequin.jsx:234`, `Storyboard.jsx:328`)하므로 지금 제거 불가. 매칭 후보가 `analyzeProduct` 응답(`analysis.matchCandidates`)에 완전히 포함되어 두 화면이 거기서 읽게 되면 제거. **→ Phase 3**
- 🆕 **`patchProject` 서버 화이트리스트** — 실서버 구현 시 `patchProject`는 `composeMode`·`copywriting`·`selectedMannequinId`만 수용. `adjustCount`·`status`는 서버 전용(요청 페이로드에 오면 무시/거부). mock의 무검증 `Object.assign`(`src/mock/api.js:65`)을 서버는 베끼지 않는다. **→ Phase 1** (projects CRUD)
- 🆕 **필수 칩 해제 불가 + `*` 표시** — 분석 기본 정보에서 `의류 종류`·`핏`은 해제 불가(데이터상 null 불가). 라벨에 포인트 색(`--ring`/Sky) `*` 표기. 해제 가능 칩(세부 카테고리·성별)과 구분. (`AnalysisForm.jsx`) **→ 독립 (순수 UI)**
- 🆕 **마네킹 성별 베이스 + 의류 스왑 구현** — 스파이크 결과 반영. 성별(`targetGenders`)이 베이스 마네킹(남/여 고정 1장)을 결정, A/B 후보 둘 다 같은 성별 베이스 위 스왑(독립 생성 아님). 베이스 자산은 운영자 시드. (`spike/base/*` 참고, AG-04) **→ Phase 4**
- 🆕 **마네킹 최초 생성 크레딧 예고** — 마네킹 페이지 진입 시 자동 생성·차감되므로, 분석 CTA 버튼 `의류정보 확정 완료`에 예상 크레딧을 부착(`의류정보 확정 완료 · 2 크레딧`). (`AnalysisForm.jsx:273`) **→ Phase 4**
- ✅ **스키마: `jobs.status` `cancelled` 제거 + `profiles.plan` basic+CHECK + `jobs.kind` CHECK** — `supabase/migrations/20260612090000_init.sql` 반영 완료(커밋 1e74490, 마이그레이션 **미적용** 상태라 적용 시 함께 반영). 코드 경로 중 `status='cancelled'` 설정 없음(검증됨).

---

## 2. 정책 미확정 (오픈 이슈)

> 구조는 준비됐고 정책 숫자/기준만 대기. 상세는 `backend_integration_plan.md` §11.

- 크레딧 단가·플랜·무료 체험·환불 — PRD §12.2 (00_README §4 Blocking).
- 품질 불만 환불 기준 — AG-P2(image-qc)와 연결.
- 자산 보존 기한·비용 상한 — 운영 정책.
- export 해상도·마켓 프리셋 — backend plan §7 P2 트리거.
- 분위기 예시 시드 입력 — 운영자 데이터(사용자: 직접 입력 예정).

---

## 3. 실행 로드맵

> 단계별 전환 순서·완료 기준은 `backend_integration_plan.md` §10(실행 로드맵)·§8(전환 단계)이 정본. 여기서 중복하지 않는다.
