# AI 파이프라인 명세 (ai_pipeline_spec.md)

> 상태: 확정 (2026-06-11, 갱신 2026-06-29) · 짝 문서: `documents/ai_agent_modules.md`(에이전트 정의), `documents/common_data_contract.md`(API 계약·크레딧·멱등 규약)
> 실행 주체: **FastAPI(Railway) job orchestration** (`documents/03_기술스택_결정서.md` §7). 프론트는 `lib/api` 함수만 호출하고, 파이프라인의 존재를 모른다.

---

## 1. 전제

- **호출 경로**: 프론트 → FastAPI → (Gemini/OpenAI). 키는 서버 전용(.env: `GEMINI_API_KEY`, `OPENAI_API_KEY`). 프론트 직접 호출 없음 (확정).
- **모델 라우팅**: 에이전트는 tier만 알고, tier→모델 매핑은 서버 설정 단일 파일 (모듈 정의서 §1 — 잠정 배정, 교체 용이성이 요구사항).
- **job 기반**: 장시간 작업은 job으로 실행하고 SSE 또는 폴링으로 진행률을 흘린다. 프론트 어댑터가 이를 기존 `onProgress(0..100)`/`onStep(steps)` 콜백으로 변환 — **화면 계약은 바뀌지 않는다** (계약 §6, 00_README §5).
- **크레딧 봉투**: 크레딧 소모 파이프라인의 응답은 `{ data, credits(잔액) }`. 차감은 서버 트랜잭션 **reserve-then-confirm**(시작 시 예약, 성공분만 확정, 실패 시 예약 해제=미차감 — backend plan §6). 환불 정책은 PRD §12.2에서 확정 예정.
- **멱등 4규칙** (계약 §6 '유료 job 멱등 규약' — mock의 `joinable`이 이미 구현한 의미를 서버가 승계):
  ① 진행 중 재호출 → 기존 job 합류(콜백 공유, 차감 1회) ② 완료 후 재호출 → 기존 산출물 + 현재 잔액 반환 ③ **실패(error)로 끝난 job 재호출 → 새 job 시작(재예약·재차감), 합류·재사용 안 함** ④ 다른 프로젝트의 job 결과는 폐기.

---

## 2. 파이프라인 인덱스

| ID | 트리거(화면 → API) | 에이전트/모듈 | 크레딧 | 출력 |
|---|---|---|---|---|
| PL-1 분석 | 입력 '입력 완료' → `analyzeProduct` | AG-01(설계) → M-01(라이브) | — | `Analysis` (실측 null) |
| PL-2 마네킹 생성 | 마네킹 최초 진입 → `generateMannequins` | AG-04 ×2 병렬 **(라이브)** | `mannequinGenerate` | `MannequinCut[]` |
| PL-3 마네킹 조정/재생성 | '의류 조정하기' → `adjustMannequin` / 재생성 모달 → `regenerateMannequins` | AG-05(설계) / AG-04(라이브) ×2 | `mannequinAdjust` / `mannequinGenerate` · adjustCount+1 | `MannequinCut` / `MannequinCut[]` |
| PL-4 상세페이지 생성 | 콘티 '이대로 생성하기' → `saveStoryboard` 후 `generateDetailPage` | AG-06(설계) ×N → AG-02(설계) → AG-03(설계) → M-02(mock) | `storyboardPerCut × source='ai' 블록 수` | `EditorBlock[]` |
| PL-5 에디터 새 컷 | AI 탭 '새 이미지 생성' → `generateImage(mode:'new')` | AG-06(설계) | `editorImage` | `WardrobeImage` |
| PL-6 에디터 컷 변형 | AI 탭 '비슷한 컷/변경 적용' → `generateImage(mode:'vary')` | AG-07(설계) | `editorImage` | `WardrobeImage` |

(세탁 안내는 파이프라인·AI 호출이 아니다 — 에디터 자동 블록을 M-02가 규칙 기반 프리셋으로 생성, ai_agent_modules §4.)

---

## 3. 파이프라인 상세

### PL-1 분석 — `analyzeProduct(projectId)`

```
입력 수집(product.name, 색상 그룹 이미지 R2 URL)
  → AG-01 product-analyst (1콜, 구조화 JSON)
  → 서버 후처리: measurements 강제 null · enum 검증
  → M-01 matching-recommender (AG-01의 styleTags 입력)
  → Analysis 조립(matchCandidates 후보 + matchSelections 기본 선택 포함) → 응답
```
- 진행률: 단일 job 0→100 (현 mock의 2.8s runJob 자리). 실패: throw, 화면 재시도 버튼.
- [P1 훅] AG-P1로 M-01 스왑 가능(동일 출력 shape).

