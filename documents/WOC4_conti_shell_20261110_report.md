실제 모델: gpt-6-astra · effort: xhigh. 이번 실행의 turn_context에서 확인했다.

# WOC4 새 콘티 화면 틀 1차

기준 커밋은 `f1c935e48a1933a02c86acfb2abe52a426da6db8`, 작업 공간은 `templates-v5/wearless_studio`다. 장부 D242~D272, 기준서 7절, r3 시안 코드와 장면 그림, 제품컷 SPEC 2·4절을 읽었다. 시안의 옛 글자·전체 탭·시그니처 네 장 줄보다 이번 지시를 우선했다. 원본 폴더·옛 작업 공간·메인 폴더 수정, git 쓰기, 브라우저 실행, 유료 API 호출은 하지 않았다.

## 항목별 반영

| 항목 | 구현과 바뀐 파일 |
|---|---|
| 1. 제품컷 섹션 촬영 방식 | `pageTemplates/productPresentation.js`가 연속 color_product run의 AI product/ghost만 모아 상태를 읽고 한 번에 바꾼다. `photoActions.js`와 기존 atomicPatch 저장을 연결해 전부 저장·롤백·되돌리기한다. 유효한 모드는 새 칸·허용된 이동에 상속하고 새 run 복제는 복사된 모드를 유지한다. 기존 빈 run은 ghost 기본을 쓴다. 옛 미지정·혼합은 읽기만으로 고치지 않고 해당 사진 설정 편집과 다음 단계 진입 전에 선택을 요구한다. `ProductPresentationPicker.jsx`는 세 방식만 보이며 실제 면은 바꾸지 않는다. `productPresentationExamples.js`, `src/data/productPresentationExamples.json`, `public/assets/product-presentations/v1/`에 표시 전용 그림 24장을 연결했다. |
| 2. 섹션 판 | `StoryboardStage.jsx`의 단추를 연필 아이콘과 흰 바탕의 “섹션 디자인 변경”으로 바꿨다. `StoryboardSectionPicker.jsx`는 기본 디자인을 먼저 보여 주는 기존 규칙을 유지하고 이름 앞에 기본 표시를 둔다. 머리 오른쪽에 섹션 빼기를 두며 첫 장에는 없다. 설명·감소 컷 수·옵션 스위치는 넣지 않았다. 기존 조립 기반 썸네일은 유지했다. 스타일은 Layout/Panels 모듈에 있다. |
| 3. 무대 | 공통 머리 부제를 “예시 이미지를 클릭해서 변경할 수 있어요”로 통일했다. 사진 아래는 상품 색이 둘 이상일 때 색 원, 오른쪽 컷 번호만 남긴다. 장소·색 이름·앞면/뒷면 글은 보이지 않고 색 이름은 접근성 이름으로 제공한다. 따온 사진의 원본 Section 안내와 글 전용 안내는 무대 본문에 별도 한 줄로 유지했다. `StoryboardStage.jsx`, `StoryboardLayout.module.css`, `Storyboard.jsx`. |
| 4. 페이지 구성 | 실제 코드에는 시안의 노치가 없었으며 이를 추가하지 않았다. `StoryboardPageList.jsx`만 `emphasizeText`를 켜고 `StoryboardPreview.jsx`가 막대 두께를 키운다. 디자인 후보·생성 결과의 글자 기하는 그대로다. 총괄의 제목 gap 3px, 컷 수 margin 0, 172/184px 너비는 유지했다. |
| 5. 테마 | `StoryboardThemePicker.jsx`에 매거진(serif), 볼드(boldGothic), 다정(template) 순으로 wearless/웨어리스 두 줄을 둔다. 이름은 title·접근성 이름으로만 보이고 미선택은 35%다. `storyboardPageStyle.js`가 기존 persistAnalysisEdit 경로로 저장하며 빠른 연속 선택은 직렬 처리한다. 마지막 전체 저장 Promise를 enqueue 때 재진입 barrier에 등록하고 다음 단계도 이를 기다린다. `AnalysisForm.jsx`, `pageStyle.js`는 기존 분석 복원·색감 변경이 선택한 글씨를 지우지 않게 했다. 기존 템플릿을 명시적으로 바꾸는 기본값 규칙은 유지한다. |
| 6. 장소 탭 | 새 콘티 전용 `StoryboardPhotoGallery.jsx`, `StoryboardChoiceControls.jsx`, `storyboardPhotoChoices.js`에서 카페를 맨 앞·기본으로 두고 전체·더보기를 없앴다. 모든 장소가 보이며 탭 사이에서 줄을 바꾼다. 현재 사진의 알려진 장소로 열고 시그니처·미상은 카페로 연다. `StoryboardSpaceSetPanel.jsx`도 같은 필터를 쓴다. 스튜디오 전용 목록은 생활 장소 탭으로 거르지 않는다. |
| 7. 빛 | “빛: 전체” 드롭다운과 연한 활성 테두리를 추가했다. 실제 조사 데이터의 lightQuality soft/hard를 “부드러운 빛/강한 빛”으로 제공한다. 전체에서는 미분류도 포함하고 빛을 고르면 해당 태그만 보인다. 메뉴 바깥 누르기·Esc·키보드 이동과 선택 후 초점 복귀를 구현했다. 새 Choices 모듈에 모았다. |
| 8. 사진 카드 | 콘티의 개별/장소/스튜디오 목록을 3열 사진 카드로 통일했다. 이름·설명·장수·사용 이력 표시는 없고 선택 체크와 늘어나는 스튜디오 컷 수만 남는다. 선택 가능한 모델·의류·촬영 방식·구도 조건을 유지하며, 모델에 맞지 않는 세트 멤버가 낱장으로 새어 나오지 않게 필터링한다. 편집기의 기존 MoodGuide는 별도로 유지한다. |
| 9. 한 장 고르기 | 첫 장·후기·궁금한 점은 medium이 기본이다. 하의는 하반신, 나머지는 상반신으로 표시한다. 이전 컷 종류 탭 대신 장소·구도 선택을 사용한다. 세트는 카드 한 칸이고 누르면 그 줄 아래에 고른 구도만 펼쳐진다. 한 장 선택 시 그 사진만 바뀌며, 페이지 방향키 이동 전에 초점을 사라지지 않는 gallery region으로 옮긴다. `StoryboardPhotoGallery.jsx`, `StoryboardPhotoPanel.jsx`, Choices 모듈. |
| 10. 시그니처 | `signatureCutPool.js`의 선택 자격을 두 템플릿 첫 장으로 넓혔다. `usesSignaturePool`을 따로 두어 감성형의 일상 사진 기본과 쇼룸형에서 직접 고른 일반 사진을 덮지 않는다. 새 선택은 projectId/slotKey의 기존 고정 해시로 고르고 첫 카드에 고정한다. 저장된 승인 시그니처가 있으면 카드도 그 사진을 유지해 무대와 같게 한다. 활성 풀은 39개, 새 24개와 제외 2개를 반영했다. 로컬 full/thumb 48개, 서버 example_assets 24개, identityScopes 재생성, cut_generator·cut_plan의 풀/원피스 구도 ID를 맞췄다. 두 템플릿 첫 장의 허용 촬영은 styling/horizon·full/medium으로 넓히고 기본값은 유지했다. |
| 11. 색감 순서 | `src/data/exampleToneOrder.json`은 지정한 version 1/빈 order로 만들었다. `orderPhotoChoices`는 카드에 보이는 exampleId의 정수 값을 오름차순으로 묶고 프로젝트·필터 해시로 시작점만 돌린다. 값 없는 카드는 기존 순서로 뒤에 둔다. 빈 파일이면 기존 순서를 유지한다. 시그니처는 이 순서와 무관하게 모든 탭·페이지의 첫 칸이다. 48장 무작위 제한은 새 콘티 목록에서 제거했다. 처음 자동 배치는 이 함수를 쓰지 않는다. |

