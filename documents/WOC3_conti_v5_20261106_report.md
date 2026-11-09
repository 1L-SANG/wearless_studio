실제 모델: gpt-6.1-sol · effort: max. 이번 턴의 turn_context에서 확인했다.

# WOC3 콘티 v5 구현 보고 (2026-11-06)

장부 D170·D171·D199·D209~D220을 기준으로 사진 판, 원단 초기 구성, 디테일 한도, 후기·문답, 쇼룸형 첫 장을 반영했다. 기존 미커밋 변경을 보존했다. 총괄의 `.sectionTitle` gap `var(--sp-3)`, 컷 수 margin-left `0`, R2-2 실측 계산 본문은 시작 복사본과 같다. 저장 blocks/DB에 필드를 추가하지 않았고 브라우저·git 쓰기·메인 코드 수정·유료 호출은 하지 않았다.

## 항목별 구현

| 항목 | 한 일 |
|---|---|
| A1 세트 칸 판 | 일반 세트 인스턴스는 같은 장소 사진과 접힌 글자 단추부터 보인다. 접힌 동안 단일/세트, 장소 탭, 구도, 예시 갤러리의 DOM 자체가 없다. 펼치면 “다른 장소 사진”과 “접기”가 보인다. D204 한 칸 인스턴스와 원래 세트 밖 사진은 펼친 상태로 시작한다. 아래 묶음 교체 목록과 저장 콜백은 유지했다. |
| A2 단일컷 스택 | 현재 필터를 통과한 멤버를 세트별 한 칸으로 묶는다. 선택 사진을 대표로 쓰고, 없으면 첫 멤버를 쓴다. 스택 캡션 없이 n장·체크·이미 사용을 표시한다. 작은 창은 사진 96×128, 최대 4열, 간격 8, 안쪽 12, 둥글기 12와 사진 둥글기 8, 안쪽 최대 높이 336이다. 위/아래 뒤집기와 가로 범위 제한, 바깥 누름 소모, 다른 스택으로 바로 이동, Esc/Tab 닫기, 화살표/Home/End 로빙 초점, 초점 가두기, 비동기 선택 뒤 초점 복귀와 끌기 시작 뒤 닫기를 연결했다. 세트컷의 별도 3×2 펼치기는 유지한다. |
| A3 무작위 48칸 | 적합성 필터 뒤 스택/낱장으로 묶고 최대 48칸을 뽑아 3×2 최대 8쪽으로 보여 준다. 고른 적합 사진/스택은 맨 앞이며, 쪽 이동에는 순서가 고정된다. 판을 다시 열거나 장소·구도를 바꾸면 새 UI 씨앗으로 섞는다. 시험에는 씨앗 함수를 주입한다. 씨앗과 순서를 저장하지 않는다. |
| A4 자동 고르기 | 갤러리 샘플러를 처음 배치·섹션 넣기에 연결하지 않았다. 자동 채우기의 안 쓴 예시 우선과 D204 저장 경로도 유지한다. D216을 적용한 후기·문답의 옛 bottomClear 힌트 제거는 아래 D7 변경에 속한다. |
| B5 원단 | 후보나 유효한 셀러 원본 closeup이 없으면 새 초기 원단 run 자체를 생략한다. 새 글 대체 run도 만들지 않는다. 추가 목록은 같은 시드 판정으로 제외된다. T03-TEXT-01 정의와 이미 저장된 0컷 원단의 숨김·저장·조립 호환성은 유지한다. 원본 ID를 꾸미거나 저장된 run을 이주시키지 않았다. |
| C6 디테일 한도 | 정본 `photoBudgets.detail`을 4로 바꾸고 `DETAIL_PHOTO_CAP`은 계속 그 한 값을 읽는다. 구조 부위는 별도로 최대 3, 원단은 최대 1이다. 구조 3개+원단 후보 또는 셀러 원본을 넣으면 4장이 남는다. 구조 2개 미만의 초기 디테일 요소 생략, 수동 세 컷 crop, 일상 12컷 추가 제한, 저장된 수동 구성 검증은 유지한다. |
| D7 기본 후기·문답 | I09-D06/T08-D07을 1000×1250(4:5) 사진, 검정 30% 전체 막, 흰 제목과 작아진 흰 96% 말풍선으로 고쳤다. 좌우 70, 아래 87.5가 각각 사진의 7%다. 말풍선은 안쪽 860의 85%인 최대 731, 본문 22/작성자 17, 안쪽 16×20, 모서리 20, 그림자 0·3·12/검정18%, 내부 사이 12다. 문답은 질문·답을 한 작은 흰 말풍선으로 묶어 좌우 번갈아 둔다. D216에 따라 해당 레시피의 bottomClear 선호와 빈 아래 공간 안내를 걷었다. |
| D8 후보 둘 | 후기 I09-SCRIM-01, 문답 T08-SCRIM-01을 더했다. 같은 한 칸 사진의 아래 58%(y525/h725)에 투명→검정72% 띠를 얹고 제목·본문·작성자/문답을 흰색으로 그린다. 공통 목록의 기존 종류·장수 검사로 후보에 함께 나온다. 와이어프레임은 같은 조립 도형에서 회색 그라데이션을 그린다. |
| D9 두 엔진 | 카탈로그 기반 scrim·고정 textColor·fillOpacity·shadow·itemGap을 JS/Python에 같이 구현했다. 디자인 ID별 조립 분기는 넣지 않았다. 4디자인×2템플릿×2색 모드×3폰트의 48조합과 128개 고정 사례에서 정확히 같은 결과를 확인했다. |
| E10 첫 장 | 쇼룸형 기본을 P01-SHOW-01 “큰 사진과 작은 상품명”으로 바꿨다. 가로 1000의 기존 시그니처 3:4 사진 아래 20 간격, 좌우 70의 28px 상품명 한 줄, 아래 28이다. 긴 이름은 공통 singleLine 규칙으로 실제 글 폭과 자간을 재어 줄인다. 시그니처 풀/성별/옷/하의/원피스/정면 미디움 규칙은 유지한다. P01-D01은 삭제하지 않았으며 재사용 원본이 있을 때 생성 수가 같은 후보로 허용한다. 디자인 왕복·되돌림, 원본 없음, product 인자를 생략한 순수 함수 호출도 시험했다. |