### PL-2 마네킹 생성 — `generateMannequins(projectId)`

```
prep(기준 색상 이미지·분석 속성)
  → AG-04 ×2 병렬 (candidate A: baseFit=분석 fit 기준, candidate B: 대비 변주)
  → MannequinCut[] 저장 → { data, credits }
```
- 진행률 연출: PRD §7.2 단계 텍스트 + 95% 스톨 — 마지막 검수/조립 시간 표현.
- 부분 성공: 1개 후보만 성공 시 그대로 반환(실패 후보는 재생성 유도). 전체 실패: 미차감.
- 멱등: 후보가 이미 존재하면 재실행·재차감 없이 기존 반환 (계약 §6 ②).
- [P1 훅] AG-P2 image-qc 게이트 (후보별 출력 직후) — retry 시 correctionPrompt(실패원인+보완점)를 다음 시도 프롬프트에 주입 (ai_agent_modules §5).

### PL-3 마네킹 조정 — `adjustMannequin(projectId, req)`

```
req 검증(adjustCount < LIMITS.mannequinAdjustMax, enum만 허용)
  → AG-05 (base 컷 + fitAdjust/lengthAdjust/matchAdjust)
  → 새 버전 MannequinCut 저장(누적 조정 상태 기록) · adjustCount+1 → { data, credits }
```
- matchAdjust의 안 바뀐 차원은 base에서 누적 유지(mock과 동일 의미).
- `regenerateMannequins` = AG-04 재실행 + adjustCount+1 (PRD §7.5).
- [P1 훅] AG-P2 image-qc 게이트 (AG-05 조정 컷 · `regenerateMannequins`의 AG-04 재생성 후보 출력 직후, correctionPrompt 주입 재시도).

### PL-4 상세페이지 생성 — `generateDetailPage(projectId)` ★핵심

입력은 전부 서버 상태에서 읽는다: 저장된 `storyboard` + `project(copywriting, selectedMannequinId)` + `product`/`analysis`. 클라이언트가 들고 있는 값을 믿지 않는다 (frontend_state_model §5).

```
info     상품·분석·콘티 데이터 수집/검증 (비AI)
prep     블록별 프롬프트·에셋 준비, selectedMannequinId 컷 로드 (비AI)
styling  ┐
horizon  ├ AG-06 cut-generator — source='ai' 블록별 1콜.
product  ┘ cutType별 그룹으로 진행률 보고, 그룹 내 병렬(동시성 상한 서버 설정).
         source='mine' 블록은 ownImages 그대로(에이전트 호출 없음).
copy     copywriting=true면: 카피 대상 블록별 AG-02 → 묶음 AG-03 검수(revise 채택)
assemble M-02 page-assembler — 컷+카피+실측 → EditorBlock[] + 자동 블록 3종 (비AI)
done     project.status='done' · { data: EditorBlock[], credits }
```
- **진행률 매핑**: 위 7단계가 `genSteps` key(info/prep/styling/horizon/product/copy/assemble)와 1:1 — `onStep`은 단계 상태(idle/running/done), `onProgress`는 가중 합산.
- **크레딧**: `storyboardPerCut × ai 블록 수` (내 이미지 제외 — 계약 §6). 컷 단위 실패는 해당 컷 미차감.
- **부분 실패 정책**: 실패 컷은 빈 슬롯 블록으로 조립하고 응답에 표시 — 전체 job을 죽이지 않는다. 사용자는 에디터 의류 탭에서 재생성(PL-5).
- **멱등**: status='generating' 재호출 → 합류, status='done' 재호출 → 기존 결과 반환 (계약 §6).
- [P1 훅] AG-P2 image-qc 게이트 (styling/horizon/product 각 AG-06 컷 출력 직후, correctionPrompt 주입 재시도; 상한 초과 실패 컷은 부분 실패 정책으로 빈 슬롯).

### PL-5 / PL-6 에디터 단건 생성·변형 — `generateImage(projectId, req)`

```
PL-5 (mode:'new'):  NewCutRequest 검증 → AG-06 1콜 → WardrobeImage(colorId 그룹) → { data, credits }
PL-6 (mode:'vary'): VaryRequest 검증 → AG-07 1콜 → WardrobeImage('misc' 그룹) → { data, credits }
```
- 단건 job(수 초). 동시 다발 호출 허용 — 에디터 UI가 로딩 셀·busy 점으로 표현(기존 동작).
- 원본 이미지는 항상 보존, 결과는 의류 탭에 추가 (PRD §10.8).
- [P1 훅] AG-P2 image-qc 게이트 (AG-06/AG-07 출력 직후, correctionPrompt 주입 재시도).

---

## 4. job 모델

