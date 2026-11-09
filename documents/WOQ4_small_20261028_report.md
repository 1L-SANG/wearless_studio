실제 모델 / effort: 이 세션에서 런타임 식별값이 노출되지 않아 확인 불가. 오너 요청값은 `gpt-6.1-sol / max`이며, 이를 실제값으로 단정하지 않는다.

WOQ4 화면 확인에서 나온 작은 수정 넷 — 지시일 2026-10-28, 검증일 2026-10-29

WOQ3 결과 위에서 네 항목을 수정했다. 아래 시험 결과는 이번 작업에서 직접 실행한 결과다.

**항목별 변경**

| 항목 | 반영한 내용 |
|---|---|
| 1. MD 프로필 원 | 공통 카탈로그 `T01-D07`의 여백을 `[29.5, 24]`로 조정했다. 기존 98×101px 바탕은 98×98px 원이 되고, 글 칸은 36→50px로 넓어졌다. 24px 글자 크기와 39px 한 줄 높이를 유지하며 원의 가운데에 놓는다. `nowrap`으로 감추지 않았다. 실제 번들 Pretendard 400의 `MD` 폭은 37px로, 새 칸에 반올림 여유까지 들어간다. 이름이 없으면 `MD`, 있으면 기존 첫 Unicode 코드 포인트 한 글자 규칙을 유지한다. 한글·라틴·보조평면 글자와 이름이 `MD`인 경우도 두 엔진에서 시험했다. |
| 2. 전체 보기 창 | 기존 네이티브 `dialog`를 유지했다. 전역 `* { margin: 0 }`을 이기도록 `position: fixed; inset: 0; margin: auto`를 명시해 화면 양 축의 가운데에 놓는다. `role="dialog"`, `aria-modal="true"`, 고유 제목 연결을 명시했다. 열린 뒤 닫기 단추에 초점을 주고, 닫을 때 실제로 창을 연 단추에 초점을 돌린다. Esc의 native `cancel` 처리·바깥 클릭 닫기·안쪽 클릭 유지와 `.modal-actions` 알약 규칙을 보존했다. |
| 3. 견본 이동 | 이동 거리는 `그림 높이 − 카드 높이`, 이동 시간은 그림·카드 높이에 관계없이 6,000ms다. `linear`로 움직이고 끝에 800ms 정지 시간을 둔다. 호버·초점이 남아 있으면 끝을 유지하고, 해제하면 맨 위로 복귀한다. 크기 변경 시에도 6초를 적용한다. 움직임 줄이기 설정과 터치 전용 기기의 호버 제한을 유지했다. |
| 4. 전체 보기 글 바탕 | 실제 단추에 `previewButton` 클래스를 붙였다. `--glass-bg` 반투명 흰 바탕, `--fg-1` 글 색, `--ring-strong` 1px 외곽선을 쓴다. 기존 `.modal-actions`의 알약 모양과 44px 최소 높이를 유지한다. |

**시험 결과**

| 실행 위치 / 명령 | 결과 | 로그 (`.scratch/woq4-20261028/` 기준) |
|---|---|---|
| 루트: `node --test tests/frontend/page-template-small.test.mjs tests/frontend/page-template-preview.test.mjs` | **22 통과**, 실패·건너뜀 0 | `frontend-green.log` |
| `server/`: 지정 venv의 `pytest -q tests/test_page_template_small.py` | **12 통과**, 실패·건너뜀 0 | `server-green.log` |
| 루트: 지정 venv Python으로 `node scripts/regenerate-page-template-fixtures.mjs` | **40사례 JS/Python 전체 블록·사진 위 글 측정 영역 정확히 일치**, 그 뒤 기대값 갱신 | `parity-regenerate.log` |
| 루트: `pnpm test:frontend` | **4,631 통과**, 실패·건너뜀 0, 종료 코드 0, 86.27초 | `frontend-all.log` |
| `server/`: `/Users/daily/Documents/wearless_studio/server/.venv/bin/pytest -q --ignore=tests/test_personalization.py` | **10,543 통과·183 건너뜀**, 실패·오류 0, 종료 코드 0, 381.00초 | `server-all.log` |
| 루트: `pnpm build` | **성공**, 종료 코드 0, Vite 17.92초 | `build.log` |

