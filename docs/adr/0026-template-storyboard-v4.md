# ADR-0026: 콘티는 고른 템플릿의 섹션 묶음으로 짠다

상태: Accepted. 템플릿 콘티(`taxonomyVersion: 4`) 구현에서 오너가 정한 결정을 정리했다. ADR-0005를 대체한다.
결정일: 2026-11-13 정리. 기준 코드는 PR #617(커밋 31cc6290)이다. 템플릿 페이지 빈 칸 판정(25항, ADR-0019)은 그 뒤 커밋 65240acd를 따른다. 상품 소개의 MD 코멘트(18항)는 2026-11-13 오너 결정과 그 뒤 커밋 b630ec1a를 따른다.

## 맥락

ADR-0005는 콘티를 후킹, 스타일링, 스튜디오, 의류 확인 네 섹션으로 나누고 사진 양(기본형, 확장형)으로 장수를 정했다. 지금은 셀러가 분석 화면에서 템플릿을 고르면 상세페이지의 섹션 순서와 섹션 디자인이 정해지고, 콘티 카드는 그 템플릿의 섹션과 사진 칸에 들어간다. 콘티 화면은 왼쪽 페이지 구성, 가운데 무대, 오른쪽 옆 판으로 바뀌었다(`src/features/storyboard/Storyboard.jsx`, `StoryboardPageList.jsx`, `StoryboardStage.jsx`, `StoryboardDrawer.jsx`). 네 섹션 이름은 화면 분류에서 빠지고 생성용 내부값으로만 남는다.

템플릿, 섹션 종류, 사진 칸, 글 칸, 섹션 디자인은 카탈로그 한 파일(`server/app/data/page_templates_v1.json`)이 정본이다. 프론트(`src/lib/pageTemplates/`)와 서버(`server/app/agents/page_templates.py`, `server/app/agents/page_template_assembler.py`)가 같은 파일을 읽는다. 카탈로그 규칙의 자세한 설명은 `src/lib/pageTemplates/README.md`에 있다.

## 결정

### 템플릿과 페이지 스타일

1. 템플릿은 감성형(`emo`)과 쇼룸형(`show`) 두 개다. 한 콘티는 템플릿 하나만 쓴다. 다른 템플릿의 섹션이 섞이면 `mixed_storyboard_templates`로 거절하고, 분석에 저장한 템플릿과 콘티의 템플릿이 다르면 콘티 저장과 상세페이지 생성 모두 `page_style_mismatch`로 거절한다(`server/app/agents/content_roles.py`의 `validate_storyboard_v4`, `server/app/routes.py`의 콘티 저장과 상세페이지 생성).
2. 페이지 스타일은 분석이 소유한다. `analysis.pageStyle`에 `version`(1), `templateId`, `fontStyle`, `colorMode`(`mono`, `point`), `catalogVersion`, `updatedAt`을 담는다(`server/app/agents/page_style.py`). 분석 payload 안의 값이라 DB 스키마는 바꾸지 않는다. 템플릿은 분석 화면에서 고르고, 글자 스타일과 색감은 콘티에서 바꾼다. 처음 값은 그 셀러가 마지막으로 쓴 페이지 스타일(`GET /v1/page-style/last`)이고, 없으면 카탈로그 기본값(`emo`, `fontStyle: template`, `colorMode: mono`)이다.
3. 마네킹컷이 있거나 마네킹 작업을 만든 뒤에는 `templateId`만 바꿀 수 없다(409 `page_style_locked`, "마네킹컷을 만든 뒤에는 템플릿을 바꿀 수 없어요.", `server/app/repo.py`의 `page_style_locked`). 글자 스타일과 색감은 그 뒤에도 바꿀 수 있다.

### 섹션과 블록 저장 모양