```ts
Job {
  id: string
  projectId: string
  kind: 'analyze' | 'mannequin' | 'mannequin_adjust' | 'detail_page' | 'editor_image'
  status: JobStatus            // idle | running | done | error
  progress: number             // 0~100
  steps?: GenStep[]            // detail_page만 (genSteps 매핑)
  result?: …                   // kind별 data
  creditsCharged: number
  createdAt / updatedAt: ISO
}
```
- **상태값**: 위 `status`(idle/running/done/error)는 **화면용 4값**이다. 서버 DB job row는 `pending`(대기열)·`running`·`done`·`error`를 가지며 `cancelled`는 없다(취소도 error로 — backend plan §2). 어댑터 매핑: `pending`→화면상 진행 중(running, progress 0), `running/done/error`→동일.
- project당 kind별 동시 1개 — 중복 시작 요청은 합류(멱등 ①). job 레코드가 이 규칙의 구현체다.
- 전달: SSE 우선, 폴리필로 폴링(GET /jobs/:id). 프론트 어댑터가 `onProgress`/`onStep`으로 변환 — `lib/api` 함수 시그니처 불변.
- 에러: `status='error'` + 한국어 message. 차감 전 실패 = 미차감, 차감 후 실패 보상(환불)은 PRD §12.2 확정 시 반영 훅. error로 끝난 job 재호출은 새 job 시작(멱등 ③).

---

## 5. 크레딧 트랜잭션 매핑

| 파이프라인 | 단가 키(`lib/limits.js`) | 차감 시점 | 실패 시 |
|---|---|---|---|
| PL-2 / PL-3 재생성 | `mannequinGenerate` | job 성공 확정 시 | 미차감 |
| PL-3 조정 | `mannequinAdjust` | 〃 | 미차감 |
| PL-4 | `storyboardPerCut × ai컷` | 컷 단위 성공분만 확정 | 실패 컷 미차감 |
| PL-5 / PL-6 | `editorImage` | job 성공 확정 시 | 미차감 |

- **reserve-then-confirm**(backend plan §6): 시작 tx에서 예상 최대 비용을 **예약**(available=balance−reserved 검증, 부족 시 402) → 성공 시 실제 성공분만 **확정**(balance 차감, ledger append) → 실패 시 예약 **해제**(미차감). 응답 `credits`(잔액=balance−reserved)로 프론트 `syncCredits` (계약 §6, frontend_state_model §6). 선검증만으로는 동시 job이 같은 잔액을 통과할 수 있어 예약이 필요.
- 단가는 임시값 — 정책 확정은 PRD §12.2 (00_README §4 Blocking 항목).

---

## 6. 환경·설정

```
# FastAPI 서버 전용 (.env / .env.local — 프론트 번들 금지)
GEMINI_API_KEY=
OPENAI_API_KEY=
MODEL_ROUTING_IMAGE_HIGH=gemini-3-pro-image        # 2026-06-12 공식 문서로 실재 확인(Nano Banana Pro, stable) — 교체는 여기서만
MODEL_ROUTING_IMAGE_LIGHT=gemini-3.1-flash-image
MODEL_ROUTING_TEXT=gemini-3.5-flash                 # 2026-07-02 결정 (Gemini 3 Flash GA) — 상세 pl1_analysis_agent_spec §2
PIPELINE_CUT_CONCURRENCY=3                          # PL-4 그룹 내 병렬 상한
```

---

## 7. 오픈 이슈

1. **모델 배정은 잠정** — tier별 비용·품질 로그(모듈 정의서 §6-5)를 근거로 재배정. 특히 제품컷·변형의 Pro 유지 여부.
2. **환불·재시도 정책 미정** (PRD §12.2) — 차감 후 실패 보상, 품질 불만 재생성 정책.
3. **이미지 동일성 검수** — QC Phase 1(Pillow 픽셀·고스트/크롭 휴리스틱)은 **라이브이나 SHADOW 모드**(게이트 미적용 — 실패해도 차단하지 않음). AG-P2 의미적 동일성 검수(이미지가 입력 상품과 같은 옷인지 판정)는 **설계만, 미구현** — 훅 위치(AG-04/05/06/07 출력 직후), retry 시 correctionPrompt 주입 루프(ai_agent_modules §5). 판정 기준·재시도 상한·크레딧 정책 설계 후 P1 투입.
4. **확장형(컬러별 컷) 콘티 구성** — getStoryboard의 composeMode별 블록 생성 규칙은 콘티 시드 단계 구현 필요(계약 §6 비고).
5. **분위기 예시 시드** — 운영자 데이터 입력 예정(에이전트 아님). 시드 스키마는 `MatchingItem` 패턴(구조적 에셋 경로)을 따른다.