회귀시험을 먼저 추가해 수정 전 프런트 19개·서버 12개가 해당 요구사항으로 실패하는 것을 확인했다. 수정 후 위 집중 시험이 모두 통과했다. 이 예상 실패 기록은 `frontend-red.log`, `server-red.log`에 보존했다.

원 안 글의 한 줄 높이·폭·원과의 중심 일치, 실제 번들 글꼴의 폭, 모달의 가운데 배치 CSS·접근성 속성·제목 연결·초점 이동과 복귀·닫기 경로, 6초 계산과 애니메이션 인수·크기 변경·정리·움직임 줄이기, 단추의 실제 바탕 클래스·토큰 선언을 검사했다.

기존 parity 36개 입력과 사진 위 글 측정 영역은 모두 보존했다. 셀러 이름 네 사례를 추가해 총 40개로 만들었다. 기존 기대값 변경은 15개 감성형 사례의 프로필 원 높이와 글 칸 x/y/폭에만 한정된다. 재생성 전에 두 엔진의 전체 결과가 같은지 검사했고, 전체 시험은 저장된 기대값을 별도로 읽어 다시 검증했다.

프런트에는 WOQ3 로그에도 있는 React SSR `useLayoutEffect`·DOM 속성 경고 17줄이 있다. 빌드에는 의존성 PURE 주석, 정적/동적 혼합 import, 500kB 초과 청크 경고가 있다. 서버에는 Python 3.12 sqlite 날짜 어댑터와 Pillow `getdata` 폐기 예고 등 1,043개 경고가 있다. 서버 건너뜀은 183개이며 `test_personalization.py`는 지시대로 제외했다.

**바꾼 파일**

구현·개발 문서 5개:

- `server/app/data/page_templates_v1.json`
- `src/features/analysis/PageTemplatePicker.jsx`
- `src/features/analysis/PageTemplatePicker.module.css`
- `src/lib/pageTemplates/previewMotion.js`
- `src/lib/pageTemplates/README.md`

시험·고정 입력 4개:

- `tests/frontend/page-template-small.test.mjs` — 새 파일
- `tests/frontend/page-template-preview.test.mjs`
- `server/tests/test_page_template_small.py` — 새 파일
- `tests/fixtures/page_templates_cases.json`

보고서: `documents/WOQ4_small_20261028_report.md`. 합계 10개다. 빌드 산출물 `dist/`와 시작 시점 사본·해시·로그 `.scratch/woq4-20261028/`는 별도다.

**보존·검토와 검증 한계**

시작 시점의 2,602개 파일 해시와 대조했다. 위 구현·시험·보고서 10개 외의 소스·시험·문서·정적 자산은 바뀌지 않았고 삭제된 파일도 없다. 기존 WOQ3 파일을 되돌리지 않았고, 공유 카탈로그의 나머지 값과 앞 작업의 미커밋 결과를 보존했다. 별도 읽기 전용 코드 검토에서도 Critical/Important/Minor 지적이 없었다.

이 작업 공간에는 `CLAUDE.md`가 없다. 이번 지시서와 기존 JSX/CSS 스택·색 토큰·공통 알약 규칙을 따랐으며 새 인라인 스타일이나 의존성을 추가하지 않았다.

사용자 지시대로 브라우저를 실행하지 않았다. 실제 화면에서의 dialog 위치·좁은 화면 여백·native 초점 가두기·Esc와 배경 이벤트 전달, 브라우저 글꼴의 픽셀 줄바꿈·시각적 중심·플랫폼별 emoji 대체, 실제 애니메이션의 체감 부드러움은 실측하지 않았다. 이번 검증은 CSS·실제 JSX와 DOM/애니메이션 경계·번들 글꼴 폭·두 엔진 좌표와 전체 결과까지다. 이 부분의 플랫폼별 차이는 화면 검증의 한계로 남는다.

작업은 지정 작업 공간 안에서 수행했다. git 쓰기, 메인 프로젝트 수정, 유료 API 호출, 결과 파일·브라우저·터미널 탭 자동 열기를 하지 않았다. 지정된 메인 프로젝트의 서버 venv는 읽기·실행에만 사용했다.
