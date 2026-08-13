# 에디터 오브젝트 라이브러리·FAQ 통합 기술 설계

- 작성일: 2026-08-12
- 상태: 제안안 — 구현 전 승인 대상
- 범위: Wearless Studio 에디터의 라이브러리 검색·클릭 추가·드래그 배치·그룹 편집과 FAQ 블록 통합

## 1. 결론

영상의 UX는 도입할 가치가 있다. 다만 화면에 보이는 모든 항목을 같은 데이터 타입으로 만들면 안 된다. 사용자에게는 하나의 `라이브러리`로 보이게 하되, 내부에서는 배치 성격을 다음 세 가지로 구분한다.

1. **단일 오브젝트** — 도형, 선, 스티커, 아이콘. 선택한 블록 안에 좌표로 배치한다.
2. **묶음 오브젝트** — 말풍선+텍스트, 배지, 강조 카드. 여러 element를 하나의 group으로 배치한다.
3. **구조화 블록** — FAQ, 사이즈표, 배송·교환, 사진 프레임. 블록 사이에 독립 블록으로 삽입한다.

외부 Interface는 세 진입점으로 제한한다.

```js
listLibraryItems({ query, category, context })
previewLibraryInsert({ blocks, itemId, target, context })
insertLibraryItem({ blocks, itemId, target, context, input, idFn })
  -> Ready | NeedsInput | Failure
```

호출자는 항목이 도형인지 FAQ인지 구분하지 않는다. 라이브러리 항목의 정의가 허용되는 drop target과 materializer를 선택하고, Module의 Implementation이 좌표 변환·ID 발급·블록 높이·그룹 선택·후속 편집을 한 번에 처리한다.

새 캔버스 엔진이나 새 드래그 라이브러리는 필요하지 않다. 현재 `elements[]`, `block.info`, HTML5 Drag and Drop, `react-moveable`을 유지하고 흩어진 삽입 로직을 깊은 Module 뒤로 옮기는 것이 가장 작은 안전한 변경이다.

## 2. 녹화 화면에서 확인한 패턴

5.6초 녹화에는 다음 흐름이 보인다.

1. 왼쪽 라이브러리에 완성형 디자인 썸네일이 격자로 노출된다.
2. FAQ 썸네일을 잡으면 실제 크기에 가까운 drag preview가 따라온다.
3. 사용자가 상세페이지의 원하는 위치로 이동한다.
4. drop 후 결과물이 하나의 묶음처럼 선택된다.
5. 이후 묶음을 이동하거나 내부 텍스트를 편집할 수 있는 구조로 보인다.

좋은 점은 사용자가 `도형 추가 → 텍스트 추가 → 정렬`을 반복하지 않고 결과물 단위로 시작한다는 것이다. Wearless에서는 이를 그대로 복제하기보다, 상품 정보의 정확성과 블록 기반 상세페이지 구조를 지키는 형태로 적용해야 한다.

## 3. 현재 코드에서 이미 가능한 것

### 3.1 단일 오브젝트 drop

`ShapePanel`은 `text/object` payload를 만들고 `CanvasBlock`이 drop을 받는다. `addShape`는 현재 scale을 나눠 1000px 캔버스의 블록 상대 좌표로 변환한다.

### 3.2 블록 사이 프레임 drop

`FramePanel`은 `text/frame` payload를 만들고 각 블록 사이의 dropline이 drop을 받아 새 블록을 삽입한다.

2026-08-13 증분 구현: `ContentPanel`의 내용 프리셋도 같은 블록 사이 dropline을 사용한다.
프리셋 drag payload는 `application/x-wearless-info-preset`이고, drop 시 `block.info`와
`elements`를 가진 완성 정보 블록을 해당 위치에 바로 삽입한다. `size`/`care`처럼
문서당 하나만 허용되는 블록은 중복 삽입 대신 기존 블록으로 이동한다.

### 3.3 다중 선택 이동

`Editor.jsx`는 선택된 element DOM node 배열을 `react-moveable` target으로 넘긴다. 같은 블록 안의 여러 요소는 이미 함께 이동하고 정렬·분배할 수 있다. 현재 그룹 resize는 막혀 있고 단일 element만 resize할 수 있다.

### 3.4 구조화 콘텐츠 재생성