## 새 디자인

| ID | 화면 이름 | 종류 | 생성 사진 | 기본 여부 |
|---|---|---|---|---|
| P01-SHOW-01 | 큰 사진과 작은 상품명 | 첫 장 | 시그니처 1 | 쇼룸형 기본 |
| I09-SCRIM-01 | 아래 어두운 띠 후기 | 후기 | 배경 1 | 공통 후보 |
| T08-SCRIM-01 | 아래 어두운 띠 문답 | 궁금한 점 | 배경 1 | 공통 후보 |

I09-D06·T08-D07은 기존 ID/이름으로 기본을 유지하며 모양만 갱신했다. 총 카탈로그는 25개에서 28개다. 저장된 첫 장 `pageDesignId`를 새 기본으로 덮어쓰지는 않는다.

## 판단과 기준 적용

- **첫 장의 고스트칸:** 실제 재사용 원본이 있으면 생성 0으로 세는 예외를 첫 장에만 적용했다. 다른 종류는 기존 실제 사진 자리 수 비교를 그대로 쓴다. 상품 사진을 뺀 상태에서 1칸 첫 장을 고스트 디자인으로 바꿔 새 컷이 생기는 후보는 막는다. 재사용이 깨진 기존 고스트 디자인의 독립 컷 복구는 이전 계약을 유지한다. product 인자 생략 시에도 후보 계산과 의존성 조정이 같은 기준 색을 읽게 했다.
- **원단 원본:** 현 사진 레코드는 Front/Back/Detail/BackDetail만 있으며 원단 전용 표지가 없다. 기존 Detail/BackDetail closeup은 원단에 쓸 수 있는 셀러 원본으로 취급했다. 기준 색을 먼저 쓰고 다른 색 원본은 기준 색의 같은 방향 전체 사진이 있어야 한다. 빈 레코드와 전체 사진만 있는 상태는 제외한다. 이미 다른 부위를 담은 미분류 closeup을 원단으로 쓸 가능성은 이 기존 계약의 한계이며, 원단 판정 UI/데이터를 이번 범위에서 새로 만들지는 않았다.
- **필터 뒤 멤버 한 장:** D171 참고 구현의 설명대로 한 장만 통과한 세트는 낱장으로 남긴다. 실제 저장 인스턴스가 한 칸인 D204의 펼침 판정과는 별개다.
- **작은 창 배치:** 인라인 스타일 없이 CSS anchor/native popover를 사용하고, 지원하지 않을 때는 가로 넘김 strip 밖의 상대 위치 sibling과 고정 행/열 CSS로 돌아간다. 측정값은 방향·열 data 속성과 스크롤에만 쓴다. 실제 Chromium/WebKit의 픽셀 배치·OS 끌기는 총괄 확인 장면으로 남긴다.
- **시안 확인:** 제공된 사진 판 PNG와 명세를 읽었다. 후기·문답 scrim.html은 그림 파일이 따로 없어 소스로 의도만 확인했으며 코드를 옮기지 않았다. 브라우저로 기준 HTML을 렌더하거나 구현 화면을 보지는 않았다. 731px 말풍선 상한은 7% 안쪽 영역의 85%로 잡아 캔버스의 85% 상한보다 작게 만들었다.
- **공용 코드 범위:** 새 UI 색/움직임 토큰은 추가하지 않았다. 작은 창은 기존 bg/fg/ring/focus/radius/sb-pop-shadow/sb-t-base를 읽는다. 검정 scrim과 그림자의 정확한 검정은 같은 페이지 카탈로그에 대응 토큰이 없어 데이터 paint 값으로 뒀고 흰색은 canvas 토큰을 쓴다. 새 UI에 인라인 스타일은 넣지 않았다. 에디터 캔버스는 저장 좌표·채움을 원래 동적 style로 그리므로 그 기존 shape paint에 데이터 boxShadow 합성만 보탰다. 내보내기는 기존 DOM 복제 경로를 유지하며 새 글·사진·선 스타일 체계를 만들지 않았다.

## D7 글색 측정

고정 흰 제목/띠 글과 자체 흰 말풍선이 있는 네 디자인은 `textColor: canvas`로 색이 정해진다. 이 그룹은 bare-photo 대비 영역 수집 대상에서 빠져 `page_template_contrast.py`에 불필요한 샘플링을 요청하지 않는다. 저장된 `overlayTextColors`나 줄별 측정이 있어도 고정 흰색을 덮지 않으며, 말풍선 본문은 자체 진한 잉크를 쓴다. 다른 첫 장·원단·SNS 그룹의 자동 측정은 그대로다.

검정30% 막을 흰 사진 위에 놓으면 흰 글자 대비는 **2.1085:1**이다. 3:1이나 작은 본문 4.5:1을 보장하는 값이 아니다. 아래 띠가 검정72%에 이른 끝에서는 같은 흰 사진 기준 **9.2329:1**이지만 띠 위쪽은 이 값보다 낮다. 오너의 고정 흰색/막 비율을 유지했으며 모든 사진의 가독성을 자동 보장한다고 보고하지 않는다. 제목 주변의 실제 옷·배경과 흰 글자의 읽힘은 화면 확인이 필요하다.

## 시험

재현을 먼저 실행했다. 사진 판 최초 9개 실패, 후속 초점/재열기/끌기 3개 실패, 사진 둥글기 6≠8 실패를 확인한 뒤 고쳤다. 원단/한도 최초 8개 실패, 후기·문답 JS16실패/2기존통과·Python13실패, 첫 장 5개 실패도 각각 고친 뒤 재실행했다.