4. 콘티는 `taxonomyVersion: 4` 블록만 받는다. 섹션은 같은 `pageSectionKey`가 이어진 블록 묶음이고, 저장 순서가 곧 페이지 순서다. 같은 키가 떨어져 다시 나오면 `noncontiguous_storyboard_section`으로 거절한다. 한 묶음 안의 블록은 `sectionId`, `pageSectionKind`, `pageSectionUsage`, `pageDesignId`가 같아야 한다(`validate_storyboard_v4`, 조립은 `page_template_assembler.py`의 `assemble_v4`).
5. 예전 콘티(`taxonomyVersion` 2, 3)는 변환하지 않는다. 서버는 `storyboard_outdated`("콘티를 다시 열어 주세요.")로 거절한다. 화면은 아직 상세페이지를 만들지 않은 프로젝트면 기본 콘티를 새로 깔고 "콘티가 새 방식으로 바뀌었어요. 확인하고 다음을 눌러 주세요"를 보여 준다. 이미 상세페이지를 만든 예전 프로젝트는 빈 콘티를 받고 "예전 방식으로 만든 페이지예요" 화면에서 에디터로 이어 간다(`src/lib/pageTemplates/seed.js`의 `resolveStoryboardEntry`, `src/lib/api/httpAdapter.js`의 `getStoryboard`). 예전 프로젝트는 시험용 데이터라 호환 작업을 더 하지 않는다.
6. 섹션 종류는 카탈로그의 10개다. 첫 장(`first`), 착용컷(`wear`), 색상과 제품(`color_product`), 원단 설명(`fabric`), 상품 소개(`intro`), 디테일 요소(`points`), 추천 대상(`for_you`), 후기(`review`), 궁금한 점(`faq`), SNS 화면(`sns`)이다. 착용컷만 여러 번 넣을 수 있고, 첫 장은 맨 위에 하나만 둔다(`repeated_storyboard_section`, `invalid_storyboard_first`). 착용컷은 쓰임(`pageSectionUsage`)이 `styling`이면 일상 착용컷, `fit`이면 스튜디오 착용컷으로 부른다.
7. 블록의 섹션 필드는 `pageSectionKey`(템플릿 ID와 섹션 자리), `pageSectionKind`, `pageSectionUsage`(착용컷만 `styling` 또는 `fit`, 나머지 종류는 비움), `pageDesignId`(카탈로그 디자인의 종류가 섹션 종류와 같아야 함), `slotKey`(카탈로그 사진 칸)다. 서버는 `sectionLayout`을 `stack`으로 고정하고, `hook`, `layoutRow`, `colorway`로 시작하는 키와 `pose`, `poseLabel`, `poseThumb`, `kind`를 지운다(`content_roles.py`의 `_canonicalize_v4_block`). 행 배치와 후킹 프레임 저장값은 없다.
8. 글 카드(`source: 'text'`)는 섹션의 글 자리를 표시한다. 사진과 생성 필드를 가질 수 없고(`invalid_storyboard_text_card`), `cutType`, `contentRole`, `sectionRole`, `slotKey`는 비운다. 섹션 종류마다 글 카드 규칙이 있다(카탈로그 `storyboardTextCard`). 첫 장, 착용컷, 색상과 제품, 원단 설명에는 글 카드가 없다. 상품 소개, 추천 대상, SNS 화면은 글 카드 하나만 둔다. 디테일 요소, 후기, 궁금한 점은 글 카드를 맨 앞에 둔다. 다른 섹션 사진을 다시 쓰는 칸의 카드가 들어 있는 SNS 화면은 글 카드를 맨 앞에 두는 규칙으로 읽고, 사진이 하나도 없는 원단 설명은 글 카드 하나로 읽는다. 글 카드는 콘티의 컷 번호와 크레딧 계산에 들어가지 않는다(상세페이지 견적은 `source: 'ai'` 블록만 센다). 글만 있는 상품 소개와 추천 대상 섹션은 지금 구성을 유지한다.

### 사진 목적과 컷

