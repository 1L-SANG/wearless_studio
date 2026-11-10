실제 모델: gpt-6-astra · effort: xhigh. 이번 실행의 turn_context에서 확인했다.

# WOC7 페이지 색감

기준 커밋은 `1f64fabadea84f373ec6206ed117c3b0f0ace193`이다. WOC5와 예시 장소 분류를 보존하며 새 작업 공간에서만 파일을 수정했다. git 쓰기, 브라우저 실행, 유료 API 호출은 하지 않았다. DB·blocks 저장 형태·pageStyle 필드는 추가하지 않았다.

## 항목별 구현

| 항목 | 내용과 주요 파일 |
|---|---|
| 1. 두 줄 머리 | 새 `StoryboardStyleControls.jsx`, `StoryboardTone.module.css`와 `StoryboardThemePicker.jsx`가 이름표/컨트롤 두 열을 공유한다. 첫 줄은 글자 스타일과 견본 셋, 둘째 줄은 페이지 색감과 두 칸이다. 이름표를 같은 왼쪽 열에서 행 가운데에 맞췄다. 제목과 안내 문구는 기존 머리 grid의 제목 자리와 전체 폭 안내 행을 유지한다. |
| 2. 색감 두 칸 | 기본은 기존 흰 바탕·잉크, 의류 색감에 맞추기는 실제 `pointPalette.accentBg/accentInk`다. 둥근 38px 높이 직사각형 둘이 견본 줄 폭을 반씩 쓴다. 선택 체크와 2px 잉크 테두리, 비선택 흐림 없음, 기존 초점/막힘 동작을 적용했다. 색·글꼴 값은 SVG의 paint/font 속성으로 읽으며 인라인 CSS나 중복 팔레트 표를 만들지 않았다. |
| 2. 무채색·저장 | `tokens.js`의 공통 neutral 판정으로 white/gray/black/null·미상·색 없음에서는 줄 전체를 숨기고 유효 mode를 mono로 본다. 저장된 point를 단순 진입/미리보기로 덮어쓰지 않는다. `Storyboard.jsx`는 기존 글자 저장과 같은 `createStoryboardStyleSaveQueue`를 쓰며 최신 스타일 ref로 빠른 글자/색감 교차 선택을 합친다. 기존 재진입·다음 단계의 queue 대기를 유지했다. |
| 3. 미리 입히기 | 새 `useStoryboardTone.js`는 pointer/focus 미리보기만 화면 상태에 둔다. 올리기/초점은 point를 보여 주고 떠나기/blur는 최신 선택으로 돌아가며 저장하지 않는다. `StoryboardStage.jsx`의 머리 띠, `StoryboardPageList.jsx`의 실제 조립 썸네일, 열린 `StoryboardSectionPicker.jsx`의 와이어프레임 색 자리로 전달한다. 사진은 와이어프레임에서 계속 회색이고 글 견본 낱말은 없다. |
| 3. 차례로 물들기 | 선택 직후 무대 머리를 바꾸고 왼쪽 각 행은 100~500ms 사이에 순서대로 새 조립 색을 받는다. fill 전환 280ms로 마지막 행까지 780ms 안의 설정이다. 상태는 800ms에 정리한다. 움직임 줄이기는 지연·전환 없이 즉시 적용한다. 프로젝트·템플릿·대표 색 변경과 unmount는 타이머를 취소한다. mono/point 조립 결과를 같은 보드·카탈로그 상태에서 기억해 두므로 hover나 wave 단계마다 다시 조립하지 않는다. |
| 4. 분석 카드 삭제 | `PageTemplatePicker.jsx`의 PageColorModePicker, 안내 문구, 관련 CSS와 `AnalysisForm.jsx`의 색 선택 callback/props/import를 제거했다. 분석에는 템플릿 두 개만 남는다. 이전에 저장한 colorMode는 복원·템플릿 변경 때 유지되며 관련 시험도 그 계약으로 갱신했다. |
| 5. 쇼룸형 옅은 판 | JS `tokens.js`와 Python `page_templates.py`에서 show의 accentBg 예외를 없앴다. 기존 실제 디자인이 사용하는 panel도 같은 point 배경에 연결했다. ink/invertBg는 바꾸지 않고, 반전된 show 섹션은 기존 sectionTokens 규칙으로 panel까지 무채색을 복원한다. 실제 쇼룸형 고스트 짝 디자인의 조립된 판 색도 검사했다. |
| 6. 세 번째 견본 | 현재 템플릿의 labelEn/titleKo에서 글꼴·굵기·기울임·대소문자·자간을 읽는다. 감성형은 Cormorant 이탤릭 소문자와 “다정”, 쇼룸형은 Cormorant 대문자·0.12em과 “기본”이다. 기본 fontStyle 값과 저장 경로는 그대로다. |