| 시험 명령/범위 | 실제 결과 |
|---|---|
| 사진 판 집중 11파일 | **89 통과/0 실패**, 새 사진 판 시험 13개 포함. 독립 검토의 꼭지 clipping 보완 뒤 해당 파일은 **14/14 통과**. photo-green.log, pointer-red.log, pointer-green.log |
| 원단·한도 집중 JS | **115 통과/0 실패**. seed-green-focused.log |
| 원단·한도 집중 Python | **56 통과/0 실패**, 실제 JS 시드를 Python 검증/조립과 비교한 새 26사례 포함. seed-python-green.log |
| 후기·문답 집중 | JS **18/18**, Python **13/13**. 회귀 JS42/Python127, 품질 JS37/Python87도 통과. 서로 겹치는 집중 실행을 합계로 세지 않았다. |
| 첫 장·후보·기존 조립 통합 5파일 | **222 통과/0 실패**. root-integration-final.log |
| product 생략 재현과 프론트 실패 보완 집중 4파일 | **33 통과/0 실패**. frontend-fixes-focused.log |
| 서버 대비 측정·기존 조립 기대 보완 2파일 | **50 통과/0 실패**, 0.44초. server-fixes-focused.log |
| `PAGE_TEMPLATE_PYTHON=/Users/daily/Documents/wearless_studio/server/.venv/bin/python node scripts/regenerate-page-template-fixtures.mjs` | **128사례**, 모든 조립 결과와 대비 영역이 정확히 같은 뒤 재생성. fixtures-final.log |
| `pnpm test:frontend` | **5,097 통과, 0 실패, 0 건너뜀**, exit 0. 51.95초. 꼭지 CSS 보완 뒤 최종 전체 실행. frontend-complete.log |
| `server/`에서 `/Users/daily/Documents/wearless_studio/server/.venv/bin/pytest -q --ignore=tests/test_personalization.py` | **10,840 통과, 183 건너뜀, 0 실패**, 경고 1,043개, exit 0. 253.00초. server-final.log |
| `pnpm build` | **성공**, exit 0. 8.94초. 큰 청크·의존성 주석 경고 외 오류 없음. build.log |
| `git diff --check`·금지 데이터 비교 | **통과**, 공백 오류 없음. 별도 새/기존 소스·시험 30개도 공백 검사 통과. 금지 데이터·DESIGN 5개, 공용 토큰·총괄 CSS·R2-2 파일 3개가 시작 복사본과 같다. |

첫 전체 프론트는 **5,092 통과/3 실패**였다. `emo/show renders section instances in page order` 두 건은 원단 없는 새 구성에 이전 run 수를 기대했고, R2-6은 25개 디자인과 첫 장 고스트 후보 거부를 기대했다. 새 계약으로 갱신했으며 최종 전체 실행은 위 숫자다. 통합 중에는 후기·문답의 옛 가변 잉크 기대도 실제 실패를 확인한 뒤 고정 흰 제목/진한 말풍선 본문으로 갱신했다. 추가 재현에서는 product 생략 경로의 기준 색 전달 차이로 생기던 불필요한 고스트 카드도 RED→GREEN으로 고쳤다.

첫 fixture 생성은 합성한 옛 글 원단 항목에 AI 전용 필드가 섞여 저장 검증에서 거부됐다. fixture 구성만 올바른 글 카드로 고쳤고 검증기를 느슨하게 만들지 않았다. 로그와 실패 기록은 `.scratch/woc3-20261106/`에 남겼다.

첫 서버 전체의 세 실패는 `test_review_and_faq_measure_only_bare_headers_not_opaque_bubbles[review/faq]` 두 건과 `test_measurement_regions_follow_the_copy_and_exclude_all_opaque_groups`다. 고정 흰색 제목을 아직 측정 대상으로 기대했다. 기본/후보 네 디자인 모두 메타데이터가 비어야 하는 시험으로 갱신했고, 가변 첫 장만 남은 수집 영역도 확인했다. 다른 첫 장·원단·SNS의 기존 측정 시험은 계속 실행한다. 고정 사례 재생성은 최종 순수 함수 보완 뒤에도 같은 128개와 같은 SHA256 `b2da1cd1b2a858fabaaa2a6667485fbaa8566fb9f8a8202a8e1b31c30143cb5c`를 냈다.

독립 읽기 전용 검토는 Critical/Important 지적 없이 끝났다. 첫 장 32회 전환·왕복, 실제 세트 카탈로그 선택 첫 칸 35사례, 고정·가변 색 그룹이 섞인 별도 양쪽 엔진 검사를 통과했다. Minor로 보고된 네이티브 popover 기본 overflow:auto의 꼭지 잘림은 명세의 꼭지 표시를 위해 이번에 고쳤다. CSS 재현이 auto≠visible로 실패한 뒤 바깥 창에 overflow:visible을 지정했고 사진 그리드의 안쪽 스크롤은 유지했다. 남긴 코드 지적은 없다. 검토에서 제외한 실제 브라우저·OS 끌기·래스터 픽셀은 아래 총괄 장면으로, 흰 글자 대비와 미분류 원본 판정은 위 판단으로 명시했다.

## 바꾼 파일

구현과 데이터:

```text
src/features/storyboard/StoryboardExampleGallery.jsx
src/features/storyboard/StoryboardStackPopup.jsx (새 파일)
src/features/storyboard/StoryboardPanels.module.css
src/features/storyboard/StoryboardPreview.jsx
src/features/editor/Editor.jsx (shape paint만)
src/lib/pageTemplates/storyboardGallery.js
src/lib/pageTemplates/seed.js
src/lib/pageTemplates/detailBudget.js
src/lib/pageTemplates/designChoices.js
src/lib/pageTemplates/sectionActions.js
src/lib/pageTemplates/sectionDependencies.js
src/lib/pageTemplates/catalog.js
src/lib/pageTemplates/assemble.js
server/app/agents/page_templates.py
server/app/agents/page_template_assembler.py
server/app/data/page_templates_v1.json
scripts/regenerate-page-template-fixtures.mjs
tests/fixtures/page_templates_cases.json
src/lib/pageTemplates/README.md
docs/superpowers/plans/2026-11-06-woc3-conti-v5.md (새 파일)
documents/WOC3_conti_v5_20261106_report.md (이 보고서)
```

