실제 모델: gpt-6.1-sol · effort: max. 현재 세션의 마지막 turn_context에서 확인했다.

# WOC2 콘티 v5 확정분 구현, 2026-11-05

작업 공간 `/Users/daily/.codex/worktrees/templates-mvp/wearless_studio`에서 WOC1b까지의 미커밋 작업을 보존하며 이어서 구현했다. 장부 D170~D208, 11/05 시안의 Codex 범위와 장면 그림, VISUAL_SPEC 및 추가 명세, panel-1104.css를 읽었다. git 쓰기, 메인 코드·장부·시안 수정, 브라우저 실행, 유료 API 호출은 하지 않았다. 결과 표시용 파일·브라우저·터미널 자동 열기도 하지 않았다.

## 항목별 결과

| 항목 | 구현과 확인 |
|---|---|
| A1 페이지 구성 | 썸네일 184px, 1360px 미만 172px, 칸은 썸네일+44px. 제목을 위로 옮겨 Section 13px Playfair 이탤릭 600, 이름 13px 700, 컷 수 11px로 표시한다. 7×10px 제목 여백, 8px 썸네일 모서리, 14px 간격과 1px/선택 2px 검정 테두리. 실제 길이를 자르지 않고, 긴 이름은 어절로 감싼다. 흰 도구 알약을 썸네일 오른쪽 위 6px 안쪽에 두고 hover/focus 때만 표시한다. 첫 장에는 도구가 없고 경계 화살표는 aria-disabled로 막아 초점을 유지한다. 제목 끌기, 손잡이·화살표 이동, 같은 제어 초점 복원을 연결했다. ＋ 줄은 제거하고 끌 때만 놓을 선을 표시한다. 빌린 고스트 사진은 첫 장의 생성 컷 수에 더하지 않고, 따온 사진만 있는 디테일은 자리 수를 표시한다. 스크롤바 공간을 썸네일 폭 밖에 확보했다. |
| A2 배경과 판 | v4 콘티 body 테마가 활성일 때만 셀러 aurora/orb를 숨기고 중앙 원 두 개를 portal로 표시한다. 원 크기·색·회전은 시안, 위치·명도는 D174 값이다. 다른 화면으로 나가면 테마와 새 원이 제거된다. 무대와 구성 판은 흰 .98 바탕, .12 선, 지정된 세 겹 그림자, 16px 판 모서리. 머리는 bg-2, 구성 머리는 스크롤바 자리까지 채운다. 장소 칸은 F0F0EE다. |
| A3 디자인 와이어프레임 | 실제 조립의 image/shape/text/line 좌표를 공통 SVG 렌더러에서 회색 면과 옅은 줄로 그린다. 디자인별 수작업 그림과 실제 사진은 쓰지 않는다. 색상과 제품은 실제 판매 색 수를 유지하고 모든 색의 앞면을 보여 주는지 검사한다. 고정 4자리라도 같은 두 색만 반복하면 제외한다. 현재 세 색상 디자인은 동적 격자라 실제 색을 모두 확장한다. 긴 착용 앨범은 네 장 표본, 고정 회전은 세 장이다. |
| A4 글 숨김 | 왼쪽, 무대의 섹션 그림, 전체 보기, 후보 모두 글 견본을 SVG text/tspan 대신 옅은 줄로 그린다. 상품 소개·추천 대상은 왼쪽과 추가 목록에서 빠지고 저장·최종 조립에는 남는다. 글 섹션 관리 모드와 수작업 대표 그림 부품을 제거했다. |
| A5 섹션 추가 | 구성 머리의 한 단추로 목록을 연다. 없는 단일 섹션과 반복 가능한 두 착용 종류만 표시하고, 누르면 기본 디자인으로 바로 넣는다. 마지막 같은 종류·쓰임 뒤, 아니면 템플릿 기본 순서의 다음 섹션 앞에 넣고 숨은 글 run은 옮기지 않는다. 기본 순서에 없는 종류는 끝에 넣는다. 명시적 끌어 놓기는 고른 run 경계를 쓴다. 목록은 열린 채 유지하고 위에 “일상 착용컷을 넣었어요 · 되돌리기”를 둔다. 사라지는 단일 섹션 줄도 클릭 직후 그림·좌표를 먼저 보관하므로 520ms 날아가기와 후속 강조가 된다. reduced-motion은 즉시 반영한다. 애니메이션은 Web Animations API의 임시 clone이며 React style 속성이나 저장 필드가 아니다. |
| A6 빼기 확인 | 착용 run만 확인 창을 연다. 순수 sectionRemovalImpact가 실제 다음 블록과 디테일 원본 이동·동반 제거·SNS 추가 컷을 미리 계산한다. 확인은 그 스냅샷을 사용해 fallback id를 다시 만들지 않는다. “그대로 두기 / 빼기”는 같은 알약이며 modal-actions를 쓴다. Escape·배경·취소는 블록을 바꾸지 않는다. Tab 초점을 가두고 닫을 때 실행 단추 또는 살아 있는 구성 제어로 돌린다. 다른 종류는 즉시 빼고 되돌린다. |
| A7 장소 바꾸기 | 교체 중인 spaceGroupId의 모든 사진을 2px 선택 상태로, 나머지만 옅게 표시한다. 묶음 선택 때문에 모든 비슷한 컷 단추가 상시 켜지지 않게 했다. |
| A8 추가 대표 그림 | 목록 64×84 틀 안에 해당 기본 디자인의 실제 조립 와이어프레임을 비율대로 넣는다. 스튜디오는 세로 세 장, 색상과 제품은 현재 카탈로그 기본 격자를 보여 준다. 세로 색상 디자인을 새로 만들지 않은 차이는 아래에 명시했다. |
| A9 자리 연결 | 실제 조립 좌표의 읽는 순서·면적을 계산해 자리/큰 자리/작은 자리를 표시하며 한 자리면 생략한다. hover는 썸네일의 같은 자리에 검정 2.4px 선과 옅은 검정 막을 표시한다. 열린 썸네일 자리 누름은 해당 사진, 다른 썸네일 첫 누름은 섹션만 연다. 동일 섹션·촬영 종류의 착용/실제 디테일 사진을 바꾸며 설정과 비슷한 컷이 따라간다. 디테일은 pointItems의 sourceBlockId·detailTargetId가 든 객체 전체를 교환하며 실제 2장/3장 시드의 validateStoryboardV4와 조립 순서를 검사했다. 서로 다른 장소도 허용하고 갈라진 run은 재키한다. 대표·후기·앞옆뒤·색별 제품·SNS·원단·따온 칸·비슷한 자식은 이유 한 줄로 막는다. 자리 id는 section/slot/source로 만들어 섹션 이동 뒤에도 선택이 유지되고, 세 crop이 같은 source를 써도 고른 한 자리만 표시한다. 임시 viewId는 저장하지 않는다. |
| A10 이름 | 카탈로그의 디테일 요소, 일상 착용컷, 스튜디오 착용컷을 사용한다. 물리 cutType과 레시피를 우선 읽어 쇼룸 연속 스튜디오도 같은 이름이다. 저장된 이름은 덮어쓰지 않는다. 에디터 미니 미리보기의 title도 같은 templateSectionName을 쓰는 것을 확인했다. |
| A11 파스텔 대비 | 파스텔을 글자색으로 쓰지 않고 fg-1을 사용한다. 끌어 놓는 선은 ring-flow-5. ADR-0021 회전 테두리 팔레트는 다시 선언하지 않았다. |
| B1 템플릿 둘 | 선택 가능한 카탈로그·분석 radio·목 쿼리·견본 그림에서 안내형을 제거했다. guide 전용 디자인 다섯 개는 공통 정의로 남겼고 T06의 사라진 guide 전용 글 분기를 emo 공통 분기로 옮겨 빈 조립을 막았다. 고정 사례는 두 템플릿과 common 디자인 사례로 다시 만들었다. |
| B2 공통 목록 | alternatives 배열에 의존하지 않고 전체 25개에서 종류·물리 쓰임·사진 자리 수·색 표현 조건으로 거른다. JS/Python 검증은 wear usage와 crop 지원 메타데이터를 함께 검사한다. 원래 템플릿은 기본 디자인 하나만 정한다. |
| B3 착용 종류 | 두 템플릿의 추가 일상은 P02-D01, 같은 장소 전신+미디움 두 장이다. 추가 스튜디오는 P04-D01, 앞옆뒤 세 장이다. 쇼룸 기본 연속 앨범은 P02-D02를 유지하고 fit/스튜디오로 표시한다. 기존 저장 제목을 바꾸지 않는다. |
| B4 쇼룸 첫 장 | 승인된 읽기 전용 origin/main 조회로 signatureCutPool.js를 복원했다. 성별·옷 종류·하의 종류·여성 원피스 전용 컷을 고르며 horizon/medium/front/all로 배정한다. 프로젝트별 안정적 선택과 기존 유효 선택을 유지한다. 첫 장 판 후보도 이 풀만 받고, 남성·여성의 저장/미리보기는 정확한 sig id를 조회한다. 오래된 previewThumb가 풀을 덮지 않게 했다. 기본 medium을 fingerprint 전에 정해 자동 채우기가 편집으로 오인되지 않는다. 배치 디자인은 그대로다. 실제 서버 worker의 sig 입력 경로까지 여덟 사례를 검증했다. |
| B5 디테일 요소 | 원단을 제외한 뚜렷한 구조 부위가 2개 이상일 때만 처음 넣고 2/3개면 2/3장의 디테일 사진만 쓴다. 0/1개면 생략한다. 수동 추가는 한 착용 원본의 세 crop이며 새 AI 컷·크레딧은 없다. 회전 정면→연속 정면→생활 정면→남은 착용 사진 순이며 업로드한 착용 사진도 허용한다. 다른 원본으로 이동하거나 착용 run이 전부 없어지면 따온 디테일 run도 제거한다. 머리의 원본 Section 번호는 이동 순서를 따른다. crop SVG는 에디터와 같은 -ox/-oy로 그린다. 한도는 공유 카탈로그 photoBudgets.detail=3을 DETAIL_PHOTO_CAP에서 읽는다. 한도를 4로 바꾸지 않았다. |
| B6 SNS 칸 수 | 선택한 디자인의 실제 reuse 자리만 만들고 I09-D14는 sns1 한 칸이다. 페이지에 안 보이는 두 번째 생성 사진은 없다. 원본 삭제 fallback도 정확히 +1 AI 컷이다. |
| B7 세트 소속 | 세트 밖 낱장 선택은 새 한 칸 인스턴스로 보존한다. variation·배경·멤버 순서와 스타일링 pose/스튜디오 all 계약이 남는다. 같은 세트 선택은 인스턴스를 유지한다. 일반 예시는 소속 없이 유지한다. 기존에 소속이 지워진 ss_도 자동 채우기 전에 복구한다. 클릭, 구도 전환 확정, 끌어 넣기, export된 독립 멤버 만들기에 같은 규칙을 연결했다. 자식·갈라진 run·이미 사용·남은 멤버·되돌리기·견적과 실제 서버 검증 표는 아래와 같다. |
| B8 색 맞춤 | 분석에서 템플릿 바로 아래 모노톤 기본/옷 색에 맞춤 두 카드를 넣고 pageStyle.colorMode의 기존 직렬 저장·복원·템플릿 변경·잠금 경로를 쓴다. 감성 모노톤을 지정 회색 묶음으로 바꾸고 흰·회색·검정·미상은 point에서도 같은 회색, 아이보리·베이지·브라운은 기존 따뜻한 묶음이다. 96개 두 엔진 토큰 조합과 840개 글자/바탕 대비를 확인했다. |

