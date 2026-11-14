# 공통 데이터 계약 (Common Data Contract)

> 상태: 확정 (2026-06-11, 갱신 2026-07-18) · 근거: `documents/PRD.md`, 프론트 API·목 구현(`src/lib/api/`, `src/mock/*`), 2026-06-11·06-14·07-17·07-18 결정 세션
> 결정 기록: `docs/adr/0001~0007`, 용어는 `/CONTEXT.md`
> 적용 방식: 이 문서가 계약의 원본이다. 프론트 API·목 레이어를 이 계약에 맞추고, 백엔드(FastAPI·Supabase·R2)와 AI 파이프라인은 같은 shape를 구현한다. 현행 코드와의 차이(마이그레이션 갭)는 루트 `TODOS.md`가 추적한다 — 충돌 시 해당 기능의 현행 ADR과 사용자 결정을 함께 확인한다.

---

## 1. 명명·직렬화 원칙

1. **저장되는 key와 enum 값은 영문 토큰, 한국어는 표시 라벨로만.** 라벨은 `catalogs`의 `{ value, label }`에서 파생한다. 단, **사용자가 직접 입력하는 자유 텍스트**(상품명, 소재명, 강조 특징, 카피, 텍스트 요소)는 한국어 그대로 저장한다.
2. **필드명은 camelCase.** DB(snake_case) 매핑은 백엔드 어댑터 책임이며 프론트 계약은 camelCase 고정.
3. **enum 토큰은 소문자 단일어 또는 snake_case**(`semi_over`). 기존 토큰 `ai-notice`, `arrow-l`은 마이그레이션 비용 대비 이득이 없어 예외로 유지한다. 같은 자동 블록 계열인 `common-notice`도 예외다.
4. **id는 불투명 문자열.** 클라이언트 임시 id는 `{prefix}_{rand}`(prefix: `prj` `prd` `col` `img` `blk` `b` `el` `w`)이며, 서버 도입 후 서버 발급 id로 대체된다. 프론트는 id 형식에 의존하지 않는다.
5. **시간은 ISO 8601 문자열**(`updatedAt`). '2시간 전' 같은 상대 표시는 화면에서 파생한다.
6. **이미지 `src`는 URL 문자열.** 현재는 objectURL/placeholder, 백엔드 이후 R2 URL. 클라 전용 표시 필드(`loading`, `fresh` 등)는 계약에 포함하지 않는다.

---

## 2. 최상위 엔티티: Project

입력, 분석, 콘티, 마네킹, 생성, 에디터, 보관함 순서의 플로우 전체를 묶는 단위. 모든 API는 `projectId`를 첫 인자로 받는다 (ADR-0001).

```ts
Project {
  id: string
  status: ProjectStatus            // draft | generating | done
  title: string                    // = product.name 미러 (보관함 표시용)
  copywriting: boolean             // 기본 true. 템플릿 콘티는 콘티에 들어갈 때와 다음 단계로 넘어가기 전에 true로 저장한다. 화면에 켜고 끄기는 없다
  selectedMannequinId: string | null
  adjustCount?: number             // 과거 저장값 읽기 전용. 새 입력·제한·프론트 동기화에 사용하지 않음
  createdAt: string                // ISO
  updatedAt: string                // ISO
}
// 폐기(2026-11): composeMode. DB `projects.compose_mode` 컬럼(기본 'basic')만 남는다.
//   서버는 이 컬럼을 읽거나 응답에 넣지 않고, PATCH로 composeMode나 compose_mode가 와도 무시한다(오류 아님)

ProjectSummary {                   // 보관함 카드 (구 LibraryItem)
  id: string
  title: string
  cover: string                    // 대표 이미지 (파생: 첫 에디터 블록 이미지)
  clothingType: ClothingType | null // 초안은 상품 미입력 → null 가능 (보관함 카드 표시는 라벨 파생)
  blockCount: number               // 구 blocks (배열과 혼동 방지 위해 개명)
  publicationCount: number         // 소유자의 ready 게시 수. 0이면 카드에서 숨김
  status: ProjectStatus
  updatedAt: string                // ISO — '2시간 전'은 표시 파생
}
```

- `copywriting` `selectedMannequinId`는 **서버 동기화 대상 선택값**이다. 프론트는 Zustand에 작업 사본을 두고 변경 시 `patchProject`로 동기화한다 (→ `frontend_state_model.md`).
- 콘티에 한 번 들어간 프로젝트는 입력(`/create/input`)이나 분석(`/create/analysis`) 주소로 가면 콘티로 돌려보내고 "콘티를 만든 뒤에는 분석으로 돌아갈 수 없어요"를 보여 준다. 생성이 끝났거나 에디터에 들어간 프로젝트는 기존 완료 안내(PRD §10.17)가 먼저다. 진입 표식은 브라우저 저장소(`sb-entered-<projectId>`)에만 두는 화면 상태이며 서버 필드가 아니다. 저장소를 쓸 수 없으면 막지 않는다(`src/lib/storyboardEntered.js`, `src/apps/seller/App.jsx`).
- 핏 설정은 `analysis.fitProfile`로 저장하고 이미지 반영은 재생성으로 처리한다. 재생성 횟수 제한은 없으며 적용 단가·무료 수정 정책은 PRD §12.2를 따른다.
- `adjustCount`와 컷의 과거 조정 필드는 저장값 읽기 호환용이며 새 요청이나 제한 근거로 쓰지 않는다.

---

## 3. 엔티티 사전

### 3.1 Product — 상품의 물리적 사실 (단일 소유)

`clothingType`·`measurements`·`measurementsUnknown`은 **Product가 단일 소유**한다. 분석 확인 폼에서 이 필드를 수정하면 `saveProduct`로 저장한다. Analysis에는 이 필드들이 없다. 에디터 '사이즈 안내' 자동 블록은 `product.measurements`를 참조한다.

```ts
Product {
  id: string
  projectId: string
  name: string                     // 자유 텍스트 (비우면 analysis.suggestedName 반영)
  clothingType: ClothingType
  colors: ColorGroup[]             // [0]은 항상 기준 색상 (isBase)
  measurements: Measurement[]      // clothingType의 measurementSchema를 따름
  measurementsUnknown: boolean
  uploadComplete: boolean
}

ColorGroup {
  id: string
  name: string                     // 쇼핑몰용 세부 색 이름(자유 텍스트, 예: 연청·워시드 블랙)
  swatchId: SwatchId | null        // 13색 계열 토큰. null = '색상 미정' (세부 뉘앙스는 name)
  isBase: boolean                  // 기준 색상 여부
  images: ImageAsset[]
}
// 파생(저장 안 함): hex ← swatchId, monotone ← MONOTONE_SWATCHES 포함 여부
// AG-01 swatchSuggestions: { colorGroupId, swatchId, colorName }[]. 빈 name/swatchId만 채우며 셀러 값은 보존한다.

ImageAsset {
  id: string
  src: string
  slot: AngleSlot | null           // 기준 색상의 각도 슬롯. 추가 색상은 'Front' 고정
  ai?: boolean                     // true = AI 생성 (기본 false = 업로드)
  file?: { name: string, size: number, type: string }   // 업로드 메타
}
// 폐기: label (slot 라벨에서 파생)

Measurement {
  key: MeasurementKey              // 영문 토큰 (§4 매핑 표)
  value: number | null             // cm. AI는 절대 추정하지 않는다 — null로 시작
  unit: 'cm'
}
// 폐기: label (catalogs.measurementLabels[key]에서 파생)
```

### 3.2 Analysis — AI 제안 + 사용자 확인 속성

2026-10-17 전체 코디 계약([ADR-0022](../docs/adr/0022-full-outfit-recommendation.md)):

- `outfitSelections = {version:1, slots:{[role]:{itemId:string|null, origin:'ai'|'user'}}, setIndex:0|1|2, optional:{bag:boolean,accessory:boolean}}`. 처음 `setIndex`는 0, 소품 두 개는 false다. 역할은 상품 종류별 `inner`, `top` 또는 `bottom`, `footwear`, `bag`, `accessory`이며 역할마다 최대 하나다. `origin:'user', itemId:null`은 명시적 비움이다. 종류 전환은 불가능한 역할만 제거한다. 서버가 활성 카탈로그 ID·역할·성별·자산 소유권을 확인한다. 사용자 업로드는 기존 보완 의류 한 벌만 허용하며 `matchClothing`, `matchIds`, `LIMITS.matchClothingMax`의 한 벌 계약은 그대로다.
- `outfitRecommendation = {version:1,status:'pending'|'ready'|'fallback'|'unavailable',model:string|null,contextKey:string,sets:Array<{slots:Record<role,string|null>,reason:string}>,candidates:Record<role,MatchItem[]>,roleStatus:Record<role,status>}`. 자료가 있으면 `sets`는 서로 다른 조합 1~3벌, 전체 자료가 없으면 빈 배열이다. 후보마다 `inSets:boolean`을 갖는다. 단일 `selections` 또는 최상위 `reason` 필드는 쓰지 않는다. 설명은 세트에만 보관하며 화면에는 표시하지 않는다. 빈 역할은 `roleStatus[role]='unavailable'`이므로 그 줄을 숨긴다.
- R1 표시 그림: 코디 `candidates[role][]`와 기존 `matchClothing[]`의 `boardUrl:string|null`은 표시 전용이다. 카탈로그는 현재 `image_asset_id`를 `metadata.sourceAssetId`로 가리키는 공개·소유자 없는·삭제되지 않은 derived 자산 중 `canonicalType='catalog_cutout'`의 URL을 쓴다. 목록 조회에서 원본 ID들을 한 번에 묶어 읽고 없으면 null이다. R2부터 커스텀은 `cutoutStatus='ready'`이고 현재 grid와 썸네일에 연결된 `canonicalType='custom_board_cutout'` PNG가 있으면 그 URL, 없으면 기존 정돈 썸네일 URL이다. 처리 중·실패면 null이다. 보드 자산은 같은 사용자·프로젝트의 private derived이며 `sourceAssetId`는 현재 `image_asset_id`, `thumbnailAssetId`는 현재 `thumbnail_asset_id`, `matchingItemId`는 아이템 ID다. `sourceHash`에 정돈 지문을 기록한다. 카탈로그와 커스텀 그림은 목록당 한 번의 묶음 조회로 읽는다. Qwen 1회 호출의 투명본 또는 정돈 없는 SAM 누끼에서 알파 bbox를 자르고 긴 변을 512px 이하로 만든다. 카드 JPEG와 생성용 raw·grid의 바이트 및 자산 ID는 유지한다. 커스텀 삭제 시 보드 자산의 이전 재시도 버전까지 기존 미참조 자산 삭제 경로로 회수한다. 프론트 우선순위는 `boardUrl`, `thumb`, `thumbnailUrl`, `imageUrl`이다. 생성용 원본·추천 판단용 썸네일·선택 저장 계약은 바꾸지 않으며, 표시 URL만 달라져도 추천 재사용 키와 유료 호출 수는 바뀌지 않는다. 저장된 분석·추천 조회, 분석 저장 응답, 공개 상태 조회도 현재 표시 URL을 갱신한다.
- A2 내부 저장은 `outfitRecommendation.sets[].concept`(48자 이내 영문 키워드)을 보존한다. 서버 캐시·서명된 승격 handoff 내부에도 보존하되 위 화면용 세트 응답에는 포함하지 않는다. 과거 데이터에 concept이 없어도 읽을 수 있으며 클라이언트 서비스 계약은 바꾸지 않는다.
- `OUTFIT_RECOMMENDATION` 기본값은 off다. off면 유료 호출 없이 결정적 폴백만 만든다. AI·폴백 공통 후보 수는 `OUTFIT_CANDIDATES_PER_ROLE` 기본 6(2~8), 같은 색 묶음 최대 2개, 스타일 중복 감점, 사용자 고정 아이템 우선이며 결정적이다. `candidates` 목록은 전체 유효 후보 그대로다. 폴백은 앞선 세트와 달라지는 역할 수를 우선해 최대 세 벌을 만든다. v2 모델 응답에서 복수 후보 역할의 선택이 전부 같은 뒤 세트와 concept 중복 세트는 버리며 한 벌 이상 남으면 ready다. 상품 판단은 원본 Front 최대 변 512px, 후보는 정돈 썸네일 최대 변 256px를 쓴다. 재사용 키에 프롬프트 v2·후보 수·유료 스위치 상태를 포함한다.
- 분석 결과를 확정 저장하고 commit/lease 성공을 확인한 직후 서버가 추천을 시작한다. 공개 분석은 core 결과가 준비된 뒤 동일한 공개 rate limit 안에서 시작한다. Gemini 3.8 Flash, thinking low, 응답 최대 4096 tokens, 1회 요청에서 세 벌을 받는다. 전체 시간 제한 `OUTFIT_TIMEOUT_SECONDS`(기본 12초, 설정 1~30초)은 동시 실행 대기도 포함한다. 사진·종류·하위분류·색·스타일·성별·핏·소재·사용자 선택·카탈로그(모델 입력인 name·fit·length 포함)·모델·프롬프트 버전 키로 같은 프로세스의 진행 요청을 합치고 120초 결과를 재사용한다. 저장된 동일 문맥의 추천은 캐시가 사라져도 재사용한다.
- `pending`은 다양성 추리기로 고른 결정적 폴백 `sets`, `fallbackReady:true`, `pendingSince`(Unix seconds)를 즉시 포함한다. 시간 제한을 지난 pending 조회는 기존 세트를 `fallback`으로 반환하며 모델을 재시작하지 않는다. 조회와 화면 진입은 유료 호출을 만들지 않는다. 늦은 결과는 추천 정보만 바꾸며 저장된 선택과 토글을 덮지 않는다.
- `GET /v1/projects/{id}/analysis/outfit-recommendations`는 저장 상태 조회다. `GET .../outfit-candidates`는 `candidates`와 `roleStatus`를 반환한다. 명시적 문맥 변경만 `POST .../outfit-recommendations`로 시작하며 pending을 저장한 뒤 뒤에서 실행한다. 일반 선택 PATCH와 동일 문맥 재저장은 추천을 시작하지 않는다. 승격 저장은 서비스 옵션 `{recommendOutfit:false}`를 전달해 재시도에서도 새 추천을 시작하지 않는다. `outfitRecommendation`과 handoff는 서버 소유이고 클라이언트 URL·설명을 신뢰하지 않는다.
- 공개 조회는 `POST /v1/public/outfit-recommendations/status`의 `{handoff}` 서명 capability를 사용한다. 단순 `contextKey`로 조회할 수 없다. 시작은 별도 multipart `POST /v1/public/outfit-recommendations`다. 캐시가 없으면 서명된 문맥과 현재 공용 카탈로그로 무료 폴백을 반환한다. 로그인 승격은 사진 바이트 바인딩·알려진 상품 문맥·모든 세트 및 선택 ID를 다시 검증하고 URL을 현재 행으로 복원한다. 진행 중 승격은 이미 시작된 추천 완료만 구독한다.
- 명시적 세트 적용은 PATCH의 일회성 `outfitSetIndex`(저장하지 않음)로 서버의 현재 `sets[index]`를 적용한다. 직접 고른 non-null 칸도 AI 세트로 바꾸지만 user-null은 보존한다. 꺼진 소품의 슬롯 기억값과 꺼짐 상태도 보존한다. 프론트는 `applyOutfitSet`을 통해 같은 변경을 먼저 표시한다. 새 문맥에서 첫 세트를 적용할 때는 `setIndex=0`으로 맞추고, 동일 문맥의 늦은 응답에서는 현재 번호를 유지한다. `optional`을 켤 때 기존 슬롯이 있으면 기억한 것을, 없으면 현재 세트 값을 사용한다. 끌 때 ID는 기억한다.
- 정규화 지점은 서버 `services/outfit.py`의 `normalize_selections`, `apply_set`, `toggle_optional`, `selection_snapshot`이며 프론트 대응은 `src/lib/outfitSelections.js`다. `generation_snapshot`은 꺼진 소품을 먼저 제거한 뒤 현재 DB ID를 검증한다. `outfitSelectionsSnapshot={version:1,slots:...}`은 실제 생성 역할만 가진다(`setIndex`, `optional`은 저장하지 않음). 실제 참조 `{role,itemId,assetId,imageSha256}`를 `outfitReferenceAssets`로 기록하고 재시도·비슷한 컷·열림 편집은 원본 recipe를 따른다. 제품 단독컷은 코디 참조를 받지 않는다.

B 화면 작업용 목 사용법(목 모드의 `api.setOutfitScenario` 또는 `src/mock/api.js` API):

```js
api.setOutfitScenario({ status: 'pending', settleTo: 'ready', delayMs: 2500 });
// 다음 analyzeProduct / recommendOutfit은 폴백 세 벌과 pending을 반환한다.
// getOutfitRecommendation은 2.5초 뒤 ready를 반환하며 현재 선택은 유지한다.
api.setOutfitScenario({ status: 'fallback' });
api.setOutfitScenario({ status: 'unavailable' }); // 전체 빈 카탈로그
api.setOutfitScenario({ status: 'ready', unavailableRoles: ['footwear'] });
api.setOutfitScenario(null); // 기본: 기존 실제 목 의류 후보 + 나머지 빈 역할
```

각 설정은 다음 분석/명시적 추천부터 적용된다. `candidates:{bag:[MatchItem,...],...}` 옵션으로 B가 가진 실제 사진 fixture를 역할별 주입할 수 있다. 기본 목은 신발·가방 등의 가짜 사진을 만들지 않는다. `pending`만 주고 `settleTo`를 생략하면 지속 pending을 재현한다. 세트는 후보 순위 조합으로 최대 세 개이며 후보의 `inSets`와 역할 상태는 자동 계산한다.

```ts
Analysis {
  projectId: string
  suggestedName: string
  subCategory: SubCategory | null  // 영문 토큰화 (§4). dress는 null
  customCategory: string | null    // enum 밖 의류의 자유 명칭(한국어, ≤20자) — AI 추측 + 사용자 주관식 수정 (2026-07-13)
  targetGenders: Gender[]          // UI 단일 선택 — 1-element 배열. dress는 ['women'] 고정
  fit: Fit
  materials: Material[]            // Material { name: string(자유 텍스트), ratio: number(%) }
  sellingPoints: string[]          // 자유 텍스트, max LIMITS.sellingPointMax(5)
  aiSuggestedPoints: string[]      // max LIMITS.aiSuggestedPointMax(2)
  featureCopy?: { point: string, desc: string }[] // 판매 특징 설명. 구 상세 카드의 동일명 fallback도 유지한다.
  partDescriptionCopy?: { point: string, desc: string }[] // 실제 상세 부위 카드 설명. detailTarget 후보 label 또는 wornCrop.label과 일치시킨다.
  mdComment: string                // 셀러 직접 입력 선택 메모. 기본값 ''. 앞뒤 공백 제거 후 최대 300자
  selectedModelId: string          // catalogs.models 참조
  matchCandidates: MatchClothing[] // AI가 제안한 매칭 의류 후보
  matchSelections: { clothingId: string, role: 'main' | 'sub' }[]   // max 2
  locked: boolean
  frontClosure?: FrontClosure       // AG-01이 사진에서 판정한 앞 여밈. 서버 소유, 클라이언트 PATCH 금지. 공개 분석은 기존 상품 증거 인계 봉투에 함께 서명해 원본 검증 후 저장한다. 없으면 판단 불가로 읽는다(ADR-0018)
  garmentColorEvidence?: GarmentColorSummary // 서버가 사진에서 측정한 공개 요약. 콘티보드 이탈 시 필요할 때만 생성하고, 없으면 상세페이지 생성이 한 번 더 잰다. 클라이언트 PATCH 금지
  pageStyle?: PageStyle            // 상세페이지 템플릿과 글자 스타일, 페이지 색감. 아래 PageStyle 규칙
}

PageStyle {
  version: 1
  templateId: PageTemplateId       // 'emo'(감성형) | 'show'(쇼룸형)
  fontStyle: FontStyle             // 카탈로그 fontStyles의 키. 현재 template | plainGothic | serif | boldGothic
  colorMode: PageColorMode         // 'mono'(기본) | 'point'(의류 색감)
  catalogVersion: string           // 카탈로그 catalogVersion과 같은 값만 받는다
  updatedAt?: string               // 시간대가 붙은 ISO 8601. 마지막 스타일 조회의 정렬 기준
}
// 기본값 = { version: 1, templateId: 'emo', fontStyle: 'template', colorMode: 'mono', catalogVersion: <현재 카탈로그> }
// 위 여섯 키 밖의 키는 받지 않는다.
// fit·clothingType은 필수(null 불가) — 분석 폼에서 해제 불가 칩(PRD §6.3).
// subCategory는 원피스에서 null, targetGenders는 일반 카테고리에서 비울 수 있으나 dress는 ['women']으로 정규화.

FrontClosure {                     // ADR-0018
  version: 1
  kind: 'zipper' | 'button' | 'snap' | 'belt' | 'none' | 'unknown'
  range: 'graded' | 'binary' | null // graded=다섯 칸, binary=전체 닫힘·전체 열림 두 칸, null=kind none
  secondary: 'zipper' | 'button' | 'snap' | 'belt' | null  // 함께 있는 여밈. kind와 같을 수 없음
  evidenceSlots: AngleSlot[]        // 여밈이 보인 사진 칸. unknown이면 빈 배열
  note: string | null               // 내부 기록용 짧은 영어 메모(≤80자). 화면과 생성 지시에 쓰지 않는다
}
// 서버 고정 규칙: zipper·unknown → graded, belt → binary, none → null, button·snap → 모델 판정(없으면 graded).
// none은 양쪽 앞 가장자리가 다 보이고 여밈 부품이 전혀 없을 때만. 형식 오류·누락·예전 분석은 unknown/graded.
// 화면: none이면 열림 항목을 설명 없이 숨김, binary면 두 칸, 그 밖에는 다섯 칸.
// 생성 직전 서버 값 맞춤: none → open. binary → closed·openToChest·partial은 closed, openToWaist·open은 open.
// 여밈 종류는 생성 지시 문구에 넣지 않는다. 상품 사진이 정본이다.

MatchClothing {
  id: string
  name: string
  thumb: string
  isCustom: boolean                // 프로젝트 전용 업로드 의류 여부
  isCompatible: boolean            // 현재 상품의 보완 타입과 일치하는지
}
// 폐기: Analysis.clothingType / measurements / measurementsUnknown (Product 소유),
//       Analysis.models (catalogs.models 참조), MatchClothing.selected / selOrder (matchSelections로 분리),
//       Analysis.washCare (세탁 안내는 분석이 아니라 에디터 자동 블록 — M-02 규칙 기반 생성, PRD §10.14)
```

`mdComment`는 감성형과 쇼룸형 모두 상품 소개 본문 칸(`source='seller+ai'`)에 들어가는 셀러 글이다(2026-11-13 오너 결정, ADR-0026 18항). 값이 공백만이 아니면 AI가 실제 쇼핑몰 상세페이지의 MD 코멘트처럼 다듬어 그 칸에 넣는다. 다듬는 말투와 형식은 카피라이팅 작업이 정한다. 다듬기가 붙기 전에는 템플릿 조립기가 저장된 값을 그대로 넣는다(앞뒤 공백은 저장할 때 지운다, `page_template_assembler.py`의 `_seller_comment`). 글 안의 줄바꿈과 공백을 지키고 문단으로 나누지 않아 글상자 하나가 되며, 그 요소에는 `aiDraft`를 붙이지 않는다. 값이 비었거나 공백뿐이면 그 칸은 AI 초안 자리이고 요소에 `aiDraft: true`를 붙인다(§5.1). 생성 대기 화면은 셀러 원문이 든 상품 소개 본문(`aiDraft`가 없고 글이 있는 `body` 요소)을 늦게 온 카피로 덮지 않는다(`src/lib/editorWaitSkeleton.js`).
문자열이 아닌 값은 저장할 수 없다. `merge=1`에서 `null`은 기존 병합 규칙에 따라 키를 삭제한다.