9. 사진 목적(`contentRole`)은 카탈로그의 사진 칸이 정한다. 첫 장의 첫 AI 착용 카드만 `hero`이고, 첫 장의 나머지 착용 카드와 그 비슷한 컷은 `benefit`이다. `sectionRole`(`hooking`, `styling`, `studio`, `product`)은 사진 목적에서 파생하는 생성용 내부값이며 화면의 섹션이 아니다. 내 이미지(`source: 'mine'`)는 `contentRole: 'custom'`, `cutType: null`, `sectionRole: null`로 저장하고 예시, 세트 소속(`spaceGroupId` 등), 참고 이미지, 촬영 방식 키를 지운다. 지우는 키의 정확한 목록은 `documents/common_data_contract.md` §3.4에 둔다. 글 카드도 `sectionRole`을 비운다(`_canonicalize_v4_block`, `canonicalize_storyboard`).
10. 컷 종류와 샷은 사진 칸이 허용하는 값 안에서만 쓴다. 허용 밖의 값이 오면 서버가 칸의 기본값으로 바꾸고, 바뀐 카드는 고른 예시를 지운다. 일상 착용컷의 사진 칸은 스타일링과 호리존을 모두 허용한다(`content_roles.py`의 `_v4_slot`). 컷 종류는 ADR-0003의 `styling | horizon | product` 셋을 그대로 쓰며, 제품컷의 방향과 촬영 방식은 ADR-0008, ADR-0024를 따른다.

### 처음 콘티

11. 처음 콘티는 템플릿과 상품 조건으로 깐다(`src/lib/pageTemplates/seed.js`의 `seedStoryboard`). 템플릿의 기본 섹션만 넣고 SNS 화면처럼 선택 섹션은 넣지 않는다.
    - 색 수로 장수를 정하는 착용컷 칸은 한 색이면 기준색 6장, 두 색 이상이면 기준색 4장에 추가 색마다 2장을 더한다. 추가 색은 앞에서 세 개까지 넣는다.
    - 앞, 옆, 뒤를 도는 스튜디오 세트 섹션은 3장이다.
    - 색상과 제품 섹션은 색마다 앞면 고스트샷 1장을 깔고, 기준색 뒷면 고스트샷은 기준색에 뒷면 사진이 있을 때만 깐다.
    - 섹션 하나의 사진은 12장까지다. 콘티에서 비슷한 컷을 추가하거나 장소 세트를 넣을 때도 같은 한도를 쓴다(`src/features/storyboard/Storyboard.jsx`).
    - 셀러가 손대지 않은 콘티는 상품 조건(의류 종류, 색과 뒷면 사진, 디테일 추천, 강조 특징, 템플릿 등)이 바뀌면 다시 깐다. 셀러가 고친 콘티는 다시 깔지 않는다(`storyboardFingerprint`, `seedProductKey`).
12. 디테일컷은 분석의 디테일 추천에서 고른다(`src/lib/pageTemplates/detailBudget.js`). 디테일 요소 섹션은 뚜렷한 구조 후보 2~3장이고, 2장이 안 되면 섹션을 만들지 않는다. 원단 설명 섹션은 원단 후보 1장이고, 후보가 없으면 섹션을 만들지 않는다. 페이지 전체 디테일컷은 4장까지다(카탈로그 `photoBudgets.detail`). 콘티에서 섹션을 넣거나 원단 확대를 더할 때도 같은 한도를 함께 쓴다(`src/lib/pageTemplates/sectionActions.js`, `src/lib/pageTemplates/stageAdditions.js`). 추천과 원본 바인딩, 검수는 ADR-0012, ADR-0014를 따른다.
13. 콘티에서 디테일 요소 섹션을 나중에 넣으면 새 디테일컷을 만들지 않고, 착용 사진 한 장에서 부위 세 곳(의류 종류에 따라 어깨선, 목선, 기장, 실루엣, 허리선 중 셋)을 잘라 쓴다. 이 따온 항목은 디테일 요소 글 카드의 `pointItems`(`kind: 'wornCrop'`)에 저장하며 생성 요청과 과금이 없다. 서버는 원본 카드가 지워진 항목도 새 생성 요청으로 바꾸지 않는다(`content_roles.py`의 `validate_storyboard_v4`). 원본 사진을 고르는 순서는 `src/lib/pageTemplates/README.md`를 따른다.
14. 콘티 무대의 AI 디테일 칸은 원본에서 그 부위를 자른 사진 위에 옅은 막을 씌우고 "{부위} 확대컷"과 "AI가 이 부분을 새로 만들어요"를 보여 준다(`src/features/storyboard/StoryboardStage.jsx`). 화면 표시일 뿐 저장 필드가 아니다. 옆 판은 원본 전체와 촬영 대상 이름을 보여 준다(ADR-0012).