## 판단과 시안 차이

- A8의 색별 세로 대표 그림은 적용하지 않았다. 현재 25개에는 CP-EMO-01(2열 4:5), CP-SHOW-01(2열 1:1), CP-GUIDE-01(3열 4:5)뿐이다. B2의 현재 카탈로그 사용과 D201의 새 디자인 보류를 우선해 실제 기본 격자를 그렸다. T02-D09 등 새 데이터는 가져오지 않았다. 쓰임 필터 뒤 현재 일상용 후보는 P02-D01 하나라 디자인 단추가 숨는다. 네 장 표본 함수는 유지하고, 둘 이상인 쇼룸 연속 스튜디오 후보로 화면에서 확인할 수 있다.
- 시안의 검토 조절판, D170 접힘 변경, D171 작은 창·스택, D199 48장 무작위, 자동 배치 엔진, 새 조사 기본 구성·디자인은 가져오지 않았다. 사진 판의 기존 접힘/세트/구도/3×2 동작을 보존했다. D204 저장 소속 수정과 첫 장 시그니처 후보 제한만 연결했다.
- 수동 crop 원본의 “남은 착용 사진”은 wear 섹션의 실제 AI/업로드 사진으로 해석했다. 첫 장·후기·SNS가 모델 사진을 담아도 디테일 원본 후보로 쓰지 않는다. 앞옆뒤 가운데 정면이 없어도 남은 옆/뒤 착용 사진까지 보수적으로 사용하고, wear 사진이 하나도 없어질 때만 derived run을 뺀다.
- 구조 디테일이 세 개면 기존 총 한도 3을 먼저 쓰고 원단은 글 fallback으로 둔다. 디테일과 crop을 한 줄에 섞지 않는다.
- 섹션 저장은 즉시 반영하고 520ms 비행은 그 저장과 독립된 화면 피드백으로 실행한다. 출발 DOM이 사라지기 전에 clone/좌표를 보관하고, 도착 뒤 강조하며 reduced-motion이면 비행을 생략한다.
- 안내형 화면 시험을 두 템플릿 시험으로 줄이는 대신 안내형에만 있던 공통 디자인과 고정 geometry·사실·서버 검증은 common 사례로 보존했다. 공유 고정 사례는 105개이며 재생성을 두 번 실행해 byte 동일함을 확인했다.