새 UI 스타일은 `StoryboardChoices.module.css`에 모았고 기존 Layout/Panels 모듈은 해당 부품만 바꿨다. 공용 배경·셀러 색 덮어쓰기와 두 페이지 조립 엔진은 수정하지 않았다. `src/lib/pageTemplates/README.md`의 Storyboard presentation도 현재 동작으로 고쳤다.

## 제품컷 SPEC 2절·4절 확인

| 점검 | 결과 |
|---|---|
| 저장 단위 | 기존 블록별 productPresentation만 사용한다. 새 블록·DB 필드나 경쟁하는 섹션 모드 필드는 없다. |
| 적용 범위 | 같은 연속 color_product run의 AI 제품 전체컷만 함께 변경한다. 디테일·내 이미지·다른 run·첫 장 별도 컷은 제외한다. 불연속 같은 key는 오류다. |
| 면·색·원본 | 방식 선택과 표시용 앞면 fallback 모두 direction/colorId/원본 바인딩을 바꾸지 않는다. |
| 새 섹션·추가·이동·복제 | 새 섹션 ghost, 기존 대상 run의 모드 상속, 이미 존재하는 빈 run의 ghost 기본, 새 run으로 복제한 유효 모드 보존을 검사했다. 새 이동 UI는 만들지 않았다. |
| 옛 미지정·혼합 | 조회 시 추측해서 통일하지 않는다. 해당 제품 사진 설정 변경 또는 생성 진행 전에 선택하도록 한다. 다른 섹션 보기·삭제와 기존 결과 조회를 막지 않는다. |
| 저장 실패·빠른 클릭 | 기존 원자 저장 한 번으로 전체 run을 처리한다. 실패하면 전체 이전 배열로 돌아가고 같은 모드로 재시도한다. 저장 중 겹친 클릭은 차단해 늦은 응답이 다른 모드를 덮지 않는다. |
| fingerprint·과금·재시도 | 기존 fingerprint의 productPresentation, main의 고유 생성 수·명시 방식 우선권·비활성 예시 제외, 저장된 recipe 재시도를 유지했다. 무대의 수와 견적은 변경 배열에서 다시 계산한다. |
| 그림 종류·면 | top/bottom/outer/dress와 ghost/flat/natural_light의 앞뒤 24개를 manifest로 분리했다. 치마는 bottom 그림을 사용한다. 미상 종류·옆면·그림 없음은 회색 이름 칸이다. |
| 그림 fallback | 같은 종류·같은 방식의 Back이 없거나 로드 실패하면 Front만 대신 쓰고 “앞면 그림, 촬영 방식 참고”를 표시한다. 그 앞면도 없으면 이름만 표시한다. 선택 자체는 가능하다. |
| 표시와 생성 분리 | 표시 그림 URL은 exampleId/refImages/refAssetIds나 PRODUCT 원본으로 보내지 않는다. 현재 면의 실제 셀러 원본 요구와 서버 생성 계약은 그대로다. |
| 경로와 비율 | `/assets/product-presentations/v1/{type}_{mode}_{face}.webp`. SPEC 4절을 따라 2:3 예시 셀에 contain으로 보여 준다. 이는 r3의 3:4 표시 상자와 다른 부분이며, 무대 사진 3:4는 그대로다. |
| 이번 범위 제외 | SPEC 3절의 첫 장 고스트 선택·독립 생성 전환·새 옵션 메타데이터는 만들지 않았다. 현재 첫 장 재사용은 기존 규칙을 따르며 변경마다 기존 의존성 조정 함수는 호출한다. |

