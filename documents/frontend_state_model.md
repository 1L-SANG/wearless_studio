# 프론트 상태 모델 (Frontend State Model)

> 상태: 확정 (2026-06-11, 갱신 2026-06-29) · 짝 문서: `documents/common_data_contract.md` · 결정 기록: `docs/adr/0002`
> 현재 코드와 이 모델의 차이(갭·적용 순서)는 `documents/TODO.md`가 추적한다 — 문서가 목표 상태다.

---

## 1. 상태 3계층과 판정 기준

상태를 추가할 때 아래 두 질문으로 계층을 정한다. 계층을 건너뛰는 배치(화면 전용 상태를 Zustand에 올리기 등)는 금지.

```
Q1. 새로고침 후에도 남아야 하나? (또는 다른 기기에서 보여야 하나?)
    YES → ① 서버 상태 — api 경계로만 읽고 쓴다
Q2. (아니라면) 라우트를 넘어 살아야 하나?
    YES → ② 전역 클라이언트 상태 — Zustand(useAppStore)
    NO  → ③ 화면 로컬 상태 — useState/useRef
```

| 계층 | 보관 위치 | 예 | 백엔드 도입 후 |
|---|---|---|---|
| ① 서버 상태 | api 경계(mock 또는 http; VITE_API_MODE) + 화면 fetch | product, analysis, mannequins, storyboard, editorBlocks, wardrobe, account, catalogs, library, project | TanStack Query 캐시로 전면 승격(Pricing·Library·CreditsHistory는 완료), store의 account/catalogs 캐시는 제거 |
| ② 전역 클라이언트 | `useAppStore` | projectId, 플로우 선택값(아래 §3) | 유지 (선택값은 patchProject로 서버 동기화) |
| ③ 화면 로컬 | 컴포넌트 useState/useRef | 폼 draft, hover, 패널 펼침, 로딩 phase, 편집 중 selection, 에디터 undo 히스토리 | 유지 |

주의: `composeMode`·`copywriting`·`selectedMannequinId`·`adjustCount`는 **②이면서 서버 동기화 대상**이다. Zustand가 작업 사본을 들고, 변경 시 `api.patchProject`로 동기화한다. "Zustand에 있으니 클라 전용"이 아니다.

---

## 2. 서버 상태 — 소유 API와 읽는 화면

| 데이터 | 읽기 | 쓰기 | 쓰는 시점 |
|---|---|---|---|
| Project | `getProject` | `createProject` `patchProject` | 새 제작 시작 / 선택값 변경 / 단계 전환 |
| Product | `getProduct` | `saveProduct` | 입력 완료 시, 분석 폼에서 실측·의류 종류 수정 시 |
| Analysis | `analyzeProduct` | `saveAnalysis` | 분석 폼 수정 시 (Product 소유 필드 제외) |
| MannequinCut[] | `getMannequins` | `generateMannequins` `regenerateMannequin({fitProfile})` | 마네킹 단계 (구 `adjustMannequin` 폐기) |
| StoryboardBlock[] | `getStoryboard` | `saveStoryboard` | **생성 CTA 클릭 시** (편집 중엔 로컬 working copy) |
| EditorBlock[] | `getEditorBlocks` `generateDetailPage` | `saveEditorBlocks` | 에디터 로드 / 저장·디바운스(구현됨) |
| Wardrobe | `getWardrobe` | `generateImage` `uploadAsset` | 에디터 |
| Account / Catalogs / Library | `getAccount` `getCatalogs` `getLibrary` | — (credits는 봉투 응답으로 갱신) | 앱 셸 / 보관함 |

---

## 3. useAppStore 확정 형태

