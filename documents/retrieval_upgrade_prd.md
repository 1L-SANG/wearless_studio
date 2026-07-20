# 검색 증강(RAG) 파운데이션 AI 업그레이드 — PRD

> 상태: **초안 (pending approval)** — 2026-07-04 작성. 실행 미승인.
> 짝 문서: `.omc/plans/rag-vectordb-foundation-ai-upgrade.md`(작업 계획·ADR), `documents/ai_pipeline_spec.md`, `documents/ai_agent_modules.md`, `documents/common_data_contract.md`, `documents/03_기술스택_결정서.md`
> 반영: 인터뷰 결정(3목표 채택·단계적·기존 스택) + Architect 검토(sound-with-changes, F1~F6) 합성안. Critic 검증 결과는 도착 시 증분 반영.

---

## 1. 배경과 문제 정의

### 1.1 현재 상태

Wearless Studio의 파운데이션 AI는 **Gemini 이미지 생성 모델**이다 (`image_high`=`gemini-3-pro-image` — `server/app/config.py:30`, 호출부 `server/app/agents/gemini_image.py:90`). 텍스트 LLM 챗봇이 아니므로, 여기서 "RAG"는 답변 생성이 아니라 **검색으로 생성 입력(프롬프트 텍스트·레퍼런스 이미지)을 보강**하는 것을 뜻한다.

현재 생성 입력을 구성하는 지식은 3종이며 각각 한계가 있다:

| 지식 | 현재 구현 | 한계 |
|---|---|---|
| 소재 렌더링 가이드 | `server/app/agents/materials.py` — canonical 키 결정적 lookup (~30블록) | 한계 아님 — 소규모 구조적 도메인에선 **이 방식이 최적**. 유지한다 (§3 비목표) |
| 매칭 의류 추천 | `server/app/services/matching.py:14` — 보완타입·성별 필터 + 밝기 정렬 | 스타일 조화 신호 없음. AG-01이 뱉는 `styleTags`가 **버려지고 있음** (`documents/ai_agent_modules.md:142`) |
| 스타일링·구도·브랜드 지식 | 없음 (프롬프트 템플릿 고정문) | 카테고리·스타일별 생성 품질 편차. 지식 확장 경로 부재 |
| 레퍼런스 이미지 | 없음 (AG-06 계약에 `refImages?: URL[]` 예약만 — `documents/ai_agent_modules.md:115`) | 컷 간 스타일 일관성을 잡아줄 시각 기준 부재 |

### 1.2 사용자가 원하는 것

**이미지 품질·일관성 보장** (1차 기준). 이를 위해 3개 역량:

1. **프롬프트 지식 검색** — 상품 속성에 맞는 스타일링/구도/브랜드 지식을 찾아 프롬프트에 주입
2. **레퍼런스 이미지 검색** — 유사·조화 이미지를 찾아 Gemini 멀티모달 입력으로 제공
3. **매칭 의류 지능화** — 규칙(밝기 정렬)을 스타일 조화 랭킹으로 업그레이드

### 1.3 설계 원칙 (검토로 확정)

1. **결정성 우선(deterministic-first)** — 같은 입력 → 같은 프롬프트 → 재현 가능한 품질. 이 코드베이스의 기존 철학(`materials.py:4-8` Reference-first·no-ratio-math, `ai_pipeline_spec.md` 멱등 규약)과 정합. 벡터 검색은 결정적 baseline을 **평가에서 이길 때만** 기본 활성.
2. **벡터는 이길 수 있는 곳에만** — n=64 카탈로그·수백 KB 문서엔 결정적 방법이 대체로 우월. 벡터가 무조건 정당한 곳은 **레퍼런스 이미지 검색**(성장하는 코퍼스, 결정적 대안 없음)뿐.
3. **기존 스택 내** — pgvector(Supabase Postgres), 신규 인프라 0. "점진 도입" 철학(`03_기술스택_결정서.md`) 준수.
4. **계약 불변** — 예약 슬롯(AG-P1, AG-06 `refImages`)만 채운다. 새 계약 필드 발명 금지 (`ai_agent_modules.md` §6.1).
5. **인젝션 표면 동결** — 주입 코퍼스는 운영자 큐레이션만. 셀러 자유텍스트는 검색 쿼리로만 쓰고 주입 콘텐츠로 금지 (`materials.py:10-13` 보안 원칙 승계).

---

## 2. 목표 / 성공 지표

### 2.1 목표 (Goals)

