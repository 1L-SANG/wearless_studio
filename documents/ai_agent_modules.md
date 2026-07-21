# AI 에이전트 모듈 정의서 (ai_agent_modules.md)

> 상태: 확정 (2026-06-11, 갱신 2026-07-17) · **모델 배정은 잠정** — §1 라우팅 테이블 한 곳만 바꾸면 전체에 반영되도록 설계한다 (사용자 결정). 모델·시스템 프롬프트는 이미지 품질 작업 단계에서 바뀔 수 있다.
> 근거: `documents/PRD.md`, `documents/common_data_contract.md`(엔티티·enum·API 계약), `documents/frontend_state_model.md`, `documents/03_기술스택_결정서.md`(FastAPI job orchestration), mock 프론트(`src/mock/*`, 특히 `matchingRecommendation.js`)
> 짝 문서: `documents/ai_pipeline_spec.md` (에이전트들이 언제·어떤 순서로 호출되는지)

---

## 1. 모델 라우팅 (단일 소스)

에이전트는 **모델명을 직접 갖지 않는다.** 각 에이전트는 `tier`만 선언하고, tier→모델 매핑은 아래 테이블(구현 시 서버 설정 파일 1개, `lib/limits.js`의 creditCosts와 같은 패턴)이 유일한 소스다. **모델 교체 = 이 테이블 한 줄 수정.**

| tier | 모델 (잠정) | 용도 기준 |
|---|---|---|
| `image_high` | **Gemini 3 Pro Image** (`gemini-3-pro-image`) | 최종 산출물에 들어가는 모든 이미지 — 의류 동일성·핏 재현이 핵심인 고작업 |
| `image_light` | **Gemini 3.1 Flash Image** (`gemini-3.1-flash-image`) | 미리보기·예시성 이미지. **현재 MVP 배정 에이전트 없음** — 분위기 예시는 운영자 시드 데이터로 대체(§5 참고). 저난도 생성 수요가 생기면 이 tier에 배정 |
| `text` | **Gemini 3 Flash** (`gemini-3.5-flash`) | 이미지 생성이 아닌 모든 작업 — 분석(비전 입력 포함)·카피·검수. 2026-07-02 사용자 결정(구 GPT-5.4 mini 잠정 배정 대체) — 생성 파라미터는 `pl1_analysis_agent_spec.md` §2 |

**API 키 (.env — FastAPI 서버 전용, 추후 추가)**

```
GEMINI_API_KEY=   # image_high / image_light / text (전 tier Gemini — 2026-07-02)
OPENAI_API_KEY=   # 예비 — text tier를 OpenAI 계열로 재배정할 때만 필요
```

**호출 경로 (확정)**: 프론트는 AI API를 직접 호출하지 않는다. 모든 에이전트 호출은 **처음부터 FastAPI 프록시 경유** — 키는 서버에만 존재하고, 프론트 번들(.env.local의 `VITE_*`)에 키를 넣지 않는다. 프론트 계약은 기존 `lib/api` 함수 그대로다.

---

## 2. 에이전트 카탈로그 (한눈 표)

