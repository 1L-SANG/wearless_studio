# AI 에이전트 모듈 정의서 (ai_agent_modules.md)

> 상태: 확정 (2026-06-11, 갱신 2026-06-14) · **모델 배정은 잠정** — §1 라우팅 테이블 한 곳만 바꾸면 전체에 반영되도록 설계한다 (사용자 결정). 모델·시스템 프롬프트는 이미지 품질 작업 단계에서 바뀔 수 있다.
> 근거: `documents/PRD.md`, `documents/common_data_contract.md`(엔티티·enum·API 계약), `documents/frontend_state_model.md`, `documents/03_기술스택_결정서.md`(FastAPI job orchestration), mock 프론트(`src/mock/*`, 특히 `matchingRecommendation.js`)
> 짝 문서: `documents/ai_pipeline_spec.md` (에이전트들이 언제·어떤 순서로 호출되는지)

---

## 1. 모델 라우팅 (단일 소스)

에이전트는 **모델명을 직접 갖지 않는다.** 각 에이전트는 `tier`만 선언하고, tier→모델 매핑은 아래 테이블(구현 시 서버 설정 파일 1개, `lib/limits.js`의 creditCosts와 같은 패턴)이 유일한 소스다. **모델 교체 = 이 테이블 한 줄 수정.**

| tier | 모델 (잠정) | 용도 기준 |
|---|---|---|
| `image_high` | **Gemini 3 Pro Image** (`gemini-3-pro-image`) | 최종 산출물에 들어가는 모든 이미지 — 의류 동일성·핏 재현이 핵심인 고작업 |
| `image_light` | **Gemini 3.1 Flash Image** | 미리보기·예시성 이미지. **현재 MVP 배정 에이전트 없음** — 분위기 예시는 운영자 시드 데이터로 대체(§5 참고). 저난도 생성 수요가 생기면 이 tier에 배정 |
| `text` | **GPT-5.4 mini** | 이미지 생성이 아닌 모든 작업 — 분석(비전 입력 포함)·카피·검수 |

**API 키 (.env — FastAPI 서버 전용, 추후 추가)**

```
GEMINI_API_KEY=   # image_high / image_light
OPENAI_API_KEY=   # text
```

**호출 경로 (확정)**: 프론트는 AI API를 직접 호출하지 않는다. 모든 에이전트 호출은 **처음부터 FastAPI 프록시 경유** — 키는 서버에만 존재하고, 프론트 번들(.env.local의 `VITE_*`)에 키를 넣지 않는다. 프론트 계약은 기존 `lib/api` 함수 그대로다.

---

## 2. 에이전트 카탈로그 (한눈 표)

| ID | 이름 | tier | 호출하는 API(계약 §6) | 크레딧 | MVP |
|---|---|---|---|---|---|
| AG-01 | product-analyst (상품 분석) | text | `analyzeProduct` | — | ✅ |
| AG-02 | copywriter (카피라이팅) | text | `generateDetailPage`(copy 단계) | — | ✅ |
| AG-03 | copy-qc (카피 검수) | text | `generateDetailPage`(copy 단계 직후) | — | ✅ |
| AG-04 | mannequin-generator (마네킹 생성) | image_high | `generateMannequins`, `regenerateMannequins` | mannequinGenerate | ✅ |
| AG-05 | mannequin-adjuster (마네킹 조정) | image_high | `adjustMannequin` | mannequinAdjust | ✅ |
| AG-06 | cut-generator (컷 생성) | image_high | `generateDetailPage`(컷 단계), `generateImage(mode:'new')` | storyboardPerCut / editorImage | ✅ |
| AG-07 | cut-variator (컷 변형) | image_high | `generateImage(mode:'vary')` | editorImage | ✅ |
| M-01 | matching-recommender (매칭 추천) | **비-AI** (룰베이스) | `analyzeProduct` 내부 | — | ✅ (구현 존재) |
| M-02 | page-assembler (상세페이지 조립) | **비-AI** (템플릿 엔진) | `generateDetailPage`(assemble 단계) | — | ✅ (mock 구현 존재) |
| AG-P1 | matching-ai-recommender | text | M-01 대체/보강 | — | P1 슬롯 |
| AG-P2 | image-qc (이미지 동일성 검수) | text | 이미지 생성 직후 게이트(AG-04/05/06/07) | — | P1 슬롯 |

