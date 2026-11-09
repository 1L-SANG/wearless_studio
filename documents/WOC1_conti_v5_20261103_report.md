실제 모델: gpt-6.1-sol · effort: max. 현재 실행 세션의 turn_context에서 확인했다.

# WOC1 콘티 v5 구현 보고서, 2026-11-03

작업 공간: `/Users/daily/.codex/worktrees/templates-mvp/wearless_studio`.
WO1부터 WOQ6까지의 미커밋 변경을 보존했다. git 쓰기, 메인 코드 수정, 브라우저 실행, 유료 API 호출은 하지 않았다. 결과 파일이나 탭도 자동으로 열지 않았다.

두 칸 구성과 겹침 옆 판, run 단위 순서와 삽입, 실제 조립 썸네일, 사진 탐색과 재사용 의존성 처리를 구현했다. `Storyboard.jsx`는 시작 시 3,925줄에서 1,249줄로 줄이고 부품으로 분리했다. 스타일 값은 `StoryboardLayout.module.css`와 `StoryboardPanels.module.css` 두 파일에 모았다.

기준은 PO_LEDGER D130부터 D169, 유효한 D108, D112, D113, D115, D151, D153과 TO_PO_1103.md다. a.html과 연결 코드, v5 그림 네 장, v4 추가 목록과 디자인 그림을 읽었다. 목업 코드를 React로 복사하지 않았다. 색, 자간 등의 마지막 조정과 새 디자인 데이터는 WOC1b와 D167 범위로 남겼다.

## 항목별 결과

| 번호 | 구현과 확인 |
|---|---|
| 1 | 왼쪽 구성 목록과 가운데 무대 두 칸. 옆 판 열림은 무대의 열 폭 계산에 참여하지 않는다. 창이 좁아지면 4칸을 유지하며 축소한다. |
| 2 | 520px 겹침 옆 판. 사진, 디자인, 추가 목록과 후보, 장소 세트 추가와 교체, 스튜디오 교체, 숨은 글 섹션 관리 연결. |
| 3 | 빈 바깥 클릭과 Esc 닫기. 누를 수 있는 바깥 요소는 원래 동작 실행. D165 모드별 섹션 전환, 추가 자리만 변경, 열기 초점과 마지막 외부 트리거 복원. |
| 4 | 상단 머리만 옅게. 왼쪽 목록은 선명하게 유지. 사진 모드의 고르지 않은 사진만 옅게. |
| 5 | 구성컷, 예상 크레딧, 다음: 마네킹컷 선택을 떠 있는 아래 바에 표시. 기존 회전 고리 사용, 옆 판에서 숨김. 템플릿 이름 표시 없음. 기존 저장 플러시와 크레딧 함수 연결. |
| 6 | assemblePage 결과의 좌표와 현재 예시 사진을 SVG로 축소. 80px 너비와 실제 조립 높이 비율. 보이는 순서의 Section 번호, 이름, 컷 수. |
| 7 | 카탈로그상 사진 칸이 없는 intro와 for_you만 목록에서 제외. 저장 run과 페이지는 유지. 사진을 빌려 쓰는 SNS는 목록에 유지. |
| 8 | 고른 섹션에 --ring-flow-stops와 --sp-ang 계열 회전 테두리. reduced-motion에서는 정지. |
| 9 | 여섯 점 손잡이의 네이티브 끌기, 위아래 키, 클릭 이동 메뉴. run 전체 이동, 첫 장 고정, 이동 후 손잡이 초점과 목록 스크롤 복원, 되돌리기. |
| 10 | 섹션 휴지통과 한 건씩 되돌리기. 첫 장에는 휴지통 없음. 순수 함수도 첫 장 삭제 거부. |
| 11 | 보이는 섹션 뒤마다 동그란 더하기와 짧은 양쪽 선. 아래 run 바로 앞 또는 페이지 끝에 삽입. 첫 장 위에는 없음. |
| 12 | 목록 패딩과 scrollbar-gutter로 선과 스크롤바 분리. 전체 보기에서 실제 전체 조립 결과와 자동 안내 블록 표시. 섹션 클릭 편집, 편집으로 돌아가기. |
| 13 | 사진 아래 왼쪽 장소 분류와 오른쪽 n번째 컷. 스튜디오와 제품은 번호만. 조사 분류가 없는 착용 예시는 기타로 표시. |
| 14 | 묶음 이름, 가운데 선, 장소 바꾸기 알약. 묶음 전체 교체. 교체 판의 대표 아래 모든 세트 사진을 항상 표시. |
| 15 | 뺄 수 있는 사진만 모서리 X. 필수 첫 장 슬롯과 SNS 재사용 슬롯은 보호. 첫 장에 나중에 더한 컷과 비슷한 컷은 제거 가능. |
| 16 | 무대의 전신과 상반신 조작 제거. 예시 탐색의 구도 필터로 이동. |
| 17 | 카탈로그 후보가 둘 이상일 때만 검정 섹션 디자인 바꾸기 버튼. 후보 수는 고정하지 않음. |
| 18 | 고른 무대 사진에 2px 안쪽 테두리. |
| 19 | packStageRows로 4칸 배치. 짧은 장소와 색 묶음 보존, 긴 묶음만 이어짐. 추가 칸은 마지막, 12컷에서 숨김. 예비 사진 비침과 빈 사진 카드 제거. |
| 20 | 원본과 기존 비슷한 컷 바로 뒤 삽입. 원본 미리보기 위 흰 막과 원본 컷 번호. 기존 계획, 상속, 생성 장수와 삭제 정책 유지. |
| 21 | 컷 추가는 자동 채움. 장소 세트 선택은 장수 입력 없이 전체 삽입. 스튜디오 세트도 전체. 비슷한 컷을 포함해 12컷 제한. |
| 22 | 사진 판 상단 썸네일과 적용 범위 문구, 중복 안내 상자 제거. 닫기 X와 실제 설정만 표시. |
| 23 | 예시 사진 아래 단일컷과 세트컷, 세트가 있으면 기본 세트컷. 없으면 모드 버튼 없이 단일컷. |
| 24 | 세트 스택과 장수, 한 세트 펼치기, 전체 예시 복귀. 단일컷은 예시 한 장씩. 사진 선택은 해당 블록만 변경. |
| 25 | 조사 장소 분류를 밑줄 탭으로 표시. 전체부터 공원까지, 자연은 더보기. 숨은 장소가 고른 값이면 기본 줄에 노출. |
| 26 | 전체, 전신, 상반신 알약. 하의는 하반신. 예시 없는 구도 비활성. 필터 자체는 저장값을 바꾸지 않고 사진 선택 때 full 또는 medium 저장. |
| 27 | 3×2 페이지와 기존 sb-expage-hit, sb-expages. 좌우 키, 가로 스냅, 숨은 페이지 Tab 제어, 썸네일 재시도와 연속 넘김 시험 보존. |
| 28 | 같은 페이지의 다른 칸이 쓰는 예시도 선택 가능, 이미 사용 배지. 자동 배정의 기존 후보 우선순위 안에서 미사용 예시 우선. 수동 갤러리에 사용 여부 차단 없음. |
| 29 | 고른 예시의 3px 테두리와 체크. 키보드 초점 표시는 별도. |
| 30 | 스튜디오 멤버의 다른 스튜디오 세트로 바꾸기와 묶음 교체. 낱장 편집도 세트 탐색 제공. 호리존 배경 제어는 기존 atomic 저장 경로로 옆 판에 유지. |
| 31 | 34px color-dot과 진한 선택 테두리. 예시 사진, 대상 색상, 매칭 의류 15px. 매칭 단일 선택, 내 사진 교체, 원본 사진 선택과 DetailTargetPicker 유지. |
| 32 | 한 장의 예시 교체 때 spaceGroupId, spaceVariation, spaceSetMemberOrder, setSelectionOrigin, 배경 소속을 비우고 all 참고로 전환. 위치 유지, 연속성에 필요한 나머지 세트 run만 재키. |
| 33 | 10종 모두, wear의 두 쓰임을 포함해 11줄. 대표 조립 미리보기와 이름, 짧은 설명, 동그란 더하기. 기존 singleton은 회색 더하기지만 클릭으로 열기. 글 섹션은 디자인과 빼기. |
| 34 | 목록 선택 뒤 큰 디자인 카드. 카드 클릭 후 삽입, 목록으로 복귀. 목록과 디자인 카드의 끌어 넣기. 디자인 모드 미리보기는 고른 반복 run의 실제 사진을 사용. |
| 35 | 착용 생성예시를 왼쪽 틈에 놓으면 일상 착용컷 2장. 같은 발행 세트가 있으면 회전 세트도 그대로 사용. 그 밖에는 성별과 품목, 모델에 맞는 같은 장소 예시 우선. 다른 구도가 없는 데이터의 예외는 아래에 명시. |
| 36 | 일상 2장, 스튜디오 3장, 색상과 제품은 기존 색상 수와 뒷면 규칙, 상품 포인트 2항목. 세 템플릿의 10종 추가 뒤 조립 이미지 빈 칸 0 시험. 모든 사진이 내 사진인 일상 착용컷 섹션도 카탈로그 레시피로 AI 컷 추가 가능. 다른 섹션은 컷 추가를 제공하지 않는다(2라운드 수정). |
| 37 | 카탈로그에서 일상 착용컷, 스튜디오 착용컷, 원단 설명으로 표시. 디자인 변경 문구 통일. 저장된 이름 재작성 제거. 에디터 미니 미리보기와 새 조립 블록 이름도 확인. |
| 38 | 원본 이동, 삭제, 재삽입, 되돌리기와 사진 설정 변경의 의존성 처리. 새 저장 필드 없음. 재사용 원본을 다른 반복 섹션으로 몰래 바꾸지 않음. 아래 표와 parity 시험 참조. |
| 39 | 섹션 넣기, 빼기, 이동, 디자인 각각 standalone undo. 일반 설정의 기존 묶음과 10초 창, hover 대기와 차례별 복원 유지. |
| 40 | 진입 불러오기, prefetch, 썸네일 게이트, 자동 저장과 충돌 복구, 다음 단계 저장 대기, 크레딧 계산, 재시도와 오류 화면, 비슷한 컷 계획, PC 전용 모바일 정책 유지. 실제 API는 기존 api 서비스만 사용. 생성 작업기 변경 없음. |