## D204 다방면 확인

활성 발행 세트 73개, 멤버 254장을 JS 선택 패치→실제 서버 canonicalize/bind→실제 detail_page_job 입력 준비까지 모두 보냈다. 저장소·자산 다운로드·생성 공급자 경계만 로컬 값으로 대체했다. 유료 호출 없이 실제 경로의 장소 판·자세·완성 예시 선택과 confirmed packet 부재를 검사했다.

| 종류 | 구도 | 방향 | 실제 멤버 수 | 묶음 검증과 생성 입력 |
|---|---|---|---:|---|
| 스타일링 | 전신 | 정면 | 79 | 통과, 해당 세트 plate + 선택 pose |
| 스타일링 | 전신 | 옆 | 2 | 통과, 해당 세트 plate + 선택 pose |
| 스타일링 | 전신 | 뒤 | 5 | 통과, 해당 세트 plate + 선택 pose |
| 스타일링 | 미디움 | 정면 | 70 | 통과, 해당 세트 plate + 선택 pose |
| 스타일링 | 미디움 | 옆 | 5 | 통과, 해당 세트 plate + 선택 pose |
| 스타일링 | 미디움 | 뒤 | 1 | 통과, 해당 세트 plate + 선택 pose |
| 스튜디오 연속 | 전신 | 정면 | 29 | 통과, 해당 멤버 all 완성 사진 |
| 스튜디오 연속 | 전신 | 옆 | 4 | 통과, 해당 멤버 all 완성 사진 |
| 스튜디오 연속 | 전신 | 뒤 | 6 | 통과, 해당 멤버 all 완성 사진 |
| 스튜디오 연속 | 미디움 | 정면 | 32 | 통과, 해당 멤버 all 완성 사진 |
| 스튜디오 연속 | 미디움 | 옆 | 4 | 통과, 해당 멤버 all 완성 사진 |
| 스튜디오 연속 | 미디움 | 뒤 | 11 | 통과, 해당 멤버 all 완성 사진 |
| 스튜디오 회전 | 전신 | 정면 | 2 | 통과, 해당 멤버 all 완성 사진 |
| 스튜디오 회전 | 전신 | 옆 | 2 | 통과, 해당 멤버 all 완성 사진 |
| 스튜디오 회전 | 전신 | 뒤 | 2 | 통과, 해당 멤버 all 완성 사진 |
| 스튜디오 회전 | 미디움 | 정면·옆·뒤 | 0 | 발행 규칙상 없음. 없는 멤버를 만들지 않음 |
| 시그니처 | 미디움 | 정면 | 8 사례 | 여성/남성 상의·아우터, 여성 치마·반바지·원피스, 남성 바지. 실제 signature 경로와 자산까지 통과 |

전체 집계에는 스타일링 여섯 구도·방향이 모두 있지만 성별/의류마다 빈 조합이 있다. 남성 전신 옆, 남성 미디움 뒤는 활성 풀에 없으며 다른 성별 사진으로 채우지 않는다. 정확한 의류별 빈 조합과 세트별 자산 경로는 `.scratch/woc2-20261105/sets-matrix.log`에 기록했다. 전체 이력 74세트/259멤버와 활성 73/254를 혼동하지 않았다.

| 교차 상황 | 확인 결과 |
|---|---|
| 다른 세트의 낱장을 고름 | 새 인스턴스, 정확한 variation/순서/배경, 스타일링 pose. 제자리 유지 |
| 같은 세트의 다른 멤버 | 같은 인스턴스, 선택 멤버 순서 갱신 |
| 묶음 중간 멤버 교체 | 앞·뒤 run 분리 후 뒤에 새 instance id. 실제 Python 저장 검증 통과 |
| 전체 세트 교체·섞기 | 장수·순서·원본+자식 계산 유지, API 검증 통과 |
| 비슷한 컷 | 원본의 멤버/소속을 상속하며 원본과 함께 이동. 새 원본의 설정과 동기화 |
| 이미 사용·남는 멤버 | 사용 가능 배지와 선택 허용 유지, 남은 멤버 reservation은 페이지 전체의 실제 example/order를 사용 |
| 오래된 ungrouped ss_ | 자동 채우기 전에 한 칸 소속을 복구. 반복 실행은 동일하고 옛 undo 스냅샷은 안 바뀜 |
| 밖에서 독립 멤버 만들기·드롭 | exported helper도 254장 전부 한 칸 소속. 제공된 합성 테스트 세트도 검증된 멤버만 만들고 없는 사진은 거부 |
| 되돌리기 | 이전 블록·그룹 id·설정 전체 복원. 이전 스냅샷 불변 |
| 무대 머리 | 착용 밖 한 칸 묶음에는 세트 머리 없음. 내부 name은 화면에 쓰지 않음 |
| 크레딧 | 같은 사진 교체·자리 이동·3 crop은 생성 수 동일. SNS fallback은 실제 추가 자리 한 컷만 증가 |

## 색 대비와 토큰

감성 mono: ink `#1f1f1f`, muted `#6b6b6b`, accentInk `#5c5c5c`, accentBg/panel `#f4f4f4`, accentLine `#e2e2e2`. 쇼룸 accentInk는 흰 바탕 대비가 3.452220:1이던 `#8a8a8a`에서 기존 muted와 같은 `#6b6b6b`로 맞췄다. 따뜻한 point 팔레트는 유지한다.

| 글자 역할 | 감성 흰 바탕 | 감성 panel | 쇼룸 흰 바탕 | 쇼룸 panel |
|---|---:|---:|---:|---:|
| ink | 16.482977 | 14.986342 | 18.883061 | 17.017583 |
| muted | 5.329212 | 4.845325 | 5.329212 | 4.802733 |
| accentInk | 6.686914 | 6.079750 | 5.329212 | 4.802733 |

검사한 생성 페이지 팔레트의 불투명 글자/배경 840쌍의 최저는 **4.777664:1**(감성 point/purple의 muted `#6b6b6b` 대 accentBg `#f6f0ff`)로 모두 4.5 이상이다. 사진 위 글의 기존 자동 대비는 바꾸지 않았다. 회전 파스텔은 텍스트 역할이 아니다. 세부 계산은 `catalog-contrast.log`, 96개 JS/Python 일치는 `woc2-catalog-color.test.mjs`/`test_woc2_catalog_color.py`다.

