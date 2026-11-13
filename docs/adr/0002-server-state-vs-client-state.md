# 서버 상태와 클라이언트 상태를 분리하고 Zustand는 얇게 유지한다

product·analysis·mannequins·storyboard·editorBlocks·wardrobe·account·catalogs는 서버 상태(서버 동기화 대상)로 보고 `lib/api` 경계로만 읽고 쓴다 — 백엔드 도입 시 TanStack Query 캐시로 승격한다. Zustand(`useAppStore`)는 라우트를 넘어 살아야 하는 클라이언트 상태만 보유한다: `projectId`와 플로우 선택값(selectedMannequinId, copywriting, 이들은 `patchProject`로 서버 동기화). 사진 양(composeMode)은 템플릿 콘티(ADR-0026)에서 화면, API, 스토어에서 지웠고 DB 컬럼만 남는다. copywriting은 템플릿 콘티에 들어갈 때 늘 `true`로 맞춘다. 한 화면 안에서만 의미 있는 상태(패널 펼침, 편집 중 selection, 에디터 undo 히스토리)는 React 로컬에 둔다. (2026-06-11, `documents/frontend_state_model.md`가 상세)

`adjustCount`는 과거 프로젝트 저장값 읽기 전용이며 현재 선택값이나 제한에 사용하지 않는다.

## Considered Options

- **Zustand 중심(플로우 데이터 전부 스토어 적재)** — 기존 store 주석의 원래 계획. Query 도입 시 서버 캐시와 스토어가 같은 데이터를 이중 관리하게 되어 기각.
- **현행 유지(전부 화면 로컬 + mock DB)** — 선택한 마네킹컷·구성 방식(당시의 사진 양, 지금은 없음)·카피 토글이 생성 단계에 전달되지 않는 증발 문제가 구조적으로 남아 기각.