## 저장과 빌려 쓰는 사진

DB 컬럼과 `blocks: list` 모양은 그대로다. 새 storyboard 필드는 추가하지 않았다. 섹션은 저장된 `pageSectionKey`의 연속 run이다. 첫 장은 앞에서 고정한다. 새 자리 계산은 `beforeSectionKey`이며, 템플릿의 `after`를 읽는 옛 위치 계산을 없앴다. 두 조립 엔진은 저장 순서를 재정렬하지 않는다.

| 경우 | 저장과 조립 결과 | 복원과 장수 |
|---|---|---|
| 원본 섹션 이동 | 기존 원본 ID와 참조 유지. 원본과 비슷한 컷의 run 전체 이동 | 생성 장수 변화 없음 |
| 같은 종류의 반복 섹션 이동 | 카탈로그의 원본 인스턴스를 계속 사용 | 임의의 다른 반복 섹션으로 바뀌지 않음 |
| 쇼룸 첫 장 고스트 원본 삭제 | `first.ghost`에 독립 제품컷 한 장 저장 | 원본 섹션을 뺀 상태보다 사진 블록 +1 |
| 감성형이나 안내형 첫 장을 P01-D01로 변경 | 그 디자인이 필요로 하는 고스트 재사용 슬롯도 해석 | 원본이 없으면 독립 제품컷 +1 |
| SNS I09-D14의 착용 원본 삭제 | 실제 디자인이 쓰는 sns1만 독립 착용컷으로 채움 | +1. 쓰지 않는 sns2를 새로 만들지 않음 |
| SNS P07-D08의 착용 원본 삭제 | sns1과 sns2를 각각 독립 착용컷으로 채움 | +2 |
| 다른 일상 착용컷 섹션이 남은 상태에서 원본 삭제 | 남은 반복 섹션으로 참조를 바꾸지 않고 독립 컷 생성 | 원본 삭제의 +1 또는 +2 계약 유지 |
| 원본 섹션 다시 넣기 | 독립 대체 컷을 없애고 원래 카탈로그 참조 복구 | 추가 장수 해제 |
| 삭제와 이동, 디자인 변경 되돌리기 | 이전 blocks 스냅샷을 한 건씩 복원 | 원래 사진, 참조, 장수 복구 |
| 재사용 대체 컷을 내 사진으로 변경 | 기존 reuse slot에 mine 허용. 일반 임의 사진을 글 run에 넣는 것은 허용하지 않음 | AI 생성 장수에서 제외, 사진 블록 수 유지 |
| fit 원본 삭제, 옆모습 변경, 내 사진 전환 | 다른 허용 AI fit 앞면 또는 뒷면으로 wornCrop 연결 변경 | 새 AI 컷 추가 없음 |
| 허용 fit 원본이 전혀 없음 | 해당 wornCrop 항목만 제거 | 다른 글과 사진, 다른 포인트는 유지 |
| fit 원본 다시 넣기 | 카탈로그 crop 규칙으로 누락 항목 복구 | 새 crop용 생성 컷 없음 |
| 포인트의 AI 디테일을 내 사진으로 변경 | 유효하지 않은 detail pointItem 참조 제거, 내 사진 자체는 유지 | 잘못된 AI 참조로 저장이 거절되지 않음 |