| # | 목표 | 측정 |
|---|---|---|
| G1 | 매칭 추천에 스타일 조화 신호 도입 | 평가셋 매칭 적합도: 신규 ≥ 현행(밝기 정렬). 보완성 회귀 0건 |
| G2 | 카테고리·스타일별 프롬프트 지식 주입 경로 확보 | 지식 주입 on/off 비교에서 품질 루브릭 점수 개선 |
| G3 | AG-06 `refImages` 채움 파이프라인 가동 | 레퍼런스 on/off 비교에서 컷 간 일관성 루브릭 점수 개선 |
| G4 | 품질·일관성의 **측정 체계** 확립 | 루브릭 채점 리포트가 각 Phase 활성/보류 판단 근거로 기능 |

### 2.2 비목표 (Non-Goals)

- **텍스트 Q&A 챗봇** — 범위 외 (사용자 인터뷰에서 제외 확정).
- **`materials.py` 소재 가이드의 벡터화** — 결정적 lookup 유지. 스냅샷 회귀 테스트로 불변 보장.
- **임베딩·벡터 전면 (2026-07-04 최종 결정)** — 3대 목적에 임베딩 필수인 게 없음(§1.2·ADR D2). 채택 = 결정적 스택만(1a 태그 매칭 + 2a 정적 지식 + Track D). **보류**: Phase 1b·2b·3, embeddings.py 이미지부, torch, Vertex, pgvector 벡터 컬럼. 재진입 = 의미적 이미지 유사도(레퍼런스·아웃라이어)가 결정적 코어로 불충분 판명 시.
- **전용 벡터 DB (Qdrant/Pinecone)** — 현 규모 과함. `ref_images`가 1만 행을 넘는 확장 신호가 올 때 재검토.
- **셀러 자유텍스트의 코퍼스 편입** — 인젝션 안전 원칙 위배.
- **ANN 인덱스 선제 도입** — n<1만에선 exact scan이 더 빠르고 recall 100%. `ref_images`가 ~1만 행을 넘을 때만 hnsw 도입.

### 2.3 성공 지표 (요약)

- 품질 루브릭(§7) 점수: 각 역량 on vs off, **on ≥ off** 이어야 기본 활성 (챌린저는 baseline을 **이겨야** 승격).
- `GET /projects/{id}/analysis/match-candidates` p95 **현행 대비 +50ms 이내** (동기 경로 — §5.1 F1 반영).
- 생성 경로(PL-2/4/5/6) 검색 추가 지연 p95 < 500ms.
- 기존 테스트 스위트(`server/tests/`) 그린 유지. 소재 가이드 출력 스냅샷 불변.

---

## 3. 사용자 시나리오

- **S1 (매칭)**: 셀러가 화이트 니트(top) 분석 완료 → 매칭 후보에 "어울리는 하의"가 스타일 조화 순으로 정렬돼 나온다. 니트-와이드슬랙스처럼 태그 친화도 높은 조합이 상단. 상의는 절대 안 나옴(보완성 유지).
- **S2 (지식)**: 셀러 상품이 "발마칸 코트"면, 콘티→생성 시 코트류 촬영 구도·실루엣 표현 지식 블록이 프롬프트에 붙어 컷 품질이 카테고리 무관하게 고르다.
- **S3 (레퍼런스)**: 코디 활용 사진을 만들 때 내부 `styling` 레시피에 같은 무드의 `refImages`가 들어가 4컷의 톤·무드가 한 페이지처럼 읽힌다. `styling`은 사용자에게 보이는 섹션명이 아니다.
- **S4 (운영자)**: 운영자가 지식 블록·레퍼런스 시드를 추가하면 배치 임베딩 스크립트 1회 실행으로 반영된다. 실패해도 재실행 안전(멱등).

---

## 4. 요구사항

### 4.1 FR-A 매칭 의류 지능화 (Phase 1)

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR-A1 | **v1 = 결정적 styleTags 친화도 랭킹.** AG-01의 `styleTags`(현재 폐기 — `ai_agent_modules.md:142`)를 되살려, `matching_items.style_tags`(스키마 기존 존재 — `supabase/migrations/20260612090000_init.sql:161`)와의 친화도 점수로 후보 랭킹. 친화도 맵은 운영자 큐레이션 정적 테이블(64개 닫힌 카탈로그) | P0 |
| FR-A2 | 보완타입·성별 하드 프리필터는 **불변** (`matching.py:17-22` 로직 승계). 벡터든 태그든 랭킹은 프리필터 통과 풀 내부에서만 | P0 |
| FR-A3 | 출력 shape = 현행 `recommend()`와 동일(`MatchingItem[]`) — 라우트 `server/app/routes.py:454-484` 계약 무변경 | P0 |
| FR-A4 | ~~v2 벡터 챌린저~~ **보류 (임베딩 0 결정, 2026-07-04)** — 태그 친화도 v1로 충분 판단. 재진입 시 챌린저 규율 유지 | 보류 |
| FR-A5 | (v2 보류로 무효) 참고 보존: 벡터 재진입 시 동기 GET(`routes.py:484`) 요청 중 임베딩 금지·사전계산·p95+50ms | 보류 |
| FR-A6 | flag로 현행(밝기)↔v1(태그)↔v2(벡터) 3단 전환·즉시 롤백 | P0 |