새 토큰은 `tokens.css`의 `.sb-v5-theme` 안에만 더했다. 공용 스타일·DESIGN.md는 바꾸지 않았다.

| 새 토큰 | 값·쓰는 곳 |
|---|---|
| sb-toc-thumb / thumb-narrow / tools-space | 184/172/44px, 구성 칸과 썸네일 |
| sb-toc-title-size / count-size / title-y / title-x / gap | 13/11/7/10/14px, 구성 제목과 섹션 간격 |
| sb-toc-border | rgba(34,42,53,.14), 썸네일 선 |
| sb-panel-bg / panel-border / panel-shadow | .98 흰색, .12 선, 명세의 세 겹 그림자. 무대·구성·확인 창 |
| sb-place-bg | F0F0EE, 사진 아래 장소 칸 |
| sb-position-veil / position-bg | fg-1의 22%/76%, 썸네일 연결 막과 자리 배지 |
| sb-orb-one / orb-two | 기존 시안 원의 conic 팔레트, 콘티 배경 두 원 |
| sb-flight-duration / shift-duration | 520/180ms, 임시 비행과 구성 줄 전환. WAAPI는 계산된 토큰을 읽음 |

## 시험

항목별 구현 전에 새 재현을 실행했다. 최초 UI는 8개 전부 실패, 카탈로그 JS는 11개 전부 실패와 Python 14실패/3통과였다. 세트·시그니처·디테일·색 개수 검사는 각각 RED 로그를 남긴 뒤 고쳤다. 후속 검토에서 찾은 정확한 crop 고름, 이동 뒤 자리 id, crop offset, 출발 줄 unmount, 첫 장 빌린 컷 수와 독립 멤버 소속도 별도 RED→GREEN으로 확인했다. 오래된 화면/guide/소속 이탈 기대값은 새 계약을 검사하는 실제 JSX·순수 함수·서버 경계 시험으로 옮겼다.

| 명령 | 최종 실제 결과 |
|---|---|
| `pnpm test:frontend` | **4,947 통과, 0 실패, 0 건너뜀**, exit 0. 63.45초. 디테일 부위 연결·유효한 최신 색 선택·첫 장 컷 수·세트 helper까지 포함. frontend-complete.log |
| `server/`에서 `/Users/daily/Documents/wearless_studio/server/.venv/bin/pytest -q --ignore=tests/test_personalization.py` | **10,755 통과, 183 건너뜀, 0 실패**, 경고 1,043개, exit 0. 277.09초. server-final.log |
| `pnpm build` | **성공**, exit 0. 8.62초. build-complete.log. 의존성 PURE 주석·500kB 이상 청크 경고가 있고 오류는 없다. |
| `PAGE_TEMPLATE_PYTHON=/Users/daily/Documents/wearless_studio/server/.venv/bin/python node scripts/regenerate-page-template-fixtures.mjs` | 105개 정확히 일치, 두 번 생성 byte 동일. SHA256 ca7390627e81c008573238da616e6562a068b217f8af079bd93e73bc6fda2018 |
| `git diff --check` | 공백 오류 없음, git 읽기만 수행 |

시험 결과 로그는 `.scratch/woc2-20261105/`에 있다. 통과 뒤 시작한 중복 frontend-verified/build-verified 실행은 자원 경합을 줄이려고 중단했으며 통과로 세지 않았다. 마지막 디테일 교환의 부위 바인딩 보완 후에는 서버→프론트→빌드를 차례로 최종 재실행했다. 최초 전체 실행은 4,954개 중 77실패였고, 규칙 변경에 맞는 기존 시험 갱신과 발견된 실제 연결 오류를 고친 뒤 재실행했다. 중간 전체 실행 4,943통과와 빌드 성공을 최종 변경 뒤의 결과로 대신 쓰지 않았다. 첫 서버 전체는 10,751통과/4실패/183건너뜀이었다. 안내형 고정 사례 한 건을 공통 사례로 바꿨고, 실제 manifest를 쓰는 새 세트 시험이 앞 시험의 임시 registry 캐시를 물려받던 순서 의존 두 건을 fixture 시작/종료 cache_clear로 고쳤다. 정확한 두 파일 순서 재현은 2실패/44통과에서 46통과가 됐다. 나머지 0.15초 제한 시험은 부하 중 0.674초였고 단독 15개 모두 0.15초에 통과했다. 제한을 느슨하게 하거나 운영 코드를 바꾸지 않고 전체를 단독으로 다시 돌렸다. 서버 결과는 이 작업 공간의 실제 숫자를 쓴다.

## 총괄 화면 확인

브라우저는 실행하지 않았다. 픽셀·실제 스크롤·OS 끌어 놓기는 총괄의 화면 검사로 남는다. 작업 공간에서 `pnpm dev:mock --host 127.0.0.1 --port 5173`을 실행하고 새 비공개 창에서 연다.

- 감성형: `http://127.0.0.1:5173/create/storyboard?mockTemplate=emo`
- 쇼룸형: `http://127.0.0.1:5173/create/storyboard?mockTemplate=show`

목 직접 진입 가드가 시드 프로젝트를 만든다. 이어하기가 뜨면 계속하기로 들어간다. 기본 목 추천은 구조 부위가 둘 이상이어서 디테일 사진 섹션이 보일 수 있다. 수동 crop 상태는 그 디테일 요소를 빼고, 섹션 추가에서 다시 넣어 만든다. 없는 섹션 목록을 보려면 SNS/후기 등 단일 종류를 빼고 추가를 연다. 추천 0/1/2/3, 네 색, 중립·따뜻한 색별 고정 상태는 생성 fixture의 woc2 사례로도 재현된다.