원본 변경 때도 같은 의존성 함수를 거치므로 저장 직전 옆모습을 앞면 crop 원본으로 남기는 오류를 막는다. SNS 필수 재사용 칸에는 삭제나 비슷한 컷 추가를 제공하지 않아 슬롯이 재생성되거나 저장 검증이 실패하는 동작을 막았다. 첫 장의 필수 슬롯은 보호하지만 나중에 추가한 비슷한 컷은 뺄 수 있다. 장수와 예상 크레딧은 새 사진 블록을 기존 계산기에 전달한다. 기존 동일 생성 설정 중복 계산 규칙은 바꾸지 않았다.

## 시안과 다른 점, 선택한 해석

- 카탈로그의 현재 후보만 그린다. 시안의 49개 대안이나 고르기 전용 조절판은 제품에 넣지 않았다. 1라운드에서는 디자인 이름이 없는 후보를 디자인 1, 디자인 2처럼 표시했다. 2라운드에서 25개 화면 이름과 사진 칸 수 필터로 바꿨다. D167 데이터가 오면 후보 수에 맞춰 그대로 동작한다.
- 썸네일은 목업 레이아웃 그림 대신 실제 `assemblePage` 좌표를 그린다. 아직 생성 전이므로 현재 예시와 원본 사진으로 채운다. 글 줄 높이와 crop, 이미지 둥근 모서리도 조립 값으로 처리한다.
- 1라운드에서는 카피라이팅 스위치를 아래 바에 유지했다. 2라운드에서 제거하고 v4 진입 때 켜짐을 저장하며, 다음 단계는 그 저장 성공을 기다리도록 고쳤다.
- D169의 끌어 넣기는 착용 예시가 대상이다. 제품 예시로 일상 착용컷을 만들 수 없으므로 제품 갤러리에는 잘못된 끌기 표시를 제공하지 않는다.
- **다른 구도 자산이 없는 예외:** 같은 세트가 없으면 현재 상품, 성별, 모델에 맞는 같은 장소 분류의 다른 구도를 우선한다. 조건에 맞는 다른 사진이 없거나 장소 분류가 미확정이면 원본 예시를 한 번 더 사용해 두 개의 유효한 사진 칸을 유지한다. 다른 성별이나 품목 사진을 끼워 넣거나 새 자산을 만들지 않는다. 현재 카탈로그를 성별과 품목 조합별로 점검하면 같은 예시를 반복하는 조합 15건, 같은 shot 조합 21건이다. 같은 자산이 여러 품목에 적용되는 중복 조합을 포함한 수다. 여성 상의의 `ex_styling_women_top_medium_snapshot_02`, `ex_styling_women_top_full_mia_cafe_snapshot_01` 등이 화면 확인 대상이다. 이 경우까지 서로 다른 구도 두 사진을 보장하려면 해당 장소의 짝 예시나 확정 장소 분류가 필요하다. 상세 목록은 `.scratch/woc1-20261103/companion-audit.json`에 기록했다.
- 조사 장소가 미확정인 예시는 기타로 표시한다. 사진을 보고 임의의 장소 분류를 만들지 않았다.
- 브라우저를 실행하지 않았으므로 PNG 일치, 실측 폭, Safari의 끌기와 포커스, 실제 스크롤 겹침을 통과했다고 주장하지 않는다. 아래 장면을 총괄이 샌드박스 밖에서 확인해야 한다.

## 시험

최종 실행 결과를 아래에 기록했다. 브라우저와 유료 모델 없이 수행했다.

| 명령 | 결과 |
|---|---|
| `pnpm test:frontend` | 4,809 passed, 0 failed, 53,958ms. 종료 코드 0 |
| `server/`에서 `/Users/daily/Documents/wearless_studio/server/.venv/bin/pytest -q --ignore=tests/test_personalization.py` | 10,696 passed, 183 skipped, 1,043 warnings, 298.09초, 종료 코드 0. 지정한 personalization 파일은 제외 |
| `pnpm build` | 성공, 16.68초. 종료 코드 0. 큰 chunk와 정적/동적 import 경고 있음 |
| `node scripts/regenerate-page-template-fixtures.mjs` | JS와 Python 완전 일치 확인 뒤 79개 fixture 갱신 |
| `node --test tests/frontend/page-template-v5-parity.test.mjs` | 1 passed. 세 템플릿마다 씨앗 7, 19, 41, 83의 12개 섞은 순서와 대체 사진 6개, 총 18개 입력 비교 |