| ID | 이름 | tier | 호출하는 API(계약 §6) | 크레딧 | MVP |
|---|---|---|---|---|---|
| AG-01 | product-analyst (상품 분석) | text | `analyzeProduct` | — | ✅ (구현 — `kind='analyze'` job + GPT↔Gemini 폴백 + 프론트 연결) |
| AG-02 | copywriter (카피라이팅) | text | `generateDetailPage`(copy 단계) | — | ✅ (백엔드 라이브) |
| AG-03 | copy-qc (카피 검수) | text | `generateDetailPage`(copy 단계 직후) | — | ✅ (백엔드 라이브) |
| AG-04 | mannequin-generator (마네킹 생성) | image_high | `generateMannequins`, `regenerateMannequin({fitProfile})` | mannequinGenerate | ✅ (백엔드 라이브) |
| AG-05 | mannequin-adjuster (마네킹 조정) | image_high | ~~`adjustMannequin`~~ | ~~mannequinAdjust~~(0) | ⛔ 폐기 — fitProfile 재생성으로 통합 |
| AG-06 | cut-generator (사진 생성 — 내부 mirror 레시피 포함) | image_high | `generateDetailPage`(컷 단계), `generateImage(mode:'new')` | storyboardPerCut / editorImage | ✅ (백엔드 라이브 — detail_page_job·editor_image_job) |
| AG-07 | cut-variator (현재 이미지 수정) | image_high | `generateImage(mode:'vary')` | editorImage | ✅ (백엔드 라이브 — editor_image_job) |
| AG-08 | selling-point-extractor (강조 특징 발굴) | text | `analyzeProduct` 내부 — AG-01과 **병렬**(같은 job), 실패 시 AG-01 points 폴백 | — | ✅ (2026-07-13) |
| M-01 | matching-recommender (매칭 추천) | **비-AI** (룰베이스) | `analyzeProduct` 내부 | — | ✅ (백엔드 라이브) |
| M-02 | page-assembler (상세페이지 조립) | **비-AI** (템플릿 엔진) | `generateDetailPage`(assemble 단계) | — | ✅ (백엔드 라이브 + mock 미러) |
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
| 입력 | `{ name: string\|null, images }` — 기준 색상 전 각도(+추가 색상 정면). **이미지는 bytes(InlineImage)로 전달** — R2 서명 URL 만료·provider 발산 회피(구현: `analyze_job.py`가 `r2.get_bytes`→base64 inline, `mannequin_job.py:173`과 동일 경로). 프롬프트는 `prompts/product_analyst_v1.txt` 외부화 |
| 구현 | 코어 `product_analyst.analyze`(순수: 프롬프트→검증→분배) + `vision_llm.analyze_with_fallback`(httpx 직접, GPT=Structured Outputs strict / Gemini=responseSchema, 순차 폴백). production=`kind='analyze'` job(무과금·멱등, `dispatcher`+`run_analyze_job`). 라우트 `POST /projects/{id}/analyze`(202 jobId). spike=`POST /analyze:spike`(flag `ANALYSIS_SPIKE` 게이트, 임시 관측). 프론트=`httpAdapter.analyzeProduct`(job 폴링→onProgress). **모델 순서 기본=계약(GPT-first, `ANALYSIS_MODEL_ORDER`); Gemini-primary 전환은 spike 후 계약 개정으로 명시.** styleTags 산출로 **M-01 매칭의 producer 선결조건만 해소**(1a 실작동엔 flag·시드정리·enum정합 별도 필요) |
| 출력 | 분석 raw: `{ clothingType, subCategory, customCategory(자유 한국어 — enum 밖 의류 명칭, 2026-07-13), targetGenders, fit, materials[], aiSuggestedPoints(≤2), suggestedName, swatchSuggestions: { colorGroupId, swatchId }[], styleTags: string[] }` — 구조화 JSON 강제. (이 raw는 그대로 저장되는 게 아니라 서버가 분배 — 아래 후처리). `aiSuggestedPoints`는 **AG-08(전용 특징 에이전트, 병렬)** 결과가 있으면 그것으로 교체 — AG-01 값은 폴백. `materials`가 비면 서버가 카테고리 보편 조성(DEFAULT_MATERIALS, 팩트체크 2026-07-13)으로 채움. |
| 후처리(서버 분배) | `clothingType`은 **Product에 기록**(Product 단일 소유 — 계약 §3.1, Analysis 아님). `subCategory`·`customCategory`·`targetGenders`·`fit`·`materials`·`aiSuggestedPoints`·`suggestedName`은 **Analysis**(계약 §3.2). `swatchSuggestions`(색상 스와치 추천)·`styleTags`(M-01 입력)는 **저장 안 하는 중간 산출물**. `measurements`는 **절대 포함 금지**(서버가 null 강제 — PRD §6.5/§15.4) |
| 프롬프트 핵심 제약 | 실측 추정 금지 · 확신 없는 소재는 비우기 · subCategory/fit/swatchId는 계약 enum 안에서만 · 단일 에이전트 1콜(속성별 분리하지 않음 — 사용자 결정) |
| 실패 | throw(한국어 message) → 화면 재시도. 크레딧 없음 |

