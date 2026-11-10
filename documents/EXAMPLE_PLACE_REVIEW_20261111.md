실제 모델: gpt-6-astra · effort: xhigh (현재 세션 turn_context 기록에서 확인)

# 예시 장소 분류 검토 — 2026-11-11

작업 브랜치: `codex/example-place-review-20261111`, 기준 커밋 `ed817527`. 작업 폴더는 `/Users/daily/.codex/worktrees/place-review-1111/wearless_studio`이며 git 쓰기·브라우저·유료 API 호출은 하지 않았다.

## 결과

- 낱장 `review`에 `placeGroup`이 있던 27장 중 24장을 사진으로 확정했다. 썸네일이 없는 3장은 그대로 남겼다. 27장은 독립 예시 10장과 세트 멤버 17장으로 구성된다.
- `status: review` 세트 17개를 모두 확인했다. 장소 충돌 12건을 해소했고 조명 관련 검토만 있던 5개의 검토 표시는 유지했다. 이 중 건물 외벽 세트 1개는 장소만 수정했다.
- 멤버의 장소를 확정하면서 연결된 다른 세트 6개의 장소 필드도 일치시켰다. 실제 변경은 예시 24개·세트 19개이며, 이미지·세트 구성·저장 구조는 그대로다.
- 현재 선택 가능한 스타일링 사진의 장소 미상은 **7장→0장**, 8개 장소 탭에 들어가는 고유 사진은 **216장→223장**이다.
- **여성 상의 카페는 0장→2장, 카드도 0개→2개**다. 두 장 모두 전신이며 상반신 카페는 0장 그대로다.

카페로 복구된 여성 상의 ID:

- `ex_styling_women_top_full_mia_cafe_snapshot_01` — 감성형 첫 장 기본 사진. 카페 장소를 확정했으며 `garmentFullyVisible` 검토는 유지했다.
- `ex_styling_women_top_full_snapshot_01` — 카페 야외 좌석 사진.

## 요청의 전제와 현재 코드의 차이

`storyboardGallery.js`의 `examplePlace()`는 `status`나 `review`로 사진을 제외하지 않는다. `placeType`을 먼저 사용하고, 없으면 `scene.placeGroup`과 `urbanSubtype`을 쓴다. 따라서 세트 74개는 수정 전에도 장소 미상이 0개였고, `review`를 해제하는 것만으로 탭 장수가 늘지는 않는다. 카페 검토 세트 2개는 모두 남성 상의다. 여성 상의의 `set_style_women_top_cafe_attrangs_170103_prod02`는 사진이 카페 앞 차도·보도여서 기존 `storefront-street` 분류를 유지했다.

`example_tags.json`의 380개는 과거 태깅 대상이다. 현재 카탈로그는 독립 예시 107개(제품컷 포함) + 세트 멤버 259개 = 366개이며, 선택 가능한 세트는 교체된 세트 제외 73개다. 태깅 대상 중 현재 카탈로그에 없는 ID는 27개이고, 현재 카탈로그의 제품컷 13개는 원래 태깅 대상이 아니다. 두 목록의 공통 ID는 353개다. 아래 집계는 태깅 대상 380개와 실제 선택 가능한 스타일링 226개를 구분한다.

## 사진 확인 범위와 판정 기준

원본 확인 경로: `/Users/daily/Documents/wearless_studio/.scratch/template-po-20261018/tone-order-20261110/thumbs/<exampleId>.webp`. 대상 27장과 검토 세트 17개의 멤버를 합치면 중복 제외 86장이고, 그중 있는 83장을 직접 보았다. 세트 상속과 근접 컷을 확인하려고 관련 세트의 다른 컷도 함께 보아 총 **101개 고유 썸네일**을 확인했다. 원본은 읽기만 했으며 확인용 합본은 이번 작업 폴더의 `.scratch/example-place-review-20261111/`에만 만들었다.

기존 값만 사용했다. 카페의 좌석·음료·카운터가 중심이면 `cafe`, 매장 앞 차도·보도가 중심이면 `urban/commercial_street`, 건물 외벽·출입문·안뜰이면 `urban/building_front`로 판단했다. 세트 멤버는 기존처럼 세트의 장소를 상속하며 별도 `scene` 필드를 추가하지 않았다. 애매한 방·좌석은 아래 이유에 가장 가까운 장소를 명시했다.