프런트 시험에는 섹션 run 이동, 첫 장 보호, 숨은 글 자리, 삽입 위치, 끝 삽입, singleton, 디자인 run 전체 변경, 재사용 복원, 유효한 드래그 짝, 세트 이탈, 실제 JSX 초점과 이벤트, 4칸 배치, 12컷, 예시 페이지 탐색을 포함했다. 세 템플릿의 10종 추가 뒤 조립된 사진 빈 칸 0을 확인했고, 각 카탈로그 디자인으로 바꿨을 때의 필수 사진도 확인했다. 여성과 남성의 지원 품목 조합에서 세 템플릿의 일상 착용컷 추가는 모두 full과 medium으로 시작함을 별도 점검했다.

첫 전체 프런트 실행은 4,789개 중 112개 실패했다. 옛 컴포넌트 경로와 대체된 화면 결정을 읽는 소스 시험을 실제 새 부품과 동작 시험으로 옮겼다. 확인된 진입 보조 함수 누락도 수정했다. 첫 서버 실행의 `_v4_slot` 변경 오류는 원인을 고치고 전체 재실행으로 확인했다. 돌리지 않은 시험이나 최초 실패를 통과로 적지 않았다.

검토 지침에 따른 별도 읽기 전용 코드 리뷰를 한 번 받았고, 지적된 저장과 상호작용 문제는 재현 시험의 실패와 수정 후 통과로 처리했다. 검토자가 코드를 수정하거나 브라우저를 실행하지 않았다. 마지막에는 긴 목록의 드래그 스크롤, 이동 손잡이 초점, 옆 판 전환 후 최신 트리거 초점 복원도 추가 확인했다.

전체 출력은 `.scratch/woc1-20261103/frontend-final.log`, `server-final.log`, `build-final.log`에 보관했다. 빌드의 큰 chunk와 정적/동적 import 중복 경고는 오류와 구분한다.

## 총괄 화면 확인 경로

이 작업에서는 개발 서버와 브라우저를 켜지 않았다. 총괄이 이 작업 공간에서 `pnpm dev:mock --host 127.0.0.1 --port 5173`을 실행한다. 새 비공개 창을 쓰면 이전 목 상태의 영향을 줄일 수 있다. 목 모드는 실제 생성 호출을 하지 않는다.

- 감성형: `http://127.0.0.1:5173/create/storyboard?mockTemplate=emo`
- 쇼룸형: `http://127.0.0.1:5173/create/storyboard?mockTemplate=show`
- 안내형: `http://127.0.0.1:5173/create/storyboard?mockTemplate=guide`

기존 목의 직접 진입 가드가 시드 프로젝트를 만든다. 이어하기 선택이 나오면 계속하기로 들어간다. 새 템플릿 상태는 새 탭 또는 새 창에서 해당 쿼리로 열어 만든다. 실제 http 모드의 저장된 프로젝트에 아래 확인용 변경을 만들 필요는 없다.

| 장면 | 상태 만드는 순서와 확인 |
|---|---|
| 기본 두 칸 | 세 주소를 각각 새 상태로 열고 글 섹션이 목록에 없는지, 첫 장이 고정인지 확인 |
| 사진과 옆 판 폭 | 일상 착용컷 클릭, 사진 클릭. 열기 전후 사진 폭 비교. 창을 줄여도 4칸 유지, 목록은 흐리지 않는지 확인 |
| 옆 판 전환과 초점 | 사진 판에서 다른 사진, 다른 섹션, 빈 배경, Esc 차례로 조작. 디자인 판에서는 다른 섹션 클릭 때 판이 유지되는지, 닫은 뒤 마지막 연 버튼으로 초점이 돌아오는지 확인 |
| 예시 탐색 | 세트컷 기본, 스택 펼치기, 전체 예시, 단일컷, 3×2 넘김, 좌우 키, 자연 탭의 더보기, 상반신과 하반신 필터 확인 |
| 사용 예시 다시 고르기 | 두 사진에 같은 예시를 선택. 이미 사용 배지가 있어도 선택되고 해당 사진만 바뀌는지 확인 |
| 세트 이탈 | 세트 한 장을 다른 예시로 변경. 위치 유지와 장소 라벨 변경, 나머지 묶음 설정 유지, 저장 후 다시 열기 확인 |
| 장소와 스튜디오 | 묶음 머리에서 교체 열기. 대표 아래 모든 세트 사진, 장수 선택 없음, 스튜디오 앞옆뒤 전체 교체와 배경 제어 확인 |
| 삽입과 글 자리 | 두 섹션 사이 추가, 후보 선택. 새 섹션이 아래 run 바로 앞에 놓이는지 확인. 추가 중 다른 틈을 눌러 디자인 후보가 유지되는지 확인 |
| 글 섹션 관리 | 추가 목록에서 회색 더하기의 상품 소개 또는 추천 대상 클릭. 디자인 후보와 빼기, 되돌리기 확인 |
| 끌어 넣기 | 추가 목록 줄과 디자인 카드, 착용 예시를 왼쪽 틈에 각각 놓기. 예시는 두 장, 같은 세트가 있으면 그 세트인지 확인. 긴 목록 가장자리와 휠 스크롤 확인 |
| 순서와 되돌리기 | 여섯 점 손잡이 끌기, 초점 후 위아래 키, 클릭 메뉴. 첫 장 위 금지, 한 건씩 되돌리기와 옮긴 손잡이 초점 확인 |
| 실제 썸네일과 전체 보기 | 사진과 디자인을 바꾸고 왼쪽 썸네일 변경 확인. 전체 보기에서 글 섹션과 자동 안내까지 이어지는지, 섹션 클릭 복귀 확인 |
| 비슷한 컷과 한도 | 원본에 여러 번 추가, 바로 뒤 순서와 흰 막 확인. 첫 장에 더한 비슷한 컷 제거. 12컷에서 추가 숨김. 자식이 많은 세트를 교체해 12컷 초과 후보 비활성 확인 |
| SNS 재사용 | 일상 원본이 있는 상태에서 SNS 추가. 원본 이동, 삭제, 재삽입, 각각 되돌리기. SNS 칸이 빈 사진이 되지 않고 추가 장수가 아래 바에 반영되는지 확인 |
| 첫 장 고스트 | 쇼룸 색상과 제품 섹션 삭제. 첫 장 고스트 유지와 +1, 다시 넣어 복구. 감성형에서는 2칸인 P01-D01 후보가 제외되고 1칸 후보만 보이는지 확인(2라운드) |
| 착용 자르기 | 원본이 필요한 포인트의 fit 섹션을 빼거나 앞면을 옆모습/내 사진으로 바꾸기. 다른 허용 원본 연결, 원본이 전혀 없으면 그 crop 항목만 사라지고 다시 넣으면 복구되는지 확인 |
| 다른 설정 | 디테일 촬영 대상과 원본, 34px 색상, 매칭 의류, 내 사진으로 바꾸기, 카피라이팅 스위치가 없고 켜짐 저장과 다음 단계 저장 대기 확인(2라운드) |
| 겹침과 브라우저 | 1280, 1440, 1920 폭, Safari, 세로가 낮은 창에서 목록 스크롤바, X, 고리, 아래 바, 작업 리본과 되돌리기 알림의 겹침 확인 |
| 데이터 예외 | 위 여성 상의 예시를 끌어 같은 장소의 다른 구도가 없는 대체 규칙을 확인. 별도 짝 예시 공급 여부를 총괄이 판단 |

