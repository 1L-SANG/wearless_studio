/* =============================================================
   lib/types.js — Type boundary.
   Pure shape definitions + closed enum catalogs. No data, no UI.
   The mock layer (mock/) produces values that conform to these;
   when a real backend lands it must return these same shapes.

   SOURCE OF TRUTH: documents/common_data_contract.md (2026-06-11).
   This file mirrors that contract; if they disagree, the contract
   document wins. Decisions: docs/adr/0001~0003.

   NOTE (TS adoption): when we migrate this file to .ts, generate
   the types from the contract document §3~§4. BlockKind stays
   non-exhaustive for auto blocks ('size'|'care'|'ai-notice')
   [user decision 2026-06-09, kept in the 2026-06-11 contract].
   ============================================================= */

/* ---- Closed enums (the union types referenced in JSDoc) ---- */
export const ClothingType = Object.freeze({ TOP: 'top', BOTTOM: 'bottom', OUTER: 'outer', DRESS: 'dress' });
export const Gender = Object.freeze({ WOMEN: 'women', MEN: 'men' });
export const Fit = Object.freeze({ SLIM: 'slim', REGULAR: 'regular', SEMI_OVER: 'semi_over', OVER: 'over' });
export const ComposeMode = Object.freeze({ SIMPLE: 'simple', BASIC: 'basic', EXTENDED: 'extended' });
export const BlockKind = Object.freeze({ HOOK: 'hook', SELLING: 'selling', STYLING: 'styling', HORIZON: 'horizon', PRODUCT: 'product', INFO: 'info' });
/** 컷 종류 — 공식 용어: 스타일링컷·호리존컷·제품컷 (ADR-0003. 'daily'·'studio' 토큰 폐기)
    + 거울샷 'mirror' (ADR-0004. 방향 없음, 샷은 full/knee만, 얼굴 기본 'hide'=폰으로 가림) */
export const CutType = Object.freeze({ STYLING: 'styling', HORIZON: 'horizon', PRODUCT: 'product', MIRROR: 'mirror' });
/** 블록 출처 — '내 이미지'는 컷 종류가 아니라 source다 (ADR-0003) */
export const BlockSource = Object.freeze({ AI: 'ai', MINE: 'mine' });
/** 공간 무드 유지 그룹의 변화 강도 — 'subtle'=같은 구도 미세 이동(기본), 'varied'=포즈·프레이밍 변주 (ADR-0004) */
export const SpaceVariation = Object.freeze({ SUBTLE: 'subtle', VARIED: 'varied' });
export const Direction = Object.freeze({ FRONT: 'front', BACK: 'back', SIDE: 'side' });
export const ProductDirection = Object.freeze({ FRONT: 'front', BACK: 'back' });
export const ShotType = Object.freeze({ FULL: 'full', KNEE: 'knee', MEDIUM: 'medium', CLOSE: 'close' });
export const ProductShotType = Object.freeze({ GHOST: 'ghost', HANGER: 'hanger', FLATLAY: 'flatlay' });
export const ProjectStatus = Object.freeze({ DRAFT: 'draft', GENERATING: 'generating', DONE: 'done' });
export const JobStatus = Object.freeze({ IDLE: 'idle', RUNNING: 'running', DONE: 'done', ERROR: 'error' });
export const ElementType = Object.freeze({ IMAGE: 'image', TEXT: 'text', SHAPE: 'shape', LINE: 'line' });
export const AngleSlot = Object.freeze({ FRONT: 'Front', BACK: 'Back', DETAIL: 'Detail', FIT: 'Fit' });
/** 마네킹 조정 — '현재(변경 없음)'는 파라미터 생략으로 표현 */
export const AdjustFit = Object.freeze({ SLIMMER: 'slimmer', LOOSER: 'looser' });
export const AdjustLength = Object.freeze({ SHORTER: 'shorter', LONGER: 'longer' });