공통 원칙: 입력·출력의 키와 enum 값은 전부 `common_data_contract.md` §4 토큰을 쓴다. 자유 텍스트(상품명·소재명·강조 특징·카피)는 한국어.

---

## 3. 에이전트 정의 (MVP)

### AG-01 product-analyst — 상품 분석

| | |
|---|---|
| tier | `text` (멀티모달 입력) |
| 호출 시점 | 입력 페이지 '입력 완료' → `analyzeProduct(projectId)` (PL-1) |
| 입력 | `{ name: string\|null, images: { colorGroupId, slot, url }[] }` — 기준 색상 전 각도 + 추가 색상 정면. 이미지는 R2 URL |
| 출력 | 분석 raw: `{ clothingType, subCategory, targetGenders, fit, materials[], aiSuggestedPoints(≤2), suggestedName, swatchSuggestions: { colorGroupId, swatchId }[], styleTags: string[] }` — 구조화 JSON 강제. (이 raw는 그대로 저장되는 게 아니라 서버가 분배 — 아래 후처리) |
| 후처리(서버 분배) | `clothingType`은 **Product에 기록**(Product 단일 소유 — 계약 §3.1, Analysis 아님). `subCategory`·`targetGenders`·`fit`·`materials`·`aiSuggestedPoints`·`suggestedName`은 **Analysis**(계약 §3.2). `swatchSuggestions`(색상 스와치 추천)·`styleTags`(M-01 입력)는 **저장 안 하는 중간 산출물**. `measurements`는 **절대 포함 금지**(서버가 null 강제 — PRD §6.5/§15.4) |
| 프롬프트 핵심 제약 | 실측 추정 금지 · 확신 없는 소재는 비우기 · subCategory/fit/swatchId는 계약 enum 안에서만 · 단일 에이전트 1콜(속성별 분리하지 않음 — 사용자 결정) |
| 실패 | throw(한국어 message) → 화면 재시도. 크레딧 없음 |

### AG-02 copywriter — 카피라이팅

| | |
|---|---|
| tier | `text` |
| 호출 시점 | PL-4 copy 단계: `project.copywriting=true`일 때 카피 대상 블록별. (세탁 안내는 AG-02 대상 아님 — 에디터 자동 블록을 M-02가 규칙 기반으로 생성, §4) |
| 입력 | `{ blockKind, cutType, product: { name, clothingType, fit, materials, measurementsKnown }, analysis: { sellingPoints, targetGenders, matchSelections }, colorLabel }` |
| 출력 | `{ texts: { role: 'headline'\|'body', text }[] }` — 블록당 1~3개 |
| 카피 방향 | PRD §11.3의 blockKind별 방향을 시스템 프롬프트로 고정 (후킹=감정·상황, 셀링=강조 특징 1~3개, 스타일링=착용 맥락, 호리존=핏·실루엣, 제품=디테일·소재) |
| 프롬프트 핵심 제약 | 사용자 확인 정보 우선 · 미확인 소재/세탁법/기능성 단정 금지 · 과장 효능 금지 (PRD §11.2) |
| 실패 | 해당 블록 카피 생략하고 진행(생성 전체를 실패시키지 않음) — 사용자가 에디터에서 직접 입력 가능 |

### AG-03 copy-qc — 카피 검수 (MVP 포함, 사용자 결정)

| | |
|---|---|
| tier | `text` |
| 호출 시점 | PL-4 copy 단계에서 AG-02 출력 직후, 블록 묶음 단위 1콜 |
| 입력 | `{ items: { blockId, text }[], confirmedFacts: { materials, sellingPoints, measurementsKnown } }` |
| 출력 | `{ results: { blockId, verdict: 'pass'\|'revise', revisedText?, reason? }[] }` — revise면 수정안을 그대로 채택 |
| 검출 대상 | 과장 효능 · 미확인 사실 단정(소재·기능성·세탁) · 확인 정보와 모순 |
| 실패 | 검수 실패 시 원문 채택 + 로그(검수는 게이트가 아니라 보정) |

### AG-04 mannequin-generator — 마네킹컷 생성