시험:

```text
tests/frontend/storyboard-woc3-photo.test.mjs (새 파일)
tests/frontend/woc3-seed-budget.test.mjs (새 파일)
tests/frontend/woc3-overlays.test.mjs (새 파일)
tests/frontend/woc3-first-opening.test.mjs (새 파일)
tests/frontend/example-gallery-paging.test.mjs
tests/frontend/helpers/storyboardComponents.mjs
tests/frontend/page-template-seed.test.mjs
tests/frontend/woc2-points-dependencies.test.mjs
tests/frontend/woc2-round3-presentation.test.mjs
tests/frontend/detail-recommendations.test.mjs
tests/frontend/page-templates-catalog.test.mjs
tests/frontend/page-template-shape-render.test.mjs
tests/frontend/page-template-design-choices.test.mjs
tests/frontend/page-template-v5-actions.test.mjs
tests/frontend/page-template-assemble.test.mjs
tests/frontend/storyboard-render-groups.test.mjs
tests/frontend/storyboard-v5-round2.test.mjs
server/tests/test_woc3_seed_budget.py (새 파일)
server/tests/test_woc3_overlays.py (새 파일)
server/tests/test_page_templates_catalog.py
server/tests/test_page_template_assembler.py
server/tests/test_page_template_title_band.py
```

`src/data/genExamples.json`, `server/app/data/example_assets.json`, 두 세트 데이터, DESIGN.md는 시작 SHA256과 같다. `StoryboardLayout.module.css`와 총괄 R2-2 시험은 바꾸지 않았다.

## 총괄 화면 확인

작업 공간에서 `pnpm dev:mock --host 127.0.0.1 --port 5173`을 실행하고 새 비공개 창으로 진입한다. 구현 에이전트는 이 서버나 브라우저를 실행하지 않았다.

- 감성형: `http://127.0.0.1:5173/create/storyboard?mockTemplate=emo`
- 쇼룸형: `http://127.0.0.1:5173/create/storyboard?mockTemplate=show`

직접 진입 가드가 목 프로젝트를 만든다. 이어하기가 뜨면 계속하기를 누른다. 기존 창의 초안이 있으면 새 제작으로 시작한다.

| 장면 | 들어가는 순서와 확인점 |
|---|---|
| D170 접힘·펼침 | 일상 착용컷의 장소 세트 사진 누르기 → 같은 장소 사진/접힌 단추만 보임 → 펼쳐 단일/세트·장소·구도·예시가 나옴 → 접기. 아래 묶음 교체 목록은 계속 있어야 한다. |
| D204 낱칸 | 다른 세트의 한 장 고르기 → 새 인스턴스 저장 → 그 칸 다시 누르기. 전체 예시가 펼쳐져 시작하고, 저장·재진입·되돌리기 뒤 사진과 소속이 맞아야 한다. |
| 스택 작은 창 | 단일컷 → 스택 누르기 → 위 꼭지/3~4열, 판 위쪽에서는 아래 뒤집힘, 좌우 끝 범위 확인. 다른 스택·바깥·Esc·Tab·화살표/Home/End·키보드 선택·끌기 모두 확인. 옆 판 자체는 Esc 한 번에 함께 닫히면 안 된다. |
| 48칸·사용 표시 | 단일/세트 양쪽에서 최대 8쪽, 같은 조건 쪽 왕복 순서 고정, 장소/구도 변경과 닫았다 다시 열기에서는 새 순서. 선택한 적합 사진/스택은 첫 쪽, 이미 사용도 선택 가능. 선택 직후 키보드 초점 확인. |
| 원단 없음/있음 | 새 상품의 Front/Back만 두고 원본 Detail/BackDetail을 비운 분석 상태에서는 초기 원단과 추가 옵션 없음. 실제 closeup 또는 명확한 원단 후보가 있으면 보임. 기존 0컷 원단 초안은 숨긴 채 저장 유지. 고정 사례 woc3-emo/show-no-fabric·seller-fabric-front/back·saved-text-fabric도 제공한다. |
| 디테일 네 장 | 구조 부위 3개+원단 후보/원본인 분석 상태에서 디테일 요소 3장과 원단 1장. woc3-emo/show-four-detail fixture가 같은 데이터를 고정한다. 수동 디테일 요소는 기존 세 crop 동작. |
| 후기·궁금한 점 | 없는 경우 섹션 추가로 넣기 → 기본 막/흰 말풍선 → 섹션 디자인 바꾸기에서 아래 어두운 띠 후보와 회색 그라데이션 비교. 실제 생성 페이지·다운로드에서 제목/본문·그림자·옷 가림·흰 글 읽힘을 확인한다. |
| 쇼룸 첫 장 | 새 쇼룸 초안의 시그니처 1장+작은 상품명 → 디자인 바꾸기에서 고스트 디자인 → 되돌리기. 구성컷/크레딧 증가 없음. 색상과 제품을 뺀 새 1칸 첫 장에는 비용이 늘어나는 고스트 후보가 없어야 한다. 긴 상품명도 한 줄. |
| 보존 확인 | 크롬 1280/1440/1920와 웹킷 1440에서 제목 간격, 172/184 썸네일, 페이지 머리 두 항목, 떠 있는 버튼·옆 판, 가로 넘침·콘솔 오류·어절 줄바꿈 확인. |

새 섹션 디자인 이관/조사 기본 구성/배치 엔진/스튜디오 추가 방식/예시·세트 데이터 정리는 이번 변경에 포함하지 않았다.

## 2라운드

실제 모델: gpt-6.1-sol · effort: max. 이번 턴의 turn_context에서 확인했다. 장부 D220·D221을 읽고 R2-1~R2-4를 반영했다. 1라운드에서 잡았던 “미분류 Detail/BackDetail도 원단 원본” 판단은 D221로 대체한다. 기존 작업과 총괄의 제목 간격은 보존했으며 브라우저·git 쓰기·메인 수정·유료 호출은 하지 않았다.