## 낱장 27장 판정

| ID | 장소 전→후 | 확정 값 | 이유 / 남은 검토 |
|---|---|---|---|
| `ex_styling_men_top_medium_snapshot_02` | 미상→건물 앞 | `urban/building_front` | 금속 외벽과 외부 계단·난간이 보여 건물 앞에 배정. |
| `ss_set_style_women_dress_botanical_attrangs_160835_root01_03` | 자연→자연 | `nature` (세트 상속) | 수영장과 열대 식재가 있는 세트의 테라스 컷이므로 자연으로 확정. 남은 검토: `garmentFullyVisible`. |
| `ss_set_style_women_outer_rainy_courtyard_nearwear_12582_prod03_02` | 건물 앞→건물 앞 | `urban/building_front` (세트 상속) | 벽돌 외벽·출입문과 차양 아래 안뜰이 보여 건물 앞으로 확정. |
| `ss_set_style_women_outer_storefront_lowclassic_6495851_prod02_02` | 상권 거리→상권 거리 | `urban/commercial_street` (세트 상속) | 책이 진열된 매장 출입구와 보도가 보여 상권 거리로 확정. |
| `ex_styling_women_dress_full_home_04` | 미상→집 | `home` | 커튼 달린 창과 식탁·의자가 있는 작은 방으로, 집에 가장 가까움. |
| `ss_set_03_resort_03` | 자연→자연 | `nature` (세트 상속) | 근접 컷의 베이지 벽·바닥이 나머지 두 수영장 테라스 컷과 이어져 자연으로 확정. |
| `ex_styling_men_bottom_full_snapshot_01` | 미상→카페 | `cafe` | 목재 출입문 안으로 카페 테이블·의자·펜던트 조명이 보여 카페 입구로 분류. 남은 검토: `lightSide`. |
| `ex_styling_women_top_full_mia_cafe_snapshot_01` | 미상→카페 | `cafe` | 매장 창 앞 야외 테이블 위 테이크아웃 컵이 보여 카페 야외석으로 분류. 남은 검토: `garmentFullyVisible`. |
| `ss_set_style_women_outer_loadingzone_beidelli_3920_prod03_03` | 건물 앞→실내 | `indoor` (세트 상속) | 화물용 승강기·보호 배관·카트가 있는 실내 공용 하역 공간이므로 실내로 분류. |
| `ss_set_style_women_outer_terrace_lowclassic_6495831_prod02_03` | 자연→자연 | `nature` (세트 상속) | 창가 근접 컷이며 다른 두 컷의 산 조망·난간 있는 테라스와 이어져 자연으로 확정. |
| `ss_set_style_women_dress_home_living_musinsa_6570399_root06_01` | 집→집 | `home` (세트 상속) | 커튼·흰 마룻바닥·소파가 이어지는 세트의 거실 컷으로 집에 배정. 남은 검토: `emptyBottom`. |
| `ss_set_05_cafe_02` | 카페→카페 | `cafe` (세트 상속) | 벽 붙박이 벤치와 원형 손님용 테이블이 있는 공간으로 카페에 가장 가까움. |
| `ss_set_style_women_top_cafe_attrangs_170103_prod02_02` | 상권 거리→상권 거리 | `urban/commercial_street` (세트 상속) | 카페 건물 바깥 차도·보도에서 촬영한 컷으로, 좌석 중심 컷과 구분해 상권 거리에 배정. 남은 검토: `shot`. |
| `ex_styling_women_outer_full_bakery_storefront_01` | 미상→상권 거리 | `urban/commercial_street` | 빵 진열창 바로 앞 보도에서 촬영하여 상점 앞·상권 거리로 분류. 남은 검토: `emptyBottom`, `garmentFullyVisible`. |
| `ss_set_01_indoor_03` | 실내→실내 | `indoor` (세트 상속) | 큰 유리창과 실내 조경을 둔 건물 공용부가 보여 실내로 확정. |
| `ss_set_05_cafe_01` | 카페→카페 | `cafe` (세트 상속) | 벽 붙박이 벤치와 손님용 원형 테이블이 보여 카페에 가장 가까움. |
| `ex_styling_women_dress_medium_resort_06` | 미상→미상 | `null` | 지정 폴더에 썸네일 없음. 사진을 못 보아 수정하지 않음. 남은 검토: `garmentFullyVisible`, `placeGroup`. |
| `ex_styling_men_outer_full_04` | 미상→미상 | `null` | 지정 폴더에 썸네일 없음. 사진을 못 보아 수정하지 않음. 남은 검토: `lightSide`, `placeGroup`. |
| `ex_styling_women_outer_full_terrace_back_01` | 미상→카페 | `cafe` | 야외 테이블·의자·파라솔과 매장 안 카운터가 보여 카페 테라스로 분류. |
| `ss_set_style_women_top_convenience_beidelli_5067_prod03_03` | 상권 거리→상권 거리 | `urban/commercial_street` (세트 상속) | 상품 진열 매장 입구와 가로변 벤치가 보여 상권 거리로 확정. |
| `ss_set_style_women_top_cafe_attrangs_170103_prod02_03` | 상권 거리→상권 거리 | `urban/commercial_street` (세트 상속) | 카페 입면 앞 도로와 보도를 걷는 컷이므로 상점 앞·상권 거리로 확정. 남은 검토: `lightSide`. |
| `ex_styling_women_dress_full_park_05` | 미상→미상 | `null` | 지정 폴더에 썸네일 없음. 사진을 못 보아 수정하지 않음. 남은 검토: `placeGroup`. |
| `ss_set_style_women_outer_storefront_lowclassic_6495851_prod02_01` | 상권 거리→상권 거리 | `urban/commercial_street` (세트 상속) | 책 진열 매장 입구에서 보도로 나오는 컷이므로 상권 거리로 확정. |
| `ss_set_style_women_outer_rainy_courtyard_nearwear_12582_prod03_01` | 건물 앞→건물 앞 | `urban/building_front` (세트 상속) | 차양 아래 테이블이 놓인 벽돌 건물 안뜰로, 업종 단정 없이 건물 앞에 배정. 남은 검토: `face`, `lightSide`, `special`. |
| `ss_set_01_women_bottom_street_slowand_10666_02` | 생활 골목→생활 골목 | `urban/local_street` (세트 상속) | 상점 간판보다 좁은 골목과 연속된 건물 벽이 중심이어서 생활 골목으로 확정. 남은 검토: `shot`. |
| `ex_styling_women_top_full_snapshot_01` | 미상→카페 | `cafe` | 벽돌 외벽 앞 목재 좌석·음료·접시가 보여 카페 야외석으로 분류. |
| `ss_set_style_women_top_park_realcoco_161014511_prod02_03` | 공원→공원 | `park` (세트 상속) | 잔디·수목·공원 벤치가 있는 세트의 근접 컷이므로 공원으로 확정. 남은 검토: `lightHard`. |

