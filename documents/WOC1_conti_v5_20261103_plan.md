# WOC1 콘티 v5 구현 계획

> 실행: Codex가 이 세션에서 직접 구현한다. using-superpowers, brainstorming, writing-plans, executing-plans, test-driven-development를 적용한다. 작업지시서의 비대화형 실행, git 쓰기 금지와 브라우저 금지가 스킬의 승인, 커밋, 화면 실행 절차보다 우선한다.

실제 모델: gpt-6.1-sol, effort: max. 현재 세션 turn_context에서 확인했다.

목표: WOC1 1~40과 PO_LEDGER D130~D169의 두 칸 콘티와 겹침 옆 판을 기존 blocks 저장 계약으로 구현한다.

구조: pageTemplates 순수 함수가 section run, 삽입 위치, 삭제 의존성과 디자인 변경을 맡는다. 콘티는 페이지 목록, 조립 결과 SVG 미리보기, 무대, 예시 탐색, 사진 설정, 섹션 선택, 장소 세트 선택, 옆 판, 다음 단계 버튼으로 분리한다. 저장, 진입, 생성과 크레딧 서비스는 기존 경로를 유지한다.

스택: Vite, React JSX, Zustand, CSS Modules, 기존 CSS 변수. 추가 의존성 없음.

정본: 사용자 WOC1 작업지시서, /Users/daily/Documents/wearless_studio/.scratch/template-po-20261018/PO_LEDGER.md D130~D169, /Users/daily/Documents/wearless_studio/.scratch/conti-a-simple-20261102/TO_PO_1103.md 및 a.html 코드와 shots/v5-*.png.

공통 제약: 기존 미커밋 변경 보존, 작업 공간 안에서만 쓰기, git 쓰기 없음, 브라우저 없음, 유료 API 없음. blocks list와 필드 유지. 첫 장 고정. 한국어 어절 유지. 인라인 스타일 금지. 후속 디테일 사진 판정과 생성 파이프라인은 범위 밖.

검토 초점:

- 숨은 글 섹션과 SNS 재사용 섹션을 구별하고, 보이는 두 섹션 사이 삽입은 아래 run 바로 앞에 둔다.
- SNS 재사용 원본 삭제로 생긴 독립 컷도 양쪽 검증과 저장 정규화를 통과해야 한다.
- 첫 장, 같은 종류 반복 섹션, 장면 묶음과 비슷한 컷의 연속성을 유지한다.
- 모든 훅은 로딩 반환 전 실행한다. 옆 판은 열기와 닫기 초점 복원을 책임진다.
- 리팩터링에 의존한 기존 소스 조각 시험은 새 부품에서 실제 동작을 시험하도록 수정한다.

## Task 1: 섹션과 사진 변경 순수 함수

파일: src/lib/pageTemplates/sectionActions.js, sectionRuns.js, sectionDependencies.js, storyboardGallery.js, storyboardStage.js, seed.js, slots.js, src/lib/storyboardExampleSelection.js, storyboardSpaceSets.js, server/app/agents/content_roles.py.

인터페이스: sectionRuns(blocks), visibleSectionRuns(blocks), moveTemplateSection(blocks,key,beforeKey), changeTemplateSectionDesign(blocks,key,designId), addTemplateSection(...,context.beforeSectionKey/designId/example), removeTemplateSection(blocks,key,context), reconcileSectionDependencies(before,after,product).

- [x] 행동 시험을 먼저 추가하고 실패 확인: run 이동, 첫 장, 글 섹션, 삽입 위치, 디자인 run, 삭제와 재사용, 같은 장소 2장, 세트 이탈, 4칸 묶음 배치.
- [x] 최소 구현과 새 독립 재사용 슬롯에 대한 양쪽 검증 보완.
- [x] 세 템플릿의 10종 및 두 wear usage를 검증하고 빈 사진 칸이 없음을 확인.

## Task 2: 화면 부품과 옆 판