### 4.2 FR-B 프롬프트 지식 검색 (Phase 2)

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR-B1 | **v1 = 정적 지식 블록.** 카테고리·styleTags 키 기반 결정적 선택(`materials.py` 패턴의 확장 도메인 판). 운영자 큐레이션, 영문 canonical 본문 | P0 |
| FR-B2 | 코퍼스 범위: 스타일링/구도/브랜드/룩북 지식. **소재 본문 제외** — `materials.py`가 정본, 중복 금지 | P0 |
| FR-B3 | 주입 위치: `prompts.py:_product_block`(`server/app/agents/prompts.py:45`) 뒤 별도 블록. ground-truth 블록·소재 가이드와 구분되는 섹션 헤더. `_sanitize` 동급 가드 | P0 |
| FR-B4 | ~~v2 kb 벡터 검색 챌린저~~ **보류 (임베딩 0 결정)** — 정적 키 선택 v1로 충분. 재진입 조건은 ADR D2 | 보류 |
| FR-B5 | **프롬프트 버전 고정.** 주입된 지식 블록의 id·버전을 job 레코드에 기록 — 코퍼스 개정이 완료된 job의 멱등 재사용(`ai_pipeline_spec.md` §멱등 ②)과 충돌하지 않게. 재임베딩·코퍼스 수정은 **새 job에만** 반영 | P0 |
| FR-B6 | 소재 가이드 출력 스냅샷 회귀 테스트 — 30블록 불변 | P0 |

### 4.3 FR-C 레퍼런스 이미지 검색 (Phase 3)

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR-C1 | 코퍼스 = 운영자 시드 레퍼런스(분위기 예시 시드 패턴 — `ai_pipeline_spec.md:160`)부터 시작. 과거 성공 생성물 편입은 성공 신호(§9-2) 정의 후 | P0 |
| FR-C2 | 이미지 멀티모달 임베딩 **오프라인 사전 적재** (`ref_images` 테이블). 요청 중 코퍼스 임베딩 금지 | P0 |
| FR-C3 | AG-06 입력 `refImages?: URL[]`(`ai_agent_modules.md:115`)에 top-k(설정값, 기본 2) 채움 — 계약·크레딧 무변경 | P0 |
| FR-C4 | 검색 실패·빈 결과 시 refImages 생략하고 생성 진행 (검색은 best-effort, 생성 차단 금지) | P0 |
| FR-C5 | `ref_images` 행수 ~1만 초과 시에만 hnsw 인덱스 도입. 그 전엔 exact scan | P1 |

### 4.4 FR-D 방어·QC 트랙 (일관성·인젝션·엣지케이스 — 2026-07-04 사용자 승인으로 추가)

배경: 셀러 자유텍스트가 프롬프트에 직행하는 경로가 라이브다 — 분석 화면 "강조하고 싶은 특징" 칩(`src/features/analysis/AnalysisForm.jsx:214-231`, 자유 입력)·소재명(`:155`)·상품명 → `prompts.py:68` → "Key features". `_sanitize`(`prompts.py:20`)는 개행·제어문자·200자만 차단하고 **의미적 조종은 통과**하며, 템플릿이 "Key features를 명확히 표현하라"(`server/prompts/mannequin_generate_v1.txt:17`)고 증폭한다. 또한 Pillow QC는 SHADOW 모드(`config.py` `mannequin_qc_enabled=False`)라 불합격도 무조건 채택된다(`mannequin_job.py:104,116`).