| 장면 | 만드는 순서·확인점 |
|---|---|
| 기본 구성 1280/1440/1920·웹킷1440 | 두 주소 각각 새 상태. 184/172px 길이 비례 썸네일, 제목 줄, 14px 사이, 스크롤바 겹침 없음, 중앙 원과 판 그림자. 첫 장 도구 없음 |
| 제목·도구·화살표 | 두 번째 섹션 hover/Tab. 도구 6px 안쪽, 제목 끌기와 손잡이 위/아래, 경계 막힘, 옮긴 뒤 같은 화살표 초점 |
| 기본 추가·비행 | 구성 머리 추가→일상/스튜디오, SNS 삭제 후 다시 추가. 디자인 단계 없이 자동 자리, 단일 줄이 사라져도 비행, 판 유지와 위 되돌리기. reduced-motion 반복 |
| 끌어 넣기 | 추가 목록 줄 또는 기존 사진 판 예시→섹션 사이 놓기. 평소 ＋ 줄 없음, 끌 때 가장 진한 파스텔 선. 숨은 글 run 앞뒤 순서 유지 |
| 와이어프레임 | 쇼룸 기본 연속 스튜디오 앨범→디자인 변경. 네 장 표본과 회색 배치 차이, 글 낱말·실제 사진 없음. 일상 10장은 후보가 하나면 단추가 숨는지 확인. 판매 네 색 상태에서는 실제 네 색 자리. 네 색 상태는 새 상품 입력에서 색상 네 개를 등록한 뒤 분석→코디→콘티로 진입 |
| 자리 연결 | 한 장 첫 장은 배지 없음. 쇼룸 첫 장 재사용/크기 다른 자리, 일상 hover와 활성 썸네일 자리 선택, 비활성 썸네일은 첫 누름에 열기만 |
| 자리 바꾸기 | 일상 동일/다른 장소 사진 교환, 비슷한 자식 동반. 고정 첫 장·후기·회전·색별 제품은 한 줄 이유. 디테일 실제 사진끼리 교환 뒤 저장/다시 열기 |
| 삭제 확인 | 수동 crop 만든 뒤 원본 착용 섹션 삭제→다음 원본 설명, 마지막 wear 삭제→동반 제거 설명. SNS 소스 삭제→+1 문구. 취소/확인/Esc, 초점/되돌리기 |
| crop 원본·표시 | 디테일 세 crop 중 두 번째 선택 후 섹션을 위아래로 옮김. 한 자리만 검정 표시 유지, 원본 Section n 갱신, 잘린 위치가 원본 사진과 맞음. 업로드 wear도 반복 |
| 장소 전체 바꾸기 | 장소 바꾸기 판에서 그 묶음 전부 2px 또렷, 다른 사진만 흐림. 세트 선택·undo·스튜디오 배경 재저장 |
| D204 낱장 | 묶음 가운데 사진을 다른 세트 멤버로 바꿈. 낱장도 새 소속, 원래 묶음 앞뒤 분리. 첫 장/후기 같은 착용 밖 낱장은 머리 없음. 저장/재진입·이미 사용·남은 멤버·견적 |
| 시그니처 | 쇼룸 첫 장을 열어 시그니처 풀만 확인. 여성/남성 상의·하의·여성 원피스 상태, 선택 즉시 썸네일, 기존 디자인 유지 |
| 색 맞춤 | 분석 템플릿 바로 아래 두 카드. 모노톤 기본→point→템플릿 전환→복원. 흰/회색/검정/미상은 회색, 아이보리/베이지/브라운은 따뜻한 색 |
| 글·SNS·아래 바 | 글 섹션 이름이 목록·추가에 없고 그림은 옅은 줄. SNS 한 자리, footer 끝에서도 아래 바 유지, 판이 열릴 때만 숨김. 에디터 미니 이름·생성 후 글 유지 |

## 바꾼 파일

WOC2 시작 시 읽기 전용 복사본과 비교한 변경 및 새 파일이다. 기존 WOC1~WOC1b 미커밋 변경 전체를 WOC2 변경으로 세지 않았다. 임시 로그/복사본은 아래 코드 목록에서 제외했다.

```text
docs/superpowers/plans/2026-11-05-woc2-conti-v5.md (new)
documents/WOC2_conti_v5_20261105_report.md (new)
public/templates/previews/preview-guide-v1.webp (removed)
scripts/regenerate-page-template-fixtures.mjs
server/app/agents/content_roles.py
server/app/agents/page_style.py
server/app/agents/page_template_assembler.py
server/app/agents/page_templates.py
server/app/data/page_templates_v1.json
server/tests/page_template_cases.py
server/tests/test_page_template_assembler.py
server/tests/test_page_template_quality.py
server/tests/test_page_template_title_band.py
server/tests/test_page_templates_catalog.py
server/tests/test_page_templates_v4.py
server/tests/test_storyboard_v5_sections.py
server/tests/test_woc2_catalog_color.py (new)
server/tests/test_woc2_point_sources.py (new)
server/tests/test_woc2_set_signature_inputs.py (new)
src/features/analysis/AnalysisForm.jsx
src/features/analysis/PageTemplatePicker.jsx
src/features/analysis/PageTemplatePicker.module.css
src/features/storyboard/Storyboard.jsx
src/features/storyboard/StoryboardBackdrop.jsx (new)
src/features/storyboard/StoryboardDrawer.jsx
src/features/storyboard/StoryboardLayout.module.css
src/features/storyboard/StoryboardPageList.jsx
src/features/storyboard/StoryboardPanels.module.css
src/features/storyboard/StoryboardPhotoPanel.jsx
src/features/storyboard/StoryboardPreview.jsx
src/features/storyboard/StoryboardRemoveDialog.jsx (new)
src/features/storyboard/StoryboardSectionPicker.jsx
src/features/storyboard/StoryboardSectionScheme.jsx (removed)
src/features/storyboard/StoryboardSpaceSetPanel.jsx
src/features/storyboard/StoryboardStage.jsx
src/features/storyboard/sectionFlight.js (new)
src/features/storyboard/storyboardPreview.js
src/lib/generationExamples.js
src/lib/pageTemplates/README.md
src/lib/pageTemplates/assemble.js
src/lib/pageTemplates/catalog.js
src/lib/pageTemplates/designChoices.js
src/lib/pageTemplates/designPreview.js
src/lib/pageTemplates/detailBudget.js (new)
src/lib/pageTemplates/pageStyle.js
src/lib/pageTemplates/sectionActions.js
src/lib/pageTemplates/sectionDependencies.js
src/lib/pageTemplates/sectionPlacement.js (new)
src/lib/pageTemplates/seed.js
src/lib/pageTemplates/slots.js
src/lib/pageTemplates/storyboardPositions.js (new)
src/lib/pageTemplates/tokens.js
src/lib/pageTemplates/validateStoryboard.js
src/lib/signatureCutPool.js (restored/new)
src/lib/storyboardCatalogDrag.js
src/lib/storyboardExampleSelection.js
src/lib/storyboardSetSelection.js (new)
src/lib/storyboardSpaceSets.js
src/mock/db.js
src/styles/tokens.css
tests/fixtures/page_templates_cases.json
tests/fixtures/page_templates_quality.json
tests/frontend/compose-mode-location.test.mjs
tests/frontend/detail-recommendations.test.mjs
tests/frontend/helpers/storyboardComponents.mjs
tests/frontend/helpers/storyboardPhotoPanelHarness.mjs
tests/frontend/helpers/storyboardStageHarness.mjs
tests/frontend/horizon-set-all-reference.test.mjs
tests/frontend/outer-closure.test.mjs
tests/frontend/page-template-assemble.test.mjs
tests/frontend/page-template-design-choices.test.mjs
tests/frontend/page-template-entry.test.mjs
tests/frontend/page-template-finish.test.mjs
tests/frontend/page-template-interactions.test.mjs
tests/frontend/page-template-parity.test.mjs
tests/frontend/page-template-preview.test.mjs
tests/frontend/page-template-quality.test.mjs
tests/frontend/page-template-screen.test.mjs
tests/frontend/page-template-seed.test.mjs
tests/frontend/page-template-selection.test.mjs
tests/frontend/page-template-ui.test.mjs
tests/frontend/page-template-v5-actions.test.mjs
tests/frontend/page-template-v5-parity.test.mjs
tests/frontend/page-template-v5-photo-actions.test.mjs
tests/frontend/page-templates-catalog.test.mjs
tests/frontend/product-cut-direction.test.mjs
tests/frontend/storyboard-catalog-drag.test.mjs
tests/frontend/storyboard-entry-placement.test.mjs
tests/frontend/storyboard-inspector-focus.test.mjs
tests/frontend/storyboard-qa-b3.test.mjs
tests/frontend/storyboard-render-groups.test.mjs
tests/frontend/storyboard-v5-round2.test.mjs
tests/frontend/storyboard-woc1b-visual.test.mjs
tests/frontend/storyboard-woc1b.test.mjs
tests/frontend/woc2-catalog-color.test.mjs (new)
tests/frontend/woc2-points-dependencies.test.mjs (new)
tests/frontend/woc2-set-dnd.test.mjs (new)
tests/frontend/woc2-sets-signature.test.mjs (new)
tests/frontend/woc2-storyboard-ui.test.mjs (new)
```