`pageStyle`은 분석이 소유하는 페이지 모양 선택이다. DB 마이그레이션 없이 분석 payload 안에 둔다.

- 저장 시점: 분석 확인 화면에서 템플릿(감성형, 쇼룸형)을 누를 때마다 `saveAnalysis`의 병합 PATCH로 저장하고, 분석 확정 직전에 마지막 값을 한 번 더 맞춘다. 콘티보드의 글자 스타일과 페이지 색감 넘기기도 같은 직렬 저장 경로로 `fontStyle`, `colorMode`, `updatedAt`을 바꾼다. 템플릿을 새로 고르면 `fontStyle`은 카탈로그 기본값(`template`)으로 돌아가고 `colorMode`는 유지한다.
- 처음 값: 프로젝트에 유효한 `pageStyle`이 없으면 화면은 `getLastPageStyle()`(로그인 사용자가 가장 최근에 저장한 스타일. 그 값이 검증을 통과하지 못하면 카탈로그 기본값)을 처음 값으로 보여 준다. 로그인하지 않았거나 조회가 실패하면 카탈로그 기본값을 쓴다.
- 서버 검증: `version`은 정수 1, `templateId`는 `emo`나 `show`, `fontStyle`은 카탈로그 `fontStyles`의 키, `colorMode`는 `mono`나 `point`, `catalogVersion`은 현재 카탈로그 값과 같아야 하며 `updatedAt`은 시간대가 붙은 ISO 문자열이어야 한다. 하나라도 어긋나거나 모르는 키가 있으면 400 `invalid_page_style` "페이지 스타일을 다시 골라 주세요."다. `null`로 지우는 PATCH도 같은 오류다.
- 템플릿 잠금: 그 프로젝트에 마네킹컷이 있거나 마네킹 작업(`mannequin`, `mannequin_adjust`)이 한 번이라도 만들어졌으면 `templateId`를 바꾸는 저장은 409 `page_style_locked` "마네킹컷을 만든 뒤에는 템플릿을 바꿀 수 없어요."다. 마네킹 단계를 거치지 않은 프로젝트는 상세페이지 작업(`detail_page`)이 상태와 상관없이 하나라도 만들어졌으면(실패한 작업 포함) 같은 코드로 막고, 문구는 "상세페이지를 만들기 시작한 뒤에는 템플릿을 바꿀 수 없어요."다(2026-11-13 오너 결정: 콘티에서 생성으로 넘어가면 잠근다). 두 조건이 다 맞으면 마네킹 쪽 문구를 쓴다. 이전 값이 없으면 기본 템플릿(`emo`)과 비교한다. 분석에 저장된 템플릿이 없으면 새 상세페이지 작업은 생성 시작 시점의 콘티 템플릿을 `metadata.lockedTemplateId`에 기록한다. 이후 콘티 저장, 분석 스타일 변경과 재생성은 이 기록을 기준으로 확인한다. 이 기록이 없는 종전 작업은 기존 잠금 규칙을 따른다. `fontStyle`, `colorMode`, `updatedAt`만 바꾸는 저장은 잠금 뒤에도 받는다. 상세페이지 생성 시작은 작업을 만들기 직전에 프로젝트 잠금을 잡고 템플릿과 콘티가 같은지 다시 확인하므로, 작업이 만들어진 순간의 `templateId`는 저장된 콘티의 템플릿과 같다.
- 읽기 보정(화면만): 저장값이 없고 저장 콘티가 모두 taxonomyVersion 4면 콘티의 템플릿을 쓴다. 카탈로그에서 빠진 `fontStyle`만 기본 글자 스타일로 바꿔 읽고 템플릿과 색감은 유지한다. 그 밖의 무효 값은 기본값으로 읽는다. 읽을 때 저장값을 고쳐 쓰지 않는다.
- 적용: 조립기는 `templateId`의 템플릿 글꼴과 색에 `fontStyle`의 글꼴 덮어쓰기를 얹는다. `colorMode='point'`는 기준 색상 스와치가 `white`, `gray`, `black`, 미지정이거나 팔레트에 없으면 실제로는 `mono`로 조립한다. 이때도 저장값은 바꾸지 않는다.
- 콘티와의 관계: 저장 콘티의 템플릿(`pageSectionKey` 앞부분)은 `pageStyle.templateId`와 같아야 한다. 다르면 콘티 저장과 상세페이지 생성이 400 `page_style_mismatch`다(§6).

`partDescriptionCopy`는 상세페이지의 `page_part_copy.generate`가 셀러 강조 특징의 사전 미스와 실제 부위 이름을 기존 completion 한 번에 묶어 얻는다. 출력은 `featureCopy`와 `partDescriptionCopy`로 분리하고 같은 이름도 각 규칙으로 따로 쓴다. 셀러 강조 특징의 기존 사전·60자 한 문장 규칙은 그대로이며 부위 설명에는 사전을 쓰지 않는다. 부위 설명은 줄바꿈으로 나뉜 두 줄, 각 줄 공백 포함 최대 22자다. 첫 줄은 확인된 부위 생김새를 쓰고 로/라로 잇고, 둘째 줄은 입었을 때의 보이는 장점을 해요체로 맺는다. 보온·통기·방수 같은 소재 기능 단정, 금지어·과장어·숨은 제어 문자·미요청 부위·길이/형식 위반은 거른다. 새 지시문은 `server/prompts/page_part_copy_v1.txt`이며 추가 교정 호출은 없다. 기존 제공자 폴백은 유지한다.

실패나 일부 응답 누락은 저장된 설명을 지우지 않는다. 다만 새 디테일 요소 디자인(`copyPolicy: partOnly`)은 전용 설명의 비어 있지 않은 한 줄이나 두 줄만 읽고 옛 한 줄이나 featureCopy/개별 사진 카피로 대체하지 않는다. 유효한 새 설명이 없으면 기존 자리표시자가 남으며, 숨긴 디자인의 예전 설명 규칙은 유지한다. 분석 전문 저장의 필드 생략·null PATCH 보호와 두 목록의 저장 모양은 그대로이며 DB 마이그레이션이나 새 콘티 필드는 없다.

`garmentColorEvidence`는 기존 13개 색상 계열/색상명을 HEX로 바꾸는 값이 아니다. 사진 분석은 이 값을 만들지 않는다. 셀러가 콘티보드에서 마네킹컷으로 넘어갈 때 서버가 필요할 때만 만든다. 필요 조건은 `horizonBackgroundMode='garment-tone'`인 호리존 세트 멤버가 있고 그 대상 색상의 측정 결과가 없는 경우다. 해당 색상의 첫 Front와 첫 Back 원본에서 관찰기는 판매 의류 안쪽 영역만 제안하고, 서버가 사진을 먼저 줄인 뒤 sRGB로 변환한 픽셀에서 색을 측정한다. 측정이 실패해도 이동은 막지 않는다. 생성이 시작될 때 필요한 색의 측정 결과가 아직 없으면(이동 때 측정이 끝나지 않았거나 실패했거나 측정 없이 넘어간 경우) 상세페이지 생성이 한 번 더 측정한다. 끝내 근거가 없으면 배경은 기존 예시를 유지한다. 사진의 관찰색이며 실물색 보정/인증 값은 아니다.

공개 요약은 `{ version: 1, clothingType, colors: [{ colorId, status: 'ready'|'unavailable', reason, observedHex?, backgroundStatus: 'ready'|'reference', backgroundReason, backgroundPolicyVersion }] }`이다. 측정 성공과 배경 변경 가능은 별도 상태다. 프런트는 지원 세트이면 선택을 허용하고 의도만 저장한다. 요약이 없거나, 현재 의류 종류나 정책 버전과 맞지 않거나, 세트의 대상 색상 중 결과가 없는 색이 있으면 아직 측정 전으로 보고 선택을 막거나 reference로 되돌리지 않는다. 대상 색상에 앞면이나 뒷면 사진이 없으면 서버가 잴 수 없으므로 바로 선택을 끄고 이유를 보여 준다. 그 밖에는 대상 색상의 결과가 `unavailable`이거나 `backgroundStatus`가 `reference`일 때만 선택을 끄고 이유를 보여 준다.

서버 내부 계약에는 색상별 명도 범위와 원본의 SHA-256·색상 ID·Front/Back·바이트 길이가 함께 저장된다. 생성 직전에 실제 입력 바이트를 다시 대조하며, 사진/의류 종류가 바뀌거나 근거가 없으면 기존 배경으로 되돌린다. 클라이언트가 제공한 HEX, 좌표, 비공개 실행 필드는 생성 권한이 없다. 측정은 로그인한 프로젝트의 서버 저장 원본에서만 하며, 비회원 분석을 넘겨받는 경로는 없다. 사진은 먼저 줄여서 측정하지만 SHA와 길이는 원본 바이트로 계산한다. 늦게 끝난 측정은 저장 직전에 현재 사진을 다시 대조하고, 그 사이 저장된 근거가 바뀌었으면 저장하지 않아 최신 근거를 덮지 않는다. 요청한 배경 모드와 실제 적용/대체 사유는 생성 자산 기록에서 구별한다.

### 3.3 MannequinCut — 마네킹 컷 버전 (단일컷 전환)

마네킹컷은 **단일 컷 + 버전 스트립**이다. 현재 선택 핏은 `analysis.fitProfile`(→ `fit_profile_spec.md`)에 두고, 각 컷은 자신을 실제로 만든 fitProfile 스냅샷을 원본 asset metadata에 함께 보관한다.

```ts
MannequinCut {
  id: string                       // `${candidate}-${version}`
  candidate: 'A' | 'B'             // 과거 저장값 읽기 호환용. 신규 단일컷은 'A'이며 후보 선택 기능 없음
  version: number
  src: string                       // 화면/생성 입력용 resolved asset URL. 톤 조정본이 있으면 최신 mannequinToneAdjusted asset, 없으면 원본 컷 asset
  baseFit: Fit                     // 생성 시 핏 (구 fitLabel '정핏'/'슬림핏')
  fitAdjust: AdjustFit | null      // 과거 저장값 읽기 전용. 현재 핏은 FitProfile이 소유
  lengthAdjust: AdjustLength | null // 과거 저장값 읽기 전용
  matchAdjust: {
    clothingId: string
    fitAdjust: AdjustFit | null
    lengthAdjust: AdjustLength | null
  } | null                         // 과거 저장값 읽기 전용. 현재 매칭 핏은 fitProfile.matchCut이 소유
  fitProfile: FitProfile | null    // 이 버전을 만든 핏. null은 "사진 그대로"
  fitProfileRecorded: boolean      // false면 스냅샷이 없던 구버전 컷이며 analysis.fitProfile 폴백
}
// 폐기: selected (선택은 project.selectedMannequinId가 소유),
//       fitLabel / lengthLabel / matchName / matchFit / matchLength / matchLabel (전부 파생)
```

`MannequinCut.id`는 원본 컷 신원이다. 톤 에디터가 적용된 뒤에도 `selectedMannequinId`는
같은 컷 id를 유지하고, 서버는 `assets.metadata.type='mannequinToneAdjusted'`와
`metadata.sourceCutId=MannequinCut.id`가 일치하는 최신 자산을 `src`로 해석한다.
상세페이지 생성과 에디터 새 이미지 생성도 이 resolved asset을 기준 마네킹 이미지로 사용한다.

### 3.4 StoryboardBlock — 콘티보드 블록

콘티는 `taxonomyVersion: 4` 블록의 배열이다. 배열 순서가 곧 페이지 순서이고, `pageSectionKey`가 같은 블록이 이어진 한 덩어리(run)가 섹션 하나다. 섹션 종류, 섹션 디자인, 사진 칸은 페이지 템플릿 카탈로그(§5)가 정한다. 서버 정규화는 `server/app/agents/content_roles.py`, 화면 검증은 `src/lib/pageTemplates/validateStoryboard.js`가 같은 규칙을 맞춘다.

```ts
StoryboardBlock {
  id: string                       // 콘티 안에서 유일, 빈 값 불가
  taxonomyVersion: 4               // 이 값만 저장하고 생성한다
  // 섹션 소속. 같은 run 안에서는 아래 다섯 값이 모두 같다
  sectionId: string                // 같은 run의 블록이 공유. 하나의 sectionId는 하나의 pageSectionKey에만 쓴다
  pageSectionKey: string           // `${templateId}.${instanceKey}`. 앞부분이 이 콘티의 템플릿
  pageSectionKind: PageSectionKind // 카탈로그 kinds의 키
  pageSectionUsage?: PageSectionUsage | null // wear에만 'styling'(일상 착용컷) 또는 'fit'(스튜디오 착용컷). 다른 종류는 없음
  pageDesignId: string             // 카탈로그 designs의 ID. 그 디자인의 kind가 pageSectionKind와 같아야 한다
  sectionTitle?: string            // 표시 캐시. 카탈로그 종류 이름에서 다시 계산
  sectionLayout: 'stack'           // 템플릿 콘티에서는 늘 stack. 사진 배치는 pageDesignId가 정한다
  sectionCustom?: boolean          // 셀러가 추가하거나 바꾼 섹션인지
  title?: string                   // @deprecated 표시 캐시. 기준 데이터가 아니다
  source: BlockSource              // 'ai' | 'mine' | 'text'
  slotKey?: string | null          // `${instanceKey}.${카탈로그 slotKey}[번호]`. 카탈로그 사진 칸과 연결. text와 비슷한 컷 후속은 null
  pointItems?: PointItem[]         // points 섹션의 text 블록에만
  // 아래는 source='ai'의 생성 레시피
  contentRole: ContentRole | null  // 시스템 배정 내부값. mine은 'custom', text는 null
  sectionRole?: StoryboardSectionRole | null // contentRole에서 파생하는 내부값. mine과 text는 null. 화면의 섹션이 아니다
  cutType: CutType | null          // 사진 칸 recipe의 allowedCutTypes 안에서만. mine과 text는 null
  shot?: ShotType | ProductShotType // 사진 칸 recipe의 allowedShots 안에서만
  direction?: Direction | ProductDirection | null   // 착용컷은 생성예시에서 파생(ADR-0015). 제품컷은 셀러가 고른 원본 사진의 면이고 대상이 있는 디테일은 대상의 원본 면이다. 예시는 제품컷 방향을 바꾸지 않는다(ADR-0008 2026-10-10 개정)
  sideStyle?: 'profile' | 'threeQuarter' | null // direction='side'일 때만. 그 밖에는 null
  productPresentation?: ProductPresentation // product/ghost 전용. 미지정은 기존 예시 주도 표현 유지(ADR-0024)
  detailTargetId?: string | null   // product/detail 촬영 대상. 서버 소유 분석 후보 ID만 선택; 좌표·사실은 서버에서 원본 검증 후 해석
  detailTargetOrigin?: 'auto' | 'user' // 추천 생성과 수동 선택/해제 구별. 사용자 편집을 재추천으로 덮지 않음
  outerClosureState?: OuterClosureState | null       // 아우터 착용컷 전용. 누락·모르는 값은 open (ADR-0018)
  colorId?: string                 // ColorGroup.id (단수 — 컬러별 컷은 블록을 색상마다 분리)
  matchIds: string[]               // 매칭 의류 후보 id (UI는 최대 1개, 배열 shape은 구 저장분 호환용)
  faceExposure: FaceExposure | null // 착용컷 기본 'same', 제품컷 null
  angle: CameraAngle               // 기본 'same'
  refImages: string[]              // '내 레퍼런스' 업로드 (생성 입력에 포함) — 프로젝트 한정, 전역 저장 없음 (ADR-0004)
  similarSourceBlockId?: string    // 원본 AI 착용 카드 ID. 원본 결과로 생성하고 별도 과금한다. 원본의 섹션과 세트 소속을 상속한다 (ADR-0016)
  exampleId?: string | null        // 촬영 연출 예시 — 예시 속 옷·신발·액세서리는 생성 근거에서 제외 (ADR-0004)
  exampleSelectionOrigin?: 'auto' | 'user' | null  // 자동 배정인지 사용자 확정인지. source와 다른 축
  setSelectionOrigin?: 'auto' | 'user' | null      // 촬영 세트의 진입 자동 배치인지 사용자 선택인지
  spaceGroupId?: string | null     // 발행된 촬영 세트 인스턴스. `ssg1__<setId>__<instanceId>`만 허용
  spaceVariation?: 'fixed' | 'subtle'  // 발행 세트가 정한 변화 강도. 임의 입력값은 허용하지 않음
  spaceSetMemberOrder?: number     // 발행 세트 안에서 이 컷이 쓰는 멤버 순서
  horizonBackgroundMode?: 'reference' | 'garment-tone' // 호리존 세트 전용. 기본 reference; 같은 spaceGroupId의 모든 컷에 함께 저장
  refScope?: 'all' | 'pose' // 서버·클라이언트가 컷에서 도출하는 내부 값. 일반 컷·스튜디오 세트는 all, 스타일링 장소 세트만 pose.
  ownImages: string[]              // source='mine'의 직접 업로드 이미지
  thumb: string                    // 예시 썸네일 (서버/목 생성, 최종 이미지 아님)
  seedProductKey?: string          // 첫 블록에만. 화면이 기본 콘티를 만들 때의 상품·분석·템플릿 지문
  seedFingerprint?: string         // 첫 블록에만. 기본 콘티를 만든 직후의 구성 지문
}

PointItem =
  | { kind: 'detail', sourceBlockId: string, detailTargetId?: string | null }
      // 같은 points run 안의 AI 디테일 블록(shot='detail')을 가리킨다
  | { kind: 'wornCrop', sourceBlockId: string, bodyPart: WornCropBodyPart, label: string,
      cropRegion: [x: number, y: number, w: number, h: number] }
      // 착용 사진에서 따온 부위. 값은 0~1 정규화, w와 h는 0보다 크고 x+w, y+h는 1 이하
// 폐기: taxonomyVersion 2·3 저장 shape(공식 4섹션 분류와 읽을 때 자동 승격),
//       layoutRowId / layoutRowVersion(행 배치는 pageDesignId가 대신함),
//       pose / poseThumb / poseLabel(별도 포즈 선택 제거), bgThumb / bgLabel(PRD §8.5에서 배경 제거),
//       colorIds(미사용 잔재), kind(sectionRole/contentRole과 중복)
```

불변식:

- 모든 블록이 `taxonomyVersion: 4`여야 한다. 하나라도 아니면 400 `storyboard_outdated`이며 서버는 옛 콘티를 변환하지 않는다. v2, v3 자동 승격은 폐기했다. 화면은 저장된 옛 콘티를 열 때 기본 콘티를 새로 만들고, 이미 생성까지 끝난 옛 프로젝트는 빈 콘티로 연다(§6 `getStoryboard`).
- 한 콘티는 템플릿 하나만 쓴다. `pageSectionKey`의 앞부분은 카탈로그 템플릿 ID, 뒷부분은 비어 있지 않은 instanceKey다. 템플릿이 섞이면 `mixed_storyboard_templates`, 키 형식이나 `sectionId`가 어긋나면 `invalid_storyboard_section`이다.
- 같은 `pageSectionKey`는 한 번만 이어서 나온다(`noncontiguous_storyboard_section`). run 안의 `sectionId`, `pageSectionKind`, `pageSectionUsage`, `pageDesignId`는 모두 같다(`invalid_storyboard_section`).
- `first` 섹션은 맨 앞에만 둔다(`invalid_storyboard_first`). 카탈로그에서 `repeatable`인 종류는 `wear`뿐이고 나머지 종류는 콘티에 한 번만 나온다(`repeated_storyboard_section`).
- `pageDesignId`의 디자인 kind는 `pageSectionKind`와 같아야 한다(`invalid_storyboard_design`). `pageSectionUsage`는 `wear`에서만 `styling | fit`이고 다른 종류에서는 없어야 한다(`invalid_storyboard_usage`).
- `source`는 `ai | mine | text`만 받는다(`invalid_storyboard_source`). AI 블록의 `slotKey`가 그 섹션의 사진 칸과 맞지 않으면 `invalid_storyboard_slot`이다.
- 글 카드(`source='text'`)는 섹션의 글 자리를 콘티에 표시하는 블록이며 사진을 만들지 않는다. run마다 최대 하나이고, 카탈로그 `kinds[kind].storyboardTextCard`가 위치를 정한다. `none`은 글 카드가 없고, `only`는 글 카드 하나만 있고 사진이 없으며, `lead`는 글 카드 하나가 run 맨 앞에 온다. `only` 종류라도 사진이 모두 재사용 칸을 대신 채운 AI 또는 내 이미지 카드면 `lead`로 본다. 사진이 없는 `fabric` run은 `only`로 본다. 어기면 `invalid_storyboard_text_card`다.
- 글 카드는 `cutType`, `contentRole`, `sectionRole`, `slotKey`가 모두 null이고 생성 레시피 키(예시, 세트, 색상, 매칭, 참고 이미지, 방향, 샷, 썸네일 등)를 갖지 않는다. 있으면 `invalid_storyboard_text_card`다.
- `pointItems`는 `points` 섹션의 글 카드에 둔다. 서버는 글 카드의 `pointItems`만 검사한다. 글 카드가 `points` 섹션 밖에 있거나, 배열이 아니거나, 항목 종류, 원본, 부위, 잘라낼 영역이 맞지 않으면 `invalid_storyboard_point_items`다(`server/app/agents/content_roles.py`의 `validate_storyboard_v4`). 사진 카드에 붙은 `pointItems`는 서버가 검사하지 않지만, 화면은 사진 카드에 이 값을 만들지 않고 프론트 검사(`src/lib/pageTemplates/validateStoryboard.js`)는 거절한다. `detail` 항목의 원본은 같은 run의 AI 디테일 블록, `wornCrop` 항목의 원본은 `wear` 섹션의 착용 사진(샷이 `full`이나 `medium`인 AI 착용컷, 또는 이미지가 있는 내 이미지)이다. 원본이 지워진 항목은 숨은 항목으로 남고 새 생성 요청이 되지 않는다. 디테일 요소의 사진 비율 규칙은 아래 "템플릿 v4 디테일 요소" 절을 따른다.
- `source='mine'`은 서버가 `contentRole='custom'`, `cutType=null`, `sectionRole=null`로 저장하고 `exampleId`, `exampleSelectionOrigin`, `spaceGroupId`, `spaceVariation`, `spaceSetMemberOrder`, `refImages`, `refAssetIds`, `productPresentation`만 지운다(`content_roles.py`의 `_canonicalize_v4_block`). `setSelectionOrigin`, `horizonBackgroundMode`, `refScope` 같은 다른 키는 남는다. 콘티 화면에서 사진을 내 이미지로 바꿀 때는 세트 소속 키(`setSelectionOrigin`, `horizonBackgroundMode` 포함)를 함께 떼고 `refScope`를 `all`로 둔다(`src/lib/pageTemplates/photoActions.js`의 `patchStoryboardPhoto`, `src/lib/storyboardSpaceSets.js`의 `detachSpaceMembership`). 내 이미지는 컷 종류가 아니다.
- AI 블록의 `cutType`과 `shot`은 연결된 사진 칸 recipe의 `allowedCutTypes`, `allowedShots` 안에서만 고른다. 일상 착용컷(`wear`, `styling`) 칸은 `styling | horizon`을 모두 허용한다. 범위 밖 값은 서버가 recipe 기본값으로 바꾸고, 그 때문에 컷이나 샷이 바뀌면 `exampleId`와 그 출처를 비우고 원래 썸네일로 되돌린다. 착용컷은 `full | medium`, 제품컷은 `ghost | detail`만 쓴다.
- `contentRole`은 사진 칸의 `contentRole`에서 정하고, 칸이 없는 AI 블록은 `cutType`과 `shot`에서 정한다(`styling`은 `coordination`, `horizon`은 `fit`, 제품은 `productOverview` 또는 `detail`). `first` 섹션의 AI 착용 카드 중 첫 카드만 `hero`, 나머지와 비슷한 컷 후속은 `benefit`이다. `sectionRole`은 `contentRole`에서 다시 계산한다(`hero | benefit`은 `hooking`, `coordination`은 `styling`, `fit`은 `studio`, `productOverview | detail`은 `product`). 이 둘은 생성과 카피를 위한 내부값이며 콘티 화면에 섹션으로 보이지 않는다.
- 제품컷은 `faceExposure=null`, `matchIds=[]`, `outerClosureState=null`로 저장한다. 착용컷의 `faceExposure`가 없거나 모르는 값이면 칸 recipe 값 또는 `same`이다.
- 비슷한 컷 후속은 원본의 생성 설정, 세트 소속, `sectionId`, `pageSectionKey`, `pageSectionKind`, `pageSectionUsage`, `pageDesignId`를 물려받고 `slotKey=null`이다. 원본은 같은 콘티의 일반 AI 착용 카드 하나여야 한다(`invalid_similar_cut_relationship`).
- 저장할 때 `hook`, `layoutRow`, `colorway`로 시작하는 키와 `pose`, `poseLabel`, `poseThumb`, `kind`를 지우고 `sectionLayout='stack'`으로 맞춘다. 서버는 정규화한 결과를 한 번 더 검증한다.
- 저장 콘티의 템플릿은 `analysis.pageStyle.templateId`와 같아야 한다. 분석에 `pageStyle`이 있는데 다르면 400 `page_style_mismatch`다.
- `contentRole='detail'`은 셀러 원본의 확인 가능한 부분을 근거로 만든다. 별도 `Detail` 슬롯 사진은 필수 조건이 아니다. 선택한 `detailTargetId`의 원본 바인딩과 영역을 서버가 검증하며, 입력·검수는 ADR-0012, 후보 선택·발행은 ADR-0014를 따른다.
- 생성예시 갤러리는 서로 다른 `cutType`을 섞지 않는다. 예시 선택이 `cutType`을 바꾸지는 않는다.
- `sectionTitle`은 표시 캐시일 뿐 기준 데이터가 아니다. 이름은 카탈로그와 사진 칸 recipe에서 다시 계산한다.
- 공개된 정상 조합의 `source='ai'` 블록은 자동 배정 뒤 `exampleId`가 사실상 필수다. 같은 공간의 pose·방향 호환 자산이 0장인 예외에는 미배정 상태와 오류 안내를 유지하며 `all`로 강등하지 않는다.
- 자동 배정은 `exampleSelectionOrigin='auto'`, 직접 선택·다른 예시 보기·참고 범위 변경·샷/컷 변경 확정은 `'user'`로 저장한다. `exampleId`가 있는데 이 필드가 없는 과거 저장분은 사용자 선택으로 간주해 `'user'`로 정규화한다.
- 진입 시 자동 배치된 촬영 세트는 `setSelectionOrigin='auto'`, 셀러가 추가하거나 교체한 세트는 `'user'`로 저장한다. 이 값은 기본 콘티 지문에서 제외한다.
- `exampleId`가 없으면 서버는 `exampleSelectionOrigin=null`을 강제한다. 레시피 정규화·섹션 이동 등으로 `exampleId`를 지울 때 예시 썸네일도 원래 카드 썸네일로 복원해 origin·thumb 고아 값을 남기지 않는다.
- 기본 콘티 지문(`seedFingerprint`)은 `exampleSelectionOrigin='auto'`인 `exampleId`를 기본값으로 취급한다. 첫 블록의 지문이 현재 구성과 같고 `seedProductKey`(의류 종류, 하위 분류, 직접 입력한 의류 이름 `customCategory`, 색상 ID와 기준 색상 여부와 뒷면 사진 유무, 성별, 디테일 추천, 강조 특징, 템플릿. `src/lib/pageTemplates/seed.js`의 `seedProductKey`)가 바뀌었을 때만 화면이 기본 콘티를 다시 만든다. 셀러가 고친 콘티는 다시 만들지 않는다.