| ID | 요구사항 | 우선순위 |
|---|---|---|
| FR-D1 | ✅ **코드 완료 (2026-07-04, 커밋 ca33045)** — `selling_points.py` + `prompts.py` 배선, off/shadow/enforce 3모드, 테스트 9종. 플래그 기본 off. **셀러 텍스트 정규화(canonicalization).** 대상: **강조특징(sellingPoints·aiSuggestedPoints)만** — 자유 입력 칩 경로(`AnalysisForm.jsx:214` → `prompts.py:68`). **상품명·소재명 제외**(상품명은 리터럴 정체성이라 치환 부적합 — `prompts.py:76`; 소재명은 `materials.py`가 이미 canonical 처리 — 이중 정규화 금지). 방식 (2026-07-04 개정 — **무모델 alias 맵**): 운영자 큐레이션 **alias→canonical 사전**(`materials.py`의 `_ALIASES` 패턴 그대로 확장 — 임베딩 아님). 셀러 표현을 정규화 키로 매핑 후 canonical 영문 본문 주입. 미매칭 시: shadow=현행 sanitize 원문 유지+미매칭 로그, enforce=미매칭 항목 제외. (개방 어휘가 사전으로 감당 안 될 만큼 커지면 그때 텍스트 임베딩 재검토 — 현재는 불요.) flag `SELLER_TEXT_CANONICALIZE=off\|shadow\|enforce` | P0 |
| FR-D1a | **Ground-truth 계약 보존 (주입 위치 분리).** enforce 모드에서 canonical 본문은 PRODUCT CONTEXT("seller-confirmed … ground truth" — `prompts.py:87-88`) **바깥의 별도 파생 블록**("NORMALIZED STYLING CUES (derived from seller input)")으로 주입 — "Key features" 줄은 PRODUCT CONTEXT에서 제거(프롬프트 템플릿 `mannequin_generate_v1.txt:17`의 참조도 파생 블록으로 갱신). 셀러 원문은 **프롬프트에서만 대체되고 DB(analysis.sellingPoints)에는 원본 보존** — AG-02 카피라이팅의 "사용자 확인 정보 우선"(`ai_agent_modules.md:73`)은 서버 상태의 원본을 읽으므로 계약 위반 없음. 이로써 "seller-confirmed" 라벨은 문자 그대로 참으로 유지 | P0 |
| FR-D2 | **Pillow QC 게이팅 활성화.** ✅ **완료 (2026-07-04)** — `scripts/qc_calibrate.py`로 캘리브레이션(FP 0/15, 실패모드 검출 20/25 — 놓침 5건은 전부 crop_zoom, 픽셀 기하로 원리적 구분 불가라 AG-P2 몫으로 문서화). 유령 판정을 2-레짐으로 개선(`qc.py` — 흰옷·모노톤 정상을 차단하던 잠재 버그 수정), 회귀 테스트 7종 추가(`tests/test_qc.py`), `MANNEQUIN_QC_ENABLED=true` (.env·.env.example). **⚠️ 이후 운영 롤백 (2026-07-07, 커밋 7ce5a68)**: 실모델(Gemini 3 Pro) 출력이 전건 거부되는 이슈로 프로덕션(AWS Copilot 매니페스트)은 `MANNEQUIN_QC_ENABLED=false`(**shadow** — 로그만, 게이팅 안 함)로 운영 중. 게이팅 재활성화는 캘리브레이션 재조정 후 | ✅(코드)/⏸(게이팅) |
| FR-D3 | **실패모드 지식베이스.** `format_qc_feedback`의 하드코딩 힌트 6개(`qc.py:100-113`)를 `kb_chunks kind='correction'`(결함사유×카테고리 키, 결정적 v1 선택)으로 확장. AG-P2 도입 시 correctionPrompt 소스로 승계 | P1 |
| FR-D4 | **입력측 업로드 QC.** 1단계=Pillow 휴리스틱(해상도·블러 variance-of-Laplacian). 2단계=의류 존재·단일 의류 판정(비전 필요 — AG-P2 계열, 설계만) | P1 |

주의: FR-D1은 **안전 목적이므로 챌린저 게이트(§7)가 아니라 안전 게이트** — 평가 기준은 "품질 개선"이 아니라 "의미 보존 + 조종 차단"(shadow 로그로 매핑 정확도 검수 후 enforce).

### 4.5 NFR (비기능)

| ID | 요구사항 |
|---|---|
| NFR-1 | **결정성**: flag 동일·코퍼스 버전 동일이면 같은 입력 → 같은 프롬프트. 검색 랭킹 tie-break 명시(예: id 오름차순) |
| NFR-2 | **지연**: match-candidates GET p95 +50ms 이내 / 생성 경로 검색 p95 <500ms |
| NFR-3 | **보안**: 신규 테이블 쓰기는 service-role 배치 스크립트만. `kb_chunks`·`ref_images`는 서버사이드 검색 전용이므로 클라이언트(`authenticated`) select **미허용**이 기본 — 필요 시 명시적 결정으로만 개방. 코퍼스는 운영자 큐레이션만 |
| NFR-4 | **관측**: 검색 호출마다 `{kind, corpus_version, k, latency, flag_state}` 로깅 (`ai_agent_modules.md` §6-5 패턴) |
| NFR-5 | **비용**: 코퍼스 임베딩은 오프라인 1회+개정 시. 요청당 임베딩 호출은 Phase 1 v2의 사전계산 경로 외 금지 |
| NFR-6 | **멱등**: 배치 임베딩 스크립트 재실행 안전 (`seed_matching.py` 패턴) |

---

## 5. 아키텍처

