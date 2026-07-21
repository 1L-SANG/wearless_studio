# 공통 데이터 계약 (Common Data Contract)

> 상태: 확정 (2026-06-11, 갱신 2026-07-18) · 근거: `documents/PRD.md`, mock 구현(`src/lib/types.js`, `src/mock/*`), 2026-06-11·06-14·07-17·07-18 결정 세션
> 결정 기록: `docs/adr/0001~0007`, 용어는 `/CONTEXT.md`
> 적용 방식: 이 문서가 계약의 원본이다. `src/lib/types.js`와 mock 레이어를 이 계약에 맞추고, 백엔드(FastAPI·Supabase·R2)와 AI 파이프라인은 같은 shape를 구현한다. 현행 코드와의 차이(마이그레이션 갭)는 `documents/TODO.md`가 추적한다 — 문서와 코드가 다르면 문서가 맞다.

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
  adjustCount: number              // @deprecated legacy — 조정 횟수 제한 폐기(fitProfile 재생성 흐름, mannequinAdjustMax=undefined)
  createdAt: string                // ISO
  updatedAt: string                // ISO
}

ProjectSummary {                   // 보관함 카드 (구 LibraryItem)
  id: string
  title: string
  cover: string                    // 대표 이미지 (파생: 첫 에디터 블록 이미지)
  clothingType: ClothingType | null // 초안은 상품 미입력 → null 가능 (보관함 카드 표시는 라벨 파생)
  blockCount: number               // 구 blocks (배열과 혼동 방지 위해 개명)
  status: ProjectStatus
  updatedAt: string                // ISO — '2시간 전'은 표시 파생
}
```

- `composeMode` `copywriting` `selectedMannequinId`는 **서버 동기화 대상 선택값**이다. 프론트는 Zustand에 작업 사본을 두고 변경 시 `patchProject`로 동기화한다 (→ `frontend_state_model.md`).
- **조정 횟수 제한(구 2회/프로젝트)은 폐기됐다** (2026-07, fitProfile 재생성 흐름 — PRD §7.4). 조정 반영은 재생성(`mannequinGenerate` 크레딧)으로만 이뤄지므로 크레딧이 자연 제한이다. `adjustCount`는 legacy 서버 필드로 남아 있을 뿐 어떤 제한에도 쓰이지 않는다(`LIMITS.mannequinAdjustMax` deprecated).

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
  customCategory: string | null    // enum 밖 의류의 자유 명칭(한국어, ≤20자) — AI 추측 + 사용자 주관식 수정 (2026-07-13)
  targetGenders: Gender[]          // UI 단일 선택 — 1-element 배열로 저장
  fit: Fit
  materials: Material[]            // Material { name: string(자유 텍스트), ratio: number(%) }
  sellingPoints: string[]          // 자유 텍스트, max LIMITS.sellingPointMax(5)
  aiSuggestedPoints: string[]      // max LIMITS.aiSuggestedPointMax(2)
  selectedModelId: string          // catalogs.models 참조
  matchCandidates: MatchClothing[] // AI가 제안한 매칭 의류 후보
  matchSelections: { clothingId: string, role: 'main' | 'sub' }[]   // max 2
  locked: boolean
}
// fit·clothingType은 필수(null 불가) — 분석 폼에서 해제 불가 칩(PRD §6.3). subCategory(원피스=null)·targetGenders(배열)는 비울 수 있음.

MatchClothing { id: string, name: string, thumb: string }
// 폐기: Analysis.clothingType / measurements / measurementsUnknown (Product 소유),
//       Analysis.models (catalogs.models 참조), MatchClothing.selected / selOrder (matchSelections로 분리),
//       Analysis.washCare (세탁 안내는 분석이 아니라 에디터 자동 블록 — M-02 규칙 기반 생성, PRD §10.14)
```

### 3.3 MannequinCut — 마네킹 컷 버전 (단일컷 전환)

