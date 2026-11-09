# WO1 구현 결과 — 2026-10-28

실제 모델과 effort의 세부 식별 정보는 세션에 노출되지 않았다. 따라서 요청값
`gpt-6.1-sol / max`가 적용됐다고 확인할 수 없다.

## 구현한 범위

- 공유 JSON 한 벌에 섹션 10종, 템플릿 3종, 1차 디자인 25개, 스킨·글꼴·색 표와
  배치 데이터를 넣었다. 기본 인스턴스는 감성형 10개, 쇼룸형 7개, 안내형 8개다.
- JS/Python 검사기와 토큰 해석기, v4 조립기를 연결했다. 서버 시작 시 카탈로그가
  잘못됐으면 DB 연결 전에 실패한다. v4 이외의 조립 경로는 유지했다.
- 섹션과 칸의 추적 필드, 카피 출처, 사진 재사용·넘침·실패, 반전 블록과 포인트 색,
  기존 자동 정보 및 지정된 공통 주의 문구를 처리한다.
- 감성형 첫 장의 위쪽 글 띠 대비를 로컬 픽셀로 측정한다. 사진의 cover 크롭을
  고려하며 실패하면 흰색이다. 기존 v3 중앙 띠 측정은 유지했다.
- 에디터 정규화와 대기 높이 보정은 `pageSectionKey` 블록을 보존한다. 대기 뼈대에
  분석의 선택 스타일을 넘기고 직접 올린 사진을 생성 대기로 지우지 않게 했다.
- 두 런타임은 같은 입력과 기대 블록을 가진 fixture 27개를 사용한다. 기본 18개
  조합과 카피 대기·끔, 글형 대체, 재사용 실패·원본 삭제, 넘침, SNS, 라이선스를
  검사한다. 일반 2색 사례의 생성 카드 수는 18 / 16 / 19장이다.

## 바꾼 파일 26개

| 구분 | 파일 |
|---|---|
| 공유 데이터 | `server/app/data/page_templates_v1.json` |
| 서버 검사·토큰 | `server/app/agents/page_templates.py` |
| 서버 v4 조립 | `server/app/agents/page_template_assembler.py` |
| 기존 조립 진입점 | `server/app/agents/page_assembler.py` |
| 서버 시작 검사 | `server/app/main.py` |
| 작업자·위쪽 띠 | `server/app/workers/detail_page_job.py` |
| JS 카탈로그 | `src/lib/pageTemplates/catalog.js` |
| JS 토큰 | `src/lib/pageTemplates/tokens.js` |
| JS v4 조립 | `src/lib/pageTemplates/assemble.js` |
| 기존 목 자동 정보 추출 | `src/lib/pageTemplates/autoBlocks.js` |
| 후속 작업 안내 | `src/lib/pageTemplates/README.md` |
| 목 진입점 | `src/mock/db.js` |
| 에디터 분석 전달 | `src/features/editor/Editor.jsx` |
| 에디터 정규화 보존 | `src/lib/storyboardTaxonomy.js` |
| 대기 뼈대 보존 | `src/lib/editorWaitSkeleton.js` |
| 공통 사례 | `tests/fixtures/page_templates_cases.json` |
| 프론트 검사 시험 | `tests/frontend/page-templates-catalog.test.mjs` |
| 프론트 조립 시험 | `tests/frontend/page-template-assemble.test.mjs` |
| 프론트 동일 결과 시험 | `tests/frontend/page-template-parity.test.mjs` |
| 서버 공통 시험 도우미 | `server/tests/page_template_cases.py` |
| 서버 검사 시험 | `server/tests/test_page_templates_catalog.py` |
| 서버 조립 시험 | `server/tests/test_page_template_assembler.py` |
| 서버 동일 결과 시험 | `server/tests/test_page_template_parity.py` |
| 위쪽 띠·작업자 옵션 시험 | `server/tests/test_page_template_title_band.py` |
| 기존 MD 보호 시험 갱신 | `server/tests/test_analysis_md_comment.py` |
| 결과 보고서 | `documents/WO1_templates_20261028_report.md` |

## 시험 결과

프론트 명령은 작업 공간 루트, 서버 명령은 `server/`에서 실행했다.

