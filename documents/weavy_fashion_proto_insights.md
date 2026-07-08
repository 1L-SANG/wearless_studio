# Weavy "Fashion Proto Visualizer" 워크플로우 분석 → Wearless 적용 인사이트

> 분석일 2026-07-07. 대상: app.weavy.ai 커뮤니티 플로우 "Fashion Proto Visualizer" (사용자 복제본).
> 방법: 브라우저 CDP로 캔버스 DOM에서 **모든 노드 텍스트 전문(61KB) 추출** — 스크린샷 잘림 없음. 입력 이미지 3종·중간 JSON·최종 렌더 원본까지 확보.
> 원본 덤프: 세션 스크래치 `weavy/dom_dump.txt` (필요시 재추출 가능).

---

## 1. 워크플로우 전체 구조

**용도**: 패션 디자이너의 프로토타입 시각화 — 테크니컬 드로잉 + 원단 사진 + Pantone 컬러칩 → 사진급 flatlay(앞/뒤 나란히) 렌더.

```
[테크니컬 드로잉 이미지] ──┬─→ ① Technical Drawing Specialist (Claude opus, temp 0)
                          │      2-phase: 전사 → 구조화 JSON (construction)
                          └─→ ② Garment Specialist (Claude, temp 0)
                                 실루엣/핏만 추출 JSON (shape)
[원단 사진 + Pantone 칩] ───→ ③ Fabric Specialist (Claude, temp 0)
                                 소재 시각속성 enum JSON (fabric+color)
①+②+③ JSON + 원본 이미지 3장 + 사람 오버라이드 7슬롯
    └─→ ④ 3D garment visualization (Nano Banana 2, 1920×1080)
           = 섹션 조립식 프롬프트 → flatlay 앞/뒤 렌더
```

- 추출은 전부 **Claude(anthropic/claude-opus-4-6), temperature 0, thinking off**. 생성은 Nano Banana 2(Gemini 이미지). — 우리 스택 방향(분석 LLM + Gemini 이미지)과 동일.
- 실측 검증: 드로잉의 콜아웃(107cm, 스탠딩칼라, CF 블라인드클로저, CB심, 사이드패널 셰이핑)이 렌더에 충실 반영. 원단 사진의 펠티드 울 질감도 재현.

## 2. 노드별 핵심 설계 (프롬프트 전문 기반)

### ① Technical Drawing Specialist — 2단계 추출
- **입력 분류 먼저**: `construction_td / colorway_sheet / print_sheet / mixed` 판별 후 단계 적용.
- **Phase 1 (raw transcription)**: 보이는 모든 텍스트를 부위별(collar, body_front, hem…)로 전사 = "working memory". **Phase 2 (structured)**: 스키마로 매핑.
- **`render` / `debug` 분리**: render는 **enum만 허용**(자유 텍스트 금지), 모든 자유 텍스트·전사·노트는 debug로. "Do not invent measurements."
- 정규화 규칙 내장: CF→center_front, DTM→dye_to_match. 장식용 포켓 `is_decorative` 플래그. 외부 문서 참조는 `external_references`로 격리.

### ② Garment Specialist — 스코프 격리
- "실루엣과 외곽선에 영향 주는 것만. **fabric, color, trims, stitching, hardware, print는 무시하라**" — 명시적 ignore 목록으로 역할 격리.
- 필드 전부 enum: silhouette(9종), fit, shoulder, collar_outline(16종), hem_shape, body_volume…
- `debug.proportions_note` max 120자, `confidence: low|med|high`.

### ③ Fabric Specialist — ★ 소재를 '사진에서 enum으로'
- 입력: 원단 사진 + 컬러 레퍼런스(Pantone 칩). 1장/2장 모드 자동.
- **9개 시각속성 enum**: `material_base`(25종: woven_plain/twill/satin, knit_jersey/rib/sweater, denim, leather, lace, seersucker, chiffon…), `texture`(15종: puckered/ribbed/felted/hairy…), `finish`(matte→glossy/coated/waxed…), `weight`(5단계), `drape`(fluid→stiff 5단계), `stretch`(4단계), `opacity`(4단계), `nap`(4단계), `sheen`(4단계) + `grain_visible`, 컬러 3필드, 패턴 3필드.
- 컬러 코드는 "PANTONE 접두어 포함 **정확히 전사**", 출처를 `color_code_source`로 기록.