섹션 종류와 사진 칸(카탈로그 `page_templates_v1.json` 기준):

| pageSectionKind | 화면 이름 | 카탈로그 `menuGroup`(화면은 묶지 않음) | 반복 | 글 카드 | 생성 사진 칸(contentRole, cutType, shot) |
|---|---|---|---|---|---|
| `first` | 첫 장 | 사진 | 아니오 | none | `hero` 1장. 착용컷 `styling` 또는 `horizon`, `full` 또는 `medium`. 제품 고스트 사진을 붙이는 짝 디자인을 고르면 재사용 칸 하나가 더 생긴다 |
| `wear` + `styling` | 일상 착용컷 | 사진 | 예 | none | `coordination`. `styling` 또는 `horizon`, `full` 또는 `medium` |
| `wear` + `fit` | 스튜디오 착용컷 | 사진 | 예 | none | 회전 세트는 `fit`, 쇼룸형 기본 연속 세트는 `coordination`. `horizon`, `full` 또는 `medium`. 회전 세트는 front, side, back 세 칸 |
| `color_product` | 색상과 제품 | 옷 자세히 | 아니오 | none | `productOverview`. `product`, `ghost`. 앞면은 색마다, 뒷면은 기준 색상만이며 기준 색상에 뒷면 사진이 있을 때만 |
| `fabric` | 원단 설명 | 옷 자세히 | 아니오 | none(사진 없는 옛 run은 only) | `detail` 1장. `product`, `detail`. 제품 사진을 붙이는 짝 디자인을 고르면 재사용 칸 하나가 더 생긴다 |
| `intro` | 상품 소개 | 상품 설명 | 아니오 | only | 없음 |
| `points` | 디테일 요소 | 상품 설명 | 아니오 | lead | `detail` 최대 3장. `product`, `detail`. 또는 착용 사진에서 따온 항목(`wornCrop`) |
| `for_you` | 추천 대상 | 상품 설명 | 아니오 | only | 없음 |
| `review` | 후기 | 후기와 소통 | 아니오 | lead | 배경 `coordination` 1장. `styling`, `full` 또는 `medium` |
| `faq` | 궁금한 점 | 후기와 소통 | 아니오 | lead | 배경 `coordination` 1장. `styling`, `full` 또는 `medium` |
| `sns` | SNS 화면 | 후기와 소통 | 아니오 | only | 재사용 칸 `sns1`, `sns2`. 원본이 빠지면 디자인이 쓰는 칸에만 대신 채울 카드를 만든다(아래 문단) |

어댑터와 서버는 이 표와 카탈로그 사진 칸에 맞지 않는 조합을 그대로 저장하거나 생성기에 보내지 않는다. 재사용 칸(`fill='reuse'`)은 같은 콘티의 다른 사진을 다시 쓰므로 그 자체로는 생성 컷 수와 크레딧에 들어가지 않는다. 다시 쓸 원본이 빠지면 화면이 그 칸에 대신 채울 카드를 만든다(`src/lib/pageTemplates/sectionDependencies.js`의 `reconcileSectionDependencies`). 이 카드는 바로 전에 그 칸이 다시 쓰던 원본 카드의 설정을 복제하므로, 원본이 내 이미지였으면 같은 이미지의 내 이미지 카드가 된다. 전 원본을 찾지 못하면 같은 종류 섹션의 AI 카드 설정을, 그것도 없으면 칸의 기본 레시피(제품 쪽은 앞면 고스트, 착용 쪽은 정면 전신 스타일링)를 쓴다. 예외로 첫 장의 고스트 칸은 늘 기준 색상 앞면 고스트 AI 카드로 채우고(색상과 제품 섹션의 촬영 방식을 먼저 골라야 할 때는 비워 둔다), 원단 설명의 제품 칸은 대신 채우지 않고 제품 사진이 없는 디자인으로 돌아간다. 대신 채운 카드가 AI 카드면 일반 AI 카드처럼 센다.

#### 템플릿 v4 디테일 요소

디테일 후보는 `PT-PHOTO-CARD-01`(사진과 글 칸 카드, 감성형 기본)과 `PT-TEXT-PHOTO-01`(제목, 설명, 사진, 쇼룸형 기본) 둘이다. 두 템플릿 모두 두 디자인을 고를 수 있으며 배치·꾸밈 축 없이 기존 `pageDesignId` 하나로 저장한다. PT-COLUMN-01, P06-D01, PT-COLUMN-CARD-01, PT-BELOW-CARD-01은 pickerHidden만 추가하고 기존 조립 정의를 유지한다. 텍스트 대체도 유지한다.

새 디자인 사진은 모두 가운데 기준 4:5 cover다. 2:3 사진은 높이의 5/6이 남는다. 새 따온 항목의 기존 `pointItems[].cropRegion`은 원본 비율을 반영한 정규화 4:5 영역이며, 실제 크기를 모르면 생성 착용컷의 3:4를 기준으로 한다. 원래 부위 중심을 보존하되 이미지 안으로 경계를 옮긴다. 같은 원본의 저장 영역과 undo는 그대로이며 새 원본으로 바뀔 때만 새 영역을 만든다. 이미 저장된 다른 비율 영역도 새 디자인에서만 4:5로 조립한다. 새 cover 경로는 빈칸 없이 채우기 위해 확대를 허용한다. 숨긴 자연 비율 디자인의 30% 폭·원본 1배 확대 제한은 그대로다.

카드형은 페이지 panel 색 바탕 위 흰 카드이며 부위 이름과 두 줄 설명을 가운데 정렬한다. 제목·설명·사진형은 원래 부위 이름 앞에 섹션 내부 번호만 붙여 왼쪽 정렬한다. 제목 글꼴과 굵기는 페이지 스타일을 따르고, 설명 노드의 `weight: 'light'`는 기존 에디터 `style.weight: 300`으로 조립된다. 카드형 설명에는 light 옵션을 쓰지 않는다. `trackingEm`, `lineHeight`, `numberPrefix`, `copyPolicy`, `includeLoose`, `itemGap`은 카탈로그 해석 옵션이며 새 콘티/DB 필드가 아니다. Pretendard 300은 기존 로컬 가변 WOFF2와 에디터·내려받기 font-weight 경로를 사용한다.

### 3.5 EditorBlock / Element — 에디터 캔버스

```ts
EditorBlockBase {
  id: string
  name: string                     // 표시명
  bg: string                       // hex
  bgOpacity?: number               // 0~1, 생략 시 1. 배경에만 적용(자식 요소 불투명도와 독립)
  h: number                        // 고정 높이(px, 기준 폭 1000)
  elements: Element[]              // 배열 순서 = z-order (뒤가 위)
}

type EditorBlock =
  | EditorBlockBase & {
      kind: StoryboardSectionRole
      contentRole: ContentRole
    }
  | EditorBlockBase & {             // 템플릿 페이지 조립 결과(page_template_assembler, assemble.js)
      kind: StoryboardSectionRole | 'info' // 섹션 첫 콘티 블록의 sectionRole. 첫 블록이 글 카드나 내 이미지라 비어 있으면 'info'
      pageSectionKey: string        // 콘티 섹션 키. 한 섹션이 에디터 블록 여러 개로 나뉠 수 있다
      pageSectionKind: PageSectionKind
      pageDesignId: string
      // contentRole과 infoType은 없다
    }
  | EditorBlockBase & {
      kind: 'info'
      infoType: EditorInfoType      // 일반 구매 정보·문구 블록은 종류 필수
      info?: object                 // `내용 추가` 폼 상태 정본 (infoType별 shape, §3.5.1)
    }
  | EditorBlockBase & {
      kind: AutoBlockKind
      auto: true
      info?: object                 // size/care 를 폼으로 강화한 경우에만 존재 (§3.5.1)
    }

Element (공통) {
  id: string
  type: ElementType
  x: number  y: number  w: number  h: number    // 블록 좌표계 (기준 폭 1000)
  rotate?: number                  // (-180, 180]
  opacity?: number                 // 0~1
  groupId?: string                 // 복합 오브젝트 선택·이동 묶음 ID
  libraryItemId?: string           // 배치한 추천 오브젝트 정의 ID(선택 메타데이터)
  hidden?: boolean
  locked?: boolean
}
Element (type='image') + {
  src: string | null               // null = 빈 슬롯 (프레임)
  sourceBlockId?: string            // 저장 콘티의 컷 id
  genFailed?: boolean               // ADR-0017, 생성하지 못한 빈 이미지 칸
  genFailReason?: string            // 서버 cut_failures.py의 이유 코드
  genFailRetryable?: boolean        // 다시 만들기 가능. false는 원본 컷이 먼저 필요한 칸뿐(ADR-0017 2026-10-11 개정)
  radius?: number
  frameSlot?: boolean              // true = drag/drop 교체·자르기·빼내기를 지원하는 이미지 슬롯
  cutType: CutType | null          // 생성 시 기록. null = 직접 업로드(미상)
  crop?: { ox: number, oy: number, iw: number, ih: number }   // 프레임 기준 원본 오프셋/크기
  blur?: number                    // 유한한 0~100, 기준 폭 1000 캔버스의 px. 액자 배경에만 조립 기본값 48을 넣는다.
}
Element (type='text') + {
  text: string                     // 자유 텍스트
  placeholder?: string             // text가 비었을 때 편집 화면에만 보이는 안내 문구. 정본·미리보기·내보내기에는 포함하지 않음
  style: TextStyle                 // h는 렌더 시 auto
  textSizing?: 'auto' | 'fixed'  // 수동 추가 기본 auto
  sourceBlockId?: string           // 생성 카피가 연결된 콘티 블록 ID
  copyRole?: string               // 생성·대기 화면 카피 병합 키
}
Element (type='shape') + {
  shape: ShapeId
  fill: string
  stroke: string | 'none'
  strokeWidth?: number
  radius?: number                  // rect 전용
}
Element (type='line') + {
  shape: LineId                    // 'arrow-l' | 'line' | 'arrow-r'
  stroke: string
  strokeWidth?: number
  dash: 'solid' | 'dashed' | 'dotted'
}

TextStyle {
  font: 'Pretendard' | 'Cal Sans' | 'Roboto Mono' | 'Cormorant'
  size: number  weight: number  color: string
  opacity?: number  tracking?: number  lineHeight?: number
  align?: 'left' | 'center' | 'right'
  maxLines?: number                 // 비편집 렌더 최대 줄 수. 시그니처 제품명은 2
  italic?: boolean  underline?: boolean  strike?: boolean
  list?: 'none' | 'bullet' | 'ordered'
  bg?: string | 'none'             // 하이라이트
}
```

액자 하단의 상점 이름은 연결된 몰의 `mall_connections.shop_name`에서 읽는다. 유효한 연결과 비어 있지 않은 이름 중 카페24 자사몰을 먼저, 지그재그를 다음으로 고른다. 카페24 준비 상태·refresh 만료도 연결 API와 같은 기준으로 확인한다. 회원의 `profiles.display_name`이나 `account.name`, 몰 식별자로 대체하지 않는다. 조립 입력은 JS `shopName` / Python `shop_name`의 정리된 문자열 하나이며 콘티나 DB 필드가 아니다. 액자 없는 생성·콘티에는 이 조회를 새로 만들지 않는다. 몰 연결 API가 비활성이면 워커도 상점명을 조회하지 않는다. 콘티는 기존 계정별 몰 연결 캐시와 서비스 레이어를 재사용한다. 이름 없음·조회 실패는 하단 줄 생략으로 끝나며 다른 디자인의 profile 이니셜 동작은 그대로다.

`image.blur`는 값 0을 포함해 필드가 있는 이미지 자체를 편집 가능한 액자 배경으로 구분한다. 별도 배경 표시 필드나 콘티 필드는 추가하지 않는다. 일반 이미지는 이 필드를 생략하며, 이미지 교체·저장·되돌리기는 값을 보존한다. 서버는 범위 밖·비유한 값·문자열·Boolean·null과 이미지가 아닌 요소의 blur를 `400 editor_blur_invalid`로 거절한다. 화면은 원래 crop/fit과 프레임 경계를 유지하고 가장자리 픽셀을 연장해 흐리게 한다. 블록·전체·ZIP·선택 배경 다운로드와 쇼핑몰 게시 이미지는 출력 배율만큼 흐림을 조절하고 JavaScript 픽셀 연산으로 PNG에 구워 넣는다. CSS/Canvas 필터 지원에 의존하지 않는다. 일반 사진의 원본 다운로드는 기존 원본 바이트를 유지한다.

양수 흐림의 작업 래스터는 최대 1,048,576픽셀·한 변 4,096픽셀로 제한한다. 큰 배경은 작업 배율을 낮추고 흐림 반경에도 같은 배율을 적용한 뒤 원래 이미지 상자에 채워 넣는다. 따라서 최종 페이지 크기·crop과 캔버스 기준 흐림 너비는 유지되며 배경의 세부 해상도만 제한된다. 0 흐림의 페이지 내보내기는 원본 바이트와 crop/fit을 그대로 써서 긴 배경의 픽셀 버퍼를 만들지 않는다. 선택 배경만 다운로드할 때 0 흐림이 브라우저 캔버스 한도를 넘으면 분할 페이지 다운로드를 안내한다.

빈 이미지 칸은 `genFailed`가 있거나, 페이지 생성 블록에서 `sourceBlockId`가 저장 콘티의 AI 블록을 가리키면 계획된 칸이다. 페이지 생성 블록은 `templateId`가 없고 `kind`가 사진 행이거나 `contentRole`이 `custom`이 아닌 블록과, `pageSectionKey`가 있는 템플릿 페이지 블록이다. 그 밖의 빈 사진 칸은 계획 없는 칸이며 프레임 자동 채움 뒤 지운 칸도 포함한다. `pageSectionKey`가 없는 정보 블록, 자동 블록, 숨긴 칸과 생성 중인 칸은 AI로 채우기 대상에서 뺀다(ADR-0019).

템플릿 페이지 블록은 `contentRole`이 없고 `kind`가 사진 행 종류가 아니지만, 판정 코드(`server/app/agents/context_fill.py`의 `slot_kind`, `src/lib/editorSlotKind.js`)는 공백만이 아닌 문자열 `pageSectionKey`가 있으면 페이지 생성 블록으로 본다. `kind='info'`로 조립된 섹션(후기, 궁금한 점, 디테일 요소처럼 글 카드가 맨 앞인 섹션 등)의 사진 칸도 같다. 그래서 템플릿 페이지의 빈 사진 칸은 `genFailed`가 있으면 계획된 칸, 저장 콘티의 AI 블록에 연결돼 있으면 계획된 칸, 연결이 없거나 AI가 아닌 블록에 연결돼 있으면 계획 없는 칸이다. 자동 블록 넷(`size`, `care`, `ai-notice`, `common-notice`)은 `pageSectionKey`가 있어도 대상 밖이다.

#### 3.5.1 `info` — `내용 추가` 폼 상태 (PRD §10.14)

`내용 추가`(에디터 '내용' 탭)로 만든 블록은 폼 상태를 `block.info`에 정본으로 저장하고,
`elements`는 info로부터 재생성한다(재편집 시 폼은 info를 읽고, 제출하면 elements를 통째로
교체 — 해당 블록의 수동 캔버스 수정은 대체된다). 요소는 위 primitives만 사용한다.
size/care 자동 블록은 새 블록을 만들지 않고 **제자리 강화**한다(kind·auto 유지 + info 부착).

infoType별 info shape (frontend `src/features/editor/presets/infoPresets.js`가 단일 소스):

| infoType | info |
|---|---|
| (kind='size') | `{ unit, columns: MeasurementKey[], rows: [{label, values: Record<MeasurementKey, number\|null>}], note, withDiagram, diagramSrc }` — diagramSrc = 실측도 슬롯 사진 정본(토글 off→on 에도 보존) |
| (kind='care') | `{ family, text }` — text에는 케어라벨 확인 문장이 항상 포함된다 |
| `required_notice` | `{ fields: [{key, label, value}] }` — 빈 value는 `정보 입력 필요`로 렌더 |
| `shipping_returns` | `{ sections: [{title, body}] }` |
| `header` | `{ nameKo, nameEn, eyebrow }` |
| `benefit_copy` | `{ items: [{title, desc, src}] }` (2~8개, src = 원형 사진 슬롯 이월값) |
| `faq` | `{ layout: 'cards'\|'chat', title, items: [{question, answer}] }` (2~6개) |
| `fit_guide` | `{ fits: Fit[], current: Fit\|null }` |
| `size_matrix` | `{ heights: string[], weights: string[], cells: string[][], note }` 새 표의 cells는 빈 문자열로 시작하며 빈 칸은 `—`로 표시한다. 저장된 cells는 그대로 유지한다. |
| `model_info` | `{ models: [{name, height, size, src}] }` (최대 3) — 기본값은 프로젝트가 실제 사용 중인 모델(선택 가상모델 또는 FaceMarket 실존 모델)의 이름·사진으로 프리필 |

`normalizeEditorBlockRole`은 `kind='info'` 블록을 그대로 통과시켜야 한다 — 섹션 역할
추론 대상이 아니다(재로드 시 kind가 덮이면 정보 블록이 깨진다).

이미지 슬롯을 가진 정보 블록(특징 포인트·모델 정보·사이즈 실측도)의 슬롯을 채울 때는
요소 `src` 와 `info` 정본을 **동시에** 기록한다(`applySlotFillToInfo`) — 이미지 요소의
서수(ordinal) = info 배열 인덱스. elements 재생성 시 사진은 info 에서 복원되고,
info 동기화 이전에 채워진 레거시 블록은 같은 서수끼리만 이월한다(`carrySlotImages`,
압축 채움 금지 — 사진이 다른 포인트로 이사하면 안 된다).

기본 정보 템플릿의 최초 삽입 순서는 `header` → 컷 흐름 사이의 `benefit_copy`/`size`/`care`/
`required_notice` → 문서 맨 아래 `shipping_returns`다. `shipping_returns`는 구매 결정을
마무리하는 안내이므로 새 상세페이지 첫 생성에서 상단 공지가 아니라 마지막 블록으로 둔다.

### 3.6 Wardrobe — 에디터 의류 탭

그룹 키는 표시 문자열('색상 1')이 아니라 **colorId**다. 색상 그룹 외 이미지는 `'misc'`(표시: '기타').

```ts
Wardrobe = Record<string /* colorId | 'misc' */, WardrobeImage[]>
WardrobeImage { id: string, assetId: string, src: string, ai?: boolean, cutType: CutType | null, detailReshoot?: true }
// detailReshoot: 자산에 원본 검수 레시피(detail_recipe 버전 1)가 있는 디테일컷. 현재 이미지 수정의 '같은 부위 다시 촬영' 대상. 아니면 필드를 생략한다.
```

### 3.7 GenJob / Account