파일: src/features/storyboard/Storyboard.jsx 및 StoryboardPageList.jsx, StoryboardPreview.jsx, StoryboardStage.jsx, StoryboardDrawer.jsx, StoryboardSectionPicker.jsx, StoryboardPhotoPanel.jsx, StoryboardExampleGallery.jsx, StoryboardSpaceSetPanel.jsx, StoryboardContinue.jsx와 콘티 CSS Modules.

인터페이스: 순수 함수 결과와 기존 서비스 핸들러를 props로 전달한다. API 호출은 기존 api 서비스만 사용한다. 미리보기는 assemblePage의 실제 geometry와 선택 예시 cutResults로 그린다.

- [x] 목록과 무대, 옆 판 상태의 행동 시험을 먼저 작성하고 실패 확인.
- [x] 고정 오른쪽 판을 제거하고 모드별 겹침 옆 판, 전체 보기, 끌기와 키보드 이동을 구현.
- [x] 단일컷과 세트컷, 장소 탭, 구도 필터, 3×2 넘김과 이미 사용 표시 구현.
- [x] 기존 사진 설정, 원본 선택, 매칭, 배경, 자동 저장, 다음 단계와 되돌리기를 연결.
- [x] 이번 변경으로 사용이 끝난 SectionAddMenu, TemplateSectionBand와 관련 코드 제거.

## Task 3: 이름과 조립 parity

파일: server/app/data/page_templates_v1.json, src/lib/pageTemplates/slots.js, src/lib/sections.js, src/features/editor/Editor.jsx 및 frontend/server 회귀 시험.

- [x] 카탈로그 이름과 usage별 표시를 적용. 저장된 블록 이름을 재작성하지 않는다.
- [x] 고정 씨앗으로 섞은 세 템플릿 페이지의 JS와 Python 결과를 직접 비교한다.
- [x] 새 부품 구조에 맞게 기존 행동 시험을 보존하고 변경된 결정의 기대값만 갱신한다.

## Task 4: 검증과 인계

- [x] pnpm test:frontend 전체.
- [x] server/에서 /Users/daily/Documents/wearless_studio/server/.venv/bin/pytest -q --ignore=tests/test_personalization.py 전체.
- [x] pnpm build.
- [x] README Storyboard presentation 갱신, 항목 1~40과 재사용 표, 차이와 화면 확인 경로를 WOC1 보고서에 기록.

## 실행 기록

- 시작 상태를 .scratch/woc1-20261103/baseline에 복사했다. 기존 미커밋 파일은 되돌리지 않는다.
- 결정: 카탈로그상 재사용 사진 칸이 있는 SNS는 왼쪽에 보인다. 글만 있는 intro와 for_you만 숨긴다.
- 결정: SNS의 재사용 원본이 없을 때 기존 slotKey에 독립 AI 컷을 저장한다. 필드 추가 없이 SNS text-only 검증에 해당 reuse 슬롯 사진만 허용한다.
- 사전 검사: JS와 Python 조립은 모두 저장된 연속 run 순서이며, 템플릿 순으로 되정렬하는 코드는 sectionActions의 삽입 위치 계산에만 남아 있다.

- Task 1 complete: section, photo, dependency, gallery and row-packing tests verified RED then GREEN.
- Task 2 complete: actual JSX callback/focus/drag/gallery tests and loaded-page SSR; no browser.
- Task 3 complete: catalog/old-editor labels and direct JS/Python parity for 18 shuffled/dependency cases.
- Task 4 complete: frontend 4,809 passed, server 10,696 passed and 183 skipped, build passed. All three final commands exited 0. Report and README updated. No browser or git writes.
- Review fix pass: source edits repair crop/detail references, SNS required slots, set gallery visibility, eligible drag companions, repeated-run previews, mixed-family replacement, undo drawer closure, 12-cut capacity, and all-seller-photo additions.
- Ruling: if no eligible same-place companion exists, keep two valid slots using the original example; do not invent an asset or use another garment/gender. PO should verify those data-limited examples separately.
- Ruling: only actual design reuse slots materialize (one for I09-D14, two for P07-D08), and the original catalog source remains authoritative even with repeated sections.
