실제 모델: gpt-6-astra · effort: xhigh. 이번 실행의 turn_context에서 확인했다.

# WOC5 화면 수정과 옅은 칸

기준 커밋은 `ed817527ea99bbb7a8f62de06ff677471dc42a96`이다. 새 작업 공간에서만 수정했다. 장부 D282와 제품컷 SPEC 3절을 따랐으며, 저장 옵션은 지시대로 기존 `pageDesignId` 짝으로 표현했다. 새 블록 필드, DB 변경, git 쓰기, 브라우저 실행, 유료 API 호출은 없다.

## 항목별 구현

| 항목 | 구현과 주요 파일 |
|---|---|
| 가. 섹션 도구 | `StoryboardPageList.jsx`, `StoryboardLayout.module.css`: 썸네일 왼쪽 위에 손잡이·휴지통 알약을 항상 표시한다. 기존 `--sp-28`로 높이·단추 폭을 맞췄다. 위아래 화살표는 알약 hover/focus-within에서 펼치며 Tab 순서에서 빠지지 않는다. 첫 장 도구 없음, 경계 이동 제한, 이동 후 같은 단추 초점을 유지한다. 총괄이 고친 제목 gap `--sp-3`와 컷 수 margin 0도 유지했다. |
| 나. 제목 안내 | `Storyboard.jsx`, Layout 모듈: “예시 이미지들로 상세페이지 초안이 구성됐어요. 원하는 느낌으로 구성해보세요.”로 교체했다. 전체 머리 폭을 쓰는 별도 grid 행, `--sb-fs-intro: 16px`, 본문 보조색 `--fg-2`다. nowrap이나 word-break 덮어쓰기는 없다. |
| 다. 글자 스타일 | `StoryboardThemePicker.jsx`, Choices 모듈: `lbl`의 “글자 스타일”, 기존 순서·title·저장 경로를 유지했다. 비선택 opacity와 `--sb-theme-inactive`를 없앴다. 고른 견본만 둥근 1.5px 잉크 안쪽 테두리다. 판이 열려도 견본은 흐려지지 않는다. `--fg-1`의 흰 바탕 대비는 19.33:1, `--bg-2` 대비는 17.73:1이다. |
| 라-1. 첫 장 고스트 | 새 `pairedDesigns.js`, `stageAdditions.js`, `sectionDependencies.js`: 실제 첫 장의 짝 디자인으로 켬·끔을 표현한다. 원래 색상과 제품 run의 모든 AI 전체컷이 명시적 ghost이고 기준 색 앞면이 있으면 재사용하여 +0컷이다. 그 외 확정 방식/원본 제거 상태에서는 깨끗한 `first.ghost` 블록을 만들어 +1컷이다. flat↔natural_light 전환과 반복 정리에서 ID가 유지되고, ghost 재사용 가능 시 전용 블록을 없앤다. 기준 색 변경도 따라간다. 다른 디자인을 고르면 새 기본 디자인의 짝으로 옮긴다. X는 짝과 전용 블록만 없애고 원본 제품 사진을 지우지 않는다. |
| 라-2. 같은 장소 1컷 더 | 묶음의 아직 안 쓴 다음 발행 멤버를 옅은 칸으로 보여 준다. 클릭하면 같은 `spaceGroupId`·배경 규칙·pose 참고를 유지하며 묶음 끝에 넣는다. 실제 저장 순서와 조립 사진 순서도 그 자리다. 남는 멤버 없음/실제 섹션 사진 12컷이면 사라진다. 스튜디오는 없다. 일반 “컷 추가”는 이 칸으로 대체하고 장소 세트 추가는 유지했다. |
| 라-3. 원단 확대 | 분석의 사용 가능한 fabric 후보, 페이지에 원단 run 없음, 같은 target 미사용, 실제 AI detail 4컷 미만일 때만 표시한다. points run에 바인딩된 detail 블록과 기존 `pointItems` 항목 하나를 더한다. 옅은 칸부터 실제 원본 크기를 측정하고, 추가 후 조립과 같은 ROI·확대 상한으로 보여 준다. 따온 사진 줄과 독립 detail 줄은 섞이지 않는다. |
| 라-4. 원단 제품컷 | 기준 색 앞면 원본을 다시 쓰는 짝 디자인이다. ghost/flat/natural_light 모두 생성 +0이다. 재사용 원본이 없어지면 기본 원단 디자인으로 돌아가며 새 생성 블록은 만들지 않는다. X와 undo는 원본을 유지한다. 정상 모드에서는 지정 제품컷 예시 자산을 쓴다. |
| 라-5. 색상과 제품 | 옅은 칸을 두지 않는다. `requiresBack`의 기존 기준 색 Back 사진 유무에 따른 시드 동작을 두 템플릿에서 검사했다. |
| 라. 저장·조립·후보 | `designChoices.js`에 pickerHidden 필터를 더했다. 짝의 기본 디자인을 기준으로 기존 종류·쓰임·사진 수 규칙을 적용하고, 선택할 때만 현재 켜진 짝을 유지한다. WOC6 후보 정책은 추가하지 않았다. Stage의 paleAddition/geometry/view ID는 화면 상태이며 저장하지 않는다. 네 가지 넣기와 짝 빼기는 기존 원자적 저장·한 건 undo 경로다. |
| 마. 사진 판 | `StoryboardPhotoGallery.jsx`: 시그니처 카드 왼쪽 위에 작은 이름표. `StoryboardPhotoPanel.jsx`, 새 `StoryboardGarmentColorField.jsx`: “의류 색상 선택”과 색 원·이름 단추를 맨 위에 두고, 한 색 상품이면 숨긴다. 기존 원본 사진 가용성·색 선택 패치·막힌 색 안내는 유지했다. 첫 장에 표시한 고스트는 기준 색 앞면을 재사용/생성하는 고정 자리여서 그 판에서 색이나 촬영 방식을 바꾸지 않는다. |
| 단위 | `StoryboardContinue.jsx`: “구성 n컷”. 예시 스택·펼침·배경 제어·비슷한 컷 정리·최소 과금 안내의 생성 수는 컷으로 바꿨다. 업로드 진행 “사진 n장 중 n장 올렸어요”는 유지했다. |

