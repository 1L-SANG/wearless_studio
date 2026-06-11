# 공통 데이터 계약 (Common Data Contract)

> 상태: 확정 (2026-06-11) · 근거: `documents/PRD.md` §13, mock 구현(`src/lib/types.js`, `src/mock/*`), 2026-06-11 결정 세션
> 결정 기록: `docs/adr/0001~0003`, 용어는 `/CONTEXT.md`
> 적용 방식: 이 문서가 계약의 원본이다. `src/lib/types.js`와 mock 레이어를 이 계약에 맞추고, 백엔드(FastAPI·Supabase·R2)와 AI 파이프라인은 같은 shape를 구현한다. 현행 코드와의 차이는 §7에 갭으로 명시한다 — 문서와 코드가 다르면 문서가 맞다.

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
  adjustCount: number              // 마네킹 조정 사용 횟수, 프로젝트에 영속 (max LIMITS.mannequinAdjustMax)
  createdAt: string                // ISO
  updatedAt: string                // ISO
}

ProjectSummary {                   // 보관함 카드 (구 LibraryItem)
  id: string
  title: string
  cover: string                    // 대표 이미지 (파생: 첫 에디터 블록 이미지)
  clothingType: ClothingType
  blockCount: number               // 구 blocks (배열과 혼동 방지 위해 개명)
  status: ProjectStatus
  updatedAt: string                // ISO — '2시간 전'은 표시 파생
}
```

- `composeMode` `copywriting` `selectedMannequinId` `adjustCount`는 **서버 동기화 대상 선택값**이다. 프론트는 Zustand에 작업 사본을 두고 변경 시 `patchProject`로 동기화한다 (→ `frontend_state_model.md`).
- 조정 횟수 2회 제한은 **프로젝트당**이다. 재진입·새로고침에도 유지되고, 재생성도 동일 카운트를 소모한다. PRD §7.4의 "세션"은 "한 프로젝트의 마네킹 단계"로 해석한다. (단, 새로고침 유지는 영속 백엔드 전제 — 현 in-memory mock에서 F5는 "서버 재시작"과 같아서 프로젝트·크레딧·횟수가 함께 일관되게 리셋된다. SPA 재진입 유지는 mock에서도 동작하며 E2E로 검증됨.)

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
  swatchId: SwatchId | null        // 정체성. null = '색상 미정'
  isBase: boolean                  // 기준 색상 여부
  images: ImageAsset[]
}
// 파생(저장 안 함): name·hex ← swatchId, monotone ← MONOTONE_SWATCHES 포함 여부
// 폐기: name(자유 텍스트), isMain. AI 색상명 추정 기획은 'AI의 swatchId 추천'으로 대체.

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

```ts
Analysis {
  projectId: string
  suggestedName: string
  subCategory: SubCategory | null  // 영문 토큰화 (§4). dress는 null
  targetGenders: Gender[]
  fit: Fit
  materials: Material[]            // Material { name: string(자유 텍스트), ratio: number(%) }
  sellingPoints: string[]          // 자유 텍스트, max LIMITS.sellingPointMax(5)
  aiSuggestedPoints: string[]      // max LIMITS.aiSuggestedPointMax(2)
  selectedModelId: string          // catalogs.models 참조
  matchCandidates: MatchClothing[] // AI가 제안한 매칭 의류 후보
  matchSelections: { clothingId: string, role: 'main' | 'sub' }[]   // max 2
  washCare: string
  locked: boolean
}