| 명령 | 결과 |
|---|---|
| `pnpm test:frontend` | **4,522 통과**, 실패·건너뜀 0 |
| `node --test tests/frontend/page-templates-catalog.test.mjs tests/frontend/page-template-assemble.test.mjs tests/frontend/page-template-parity.test.mjs` | **97 통과** |
| `/Users/daily/Documents/wearless_studio/server/.venv/bin/pytest -q --ignore=tests/test_personalization.py` | **10,450 통과, 183 건너뜀**, 실패·오류 0 |
| 아래 서버 선택 명령 | **355 통과**, 실패 0. 위쪽 cover 띠 보완 후 재실행 |
| `pnpm build` | 성공. 500kB를 넘는 청크 경고 있음 |
| `pnpm exec prettier --check src/lib/pageTemplates/*.js tests/frontend/page-template*.test.mjs tests/frontend/page-templates-catalog.test.mjs` | 통과 |
| `git diff --check` | 통과 |

서버 선택 명령:

```sh
/Users/daily/Documents/wearless_studio/server/.venv/bin/pytest -q \
  tests/test_page_templates_catalog.py \
  tests/test_page_template_assembler.py \
  tests/test_page_template_parity.py \
  tests/test_page_template_title_band.py \
  tests/test_page_assembler.py \
  tests/test_analysis_md_comment.py \
  tests/test_detail_page.py \
  tests/test_detail_page_identity_source.py \
  tests/test_identity_scope_wiring.py \
  tests/test_hook_title_layout.py \
  tests/test_detail_cut_preview.py
```

전체 서버 시험의 `tests/test_personalization.py`는 로컬 DB
`127.0.0.1:54322` 연결이 `Operation not permitted`로 거절돼 **105개 setup 오류**가
발생했다. 이 파일을 제외해 나머지를 실행했다. DB 의존 시험은 통과했다고 하지 않는다.
전체 서버 실행 뒤 추가한 cover 측정 경계 사례 두 개는 위의 최종 355개 선택 실행에
포함된다.

기존 프론트의 Python 교차 시험이 작업 공간의 `server/.venv/bin/python`을 사용하므로,
없는 `server/.venv`를 지시받은 가상환경으로 연결했다. 이 링크는 git 제외 대상이며
가상환경 원본 코드는 고치지 않았다.

## 계약·시안과의 관계

- 계약의 내용 규칙을 바꾼 곳은 없다. 계약에 픽셀 좌표가 없는 곳은 시안의 860px
  배치를 1000px 데이터로 옮기고 공통 글 높이 추정 규칙을 사용했다. 화면의 최종
  글꼴 크기·여백·사진 크롭은 총괄의 시각 검사가 남아 있다.
- 계열·글형 디자인의 데이터 ID가 미정이어서 `T03-GUIDE-01`, `T03-TEXT-01`,
  `PT-EMO-TEXT`를 부여했다. 시안의 추가 본문·칩·모델 정보·구매 전 확인은 계약의
  글 구조와 제외 규칙을 우선했다. 두께·비침·신축·안감 등 없는 사실은 만들지 않았다.
- 안내형 FAQ의 추가 위치는 마지막 상품 포인트 뒤, 색상과 제품 바로 앞이다.
  원단 후보가 없을 때 상품 포인트에 세 장까지 배분할 수 있도록 슬롯의 최대값은
  3으로 두고, 원단을 포함한 페이지 전체 한도도 3으로 명시했다. 배분은 WO2 몫이다.
- `notice`는 현재 사실 경로가 없어 `unavailable: true`로 숨긴다. 상품 소개 `facts`는
  확인된 색상·소재·핏을 합친 값이다.
- v4 필드가 v3 정규화로 덮이지 않도록 작업자에서 그 경로를 건너뛴다. 새 v4 저장
  검사·정규화 및 생성 전 거절 규칙은 WO2에 남겼다.
- 기존 MD 시험의 정적 전역 금지 규칙은 새 계약 §7.1과 충돌해, 새 카탈로그와 v4
  조립기만 허용하도록 갱신했다. 기존 AI 프롬프트가 MD 글을 읽지 않는 동작 시험과
  다른 기존 모듈의 금지 검사는 유지했다. AI 카피 생성기 연결은 하지 않았다.

## 남은 위험과 후속 범위

- 브라우저 실행 금지로 실제 화면·미리보기·내보내기와 실사진의 대비는 검사하지
  않았다. 상품명과 색 이름이 긴 경우를 포함해 총괄의 시각 QA가 필요하다.
- Gowun Batang 번들과 Pretendard 800 에디터 굵기 표 변경은 WO3다. serif는 WO1
  활성 스타일 시험에서 사용하지 않았다. 셀러 프로필 조회도 WO3이며 현재는 MD와
  빈 계정 placeholder를 사용한다.
