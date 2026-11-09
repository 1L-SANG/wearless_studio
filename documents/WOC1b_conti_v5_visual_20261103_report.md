실제 모델: gpt-6.1-sol · effort: max. 현재 실행 세션의 turn_context에서 확인했다.

# WOC1b 콘티 v5 시각 다듬기와 남은 손질, 2026-11-03

작업 공간은 `/Users/daily/.codex/worktrees/templates-mvp/wearless_studio`다. WOC1 2라운드와 앞 작업의 미커밋 변경을 보존했다. git 쓰기, 메인 폴더 코드 수정, 브라우저 실행, 유료 API 호출은 하지 않았다. 파일·터미널·브라우저를 결과 표시용으로 자동 열지 않았다. `DESIGN.md`는 수정하지 않았다.

기준은 `conti-final-20261103/VISUAL_SPEC.md`의 표, NOTES, a.html과 연결 코드, qa/after와 scene-notes, 장부 D165~D173이다. 고정 머리 띠·세트 사진 판·무대·디자인 후보 장면 그림을 읽었고, 목업 코드를 그대로 이식하지 않았다. D172가 명세 8절의 더스티 블루 재선언보다 우선하므로 셀러 앱의 ADR-0021 파스텔을 그대로 상속한다. D171과 D173은 적용하지 않았다.

## 항목별 결과

| 항목 | 한 일 |
|---|---|
| 1. 시각 명세 | UI 글자 역할을 28·17·15·14·13·12px로 묶고 굵기·행간·자간을 적용했다. Section만 Playfair Display 500 이탤릭과 `--fg-2-read`, 나머지 글은 `--fg-1`·`--fg-2`로 정리했다. 숫자에는 tabular-nums, 버튼에는 몸글 자간을 쓴다. 사진 8px, 판 16px, 줄과 후보 12px, 썸네일 6px 둥글기와 지정된 여백·선·유리판·그림자를 적용했다. 새 스타일은 콘티 전용 두 모듈에 있고 토큰은 `.sb-v5-theme` 범위로 제공한다. |
| 1. 크기와 상태 | 옆 판은 `min(520px, 40vw)`, 위·아래·오른쪽 12px. 머리 48px와 몸 스크롤을 분리해 X와 뒤로 가기를 고정했고, 몸을 내렸을 때만 머리 아래 선이 생긴다. 닫기·추가 원·휴지통 32px, 예시 모드 높이 40px, 구도 알약 32px, 다음 버튼 44px, 색상 원 34px, 목록 그림 64×84, 후보 그림 최대 196×250이다. 고름은 무대 2px, 예시·지금 세트 3px `--fg-1`; 키보드 초점은 파랑 또는 검정 단추·목차 줄의 `--cta-focus-ring`으로 나눴다. |
| 1. 움직임과 되돌리기 | 색·선·그림자 150ms, 판·흐림·위치·아래 받침·알림 200ms, 회전 9s. reduced-motion은 새 움직임을 끈다. 되돌리기는 위 고정 알림을 제거하고 아래 유리 받침의 “n가지 바꿨어요 · 되돌리기”로 옮겼다. 기존 operationCount, 설정 묶음, 섹션 작업별 한 건, 10초 창과 hover 일시정지는 유지하고 키보드 초점에도 일시정지를 연결했다. 옆 판이 열리면 받침은 200ms로 숨고 숨은 버튼은 Tab과 클릭 대상에서 빠진다. |
| 1. 전체 보기와 공용 부품 | 전체 보기는 왼쪽 목록을 비우고 한 칸 전체를 사용하며 머리를 왼쪽에 맞췄다. 편집 복귀 시 현재 목록 줄 초점을 복원한다. 추가 목록에서 연 숨은 글 섹션은 머리 띠의 섹션 목록으로 돌아갈 수 있고, 빼도 같은 삽입 자리의 목록으로 돌아간다. 갤러리 점은 넘칠 때만 양끝 가림을 적용하고 현재 점을 따라 스크롤하며 화살표가 밀려나지 않는다. 공용 갤러리·인스펙터 CSS는 v5 범위 안에 가뒀다. |
| 2. D170 세트 사진 판 | 현재 세트의 같은 사진들을 먼저 보여 준다. 장소 세트는 “이 컷만 다른 사진으로 바꾸기” 아래에 전체 예시를 접어 두며, 누르면 같은 자리에서 모드·장소·구도·3×2와 “이 사진만 바뀌어요”, 접기를 연다. 세트에서 빠진 사진과 세트 밖 사진은 바로 전체 예시로 열린다. 스튜디오는 시안의 horizon 분기처럼 같은 세트 먼저, 다른 스튜디오 예시는 열린 상태다. 위에 보여 준 현재 세트는 아래 대안에서 제외한다. 이미 사용 표시가 있어도 낱장 선택할 수 있고, 내 사진 교체는 접힘과 관계없이 남는다. |
| 2. 세트 소속 | 같은 세트 안의 다른 멤버를 고르면 소속을 유지하고 member order만 해당 사진의 값으로 바꾼다. 다른 세트나 낱장을 고르면 그 컷만 기존 D169 경로로 소속을 비우고 완성 사진을 사용한다. 사진의 저장 위치는 옮기지 않는다. 스튜디오 전체 바꾸기와 배경 atomic 저장·실패 재시도 경로도 유지한다. |
| 3. 후보 표본 | 새 순수 함수 `sectionDesignPreviewSample`은 일상 착용컷을 앞의 4장으로, 모자라면 같은 사진 반복으로 그린다. 핏은 앞·옆·뒤 3자리이며 모자라면 앞 사진을 반복한다. 다른 고정 섹션은 구조상 사진을 사용하고 추가한 비슷한 컷 overflow는 후보에서 뺀다. 후보를 고를 수 있는지 판단하는 일은 실제 run을 기준으로 먼저 하고, 그 다음 미리보기에서만 표본을 만든다. 삽입 후보도 같은 함수를 쓴다. 실제 blocks·저장·되돌리기·크레딧 장수는 바꾸지 않는다. |
| 4. 착용 디자인 쓰임 | 카탈로그의 P02-D01, P02-D02는 `usage: styling`, P04-D01은 `usage: fit`으로 명시했다. JS/Python이 쓰임의 필수 값과 템플릿 기본 섹션과의 일치를 검증한다. 후보는 종류·쓰임·사진 위치 수가 모두 맞아야 한다. 기존 run 전체 변경과 섹션 삽입도 같은 후보 제한을 쓴다. 새 저장 블록 필드는 없다. |
| 5. 글 섹션 머리 | 시안 app.js의 정해진 문구인 “글은 AI가 써요. 에디터에서 고칠 수 있어요.”를 상품 소개·추천 대상에 쓴다. 사진 안내 문구와 Section 0은 없다. 사진이 있는 다른 섹션 안내는 유지한다. |
| 6. D126 안내형 | 첫 장 기본은 horizon 그대로, styling을 허용에 더하고 창빛 우선 hint는 기본과 추가 레시피에서 제거했다. 일상 착용컷 기본을 styling과 styling 세트로 바꿔 감성형과 같은 장소 세트 배정 규칙을 쓴다. 핏 앨범은 기존 horizon medium 기본을 유지하면서 styling을 허용하고 단순한 배경의 생활 장소도 고를 수 있게 안내한다. 템플릿 느낌별 예시 풀이나 별도 사진 자산은 만들지 않았다. 81개 고정 사례를 두 엔진의 정확한 일치 뒤 갱신했다. |

