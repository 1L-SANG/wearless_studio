# 의류 GIF 저장과 내보내기 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 의류 안에서 GIF를 만들고 저장해 캔버스와 다운로드에서 사용할 수 있게 한다.
**Architecture:** 기존 인코더와 업로드 어댑터를 재사용한다. GIF 메타데이터는 이미지 요소에 동반하며 내보내기만 poster로 치환한다. 서버 없는 미삽입 목록은 로컬에 보관한다.
**Tech Stack:** Vite, React JSX, CSS Modules, gifenc, JSZip, Node test.
**Spec:** docs/superpowers/specs/2026-10-13-thumbnail-gif-design.md

## Global Constraints
- 지정 워크트리와 feat/thumbnail-gif 브랜치만 사용. 원격 푸시, 유료 API, 네트워크, 패키지 설치 금지.
- 서버와 DB, 콘티보드 계약 유지. 기존 토큰, 밑줄 탭, 알약 규칙 준수.
- 브라우저 검증은 Claude 담당. 최종 빌드와 전체 프론트 테스트를 실행한 뒤 하나의 구현 커밋으로 묶는다.

## Review Focus
- 미삽입 저장 뒤 재접속과 프로젝트 변경: 목록 복원과 프로젝트 격리.
- poster 업로드 실패: 결과 유지, 미완성 항목 없음.
- GIF가 들어 있는 슬롯의 일반 사진 교체와 비우기: 이전 GIF 메타데이터 제거.
- 같은 GIF를 여러 번 넣거나 숨김: ZIP의 원본 중복 제거와 숨김 제외.
- 공증 ZIP: 정지 이미지 서명 바이트 유지, GIF는 공증 대상에서 제외.

### Task 1: 규격과 제작 패널
**Files:** src/lib/gif/gifLadder.js, encodeGif.js, src/features/editor/GifThumbnailPanel.jsx와 CSS, EditorPanels.jsx, Editor.jsx, tests/frontend/gif-thumbnail.test.mjs.
**Interfaces:** presetSize(id, scale, sourceSize), coverCrop(..., { anchorY }), GIF_PHOTO_DELAY_MS=1500. WardrobePanel의 onSaveGif(result) 소비.
- [x] 원본 높이 짝수, 상한 1200, crop 8%, 고정 시간, 선택과 모드 테스트를 먼저 추가하고 실패 확인.
- [x] 기존 사진 타일, 탭, 비율 틀, 저장 버튼을 구현하고 테스트 통과 확인.

### Task 2: 저장과 이미지 계약
**Files:** src/lib/gif/gifMedia.js, src/lib/api/gifWardrobe.js, httpAdapter.js, src/mock/api.js와 db.js, editorWardrobe.js, editorLibrary.js, presets/infoPresets.js, editorSelection.js, canvasAiApply.js, Editor.jsx.
**Interfaces:** saveWardrobeGif(projectId, result, options), deleteWardrobeGif(projectId, id), gifImageFields(image), isGifImage(image), gifStillPatch(image).
- [x] 목 저장과 조회, URL 독립 수명, 실패 원자성, 블록과 슬롯 삽입 및 일반 사진 교체 테스트를 추가하고 실패 확인.
- [x] 업로드와 로컬 목록, 메타데이터 전달과 해제를 구현하고 테스트 통과 확인.

### Task 3: 인스펙터와 내보내기
**Files:** EditorPanels.jsx, useCanvasAi.js, canvasAiActions.js, EditorDownloadModal.jsx, editorExport.js, gifMedia.js.
**Interfaces:** collectGifImages(blocks), replaceGifPosters(clone), appendGifFiles(zip, images), downloadGifImages(images), downloadGifImage(image).
- [x] 정지 전환과 Undo용 불변 patch, AI 제외, poster 치환, ZIP 바이트와 공증 제외 테스트를 추가하고 실패 확인.
- [x] GIF 정보, 받기와 정지 전환, 다운로드 안내와 별도 파일 포함을 구현하고 테스트 통과 확인.

### Task 4: 전체 검증과 커밋
- [x] pnpm build. 종료 코드 0 확인.
- [x] VITE_API_BASE_URL='' pnpm test:frontend:files tests/frontend/gif-thumbnail.test.mjs. failed: 0 확인.
- [x] VITE_API_BASE_URL='' pnpm test:frontend:files 'tests/frontend/*.test.mjs'. failed: 0 확인.
- [x] 신선한 컨텍스트의 리뷰 한 번, 필요한 수정만 재현 테스트로 검증.
- [ ] git diff --check와 브랜치 확인 후 feat/thumbnail-gif에만 커밋. 원문 검증 로그와 해시 보고.

## 검증 결과와 인계
- 실행 모델: 현재 세션 turn_context에서 gpt-6-astra, effort xhigh 확인.
- 빌드: pnpm build 종료 코드 0, built in 12.57s. 기존 대형 청크 경고가 남음.
- 지정 GIF 명령: files: 293, failed: 0. GIF 테스트는 42개 모두 통과.
- 전체 명령: files: 293, failed: 0. 전체 Node 테스트 3663개 모두 통과.
- 독립 리뷰의 정보 카드 GIF 정보 유실 1건을 재현한 뒤 수정. 재현 테스트와 전체 테스트 통과.
- 브라우저, 실서버 업로드, 유료 API 호출은 실행하지 않음. 브라우저는 지시대로 Claude가 확인.
- 커밋 차단: git add가 `.git/worktrees/gif-thumb-1013/index.lock` 생성 시 Operation not permitted를 반환함. 이 세션의 Git 폴더는 읽기 전용이며 권한 승격이 허용되지 않는다. 스테이징과 커밋을 만들지 못했고 푸시는 하지 않았다.
- 변경은 지정 워크트리의 feat/thumbnail-gif에 남아 있다. Git 쓰기 권한이 있는 환경에서 최종 커밋이 필요하다.