마네킹컷은 **단일 컷 + 버전 스트립**이다(구 A/B 2후보안 폐기, 2026-07). 핏 상태는 `analysis.fitProfile`(→ `fit_profile_spec.md`)이 소유하고, 컷 엔티티는 버전 이미지만 담는다.

```ts
MannequinCut {
  id: string                       // `${candidate}-${version}`
  candidate: 'A' | 'B'             // @deprecated 단일컷 전환 후 legacy id/API 호환용 — 항상 'A'
  version: number
  src: string
  baseFit: Fit                     // 생성 시 핏 (구 fitLabel '정핏'/'슬림핏')
  fitAdjust: AdjustFit | null      // @deprecated — FitProfile로 대체
  lengthAdjust: AdjustLength | null // @deprecated — FitProfile로 대체
  matchAdjust: {
    clothingId: string
    fitAdjust: AdjustFit | null
    lengthAdjust: AdjustLength | null
  } | null                         // @deprecated — 매칭 핏은 fitProfile.matchCut이 소유
}
// 폐기: selected (선택은 project.selectedMannequinId가 소유),
//       fitLabel / lengthLabel / matchName / matchFit / matchLength / matchLabel (전부 파생)
```

### 3.4 StoryboardBlock — 콘티보드 블록

```ts
StoryboardBlock {
  id: string
  taxonomyVersion: 2               // 콘티 분류 스키마 버전. 다른 버전의 읽기 호환은 제공하지 않음
  sectionId: string                // 연속된 같은 섹션 블록이 공유하는 id
  sectionRole: StoryboardSectionRole
  sectionTitle?: string            // @deprecated 전환기 표시 캐시. sectionRole에서 다시 계산
  sectionLayout: SectionLayout     // 기본 'stack'
  sectionCustom?: boolean          // 사용자가 순서·구성을 직접 바꿨는지
  contentRole: ContentRole         // 시스템이 배정하는 내부 사진 목적. 콘티 UI에서 선택·표시하지 않음
  title?: string                   // @deprecated 전환기 표시 캐시. contentRole에서 다시 계산
  source: BlockSource              // 'ai' | 'mine'
  cutType: CutType | null          // 내부 생성 레시피. UI 분류가 아님. source='mine'이면 null
  direction?: Direction | ProductDirection | null   // cutType에 따라 옵션 셋이 다름. mirror는 null (ADR-0004)
  shot?: ShotType | ProductShotType
  outerClosureState?: OuterClosureState | null       // 아우터 착용컷(styling·horizon·mirror) 전용. 누락 기본 open
  colorId?: string                 // ColorGroup.id (단수 — 컬러별 컷은 블록을 색상마다 분리)
  pose: PoseId                     // 기본 'auto' (구 _pose)
  matchIds: string[]               // 매칭 의류 후보 id
  faceExposure: FaceExposure       // 기본 'same'
  angle: CameraAngle               // 기본 'same'
  refImages: string[]              // '내 레퍼런스' 업로드 (생성 입력에 포함) — 프로젝트 한정, 전역 저장 없음 (ADR-0004)
  exampleId?: string | null        // 촬영 연출 예시 — 예시 속 옷·신발·액세서리는 생성 근거에서 제외 (ADR-0004)
  spaceGroupId?: string | null     // 공간 무드 유지 그룹 — 같은 id = 같은 공간에서 생성 (ADR-0004)
  spaceVariation?: 'subtle' | 'varied'  // 그룹 내 변화 강도. 기본 'subtle' (ADR-0004)
  refScope?: 'all' | 'bg' | 'pose' // 예시에서 참고할 범위
  layoutRowId?: string             // 2단·3단 배치에서 같은 행을 공유하는 id
  layoutRowVersion?: 1
  ownImages: string[]              // source='mine'의 직접 업로드 이미지
  thumb: string                    // 예시 썸네일 (서버/목 생성, 최종 이미지 아님)
}
// 폐기: poseThumb / poseLabel(카탈로그 파생), bgThumb / bgLabel(PRD §8.5에서 배경 제거),
//       colorIds(미사용 잔재), kind(sectionRole/contentRole과 중복)
```