## 명세와 다른 점과 해석

- **D172 우선:** 명세 8절 1번과 NOTES의 더스티 블루 재선언은 적용하지 않았다. `sellerDesign.css`와 기존 `--ring-flow-*` 값은 그대로다.
- **D171·D173 제외:** 단일컷을 새 스택·작은 창으로 바꾸지 않았고, 무대나 페이지 구성에 새 배경색을 깔지 않았다. 따뜻한 회색 새 이름은 대표 도식 안에만 쓴다.
- **실제 사진 미리보기 유지:** 도구 틀과 대표 도식에는 명세 토큰을 썼지만, 후보와 목차는 요청대로 실제 섹션 사진과 조립 배치를 쓴다. 회색 와이어 전용 목업 레이아웃이나 새 디자인 데이터를 만들지 않았다. 도식 안의 작은 글자는 그림의 일부이며 UI 글자 6단과 구분한다. 입력은 기존 규칙대로 16px 이상이다.
- **무대 높이:** 기존 React 화면의 CSS 기반 스크롤 제한을 유지하고 아래 받침 공간을 확보하도록 `max-height: calc(100vh - 302px)`로 두었다. 시안의 JS `--col-top` 계산은 옮기지 않았다. 낮은 창과 실제 헤더 높이에 따른 간격은 총괄 화면 확인 대상이다.
- **고정 표본:** 장수가 늘어나는 일상 착용컷만 4장으로 고정하고 핏 3자리와 나머지 고정 섹션의 구조를 보존한다. 목차 썸네일과 전체 보기는 실제 장수 그대로다. 후보용 복제 ID는 미리보기에서만 쓰고 서버에 보내지 않는다.
- **스튜디오 접힘:** `variant-a.js`의 `!hz` 조건을 따라 장소 사진만 접는다. 스튜디오는 같은 세트와 다른 스튜디오 사진을 열린 채 보여 준다. 해당 장면을 총괄 재검사 목록에 넣었다.
- **핏 앨범 허용:** 기존 recipe의 full/medium 허용은 없애지 않고 styling을 더했다. 기본은 medium이고 단순 배경 안내를 붙였다. 새 배경 판정기나 구도별 강제 제한은 만들지 않았다. 유효한 기존 저장 사진도 일괄 변경하거나 새로 시드하지 않는다.
- **저장 계약 유지:** 카탈로그에만 디자인 usage를 더했다. blocks list, pageSectionKey run 순서, 첫 장 고정, 의존 사진과 재사용 규칙, 생성 파이프라인은 유지했다. 과거에 저장한 다른 쓰임 디자인을 강제로 갈아 끼우지 않고 새 후보만 제한한다.