### 5.1 검토 반영 사항 (원계획 대비 변경)

| 검토 지적 | PRD 반영 |
|---|---|
| F1: `recommend()` 호출부는 동기 GET, PL-1 job 아님 (`routes.py:484`; `ai_pipeline_spec.md:43`은 낡음). **Critic 검증 추가: PL-1/AG-01 분석 파이프라인은 라이브 코드에 아예 없음**(설계만 — `server/app/agents/`에 analyst 부재, `routes.py`에 analyze 핸들러 부재). 동기 GET이 유일한 라이브 스왑 지점 | FR-A5: 요청 중 임베딩 금지 + GET p95 예산. **부수 조치**: `ai_pipeline_spec.md:43`의 M-01 위치 서술을 실코드에 맞게 정정. styleTags 부활(FR-A1)은 AG-01 구현 전까지 셀러 확인 속성·카테고리 기반 태그 유도로 시작 |
| F2: n=64에 ANN 무의미 | §2.2 비목표: exact scan 기본, hnsw는 ref_images ~1만 행 초과 시만 |
| F3: 인라인 임베딩 컬럼이 seed 멱등 upsert와 수명 충돌 | §5.3: 사이드카 `matching_embeddings` 테이블. `seed_matching.py`는 Vertex 무의존 유지 |
| F4: RLS "패턴 따름" 느슨 | NFR-3: service-role 쓰기, 클라 select 기본 미허용 명시 |
| F5: styleTags 방치 | FR-A1: v1을 태그 친화도로 — 제로 인프라 결정적 baseline |
| F6: 평가 게이트 순환 (AG-P2 미구현) | §7: 평가는 **human-scored 루브릭**으로 시작, AG-P2 구현 전까지 자동 게이트 주장 금지. 하니스는 shadow report → AG-P2 도입 시 자동화 승격 |
| 비결정성 vs 멱등 긴장 | FR-B5: 지식 블록 버전을 job에 고정 기록, 코퍼스 개정은 새 job에만 |

### 5.2 구성도

```
                    ┌────────────────────────────────────────────────┐
                    │ Supabase Postgres (+ pgvector, Phase 0에 확장만) │
                    │  kb_chunks           (text_embedding)           │
                    │  ref_images          (image_embedding)          │
                    │  matching_embeddings (item_id, kind, vector)    │ ← 사이드카(F3)
                    │  style_affinity      (tag_a, tag_b, score)      │ ← 결정적 v1(FR-A1)
                    └────────────────────────────────────────────────┘
                          ▲ 오프라인 배치(운영자, service-role)
   scripts/embed_corpus.py┘  embed_text=bge-m3/e5(로컬) / embed_image=SigLIP(로컬) — CPU 배치
        요청 경로:
   [매칭]   GET match-candidates ─ 프리필터(불변) ─ v1 태그 친화도 ─(챌린저 v2 벡터)─ MatchingItem[]
   [지식]   PL-2/4 prep ─ v1 정적 블록 선택 ─(챌린저 v2 kb 검색)─ prompts.py 별도 블록 주입, 버전 기록
   [레퍼런스] PL-4/5 prep ─ ref_images 검색(top-k) ─ AG-06 refImages 채움 (best-effort)
```

### 5.3 데이터 모델 (초안)

> **2026-07-05 축소 완료 (임베딩 0, PR 리뷰 반영)**: 마이그레이션을 결정적 2테이블로 축소·확정 — `supabase/migrations/20260704000000_retrieval_kb_affinity.sql` = `kb_chunks`(id·kind·keys·body_en·version·is_active·updated_at, **벡터 컬럼 없음**) + `style_affinity`(tag_a·tag_b·score). RLS도 이 2개만 enable. 보류(재진입 시 별도 forward 마이그레이션): `create extension vector`, `text/image_embedding` 컬럼, `ref_images`·`matching_embeddings` 테이블. 동반 정리: `embeddings.py`·config 임베딩 필드(openai_api_key·embed_*·retrieval_refimages)·`sqlparse` dev 의존 제거. 셀러 정규화(FR-D1)는 alias 사전이라 테이블 불요(코드 상수). 아래 코드블록은 원설계(재진입 참조용) — 실제 적용본은 위 파일.