### ④ 이미지 생성 프롬프트 — 섹션 조립 + 우선순위 명문화
```
CRITICAL — EXACT OUTLINE MATCH (드로잉 = 바인딩 템플릿, "not a suggestion")
=== GARMENT CONSTRUCTION ===   ← ①JSON 통째
=== GARMENT SHAPE AND PROPORTIONS === ← ②JSON 통째
=== FABRIC AND COLOR ===       ← ③JSON 통째 + "질감은 Image 2, 색은 Image 3"
=== DESIGNER OVERRIDES ===     ← 사람 입력 7슬롯 (Length/Fit/Silhouette/Weight/Drape/Stretch/Fiber)
    "비어 있으면 무시, 값이 있으면 AI 분석과 충돌해도 항상 우선"
=== RENDERING INSTRUCTIONS (fixed) ===
    NO TEXT(5줄 강조) / LAYOUT / 프레젠테이션 / 배경·조명(#F0F0F0)
    FABRIC RENDERING: 조건부 라인 — "If has_quilting is true, show quilting texture…",
                      "If opacity is semi_transparent…, show subtle visibility"
    CONSTRUCTION: "If the JSON conflicts with what is visible in Image 1, the IMAGE wins"
    CONSISTENCY: 앞/뒤 동일 의류·색·비율
```

### 실측에서 잡힌 한계 (그대로 교훈)
- 추출 JSON이 드로잉의 **"double welt pockets"·"top stitching" 콜아웃을 누락**(side_seam으로 잘못 분류). 그런데 **최종 렌더에는 웰트 포켓이 정확히 그려짐** — "이미지가 이긴다" 규칙이 추출 오류를 구제. → temp 0 Claude도 콜아웃을 놓친다 = 텍스트 추출을 정본으로 삼으면 안 되고, 이미지-우선 계층이 실제 안전망으로 작동한다는 실증.
- debug 블록(전사 원문 포함)까지 이미지 프롬프트에 통째 주입 → 토큰 낭비 + 텍스트 렌더 오염 위험(NO TEXT 5줄 강조가 필요해진 이유로 추정).

## 3. Wearless 적용 인사이트 (우선순위순)

### A. ★★★ Fabric Specialist의 enum 스키마 → AG-01 확장 (즉시 차용 가능)
지난 소재 토론의 결론 — "조성(materials[])은 겉모습을 절반밖에 결정 못 한다, 비전이 시각속성을 직접 뽑아야 한다" — 를 이 플로우가 **검증된 스키마로 실증**한다. AG-01 분석 출력에 `fabricVisual` 섹션 추가:
```
material_base / texture / finish / weight / drape / stretch / opacity / nap / sheen (전부 enum)
```
- 셀러 실물 사진에서 직접 추출 → 조성 무입력·오입력에도 동작. 기존 `materials{name,ratio}`는 prior/검증용으로 유지.
- **기존 자산 재활용**: `materials.py` 블록 문장들은 그대로 두고, 선택 키만 "조성 추론" → "fabricVisual enum"으로 교체 가능. 문장 라이브러리는 이미 있다.
- render/debug 분리 원칙도 함께: 이미지 프롬프트에는 enum(render)만, 자유 텍스트는 디버그/추적용.

### B. ★★ SELLER OVERRIDES 블록 — 컷 에이전트 프롬프트 조립 문법
셀러가 분석 결과를 수정하는 UX(이미 우리 플로우에 존재: seller-confirmed analysis)를 이미지 프롬프트 레벨에서 명문화:
```
=== SELLER OVERRIDES === (빈 값 무시 / 값 있으면 AI 분석보다 항상 우선)
=== AI ANALYSIS ===      (fabricVisual enum + 카테고리…)
충돌 계층: 제품 레퍼런스 이미지 > 셀러 오버라이드 > AI 분석
```
"empty = ignore, present = always wins" 시맨틱이 단순하고 모델이 잘 따른다(실측 렌더가 override의 wool을 반영).

### C. ★★ 조건부 렌더 라인 (enum → if-라인 매핑)
정적 블록 대신 추출된 enum이 조건부 한 줄씩 켜는 방식:
- `opacity=semi_transparent` → "show subtle visibility through fabric"
- `nap≥medium` → 기모/스웨이드 라인, `sheen=high` → 새틴 하이라이트 라인…
우리 컷 에이전트 `cut_generate` 템플릿의 `[[CUT:…]][[SHOT:…]]` 조립 구조와 동형 — 소재도 같은 방식으로 조립하면 된다. 지난 토론의 "샷 타입별 소재 라인 분리"와 자연 결합.

### D. ★ 2-phase 추출 (전사→구조화)
상세페이지 스펙 이미지·치수표·라벨 사진을 받는 순간 그대로 유효한 패턴. 단, 위 실측처럼 **전사도 누락된다** — 전사 결과를 정본화하지 말고 이미지 동반 입력 + 이미지-우선 규칙 유지가 필수.

