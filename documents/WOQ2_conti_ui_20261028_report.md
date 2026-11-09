실제 모델 / effort: 런타임 식별값이 이 세션에 노출되지 않아 확인 불가. 오너 요청값은 `gpt-6.1-sol / max`이며, 이를 실제값으로 단정하지 않는다.

# WOQ2 콘티·템플릿 고르기 화면 보강 — 2026-10-28

## 결과와 작업 기준

WO1·WO2·WOQ의 미커밋 변경 위에서 작업했다. 지정된 디자인 점검 보고서 `conti-ui-20261028/report/index.html`을 끝까지 읽고, `patch/conti-ui.diff`를 현재 파일과 대조해 반영했다. 기존 작업을 되돌리거나 git 쓰기를 하지 않았다.

**추가 지시를 우선 적용했다.** 최종 고르기 위치는 분석 내용 다음, ‘코디 고르기’ 버튼 앞이다. 코디 단계로 들어가기 전에 현재 선택 저장을 기다린다. 긴 견본의 호버·포커스 미리보기와 전체 보기 대화상자, 이미지 누락 대체 표시를 구현했고, 이름은 ‘추천 대상’, ‘후기와 소통’으로 바꿨다.

필수 시험의 최종 결과는 프런트 **4,569 통과**, 서버 **10,482 통과·183 건너뜀**, 빌드 성공이다. 브라우저는 실행하지 않았다.

## 추가 지시 반영

### 1. 분석 단계에서 템플릿 선택

- `AnalysisStepFlow.afterAnalysis`에 고르기를 배치했다. 분석 정보 → 템플릿 고르기 → 코디 고르기 버튼 → 코디보드 순서다. 분석을 접으면 고르기도 함께 접힌다.
- `beforeEnter={prepareOutfit}`가 MD 코멘트와 템플릿을 저장한다. `persistSelectedPageStyle`은 저장 대기열이 갱신되면 최신 대기열까지 기다리고, 마지막 선택 또는 기본값도 저장한 뒤 완료한다.
- 선택 준비 전·유효하지 않은 선택·저장 실패에서는 코디 단계가 열리지 않는다. 진입을 준비하는 동안 고르기를 비활성화한다. 오류가 나면 분석 단계와 재시도 가능한 진입 버튼을 유지한다.
- `pageStyle` 구조, 서버 잠금, 로그인 전 초안 승격, 다음 프로젝트의 마지막 선택 기본값은 기존 WO2 경로를 사용한다. 최종 확정도 같은 저장 함수를 기다린다.

### 2. 긴 견본 미리보기

- 파일 경로와 대체 사진, 설명의 정본은 `server/app/data/page_templates_v1.json`의 `templates[].preview`다.

| 템플릿 | 긴 견본 URL | 총괄이 넣을 파일 | 파일 누락 시 대체 |
|---|---|---|---|
| 감성형 | `/templates/previews/emo.webp` | `public/templates/previews/emo.webp` | `/models/demo/cut1.webp` |
| 쇼룸형 | `/templates/previews/show.webp` | `public/templates/previews/show.webp` | `/models/demo/cut2.webp` |
| 안내형 | `/templates/previews/guide.webp` | `public/templates/previews/guide.webp` | `/models/demo/cut2.webp` |