```sql
-- Phase 0 마이그레이션
create extension if not exists vector;

-- 지식 청크 (운영자 큐레이션, 서버 전용)
create table public.kb_chunks (
  id           text primary key,
  kind         text not null,           -- 'styling' | 'composition' | 'brand' | ...
  keys         jsonb not null,          -- 카테고리·styleTags 매칭 키 (v1 정적 선택용)
  body_en      text not null,           -- canonical 영문 본문 (주입용)
  version      integer not null default 1,
  text_embedding vector(1536),          -- v2 챌린저용 (nullable — v1은 keys만 사용)
  is_active    boolean not null default true,
  updated_at   timestamptz not null default now()
);
-- RLS: 활성화하되 클라 정책 없음 = service-role 전용 (NFR-3)
alter table public.kb_chunks enable row level security;

-- 레퍼런스 이미지 (운영자 시드 → 추후 성공 생성물)
create table public.ref_images (
  id            text primary key,
  r2_key        text not null,
  cut_type      text not null,          -- 'styling' | 'horizon' | 'product' | 'mirror'
  mood_tags     jsonb not null default '[]',
  image_embedding vector(1408),         -- Vertex multimodal 차원(확정 시 조정)
  source        text not null default 'seed',  -- 'seed' | 'generated'
  is_active     boolean not null default true,
  created_at    timestamptz not null default now()
);
alter table public.ref_images enable row level security;

-- 매칭 임베딩 사이드카 (F3 — matching_items 본체·seed 스크립트 불변)
create table public.matching_embeddings (
  item_id     text not null references public.matching_items(id) on delete cascade,
  kind        text not null,            -- 'image' | 'style'
  embedding   vector not null,
  model       text not null,
  embedded_at timestamptz not null default now(),
  primary key (item_id, kind)
);
alter table public.matching_embeddings enable row level security;

-- 태그 친화도 (FR-A1 v1 — 운영자 큐레이션 정적 맵)
create table public.style_affinity (
  tag_a text not null,
  tag_b text not null,
  score real not null,                  -- 0..1
  primary key (tag_a, tag_b)
);
alter table public.style_affinity enable row level security;
```

인덱스: **없음(exact scan)**. `ref_images` ~1만 행 초과 시 `create index ... using hnsw (image_embedding vector_cosine_ops)` (FR-C5).

### 5.4 신규 서버 모듈

| 파일 | 역할 |
|---|---|
| `server/app/services/embeddings.py` | **자체 호스팅 PyTorch (D2 개정 2026-07-04)**: `embed_text()`=bge-m3/multilingual-e5(한국어↑), `embed_image()`=SigLIP/open-CLIP. sentence-transformers/transformers 로컬 추론, CPU 배치. 모델은 config 단일소스. 관측 로깅. ⚠️ 현 커밋본은 OpenAI/Vertex API판 — 리워크 대상 |
| `server/app/services/retrieval.py` | kb 선택(v1 정적/v2 벡터)·ref 검색·매칭 랭킹(v1 태그/v2 벡터). 순수 함수 지향, flag 분기 |
| `server/scripts/embed_corpus.py` | 코퍼스 배치 임베딩 (멱등, service-role) |
| `server/scripts/eval_quality.py` | 평가 하니스 — 고정 평가셋 생성 실행 + 루브릭 채점 시트 출력 (§7) |

### 5.5 Flag 체계

```
RETRIEVAL_MATCHING=off|tags|vector      # FR-A6 (기본 off=현행 밝기)
RETRIEVAL_KNOWLEDGE=off|static|vector   # FR-B (기본 off)
RETRIEVAL_REFIMAGES=off|on              # FR-C (기본 off)
SELLER_TEXT_CANONICALIZE=off|shadow|enforce  # FR-D1 (기본 off, 안전 게이트)
# FR-D2는 기존 MANNEQUIN_QC_ENABLED 사용 (신규 flag 없음)
```
config.py `Settings`에 필드 추가, env 단일소스 (`model_routing.py` 패턴).

---

## 6. 단계별 롤아웃

| Phase | 내용 | 벡터 의존 | Vertex 의존 | 게이트 |
|---|---|---|---|---|
| **0** | pgvector 확장 + 스키마 + `embeddings.py`/`retrieval.py` 골격 + flag + 평가 루브릭·평가셋(상품 ≥20) + baseline 채점 | 스키마만 | ✗ | 기존 테스트 그린, 행위 변화 0 |
| **1a** | 매칭 v1: styleTags 부활 + `style_affinity` 시드 + 태그 랭킹 | ✗ | ✗ | 보완성 회귀 0 + 루브릭 ≥ 현행 |
| **1b** | 매칭 v2 챌린저: `matching_embeddings` 적재 + 벡터 랭킹 (shadow) | ✓ | ✓(폴백 가능) | v1을 루브릭에서 **이기면** 승격 |
| **2a** | 지식 v1: `kb_chunks` 큐레이션 시드 + 정적 선택 주입 + 버전 기록(FR-B5) | ✗ | ✗ | 소재 스냅샷 불변 + 루브릭 개선 |
| **2b** | 지식 v2 챌린저: 텍스트 임베딩 검색 (shadow) | ✓ | ✗ | v1을 이기면 승격 |
| **3** | 레퍼런스: `ref_images` 시드 임베딩 + AG-06 `refImages` 채움 | ✓ | ✓(또는 폴백) | 컷 일관성 루브릭 개선 |
| **D2** | QC 게이팅: 표본 캘리브레이션 → `mannequin_qc_enabled=True` | ✗ | ✗ | 거짓양성률 <10% (표본 기준), 정상 이미지 차단 0 |
| **D1** | 텍스트 정규화: canonical 어휘 시드 + shadow 매핑 → enforce | ✓(텍스트만) | ✗ | shadow 매핑 정확도 검수(운영자) 후 enforce 승격 |