/* =============================================================
   JSDoc shapes (contract §3 mirror)
   -------------------------------------------------------------
   @typedef {Object} Project
   @property {string} id
   @property {ProjectStatus} status
   @property {string} title              product.name 미러 (보관함 표시)
   @property {ComposeMode} composeMode
   @property {boolean} copywriting
   @property {string|null} selectedMannequinId
   @property {number} adjustCount        프로젝트당 영속, max LIMITS.mannequinAdjustMax
   @property {string} createdAt          ISO
   @property {string} updatedAt          ISO

   @typedef {Object} ProjectSummary      보관함 카드 (구 LibraryItem)
   @property {string} id
   @property {string} title
   @property {string} cover
   @property {ClothingType} clothingType
   @property {number} blockCount
   @property {ProjectStatus} status
   @property {string} updatedAt          ISO — '2시간 전' 표시는 화면 파생

   @typedef {Object} ImageAsset
   @property {string} id
   @property {string} src                objectURL/placeholder → R2 URL
   @property {string} [slot]             AngleSlot. 기준 색상의 각도 슬롯
   @property {boolean} [ai]              true = AI 생성
   @property {{name:string,size:number,type:string}} [file]  업로드 메타

   @typedef {Object} ColorGroup
   @property {string} id
   @property {string|null} swatchId      정체성 = 12색 팔레트. null = '색상 미정'
   @property {boolean} isBase            기준 색상 여부
   @property {ImageAsset[]} images
   (파생: name·hex ← swatchId, monotone ← MONOTONE 스와치 포함 여부)

   @typedef {Object} Measurement
   @property {string} key                MeasurementKey 영문 토큰 (라벨은 catalogs.measurementLabels)
   @property {number|null} value         cm. AI는 추정하지 않는다 — null로 시작
   @property {string} unit               'cm'

   @typedef {Object} Material
   @property {string} name               자유 텍스트 (한국어 허용)
   @property {number} ratio              percent

   @typedef {Object} Product             상품의 물리적 사실 — clothingType·실측의 단일 소유자
   @property {string} id
   @property {string} name
   @property {ClothingType} clothingType
   @property {ColorGroup[]} colors
   @property {Measurement[]} measurements
   @property {boolean} measurementsUnknown
   @property {boolean} uploadComplete

   @typedef {Object} Analysis            AI 제안 + 사용자 확인 속성
   @property {string} [suggestedName]
   @property {string|null} subCategory   영문 토큰 (catalogs.subCategories)
   @property {Gender[]} targetGenders
   @property {Fit} fit
   @property {Material[]} materials
   @property {string[]} sellingPoints    자유 텍스트 (max 5)
   @property {string[]} aiSuggestedPoints (max 2)
   @property {string} selectedModelId
   @property {MatchClothing[]} matchClothing  후보 목록 (선택 상태 포함 — 계약은 matchSelections 분리, §7 갭)
   @property {string} washCare
   @property {boolean} locked

   @typedef {Object} Model
   @property {string} id
   @property {string} name
   @property {string} thumb
   @property {boolean} recommended

   @typedef {'women'|'men'|'unisex'} MatchingItemGender

   @typedef {Object} MatchingItem       Supabase-ready 매칭 의류 seed 원본
   @property {string} id
   @property {string} name
   @property {'top'|'bottom'} clothingType
   @property {MatchingItemGender} gender
   @property {string} category          catalogs.subCategories 계열 토큰 우선
   @property {string} colorName
   @property {string} colorGroup        swatchColors id 계열 토큰 우선
   @property {string[]} styleTags
   @property {Fit} fit
   @property {string} length
   @property {string} imageUrl          structured asset path, not external stock
   @property {string} thumbnailUrl      structured asset path, not external stock
   @property {boolean} isActive
   @property {number} sortOrder

   @typedef {Object} MatchClothing
   현재 UI가 소비하는 legacy shape. mock 추천 레이어가 MatchingItem에서 변환한다.
   @property {string} id
   @property {string} name
   @property {string} thumb
   @property {string} [imageUrl]
   @property {string} [thumbnailUrl]
   @property {MatchingItemGender} [gender]
   @property {boolean} selected
   @property {number} [selOrder]         1=메인, 2=서브

   @typedef {Object} MannequinCut
   @property {string} id                 `${candidate}-${version}`
   @property {'A'|'B'} candidate
   @property {number} version
   @property {string} src
   @property {Fit} baseFit               후보 생성 시 핏 (구 fitLabel)
   @property {string|null} fitAdjust     AdjustFit — 원본 대비 누적 조정 상태
   @property {string|null} lengthAdjust  AdjustLength
   @property {{clothingId:string,fitAdjust:string|null,lengthAdjust:string|null}|null} matchAdjust
   (선택 여부는 project.selectedMannequinId가 소유 — cut.selected 폐기)

   @typedef {Object} StoryboardBlock
   @property {string} id
   @property {BlockKind} kind            섹션 역할 (사용자 추가 블록 기본 'info')
   @property {BlockSource} source        'ai' | 'mine'
   @property {string|null} cutType       CutType. source='mine'이면 null
   @property {Direction|ProductDirection|null} [direction]  mirror는 null — 방향 개념 없음 (ADR-0004)
   @property {ShotType|ProductShotType} [shot]
   @property {string} [colorId]
   @property {string} pose               PoseId, 기본 'auto' (구 _pose)
   @property {string[]} matchIds
   @property {'same'|'show'|'hide'} faceExposure
   @property {'same'|'low'|'high'} angle
   @property {string[]} refImages        내 레퍼런스 — 생성 입력(NewCutRequest)에 포함. 프로젝트(블록) 한정, 전역 저장 없음
   @property {string|null} [exampleId]   분위기 예시 선택 — "예시 그대로, 옷·모델만 교체" (ADR-0004)
   @property {string|null} [spaceGroupId] 공간 무드 유지 그룹 — 같은 id = 같은 공간에서 생성 (ADR-0004)
   @property {SpaceVariation} [spaceVariation] 그룹 내 변화 강도 — 기본 'subtle'
   @property {string[]} [ownImages]      source='mine'
   @property {string} thumb              예시 썸네일 (최종 이미지 아님)
   @property {string} [title]            과도기 표시 필드 — 계약상 파생 (kind 라벨)

   @typedef {Object} Element
   @property {string} id
   @property {ElementType} type
   @property {number} x @property {number} y
   @property {number} w @property {number} h
   @property {number} [rotate]           (-180, 180]
   @property {number} [radius]
   @property {number} [opacity]          0~1
   @property {boolean} [hidden]
   @property {boolean} [locked]
   @property {string|null} [src]         image. null = 빈 슬롯
   @property {string|null} [cutType]     image. CutType — 생성 시 기록 (직접 업로드는 null=미상)
   @property {{ox:number,oy:number,iw:number,ih:number}} [crop]  image 인라인 크롭
   @property {string} [text]             text
   @property {Object} [style]            text — TextStyle (계약 §3.5)
   @property {string} [shape]            shape: ShapeId / line: 'arrow-l'|'line'|'arrow-r'
   @property {string} [fill]             shape
   @property {string} [stroke]           shape·line ('none' 가능)
   @property {number} [strokeWidth]
   @property {string} [dash]             line: 'solid'|'dashed'|'dotted'

   @typedef {Object} Block               EditorBlock
   @property {string} id
   @property {string} name
   @property {string} kind               BlockKind | 'size'|'care'|'ai-notice' (auto)
   @property {string} bg
   @property {number} [h]                고정 높이 (px, 기준 폭 1000)
   @property {Element[]} elements        배열 순서 = z-order
   @property {boolean} [auto]

   @typedef {Object} NewCutRequest    AI 탭 '새 컷 추가' 생성 입력 (계약 §6)
   @property {'new'} mode
   @property {string} colorId
   @property {CutType} cutType
   @property {Direction|ProductDirection|null} direction  mirror는 null — 방향 없음 (ADR-0004)
   @property {ShotType|ProductShotType} shot
   @property {string} modelId
   @property {string|null} [exampleId]   분위기 예시 — "예시 그대로, 옷·모델만 교체" (ADR-0004)
   @property {string[]} [refImages]

   @typedef {Object} GenStep
   @property {string} key                info|prep|styling|horizon|product|copy|assemble
   @property {string} label
   @property {JobStatus} status

   @typedef {Object} GenJob
   @property {string} [id]
   @property {number} progress
   @property {GenStep[]} steps
   @property {BlockKind[]} composition

   @typedef {Object} Account
   @property {string} name
   @property {string} avatar
   @property {number} credits
   @property {'Free'|'Pro'|'Team'} plan

   크레딧 봉투: 크레딧을 소모하는 API는 { data, credits }를 반환한다.
   credits = 차감 후 잔액 — 화면은 store.syncCredits()로 반영 (계약 §6).
   ============================================================= */

/** Convenience namespace mirroring the prototype's window.WT */
export const WT = Object.freeze({
  ClothingType, Gender, Fit, ComposeMode, BlockKind, CutType, BlockSource, SpaceVariation,
  Direction, ProductDirection, ShotType, ProductShotType, ProjectStatus,
  JobStatus, ElementType, AngleSlot, AdjustFit, AdjustLength,
});

export default WT;
