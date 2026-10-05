# AI 카피 캡션 슬롯 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AI 카피를 사진 밖의 편집 가능한 캡션 슬롯으로 옮기고, 셀러 텍스트·시그니처 제목·규칙 라벨을 보존하며, 빈 안내 문구가 결과물에 포함되지 않게 한다.

**Architecture:** 서버 조립기와 mock 조립기가 같은 슬롯 shape을 만들고, 대기 스켈레톤은 그 shape을 서버의 동적 이미지 높이에 맞춘다. 에디터는 `placeholder`를 편집 화면에서만 표시하고 preview/export 전용 높이 계산과 DOM 정리를 통해 빈 텍스트를 결과물에서 구조적으로 제거한다. 시그니처 제목 색은 생성 직후 이미지 중앙 띠의 상대 명도를 한 번 측정해 조립 메타로 전달한다.

**Tech Stack:** Python/FastAPI worker + Pillow, Vite React JavaScript/JSX, Node test runner, pytest.

**Spec:** `docs/superpowers/specs/2026-08-04-editor-text-slots-design.md`, `docs/superpowers/specs/2026-08-14-hooking-first-screen-design.md`, `documents/editor_wait_dev_spec.md`, 작업지시서 B1.

## Global Constraints

- 사진 크기는 줄이지 않고 블록 높이만 늘린다.
- 새 카피 슬롯은 `copy_by_block[blockId]`의 `role='headline'`만 소비하며 `body`는 무시한다.
- 시그니처 첫 컷은 `hookTitle`만 두고 캡션·꼬리표를 만들지 않는다.
- 빈 안내 문구는 `placeholder`에만 저장하며 preview/download에는 렌더하지 않는다.
- 템플릿 프레임 동작과 기존 프로젝트 자동 이관은 바꾸지 않는다.
- 카피 생성 프롬프트·검수는 바꾸지 않는다.
- 새 패키지·DB 마이그레이션·커밋을 만들지 않는다.

## Review Focus

- 동적 세로 이미지에서 캡션 y와 블록 높이가 이미지 하단을 따라가는지.
- 옛 오버레이 판정이 셀러 텍스트·배지·hookTitle·캡션·라벨을 오탐 삭제하지 않는지.
- row·signature·일반 single 사이에서 copyRole/sourceBlockId 병합 키가 안정적인지.
- 공백뿐인 텍스트도 preview/export에서 안내 문구처럼 제외되는지.
- 시그니처 밝기 측정 실패·메타 누락 시 안전한 흰색 폴백을 유지하는지.

---

### Task 1: 옛 오버레이만 제거

**Files:**
- Modify: `src/features/editor/editorAppearance.js`
- Test: `tests/frontend/editor-appearance.test.mjs`

**Interfaces:**
- Consumes: 사진 요소의 `sourceBlockId`와 텍스트 요소의 `copyRole`, 좌표.
- Produces: `stripPhotoBlockTextElements(blocks)`가 사진 안쪽의 `headline|body`만 제거.

- [ ] 보존/삭제 사례를 모두 담은 실패 테스트를 작성하고 실행한다.
- [ ] 연결된 사진 상자 안쪽에 있는 legacy `headline|body`만 판정하도록 구현한다.
- [ ] frontend appearance 테스트를 다시 실행한다.

### Task 2: 서버 캡션·꼬리표·행 라벨·시그니처 대비

**Files:**
- Modify: `server/app/agents/page_assembler.py`
- Modify: `server/app/workers/detail_page_job.py`
- Test: `server/tests/test_page_assembler.py`
- Test: worker 밝기 메타를 소유한 서버 테스트 파일

**Interfaces:**
- Consumes: storyboard contentRole/direction/hookTitleOverlay, cut result dimensions/title color, copy result headline.
- Produces: single caption/headline, deterministic label, row label, signature hookTitle color metadata.

- [ ] 정확한 좌표·placeholder·POINT 순번·행 라벨·body 무시·signature 색 테스트를 먼저 작성한다.
- [ ] single/row 조립 헬퍼와 제목 대비 색 계산을 최소 구현한다.
- [ ] 생성 이미지 중앙 띠 색을 worker에서 한 번 측정해 cut result로 전달한다.
- [ ] 관련 pytest를 실행한다.

### Task 3: mock 조립기와 대기 스켈레톤 병합

**Files:**
- Modify: `src/mock/db.js`
- Modify: `src/lib/editorWaitSkeleton.js`
- Test: `tests/frontend/storyboard-opening-row.test.mjs`
- Test: `tests/frontend/generating-wait-window.test.mjs` 또는 `tests/frontend/editor-qa-fixes.test.mjs`

**Interfaces:**
- Consumes: 서버와 동일한 caption/label element shape.
- Produces: mock/server parity와 `genAutoText` 기반 셀러 우선 병합.

- [ ] mock/server shape, 셀러 수정 보존, 미수정 캡션 AI 채움 실패 테스트를 작성한다.
- [ ] mock 슬롯과 `alignSkeletonToServer`를 새 headline 캡션 좌표로 바꾼다.
- [ ] 관련 frontend 테스트를 실행한다.

### Task 4: placeholder 편집 표시와 결과물 제외

**Files:**
- Modify: `src/features/editor/editorGeometry.js`
- Modify: `src/features/editor/Editor.jsx`
- Modify: `src/features/editor/editorExport.js`
- Modify: `src/styles/features.css`
- Test: `tests/frontend/editor-geometry.test.mjs`
- Test: `tests/frontend/editor-export.test.mjs`
- Test: `tests/frontend/editor-appearance.test.mjs`

**Interfaces:**
- Produces: `getBlockRenderHeight(block, { forExport })`, 빈 텍스트 preview 생략, export clone placeholder 제거/높이 축소.

- [ ] 빈 캡션 높이 축소와 preview/export 제외 테스트를 작성해 실패를 확인한다.
- [ ] 편집 화면 placeholder 스타일과 preview 조건부 렌더를 구현한다.
- [ ] export clone의 placeholder 제거와 전용 높이를 구현한다.
- [ ] 관련 frontend 테스트를 실행한다.

### Task 5: 계약 문서와 콘티 안내문

**Files:**
- Modify: `documents/common_data_contract.md`
- Modify: `src/features/storyboard/Storyboard.jsx`
- Test: 기존 frontend source-contract 테스트 또는 가장 가까운 테스트 파일

**Interfaces:**
- Produces: `Element(type='text').placeholder?: string` 계약과 실제 카피 토글 설명.

- [ ] 안내문 기대를 테스트로 고정한 뒤 문구를 `AI가 사진마다 소제목을 써 넣어요`로 바꾼다.
- [ ] 살아 있는 타입 정본인 common contract에 placeholder 한 줄을 추가한다.
- [ ] 삭제된 고아 `src/lib/types.js`는 복원하지 않고 최종 보고에 근거를 남긴다.

### Task 6: 전체 검증과 자체 리뷰

**Files:**
- Review: 전체 diff와 위 계약 문서.

- [ ] 변경 파일별 targeted 테스트를 다시 실행한다.
- [ ] `pnpm test:frontend`를 실행해 기준 실패 외 회귀가 없는지 센다.
- [ ] `pnpm build`를 실행한다.
- [ ] 지정된 전체 server pytest를 실행해 기준 실패 외 회귀가 없는지 센다.
- [ ] `git diff --check`, `git status --short`, 요구사항 체크리스트를 확인한다.
- [ ] 커밋하지 않고 실제 수치·변경 파일·판단 차이·남은 위험을 한국어로 보고한다.