| | |
|---|---|
| tier | `image_high` |
| 호출 시점 | 마네킹 단계 최초 진입 `generateMannequins` / 전부 재생성 `regenerateMannequins` (PL-2/3) |
| 생성 방식 | **성별 베이스 + 의류 스왑** (스파이크 결정 2026-06-12). 맨바닥 독립 생성이 아니라, **분석에서 고른 성별(`targetGenders`)이 베이스 마네킹(남/여 각 고정 1장, 운영자 시드)을 결정**하고 그 위에 의류만 교체. A/B 후보는 **같은 성별 베이스** 위의 변주(독립 생성·성별 혼합 아님). |
| 입력 | `{ baseMannequinUrl: 성별 베이스 URL, productImages: 기준 색상 전 각도 URL[], clothingType, fit, candidate: 'A'\|'B', baseFit: Fit }` — A/B는 baseFit 등 변주를 다르게 줘 2안 |
| 출력 | `{ imageUrl }` → 서버가 `MannequinCut { id, candidate, version, src, baseFit, *Adjust:null }`로 포장 (계약 §3.3) |
| 프롬프트 핵심 제약 | **베이스 마네킹의 인물·포즈·구도·배경 동결, 의류만 교체** · 의류 구조·디테일·컬러 보존 최우선 · 모델 얼굴 없음 |
| 실패 | 후보 1개만 성공해도 결과 반환(부분 성공), 전체 실패 시 throw + 크레딧 미차감 |

### AG-05 mannequin-adjuster — 마네킹 조정

| | |
|---|---|
| tier | `image_high` |
| 호출 시점 | '의류 조정하기' → `adjustMannequin` (PL-3) |
| 입력 | `{ baseImageUrl, fitAdjust?: 'slimmer'\|'looser', lengthAdjust?: 'shorter'\|'longer', matchAdjust?: { item: MatchingItem, fitAdjust?, lengthAdjust? } }` — '현재'는 필드 생략 (계약 §6) |
| 출력 | `{ imageUrl }` → 새 버전 MannequinCut. 조정 상태는 서버가 누적 기록 |
| 프롬프트 핵심 제약 | 지시된 차원만 변경, 나머지(의류 디테일·구도) 동결 — 연속 조정의 시각적 일관성(PRD §17 R&D 인지) |

### AG-06 cut-generator — 컷 생성 (스타일링·호리존·제품)

| | |
|---|---|
| tier | `image_high` |
| 호출 시점 | ① PL-4: 저장된 콘티의 `source='ai'` 블록별 1콜 ② 에디터 '새 컷 추가' → `generateImage(mode:'new')` (PL-5) |
| 입력 | `{ cutType: 'styling'\|'horizon'\|'product', direction, shot, colorGroup: { swatchId, images: URL[] }, baseMannequinUrl(project.selectedMannequinId의 컷), modelId?, pose?, matchItems?: MatchingItem[], faceExposure, angle, refImages?: URL[] }` — cutType별 유효 옵션 셋은 계약 §4 (product는 ProductDirection/ProductShotType) |
| 출력 | `{ imageUrl, cutType }` → PL-4에선 블록 이미지, PL-5에선 `WardrobeImage { ai:true, cutType }` |
| 색상 변형 | 별도 에이전트 아님 — `colorGroup`이 추가 색상이면 같은 의류를 해당 스와치로 재현 (PRD §17 '색상별 동일 의류 재현' R&D 인지) |
| 프롬프트 핵심 제약 | 상품 동일성 보존 최우선 · 선택 마네킹컷의 핏·실루엣 기준 준수(PRD §7.1) · product 컷은 모델 없음(고스트/행거/플랫레이) · styling 컷은 matchItems 착장 반영 |
| 실패 | PL-4: 실패 블록은 빈 슬롯 블록으로 조립하고 결과에 표시(전체 중단 없음, 해당 컷 크레딧 미차감) · PL-5: throw + 미차감 |

> **구현 구조 (2026-06-20 결정)**: cutType별 프롬프트를 **독립 파일/모듈로 분리**(예 `prompts/cuts/product`·`horizon`·`styling`) — 한 컷을 작업할 때 다른 컷 프롬프트를 읽지 않는다. 입출력 계약·R2 입출력·재시도·로깅 등 **배관은 공통 1벌**(컷마다 복붙 금지). tier(모델)는 셋 다 `image_high` 공유 — **컷별 모델 분리는 보류**(저난도 컷에 `image_light`를 쓸 근거가 생기면 그때 §1 테이블에서 분기). 카탈로그를 `AG-06-product/horizon/styling` 3행으로 표기하는 건 선택일 뿐 **계약은 단일**. `styling` = 일상/룩북 컷(별도 '일상' cutType 신설 안 함 — 라벨만).
> **다양성은 AG-06의 책임이 아니다**: AG-06은 주어진 1개 spec(direction/shot/pose/angle)을 충실히 렌더할 뿐, 같은 cutType 컷들 간 변주는 **콘티(shot-list) 구성 단계**가 정한다 — §5 '컷 다양성' 참조.