## 짝 디자인과 서버 변경

| 기본 | 짝 | 상태 | 추가 자리 |
|---|---|---|---|
| P01-D11 | P01-D01 | 기존 정의, pickerHidden=true | ghost |
| P01-D09 | P01-D09-G | 새 정의, pickerHidden=true | ghost |
| P01-SHOW-01 | P01-SHOW-01-G | 새 정의, pickerHidden=true | ghost |
| T03-D01 | T03-D01-P | 새 정의, pickerHidden=true | product |
| T03-D06 | T03-D06-P | 새 정의, pickerHidden=true | product |

카탈로그는 총 32개이며 숨김 짝 5개다. 새 짝은 원래 레이아웃 뒤에 기존 panel/photo를 붙이고, 첫 장은 P01-D01의 연한 판과 고스트 자리 치수를 사용한다. 저장된 숨김 디자인도 정상 조립한다.

서버 수정은 `page_templates.py`의 숨김 bool·재사용 슬롯 검증과 `page_template_assembler.py`의 원래 run/기준 색/앞면/명시적 고스트 재사용 조건, P01-D09-G의 기존 제목 줄바꿈·배치 정책 적용뿐이다. JS도 동일하다. routes, content_roles 저장 정규화, 생성 엔진, 필수 단추 QC, 크레딧 정책은 수정하지 않았다. 새 전용 고스트는 기존 제품 전체컷 계약을 통과한다. 실제 저장·생성 plan·QC 분기·필수 앞면 검사·중복 생성 수는 provider를 호출하지 않는 서버 시험으로 확인했다.

전용 고스트는 product/ghost/front, angle same, 기준 색, 빈 match/ref 배열만 갖는 새 recipe다. 예시·장소 세트·무드·비슷한 컷·셀러 이미지 입력을 기존 제품 블록에서 복사하지 않는다. 재사용 원본의 생성 실패로 별도 컷을 추가하지 않으며 같은 실패/재시도 source를 유지한다.

## 경계에서 선택한 동작

- 이미 저장된 누락/혼합 촬영 방식은 추정하지 않는다. 기존 방식 선택과 다음 단계 차단을 유지하며 첫 장 고스트 옅은 칸은 방식이 확정된 뒤 보여 준다.
- 원단 제품 재사용이 켜진 뒤 원본이 없어지면 원래 디자인으로 돌아간다. 자동 유료 생성은 없다. undo는 직전 원본과 짝을 함께 복원한다.
- 원단의 재사용 가능한 옛 업로드/미확정 run에 촬영 방식이 없으면 모드를 지어내지 않고 실제 앞면 원본을 옅은 그림으로 사용한다.
- 따온 사진 3개와 원단 detail이 섞인 points에서 마지막 착용 원본을 빼면, 사용할 수 없는 따온 항목만 제거하고 독립 원단 detail은 남긴다. 확인창도 “따온 사진만 빠지고 디테일 사진은 남아요”라고 알린다. 따온 항목만 있던 run은 기존처럼 빠진다.
- catalogVersion은 기존 `page-templates-2026-10-28.2`를 유지했다. 이번 정의 추가만으로 버전을 올리면 기존 show pageStyle이 정규화에서 기본 emo로 돌아가는 문제가 있어 저장 스타일 호환을 우선했다.
- 크레딧은 기존 고유 생성 수와 최소 과금 규칙을 따른다. +1 생성이어도 최소 과금 구간에서는 금액이 그대로일 수 있다.

