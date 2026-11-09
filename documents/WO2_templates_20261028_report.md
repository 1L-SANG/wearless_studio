실제 모델 / effort: 세션에서 세부 식별값을 확인할 수 없음. 요청값은 `gpt-6.1-sol / max`이며, 적용됐다고 단정하지 않는다.

# WO2 결과 — 2026-10-28

## 결과

- WO1 2라운드의 미커밋 변경 위에서 구현했다. WO1 공유 카탈로그와 fixture, 가운데 글 대비, 안내형 목 아래 자르기 값은 되돌리지 않았다. 카탈로그·토큰·대비 모듈·에디터와 대기 뼈대의 시작 스냅샷 해시도 그대로임을 확인했다.
- 분석 확인 자리를 세 템플릿 카드로 바꾸고 견본 사진임을 표시했다. 마지막 선택을 먼저 읽으며, `pageStyle` 저장을 기다린 뒤 진행한다. 이번 선택은 `template` 글꼴과 `mono` 색이다. 로그인 전 초안·승격·PC 초안 복원은 분석 객체 전체를 유지하는 기존 경로를 쓴다.
- `GET /v1/page-style/last`, 허용 목록 검사, `page_style_locked` 409, 재분석 payload·완료 응답·이벤트 이월을 구현했다. 스타일 저장과 마네킹 작업 생성은 같은 프로젝트 advisory transaction lock을 공유한다. `locked` 열은 기준으로 쓰지 않는다.
- 콘티는 v4 섹션 run이다. 세 템플릿 기본 인스턴스 10 / 7 / 8개, 착용 사진 사용 방식, 칸별 촬영 방식·샷, 네 묶음 추가 메뉴, 섹션 빼기·되돌리기, 글 카드, 촬영 방식 라벨을 연결했다. 사진 양·훅 스타일 UI와 v3 기본 구성을 제거했다.
- 생성 전 옛 콘티는 새 템플릿으로 구성해 안내한다. 생성한 옛 페이지는 안내와 `/editor/<projectId>` 버튼을 보여 준다. v4가 아닌 저장·생성은 `storyboard_outdated` 400이다.
- `blocks: list`를 유지했다. DB 마이그레이션, git 쓰기, 브라우저 실행, 유료 API 호출, 결과 탭 자동 열기는 하지 않았다.

## 시험 결과

루트의 프론트·빌드와 `server/`의 서버 시험을 실행했다. 브라우저와 유료 API는 사용하지 않았다.

| 명령 | 최종 결과 |
|---|---|
| `pnpm test:frontend` | **4,495 통과, 실패·건너뜀 0** |
| `/Users/daily/Documents/wearless_studio/server/.venv/bin/pytest -q` | 최초 전체 실행: 로컬 DB `127.0.0.1:54322` 연결 불가로 `test_personalization.py` **105개 setup 오류**. 통과로 세지 않음 |
| `/Users/daily/Documents/wearless_studio/server/.venv/bin/pytest -q --ignore=tests/test_personalization.py` | **10,428 통과, 183 건너뜀, 실패·오류 0** |
| `pnpm build` | **성공**. 기존 큰 청크·의존 라이브러리 주석 경고 있음 |
| `git diff --check` | 통과 |

최종 로그: `.scratch/wo2/server-verified.log`, `.scratch/wo2/frontend-signoff.log`, `.scratch/wo2/build-signoff.log`. 화면 모양·320/360/375/390/414px·WebKit·스크린샷 검사는 실행하지 않았다. 브라우저 없이 React SSR로 분석 선택 카드, 세 템플릿 콘티, 옛 콘티 재구성·에디터 안내가 실제 컴포넌트에서 렌더링되는지는 검사했다.

마지막 조립·저장 보완 뒤 관련 서버 **92개**, 템플릿 관련 프론트 **97개**, 화면·비슷한 컷 렌더 **8개**도 선택 실행에서 통과했다. 원본·후속 카드의 이동·교환 보완 뒤 관련 **26개**도 통과했다. 전체 결과와 중복 집계하지 않는다.

새 핵심 시험:

- `page-template-seed.test.mjs`: 세 템플릿 × 1·2·3색, 후보 0~4개, 원단 우선·전체 3장 한도, 자른 착용 사진, 실패·삭제·견적, 실제 JS→Python 조립 좌표 일치, 메뉴·반복·지문·옛 보드·bottomClear.
- `page-template-selection.test.mjs`: 게스트 분석 병합, 다음 프로젝트 기본값, 마네킹 작업 뒤 전체 스타일 잠금, 초안 승격과 PC 복원.
- `page-template-screen.test.mjs`: 실제 선택 카드·콘티·옛 페이지 안내의 SSR 렌더. 브라우저 검사 대체로 주장하지 않는다.
- `test_page_templates_v4.py`: 저장 구조 거절, 슬롯 복구, 글 카드 모양, 스타일 검사·잠금·기본값·이월, 옛 보드 400, 글 카드 제외 견적.
- 기존 비슷한 컷·revision 충돌·장소 세트·빈 칸 재시도·AI로 채우기·정산·실제 모델 라이선스 시험을 v4 입력으로 갱신했다. **시험 helper에서만** v4 필드를 보완하며, 앱에는 v3 변환 경로가 없다.

## D88 상품 포인트

번호형 `PT-EMO-01`, `P06-D01`, `T05-D07`의 글 카드에 `pointItems`를 저장한다. 실제 디테일 카드 뒤 부족한 항목을 같은 페이지 호리존 핏 확인용 앞·뒤 사진 참조로 채운다. 새 사진 카드는 만들지 않는다.

| 후보 조건 | 원단 사진 | 번호형 상품 포인트 |
|---|---:|---|
| 0개 | 0, 글형 | 착용 자르기 3개 |
| 원단 1개 | 1 | 착용 자르기 3개 |
| 원단 + 디테일 1개 | 1 | 디테일 1 + 착용 자르기 2 |
| 원단 + 디테일 2개 이상 | 1 | 디테일 2 + 착용 자르기 1 |
| 원단 없이 디테일 3개 | 0, 글형 | 디테일 3개 |

- `pointItems[]`: `kind: detail | wornCrop`, `sourceBlockId`, 디테일 대상 또는 `bodyPart`, `label`, `cropRegion: [x,y,w,h]`(원본 비율).
- 부위는 어깨선·목선이 앉는 모양·기장·실루엣·입었을 때 허리선으로 제한한다. 자른 항목에는 `featureCopy`의 구조 설명을 자동 대입하지 않는다. WO3는 이 항목 종류와 부위를 읽어 핏 설명을 쓸 수 있다. 현재 AI 글은 자리표시다.
- 이미지 요소는 원본 `sourceBlockId`와 편집기의 `crop: {ox,oy,iw,ih}`를 가진다. 실제 표시 원본 폭이 절반 미만이 되지 않게 확대도 제한한다. `pointItemKind`, `bodyPart` 메타도 남긴다.
- 원본 실패는 같은 실패 표시, 원본 삭제는 해당 번호 항목 숨김이다. 원본이 없는 사진·번호를 새로 생성하지 않는다. 생성 장수·견적에 더하지 않는다.
- 안내형 `T07-D06` 목 아래 자르기 값은 WO1 2라운드 그대로다.

## 예시 꼬리표

`server/app/data/example_tags.json`에 지정 파일을 그대로 복사했다(낱장 380, 세트 74). 원본과 복사본 SHA-256은 모두 `178ea36800b10eab4c85dcbee83e4822c91f55800c6f427342468d8318b3c923`이다.

읽기: `src/lib/pageTemplates/examplePreferences.js` → 일반 자동 배정기의 후보 정렬. `emptyBottom`이 `review`에 있는 낱장은 그 값만 버린다. 다른 꼬리표 때문에 `status: review`인 낱장의 값은 쓴다. 믿을 수 있는 true 후보가 없으면 기존 후보와 순서를 그대로 돌려준다. `topClear`는 사용하지 않는다.

## 글 카드 확인표

작업 시작 스냅샷을 다시 세었다. 비교식 등장 횟수 기준 JS **65개**, Python **15개**이며 WO1 추가 조립 경로도 포함한다. 아래 숫자는 `mine` 비교식 수다. 남아 있는 비교가 곧 AI 판정이라는 뜻은 아니다.