MatchClothing { id: string, name: string, thumb: string }
// 폐기: Analysis.clothingType / measurements / measurementsUnknown (Product 소유),
//       Analysis.models (catalogs.models 참조), MatchClothing.selected / selOrder (matchSelections로 분리)
```

### 3.3 MannequinCut — 마네킹 후보와 조정 상태

라벨 문자열('정핏', '더 여유롭게')을 상태로 저장하지 않는다. 조정 상태는 enum, 표시 라벨은 파생.

```ts
MannequinCut {
  id: string                       // `${candidate}-${version}`
  candidate: 'A' | 'B'
  version: number
  src: string
  baseFit: Fit                     // 후보가 생성될 때의 핏 (구 fitLabel '정핏'/'슬림핏')
  fitAdjust: AdjustFit | null      // 원본 대비 누적 조정 상태. null = 원본
  lengthAdjust: AdjustLength | null
  matchAdjust: {
    clothingId: string
    fitAdjust: AdjustFit | null
    lengthAdjust: AdjustLength | null
  } | null
}
// 폐기: selected (선택은 project.selectedMannequinId가 소유),
//       fitLabel / lengthLabel / matchName / matchFit / matchLength / matchLabel (전부 파생)
```

### 3.4 StoryboardBlock — 콘티보드 블록

```ts
StoryboardBlock {
  id: string
  kind: BlockKind                  // 섹션 역할. 사용자 추가 블록 기본 'info'
  source: BlockSource              // 'ai' | 'mine'
  cutType: CutType | null          // source='mine'이면 null (ADR-0003)
  direction?: Direction | ProductDirection      // cutType에 따라 옵션 셋이 다름
  shot?: ShotType | ProductShotType
  colorId?: string                 // ColorGroup.id (단수 — 컬러별 컷은 블록을 색상마다 분리)
  pose: PoseId                     // 기본 'auto' (구 _pose)
  matchIds: string[]               // 매칭 의류 후보 id
  faceExposure: FaceExposure       // 기본 'same'
  angle: CameraAngle               // 기본 'same'
  refImages: string[]              // '내 레퍼런스' 업로드 (생성 입력에 포함)
  ownImages: string[]              // source='mine'의 직접 업로드 이미지
  thumb: string                    // 예시 썸네일 (서버/목 생성, 최종 이미지 아님)
}
// 폐기: title(kind·cutType 라벨에서 파생), poseThumb / poseLabel(카탈로그 파생),
//       bgThumb / bgLabel(PRD §8.5에서 배경 제거 — 보존 불필요), colorIds(미사용 잔재)
```

### 3.5 EditorBlock / Element — 에디터 캔버스

```ts
EditorBlock {
  id: string
  name: string                     // 표시명
  kind: BlockKind | AutoBlockKind  // auto 블록은 'size' | 'care' | 'ai-notice'
  auto?: true                      // 자동 안내 블록 여부
  bg: string                       // hex
  h: number                        // 고정 높이(px, 기준 폭 1000)
  elements: Element[]              // 배열 순서 = z-order (뒤가 위)
}

Element (공통) {
  id: string
  type: ElementType
  x: number  y: number  w: number  h: number    // 블록 좌표계 (기준 폭 1000)
  rotate?: number                  // (-180, 180]
  opacity?: number                 // 0~1
  hidden?: boolean
  locked?: boolean
}
Element (type='image') + {
  src: string | null               // null = 빈 슬롯 (프레임)
  radius?: number
  cutType: CutType | null          // 생성 시 기록. null = 직접 업로드(미상)
  crop?: { ox: number, oy: number, iw: number, ih: number }   // 프레임 기준 원본 오프셋/크기
}
Element (type='text') + {
  text: string                     // 자유 텍스트
  style: TextStyle                 // h는 렌더 시 auto
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
  font: 'Pretendard' | 'Cal Sans' | 'Roboto Mono'
  size: number  weight: number  color: string
  opacity?: number  tracking?: number  lineHeight?: number
  align?: 'left' | 'center' | 'right'
  italic?: boolean  underline?: boolean  strike?: boolean
  list?: 'none' | 'bullet' | 'ordered'
  bg?: string | 'none'             // 하이라이트
}
```

### 3.6 Wardrobe — 에디터 의류 탭

그룹 키는 표시 문자열('색상 1')이 아니라 **colorId**다. 색상 그룹 외 이미지는 `'misc'`(표시: '기타').

```ts
Wardrobe = Record<string /* colorId | 'misc' */, WardrobeImage[]>
WardrobeImage { id: string, src: string, ai?: boolean, cutType: CutType | null }
```

### 3.7 GenJob / Account

```ts
GenJob {
  id?: string                      // 백엔드 도입 시 job 추적용
  status: JobStatus
  progress: number                 // 0~100
  steps: GenStep[]                 // GenStep { key: string, label: string, status: JobStatus }
  composition: BlockKind[]
}
// step key: info | prep | styling | horizon | product | copy | assemble