## review 세트 17개 판정

| 세트 ID | placeGroup 전→후 | 확정 placeType / 탭 | 이유 / 상태 |
|---|---|---|---|
| `set-09-men-outer-outdoor-resort-upnormal-7100` | `nature`→`nature` | `resort-terrace` / 자연 | 수영장과 산·소나무가 보이는 휴양 테라스여서 자연 유지. 상태: `ok`. |
| `horizon-sequence-07-men-top-pleated` | `studio`→`studio` | `horizon-studio` / 스튜디오 | 다섯 컷 모두 단색 배경과 바닥의 스튜디오; windowGradient 검토는 유지. 상태: `review`. |
| `set-01-men-top-indoor-retro-counter-byslim-28625` | `indoor_family`→`cafe` | `cafe-shop-interior` / 카페 | 조미료 통·금속 카운터·주방 창이 있는 소형 식음 매장으로 카페에 배정. 상태: `ok`. |
| `set-style-women-dress-botanical-attrangs-160835-root01` | `outdoor_family`→`nature` | `resort-terrace` / 자연 | 수영장·열대 식재·테라코타 바닥이 있는 휴양 테라스로 자연에 배정. 상태: `ok`. |
| `set_style_women_top_home_hotping_48041_prod02` | `home`→`home` | `home-interior` / 집 | 싱크대·주방 작업대와 생활 의자가 보여 집으로 확정. 상태: `ok`. |
| `set_style_women_top_park_realcoco_161014511_prod02` | `park`→`park` | `park-garden` / 공원 | 잔디·나무·공원 벤치가 함께 보여 공원으로 확정. 상태: `ok`. |
| `horizon-sequence-figma-s08-v2` | `studio`→`studio` | `horizon-studio` / 스튜디오 | 여섯 컷 모두 단색 벽·바닥의 스튜디오; windowGradient 검토는 유지. 상태: `review`. |
| `horizon-sequence-figma-s14-v2` | `studio`→`studio` | `horizon-studio` / 스튜디오 | 여섯 컷 모두 단색 벽·바닥의 스튜디오; windowGradient 검토는 유지. 상태: `review`. |
| `set-04-men-top-indoor-window-room-anotheroffice-1881` | `indoor`→`home` | `home-interior` / 집 | 창·작은 책상·의자를 둔 방으로 주거 방에 가장 가까워 기존 집 분류를 확정. 상태: `ok`. |
| `set-09-coast` | `outdoor_family`→`nature` | `waterfront` / 자연 | 물과 항만 방파제가 세 컷에 이어져 해안·수변인 자연으로 확정. 상태: `ok`. |
| `set-style-women-dress-night-riverwalk-maybins-40948-root07` | `outdoor_family/urban`→`nature` | `waterfront` / 자연 | 강물·수변 난간·물에 비친 야경이 중심이므로 수변인 자연으로 확정. 상태: `ok`. |
| `set-06-men-common-outdoor-quiet-alley-jogunshop-32115` | `urban/building_front`→`urban/building_front` | `urban-building-exterior` / 건물 앞 | 세 컷 모두 건물 외벽·문·창 바로 앞이며 골목 전경이 없어 건물 앞으로 확정; lightQuality 검토 유지. 상태: `review`. |
| `set-07-women-bottom-riverside-bbibbongunni-22538` | `outdoor_family/urban`→`nature` | `waterfront` / 자연 | 강과 수변 산책로가 보이며 근접 컷의 포장도 이어져 자연으로 확정. 상태: `ok`. |
| `horizon-sequence-09-women-dress-knit` | `studio`→`studio` | `horizon-studio` / 스튜디오 | 다섯 컷 모두 단색 스튜디오 배경; lightQuality 검토는 유지. 상태: `review`. |
| `set-08-park` | `park/urban`→`park` | `park-garden` / 공원 | 나무·잔디·조경 산책로가 이어지는 공원으로 확정. 상태: `ok`. |
| `set-07-men-top-indoor-small-shop-upnormal-7126` | `cafe`→`cafe` | `cafe-shop-interior` / 카페 | 작은 매장의 창가 테이블·스툴·물품 선반이 보여 기존 카페·소형 매장 분류 확정. 상태: `ok`. |
| `set-12-men-common-indoor-reading-corner-gopeople-7896` | `indoor_family`→`indoor` | `library-interior` / 실내 | 가득 찬 책장과 독서 테이블이 있는 서점·독서 공간이므로 실내로 확정. 상태: `ok`. |