## 이번 실행에서 바꾼 파일

### src/features/storyboard

- `src/features/storyboard/Storyboard.jsx`
- `src/features/storyboard/StoryboardContinue.jsx`
- `src/features/storyboard/StoryboardControls.jsx`
- `src/features/storyboard/StoryboardDrawer.jsx`
- `src/features/storyboard/StoryboardExampleGallery.jsx`
- `src/features/storyboard/StoryboardLayout.module.css`
- `src/features/storyboard/StoryboardPageList.jsx`
- `src/features/storyboard/StoryboardPanels.module.css`
- `src/features/storyboard/StoryboardPhotoPanel.jsx`
- `src/features/storyboard/StoryboardPreview.jsx`
- `src/features/storyboard/StoryboardSectionPicker.jsx`
- `src/features/storyboard/StoryboardSpaceSetPanel.jsx`
- `src/features/storyboard/StoryboardStage.jsx`
- `src/features/storyboard/storyboardPreview.js`
- `src/features/storyboard/SectionAddMenu.jsx` 삭제
- `src/features/storyboard/SpaceSetHoverPreview.css` 삭제
- `src/features/storyboard/SpaceSetHoverPreview.jsx` 삭제
- `src/features/storyboard/StoryboardTextCard.jsx` 삭제
- `src/features/storyboard/TemplateSectionBand.jsx` 삭제
- `src/features/storyboard/TemplateSections.module.css` 삭제
- `src/features/storyboard/storyboardOverlayTop.js` 삭제
- `src/features/storyboard/storyboardSectionPresentation.js` 삭제

### src/lib/pageTemplates

- `src/lib/pageTemplates/README.md`
- `src/lib/pageTemplates/assemble.js`
- `src/lib/pageTemplates/catalog.js`
- `src/lib/pageTemplates/photoActions.js`
- `src/lib/pageTemplates/sectionActions.js`
- `src/lib/pageTemplates/sectionDependencies.js`
- `src/lib/pageTemplates/sectionRuns.js`
- `src/lib/pageTemplates/seed.js`
- `src/lib/pageTemplates/slots.js`
- `src/lib/pageTemplates/storyboardGallery.js`
- `src/lib/pageTemplates/storyboardStage.js`
- `src/lib/pageTemplates/validateStoryboard.js`

### tests/frontend

- `tests/frontend/compose-mode-location.test.mjs`
- `tests/frontend/detail-recipe-availability.test.mjs`
- `tests/frontend/example-gallery-paging.test.mjs`
- `tests/frontend/example-owned-pose-direction.test.mjs`
- `tests/frontend/generation-gallery-direction.test.mjs`
- `tests/frontend/genexamples-autofill.test.mjs`
- `tests/frontend/helpers/storyboardComponents.mjs`
- `tests/frontend/helpers/storyboardPhotoPanelHarness.mjs`
- `tests/frontend/helpers/storyboardStageHarness.mjs`
- `tests/frontend/horizon-background-control.test.mjs`
- `tests/frontend/horizon-set-all-reference.test.mjs`
- `tests/frontend/identity-scope.test.mjs`
- `tests/frontend/outer-closure.test.mjs`
- `tests/frontend/page-template-interactions.test.mjs`
- `tests/frontend/page-template-screen.test.mjs`
- `tests/frontend/page-template-seed.test.mjs`
- `tests/frontend/page-template-ui.test.mjs`
- `tests/frontend/page-template-v5-actions.test.mjs`
- `tests/frontend/page-template-v5-parity.test.mjs`
- `tests/frontend/page-template-v5-photo-actions.test.mjs`
- `tests/frontend/pr499-storyboard-fixes.test.mjs`
- `tests/frontend/product-cut-direction.test.mjs`
- `tests/frontend/product-source-photos.test.mjs`
- `tests/frontend/storyboard-drag-scroll.test.mjs`
- `tests/frontend/storyboard-initial-reveal-gate.test.mjs`
- `tests/frontend/storyboard-inspector-focus.test.mjs`
- `tests/frontend/storyboard-inspector-preview.test.mjs`
- `tests/frontend/storyboard-inspector-priority.test.mjs`
- `tests/frontend/storyboard-owner-feedback.test.mjs`
- `tests/frontend/storyboard-qa-b1.test.mjs`
- `tests/frontend/storyboard-qa-b2.test.mjs`
- `tests/frontend/storyboard-qa-b3.test.mjs`
- `tests/frontend/storyboard-qa-b4.test.mjs`
- `tests/frontend/storyboard-qa-b6.test.mjs`
- `tests/frontend/storyboard-similar-oncard.test.mjs`
- `tests/frontend/storyboard-space-set-card-cover.test.mjs`
- `tests/frontend/storyboard-space-set-inspector.test.mjs`
- `tests/frontend/storyboard-space-sets.test.mjs`
- `tests/frontend/storyboard-v5-gallery-stage.test.mjs`

### 공통 라이브러리, 서버와 fixture

