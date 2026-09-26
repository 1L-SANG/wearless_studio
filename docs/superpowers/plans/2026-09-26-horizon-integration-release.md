# 호리존 세트 통합 릴리스 Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development for scoped implementation and independent verification. User authorized completion and one PR on 2026-09-26.

**Goal:** 기존 인스펙터를 유지한 세트 선택·드래그·배경 맞춤과 승인된 신규/수정 자산을 하나의 검토 가능한 PR로 완성한다.

**Architecture:** 기존 Vite/Zustand/service와 서버 생성 흐름을 유지한다. 의류 톤은 검증된 사진 근거가 있는 선택 컷에만 추가한다. 자산은 기존 릴리스 계약의 새 불변 경로로 발행하고 서버/프런트 카탈로그를 함께 갱신한다.

**Tech Stack:** React/JS/CSS tokens, FastAPI/Python, existing R2 release tooling.

**Spec:** docs/adr/0013-horizon-set-background-tone.md, documents/space_set_release_contract.md, reference/horizon-inspector-concepts/2026-09-26/pr-readiness.md.

## Global Constraints

- D:/wearless_studio/.worktrees/horizon-set-characteristics에서 작업. canonical 충돌 파일은 덮어쓰지 않는다.
- 기존 Inspector 방향·색상·샷·아우터 등 본문과 중앙 섹션 구조 유지.
- Flare 실험을 제품의 모델 교체·MASTER 입력 전환으로 가져오지 않는다.
- 기존 릴리스 키/마이그레이션은 불변. 대용량 연구·소스 사진은 커밋하지 않는다.

## Review Focus

- 5~7컷 hover에서 일정한 이미지 높이와 실제 scrollTop 변화.
- 세트/개별 드래그 중 wheel/edge scroll, 취소·drop·undo 후 상태 복구.
- 인스펙터 우측 고정 preview의 좁은 화면 fallback; 기본 UI 이동 금지.
- 신규 세트 완성 all의 프레이밍·정체성·컷 순서·계보 일치. 사용자 재확인에 따라 호리존 pose 생성은 하지 않는다.
- 옵션 off/측정 실패는 기존 모델·입력·QC와 같고 배경 지시만 opt-in.

## Tasks

- [x] UI: Storyboard.jsx, scoped hover/CSS, catalog drag/scroll helpers. 기존 Inspector 시각 구조 보존; all 범위 조건만 조정. 실제 browser 이벤트/저장/scroll/pin/undo 22검사. Windows native CDP drag 제약은 기록.
- [x] Capacity: horizon-sequence 최대7·horizon all 필수/pose 선택; rotation/styling 기존 제한. 경계/봉인 roundtrip 테스트 통과.
- [x] Assets: 8세트42·린넨2와 재사용3장, 총47 원본·계보 확인. 사용자 결정대로 pose 추가 생성 없이 완성 예시를 사용. 통합+독립 시각 검수 PASS.
- [x] Release: 불변 stage/hash·dry-run·94개 실제 upload·전수원격SHA 확인·양쪽 catalog적용. 린넨 새ID버전과 구 버전 선택목록 제외/저장호환 유지.
- [ ] Integration: 최신main 기준 관련서버/프런트검사·build·실제UI 확인. 다른에이전트 독립리뷰와 수정재검토.
- [ ] PR: 제품파일만 선별commit/push, 하나의 PR 생성·첨부. 이미배포된detailPR410/417은중복하지 않는다.