```ts
GenJob {
  id?: string                      // 백엔드 도입 시 job 추적용
  status: JobStatus
  progress: number                 // 0~100
  steps?: GenStep[]                // HTTP 어댑터는 현재 단계 목록을 주지 않으며, mock/legacy에서만 제공
  composition: StoryboardSectionRole[]
}
// mock/legacy step key: info | prep | styling | horizon | product | copy | assemble
// 화면은 공식 섹션 순서인 후킹→스타일링→스튜디오→의류 확인으로 보여준다.
// HTTP 어댑터는 현재 onProgress만 전달한다. onStep 체크리스트 실배선은 TODO다.

Account { name: string, avatar: string, credits: number, plan: PlanTier }
// PlanTier 토큰: 'basic' | 'plus' | 'seller' (소문자 저장, 표시 라벨은 catalogs 파생 — §4). 가격·혜택 구분은 크레딧 정책과 함께 추후 확정.
```

---

## 4. Enum 사전

| Enum | 토큰 | 한국어 라벨 | 비고 |
|---|---|---|---|
| ClothingType | `top` `bottom` `outer` `dress` | 상의/하의/아우터/원피스 | |
| SubCategory (top) | `tshirt` `sweatshirt` `shirt` `knit` | 티셔츠/맨투맨/셔츠/니트 | ★ 한국어 값 → 토큰화 |
| SubCategory (bottom) | `cotton_pants` `training_pants` `jeans` `slacks` `skirt` | 면바지/트레이닝 팬츠/청바지/슬랙스/치마 | |
| SubCategory (outer) | `shirt` `jacket` `cardigan` `padding` `coat` | 셔츠/자켓/가디건/패딩/코트 | |
| Gender | `women` `men` | 여자/남자 | |
| Fit | `slim` `regular` `semi_over` `over` | 슬림핏/정핏/세미오버/오버핏 | |
| ComposeMode | `basic` `extended` | 기본형/확장형 | 폐기(2026-11). 화면, API, 스토어, 카탈로그에서 지웠고 DB `projects.compose_mode` 컬럼만 남는다. 서버는 읽거나 돌려주지 않고 PATCH로 와도 무시한다(§2) |
| **StoryboardSectionRole** | `hooking` `styling` `studio` `product` | 후킹/스타일링/스튜디오/의류 확인 | 템플릿 콘티에서는 `contentRole`에서 파생하는 내부값이다. 화면의 섹션은 `PageSectionKind`가 정한다. 에디터 블록 `kind`와 생성 문맥이 이 값을 읽는다 |
| **ContentRole** | `hero` `benefit` `coordination` `fit` `productOverview` `detail` `custom` | 첫 장면/핵심 장점/코디 활용/핏 확인/제품 전체/디테일/직접 구성 | 콘티에서는 비노출 자동값. 템플릿 콘티는 카탈로그 사진 칸의 값을 쓰고, 칸이 없으면 컷·샷으로 파생한다. 첫 장의 첫 AI 착용 카드만 `hero`. 에디터 새 이미지 추가에서도 컷·샷에서 파생하는 내부값 (ADR-0026) |
| **CutType** | `styling` `horizon` `product` | 스타일링컷/호리존컷/제품컷 | 생성 레시피 값. 콘티 사진 판은 사진 칸 recipe의 `allowedCutTypes` 안에서만 고르게 하며 내부 역할은 자동 결정 (ADR-0003·0004·0026) |
| **BlockSource** | `ai` `mine` `text` | AI 생성/내 이미지/글 카드 | '내 이미지'는 컷 종류가 아님. `text`는 템플릿 콘티에서 섹션의 글 자리를 표시하는 블록이며 사진을 만들지 않는다(§3.4) |
| AutoBlockKind | `size` `care` `ai-notice` `common-notice` | 사이즈/세탁/AI 생성 안내/공통 주의 | 앞 셋은 2026-06-09 결정 유지. `common-notice`는 템플릿 조립기가 맨 끝에 붙이는 한 줄 안내(카탈로그 `commonNotice`, 화면에 따라 색이 달라 보일 수 있다는 문구)이며 `info`가 없다 |
| EditorInfoType | `materials` `options` `shipping_returns` `required_notice` `benefit_copy` `fit_copy` `model_info` `fabric_properties` `color_description` `brand_story` `faq` `reviews` `related_products` `promotion` `social` `header` `fit_guide` `size_matrix` | 에디터의 구매 정보·문구·선택 콘텐츠 | `kind='info'`일 때 사용. AutoBlockKind 4종과 겹치지 않음. ★ `header`(타이포 상품명 헤더)·`fit_guide`(핏 도식 비교)·`size_matrix`(키×몸무게 추천 사이즈)는 2026-07-29 상세페이지 분석에서 승격 (§3.5.1) |
| SectionLayout | `stack` `twoColumn` `threeColumn` `grid2x2` `colorCompare` | 세로 1열/2단/3단/2×2/컬러 비교 | 템플릿 콘티는 늘 `stack`으로 저장하고 사진 배치는 `pageDesignId`가 정한다 |
| **PageTemplateId** | `emo` `show` | 감성형/쇼룸형 | `PageStyle.templateId`. 카탈로그 `templates`의 ID |
| **PageSectionKind** | `first` `wear` `color_product` `fabric` `intro` `points` `for_you` `review` `faq` `sns` | 첫 장/착용컷/색상과 제품/원단 설명/상품 소개/디테일 요소/추천 대상/후기/궁금한 점/SNS 화면 | 템플릿 콘티의 섹션 종류. 카탈로그 `kinds`가 정본. `wear`만 여러 번 넣을 수 있다 |
| **PageSectionUsage** | `styling` `fit` | 일상 착용컷/스튜디오 착용컷 | `wear` 섹션에만 쓴다 |
| **FontStyle** | `template` `plainGothic` `serif` `boldGothic` | 템플릿 기본(감성형 다정, 쇼룸형 기본)/담백 고딕/매거진/볼드 | `PageStyle.fontStyle`. 카탈로그 `fontStyles`의 키가 정본이다. 빠진 값은 읽을 때 `template`로 본다 |
| **PageColorMode** | `mono` `point` | 기본/의류 색감 | `PageStyle.colorMode`. 기준 색상이 흰색, 회색, 검정이거나 미지정이면 `point`도 `mono`로 조립 |
| StoryboardTextCardPolicy | `none` `only` `lead` | 글 카드 없음/글 카드만/글 카드가 맨 앞 | 카탈로그 `kinds[].storyboardTextCard`. §3.4 불변식 |
| WornCropBodyPart | `shoulder` `neckline` `length` `silhouette` `waistline` | 어깨선/목선/기장/실루엣/허리선 | `PointItem.kind='wornCrop'`의 부위 |
| Direction | `front` `back` `side` | 정면/뒷면/사이드 | 모델 컷용 |
| ProductDirection | `front` `back` | 앞면, 뒷면 | 고른 원본 사진의 면. 화면은 `productSourcePhotos`의 사진 고르기로 보여준다(ADR-0008 2026-10-10 개정) |
| ShotType | `full` `medium` | 풀샷/중간샷 | 서비스 착용컷은 두 값만 쓴다. 생성예시 선별 도구의 `medium_knee`는 수집 원본 `knee`·`medium`을 합치는 선별 전용 토큰이며 서비스 저장값이 아니다(ADR-0007) |
| ProductShotType | `ghost` `detail` | 고스트샷/디테일샷 | 전체컷의 내부값은 ghost이며, productPresentation 미지정 시 기본은 입은 듯한 부피, 플랫레이 생성예시를 고르면 펼친 표현·구도를 따른다. 기존 `flatlay` 입력은 `ghost`로 정규화. `detail`은 셀러 원본과 촬영 대상의 확인 가능한 부분을 생성·검수 근거로 사용하며, 별도 Detail 슬롯 사진을 필수로 요구하지 않음(ADR-0012·0014) |
| ProductPresentation | `ghost` `flat` `natural_light` | 고스트컷, 평면컷, 자연광컷 | product/ghost의 선택 촬영 방식. 명시하면 표현, 배경과 조명은 예시보다 우선한다. 원본 상품 사실과 앞뒷면은 유지한다. 평면은 정면 상품사진이며 바닥 플랫레이가 아니다. 미지정·미지값과 다른 컷에서는 키를 생략한다(ADR-0024). |
| **OuterClosureState** | `closed` `openToChest` `partial` `openToWaist` `open` | 전체 닫힘/가슴까지/반만 열림/배까지/전체 열림 | 아우터 착용컷 전용. 화면 위에서 아래 순서. 누락·모르는 값은 `open`, 그 외 컷·카테고리에서는 무시. `openToChest`·`openToWaist`는 2026-10-10 추가, `partial`은 "명치 아래까지"로 뜻을 좁힘. 두 칸 옷은 `closed`·`open`만 쓴다(ADR-0018) |
| **ProjectStatus** | `draft` `generating` `done` | 초안/생성 중/완료 | ★ 신설 |
| **PlanTier** | `basic` `plus` `seller` | Basic/Plus/Seller | ★ 신설 — 라벨 대문자, 토큰 소문자 |
| JobStatus | `idle` `running` `done` `error` | | 화면용 4값. 서버 job row는 `pending`(화면엔 진행 중) 추가, `cancelled` 없음(error 통일) — backend plan §2·§4 |
| ElementType | `image` `text` `shape` `line` | | |
| AngleSlot | `Front` `Back` `Detail` `BackDetail` | 앞면/뒷면/앞면 디테일/뒷면 디테일 | `Detail`=앞면 디테일(값 재사용, 2026-08-07 개편 — 기존 데이터 무마이그레이션 재해석) · `Fit` 폐기(실사용 0건) |
| SwatchId | `white` `gray` `black` `ivory` `beige` `brown` `purple` `red` `yellow` `green` `blue` `navy` `pink` | 13색 팔레트(purple 2026-08-15 추가) | MONOTONE_SWATCHES = white·gray·black·ivory·beige |
| **StyleTag** | `basic` `daily` `minimal` `casual` `formal` `classic` `sporty` `trendy` `street` `chic` `feminine` `lovely` `romantic` `vintage` `retro` `modern` `luxury` `preppy` `workwear` `athleisure` `cozy` `unique` `sophisticated` `y2k` | 베이식/데일리/미니멀/캐주얼/포멀/클래식/스포티/트렌디/스트릿/시크/페미닌/러블리/로맨틱/빈티지/레트로/모던/럭셔리/프레피/워크웨어/애슬레저/코지/유니크/소피스티케이티드/Y2K | ★ 닫힌 enum(24) — AG-01 `styleTags` 출력·M-01 매칭 친화도(`style_affinity`) 공통 정본. 단일 소스 = `server/app/agents/style_tags.py`. 앞 8=affinity 부트스트랩, 뒤 16=운영자 확장. 저장 안 함(중간 산출물) |
| **MeasurementKey** | `totalLength` `shoulderWidth` `chestWidth` `sleeveLength` `waistWidth` `hipWidth` `thighWidth` `rise` `hemWidth` `armhole` | 총장/어깨너비/가슴단면/소매길이/허리단면/엉덩이단면/허벅지단면/밑위/밑단단면/암홀 | ★ 한국어 키 → 토큰화 |
| **AdjustFit** | `slimmer` `looser` | 더 슬림하게/더 여유있게 | 과거 저장값 읽기 전용. 새 요청은 FitProfile 사용 |
| **AdjustLength** | `shorter` `longer` | 더 짧게/더 길게 | 과거 저장값 읽기 전용. 새 요청은 FitProfile 사용 |
| FaceExposure | `same` `show` `hide` | 동일/노출/비노출 | |
| CameraAngle | `same` `low` `high` | 동일/로우/하이 | |
| PoseId | `auto` `stand` `walk` `sit` `lean` `turn` | AI 자동/서기/걷기/앉기/기대기/돌아보기 | |
| ShapeId | `circle` `rect` `triangle` `diamond` `star` `heart` `hexagon` `bubble` | | |

measurementSchema (clothingType → MeasurementKey[]):

```
top:    totalLength, shoulderWidth, chestWidth, sleeveLength
bottom: totalLength, waistWidth, hipWidth, thighWidth, rise, hemWidth
outer:  totalLength, shoulderWidth, chestWidth, sleeveLength
dress:  totalLength, shoulderWidth, chestWidth, waistWidth, armhole, sleeveLength
```

---

## 5. 카탈로그 (catalogs)

공용 옵션 셋은 `getCatalogs()`로 받는다. 콘티의 의미 분류는 프론트 단일 소스 `src/lib/storyboardTaxonomy.js`에서 관리한다.

실제 생성예시는 상품 종류와 별도로 적용 범위를 가진다(ADR-0006).

```ts
GenerationExample {
  id: string
  thumb: string
  cutType: CutType
  gender: Gender | null                    // 착용컷은 women|men, 성별 공용 제품컷은 null
  clothingType: ClothingType              // release manifest의 sourceClothingType
  applicableClothingTypes: ClothingType[]
  shot: ShotType | ProductShotType
  mood: string | null                  // styling만
  detailSubject: '원단·봉제' | '단추·지퍼' | '포켓' | null // product detail만
  presentationMethod: 'ghost' | 'flatlay' | null          // product ghost만
  rank: number
  variants: ('all' | 'pose' | 'bg')[] // 실제 발행된 원본 variant만. thumb는 별도 필드
}
```

- `applicableClothingTypes`는 비어 있지 않고 중복이 없으며 `clothingType`을 포함한다.
- 제품 생성예시는 성별 공용이므로 `gender=null`이고, UI 성별 필터의 영향을 받지 않는다. 착용 생성예시는 `women|men`을 유지한다.
- `upper`는 `ClothingType`이 아니다. 공용 범위도 실제 상품 종류 배열로 명시한다.
- 스타일링 공용은 사람 검토를 거친 풀샷 `[top,outer]`만 허용한다. 일반 호리존 풀샷은 성별이 지원하는 전 의류, 호리존 중간샷은 상단 크롭 계열(여성 `[top,outer,dress]`, 남성 `[top,outer]`)끼리 공유한다. 하의 중간샷은 `[bottom]` 전용이다(ADR-0006).
- UI와 서버는 현재 `Product.clothingType`이 적용 목록에 있는지 검증한다. 목록은 `StoryboardBlock`에 복제하지 않고 `exampleId`가 가리키는 생성예시 정본에서 읽는다.
- `refScope`는 컷과 세트 소속에서 도출하는 내부 입력 구분이다. 사용자 선택이나 의류 적용 범위로 쓰지 않는다.
- `refScope='all'`은 장소·조명·분위기·포즈·프레이밍·구성을 함께 참고하되 컷 종류별 배경 규칙을 적용한다. `styling`은 포즈 의미와 카메라 거리·헤드룸·인물 크기, 장소 유형·시간대·큰 색감·광원 논리·전체 분위기와 톤을 유지하면서 같은 시각적 가족의 자연스러운 다른 구체 장소를 만든다. 장소 차이를 위한 구조·가구·식물·간판·설비·소품 변경 개수는 두지 않으며, 없던 요소를 억지로 추가·이동하지 않는다. `horizon`은 같거나 거의 같은 일반적인 중립 스튜디오 배경·조명·그림자를 허용하며 강제로 다른 세트를 만들지 않는다(ADR-0011).
- `product`의 `all`은 제품 단독컷 계약으로 예시의 중립 배경·프레이밍·구성을 따를 수 있으며, 알아볼 수 있는 실제 장소 변주 규칙의 대상이 아니다.
- 일반 컷은 선택한 완성 생성예시를 참고한다. 참고 범위 선택 기능은 제공하지 않는다. 스타일링 장소 세트만 내부적으로 공통 장소와 컷별 자세 자산을 나눠 쓰며, 스튜디오 세트는 완성 예시를 직접 참고한다. 이 내부 구조는 사용자가 고르는 모드가 아니다.
- 과거 일반 컷의 참고 범위 값은 현재 일반 생성예시 참고로 정규화한다. 선택 예시·기존 출력 이미지·생성 이력은 삭제하지 않는다.
- 장소 세트의 내부 자세 자산이 없으면 완성 예시로 몰래 대체하지 않는다. 일반·제품 생성예시는 완성 사진만 사용한다.
- 사용자가 고른 매칭 의류는 `styling | horizon` 모든 착용컷의 상품 고유 의류 기준이다. 판매 상품과 매칭 의류만 고유 디자인·색·구조의 근거로 쓰며, 이 둘이 제공하지 않은 코디 영역은 무지·무브랜드의 중립 기본 의류·신발로만 채울 수 있다. 제품 단독컷에는 매칭 의류를 적용하지 않는다.

- **현행 추가**: `productSourcePhotos`(제품컷 원본 사진 고르기 라벨. `{ value: 'front', slot: 'Front', label: '내 앞면 사진' }`, `{ value: 'back', slot: 'Back', label: '내 뒷면 사진' }`, 2026-10-10에 `productDirections`를 대체), `productShotTypes`, `outerClosureStates`(아우터 열림 정도 5종, 화면 위에서 아래 순서. 항목은 `{ value, label, caption }`이며 caption은 카드 표시용: 가슴까지 열림, 배까지 열림), `measurementLabels`(key를 한국어로)
- **로컬 의미 계약**: `storyboardSections`, `contentRoles`에 해당하는 값과 라벨은 `storyboardTaxonomy.js`가 제공한다. 카탈로그 응답과 중복 저장하지 않는다. `contentRoles` 라벨은 콘티보드에는 노출하지 않고 에디터의 `새 이미지 추가` 등 별도 흐름에서만 사용한다.
- **컷 종류 노출 범위**: `CutType`은 생성 레시피의 정본이다. 콘티 사진 판은 사진 칸 recipe가 허용한 값만 쉬운 라벨로 직접 고르게 하되, 별도 사용자 분류 enum·상단 탭·페이지 섹션으로 복제하지 않는다.
- **생성예시 불변 릴리스 기록(2단계 운영 적용 완료, 2026-07-20; 소비 규칙 2026-07-21 개정)**: `server/tools/release_genexamples.py`가 확정 manifest를 검증해 서버 레지스트리 v2와 위 프론트 카탈로그를 같은 릴리스에서 만든다. QC 승인 예시 192개(`all` 192·`pose` 12·`bg` 14)와 파생 thumb 192개를 R2 불변 경로 `2026-07-19-pilot-qc-01`로 발행했다. 레지스트리 v2는 원본 variant URL·thumb·적용 의류 종류·컷·샷·성별을 함께 가진다. 프론트 `MoodGuide`는 실제 v2 JSON을 소비해 현재 카드의 `cutType`·샷·상품 종류·성별로 정확히 필터링하고 페이지당 최대 6장을 노출한다. 서로 다른 착용 `cutType`을 한 갤러리에 섞지 않는다. 생성예시 원본·재생성본과 R2 릴리스 계약은 바꾸지 않는다.
- **활성 생성예시 집합(2026-07-28 당시)**: 사용자 최종 제외 16개 ID는 활성 선택 원본·서비스 자산·프론트 카탈로그·서버 레지스트리에서 제거한다. 당시 활성 선택은 **191개(착용 169·제품 22)**이고, 생성 manifest의 기대 작업 수는 **529개(`all` 191 + `pose` 169 + `bg` 169)**였다. 당시 프론트·서버 활성 레지스트리는 **176개**를 소비했다. 제외 tombstone은 재선택·재생성을 막는 비활성 차단 기록으로만 남으며 카탈로그 항목으로 반환하지 않는다. 과거 선택 207개와 불변 R2 발행 192개는 감사·롤백 역사이므로 삭제하거나 덮어쓰지 않는다.
- **아우터·원피스 개별 스타일링 보강 릴리스(2026-07-31, 이전 정본)**: 사용자 검토 52개 중 승인 43개와 수정 후 승인 2개를 합친 45개만 새 `all`로 발행하고 제외 7개는 발행하지 않았다. 기존 활성 176개를 함께 봉인한 불변 릴리스 `2026-07-31-individual-styling-01`에서 프론트·서버 활성 레지스트리는 **221개(착용 199·제품 22)**였다. R2는 `all` 221·`pose` 12·`bg` 14·결정적 WebP `thumb` 221, 총 **468개**이고 원격 key·파일 크기 전수 대조에서 누락·추가·불일치가 모두 0이었다. 신규 45개는 여성 아우터 13(풀 8·중간 5), 남성 아우터 16(풀 8·중간 8), 여성 원피스 16(풀 9·중간 7)이며, 이 여섯 스타일링 조합을 공개 조합표에 함께 열었다.
- **상의·하의 개별 스타일링 보강 릴리스(2026-08-03, 현재 정본)**: 사용자 최종 확정 45개(여성 하의 11·여성 상의 12·남성 하의 11·남성 상의 11)를 포함한 불변 릴리스 `2026-08-03-individual-styling-01`이 현재 프론트·서버 카탈로그의 정본이다. 두 카탈로그는 같은 **254개(착용 244·제품 10)** ID를 소비한다. R2는 `all` 254·`pose` 12·`bg` 14·결정적 WebP `thumb` 254, 총 **534개**다. 2026-08-03 재검증에서 공개 R2 객체 534개를 다시 내려받아 로컬 봉인본과 SHA-256을 전수 대조했고 누락·추가·바이트 불일치가 모두 0이었다. 제작자 QC HOLD 8개는 사용자의 묶음별 최종 확정을 오너 승인으로 적용하되 원래 판정과 사유를 감사 기록에 보존한다.
- **변경**: `subCategories`를 `{ value, label }[]`로 (현재 한국어 문자열 배열)
- **유지**: `clothingTypes` `genders` `fits` `shotTypes` `angleSlots` `angleLabels` `swatchColors` `genExamples` `frames` `shapes` `lines` `fonts` `downloadOptions` `models` `creditCosts`(로컬 기본값은 `lib/limits.js`; 에디터 HTTP 가격은 서버 `credit-quote`로 확인). `genExamples`는 `cutType`·`shot`과 `applicableClothingTypes`의 현재 상품 `clothingType` 포함 여부로 필터링하고, 착용컷은 `gender`를 정확히 맞추되 제품컷의 `gender=null`은 성별 공용으로 취급한다. product detail의 `detailSubject`처럼 카드 조건보다 세밀한 축은 rank 순 라운드로빈으로 섞는다. styling의 `mood`는 자동 배정과 다시 뽑기 후보를 고를 때만 섞는다. 자동 배정과 다시 뽑기는 이 순서의 앞 6장에서 고르고, 갤러리는 조건에 맞는 예시 전부를 페이지당 최대 6장씩 보여 준다.

`creditCosts`의 에디터 키는 `{ editorImage: 19, editorEdit: 5, editorSimilar: 8 }`이다. 같은 세 키를 인증·프로젝트 소유 확인을 거친 `GET /v1/projects/{id}/credit-quote`에서 반환한다(환경변수 설정값 반영). `editorEdit`는 착용컷·업로드의 색상과 열림 정도, `editorSimilar`는 비슷한 컷, `editorImage`는 새 이미지·고스트·디테일·빈 칸 등 나머지 행동의 표시 단가다. HTTP 화면은 로컬 새 키를 합치지 않고 서버 견적에서 새 키가 빠지면 `editorImage`로 대체한다. 서버는 검증·복원한 요청으로 예약액을 결정하며 성공 확정·실패 해제는 그 예약액을 그대로 쓴다. 원장 `action_key=editorImage`와 같은 키 재요청·409 키 재사용 거절 계약은 유지한다.

같은 견적 응답은 `detailPageGenerate: { extensionModelFee, selectedModelId }`도 반환한다(2026-11-13 오너 결정: 확장 가상모델 요금은 콘티에서 생성으로 넘어갈 때 함께 차감, 상품당 한 번). `extensionModelFee`는 "이 프로젝트가 지금 상세페이지 생성을 요청하면 컷 값에 더 얹힐 확장 가상모델 요금"이다. 규칙은 다음과 같다.