Account { name: string, avatar: string, credits: number, plan: 'Free' | 'Pro' | 'Team' }
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
| ComposeMode | `simple` `basic` `extended` | 간단형/기본형/확장형 | |
| **CutType** | `styling` `horizon` `product` | 스타일링컷/호리존컷/제품컷 | ★ 신설 — `daily`·`studio` 폐기 (ADR-0003) |
| **BlockSource** | `ai` `mine` | AI 생성/내 이미지 | ★ 신설 — '내 이미지'는 컷 종류가 아님 |
| BlockKind | `hook` `selling` `styling` `horizon` `product` `info` | 후킹/셀링포인트/스타일링/호리존/제품/정보 | CutType과 직교 (섹션 역할) |
| AutoBlockKind | `size` `care` `ai-notice` | 사이즈/세탁/AI 생성 안내 | 2026-06-09 결정 유지 |
| Direction | `front` `back` `side` | 정면/뒷면/사이드 | 모델 컷용 |
| ProductDirection | `front` `back` | 앞면/뒷면 | 제품컷용 ★ 카탈로그 승격 |
| ShotType | `full` `knee` `medium` `close` | 풀샷/무릎샷/미디움샷/확대샷 | 모델 컷용 |
| ProductShotType | `ghost` `hanger` `flatlay` | 고스트컷/행거컷/플랫레이샷 | ★ 카탈로그 승격 |
| **ProjectStatus** | `draft` `generating` `done` | 초안/생성 중/완료 | ★ 신설 |
| JobStatus | `idle` `running` `done` `error` | | |
| ElementType | `image` `text` `shape` `line` | | |
| AngleSlot | `Front` `Back` `Detail` `Fit` | 앞면/뒷면/디테일/착용 이미지 | 기존 토큰 유지 |
| SwatchId | `white` `gray` `black` `ivory` `beige` `brown` `red` `yellow` `green` `blue` `navy` `pink` | 12색 팔레트 | MONOTONE_SWATCHES = white·gray·black·ivory·beige |
| **MeasurementKey** | `totalLength` `shoulderWidth` `chestWidth` `sleeveLength` `waistWidth` `hipWidth` `thighWidth` `rise` `hemWidth` `armhole` | 총장/어깨너비/가슴단면/소매길이/허리단면/엉덩이단면/허벅지단면/밑위/밑단단면/암홀 | ★ 한국어 키 → 토큰화 |
| **AdjustFit** | `slimmer` `looser` | 더 슬림하게/더 여유있게 | '현재' = 파라미터 생략 |
| **AdjustLength** | `shorter` `longer` | 더 짧게/더 길게 | |
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

화면은 옵션 셋을 하드코딩하지 않고 `getCatalogs()`로 받는다. 변경 사항:

- **추가**: `productDirections`, `productShotTypes`(현재 콘티 인스펙터·AI 패널·변형 패널 3곳에 하드코딩), `measurementLabels`(key → 한국어), `cutTypes`(구 `cutSources` 대체 — `mine` 제외 3종, '내 이미지'는 UI에서 source 토글로 합성)
- **변경**: `subCategories`를 `{ value, label }[]`로 (현재 한국어 문자열 배열)
- **유지**: `clothingTypes` `genders` `fits` `directions` `shotTypes` `angleSlots` `angleLabels` `swatchColors` `composeModes` `poses` `varyOptions` `genExamples` `frames` `shapes` `lines` `fonts` `downloadOptions` `models` `creditCosts`(원본은 `lib/limits.js`)
- **폐기 예정**: `backgrounds`(콘티 배경 제거 — `varyOptions.bg`가 에디터 변형용으로 대체), `extendedColorPriority`(미사용), `cutSources`

---

## 6. API 계약

경계는 `src/lib/api/index.js` 하나다. 모든 함수는 Promise를 반환하고, 장시간 작업은 `onProgress(0..100)` / `onStep(steps)` 콜백을 받는다(실서버 어댑터가 job 폴링을 콜백으로 변환). 화면은 mock/db·placeholder를 직접 import하지 않는다.

**크레딧 규약**: 크레딧을 소모하는 API는 `{ data, credits }` 봉투로 반환한다. `credits`는 차감 후 잔액이며, 화면은 이 값을 `store.syncCredits()`로 반영한다. 실패(throw) 시 차감 없음 — 환불·재시도 정책은 백엔드 시점에 확정(PRD §12.2).