## 시험

| 명령 | 최종 결과 |
|---|---|
| `pnpm test:frontend` | **5,412 통과, 실패 0, 건너뜀 0**. 최종 로그 `frontend-complete.log`, 68.04초. |
| `server/`에서 `/Users/daily/Documents/wearless_studio/server/.venv/bin/pytest -q --ignore=tests/test_personalization.py` | **11,939 통과, 285 건너뜀, 실패 0**. 경고 1,043건, 273.66초. `server.log`. |
| `PAGE_TEMPLATE_PYTHON=/Users/daily/Documents/wearless_studio/server/.venv/bin/python node scripts/regenerate-page-template-fixtures.mjs` | **148개**의 JS/Python 전체 결과 일치 뒤 재생성. WOC5 고정 사례 16개 추가. `fixtures.log`. |
| `pnpm build` | **성공**, 8.00초, exit 0. `build.log`. 정적/동적 import 혼용 및 큰 청크 경고는 남아 있다. |
| `~/.claude/skills/impeccable/scripts/impeccable detect --json <변경 JSX/CSS 18개>` | exit 0, 결과 `[]`. `impeccable.json`. 파일만 검사했고 URL/브라우저 검사는 하지 않았다. |
| `git diff --check` | 통과. |

새 회귀 시험은 옅은 칸 조건·소진·상한·중복 target·실제 넣는 자리·원단 ROI 일치, 재사용/전용 생성과 가격, 촬영 방식 전환·기준 색 변경·ID 안정성·X/undo snapshot, 숨김 후보와 저장된 짝, 마지막 착용 원본 제거 확인창을 포함한다. 서버의 WOC5 생성 경로 시험 13개는 유료 provider 없이 저장 정규화·생성 plan·필수 앞면·필수 단추 QC 분기를 실행했다. 초기 통합 시험에서 발견된 옛 화면 문구·직접 고스트 디자인 선택 기대와 테스트 harness 연결도 실제 새 경로를 검사하도록 갱신했다.

## 총괄 화면 확인

새 작업 공간에서 `pnpm dev:mock --host 127.0.0.1 --port 5173`을 실행한 뒤 다음 주소로 새 목 상태에 들어간다. 기존 목 상태가 남으면 새 브라우저 컨텍스트/프로젝트로 시작한다. 이번 작업에서는 개발 서버나 브라우저를 직접 실행하지 않았다.

- 기본 감성형: `http://127.0.0.1:5173/create/storyboard?mockTemplate=emo`
- 기본 쇼룸형: `http://127.0.0.1:5173/create/storyboard?mockTemplate=show`
- 분석 디테일 포함: 위 주소 뒤 `&mockDetail=full`

| 장면 | 만드는 순서와 기대 |
|---|---|
| 도구와 머리 | 1280·1440 폭에서 두 템플릿을 연다. 첫 장 외 손잡이·휴지통 항상 표시, 알약 hover/Tab 시 화살표, 같은 단추 초점으로 이동, 긴 안내 문구의 어절 줄바꿈을 확인한다. |
| 글자 스타일 | 견본 세 개가 모두 진하고 고른 것만 테두리인지, 사진 판을 열어도 견본이 흐려지지 않는지 확인한다. 기존 fontStyle 저장·재진입도 확인한다. |
| 첫 장 0컷 재사용 | 기본 ghost 제품 섹션 상태에서 첫 장 옅은 고스트칸을 넣는다. 아래 구성·크레딧이 그대로이고 X/undo가 원본 제품 사진을 지우지 않아야 한다. |
| 첫 장 별도 생성 | 제품 섹션을 flat으로 바꾼 후 첫 장 고스트를 넣는다. 구성 +1, natural_light 전환에도 동일 자리, ghost로 돌아오면 전용 컷이 사라지고 재사용으로 바뀐다. 켜진 채 첫 장 디자인을 바꿔도 고스트가 유지된다. |
| 장소 묶음 | 일상 착용컷에서 같은 장소 1컷 더를 누른다. 그 묶음 끝에 들어가고 소진된 묶음 칸은 사라져야 한다. undo, 12컷 도달, 비슷한 컷 때문에 옅은 칸만 다음 행에 있는 상태의 장소 바꾸기도 확인한다. 스튜디오에는 옅은 칸이 없다. |
| 원단 확대 | mockDetail=full로 진입 → 원단 설명 섹션을 뺀다 → 디테일 요소의 원단 확대를 넣는다. 실제 detail은 3→4컷, 칸의 부위는 추가 전후 같아야 한다. X/undo, 이미 같은 원단 있음, 4컷 상한도 확인한다. |
| 원단 제품컷 | mockDetail=full의 원단 설명을 연다. 제품컷을 넣어도 생성 수가 늘지 않는다. 색상과 제품 방식을 바꿔도 재사용하며, 원본 섹션을 빼면 해당 자리만 사라진다. |
| 사진 판 | 여러 색 상품에서 이름표→색 원+이름→구도·장소→예시 순서, 한 색일 때 색 줄 없음, 시그니처 왼쪽 위 이름표를 확인한다. 첫 장 고스트를 누르면 기준 색 앞면 안내만 있어야 한다. |
| 숨김 짝·단위 | 고스트/제품컷 켠 상태에서 디자인 판에 별도 짝 카드가 늘지 않는지, 기본 카드 체크와 전환이 맞는지, 아래 버튼·스택·팝업에서 컷 단위인지 확인한다. |

