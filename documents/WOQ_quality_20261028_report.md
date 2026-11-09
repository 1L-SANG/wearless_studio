실제 모델 / effort: 이 세션에는 런타임 세부 식별값이 제공되지 않아 확인할 수 없음. 요청값 `gpt-6.1-sol / max`의 적용 여부를 단정하지 않는다.

# WOQ 템플릿 화면 품질 보강 — 2026-10-28

## 결과와 보존 범위

WO1·WO2의 미커밋 작업 위에서 구현했다. 비교 보고서 `fidelity-20261028/report/index.html`을 끝까지 읽고 C1~C16, 패치 JSON·diff·CHANGES·패치 스크립트의 근거를 확인했다. 작업 시작 시 현재 카탈로그가 디자인 패치의 원본 JSON과 같았으므로, 패치를 그대로 반영한 뒤 코드 적용에 따른 임시 보정값을 정리했다.

- 두 조립기에 같은 폭·여백·반복 간격·표·도형·대비 메타데이터 처리를 넣었다.
- 템플릿은 이어보기로 열고, 떼어보기에서도 같은 섹션의 블록을 하나의 시각적 판으로 붙인다. 저장된 블록을 합치지 않았다.
- 생성 완료 시 손대지 않은 섹션은 완성본의 도형 폭·문단 수·소개 글·줄별 대비를 받는다. 셀러 편집 보호와 자동 글 높이 보정을 함께 처리했다.
- `blocks: list`, 1000px 캔버스, `maxBlockHeight: 3000`, 사진별 블록, 사진 교체·순서 변경, 기존 PNG/ZIP/내려받기 경로를 유지했다. WO2의 `pointItems`와 착용 사진 자르기를 유지했다.
- git 쓰기, 메인 폴더 수정, 브라우저 실행, 유료 API 호출, DB 마이그레이션, 결과 탭 자동 열기를 하지 않았다. 프로젝트 메인 폴더에서는 지시된 비교 자료와 지정 venv 실행 파일을 읽기만 했다.

## C1~C16