```js
useAppStore = {
  /* ① 서버 상태의 전역 캐시 — TanStack Query 전면 전환 전까지 store가 보관 (Pricing·Library·CreditsHistory는 이미 useQuery로 전환) */
  account: Account | null,
  catalogs: Catalogs | null,
  loadAccount(), loadCatalogs(),          // 1회 로드 (현행 유지)
  syncCredits(credits),                   // 크레딧 봉투 응답의 잔액 반영 (구 spendCredits 대체)

  /* ② 현재 작업 프로젝트 */
  projectId: string | null,
  startProject(),                         // api.createProject() + 아래 선택값 초기화 (구 resetFlow)

  /* ② 플로우 선택값 — project 필드의 작업 사본. setter는 store 갱신 + api.patchProject 동기화 */
  selectedMannequinId: string | null,  selectMannequin(id),
  composeMode: 'basic',                setComposeMode(v),
  copywriting: true,                   setCopywriting(v),
  adjustCount: 0,                      // 서버 응답으로만 갱신 (adjust/regenerate 결과 반영)
}
```

**store에서 제거하는 것** (현재 선언만 있고 사용처 0): `product` `analysis` `mannequins` `storyboard`와 그 setter들(`setProduct` `patchProduct` `setAnalysis` `patchAnalysis` `setMannequins` `setStoryboard`). 이 데이터는 ① 서버 상태다 — 화면이 api로 fetch한다 (ADR-0002).

---

## 4. 화면별 상태 매핑

| 라우트 | 읽는 서버 상태 | 저장(쓰기) 트리거 | store 사용 | 대표 로컬 상태(③) |
|---|---|---|---|---|
| `/create/input` | getProduct, getCatalogs → analyzeProduct | 입력 완료 → `saveProduct`(+uploadComplete) 후 분석; 분석 폼 변경 → 필드 소유자 따라 `saveAnalysis` 또는 `saveProduct` | projectId 없으면 `startProject()` | phase(input/analyzing/done), expanded, 칩 draft, 소재 편집 인덱스 |
| `/create/mannequin` | getMannequins(비면 generateMannequins), getProduct, getProject | 조정/재생성 → api 호출 → `syncCredits` + adjustCount 반영; 컷 선택 → `selectMannequin`; 구성 방식 → `setComposeMode` | selectedMannequinId, composeMode, adjustCount | progress, busy, picking, confirmRegen, 조정 세그 선택값 |
| `/create/storyboard` | getStoryboard, getProduct(색상), getAnalysis(매칭 후보·선택) | **생성 CTA → `saveStoryboard` → navigate** | copywriting(`setCopywriting`) | blocks working copy, selectedId, splitOpen, dirty, 스냅샷 ref, drag 상태 |
| `/create/generating` | generateDetailPage(projectId) | 완료 시 서버가 status='done' | projectId, syncCredits | progress, steps |
| `/editor/:projectId` | getEditorBlocks, getWardrobe, getCatalogs, getAccount, getProduct | 이미지 생성/변형 → `generateImage` + `syncCredits`; 저장 → `saveEditorBlocks`(저장 버튼+디바운스) | account(표시), syncCredits, projectId | blocks + undo 히스토리, selEls/selBlock, cropping, scale, tab, layerFloat, pendingSlot, varyTarget |
| `/library` | getLibrary | 새 상세페이지 → `startProject()` | startProject | phase, items |

콘티보드의 blocks는 "서버 상태의 working copy" 패턴이다: 진입 시 fetch → 로컬에서 편집(스냅샷/원래대로 포함) → 생성 CTA에서 한 번에 `saveStoryboard`. 카드 단위 '수정 완료'는 로컬 확정일 뿐 서버 저장이 아니다.

---

## 5. 플로우 시퀀스 — 단계 전환마다 무엇이 저장되는가