`ContentPanel → InfoBlockModal → buildInfoBlock` 흐름은 `block.info`를 정본으로 저장하고 기존 primitive element로 렌더링한다. FAQ도 이 경로를 따라야 한다.

### 3.5 저장과 undo

편집 문서는 `blocks` 전체가 저장 단위다. 한 번의 `setBlocks` 변경으로 삽입하면 기존 자동 저장과 undo 스냅샷을 그대로 활용할 수 있다. 서버는 `editor_blocks`를 JSONB로 저장하므로 optional group metadata도 보존 가능하다.

## 4. 해결해야 할 구조적 문제

현재 삽입 로직은 `addFrame`, `onFrameDrop`, `addShape`, `insertImage`, `addText`, `submitInfo`로 나뉘어 있다. 이 구조를 항목 종류가 늘어날 때마다 복제하면 다음 문제가 생긴다.

- 클릭 추가와 drag 추가 결과가 달라진다.
- scale 좌표 변환과 clamp 규칙이 여러 곳에 복제된다.
- 새 항목마다 `Editor.jsx`가 계속 커진다.
- FAQ처럼 `info`가 정본인 콘텐츠와 자유 element의 소유권이 섞인다.
- 한 번의 drop이 여러 state update로 나뉘면 undo가 원자적이지 않다.
- 라이브러리 썸네일과 실제 렌더가 다른 별도 구현으로 drift할 수 있다.

삭제 테스트를 적용하면 `EditorLibrary` Module을 삭제했을 때 위 복잡성이 각 패널과 캔버스로 다시 퍼진다. 따라서 이 Module은 충분한 Depth와 Locality를 가진다.

## 5. 권장 Module과 Interface

### 5.1 외부 seam

```js
// 표시용 목록. preview, label, category, placement hint만 노출한다.
listLibraryItems({ query, category, context })
  -> LibraryItemSummary[]

// drag 중 실제 삽입 결과와 같은 규칙으로 ghost 또는 insertion line을 계산한다.
previewLibraryInsert({ blocks, itemId, target, context })
  -> PlacementPreview

// 클릭과 drag가 공유하는 유일한 쓰기 진입점.
insertLibraryItem({ blocks, itemId, target, context, input, idFn })
  -> InsertOutcome
```

`target`은 사용자의 의도만 표현한다.

```js
type InsertTarget =
  | { kind: 'default', anchorBlockId?: string }
  | { kind: 'inside-block', blockId: string, point: { x: number, y: number } }
  | { kind: 'between-blocks', index: number };
```

drag preview는 문서와 ID를 절대 변경하지 않는다.

```js
type PlacementPreview =
  | { kind: 'element-box', blockId: string, rect: { x, y, w, h } }
  | { kind: 'block-line', index: number, estimatedHeight: number }
  | { kind: 'invalid', code: string };
```

`InsertOutcome`은 즉시 적용 가능한 transaction, 입력이 더 필요한 상태, 실패를 구분한다.

```js
type InsertOutcome =
  | {
      status: 'ready';
      blocks: EditorBlock[];
      selection: { blockId: string, elementIds: string[] } | null;
      announcement: string;
    }
  | {
      status: 'needs-input';
      editor: 'faq' | string;
      initialInfo: object;
      pending: { itemId: string, target: InsertTarget };
    }
  | {
      status: 'error';
      code: string;
      userMessage: string;
    };
```

### 5.2 Interface 불변조건

- 입력 `blocks`를 mutate하지 않는다.
- 생성되는 block·element·group ID는 모두 유일하다.
- element의 x는 캔버스 폭 0~1000 안으로 clamp하고 y는 0 이상이다.
- element가 아래로 넘치면 블록 높이를 확장한다.
- 구조화 블록은 `info`와 `elements`를 같은 transaction에서 함께 만든다.
- 클릭 추가와 drag 추가는 같은 materializer를 사용한다.
- 삽입은 한 번의 문서 변경으로 완료돼 undo 한 번에 되돌아간다.
- preview와 `needs-input`은 blocks·history·ID를 변경하지 않는다.
- 허용되지 않은 target은 조용히 다른 의미로 바꾸지 않고 명시적인 오류 코드를 반환한다.

### 5.3 오류

```text
UNKNOWN_LIBRARY_ITEM
INVALID_DROP_TARGET
MISSING_REQUIRED_CONTEXT
UNSAFE_ASSET
CONTENT_OVERFLOW
```