### E. 채택하지 말 것
1. **3-스페셜리스트 분리 호출**: 스코프 격리 효과는 있지만 LLM 3콜 비용·지연. 우리는 AG-01 한 콜에 섹션 스키마(shape/fabric/construction)로 충분 — 품질 문제가 실측되면 그때 분리.
2. **debug까지 프롬프트 주입**: render enum만 주입. 우리 PRODUCT CONTEXT는 이미 간결 지향.
3. **테크니컬 드로잉 전제**: 그들의 입력은 선화(디자이너 B2B), 우리는 실물 사진(셀러) — outline-match 문구("treat every line as binding")는 우리의 "reference wins"와 같은 역할이며 이미 보유. 드로잉 특화 부분은 불필요.

## 4. 제안 로드맵 (코드 배선은 별도 결정)
1. AG-01 스키마에 `fabricVisual` enum 섹션 추가 설계(공통 데이터 계약 §4에 enum 정의) — Fabric Specialist 스키마를 한국 커머스에 맞게 트림.
2. `materials.py`에 fabricVisual 입력 경로 추가: enum 매핑이 1순위, 조성 추론은 fallback.
3. 컷 에이전트 설계 시 프롬프트 조립기에 SELLER OVERRIDES 섹션 + 충돌 계층 문장 + 조건부 소재 라인 채택.
4. 3-arm 검증(무블록/명사만/enum기반)은 여전히 선행 과제 — 이 플로우도 "효과 실증"이 아니라 "설계 실증"임을 유의.

---

## 5. 정정·주의: "예시 원단 라이브러리"가 아니다 (오독 방지)

Weavy의 원단 사진은 참고용 예시가 아니라 **그 옷에 실제로 쓸 원단의 실물 사진**, Pantone 칩은 목표 색의 실물 근거다. 즉 ground truth를 역할별로 쪼갠 것: **Image 1 = 외곽선 정본, Image 2 = 질감 정본, Image 3 = 색 정본.**

- ❌ **하지 말 것**: "감지된 소재에 맞는 일반 원단 예시 이미지를 라이브러리에서 첨부". 이미지 컨디셔닝은 텍스트보다 강해서, 일반 데님 스와치가 셀러 옷의 실제 워싱·색·질감을 스와치 쪽으로 끌어당긴다. Weavy는 원단 사진 자체가 정답이라 가능했던 방식 — 우리의 정답은 셀러 제품 사진이다.
- ✅ **응용 변형 (2026-07-07 2차 정련 — "신뢰"가 아니라 "게이트"로 결정)**: 셀러를 믿을지 말지는 잘못된 프레임. 실제 비대칭은 ① 접사는 광학적으로 실패율이 구조적으로 높고(최소초점거리·모아레·NR 뭉개짐 — 실력 무관), ② 자동 크롭은 제품 사진 품질을 *상속*하지만 셀러 클로즈업은 *독립적 실패 원천을 추가*하며 그 실패가 조용하다(렌더가 깨지지 않고 질감·색이 은근히 오염). 단 잘 찍힌 클로즈업은 크롭보다 명백히 우수(원거리 사진 크롭은 수백 px라 니트 게이지·트윌 결 해상 불가 — 크롭은 바닥이지 천장이 아님). 따라서:

  **질감 레퍼런스 소스 우선순위 = 셀러 클로즈업(선택 업로드) > 자동 크롭 > 없음. 전 소스 동일 QC 게이트(선명도·모아레·색캐스트·부위 적합성) 통과분만 사용, 탈락 시 조용히 다음 순위 fallback. 권한은 항상 질감 전용 — 색은 어떤 경우에도 전체 제품 사진에서.**

  잘 찍는 셀러는 클로즈업 경로로 최고 품질, 못 찍는 셀러는 게이트가 걸러 무해. 클로즈업 업로드 QC 통과율을 로깅해 기능 확대/축소를 데이터로 결정. 저품질 원단의 충실 재현 vs 이상화는 제품 정책 이슈로 별도 결정.

## 6. 추가 인사이트 — 프롬프트 기법·입력 구성·운영 (2차 분석)