불변식:

- 자동 콘티의 섹션 순서는 `benefit → fit → product`다. `구매 정보`와 `같은 장소`는 섹션을 만들지 않는다.
- `hero|benefit`은 `benefit`, `coordination|fit|realWear`는 `fit`, `productOverview|detail`은 `product` 섹션에 속한다. `custom`은 사용자가 놓은 섹션을 따른다.
- AI 카드의 `contentRole`은 사용자 입력값이 아니다. 기본 구성·현재 섹션·카드 순서로 자동 배정하며 `benefit` 섹션의 첫 AI 카드만 `hero`다. 이동·복제·삭제·순서 변경·되돌리기와 서버 저장에서 다시 정규화한다.
- `source='mine'`은 `cutType=null`, `contentRole='custom'`이다. 현재 UI에서 내 이미지는 AI 사진 목적을 다시 고르지 않으며, 어느 큰 섹션에 놓였는지는 `sectionRole`로 따로 저장한다. 내 이미지는 컷 종류가 아니다.
- `cutType`은 기본 `contentRole`이 정하는 내부 값이다. 화면은 cutType을 섹션이나 탭으로 노출하지 않는다. 핏·코디에서는 `styling | horizon | mirror` 예시를 한 갤러리에 섞어 보여주고, 사용자가 고른 예시의 `cutType`에 따라 내부 `coordination | fit | realWear`와 레시피를 함께 바꾼다. 제품 확인에서는 `ghost | detail` 샷 선택이 내부 `productOverview | detail`을 바꾼다.
- `contentRole='detail'`은 상품 전체 색상 중 `ImageAsset.slot='Detail'` 입력이 하나 이상 있으면 유효하다. 목표 `colorId`에 Detail이 없으면 기준색, 그다음 Detail 보유 첫 색상의 사진을 구조·재질 근거로 쓰고 색만 목표 색상군으로 전환한다.
- `taxonomyVersion: 2`만 저장한다. 레거시 블록 매핑은 제공하지 않으며, v2 입력의 역할 누락은 `source='mine'`과 `cutType` 규칙으로만 방어적으로 정규화한다.
- `sectionTitle`과 `title`은 현재 구현의 전환기 캐시일 뿐 기준 데이터가 아니다. 읽을 때 enum 라벨로 덮어쓰며, 후속 정리에서 저장 shape에서 제거한다.

사진 목적과 내부 생성값의 조합:

| contentRole | sectionRole | cutType | 허용 shot·direction |
|---|---|---|---|
| `hero` | `benefit` | `styling` | 사람용 ShotType · front/back/side |
| `benefit` | `benefit` | `horizon` | 사람용 ShotType · front/back/side |
| `coordination` | `fit` | `styling` | 사람용 ShotType · front/back/side |
| `fit` | `fit` | `horizon` | 사람용 ShotType · front/back/side |
| `realWear` | `fit` | `mirror` | full/medium · direction=null |
| `productOverview` | `product` | `product` | ghost · front/back |
| `detail` | `product` | `product` | detail · front/back · 상품 전체 중 Detail 입력 필수 |
| `custom` | 현재 놓인 섹션 | 기존 레시피 또는 선택한 생성예시에서 정한 레시피 | 해당 cutType의 유효 옵션을 그대로 적용. source=mine이면 cutType=null |

어댑터와 서버는 이 표에 맞지 않는 조합을 그대로 저장하거나 생성기에 보내지 않는다. 목적을 바꾸면 기본 레시피와 옵션을 함께 다시 맞춘다.

### 3.5 EditorBlock / Element — 에디터 캔버스