오류는 기술 메시지를 UI에 직접 노출하지 않는다. 호출부는 code를 한국어 안내와 drop preview 상태로 변환한다.

## 6. seam 뒤의 Implementation

라이브러리 정의는 다음 정보를 갖지만 이 구조를 외부 호출자가 알 필요는 없다.

```js
{
  id: 'content.faq.cards',
  label: 'FAQ 카드형',
  category: 'content',
  preview: '/editor-library/faq-cards.webp',
  placement: 'between-blocks',
  materializer: 'info-block',
  recipe: 'faq',
  variant: 'cards'
}
```

private materializer는 실제로 여러 개가 필요하다.

- `primitive` — shape, line, image element 하나
- `composite` — 여러 element + group metadata
- `frame-block` — 이미지 슬롯을 가진 새 블록
- `info-block` — `buildInfoBlock`을 사용하는 구조화 블록

이는 실제로 동작이 다른 네 Adapter이므로 seam이 가상 추상화가 아니다. 다만 Adapter registry는 Module 내부 seam으로 유지하고 `Editor.jsx`에 노출하지 않는다.

## 7. 항목별 배치 규칙

| 항목 | 내부 결과 | 클릭 | drag | 편집 |
|---|---|---|---|---|
| 도형·선·스티커 | element 1개 | 선택 블록 중앙 | 블록 안 좌표 | 기존 속성 패널 |
| 말풍선·배지 | element 여러 개 + group | 선택 블록 중앙 | 블록 안 좌표 | 그룹 이동, 내부 편집 |
| 사진 프레임 | 새 block | 현재 블록 뒤 | 블록 사이 | 슬롯 사진 채우기 |
| FAQ·사이즈·정책 | `info` 정본의 새 block | 현재 블록 뒤 | 블록 사이 | 전용 폼 |

### 왜 FAQ를 기존 블록 위에 자유 배치하지 않는가

FAQ는 질문 수와 답변 길이에 따라 높이가 바뀌고, 나중에 폼을 다시 열면 `elements`가 재생성된다. 이를 다른 사진·텍스트가 있는 블록 안에 overlay하면 재생성 때 충돌하고 수동 배치가 사라질 수 있다. 따라서 MVP에서는 독립 블록으로 삽입한다.

사용자는 영상처럼 FAQ 썸네일을 drag할 수 있지만, canvas 중앙이 아니라 블록 사이 insertion line에 drop한다. 블록 안으로 가져오면 가장 가까운 위·아래 insertion line을 강조해 의미를 명확하게 한다.

## 8. FAQ 통합 방식

FAQ는 semantic recipe 하나만 구현하고 라이브러리에서는 두 variant 썸네일로 보여준다.

```js
{
  infoType: 'faq',
  info: {
    layout: 'cards' | 'chat',
    title: 'FAQ',
    backgroundSrc: null,
    items: [{ question: '', answer: '' }]
  }
}
```

- `FAQ 카드형`과 `FAQ 대화형`은 서로 다른 기능이 아니라 같은 recipe의 초기 variant다.
- drop 시 선택한 위치와 variant를 pending placement로 기억하고 FAQ 폼을 연다.
- 사용자가 폼을 제출할 때만 `info`와 `elements`를 가진 완성 블록을 한 번에 삽입한다. 모달을 닫으면 문서는 바뀌지 않는다.
- 확인된 상품 데이터만 답변에 자동 입력한다.
- 두께·비침·방수 등 근거가 없는 속성은 질문만 제안하고 셀러가 답한다.
- 사진 대화형은 `backgroundSrc`가 있을 때만 사진을 렌더하고, 없으면 단색 대화형으로 안전하게 fallback한다.
- static export이므로 모든 답변은 펼쳐진 상태다.

## 9. 묶음 오브젝트의 데이터 모델

Phase 2에서 최소한의 optional metadata를 추가한다.

```js
Element {
  ...,
  groupId?: string
}

EditorBlock {
  ...,
  groups?: {
    [groupId: string]: {
      libraryItemId: string,
      locked: boolean
    }
  }
}
```

동작 규칙:

- drop 직후에는 group의 모든 element를 선택한다.
- 한 번 클릭하면 group 전체를 선택한다.
- 더블클릭 또는 `그룹 안 편집`으로 child 선택 모드에 들어간다.
- `그룹 해제`는 groupId와 groups entry만 제거하고 element는 보존한다.
- MVP group은 함께 이동만 지원한다. 현재 Moveable 설정이 다중 선택 resize를 막으므로 group resize는 후속 단계로 둔다.
- group은 자유 element의 편집 편의 metadata다. `block.info` 같은 별도 콘텐츠 정본이 아니다.

## 10. 라이브러리 UI

삽입 기능은 `프레임`, `내용`, `오브젝트` 탭에 흩어놓기보다 하나의 `라이브러리` 패널에서 찾게 한다.

### 상단

- 검색
- 카테고리: `전체 / 내용 블록 / 사진 프레임 / 말풍선·배지 / 도형·선 / 스티커`
- 최근 사용·즐겨찾기는 사용 데이터가 생긴 뒤 추가한다.

### 타일

- 실제 recipe를 축소 렌더하거나 같은 element 정의로 만든 preview를 사용한다.
- `새 블록`과 `현재 블록 안` 배치를 배지로 구분한다.
- click과 drag를 모두 제공한다.
- drag 중 실제 bounding box 비율의 ghost를 보여준다.
- 허용되는 drop zone만 강조한다.

### 선택 이후

라이브러리는 삽입 surface이고 기존 텍스트·이미지·도형 속성 패널은 편집 surface로 유지한다. 하나의 패널이 카탈로그 검색과 모든 속성 편집을 동시에 맡지 않는다.

## 11. 입력 Adapter

현재 데스크톱 에디터에서는 HTML5 Drag and Drop을 유지한다. payload는 둘로 나뉜 `text/frame`, `text/object` 대신 하나로 통일한다.

```text
application/x-wearless-library-item = <itemId>
```

현행 증분 구현은 통합 MIME 전 단계다. 블록 사이 삽입은 `text/frame`,
`application/x-wearless-info-preset`, wardrobe image payload를 모두 허용하고,
최종 Library Module 도입 때 위 단일 MIME으로 정리한다.

클릭도 같은 `InsertTarget`으로 normalize한다.

- click Adapter → `{ kind: 'default', anchorBlockId }`
- object drop Adapter → `{ kind: 'inside-block', blockId, point }`
- block drop Adapter → `{ kind: 'between-blocks', index }`

모바일 에디팅을 지원할 때 HTML5 DnD 대신 Pointer Events Adapter를 추가할 수 있다. 그때 두 Adapter가 생기므로 입력 seam이 실제 가치를 갖는다. 지금부터 별도 drag framework를 추가하지 않는다.

## 12. 파일 배치 제안

```text
src/features/editor/library/
  catalog.js                 # 검색 메타데이터와 item 정의
  insertLibraryItem.js       # 외부 쓰기 Interface
  listLibraryItems.js        # 외부 읽기 Interface
  placement.js               # 좌표·drop target·clamp
  materializers/
    primitive.js
    composite.js
    frameBlock.js
    infoBlock.js
  LibraryPanel.jsx
```

기존 파일 변경 역할:

- `Editor.jsx` — 기존 add 함수의 세부 구현을 제거하고 Interface 호출·state 반영·selection만 담당
- `EditorPanels.jsx` — FramePanel·ShapePanel의 삽입 목록을 LibraryPanel로 점진 통합, 속성 패널은 유지
- `ContentPanel.jsx` — FAQ 전용 폼 진입은 유지하되 목록은 LibraryPanel에서도 노출
- `infoPresets.js` — FAQ recipe·기본값·빌더 추가
- `InfoBlockModal.jsx` — FAQ 반복 Q/A 폼과 layout 선택 추가
- `editorGeometry.js` — DOM과 무관한 placement 계산을 재사용하거나 library/placement에서 호출
- `common_data_contract.md` — `faq` info shape와 optional group metadata 반영

## 13. 구현 순서

### Phase 1 — FAQ + 통합 drag contract

1. `faq` info recipe와 폼을 추가한다.
2. `insertLibraryItem` pure Module을 만든다.
3. 기존 shape·line·frame·FAQ를 catalog item으로 등록한다.
4. payload를 단일 MIME으로 통합한다.
5. click과 drag를 동일 Interface로 연결한다.
6. 새 `라이브러리` 패널에서 기존 항목을 먼저 노출한다.