## 대상 멤버와 연결된 세트의 장소 정합성 수정 6개

| 세트 ID | 변경 | 이유 |
|---|---|---|
| `set-05-cafe` | `home`→`cafe` | 카페 세트의 벽 붙박이 좌석·원형 테이블을 확인하여 home과 placeType의 불일치 해소. |
| `set_style_women_outer_loadingzone_beidelli_3920_prod03` | `indoor`→`indoor`; `industrial-yard`→`building-interior` | 야외 작업장이 아니라 승강기·보호 배관·카트가 있는 실내 공용 하역 공간으로 분류. |
| `set_style_women_outer_rainy_courtyard_nearwear_12582_prod03` | `urban/building_front`→`urban/building_front`; `industrial-yard`→`urban-building-exterior` | 차양·외벽·문·야외 좌석이 있는 건물 안뜰로 산업 현장 근거가 없어 건물 앞으로 분류. |
| `set_style_women_outer_storefront_lowclassic_6495851_prod02` | `urban/building_front`→`urban/commercial_street` | 책 진열 매장 출입구와 보도가 보여 기존 상권 거리 placeType에 하위 분류를 맞춤. |
| `set_style_women_outer_terrace_lowclassic_6495831_prod02` | `indoor`→`nature` | 두 전신 컷의 산 조망·개방된 난간 테라스를 기준으로 기존 resort-terrace와 scene을 일치시킴. |
| `set_style_women_top_cafe_attrangs_170103_prod02` | `urban/urban`→`urban/commercial_street` | 카페 입면 앞 차도·보도를 걷는 세트여서 상권 거리 하위 분류를 확정. |