### AG-07 cut-variator — 현재 컷 변형

| | |
|---|---|
| tier | `image_high` |
| 호출 시점 | 에디터 AI 탭 '현재 컷 변형' → `generateImage(mode:'vary')` (PL-6) |
| 입력 | VaryRequest(계약 §6): `{ source: { url, cutType }, changes: { type: 'direction'\|'shot'\|'pose'\|'face'\|'bg', value }[], refBg?: URL }` — `changes=[]`는 '비슷한 컷 만들기' |
| 출력 | `{ imageUrl, cutType }` → `WardrobeImage`(misc 그룹). 원본은 보존(PRD §10.8) |
| 적용 순서 계약 | 구도(direction·shot) 기준 → 포즈·표정 → 배경(refBg 포함)이 구도를 따라감 — EditorPanels의 칩 순서와 동일 |
| 프롬프트 핵심 제약 | 지시된 change 외 동결(인물·의류 동일성) · cutType 미상 소스는 styling으로 가정(기존 계약) |

---

## 4. 비-AI 모듈 (파이프라인 구성요소 — AI 호출 없음)

### M-01 matching-recommender — 매칭 의류 추천 (룰베이스, 구현 존재)

- **구현**: `src/mock/matchingRecommendation.js` → 백엔드 이관 시 동일 로직을 FastAPI로. 시드: `seedMatchingItems.js`(Supabase-ready `MatchingItem`).
- **입력**: `{ clothingType, targetGenders, styleTags }` — **styleTags는 AG-01이 산출**(사용자 결정: 룰베이스 + 분석이 태그 공급).
- **로직**: 보완 타입 필터(top계열→bottom) → 성별 필터(unisex 포함) → styleTags 겹침 점수 → sortOrder. 결정적·비용 0.
- **출력**: `MatchingItem[]` → `toLegacyMatchClothing`으로 UI shape 변환(상위 2개 메인/서브 기본 선택).
- **호출 시점**: PL-1에서 AG-01 직후 → `analysis.matchCandidates`(후보) + `matchSelections`(상위 2개 메인/서브 기본 선택)으로 응답에 포함 (계약 §3.2).

### M-02 page-assembler — 상세페이지 조립 (결정적 템플릿 엔진, 사용자 결정)

- **구현 기준**: mock의 `buildEditorBlocksFromStoryboard`(`src/mock/db.js`)가 이 모듈의 자리. 실서버도 같은 결정적 로직.
- **입력**: `{ storyboard: StoryboardBlock[], cutResults: { blockId, imageUrl }[], copyResults: { blockId, texts }[], product(실측 포함), copywriting }`.
- **로직**: blockKind별 레이아웃 템플릿으로 `EditorBlock[]` 배치(기준 폭 1000) + 자동 블록 3종(PRD §10.14). AI 호출 없음.
  - **사이즈 안내** = `product.measurements`.
  - **세탁 안내** = **규칙 기반 프리셋**(AI 아님): `clothingType`별 대표 소재(가장 많이 팔리는 소재)에 맞춘 세탁 문구를 미리 정해두고 선택, 소재가 애매하면 **기본 세탁방침**으로 폴백. 실제 케어라벨 확인 권장 문구 포함. (A1 결정 2026-06-14)
  - **AI 생성 안내** = AI 이미지 사용·차이 가능 고지.
- **출력**: `EditorBlock[]` (계약 §3.5).

---

## 5. P1+ 슬롯 (입출력 계약만 예약 — MVP 미구현)

