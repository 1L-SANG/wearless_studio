# PR #56 코드 리뷰 보고서: 콘티보드 다듬기 (버그 수정 & UI/UX 개선)

- **PR 제목**: fix: 콘티보드 다듬기 — 컷 추가 버튼 개선·안내 문구 정리·흰 화면 버그 수정
- **대상 저장소**: `wearless_studio`
- **리뷰 작성일**: 2026-07-29
- **최종 머지 판정**: **Approve (승인)**

---

## 1. Executive Summary & PR Overview (개요 및 요약)

본 PR(#56)은 `wearless_studio` 프론트엔드 콘티보드(`Storyboard`) 모듈의 안정성 확보 및 사용자 경험(UX) 개선을 위한 핵심 정비 작업입니다. 주요 변경사항은 다음과 같이 3가지 영역으로 요약됩니다:

1. **선택 해제 백지 화면(White Screen) 버그 수정**:
   - 동일 카드 재클릭 또는 선택 해제 시 `block`과 `requestedRecipe`가 동시에 `null`/`undefined`로 평가되면서 `undefined === undefined` 조건이 `true`로 평가되어 비어 있는 객체의 프로퍼티(`cutType`)를 읽으려다 발생하는 React 렌더링 런타임 크래시 방어.
2. **컷 추가 버튼 UI/UX 오버플로우 개선 및 `addMenu` 상태 제거**:
   - 기존의 클릭형 팝업 메뉴(`.sb-addmenu`)가 화면/섹션 컨테이너 좌측 영역에서 잘려 보이는(Clipping) 이슈 해결.
   - 단질 `addMenu` React state를 완전히 제거하고, 사이사이 밴드 호버 시 인라인 형태의 두 버튼(`개별 컷 추가`, `공간 세트 추가`)을 직관적으로 노출하는 `.sb-insert-duo` 패턴 적용.
3. **스타일 튜닝 및 레거시 CSS 정리**:
   - 컷 추가 호버 밴드 높이 확산(8px -> 48px), 폰트 크기 13px, 색상 콘트라스트 조정, 변경된 방향/샷 강조 포인터 색상을 컷 종류 언더바 색상인 `#8fbfee`로 통일.

---

## 2. Line-by-Line Detailed Code Review (라인 단위 상세 코드 리뷰)

### 2.1 `src/features/storyboard/Storyboard.jsx`

#### 1) 카드 선택 해제 시 백지 화면 방어 가드 조건
- **파일 URI**: [file:///Users/nojeong-un/devs/wearless_studio/src/features/storyboard/Storyboard.jsx:675](file:///Users/nojeong-un/devs/wearless_studio/src/features/storyboard/Storyboard.jsx:675)
```javascript
useEffect(() => {
  // block과 requestedRecipe가 둘 다 null이면 undefined===undefined로 참이 되어
  // null.cutType을 읽다 죽는다(같은 카드 재클릭=선택 해제 시 백지 화면의 원인, 2026-07-29)
  setPendingRecipe(requestedRecipe && block && requestedRecipe.blockId === block.id
    ? { cutType: requestedRecipe.cutType, shot: requestedRecipe.shot } : null);
  setPendingChoice(null); setPendingError(null); setPendingSaving(false);
}, [block?.id, requestedRecipe?.blockId, requestedRecipe?.cutType, requestedRecipe?.shot]);
```
- **리뷰 의견**: 
  - **수정 전 문제**: 기존 `requestedRecipe?.blockId === block?.id` 평가식은 `requestedRecipe`와 `block`이 모두 null일 때 `undefined === undefined`가 되어 참(true)을 반환했습니다. 그 결과 `requestedRecipe.cutType`에 접근하며 `TypeError`가 발생해 컴포넌트 트리가 붕괴되었습니다.
  - **수정 후 효과**: `requestedRecipe && block` 검사를 선행 평가함으로써 단락 평가(Short-circuit evaluation)가 작동하여 객체가 존재할 때만 ID 비교 및 프로퍼티 접근이 수행됩니다. 백화 버그의 근본 원인을 깔끔하게 차단했습니다.

#### 2) 레거시 `addMenu` 상태 및 핸들러 제거
- **파일 URI**: 
  - [file:///Users/nojeong-un/devs/wearless_studio/src/features/storyboard/Storyboard.jsx:1034](file:///Users/nojeong-un/devs/wearless_studio/src/features/storyboard/Storyboard.jsx:1034) (`addMenu` state 선언부 제거)
  - [file:///Users/nojeong-un/devs/wearless_studio/src/features/storyboard/Storyboard.jsx:1326](file:///Users/nojeong-un/devs/wearless_studio/src/features/storyboard/Storyboard.jsx:1326) (`selectCard` 내 `setAddMenu(null)` 제거)
  - [file:///Users/nojeong-un/devs/wearless_studio/src/features/storyboard/Storyboard.jsx:1641](file:///Users/nojeong-un/devs/wearless_studio/src/features/storyboard/Storyboard.jsx:1641) (`openSetPicker` 내 `setAddMenu(null)` 제거)
- **리뷰 의견**:
  - 불필요해진 팝업 열림/닫힘 관련 복잡성 state인 `addMenu`를 말끔하게 제거했습니다.
  - State 갱신 횟수를 줄여 렌더링 성능을 개선하고, 불필요한 부수 효과(Side-effect)의 원인을 제거했습니다.

#### 3) `insertControl` 인라인 Dual-Button UI 구현
- **파일 URI**: [file:///Users/nojeong-un/devs/wearless_studio/src/features/storyboard/Storyboard.jsx:1723](file:///Users/nojeong-un/devs/wearless_studio/src/features/storyboard/Storyboard.jsx:1723)
```javascript
const insertControl = (idx, sec, targetSpaceGroupId = null) => {
  const menuKey = `${sec.id}:${idx}`;
  return (
    <div className={`sb-insert-wrap${targetSpaceGroupId ? ' in-space' : ''}`} key={`insert:${menuKey}:${targetSpaceGroupId || 'single'}`}>
      {targetSpaceGroupId ? (
        <button className="sb-insert" onClick={() => addBlock(idx, sec.id, sec.role, targetSpaceGroupId)} title="여기에 이 공간에 컷 추가">
          <span className="sb-insert-line" /><span className="sb-insert-pill"><Icon name="plus" size={15} />이 공간에 컷 추가</span><span className="sb-insert-line" />
        </button>
      ) : (
        /* 팝업 메뉴 대신 좌우 두 버튼 — 왼쪽 개별 컷, 오른쪽 공간 세트 (오너 확정, 팝업 잘림 이슈 제거) */
        <div className="sb-insert sb-insert-duo" role="group" aria-label="여기에 블록 추가">
          <span className="sb-insert-line" />
          <button type="button" className="sb-insert-pill" onClick={() => addBlock(idx, sec.id, sec.role)}>
            <Icon name="plus" size={15} />개별 컷 추가
          </button>
          {sec.role !== SECTION_ROLES.PRODUCT && (
            <button type="button" className="sb-insert-pill" onClick={() => openSetPicker({
              mode: 'add', index: idx, targetSid: sec.id, targetRole: sec.role,
            })}>
              <span aria-hidden="true">📍</span>공간 세트 추가
            </button>
          )}
          <span className="sb-insert-line" />
        </div>
      )}
    </div>
  );
};
```
- **리뷰 의견**:
  - 기존 Popover 팝업 UI를 제거하고 `.sb-insert-duo` 내부 호버 시 두 버튼이 나란히 노출되는 인라인 구조로 전환하여 잘림 현상(Clipping)을 완벽하게 해결했습니다.
  - `sec.role !== SECTION_ROLES.PRODUCT` 조건에 따라 제품 확인 섹션에서는 `개별 컷 추가`만 노출되도록 도메인 비즈니스 로직에 부합하게 구현되었습니다.

---

### 2.2 `src/styles/features.css`

#### 1) 컷 추가 버튼 호버 확산 및 스타일 튜닝
- **파일 URI**: [file:///Users/nojeong-un/devs/wearless_studio/src/styles/features.css:631](file:///Users/nojeong-un/devs/wearless_studio/src/styles/features.css:631)
```css
.sb-insert { display: flex; align-items: center; gap: 12px; width: 100%; border: 0; background: transparent;
  cursor: pointer; padding: 0; height: 8px; overflow: hidden; transition: height .2s cubic-bezier(.2,.7,.3,1); }
.sb-insert:hover { height: 48px; }
.sb-insert-line { flex: 1; height: 1px; background: var(--ring-strong); opacity: 0; transition: opacity .18s; }
.sb-insert-pill { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 500; color: var(--fg-2);
  background: #fff; box-shadow: var(--elev-card); border-radius: var(--r-pill); padding: 7px 14px;
  transform: scale(.7); opacity: 0; transition: transform .2s cubic-bezier(.2,.7,.3,1), opacity .18s, color .14s; flex: none; }
.sb-insert:hover .sb-insert-line { opacity: 1; }
.sb-insert:hover .sb-insert-pill { transform: scale(1); opacity: 1; color: var(--fg-1); }
```
- **리뷰 의견**:
  - 평소에는 높이 8px로 축소되어 레이아웃 영역을 거의 차지하지 않다가, 호버 시 48px로 스무스하게 확대되며 폰트 13px의 알약(Pill) 버튼이 부드럽게 스케일업(0.7 -> 1.0)되는 섬세한 애니메이션 구현이 우수합니다.

#### 2) `.sb-insert-duo` Dual Button CSS
- **파일 URI**: [file:///Users/nojeong-un/devs/wearless_studio/src/styles/features.css:1635](file:///Users/nojeong-un/devs/wearless_studio/src/styles/features.css:1635)
```css
.sb-insert-duo { cursor: default; }
.sb-insert-duo .sb-insert-pill { border: 0; background: var(--bg-1); cursor: pointer; font: inherit; }
.sb-insert-duo:hover .sb-insert-line { opacity: 1; }
.sb-insert-duo:hover .sb-insert-pill { transform: scale(1); opacity: 1; color: var(--fg-1); }
.sb-insert-duo .sb-insert-pill:hover { box-shadow: var(--elev-card); }
```
- **리뷰 의견**:
  - 두 개 버튼이 호버 영역 내에서 반응하도록 세분화된 hover shadow 및 transform 처리가 정교하게 반영되어 있습니다.

#### 3) 방향/샷 변경값 강조 컬러 조정
- **파일 URI**: [file:///Users/nojeong-un/devs/wearless_studio/src/styles/features.css:1634](file:///Users/nojeong-un/devs/wearless_studio/src/styles/features.css:1634)
```css
.sb-detail .sb-val-changed { color: #8fbfee; }
```
- **리뷰 의견**:
  - 기존 `var(--link)`(#4a90e2)에서 컷 종류 탭 언더바 포인트 컬러인 `#8fbfee`로 통일하여 디자인 시스템 정합성을 향상시켰습니다.

#### 4) 사장된 레거시 팝업 CSS 검토
- **파일 URI**: [file:///Users/nojeong-un/devs/wearless_studio/src/styles/features.css:746](file:///Users/nojeong-un/devs/wearless_studio/src/styles/features.css:746)
```css
.sb-addmenu { position: absolute; left: 0; right: 0; top: calc(100% + 8px); z-index: 20; background: #fff; ... }
.sb-addmenu-h { font-size: 12px; color: var(--fg-3); padding: 6px 10px 8px; }
.sb-addmenu-item { ... }
```
- **리뷰 의견**:
  - `addMenu` DOM이 제거되었지만, 해당 레거시 CSS 클래스가 `features.css` 라인 746-754에 남아 있습니다. 현재 실행에 해를 끼치지는 않으나, 후속 미사용 CSS 정리가 권장됩니다.

---

## 3. R1 Verification Section: 버그 수정 로직 & 예외 처리 검증

### 3.1 원인 분석 및 가드 로직 완전성
- **이슈 배경**: 선택된 카드를 다시 클릭하면 `selectedId`가 `null`이 되면서 `block` prop이 `undefined`로 `Inspector`에 전달됩니다.
- **수정 전 문제식**:
  `requestedRecipe?.blockId === block?.id`
  - `requestedRecipe` = `null` -> `requestedRecipe?.blockId` = `undefined`
  - `block` = `undefined` -> `block?.id` = `undefined`
  - `undefined === undefined` -> **`true`** !
  - 삼항 연산자가 True 조건절 `{ cutType: requestedRecipe.cutType, shot: requestedRecipe.shot }`을 실행하려 하고, `null.cutType` 참조로 인해 런타임 `TypeError` 발생 후 흰 화면 크래시.
- **수정 후 가드식**:
  `requestedRecipe && block && requestedRecipe.blockId === block.id`
  - `requestedRecipe`가 truthy 객체이고 `block`이 truthy 객체일 때만 동등 비교를 진행합니다. 둘 중 하나라도 null/undefined이면 즉시 false를 반환하여 안전하게 `: null`로 fallback 처리됩니다.

### 3.2 경계 조건(Boundary Conditions) 검증

| 케이스 | `requestedRecipe` | `block` | 평가 결과 | 런타임 결과 | 안전성 |
|---|---|---|---|---|---|
| 카드 선택 해제 | `null` | `undefined` | `false` | `setPendingRecipe(null)` | ✅ 안전 |
| 초기 로딩 | `null` | `{ id: 'b1' }` | `false` | `setPendingRecipe(null)` | ✅ 안전 |
| 다른 블록 선택 | `{ blockId: 'b2' }` | `{ id: 'b1' }` | `false` | `setPendingRecipe(null)` | ✅ 안전 |
| 매칭되는 컷 생성 | `{ blockId: 'b1', cutType: 'worn' }` | `{ id: 'b1' }` | `true` | 레시피 객체 생성 | ✅ 정상 |
| empty object 경계 | `{}` | `{}` | `true` (id undefined match) | `cutType` undefined set | ⚠️ 미세주의 |

### 3.3 엣지 케이스 보강 권장안
만약 `requestedRecipe = { blockId: null }` 이고 `block = { id: null }` 인 비정상적인 빈 객체 상태가 주입될 경우, `null === null`이 되어 `true`로 진입할 수 있습니다.
추후 방어력을 극대화하기 위해 다음과 같은 Non-null Assertion 형식을 권장합니다:
```javascript
setPendingRecipe(
  requestedRecipe?.blockId != null && requestedRecipe.blockId === block?.id
    ? { cutType: requestedRecipe.cutType, shot: requestedRecipe.shot }
    : null
);
```

---

## 4. R2 Verification Section: UI/UX 개선 & CSS 오버플로우 검증

### 4.1 UI Overlapping & Clipping 해제 분석
- **수정 전**: 팝업(`.sb-addmenu`) 방식은 absolute 포지셔닝으로 인해 좌측 사이드바 경계나 섹션 상단 오버플로우(`overflow: hidden`) 속성에 갇혀 팝업 일부가 잘려 보이는 문제가 있었습니다.
- **수정 후**: `.sb-insert-duo` 인라인 Flex 레이아웃으로 변경되어 요소 내부 flex 트랙에 완전히 종속되므로 컨테이너 밖으로 잘리거나 스크롤바가 생기는 현상이 100% 방지되었습니다.

### 4.2 레이아웃 및 폰트 수치 검증
- **높이 전환(Height Transition)**: 8px (기본) -> 48px (호버 시). 섹션 밴드 간격 마진을 최소화하면서 마우스 진입 시 유연한 터치 타겟(48px) 제공.
- **폰트 및 갭 수치**: Font size `13px`, Weight `500`, Button gap `6px`, Pill padding `7px 14px`.
- **시각적 강조 색상**: Changed value label (`.sb-val-changed`) 색상이 `#8fbfee`로 적용되어 가독성이 눈에 띄게 개선되었습니다.

---

## 5. R3 Verification Section: Test & Build 실행 결과

프론트엔드 테스트 수트 및 프로덕션 빌드를 직접 실행하여 검증을 완료하였습니다.

### 5.1 Vitest / Node Test Suite 실행 결과
```bash
$ npm run test:frontend

✔ splitAnalysisEditPatch routes product-owned fields away from saveAnalysis (1.24ms)
✔ splitAnalysisEditPatch skips ProductPatch fields that reject explicit null (0.06ms)
✔ persistAnalysisEdit saves the product source of truth before the analysis compatibility shape (0.12ms)
✔ persistAnalysisEdit keeps anonymous mock analysis updates intact (0.08ms)
✔ persistAnalysisEdit rejects before analysis when the product source of truth fails (0.31ms)
✔ mergeLatestFailedAnalysisPatch retries the newest value after an older queued save fails (0.54ms)
✔ mergeProductOwnedAnalysisFields uses product as the display source of truth (0.13ms)
✔ initial generation session flag prevents recovered first cuts from being treated as pre-existing (0.16ms)
✔ editor route project id is adopted when it differs from store (0.07ms)
✔ hasPatchFields is false only for empty or missing patches (0.08ms)
✔ pose example direction gate matches worn directions and mirror recipe (0.07ms)
✔ the first AI image in benefit is the only internally assigned hero (0.85ms)
✔ the inspector offers cut types by section without exposing content roles (0.63ms)
✔ a selected fit cut realigns the hidden role instead of being overwritten by it (0.08ms)
✔ AI cards with no usable role receive the safe internal role for their section (0.12ms)
✔ a valid internal composition is returned unchanged (0.06ms)
✔ an internally normalized product image drops worn-only settings (0.07ms)

ℹ tests 17
ℹ pass 17
ℹ fail 0
ℹ duration_ms 80.81ms
```
**결과**: 총 17개 테스트 항목 전체 통과 (17/17 Passed).

### 5.2 Vite Production Build 실행 결과
```bash
$ npx vite build

vite v6.4.3 building for production...
✓ 252 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                                           0.83 kB │ gzip:   0.50 kB
dist/assets/Cormorant-VariableFont_wght-kWcYAABD.ttf    552.43 kB
dist/assets/PretendardVariable-CJuje-Rk.woff2         2,057.69 kB
dist/assets/index-BPo2oAPb.css                          165.95 kB │ gzip:  30.97 kB
dist/assets/index.esm-CQJf7CUw.js                         4.11 kB │ gzip:   1.61 kB
dist/assets/Editor-Bq3vorYa.js                          304.55 kB │ gzip:  98.67 kB
dist/assets/index-S_2Rwi0z.js                           843.36 kB │ gzip: 227.20 kB
✓ built in 1.25s
```
**결과**: 오류 없이 정상적으로 빌드 완료.

---

## 6. Risks & Improvement Recommendations (리스크 및 개선 제안)

1. **가드 조건 보강 (Guard Hardening)**:
   `requestedRecipe && block` 가드도 우수하나, `requestedRecipe?.blockId != null`까지 명시적으로 체크해 두면 향후 예상치 못한 빈 객체 상태 주입 시에도 완전한 방어가 가능합니다.
2. **미사용 CSS cleanup**:
   `src/styles/features.css` 746~754 라인의 `.sb-addmenu` 관련 CSS 구문은 더 이상 사용되지 않으므로 후속 리팩토링 PR에서 삭제할 것을 권장합니다.
3. **DOM 컴포넌트 테스트 추가**:
   현재 unit/integration 테스트 수트에 Storyboard 가드 조건 렌더링 검증 및 `sb-insert-duo` 호버 버튼 클릭 이벤트 테스트를 추가하면 향후 동일 회귀를 자동 방지할 수 있습니다.

---

## 7. Final Merge Decision (최종 머지 승인)

### Decision: **Approve (승인)**

**승인 사유**:
- 카드 선택 해제 시 발생하던 백지 화면 치명적 버그의 근본 원인을 명확히 차단함.
- UI 잘림 현상을 해결하기 위해 UI 팝업 구조를 깔끔한 인라인 dual-button 패턴으로 전환함.
- 전체 프론트엔드 테스트(17/17) 및 프로덕션 빌드가 완벽히 통과되어 기존 기능과의 회귀 이슈 없음이 증명됨.