- 그 프로젝트에 마네킹컷이나 마네킹 작업이 하나라도 있으면 0이다(마네킹 단계에서 이미 다뤘다. 마네킹 단계를 거친 프로젝트의 예약액과 정산액은 이전과 같다).
- 끝난 상세페이지 작업이 이 요금을 이미 차감한 프로젝트는 0이다.
- 그 밖에는 기존 요금표 그대로다(요금제와 고른 모델에 따라 19, Seller 10, Pro와 기본 모델과 실제 모델은 0).
- 견적 라우트와 상세페이지 생성 시작 라우트는 같은 함수와 같은 재료로 이 값을 계산한다. `selectedModelId` 쿼리로 모델을 덮어쓰는 동작은 이 값에도 적용한다. 기존 키(`plan`, `mannequinGenerate`, `mannequinRegenerate`, `storyboardPerCut`, 에디터 세 키)는 그대로다.
- 상세페이지 생성 시작은 AI 컷 값이 0보다 클 때만 이 요금을 컷 값에 더해 예약하고, 작업 `metadata`에 `extensionModelFee`, `plan`, `selectedModelId`를 남긴다(요금이 0이면 `metadata`는 이전과 같다). 정산은 예약 때 적어 둔 값만 믿는다. 컷 값 정산액이 0보다 크면 요금 전액을 함께 차감하고 성공 `metadata.extensionModelFeeCharged`에 실제 차감액을 남긴다. 전부 실패하면 예약 전체가 풀린다. 크레딧 내역은 `detailPageGenerate` 한 줄이다.

- **폐기 예정**: `backgrounds`(콘티 배경 제거), `extendedColorPriority`(미사용), `cutSources`
- **폐기됨(2026-10-10)**: `directions`(방향 칩, ADR-0015), `productDirections`(`productSourcePhotos`로 대체), `varyOptions`(현재 이미지 수정의 배경, 포즈, 표정 카드, ADR-0016 개정)
- **폐기됨(2026-11)**: `composeModes`(사진 양 기본형, 확장형. 템플릿 선택으로 대체)

### 5.1 페이지 템플릿 카탈로그 (`page_templates_v1.json`)

템플릿 콘티와 상세페이지 조립이 함께 쓰는 카탈로그는 `server/app/data/page_templates_v1.json` 하나다. 프론트는 이 파일을 직접 import하고(`src/lib/pageTemplates/catalog.js`), 서버는 `app.agents.page_templates`가 읽어 검증한다. API는 시작할 때 이 파일을 검증하며, 잘못된 데이터는 다른 배치로 대체하지 않고 오류로 본다. 이 카탈로그는 `getCatalogs()` 응답이 아니다.

| 키 | 내용 |
|---|---|
| `schemaVersion`, `catalogVersion` | 파일 형식 버전과 내용 버전. `PageStyle.catalogVersion`은 `catalogVersion`과 같아야 한다 |
| `canvasWidth` | 페이지 기준 폭 1000. 카탈로그 검사가 `schemaVersion: 1`과 함께 이 값을 확인한다 |
| `defaults` | `{ templateId: 'emo', fontStyle: 'template', colorMode: 'mono' }`. PageStyle 기본값 |
| `templates[]` | 고를 수 있는 템플릿 두 개, `emo`(감성형)와 `show`(쇼룸형). 각 템플릿은 글꼴(`fonts`), 색(`colors`), 간격, 기본 섹션 순서(`sections`), 섹션 추가 때 쓰는 정의(`addPlacements`), 미리보기를 가진다 |
| `templates[].sections[]` | `instanceKey`, `kind`, `designId`(기본 디자인), `usage`(wear만), `presence`(`optional`은 기본 콘티에 넣지 않음), `photoSlots[]` |
| `photoSlots[]` | `slotKey`, `fill`(`generate`는 AI 카드, `reuse`는 같은 콘티의 다른 사진 재사용), `count`, `color`(`base`, `each`, `cycle`), `requiresBack`, `contentRole`, `recipe` |
| `photoSlots[].recipe` | `cutType`, `allowedCutTypes`, `shot`, `allowedShots`, `direction`, `faceExposure`, `set`(`styling` 장소 세트, `sequence` 연속 세트, `rotation` 회전 세트, 없음), `detailPool`(`points`, `fabric`) |
| `kinds` | 섹션 종류 10개. `name`(wear는 `namesByUsage`), `menuGroup`, `repeatable`, `usages`, `storyboardTextCard`, `textSlots` |
| `menuGroups` | 섹션 추가 메뉴 묶음. `photo`(사진), `detail`(옷 자세히), `describe`(상품 설명), `social`(후기와 소통). 지금 콘티의 섹션 추가 판은 이 묶음으로 나누지 않고 한 목록으로 보인다(`StoryboardSectionPicker.jsx`) |
| `designs` | 섹션 디자인 정의. `kind`, `displayName`, `usage`, `layout`과 선택 메타데이터 `choiceAxes`(`layout`: 한 열, 2열, 큰 사진 아래 두 칸, 색 격자 등. `decoration`: 기본, 액자, 부위별 카드), `minPhotos`, `minColors`, `framePhotos`, `pickerHidden` |
| `fontStyles` | 글자 스타일 네 개. 템플릿의 `labelEn`, `titleKo` 글꼴을 덮어쓰는 값이며 `template`은 덮어쓰지 않는다 |
| `fontStyleLabels` | 글자 스타일의 화면 이름. 지금은 `plainGothic`(담백 고딕) 하나만 있고, 나머지 이름은 화면 코드가 정한다(`StoryboardThemePicker.jsx`) |
| `pendingFontBundles` | 아직 번들에 넣지 않은 글꼴 목록. 지금은 빈 배열이다 |
| `pointPalette`, `swatchHex`, `neutralColors`, `overlayColors`, `canvasColor` | 페이지 색감과 고정 색. `point` 모드는 기준 색상 스와치의 `pointPalette` 값을 쓴다 |
| `photoBudgets` | `{ detail: 4, maxExtraColors: 3 }`. `detail`은 페이지 전체 디테일 사진 한도다(`src/lib/pageTemplates/detailBudget.js`의 `DETAIL_PHOTO_CAP`). `maxExtraColors`는 코드가 읽지 않는다. 추가 색상 한도는 착용컷 사진 칸의 `count.maxExtraColors`(지금 3)가 정한다(`seed.js`의 `seedStoryboard`) |
| `firstTitleBand`, `firstTitlePlate` | 첫 장 사진 위 상품명의 위치 후보와 자동 받침 규칙(PRD §8) |
| `frameStyle` | 일상 착용컷 액자 꾸밈의 치수와 색 |
| `textMetrics`, `factPaths`, `fitLabels`, `swatchLabels`, `commonNotice`, `autoBlocks`, `editorFonts` | 조립용 글자 측정값, 글 칸이 읽는 사실 경로, 표시 이름, 자동 블록 설정 |

- `pageDesignId`는 배치와 꾸밈 조합 하나를 뜻하는 완성된 디자인 ID다. 화면은 `choiceAxes`로 배치와 디자인을 나눠 보여 주지만 저장값은 `pageDesignId` 하나다. `framePhotos` 같은 메타데이터는 콘티나 DB의 새 필드가 아니다.
- `pickerHidden`은 고르기 목록에서만 숨긴다. 저장된 숨은 디자인은 그대로 조립하고 옮기지 않는다.
- 첫 장과 원단 설명에는 제품 사진을 붙이는 짝 디자인(`P01-D09`와 `P01-D09-G`, `P01-SHOW-01`과 `P01-SHOW-01-G`, `T03-D01`과 `T03-D01-P`, `T03-D06`과 `T03-D06-P` 등)이 있다. 짝 쪽은 `pickerHidden`이다.
- 예전 `guide` 템플릿은 카탈로그 `templates`에 없어 고를 수 없다. 그 템플릿용 디자인(`CP-GUIDE-01`, `T03-GUIDE-01`)은 `pickerHidden` 디자인으로 남아 있다.

글 칸(`kinds[].textSlots`)의 `source`는 `static`(고정 문구), `fact`(상품명, 색 이름, 소재 혼용률 같은 사실), `ai`, `aiDraft`(셀러가 고칠 초안), `seller+ai`(셀러 입력이 있으면 그 글을 다듬어 쓰고, 없으면 AI 초안을 쓰는 칸. 지금은 상품 소개 본문 하나이며 §3.2 `mdComment` 참고)다. 글 칸에 연결된 글 요소는 `sourceBlockId`, `copyRole`, `textSlotVersion: 1`을 갖고, 글이 비면 편집 화면에만 보이는 `placeholder`(디자인 노드나 카탈로그의 `default`)를 단다. 동작은 다음과 같다.

- `static` 칸은 고정 문구로, `fact` 칸은 값이 있으면 그 사실로 조립할 때 채운다.
- AI가 채우는 칸은 디테일 요소의 부위 설명(`items.n.desc`, `analysis.partDescriptionCopy`를 읽는다. §3.2), 후기 초안, 상품 소개 본문이다.
- 상품 소개 본문(`seller+ai`)은 `analysis.mdComment`가 있으면 그 글을 AI가 다듬어 넣고(다듬기가 붙기 전에는 원문 그대로 하나의 글상자), 없으면 AI 초안을 넣는다(§3.2). 감성형과 쇼룸형 모두 같다. 쇼룸형 상품 소개(`T06-D01`)는 요약 판 맨 위에 영문 라벨 `Comment`와 이 본문을 둔다.
- 후기(`review`)는 셀러가 고치기 쉽도록 AI가 초안을 넣어 두고 셀러가 에디터에서 실제 후기로 고치는 칸이다(2026-10-26 오너 결정). 초안은 만들 때마다 조금씩 다르게 쓰고, 이름 칸 기본값은 `김태희`, 별점 기본값은 5개다.
- 그 밖의 `ai`, `aiDraft` 칸(첫 장 영문 상품명, 착용컷 제목과 소개, 원단 제목과 설명, 색상 소개, 상품 소개의 제목과 요약, 디테일 요소 제목과 핏 설명, 추천 대상, 질문과 답 등)은 빈 글과 안내 문구로 나간다. 옛 사진별 제목 카피 생성(AG-02 `headline`)과 검수(AG-03)는 카탈로그에 `headline` 글 칸이 없어 쓰이지 않았고 2026-11-13에 지웠다. 조립기의 `copy_results` 입력(`page_template_assembler.py`의 `ctx.copy`)은 새 카피를 위해 남겼다.
- `aiDraft: true`인 글 요소는 셀러가 글 내용을 바꾸면 그 표시를 지운다(위치, 크기, 색 같은 서식만 바꾸면 남는다, `src/features/editor/editorTextEdit.js`). 내려받기 창과 몰에 붙여넣을 코드 만들기 창은 실제로 나가는 글 중 표시가 남고 글이 빈 칸이 아닌 요소가 있으면, 그 섹션 이름을 한 줄로 보여 주고 한 번만 묻는다(`src/features/editor/editorAiDrafts.js`, 2026-11-14 오너 결정). 블록 하나 내려받기와 쇼핑몰 직접 등록은 묻지 않는다.
- 글 칸별로 무엇을 어떻게 쓸지, 어디를 강조할지 같은 카피 체계는 별도 카피라이팅 작업에서 정한다. 이 절은 그때 다시 고친다.


---

## 6. API 계약

경계는 `src/lib/api/index.js` 하나다. 모든 함수는 Promise를 반환하고, 장시간 작업은 `onProgress(0..100)` 콜백을 받는다. `onStep(steps)`는 현재 mock 상세페이지 생성에만 있으며 HTTP 배선은 TODO다. 화면은 mock/db·placeholder를 직접 import하지 않는다.

**크레딧 규약**: 크레딧을 소모하는 API는 `{ data, credits }` 봉투로 반환한다. `credits`는 차감 후 잔액이며, 화면은 이 값을 `store.syncCredits()`로 반영한다. 실패(throw) 시 차감 없음 — 환불·재시도 정책은 백엔드 시점에 확정(PRD §12.2).

**유료 job 멱등 규약**: 장시간 유료 작업(`generateMannequins`, `generateDetailPage`)은 ① 같은 프로젝트에서 **진행 중**일 때 다시 호출되면 새 작업을 시작하지 않고 진행 중인 job에 합류시키고(진행 콜백 공유, 차감 1회), ② **이미 완료**된 뒤 다시 호출되면(마네킹 후보 존재 / `project.status='done'`) 재실행·재차감 없이 기존 산출물과 현재 잔액을 반환한다, ③ **실패(error)로 끝난 뒤** 다시 호출되면 새 job을 시작한다(크레딧 재예약·재차감 대상) — 실패한 job은 합류·재사용하지 않는다. StrictMode 이중 mount, 생성 중 이탈 후 재진입, 완료 후 라우트 재방문 어느 경우도 재차감으로 이어지지 않기 위한 **서버 책임**이며, 실서버는 project별 job 레코드로 구현한다. `credits`가 델타가 아니라 **잔액**인 것도 합류·재호출 응답의 멱등성을 위해서다. (완료 후 전체 재생성은 의도적으로 플로우에 없다 — PRD §10.17, 필요 컷은 에디터에서 추가한다.)

| 함수 | 입력 | 반환 | 크레딧/비고 |
|---|---|---|---|
| `createProject()` | — | `Project` | 새 제작 시작. 구 `resetDraft()` 대체 |
| `getProject(projectId)` | | `Project` | |
| `patchProject(projectId, patch)` | 선택값 patch | `Project` | 수용 필드 **화이트리스트**: copywriting·selectedMannequinId만. 폐기한 `composeMode`, `compose_mode`는 보내도 무시하고 나머지만 반영한다. `status`는 생성 job의 부수효과로만 바꾸고 `adjustCount`는 과거 저장값 읽기 전용으로 새 요청에 받지 않는다. frontend_state_model §6 |
| `getLibrary()` | | `ProjectSummary[]` | |
| `getAccount()` | | `Account` | |
| `getCatalogs()` | | `Catalogs` | |
| `getProduct(projectId)` | | `Product` | |
| `saveProduct(projectId, patch)` | | `Product` | 실측·의류 종류 포함 (Product 소유) |
| `analyzeProduct(projectId, { onProgress })` | | `Analysis` | 실측은 항상 null로 반환 |
| `saveAnalysis(projectId, patch)` | | `Analysis` | |
| `getMatchClothing(projectId)` | | `MatchClothing[]` | 각 항목의 `isCustom`·`isCompatible`을 보존한다. 분석·마네킹·콘티 화면이 같은 매칭 선택을 읽는다. |
| `getMannequins(projectId)` | | `MannequinCut[]` | |
| `generateMannequins(projectId, { onProgress })` | | `{ data: MannequinCut[], credits }` | `mannequinGenerate` · 콘티 화면이 콘티를 불러온 뒤 자동 호출하고, 마네킹 화면 진입 때도 호출한다(진행 중이면 합류) |
| `regenerateMannequin(projectId, { fitProfile, onProgress })` | 확인 스텝에서 확정한 FitProfile(축+matchCut) | `{ data: MannequinCut[], credits }` | `mannequinGenerate` · 요청 시에는 잡 스냅샷만 남기고, 성공 종결에서 새 버전과 analysis.fitProfile을 함께 확정·자동 선택 (구 `regenerateMannequins` 대체) |
| `getStoryboard(projectId)` | | `StoryboardBlock[]` | 서버 `GET /v1/projects/{id}/storyboard`는 저장된 배열을 그대로 돌려준다(`withRevision=1`이면 `{blocks, revision}`). 화면 어댑터는 저장 콘티가 없거나 taxonomyVersion 4가 아닌 블록이 있으면 분석의 `pageStyle` 템플릿으로 기본 콘티를 새로 만든다. 옛 콘티를 새로 만든 경우에만 알림 `rebuilt`를 달고, 셀러가 확인하기 전에는 저장하지 않는다. 옛 콘티인데 이미 생성이 끝났거나 에디터 블록이 있으면 빈 배열을 돌려준다(알림 `legacyGenerated`). 고치지 않은 기본 콘티는 `seedProductKey`가 바뀌었을 때만 다시 만든다 |
| `saveStoryboard(projectId, blocks, options?)` | `options`: `{ keepalive?, autoAssignment? }` | HTTP 어댑터는 `{ blocks, revision }`, 목은 `StoryboardBlock[]` | 다음 단계로 넘어가기 전 반드시 호출. HTTP 어댑터는 이 탭이 마지막으로 읽은 revision을 늘 `baseRevision`으로 붙여 `PUT /v1/projects/{id}/storyboard?baseRevision=N`을 보내고, revision을 모르면 먼저 `withRevision=1`로 콘티를 읽는다(`src/lib/storyboardRevisionSync.js`). `keepalive`는 요청 옵션이고, `autoAssignment`는 목에서 자동 예시 배정만 한 저장을 셀러 수정으로 치지 않게 하는 표시다. 서버는 taxonomyVersion 4만 받고 §3.4 규칙으로 정규화한 뒤 저장한다. 요청 본문이 배열이 아니면 422 `validation_error`, 옛 콘티는 400 `storyboard_outdated`, 구성 위반은 400 해당 코드, 분석 템플릿과 다르면 400 `page_style_mismatch`, 다른 곳에서 먼저 저장했으면 409 `revision_conflict`(최신 콘티와 revision을 함께 줌). 서버는 `baseRevision`이 있으면 `{blocks, revision}`을, 없으면 정규화한 배열을 돌려준다 |
| `getLastPageStyle()` | | `PageStyle` | `GET /v1/page-style/last`. 로그인 사용자의 삭제되지 않은 프로젝트 중 `analysis.pageStyle`이 객체인 가장 최근 값(`updatedAt`, 프로젝트 수정 시각 순)을 검증해 돌려준다. 없거나 검증에 실패하면 카탈로그 기본값이다. 화면은 로그인하지 않았으면 서버를 부르지 않고 기본값을 쓴다. 무과금 |
| `generateDetailPage(projectId, { onProgress, onStep })` | | 완성분과 빈 칸의 `{ data: EditorBlock[], credits, creditsCharged, partial? }` | 일부 AI 컷 자리가 비면 완성분을 발행하고 그 유료 컷 값을 견적에서 뺀다(ADR-0017). 전부 실패만 미발행·예약 전액 해제. QC 미통과 디테일의 최선 생성 후보는 표시를 남겨 제공·정상 차감한다. AI 생성 컷 1~4장은 최소 5컷 값, 5장 이상은 실제 생성 컷 수 × `storyboardPerCut`. 내 이미지와 글 카드는 제외. 저장 콘티가 taxonomyVersion 4가 아니면 400 `storyboard_outdated`, 분석 템플릿과 다르면 400 `page_style_mismatch` |
| `getEditorBlocks(projectId)` | | `EditorBlock[]` | |
| `saveEditorBlocks(projectId, blocks)` | | `void` | 구현됨 — 저장 버튼 + 1.5s 디바운스 자동 저장 + 이탈 시 플러시 |
| `getWardrobe(projectId)` | | `Wardrobe` | |
| `generateImage(projectId, req)` | `NewCutRequest \| VaryRequest` (아래) | `{ data: WardrobeImage, credits, creditsCharged }` | 표시 단가는 `editorEdit` / `editorSimilar` / `editorImage`, 원장 action은 `editorImage` |
| `fillEmptySlot(projectId, req)` | `FillRequest`와 필수 `idempotencyKey`, `onJobStarted`, `onProgress` | `{ data: WardrobeImage, credits, contextFill }` | `editorImage`, 계획 없는 칸만 받음 |
| `uploadAsset(file)` | `File` | `ImageAsset` | 실서비스 계약 — mock은 `pickAnyImage()`(mock 전용 헬퍼)로 대행 |
| `download(projectId, format)` | `'long' \| 'zip'` | `{ ok }` | 에디터 실제 다운로드는 `editorExport.js`의 클라이언트 렌더가 담당한다 |

```ts
NewCutRequest {                    // AI 탭 '새 이미지 추가'
  mode: 'new'
  productPresentation?: ProductPresentation // product/ghost 전용. 예시 없이 원본에서 생성 가능(ADR-0024)
  retryBlockId?: string            // ADR-0017, 저장 콘티의 실제 빈 칸 복원과 활성 작업 합류
  detailTargetId?: string | null   // product/detail만. 서버 소유 detailRecommendations의 ID; 원본 해시·방향 검증 후 해석
  detailTargetOrigin?: 'auto' | 'user' // 콘티 추천/수동 선택 구별. 클라이언트 사실·좌표는 생성 권한이 없음
  colorId: string                  // 구 group('색상 1') 대체
  sectionRole?: StoryboardSectionRole  // 향후 섹션에 바로 삽입하는 경로용. 현재 UI는 의류 탭에 먼저 추가하므로 생략
  contentRole: ContentRole          // inferContentRole(cutType, shot)로 내부 자동 결정 — UI 선택 아님(콘티보드와 동일 규칙).
                                    // '첫 장면(hero)·핵심 장점(benefit)' 목적은 에디터 신규 생성에서 제거(2026-08-13 오너 결정)
  cutType: CutType                 // 에디터도 콘티보드처럼 컷 종류를 UI에서 직접 선택(2026-08-13 개편). 발행 예시 없는 컷 종류는 비활성. 단 명시적 productPresentation의 제품 전체컷은 네 의류 종류 모두 원본으로 생성 가능
  direction: Direction | ProductDirection | null   // 제품컷은 고른 원본 사진의 면 (ADR-0008 2026-10-10 개정)
  shot: ShotType | ProductShotType
  modelId: string
  outerClosureState?: OuterClosureState | null  // 아우터 착용 이미지 전용 — 에디터 UI에 노출(2026-08-13), 기본값 open. 칸 수 규칙은 콘티와 같다(ADR-0018)
  matchIds?: string[]              // 매칭 의류 — UI는 최대 1개(matchClothingMax, PRD §6.8), 착용컷 전용(제품컷은 빈 배열)
  exampleId?: string | null        // 촬영 연출 예시 — 예시 속 옷·신발·액세서리는 생성 근거에서 제외 (ADR-0004)
  refImages?: string[]
}
VaryRequest {                      // 캔버스에서 고른 사진으로 새 컷 만들기
  mode: 'vary'
  retryBlockId?: string            // ADR-0017, 비슷한 컷 후속 빈 칸의 원본 사진 변형
  variationMode?: 'similar' | 'color' | 'closure' // 없으면 같은 부위 다시 촬영
  source: { src: string, cutType?: CutType }   // 컷 종류는 서버 기록으로 확인
  colorId?: string                // color일 때 목표 상품 색
  outerClosureState?: OuterClosureState // closure일 때 목표 열림 정도
}
```

현재 이미지 수정은 다음 요청을 받는다(2026-10-12, ADR-0020).