| 파일 | 시작 → 현재 | 확인·처리 |
|---|---:|---|
| `server/app/agents/content_roles.py` | 5 → 3 | text 모양 선검사, null 역할 유지. mine과 ai 분리. 유사 컷도 ai 원본만. |
| `server/app/agents/horizon_background.py` | 3 → 3 | 기존 비교 유지: horizon·garment-tone·signature 추가 조건이 있어 생성 필드 없는 text는 모두 제외. |
| `server/app/agents/page_assembler.py` | 5 → 0 | v3 사진별 조립 루프와 source!=mine 분기 삭제. |
| `server/app/agents/page_template_assembler.py` | 2 → 2 | 사진 목록에서 text를 제외한 뒤 mine/생성 사진을 분기. |
| `src/features/storyboard/Storyboard.jsx` | 28 → 12 | 생성·견적·예시·인스펙터는 ai만. 사진 수·번호도 text 제외. text는 별도 카드, 삭제는 섹션 단위. |
| `src/lib/api/shapes.js` | 1 → 0 | 사진 양별 시드·지문·훅 스타일 분기 제거. 분석 기본 shape만 유지. |
| `src/lib/customMatchPromotion.js` | 1 → 0 | 매칭 승격은 source=ai인 착용 사진에만 적용. |
| `src/lib/detailRecommendations.js` | 2 → 1 | 디테일 판정은 ai만. mine 전환 때 대상 연결 해제는 유지. |
| `src/lib/generationCutCount.js` | 1 → 0 | source=ai만 계산. text는 0장·0크레딧. |
| `src/lib/horizonBackground.js` | 3 → 0 | 측정·세트 배경 변경은 ai만. |
| `src/lib/horizonBackgroundAvailability.js` | 1 → 0 | 배경 가용성은 ai 호리존 멤버만. |
| `src/lib/identityScope.js` | 1 → 0 | text와 mine은 생성 범위 필터에서 통과. 거절 대상은 ai만. |
| `src/lib/pageTemplates/assemble.js` | 2 → 2 | run에서 text를 사진 목록에서 제외한 뒤 mine/생성 사진을 분기. |
| `src/lib/sections.js` | 9 → 0 | v4만 검사. 글 카드는 촬영 역할 null, 행·네 섹션 정렬 제거. |
| `src/lib/storyboardEntryPlacement.js` | 2 → 0 | 옛 오프닝·진입 이월 제거. 세트 고르기 함수만 유지. |
| `src/lib/storyboardHookFrame.js` | 3 → 0 | 파일 삭제. 글 카드에 적용할 훅 프레임 경로 없음. |
| `src/lib/storyboardSpaceSets.js` | 1 → 0 | 세트 생성은 명시적인 사진 액션. 멤버 source=ai, 새 slotKey=null. |
| `src/lib/storyboardTaxonomy.js` | 4 → 3 | text를 먼저 통과, mine은 custom. AI만 칸 레시피·hero 배정. |
| `src/mock/db.js` | 6 → 0 | 옛 조립 제거. cutResults는 source=ai만 생성. |

화면의 사진 번호와 AI/셀러 사진 수는 글 카드를 제외한다. 내 사진으로 바꾼 기존 카드는 템플릿의 원래 `slotKey`를 유지한다.

서버 생성·견적(`routes.py`, `detail_page_job.py`)은 `source == ai`만 쓴다. 새 seed·메뉴·검사기·pointItems 조립 경로도 별도로 검사했다. 글 카드에는 촬영·예시·세트·매칭·참고 사진 필드가 없고 비용과 실패 항목을 만들지 않는다.

## 삭제한 v3 코드와 유지한 코드

삭제 파일:

- `server/app/agents/hook_title_metrics.py` — 서버 v3 제목 조립 전용 표. 프론트 수동 제목용 표는 유지.
- `src/features/analysis/composeModeSelection.js`
- `src/features/storyboard/storyboardComposeMode.js`
- `src/lib/signatureCutPool.js`
- `src/lib/storyboardHookFrame.js`

삭제·대체한 주요 함수:

