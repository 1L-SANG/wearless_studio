실제 모델: gpt-6-astra · effort: xhigh. 현재 실행의 turn_context에서 확인했다.

# WOC-PORT 새 콘티 이식 보고서

작업 공간은 `/Users/daily/.codex/worktrees/templates-v5/wearless_studio`, 기준은 `0c6885b8170505b488bd5cf8363ce610c6279f33`이다. 옛 templates-mvp와 메인 폴더는 읽기만 했다. 총괄이 적용한 변경 위에서 충돌 표시를 제거하고, WOC1~WOC3의 v5 화면과 최신 main의 제품컷 계약을 연결했다. git 쓰기·브라우저·유료 API 호출은 하지 않았다. index의 충돌 상태 정리는 총괄에게 남긴다.

## 충돌 10개

| 파일 | 해결 내용 |
|---|---|
| `server/app/routes.py` | v4 콘티 검증 오류와 템플릿 불일치 처리를 살리고, main의 `product_runtime_spec` 실행 사본으로 예시·세트 적합성을 검사하는 경로를 합쳤다. 저장 원본에는 선택한 촬영 방식과 비활성 예시 설정이 남고, 명시적 촬영 방식의 실행 검증에서는 비활성 참고 입력을 제외한다. |
| `server/app/workers/cut_checkpoints.py` | 기존 색상 master·대상 근거·해시 확인을 먼저 수행하고, 제품 전체컷의 저장된 final은 main대로 base 후보로 내려 필수 단추 QC를 다시 거치게 했다. 재사용 base를 다른 base로 덮지 않는 조건도 유지했다. |
| `src/features/storyboard/Storyboard.jsx` | v5에서 분리한 무대·사진 판·목록·갤러리를 유지하고 구형 Inspector/Card/Frame 본문은 복구하지 않았다. 충돌 내부에 있던 main의 유효한 촬영 방식·원본 우선 로직은 현재 PhotoPanel·ExampleGallery·preview helper에 연결했다. |
| `src/lib/api/shapes.js` | WOC의 v4 시드 전환을 유지했다. 퇴역한 defaultStoryboard·hook 시드·구형 fingerprint를 되살리지 않고, 실제 `pageTemplates/seed.js`에 촬영 방식 기본값과 편집 판정을 반영했다. defaultAnalysisShape는 유지한다. |
| `src/lib/generationCutCount.js` | WOC의 AI 블록만 세는 규칙과 main의 촬영 방식별 고유 키를 합쳤다. 명시적 방식에서는 비활성 예시·참고 입력을 키에서 제외하고, 실제 착용 세트와 비슷한 컷은 기존대로 각각 센다. |
| `src/lib/sections.js` | v4 검증·연속 run·이름·세트/비슷한 컷 정규화를 유지하며 main의 잘못된 값·비대상 `productPresentation` 제거를 함께 적용했다. 유효한 방식은 저장·복제 후에도 남는다. |
| `src/lib/storyboardTaxonomy.js` | v4 슬롯별 촬영 규칙·역할·첫 장 판정과 main의 세 가지 촬영 방식, 변경 전후 정규화, 방향 변경 시 낡은 예시 초기화를 합쳤다. `shot`에 flat/natural_light를 추가하지 않았다. |
| `tests/frontend/storyboard-entry-placement.test.mjs` | 실제 v4 시드·쇼룸 시그니처 검사로 유지하고, #608이 추가한 ‘혼합 구도가 없는 네 번째 장소도 배치’ 고정 카탈로그 시험을 합쳤다. 운영 예시 태그 수정과 무관하게 이 동작을 검사한다. |
| `tests/frontend/storyboard-inspector-preview.test.mjs` | 지워진 Storyboard 내부 함수를 문자열로 추출하는 방식을 현재 공용 preview helper 검사로 옮겼다. main의 명시적 촬영 방식 원본 우선 회귀는 `product-presentation-preview.test.mjs`에서 실제 무대·목록·전체 보기까지 검사한다. |
| `tests/frontend/storyboard-similar-oncard.test.mjs` | 실제 v5 Stage와 기존 harness를 사용한다. 원본만 비슷한 컷을 추가하고 원본 ID로 실행하는 동작, 후속 사진·번호, 12컷·SNS 제한과 사진 비율·초점 검사를 유지한다. 제거된 구형 DOM을 복구하지 않았다. |