| 항목 | 반영 내용 |
|---|---|
| C1 | `pageDesignId`가 하나라도 있으면 이어보기 기본값. 사용자 선택만 프로젝트별 localStorage에 저장하며, 읽기·쓰기 예외와 잘못된 값은 기본값으로 처리한다. 연속 `pageSectionKey`별 DOM 판에 그림자를 한 번 주고 내부 틈은 없앴다. 떼어보기 섹션 사이 간격은 8px이며, 사진/프레임 끌기 중에는 삽입 행을 유지한다. |
| C2 | panel/list의 `hug`, `minW`, `maxW`, list `hugMode: each/max`를 구현했다. 공백 330, ASCII 560, 그 밖의 코드포인트 1000 단위로 어절 줄바꿈 후 최대 줄 폭을 올림한다. 자식 x와 가로 여백을 더하고, 줄어든 안쪽 폭으로 다시 배치한다. 추천·후기·질문/답·사이즈 팁·MD·Front/Side 알약에 적용했다. 색 이름/칩 묶음도 측정 폭을 쓰며, 혼용률 밑줄은 `w: text`로 앞 글을 따른다. 빈 글의 밑줄은 숨긴다. |
| C3 | 숫자 padding과 `[세로, 가로]`를 두 조립기와 검증기에 적용했다. 추천 `[14,22]`, 후기 `[20,24]`, 질문/답 `[16,22]`, MD `[22,26]`, 포인트 팁 `[18,24]`, Front/Side `[9,18]`. 잘못된 배열·음수·범위·옵션을 거절한다. |
| C4 | 마지막 블록 높이에 섹션 간격을 한 번만 더한다. 바탕이 캔버스와 다르면 해당 간격만 캔버스색 사각형으로 덮고 `locked`, `pageSectionGap`을 표시한다. 빈 간격 블록은 만들지 않는다. |
| C5 | photoStack·photoGrid·pointRows 뒤 `gap`을 한 번 적용한다. T07-D06 팁 padding은 데이터 임시값 34에서 26으로 되돌리고 photoStack gap 40을 적용했다. |
| C6 | 실제 글 줄마다 사진 좌표를 cover/crop 역변환하고, 픽셀별 대비의 하위 10% 값을 비교한다. 픽셀 평균·축소 리샘플링을 쓰지 않는다. 어느 글색도 줄 픽셀 90%에서 3:1을 만족하지 못할 때만 그 줄에 그늘을 켠다. 흰 글은 어두운 번짐, 검정 글은 밝은 번짐이다. 저장 텍스트의 줄별 결과를 에디터와 다운로드가 동일 DOM으로 그리며, 글·위치·폭·서식이 바뀌면 과거 측정은 적용하지 않는다. 픽셀 계산은 생성 작업자의 이벤트 루프 밖에서 실행한다. |
| C7 | factTable inline·columns·stacked를 구현했다. 안내형 columns는 라벨 폭 150, 값 크기 0.72의 muted 라벨, 행 간격 20, 아래 1px 선. 쇼룸형 stacked는 라벨 위·값 아래, 행 간격 24. |
| C8 | 안내형 임시 `•` 글자를 제거하고 6px 원/사각형을 첫 줄 가운데 높이에 배치한다. 본문은 22px 들여 쓴다. |
| C9 | 감성형·안내형 디테일 pointRows의 글 묶음을 사진 높이 가운데에 배치하고 사진 radius 16을 적용했다. WO2의 원본 참조·착용 자르기는 유지했다. |
| C10 | 1~10을 한·두·세·네·다섯·여섯·일곱·여덟·아홉·열로 표시한다. 예: `두 가지 색이에요`. 그 위는 숫자. 쇼룸·안내형의 불필요한 해요체 색상 제목은 디자인 데이터대로 제거했다. |
| C11 | MD body를 줄바꿈·빈 줄로 나눠 최대 세 말풍선으로 배치한다. 각 폭은 개별 hug, 사이 12px. 제목은 첫 말풍선에만 둔다. 네 문단 이상이면 셋째에 남은 문단을 이어서 글을 잃지 않는다. |
| C12 | 실제 단색 바탕과 같은 fill의 panel은 도형과 가로 안쪽 여백을 없앤다. 세로 여백은 유지한다. 사진 위 후기/질문 배경은 단색 바탕으로 취급하지 않아 흰 말풍선을 보존한다. |
| C13 | 지시대로 WO3 범위로 남겼다. 데이터 패치의 `Magazine`, `No. 001`은 유지했다. 셀러 프로필 조회·brand 역할 생성은 추가하지 않았다. |
| C14 | 색 캡션에 실제 swatchId의 10px 원을 붙인다. 색 이름의 계산 폭과 함께 정렬한다. 두 엔진은 같은 `swatchHex` 데이터를 읽는다. |
| C15 | 기존 28개 parity를 재생성하고 첫 장 intro 2개·MD 문단·줄별 대비 4개를 추가해 32개로 만들었다. 재생성 명령은 JS/Python의 블록과 측정 영역이 정확히 일치하는지 먼저 확인한다. 제거한 영문 색 캡션과 안내형 표 구분선의 옛 기대값도 갱신했다. |
| C16 | 영문 색 역할은 실제 상품 색 이름이 영문일 때만 그 이름을 사용하고, 그 밖에는 숨긴다. 민트의 swatchId가 green이라고 `Green`을 만들지 않는다. |

본문 행간 1.6, 라벨 크기·muted 위계, 사진 아래 여백, 쇼룸 검정 섹션 위 여백, 캡션 단순화, 뒷면 반 폭, 정적 라벨 등 디자인 패치의 수정도 반영했다. 수동 텍스트 프리셋, orb·글래스·사진 타일 비율·콘티 서체, 모바일 입력 후 PC 이어하기 정책은 변경하지 않았다. 새 CSS에 word-break 덮어쓰기를 넣지 않았으며, 새 화면 처리는 CSS·토큰을 사용했다.

## 총괄 결정 세 건과 그늘

1. **감성형 상품 포인트:** PT-EMO-01 구성과 `Fit check`를 유지했다. 디테일 사진 및 WO2의 `pointItems` 착용 자르기로 행을 채우는 경로를 보존했다.
2. **SIZE:** 현재 허용된 사실 경로에 확인된 사이즈 이름이 없어 표에 SIZE를 만들지 않는다. 임의의 `product.size` 입력이나 실측 숫자에서 사이즈를 추정하지 않는다.
3. **첫 장 소개:** first의 선택 `intro` 역할을 추가했다. 쇼룸형은 기존 색 목록 아래·고스트 사진 위, 안내형은 기존 첫 장 글 묶음 아래에 둔다. 값이 비면 요소와 높이를 만들지 않는다. 문장 생성은 WO3에 남겼고, 이번에는 제공된 카피만 배치한다.