- 늦은 카피가 높이나 추천 항목 수를 바꾸는 경우의 배치·칸 병합은 WO3에서
  처리해야 한다. 완성 조립 결과와 기존 대기 높이 보정의 우회까지 검증했다.
- 콘티 시드·화면·스타일 저장/잠금·v3 제거는 WO2다. 현재 셀러 UI는 기존 경로를
  사용하며, v4 입력을 주었을 때 새 조립기가 동작하는 기반을 구현했다.
- DB 의존 시험 105개는 연결 가능한 환경에서 재실행해야 한다.

git 커밋·브랜치 변경·스테이징, DB 마이그레이션, 브라우저 실행, 유료 API 호출은
하지 않았다. 결과 파일이나 브라우저를 Codex 탭으로 자동으로 열지 않았다.

## 2라운드 — 총괄 리뷰 수정 (2026-10-28 밤)

실제 모델 **gpt-6.1-sol**, reasoning effort **max**. 총괄이 확인한 실행 로그의
`model: gpt-6.1-sol`, `reasoning effort: max`에 근거한다. 위의 1라운드 기록은 당시
기록으로 보존한다. 현재 동작은 이 절을 따른다.

### 네 가지 수정

1. **D86 첫 장 가운데 글.** P01-D09의 영문 라벨·상품명·한 줄 소개·색 이름을
   실제 글 상자 전체의 세로 중심으로 배치한다. 마지막 글의 trailing gap은 중심
   계산에서 제외한다. `firstTitleBand`는 중앙 40%~60%로 바꿨다. 글이 길면 실제
   묶음 높이에 맞춰 가운데에 두고 그 실제 영역을 측정한다. 첫 장과 추가 메뉴의
   `topClear` 우선 배정 및 위쪽 여백 힌트를 제거했으며 검사기도 이 값을 거절한다.
   후기·문답의 `bottomClear`는 유지한다.
2. **사진 위 맨글의 묶음별 대비.** 모든 `overlay.groups`와
   `photoGrid.overlayGroups`에 고유 묶음 ID를 두었다. 두 조립기의 같은 배치 함수가
   글·사진의 실제 좌표를 계산하며, 맨글 영역만 측정 목록에 남긴다. 불투명 패널과
   말풍선 안의 글은 목록에서 제외하고 원래 스킨 글색을 유지한다.
   서버의 새 `page_template_contrast.py`가 생성 직후 해당 영역을 원본 픽셀로 역산해
   평균 상대 명도와 흰색·검정의 대비를 비교한다. cover 및 저장 crop 좌표를 모두
   고려하고, 실패하면 흰색이다. 원단의 위·아래 묶음은 별개로 고른다.
   SNS 재사용 위치와 생성에서 접힌 복제 컷의 위치도 함께 측정한다.
   결과는 컷의 `overlayTextColors["<섹션>:<목적 칸>:<묶음 ID>"]`로 전달하고 최종
   텍스트 색으로 굳힌다. 에디터 블록 저장 필드를 추가하지 않았다. 목과 대기 뼈대는
   측정 결과가 없으므로 데이터 기본 흰색을 사용한다. v3 시그니처 측정은 유지한다.
3. **핏 앨범 목 아래 자르기.** `cropTop`을 **0.12 → 0.25**로 바꿨다. 기존 조립기가
   이미 이 비율을 편집기의 `crop: {ox, oy, iw, ih}`로 바꾸고, 편집기는 이를
   `.el-cropped` 안에서 음수 위치와 확대 크기로 적용하고 있었다. 12%는 목 아래까지
   충분하지 않았다. 현재 목 데이터의 800×1200 착용 대역은 머리 아래가 약 18.4%, 어깨
   시작이 약 19.3%여서 12%에서는 얼굴이 남고, 25%에서는 그 아래부터 보인다.
   총괄이 제시한 미디움 컷 기준 20% 이상을 적용했다. 확대 후 가로도 가운데로
   맞추고 아래쪽 빈 틈이 생기지 않게 crop 좌표를 제한했다.
   원본 위 23%를 얼굴·목으로 표시한 합성 픽셀 시험에서 세 장 모두 그 영역이
   사라짐을 확인했다. 기존 편집기 crop 열기·적용 함수가 같은 좌표를 유지하는지도
   검사했다. 실제 미디움 사진에서 목선이 적절한지는 총괄의 시각 확인이 남는다.
