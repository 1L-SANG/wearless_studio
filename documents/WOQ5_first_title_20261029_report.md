실제 모델 / effort: 이 세션에서 런타임 모델 ID와 effort가 노출되지 않아 확인 불가. 오너 요청값은 `gpt-6.1-sol / max`이며, 이를 실제값으로 단정하지 않는다.

WOQ5 감성형 첫 장 글 위아래 선과 글 자리 대체 — 2026-10-29

WOQ4 작업 공간을 이어서 수정했다. 아래 시험 결과는 독립 검토의 공백 줄 수정까지 포함한 최종 코드에서 이번 작업 중 직접 실행한 결과다.

**항목별 변경**

| 항목 | 반영 내용 |
|---|---|
| 1. 위아래 선 | `P01-D09`의 공통 JSON 노드에 가로 56px·세로 1.5px 선 두 개를 넣었다. 영문 라벨 위와 색 이름 아래에 각각 18px 간격을 두며, x=472로 가운데 정렬한다. 두 엔진의 `rule` 해석기가 `onPhoto` 글색을 선의 채움색으로 사용한다. 기준은 1000px 캔버스다. |
| 2. 자리 선택 | 첫 장 그룹의 데이터에 `center → top → bottom`, 사진 높이의 8% 여백을 둔다. 선을 포함한 전체 묶음을 옮기며 줄바꿈 후 실제 높이를 사용한다. 서버는 각 자리의 실제 줄 영역을 C6와 같은 원본 픽셀·하위 10% 대비로 측정한다. 한 글색으로 모든 줄이 3:1을 만족하는 첫 자리를 고른다. |
| 3. 모두 부족한 경우 | 각 글색의 가장 낮은 줄 대비를 구해 더 높은 글색과 자리를 고른다. 세 자리 모두 부족하면 이 점수가 가장 높은 자리를 쓰고, 고른 글색으로 3:1에 못 미치는 줄에만 기존 옅은 그늘을 준다. 자리 점수가 같으면 가운데·위·아래 순서를 유지한다. |
| 4. 컷 결과와 두 엔진 | 생성 직후 기존 `_image_metrics` 경로가 `overlayTextPositions[groupKey]`를 글색·줄 측정값과 함께 컷 결과로 넘긴다. JS/Python 조립기는 이 값을 읽어 글과 두 선을 같은 자리로 확정한다. 줄의 글색도 그룹 글색을 따른다. 미편집 대기 뼈대는 완료 병합에서 서버의 최종 배치를 받아들인다. |
| 5. 범위와 기본값 | 자리 대체는 `P01-D09`에만 적용한다. 다른 사진 위 글은 기존 줄별/그룹별 글색과 좌표를 유지하고 자리 메타데이터를 무시한다. 목·대기는 가운데 기본값이다. 자리 값이 없거나 잘못됐으면 가운데, 이미지 측정이 실패하면 가운데·흰 글·그늘 없음으로 둔다. |

첫 장의 그룹 점수는 **한 글색의 줄별 대비 중 최솟값**으로 정했다. 줄마다 흰색·검정을 달리 쓰면 가운데를 통과시키는 방식은 이 첫 장에 적용하지 않는다. 따라서 두 선과 글 묶음의 글색이 일치한다. 기존 C6의 3:1/90% 기준은 유지했다. 빈 줄과 공백·탭만 있는 줄은 대비 판정에서 제외하되 줄 번호와 그늘 없는 메타데이터는 보존한다. 매우 긴 묶음은 사진 안에 들어갈 수 있는 범위에서 가장자리 여백을 줄인다.

**시험 결과**

| 위치 / 명령 | 결과 | 로그 (`.scratch/woq5-20261029/`) |
|---|---|---|
| 시작 상태: 프런트 assembly/parity | 73 통과 | `frontend-baseline.log` |
| 시작 상태: 서버 quality/title-band/parity | 152 통과 | `server-baseline.log` |
| 최종 프런트 first-title/parity | 72 통과 | `frontend-review-fix-green.log` |
| 최종 서버 first-title/title-band/quality/parity | 198 통과 | `server-review-fix-green.log` |
| 지정 Python으로 `node scripts/regenerate-page-template-fixtures.mjs` | 52사례의 전체 블록·측정 영역 JS/Python 정확히 일치 후 기대값 갱신 | `parity-final.log` |
| 루트: `pnpm test:frontend` | **4,662 통과**, 실패·건너뜀 0, 종료 코드 0, 107.72초 | `frontend-all-final.log` |
| `server/`: `/Users/daily/Documents/wearless_studio/server/.venv/bin/pytest -q --ignore=tests/test_personalization.py` | **10,589 통과·183 건너뜀**, 실패·오류 0, 종료 코드 0, 359.30초 | `server-all-final.log` |
| 루트: `pnpm build` | **성공**, 종료 코드 0, Vite 표시 1분 3초 | `build-final.log` |

시험을 먼저 추가해 구현 전 프런트 10개·서버 21개가 요구사항 누락으로 실패하는 것을 확인했다(`frontend-red.log`, `server-red.log`). 빈 줄 때문에 측정 전체가 흰색 기본값으로 돌아가는 추가 문제도 별도 시험 실패를 확인한 뒤 수정했다(`server-blank-line-red.log`). 공백·탭만 있는 줄의 추가 판정 문제도 프런트 3개·서버 3개 실패 후 수정했다(`frontend-whitespace-red.log`, `server-whitespace-red.log`).

검토 수정 전 첫 전체 실행은 프런트 4,659 통과, 서버 10,586 통과·183 건너뜀, 빌드 성공이었다(`frontend-all.log`, `server-all.log`, `build.log`). 위 최종 재실행은 공백·탭 회귀시험과 수정까지 포함한다.

