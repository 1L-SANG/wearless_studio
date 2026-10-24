# 공통 데이터 계약 (Common Data Contract)

> 상태: 확정 (2026-06-11, 갱신 2026-07-18) · 근거: `documents/PRD.md`, 프론트 API·목 구현(`src/lib/api/`, `src/mock/*`), 2026-06-11·06-14·07-17·07-18 결정 세션
> 결정 기록: `docs/adr/0001~0007`, 용어는 `/CONTEXT.md`
> 적용 방식: 이 문서가 계약의 원본이다. 프론트 API·목 레이어를 이 계약에 맞추고, 백엔드(FastAPI·Supabase·R2)와 AI 파이프라인은 같은 shape를 구현한다. 현행 코드와의 차이(마이그레이션 갭)는 루트 `TODOS.md`가 추적한다 — 충돌 시 해당 기능의 현행 ADR과 사용자 결정을 함께 확인한다.

---

## 1. 명명·직렬화 원칙

1. **저장되는 key와 enum 값은 영문 토큰, 한국어는 표시 라벨로만.** 라벨은 `catalogs`의 `{ value, label }`에서 파생한다. 단, **사용자가 직접 입력하는 자유 텍스트**(상품명, 소재명, 강조 특징, 카피, 텍스트 요소)는 한국어 그대로 저장한다.
2. **필드명은 camelCase.** DB(snake_case) 매핑은 백엔드 어댑터 책임이며 프론트 계약은 camelCase 고정.
3. **enum 토큰은 소문자 단일어 또는 snake_case**(`semi_over`). 기존 토큰 `ai-notice`, `arrow-l`은 마이그레이션 비용 대비 이득이 없어 예외로 유지한다.
4. **id는 불투명 문자열.** 클라이언트 임시 id는 `{prefix}_{rand}`(prefix: `prj` `prd` `col` `img` `blk` `b` `el` `w`)이며, 서버 도입 후 서버 발급 id로 대체된다. 프론트는 id 형식에 의존하지 않는다.
5. **시간은 ISO 8601 문자열**(`updatedAt`). '2시간 전' 같은 상대 표시는 화면에서 파생한다.
6. **이미지 `src`는 URL 문자열.** 현재는 objectURL/placeholder, 백엔드 이후 R2 URL. 클라 전용 표시 필드(`loading`, `fresh` 등)는 계약에 포함하지 않는다.

---

## 2. 최상위 엔티티: Project

플로우 전체(입력 → 분석 → 마네킹 → 콘티 → 생성 → 에디터 → 보관함)를 묶는 단위. 모든 API는 `projectId`를 첫 인자로 받는다 (ADR-0001).