### AG-02 copywriter — 카피라이팅

| | |
|---|---|
| tier | `text` |
| 호출 시점 | PL-4 copy 단계: `project.copywriting=true`일 때 카피 대상 블록별. (세탁 안내는 AG-02 대상 아님 — 에디터 자동 블록을 M-02가 규칙 기반으로 생성, §4) |
| 입력 | `{ contentRole, sectionRole, cutType, product: { name, clothingType, measurementsKnown }, analysis: { fit, materials, sellingPoints, targetGenders, matchSelections }, colorLabel }`. `blockKind`는 예전 콘티를 읽는 동안의 폴백일 뿐 새 입력의 기준이 아니다. |
| 출력 | `{ texts: { role: 'headline'\|'body', text }[] }` — 블록당 1~3개 |
| 카피 방향 | PRD §11.3의 `contentRole`별 방향을 시스템 프롬프트로 고정한다. 첫 장면은 짧은 헤드라인, 나머지는 핵심 장점·코디·핏·실제 착용 느낌·제품 전체·확인된 디테일에 맞는 본문을 만든다. |
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

> **핏 반영(fidelity) P0 (2026-07-13)**: 입력 fitProfile은 **잡 생성 시점 payload 스냅샷**이 정본(워커의 analysis 재독 금지 — 경합·무음 유실 차단, legacy 잡만 폴백). 프롬프트는 정체성(색·패턴·소재·디테일)과 조형(핏·기장·실루엣)을 분리 — 선언 축은 "사진과 충돌할 때만" 우선하고, top/outer 기장 선언 시 untucked·밑단 완전 노출을 강제(매칭 하의 tuck 가림 방지). 셀러 조정 축은 CHANGES 섹션으로 재강조. 관측 = 호출 직전 `prompt_rendered` 이벤트(profile/prompt SHA-256, 원문 미포함). 정본: documents/mannequin_fit_fidelity_plan.md (P1=축 인지 QC는 부록 스키마).

| | |
|---|---|
| tier | `image_high` (Gemini 3 Pro 단일 티어 — Flash 프로모션 없음) |
| 호출 시점 | 마네킹 페이지 최초 진입 시 **자동** `generateMannequins` / 핏 확인 후 `regenerateMannequin(projectId, { fitProfile })` (PL-2/3) |
| 생성 방식 | **성별 베이스 + 의류 스왑** (스파이크 결정 2026-06-12). 맨바닥 독립 생성이 아니라, **분석에서 고른 성별(`targetGenders`)이 베이스 마네킹(남/여 각 고정 1장, 운영자 시드)을 결정**하고 그 위에 의류만 교체. **단일 후보 생성**(DB/API 호환을 위해 legacy `candidate='A'` 유지 — 구 A/B 2후보안 폐기). 핏은 `fitProfile`(축 선언 + `matchCut`)이 프롬프트 블록으로 주입되고, 선택된 매칭 하의 이미지가 있으면 함께 착장. |
| 입력 | `{ baseMannequinUrl: 성별 베이스 URL, productImages: 기준 색상 전 각도 URL[], matchImage?: 매칭 하의 URL, fitProfile }` — 매칭 하의 이미지가 없는 잡에선 `effective_fit_profile`이 `matchCut`을 제거(없는 옷 지시 방지, `server/app/agents/mannequin.py`) |
| 출력 | `{ imageUrl }` → 서버가 `MannequinCut { id, candidate:'A'(legacy), version, src }`로 포장 — 재생성 시 새 버전으로 스트립에 추가·자동 선택 |
| QC | Pillow 휴리스틱 QC + AG-P2 비전 QC 2중 — **현재 둘 다 shadow/off**(로그만, 게이팅 안 함 — `MANNEQUIN_QC_ENABLED=false`, `image_qc='off'`). 게이팅 활성 시 최대 2회 교정 재시도(`mannequin_max_attempts=2`) |
| 프롬프트 핵심 제약 | **베이스 마네킹의 인물·포즈·구도·배경 동결, 의류만 교체** · 의류 구조·디테일·컬러 보존 최우선 · 모델 얼굴 없음 |
| 실패 | 성공 시 잡당 `mannequinGenerate`(=2) 차감(예약량과 동일 — 구 "성공 후보 수 × 1" 폐기), 실패 시 미차감(예약 release). finalize는 lease-fenced 원자 처리 |