```ts
EditorBlockBase {
  id: string
  name: string                     // 표시명
  bg: string                       // hex
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
    }
  | EditorBlockBase & {
      kind: AutoBlockKind
      auto: true
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
  font: 'Pretendard' | 'Cal Sans' | 'Roboto Mono' | 'Cormorant'
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
  steps?: GenStep[]                // HTTP 어댑터는 현재 단계 목록을 주지 않으며, mock/legacy에서만 제공
  composition: StoryboardSectionRole[]
}
// mock/legacy step key: info | prep | styling | horizon | product | copy | assemble
// 화면 라벨은 styling='핵심 장점', horizon='핏·코디', product='제품 확인'으로 보여준다.
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
| **StoryboardSectionRole** | `benefit` `fit` `product` | 핵심 장점/핏·코디/제품 확인 | 사용자에게 보이는 섹션, 순서 고정 (ADR-0005) |
| **ContentRole** | `hero` `benefit` `coordination` `fit` `realWear` `productOverview` `detail` `custom` | 첫 장면/핵심 장점/코디 활용/핏 확인/실제 착용 느낌/제품 전체/디테일/직접 구성 | 콘티에서는 비노출 자동값, 에디터 새 이미지 추가에서는 현행 목적 선택값 (ADR-0005) |
| **CutType** | `styling` `horizon` `product` `mirror` | 내부 styling/horizon/product/mirror 레시피 | 생성용 기술값. 사용자 섹션·탭으로 노출하지 않음 (ADR-0003~0005) |
| **BlockSource** | `ai` `mine` | AI 생성/내 이미지 | ★ 신설 — '내 이미지'는 컷 종류가 아님 |
| AutoBlockKind | `size` `care` `ai-notice` | 사이즈/세탁/AI 생성 안내 | 2026-06-09 결정 유지 |
| EditorInfoType | `materials` `options` `shipping_returns` `required_notice` `benefit_copy` `fit_copy` `model_info` `fabric_properties` `color_description` `brand_story` `faq` `reviews` `related_products` `promotion` `social` | 에디터의 구매 정보·문구·선택 콘텐츠 | `kind='info'`일 때 사용. AutoBlockKind 3종과 겹치지 않음 |
| SectionLayout | `stack` `twoColumn` `threeColumn` `grid2x2` `colorCompare` | 세로 1열/2단/3단/2×2/컬러 비교 | 섹션 안 사진 배치 |
| Direction | `front` `back` `side` | 정면/뒷면/사이드 | 모델 컷용 |
| ProductDirection | `front` `back` | 앞면/뒷면 | 내부 product 레시피용 ★ 카탈로그 승격 |
| ShotType | `full` `medium` | 풀샷/중간샷 | 서비스 착용컷은 두 값만 쓴다. 생성예시 선별 도구의 `medium_knee`는 수집 원본 `knee`·`medium`을 합치는 선별 전용 토큰이며 서비스 저장값이 아니다(ADR-0007) |
| ProductShotType | `ghost` `detail` | 고스트샷/디테일샷 | 고스트샷은 옷 전체이며 기본은 입은 듯한 부피, 플랫레이 생성예시를 고르면 펼친 표현·구도를 따른다. 기존 `flatlay` 입력은 `ghost`로 정규화. `detail`은 상품 전체 중 Detail 입력 사진 필요. 목표 색상에 없으면 타색 Detail의 구조·재질을 유지하고 색만 전환 |
| **OuterClosureState** | `open` `partial` `closed` | 전체 열림/부분 열림/전체 닫힘 | 아우터 착용컷 전용. 누락 기본 `open`, 그 외 컷·카테고리에서는 무시 |
| **ProjectStatus** | `draft` `generating` `done` | 초안/생성 중/완료 | ★ 신설 |
| **PlanTier** | `basic` `plus` `seller` | Basic/Plus/Seller | ★ 신설 — 라벨 대문자, 토큰 소문자 |
| JobStatus | `idle` `running` `done` `error` | | 화면용 4값. 서버 job row는 `pending`(화면엔 진행 중) 추가, `cancelled` 없음(error 통일) — backend plan §2·§4 |
| ElementType | `image` `text` `shape` `line` | | |
| AngleSlot | `Front` `Back` `Detail` `Fit` | 앞면/뒷면/디테일/착용 이미지 | 기존 토큰 유지 |
| SwatchId | `white` `gray` `black` `ivory` `beige` `brown` `red` `yellow` `green` `blue` `navy` `pink` | 12색 팔레트 | MONOTONE_SWATCHES = white·gray·black·ivory·beige |
| **StyleTag** | `basic` `daily` `minimal` `casual` `formal` `classic` `sporty` `trendy` `street` `chic` `feminine` `lovely` `romantic` `vintage` `retro` `modern` `luxury` `preppy` `workwear` `athleisure` `cozy` `unique` `sophisticated` `y2k` | 베이식/데일리/미니멀/캐주얼/포멀/클래식/스포티/트렌디/스트릿/시크/페미닌/러블리/로맨틱/빈티지/레트로/모던/럭셔리/프레피/워크웨어/애슬레저/코지/유니크/소피스티케이티드/Y2K | ★ 닫힌 enum(24) — AG-01 `styleTags` 출력·M-01 매칭 친화도(`style_affinity`) 공통 정본. 단일 소스 = `server/app/agents/style_tags.py`. 앞 8=affinity 부트스트랩, 뒤 16=운영자 확장. 저장 안 함(중간 산출물) |
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
- `upper`는 `ClothingType`이 아니다. `[top, outer]`을 화면에서 `상의·아우터 공용`으로 표시할 뿐이다.
- 둘 이상의 종류에 공용인 예시는 사람 검토를 거친 스타일링·호리존 풀샷에만 허용한다. 샷을 중간샷(선택판 `medium_knee`)·제품·디테일로 다시 분류하면 적용 목록을 `[sourceClothingType]`으로 좁힌다.
- UI와 서버는 현재 `Product.clothingType`이 적용 목록에 있는지 검증한다. 목록은 `StoryboardBlock`에 복제하지 않고 `exampleId`가 가리키는 생성예시 정본에서 읽는다.
- `refScope`는 예시에서 전부·배경·포즈 중 무엇을 참고할지 나타내는 별도 축이며 의류 종류 적용 범위로 재사용하지 않는다.
- `refScope='all'`은 장소·조명·분위기·포즈·프레이밍·구성, `pose`는 자세만, `bg`는 장소·조명·분위기만 참고한다. `pose` 자산의 캔버스나 원본 크롭은 프레이밍 근거가 아니며 현재 카드의 `shot`과 `cutType`이 카메라 거리·몸 크기·크롭을 정한다. 어느 범위에서도 예시 속 의류·신발·액세서리를 가져오지 않는다(ADR-0009).
- `pose`·`bg`는 선택적 전용 variant다. 전용 자산이 없으면 `all`로 대체하지 않으며, UI와 서버는 실제 발행된 variant만 사용 가능하다고 판단한다. 제품 `ghost | detail` 예시는 `all`만 사용한다.
- 사용자가 고른 매칭 의류는 `styling | horizon | mirror` 모든 착용컷의 의류 기준이다. 판매 상품과 매칭 의류가 착용컷 의류의 유일한 근거이며, 제품 단독컷에는 매칭 의류를 적용하지 않는다.

- **현행 추가**: `productDirections`, `productShotTypes`, `outerClosureStates`(아우터 열림 정도 3종), `measurementLabels`(key → 한국어)
- **로컬 의미 계약**: `storyboardSections`, `contentRoles`에 해당하는 값과 라벨은 `storyboardTaxonomy.js`가 제공한다. 카탈로그 응답과 중복 저장하지 않는다. `contentRoles` 라벨은 콘티보드에는 노출하지 않고 에디터의 `새 이미지 추가` 등 별도 흐름에서만 사용한다.
- **후속**: `editorInfoTypes`는 구매 정보 UI 구현 때 카탈로그에 추가한다(`TODO.md`).
- **내부 전용**: `CutType` enum은 생성 레시피에만 쓴다. 별도 사용자용 `cutTypes` 카탈로그나 탭을 만들지 않는다.
- **생성예시 릴리스(2단계 운영 적용 완료, 2026-07-20)**: `server/tools/release_genexamples.py`가 확정 manifest를 검증해 서버 레지스트리 v2와 위 프론트 카탈로그를 같은 릴리스에서 만든다. QC 승인 예시 192개(`all` 192·`pose` 12·`bg` 14)와 파생 thumb 192개를 R2 불변 경로 `2026-07-19-pilot-qc-01`로 발행했고, 저장소 JSON도 이 릴리스로 함께 교체했다. 레지스트리 v2는 원본 variant URL·thumb·적용 의류 종류·컷·샷·성별을 함께 가진다. 프론트 `MoodGuide`는 실제 v2 JSON을 소비해 샷·상품 종류·성별로 필터링하고 최대 6장을 노출한다. 핏·코디는 세 착용 `cutType`을 함께 후보로 삼고, 그 밖의 섹션은 현재 내부 `cutType`만 후보로 삼는다.
- **변경**: `subCategories`를 `{ value, label }[]`로 (현재 한국어 문자열 배열)
- **유지**: `clothingTypes` `genders` `fits` `directions` `shotTypes` `angleSlots` `angleLabels` `swatchColors` `composeModes` `poses` `varyOptions` `genExamples` `frames` `shapes` `lines` `fonts` `downloadOptions` `models` `creditCosts`(원본은 `lib/limits.js`). `genExamples`는 `cutType`·`shot`과 `applicableClothingTypes`의 현재 상품 `clothingType` 포함 여부로 필터링하고, 착용컷은 `gender`를 정확히 맞추되 제품컷의 `gender=null`은 성별 공용으로 취급한다. styling의 `mood`, product detail의 `detailSubject`처럼 카드 조건보다 세밀한 축은 rank 순 라운드로빈으로 섞어 최대 6장을 노출한다.
- **폐기 예정**: `backgrounds`(콘티 배경 제거 — `varyOptions.bg`가 에디터 변형용으로 대체), `extendedColorPriority`(미사용), `cutSources`

---

## 6. API 계약

경계는 `src/lib/api/index.js` 하나다. 모든 함수는 Promise를 반환하고, 장시간 작업은 `onProgress(0..100)` 콜백을 받는다. `onStep(steps)`는 현재 mock 상세페이지 생성에만 있으며 HTTP 배선은 TODO다. 화면은 mock/db·placeholder를 직접 import하지 않는다.

**크레딧 규약**: 크레딧을 소모하는 API는 `{ data, credits }` 봉투로 반환한다. `credits`는 차감 후 잔액이며, 화면은 이 값을 `store.syncCredits()`로 반영한다. 실패(throw) 시 차감 없음 — 환불·재시도 정책은 백엔드 시점에 확정(PRD §12.2).

**유료 job 멱등 규약**: 장시간 유료 작업(`generateMannequins`, `generateDetailPage`)은 ① 같은 프로젝트에서 **진행 중**일 때 다시 호출되면 새 작업을 시작하지 않고 진행 중인 job에 합류시키고(진행 콜백 공유, 차감 1회), ② **이미 완료**된 뒤 다시 호출되면(마네킹 후보 존재 / `project.status='done'`) 재실행·재차감 없이 기존 산출물과 현재 잔액을 반환한다, ③ **실패(error)로 끝난 뒤** 다시 호출되면 새 job을 시작한다(크레딧 재예약·재차감 대상) — 실패한 job은 합류·재사용하지 않는다. StrictMode 이중 mount, 생성 중 이탈 후 재진입, 완료 후 라우트 재방문 어느 경우도 재차감으로 이어지지 않기 위한 **서버 책임**이며, 실서버는 project별 job 레코드로 구현한다. `credits`가 델타가 아니라 **잔액**인 것도 합류·재호출 응답의 멱등성을 위해서다. (완료 후 전체 재생성은 의도적으로 플로우에 없다 — PRD §10.17, 필요 컷은 에디터에서 추가한다.)

| 함수 | 입력 | 반환 | 크레딧/비고 |
|---|---|---|---|
| `createProject()` | — | `Project` | 새 제작 시작. 구 `resetDraft()` 대체 |
| `getProject(projectId)` | | `Project` | |
| `patchProject(projectId, patch)` | 선택값 patch | `Project` | 수용 필드 **화이트리스트**: composeMode·copywriting·selectedMannequinId만. **adjustCount·status는 서버 전용** — 페이로드에 오면 무시/거부(adjustCount는 legacy 필드, status는 생성 job의 부수효과로만 변경). frontend_state_model §6 |
| `getLibrary()` | | `ProjectSummary[]` | |
| `getAccount()` | | `Account` | |
| `getCatalogs()` | | `Catalogs` | |
| `getProduct(projectId)` | | `Product` | |
| `saveProduct(projectId, patch)` | | `Product` | 실측·의류 종류 포함 (Product 소유) |
| `analyzeProduct(projectId, { onProgress })` | | `Analysis` | 실측은 항상 null로 반환 |
| `saveAnalysis(projectId, patch)` | | `Analysis` | |
| `getMatchClothing(projectId)` | | `MatchClothing[]` | **과도기 함수** — 마네킹·콘티 화면이 매칭 후보를 별도로 읽는다. 최종은 `analyzeProduct` 응답(`analysis.matchCandidates`)에 포함되어 제거 예정(TODO.md) |
| `getMannequins(projectId)` | | `MannequinCut[]` | |
| `generateMannequins(projectId, { onProgress })` | | `{ data: MannequinCut[], credits }` | `mannequinGenerate` · 페이지 최초 진입 시 자동 호출 |
| `adjustMannequin(projectId, { baseId, fitAdjust?, lengthAdjust?, matchAdjust?, onProgress })` | enum 값만 | ~~`{ data: MannequinCut, credits }`~~ | **@deprecated (2026-07)** — fitProfile 재생성으로 통합, 페이지에서 미호출 (`mannequinAdjust`=0). 서버 `:adjust`는 항상 **410 Gone**(잡 미생성) |
| `regenerateMannequin(projectId, { fitProfile, onProgress })` | 확인 스텝에서 확정한 FitProfile(축+matchCut) | `{ data: MannequinCut[], credits }` | `mannequinGenerate` · fitProfile을 analysis에 영속 후 새 버전 생성·자동 선택 (구 `regenerateMannequins` 대체) |
| `getStoryboard(projectId)` | | `StoryboardBlock[]` | 저장된 v2 배열을 반환. 화면은 working copy를 만들기 전에 내부 역할을 검증하고 누락값·첫 hero를 섹션과 순서로 자동 정규화 |
| `saveStoryboard(projectId, blocks)` | | `StoryboardBlock[]` | 생성 CTA 시 반드시 호출. taxonomyVersion=2만 저장 |
| `generateDetailPage(projectId, { onProgress, onStep })` | | `{ data: EditorBlock[], credits }` | `storyboardPerCut × source='ai'인 블록 수` — 내 이미지 블록은 생성 작업이 없어 차감 제외 |
| `getEditorBlocks(projectId)` | | `EditorBlock[]` | |
| `saveEditorBlocks(projectId, blocks)` | | `void` | 구현됨 — 저장 버튼 + 1.5s 디바운스 자동 저장 + 이탈 시 플러시 |
| `getWardrobe(projectId)` | | `Wardrobe` | |
| `generateImage(projectId, req)` | `NewCutRequest \| VaryRequest` (아래) | `{ data: WardrobeImage, credits }` | `editorImage` |
| `uploadAsset(file)` | `File` | `ImageAsset` | 실서비스 계약 — mock은 `pickAnyImage()`(mock 전용 헬퍼)로 대행 |
| `download(projectId, format)` | `'long' \| 'zip'` | `{ ok }` | 실제 렌더링은 P1 |

```ts
NewCutRequest {                    // AI 탭 '새 이미지 추가'
  mode: 'new'
  colorId: string                  // 구 group('색상 1') 대체
  sectionRole?: StoryboardSectionRole  // 향후 섹션에 바로 삽입하는 경로용. 현재 UI는 의류 탭에 먼저 추가하므로 생략
  contentRole: ContentRole          // 에디터 새 이미지 추가의 목적값. 콘티보드 내부 자동값과 UI 범위가 다름
  cutType: CutType                 // contentRole에서 파생한 내부 레시피. UI에서 직접 선택하지 않음
  direction: Direction | ProductDirection | null   // mirror는 null — 방향 없음 (ADR-0004)
  shot: ShotType | ProductShotType
  modelId: string
  outerClosureState?: OuterClosureState | null  // 아우터 착용 이미지 전용. 현재 에디터 UI 미노출 시 open 기본값
  exampleId?: string | null        // 촬영 연출 예시 — 예시 속 옷·신발·액세서리는 생성 근거에서 제외 (ADR-0004)
  refImages?: string[]
}
VaryRequest {                      // AI 탭 '현재 이미지 수정' — changes 빈 배열 = '비슷한 컷 만들기'
  mode: 'vary'
  source: { src: string, cutType: CutType }    // 미상이면 styling으로 가정해 보냄
  changes: { type: 'direction' | 'shot' | 'pose' | 'face' | 'bg', value: string }[]
  refBg?: string
}
```

`현재 이미지 수정`은 디테일샷 전환을 제공하지 않는다. 디테일은 상품 전체 중 실제 `Detail` 입력을 연결하는 `새 이미지 추가`에서만 만들며, 목표 색상에 Detail이 없으면 타색 근거의 색만 전환한다. 서버는 수정 경로 우회 요청을 `detail_variation_unsupported`로 실패시킨다.

**에러 규약**: 실패는 `Error`를 throw하고 `message`는 사용자에게 그대로 보여줄 한국어 문장이다.

---

## 7. 현행 코드와의 갭 (마이그레이션 TODO)

→ **`documents/TODO.md` §1로 이관.** 코드가 이 계약을 아직 못 따라간 항목(✅ 완료 기록 + 🔶 남음 + 🆕 신규)은 작업 때마다 각 설계 문서를 고치지 않고 TODO.md 한곳에서 추적한다.

---

## 8. 백엔드 구현 현황 (2026-06-29 기준 라이브)

- Supabase 스키마 구현 완료 (9개 마이그레이션, 17테이블): `profiles`, `credit_accounts`, `credit_ledger`, `projects`, `products`, `analyses`, `assets`, `mannequin_cuts`, `wardrobe_images`, `matching_items`, `jobs`, `job_events`, `exports`, `pricing_plans`, `payment_history`, `credit_sources`, `refund_requests`. 전체 RLS 활성, 쓰기는 service-role/FastAPI 전용.
- 생성 작업은 서버에서 job(id·status)으로 관리한다. 현재 프론트 어댑터는 `GenJob`을 폴링해 `onProgress`만 전달한다. 서버 SSE 경로는 구현돼 있으나 프론트 구독과 `onStep` 배선은 TODO다.
- 크레딧 차감은 서버 트랜잭션이 원본이고 `{ data, credits }`의 `credits`가 그 결과다. 프론트 선차감 금지.