## 2라운드

실제 모델: gpt-6.1-sol · effort: max. 이번 턴의 turn_context에서 확인했다. 총괄의 2026-11-05 화면 지적 7개를 반영했다. 기존 미커밋 변경을 보존했고, 브라우저·git 쓰기·메인 코드 수정·유료 API 호출은 하지 않았다. 제공된 crop 근거 그림을 파일로 읽어 왼쪽 조립 영역과 무대의 차이를 확인했다.

| 항목 | 원인과 고친 곳 |
|---|---|
| 1. 172px 미적용 | 모듈의 `.page`와 공용 테마의 `.sb-v5-theme`가 같은 변수의 소유자여서, 같은 요소의 선언 순서나 반복 테마 루트에서 기본값 184px이 다시 선택될 수 있었다. 좁은 화면 재선언을 `tokens.css`의 기본 테마 바로 뒤로 옮겨 body·페이지·portal의 모든 테마 루트가 같은 media 값을 받게 했다. 모듈의 변수 재선언은 제거했다. 1280·1359px에서는 썸네일 172/칸 216px, 1360·1440·1920px에서는 184/228px이 되도록 선언 위치·순서·해결값을 시험했다. |
| 2. 제목 두 줄 | 공통 6px gap 두 개가 이름의 가용 폭을 줄였다. `sectionTitle`의 gap은 4px, 컷 수의 왼쪽 margin은 2px로 해 두 구간을 4px/최소 6px로 만들었다. 이름의 min-width는 0, flex-wrap과 어절 줄바꿈은 유지한다. 총괄 측정 폭 53+77+23+4+6=163px이 184px 썸네일의 안쪽 164px 안에 들어가는지 시험했다. 172px에서 긴 이름이 두 줄이 되는 것은 허용하며 말줄임을 넣지 않았다. |
| 3. 빈 머리 띠 | 내용 없는 `groupHead`도 매번 렌더링해 26px+8px을 차지했다. `StoryboardStage`가 행 안에 실제 장소/색 머리가 있는지 먼저 판단하고, 하나도 없으면 머리 칸을 렌더하지 않는다. 머리가 있는 행은 빈 이웃 칸의 자리도 유지한다. 추가 타일의 기본 위 margin은 0, 머리가 있는 행에만 같은 34px을 둔다. `--sb-group-head-height: 26px` 토큰을 머리·교체 알약·추가 타일 정렬에 함께 쓴다. |
| 4. 색 이름 | 색 묶음의 글을 고정 “두 번째 색”으로 만들던 분기를 없앴다. 색상과 제품 섹션에는 묶음 머리가 없으며, 사진 아래 회색 칸은 기존 `colorDisplayName`으로 상품 이름을 먼저 읽는다. 뒷면 방향/고정 뒷면 자리에는 “뒷면”을 쓴다. 일상 추가 색 묶음은 네이비·민트·차콜처럼 실제 상품 색 이름이고, 기준 색에 더한 묶음의 “추가한 컷”은 유지한다. 이름이 없는 경우에만 기존 swatch/색상 순번 fallback을 쓴다. |
| 5. 섹션 추가 알약 | 헤더 추가 단추가 전체 보기의 공통 글자 단추 규칙만 받았다. 별도 `outlineAdd` 클래스로 1px 테두리·흰 바탕·pill 모서리를 복원하고 글을 “＋ 섹션 추가”로 바꿨다. 전체 보기의 글자·밑줄 규칙과 기존 클릭 동작은 그대로다. |
| 6. 기타 칸 | 장소 분류 함수의 unknown 결과를 무대가 그대로 표시했다. 무대 caption에서 unknown/studio는 장소 칸을 만들지 않고 오른쪽 컷 번호만 둔다. 알려진 장소는 유지한다. 사진 판의 분류·필터·카탈로그 값은 바꾸지 않았다. |
| 7. 따온 영역 | 바깥 SVG viewBox만 최종 크기에 맞춰도 그 안의 원본 image가 영역 밖까지 그려져, 세로 카드의 여백에 원본이 비쳤다. 무대 crop 안에 최종 조립 이미지의 w/h와 같은 SVG viewport를 하나 더 두고 overflow=hidden으로 자른다. 그 결과를 바깥 SVG의 `xMidYMid meet`로 3:4 카드 안에 넣고 남는 곳은 bg-2로 둔다. 최종 src·iw/ih·-ox/-oy는 그대로 쓰며, 상의·아우터·하의·원피스의 실제 조립 결과로 시험했다. `seed.js`의 모든 영역값, 조립 엔진, `StoryboardPreview.jsx`와 왼쪽 썸네일은 수정하지 않았다. 기존 어깨선/목선 영역의 겹침을 다시 정하는 작업도 하지 않았다. |

바꾼 파일은 `StoryboardLayout.module.css`, `StoryboardPageList.jsx`, `StoryboardStage.jsx`, `tokens.css`, `pageTemplates/README.md`, 이 보고서다. 시험은 새 `tests/frontend/woc2-round2-presentation.test.mjs`와 기존 `woc2-storyboard-ui.test.mjs`, `page-template-ui.test.mjs`, `helpers/storyboardComponents.mjs`를 갱신했다. 기존 색 이름 함수는 가져와 쓰기만 했으며 서버 코드·저장 모양·crop 데이터는 바꾸지 않았다.