- `src/lib/generationExamples.js`
- `src/lib/sections.js`
- `src/lib/storyboardTaxonomy.js`
- `src/lib/storyboardSpaceSets.js`
- `src/lib/storyboardExampleSelection.js`
- `src/lib/storyboardExampleFeedback.js`
- `src/lib/storyboardDragScroll.js`
- `server/app/agents/content_roles.py`
- `server/app/agents/page_template_assembler.py`
- `server/app/agents/page_templates.py`
- `server/app/data/page_templates_v1.json`
- `server/tests/test_storyboard_v5_sections.py`
- `tests/fixtures/page_templates_cases.json`
- `documents/WOC1_conti_v5_20261103_plan.md`
- `documents/WOC1_conti_v5_20261103_report.md`

에디터 코드 파일 자체를 다시 쓰지 않아도 카탈로그 이름 해석 함수와 조립 엔진의 이름을 통해 미니 미리보기가 바뀐다. 기존 저장 블록의 name과 sectionTitle을 일괄 재작성하지 않았다.

## 2라운드

실제 모델: **gpt-6.1-sol**, effort: **max**. 같은 작업 공간에서 2026-11-03 총괄 화면 검사 지적 11개를 수정했다. 앞 작업을 되돌리지 않았고 git 쓰기, 메인 코드 수정, 브라우저 실행, 유료 API 호출을 하지 않았다. 아래 내용이 위 1라운드 기록 중 변경된 동작을 대체한다.

### 항목별 원인과 수정

| 항목 | 원인 | 고친 곳과 확인 |
|---|---|---|
| 1. 내부 세트 이름 | 무대와 예시 스택에서 `set.name`을 직접 출력했고, 세트 판에도 원문 fallback이 남았다. | `StoryboardStage`, `StoryboardExampleGallery`, `StoryboardSpaceSetPanel` 모두 `spaceSetDisplayName(set)`만 쓴다. 내부 장소 설명·tone·검수 문자열은 출력하지 않는다. 실제 JSX 렌더에서 무대, 스타일링/호리존 사진 스택, 장소 추가/교체, 스튜디오 회전/시퀀스 판을 확인했다. `PASS`, `Attrangs`, 내부 A2/D5 표기와 원문 이름이 없다. |
| 2. 사진 아래 간격 | 공용 `.fx-sheen .btn-primary`가 모듈의 `.similar`보다 우선해 비슷한 컷 버튼을 `position: relative`로 만들었다. 보이지 않는 버튼도 높이를 차지했다. 그리드도 기본 stretch였다. | `StoryboardLayout.module.css`에서 사진 안 비슷한 컷 버튼의 선택자 우선순위를 높여 absolute를 보장하고 무대 행·사진 그리드를 start 정렬했다. 캡션은 사진 프레임 바로 다음에 남는다. 실제 공용 CSS와 모듈 CSS의 적용 우선순위 재현 시험으로 확인했다. 픽셀 간격의 최종 화면 확인은 총괄에게 남긴다. |
| 3. 늘릴 수 없는 섹션의 추가 칸 | `canAdd`가 일부 종류만 제외하는 조건이었다. 순수 추가 함수도 같은 범위로 열려 있었다. | 무대와 `addStoryboardPhoto`를 `wear`/`styling`에만 허용하고 세트 추가 핸들러도 같은 조건으로 막았다. 다른 섹션은 “사진을 누르면 오른쪽에서 바꿔요”. 자격 있는 원본의 비슷한 컷 추가는 유지한다. 위 보고서의 모든 내 사진 섹션 추가 가능 문구도 일상 착용컷으로 좁혔다. |
| 4. 예시 셔플 단추 | 첫 장에 시안에 없는 수동 셔플 단추를 넘겼다. | 단추, prop, 화면 핸들러, tick ref, import와 전용 CSS를 제거했다. 자동 예시 정책과 기존 공용 순수 함수 시험은 바꾸지 않았다. 렌더에 예시 셔플 단추가 없다. |
| 5. 카피라이팅 스위치 | 옛 설정 스위치와 낙관적 선택 상태가 아래 바에 남았다. | `StoryboardContinue`의 스위치와 props, 본문의 선택 ref/콜백을 제거했다. `copywritingSelection.js`는 삭제하고 `v4Copywriting.js`로 바꿨다. v4 진입은 로컬 복원 값이 true여도 기존 store → 프로젝트 PATCH 직렬 체인으로 true를 확인한다. 다음 단계는 그 확인과 콘티 저장 성공을 기다린다. 실패한 확인은 true 로컬 값만 보고 건너뛰지 않고 재시도한다. 구 프로젝트 실패가 다른 프로젝트 값을 되돌리지 않는 것도 시험했다. 기존 프로젝트 bool 필드와 계산 함수, 레거시 저장값 계약은 유지한다. |
| 6. 후보 이름·기본·컷 수 | 후보 번호를 임시 이름으로 썼고, 템플릿의 어느 섹션에서든 쓰이면 기본 표시를 붙였다. 종류만 같으면 칸 수가 달라도 후보에 들어갔다. | 기존 25개 디자인에 카탈로그 메타데이터 `displayName`을 추가하고 JS/Python 양쪽이 필수 짧은 문자열을 검증한다. `designChoices.js`에서 선택한 섹션 인스턴스의 기본 하나만 찾고, 같은 종류·같은 사진 위치 수 후보만 허용한다. 재사용 위치도 세며 조립 후 overflow 사진 수로 판단하지 않는다. 화면, 섹션 넣기, run 전체 디자인 변경에 같은 제한을 적용한다. 사진이 없는 글 전용 fallback은 그 카드가 선택된 상태로 표시한다. 새 블록 필드는 없다. |
| 7. 후보 미리보기 잘림 | 카드 높이는 제한했지만 SVG는 원래 가로 비율의 높이로 그려 윗부분만 보였다. | `StoryboardPreview`의 fit 모드는 전체 조립 높이의 viewBox, 100% 너비/높이, `xMidYMid meet`와 전용 CSS로 섹션 전체를 카드 안에 넣는다. 왼쪽 실제 길이 썸네일은 기존 비율을 유지한다. 여러 조립 블록을 모두 포함한 긴 미리보기 렌더를 시험했다. |
| 8. 추가 목록 그림·설명 | 가변 높이의 실제 조립 미리보기를 잘랐고 착용 종류의 설명을 숨겼다. | `StoryboardSectionScheme.jsx`에서 10종, 착용 두 쓰임의 11개 React SVG 대표 그림을 64×84로 통일했다. 목업 문자열 코드를 복사하지 않고 기존 토큰으로 그렸다. 일상 설명은 “카페, 골목, 집처럼 장소에서 찍은 사진”, 스튜디오는 “단색 배경에서 앞, 옆, 뒤를 찍은 사진”. 무대 머리에 이 설명을 더하지 않았다. |
| 9. Section 0 | 숨은 글 run이 보이는 목록에 없어 index가 -1이 됐지만 머리는 무조건 번호를 그렸다. | 무대는 0 이하 번호를 그리지 않는다. 상품 소개와 추천 대상은 이름만 표시한다. 보이는 섹션 번호와 저장 run 순서는 유지한다. |
| 10. SVG NaN | 누적 높이에 undefined/비정상 높이가 들어가면 다음 translate가 NaN이 됐고 elements가 없는 블록도 방어하지 않았다. | 유한한 양수 높이만 누적하고 없는 elements는 빈 배열로 처리한다. 좌표가 비정상인 요소는 미리보기에서 제외한다. 높이 누락/NaN/0, 빈 배열, null, 요소 없는 블록을 실제 SVG로 렌더해 유효한 viewBox와 translate만 나오는지 확인했다. |
| 11. 아래 버튼 유실 | 페이지 진입 효과의 transform 안에 있던 fixed 요소가 뷰포트 대신 그 조상을 기준으로 배치됐다. | 아래 바를 `document.body` portal로 옮기고 푸터 위 stacking을 보장했다. 옆 판이 열릴 때만 제거한다. 실제 JSX가 body에 portal을 만드는 경계를 시험했고 SSR은 문서 없이도 렌더한다. 페이지 끝에서의 위치는 총괄 화면 재검사 대상으로 명시한다. |