이 단계는 element schema 변경 없이 가능하다.

### Phase 2 — 말풍선·배지 composite group

1. optional `groupId`와 `groups` metadata를 추가한다.
2. 질문 말풍선·답변 말풍선·강조 배지 3~5개를 등록한다.
3. group 전체 선택·이동·그룹 해제를 구현한다.
4. 저장·재로드·undo를 검증한다.

### Phase 3 — 카탈로그 확장

1. 아이콘·스티커·구분선·텍스트 배지 팩을 추가한다.
2. 검색·카테고리·최근 사용을 추가한다.
3. 실제 사용률이 확인된 뒤 즐겨찾기를 추가한다.
4. 카탈로그를 원격 운영해야 할 필요가 생기면 `getCatalogs`의 library 부분만 서버 Adapter로 옮긴다.

### Phase 4 — 고급 그룹 편집

- 비율 유지 group resize
- 그룹 내부 직접 편집 모드
- 사용자 정의 그룹 저장(`내 오브젝트`)
- Pointer Events 기반 touch drag

MVP 전에 넣지 않는다.

## 14. 테스트 seam

### pure Module 테스트

- 모든 catalog item이 유효한 기존 primitive만 방출한다.
- 같은 입력과 deterministic idFn은 같은 결과를 만든다.
- click과 같은 위치의 drag는 좌표를 제외하고 같은 recipe를 만든다.
- scale 0.1, 0.4, 1, 2에서 screen point가 같은 canvas point로 변환된다.
- element는 x 0~1000, y 0 이상이며 overflow 시 block height가 확장된다.
- preview는 ID를 발급하거나 문서·history를 변경하지 않는다.
- 입력 전 FAQ drop은 `needs-input`을 반환하고 문서를 변경하지 않는다.
- 구조화 FAQ는 `info`와 `elements`가 함께 생성된다.
- 허용되지 않은 drop target은 `INVALID_DROP_TARGET`이다.

### 통합 테스트

- FAQ 폼 제출 한 번이 undo 한 번으로 제거된다.
- group을 drop한 뒤 한 번의 drag로 모든 child가 같은 delta만큼 이동한다.
- 저장 후 reload해도 groupId·FAQ info·variant가 유지된다.
- FAQ 폼 재편집 후 Q/A 순서와 backgroundSrc가 유지된다.
- 기존 shape·frame click 추가의 동작이 바뀌지 않는다.

### 시각 QA

- drop ghost와 실제 결과의 비율이 일치한다.
- block item을 canvas 안으로 가져가도 잘못 overlay되지 않고 insertion line이 보인다.
- FAQ 2~6개, 긴 한글 답변, 사진 유무에서 겹침이 없다.
- 1000px 원본과 에디터 scale 40%에서 선택 박스가 일치한다.

## 15. 주요 위험과 대응

### `block.info`와 수동 element 편집 충돌

구조화 블록을 자유 group으로 만들지 않고 독립 info block으로 유지한다. 기본 조작은 블록 이동·재정렬이며 내부 콘텐츠는 전용 폼으로 편집한다.

### Editor.jsx 비대화

새 항목별 handler를 추가하지 않는다. 삽입 계산은 pure Module로 옮기고 Editor는 결과를 한 번 적용한다.

### 썸네일과 실제 결과 drift

가능하면 실제 materializer가 방출한 block/element를 preview renderer에 넣는다. 별도 HTML 마크업으로 썸네일을 재구현하지 않는다.

### 외부 SVG 보안

초기 스티커는 신뢰된 내장 SVG path 또는 raster asset만 허용한다. 임의 SVG 업로드와 inline HTML은 범위에서 제외한다.

### 항목 과잉

첫 릴리스는 FAQ 2 variant, 기존 사진 프레임 4개, 기존 도형·선, 말풍선·배지 3~5개로 제한한다. 실제 추가·삭제·검색 데이터를 본 뒤 늘린다.

## 16. 설계 대안 비교

### 안 A — 최소 Interface