## 장소별 장수 전후

단위는 사진 장수이며 세트 열만 세트 개수다. `전→후` 표기다. 태깅 380개 열은 현재 카탈로그에 없는 예시도 기존 태그로 계산했다.

| 장소 | 태깅 대상 380장 | 선택 가능한 스타일링 226장 | 태깅 세트 74개 |
|---|---:|---:|---:|
| 카페 (`cafe`) | 16→20 | 10→14 | 3→3 |
| 집 (`home`) | 24→25 | 23→24 | 6→6 |
| 실내 (`indoor`) | 44→47 | 40→43 | 11→12 |
| 생활 골목 (`alley`) | 35→32 | 29→26 | 4→3 |
| 상권 거리 (`street`) | 25→26 | 25→26 | 8→8 |
| 건물 앞 (`building`) | 46→47 | 39→40 | 7→7 |
| 공원 (`park`) | 13→13 | 13→13 | 4→4 |
| 자연 (`nature`) | 37→37 | 37→37 | 11→11 |
| 스튜디오 (`studio`) | 130→130 | 3→3 | 20→20 |
| 미상 (`unknown`) | 10→3 | 7→0 | 0→0 |

실제 갤러리 집계는 `withStoryboardSpaceSetExamples()`로 현재 카탈로그를 합치고, `storyboardSpaceSetsFor()`와 `photoGalleryChoices()`를 그대로 실행했다. 조건은 `cutTypes=["styling"]`, `light="all"`, `identityKind=null`, `shot="all"/"full"/"medium"`이다. 카드 수는 세트 묶음을 1개로 세며 고정 시그니처는 제외했다. 적용 옷 종류가 겹치는 예시가 있어 성별·옷 종류별 행을 더하면 고유 사진 합계와 다르다.

### 성별·옷 종류별 사진 장수

| 성별·옷 종류·구도 | 카페 | 집 | 실내 | 생활 골목 | 상권 거리 | 건물 앞 | 공원 | 자연 | 미상 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 여성 상의 전신+부분 | 0→2 | 4→4 | 4→4 | 4→4 | 9→9 | 6→6 | 3→3 | 4→4 | 2→0 |
| 여성 하의 전신+부분 | 0→0 | 9→9 | 6→6 | 7→7 | 9→9 | 2→2 | 0→0 | 8→8 | 0→0 |
| 여성 아우터 전신+부분 | 0→2 | 0→0 | 3→6 | 5→5 | 4→5 | 6→3 | 0→0 | 3→3 | 3→0 |
| 여성 원피스 전신+부분 | 0→0 | 4→5 | 5→5 | 5→5 | 0→0 | 3→3 | 3→3 | 9→9 | 1→0 |
| 남성 상의 전신+부분 | 6→6 | 3→3 | 9→9 | 1→1 | 0→0 | 2→3 | 1→1 | 3→3 | 1→0 |
| 남성 하의 전신+부분 | 3→4 | 3→3 | 6→6 | 3→3 | 3→3 | 10→10 | 3→3 | 7→7 | 1→0 |
| 남성 아우터 전신+부분 | 1→1 | 0→0 | 7→7 | 4→1 | 0→0 | 10→13 | 3→3 | 3→3 | 0→0 |

### 성별·옷 종류별 표시 카드 수