### 디자인 이름 25개

새 디자인과 레이아웃 자산은 추가하지 않았다. 아래는 기존 디자인의 화면 이름이다. 공유 ID는 카탈로그에 이름 하나를 둔다. `P01-D01`은 실제 두 사진·고스트칸 배치를 설명하는 쇼룸 이름, `P02-D02`는 FIXED와 안내형에서 쓰는 이름을 선택했다. 칸 수 제한으로 감성형 첫 장에 `P01-D01`을 새로 고를 수 없다. 기존 저장된 다른 장수 디자인의 조립은 유지하며 parity fixture에서도 이를 확인했다.

| 디자인 ID | displayName | 근거 |
|---|---|---|
| P01-D09 | 사진 가운데 소개 | data.js ALTS, alts-r3.js |
| P01-D01 | 사진 아래 제목과 고스트컷 | 쇼룸 ALTS, alts-r3.js |
| P01-D11 | 사진 아래 제목과 문단 | ALTS, alts-r3.js |
| P02-D01 | 큰 사진 이어 보기 | FIXED, alts-r3.js |
| P02-D02 | 제목 아래 연속 사진 | FIXED, 안내형 alts-r3.js |
| P04-D01 | 앞, 옆, 뒤 한 장씩 | FIXED, alts-r3.js |
| CP-EMO-01 | 색별 사진과 뒷면 | FIXED, alts-r3.js |
| CP-SHOW-01 | 정사각 색상과 앞뒤 | FIXED, alts-r3.js |
| CP-GUIDE-01 | 세 칸 색상과 앞뒤 | FIXED, alts-r3.js |
| T03-D01 | 사진 옆 설명 | ALTS, alts-r3.js |
| T03-D06 | 사진 안 소재 설명 | 쇼룸 ALTS |
| T03-GUIDE-01 | 제목 아래 원단 사진 | 안내형 ALTS, alts-r3.js |
| T03-TEXT-01 | 글로 읽는 소재 설명 | **이번 명명**: 사진 없는 소재 문단·혼용률 배치 |
| T01-D07 | 셀러 말풍선 코멘트 | FIXED |
| T06-D01 | 한눈에 보는 요약 | FIXED |
| PT-EMO-01 | 핏 문단과 번호 행 | 감성형 ALTS |
| PT-EMO-TEXT | 핏 문단과 사이즈 팁 | **이번 명명**: 사진 없는 핏 문단과 팁 상자 |
| P06-D01 | 사진 아래 설명 | alts-r3.js |
| T05-D07 | 사진 옆 번호와 문단 | 안내형 ALTS, alts-r3.js |
| T07-D06 | 핏 앨범 | FIXED, alts-r3.js |
| T08-D01 | 체크 알약 | ALTS, alts-r3.js |
| I09-D06 | 사진 위 후기 말풍선 | FIXED, alts-r3.js |
| T08-D07 | 사진 위 문답 말풍선 | FIXED |
| I09-D14 | SNS 메시지 | FIXED, alts-r3.js |
| P07-D08 | 스토리 화면 두 칸 | alts-r3.js |

### 기준 시안과 남겨 둔 차이

새 디자인 데이터는 범위 밖이므로 시안의 추가 후보를 만들지 않았다. 현재 25개 중 종류와 사진 칸 수가 맞는 후보만 보인다. 원단 사진이 없는 섹션은 글 전용 fallback 하나를 표시한다. 64×84 대표 그림만 종류별 도식이고, 디자인 카드·왼쪽 썸네일·전체 보기는 실제 조립 결과를 쓴다. 색, 서체, 자간, 되돌리기 알림 위치 등의 WOC1b 조정은 하지 않았다. 디테일 판정, 남는 세트 사진, 미사용 순서 개선, 셀러 사진 잘라 보기, 서버 생성 파이프라인도 변경하지 않았다.

### 이번 라운드 파일