- **AG-P1 matching-ai-recommender** (`text`): M-01 대체/보강. 입력 = M-01과 동일 + 상품 이미지. 출력 = `MatchingItem.id` 랭킹 + 사유. M-01과 같은 출력 shape를 유지해 스왑 가능하게.
- **AG-P2 image-qc — 이미지 동일성 검수 + 보정 지시** (`text`, 비전 입력): 생성 이미지가 입력 상품과 같은 옷인지(색·패턴·넥라인·디테일 변형 여부) 판정. 입력 = `{ productImages, generatedUrl, sourceAgent, genSpec }` — `genSpec`은 상위 에이전트의 생성 파라미터로 **에이전트별 형태가 다름**(AG-06/07=cutSpec, AG-04/05=마네킹 spec). 출력 = `{ verdict: 'pass'\|'retry', mismatches[], correctionPrompt?: string }`. **retry면 실패원인+보완점을 담은 `correctionPrompt`를 생성**해, 재생성 호출 시 **그 상위 에이전트의 원래 프롬프트에** 우선순위 보정 지시로 주입(주입 메커니즘은 에이전트 무관)(2026-06-20 결정). 훅 위치: AG-04/05/06/07 출력 직후 게이트(ai_pipeline_spec §3) — 마네킹·컷 공통 게이트라 입력을 cut 전용으로 가정하지 않는다. 재시도 상한·크레딧 정책은 PRD §12.2와 함께 확정.
  - **선례(메커니즘만)**: 스파이크(`spike/codex-phase4-mannequin-job-design.md` §5)에 **비-AI 싼 QC**(Pillow 크롭/프레이밍/고스트 휴리스틱)를 1차 게이트로 두고, `format_qc_feedback()`이 실패 사유를 다음 시도 프롬프트에 붙이는 **피드백 재시도 루프**가 설계돼 있다. AG-P2는 이 루프의 **의미(semantic) 단계**를 채운다(동일한 correctionPrompt 주입 메커니즘을 '같은 옷인가' 판정으로 확장).
    - ⚠️ **폐기 주의**: 스파이크의 *Flash 기본 → QC 실패 시 Pro 승격(4회 escalation)* tier 설계는 **현행 §1 라우팅에서 폐기**. 최종 이미지(AG-04/05/06/07)는 `image_high`(Pro) 직접 사용이고 `image_light`(Flash)는 MVP 미배정 — 재시도도 동일 tier(`image_high`)에서 correctionPrompt만 강화한다(별도 Flash→Pro 단계 없음). 재시도 상한은 PRD §12.2와 함께 확정.
- **컷 다양성 (shot-list 구성) — 책임 분리, 구현 방식 미확정**: 상세페이지는 같은 cutType 컷이 여러 장 들어가는데 전부 비슷한 구도/느낌이면 안 된다. **이 변주는 AG-06(렌더러)이 아니라 콘티(storyboard) 구성 단계의 책임** — 같은 cutType 블록들에 서로 다른 `(direction × shot × pose × angle)` spec을 배정해 변주를 만든다. 결정 대기: ⓐ **비-AI 룰 기반 shot-spread**(콘티 시드가 cutType별 옵션 매트릭스에서 충돌 없이 분산 — M-01/M-02 패턴, 결정적·비용0, **추천**) vs ⓑ **AI art-director 에이전트**(컷 묶음에 큐레이션된 다양 shot-list를 제안). ⓐ로 시작하고 기계적으로 느껴지면 ⓑ를 P1로. (ai_pipeline_spec §7.4 '콘티 구성' 오픈이슈와 연결)
- **분위기 예시는 에이전트가 아니다** — 콘티/에디터의 '생성예시'는 AI 모델·매칭 의류처럼 **운영자가 미리 넣는 시드 데이터**로 확정(사용자 결정). `image_light` tier는 이런 저난도 생성 수요가 실제로 생길 때 배정한다.

---

## 6. 공통 규약

1. **계약 우선**: 모든 입출력 키·enum은 `common_data_contract.md` §3~§4. 에이전트가 새 필드를 만들지 않는다.
2. **구조화 출력**: text tier는 JSON schema 강제(파싱 실패 = 실패로 처리, 1회 자동 재시도).
3. **이미지 자산**: 에이전트 입출력 이미지는 R2 URL(또는 asset id). 프론트 objectURL은 `uploadAsset` 경유 후 사용.
4. **금지 사항(전 에이전트 공통 시스템 규칙)**: 실측 추정 금지 · 미확인 정보 단정 금지 · 계약 enum 밖 값 금지.
5. **관측**: 에이전트 호출마다 `{ agentId, tier, model, projectId, jobId, latency, tokenOrImageCount }` 로깅 — 모델 배정이 잠정이므로 tier별 비용·품질 비교가 교체 판단 근거가 된다.
6. **멱등·크레딧**: 에이전트는 무상태. 멱등·차감·합류는 전부 파이프라인(job) 책임 — `ai_pipeline_spec.md` §4, 계약 §6.