### AG-05 mannequin-adjuster — 마네킹 조정 ~~(폐기)~~

> **폐기 (2026-07)**: 마네킹 페이지의 slimmer/looser 조정 흐름이 **fitProfile 기반 재생성**(AG-04 `regenerateMannequin`)으로 통합되면서 페이지에서 더 이상 호출하지 않는다. 크레딧 항목 `mannequinAdjust`도 deprecated(비용 0). 서버 `:adjust` 라우트는 항상 **410 Gone** — 잡을 생성하지 않는다(단가 0 상태에서 잡 생성을 허용하면 무과금 AI 생성 경로가 되므로 차단). 워커(`mannequin_adjust_job`)는 **툼스톤** — 큐에 남은 legacy 잡을 **AI 호출 없이** 실패 종결(예약 크레딧 release)만 한다(생성 코드 제거, 같은 무과금 경로 차단). 프롬프트 헬퍼(`mannequin_adjuster.py`)는 AG-07이 재사용해 파일만 잔존 — 아래 표는 레거시 기록.

| | |
|---|---|
| tier | `image_high` |
| 호출 시점 | (구) '의류 조정하기' → `adjustMannequin` (PL-3) |
| 입력 | `{ baseImageUrl, fitAdjust?: 'slimmer'\|'looser', lengthAdjust?: 'shorter'\|'longer', matchAdjust?: { item: MatchingItem, fitAdjust?, lengthAdjust? } }` — '현재'는 필드 생략 (계약 §6) |
| 출력 | `{ imageUrl }` → 새 버전 MannequinCut. 조정 상태는 서버가 누적 기록 |
| 프롬프트 핵심 제약 | 지시된 차원만 변경, 나머지(의류 디테일·구도) 동결 — 연속 조정의 시각적 일관성(PRD §17 R&D 인지) |

### AG-06 cut-generator — 이미지 생성 (내부 레시피: styling·horizon·product·mirror)

`cutType`은 페이지를 나누는 콘티 분류가 아니라 이미지 생성 레시피다. 콘티 카드 인스펙터에서 사용자가 섹션별 허용 컷 종류를 직접 고르면, 앞 단계가 그 `cutType`을 저장하고 내부 `contentRole`을 자동으로 맞춘다. AG-06은 선택된 레시피를 렌더링하며, 생성예시는 현재 `cutType` 안에서 가능한 구도와 분위기를 보탠다(ADR-0005).