### 섹션 디자인과 첫 장 제목

15. 섹션 디자인은 배치와 디자인 선택 두 줄로 고르지만 저장값은 `pageDesignId` 하나다. 두 줄은 카탈로그 `choiceAxes`에서 만든 화면 표시이며 블록에 따로 저장하지 않는다(`src/lib/pageTemplates/designAxes.js`). 두 줄이 없는 섹션은 디자인 목록 하나로 고르고, 고를 것이 하나뿐인 줄은 숨긴다.
16. 사진 위에 제목을 얹는 첫 장 디자인(`P01-D09`, `P01-D09-G`)은 가운데, 위, 아래 세 자리를 모두 잰다. 제목 줄마다 사진 픽셀 하위 10%의 대비를 재서 흰 글과 검은 글 중 나은 쪽이 3:1 이상인 자리를 쓴다. 가장 높은 대비의 90% 안에 드는 자리가 여럿이면 가운데, 아래, 위 순으로 고른다. 어느 자리도 3:1이 안 되면 `#111111` 60% 검정 받침에 흰 글을 쓴다. 받침은 가운데에 먼저 두고, 받침 자리의 절반 이상이 옷의 세로 범위(의류 종류와 샷별 카탈로그 `garmentBands`)와 겹치면 아래에 둔다. 두 자리 모두 그만큼 겹치면 덜 겹치는 쪽에 두고, 겹치는 정도가 같으면 가운데에 둔다(`page_template_contrast.py`의 `_plate_position`). 정본은 카탈로그 `firstTitlePlate`와 디자인의 `positionFallback`이고, 서버 측정은 `server/app/agents/page_template_contrast.py`다.

### 카피