## 새 토큰과 사용처

`src/styles/tokens.css`의 `.sb-v5-theme`에 명세의 새 이름 21개를 선언했다. 페이지, 옆 판, 아래 받침이 이 클래스를 갖는다. `useStoryboardTheme`은 v4 콘티가 열린 동안 body에도 이 범위를 붙여 형제 요소인 상단 머리의 200ms 전환에 토큰을 제공하고, 이동하거나 레거시 화면이면 해제한다. body portal에서도 값이 끊기지 않고 다른 화면에는 적용되지 않는다. 공용 `features.css`, `sellerDesign.css`는 수정하지 않았다.

| 이름 | 값 | 사용처 |
|---|---|---|
| `--sb-fs-title` | 28px | 페이지 제목 |
| `--sb-fs-head` | 17px | 무대·옆 판·전체 보기 제목 |
| `--sb-fs-sub` | 15px | 판 소제목, 예시 모드, 다음 버튼, 무대 Section 표기 |
| `--sb-fs-body` | 14px | 목차 이름·Section 표기, 장소 탭, 후보 이름 |
| `--sb-fs-btn` | 13px | 작은 단추, 아래 숫자·되돌리기 |
| `--sb-fs-note` | 13px | 설명·세트 이름·빈 상태 |
| `--sb-fs-meta` | 12px | 컷 수, 장소·번호, 배지·쪽 점 안내, 기본 표시 |
| `--sb-line-control` | `var(--fg-3)` | 테두리 알약, 묶음 선, 추가 칸 |
| `--sb-t-fast` | `var(--cta-transition)` = 150ms | 색·선·그림자 올림 |
| `--sb-t-base` | 200ms | 판 등장, 자리·흐림·아래 바·되돌리기 |
| `--sb-spin` | 9s | 고른 목차 줄과 회전 버튼 |
| `--sb-font-index` | Playfair Display, Georgia, serif | 목차·무대의 Section 표기 |
| `--sb-wire-w` | `var(--bg-1)` | 대표 도식의 흰 카드 |
| `--sb-wire-1` | #f4f4f2 | 도식·후보 그림 받침 |
| `--sb-wire-2` | #eaeae7 | 도식 카드 선 |
| `--sb-wire-3` | #dfdfdb | 도식 메시지 면 |
| `--sb-wire-inv` | #3a3a38 | 도식 질문 면 |
| `--sb-wire-comp` | #c8c8c3 | 도식 사진 선 |
| `--sb-wire-photo` | #d6d6d1 | 도식 사진 자리 |
| `--sb-wire-title` | #474743 | 도식 글·선 |
| `--sb-wire-on-photo` | rgba(255,255,255,.82) | 도식의 진한 면 위 글 |

새 색을 고르지 않고 시안의 기존 와이어 값에 이름을 붙였다. 아울러 기존 `.sb-canvas-shell`의 `--sb-pop-shadow`와 `--sb-ease` 두 값을 동일하게 이 범위에도 제공했다. `--sb-card-shadow`의 기존 공용 선언은 건드리지 않았다. 모듈에는 새 원시 색과 `word-break` 덮어쓰기가 없다.

## 바꾼 파일