## 남은 위험

브라우저를 실행하지 않았으므로 실제 1440 한 줄, 도구 펼침의 픽셀 겹침, 모바일/태블릿 넘김 화면, 애니메이션과 Tab 초점의 최종 시각 판정은 총괄 확인이 필요하다. 정적 검출·SSR·순수 함수 검사는 실제 화면 확인을 대신하지 않는다. 실제 유료 생성과 배포/CDN 확인은 수행하지 않았다. WOC6 디자인 목록·꾸밈과 WOC7 페이지 색감은 범위 밖으로 남겼다.

## 바뀐 파일

```text
documents/WOC5_screen_fixes_20261111_report.md
docs/superpowers/plans/2026-11-11-woc5.md
scripts/regenerate-page-template-fixtures.mjs
server/app/agents/page_template_assembler.py
server/app/agents/page_templates.py
server/app/data/page_templates_v1.json
server/tests/test_page_templates_catalog.py
server/tests/test_woc5_catalog.py
server/tests/test_woc5_generation.py
src/features/storyboard/HorizonBackgroundControl.jsx
src/features/storyboard/ProductPresentationPicker.jsx
src/features/storyboard/Storyboard.jsx
src/features/storyboard/StoryboardChoices.module.css
src/features/storyboard/StoryboardContinue.jsx
src/features/storyboard/StoryboardExampleGallery.jsx
src/features/storyboard/StoryboardGarmentColorField.jsx
src/features/storyboard/StoryboardLayout.module.css
src/features/storyboard/StoryboardPageList.jsx
src/features/storyboard/StoryboardPaleSlot.jsx
src/features/storyboard/StoryboardPaleSlot.module.css
src/features/storyboard/StoryboardPanels.module.css
src/features/storyboard/StoryboardPhotoGallery.jsx
src/features/storyboard/StoryboardPhotoPanel.jsx
src/features/storyboard/StoryboardStackPopup.jsx
src/features/storyboard/StoryboardStage.jsx
src/features/storyboard/StoryboardThemePicker.jsx
src/lib/pageTemplates/README.md
src/lib/pageTemplates/assemble.js
src/lib/pageTemplates/catalog.js
src/lib/pageTemplates/designChoices.js
src/lib/pageTemplates/pairedDesigns.js
src/lib/pageTemplates/sectionActions.js
src/lib/pageTemplates/sectionDependencies.js
src/lib/pageTemplates/stageAdditions.js
src/lib/pageTemplates/storyboardStage.js
src/styles/tokens.css
tests/fixtures/page_templates_cases.json
tests/frontend/helpers/storyboardComponents.mjs
tests/frontend/helpers/storyboardPhotoPanelHarness.mjs
tests/frontend/horizon-background-control.test.mjs
tests/frontend/page-template-design-choices.test.mjs
tests/frontend/page-template-screen.test.mjs
tests/frontend/page-template-ui.test.mjs
tests/frontend/page-template-v5-actions.test.mjs
tests/frontend/page-template-v5-parity.test.mjs
tests/frontend/page-templates-catalog.test.mjs
tests/frontend/product-source-photos.test.mjs
tests/frontend/storyboard-qa-b1.test.mjs
tests/frontend/storyboard-v5-round2.test.mjs
tests/frontend/storyboard-woc3-photo.test.mjs
tests/frontend/woc-port-integration.test.mjs
tests/frontend/woc3-first-opening.test.mjs
tests/frontend/woc4-ui.test.mjs
tests/frontend/woc5-additions.test.mjs
tests/frontend/woc5-catalog.test.mjs
tests/frontend/woc5-stage.test.mjs
tests/frontend/woc5-ui.test.mjs
```

보호한 `exampleToneOrder.json`, `example_tags.json`, 예시·세트 데이터 4개, `horizon_background_policy.json`, `DESIGN.md`는 시작 해시와 같다. 작업 로그는 `.scratch/woc5-20261111/`에 있다.