17. 템플릿 콘티는 카피를 늘 켠다. 콘티에 들어올 때와 진행 버튼으로 마네킹 단계에 넘어가기 전에 프로젝트의 `copywriting`을 `true`로 맞춘다(`src/features/storyboard/v4Copywriting.js`).
18. AI가 채우는 글은 디테일 요소의 부위 설명(`analysis.partDescriptionCopy`, `server/app/agents/page_part_copy.py`), 후기 초안(20항), 상품 소개 본문이다. 기본 디자인이 아닌 일부 디테일 요소 디자인은 부위 설명이 없을 때 셀러 강조 특징 설명(`analysis.featureCopy`)으로 대신 채운다. 상세페이지 작업의 AG-02는 사진마다 `headline`만 만드는데(`server/app/workers/detail_page_job.py`의 `_gen_copy`), 카탈로그 글 칸에는 `headline` 역할이 없어서 조립에 쓰이지 않는다. 그래서 첫 장 영문 상품명, 착용컷 제목과 소개, 원단 제목과 설명, 색상 소개, 디테일 요소 제목과 핏 설명, 상품 소개 제목과 요약, 추천 대상, 질문과 답, SNS 문구 같은 다른 AI 글 칸은 빈 글과 안내 문구(`placeholder`)로 나간다(`page_template_assembler.py`의 `_content_for`, `_render_text`). 상품명, 색 이름, 소재 같은 사실 칸은 상품과 분석 값을 그대로 쓴다.
    - 상품 소개 본문(`seller+ai`)에는 셀러가 분석 화면에서 쓴 MD 코멘트(`analysis.mdComment`)를 넣는다(2026-11-13 오너 결정). 셀러마다 말투와 형식이 달라서, AI가 실제 쇼핑몰 상세페이지에 실리는 MD 코멘트처럼 다듬고 고쳐서 넣는다. 감성형과 쇼룸형 모두 넣는다. 다듬는 말투와 형식은 카피라이팅 작업이 정한다.
    - 다듬기가 붙기 전에는 두 템플릿 모두 셀러 원문을 줄바꿈까지 그대로 하나의 글상자에 넣는다(`page_template_assembler.py`의 `_seller_comment`, `_render_paragraphs`). 셀러 글이 없거나 공백뿐이면 AI가 초안을 쓴다. 생성 대기 화면은 셀러 원문이 든 본문을 늦게 온 카피로 덮지 않는다(`src/lib/editorWaitSkeleton.js`).
    - 쇼룸형 상품 소개(한눈에 보는 요약, `T06-D01`)는 요약 판 맨 위에 영문 라벨 "Comment"와 MD 코멘트 본문을 둔다.
    - 카탈로그의 상품 소개 설명 문구(`storyboardTextNote`)는 "MD 코멘트를 넣어요"다. 다만 글만 있는 상품 소개는 콘티의 섹션 추가 목록에 나오지 않으므로(`src/features/storyboard/StoryboardSectionPicker.jsx`의 `isHiddenStoryboardKind`) 지금 화면에는 이 문구가 보이지 않는다.
19. 카피 체계(어느 칸에 무엇을 어떤 말투로 쓸지, 굵게와 강조를 어디에 줄지)는 별도 카피라이팅 작업에서 정한다.
20. 후기 섹션은 2026-10-26 오너 결정을 따른다. 셀러가 고치기 쉽도록 AI가 후기 초안을 미리 넣어 두고, 셀러가 에디터에서 실제 후기로 고친다. 초안은 매번 조금씩 다르게 쓰고, 이름 칸 기본값은 "김태희", 별점 기본값은 5개다. 카탈로그의 후기 글 칸(`items.n.text`, `items.n.who`)은 AI 초안 칸(`aiDraft`)이다.

### 지운 것과 막은 것

21. 사진 양(`composeMode`, 기본형과 확장형)은 없앴다. 화면, API 요청과 응답, 초안 저장, 스토어에서 지웠고 `projects.compose_mode` DB 컬럼만 남는다. 컷 수는 템플릿과 상품 조건이 정하고 셀러가 콘티에서 더하거나 뺀다.
22. 콘티에 들어간 뒤에는 그 프로젝트의 상품 입력이나 분석 주소로 가면 콘티로 돌려보내고 "콘티를 만든 뒤에는 분석으로 돌아갈 수 없어요"를 보여 준다. 상세페이지를 이미 만들었거나 에디터에 들어간 프로젝트는 콘티 대신 에디터로 가라는 안내 창을 띄운다(`src/features/shell/shell.jsx`의 `useStoryboardInputGuard`, `DoneGuardModal`). 콘티 화면에 들어가면 브라우저 저장소에 프로젝트별 표식을 남기고 그 표식으로 판단한다(`src/lib/storyboardEntered.js`, `src/apps/seller/App.jsx`). 새 상품 만들기는 다른 프로젝트라 입력, 분석, 콘티 순서로 간다. 표식은 브라우저에만 있으므로 저장소를 쓸 수 없는 브라우저에서는 막지 않는다.
23. 콘티에는 "수정 완료" 잠금이 없다(`Storyboard.jsx`). 바꾼 것은 되돌리기로 무른다. 얼굴 노출과 앵글 선택은 옆 판에서 숨겼고 필드와 생성 로직은 그대로 둔다(`src/features/storyboard/StoryboardPhotoPanel.jsx`). 얼굴 노출은 사진 칸의 기본값을 쓴다.