| 시험 명령 | 실제 결과 |
|---|---|
| `node --test tests/frontend/woc2-round2-presentation.test.mjs` | 최초 **0 통과/11 실패**를 확인한 뒤 수정. 마지막 **11 통과/0 실패**, 0.45초. CSS 변수 선언 소유자/순서, 4px·6px 간격, 빈 행·혼합 행, 색/뒷면 caption, 알약, unknown 생략, 네 옷 종류 crop clipping과 영역값 고정을 검사한다. |
| `node --test tests/frontend/woc2-round2-presentation.test.mjs tests/frontend/woc2-storyboard-ui.test.mjs tests/frontend/page-template-ui.test.mjs tests/frontend/storyboard-woc1b-visual.test.mjs tests/frontend/storyboard-similar-oncard.test.mjs` | **49 통과/0 실패**, 0.70초. 이후 강화한 전체 crop 값 검사는 위 11개 재실행으로 다시 통과했다. |
| `pnpm test:frontend` | **4,958 통과, 0 실패, 0 건너뜀**, exit 0. 54.07초. |
| `server/`에서 `/Users/daily/Documents/wearless_studio/server/.venv/bin/pytest -q --ignore=tests/test_personalization.py` | **10,755 통과, 183 건너뜀, 0 실패**, 경고 1,043개, exit 0. 266.94초. |
| `pnpm build` | **성공**, exit 0. 11.80초. 큰 청크·의존성 주석 경고 외 오류 없음. |
| `git diff --check` | 공백 오류 없음. git 읽기만 했다. |

로그는 `.scratch/woc2-round2-20261105/`에 있다. 최초 실패를 통과로 쓰지 않았고, `seed.js` 전체도 작업 전 SHA256과 같음을 별도로 확인했다.

화면은 실행하지 않았다. 총괄은 앞의 목 주소 두 개에서 다음 장면을 다시 확인한다.

- 1440→1360→1359→1280에서 썸네일/칸이 184/228→172/216px로 바뀌는지, 테마가 중첩돼도 유지되는지.
- 쇼룸 “Section 2 스튜디오 착용컷 10컷”이 184px에서 한 줄인지, 좁은 창에서 어절로만 감싸는지. 헤더의 ＋ 알약과 전체 보기 글자 단추도 확인한다.
- 첫 장·디테일 요소·색상과 제품의 맨 위 빈 띠가 사라졌는지. 장소 머리와 함께 놓인 독립 컷·추가 타일은 높이를 맞추는지.
- 색상과 제품에 상품 색 이름/뒷면만, 추가 색 착용 묶음에 해당 색 이름이 보이는지. 장소가 없는 예시에 기타 칸이 없고 번호는 남는지.
- 디테일 요소를 빼고 다시 넣어 crop 세 장을 만든 뒤, 무대가 왼쪽 조립 썸네일과 같은 영역만 보이고 남는 곳이 회색인지. 이번 라운드는 표시를 고친 것이며 crop 영역값 판단은 총괄의 다음 결정으로 남긴다.


## 3라운드

실제 모델: gpt-6.1-sol · effort: max. 이번 턴의 최근 turn_context를 읽어 확인했다. 총괄이 직접 고친 `.sectionTitle` gap `var(--sp-3)`와 컷 수 margin-left `0`, R2-2의 실측 계산 `53+81+23+3+3`을 그대로 보존했다. 시작 복사본과 해당 CSS 두 규칙 및 시험 본문이 같은지도 확인했다. 기존 미커밋 작업을 되돌리지 않았으며 브라우저·git 쓰기·메인 코드 수정·유료 호출은 하지 않았다. 전달한 crop_study.png는 파일로 읽었다.

| 항목 | 원인과 고친 곳 |
|---|---|
| 1. 좁은 머리 | 216px 칸의 머리에 세 항목을 넣어 각각 감쌌다. `StoryboardPageList.jsx` 머리에는 “페이지 구성”과 “＋ 섹션 추가”만 두고, 제목/단추에 nowrap·줄어들지 않음을 지정했다. “전체 보기”는 목록 스크롤 안의 마지막 섹션과 끝 drop 자리 다음으로 옮겼다. 기존 글자 단추 모양·12px·밑줄·콜백을 유지한다. 제목 줄의 총괄 수정은 건드리지 않았다. |
| 2. 0컷 원단 | 기존 숨김 판정은 종류가 글 전용인 경우만 봤다. `sectionRuns.js`의 `isTextOnlySection`에 실제 non-text 블록이 없는 fabric run을 포함했다. 저장/최종 조립에서는 그대로 남고, 왼쪽/전체 보기/추가 후보에서는 빠진다. 사진이 있는 원단은 계속 보인다. 새 추가 후보도 실제 시드가 글 대체뿐이면 제외한다. 무대의 원본 Section 번호와 옛 활성 키 fallback도 같은 visibleSectionRuns를 사용한다. 숨은 run을 옮기거나 지우는 변경은 하지 않았다. 두 템플릿 사진 유무, 숨은 원단 사이 자동 삽입·이동·되돌리기·저장 검증을 시험했다. |
| 3. crop 품질 | 두 엔진의 확대 계산을 `min(영역을 채우는 배율, 슬롯폭/(원본폭×0.3), 실제 크기를 알 때 1)`로 맞췄다. 확대 후 `max(슬롯폭, 이미지폭)`으로 다시 늘리던 처리를 제거해 작은 사진도 native 1배 제한을 지킨다. seed의 12개 영역을 아래 값으로 바꾸고 이름/bodyPart는 유지했다. JS/Python 검증의 옛 50% 영역 폭 제한은 양의 폭/높이로 바꾸되 유한값·범위·원본·부위 검사는 유지했다. 이로써 요청한 28% 목선 ROI도 저장되며 실제 확대는 30% 폭 제한을 받는다. |

| 옷 종류 | 첫째 영역 | 둘째 영역 | 셋째 영역 |
|---|---|---|---|
| top | [0.44,0.20,0.30,0.16] | [0.36,0.20,0.28,0.17] | [0.30,0.27,0.40,0.24] |
| outer | [0.44,0.20,0.30,0.16] | [0.28,0.20,0.44,0.34] | [0.30,0.40,0.40,0.24] |
| bottom | [0.34,0.36,0.32,0.14] | [0.30,0.40,0.40,0.50] | [0.30,0.72,0.40,0.24] |
| dress | [0.44,0.20,0.30,0.16] | [0.30,0.30,0.40,0.40] | [0.30,0.60,0.40,0.26] |

저장된 `pointItems.cropRegion`은 원본 ID가 같으면 그대로 쓴다. 섹션 순서 이동, 재진입, 같은 원본의 설정 변경으로 덮어쓰지 않는다. 새로 넣는 항목과 실제 다른 원본 ID로 옮겨지는 항목에만 현재 옷 종류의 영역을 bodyPart로 찾아 적용한다. 이전 사용자 영역을 유지하는 고정 사례 두 개도 남겼다. 2라운드 무대의 중첩 SVG clipping은 바꾸지 않아 계속 최종 조립 영역만 표시한다.