| | |
|---|---|
| tier | `image_high` |
| 호출 시점 | ① PL-4: 저장된 콘티의 `source='ai'` 블록별 1콜 ② 에디터 `새 이미지 추가` → `generateImage(mode:'new')` (PL-5) |
| 입력 | `{ contentRole, sectionRole?, cutType, direction, shot, outerClosureState?, colorGroup: { swatchId, images: URL[] }, baseMannequinUrl(project.selectedMannequinId의 컷), modelId?, pose?, matchItems?: MatchingItem[], faceExposure, angle, refImages?: URL[] }`. `cutType`은 사용자가 고른 생성 레시피의 정본이며, 서버는 섹션별 허용 컷과 세부 옵션을 검증한다. `contentRole`은 핵심 장점의 순서, 핏·코디의 `cutType`, 제품 확인의 `shot`에서 파생한 내부값으로 일치시킨다. 유효한 사용자 컷 선택을 예전 역할 기본값으로 덮어쓰지 않는다. **mirror 계약(ADR-0004·0007)**: `direction=null` · `shot`은 full/medium만 · `faceExposure`는 hide(기본, 폰으로 가림)/show만 · `pose='auto'` 고정. 유효한 `cutType`을 해석할 수 없을 때만 **에러**(`unknown_cut_type`)다. `product+detail`은 상품 전체 중 실제 `Detail` 입력 이미지가 생성 입력에 포함돼야 한다. 목표 색상에 없으면 기준색, 그다음 Detail 보유 첫 색상의 구조·재질을 근거로 쓰고 색만 목표 색상군으로 전환하며, 전 색상에 없으면 `detail_reference_required`로 해당 사진을 실패시킨다. |
| 출력 | `{ imageUrl, cutType }` → PL-4에선 블록 이미지, PL-5에선 `WardrobeImage { ai:true, cutType }` |
| 색상 변형 | 별도 에이전트 아님 — `colorGroup`이 추가 색상이면 같은 의류를 해당 스와치로 재현 (PRD §17 '색상별 동일 의류 재현' R&D 인지) |
| 프롬프트 핵심 제약 | 상품 동일성 보존 최우선 · 선택 마네킹컷의 핏·실루엣 기준 준수(PRD §7.1) · product는 모델 없음(고스트/플랫레이/디테일) · 디테일은 입력 근거 밖의 원단·안감·부자재 생성 금지 · styling·mirror는 matchItems 착장 반영 · mirror는 캐주얼 거울 셀피 구도(스튜디오 연출 아님) |
| 실패 | PL-4: 일부 실패는 빈 슬롯으로 조립하고 성공 컷만 과금. AI 컷 전부 실패는 `all_cuts_failed`로 작업 실패·예약 해제 · PL-5: throw + 미차감 |

> **구현 구조 (2026-06-20 결정 → 2026-07 개정)**: 프롬프트는 **단일 섹션 템플릿** `server/prompts/cut_generate_v1.txt`(`[[CUT:styling|horizon|product|mirror]]` 섹션)로 통합 — 구 `prompts/cuts/*` 컷별 파일은 삭제됐다. 컷별 계약 정규화·옵션 검증은 `server/app/agents/cut_generator.py`가 담당. 입출력 계약·R2 입출력·재시도·로깅 등 **배관은 공통 1벌**(컷마다 복붙 금지). tier(모델)는 전 컷 `image_high` 공유 — **컷별 모델 분리는 보류**(저난도 컷에 `image_light`를 쓸 근거가 생기면 그때 §1 테이블에서 분기). `styling` = 일상/룩북 컷(별도 '일상' cutType 신설 안 함 — 라벨만). 무드/공간 예시 뉘앙스(EXNUANCE)는 정면 계열(front·mirror)에만 적용, 측면/후면은 무드만(밴드 규칙).
> **다양성은 AG-06의 책임이 아니다**: AG-06은 주어진 1개 spec(direction/shot/pose/angle)을 충실히 렌더할 뿐, 같은 사진 목적 안의 구도 변주는 **콘티(shot-list) 구성 단계**가 정한다 — §5 '컷 다양성' 참조.
> **가상모델 아이덴티티 레퍼런스 계약 (2026-07-14 개정 — C방식 확정, 구 2026-07-10 '정면 1장' 계약 대체)**: 사람컷(styling·horizon·mirror)에서 `modelId`가 지정되면 해당 가상모델의 **face_front 원본 베이스컷 1장 + 세드카드 그리드(2x2 멀티앵글, 자르지 않은 통짜) 1장**을 첨부한다 — shot·표정·포즈 무관 동일 규칙, product 컷은 첨부 없음. 근거 = v3 매트릭스(5조합×4포즈×3모델=60컷) + C 스트레스(표정3·비정형포즈4×2모델=16컷): ① 원본 1장 단독은 **버즈컷 표본 착시** — 헤어 있는 모델(m1·w1)에서 컷마다 헤어스타일이 변해 컷 간 일관성 실패 ② 그리드가 각도·헤어 정보를 공급해 헤어 고정 + 질감(주근깨) 최고 보존 ③ 표정 변화·착석·뒷모습(그리드 사각지대)까지 16/16 아이덴티티 유지 ④ 그리드 레이아웃이 출력에 새어나온 사례 0/28. **원본이 얼굴 질감의 정본, 그리드는 각도·헤어의 정본** — 시트 낱장 크롭을 기본 경로에 쓰지 않는다(2차 생성물 열화 전파 금지 원칙은 유지하되, 그리드는 질감보존 강화 v2 팩 산출물만 사용).
> - **첨부 순서·매니페스트**: `images = [mannequin?, model_face?, model_sheet?, *prod(slot순), match?, *mood]` — MODEL 2장은 마네킹 다음. 고정 라벨(셀러 데이터 미포함, 실험 검증 문구 — 빼지 말 것):
>   - model_face: `MODEL — frontal close-up of the model (identity ground truth; do NOT copy this image's pose, framing, or clothing)`
>   - model_sheet: `MODEL SHEET — a 2x2 grid of four studio portraits of the SAME single person (identity reference only). Do NOT copy the grid layout, framing, poses, or clothing; the output must be one single normal photograph, never a grid`
> - **모델 자산(R2 시드)**: face_front=원본 무가공(webp), grid_sedcard=v2 팩 그리드 리샘플(max 1536px), 시트 낱장 4종(three_quarter/profile/body_front/body_back)=QC 폴백·미래 용도 보관. manifest=`server/app/data/virtual_models.json`.
> - **조건부 폴백(P1, QC 게이트 전제)**: QC(AG-P2/ArcFace 유사도) 실패 컷에 한해 시트 `body_front` 1장 추가 재시도(저비용 보험).
> - **백로그**: 시선(gaze) 지시 순종 약함 — "카메라 밖 응시" 표정은 프롬프트 보강 필요(스트레스 테스트에서 2/2 무시, 아이덴티티는 유지).