- `variationMode='similar'`: 한 장은 `credit_cost_editor_similar`(현재 8, 카탈로그 `editorSimilar`)이다. 빈 칸 다시 만들기와 fill에서 내부적으로 쓰는 similar는 `editorImage`(19)를 유지한다. 현재 프로젝트 소유의 착용 사진과 직접 올린 사진을 받는다. 업로드의 컷 종류를 모르면 `styling`으로 본다. 원본 검수 레시피가 있는 디테일컷과 제품컷은 받지 않는다. 사람이 없어 계획이 실패하면 차감하지 않는다.
- `variationMode='color'`와 `variationMode='closure'`: 프로젝트의 AI 착용컷과 `assets.source='upload'` 사진을 고쳐 만든다. `Idempotency-Key`가 없으면 400 `idempotency_key_required`다. 제품컷과 디테일컷은 400 `edit_source_unsupported`이며 문구는 "이 사진에서는 색상과 열림 정도를 바꿀 수 없어요."다. 색은 상품 색 목록에 있어야 하며 400 `color_unknown`, 목표 색 사진이 없으면 409 `no_selected_color_images` "이 색 상품 사진이 없어요."다. 열림은 아우터이고 앞 여밈이 있어야 하며 아니면 400 `closure_unsupported`다. 다섯 값 중 하나를 받고 두 칸 여밈은 서버가 보정한다. 보정한 값이 현재 값과 같으면 400 `edit_same_value`다. 접수는 202 `{jobId}`이며 한 장은 `credit_cost_editor_edit`(현재 5, 카탈로그 `editorEdit`), 실패는 미차감이다. 완료는 기존 `{data, credits, creditsCharged}`이고 `data.assetId`가 포함된다.
- `variationMode` 없음: 자산에 원본 검수 레시피(`assets.metadata.detail_recipe`, 버전 1)가 있는 디테일컷만 받는다. 같은 촬영 대상과 원본 면으로 다시 촬영한다. 고스트샷, 레시피 없는 제품컷, 착용 사진은 400 `vary_unsupported`이며 크레딧을 예약하지 않는다.

변경값과 배경 참조는 없앴다. `changes`가 빈 배열이 아니거나 `refBg`, `refBgAssetId`가 있으면 400이다. 이전 클라이언트가 보내던 빈 `changes: []`는 받는다. 현재 이미지 수정은 컷 종류나 샷을 바꾸지 않으며, 디테일은 `새 이미지 추가`에서만 새로 만든다.

최초 원본과 이력은 서버가 실제 자산에서 조회한다. 클라이언트의 `_similarCut`은 버린다. 서버 payload `_similarCut={version:1,rootAssetId,sourceAssetId}`의 root는 처음 선택한 원본, source는 이번에 선택한 사진이다. 파생 결과에서 다시 생성해도 root 이미지를 사용하며, 같은 사용자·프로젝트 소유와 삭제 여부를 접수·실행 시 재검증한다.

동작 계획은 `jobs.metadata.similar_cut={version:1,rootAssetId,sourceAssetId,plan}`에 이미지 생성 전에 저장하고 결과 `assets.metadata.similar_cut`에도 보존한다. `plan`은 `actionKey, actionSummary, instructions, preserve[], avoid[]`를 기본으로 갖는다. 2026-10-15 개정(ADR-0016) 뒤 새 계획은 `sourceState`(원본 상태 장부), `choice={action, hand, target, secondary}`(서버 동작 표의 동작, 손은 `imageLeft`·`imageRight`·`both`·`none`, 보조 시선 또는 표정), `fabricResponse`(새 자세의 옷 주름 설명), `keepUnchanged[]`(서버가 장부로 만든 그대로 둘 것), `movingPart`(움직이는 부위를 역할로 부른 말)를 함께 저장한다. 이 필드가 없는 예전 계획도 이력으로 읽고 `actionKey`로만 반복을 비교한다. 반복은 같은 원본의 이전 계획과 `choice`의 동작, 손, 보조 시선 조합이 모두 같을 때로 본다. 최근 12개 완료/예약 계획을 참고하고 실패·취소 작업은 제외한다. 저장 시 원본 단위 잠금과 이력 비교, 작업 lease를 확인하며 외부 AI 호출 동안 DB 연결을 잡지 않는다. 재시도는 이미 저장된 계획을 재사용한다. 계획 실패 시 B 방식으로 전환하지 않으며 기존 실패·크레딧 반환 계약을 따른다. DB 스키마 변경은 없다.

`editor_image`는 같은 중복 방지 키의 정규화한 요청 내용이 같으면 같은 작업을 돌려준다. 서버가 붙인 `_` 시작 필드는 비교에서 제외한다. 내용이 다르면 409 `idempotency_key_reused` "같은 요청 번호로 다른 내용이 왔어요. 다시 눌러 주세요."다. `mode='fill'`은 선택한 칸으로 비교하며 다른 키로 같은 칸의 진행 중 작업에 합류하는 동작을 유지한다.

`POST /v1/projects/{projectId}/editor:asset-info`는 `{assetIds: string[]}`를 받는다. 최대 60개를 받고 중복을 제거하며 형식 오류는 400 `asset_ids_invalid`다. 프로젝트 소유를 확인하고 `asset_recipe.resolve_asset_recipes`로 읽어 200 `{items: Record<assetId, {kind, origin, cutType, colorId, realDerived, current, recipe}>}`를 돌려준다. `kind`는 `upload`, `worn`, `ghost`, `detail`, `product_unknown`, `unknown` 중 하나다. `current`는 `colorId`, `outerClosureState`, `direction`, `detailTargetId`를 담고 실제 값을 모르면 `null`이다. 업로드는 의류 색 그룹만 알 수 있다. `recipe`는 공용 생성 필드를 모두 담거나 `null`이다. 다른 프로젝트의 자산과 없는 자산은 `items`에서 빠진다.

**에러 규약**: 실패는 `Error`를 throw하고 `message`는 사용자에게 그대로 보여줄 한국어 문장이다.

### 템플릿 콘티와 페이지 스타일 오류 (2026-11)

콘티 저장(`PUT /v1/projects/{id}/storyboard`)과 상세페이지 생성 시작(`POST /v1/projects/{id}/detail-page:generate`)은 저장 콘티를 §3.4 규칙으로 검증한다. 콘티 오류의 문구는 `storyboard_outdated`만 "콘티를 다시 열어 주세요.", 아래 표에 문구를 따로 적은 코드를 뺀 나머지 구성 오류는 "콘티 구성을 다시 확인해 주세요."다.

| 코드 | 상태 | 언제 나는가 |
|---|---|---|
| `validation_error` | 422 | 콘티 저장 요청 본문이 배열이 아니다. 서버 공통 입력 검사가 막으며 문구는 "요청 형식이 올바르지 않습니다."다(`server/app/main.py`의 `validation_exception_handler`) |
| `storyboard_outdated` | 400 | 블록 중에 객체가 아니거나 taxonomyVersion 4가 아닌 것이 있다. 서버는 변환하지 않는다 |
| `invalid_storyboard_block_id` | 400 | 블록 `id`가 없거나 겹친다 |
| `invalid_storyboard_section` | 400 | `pageSectionKey` 형식, 템플릿, 종류가 카탈로그와 맞지 않거나, `sectionId`가 없거나 다른 섹션과 겹치거나, run 안의 섹션 값이 다르다 |
| `mixed_storyboard_templates` | 400 | 한 콘티에 두 템플릿의 섹션이 섞였다 |
| `noncontiguous_storyboard_section` | 400 | 같은 섹션이 떨어져서 두 번 나온다 |
| `invalid_storyboard_first` | 400 | 첫 장 섹션이 맨 앞이 아니다 |
| `repeated_storyboard_section` | 400 | 반복할 수 없는 섹션 종류가 두 번 나온다 |
| `invalid_storyboard_design` | 400 | `pageDesignId`가 없거나 다른 종류의 디자인이다 |
| `invalid_storyboard_usage` | 400 | `wear`에 `styling`, `fit`이 아닌 용도가 있거나, 다른 종류에 용도가 있다 |
| `invalid_storyboard_source` | 400 | `source`가 `ai`, `mine`, `text`가 아니다 |
| `invalid_storyboard_slot` | 400 | AI 블록의 `slotKey`가 그 섹션 사진 칸과 맞지 않는다 |
| `invalid_storyboard_text_card` | 400 | 글 카드 수나 위치가 섹션 종류 정책과 다르거나, 글 카드에 생성 레시피 키가 있다 |
| `invalid_storyboard_point_items` | 400 | 디테일 요소가 아닌 섹션의 글 카드에 `pointItems`가 있거나, 배열이 아니거나, 항목 종류, 원본, 부위, 잘라낼 영역이 맞지 않는다. 서버는 글 카드의 `pointItems`만 검사한다 |
| `unknown_cut_type` | 400 | 지원하지 않는 컷 종류. 문구 "지원하지 않는 컷 종류예요." |
| `invalid_similar_cut_relationship` | 400 | 비슷한 컷 후속의 원본이 같은 콘티의 일반 AI 착용 카드가 아니다. 문구 "비슷한 컷의 원본 사진 카드를 확인해 주세요." |
| `page_style_mismatch` | 400 | 분석의 `pageStyle.templateId`가 있고 콘티 템플릿과 다르다. 문구 "고른 템플릿으로 콘티를 다시 열어 주세요." |
| `invalid_page_style` | 400 | 분석 저장(`PATCH /v1/projects/{id}/analysis`)의 `pageStyle`이 §3.2 검증을 통과하지 못했다. 문구 "페이지 스타일을 다시 골라 주세요." |
| `page_style_locked` | 409 | 마네킹컷이나 마네킹 작업이 있는 프로젝트에서 `templateId`를 바꾸려 했다. 문구 "마네킹컷을 만든 뒤에는 템플릿을 바꿀 수 없어요." 마네킹 단계 없이 상세페이지 작업이 만들어진 프로젝트는 문구 "상세페이지를 만들기 시작한 뒤에는 템플릿을 바꿀 수 없어요." |
| `missing_front_photo` | 400 | 상세페이지 생성 시작이 새 작업을 만들려는데 기준 색상 정면 사진이 없다. 문구 "기준 색상 정면 사진을 먼저 올려주세요." 이미 도는 작업에 합류하거나 완성본을 돌려받는 요청은 막지 않는다. 작업과 예약은 남지 않는다. |

기존 생성예시와 촬영 세트 검증 오류(`unknown_example_id`, `example_not_applicable`, `example_cut_mismatch`, `example_gender_mismatch` 등)는 그대로다. 상세페이지 생성 작업도 시작할 때 저장 콘티를 같은 규칙으로 다시 정규화한다.

### 계획 없는 칸 AI로 채우기 (ADR-0019)

화면은 에디터 저장 뒤 `POST /v1/projects/{projectId}/editor:generate-image`를 호출한다. `Idempotency-Key` 헤더는 필수다. 서버는 `slot` 외의 클라이언트 컷 설정과 `_contextFill`을 읽지 않고, 저장된 칸과 주변 사진, 상품, 분석으로 접수 때 한 번 판단한다. 상세페이지 생성 중에는 접수하지 않는다.

```ts
FillRequest {
  mode: 'fill'
  slot: { blockId: string, elementId: string }
}
// 접수 202. onJobStarted(jobId, contextFill)에 그대로 전달한다.
{
  jobId: string
  contextFill: {
    slot: { blockId: string, elementId: string }
    label: string                  // 공백 포함 20자 이하, 줄표 없이 생성 중 한 줄로 표시
    path: 'new' | 'similar'
    cutType: CutType
    colorId: string
  }
}
// 완료 작업 result와 done 이벤트는 같은 봉투다.
{
  data: WardrobeImage & {
    contextFill: { slot: { blockId: string, elementId: string }, label: string, colorId: string }
  }
  credits: number
  creditsCharged: number
}
```

같은 요청 키는 같은 작업을 재생한다. 키가 달라도 같은 `blockId:elementId`의 진행 중 작업에는 합류하며 그 작업의 판단을 돌려준다. 합류한 키를 완료 뒤 재전송해도 같은 작업이다. 새 작업에만 에디터 이미지 한 장 값인 19크레딧을 예약하고 실패하면 차감하지 않는다. 작업자는 저장된 판단을 다시 하지 않는다. 서버는 에디터 블록을 수정하지 않으며 결과는 의류 탭에 저장한다. 칸에 넣기는 화면이 맡는다.

다음 거절은 작업과 예약을 만들기 전에 기존 오류 봉투의 `code`, `message`로 돌려준다.

| 상태 | code | 경우 | message |
|---|---|---|---|
| 400 | `fill_slot_invalid` | slot 형식 오류 또는 요청 키 없음 | 이 칸을 찾지 못했어요. |
| 409 | `fill_slot_not_found` | 저장된 에디터에 칸 없음 | 저장 중이에요. 잠시 뒤 다시 눌러 주세요. |
| 409 | `fill_slot_not_empty` | 이미 사진이 있음 | 이미 사진이 들어 있는 칸이에요. |
| 400 | `fill_slot_planned` | 계획된 칸 | 이 칸은 원래 콘티대로 다시 만들어요. |
| 400 | `fill_slot_unsupported` | `pageSectionKey`가 없는 정보 블록, 자동 블록, 숨긴 칸 | 이 칸은 AI로 채울 수 없어요. |
| 400 | `fill_slot_unsupported` | 생성 중인 칸 | 이 칸은 지금 만들고 있어요. |
| 409 | `page_generation_active` | 상세페이지 생성 중 | 상세페이지를 만드는 중이에요. 끝나면 눌러 주세요. |
| 409 | `fill_unavailable` | 만들 수 있는 컷 없음 | 이 칸에 만들 수 있는 컷이 없어요. |

크레딧 부족의 402와 실제 모델 생성 닫힘의 409는 기존 규약을 따른다. 템플릿 페이지 블록에서 어느 칸이 `fill_slot_planned`, `fill_slot_unsupported`가 되는지는 §3.5의 칸 판정 문단을 따른다.

판단 기록은 `jobs.payload._contextFill`, `jobs.metadata.contextFill`, `assets.metadata.context_fill`에 남긴다. 자산 기록에는 `attempt`만 뺀다.

```ts
ContextFillRecord {
  version: 1
  slot: { blockId: string, elementId: string }
  slotKey: string
  purpose: ContentRole
  rule: 'pair_similar' | 'row_direction' | 'page_direction' | 'ghost_order' | 'ghost_other_face'
      | 'detail_unused' | 'detail_to_ghost' | 'color_compare' | 'section_new'
      | 'relax_other_color' | 'relax_shot' | 'section_default'
  label: string
  path: 'new' | 'similar'
  colorId: string
  anchorAssetId: string | null
  attempt: number                 // 같은 칸의 완료된 AI로 채우기 작업 수
}
```

모든 에디터 이미지 성공 자산은 `assets.metadata.generation`에 실제 생성 설정을 남긴다. 필드 목록은 서버의 `content_roles.GENERATION_RECIPE_FIELDS` 하나이며 아래 기본 22키는 값이 없으면 `null`이다. `productPresentation`은 유효한 제품 전체컷에서만 추가하는 선택 키이며 기존 자산에는 자동으로 채우지 않는다. 열림 보정, 예시 방향 적용, 시그니처 재배정이 끝난 뒤 값을 쓴다.

```ts
generation: {
  version: 1
  origin: 'page' | 'editor_new' | 'editor_retry' | 'editor_similar' | 'editor_reshoot'
      | 'editor_color' | 'editor_closure' | 'context_fill' | 'upload'
  autoSelected: boolean            // context_fill만 true
  sourceAssetId: string | null     // 변주의 원본. 새 컷이면 null
  recipeFrom: 'self' | 'anchor'
  recipe: {
    sectionRole: StoryboardSectionRole | null
    contentRole: ContentRole | null
    cutType: CutType | null
    shot: ShotType | ProductShotType | null
    productPresentation?: ProductPresentation
    direction: Direction | ProductDirection | null
    sideStyle: string | null
    colorId: string | null
    faceExposure: FaceExposure | null
    modelId: string | null
    matchIds: string[] | null
    matchIdsOrigin: string | null
    refAssetIds: string[] | null
    refScope: RefScope | null
    exampleId: string | null
    exampleSelectionOrigin: string | null
    outerClosureState: OuterClosureState | null
    detailTargetId: string | null
    detailTargetOrigin: string | null
    spaceGroupId: string | null
    spaceVariation: string | null
    spaceSetMemberOrder: number | null
    horizonBackgroundMode: string | null
  } | null
}
```

새 컷은 `recipeFrom='self'`다. 비슷한 컷과 같은 부위 다시 촬영은 공용 `asset_recipe.resolve_asset_recipes`가 원본 설정을 찾으면 상속하고 `recipeFrom='anchor'`로 남기며, 못 찾으면 `recipe=null`이다. 직접 올린 자산은 `assets.source='upload'`로 판정하고 새 기록을 쓰지 않는다. 색상과 열림 편집은 `origin='editor_color'` 또는 `origin='editor_closure'`, `autoSelected=false`, 고른 원본의 `sourceAssetId`, `recipeFrom='anchor'`를 남긴다. 원본 레시피의 바꾼 필드만 고치며 레시피가 없으면 모든 필드를 `null`로 두고 바꾼 값만 채운다. 색상 결과의 의류 그룹은 목표 색이고 열림과 비슷한 컷은 원본의 의류 색 그룹을 물려받는다. 상세페이지 컷은 `origin='page'`와 최종 목표색 레시피를 남긴다.

#### 추가 색상 실행 기록 (ADR-0025)

`block.colorId`와 `generation.recipe.colorId`는 최종 목표색이다. 내부에서 기준색으로 렌더하더라도 이 저장값을 바꾸지 않는다. 서버의 `garment_color.plan_for`와 `render_spec`만 별도 실행색을 계산한다. 제품 단독컷은 목표색 원본에서 직접 만든다.

색 적용 결과에는 선택적으로 다음 서버 전용 실행 기록을 `assets.metadata.colorExecution`에 추가한다. 클라이언트가 이 값을 보내도 실행 권한이나 근거로 사용하지 않는다.

```ts
colorExecution: {
  version: 1
  mode: 'direct' | 'base_then_color'
  targetColorId: string | null
  baseColorId: string | null
  renderColorId: string | null
  targetEvidence: { assetId: string, sha256: string }[]
  masterAssetId: string | null
  masterSha256: string | null
  colorCheck: {
    status: 'not_run' | 'passed' | 'failed' | 'unavailable' | 'indeterminate'
    policyVersion?: string
    method?: 'ai_visual'
  }
}
```

`masterAssetId`는 같은 사용자와 프로젝트에 속하는 내부 기준색 이미지다. 의류 목록에 넣지 않고 최종 장수나 차감에 포함하지 않는다. 실행색, 목표색 근거와 검수 정책은 체크포인트의 동일성 판단에 포함한다. 색 적용 실패 시 기준색으로 성공 처리하지 않는다. `passed`는 AI 시각 판정의 통과이며 실물색 측정이나 동일 픽셀 보장이 아니다.

`targetEvidence`는 별도 색 적용에 실제로 사용한 근거 목록이며 `direct`에서는 빈 목록일 수 있다. 직접 생성의 상품 사진 근거는 기존 생성 레시피와 입력 계약을 따른다. `colorCheck`는 선택적으로 검수한 이미지의 `checkedOutputSha256`과 `scope: 'color_stage_before_neck_repair'`를 남겨, 후속 목선 보정 결과까지 같은 바이트로 검수한 것처럼 해석하지 않게 한다.

---

## § 게시(detail publication)

행 쓰기는 서버만 한다. 클라이언트 역할은 본인 행 조회만 가능하다.

| 필드(DB) | 의미 |
| --- | --- |
| `id`, `user_id`, `project_id` | 게시·소유자·프로젝트 UUID. 프로젝트 삭제 시 `project_id`만 null로 바뀌고 게시 이미지는 유지한다. |
| `version`, `status` | 프로젝트 안의 불변 순번(프로젝트와 복합 unique), `pending`·`ready`·`deleted`. |
| `preset`, `width` | `smartstore`·`coupang`·`high` 폭 프리셋과 렌더 폭(px). |
| `slice_count`, `total_bytes` | 조각 수와 전체 바이트. |
| `product_name`, `editor_revision` | 게시 시점의 상품명과 에디터 리비전. |
| `files` | `{index,r2_key,bytes,width,height,asset_id}` 배열. 조각은 `assets`에도 `source='export'`, `visibility='public'`, `metadata.publication_id`로 등록한다. |
| `expires_at` | 보관 만료 시각. 구독 중이면 null. |
| `created_at`, `ready_at`, `deleted_at` | 생성, 게시 완료, 업로드 대기 시간이 지난 뒤 이미지와 CDN 정리를 확인한 시각. 이후 재정리가 필요하면 `deleted_at`을 다시 null로 바꾼다. |
| `cleanup_after`, `cleanup_generation` | 다음 삭제 정리 시각과 정리 시도 순번. `cleanup_after`가 null이면 재확인이 끝난다. 순번이 최신인 요청만 정리 완료를 기록한다. |

상태 전이는 `pending → ready → deleted`이며 30분 동안 완료되지 않은 `pending`도 `deleted`로 정리한다. 재게시는 새 버전을 만들고 기존 게시를 바꾸지 않는다.

`deleted`이면서 `deleted_at=null`이면 이미지와 CDN 정리를 다시 확인해야 하는 삭제 의도다. 만료 정리는 게시 행을 잠근 뒤 별도 SQL 문으로 현재 프로필을 읽고 공유 잠금을 잡는다. 재구독이 그 전에 커밋됐으면 보존하고, 삭제 의도가 먼저 확정된 버전은 이후 재구독으로 복구하지 않는다. 하루 한 번 만료일을 계산할 때도 게시, 프로필 순서로 잠그고 처음 잠근 게시만 갱신한다. 저장소 정리에 실패해도 행과 삭제 의도를 유지해 재시도한다.

명시적 삭제와 탈퇴는 이미지와 CDN을 즉시 지운다. 이미 `deleted`인 게시의 반복 삭제 요청은 저장소 호출 없이 204로 응답하며, 정리 미완 건은 워커가 재시도한다.

`cleanup_after`의 첫 값은 모든 업로드 주소를 발급한 뒤의 시각에 주소 유효 시간 5분과 전송 여유 1시간을 더해 저장한다. 생성 시각은 잠금 대기보다 빠를 수 있으므로 이 계산에 쓰지 않는다. 이 시각이 지난 첫 정리는 이미지가 없어도 CDN을 다시 지운 뒤 `deleted_at`을 기록하고 하루 뒤 재확인을 한 번 예약한다. 1시간은 운영상 여유이며 저장소가 전송 완료를 보장하는 상한은 아니다. 재확인은 HEAD로 존재 여부만 보고, 이미지가 없으면 `cleanup_after`를 null로 두고 끝낸다. 이미지가 다시 생겼으면 삭제와 CDN 정리 후 다시 하루 뒤 재확인을 한 번 예약한다. 삭제 기록은 계속 보존한다.

재정리할 때는 삭제 전에 재시도 의도와 새 순번을 저장하고, 완료 시 같은 순번인지 확인한다. 다른 요청의 CDN 정리 실패를 오래된 완료 응답이 덮어쓰지 않는다. 한 번의 시간별 실행은 새 만료, 삭제 재확인, 미완료 게시를 각각 최대 100건 처리한다. 각 목록의 한도는 분리하며 삭제 재확인은 예정 시각 순으로 처리한다. 밀린 건이 많거나 저장소 오류가 지속되면 하루보다 늦어질 수 있다.

R2 키는 `pub/<user_id>/<publication_id>/<index 2자리>.jpg`이며 JPEG와 `Cache-Control: public, max-age=31536000, immutable`을 고정한다.

`GET /v1/projects/{projectId}/detail-publications`는 `{items:[...]}`를 반환한다. 각 항목은 `publicationId`, `version`, `files`, `expiresAt`, `createdAt`, `preset`, `width`, `sliceCount`, `totalBytes`와 게시 시점의 `editorRevision`(정수), `productName`(문자열)을 포함한다.