합성 픽셀 시험은 흰/검정 글 각각의 가운데 유지·위 선택·아래 선택, 세 자리 모두 실패했을 때 각각의 최적 자리, 자리 점수 동률, 앞의 합격 자리보다 뒤의 대비가 더 높아도 순서를 지키는 경우, 한 줄만 그늘, 줄마다 반대 글색이 필요한 경우, cover·명시적 crop의 원본 픽셀 매핑, 빈 줄과 잘못된 이미지까지 검사한다. JS 시험은 실제 선 치수·간격·글색, 세 자리의 좌표, 잘못된 자리 값, 여러 줄 묶음과 생성 완료 병합을 검사한다.

기존 parity 40개 입력은 모두 보존했다. 실제 서버 합성 픽셀 측정값을 넣은 가운데·위·아래 × 두 글색, 모두 실패한 세 자리, 여러 줄, 잘못된 자리 값, 다른 섹션 무시 사례 등 12개를 추가했다. 기존 사례의 첫 장 외 모든 블록·측정 영역은 WOQ4 기대값과 동일함을 별도로 대조했다.

**바꾼 파일**

구현·개발 문서 7개:

- `server/app/data/page_templates_v1.json` — 첫 장 선 노드·간격·자리 정책.
- `server/app/agents/page_templates.py` — 첫 장 전용 자리 정책 검증.
- `server/app/agents/page_template_assembler.py` — 선색·자리 확정·후보 줄 영역.
- `server/app/agents/page_template_contrast.py` — 줄별 점수·자리/글색 선택·그늘·빈 줄 처리.
- `src/lib/pageTemplates/catalog.js` — Python과 같은 자리 정책 검증.
- `src/lib/pageTemplates/assemble.js` — Python과 같은 선색·배치·후보 영역.
- `src/lib/pageTemplates/README.md` — 메타데이터와 선택 규칙 설명.

시험·고정 입력 5개:

- `tests/frontend/page-template-first-title.test.mjs` — 새 파일.
- `server/tests/test_page_template_first_title.py` — 새 파일.
- `server/tests/test_page_template_title_band.py` — 첫 장 자리 메타데이터 계약 반영.
- `server/tests/test_page_template_quality.py` — 기존 독립 줄색 측정을 다른 오버레이에서 보존.
- `tests/fixtures/page_templates_cases.json` — 기존 40사례 보존·12사례 추가·두 엔진 검증 후 기대값 갱신.

보고서: `documents/WOQ5_first_title_20261029_report.md`. 합계 13개다. 빌드 산출물 `dist/`, 시작 사본·해시·로그·실행 계획 `.scratch/woq5-20261029/`는 별도다.

**보존·검토·검증 한계**

시작 시점의 2,604개 파일을 해시로 기록해 대조했다. 이번 구현·시험·고정 입력·보고서 13개 외의 소스·시험·문서·정적 자산은 바뀌지 않았고 삭제된 파일도 없다. 공유 카탈로그의 변경은 `P01-D09` 내부에 한정된다. 최종 시험 시작 시점의 소스 해시도 따로 기록했다. WOQ4를 포함한 앞 작업의 미커밋 변경을 보존했다.

`requesting-code-review`에 따른 읽기 전용 독립 검토는 `gpt-6.1-sol / max`로 지정해 실행했다. 검토자도 실제 런타임 ID의 별도 확인은 불가능하다고 보고했다. 초기 Critical/Important 지적은 없었고 공백·탭 줄의 측정 문제 한 건이 있었다. 검토자는 Minor로 분류했지만, 빈 줄 때문에 합격한 가운데 자리를 바꾸면 이번 자리 선택 규칙이 어긋나므로 정확성 수정으로 처리했다. 두 엔진의 측정 영역과 서버 선택을 각각 실패→통과로 확인했고, 검토자가 판단을 보류한 항목이나 남긴 미수정 지적은 없다. 기록은 `.scratch/woq5-20261029/review.md`에 있다.

프런트 전체 시험에는 기존 WOQ4 로그와 같은 React SSR `useLayoutEffect`·DOM 속성 경고 17줄이 있다. 빌드에는 의존성 PURE 주석, 정적/동적 혼합 import, 500kB 초과 청크 경고가 있다. 서버에는 Python 3.12 sqlite 날짜 어댑터와 Pillow `getdata` 폐기 예고 등 1,043개 경고가 있다. 서버 건너뜀은 183개이며 `test_personalization.py`는 지시대로 제외했다. 브라우저로 화면을 열지 않았으므로 경고의 실제 화면 영향은 이번에 새로 측정하지 않았다.

이 작업 공간에는 `CLAUDE.md`가 없다. 작업지시서와 기존 JSX/CSS·JSON·Python 스택을 따랐고 새 의존성·화면 문구·인라인 스타일·DB 마이그레이션을 추가하지 않았다.

브라우저는 실행하지 않았다. 실제 사진에서의 시각적 미감, 브라우저 글꼴의 픽셀 줄바꿈과 가는 선의 안티앨리어싱은 화면 실측하지 않았다. 이번 검증은 합성 픽셀, 실제 조립 좌표·줄 메타데이터, 두 엔진의 전체 출력과 기존 완료 병합까지다.

지정 작업 공간 안에서만 수정했다. git 쓰기, 메인 프로젝트 수정, 유료 API 호출, 결과 파일·브라우저·터미널 탭 자동 열기를 하지 않았다. 지정된 메인 프로젝트 서버 venv는 지시된 시험을 위한 실행에만 사용했다.