## 자산과 토큰

| 시그니처 그룹 | 새 ID | 활성 풀 |
|---|---|---|
| 여성 바지 | sig_women_bottom_pants_03~09, 7개 | 01~09, 9개 |
| 여성 치마 | sig_women_bottom_skirt_03~09, 7개 | 02~09, 8개 |
| 여성 원피스 | sig_women_dress_03~06, 4개 | 01~06, 6개 |
| 남성 바지 | sig_men_bottom_pants_03~08, 6개 | 01, 03~08, 7개 |
| 상의·아우터 | 새 그림 없음 | 여성 01~04, 남성 01~05 유지 |

제외한 `sig_women_bottom_skirt_01`, `sig_men_bottom_pants_02`의 서버 항목과 로컬 읽기 자산은 남겼다. 새 full WebP는 1024×1536, thumb는 512×768이다. 제품 예시 24개는 제공된 WebP 바이트 그대로 복사했다. 총 72개 새 WebP와 출처 sidecar 72개를 두었고, `.scratch/woc4-20261110/asset-provenance.json`에 출처·해시·치수를 기록했다. 제품 해시는 복사 결과와 같고 시그니처의 sourceSha256은 변환 전 원본 PNG의 해시다. 새 이미지 생성은 하지 않았다.

`.sb-v5-theme`에 추가한 토큰은 다음 일곱 개다. Choices 모듈의 q9 견본에만 쓰며 나머지 UI 색은 기존 토큰을 사용한다. 부품 치수·글자 크기·굵기 같은 일부 값은 시안의 로컬 CSS 값이다.