**유료 job 멱등 규약**: 장시간 유료 작업(`generateMannequins`, `generateDetailPage`)은 ① 같은 프로젝트에서 **진행 중**일 때 다시 호출되면 새 작업을 시작하지 않고 진행 중인 job에 합류시키고(진행 콜백 공유, 차감 1회), ② **이미 완료**된 뒤 다시 호출되면(마네킹 후보 존재 / `project.status='done'`) 재실행·재차감 없이 기존 산출물과 현재 잔액을 반환한다. StrictMode 이중 mount, 생성 중 이탈 후 재진입, 완료 후 라우트 재방문 어느 경우도 재차감으로 이어지지 않기 위한 **서버 책임**이며, 실서버는 project별 job 레코드로 구현한다. `credits`가 델타가 아니라 **잔액**인 것도 합류·재호출 응답의 멱등성을 위해서다. (완료 후 전체 재생성은 의도적으로 플로우에 없다 — PRD §10.17, 필요 컷은 에디터에서 추가한다.)

| 함수 | 입력 | 반환 | 크레딧/비고 |
|---|---|---|---|
| `createProject()` | — | `Project` | 새 제작 시작. 구 `resetDraft()` 대체 |
| `getProject(projectId)` | | `Project` | |
| `patchProject(projectId, patch)` | 선택값 patch | `Project` | composeMode·copywriting·selectedMannequinId·adjustCount·status |
| `getLibrary()` | | `ProjectSummary[]` | |
| `getAccount()` | | `Account` | |
| `getCatalogs()` | | `Catalogs` | |
| `getProduct(projectId)` | | `Product` | |
| `saveProduct(projectId, patch)` | | `Product` | 실측·의류 종류 포함 (Product 소유) |
| `analyzeProduct(projectId, { onProgress })` | | `Analysis` | 실측은 항상 null로 반환 |
| `saveAnalysis(projectId, patch)` | | `Analysis` | |
| `draftWashCare(projectId)` | | `string` | |
| `getMannequins(projectId)` | | `MannequinCut[]` | |
| `generateMannequins(projectId, { onProgress })` | | `{ data: MannequinCut[], credits }` | `mannequinGenerate` |
| `adjustMannequin(projectId, { baseId, fitAdjust?, lengthAdjust?, matchAdjust?, onProgress })` | enum 값만 | `{ data: MannequinCut, credits }` | `mannequinAdjust` · 서버가 adjustCount 증가 |
| `regenerateMannequins(projectId, { onProgress })` | | `{ data: MannequinCut[], credits }` | `mannequinGenerate` · adjustCount 증가 |
| `getStoryboard(projectId)` | | `StoryboardBlock[]` | project.composeMode 기반으로 구성 |
| `saveStoryboard(projectId, blocks)` | | `StoryboardBlock[]` | 생성 CTA 시 반드시 호출 |
| `generateDetailPage(projectId, { onProgress, onStep })` | | `{ data: EditorBlock[], credits }` | `storyboardPerCut × source='ai'인 블록 수` — 내 이미지 블록은 생성 작업이 없어 차감 제외 |
| `getEditorBlocks(projectId)` | | `EditorBlock[]` | |
| `saveEditorBlocks(projectId, blocks)` | | `void` | 구현됨 — 저장 버튼 + 1.5s 디바운스 자동 저장 + 이탈 시 플러시 |
| `getWardrobe(projectId)` | | `Wardrobe` | |
| `generateImage(projectId, req)` | `NewCutRequest \| VaryRequest` (아래) | `{ data: WardrobeImage, credits }` | `editorImage` |
| `uploadAsset(file)` | `File` | `ImageAsset` | 실서비스 계약 — mock은 `pickAnyImage()`(mock 전용 헬퍼)로 대행 |
| `download(projectId, format)` | `'long' \| 'zip'` | `{ ok }` | 실제 렌더링은 P1 |

```ts
NewCutRequest {                    // AI 탭 '새 컷 추가'
  mode: 'new'
  colorId: string                  // 구 group('색상 1') 대체
  cutType: CutType
  direction: Direction | ProductDirection
  shot: ShotType | ProductShotType
  modelId: string
  refImages?: string[]
}
VaryRequest {                      // AI 탭 '현재 컷 변형' — changes 빈 배열 = '비슷한 컷 만들기'
  mode: 'vary'
  source: { src: string, cutType: CutType }    // 미상이면 styling으로 가정해 보냄
  changes: { type: 'direction' | 'shot' | 'pose' | 'face' | 'bg', value: string }[]
  refBg?: string
}
```