```
[새 제작]   startProject() → api.createProject() → projectId 확보, 선택값 초기화
[입력]      입력 완료 → saveProduct({name, colors, uploadComplete:true}) → analyzeProduct()
[분석 확인]  폼 수정 → saveAnalysis(patch) / 실측·의류 종류는 saveProduct(patch)
            "마네킹컷 만들기" → navigate (분석은 이미 저장돼 있음)
[마네킹]    진입 시 자동 generateMannequins → syncCredits
            핏 확인 스텝(유지/조정) → 변경 시 regenerateMannequin({fitProfile}) → syncCredits, 새 버전 자동 선택
            컷 선택 → selectMannequin(id) → patchProject({selectedMannequinId})
            구성 방식 → setComposeMode(v) → patchProject({composeMode})
[콘티]      getStoryboard(projectId)  ← project.composeMode 기반 구성
            편집은 로컬 → 생성 CTA → saveStoryboard(blocks) + setCopywriting 동기화 → navigate
[생성 대기]  generateDetailPage(projectId) — 서버가 저장된 콘티·selectedMannequinId·copywriting을 읽음
            완료 → navigate(/editor/:projectId)
[에디터]    getEditorBlocks(projectId) → 로컬 편집 + undo. 저장은 saveEditorBlocks(저장 버튼+1.5s 디바운스+이탈 플러시, 구현됨)
[보관함]    getLibrary() → 카드 클릭 → /editor/:projectId
```

핵심 규칙: **생성 단계는 클라이언트가 들고 있는 값을 믿지 않는다.** `generateDetailPage`의 입력은 전부 서버(project + 저장된 storyboard)에서 읽는다. 그래서 콘티 CTA에서 saveStoryboard가 필수다.

---

## 6. 크레딧 — 단일 소스 규약

- 표시 소스는 **`store.account.credits` 하나**다. TopNav 배지와 에디터가 같은 값을 본다. 에디터의 자체 `account` 사본은 제거한다.
- 차감은 서버(현재 mock api) 책임이다. 크레딧 소모 API의 `{ data, credits }` 봉투에서 `credits`를 받아 `store.syncCredits(credits)`로 반영한다. 프론트 선차감·자체 계산 금지.
- 소모 전 예고(버튼 라벨)는 `catalogs.creditCosts`(원본 `lib/limits.js`)로 계산한다 — 이건 표시용이며 차감 근거가 아니다.
- **mount effect에서 크레딧을 소모하면 반드시 멱등이어야 한다.** 두 겹으로 보장한다 — ① 화면: 소모 호출 직전에 `cancelled` 플래그(cleanup에서 set)를 확인해 StrictMode 이중 실행을 차단(`Mannequin.jsx`·`Generating.jsx` 패턴). ② 서버(mock api): 진행 중인 유료 job에 중복 시작 요청이 오면 새 작업 대신 **기존 job에 합류**시켜 1회만 차감(계약 §6 유료 job 멱등 규약 — 생성 중 이탈 후 재진입까지 커버). 새 project 생성 같은 서버 변이도 마운트가 아닌 명시적 사용자 액션에서만 일으킨다.

---

## 7. 에디터 내부 상태 노트 (의도된 로컬 설계)

에디터는 단일 라우트의 고빈도 편집 화면이므로 아래는 **로컬 유지가 결정 사항**이다. Zustand로 옮기지 않는다.

- `blocks` + undo/redo 히스토리: `useRef` 기반 past/future 스택, 350ms 내 연속 변경 병합, 상한 80. 저장은 이 로컬 상태의 스냅샷을 `saveEditorBlocks`로 보내는 것(구현됨).
- react-moveable 제스처: 진행 중에는 DOM style에만 라이브 반영(`liveRef`)하고 gesture end에 한 번 상태 커밋 — 매 프레임 setState가 컨트롤박스를 재생성해 리사이즈를 죽이는 되먹임을 차단하는 검증된 패턴. 유지.
- selection(selEls/selBlock), cropping, scale, tab, layerFloat, pendingSlot, varyTarget: 전부 ③ 화면 로컬.

---

## 8. 현행 코드 갭과 적용 순서

→ **`documents/TODO.md` §1로 이관.** 상태 계층 관점의 갭·적용 순서(P0 선택값 증발 해결은 ✅ 반영 완료, TanStack Query·Analysis 중복 필드 제거 등은 남음)는 계약 쪽 마이그레이션 갭과 함께 TODO.md 한곳에서 추적한다.