Track D는 Phase 번호와 독립 병렬: **D2는 지금 즉시 가능**(코드 기존재, Phase 0 무의존). D1은 Phase 0(`embeddings.py`) 이후. FR-D3은 2a와 함께, FR-D4는 후순위.

의존성 주의: **Vertex 인증(R3)이 1b·3만 막는다.** 1a·2a·2b는 Vertex 무관 — 인증 프로비저닝과 병렬 진행 가능. (원계획의 "매칭이 최저 리스크" 서사는 v2 벡터 기준으론 틀렸고, v1 태그 기준으로만 참.)

---

## 7. 품질 평가 (게이트 설계 — F6 반영)

- **도구**: AG-P2(의미적 동일성 검수)는 미구현(`ai_agent_modules.md:44,162`) → 자동 채점 게이트는 **불가**. 시작은 **human-scored 루브릭** + 이미 라이브인 **Pillow QC**(현재 SHADOW 모드 — `ai_pipeline_spec.md:158`)를 결정적 보조 프록시(크롭/고스트 휴리스틱)로 병용.
- **루브릭(운영자 채점, 5점 척도)**: ① 의류 동일성(색·패턴·디테일 보존) ② 컷 간 스타일 일관성(톤·무드·배경) ③ 카테고리 적합 구도 ④ 매칭 조합 자연스러움.
- **프로토콜**: 고정 평가셋 상품 ≥20건 × flag on/off 생성 → 블라인드 채점(어느 쪽이 on인지 미표기) → 평균·분산 리포트.
- **게이트 규칙**: v1(결정적)은 현행 **이상**이면 활성. v2(벡터 챌린저)는 v1을 **초과**해야 승격. 동률이면 결정적 쪽 유지(원칙 1).
- **승격 경로**: AG-P2 구현 시 루브릭 ①을 자동 채점으로 대체 → 하니스가 shadow report에서 자동 게이트로 승격.

---

## 8. 리스크

| # | 리스크 | 완화 |
|---|---|---|
| R1 | 매칭 벡터화가 look-alike 회귀 | 프리필터 불변(FR-A2) + 보완성 회귀 테스트. v2는 챌린저라 v1 못 이기면 미승격 |
| R2 | 소재 가이드 회귀 | `material_guidance()` 불변 + 스냅샷 테스트(FR-B6). 코퍼스에서 소재 본문 제외(FR-B2) |
| R3 | ~~Vertex 인증~~ **소멸** (D2 자체호스팅 확정 2026-07-04). 신규: torch ~2GB 의존이 ECR 이미지 크기·ECS(Fargate) 콜드스타트 증가 | 임베딩을 배치/워커 프로세스로 분리(API 비대화 방지), 모델 가중치 1회 캐시. CPU 배치라 GPU 불요. 1b·3의 Vertex 차단 원인 제거됨 |
| R4 | 인젝션 표면 증가 | 코퍼스 운영자 큐레이션만(NFR-3), canonical 영문 본문, `_sanitize` 가드(FR-B3) |
| R5 | "품질 보장" 근거 부재 | human 루브릭 프로토콜(§7). 미구현 도구(AG-P2)로 게이트 주장 금지 |
| R6 | 성공 생성물 신호 부재(레퍼런스 백필) | 운영자 시드로 시작(FR-C1). 신호(§9-2) 정의는 별도 결정 |
| R7 | 동기 GET 지연 회귀(F1) | 요청 중 임베딩 금지(FR-A5), p95 예산 게이트 |
| R8 | 프롬프트 비결정성 vs 멱등 충돌 | 지식 블록 버전 job 고정(FR-B5), tie-break 명시(NFR-1) |
| R9 | 코퍼스 큐레이션 노동이 실제 병목 | v1들(태그 친화도·정적 블록)은 큐레이션 규모가 작고 닫혀 있음. kb 코퍼스 소유자·범위는 §9-4 선결 |
| R10 | 정규화 매핑이 의미 왜곡(엉뚱한 canonical로 매핑 → 잘못된 특징 강조) | shadow 단계에서 매핑 로그 운영자 검수 → 정확도 확인 후 enforce. 유사도 임계 미달 시 제외(주입 안 함). 어휘 커버리지 부족은 canonical 어휘 증보로 대응 |
| R11 | QC 게이팅 거짓양성(정상 이미지 차단 → 재시도 낭비·크레딧·지연) | 캘리브레이션 표본으로 임계 조정, 거짓양성률 게이트(<10%) 통과 후 활성. `mannequin_max_attempts=2` 상한 유지. 부분 성공 정책 불변 |