- `shapes.js`: `defaultStoryboard`, `setMemberBlocks`, `stylingFallback`, `horizonRotationFallback`, `storyboardTemplateFingerprint`, `applySeededHookStyle`, `isDefaultStoryboardForMode`.
- `storyboardEntryPlacement.js`: `applyOpeningRow`, `hasOpeningRow`, `isOpeningPair`, `migrateLegacyEntryStylingRuns`.
- `Storyboard.jsx`: `deriveFixedSections`, `ComposeModeSegment`, `HookStyleChip`, `SignatureCutGallery`, `StoryboardFrame`, `StoryboardMoodGrid`, 훅 스타일 전환, 사진 양 전환, 숨겨진 옛 행 레이아웃 메뉴.
- `sections.js`: 네 섹션 안정 정렬·촬영 방식 강제·v3 승격을 v4 검사로 교체. `patchSection`, `normalizeRows`, `normalizeSectionLayouts`, `gridLayoutForCount`, `rowSizeFor`, `LAYOUT_ROW` 제거.
- `storyboardUnits.js`: v3 `frameUnits`를 개별 카드용 `cardUnits`로 교체. 세트 run 보호는 유지.
- 서버 `content_roles.canonicalize_storyboard`: v3 재정렬·섹션별 컷 강제를 제거하고 v4 run 검사로 교체.
- 서버 `page_assembler`: 옛 사진별 조립, `_image_el`, `_image_box_for_width`, `_image_box`, `_caption_height`, `_hook_title_layout`, `_block_height`, `_cut_meta_by_block`, `_copy_texts_by_block`, `_text_for_role`, `_overview_label`, `_color_label_style`, `_single_tag`, `_row_label`, `_colorway_pair`, `_display_text`, `_colorway_labels`와 전용 상수 제거.
- 작업자 `_repair_signature_storyboard`와 v3 전용 우회 분기 제거. 목의 옛 사진별 조립과 사진 양 재시드도 제거.

유지:

- 목 초기 마네킹 견본은 `previewOnly`로 구분한다. 목에서도 실제 생성 컷과 마네킹 작업 기록은 스타일을 잠근다.
- `composeMode` DB·프로젝트 필드와 store API: 기존 초안·프로젝트 계약의 필드이므로 남긴다. v4 기본 구성과 UI는 읽지 않는다.
- `inferSectionRole` / `resolve_section_role`의 과거 역할 해석, `canonicalize_storyboard_block`의 일반 레시피 처리: 에디터 자동 채움·독립 이미지 생성·옛 에디터 재시도가 사용한다. 콘티 목록은 이 해석으로 v3를 승격하지 않는다.
- 서버 signature 자산 라우팅·배경·픽셀 메타: `editor_image_job.py`와 옛 에디터 원본 다시 만들기 등이 사용한다. 기본 콘티와 선택 UI에서는 사용하지 않는다.
- `editorTextSlots.js`의 수동 제목·캡션 프리셋과 관련 글 높이 규칙: 에디터 기능이다.
- 자동 사이즈·세탁·AI 안내·라이선스 QR, 실제 모델 범위, 장소 세트, 비슷한 컷, revision 동기화, 정산·재시도 경로.

## 계약·시안과의 관계와 문서에 반영할 사실

- 총괄 확정 D86·D88을 우선했다. 가운데 첫 장 글·대비는 WO1 그대로이며, D88이 기존의 두 곳만 재사용·사진 0장 번호형 숨김 규칙을 덮어쓴다. 그 밖에 임의로 바꾼 계약은 없다.
- 선택 카드의 전용 래스터가 정해지지 않아 기존 로컬 견본 사진 `public/models/demo/cut1.webp`, `cut2.webp`와 CSS로 가운데 글·사진 아래 제목 등 첫 장 차이를 표현했다. 실제 상품 사진과 혼동하지 않도록 견본임을 표시한다.
- 글꼴·색 선택, 실제 AI 카피, 컷 수 최종 확정, 디자인 바꾸기, 구조 근접 착용샷, 공통 계약·ADR 수정은 하지 않았다.
- 문서 반영 필드: `analysis.pageStyle`, v4 섹션·칸 필드, `source: text`와 생성 설정 필드 금지, `pointItems`·`cropRegion`, 에디터 이미지의 `pointItemKind`·`bodyPart`, 기본 콘티 지문 `seedFingerprint`·`seedProductKey`.
- 문서 반영 API·에러: `GET /v1/page-style/last`, `invalid_page_style` 400, `page_style_locked` 409, `page_style_mismatch` 400, `storyboard_outdated` 400, `invalid_storyboard_section`, `invalid_storyboard_design`, `invalid_storyboard_usage`, `invalid_storyboard_block_id`, `invalid_storyboard_text_card`, `invalid_storyboard_source`, `invalid_storyboard_slot`, `invalid_storyboard_point_items`, `mixed_storyboard_templates`, `noncontiguous_storyboard_section`, `invalid_storyboard_first`, `repeated_storyboard_section` 400 코드. 스타일 잠금은 유효한 객체 전체 값 비교이고 동일 객체 저장은 허용한다.
- 문서 반영 동작: 템플릿 순서 보존, 첫 장 재추가 시 맨 앞, 섹션 안 카드 재정렬, 유사 컷의 같은 섹션·`slotKey: null`과 원본 이동·교환 시 후속 카드 동반 이동, 글 카드 0원, 원단 포함 디테일 3장, 자른 착용 항목의 생성 제외, 옛 보드 읽기 안내, 재분석 이월, 위 삭제·유지 함수 목록.