만료 규칙은 `profiles.plan`을 기준으로 한다. free가 아니면 `expires_at=null`, 처음부터 free면 `created_at+6개월`, 구독 종료를 처음 관찰한 free 게시면 관찰 시각+6개월이다. 이미 정해진 free 만료일은 유지하고 삭제 의도 확정 전에 재구독하면 null로 되돌린다. 워커는 하루 한 번 만료일을 계산하고 한 시간마다 정리한다. 탈퇴 시에는 즉시 삭제를 시도하고 실패는 재시도한다.

카페24 등록은 워커에서 게시 이미지를 카페24로 복사한다. 이 사실은 기존 게시 삭제와 만료 정책을 바꾸지 않는다.

## § 쇼핑몰 연결

`MALL_CONNECT_ENABLED` 기본값은 false이며 꺼져 있으면 `/v1/malls` 라우트와 프로필의 쇼핑몰 연결 메뉴를 제공하지 않는다. 연결 API가 꺼져 있으면 메뉴의 지그재그, 카페24, 에이블리 항목은 '준비 중'으로 비활성 표시한다. 에이블리와 스마트스토어는 `unavailable` 표시만 제공한다.

| 저장 필드 | HTTP 필드 | 의미 |
|---|---|---|
| `mall` | `mall` | `zigzag`, `ably`, `smartstore`, `cafe24` |
| `status` | `status` | 저장은 `connected`, `revoked`. 활성 연결이 없으면 응답은 `none` |
| `shop_id`, `shop_name` | `shopName`, 카페24의 `mallId` | 인증으로 조회한 스토어. 지그재그 ID는 서버에서만 사용 |
| `key_hint` | `keyHint` | Access Key 끝자리 최대 8자. 원문 전체가 될 만큼 짧으면 null |
| `access_key_enc`, `secret_key_enc` | 제공하지 않음 | 전용 `MALL_KEYS_KEK`와 `pgp_sym_encrypt`로 암호화 |
| `permissions` | `permissions` | 셀러용 권한 문구 배열 |
| `connected_at`, `last_registered_at` | `connectedAt`, `lastRegisteredAt` | ISO 8601 또는 null |
| `mall_defaults.shop_id` | 제공하지 않음 | 주소 기본값이 속한 스토어 ID |
| `mall_defaults.revision` | `addressesRevision` | 현재 몰 판매 정보와 주소 선택을 함께 관리하는 리비전 |

연결은 새 키로 스토어 조회를 마친 뒤 저장한다. 인증정보 변경은 검증이 성공한 경우에만 기존 연결을 `revoked`로 바꾸고 새 `connected` 행을 같은 트랜잭션에 만든다. 실패하면 기존 연결을 유지한다. 연결할 스토어가 `mall_defaults.shop_id`와 다르면 `addresses`를 비우고 revision을 증가시킨 뒤 스토어 ID를 갱신한다. 해당 몰의 판매 정보와 같은 스토어의 주소 선택은 유지한다. 인증정보 변경에서 기존 연결과 스토어가 다르면 400 `shop_mismatch`로 거절한다. 연결 해제와 탈퇴는 암호문을 즉시 지우고 `revoked`로 바꾸며 기본 설정을 보존한다. 응답과 로그에 키 원문을 넣지 않는다.

인증정보 변경은 외부 검증을 시작하기 전에 현재 연결 ID를 기록한다. 저장할 때 연결 해제와 같은 잠금 안에서 그 연결이 여전히 활성 상태인지 확인한다. 연결이 없거나 해제되거나 다른 연결로 교체됐다면 409 `connection_changed`로 거절하며, 늦게 끝난 변경 요청이 연결을 다시 만들지 않는다.

`GET /v1/malls`는 `{items:[...]}`다. 각 몰에 연결 정보, `addressesRevision`, 활성 연결 ID의 SHA-256인 공개 `connectionVersion`과 `registration:{status,reasonCode?}`를 제공한다. 등록 상태는 `available`, `preparing`, `reconnect_required`다. 연결됨과 등록 가능은 독립이며 Phase A의 지그재그와 에이블리 등록은 항상 preparing이다. 준비 중 이유에 비밀 설정 이름을 넣지 않는다. 지그재그 연결과 인증정보 변경의 `{accessKey,secretKey}` 입력, 앞뒤 공백 제거와 1~256자 제한, 합산 시간당 10회 제한은 유지한다.

판매 정보는 `mall_defaults`의 `(user_id, mall)`별로 따로 저장한다. 배송, 반품, 고시, 과세, 상품 출처, 발송일, 진열 방식과 주소 중 해당 몰에 허용된 필드만 읽고 쓴다. 몰 사이 값 복사나 자동 동기화는 하지 않으며 API에 shop_id를 노출하지 않는다. 판매 정보 저장에는 활성 연결이 필요하다.

| 경로 | 입력과 응답 |
|---|---|
| `GET /v1/malls/{mall}/registration-defaults` | `{revision,missingItems,...해당 몰의 허용 필드}`. 저장 행이 없으면 revision 0과 몰별 초기값 반환 |
| `PUT /v1/malls/{mall}/registration-defaults` | `{baseRevision,...해당 몰에서 변경할 필드}`. 구역 내부는 명시한 필드만 병합하고 새 revision 반환. 충돌은 409 `defaults_conflict`, 연결 없음은 409 `connection_required`, 연결 세대 변경은 409 `connection_changed`. 주소는 현재 스토어의 주소록 ID만 허용 |
| `GET /v1/malls/{mall}/registration-catalog` | `{mall,status,catalogVersion,complete,categories,priceBasis,imageRatio,capabilities,issues}`. 사용자, 몰, 연결 세대와 계약 버전별로 최대 10분 캐시. 실패와 잘린 목록은 캐시하지 않음 |
| `GET /v1/malls/zigzag/addresses` | `{id,name,address,postcode,is_default}[]`. 50개씩 skip_count로 끝까지 조회하고 ID 중복 제거 |

| 몰 | 판매 정보 허용 필드 |
|---|---|
| 카페24 | `taxType`, `disclosure.country`, `listingMode`. 과세는 A 또는 B로 변환하며 제조국은 made_in_code로 변환한다. 에디터 고시의 제조국이 우선이다. 배송, 반품, 주소와 나머지 고시 기본값은 받지 않는다 |
| 지그재그 | `shipping:{feeType,baseFee,jejuFee,isolatedFee,conditionalAmount}`, `returns:{partialFee,totalFee,exchangeFee}`, `disclosure:{country,manufacturer,asManager,phone,warrantyText}`, `taxType`, `productOrigin`, `shippingDays`, `listingMode`, `addresses.returnAddressId`. 반품지만 받고 등록 입력의 address.return_id로 보낸다. 출고지와 shipping_id는 없다 |
| 에이블리 | 준비 중. 판매 정보 입력을 받지 않으며 PUT은 400 `invalid_defaults` |

`shipping.feeType`은 FREE, CHARGED, CONDITIONAL_FREE 중 하나다. 몰별 선택은 `taxType:TAX|FREE`, `productOrigin:domestic|imported`, `shippingDays:1..30`, `listingMode:HIDDEN|ON_SALE`이며 허용된 필드의 초기값은 TAX, domestic, 2, HIDDEN이다. 금액은 Boolean을 제외한 0 이상 2,147,483,647 이하 정수이며 conditionalAmount는 1 이상이다. 무료 배송의 baseFee는 0이며 조건부 무료가 아니면 conditionalAmount를 제거한다. disclosure의 빈 문자열은 해당 필드를 지우고 null은 모든 판매 정보 필드에서 거부한다. 구역을 덜 채워도 저장 가능하며 missingItems는 `disclosure.country`, `addresses.returnAddressId` 같은 누락 필드 경로를 나타낸다. 몰별 허용 목록 밖 필드는 400 `invalid_defaults`로 거절한다. 이전 zigzag/defaults PUT은 제공하지 않는다.

`/settings/malls`는 쇼핑몰 연결 목록과 연결된 몰의 판매 정보 상태 및 이동을 보여 준다. `/settings/malls/:mall`은 카페24 또는 지그재그 한 몰의 판매 정보만 보여 주며, 지그재그 반품지도 여기서 고른다. 연결되지 않은 몰의 상세 주소는 연결 목록으로 이동한다. 등록 창의 open_defaults는 해당 몰의 상세 주소로 이동한다.

카페24 저장 계약: `shop_id`는 `mall_id`이고 `access_key_enc`, `secret_key_enc`에 access token과 refresh token을 각각 암호화한다. `token_expires_at`, `refresh_expires_at`은 timestamptz, `scopes`는 기본값이 빈 배열인 필수 text[]다. 만료 시각은 토큰 수신 시각에서 각각 2시간에 60초를 뺀 값, 14일에 1시간을 뺀 값으로 계산하며 access 만료 시각은 토큰 버전으로도 쓴다. 카페24는 해당 몰의 과세, 제조국 기본값과 진열 방식을 사용하고 주소 선택은 지원하지 않는다.

카페24 연결 응답에는 `mallId`(`shop_id`)와 `tokenExpiresAt`을 포함한다. 연결 세대는 해당 사용자와 몰의 가장 최근 행(`created_at desc, id desc`)의 `id:status`이고 행이 없으면 `none`이다.

`GET /v1/malls`의 `{items:[...]}` 봉투와 지그재그 항목은 유지한다. 카페24 항목은 `mall`, `status`, `shopName`, `mallId`, `permissions`, `connectedAt`, `lastRegisteredAt`, `tokenExpiresAt`을 제공한다. 준비 전에는 `unavailable`, 활성 행이 없으면 `none`, refresh 만료 또는 영속 무효화 뒤에는 `reconnect_required`, 그 외에는 `connected`다.

화면 상태와 HTTP 연결 상태는 구분한다. 지그재그 줄은 기존 `none`, `editing`, `verifying`, `connected`, `failed` 다섯 상태를 유지한다.

| 카페24 화면 상태 | 표시와 동작 |
|---|---|
| `none` | 자사몰 관리자 로그인 안내, 연결하기 버튼 |
| `editing` | 몰 아이디 입력, 카페24에서 앱 설치, 접기. 붙여 넣은 URL의 프로토콜, 도메인 접미사와 경로를 제거하고 소문자로 정규화 |
| `starting` | 설치 시작 중, 입력과 버튼 비활성. 접수는 한 번만 보내고 같은 탭에서 설치 주소로 이동 |
| `connected` | 스토어 이름, 몰 아이디, 연결한 날과 마지막 등록. 고정 권한은 표시하지 않으며 관리 메뉴에는 연결 해제만 제공 |
| `failed` | 입력을 유지하고 결과 코드의 셀러 문구 표시. 결과 쿼리를 읽은 뒤 history.replaceState로 mall과 result만 제거 |
| `reconnect_required` | 다시 설치 안내, 다시 연결을 누르면 응답의 mallId로 입력칸 복원 |
| `unavailable` | 준비 중. 연결 목록 로딩 또는 API 비활성도 같은 표시 |

여러 몰 등록 창은 먼저 프로젝트별 recoveries를 확인한다. 복원할 작업이 없을 때 저장 완료, 현재 editorRevision 확인, 같은 revision의 ready 게시 준비, preflight 순서로 진행한다. 대표 이미지는 필수이며 본인 프로젝트 자산만 선택하고 추가 이미지는 서로 다른 자산 최대 4개다. 등록 가격과 옵션별 수량을 확인하고 선택한 몰을 묶음으로 접수한다. 실행 결과를 잃으면 새로 생성하지 않고 같은 batchId 재생 또는 기존 jobId 확인을 사용한다. 화면 전환은 A04와 A05에서 공통 API에 연결한다.

설치 성공 결과는 연결 목록을 갱신하는 신호로만 사용한다. 연결됨, 연결 없음, 재연결 필요 표시는 현재 계정의 서버 응답을 따르며, 이전 설치 성공 표시가 이후 해제나 만료 상태를 가리지 않는다.

카페24 호출 직전에 고정한 `connection_id`로 토큰을 읽고, access 만료 여유가 5분 이하면 갱신한다. `mall:{user}:cafe24` 잠금 안에서 행과 만료 여유를 다시 검사해 동시 요청이 한 번만 회전하도록 한다. 갱신 중에는 예외적으로 외부 호출의 최대 8초 동안 몰 잠금을 유지하며, 새 access와 refresh 암호문 및 만료 시각을 커밋한 뒤에만 반환한다. `invalid_grant`이면 refresh 만료 표시와 두 암호문 삭제를 커밋해 재설치를 요구한다. 회전 응답이나 DB 커밋을 잃으면 다음 갱신의 `invalid_grant`로 같은 상태가 된다.

401 강제 갱신과 두 번째 401의 영속 무효화는 거절당한 `token_expires_at`이 아직 최신일 때만 적용한다. 그 사이 다른 요청이 회전했다면 현재 토큰을 재사용한다. 두 번째 401의 무효화가 버전 변경으로 적용되지 않으면 최신 토큰으로 한 번 더 시도하며, 원격 함수 호출은 총 3회까지다. 앱 삭제 의미의 403도 같은 조건부 무효화를 적용하고, 일반 권한 부족 403은 토큰을 지우지 않는다. 토큰 서비스의 사용자, 연결 ID, 거절된 버전과 호출 함수 인자는 키워드 전용이다.

`DELETE /v1/malls/cafe24`는 로그인 필수다. 몰 잠금 안에서 로컬 연결을 해제하고 암호문을 지우며, 활성 연결 유무와 관계없이 미저장 설치 시도를 무효화하고 커밋한다. 접수, 시작, 소비됐지만 토큰 교환 중인 시도 모두 포함한다. 이후 메모리에 확보한 refresh로 원격 revoke를 최선으로 시도하며 실패해도 204다. 원격 대기 중 새 연결이 생겨도 건드리지 않는다. 탈퇴도 같은 계정 잠금 안에서 로컬 해제와 미저장 시도 무효화를 수행하고 원격 revoke는 하지 않는다. 잠금 순서는 `mall-account:{user}`, `mall:{user}:{mall}`, `mall-defaults:{user}:{mall}`이며 몰 잠금 뒤에 계정 잠금을 잡지 않는다.

로그인 전 앱 실행의 `POST /v1/malls/cafe24/launch`도 검증된 몰별로 최근 1시간 10회까지만 대기 설치를 만든다. 기존 `client_event_rate_limits`의 global 범위와 `mall-launch:{mall_id}` 키를 사용하며, DB 잠금 안에서 횟수 확인과 기록을 수행한다. 로그인 계정이나 서명 쿼리를 바꿔도 같은 몰의 제한을 공유하고 초과하면 대기 행과 쿠키를 추가하지 않고 429 `rate_limited`를 반환한다.

연결 해제는 같은 트랜잭션에서 브라우저의 대기 설치 ID와 비밀 쿠키 해시가 일치하는 행도 삭제한다. 이미 교환 중인 콜백은 삭제된 행에 토큰을 저장할 수 없다. 다른 브라우저의 대기 설치도 확정할 때 해당 계정의 카페24 연결 생성·해제 이력을 검사해, 대기 설치 이후의 변경이 있으면 409 `connection_changed`로 거절한다. 생성과 해제의 비교 시각은 몰 잠금을 얻은 뒤 실제 변경 시각으로 기록하며, 토큰 갱신의 updated_at은 이 판정에 사용하지 않는다. 해제 후 새로 시작한 설치는 허용한다.

| 라우트 | 계약 |
|---|---|
| `POST /v1/malls/cafe24/install` | 로그인 필수. `{mallId,returnTo?}`의 몰 아이디를 정규화하고 허용 목록을 검사한다. 준비 전 503 `cafe24_unconfigured`, 잘못된 몰 400 `invalid_mall_id`, 허용 목록 밖 403 `cafe24_not_allowed`, 폐쇄 계정 404 `account_closed`. 지그재그와 시간당 10회 제한을 공유하며 초과 시 429 `rate_limited`. 계정 잠금과 몰 잠금을 순서대로 잡고 계정 폐쇄 검사, 연결 세대 조회, 설치 시도 저장과 커밋까지 유지한다. 겹친 해제 요청은 커밋한 설치 시도를 무효화할 수 있어야 한다. 시도 행 커밋 뒤 `{startUrl}`과 브라우저 비밀 쿠키를 발급한다. URL에는 비밀과 서명이 없다. |
| `GET /v1/malls/cafe24/install/{attempt_id}/start` | 로그인 없이 쿠키 해시가 시도 행과 일치해야 한 번만 시작할 수 있다. 쿠키를 새로 발급하지 않는다. `{a:attempt_id,e:만료 epoch}`만 서명한 state를 사용해 카페24 승인 주소로 302 이동한다. |
| `GET /v1/malls/cafe24/callback` | 로그인 없이 서명 state, 쿠키, 시도 행을 대조한다. 시도를 원자적으로 소비하고 커밋한 뒤 토큰을 교환한다. 응답 몰과 허용 목록, scope를 재검사하고, 저장 시 계정 잠금, 몰 잠금, 시도 행 `for update` 순서로 폐쇄 여부와 소비됨, 미저장, 미무효화, 세대 일치를 확인한다. 토큰을 암호화하고 `saved_at`을 같은 트랜잭션에 저장한다. 실패해 받은 토큰은 최선으로 폐기한다. 항상 고정 `public_web_origin`의 `/settings/malls?mall=cafe24&result=...`로 302 이동한다. |

`returnTo`는 `/`로 시작하고 `//`로 시작하지 않는 경로만 저장하고 성공 이동 주소의 쿼리에 포함한다. 설치 쿠키는 `wl_cafe24_install={attempt_id}.{secret}`, `Secure; HttpOnly; SameSite=Lax; Path=/v1/malls/cafe24; Max-Age=600`이며 Domain을 지정하지 않는다. 접수, 시작, 콜백 응답은 `Cache-Control: no-store`, 시작과 콜백은 `Referrer-Policy: no-referrer`를 사용한다. 콜백은 성공과 실패 모두 쿠키를 지운다. 시작과 콜백의 접근 로그 쿼리는 `[redacted]`로 가린다. 토큰, code, state, 비밀 원문을 결과 응답이나 로그에 넣지 않는다.

| `result` | 셀러 문구 |
|---|---|
| `connected` | 연결됨 |
| `cancelled` | 설치를 취소했어요. 다시 시도하면 돼요 |
| `state_invalid` | 설치 확인이 만료됐어요. 같은 브라우저에서 다시 시작해 주세요 |
| `mall_mismatch` | 입력한 몰 아이디와 로그인한 몰이 달라요. 몰 아이디를 확인해 주세요 |
| `missing_permission` | 권한을 모두 허용해야 등록할 수 있어요. 다시 설치하며 권한을 허용해 주세요 |
| `not_allowed` | 지금은 시범 운영 중이라 등록된 몰만 연결할 수 있어요 |
| `connection_changed` | 그 사이 연결이 바뀌었어요. 지금 상태를 확인한 뒤 다시 시도해 주세요 |
| `token_exchange_failed` | 카페24에서 인증을 마치지 못했어요. 잠시 뒤 다시 시도해 주세요 |
| `account_closed` | 계정 삭제가 완료되어 사용할 수 없습니다. |

`mall_install_attempts`는 서버 전용이며 RLS를 켜고 anon과 authenticated의 모든 권한을 회수한다. service_role만 접근한다. `user_id`, `mall='cafe24'`, `mall_id`, `expected_generation`, `return_to`, `cookie_hash`(SHA-256), `created_at`, `expires_at`과 `started_at`, `consumed_at`, `saved_at`, `voided_at`을 보관한다. 브라우저 비밀 원문과 토큰은 저장하지 않으며 시도는 10분 뒤 만료된다.

설정은 `CAFE24_ENABLED=false`, `CAFE24_CLIENT_ID`, `CAFE24_CLIENT_SECRET`, `CAFE24_REDIRECT_URI=https://api.wearless.kr/v1/malls/cafe24/callback`, `CAFE24_SCOPES='mall.read_product mall.write_product mall.read_store mall.read_category'`, `CAFE24_API_VERSION=2026-09-01`, `CAFE24_ALLOWED_MALL_IDS=''`다. client secret은 서버 비밀이다. `cafe24_ready`는 스위치와 client ID, client secret, `MALL_KEYS_KEK`가 모두 있어야 참이다. `cafe24_api_origin`은 콜백의 오리진이다. 허용 몰 목록은 쉼표로 나눠 공백을 제거한 frozenset이며 빈 목록은 제한 없음이다. `registration_enabled`는 `MULTIMALL_REGISTER_ENABLED`(기본 false), `MALL_CONNECT_ENABLED`, `DETAIL_PUBLISH_ENABLED`, `cafe24_ready`를 모두 요구한다.

## § 여러 몰 등록

정본은 `docs/superpowers/specs/2026-10-28-multimall-design.md` 3절 첫머리의 10월 30일 변경 지시, 3.5절과 4~8절이다. 기본값은 10월 30일 변경 지시를 우선한다. A02는 서버 계약과 읽기, 접수, 확인, 설정 재개까지 제공한다. A03의 어댑터와 작업 실행이 연결되고 인수 검증을 마칠 때까지 MULTIMALL_REGISTER_ENABLED를 켜지 않는다.

RegisterDraft는 projectId, publicationId, editorRevision, selectedMalls, productName, basePrice, defaultQuantity, priceOverrides, sizes, variantOverrides, mainAssetId, additionalAssetIds, categories, confirmedSourcesByMall, salesOverrides다. 상품명은 공백 정리 뒤 1~100자이며 제어문자를 거부한다. 가격은 1 이상, 수량과 추가금액은 0 이상 정수이고 Boolean, 소수, 음수와 최종 가격 overflow를 거부한다. 수량 미입력은 0이다. 색상은 저장된 그룹 ID와 이름만 쓰고 sizes는 저장된 kind='size' 블록의 info.rows 라벨과 일치해야 한다. 색상과 사이즈의 JSON 배열 직렬화가 variantKey이며 공백 정리로 충돌하는 라벨과 중복 override를 거부한다. 앱의 옵션 상한은 200개다.

에디터 required_notice.info.fields의 10개 키가 고시 정본이다. 기존 셀러 입력을 우선하고 빈칸에만 저장된 소재, 색상, 실제 kind='size' 치수와 kind='care' 문구, 해당 몰의 disclosure 기본값을 보충한다. 안내문과 NBSP, FREE만으로 실측을 만들지 않는다. 다른 값을 가진 고시 블록은 충돌로 기록하고 그 필드를 전송하지 않는다. 제조연월은 자동 생성하지 않는다. 보충 출처와 누락은 snapshot.notice에 고정한다. 카페24의 소재와 원산지 누락은 경고이며 배송과 반품 기본값은 요구하지 않는다.

| 경로 | 계약 |
|---|---|
| `POST /v1/malls/registrations/preflight` | RegisterDraft. `{editorRevision,inputHash,readyMalls,blockedMalls,items}` 반환. 몰별 ready, blocked, preparing과 issues, warnings, ready인 몰의 sourceVersions, summary를 제공. 상품 쓰기, 잡 생성과 등록 한도 소비 없음 |
| `POST /v1/malls/registration-batches` | `{batchId:UUID,draft:RegisterDraft}`. 새 접수 202, 같은 요청 재생 200. `{batchId,acceptedAt,items,readyCount}` 반환 |
| `GET /v1/malls/registration-batches/{batchId}` | 원래 영수증의 blocked 항목과 accepted 항목의 현재 registration 뷰를 합침. 사본 불일치는 500 |
| `GET /v1/malls/registrations/{jobId}` | 소유자, kind와 payload.mall을 제한한 공통 등록 뷰 |
| `POST /v1/malls/registrations/{jobId}/verify` | 종료된 unknown과 duplicate_suspected만 원격 조회로 확인. 생성과 설정 쓰기 없음. 현재 연결 세대 재검사 후 저장. 사용자당 분당 6회 |
| `POST /v1/malls/registrations/{jobId}/resume` | `{requestKey:UUID}`. 확인된 상품의 안전한 미완료 설정만 같은 잡을 pending으로 전환. 원본 payload와 checkpoints 유지. 같은 requestKey는 재생. 추가 이미지 POST가 불명확하면 관리자 확인만 허용. 사용자당 분당 6회 |
| `GET /v1/projects/{projectId}/mall-registrations` | `{items,recoveries:{cafe24:[],zigzag:[]}}`. 최신 5건 외에도 현재 스토어 전체 이력의 활성, 생성 불명확, 설정 미완료를 복구. Phase A의 zigzag 배열은 비어 있음 |