| 토큰 | 값 |
|---|---|
| --sb-theme-inactive | .35 |
| --sb-theme-width | 96px |
| --sb-theme-gap | 10px |
| --sb-theme-radius | 10px |
| --sb-theme-padding | 6px 10px 6px 6px |
| --sb-theme-bar | #c9c9c5 |
| --sb-theme-serif-ko | "Gowun Batang", serif |

`seller.html`의 기존 Google Fonts 요청에 Gowun Batang 700을 더했다. 이 글꼴은 이번에 self-host하지 않았다. 기존 Cormorant 파일은 normal 가변 글꼴이므로 다정 견본의 italic은 브라우저 합성 또는 fallback이다. 이견이 있는 DESIGN.md의 기존 원격 글꼴·굵기 설명은 이번에 고치지 않았다. 견본은 정해진 스타일 값을 선택하며, 실제 `template` 글꼴은 선택한 템플릿의 기존 정의를 따른다.

## 시험과 검토

| 명령·범위 | 실제 결과 |
|---|---|
| 새 동작 집중: `MOCK_API_LATENCY_SCALE=0 node --test tests/frontend/woc4-contracts.test.mjs tests/frontend/woc4-ui.test.mjs tests/frontend/woc4-atomic.test.mjs` | **29 통과, 0 실패**. 섹션 범위·미지정·혼합·상속·복제·롤백·중복 클릭·재시도·필터·구도·톤 순서·시그니처·테마 저장·재진입·초점을 포함한다. final-focused.log |
| 이전 문구·풀 기대값 이관 집중 | **201 통과, 0 실패**. 첫 전체 프론트의 31건 실패를 새 조건에 맞춰 연결했다. 실제 모델 자격 필터도 보완했다. migrated-focused.log |
| 독립 검토에서 찾은 저장·초점 경계 | 수정 전에 **2 실패**를 재현하고 수정 후 통과했다. review-red.log, review-green.log. 재검토는 두 수정, 이후 단일 슬롯 탭 정리와 복제 helper 수정 각각에 한정해 해결로 판정했다. |
| 서버 새 시그니처: `pytest -q tests/test_woc4_signature.py` | **27 통과**. 첫 실행의 26건 실패는 테스트에 실제 런타임 방향 확인 값과 sectionId가 빠진 것이어서 fixture를 보완했다. 서버 동작을 완화하지 않았다. server-focused-second.log |
| 서버 관련 5개 파일 집중 | **173 통과, 0 실패**. 이전 활성 풀·레지스트리 개수 기대 3건을 승인된 39개/서버 41개로 고쳤다. server-regressions-second.log |
| 고정 사례 재생성 | `PAGE_TEMPLATE_PYTHON=/Users/daily/Documents/wearless_studio/server/.venv/bin/python node scripts/regenerate-page-template-fixtures.mjs`: **132사례 JS/Python 정확히 일치**, exit 0. 재생성 파일은 기존 결과와 같다. fixtures-final.log |
| `pnpm test:frontend` | **5,327 통과, 0 실패, 0 건너뜀**, **52.178초**, exit 0. frontend-complete.log |
| `server/`에서 `/Users/daily/Documents/wearless_studio/server/.venv/bin/pytest -q --ignore=tests/test_personalization.py` | **11,865 통과, 0 실패, 285 건너뜀**, 경고 1,043개, **289.96초**, exit 0. server-full.log |
| `pnpm build` | **성공**, **8.41초**, exit 0. 큰 청크 경고가 있으며 빌드 오류는 없다. build-final.log |
| 정적 디자인 검사 | 바꾼 JSX/CSS 대상 impeccable detect **exit 0, []**. 이후 작은 수정은 편집 hook에도 지적이 없었다. design-detect.json |
| 문서·출처 검토 | 72개 WebP/sidecar, 원본 해시·출력 치수, 기존 디자인 체계 보존을 확인했다. documentation-check.md |
| 보존·공백 검사 | `git diff --check` 통과. 기존 예시/세트 JSON 3개·horizon 배경 정책·두 조립 엔진·DESIGN·셀러 배경/덮어쓰기·고정 사례는 그대로다. 기존 서버 자산 124개와 메타도 같고 새 시그니처 24개만 추가됐다. 카탈로그는 첫 장 허용 촬영·구도 외 데이터가 같다. preservation.json |