## 총괄 화면 검사 경로

Codex는 서버나 브라우저를 화면 표시용으로 띄우지 않았다. 총괄이 새 목 세션에서 다음 명령을 실행하면 된다.

```sh
pnpm dev:mock --host 127.0.0.1 --port 5173
```

| 확인할 화면 | URL과 조작 |
|---|---|
| 분석의 템플릿 고르기 | `http://127.0.0.1:5173/create/input?facemarket=0` → 목 상품 사진 확인·분석 → 코디 고르기 단계 끝. 세 템플릿 카드와 견본 안내, 확인 저장 대기 확인 |
| 감성형 콘티 | `http://127.0.0.1:5173/create/storyboard?facemarket=0&mockTemplate=emo` |
| 쇼룸형 콘티 | 위 URL의 `mockTemplate=show` |
| 안내형 콘티 | 위 URL의 `mockTemplate=guide` |
| 섹션 추가 메뉴 | 콘티 위 `＋ 섹션 추가` → 네 묶음 확인. 착용 사진 두 사용 방식·두 번째 상품 포인트·SNS 추가 → 섹션 빼기 → 하단 변경 되돌리기 |
| 생성 전 옛 콘티 | `http://127.0.0.1:5173/create/storyboard?facemarket=0&mockTemplate=emo&mockLegacy=before` → 재구성 한 줄과 새 섹션 |
| 생성한 옛 콘티 | 위 URL의 `mockLegacy=after` → 한 줄 안내와 에디터 버튼 → 기존 에디터 페이지 확인 |

쿼리는 목 데이터에만 적용된다. 서로 다른 시나리오는 새 탭의 새 목 세션으로 여는 것이 가장 확실하다. 폰·태블릿의 사진 입력 뒤 PC 이어 하기 정책은 유지했다.

남은 위험: 실제 사진의 자르기 부위와 가독성, 작은 폭·WebKit·내보내기는 총괄 시각 QA가 필요하다. DB 의존 105개는 연결 가능한 환경에서 다시 돌려야 한다. 현재 장수는 데이터 값이며 확정 견적 정책으로 선언하지 않는다. 프론트 빌드의 큰 청크 경고와 기존 서버 라이브러리 경고는 남는다.

## 파일 목록

WO2 시작 스냅샷 대비다. WO1 누적 diff 전체와 구분했다. 이 보고서는 아래 소스 집계와 별도다.

### 구현 변경 30개

- `server/app/agents/content_roles.py`
- `server/app/agents/page_assembler.py`
- `server/app/agents/page_template_assembler.py`
- `server/app/repo.py`
- `server/app/routes.py`
- `server/app/workers/detail_page_job.py`
- `src/features/analysis/AnalysisForm.jsx`
- `src/features/product-input/saveRouting.js`
- `src/features/storyboard/Storyboard.jsx`
- `src/features/storyboard/storyboardUnits.js`
- `src/lib/api/httpAdapter.js`
- `src/lib/api/shapes.js`
- `src/lib/customMatchPromotion.js`
- `src/lib/detailRecommendations.js`
- `src/lib/generationCutCount.js`
- `src/lib/generationExamples.js`
- `src/lib/horizonBackground.js`
- `src/lib/horizonBackgroundAvailability.js`
- `src/lib/identityScope.js`
- `src/lib/pageTemplates/assemble.js`
- `src/lib/sections.js`
- `src/lib/storyboardCatalogDrag.js`
- `src/lib/storyboardEntryPlacement.js`
- `src/lib/storyboardRenderGroups.js`
- `src/lib/storyboardSimilarCuts.js`
- `src/lib/storyboardSpaceSets.js`
- `src/lib/storyboardTaxonomy.js`
- `src/mock/api.js`
- `src/mock/db.js`
- `src/styles/features.css`