### AG-07 cut-variator — 현재 이미지 수정

| | |
|---|---|
| tier | `image_high` |
| 호출 시점 | 에디터 AI 탭 `현재 이미지 수정` → `generateImage(mode:'vary')` (PL-6) |
| 입력 | VaryRequest(계약 §6): `{ source: { src, cutType }, changes: { type: 'direction'\|'shot'\|'pose'\|'face'\|'bg', value }[], refBg?: URL }` — `changes=[]`는 '비슷한 컷 만들기'. 디테일 전환은 실제 Detail 근거를 연결할 수 없어 `detail_variation_unsupported`로 차단한다. |
| 출력 | `{ imageUrl, cutType }` → `WardrobeImage`(misc 그룹). 원본은 보존(PRD §10.8) |
| 적용 순서 계약 | 구도(direction·shot) 기준 → 포즈·표정 → 배경(refBg 포함)이 구도를 따라감 — EditorPanels의 칩 순서와 동일 |
| 프롬프트 핵심 제약 | 지시된 change 외 동결(인물·의류 동일성) · cutType 미상 소스는 styling으로 가정(기존 계약) |

---

## 4. 비-AI 모듈 (파이프라인 구성요소 — AI 호출 없음)

### M-01 matching-recommender — 매칭 의류 추천 (룰베이스, 백엔드 라이브)

- **구현**: FastAPI 라이브 (`server/app/services/matching.py`). 엔드포인트: `GET /projects/{id}/analysis/match-candidates?clothingType=&gender=&limit=`. 시드 64개 (`server/seed/matching_items.json`, `matching_items` 테이블 + R2).
- **입력**: `clothingType`, `gender`(단수), `limit?` — HTTP 쿼리 파라미터. styleTags 입력 미사용(현행 알고리즘에서 제거됨).
- **로직**: 보완 타입 필터(top/outer/dress→bottom, 나머지→top) → 성별 필터(is_active + type + gender, unisex 항상 포함) → `-color_brightness` 내림차순 후 sort_order. 결정적·비용 0.
- **출력**: `MatchingItem[]` → `analysis.matchCandidates`(후보) + `matchSelections`(상위 2개 메인/서브 기본 선택).
- **호출 시점**: PL-1에서 AG-01 직후 → `analysis.matchCandidates`(후보) + `matchSelections`(상위 2개 메인/서브 기본 선택)으로 응답에 포함 (계약 §3.2).