검증 로그와 검토 범위는 `.scratch/woc4-20261110/`에 있다. 브라우저 캡처·실제 픽셀·폰트 로딩·가로 넘침 검사는 실행하지 않았다. 코드·SSR·순수 함수·입력 계약 검사와 실제 화면 확인을 구분한다.

## 남은 확인과 4라운드 시안

- **머지 전 새 시그니처 24개의 CDN 게시가 필요하다.** 서버는 지정한 `signature/v1/all/<id>.png`, `thumb/<id>.webp`를 사용한다. 로컬 WebP 준비는 이 URL의 게시를 뜻하지 않는다. 총괄이 원본 PNG와 thumb를 올리기 전 실제 생성 참조는 준비되지 않은 상태다. 이번에 R2 업로드·유료 생성은 하지 않았다.
- 색감 order는 빈 파일이다. 측정값이 들어오기 전에는 미분류 카드의 기존 순서가 유지된다. 실제 색감 인접성 평가는 총괄이 값을 넣은 뒤 해야 한다.
- 현재 데이터에는 여성 상의·상반신·카페처럼 결과가 없는 조합이 있다. 카페 기본 규칙을 다른 탭으로 조용히 바꾸지 않고 빈 상태를 보여 준다.
- 기존 승인 시그니처가 저장된 프로젝트는 카드와 무대가 그 사진을 함께 유지한다. 새 선택이 없는 프로젝트만 새 풀의 고정 해시를 사용해, 풀 확장으로 기존 선택이 바뀌지 않게 했다.
- 4라운드 시안이 오면 새 Choices 모듈의 필터 간격·빛 메뉴·3열 카드·펼침 면·테마 견본 정렬 값을 다시 맞출 수 있다. 새 섹션 디자인, 페이지 색감 줄, 옅은 추가 칸과 첫 장 고스트 전환은 별도 작업이다.
- 총괄은 `pnpm dev:mock --host 127.0.0.1 --port 5173` 후 `/create/storyboard?mockTemplate=emo` 및 `show`, 디테일 확인은 `&mockDetail=full`로 들어가면 된다. 카페/현재 장소 열림, 상반신·하반신, 시그니처→장소→재진입, 세트 펼침·키보드 넘김, 제품 앞뒤 슬롯에서 섹션 모드 함께 바뀜·실패 재시도·undo, 빠른 테마 연속 선택 후 재진입, 섹션 빼기와 썸네일 막대를 확인해야 한다. 기본 목 의류와 다른 성별·치마·원피스는 분석에서 값을 바꾼 새 프로젝트로 확인한다.
