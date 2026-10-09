# 생성예시 중심 포즈·방향 정리 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 수동 방향과 과거 포즈 선택값을 제거하고 생성예시의 연출을 생성까지 일관되게 전달한다.

**Architecture:** 화면은 예시 선택을 사용하고 서버는 발행 예시의 방향 메타데이터를 정본으로 읽는다. 과거 포즈를 저장·생성 경계에서 제거하며 예시 포즈 참조와 작은 변주는 유지한다.

**Tech Stack:** Vite, React JSX, Python, FastAPI, Node test, pytest.

**Spec:** `docs/adr/0015-example-owned-pose-and-direction.md`, 이번 대화의 승인된 정리안.

## Global Constraints

- 기존 작업 폴더의 다른 수정은 보존한다. 이번 작업 전 파일 사본과 작업 후 차이를 별도로 기록한다.
- 이번 대화의 PR 생성 승인 범위에서 커밋과 브랜치 푸시를 한다. 배포, DB와 R2 쓰기는 하지 않는다.
- blocks 배열과 섹션·행 계약은 유지한다.
- Vite React와 기존 CSS 토큰, DESIGN.md, 한글 어절 줄바꿈 규칙을 따른다.
- 윈도우의 새 비슷한 컷 구현과 현재 이미지 수정의 포즈·표정 메뉴는 이번 작업 범위가 아니다.

## Review Focus

- 예전 저장본의 포즈가 예시 참고와 색상별 변주를 끄지 않아야 한다.
- 사선 예시를 고른 가상모델은 완전 옆모습 지시를 받지 않아야 한다.
- 예시를 처음 고르는 경우와 교체하는 경우 모두 내부 방향을 복사해야 한다.
- 호리존·스타일링 세트는 완성 예시·포즈 자산의 기존 역할을 유지해야 한다.
- 원본 기반 디테일의 촬영 방향은 예시로 덮어쓰지 않아야 한다.

## Tasks

### Task 1: 서버에서 예시의 포즈와 방향을 정본으로 사용

Files: `server/app/agents/content_roles.py`, `cut_generator.py`, `cut_plan.py`, `confirmed_gpt_runtime.py`, `server/app/workers/detail_page_job.py`, `editor_image_job.py`, 관련 tests.

- [x] 과거 포즈 제거, 예시 방향 복원, 가상모델 사선 지시를 재현하는 실패 테스트를 추가하고 실행한다.
- [x] 자동 배정·명시 포즈 우선 처리를 제거하고 발행 예시 메타데이터를 전달한다.
- [x] 관련 테스트와 작은 변주·동일 컷 재사용 회귀를 확인한다.

### Task 2: 화면의 수동 방향과 포즈 저장값 제거

Files: `src/features/storyboard/Storyboard.jsx`, `src/features/editor/EditorPanels.jsx`, `src/lib/storyboardExampleSelection.js`, `src/lib/storyboardTaxonomy.js`, `src/lib/api/shapes.js`, `src/lib/sections.js`, 관련 tests.

- [x] 예시 선택 결과와 오래된 포즈 정리, 방향 버튼 부재를 검증하는 테스트를 먼저 실행한다.
- [x] 수동 방향 제어를 제거하고 예시 메타데이터를 요청에 전달한다. 예전 포즈값을 새 저장본으로 복사하지 않는다.
- [x] 프론트 테스트와 빌드를 실행한다.

### Task 3: 정본 문서와 최종 검증

Files: `documents/PRD.md`, `documents/common_data_contract.md`, `CONTEXT.md`, 관련 ADR.

- [x] 포즈·방향의 사용자 우선 문구를 현재 예시 우선 계약과 맞춘다.
- [x] 서버 전체 테스트, 프론트 전체 테스트와 빌드를 실행한다. 기존 실패는 별도로 기록한다.
- [x] 사용 가능한 내장 브라우저에서 화면을 확인한다. 브라우저 제한은 우회하지 않고 검증 한계를 보고한다.
- [x] 요청한 코드 리뷰 스킬에 따라 이번 변경만 새 리뷰어가 읽고, 도달 가능한 결함을 수정한다.

검증 결과와 남은 기존 실패는 `.scratch/example-owned-pose-direction-20261009/validation.md`에 기록했다. 좁은 QA 패널의 가로 넘침, 글자 단위 자동 검사와 별도 WebKit 검증 한계는 통과로 표시하지 않는다.