```ts
Project {
  id: string
  status: ProjectStatus            // draft | generating | done
  title: string                    // = product.name 미러 (보관함 표시용)
  composeMode: ComposeMode         // 기본 'basic'
  copywriting: boolean             // 기본 true
  selectedMannequinId: string | null
  adjustCount?: number             // 과거 저장값 읽기 전용. 새 입력·제한·프론트 동기화에 사용하지 않음
  createdAt: string                // ISO
  updatedAt: string                // ISO
}

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

- `composeMode` `copywriting` `selectedMannequinId`는 **서버 동기화 대상 선택값**이다. 프론트는 Zustand에 작업 사본을 두고 변경 시 `patchProject`로 동기화한다 (→ `frontend_state_model.md`).
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
  mdComment: string                // 셀러 직접 입력 선택 메모. 기본값 ''. 앞뒤 공백 제거 후 최대 300자
  selectedModelId: string          // catalogs.models 참조
  matchCandidates: MatchClothing[] // AI가 제안한 매칭 의류 후보
  matchSelections: { clothingId: string, role: 'main' | 'sub' }[]   // max 2
  locked: boolean
  frontClosure?: FrontClosure       // AG-01이 사진에서 판정한 앞 여밈. 서버 소유, 클라이언트 PATCH 금지. 공개 분석은 기존 상품 증거 인계 봉투에 함께 서명해 원본 검증 후 저장한다. 없으면 판단 불가로 읽는다(ADR-0018)
  garmentColorEvidence?: GarmentColorSummary // 서버가 사진에서 측정한 공개 요약. 콘티보드 이탈 시 필요할 때만 생성하고, 없으면 상세페이지 생성이 한 번 더 잰다. 클라이언트 PATCH 금지
}
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

`mdComment`는 지금은 저장만 하고 AI 입력에는 쓰지 않는다. 템플릿이 확정된 뒤 연결한다.
문자열이 아닌 값은 저장할 수 없다. `merge=1`에서 `null`은 기존 병합 규칙에 따라 키를 삭제한다.

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

```ts
StoryboardBlock {
  id: string
  taxonomyVersion: 3               // 공식 4섹션 분류. v2 저장본은 읽을 때 자동 승격
  sectionId: string                // 연속된 같은 섹션 블록이 공유하는 id
  sectionRole: StoryboardSectionRole
  sectionTitle?: string            // @deprecated 전환기 표시 캐시. sectionRole에서 다시 계산
  sectionLayout: SectionLayout     // 기본 'stack'
  sectionCustom?: boolean          // 사용자가 순서·구성을 직접 바꿨는지
  contentRole: ContentRole         // 시스템이 배정하는 내부 사진 목적. 콘티 UI에서 선택·표시하지 않음
  title?: string                   // @deprecated 전환기 표시 캐시. contentRole에서 다시 계산
  source: BlockSource              // 'ai' | 'mine'
  cutType: CutType | null          // 생성 레시피. 콘티 인스펙터에서 섹션별 허용 컷을 직접 선택. source='mine'이면 null
  direction?: Direction | ProductDirection | null   // 착용컷은 생성예시에서 파생(ADR-0015). 제품컷은 셀러가 고른 원본 사진의 면이고 대상이 있는 디테일은 대상의 원본 면이다. 예시는 제품컷 방향을 바꾸지 않는다(ADR-0008 2026-10-10 개정)
  shot?: ShotType | ProductShotType
  detailTargetId?: string | null   // product/detail 촬영 대상. 서버 소유 분석 후보 ID만 선택; 좌표·사실은 서버에서 원본 검증 후 해석
  detailTargetOrigin?: 'auto' | 'user' // 추천 생성과 수동 선택/해제 구별. 사용자 편집을 재추천으로 덮지 않음
  outerClosureState?: OuterClosureState | null       // 아우터 착용컷(styling·horizon) 전용. 누락·모르는 값은 open (ADR-0018)
  colorId?: string                 // ColorGroup.id (단수 — 컬러별 컷은 블록을 색상마다 분리)
  matchIds: string[]               // 매칭 의류 후보 id (UI는 최대 1개, 배열 shape은 구 저장분 호환용)
  faceExposure: FaceExposure       // 기본 'same'
  angle: CameraAngle               // 기본 'same'
  refImages: string[]              // '내 레퍼런스' 업로드 (생성 입력에 포함) — 프로젝트 한정, 전역 저장 없음 (ADR-0004)
  similarSourceBlockId?: string    // 원본 AI 착용 카드 ID. 원본 결과로 생성하고 별도 과금한다. 원본의 세트 소속을 상속하며 예비 멤버는 쓰지 않는다 (ADR-0016)
  exampleId?: string | null        // 촬영 연출 예시 — 예시 속 옷·신발·액세서리는 생성 근거에서 제외 (ADR-0004)
  exampleSelectionOrigin?: 'auto' | 'user' | null  // 자동 배정인지 사용자 확정인지. source와 다른 축
  setSelectionOrigin?: 'auto' | 'user' | null      // 촬영 세트의 진입 자동 배치인지 사용자 선택인지
  spaceGroupId?: string | null     // 발행된 촬영 세트 인스턴스. `ssg1__<setId>__<instanceId>`만 허용
  spaceVariation?: 'fixed' | 'subtle'  // 발행 세트가 정한 변화 강도. 임의 입력값은 허용하지 않음
  horizonBackgroundMode?: 'reference' | 'garment-tone' // 호리존 세트 전용. 기본 reference; 같은 spaceGroupId의 모든 컷에 함께 저장
  refScope?: 'all' | 'pose' // 서버·클라이언트가 컷에서 도출하는 내부 값. 일반 컷·스튜디오 세트는 all, 스타일링 장소 세트만 pose.
  layoutRowId?: string             // 2단·3단 배치에서 같은 행을 공유하는 id
  layoutRowVersion?: 1
  ownImages: string[]              // source='mine'의 직접 업로드 이미지
  thumb: string                    // 예시 썸네일 (서버/목 생성, 최종 이미지 아님)
}
// 폐기: pose / poseThumb / poseLabel(별도 포즈 선택 제거), bgThumb / bgLabel(PRD §8.5에서 배경 제거),
//       colorIds(미사용 잔재), kind(sectionRole/contentRole과 중복)
```

불변식:

- 자동 콘티의 섹션 순서는 `hooking → styling → studio → product`다. `구매 정보`와 `같은 장소`는 섹션을 만들지 않는다.
- `hero|benefit`은 `hooking`, `coordination`은 `styling`, `fit`은 `studio`, `productOverview|detail`은 `product` 섹션에 속한다. `custom`은 사용자가 놓은 섹션을 따른다.
- AI 카드의 `contentRole`은 사용자 입력값이 아니다. `hooking`은 카드 순서, `styling|studio`는 섹션과 `cutType`, `product`는 선택한 `shot`으로 자동 배정한다. `hooking` 섹션의 첫 AI 카드만 `hero`다. 이동·복제·삭제·순서 변경·컷·샷 변경·되돌리기와 서버 저장에서 다시 정규화한다.
- `source='mine'`은 `cutType=null`, `contentRole='custom'`이다. 현재 UI에서 내 이미지는 AI 사진 목적을 다시 고르지 않으며, 어느 큰 섹션에 놓였는지는 `sectionRole`로 따로 저장한다. 내 이미지는 컷 종류가 아니다.
- `cutType`은 생성 레시피 값이지만 콘티 인스펙터에서 컷 종류로 직접 고른다. `hooking`은 `styling | horizon`, `styling`은 `styling`, `studio`는 `horizon`만 허용한다. `product`는 `cutType='product'`로 고정하고 `ghost | detail` 샷을 고른다.
- 생성예시 갤러리는 서로 다른 `cutType`을 섞지 않는다. 현재 카드의 `cutType`과 샷·성별·적용 의류 종류가 정확히 맞는 예시만 3열 2줄, 페이지당 최대 6장씩 보여 주고 나머지는 옆 페이지로 넘겨 모두 볼 수 있다. 예시 선택이 `cutType`을 바꾸지는 않는다.
- `contentRole='detail'`은 셀러 원본의 확인 가능한 부분을 근거로 만든다. 별도 `Detail` 슬롯 사진은 필수 조건이 아니다. 선택한 `detailTargetId`의 원본 바인딩과 영역을 서버가 검증하며, 입력·검수는 ADR-0012, 후보 선택·발행은 ADR-0014를 따른다.
- `taxonomyVersion: 3`만 새로 저장한다. v2의 `benefit`은 `hooking`, `fit`은 `cutType='horizon'`이거나 `contentRole='fit'`이면 `studio`, 그 외에는 `styling`으로 읽을 때 자동 승격한다.
- `sectionTitle`과 `title`은 현재 구현의 전환기 캐시일 뿐 기준 데이터가 아니다. 읽을 때 enum 라벨로 덮어쓰며, 후속 정리에서 저장 shape에서 제거한다.
- 공개된 정상 조합의 `source='ai'` 블록은 자동 배정 뒤 `exampleId`가 사실상 필수다. 같은 공간의 pose·방향 호환 자산이 0장인 예외에는 미배정 상태와 오류 안내를 유지하며 `all`로 강등하지 않는다.
- 자동 배정은 `exampleSelectionOrigin='auto'`, 직접 선택·다른 예시 보기·참고 범위 변경·샷/컷 변경 확정은 `'user'`로 저장한다. `exampleId`가 있는데 이 필드가 없는 과거 저장분은 사용자 선택으로 간주해 `'user'`로 정규화한다.
- 진입 시 자동 배치된 촬영 세트는 `setSelectionOrigin='auto'`, 셀러가 추가하거나 교체한 세트는 `'user'`로 저장한다. 이 값은 기본 콘티 지문에서 제외한다.
- `exampleId`가 없으면 서버는 `exampleSelectionOrigin=null`을 강제한다. 레시피 정규화·섹션 이동 등으로 `exampleId`를 지울 때 예시 썸네일도 원래 카드 썸네일로 복원해 origin·thumb 고아 값을 남기지 않는다.
- 기본 콘티 지문과 mock `storyboardDirty` 판정에서는 `exampleSelectionOrigin='auto'`인 `exampleId`와 그 배정이 정한 `refScope`를 기본값으로 취급한다. 따라서 자동 배정 저장만으로 사진 양 변경 시 기본 콘티 재시드가 막히지 않으며, `'user'` 또는 origin 없는 기존 선택은 사용자 수정으로 판정한다.

사진 목적과 내부 생성값의 조합:

| contentRole | sectionRole | cutType | 허용 shot·direction |
|---|---|---|---|
| `hero` | `hooking` | `styling` 또는 `horizon` | 사람용 ShotType · front/back/side |
| `benefit` | `hooking` | `styling` 또는 `horizon` | 사람용 ShotType · front/back/side |
| `coordination` | `styling` | `styling` | 사람용 ShotType · front/back/side |
| `fit` | `studio` | `horizon` | 사람용 ShotType · front/back/side |
| `productOverview` | `product` | `product` | ghost · front/back |
| `detail` | `product` | `product` | detail · front/back · 검증된 셀러 원본 근거 |
| `custom` | 현재 놓인 섹션 | `null` | 직접 업로드한 내 이미지. AI 생성 옵션 없음 |

어댑터와 서버는 이 표에 맞지 않는 조합을 그대로 저장하거나 생성기에 보내지 않는다. 섹션·컷·샷이 바뀌면 내부 목적과 유효 옵션을 다시 맞추되, 섹션에서 허용된 사용자 컷 선택을 이전 `contentRole`의 기본 레시피로 덮어쓰지 않는다.

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

빈 이미지 칸은 `genFailed`가 있거나, 템플릿이 아닌 페이지 생성 블록에서 `sourceBlockId`가 저장 콘티의 AI 블록을 가리키면 계획된 칸이다. 그 밖의 빈 사진 칸은 계획 없는 칸이며 프레임 자동 채움 뒤 지운 칸도 포함한다. 정보 블록, 자동 블록, 숨긴 칸과 생성 중인 칸은 AI로 채우기 대상에서 뺀다(ADR-0019).

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
| ComposeMode | `basic` `extended` | 기본형/확장형 | 같은 섹션 구조에서 사진 수만 다름. 이 두 값 외에는 읽기·쓰기 모두 거부 |
| **StoryboardSectionRole** | `hooking` `styling` `studio` `product` | 후킹/스타일링/스튜디오/의류 확인 | 사용자에게 보이는 공식 4섹션, 순서 고정 (ADR-0005) |
| **ContentRole** | `hero` `benefit` `coordination` `fit` `productOverview` `detail` `custom` | 첫 장면/핵심 장점/코디 활용/핏 확인/제품 전체/디테일/직접 구성 | 콘티에서는 비노출 자동값. 후킹은 순서, 스타일링·스튜디오는 섹션과 컷, 의류 확인은 샷으로 파생. 에디터 새 이미지 추가에서도 컷·샷에서 파생하는 내부값 (ADR-0005) |
| **CutType** | `styling` `horizon` `product` | 스타일링컷/호리존컷/제품컷 | 생성 레시피 값. 콘티 인스펙터는 섹션과 무관하게 세 촬영 방식의 선택지를 제공하며 내부 역할은 자동 결정 (ADR-0003~0005) |
| **BlockSource** | `ai` `mine` | AI 생성/내 이미지 | ★ 신설 — '내 이미지'는 컷 종류가 아님 |
| AutoBlockKind | `size` `care` `ai-notice` | 사이즈/세탁/AI 생성 안내 | 2026-06-09 결정 유지 |
| EditorInfoType | `materials` `options` `shipping_returns` `required_notice` `benefit_copy` `fit_copy` `model_info` `fabric_properties` `color_description` `brand_story` `faq` `reviews` `related_products` `promotion` `social` `header` `fit_guide` `size_matrix` | 에디터의 구매 정보·문구·선택 콘텐츠 | `kind='info'`일 때 사용. AutoBlockKind 3종과 겹치지 않음. ★ `header`(타이포 상품명 헤더)·`fit_guide`(핏 도식 비교)·`size_matrix`(키×몸무게 추천 사이즈)는 2026-07-29 상세페이지 분석에서 승격 (§3.5.1) |
| SectionLayout | `stack` `twoColumn` `threeColumn` `grid2x2` `colorCompare` | 세로 1열/2단/3단/2×2/컬러 비교 | 섹션 안 사진 배치 |
| Direction | `front` `back` `side` | 정면/뒷면/사이드 | 모델 컷용 |
| ProductDirection | `front` `back` | 앞면, 뒷면 | 고른 원본 사진의 면. 화면은 `productSourcePhotos`의 사진 고르기로 보여준다(ADR-0008 2026-10-10 개정) |
| ShotType | `full` `medium` | 풀샷/중간샷 | 서비스 착용컷은 두 값만 쓴다. 생성예시 선별 도구의 `medium_knee`는 수집 원본 `knee`·`medium`을 합치는 선별 전용 토큰이며 서비스 저장값이 아니다(ADR-0007) |
| ProductShotType | `ghost` `detail` | 고스트샷/디테일샷 | 고스트샷은 옷 전체이며 기본은 입은 듯한 부피, 플랫레이 생성예시를 고르면 펼친 표현·구도를 따른다. 기존 `flatlay` 입력은 `ghost`로 정규화. `detail`은 셀러 원본과 촬영 대상의 확인 가능한 부분을 생성·검수 근거로 사용하며, 별도 Detail 슬롯 사진을 필수로 요구하지 않음(ADR-0012·0014) |
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
- **컷 종류 노출 범위**: `CutType`은 생성 레시피의 정본이다. 콘티 인스펙터는 현재 섹션에서 허용된 값만 쉬운 라벨로 직접 고르게 하되, 별도 사용자 분류 enum·상단 탭·페이지 섹션으로 복제하지 않는다.
- **생성예시 불변 릴리스 기록(2단계 운영 적용 완료, 2026-07-20; 소비 규칙 2026-07-21 개정)**: `server/tools/release_genexamples.py`가 확정 manifest를 검증해 서버 레지스트리 v2와 위 프론트 카탈로그를 같은 릴리스에서 만든다. QC 승인 예시 192개(`all` 192·`pose` 12·`bg` 14)와 파생 thumb 192개를 R2 불변 경로 `2026-07-19-pilot-qc-01`로 발행했다. 레지스트리 v2는 원본 variant URL·thumb·적용 의류 종류·컷·샷·성별을 함께 가진다. 프론트 `MoodGuide`는 실제 v2 JSON을 소비해 현재 카드의 `cutType`·샷·상품 종류·성별로 정확히 필터링하고 페이지당 최대 6장을 노출한다. 서로 다른 착용 `cutType`을 한 갤러리에 섞지 않는다. 생성예시 원본·재생성본과 R2 릴리스 계약은 바꾸지 않는다.
- **활성 생성예시 집합(2026-07-28 당시)**: 사용자 최종 제외 16개 ID는 활성 선택 원본·서비스 자산·프론트 카탈로그·서버 레지스트리에서 제거한다. 당시 활성 선택은 **191개(착용 169·제품 22)**이고, 생성 manifest의 기대 작업 수는 **529개(`all` 191 + `pose` 169 + `bg` 169)**였다. 당시 프론트·서버 활성 레지스트리는 **176개**를 소비했다. 제외 tombstone은 재선택·재생성을 막는 비활성 차단 기록으로만 남으며 카탈로그 항목으로 반환하지 않는다. 과거 선택 207개와 불변 R2 발행 192개는 감사·롤백 역사이므로 삭제하거나 덮어쓰지 않는다.
- **아우터·원피스 개별 스타일링 보강 릴리스(2026-07-31, 이전 정본)**: 사용자 검토 52개 중 승인 43개와 수정 후 승인 2개를 합친 45개만 새 `all`로 발행하고 제외 7개는 발행하지 않았다. 기존 활성 176개를 함께 봉인한 불변 릴리스 `2026-07-31-individual-styling-01`에서 프론트·서버 활성 레지스트리는 **221개(착용 199·제품 22)**였다. R2는 `all` 221·`pose` 12·`bg` 14·결정적 WebP `thumb` 221, 총 **468개**이고 원격 key·파일 크기 전수 대조에서 누락·추가·불일치가 모두 0이었다. 신규 45개는 여성 아우터 13(풀 8·중간 5), 남성 아우터 16(풀 8·중간 8), 여성 원피스 16(풀 9·중간 7)이며, 이 여섯 스타일링 조합을 공개 조합표에 함께 열었다.
- **상의·하의 개별 스타일링 보강 릴리스(2026-08-03, 현재 정본)**: 사용자 최종 확정 45개(여성 하의 11·여성 상의 12·남성 하의 11·남성 상의 11)를 포함한 불변 릴리스 `2026-08-03-individual-styling-01`이 현재 프론트·서버 카탈로그의 정본이다. 두 카탈로그는 같은 **254개(착용 244·제품 10)** ID를 소비한다. R2는 `all` 254·`pose` 12·`bg` 14·결정적 WebP `thumb` 254, 총 **534개**다. 2026-08-03 재검증에서 공개 R2 객체 534개를 다시 내려받아 로컬 봉인본과 SHA-256을 전수 대조했고 누락·추가·바이트 불일치가 모두 0이었다. 제작자 QC HOLD 8개는 사용자의 묶음별 최종 확정을 오너 승인으로 적용하되 원래 판정과 사유를 감사 기록에 보존한다.
- **변경**: `subCategories`를 `{ value, label }[]`로 (현재 한국어 문자열 배열)
- **유지**: `clothingTypes` `genders` `fits` `shotTypes` `angleSlots` `angleLabels` `swatchColors` `composeModes` `genExamples` `frames` `shapes` `lines` `fonts` `downloadOptions` `models` `creditCosts`(로컬 기본값은 `lib/limits.js`; 에디터 HTTP 가격은 서버 `credit-quote`로 확인). `genExamples`는 `cutType`·`shot`과 `applicableClothingTypes`의 현재 상품 `clothingType` 포함 여부로 필터링하고, 착용컷은 `gender`를 정확히 맞추되 제품컷의 `gender=null`은 성별 공용으로 취급한다. product detail의 `detailSubject`처럼 카드 조건보다 세밀한 축은 rank 순 라운드로빈으로 섞는다. styling의 `mood`는 자동 배정과 다시 뽑기 후보를 고를 때만 섞는다. 자동 배정과 다시 뽑기는 이 순서의 앞 6장에서 고르고, 갤러리는 조건에 맞는 예시 전부를 페이지당 최대 6장씩 보여 준다.

`creditCosts`의 에디터 키는 `{ editorImage: 19, editorEdit: 5, editorSimilar: 8 }`이다. 같은 세 키를 인증·프로젝트 소유 확인을 거친 `GET /v1/projects/{id}/credit-quote`에서 반환한다(환경변수 설정값 반영). `editorEdit`는 착용컷·업로드의 색상과 열림 정도, `editorSimilar`는 비슷한 컷, `editorImage`는 새 이미지·고스트·디테일·빈 칸 등 나머지 행동의 표시 단가다. HTTP 화면은 로컬 새 키를 합치지 않고 서버 견적에서 새 키가 빠지면 `editorImage`로 대체한다. 서버는 검증·복원한 요청으로 예약액을 결정하며 성공 확정·실패 해제는 그 예약액을 그대로 쓴다. 원장 `action_key=editorImage`와 같은 키 재요청·409 키 재사용 거절 계약은 유지한다.

- **폐기 예정**: `backgrounds`(콘티 배경 제거), `extendedColorPriority`(미사용), `cutSources`
- **폐기됨(2026-10-10)**: `directions`(방향 칩, ADR-0015), `productDirections`(`productSourcePhotos`로 대체), `varyOptions`(현재 이미지 수정의 배경, 포즈, 표정 카드, ADR-0016 개정)

---

## 6. API 계약

경계는 `src/lib/api/index.js` 하나다. 모든 함수는 Promise를 반환하고, 장시간 작업은 `onProgress(0..100)` 콜백을 받는다. `onStep(steps)`는 현재 mock 상세페이지 생성에만 있으며 HTTP 배선은 TODO다. 화면은 mock/db·placeholder를 직접 import하지 않는다.

**크레딧 규약**: 크레딧을 소모하는 API는 `{ data, credits }` 봉투로 반환한다. `credits`는 차감 후 잔액이며, 화면은 이 값을 `store.syncCredits()`로 반영한다. 실패(throw) 시 차감 없음 — 환불·재시도 정책은 백엔드 시점에 확정(PRD §12.2).

**유료 job 멱등 규약**: 장시간 유료 작업(`generateMannequins`, `generateDetailPage`)은 ① 같은 프로젝트에서 **진행 중**일 때 다시 호출되면 새 작업을 시작하지 않고 진행 중인 job에 합류시키고(진행 콜백 공유, 차감 1회), ② **이미 완료**된 뒤 다시 호출되면(마네킹 후보 존재 / `project.status='done'`) 재실행·재차감 없이 기존 산출물과 현재 잔액을 반환한다, ③ **실패(error)로 끝난 뒤** 다시 호출되면 새 job을 시작한다(크레딧 재예약·재차감 대상) — 실패한 job은 합류·재사용하지 않는다. StrictMode 이중 mount, 생성 중 이탈 후 재진입, 완료 후 라우트 재방문 어느 경우도 재차감으로 이어지지 않기 위한 **서버 책임**이며, 실서버는 project별 job 레코드로 구현한다. `credits`가 델타가 아니라 **잔액**인 것도 합류·재호출 응답의 멱등성을 위해서다. (완료 후 전체 재생성은 의도적으로 플로우에 없다 — PRD §10.17, 필요 컷은 에디터에서 추가한다.)

| 함수 | 입력 | 반환 | 크레딧/비고 |
|---|---|---|---|
| `createProject()` | — | `Project` | 새 제작 시작. 구 `resetDraft()` 대체 |
| `getProject(projectId)` | | `Project` | |
| `patchProject(projectId, patch)` | 선택값 patch | `Project` | 수용 필드 **화이트리스트**: composeMode·copywriting·selectedMannequinId만. `status`는 생성 job의 부수효과로만 바꾸고 `adjustCount`는 과거 저장값 읽기 전용으로 새 요청에 받지 않는다. frontend_state_model §6 |
| `getLibrary()` | | `ProjectSummary[]` | |
| `getAccount()` | | `Account` | |
| `getCatalogs()` | | `Catalogs` | |
| `getProduct(projectId)` | | `Product` | |
| `saveProduct(projectId, patch)` | | `Product` | 실측·의류 종류 포함 (Product 소유) |
| `analyzeProduct(projectId, { onProgress })` | | `Analysis` | 실측은 항상 null로 반환 |
| `saveAnalysis(projectId, patch)` | | `Analysis` | |
| `getMatchClothing(projectId)` | | `MatchClothing[]` | 각 항목의 `isCustom`·`isCompatible`을 보존한다. 분석·마네킹·콘티 화면이 같은 매칭 선택을 읽는다. |
| `getMannequins(projectId)` | | `MannequinCut[]` | |
| `generateMannequins(projectId, { onProgress })` | | `{ data: MannequinCut[], credits }` | `mannequinGenerate` · 페이지 최초 진입 시 자동 호출 |
| `regenerateMannequin(projectId, { fitProfile, onProgress })` | 확인 스텝에서 확정한 FitProfile(축+matchCut) | `{ data: MannequinCut[], credits }` | `mannequinGenerate` · 요청 시에는 잡 스냅샷만 남기고, 성공 종결에서 새 버전과 analysis.fitProfile을 함께 확정·자동 선택 (구 `regenerateMannequins` 대체) |
| `getStoryboard(projectId)` | | `StoryboardBlock[]` | 저장된 배열을 반환. 화면은 working copy를 만들기 전에 v2를 v3 공식 4섹션으로 승격하고 허용 컷·샷과 내부 역할을 정규화 |
| `saveStoryboard(projectId, blocks)` | | `StoryboardBlock[]` | 생성 CTA 시 반드시 호출. taxonomyVersion=3만 저장 |
| `generateDetailPage(projectId, { onProgress, onStep })` | | 완성분과 빈 칸의 `{ data: EditorBlock[], credits, creditsCharged, partial? }` | 일부 AI 컷 자리가 비면 완성분을 발행하고 그 유료 컷 값을 견적에서 뺀다(ADR-0017). 전부 실패만 미발행·예약 전액 해제. QC 미통과 디테일의 최선 생성 후보는 표시를 남겨 제공·정상 차감한다. AI 생성 컷 1~4장은 최소 5컷 값, 5장 이상은 실제 생성 컷 수 × `storyboardPerCut`. 내 이미지 블록은 제외 |
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
  retryBlockId?: string            // ADR-0017, 저장 콘티의 실제 빈 칸 복원과 활성 작업 합류
  detailTargetId?: string | null   // product/detail만. 서버 소유 detailRecommendations의 ID; 원본 해시·방향 검증 후 해석
  detailTargetOrigin?: 'auto' | 'user' // 콘티 추천/수동 선택 구별. 클라이언트 사실·좌표는 생성 권한이 없음
  colorId: string                  // 구 group('색상 1') 대체
  sectionRole?: StoryboardSectionRole  // 향후 섹션에 바로 삽입하는 경로용. 현재 UI는 의류 탭에 먼저 추가하므로 생략
  contentRole: ContentRole          // inferContentRole(cutType, shot)로 내부 자동 결정 — UI 선택 아님(콘티보드와 동일 규칙).
                                    // '첫 장면(hero)·핵심 장점(benefit)' 목적은 에디터 신규 생성에서 제거(2026-08-13 오너 결정)
  cutType: CutType                 // 에디터도 콘티보드처럼 컷 종류를 UI에서 직접 선택(2026-08-13 개편). 발행 예시 없는 컷 종류는 비활성
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
| 400 | `fill_slot_unsupported` | 정보 블록, 자동 블록, 숨긴 칸 | 이 칸은 AI로 채울 수 없어요. |
| 400 | `fill_slot_unsupported` | 생성 중인 칸 | 이 칸은 지금 만들고 있어요. |
| 409 | `page_generation_active` | 상세페이지 생성 중 | 상세페이지를 만드는 중이에요. 끝나면 눌러 주세요. |
| 409 | `fill_unavailable` | 만들 수 있는 컷 없음 | 이 칸에 만들 수 있는 컷이 없어요. |

크레딧 부족의 402와 실제 모델 생성 닫힘의 409는 기존 규약을 따른다.

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

모든 에디터 이미지 성공 자산은 `assets.metadata.generation`에 실제 생성 설정을 남긴다. 필드 목록은 서버의 `content_roles.GENERATION_RECIPE_FIELDS` 하나이며 아래 22키를 모두 담고 값이 없으면 `null`이다. 열림 보정, 예시 방향 적용, 시그니처 재배정이 끝난 뒤 값을 쓴다.

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

새 컷은 `recipeFrom='self'`다. 비슷한 컷과 같은 부위 다시 촬영은 공용 `asset_recipe.resolve_asset_recipes`가 원본 설정을 찾으면 상속하고 `recipeFrom='anchor'`로 남기며, 못 찾으면 `recipe=null`이다. 직접 올린 자산은 `assets.source='upload'`로 판정하고 새 기록을 쓰지 않는다. 색상과 열림 편집은 `origin='editor_color'` 또는 `origin='editor_closure'`, `autoSelected=false`, 고른 원본의 `sourceAssetId`, `recipeFrom='anchor'`를 남긴다. 원본 레시피의 바꾼 필드만 고치며 레시피가 없으면 모든 필드를 `null`로 두고 바꾼 값만 채운다. 색상 결과의 의류 그룹은 목표 색이고 열림과 비슷한 컷은 원본의 의류 색 그룹을 물려받는다. 상세페이지의 `page` 기록은 후속 작업으로 미룬다.

---

## § 게시(detail publication)

| 필드(DB) | 의미 |
| --- | --- |
| `id`, `user_id`, `project_id` | 게시·소유자·프로젝트 UUID. 프로젝트 삭제 시 `project_id`만 null로 바뀌고 게시 이미지는 유지한다. |
| `version`, `status` | 프로젝트 안의 불변 순번(프로젝트와 복합 unique), `pending`·`ready`·`deleted`. |
| `preset`, `width` | `smartstore`·`coupang`·`high` 폭 프리셋과 렌더 폭(px). |
| `slice_count`, `total_bytes` | 조각 수와 전체 바이트. |
| `product_name`, `editor_revision` | 게시 시점의 상품명과 에디터 리비전. |
| `files` | `{index,r2_key,bytes,width,height,asset_id}` 배열. 조각은 `assets`에도 `source='export'`, `visibility='public'`, `metadata.publication_id`로 등록한다. |
| `expires_at` | 보관 만료 시각. 구독 중이면 null. |
| `created_at`, `ready_at`, `deleted_at` | 생성·게시 완료·삭제 완료 시각. |

상태 전이는 `pending → ready → deleted`이며 30분 동안 완료되지 않은 `pending`도 `deleted`로 정리한다. 재게시는 새 버전을 만들고 기존 게시를 바꾸지 않는다.

`deleted`이면서 `deleted_at=null`이면 이미지와 CDN 정리가 아직 끝나지 않은 삭제 의도다. 만료 정리는 게시별 현재 구독 확인과 삭제 의도 저장을 프로필 갱신과 같은 잠금으로 처리한다. 그 전에 재구독이 끝나면 보존하고, 삭제 의도가 먼저 확정된 버전은 이후 재구독으로 복구하지 않는다. 저장소 정리에 실패해도 행과 삭제 의도를 유지해 재시도한다. 유효한 업로드 주소의 늦은 전송을 정리할 수 있도록 명시적 삭제와 탈퇴는 정리 표시를 잠시 보존한다.

R2 키는 `pub/<user_id>/<publication_id>/<index 2자리>.jpg`이며 JPEG와 `Cache-Control: public, max-age=31536000, immutable`을 고정한다.

만료 규칙은 `profiles.plan`을 기준으로 한다. free가 아니면 `expires_at=null`, 처음부터 free면 `created_at+6개월`, 구독 종료를 처음 관찰한 free 게시면 관찰 시각+6개월이다. 이미 정해진 free 만료일은 유지하고 삭제 의도 확정 전에 재구독하면 null로 되돌린다. 워커는 하루 한 번 만료일을 계산하고 한 시간마다 정리한다. 탈퇴 시에는 즉시 삭제를 시도하고 실패는 재시도한다.

## § 쇼핑몰 연결

`MALL_CONNECT_ENABLED` 기본값은 false이며 꺼져 있으면 `/v1/malls` 라우트와 몰 메뉴를 제공하지 않는다. 에이블리와 스마트스토어는 `unavailable` 표시만 제공한다.

| 저장 필드 | HTTP 필드 | 의미 |
|---|---|---|
| `mall` | `mall` | `zigzag`, `ably`, `smartstore` |
| `status` | `status` | 저장은 `connected`, `revoked`. 활성 연결이 없으면 응답은 `none` |
| `shop_id`, `shop_name` | `shopName` | 인증키로 조회한 스토어. ID는 서버에서만 사용 |
| `key_hint` | `keyHint` | Access Key 끝자리 최대 8자. 원문 전체가 될 만큼 짧으면 null |
| `access_key_enc`, `secret_key_enc` | 제공하지 않음 | 전용 `MALL_KEYS_KEK`와 `pgp_sym_encrypt`로 암호화 |
| `permissions` | `permissions` | 셀러용 권한 문구 배열 |
| `connected_at`, `last_registered_at` | `connectedAt`, `lastRegisteredAt` | ISO 8601 또는 null |
| `mall_defaults.shop_id` | 제공하지 않음 | 주소 기본값이 속한 스토어 ID |
| `mall_defaults.completed_items` | `defaults.completedItems` | 완료한 `shipping`, `returns`, `addresses`, `disclosure` |

연결은 새 키로 스토어 조회를 마친 뒤 저장한다. 인증정보 변경은 검증이 성공한 경우에만 기존 연결을 `revoked`로 바꾸고 새 `connected` 행을 같은 트랜잭션에 만든다. 실패하면 기존 연결을 유지한다. 연결할 스토어가 `mall_defaults.shop_id`와 다르면 `addresses`를 비우고 완료 항목에서 제외한 뒤 스토어 ID를 갱신한다. 다른 기본 설정과 같은 스토어의 재연결 설정은 유지한다. 인증정보 변경에서 기존 연결과 스토어가 다르면 400 `shop_mismatch`로 거절한다. 연결 해제와 탈퇴는 암호문을 즉시 지우고 `revoked`로 바꾸며 기본 설정을 보존한다. 응답과 로그에 키 원문을 넣지 않는다.

`GET /v1/malls`는 `{items:[{mall,status,shopName,keyHint,permissions,connectedAt,lastRegisteredAt,defaults}]}`이며 `defaults`는 저장된 값과 `completedItems`, `total:4`를 포함한다. 저장된 값이 없으면 각 묶음은 빈 객체다. `POST /v1/malls/zigzag/connect`, `POST .../rotate`의 입력은 `{accessKey,secretKey}`이며 앞뒤 공백 제거 후 각각 1~256자다. 두 동작을 합쳐 사용자당 시간당 10회로 제한한다. `DELETE /v1/malls/zigzag`는 기본 설정을 지우지 않는다.

`GET /v1/malls/zigzag/addresses`는 `{id,name,address,postcode,is_default}[]`를 반환한다. `PUT /v1/malls/zigzag/defaults`는 아래 값을 부분 저장하고 누적 저장값으로 완료 항목을 계산한다.

| 묶음 | 필드 |
|---|---|
| `shipping` | `feeType:FREE\|CHARGED\|CONDITIONAL_FREE`, `baseFee`, `jejuFee`, `isolatedFee`, `conditionalAmount` |
| `returns` | `partialFee`, `totalFee`, `exchangeFee` |
| `addresses` | `shippingAddressId`, `returnAddressId` |
| `disclosure` | `country`, `manufacturer`, `asManager`, `phone` |
| 공통 선택 | `taxType:TAX\|FREE` 기본 TAX, `productOrigin:domestic\|imported` 기본 domestic |

금액은 0 이상 정수다. 조건부 무료는 `conditionalAmount`가 필수다. 배송비의 무료 정책은 `baseFee` 없이 완료할 수 있다. 전화번호는 숫자와 하이픈만 허용한다. 오류는 `{error:{code,message}}`이며 연결 오류 코드는 `invalid_key`, `missing_permission`, `unreachable`, `unknown`이다. 원격 오류 원문은 응답에 포함하지 않는다.

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