---

## 9. 오픈 이슈 — 2026-07-04 사용자 결정으로 전부 해소

| # | 이슈 | 결정 |
|---|---|---|
| 1 | ~~Vertex 인증 경로~~ → **자체 호스팅 PyTorch 확정** (2026-07-04). 대체 오픈이슈: 로컬 모델 선택(텍스트 bge-m3 vs e5, 이미지 SigLIP base vs so400m)·서빙 위치(배치 스크립트 vs 워커 프로세스)·벡터 차원 확정 → 마이그레이션·embeddings.py 리워크 |
| 2 | 성공 생성물 편입 신호 | ✅ **운영자 수동 선별** — 품질 통제 우선, 자동 신호는 추후 전환 |
| 3 | 평가셋·채점자 | ✅ **사용자 본인 직접** — 상품 20건 선정 기준은 Phase 0 실행 시 확정 |
| 4 | kb 코퍼스 소유자 | ✅ **사용자 본인** — AI가 초안 생성 → 본인 검수 형태로 노동 축소 |
| 5 | styleTags 어휘 | ✅ **닫힌 enum** — 운영자 정의 20~30개, 시드 태그(basic·daily 등) 기반 출발 |
| 6 | `ai_pipeline_spec.md:43` 정정 | 결정 불요 — 실행 시 작업 (계획 step 7) |
| 7 | canonical 어휘 규모·임계값 | 소유자 본인(#4와 동일). 초기 규모 ~100개, 임계값은 shadow 로그 실측으로 캘리브레이션 |
| 8 | QC 캘리브레이션 표본 | 권장안 채택 — 스파이크·기존 생성물 재활용 + 부족분 신규 생성 |

---

## 변경 이력

- 2026-07-04: 초안. 작업 계획 + Architect 검토(F1~F6) 합성안 반영 — deterministic-first(v1) + 벡터 챌린저(v2) 구조, 사이드카 임베딩 테이블, exact scan 기본, human 루브릭 게이트, 지식 버전 job 고정.
- 2026-07-04 v1.5: **D1 강조특징 정규화 구현**(커밋 ca33045) — 무모델 alias 사전(selling_points.py) + prompts.py off/shadow/enforce 배선. 인젝션 폐기·ground-truth 라벨 보존(FR-D1a) 검증. 테스트 75 passed. 플래그 기본 off.
- 2026-07-04 v1.4: **임베딩·벡터 전면 보류 확정** (사용자 결정, ADR D2 최종). 3대 목적에 임베딩 불필요 확인 → 채택 = 결정적 스택(1a 태그 매칭 + 2a 정적 지식 + Track D alias 정규화·QC). FR-A4/A5/B4·Phase 1b/2b/3 보류. FR-D1을 임베딩 최근접 → **무모델 alias 사전**으로 개정. 스키마 벡터부 축소(결정적 테이블 2개만). embeddings.py·벡터 마이그레이션은 dormant 스캐폴드(재진입 시). 재진입 조건 = 이미지 유사도 필요 판명 시(자체 호스팅 PyTorch).
- 2026-07-04 v1.3: **임베딩 = 자체 호스팅 PyTorch 확정**(사용자 결정, ADR D2 개정) — SigLIP/open-CLIP(이미지) + bge-m3/e5(텍스트), CPU 오프라인 배치. 근거: 오프라인 배치라 GPU 불요·한국어 우세·결정성 정합·Vertex 인증 벽 소멸. 생성은 Gemini API 불변, 자체 AI 훈련 제외 확정. R3 소멸(→torch 의존 리스크로 대체). 후속 리워크: embeddings.py(API→로컬), 마이그레이션 벡터 차원(1536/1408→모델 dim).
- 2026-07-04 v1.2: 오픈이슈 8건 전부 해소(사용자 결정 — §9 표) + LangChain/LangGraph 미채택 확정(계획 ADR D7).
- 2026-07-04 v1.1: **방어 트랙 FR-D 추가** (사용자 목적 재확인: 일관성·인젝션 방지·엣지케이스 방어) — D1 셀러 텍스트 정규화 검색(승인됨), D2 QC 게이팅 활성화, D3 실패모드 KB, D4 입력측 QC. R10·R11, 오픈이슈 7·8, flag 2종 추가.