의류 색감 칸의 글자 대비는 모든 팔레트에서 4.5:1 이상이다. 표시되는 유채색 중 최저는 green **5.26:1**, 최고는 ivory **6.73:1**이다. 나머지는 yellow/beige 5.41, blue 5.43, navy 5.44, brown/purple 5.54, red 5.56, pink 5.62다.

새 콘티 토큰은 `--sb-tone-height: 38px`와 `--sb-tone-transition: 280ms`다. 기존 둥글기·여백·서체·잉크 토큰을 재사용했다. 카탈로그 디자인 추가/숨김 변경, 옅은 판 배치나 WOC6 꾸밈 변경은 없다.

## 판단한 경계

- 기존 카탈로그의 옅은 판은 `fill: panel`이다. accentBg의 show 예외만 제거하면 실제 고정 사례 결과가 하나도 바뀌지 않아, point에서 panel과 accentBg를 같은 값으로 매핑했다. 디자인 파일을 바꾸지 않고 D277의 실제 화면 동작을 만족시키는 최소 연결이다. 진한 판은 기존 반전 토큰으로 보호한다.
- 대표 색 표시는 기존 `baseColorOf`를 따른다. isBase가 있으면 그 색, 없으면 첫 색이다. 색 목록이 없거나 swatchId가 미상인 경우는 mono다. 단순 색 이름 텍스트로 색 계열을 추측하지 않는다.
- 제목/안내 문구는 기존 머리의 셀과 문구를 유지한다. 새 색감 줄의 실제 높이·간격 및 1280/1440 픽셀 판정은 총괄 화면 검사 대상이다.
- 와이어프레임의 사진과 진한 도형을 회색으로 단순화하는 기존 규칙은 유지한다. 이번에는 의류 색감의 옅은 표면만 미리 입힌다. WOC6 디자인 미리보기 재설계는 하지 않았다.
- 포인터와 키보드 초점이 동시에 point 칸에 있으면 둘 다 떠났을 때 미리보기를 해제한다. 고르기는 미리보기 상태를 지우고 명시적으로 저장한다. 앞선 실패가 뒤의 성공 상태를 덮지 않으며, 최신 저장 실패는 기존 위치에 오류를 표시해 다시 고를 수 있다.

## 시험

| 명령 | 결과 |
|---|---|
| `pnpm test:frontend` | **5,430 통과, 실패 0, 건너뜀 0**, 51.99초. `frontend-second.log`. |
| `server/`에서 `/Users/daily/Documents/wearless_studio/server/.venv/bin/pytest -q --ignore=tests/test_personalization.py` | **11,960 통과, 285 건너뜀, 실패 0**, 경고 1,043건, 276.98초. `server.log`. |
| `PAGE_TEMPLATE_PYTHON=/Users/daily/Documents/wearless_studio/server/.venv/bin/python node scripts/regenerate-page-template-fixtures.mjs` | **148개 전체 결과 JS/Python 일치**, 재생성. 실제 point 판 색 변경으로 기대값 29곳이 바뀌었다. `fixtures.log`. |
| `pnpm build` | **성공**, exit 0, 7.82초. 정적/동적 import 혼용 및 큰 청크 경고는 남아 있다. `build.log`. |
| `~/.claude/skills/impeccable/scripts/impeccable detect --json <변경 JSX/CSS>` | **14개 파일**, exit 0, 지적 `[]`. `impeccable.json`. |
| `git diff --check` | 통과. |

WOC7 집중 시험은 헤더/표시 조건/대비/실제 폰트 속성, pointer·focus 되돌리기, 타이머 취소·움직임 줄이기, 실제 선택 handler의 빠른 네 번 저장과 재진입 대기, 실제 Stage/목록/와이어프레임 색 전달, 두 엔진의 모든 palette 조합을 검사한다. 처음 실패시킨 show 판/neutral/분석 카드 검사와 통합 과정에서 발견한 옛 테스트 기대값을 고친 기록은 `.scratch/woc7-20261111/`에 있다. 독립 검토 기록은 `review.md`이며 추가 차단 문제는 없었다.

## 총괄 화면 확인 방법

새 작업 공간에서 `pnpm dev:mock --host 127.0.0.1 --port 5173`을 실행한다. 실제 브라우저/개발 서버는 이번 작업에서 직접 실행하지 않았다.

- 감성형: `http://127.0.0.1:5173/create/storyboard?mockTemplate=emo`
- 쇼룸형: `http://127.0.0.1:5173/create/storyboard?mockTemplate=show`
- 원단·디테일도 볼 때 위 주소에 `&mockDetail=full`을 더한다.