| 항목 | 원인과 고친 곳 |
|---|---|
| R2-1 원단 기준 | `detailBudget.js`의 sellerFabricTarget과 업로드 대체 경로를 삭제했다. 이제 ready 분석에서 기존 적합성 검사(clear/standalone 등)를 통과한 `kind: fabric` 후보만 원단을 만든다. 시드와 추가 목록이 같은 `seedDetailTargets`를 쓰며 Detail/BackDetail 업로드만 있으면 둘 다 제외된다. 구조 3+원단 후보 1의 네 장, 기존 수동 디테일 경로, 저장된 0컷 원단의 숨김·검증·조립, T03-TEXT-01은 유지한다. Python에 별도 초기 시더는 없으므로 실제 JS 시드를 Python에서 검증·조립하는 비교 시험과 고정 사례를 갱신했다. |
| R2-2 띠 미리보기 | 기존 wire-3가 사진 자리와 너무 비슷했다. `.sb-v5-theme`에 `--sb-wire-scrim-end: #777773` 하나를 더하고, 공유 `StoryboardPreview`의 와이어프레임 그라데이션 끝에서만 쓴다. 아래 58%/투명도 0→.72는 유지하며 그 위 글 선은 기존 bg-1 흰색을 읽는다. 앞서 그린 그라데이션 위에 놓인 글에만 적용하고 비정상/빈 paint도 안전하게 건너뛴다. 기본 말풍선 카드의 회색·흰 면·기존 글 선, 실제 페이지의 검정72%/흰 글, JS/Python 조립 코드는 바꾸지 않았다. 같은 부품을 wireframe으로 쓰는 다른 미리보기도 같은 값을 받는다. |
| R2-3 목록 순서 | `sectionDesignChoices`에서 기존 적합성 필터를 마친 뒤 그 run의 `defaultSectionDesignId`를 맨 앞에 둔다. 나머지는 카탈로그 상대 순서를 보존한다. 기본 배지 함수·현재 선택 체크·클릭/되돌리기 동작은 그대로다. 쇼룸형 첫 장은 큰 사진과 작은 상품명이 첫 카드, 감성형 후기는 사진 위 후기 말풍선이 첫 카드다. 후보가 될 수 없는 디자인을 순서 때문에 새로 허용하지 않는다. |
| R2-4 목 후보 | 목 `buildDraft`가 `mockDetail=full`일 때만 새 목 전용 공개 후보 JSON을 복제해 사용한다. Front에서 목선·소맷단·밑단, Detail에서 골지 원단을 읽는 구조 3+원단 1이다. 실제 목 원본 바이트와 기존 서버 build_contract/public_summary로 만든 ID·해시·좌표·적합성 필드를 사용했다. 시험에서 목 초안의 실제 사진 바이트로 재정규화해 정확히 같은 후보가 나오는지 확인한다. 표시 없음/다른 값이면 기존 unavailable·빈 후보 그대로다. 운영 분석·HTTP 어댑터는 수정하지 않았다. |

와이어프레임 대비는 다음과 같다. 실제 합성 결과도 기준을 넘는지 시험한다.

| 비교 | 값/대비 | 기준 |
|---|---|---|
| 사진 자리 | `--sb-wire-photo` #d6d6d1 | 기존 값 |
| 띠 끝 토큰과 사진 자리 | #777773 / #d6d6d1, **3.083:1** | ≥1.8:1 |
| .72 합성 뒤 띠 끝과 사진 자리 | RGB(145.6,145.6,141.32), **2.153:1** | ≥1.8:1 |
| 흰 선과 띠 끝 토큰 | bg-1 흰색, **4.496:1** | ≥2:1 |
| 흰 선과 .72 합성 끝 | **3.140:1** | ≥2:1 |

지시와 다른 동작은 더하지 않았다. 기존 후보의 clear/standalone·ready 조건은 유지했으며, 새 목 후보는 원본 네 칸의 기존 순서 중 Front(0), Detail(2)를 참조한다. sourceSha256과 dt1 ID는 해당 실제 목 사진 바이트에 묶였다. 목 사진을 바꾼 임의 상품을 분석하는 기능으로 넓히지 않은 화면 검사 전용 상태다. 1라운드의 고정 흰 글자 대비 한계는 실제 디자인을 바꾸지 않았으므로 그대로다.

바꾼 파일:

```text
src/lib/pageTemplates/detailBudget.js
src/lib/pageTemplates/seed.js
src/lib/pageTemplates/designChoices.js
src/features/storyboard/StoryboardPreview.jsx
src/features/storyboard/StoryboardLayout.module.css (미리보기 글 선 규칙만)
src/styles/tokens.css (콘티 범위 띠 끝 토큰만)
src/mock/db.js
src/mock/detailRecommendationsFixture.json (새 목 전용 데이터)
scripts/regenerate-page-template-fixtures.mjs
tests/fixtures/page_templates_cases.json
tests/frontend/woc3-round2.test.mjs (새 파일)
tests/frontend/woc3-seed-budget.test.mjs
tests/frontend/woc2-round3-presentation.test.mjs
tests/frontend/woc3-overlays.test.mjs
tests/frontend/page-template-design-choices.test.mjs
server/tests/test_woc3_seed_budget.py
server/tests/test_woc3_round2_mock_details.py (새 파일)
src/lib/pageTemplates/README.md
documents/WOC3_conti_v5_20261106_report.md (이 절)
```