- 카드의 기존 3열 배치와 사진 창 4:5 비율을 유지한다. 긴 그림은 폭에 맞추고 처음에는 맨 위를 보여 준다. 불러오기 전과 실패 시에는 기존 견본을 표시한다.
- 마우스 호버 또는 키보드 포커스에서 끝까지 한 번 이동한다. 이동 거리 = 표시된 그림 높이 − 사진 창 높이, 시간 = 이동 거리 ÷ 사진 창 높이 × 1,000ms다. 끝에서 멈추며, 호버·포커스가 끝나면 애니메이션을 취소해 맨 위로 돌아온다. 크기와 미디어 설정이 바뀌면 다시 계산한다.
- `prefers-reduced-motion`에서는 자동 이동하지 않는다. 마우스 호버가 없는 기기에서도 항상 있는 ‘전체 보기’ 버튼을 사용할 수 있다.
- 전체 보기는 이름이 연결된 네이티브 모달 `dialog`이며, 본문을 세로 스크롤한다. 닫기·Escape·배경 클릭과 초점 복귀를 처리했다. 선택 라벨 밖의 별도 버튼이므로 미리보기를 열어도 템플릿 선택은 바뀌지 않는다. 버튼은 `.modal-actions` 공통 알약·높이 규칙을 따른다.
- 새 인라인 스타일 없이 Web Animations API의 transform으로 움직인다. 새 그림 자산은 만들거나 교체하지 않았다. **긴 견본 파일은 아직 없으므로 현재 화면에서는 기존 사진이 표시된다.**

### 3. 오너 확정 이름

- 종류 이름을 ‘추천 대상’, 메뉴 묶음을 ‘후기와 소통’으로 변경했다. 메뉴는 별도 이름 지도를 없애고 카탈로그의 `menuGroups`를 읽는다.
- 콘티 머리와 글 카드, 새로 조립되는 에디터 블록 이름에 반영했다. 이미 저장된 페이지의 에디터 미리보기 이름도 카탈로그로 표시하며 저장된 블록 이름은 덮어쓰지 않는다.
- 프런트·서버 기대값과 고정 페이지 fixture를 갱신했다. fixture 변경은 이름 14곳뿐이고, 좌표·크기·문구 요소·사진·도형·대비 값은 그대로다.

## K1~K9와 메뉴 장수

| 항목 | 최종 구현 |
|---|---|
| K1 | 추가 지시로 대체했다. 분석 내용 다음·코디 진입 전에 고르고 저장 완료를 기다린다. 제목 16px·설명 12px와 왼쪽 축을 유지했다. 고르기는 확인 줄 밖이고 `.confirm:has(...)` 임시 덮어쓰기는 없다. 기존 -80px 끌어올림도 제거했다. |
| K2 | 기존 `sb-deck-headrow`와 Section 장식 서체·화살표·컷 범위를 사용한다. 범위는 `photoOrdinals` 기준이라 글 카드가 번호를 밀지 않고, 글만 있는 섹션은 비운다. 작은 빼기 버튼에 섹션 이름·용도/꼬리표를 포함한 aria-label을 붙였다. |
| K3 | 콘티 글 카드는 그리드 한 줄 전체를 차지한다. 제목과 카드 안 빼기를 없애고 들어갈 글을 표시한다. 섹션 삭제는 머리의 버튼 하나로 처리한다. 인스펙터용 제목 표시는 유지했다. |
| K4 | 닫힌 메뉴는 작은 알약과 기호 하나다. 이미 있는 단일 섹션은 ‘들어 있어요’로 비활성화하고, 반복 가능한 섹션은 ‘하나 더’로 표시한다. 착용 사진 버튼에 ‘4컷’·‘3컷’을 미리 보인다. 버튼 묶음은 폭이 부족하면 같은 알약 모양으로 줄을 바꾼다. |
| K5 | 추가한 섹션을 펼치고 DOM 배치 후 부드럽게 스크롤하며 1초 강조한다. 움직임 줄이기에서는 즉시 이동한다. 알림은 ‘착용 사진 4컷을 넣었어요’처럼 실제 늘어난 사진 수를 포함한다. 추가·추가·삭제를 각각 한 건씩 되돌리며 앞선 설정 변경과도 묶지 않는다. 일반 설정의 기존 묶음 되돌리기는 유지한다. |
| K6 | `storyboardNote`를 `storyboardPhotoNote`·`storyboardTextNote`로 분리했다. 착용 용도별 설명은 `storyboardPhotoNotesByUsage`, 착용 자르기 설명은 `storyboardCropNote`다. 디자인별 `storyboardTextNote`와 섹션의 `displayTag`를 읽으므로 컴포넌트 임시 문구·키 끝말 지도 없이 안내형 디테일/핏을 구분한다. 이름 보류는 추가 지시로 취소했다. |
| K7 | 템플릿에서는 옛 디테일 추천 안내를 상품 포인트 섹션에서만 표시한다. 색상과 제품에는 표시하지 않는다. 안내 여백을 사진 그리드의 안쪽 여백에 맞췄다. 기존 비템플릿 PRODUCT 조건은 유지한다. |
| K8 | 이미 생성한 옛 페이지에는 공용 옷 태그 그림, 에디터 열기·보관함으로 버튼을 표시한다. 다시 짠 콘티에는 사본의 안내 줄을 반영했다. |
| K9 | 사진이 한 장이거나 글만 있는 섹션은 처음부터 펼친다. 미리 가져온 첫 렌더와 비동기 로드 모두 같은 초기값 함수를 사용하며, 이후 사용자가 접은 상태를 매 렌더마다 강제로 열지 않는다. |