- 화면과 전용 스타일: `src/features/storyboard/Storyboard.jsx`, `StoryboardContinue.jsx`, `StoryboardDrawer.jsx`, `StoryboardExampleGallery.jsx`, `StoryboardLayout.module.css`, `StoryboardPageList.jsx`, `StoryboardPanels.module.css`, `StoryboardPhotoPanel.jsx`, `StoryboardSectionPicker.jsx`, `StoryboardSectionScheme.jsx`, `StoryboardSpaceSetPanel.jsx`, `StoryboardStage.jsx`, 새 `useStoryboardTheme.js`.
- 토큰: `src/styles/tokens.css`의 v5 범위만 추가.
- 순수 함수와 규칙: 새 `src/lib/pageTemplates/designPreview.js`, `designChoices.js`, `catalog.js`, `README.md`, `src/lib/storyboardExampleSelection.js`.
- 공유 카탈로그·검증: `server/app/data/page_templates_v1.json`, `server/app/agents/page_templates.py`.
- 고정 사례: `tests/fixtures/page_templates_cases.json` 79개에서 81개. 기존 안내형 입력 10개를 새 생활 장소 기본으로 맞췄고 첫 장·핏 앨범의 생활 장소 medium 두 사례를 더했다. 재생성 도구 코드는 바꾸지 않았다.
- 새 시험: `tests/frontend/storyboard-woc1b.test.mjs`, `storyboard-woc1b-visual.test.mjs`.
- 기존 시험 갱신: `tests/frontend/helpers/storyboardStageHarness.mjs`, `page-template-ui.test.mjs`, `page-template-interactions.test.mjs`, `storyboard-initial-reveal-gate.test.mjs`, `storyboard-owner-feedback.test.mjs`, `storyboard-qa-b1.test.mjs`, `server/tests/test_page_templates_catalog.py`.
- 보고서: `documents/WOC1b_conti_v5_visual_20261103_report.md`.

## 총괄 화면 확인

브라우저를 실행하지 않았으므로 픽셀·대비·어절 끊김·실제 스크롤의 화면 합격은 주장하지 않는다. 총괄이 작업 공간에서 `pnpm dev:mock --host 127.0.0.1 --port 5173`을 실행하고 새 비공개 창에서 새 상태로 연다.

- 감성형: `http://127.0.0.1:5173/create/storyboard?mockTemplate=emo`
- 쇼룸형: `http://127.0.0.1:5173/create/storyboard?mockTemplate=show`
- 안내형: `http://127.0.0.1:5173/create/storyboard?mockTemplate=guide`

| 장면 | 확인 순서 |
|---|---|
| 기본 틀 | 세 템플릿을 1280·1440·1920, 웹킷 1440에서 열어 제목·Section·목차·메타의 크기, 선과 8/12/16 둥글기, 파스텔 고리를 비교한다. 무대와 목차에 D173의 새 배경이 없어야 한다. |
| 옆 판과 초점 | 사진·디자인·추가·장소 판을 각각 연다. 48px 띠의 X와 뒤로 가기가 본문과 겹치지 않고 내렸을 때만 선이 생기는지, Esc와 바깥 클릭, 초점 복원이 유지되는지 확인한다. |
| 세트 칸 | 장소 묶음의 사진을 눌러 같은 세트 사진과 “이 컷만 다른 사진으로 바꾸기”가 먼저 나오는지 본다. 펼친 자리의 범위 글과 접기, 내 사진 단추, 이미 사용 배지, 같은 세트 재선택 소속 유지를 확인한다. |
| 세트 이탈 | 펼쳐 다른 세트의 낱장을 고른다. 해당 칸만 자리 그대로 빠지고 장소 라벨이 바뀌어야 한다. 판을 닫고 다시 열면 전체 예시가 즉시 나와야 한다. 묶음 전체 바꾸기는 별개로 유지한다. |
| 스튜디오 | 같은 세트가 먼저, 다른 스튜디오 예시는 열린 상태인지 확인한다. 앞·옆·뒤 전체 바꾸기와 배경 저장, 낱장 선택, 3px 검정 고름이 유지되는지 본다. |
| 갤러리 | 세트/단일컷 40px 칸, 구도 알약 32px, 3×2, 이미 사용 12px, 고름 3px·초점 파랑을 비교한다. 단일컷 전체에서 점이 많아도 ‹ ›가 보이고 현재 점이 따라와야 한다. D171 작은 창은 없어야 한다. |
| 긴 디자인 후보 | 일상 착용컷을 10장까지 늘려 디자인 판을 연다. 후보가 네 장 표본이고 P04 핏 디자인이 없어야 한다. 두 장뿐인 새 섹션의 삽입 후보도 네 장을 반복해 보여야 한다. 실제 무대 컷 수는 바뀌지 않아야 한다. |
| 추가 목록과 글 | 64×84 그림·설명·32px 검정 ＋와 이미 있음 상태를 비교한다. 기존 상품 소개/추천 대상은 이름과 글 안내만 보여야 한다. 섹션 목록으로 복귀, 빼기 후 목록, 되돌리기를 확인한다. |
| 되돌리기와 아래 바 | 넣기·옮기기·빼기·디자인 변경과 설정 묶음을 실행한다. 위 진행 막대에 작업 알림이 뜨지 않고 아래에 n가지와 되돌리기가 나와야 한다. hover·키보드 초점 동안 유지, 단계 이동 저장 대기, 페이지 끝 위치를 확인한다. 옆 판이 열리면 받침 전체가 숨는다. |
| 안내형 D126 | 새 상태에서 일상 사진이 생활 장소 세트인지 확인한다. 첫 장을 styling으로, 핏 앨범을 생활 장소 medium으로 바꾸어 저장 후 다시 열어도 되돌아가지 않는지 본다. 기존 페이지를 임의로 새 구성으로 바꾸지 않아야 한다. |
| 전체 보기와 움직임 | 전체 보기가 한 칸 전체를 쓰고 편집 복귀가 되는지, reduced-motion에서 회전·판·흐림 전환이 꺼지는지 확인한다. |