기본 목 상품의 대표 색은 블랙이므로 처음에는 페이지 색감 줄이 없는 것이 맞다. 유채색 확인은 같은 목 프로젝트의 `/create/input`으로 돌아가 첫 번째 대표 색상 사진 묶음 아래 색상 선택에서 블루(또는 베이지·그린)를 고른 뒤 기존 확인 흐름으로 콘티에 돌아온다. 이름만 바꾸거나 콘티 개별 컷의 의류 색을 고르는 것은 대표 swatch 변경이 아니다. 검정·회색·흰색으로 되돌려 숨김과 기본 렌더도 확인한다. 프로젝트 상태를 재사용하면 저장된 선택이 이어지므로 초기 상태 확인에는 새 목 프로젝트/브라우저 컨텍스트를 쓴다.

| 장면 | 기대 |
|---|---|
| 1280·1440 머리 | 두 이름표가 같은 왼쪽 열에서 각각 컨트롤 중앙에 맞고, 색 두 칸이 견본 셋과 같은 전체 폭이다. 제목·안내·컨트롤이 겹치지 않는다. |
| 블랙 대표 색 | 색감 줄이 없다. 과거 point 선택이 있어도 조립은 기본이다. 글자 스타일은 계속 고를 수 있다. |
| 블루 대표 색, 기본 선택 | point 칸 자체는 블루 옅은 바탕과 진한 글씨다. 선택하지 않은 칸도 흐리지 않는다. |
| 올리기·키보드 초점 | point 칸 hover/Tab focus 때 머리 띠·왼쪽 판·열린 디자인 후보의 색 자리가 미리 바뀐다. 떠나기/blur 후 원래대로 돌아간다. 새 저장은 없다. |
| 선택 효과 | 고르면 체크와 2px 테두리. 무대부터 왼쪽까지 짧게 차례로 바뀐다. 시스템 움직임 줄이기를 켜면 한 번에 바뀐다. |
| 빠른 교차 선택 | 매거진→의류 색감→볼드→기본을 빠르게 고르고 다른 단계/콘티 재진입을 한다. 마지막 볼드+기본이 유지된다. |
| 쇼룸형 판 | 첫 장의 고스트칸 또는 원단의 제품컷을 넣어 옅은 판을 만든다. 의류 색감에서는 그 판도 물들고 진한 반전 판은 무채색이다. |
| 세 번째 견본 | 감성형은 소문자 이탤릭 “다정”, 쇼룸형은 대문자와 넓은 자간의 “기본”. 고른 뒤 실제 페이지 글꼴과 일치한다. |
| 분석 단계 | 템플릿 둘만 보이고 색 카드·옛 안내 문구는 없다. 이미 선택한 colorMode는 콘티에서 이어진다. |

## 남은 위험

브라우저 금지에 따라 1280/1440 실측, SVG 글꼴의 실제 로딩과 수직 위치, 차례로 물드는 인상은 총괄 확인이 필요하다. 정적 검사와 SSR는 픽셀 화면 검사를 대신하지 않는다. 유료 생성과 운영 배포는 수행하지 않았다. 기존 정적/동적 import 혼용과 큰 청크 경고는 남아 있으며 이번 색감 범위에서 번들 구성을 바꾸지는 않았다.

## 바뀐 파일

```text
documents/WOC7_page_tone_20261111_report.md
docs/superpowers/plans/2026-11-11-woc7.md
server/app/agents/page_templates.py
server/tests/test_woc7_tone.py
src/features/analysis/AnalysisForm.jsx
src/features/analysis/PageTemplatePicker.jsx
src/features/analysis/PageTemplatePicker.module.css
src/features/storyboard/Storyboard.jsx
src/features/storyboard/StoryboardChoices.module.css
src/features/storyboard/StoryboardLayout.module.css
src/features/storyboard/StoryboardPageList.jsx
src/features/storyboard/StoryboardPreview.jsx
src/features/storyboard/StoryboardSectionPicker.jsx
src/features/storyboard/StoryboardStage.jsx
src/features/storyboard/StoryboardStyleControls.jsx
src/features/storyboard/StoryboardThemePicker.jsx
src/features/storyboard/StoryboardTone.module.css
src/features/storyboard/useStoryboardTone.js
src/lib/pageTemplates/README.md
src/lib/pageTemplates/tokens.js
src/styles/tokens.css
tests/fixtures/page_templates_cases.json
tests/frontend/helpers/storyboardComponents.mjs
tests/frontend/page-template-screen.test.mjs
tests/frontend/woc2-catalog-color.test.mjs
tests/frontend/woc3-round3-preview-dimensions.test.mjs
tests/frontend/woc7-controls.test.mjs
tests/frontend/woc7-preview.test.mjs
tests/frontend/woc7-tone.test.mjs
```

`exampleToneOrder.json`, `example_tags.json`, 예시·세트 데이터 4개, `page_templates_v1.json`, `DESIGN.md`는 시작 시점의 SHA-256과 같다. 이전 WOC5 작업과 총괄의 장소 분류를 보존했다.