| 성별·옷 종류·구도 | 카페 | 집 | 실내 | 생활 골목 | 상권 거리 | 건물 앞 | 공원 | 자연 | 미상 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 여성 상의 전신+부분 | 0→2 | 2→2 | 2→2 | 4→4 | 3→3 | 4→4 | 1→1 | 2→2 | 0→0 |
| 여성 하의 전신+부분 | 0→0 | 5→5 | 2→2 | 5→5 | 3→3 | 2→2 | 0→0 | 4→4 | 0→0 |
| 여성 아우터 전신+부분 | 0→2 | 0→0 | 1→2 | 5→5 | 2→3 | 2→1 | 0→0 | 1→1 | 0→0 |
| 여성 원피스 전신+부분 | 0→0 | 2→3 | 3→3 | 3→3 | 0→0 | 1→1 | 1→1 | 3→3 | 0→0 |
| 남성 상의 전신+부분 | 2→2 | 1→1 | 3→3 | 1→1 | 0→0 | 2→3 | 1→1 | 1→1 | 0→0 |
| 남성 하의 전신+부분 | 1→2 | 1→1 | 2→2 | 1→1 | 1→1 | 8→8 | 1→1 | 3→3 | 0→0 |
| 남성 아우터 전신+부분 | 1→1 | 0→0 | 5→5 | 2→1 | 0→0 | 6→7 | 1→1 | 1→1 | 0→0 |

### 구도별 사진 장수

| 성별·옷 종류·구도 | 카페 | 집 | 실내 | 생활 골목 | 상권 거리 | 건물 앞 | 공원 | 자연 | 미상 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 여성 상의 전신 | 0→2 | 1→1 | 1→1 | 3→3 | 3→3 | 2→2 | 1→1 | 3→3 | 2→0 |
| 여성 상의 부분 | 0→0 | 3→3 | 3→3 | 1→1 | 6→6 | 4→4 | 2→2 | 1→1 | 0→0 |
| 여성 하의 전신 | 0→0 | 5→5 | 5→5 | 4→4 | 7→7 | 2→2 | 0→0 | 4→4 | 0→0 |
| 여성 하의 부분 | 0→0 | 4→4 | 1→1 | 3→3 | 2→2 | 0→0 | 0→0 | 4→4 | 0→0 |
| 여성 아우터 전신 | 0→2 | 0→0 | 2→3 | 4→4 | 2→3 | 2→1 | 0→0 | 2→2 | 3→0 |
| 여성 아우터 부분 | 0→0 | 0→0 | 1→3 | 1→1 | 2→2 | 4→2 | 0→0 | 1→1 | 0→0 |
| 여성 원피스 전신 | 0→0 | 2→3 | 4→4 | 2→2 | 0→0 | 2→2 | 2→2 | 4→4 | 1→0 |
| 여성 원피스 부분 | 0→0 | 2→2 | 1→1 | 3→3 | 0→0 | 1→1 | 1→1 | 5→5 | 0→0 |
| 남성 상의 전신 | 3→3 | 1→1 | 3→3 | 0→0 | 0→0 | 0→0 | 1→1 | 1→1 | 0→0 |
| 남성 상의 부분 | 3→3 | 2→2 | 6→6 | 1→1 | 0→0 | 2→3 | 0→0 | 2→2 | 1→0 |
| 남성 하의 전신 | 2→3 | 1→1 | 3→3 | 1→1 | 2→2 | 3→3 | 1→1 | 4→4 | 1→0 |
| 남성 하의 부분 | 1→1 | 2→2 | 3→3 | 2→2 | 1→1 | 7→7 | 2→2 | 3→3 | 0→0 |
| 남성 아우터 전신 | 0→0 | 0→0 | 3→3 | 1→0 | 0→0 | 5→6 | 1→1 | 1→1 | 0→0 |
| 남성 아우터 부분 | 1→1 | 0→0 | 4→4 | 3→1 | 0→0 | 5→7 | 2→2 | 2→2 | 0→0 |

`부분`은 상의·아우터·원피스의 상반신, 하의의 하반신 선택에 해당한다. 미상 카드 수가 수정 전에도 0인 것은 미상 사진이 어떤 장소 탭에도 들어가지 않기 때문이다.

별도 잔여 사항: 현재 스타일링 후보 중 `studio`로 분류된 3장은 수정 전후 그대로 8개 장소 탭 밖에 있다. 이번 `placeGroup` 검토 대상이 아니므로 바꾸지 않았다: `ex_styling_women_dress_medium_home_08`, `ex_styling_men_outer_full_06`, `ex_styling_men_outer_full_08`.