### 구현 추가 15개

- `server/app/agents/page_style.py`
- `server/app/data/example_tags.json`
- `src/features/analysis/PageTemplatePicker.jsx`
- `src/features/analysis/PageTemplatePicker.module.css`
- `src/features/storyboard/SectionAddMenu.jsx`
- `src/features/storyboard/StoryboardTextCard.jsx`
- `src/features/storyboard/TemplateSectionBand.jsx`
- `src/features/storyboard/TemplateSections.module.css`
- `src/lib/pageTemplates/entryNotices.js`
- `src/lib/pageTemplates/examplePreferences.js`
- `src/lib/pageTemplates/pageStyle.js`
- `src/lib/pageTemplates/sectionActions.js`
- `src/lib/pageTemplates/seed.js`
- `src/lib/pageTemplates/slots.js`
- `src/lib/pageTemplates/validateStoryboard.js`

### 회귀 시험 수정 62개

- `server/tests/test_ai_timeout_callers.py`
- `server/tests/test_analysis_internal_writes.py`
- `server/tests/test_asset_recipe.py`
- `server/tests/test_canvas_ai_server.py`
- `server/tests/test_context_fill.py`
- `server/tests/test_context_fill_routes.py`
- `server/tests/test_cut_identity_fallback.py`
- `server/tests/test_cut_input_authority.py`
- `server/tests/test_cut_output_qc_wiring.py`
- `server/tests/test_cuts.py`
- `server/tests/test_detail_cut_preview.py`
- `server/tests/test_detail_incomplete_cuts_partial.py`
- `server/tests/test_detail_page.py`
- `server/tests/test_detail_page_fallback.py`
- `server/tests/test_detail_page_identity_source.py`
- `server/tests/test_detail_page_license_face.py`
- `server/tests/test_detail_partial_publish.py`
- `server/tests/test_editor_generation_record.py`
- `server/tests/test_example_owned_pose_direction.py`
- `server/tests/test_fm_real_model_generation.py`
- `server/tests/test_garment_color_transport.py`
- `server/tests/test_horizon_all_reference.py`
- `server/tests/test_horizon_background.py`
- `server/tests/test_identity_scope_wiring.py`
- `server/tests/test_outer_closure_pricing.py`
- `server/tests/test_outfit_release_gate.py`
- `server/tests/test_page_assembler.py`
- `server/tests/test_page_output_qc_wiring.py`
- `server/tests/test_product_cut_source_photo.py`
- `server/tests/test_routes.py`
- `server/tests/test_signature_cut_routing.py`
- `server/tests/test_storyboard_revision.py`
- `server/tests/test_storyboard_similar_cuts.py`
- `tests/frontend/analysis-md-comment.test.mjs`
- `tests/frontend/colorway-matching.test.mjs`
- `tests/frontend/compose-mode-location.test.mjs`
- `tests/frontend/custom-matching.test.mjs`
- `tests/frontend/detail-recommendations.test.mjs`
- `tests/frontend/draft-slot-takeover.test.mjs`
- `tests/frontend/editor-text-slots.test.mjs`
- `tests/frontend/example-owned-pose-direction.test.mjs`
- `tests/frontend/face-render-warm-on-select.test.mjs`
- `tests/frontend/genexamples-autofill.test.mjs`
- `tests/frontend/horizon-background.test.mjs`
- `tests/frontend/horizon-set-all-reference.test.mjs`
- `tests/frontend/mobile-product-input.test.mjs`
- `tests/frontend/pr499-storyboard-fixes.test.mjs`
- `tests/frontend/product-cut-direction.test.mjs`
- `tests/frontend/removed-cut-compatibility.test.mjs`
- `tests/frontend/retired-reference-modes.test.mjs`
- `tests/frontend/storyboard-bundle-integrity.test.mjs`
- `tests/frontend/storyboard-catalog-drag.test.mjs`
- `tests/frontend/storyboard-entry-placement.test.mjs`
- `tests/frontend/storyboard-internal-roles.test.mjs`
- `tests/frontend/storyboard-owner-feedback.test.mjs`
- `tests/frontend/storyboard-prefetch.test.mjs`
- `tests/frontend/storyboard-qa-b3.test.mjs`
- `tests/frontend/storyboard-render-groups.test.mjs`
- `tests/frontend/storyboard-similar-cut.test.mjs`
- `tests/frontend/storyboard-similar-oncard.test.mjs`
- `tests/frontend/storyboard-space-set-inspector.test.mjs`
- `tests/frontend/storyboard-units.test.mjs`