## 충돌 없이 적용된 부분에서 고친 뜻

| 부분 | 변경과 확인 |
|---|---|
| 새 제품 전체컷 | 두 템플릿·네 의류 종류의 시드가 만드는 `cutType: product, shot: ghost`에 `productPresentation: ghost`를 명시한다. 섹션 추가도 같은 시드를 쓴다. 이전 원본이 없는 재사용 fallback의 제품 recipe에도 같은 기본값을 준다. |
| 편집 판정 | v4 `storyboardFingerprint`에 유효한 촬영 방식을 넣었다. ghost를 flat/natural_light로 바꾸면 셀러 편집으로 인식되어 재시드하지 않는다. 값이 없던 옛 블록에는 fingerprint 필드를 억지로 추가하지 않는다. |
| 기존 recipe 복사 | 저장·복제·재시도는 기존 촬영 방식을 유지한다. 기존 제품 원본을 복사해 만드는 fallback도 원본 방식을 유지한다. 첫 장을 새 독립 고스트로 전환하는 D254 규칙은 이번에 넣지 않았다. |
| 사진 판의 기존 처리 | 현재 방식 전달, 제품 전체컷 pending 기본 ghost, 예시 없이 적용·실패 후 재시도, 사용 가능한 앞·뒤 원본 보정을 복원했다. 구형 촬영 방식 chips나 새 D251/D254 판은 추가하지 않았다. |
| 에디터와 공유 갤러리 | 에디터가 넘기던 productPresentation을 분리된 MoodGuide가 놓치고 있었다. 명시적 방식에서 비활성 예시·오류 목록을 숨기고 전체/디테일 선택은 유지한다. v5의 내 사진 전환 동작도 유지했다. |
| 미리보기 원본 | 명시적 촬영 방식은 실제 색의 앞·뒤 셀러 원본을 읽는다. 해당 원본이 없으면 오래된 예시·thumb로 대체하지 않는다. 분석 디테일 영역·따온 사진·생성 결과의 기존 규칙은 그대로다. |
| 서버 내 사진 정규화 | v4의 mine 조기 반환에 main의 촬영 방식 정리가 빠져 있었다. 기존 제거 필드 목록에 productPresentation을 넣어 비대상 값이 저장되지 않게 했다. 먼저 실패하는 v4 회귀를 확인했다. |
| 시험 연결 | 제품컷 저장 시험의 구형 콘티 입력을 기존 v4 helper로 바꾸고, 구형 Inspector 추출 시험을 현재 PhotoPanel/Stage로 옮겼다. 에디터 harness에는 이미 추가돼 있던 의류 색 helper를 연결했다. 기존 계약 assertion을 실제 실행 경로에 맞췄다. |
| 전체 서버 시험의 입력·카탈로그 기대 | #604의 실제 작업 입력 검사 12건도 v4 fixture를 사용하게 했다. 방식·뒷면 원본·비활성 참고 제외 assertion은 그대로다. #608이 제공하는 남성 전신 옆면은 존재함을 명시하고, 현재 없는 남성 상반신 뒷면만 부재로 검사한다. 자산은 고치지 않았다. |
| 고정 사례 | 최신 main 자산과 새 명시적 ghost 시드로 132사례를 다시 만들었다. 저장 구조나 조립 엔진 형식은 바꾸지 않고 두 엔진의 정확한 일치를 확인했다. |