4. **반전 요약 선 정렬.** T06-D01 안쪽 선에 템플릿 정렬을 적용했다. 쇼룸형 선의
   중심은 캔버스 `x=500`이며 안내형은 기존 왼쪽 `x=112`를 유지한다. 안내형 머리의
   짧은 선 `x=56`도 그대로다.

### 2라운드 변경 파일 18개

- `server/app/data/page_templates_v1.json`
- `server/app/agents/page_templates.py`
- `server/app/agents/page_template_assembler.py`
- `server/app/agents/page_template_contrast.py` (새 파일)
- `server/app/workers/detail_page_job.py`
- `src/lib/pageTemplates/catalog.js`
- `src/lib/pageTemplates/assemble.js`
- `src/lib/pageTemplates/README.md`
- `tests/fixtures/page_templates_cases.json`
- `tests/frontend/page-templates-catalog.test.mjs`
- `tests/frontend/page-template-assemble.test.mjs`
- `tests/frontend/page-template-parity.test.mjs`
- `server/tests/page_template_cases.py`
- `server/tests/test_page_templates_catalog.py`
- `server/tests/test_page_template_assembler.py`
- `server/tests/test_page_template_parity.py`
- `server/tests/test_page_template_title_band.py`
- `documents/WO1_templates_20261028_report.md`

### 계약과의 관계

계약 v2 §6.4의 위쪽 고정과 §6.5의 `topClear`는 오너 결정 D86과 이번 지시에 따라
폐기했다. 사진 위 맨글을 모두 묶음별 자동 글색으로 바꾸는 것도 이번 지시에 따른다.
다른 내용 구성·글 구조·생성 장수·v3 동작은 바꾸지 않았다.

### 2라운드 시험 결과

프론트 명령은 작업 공간 루트, 서버 명령은 `server/`에서 실행했다.

| 명령 | 결과 |
|---|---|
| `pnpm test:frontend` | **4,529 통과**, 실패·건너뜀 0 |
| `node --test tests/frontend/page-templates-catalog.test.mjs tests/frontend/page-template-assemble.test.mjs tests/frontend/page-template-parity.test.mjs` | **104 통과** |
| `.venv/bin/pytest -q --ignore=tests/test_personalization.py` | **10,467 통과, 183 건너뜀**, 실패·오류 0 |
| 아래 서버 선택 명령 | **303 통과**, 실패 0 |
| `pnpm build` | 성공. 500kB 초과 청크 경고 있음 |
| `pnpm exec prettier --check src/lib/pageTemplates/*.js tests/frontend/page-template*.test.mjs tests/frontend/page-templates-catalog.test.mjs` | 통과 |
| `git diff --check` | 통과 |

```sh
.venv/bin/pytest -q \
  tests/test_page_templates_catalog.py \
  tests/test_page_template_assembler.py \
  tests/test_page_template_parity.py \
  tests/test_page_template_title_band.py \
  tests/test_page_assembler.py \
  tests/test_detail_page.py \
  tests/test_analysis_md_comment.py
```

공통 fixture는 **28개**다. 원단 한 장의 위·아래 글색이 서로 다른 사례를 더했고,
두 런타임에서 완성 블록과 측정 영역 목록을 모두 비교한다. 단위 시험은 가운데
글 배치, 밝고 어두운 각 묶음, cover·명시적 crop 역산, 측정 실패의 흰색, 불투명
말풍선 제외, SNS 재사용, 중복 생성 접기, 편집기 crop 적용, 쇼룸·안내 선 정렬을
포함한다. 복제 경로 시험에서는 가짜 생성기를 한 번만 호출하면서 두 SNS 위치의
글색을 모두 보존하는 것을 확인했다.

최종 전체 서버 시험은 마지막 복제 경로 수정까지 반영해 실행했다. 지시대로
`test_personalization.py`는 제외했고, 제외·건너뛴 시험은 통과 수에 넣지 않았다.
로그는 작업 공간의 `.scratch/wo1-round2/`에 있다.

### 남은 확인

브라우저·실사진 생성 호출 없이 코드와 합성 픽셀로 검사했다. 실제 미디움 사진의
얼굴·목선, 복잡한 무늬 위 가독성, 크롬·웹킷의 미리보기·내보내기 확인은 총괄 몫으로
남는다. git 쓰기, 브라우저 실행, 유료 API 호출, 메인 폴더 코드 수정은 하지 않았다.