메뉴로 넣는 스타일링은 **기준 색 4컷·장소 그룹 하나**, 핏 확인은 **앞·옆·뒤 3컷**이다. 기본 페이지의 스타일링 섹션은 기존 장수와 추가 색 카드를 유지한다. 메뉴 추가 섹션에는 추가 색 카드를 붙이지 않는다.

현재 공개 스타일링 장소 세트의 포즈 예시는 3개다. 감성형의 네 번째 추가 컷은 같은 장소 그룹에서 공개 포즈 하나를 다시 참조한다. 쇼룸형·안내형은 sequence 세트의 앞 네 멤버를 사용한다. 서로 다른 새 포즈 자산 4개를 준비한 것은 아니다. 기존 서버 검증과 생성 수 계산은 이 구성에 대해 각각 +4컷을 인정했다.

원래 K10의 새 그림 제작은 수행하지 않았다. 추가 지시가 요구한 긴 견본의 데이터 경로·표시·상호작용·대체 처리는 구현했고, 실제 그림 공급은 총괄 담당으로 남는다.

## 시험 결과

아래 필수 명령은 추가 지시와 최종 에디터 이름 표시 변경까지 반영한 상태에서 확인했다. 로그는 `.scratch/woq2-20261028/`에 있다.

| 실행 위치·명령 | 최종 결과 | 로그 |
|---|---|---|
| 루트: `pnpm test:frontend` | **4,569 통과**, 실패·건너뜀 0 | `frontend-all.log`, 종료 코드 0 |
| `server/`: `/Users/daily/Documents/wearless_studio/server/.venv/bin/pytest -q --ignore=tests/test_personalization.py` | **10,482 통과·183 건너뜀**, 실패·오류 0, 경고 1,043개 | `server-all.log`, 종료 코드 0 |
| 루트: `pnpm build` | **성공**, 35.84초 | `build.log`, 종료 코드 0 |
| 루트: `GIT_OPTIONAL_LOCKS=0 git diff --check` | 통과 | 공백 오류 없음 |

빌드에는 의존성의 PURE 주석, 정적/동적 혼합 import, 500kB 초과 청크 경고가 있다. 서버 경고에는 Python 3.12 sqlite 날짜 어댑터 폐기 예고 등이 있다.

추가 검증:

- 집중 회귀 **303 통과**, 실패·건너뜀 0. 명령: `MOCK_API_LATENCY_SCALE=0 node --test tests/frontend/page-template-*.test.mjs tests/frontend/page-templates-catalog.test.mjs tests/frontend/storyboard-owner-feedback.test.mjs tests/frontend/storyboard-qa-b1.test.mjs tests/frontend/compose-mode-location.test.mjs tests/frontend/outfit-v18-focus-motion.test.mjs tests/frontend/analysis-md-comment.test.mjs tests/frontend/draft-slot-takeover.test.mjs` (`frontend-focused.log`).
- 기존 확정·로그인 관련 시험 도우미가 공통 저장 함수를 실제로 실행하도록 갱신한 뒤 **125 통과**. 명령: `MOCK_API_LATENCY_SCALE=0 node --test tests/frontend/face-render-warm-on-select.test.mjs tests/frontend/mobile-product-input.test.mjs` (`frontend-confirmation.log`). 최종 전체 시험에도 포함된다.
- 최종 이름 표시·고르기·미리보기 집중 시험 **24 통과**. 명령: `MOCK_API_LATENCY_SCALE=0 node --test tests/frontend/page-template-ui.test.mjs tests/frontend/page-template-screen.test.mjs tests/frontend/page-template-entry.test.mjs tests/frontend/page-template-preview.test.mjs` (`frontend-addendum.log`).
- 실제 JS 추가 섹션을 기존 Python 정규화·공개 세트 바인딩으로 검증한 **18조합 통과**: 템플릿 3종 × 색 1·2·4개 × 스타일링/핏. 기준 색만 추가되고 생성 수가 +4/+3으로 일치한다. 명령은 아래와 같다 (`section-save-check.log`).

```sh
node .scratch/woq2-20261028/create-section-save-cases.mjs
PYTHONPATH=server /Users/daily/Documents/wearless_studio/server/.venv/bin/python .scratch/woq2-20261028/check-section-save.py
```

새 시험은 Section 번호·사진 범위·빼기 aria-label, 글 카드 전체 행·제목/삭제 중복 없음, 메뉴 두 상태와 컷 수, 넣은 섹션 펼침·스크롤·1초 강조, 되돌리기 한 건 단위와 앞선 설정 보존, 4컷·3컷 및 기본 구성 보존, 초기 펼침 두 경로, 디테일 안내 조건, 카탈로그 문구·꼬리표, 분석 단계 배치, 저장 대기/실패/미선택 차단, 호버·포커스·속도·크기 변화·움직임 줄이기, 파일 누락 대체, 전체 보기와 초점 복귀, 저장된 옛 이름의 새 표시를 다룬다.

브라우저와 실제 이미지 레이아웃을 대신한 DOM 경계·치수 및 훅 시험이다. **Chrome·WebKit 실행, 스크린샷, 실제 어절 중간 줄바꿈·가로 넘침 자동 검사는 하지 않았다.**

## 바꾼 파일

구현·카탈로그·개발 문서 19개:

- `src/features/analysis/AnalysisForm.jsx`
- `src/features/analysis/AnalysisStepFlow.jsx`
- `src/features/analysis/AnalysisStepFlow.module.css`
- `src/features/analysis/PageTemplatePicker.jsx`
- `src/features/analysis/PageTemplatePicker.module.css`
- `src/features/storyboard/Storyboard.jsx`
- `src/features/storyboard/TemplateSectionBand.jsx`
- `src/features/storyboard/StoryboardTextCard.jsx`
- `src/features/storyboard/SectionAddMenu.jsx`
- `src/features/storyboard/TemplateSections.module.css`
- `src/features/storyboard/storyboardSectionPresentation.js` — 새 파일
- `src/features/editor/Editor.jsx`
- `src/lib/pageTemplates/sectionActions.js`
- `src/lib/pageTemplates/seed.js`
- `src/lib/pageTemplates/slots.js`
- `src/lib/pageTemplates/previewMotion.js` — 새 파일
- `src/lib/pageTemplates/README.md`
- `src/styles/features.css`
- `server/app/data/page_templates_v1.json`

시험·고정 입력 15개:

- `tests/frontend/page-template-screen.test.mjs`
- `tests/frontend/page-template-seed.test.mjs`
- `tests/frontend/page-template-ui.test.mjs` — 새 파일
- `tests/frontend/page-template-interactions.test.mjs` — 새 파일
- `tests/frontend/page-template-preview.test.mjs` — 새 파일
- `tests/frontend/page-template-entry.test.mjs` — 새 파일
- `tests/frontend/page-templates-catalog.test.mjs`
- `tests/frontend/storyboard-owner-feedback.test.mjs`
- `tests/frontend/compose-mode-location.test.mjs`
- `tests/frontend/analysis-md-comment.test.mjs`
- `tests/frontend/draft-slot-takeover.test.mjs`
- `tests/frontend/face-render-warm-on-select.test.mjs`
- `tests/frontend/mobile-product-input.test.mjs`
- `tests/fixtures/page_templates_cases.json`
- `server/tests/test_page_templates_catalog.py`