C6 그늘 허용 결정도 반영했다. 에디터에 기존 텍스트 그늘 렌더가 없음을 먼저 확인했고, 줄별 span과 CSS 그늘을 추가했다. 다운로드는 이 에디터 DOM을 복제해 SVG/이미지로 만드는 기존 경로를 사용한다. SSR·렌더 경로·CSS 검사는 실행했으나 실제 브라우저/PNG 픽셀 비교를 실행했다고 주장하지 않는다.

## 시험 결과

아래 로그는 작업 공간 `.scratch/woq/`에 있다. 선택 실행과 전체 실행의 통과 수는 합산하지 않는다.

| 명령 | 최종 결과 | 로그 |
|---|---|---|
| `node scripts/regenerate-page-template-fixtures.mjs` | **32개 입력의 JS/Python 블록·측정 영역 정확히 일치 후 재생성** | 명령 출력 |
| `pnpm test:frontend` | **4,531 통과, 실패·건너뜀 0** | `frontend-signoff.log` |
| `cd server && /Users/daily/Documents/wearless_studio/server/.venv/bin/pytest -q --ignore=tests/test_personalization.py` | **10,482 통과, 183 건너뜀, 실패·오류 0** | `server-signoff.log` |
| `pnpm build` | **성공** | `build-signoff.log` |
| `git diff --check` | 통과 | 읽기 전용 검사 |

서버 템플릿 선택 실행:

`cd server && /Users/daily/Documents/wearless_studio/server/.venv/bin/pytest -q tests/test_page_template_quality.py tests/test_page_template_assembler.py tests/test_page_template_title_band.py tests/test_page_template_parity.py tests/test_page_templates_catalog.py`

**182 통과**, `server-final-focused.log`. 이후 서버 소스는 바뀌지 않았다. 마지막 DOM 자동 높이·셀러 편집 보완의 프론트 선택 실행은 다음과 같다.

`node --test tests/frontend/page-template-quality.test.mjs tests/frontend/editor-appearance.test.mjs tests/frontend/editor-copy-preservation.test.mjs`

**87 통과**, `frontend-last-focused.log`.

새 시험은 고정 문구 12개(한글·ASCII·혼합·여러 공백·명시 줄바꿈·빈 줄·빈 문자열·긴 어절·이모지·숫자), 실제 도형 폭의 엔진 간 정확한 일치, each/max·정렬·padding 검증, C4~C12·C14·C16, C1 기본값·프로젝트별 기억·저장 실패·연속 섹션, 생성 완료·셀러 편집 보호를 포함한다. C6는 단색·회색·90% 경계·11% 불리한 픽셀·체커보드·독립 줄·crop·실패 기본값·중복 사진 재사용을 합성 픽셀로 검사한다. 줄별 SSR은 실패한 줄에만 그늘 클래스가 붙고, 편집 뒤 과거 결과가 무효가 되는지도 확인한다.

`test_personalization.py`는 지시된 명령에서 제외했다. 183개 건너뜀은 통과로 세지 않았다. 서버 기존 경고 1,043건이 있으며, 빌드의 기존 큰 청크와 의존 라이브러리 PURE 주석 경고는 남는다. 320/360/375/390/414px·WebKit·스크린샷·실제 다운로드 이미지의 화면 검사는 실행하지 않았다.

## 계약 문서에 반영할 차이

- first `textSlots`에 선택 `intro` 추가: `source: ai`, 빈 기본값, `maxChars: 180`, `optional: true`; 쇼룸·안내형에서 `hideEmpty` 배치. required 역할에는 추가하지 않았다.
- 레이아웃 옵션 확장: `hug/minW/maxW/hugMode`, 숫자 또는 `[padY,padX]`, `rule.w: text`, factTable `layout`, list `marker`, pointRows `valign/photoRadius`, 캡션 `swatchDot`, MD panel `splitParagraphs`와 제목 `firstItemOnly`.
- shared catalog에 기존 콘티 견본과 같은 `swatchHex` 추가. schemaVersion과 저장 API의 `blocks: list`는 유지했다.
- 컷 결과에 기존 `overlayTextColors`와 함께 `overlayTextLines`를 추가했다. 측정 계획에는 `lines[]`가 추가되며 줄 키는 `<groupKey>:<bareTextIndex>:<lineIndex>`다.
- 저장 텍스트에 선택 `contrastLines`와 `contrastFor`가 추가된다. 그룹 키는 저장하지 않는다. 그늘은 CSS 토큰 기반이며 실제 사진 픽셀 대비가 부족한 줄에만 적용된다.
- MD의 원래 `body` 역할은 유지하되 `paragraphIndex/paragraphCount`로 여러 요소를 구분한다. 세 말풍선 한도 이후의 문단은 셋째에 합치는 구현 판단을 했다.
- 분리된 특징 표 셀은 `copyRole: facts`를 유지하면서 `factLabel/factPart`로 구분한다. 완료 병합은 이 식별자와 문단 번호를 사용한다.
- 간격은 같은 블록 안 잠긴 사각형 `pageSectionGap`으로 표현한다. C12의 같은 색 판 생략은 실제 단색 바탕에서만 적용한다.
- 생성 중 `genTemplateBaseline`으로 자동 배치와 셀러 편집을 구분하며 완료 병합에서 제거한다. 글 내용·서식·상자가 생성 기준과 같을 때의 DOM 높이 보정만 자동으로 취급한다. 셀러의 블록 높이·위치·글 편집은 보호한다.
- 어절을 강제로 나누던 높이 추정 대신 공백을 보존하는 어절 줄바꿈을 공유한다. 가용 폭보다 긴 어절은 글자 크기를 줄인다. 서버 canvas 측정은 사용하지 않는다.

