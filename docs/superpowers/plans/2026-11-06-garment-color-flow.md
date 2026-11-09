# 의류 색상 생성 연결 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. 사용자 지시대로 Codex가 구현하고 Opus 5.5 xhigh가 기획과 검토를 맡는다. 커밋은 하지 않는다.

**Goal:** 추가 색상 착용컷이 대표색의 충분한 상품 근거를 사용하고 마지막에 색상을 적용해 원본 의류의 일관성을 유지한다.

**Architecture:** 저장된 목표 `colorId`와 내부 실행색을 분리한다. 기존 전체 생성 파이프라인의 대표색 결과에 독립된 색 적용 단계를 붙이며, 내부 master를 보관해 연쇄 재색칠을 피한다. UI는 기존 색상 선택을 유지하고 근거와 불가 이유를 명확히 한다.

**Tech Stack:** Vite, React JSX, CSS Modules, FastAPI Python, 기존 R2와 자산 메타데이터.

**Spec:** `docs/superpowers/specs/2026-11-06-garment-color-flow.md`

## Global Constraints

- 작업 공간은 `/Users/daily/.codex/worktrees/garment-color-flow/wearless_studio`, 프로듀싱 작업 공간의 미커밋 사본이다.
- 스냅샷 기준은 `.scratch/colorway-flow-20261106/baseline-hashes.json`. 기존 264개 파일 변경을 되돌리지 않는다.
- 기존 blocks 저장 형식, 크레딧 정책과 확정 GPT 프롬프트를 유지한다. DB 마이그레이션 없음.
- 작업자는 다른 작업 공간을 수정하지 않는다. 총괄은 검증 뒤 원본 프로듀싱 파일의 변경 여부를 확인하고 이번 기능의 차이만 통합한다. 운영 배포, 커밋, 푸시는 금지한다. 이미지 API는 root만 합계 5달러 이내로 실행한다.
- 화면은 DESIGN.md와 토큰을 유지하고 한국어 어절 줄바꿈, 44px 터치 영역, 이름 있는 선택과 키보드 초점을 확인한다.

## Review Focus

- 목표색과 기준색이 뒤집혀 대표색을 추가색으로 저장하는 경우.
- 색 적용 실패나 체크포인트 재사용 시 목표색이 아닌 이미지가 성공으로 나오는 경우.
- 내부 master가 의류 탭에 노출되거나 다른 사용자와 프로젝트에서 재사용되는 경우.
- 대표색 생성과 색 변경의 개별 호출이 사용자에게 두 장으로 과금되는 경우.
- 새 콘티의 저장과 디테일 원본 바인딩을 색 선택 UI가 깨뜨리는 경우.

### Task 1: 서버 색 계획과 생성 연결

**Files:** 새 `server/app/agents/garment_color.py`, 필요 시 `server/app/services/garment_color.py`; `server/app/workers/detail_page_job.py`, `server/app/workers/editor_image_job.py`, `server/app/agents/garment_edit.py`, `server/app/agents/asset_recipe.py`, `server/app/routes.py`, 관련 서버 테스트.

**Interfaces:** 서버 전용 색 계획은 목표색, 실행색, 근거 IDs, `direct` 또는 `base_then_color`를 제공한다. 기존 생성 입력과 출력에 붙일 수 있는 작은 함수로 구현한다. 결과 메타데이터는 `colorExecution`이며 UI 저장 레시피를 대신하지 않는다.

- [x] 목표색 착용컷에서 기준색 입력 후 색 변경 순서, 제품컷 직접 생성, 잘못된 색과 누락 사진을 검증하는 테스트를 먼저 실패시킨다.
- [x] 기존 파이프라인을 호출하는 대표색 실행 계획과 마지막 색 적용 단계를 연결한다.
- [x] master와 목표색 근거의 서버 소유 연결, 체크포인트와 실패 처리를 검증한다.
- [x] 에디터 색상 확장에서 master를 다시 사용하고 원본 보존과 목표색 기록을 검증한다.
- [x] 관련 pytest를 실행하고 변경 목록과 근거를 보고한다.

### Task 2: 입력, 새 콘티와 에디터의 색 선택

**Files:** `src/features/product-input/ProductInput.jsx`, 새 `src/lib/garmentColors.js`, 새 공용 색상 필드와 CSS, `src/features/storyboard/StoryboardPhotoPanel.jsx`, 필요 시 `src/features/editor/EditorPanels.jsx`, `src/features/editor/canvasAiActions.js`, 관련 프론트 테스트.

**Interfaces:** 공용 순수 함수가 실제 업로드 사진으로 선택 가능 여부와 이유를 계산하고 기존 단일 `colorId` 변경을 반환한다. 디테일 표적과 제품 원본 면 보정은 기존 함수를 사용한다.

- [x] 실제 원본이 있는 색 선택, 없는 색 비활성, 제품 뒷면 부족, 디테일 기준색 제한과 저장 값 보존을 테스트한다.
- [x] 기존 색 점과 이름, 선택색 원본 사진, 짧은 사용 안내를 연결한다. 생성 모드 설정은 추가하지 않는다.
- [x] 에디터 색상 변경은 기존 교체와 새 블록 추가 흐름을 유지한다.
- [x] 관련 프론트 테스트와 impeccable detect를 실행한다.

### Task 3: 이미지 확인과 통합 검증

**Files:** `.scratch/colorway-flow-20261106/`, 정본 `documents/PRD.md`, `documents/common_data_contract.md`, `CONTEXT.md`, 새 ADR.

- [x] Opus 독립안과 사용자 정정에 따른 합의, 반대 논거를 기록한다.
- [x] 가능하면 실제 대표색 완성컷과 추가색 사진으로 GPT 소량 검증, 합계 5달러 이하.
- [x] 각 구현의 차이를 검토하고 새 콘티 및 최종컷 전체 연결을 확인한다.
- [x] 전체 프론트, 전체 서버, 빌드, 화면 검증을 실행한다. 실패는 원인과 범위를 남긴다.
- [x] 정본 계약과 이전 프로듀싱 작업에 합칠 변경분을 정리한다.

## 검증 기록

전체 프론트 390파일 통과. 전체 서버 11,125 통과, 140 건너뜀. 마지막 원본 색 기록 보정은 관련 176개 통과 및 독립 재검토. GPT 이미지 API3회 usage 환산 합계 $0.069292. 실제 시험에서 질감/깃 변화 한계를 확인해 별도 색상 검수를 연결함. 화면은 내장 브라우저에서 데스크톱3폭, 입력5폭 검사, WebKit별도 실행은 하지 않음. 상세 결과는 documents/research/2026-11-06-color-flow-validation.md. 커밋/푸시/운영배포 없음.