실제 크기를 모르는 경우의 판단도 분리했다. 예시 카탈로그에는 실제 width/height가 없으며 `storyboardPreviewCuts`의 1024×1365는 기존 비율 추정값이다. 이 추정값에 native 제한을 적용하지 않도록 임시 cutResults에만 `dimensionsKnown:false`를 붙였다. 두 엔진 모두 이 경우 30% 규칙만 쓴다. 저장 블록/DB 필드는 추가하지 않았고, 실제 생성 결과의 양의 pixel 크기가 있으면 1배 제한을 쓴다.

작은 원본은 빈 공간이 생겨도 다시 늘리지 않는다. 96×144px 원본은 428×428px 칸에서도 96×144px, 1024×1536px 사례는 1배 이하, 3072×4608px 사례는 1427×2140px로 줄었다. 작은 원본의 offset은 기존처럼 음수가 되지 않게 두므로 오른쪽/아래에 공간이 남을 수 있다. 두 엔진의 기존 정수 반올림도 유지해 30% 경계에는 1px 미만 차이가 생길 수 있다. 이는 새 보간·이동 규칙을 더하지 않고 지정된 확대 제한을 적용한 판단이다.

고정 사례는 105개에서 **111개**로 다시 만들었다. 저장된 옛 영역 두 사례, 작은/큰/크기 미상/추정 미리보기 원본을 더했고, JS/Python 결과가 정확히 같은 것을 확인한 뒤 썼다. 카탈로그 디자인이나 저장 필드를 늘린 작업은 아니다.

읽기 전용 별도 검토는 Critical/Important/Minor 지적 없이 끝났다. 두 템플릿×네 옷 종류×아홉 크기의 216개 crop 계산과 24개 원본 재배정 검사를 실행했고, 블록 입력이 바뀌지 않음도 확인했다. 실제 브라우저 화면은 금지 범위여서 검토하지 않았으며 아래 총괄 장면으로 남긴다. 전체 시험·빌드는 루트 구현 에이전트가 직접 순서대로 실행했다.

| 시험 | 실제 결과 |
|---|---|
| 새 UI `woc2-round3-presentation.test.mjs` | 최초 **8 실패/3 통과**, 수정 뒤 **11 통과/0 실패**. 이전에 이미 되던 사진 원단과 추가 가능 원단도 함께 보존 검사했다. |
| 새 crop `woc2-round3-crops.test.mjs` | 최초 **24 실패/2 통과**, 수정 뒤 **26 통과/0 실패**. JS 입력·저장 검증과 실제 Python 결과를 비교한다. |
| 새 서버 `test_woc2_round3_crops.py` | 최초 **12 실패/9 통과**, 검증 수정 뒤 별도로 계산 실패도 확인, 최종 **21 통과/0 실패**. |
| UI 통합 6파일 | **58 통과/0 실패**, 총괄이 고친 R2 제목 시험 포함. |
| crop·시드·의존·기존 사진동작 집중 | **135 통과/0 실패**. 고정 사례·조립 집중 JS **150 통과**, Python **176 통과**. |
| `PAGE_TEMPLATE_PYTHON=/Users/daily/Documents/wearless_studio/server/.venv/bin/python node scripts/regenerate-page-template-fixtures.mjs` | **111개**, 두 엔진 결과가 정확히 일치한 뒤 재생성. 루트에서 재생성해도 byte 동일, SHA256 be1c842d899442708fb9b4c90bd5ac4c844909d83c4f2ab39707bb6e76e8bdc7. |
| `pnpm test:frontend` | **5,001 통과, 0 실패, 0 건너뜀**, exit 0. 53.07초. frontend-final.log |
| `server/`에서 `/Users/daily/Documents/wearless_studio/server/.venv/bin/pytest -q --ignore=tests/test_personalization.py` | **10,782 통과, 183 건너뜀, 0 실패**, 경고 1,043개, exit 0. 244.75초. server.log |
| `pnpm build` | **성공**, exit 0. 7.96초. 큰 청크·의존성 주석 경고 외 오류 없음. build.log |
| `git diff --check` | 공백 오류 없음. git 읽기만 했다. |

첫 전체 프론트 실행은 5,000통과/1실패였다. 실패한 `R2-3 only everyday wear exposes cut/set addition and the twelve-cut hint`는 사진 없는 원단을 보이는 사진 섹션으로 만들고 일반 사진 안내를 기대했다. 실제 원단 사진이 있는 시드로 바꿔 기존 안내/추가 제한을 계속 검사하고, 사진 없는 숨김은 새 시험이 담당하도록 고쳤다. 실패를 생략하거나 통과로 세지 않았다. 로그는 `.scratch/woc2-round3-20261105/`에 있다.

바꾼 코드는 `StoryboardPageList.jsx`, `StoryboardLayout.module.css`, `StoryboardSectionPicker.jsx`, `StoryboardStage.jsx`, `Storyboard.jsx`의 활성 run 선택 한 줄, `storyboardPreview.js`, `sectionRuns.js`, `seed.js`, `assemble.js`, `sectionDependencies.js`, `validateStoryboard.js`, Python `page_template_assembler.py`·`content_roles.py`다. fixture 재생성 도구/사례, 새 UI·crop 시험과 직접 바뀐 계약의 기존 시험, README·계획·이 보고서를 함께 갱신했다. 공용 색·폰트·앞 작업은 되돌리지 않았다.

총괄 확인 장면은 기존 목 주소 두 개에서 만든다.

- 크롬·웹킷 1280/1440: 머리에 두 항목만 한 줄, 전체 보기는 목록 맨 아래. 전체 보기→편집 복귀, 추가 판 열기/닫기, 마지막 drop 자리 동작 유지.
- 원단 사진이 없는 기본 상태: 목록과 추가 판에 0컷 원단 없음. crop 원본 Section 번호도 실제 보이는 목록 번호와 일치. 최종 페이지에는 원단 글 run 유지.
- 원단 사진 후보가 있는 분석 상태: 원단 사진 칸이 있는 run은 계속 보임. 사진 없는 원단 앞뒤 섹션을 넣고 옮겨도 숨은 run의 저장 위치 유지.
- 새 디테일 요소를 넣어 상의/아우터/하의/원피스의 세 부위 확인. 같은 원본의 이전 저장 항목은 기존 영역, 원본 섹션을 빼서 다른 사진으로 옮기면 새 영역. 실행 취소는 이전 값 전체 복원.
- crop은 회색 여백 밖으로 원본이 새지 않고 왼쪽 조립과 같은 영역만 보임. 작은 실제 원본은 강제로 늘리지 않음. 새 확대/영역의 주관적 품질은 제공한 연구 그림과 화면에서 총괄이 확인.