추가로 고친 구현 파일은 `src/lib/pageTemplates/seed.js`, `sectionDependencies.js`, `src/features/storyboard/StoryboardPhotoPanel.jsx`, `StoryboardExampleGallery.jsx`, `storyboardPreview.js`, `server/app/agents/content_roles.py`다. 시험은 `product-presentation.test.mjs`, `product-presentation-preview.test.mjs`, `server/tests/test_product_photography.py`, `test_cut_input_authority.py`, `test_woc2_set_signature_inputs.py`를 연결하고 `woc-port-contracts.test.mjs`, `woc-port-integration.test.mjs`를 더했다. 고정 사례는 `tests/fixtures/page_templates_cases.json`이다. 변경 계획과 실행 근거는 `docs/superpowers/plans/2026-11-09-woc-port-v5.md`, `.scratch/woc-port-20261109/`에 있다.

## SPEC.md 5절 점검

| 점검 항목 | 확인 결과 |
|---|---|
| #604 taxonomy | PRODUCT_PRESENTATION_OPTIONS, productPresentationOf, 유효값 보존·잘못된 값/비대상 제거를 유지한다. shot은 ghost/detail이고 세 방식은 productPresentation에만 둔다. |
| shapes·저장 | v4 정규화, JSON 재로드·복제, draft·원격 저장·생성 기록·실패 재시도에서 방식과 면을 보존한다. 새로운 첫 장 옵션 필드는 추가하지 않았다. |
| 고유 생성 수 | main의 촬영 방식별 키와 비활성 입력 제외 조건을 유지한다. 서버 `_duplicate_source_indexes` 및 실행 사본 정규화와 비교했고 각 방식의 독립 생성·같은 방식의 중복 제거를 검사했다. |
| 서버 생성 | product_photography·prompt·CutPlan·현재 방향 원본 요구·명시적 방식 우선권은 main 구현을 유지한다. detail_page_job와 editor_image_job의 깨끗이 적용된 변경도 HEAD·옛 작업 공간과 비교했다. |
| 서버 검수 | 필수 단추 QC·prompt/버전·체크포인트 재검수·저장 전 결과 해시·최대 한 번 원본 재생성·미차감/예약 해제를 유지한다. 일반 QC의 recipeFamily/productVariant/shot/productPresentation 전달도 유지한다. |
| 에디터 | 새 이미지 세 방식 선택과 요청, canvas 색·면 변경, failedCutRetry와 저장된 asset recipe를 확인했다. 에디터 단건 생성에 섹션 단위 규칙을 추가하지 않았다. |
| 새 콘티 | 새 시드의 ghost·fingerprint·기존 sectionDependencies와 양쪽 조립을 연결했다. SPEC의 연속 run 일괄 수정·새 firstGhost 옵션·재사용/독립 고스트 전환 제안은 사용자 지시대로 다음 작업이다. |
| 옛 UI 교체 | v5 화면을 유지하며 pending 제품컷 적용, 원본 Picker와 저장·검증 로직을 옮겼다. 구형 세 방식 chips 복구와 새 판 디자인은 보류했다. |
| #606·#608 | genExamples, storyboardSpaceSets, example_assets, space_set_assets 네 JSON은 HEAD와 바이트가 같다. 개별컷 64장과 분류·카메라 태그를 옛 데이터로 덮지 않았다. |

## 시험

기존 프론트 시험 일부가 `server/.venv/bin/python`을 직접 실행하므로, 새 작업 공간 안에만 `server/.venv` 심볼릭 링크를 만들어 지정된 기존 venv를 사용했다. venv 설치나 외부 파일 변경은 하지 않았다.