`list → place → openEditor` 3개만 두고 모든 항목을 기존 primitive로 materialize한다. 가장 높은 Depth와 작은 호출 surface가 장점이다. 현재 구현에 가장 가까워 도입 비용도 낮다. 다만 영상 같은 drag ghost를 호출부가 따로 계산하면 preview와 실제 배치가 어긋날 수 있고, 범용 폼 DSL까지 한 번에 도입하면 오히려 범위가 커진다.

### 안 B — 최대 확장성

`LibraryRef { namespace, id, version }`, 비동기 카탈로그, document revision, 원격 pack, definition migration까지 Interface에 포함한다. 로컬·원격 카탈로그가 함께 운영되는 시점에는 강하지만 현재는 로컬 catalog 하나뿐이다. 지금 적용하면 async·revision·migration이 실제 사용자 가치보다 먼저 들어가는 가상 seam이 된다.

### 안 C — 기본 사용자 흐름 최적화

`query → preview → insert`와 원자적 transaction을 중심으로 `검색 → drag ghost → drop → 묶음 선택 → FAQ 편집`을 직접 모델링한다. 영상의 동작과 가장 잘 맞는다. 다만 모든 항목에 영구 object metadata와 group resize를 즉시 적용하면 저장 계약과 Moveable 제스처 변경 범위가 커진다.

### 권장 혼합안

- 안 A의 동기식 로컬 materialization과 기존 primitive 호환을 채택한다.
- 안 C의 `preview`와 원자적 insert transaction을 채택한다.
- FAQ 입력은 `needs-input`으로 처리해 미완성 블록을 만들지 않는다.
- 영구 group metadata는 composite가 들어가는 Phase 2에만 추가한다.
- 안 B의 namespace·원격 Adapter·revision·migration은 원격 pack 요구가 생길 때 도입한다.
- 범용 폼 DSL과 전체 command bus는 도입하지 않고 기존 `InfoBlockModal`을 Adapter로 재사용한다.

이 혼합안은 현재 호출자가 배워야 할 Interface를 세 개로 유지하면서도 drag preview, 클릭·드롭 동등성, FAQ 안전 입력, 한 번의 undo를 모두 같은 seam에서 검증할 수 있다.

## 17. 제품 결정 요약

- 사용자에게는 하나의 라이브러리로 보여준다.
- 내부 데이터는 single object, composite group, structured block을 구분한다.
- FAQ는 한 recipe + 두 visual variant로 구현한다.
- FAQ는 블록 사이에 삽입하고 말풍선 장식은 블록 안에 삽입한다.
- 기존 react-moveable과 block/element 모델을 유지한다.
- 삽입을 한 deep Module의 Interface로 모아 click·drag·undo·save를 일관되게 만든다.
- 첫 구현에서 scene graph 재작성, 원격 카탈로그, touch DnD, group resize는 하지 않는다.

## 18. 추가 결정 — 배경 불투명도·빈 프레임·초기 카탈로그

세부 조사와 근거는 [에디터 배경 투명도·빈 프레임·MVP 라이브러리 조사](./research/2026-08-12-editor-opacity-empty-frames-mvp-library.md)에 정리했다.

- 기존 `Element.opacity`는 이미지·도형·텍스트의 공통 불투명도 계약으로 유지한다.
- 블록에는 optional `bgOpacity`를 추가한다. `CanvasBlock` 자체에 CSS `opacity`를 주지 않고 배경색만 alpha 합성해 자식 요소가 흐려지지 않게 한다.
- 사진 배경이 있는 구조화 블록에는 `overlayColor`와 `overlayOpacity`를 별도로 둔다.
- 프레임은 비어 있는 상태로 배치할 수 있고, 사진 drop 시 `cover`, 재-drop 시 교체, 더블클릭 시 crop/reposition으로 동작한다.
- 프레임 공통 액션은 `이미지 교체 / 자르기 / 초기화 / 빼내기`다.
- 초기 추천 프레임은 `1컷 / 2분할 / 3분할 / 2×2 / 큰사진+2장 / 컬러 비교` 6종으로 제한한다.
- 초기 추천 오브젝트는 `반투명 텍스트 박스 / Q&A 말풍선 / 구분선 / 화살표 / 라벨 배지` 5종으로 제한한다.
- 기존 Before/After와 기본 도형은 유지하되 첫 추천 영역에서는 뒤로 배치한다.
- 실제 추천 순서는 추가·교체·검색 실패 이벤트를 수집한 뒤 사용률로 조정한다.