보고서: `documents/WOQ2_conti_ui_20261028_report.md`.

## 계약·보존 확인

- **저장 계약과 다르게 한 곳은 없다.** `blocks: list`, 사진별 블록, 서버 저장·검증·생성 규칙, `pageStyle` 계약을 유지했다. 서버 앱 Python, API 레이어, DB 스키마를 바꾸지 않았다.
- 시작 시점과 비교해 카탈로그의 레이아웃·토큰·슬롯 레시피·기본 컷 수·조립 데이터가 동일함을 확인했다. 차이는 WOQ2 화면 문구·꼬리표·견본 메타데이터와 오너가 승인한 이름이다. WOQ의 에디터 조립·대비·병합 변경도 보존했다.
- 새 인라인 스타일·`word-break` 덮어쓰기는 없다. 에디터·콘티·분석 폼의 기존 인라인 스타일 개수도 각각 57·8·10으로 그대로다. `app.css`의 한국어 기본 줄바꿈, orb·입력 글래스·사진 타일 비율·콘티 서체, 모바일/태블릿 PC 이어하기 정책을 보존했다.
- 사본과 달리 임시 `.confirm:has(...)`, 컴포넌트 설명 문구 지도, 키 끝말 꼬리표 지도를 사용하지 않는다. 고르기 위치와 이름 보류는 추가 지시로 대체했다.
- 메인 폴더 수정, git 쓰기, 브라우저 실행, 유료 API 호출, DB 마이그레이션, 결과 탭 자동 열기를 하지 않았다. 지정된 사본 자료와 venv는 읽기·실행 용도로만 사용했다.

## 총괄·디자인 세션 화면 확인 자리

1. 분석 단계에서 상품 정보 다음에 고르기가 보이고, 코디 단계에는 확인 버튼만 남는지 확인한다. 마지막 선택 로드·기본값·저장 지연·저장 실패·분석 다시 펼치기·로그인 승격도 함께 본다.
2. 총괄이 세 긴 그림을 넣은 뒤 첫 장 크롭, 마우스 호버·키보드 포커스의 끝까지 이동과 속도, 이탈 시 맨 위 복귀, 창 크기 변경을 확인한다. 파일 누락·느린 로드에서는 기존 견본이 유지되어야 한다.
3. 움직임 줄이기와 마우스 없는 기기의 전체 보기, 대화상자 세로 스크롤·닫기·Escape·배경 클릭·초점 가두기/복귀를 실제 Chrome·WebKit에서 확인한다.
4. 세 콘티의 Section 서체·화살표·사진 범위·작은 빼기, 안내형 디테일/핏 꼬리표, 글 카드 전체 폭과 문구, 섹션 내부 디테일 안내 여백을 확인한다.
5. 추가 섹션의 펼침·스크롤 위치·1초 강조, 스타일링 4컷과 핏 3컷, 추가 색 카드 없음, 추가·추가·삭제 후 한 번씩 되돌리기를 확인한다. 감성형 네 번째 컷의 공개 포즈 재참조도 확인한다.
6. 옛 생성 페이지의 옷 태그·두 버튼, 다시 짠 안내 줄, 한 장/글 전용 섹션의 첫 펼침을 확인한다.
7. 폭 **320, 360, 375, 390, 414px**에서 스크린샷과 어절 중간 줄바꿈·가로 넘침 자동 검사를 수행하고 WebKit도 함께 본다. 기존 보고서의 좁은 PC 창 코디 줄 넘침은 이번 범위에서 수정하지 않았다. 실제 모바일·태블릿은 기존 사진 입력 후 PC 이어하기 정책을 확인한다.