### 조립 엔진과 에디터 블록

24. 페이지 조립 엔진은 프론트(`src/lib/pageTemplates/assemble.js`)와 서버(`server/app/agents/page_template_assembler.py`) 두 벌이다. 같은 카탈로그와 같은 사례 파일(`tests/fixtures/page_templates_cases.json`, 304건)로 두 결과가 같은지 시험한다. 카탈로그가 잘못되면 서버는 시작하지 않고(`server/app/main.py`의 `load_catalog`), 프론트도 같은 검사(`validateCatalog`)를 한다.
25. 조립 결과 에디터 블록은 `pageSectionKey`, `pageSectionKind`, `pageDesignId`를 가진다. `kind`는 섹션 첫 카드의 내부 섹션 역할(`hooking`, `styling`, `studio`, `product`)이고, 첫 카드가 글 카드나 내 이미지면 `info`다. 블록에는 `contentRole`이 없고, 사진 요소가 `slotKey`, `roleHint`, `sourceBlockId`를 가진다. 페이지 끝에는 자동 블록 넷을 붙인다. 사이즈 안내(`size`), 세탁 안내(`care`), AI 생성 안내(`ai-notice`), 공통 주의(`common-notice`)다(`assemble_v4`, `server/app/agents/page_assembler.py`의 `build_auto_blocks`). 에디터 빈 칸 판정은 ADR-0019를 따른다. `pageSectionKey`가 있는 블록의 사진 칸은 kind가 `info`여도 일반 사진 칸처럼 판정하므로, 디테일 요소, 후기, 궁금한 점 섹션의 실패 칸도 에디터에서 원래 콘티대로 다시 만들 수 있다. 자동 블록 넷은 다시 만들기와 AI로 채우기 대상이 아니다.

## 결과

- 콘티의 섹션 순서와 장수를 셀러가 템플릿 안에서 바꾼다. 저장 계약(`blocks: list`)은 그대로이고 블록 필드만 바뀐다. DB 마이그레이션은 없다.
- 상세페이지 견적과 생성은 AI 사진 카드만 센다. 글 카드와 따온 항목은 비용이 없다.
- 바꾸지 않는 것: ADR-0003의 컷 레시피, ADR-0004의 레퍼런스 소유, ADR-0006의 적용 의류 범위, ADR-0009와 ADR-0011의 참고 범위와 자연 변주, ADR-0012와 ADR-0014의 디테일 원본 바인딩과 검수, ADR-0016의 비슷한 컷 계보, ADR-0017의 부분 발행, 구매 정보(사이즈, 소재, 세탁, 배송, 교환, 반품, 필수 고지)는 에디터에서 넣는다는 원칙.

## 대체와 개정

- ADR-0005를 대체한다. 그 문서에서 이어 쓰는 것은 컷 종류 셋, 사진 목적을 내부값으로 두고 사용자가 고르지 않는다는 원칙, 내 이미지의 저장 모양(`sectionRole`은 이제 비운다), 구매 정보는 에디터에서 넣는다는 원칙이다. 네 섹션 화면 분류, v2 승격 규칙, 사진 양은 없앴다.
- 이 결정에 맞춰 일부 문장을 고친 ADR: ADR-0002(사진 양), ADR-0008(제품컷 시드), ADR-0012(디테일 장수와 무대 표시), ADR-0013(세트 카드와 배경 버튼 자리), ADR-0015(생성예시 갤러리), ADR-0016(비슷한 컷 자리), ADR-0019(빈 칸 판정). 용어만 고친 ADR: ADR-0010, ADR-0011, ADR-0018, ADR-0020, ADR-0025.

## 확인 중

- 카피 체계 전반과 MD 코멘트를 다듬는 말투와 형식. 모두 별도 카피라이팅 작업에서 정한다.