### 프롬프트 작성 기법
- **(a) 실패모드 열거형 프롬프트** ★ 가장 배울 점. OUTLINE MATCH 블록은 일반론이 아니라 실제 겪은 실패의 나열이다: "V넥이 그려져 있으면 그대로 — 닫지 마라", "오픈 프론트를 닫아 렌더하지 마라", "칼라 접힘 각도 그대로". **프롬프트를 회귀 테스트 목록처럼 축적** — QC 반성 루프(실행 시점 교정)와 별개로, 만성 실패를 베이스 프롬프트에 영구 각인. 우리 만성 실패(플랫레이→고스트 변환, 2K 유령이미지 등)를 이 형식으로 명문화 가능.
- **(b) NO TEXT 5중 강조의 이유**: 입력 이미지(드로잉)에 콜아웃 텍스트가 가득해서 모델이 텍스트를 새어 그린다. 우리도 스펙 이미지·라벨 사진을 입력에 넣는 순간 같은 리스크 → 그때 이 블록 차용.
- **(c) `unknown`의 퍼스트클래스화 + "Never invent"**: 모든 enum에 unknown/none, "판별 불가면 unknown". 환각 억제를 스키마 레벨에서 해결. AG-01 "확신 없는 소재는 비우기"의 필드 단위 세분화 버전 — 부분 확신(핏 확실·길이 불확실)을 살린다.
- **(d) `confidence` 필드 게이트**: 스페셜리스트마다 low|med|high → low면 해당 섹션 미주입 또는 QC 플래그. "저확신 분석은 주입하지 않는다"는 안전장치로 확장 가능.
- **(e) 도메인 용어 정규화 사전 내장**: CF→center_front, DTM→dye_to_match. 한국 커머스판: "아방핏→oversized", "총장→length", "안기모" 등 셀러 용어 사전을 AG-01 프롬프트에 내장.
- **(f) 자유 텍스트 길이 제한**: proportions_note ≤120자, texture_notes ≤80자 — debug 비대화 방지.
- **(g) `is_decorative` 플래그**: 페이크 포켓·장식 단추를 스키마로 구분 — "장식 지퍼를 열리게 그리는" 류의 상세컷 실수 방지.

### Input 구성
- **(h) 이미지별 단일 권한 선언**: 역할 라벨(PRODUCT/MOOD/MATCH)에서 한 발 더 — "질감은 Image 2에서, 색은 Image 3에서"처럼 **각 이미지가 어떤 속성의 정본인지 문장으로 못박기**. 긍정형("X는 여기서 가져와라")이 부정형("가져오지 마라")보다 강하다. MOOD가 의류 색을 오염시키면 이게 처방.
- **(i) 오버라이드 = 고오류 필드만 노출한 타입드 폼**: 슬롯 7개뿐, 전부 선택지형(자유 텍스트 아님). 전체 스키마가 아니라 **AI가 자주 틀리는 필드만** 노출 — 셀러 수정 UI 설계 원칙. 인젝션 안전(canonical-key-only)과 일치.
- **(j) 입력 분류 게이트** ★ 우리에게 가장 실용적. 추출 전에 입력 유형 분류(construction_td/colorway_sheet/…) 후 경로 분기. 우리 버전: **셀러 사진 타입 분류(착용/마네킹/행거/플랫레이/디테일)를 파이프라인 첫 단계로** — 플랫레이가 주적이라는 스파이크 결론과 직결. 플랫레이 판정 시 드레이프 prior 강화 + QC 강화.

### 운영
- **(k) 추출은 temp 0 + thinking off**: 결정성·비용(1크레딧). 우리 분석 콜 세팅 점검 가치.
- **(l) "원단만 갈아끼우기" 구조**: 같은 드로잉에 원단/색 스왑으로 변형 생성. 직접 해당 없음(우리는 실물을 그림). 단 매칭 의류 색상 변형 제안·시즌 컬러 시안 기능이 생기면 이 구도가 정답.

## 7. 채택 우선순위 (컷 에이전트 설계 참조용)

| 순위 | 인사이트 | 적용처 |
|---|---|---|
| 1 | 입력 사진 타입 분류 게이트 (j) | AG-01 첫 단계, 플랫레이 대응 |
| 2 | 실패모드 열거형 프롬프트 (a) | 마네킹·컷 베이스 프롬프트 |
| 3 | 이미지별 권한 선언 (h) | cut_generate 매니페스트 강화 |
| 4 | 오버라이드 타입드 폼 (i) | 셀러 분석 수정 UI |
| 5 | unknown/confidence 규율 (c,d) | AG-01 스키마 |
| 6 | 질감 레퍼런스 QC 게이트: 클로즈업 업로드 > 자동 크롭 > 없음 (§5) | 클로즈업 컷 질감용 |
| — | fabricVisual enum 스키마 (§3-A) | AG-01 + materials.py (별도 최우선 트랙) |

**금지**: 일반 원단 예시 이미지 첨부(§5) — 실제 옷과 다른 스와치가 정본(제품 사진)과 싸운다.