## 시험 명령과 실제 결과

동작 변경 2~6번은 실제 JSX와 순수 함수 재현 8개를 먼저 실행해 **0 통과, 8 실패**를 확인한 뒤 **8 통과, 0 실패**로 고쳤다. 이후 스튜디오 분기, 핏 표본, 세 템플릿 처음 채우기, 토큰·치수·상태·본문 스크롤·v5 범위와 해제를 더했다. 새 두 시험 파일은 합계 29개이며 최종 전체 시험에 포함된다.

| 명령과 위치 | 실제 결과 |
|---|---|
| 작업 공간 `pnpm test:frontend` | **4,870 통과, 0 실패, 0 건너뜀**, exit 0. 54.91초 |
| 작업 공간 `server/`에서 `/Users/daily/Documents/wearless_studio/server/.venv/bin/pytest -q --ignore=tests/test_personalization.py` | **10,707 통과, 183 건너뜀, 0 실패**, 경고 1,043개, exit 0. 243.08초 |
| 작업 공간 `pnpm build` | **성공**, exit 0. 17.86초 |
| `PAGE_TEMPLATE_PYTHON=/Users/daily/Documents/wearless_studio/server/.venv/bin/python node scripts/regenerate-page-template-fixtures.mjs` | **81개 사례 JS/Python 정확히 일치 후 재생성** |
| `MOCK_API_LATENCY_SCALE=0 node --test tests/frontend/storyboard-woc1b*.test.mjs tests/frontend/page-template-screen.test.mjs` | **30 통과, 0 실패**. 새 동작·시각 시험 29개와 기존 화면 SSR 1개 |

처음 채우기 확인은 세 템플릿 × 색상 수 1·2·4의 9개 상태다. 모든 실제 조립 이미지에 src와 원본 블록이 있고, guide 일상 착용컷은 styling으로 유지됐다. 기존 run 순서·의존성·저장 시험과 고정 씨앗을 섞은 JS/Python parity 18개 페이지도 전체 시험에서 통과했다.

전체 프런트 실행 한 번은 화면 시험 통과 출력 뒤 종료 대기에서 진행하지 않아 중단했다. 같은 화면 시험은 20초 제한의 단독 실행에서 1개 통과·정상 종료했고, 이후 전체 재실행과 최종 코드 전체 실행이 각각 정상 종료했다. 중단된 로그를 통과 숫자로 쓰지 않았다.

최종 로그는 `.scratch/woc1b-20261103/frontend-verified.log`, `server.log`, `build-verified.log`다. 재현은 `red.log`, `green-initial.log`, 최종 집중 확인은 `theme-final.log`, 고정 사례는 `fixtures.log`에 있다. 빌드의 큰 청크와 import 경고는 오류와 구분한다. 총괄의 사전 시험 숫자를 재사용하지 않았고 브라우저·실서버 생성·유료 API 시험은 실행하지 않았다.