- 화면: `src/features/storyboard/Storyboard.jsx`, `StoryboardStage.jsx`, `StoryboardContinue.jsx`, `StoryboardPreview.jsx`, `StoryboardExampleGallery.jsx`, `StoryboardSpaceSetPanel.jsx`, `StoryboardSectionPicker.jsx`, 새 `StoryboardSectionScheme.jsx`, 새 `v4Copywriting.js`, `StoryboardLayout.module.css`, `StoryboardPanels.module.css`. 옛 `copywritingSelection.js` 삭제.
- 순수 함수와 규칙: `src/lib/pageTemplates/designChoices.js` 추가, `catalog.js`, `photoActions.js`, `sectionActions.js`, `README.md` 수정.
- 공유 카탈로그: `server/app/data/page_templates_v1.json`, Python 검증 `server/app/agents/page_templates.py`.
- 새 시험: `tests/frontend/storyboard-v5-round2.test.mjs`, `page-template-design-choices.test.mjs`.
- 기존 시험 조정: `tests/frontend/helpers/storyboardStageHarness.mjs`, `page-template-interactions.test.mjs`, `page-template-ui.test.mjs`, `page-template-v5-parity.test.mjs`, `page-template-v5-photo-actions.test.mjs`, `parallel-flow-routing.test.mjs`, `storyboard-owner-feedback.test.mjs`, `storyboard-qa-b1.test.mjs`, `storyboard-qa-b2.test.mjs`, `server/tests/test_page_templates_catalog.py`.
- 보고서: `documents/WOC1_conti_v5_20261103_report.md`.

### 총괄 화면 재검사

위의 목 주소와 진입 절차를 그대로 쓴다. 이 라운드에서도 브라우저를 실행하지 않았으므로 아래 픽셀·스크롤 검사는 통과했다고 주장하지 않는다.

| 장면 | 상태 만드는 순서와 확인 |
|---|---|
| 이름과 캡션 | 감성형 일상 착용컷을 열고 사진·장소 바꾸기·장소 세트 추가를 차례로 연다. 스택과 묶음 이름에 내부 상점/검수 문자열이 없는지, 사진 바로 아래에 장소와 번호가 붙는지 확인한다. |
| 첫 장·원단·스튜디오 | 각 섹션에 일반 컷 추가·장소 세트 추가·12컷 안내가 없고, 일반 안내 문구만 있는지 확인한다. 자격 있는 원본의 비슷한 컷 추가는 계속 동작해야 한다. 첫 장 셔플 동그라미도 없어야 한다. |
| 후보 | 감성형 첫 장은 두 1칸 후보만, 쇼룸 첫 장은 2칸 기본만 남는다. 일상 착용컷·원단 사진이 있는 원단 설명·안내형 두 포인트의 후보에서 이름, 기본 하나, 전체 축소 배치 차이를 확인한다. 글 fallback도 선택 테두리가 있어야 한다. |
| 추가 목록·글 섹션 | 각 틈의 섹션 추가에서 대표 그림이 모두 64×84이고 글 종류도 보이는지 확인한다. 착용 두 설명을 확인하고 기존 상품 소개/추천 대상에 들어가면 번호 없이 이름만 나와야 한다. |
| 스튜디오 이름 | 스튜디오 사진 판의 세트 스택, 다른 스튜디오 세트로 바꾸기에서 내부 A2/D5 이름이 보이지 않는지 확인한다. 하의 상품에서도 같은 함수를 쓴다. |
| 페이지 끝 아래 바 | 섹션을 넣어 아래로 내려간 상태와 페이지 끝에서 구성컷·예상 크레딧·다음 버튼이 계속 보이는지 확인한다. 옆 판을 열 때만 숨고 닫으면 다시 보이며 카피라이팅 스위치가 없어야 한다. |
| 오류와 저장 | 사진 판을 열고 후보를 바꾸면서 콘솔의 NaN transform 오류가 없는지 확인한다. 저장 실패를 재현할 경우 다음 단계로 이동하지 않고, 재시도 성공 뒤에만 이동해야 한다. |

### 시험 명령과 실제 결과

항목 1부터 11까지의 재현 시험 11개를 수정 전 먼저 실행해 **0 통과, 11 실패**를 확인했다. 수정 뒤 이 11개가 모두 통과했다. 기본 표시·후보 전체 축소 확인을 더한 화면 재현 12개와 순수 디자인 규칙 13개는 합계 **25 통과, 0 실패**다. 복원된 true도 서버에 확인해야 하는 경우와, 그 확인 실패 뒤 다음 단계에서 재시도해야 하는 경우도 각각 실패를 확인한 뒤 고쳤다. 최종 전체 시험에는 이 재현과 기존 저장·되돌리기·재사용 시험이 함께 들어 있다.

| 명령과 위치 | 결과 |
|---|---|
| 작업 공간에서 `pnpm test:frontend` | **4,838 통과, 0 실패, 0 건너뜀**, exit 0. 54.29초 |
| 작업 공간 `server/`에서 `/Users/daily/Documents/wearless_studio/server/.venv/bin/pytest -q --ignore=tests/test_personalization.py` | **10,701 통과, 183 건너뜀, 0 실패**, 경고 1,043개, exit 0. 297.92초 |
| 작업 공간에서 `pnpm build` | **성공**, exit 0. 16.35초 |
| `MOCK_API_LATENCY_SCALE=0 node --test tests/frontend/page-template-design-choices.test.mjs tests/frontend/storyboard-v5-round2.test.mjs` | **25 통과, 0 실패**. 실제 JSX/SVG, 셀러 이름, 위치 수 보존, 기본 하나, 메타데이터 검증 |

JS/Python parity는 전체 프런트 시험 안에서 세 템플릿 × 고정 씨앗 4개와 의존성 대체 상태를 포함한 **18개 페이지**를 동일 결과로 확인했다. 기존에 저장된 2장 SNS 디자인도 fixture로 유지해 새 선택 제한과 저장 호환성을 구분했다. 서버 숫자는 이번 작업 공간에서 실제로 돌린 결과다. 총괄의 사전 실행 숫자를 재사용하지 않았다.

로그는 `.scratch/woc1-20261103/round2-red.log`, `round2-regressions.log`, `round2-copy-restore-red.log`, `round2-copy-confirmation-red.log`, `round2-copy-confirmation-green.log`, `round2-frontend-final.log`, `round2-server.log`, `round2-build-final.log`에 있다. 빌드의 청크 크기와 import 관련 경고는 오류와 구분한다. 브라우저·실서버 생성·유료 API 시험은 실행하지 않았다.