### 새 시험·도우미 6개

- `server/tests/storyboard_v4_cases.py`
- `server/tests/test_page_templates_v4.py`
- `tests/frontend/helpers/templateStoryboard.mjs`
- `tests/frontend/page-template-screen.test.mjs`
- `tests/frontend/page-template-seed.test.mjs`
- `tests/frontend/page-template-selection.test.mjs`

### 지운 시험 파일

- `server/tests/test_hook_title_layout.py`
- `tests/frontend/signature-cut-pool.test.mjs`
- `tests/frontend/storyboard-hook-frame.test.mjs`
- `tests/frontend/storyboard-opening-row.test.mjs`

프론트의 위 세 시험 파일은 v3 훅 스타일·오프닝 행·전용 풀 선택만 검증했다. 첫 장·섹션·생성예시의 현재 동작은 새 seed·render·선택 시험으로 옮겼다. 그 밖에 사진 양·고정 네 그룹·옛 행을 전제로 한 사례는 같은 파일에서 v4 사례로 바꿨다. 라이선스 안내·자동 정보·직접 올린 사진·결정적 id 시험은 v4 조립으로 유지했다.

### 지운 서버 시험 함수

v3 사진별 배치·옛 제목 조립 및 사용하지 않는 콘티 signature 보정만 제거했다. 새 조립 회귀는 WO1 카탈로그·조립·패리티 시험과 이번 실제 seed 교차 시험이 맡는다.

`server/tests/test_page_assembler.py`:

- `test_ai_block_maps_to_image_element_with_cut_result_url`
- `test_caption_height_keeps_twenty_korean_characters_on_one_line_and_wraps_twenty_one`
- `test_color_tag_fits_the_400px_box_kept_by_an_already_open_old_frontend`
- `test_colorway_pair_falls_back_to_separate_blocks_when_matching_garments_diverge`
- `test_copywriting_ignores_body_for_non_hero_content_roles`
- `test_copywriting_off_keeps_empty_caption_and_deterministic_labels`
- `test_copywriting_on_but_no_matching_copy_result_keeps_placeholder_slot`
- `test_copywriting_on_uses_headline_for_every_single_photo_caption`
- `test_explicit_detail_role_sets_editor_identity`
- `test_extended_colorway_pair_assembles_full_left_medium_right_with_labels`
- `test_four_cut_grid_merges_into_one_block_with_adjacent_images`
- `test_ghost_tags_resolve_color_names_and_keep_wide_box_clear_of_photo`
- `test_headline_copy_sits_below_dynamic_image_and_body_is_ignored`
- `test_mine_block_preserves_explicit_section_role`
- `test_mine_block_with_no_own_images_has_no_elements`
- `test_missing_cut_result_renders_empty_slot_without_crash`
- `test_mixed_row_keeps_unresolved_label_position_and_width`
- `test_partial_cut_results_only_missing_block_is_empty`
- `test_row_color_labels_stay_below_their_own_photo_and_fit_the_column`
- `test_short_image_still_contains_headline`
- `test_signature_first_cut_keeps_only_contrast_aware_hook_title`
- `test_signature_long_product_name_wraps_at_words_into_two_centered_lines`
- `test_single_color_ghost_tags_keep_original_text_and_geometry`
- `test_single_photo_labels_follow_content_role_and_point_sequence`
- `test_two_and_three_column_rows_place_labels_below_each_photo`
- `test_two_column_opening_row_merges_and_keeps_copy_below_images`

`server/tests/test_hook_title_layout.py`:

- `test_hook_title_empty_values`
- `test_hook_title_layout_matches_shared_contract`
- `test_hook_title_metrics_and_limits_match_shared_contract`
- `test_hook_title_normalizes_only_contract_whitespace`
- `test_signature_assembly_uses_title_layout`

`server/tests/test_signature_cut_routing.py`:

- `test_detail_page_worker_safety_net_remaps_and_logs`
- `test_detail_page_worker_safety_net_remaps_dress_to_dress_pool`