## 바꾼 파일

작업 시작 manifest 대비 **23개**이며, 이 보고서를 더하면 24개다. WO1·WO2 누적 git diff 전체와 구분했다.

구현·데이터·설명·재생성 도구 15개:

- `server/app/data/page_templates_v1.json`
- `server/app/agents/page_template_assembler.py`
- `server/app/agents/page_template_contrast.py`
- `server/app/agents/page_templates.py`
- `server/app/workers/detail_page_job.py`
- `src/lib/pageTemplates/assemble.js`
- `src/lib/pageTemplates/catalog.js`
- `src/lib/pageTemplates/README.md`
- `src/lib/editorWaitSkeleton.js`
- `src/features/editor/Editor.jsx`
- `src/features/editor/TemplateContrastText.jsx`
- `src/features/editor/editorViewMode.js`
- `src/styles/features.css`
- `src/styles/tokens.css`
- `scripts/regenerate-page-template-fixtures.mjs`

시험·고정 입력 8개:

- `tests/fixtures/page_templates_cases.json`
- `tests/fixtures/page_templates_quality.json`
- `tests/frontend/page-template-assemble.test.mjs`
- `tests/frontend/page-template-quality.test.mjs`
- `tests/frontend/page-template-contrast-render.test.mjs`
- `server/tests/test_page_template_assembler.py`
- `server/tests/test_page_template_title_band.py`
- `server/tests/test_page_template_quality.py`

## 총괄·디자인 세션 화면 확인 자리

- 세 템플릿 × 모노톤/포인트: 이어보기 최초 진입, 프로젝트별 떼어보기 기억, 같은 섹션 경계 0px, 섹션 사이 8px·그림자, 프레임 끌기 삽입 위치.
- 감성형 첫 장과 후기·FAQ 머리: 짙은 옷/흰 벽 혼합, 줄별 흰/검정 선택, 부족한 줄만 그늘, 글을 고친 뒤 낡은 그늘이 남지 않는지. 에디터와 실제 PNG/ZIP 결과를 나란히 비교한다.
- 추천 체크와 글 사이, 후기·질문/답·MD 말풍선 폭과 좌우 정렬, MD 1~3개 및 긴 문단, Front/Side 알약, 혼용률 밑줄, 색 이름·흰색을 포함한 10px 색 점.
- 쇼룸 검정 원단/포인트 및 안내형 회색 핏 앨범 끝의 캔버스색 간격. 핏 앨범 마지막 카드와 팁 사이 40px, 팁 padding 26.
- 쇼룸 stacked/안내형 columns 특징 표, 점과 첫 줄 가운데, 사진 옆 글 가운데·모서리, 확인되지 않은 SIZE 숨김.
- 첫 장 intro가 없는 정상 생성의 빈 자리 없음과, 테스트 카피를 넣은 쇼룸/안내형의 위치. WO3 카피가 연결된 뒤 실제 문장 길이도 다시 본다.
- 생성 대기에서 글·도형·소개가 완성본으로 바뀌는 흐름, 자동 높이 보정, 셀러가 바꾼 글·위치·블록 높이의 보존, WO2 착용 자르기.
- 폭 **320, 360, 375, 390, 414px**, Chrome·WebKit의 어절 중간 줄바꿈·가로 넘침 자동 검사와 스크린샷. 실제 모바일·태블릿은 기존 사진 입력 후 PC 이어하기 정책도 유지되는지 확인한다.