**에러 규약**: 실패는 `Error`를 throw하고 `message`는 사용자에게 그대로 보여줄 한국어 문장이다.

---

## 7. 현행 코드와의 갭 (마이그레이션 TODO)

계약 확정(2026-06-11) 시점의 차이 목록. 같은 날 P0 구현으로 대부분 반영됨 — 상태를 함께 표기한다.

1. ✅ **project 부재** — `createProject`/`getProject`/`patchProject` 신설, `/editor/:id`를 projectId로 사용. (반영됨)
2. ✅ **컷 토큰** — `cutType: styling|horizon|product` + `source: ai|mine`로 통일, `CutSource`·`daily`·`studio` 제거. UI 라벨 '일상컷' → '스타일링컷'. **PRD §8.4 탭 명칭 갱신은 남음.**
3. ✅ **한국어 저장값** — 조정값 enum(slimmer/looser·shorter/longer), `MannequinCut` baseFit/fitAdjust/lengthAdjust/matchAdjust, wardrobe 키 colorId/'misc', `MeasurementKey` 영문화 + `measurementLabels`, `subCategories` {value,label}. (반영됨)
4. 🔶 **Product/Analysis 중복** — mock `saveAnalysis`가 Product 소유 필드(clothingType·measurements·measurementsUnknown)를 product로 동기화하도록 반영. analysis 레코드에서 해당 필드를 **제거**하는 일원화는 남음.
5. ✅ **saveStoryboard 미호출** — 생성 CTA에서 저장 후 이동. (반영됨)
6. ✅ **generateDetailPage가 콘티를 무시** — 저장된 storyboard 기반 `buildEditorBlocksFromStoryboard`로 생성, 카피라이팅·자동 블록 반영. (반영됨)
7. ✅ **크레딧 봉투** — 소모 5종이 `{ data, credits }` 반환 + mock 차감, 화면은 `store.syncCredits`로 단일화(에디터 로컬 account 제거). (반영됨)
8. ✅ **제품컷 옵션 하드코딩 3곳** — `catalogs.productDirections`/`productShotTypes`로 이동. (반영됨)
9. 🔶 **경계 위반 import** — id 생성기는 `lib/ids.js`로 이동 완료(화면·mock 공용, `DB.uid` 제거). `Placeholder` 직접 import(콘티 새 블록 썸네일·분위기 예시)는 분위기 예시의 운영자 시드 전환 시 함께 정리.
10. ✅ **콘티 '내 레퍼런스' 휘발** — `MoodGuide` 제어형 전환: 콘티는 `block.refImages`에 저장, 에디터 AI 패널은 `NewCutRequest.refImages`로 생성 입력에 포함. (반영됨)
11. ✅ **typedef 갱신** — `lib/types.js`가 본 계약을 미러링. TS 전환 시 이 문서가 `.ts` 계약의 사양이 된다. (반영됨)
12. ✅ **ProjectSummary** — `updatedAt` ISO화('N시간 전'은 화면 파생), `blocks` → `blockCount`. (반영됨)
13. **죽은 코드 표기만** — `AnalysisForm.jsx`의 미사용 `Analysis` 라우트 컴포넌트(구 시그니처 호출 포함), `colorIds` 잔존 읽기. `catalogs.backgrounds`·`extendedColorPriority`는 소비처가 없어 db 재작성 시 제거됨.

---

## 8. 백엔드 확장 노트 (참고용, 설계 아님)

- Supabase 테이블 후보: `projects`, `products`, `analyses`, `mannequin_cuts`, `assets`(R2 키 보유), `credit_ledger`. `storyboard`·`editor_blocks`는 초기에 projects의 jsonb 컬럼으로 시작해도 계약과 충돌하지 않는다.
- 생성 작업은 서버에서 job(id·status)으로 관리하고, 프론트 어댑터가 `GenJob` 폴링을 `onProgress`/`onStep` 콜백으로 변환한다 — 화면 계약은 바뀌지 않는다.
- 크레딧 차감은 서버 트랜잭션이 원본이고 `{ data, credits }`의 `credits`가 그 결과다. 프론트 선차감 금지.