## 못 본 ID

- `ex_styling_men_outer_full_04`
- `ex_styling_women_dress_full_park_05`
- `ex_styling_women_dress_medium_resort_06`

세 ID 모두 현재 `src/data/genExamples.json`에 없으며, `placeGroup: null` 및 기존 검토 항목을 보존했다. 지정 경로 밖에서 사진을 내려받거나 파일명만으로 분류하지 않았다.

## 파생 데이터와 보존 범위

- `server/app/data/example_tags.json`: 장소 필드, 해당 `review`/`conflicts`의 `placeGroup`, 이에 따른 `status`와 `counts`만 수정. 예시 `ok/review`는 212/168→226/154, 세트는 57/17→69/5.
- `server/app/data/space_set_assets.json`: 건물 외벽·실내 하역장·건물 안뜰 세트 3개의 `placeType`을 맞춤.
- `src/data/storyboardSpaceSets.json`: 같은 세트 3개의 `place`와 `placeType`을 맞춤.
- `example_tags.json`을 별도 프런트엔드 태그 파일로 생성하는 경로는 없고 세 소비 모듈이 직접 가져온다. `templateFit`, 조명, 구도, 옷 종류, 컷 정보, URL, 이미지, 세트 멤버·순서, 릴리스 메타데이터는 보존했다. 초기 태깅의 `model`, `taggedAt`, `tagSheetVersion`도 원본 이력으로 유지했다. `counts.missing`은 태깅 데이터의 기존 집계이므로 썸네일 누락 수로 덮어쓰지 않았다.
- PR #608의 커밋 `76ca40da`는 적용 옷 종류와 카메라·구도 태그를 교정했다. `git log --oneline -3 -- server/app/data/example_assets.json src/data/genExamples.json` 및 해당 커밋을 확인했고, 그 두 파일은 이번에 바꾸지 않았다.

## 시험 및 검증

전체 시험은 브라우저 없이 Node 시험과 Vite SSR로 실행했다. 기존 `server/.venv`가 없어 설치된 Python 환경을 작업 폴더 안에서 링크했고, `PYTHONDONTWRITEBYTECODE=1`로 외부 Python 캐시 생성을 막았다. 임시 파일은 작업 폴더 안 `TMPDIR`로 모았다.

```sh
TMPDIR="$PWD/.scratch/example-place-review-20261111/tmp" PYTHONDONTWRITEBYTECODE=1 pnpm test:frontend
```

첫 실행: **5,345개 중 5,170 통과 / 175 실패 / 0 취소 / 0 건너뜀**, 종료 코드 1, 52.284초. 기존 `node_modules` 링크가 다른 작업 폴더를 향하여 Vite의 `.vite/deps` 캐시 갱신이 `EPERM`으로 차단됐다. 의존성은 같은 설치본을 읽도록 유지하고 캐시 디렉터리만 이번 작업 폴더 안에 두어 전체를 다시 실행했다.

캐시 분리 후 전체 재실행: **5,345/5,345 통과**, 실패·취소·건너뜀 0, 종료 코드 0, 55.503초. 장소 탭·갤러리 시험을 포함한 전체 결과다.

```text
ℹ tests 5345
ℹ pass 5345
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 55503.173417
```

변경 범위 검증: `python3 .scratch/example-place-review-20261111/verify-review.py` — **PASS**. 예시 24개·세트 19개 변경, 없는 3개 원문 보존, 모든 비장소 필드·세트 구성·이미지 메타데이터 보존, 서버/프런트 세트 74개 장소 일치, 각 카탈로그 장소 변경 3개, 실제 스타일링 장소 미상 0장 및 여성 상의 카페 2장을 확인했다.

장수 산출: `node .scratch/example-place-review-20261111/count-places.mjs <결과.json>`을 변경 전후 각각 실행했다. `git diff --check`도 통과했다. 로그·전후 집계·사진 확인용 합본은 `.scratch/example-place-review-20261111/`에 보관했다. 커밋·PR·브라우저 검증은 실행하지 않았다.