| 시험 명령/범위 | 실제 결과 |
|---|---|
| 원단 JS 시드·추가 목록 RED | **14 실패/41 통과**를 먼저 확인. fabric-red.log |
| 원단 Python 비교 RED | **8 실패/18 통과**를 먼저 확인. fabric-python-red.log |
| 새 미리보기·목·순서 RED | **9 실패/2 기존 통과**를 먼저 확인. 빈 paint 경계도 별도 실패를 확인했다. presentation-red.log, preview-guard-red.log |
| 목 서버 정규화 RED | **2 실패**, full 표시를 읽지 않는 기존 상태를 재현했다. mock-python-red.log |
| `node --test tests/frontend/woc3-round2.test.mjs tests/frontend/woc3-seed-budget.test.mjs tests/frontend/woc2-round3-presentation.test.mjs tests/frontend/woc3-overlays.test.mjs tests/frontend/page-template-design-choices.test.mjs tests/frontend/woc2-catalog-color.test.mjs` | **110 통과/0 실패**. focused-complete.log |
| `server/`에서 지정 Python의 `pytest -q tests/test_woc3_seed_budget.py tests/test_woc3_round2_mock_details.py` | **28 통과/0 실패**, 0.46초. python-green-first.log |
| `PAGE_TEMPLATE_PYTHON=/Users/daily/Documents/wearless_studio/server/.venv/bin/python node scripts/regenerate-page-template-fixtures.mjs` | **132사례**, 두 엔진의 조립 결과·측정 영역이 정확히 일치한 뒤 재생성. 기존 seller-fabric-front/back 이름의 사례는 이제 원단 run이 없다. 목 full/default 두 템플릿 네 사례를 더했다. fixtures.log |
| `pnpm test:frontend` | **5,113 통과, 0 실패, 0 건너뜀**, exit 0. 51.86초. frontend.log |
| `server/`에서 `/Users/daily/Documents/wearless_studio/server/.venv/bin/pytest -q --ignore=tests/test_personalization.py` | **10,846 통과, 183 건너뜀, 0 실패**, 경고 1,043개, exit 0. 245.51초. server.log |
| `pnpm build` | **성공**, exit 0. 8.34초. 큰 청크 등 기존 경고 외 오류 없음. build.log |
| `git diff --check`·보존 비교 | **통과**. 보호 대상 10개(운영 예시·세트·카탈로그·DESIGN·HTTP·두 엔진·총괄 시험)는 시작 SHA256과 같고, 제목 CSS 두 규칙도 같다. 새/변경 구현·시험 10개에 별도 공백 검사도 통과했다. |

집중 시험 중 미리보기 시험이 섹션 밖 자동 정보 블록까지 포함해 글 선 수를 잘못 세던 설정을 실제 디자인 카드처럼 해당 섹션만 그리도록 고쳤다. 실패 기록은 지우거나 통과로 세지 않았다. 로그는 `.scratch/woc3-round2-20261106/`에 있다. 이번 라운드에는 서브에이전트를 사용하지 않았다.

총괄은 `pnpm dev:mock --host 127.0.0.1 --port 5173` 후 새 비공개 창에서 다음 주소를 연다. 구현 에이전트는 이 목 웹서버나 브라우저를 켜지 않았다.

- 기본 감성형: `http://127.0.0.1:5173/create/storyboard?mockTemplate=emo`
- 기본 쇼룸형: `http://127.0.0.1:5173/create/storyboard?mockTemplate=show`
- full 감성형: `http://127.0.0.1:5173/create/storyboard?mockTemplate=emo&mockDetail=full`
- full 쇼룸형: `http://127.0.0.1:5173/create/storyboard?mockTemplate=show&mockDetail=full`

| 확인 장면 | 순서와 기대 |
|---|---|
| 기본 원단 제외 | 기본 두 주소에서 새 목 초안으로 진입. Detail/BackDetail 이미지가 목 상품에 남아 있어도 원단과 디테일 요소는 초기 목록에 없어야 한다. 추가 목록에서는 원단을 제외하고 디테일 요소의 기존 수동 crop 추가는 유지한다. 이전 창의 저장 초안이면 새 제작으로 시작한다. |
| full 네 장 | full 두 주소에서 새 초안. 디테일 요소 3컷과 원단 설명 1컷, 디테일 총4장. 원단을 뺀 뒤 추가 목록에는 원단 설명이 다시 나와야 한다. |
| 띠 카드 | 감성형 후기·궁금한 점에서 섹션 디자인 바꾸기. 아래 어두운 띠 후보의 사진 아래 58%가 진해지고 제목/본문/작성자·문답 선이 흰색이어야 한다. 기본 말풍선 카드와 실제 페이지의 모양은 이전 검사와 같아야 한다. 다른 wireframe 호출부도 같은 띠/선. |
| 기본 맨 앞 | 쇼룸 첫 장에서 큰 사진과 작은 상품명이 첫 카드와 기본 배지. 고스트 디자인을 고른 뒤 다시 열어도 기본은 첫 카드이고 체크는 고스트에 남는다. 감성형 후기에서도 기본 첫 카드·선택 체크를 확인한다. |
| 보존 | 1280/1440/1920의 제목 간격·172/184 썸네일, D170 접힘, 스택 작은 창·쪽 넘김, 저장·되돌리기·다음 단계, 오류/어절 끊김·가로 넘침을 확인한다. |

## 3라운드

실제 모델: gpt-6.1-sol · effort: max. 이번 실행의 turn_context에서 확인했다. WOC3 2라운드 이후 상태를 유지하며 R3-1·R3-2를 반영했다. 작업 공간 밖 수정, git 쓰기, 브라우저 실행, 유료 API 호출은 하지 않았다.