카테고리 추천 도우미는 직접 완전 일치, 의류 사전 일치, parentCategoryId를 따른 상위 경로 포함 순으로 비교하며 동률은 자동 선택하지 않는다. 추가 AI 호출은 없다.

SourceVersions는 connectionVersion, mallDefaultsRevision, catalogVersion, editorRevision이다. 기본값 버전은 mallDefaultsRevision 하나로 해당 몰의 판매 정보와 주소 선택을 함께 확인한다. 접수는 현재 ready 후보에만 확인 버전을 요구한다. 누락은 preflight_required, 연결 변경은 connection_changed, 몰 기본값 변경은 defaults_changed, 카탈로그 변경은 catalog_changed로 해당 몰만 차단한다. preparing과 blocked 몰에 버전이 없어도 다른 몰을 접수할 수 있다. 카페24 B 기준은 priceOverrides.cafe24에 amount와 basis=tax_excluded를 명시해야 한다. 다른 기준은 selling_price이며 서버가 임의 세금 변환을 하지 않는다.

접수는 계정, 몰 연결(cafe24 다음 zigzag), 몰별 기본값, 묶음, 프로젝트 등록 잠금 뒤 게시 행과 프로젝트 행을 잠근다. `publication.editor_revision == draft.editorRevision == projects.editor_revision`을 재검사하고 현재 사실에서 snapshot을 고정한다. 외부 조회는 이 트랜잭션 밖에서 끝낸다. 준비된 몰마다 무과금 mall_register 잡 하나와 시간당 신규 등록 슬롯 하나를 같은 트랜잭션으로 저장한다. 슬롯이 모자라면 묶음 전체를 429로 롤백한다. 접수할 몰이 없으면 409 no_ready_malls 및 최신 items를 반환하며 영수증을 저장하지 않는다. 커밋 후 dispatcher를 한 번 깨운다.

idempotency_key는 `mall-register:{user_id}:{batchId}:{mall}`이다. 정규화한 클라이언트 요청 전체를 SHA-256으로 해시하며 selectedMalls와 객체 키는 정렬하고 이미지와 옵션의 의미 있는 순서는 보존한다. confirmedSourcesByMall도 해시에 포함하되 현재 서버 연결 ID나 조회 시각을 덧붙이지 않는다. 같은 batchId는 계정 폐쇄와 요청 형식을 확인한 뒤 현재 자원 재검사보다 먼저 원래 영수증을 재생한다. 다른 입력은 409 idempotency_key_reused다.

payload는 mall, batchId, requestHash, connectionId, shopId, publicationId, editorRevision, snapshot만 가진다. snapshot은 상품명, 가격과 기준, 옵션별 수량과 추가금액, 소유 자산의 이미지 순서, 고시, 카테고리, 해당 몰의 기본값과 선택, 이미지 비율, 가격 설정 및 카탈로그 버전을 고정한다. 실행 중 현재 기본값으로 다시 만들지 않는다. metadata.batchReceipt는 batchId, requestHash, acceptedAt, selectedMalls와 items를 모든 새 잡에 동일하게 보관한다. externalCode는 wl과 최초 잡 ID hex 앞 16자리이며 productId는 몰 공통 문자열이다. 같은 프로젝트라도 서로 다른 몰은 동시에 활성일 수 있고 같은 몰의 활성 잡은 하나다.

공통 등록 뷰는 jobId, mall, status, progress, stage, outcome, setupStatus, productId, productUrl, adminUrl, stages, retryAction, code, warnings, retention, publicationId, createdAt, finishedAt이다. pending과 running의 outcome은 null이고 setupStatus는 pending이다. 종료 시 productId 확인, rejectedAt, create.sentAt, 전송 전 실패 순으로 판정한다. 상품 확인이면 created를 유지하고 모든 설정이 끝나야 complete, 미완료면 needs_attention이다. unknown과 duplicate_suspected는 verify, 안전한 설정 미완료는 resume, 생성 전 확정 실패는 new_request를 제공한다. checkpoints는 단계 이름별 객체이며 status=complete로 완료를 표시한다. uploading_additional_images.requests에는 요청별 sentAt과 status를 남기고 불명확한 POST는 자동 재개하지 않는다.

복구 조회도 공통 뷰와 같은 완료 기준을 사용한다. productId가 확인된 종료 작업은 result.setupStatus가 complete이거나 SETUP_STAGES의 모든 필수 체크포인트가 complete이면 미해결 목록에서 제외한다. 최종 결과 저장 전에 작업이 중단돼 result가 없어도 같다. 필수 단계가 하나라도 미완료이면 복구 대상으로 유지하고, pending과 running 작업은 완료 체크포인트 유무와 관계없이 활성 작업으로 유지한다.

카페24 재고 요청이 연결 생성 또는 연결 확보 단계에서 실패해 전송되지 않았고 앞선 미확인 요청도 없으면 setting_inventory.requestNotSent=true를 기록한다. 이 경우 설정 이어 하기는 비공개 상태와 관리자 변경 여부를 다시 확인한 뒤 같은 절대 수량 요청을 보낼 수 있다. 읽기와 쓰기 도중의 시간 초과는 전송 여부를 단정하지 않으며, 뒤의 연결 실패나 오류 응답이 앞선 미확인 요청 기록을 지우지 않는다. 전송 여부가 미확인인 요청은 목표 재고가 조회되면 재전송 없이 완료하고, 판매 전환 전송 기록 뒤에는 재고를 다시 쓰지 않는다.

모든 새 API는 로그인, 소유자 조건과 Cache-Control: no-store, `{error:{code,message,...}}`를 사용한다. 비밀값, 임의 외부 URL, upstream 원문과 예외 객체를 결과와 이벤트에 보관하지 않는다. 카페24 전용 등록 경로, 이전 요청 모델과 단일 recovery 응답은 제거한다. OAuth, 토큰 회전 및 카페24로 게시 이미지를 복사하는 정책은 유지한다. 지그재그 게시 보관 정책은 Phase B 전까지 적용하지 않는다.

## 7. 현행 코드와의 갭 (마이그레이션 TODO)

→ **루트 `TODOS.md` §1로 이관.** 코드가 이 계약을 아직 못 따라간 항목(✅ 완료 기록 + 🔶 남음 + 🆕 신규)은 작업 때마다 각 설계 문서를 고치지 않고 TODOS.md 한곳에서 추적한다.

---

## 8. 백엔드 구현 현황 (2026-06-29 기준 라이브)

- Supabase 스키마 구현 완료 (9개 마이그레이션, 17테이블): `profiles`, `credit_accounts`, `credit_ledger`, `projects`, `products`, `analyses`, `assets`, `mannequin_cuts`, `wardrobe_images`, `matching_items`, `jobs`, `job_events`, `exports`, `pricing_plans`, `payment_history`, `credit_sources`, `refund_requests`. 전체 RLS 활성, 쓰기는 service-role/FastAPI 전용.
- 생성 작업은 서버에서 job(id·status)으로 관리한다. 현재 프론트 어댑터는 `GenJob`을 폴링해 `onProgress`만 전달한다. 서버 SSE 경로는 구현돼 있으나 프론트 구독과 `onStep` 배선은 TODO다.
- 크레딧 차감은 서버 트랜잭션이 원본이고 `{ data, credits }`의 `credits`가 그 결과다. 프론트 선차감 금지.

### 상세페이지 부분 완성 결과와 빈 칸 다시 만들기 (ADR-0017)

일부 실패로 완료된 상세페이지 작업의 `result`, `done` 이벤트와 정산 메타데이터에는 같은 `partial` 요약을 넣는다. 전부 성공이면 생략한다.

```ts
partial?: {
  requestedCuts: number             // 요청한 이미지 칸 수. 복제 칸 포함
  completedCuts: number             // 완성한 이미지 칸 수. 복제 칸 포함
  failedCuts: { blockId: string, reason: string, retryable: boolean, sourceBlockId?: string }[]
  quoteCredits: number             // 예약 견적
  creditsCharged: number            // 성공 확정에서 실제 차감
  creditsNotCharged: number         // 예약 견적에서 차감하지 않은 값
}
```

정산의 요청 수는 예약 `aiCount`, 완성 수는 새로 생성한 자산 수다. 복제 칸은 이 과금 단위에 포함하지 않는다. `chargeCuts = completed == 0 ? 0 : max(completed, max(aiCount, minCuts) - max(0, aiCount - completed))`를 예약 단가와 곱하고 예약액으로 상한을 둔다. 스냅샷이 없는 옛 작업은 완성 자산 수에 단가를 곱한다.

빈 칸 다시 만들기의 `NewCutRequest`와 비슷한 컷 후속 `VaryRequest`는 `retryBlockId?: string`을 보낸다. 서버는 저장 콘티에서 설정을 복원하고 해당 `sourceBlockId`의 빈 이미지 칸이 실제로 있을 때만 받는다. 호리존 세트의 기존 검증도 유지한다. 같은 칸의 진행 중 `editor_image` 작업에는 합류하며 새 예약을 만들지 않는다. 합류한 요청 키도 기존 jobs.metadata에 보관하여 원래 작업이 끝난 뒤 접수 확인을 재전송해도 같은 작업을 돌려준다. 원래 작업의 키를 덮어쓰지 않으며 새 키의 새 요청은 구분한다. 후속 칸의 `mode:'new'`는 `similar_source_required`로 거절하고, 채워진 원본 사진의 `mode:'vary', variationMode:'similar'` 경로를 쓴다. 자산 메타데이터에 `retryBlockId`를 남긴다. 다시 만들기도 생성 작업과 같은 대체 규칙을 쓴다(ADR-0017 2026-10-11 개정). 대체로 채운 컷은 상세페이지와 에디터 모두 `assets.metadata.fallback: [{kind, ...}]`를 남긴다. `kind`는 `matching_substituted`(`missingMatchId`, `substituteMatchId`)와 `direction_from_example`(`exampleId`, `fromDirection`, `fromSideStyle`, `toDirection`, `toSideStyle`)이다. 새로 만드는 컷 값은 `credit_cost_editor_image`다. 같은 사진 복제의 원본 실패 칸은 앞선 원본 칸이 채워지면 그 사진을 그대로 넣으며, 새 생성이나 추가 차감은 하지 않는다.

같은 사진 복제의 원본 실패에는 실제 실행에서 합친 원본 칸 번호를 `cut_failed` 이벤트와 `partial.failedCuts[].sourceBlockId`에 남긴다. 빈 이미지 요소에는 `genFailSourceBlockId`로 보존한다. 요소의 기존 `sourceBlockId`는 해당 빈 칸 자체의 콘티 번호이며 바꾸지 않는다. 에디터는 기록된 원본이 완성되면 그 사진을 재사용하고, 새 생성이나 추가 차감을 하지 않는다. 이 필드가 없는 예전 저장본은 기존 설정 비교로 원본을 찾는다.

### 콘티의 비슷한 컷 예약 (ADR-0016)

후속 컷은 원본의 `spaceGroupId`와 `spaceVariation`, 배경 설정을 상속하고 같은 연속 세트의 장수에 포함한다. `spaceSetMemberOrder`는 없으며 예비 멤버를 쓰지 않는다. 원본이 세트 밖이면 후속 컷도 세트 밖이다. 세트 교체와 삭제는 원본 ID의 생존을 따르고, 원본의 세트 이탈 이동은 후속 컷을 함께 옮긴다. 후킹 프레임 슬롯 후보에서는 항상 제외한다.

후속 블록의 `similarSourceBlockId`는 같은 콘티의 일반 AI 착용 블록을 가리킨다. 생성예시 사진을 편집하지 않는다. 원본을 성공적으로 생성한 뒤 그 결과 사진으로 후속 컷을 만들며, 모두 최초 원본을 기준으로 한다. 후속 카드의 위치와 ID는 유지하고 상품·예시 설정은 원본에서 상속한다. 일반 복제의 동일 사진 재사용과 달리 후속 컷마다 새로운 사진을 생성·정산한다. 사용자 방향·포즈·표정 선택은 없다. 원본 삭제 시 후속 카드를 함께 삭제하며 되돌리기가 가능하다. 서버는 존재하지 않는 원본, 후속 컷을 원본으로 삼는 관계, 제품·디테일·내 사진 원본을 거절한다. 계획과 생성 실행은 서버 작업 소유이며 에디터 마운트·새로고침으로 새 호출을 만들지 않는다.

### 확정 전 초안 정돈 사진 (WO-10, 2026-10-19)

`DRAFT_VISUALS=on`일 때 로그인한 PC 셀러의 기준 색상 앞면과 내 옷을 프로젝트 생성 전에 준비한다. 비회원과 모바일과 태블릿의 원본 표시 및 PC 이어하기 정책은 유지한다. 기본값은 `off`이며 운영 매니페스트에서 자동으로 켜지 않는다. `on`에는 `FAL_KEY`가 필요하다. 기존 `GET /v1/me/account`는 `draftVisualsEnabled:boolean`을 반환한다. 클라이언트는 현재 계정에서 이 값이 명시적으로 `true`일 때만 준비 POST, 조회 GET, 내 옷 준비 업로드를 시작한다. 필드가 없는 구 서버와 계정 전환 직후에는 off로 취급한다.

- `POST /v1/draft-slot/visuals:prepare`: Bearer 인증과 `X-Draft-Token` 필수. 본문은 `{role:"product"|"custom", sourceAssetIds:[UUID], localId?:string}`. 상품은 서버 기준 색상(`isBase`, 없으면 첫 색상)의 `Front` 한 장, 내 옷은 현재 슬롯의 `customMatch.sources` 순서와 일치해야 한다. 원본은 해당 사용자 소유 `purpose=draft_slot` 업로드다.
- 준비 응답: `{state:"processing"|"ready"|"failed"|"off", jobId:UUID|null, sourceAssetIds:[UUID]}`. 상품에는 기존 상품 visuals와 같은 `front:{tidyStatus,tidyUrl,cutoutStatus,cutoutUrl,width,height}`가 추가된다. 내 옷에는 `{localId,boardUrl,thumbnailUrl,cutoutStatus}`가 추가된다. 한도 또는 보조 인프라 실패는 `state:"off", reason:"limit"|"unavailable"`로 돌려주며 분석을 막지 않는다.
- `GET /v1/draft-slot/visuals`: 같은 인증과 작업권 검증을 거쳐 현재 원본에 해당하는 `{state,product:준비응답|null,custom:준비응답|null}`를 반환한다. 잡 생성과 재시도는 하지 않는다. 꺼져 있으면 `{state:"off",product:null,custom:null}`이다. 프론트는 5초마다 조회하며 최대 60회, 누적 오류 3회로 제한한다. 완료, 실패, 꺼짐, 사진과 계정 교체, 화면 이탈 때 중단한다.
- `POST /v1/draft-slot/visuals:promote`: Bearer 인증 필수. `{jobId,projectId,sourceAssetIds}`로 이미 업로드된 프로젝트 원본의 실제 해시를 검증하고 `{joined:true,jobId}`를 반환한다. 슬롯의 작업권을 이미 소비했으므로 초안 토큰은 받지 않는다. 잡과 프로젝트 모두 같은 사용자 소유여야 한다. 상품 저장 전에 호출한다. 연결에 성공하면 상품 PATCH의 선택 필드 `draftVisualJobId`도 전달한다. 이 필드는 상품에 저장하지 않으며 준비 스위치를 끈 직후의 확정도 진행 중 작업에 합류하게 한다.
- `POST /v1/projects/{id}/analysis/custom-match-item`의 선택 필드 `draftVisualJobId`는 내 옷 등록 직후 같은 방식으로 연결한다. 확정 전에는 `matching_items`를 생성하지 않는다. 표시 갱신은 `boardUrl`, 썸네일, 상태만 병합하며 선택, 순서, 사용자 편집은 보존한다.

초안 슬롯 payload의 선택 필드는 다음과 같다. `customMatch.sources`에는 blob 대신 서버 원본 참조를 저장한다. `draftVisuals`는 화면에서 결과를 현재 원본과 대조하고 확정 때 연결하는 참조이며, IndexedDB의 `visuals`에도 같은 내용을 보존한다. 로컬 복원은 앞면의 `sourceUrl`과 서버 원본 ID를 재사용해 새 업로드와 중복 정돈을 막는다. 내 옷 참조에는 복원을 위한 `sources`도 보존한다. 서버는 이를 신뢰하지 않고 소유권과 사진 해시를 다시 검증한다. GET 응답은 현재 서버 원본 ID와 대조해 잃어버린 로컬 인계 참조도 복구한다. 확정은 준비 요청을 최대 3초만 기다리고, 시간 초과나 취소 때 준비 POST와 내 옷 업로드를 중단한다. 업로드 URL 발급, R2 전송 직전, 완료 요청마다 최초 사용자와 현재 작업 유효성을 검증한다.

```json
{
  "customMatch": {
    "localId": "local-custom-id",
    "selected": false,
    "sources": [{"id": "source-uuid", "src": "https://images.example/original.jpg"}]
  },
  "draftVisuals": {
    "product": {"jobId": "job-uuid", "imageId": "source-uuid", "src": "https://images.example/front.jpg", "sourceUrl": "https://images.example/front.jpg", "sourceAssetIds": ["source-uuid"]},
    "custom": {"jobId": "custom-job-uuid", "localId": "local-custom-id", "sourceAssetIds": ["source-uuid"]}
  }
}
```

기존 `sam_preprocess`, `matching_cutout` kind를 사용하며 `project_id=NULL`, `payload.mode="draft_visuals"`로 분기한다. 사진 스냅샷과 처리 버전은 잡 payload에, 한도 예약과 승격 목적지는 metadata에 기록한다. 같은 사용자와 역할과 사진 해시와 처리 버전의 요청은 하나의 잡에 합류한다. 새 옷은 사용자당 UTC 하루 20벌, 동시에 2건이며 서비스 전체 일예산은 `DRAFT_VISUALS_DAILY_BUDGET_USD`(기본 5달러)다. 실제 호출보다 보수적인 상한을 예약하고 DB 전역 잠금 안에서 확인과 잡 생성을 함께 수행한다. 캐시 합류는 새 한도를 소비하지 않는다. 워커도 유료 호출 전에 같은 전역 잠금과 잡 행 잠금 안에서 DB의 UTC 실행일 예산을 재검증한다. 전날 미집행 예약은 `metadata.budgetDate`로 실행일에 재예약하고 `paidStarted`와 함께 선커밋한다. 당일 예산이 부족하면 호출 없이 `failed/daily_budget`로 끝내 원본을 표시한다.

유료 실행 시작을 잡 행에 먼저 커밋한다. 응답 유실이나 리스 회수 뒤 같은 유료 요청은 재발행하지 않으며, 초안 모드에서는 제공자의 5xx 응답도 자동 재호출하지 않는다. Qwen 원출력은 후속 마스크 호출 전에 저장한다. 승격 요청과 워커 완료는 같은 잡 행을 `FOR UPDATE`로 잠가 완료 순서와 관계없이 결과를 전달한다. 상품 누끼와 정돈본은 프로젝트 소유의 별도 키로 복사하고, 내 옷은 grid와 썸네일과 보드 PNG를 연결한다. 기존 전역 캐노니컬 파일은 변경하거나 소유 자산 행을 빌려오지 않는다. 상품 저장과 내 옷 등록은 클라이언트 잡 ID가 없어도 소유 사용자, 순서 있는 원본 바이트 해시, 역할, 처리 버전으로 기존 잡을 찾아 연결한다. 준비 후 스위치가 off로 바뀌어도 기존 사용자 작업이 있으면 이 조회를 유지한다. 유료 결과의 `result.state=ready`와 대상별 `applied/deliveryState`는 별도로 보존한다. 복사 실패는 대상의 `deliveryState=pending`을 유지하며 프로젝트 visuals 조회, 매칭 후보 조회, 상품 저장 큐잉에서 모델 호출 없이 전달만 재시도한다. 전달 완료, 유료 작업 진행 중, 또는 보존된 ready 결과의 전달 대기일 때만 프로젝트 앞면 정돈을 생략하고 필요한 뒷면만 처리한다. 실패한 잡의 승격 기록만으로는 앞면을 생략하지 않는다. 연결 실패는 프로젝트 확정을 막지 않는다.

확정의 `DELETE /v1/draft-slot?promote=1`은 기존 토큰 소비 순서를 유지하면서 관련 잡에 30분간 승격 대기 표시를 남긴다. 그 사이 워커가 현재 원본을 잃지 않는다. 일반 삭제에는 이 표시를 남기지 않는다. 7일 회수는 내 옷 원본과 초안 파생 결과까지 포함하고, 활성 잡, 승격 대기와 연결, 현재 슬롯과 프로젝트 참조, 전역 캐노니컬 파일을 보존한다. DB 마이그레이션은 추가하지 않는다. 신규 요청을 켜기 전에 모든 서버와 워커를 교체해야 한다. 실연동과 화면 검사는 샌드박스 밖에서 진행한다.

## 명시적 제품 전체컷 단추 검수 (2026-11-02, ADR-0024)

일반 컷 QC의 검사 기준에도 서버에서 정규화한 `productPresentation`을 전달한다. `generic_v1`, `recipeFamily=product`, `productVariant=ghost`, `shot=ghost`와 유효한 촬영 방식이 모두 확인된 경우에만 촬영 방식과 camera, scene, light, captureTone의 `storyboard` 권한을 허용한다. 상품 구조와 소재, 부자재, 글자는 계속 상품 원본의 권한이다. 값이 없거나 착용컷과 디테일컷인 경우에는 기존 권한 검증을 유지한다.

`productPresentation`을 지정한 `product/ghost` 생성에는 서버의 필수 단추 검수가 적용된다. 클라이언트는 기대 개수나 검수 통과 여부를 지정하지 않는다. 원본과 독립 반전 관찰의 부위별 개수·배열·방향이 명확하게 일치해야 생성하며 후보도 같은 방식으로 확인한다. 후보가 불일치하거나 불확실하면 원본에서 최대 한 번 재생성하고 재검수한다. 끝내 통과하지 못하면 최초 후보로 폴백하지 않는다.

성공 자산은 `assets.metadata.product_button_qc`에 정책 버전, 원본 해시·선택 면·관찰 결과, 독립 관찰의 사용량, 시도별 후보 해시·판정, 최종 통과 해시를 기록한다. 생성 job 메타 요약 키는 `productButtonQc`다. 검수 결과는 서버 내부 관찰 기록이며 상품 사실이나 사람의 검수 인증으로 취급하지 않는다.

`product_button_source_uncertain`, `product_button_source_disagreement`, `product_button_qc_failed`, `product_button_preservation_failed` 및 검수 불가 오류는 컷 실패로 처리한다. 이미지와 검수 영수증의 해시가 맞지 않아도 저장하지 않는다. 에디터는 실패와 미차감을 알리고 상세페이지는 기존 부분 발행·정산 규칙을 사용한다. 전체컷 슬롯 가격은 바뀌지 않는다.