| 명령·범위 | 실제 결과 |
|---|---|
| 새 시드·fingerprint·추가·fallback RED → GREEN | **11 실패/1 기존 통과 → 12 통과**. integration-red.log, integration-green.log |
| PhotoPanel·공유 갤러리 RED → GREEN | 실제 기능 실패 **3 실패/8 통과** 후 복원. 처음 발견한 구형 import·SSR React 설정은 테스트 연결 오류로 따로 고쳤다. photo-panel-red.log |
| v5 미리보기 RED → GREEN | **4 실패/2 기존 통과 → 6 통과**, 무대·목록·전체 보기 포함. 담당자의 독립 실행 기록은 clean-audit.md, root 통합 재실행은 presentation-green-first.log에 있다. |
| `MOCK_API_LATENCY_SCALE=0 node --test tests/frontend/product-presentation.test.mjs tests/frontend/product-presentation-preview.test.mjs tests/frontend/woc-port-integration.test.mjs` | **29 통과, 0 실패**, exit 0. presentation-green-first.log |
| 공통 계약 집중 7개 파일 | **127 통과, 0 실패**, exit 0. contracts-final-tests.log |
| 서버 v4 mine 정규화 RED | **54 통과, 1 실패** 후 수정. server-v4-normalization-red.log |
| 지정 pytest의 제품촬영·단추QC·체크포인트·의류색·에디터·routes·v4 집중 12개 파일 | **468 통과, 0 실패**, 6.48초, exit 0. server-targeted-green.log |
| 첫 전체 서버와 이식 회귀 재검사 | 첫 전체는 **11,825 통과, 13 실패, 285 건너뜀**, 303.69초. 구형 입력 12건과 #608 카탈로그 기대 1건을 고친 뒤 `pytest -q tests/test_cut_input_authority.py tests/test_woc2_set_signature_inputs.py`는 **58 통과, 0 실패**, 0.68초. server-full.log, server-port-regressions.log |
| 고정 사례 재생성 | `PAGE_TEMPLATE_PYTHON=/Users/daily/Documents/wearless_studio/server/.venv/bin/python node scripts/regenerate-page-template-fixtures.mjs`: **132사례 JS/Python 정확히 일치**, exit 0. fixtures.log |
| `pnpm test:frontend` 전체 | **5,298 통과, 0 실패, 0 건너뜀**, **62.830초**, exit 0. frontend-final.log. 첫 실행은 중간 출력 뒤 종료되지 않아 중단했고 통과로 세지 않았다. 제한 시간 진단 재실행(`--test-timeout=60000`)도 **5,298 통과, 실패·시간 초과 0**, 71.797초였으며 원래 명령으로 다시 확인했다. |
| `server/`에서 `/Users/daily/Documents/wearless_studio/server/.venv/bin/pytest -q --ignore=tests/test_personalization.py` | **11,838 통과, 0 실패, 285 건너뜀**, 경고 1,043개, **278.96초**, exit 0. server-final.log |
| `pnpm build` | **성공**, exit 0, **9.27초**. 큰 청크 경고가 있으며 빌드 오류는 없다. build.log |
| 정적 디자인 검사 | 지정 impeccable detect를 바꾼 JSX 3개(Storyboard·PhotoPanel·ExampleGallery)에 실행: **exit 0, []**. CSS는 바꾸지 않았다. |
| 독립 검토 | 집중 **37/37 통과**, pending 제품컷 저장 실패→같은 값으로 재시도 검증 통과, 남은 지적 없음. 마지막 서버 시험 자료 수정도 별도로 **13/13 통과** 및 최신 main 자산과 대조 확인했다. final-review.md와 후속 검토 기록. |
| 최종 보존 검사 | 보호 JSON **4개가 HEAD와 바이트 동일**, 두 조립 엔진·총괄 제목 CSS는 시작 SHA256과 동일. 충돌 표시 **0**, index의 unmerged 파일 **10개 유지**. preservation.json |
| `git diff --check`·새 파일 공백 검사 | **통과**. 새 시드·회귀 시험·보고서·계획도 끝 공백과 마지막 개행을 확인했다. |

## 남은 확인

- 브라우저 화면 검사는 실행하지 않았다. 총괄이 v5 사진 판의 제품 원본·앞뒤 선택·빈 원본 상태, 디테일 crop, 에디터 세 방식, 저장 실패 후 재시도와 다음 단계 이동을 확인해야 한다.
- D251·D254의 섹션 촬영 방식 일괄 선택, 새 판과 첫 장 독립 고스트 전환은 다음 작업이다. 현재 첫 장 재사용 슬롯은 기존 WOC 규칙을 따른다.
- git index는 의도적으로 정리하지 않았다. 파일의 충돌 표시 제거와 별개로 총괄이 index 상태를 정리해야 한다.