| 항목 | 원인과 반영 |
|---|---|
| R3-1 시그니처 분류 | Product에 없는 하위 분류를 Product에서만 읽어 여성 치마가 바지 풀로 떨어졌다. 공통 시그니처 경계가 Analysis의 `subCategory`·`customCategory`를 받도록 하고, 실제 진입의 두 자동 배치, 사진 판, 저장 오류 복구, 사진·섹션 추가, 다시 섞기와 자격 검사, 고정 사례 생성 호출까지 전달했다. 풀 데이터와 기존 성별·촬영 규칙은 유지한다. `skirt`, 치마·스커트 직접 분류는 치마 풀이고, 모르는 여성 하의는 바지 풀이다. 남성 하의, 상의·아우터, 여성 원피스의 결과도 유지했다. 유효한 셀러 치마 선택은 자동 배치 뒤에도 남는다. |
| R3-1 다시 만들기 | `seedProductKey`에 Analysis의 두 분류를 넣고, 분석 저장 시 콘티 prefetch를 비우는 `STORYBOARD_SEED_PATCH_KEYS`에도 추가했다. Product 저장 필드로 옮기지 않았다. 바지에서 치마로 바뀐 미편집 콘티는 다시 만들고 다음 진입은 안정된다. 편집한 콘티는 전체 재시드하지 않는다. |
| R3-2 후보 원본·영역 | 새 `detailTargetPreview`가 기준 색의 `selectPublicAnalysisPhotos(...)[sourceIndex]`와 `sourceSlot`을 함께 확인하고 유효한 후보 영역을 돌려준다. 기존 `detailTargetPresentation`의 본문과 `src`·`label`·`target` 반환은 그대로다. 편집기·실패 재시도·DetailTargetPicker의 원본 선택과 생성 입력도 바꾸지 않았다. |
| R3-2 화면 조립 | 새 `assembleStoryboardPreview`가 기존 JS 조립 결과의 이미지 요소에만 임시 crop과 `previewDetailCrop`을 붙인다. 무대는 기존 따온 사진의 잘린 SVG 뷰포트로 이를 그리고, 페이지 구성·전체 보기는 같은 조립 결과를 쓴다. 디테일 요소와 원단 설명에 모두 적용한다. 후보·원본·유효 영역을 찾지 못하면 기존 전체 사진으로 돌아가고, 실제 생성 결과 사진이 있으면 그 결과가 우선한다. 저장 blocks·DB와 원래 두 조립 엔진은 그대로다. |
| R3-2 확대 상한·실측 | 영역 중심의 cover 계산에 원본 폭 30% 상한을 적용하고, 실제 비트맵 크기가 있으면 1배를 넘지 않는다. 작은 원본을 칸 크기로 다시 키우지 않는다. 원본 크기 필드가 보통 없으므로 `useDetailPreviewDimensions`가 서로 다른 원본 URL의 intrinsic 크기를 화면에서만 읽는다. 이 임시 map으로 정사각형·가로 사진도 실제 비율을 쓰며, 소스 변경·언마운트 후 콜백은 해제한다. 로딩 전·실패 때는 기존 1024×1365 추정과 30% 규칙을 쓴다. SVG는 고정 비트맵 픽셀이 없으므로 실제 비율만 쓰고 1배 상한은 걸지 않는다. 목 SVG도 로딩 후 네 영역이 구별된다. |

이전 형식의 `seedProductKey`에는 두 분류 키가 없으므로, **기존 미편집 v4 콘티는 두 템플릿·분류 미상까지 한 번 다시 만들어질 수 있다.** 새 열쇠로 만든 다음 진입에서는 다시 만들지 않는다. 편집한 콘티는 저장된 목록과 편집 내용을 유지한다. 기존 진입 검증의 부적합 예시 보정은 계속 적용되며, 현재 치마 풀에 맞는 셀러 선택은 유지한다.

호환용 직접 호출에서 Analysis 인수를 생략하거나 통째로 null로 넘기면 기존 평면 분류 인수를 읽는다. 실제 Analysis 객체를 넘기면 그 객체의 두 필드가 기준이며, 필드가 null인 경우에도 낡은 Product 분류로 돌아가지 않는다. 운영 진입과 사진 판은 Analysis 문맥을 명시한다. 지시와 다른 화면 동작은 더하지 않았다. 원본 크기 읽기는 정확한 영역 표시와 픽셀 상한을 위한 화면 전용 보완이다. 원본이 읽히기 전의 추정 크기 사용은 보고한 제한이다.

바꾼 파일:

```text
src/lib/signatureCutPool.js
src/lib/generationExamples.js
src/lib/storyboardExampleShuffle.js
src/lib/pageTemplates/photoActions.js
src/lib/pageTemplates/seed.js (seedProductKey만)
src/lib/detailRecommendations.js (미리보기 helper 추가)
src/features/product-input/saveRouting.js
src/features/storyboard/Storyboard.jsx
src/features/storyboard/StoryboardPhotoPanel.jsx
src/features/storyboard/StoryboardStage.jsx
src/features/storyboard/StoryboardSectionPicker.jsx
src/features/storyboard/storyboardPreview.js
src/features/storyboard/useDetailPreviewDimensions.js (새 파일)
scripts/regenerate-page-template-fixtures.mjs (Analysis 전달만)
tests/frontend/woc3-round3-signature.test.mjs (새 파일)
tests/frontend/woc3-round3-detail-preview.test.mjs (새 파일)
tests/frontend/woc3-round3-preview-dimensions.test.mjs (새 파일)
tests/frontend/helpers/storyboardComponents.mjs
tests/frontend/storyboard-prefetch.test.mjs
src/lib/pageTemplates/README.md
docs/superpowers/plans/2026-11-06-woc3-round3.md
documents/WOC3_conti_v5_20261106_report.md (이 절)
```