### M-02 page-assembler — 상세페이지 조립 (결정적 템플릿 엔진, 사용자 결정)

- **구현 기준**: 실서버 `server/app/agents/page_assembler.py`가 기준 구현이며, mock의 `buildEditorBlocksFromStoryboard`(`src/mock/db.js`)가 같은 결과 구조를 흉내 낸다.
- **입력**: `{ storyboard: StoryboardBlock[], cutResults: { blockId, imageUrl }[], copyResults: { blockId, texts }[], product(실측 포함), copywriting }`.
- **로직**: `sectionRole` 순서와 `contentRole`별 레이아웃·카피 위치로 `EditorBlock[]`을 배치한다(기준 폭 1000). 구 `blockKind`는 오래된 데이터의 폴백으로만 읽는다. AI 호출 없음.
  - **사진 섹션** = 핵심 장점(`benefit`) → 핏·코디(`fit`) → 제품 확인(`product`). 같은 장소는 별도 섹션을 만들지 않는다.
  - **구매 정보** = 콘티 밖에서 에디터 블록으로 구성한다(PRD §10.14). 현재 라이브 조립기는 사이즈·세탁·AI 안내 자동 블록 3종까지 만든다. 소재·옵션·배송·교환·필수 고지 등을 `kind='info'`와 `infoType` 일반 블록으로 만들고, 빈 필수값을 `정보 입력 필요`로 보여주는 부분은 `TODO.md`의 에디터 구매 정보 항목에서 추적한다.
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
- **사진 다양성 (shot-list 구성) — 책임 분리, 구현 방식 미확정**: 같은 내부 `contentRole`과 사용자가 고른 `cutType`의 사진이 여러 장일 때 전부 비슷한 구도와 느낌이면 안 된다. **이 변주는 AG-06이 아니라 콘티 구성 단계의 책임**이다. 콘티가 선택된 레시피 안에서 서로 다른 `(direction × shot × pose × angle)`을 배정한다. 결정 대기: ⓐ 비-AI 룰 기반 분산(결정적·비용 0, 추천) vs ⓑ AI art-director가 묶음 전체를 큐레이션. ⓐ로 시작하고 결과가 기계적으로 느껴질 때 ⓑ를 P1로 검토한다.
- **분위기 예시는 에이전트가 아니다** — 콘티/에디터의 '생성예시'는 AI 모델·매칭 의류처럼 **운영자가 미리 넣는 시드 데이터**로 확정(사용자 결정). `image_light` tier는 이런 저난도 생성 수요가 실제로 생길 때 배정한다.

---

## 6. 공통 규약

1. **계약 우선**: 모든 입출력 키·enum은 `common_data_contract.md` §3~§4. 에이전트가 새 필드를 만들지 않는다.
2. **구조화 출력**: text tier는 JSON schema 강제(파싱 실패 = 실패로 처리, 1회 자동 재시도).
3. **이미지 자산**: 에이전트 입출력 이미지는 R2 URL(또는 asset id). 프론트 objectURL은 `uploadAsset` 경유 후 사용.
4. **금지 사항(전 에이전트 공통 시스템 규칙)**: 실측 추정 금지 · 미확인 정보 단정 금지 · 계약 enum 밖 값 금지.
5. **관측**: 에이전트 호출마다 `{ agentId, tier, model, projectId, jobId, latency, tokenOrImageCount }` 로깅 — 모델 배정이 잠정이므로 tier별 비용·품질 비교가 교체 판단 근거가 된다.
6. **멱등·크레딧**: 에이전트는 무상태. 멱등·차감·합류는 전부 파이프라인(job) 책임 — `ai_pipeline_spec.md` §4, 계약 §6.