| 시험 명령/범위 | 실제 결과 |
|---|---|
| 시그니처 재현 RED | 실제 진입·사진 판·선택 유지·재시드·prefetch 시험에서 **21 실패, 13 기존 통과**를 먼저 확인했다. 테스트 harness의 default export 처리 오류를 먼저 고친 후 기록한 행동 실패다. signature-red.log |
| `MOCK_API_LATENCY_SCALE=0 node --test tests/frontend/woc3-round3-signature.test.mjs` | **34 통과, 0 실패**. 기존 배치·seed·prefetch 등 11개 파일의 집중 회귀는 **267 통과**. signature-green.log, signature-focused.log |
| 디테일 미리보기 RED | 별도 resolver·조립 wrapper가 없는 상태에서 **11 실패**. 추정 크기를 native로 오인하는 경계도 따로 실패시켰다. detail-red.log, detail-estimate-red.log |
| 미리보기·기존 crop·추천·2라운드 집중 회귀 | **74 통과, 0 실패**. 처음에는 JSX attribute의 HTML escape를 테스트 parser가 복원하지 못한 두 건을 고쳤다. 구현의 이미지 좌표 차이는 아니었고 실패 기록은 남겼다. detail-focused-final.log |
| 원본 크기 RED와 후속 검토 | 실측 map·observer·hook을 넣기 전 **7 실패, 12 기존 통과**. 후속 검토의 추정→실측 승격 및 SVG 경계도 **3 실패, 19 기존 통과** 후 수정했다. dimensions-red.log, dimensions-review-red.log |
| `node --test tests/frontend/woc3-round3-detail-preview.test.mjs tests/frontend/woc3-round3-preview-dimensions.test.mjs` | 최종 경계 시험 **22 통과, 0 실패**. 두 목 템플릿의 네 영역, 실제 무대/목록/전체 보기 JSX, 생성 결과 우선, 누락 시 fallback, 따온 사진 보존, 실제/추정/SVG 크기, 콜백 해제를 포함한다. dimensions-review-green.log |
| `PAGE_TEMPLATE_PYTHON=/Users/daily/Documents/wearless_studio/server/.venv/bin/python node scripts/regenerate-page-template-fixtures.mjs` | **132사례**, JS/Python 조립 결과·측정 영역 일치 후 재생성. 결과 파일은 시작 SHA256 `51c75edc0980939492f268521112c39fc878e673f432e25016a04b702a360bcc`와 같아 바이트 변경이 없다. fixtures.log |
| `pnpm test:frontend` | **5,169 통과, 0 실패, 0 건너뜀**, exit 0. **48.273초**. frontend-verified.log |
| `server/`에서 `/Users/daily/Documents/wearless_studio/server/.venv/bin/pytest -q --ignore=tests/test_personalization.py` | **10,846 통과, 183 건너뜀, 0 실패**, 경고 1,043개, exit 0. **233.04초**. 이번 작업 공간의 실제 실행 숫자이며 서버 코드는 이 라운드에서 바꾸지 않았다. server.log |
| `pnpm build` | **성공**, exit 0. **8.05초**. 큰 청크·혼합 import 등 기존 경고 외 오류 없음. build-verified.log |
| `/Users/daily/.claude/skills/impeccable/scripts/impeccable detect --json` + 바꾼 JSX 4개 | **exit 0, 결과 []**. Storyboard·PhotoPanel·Stage·SectionPicker만 정적 파일 검사했다. CSS는 바꾸지 않았다. document/init/URL 검사나 브라우저는 사용하지 않았다. impeccable-detect-final.json |
| 보존 비교 | 두 원래 조립 엔진, 고정 사례, 운영 예시·세트·카탈로그, DESIGN, 토큰, 기존 Preview, HTTP·목 어댑터, 총괄의 제목 CSS와 R2-2 시험 등 **16개가 시작 SHA256과 같다**. preservation.json |
| `git diff --check`·별도 공백 검사 | **통과**. 새 파일을 포함한 변경 파일 22개의 끝 공백·끝 개행도 검사했다. |

로그는 `.scratch/woc3-round3-20261106/`에 있다. 독립 검토에서 원본 실측값이 이전 `dimensionsKnown:false`를 덮지 못하는 경계 한 건을 재현해 고쳤다. 다시 검토해 남은 지적 없음과 집중 22개 통과를 확인했다. 최종 생성 조립과 따온 사진 식·영역은 변경하지 않았다.

총괄 화면 확인은 `pnpm dev:mock --host 127.0.0.1 --port 5173` 후 새 비공개 창에서 한다. 구현 에이전트는 이 웹서버나 브라우저를 켜지 않았다.

- 감성형 전체 디테일: `http://127.0.0.1:5173/create/storyboard?mockTemplate=emo&mockDetail=full`
- 쇼룸형 전체 디테일: `http://127.0.0.1:5173/create/storyboard?mockTemplate=show&mockDetail=full`
- 기본 쇼룸형: `http://127.0.0.1:5173/create/storyboard?mockTemplate=show`

| 확인 장면 | 순서와 기대 |
|---|---|
| 디테일 네 칸 | full 두 주소에서 새 초안 → 디테일 요소와 원단 설명. 구조 세 칸은 각기 목선·소맷단·밑단 영역, 원단은 Detail 원본의 원단 영역을 보여야 한다. 무대·왼쪽 썸네일·전체 보기의 영역이 같고, 원본이 로딩된 뒤에도 전체 앞면으로 돌아가지 않아야 한다. 목 자료는 기존 SVG이므로 실제 상품 부위의 사진 품질 평가는 별도 실제 사진 상태에서도 한다. |
| 실제 원본 비율 | 원본이 정사각형·가로인 후보 바인딩 상품과 작은 비트맵 상품에서 영역 중심·30%/1배 상한·남는 bg-2를 확인한다. 후보나 원본이 없으면 기존 전체 사진, 생성 결과가 있으면 결과 사진이어야 한다. |
| 치마 시그니처 | 기본 쇼룸 주소에서 목 상품은 기본 상의다. 입력·분석으로 돌아가 여성 하의와 치마 분류를 저장한 뒤 콘티에 재진입한다. 첫 장과 사진 판이 치마 풀이고, 그중 한 장을 고른 뒤 재진입해도 유지되어야 한다. 직접 입력한 치마·스커트도 같다. 새 쿼리 옵션은 만들지 않았다. |
| 다시 만들기 | 미편집 초안을 바지에서 치마로 바꾸어 재진입 → 첫 장 치마 예시와 한 번의 재구성. 콘티를 먼저 편집한 상태에서는 전체 배치를 유지한다. 다음 진입에서는 같은 열쇠로 반복 재구성하지 않는다. 모르는 여성 하의·남성 하의·상의·원피스도 기존 풀 확인. |
| 기존 기능 | 생성 뒤 페이지와 편집기의 디테일 결과, 수동 디테일 요소의 따온 사진, D170/171 사진 판, 제목 간격과 172/184 썸네일, 저장·되돌리기·다음 단계·콘솔 오류를 확인한다. |
