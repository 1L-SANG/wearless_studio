# Wearless — 구현 현황·할 일 (TODO / Status)

> 역할: **설계 문서(PRD·계약·상태모델·파이프라인)에서 "해야 할 일 / 진행 상태 / 코드가 아직 계약을 못 따라간 갭"을 한곳에 모은 문서.**
> 설계 문서들은 "무엇이 맞는가(정체성·계약)"에 집중하고, "무엇이 아직 안 됐는가"는 여기서 추적한다.
> 갱신 방식: 작업을 끝낼 때마다 각 설계 문서를 고치지 않고 **이 문서만 주기적으로 갱신**한다.
> 정책 미확정(크레딧 단가·환불 등)과 단계별 실행 로드맵은 중복하지 않고 `backend_integration_plan.md` §10(로드맵)·§11(오픈 이슈)을 가리킨다.
> 최종 갱신: 2026-07-28

---

## 1. 코드 ↔ 계약 동기화 갭 (마이그레이션 TODO)

> "문서가 맞고 코드가 아직 못 따라간" 항목. 문서가 정본이다(`common_data_contract.md` §5 적용 방식).
> ✅ = 반영 완료(기록), 🔶 = 남음, 🆕 = 2026-06-14 결정으로 새로 추가.

### 완료 (기록용)

- ✅ `project` 신설(`createProject`/`getProject`/`patchProject`), `/editor/:id`를 projectId로 사용.
- ✅ 컷 토큰 통일(`cutType: styling|horizon|product` + `source: ai|mine`), `daily`·`studio` 제거. *(이후 ADR-0004로 `mirror`(거울샷) 추가 — 현재 4종)*
- ✅ 한국어 저장값 영문화(조정 enum, `MeasurementKey`, `subCategories {value,label}` 등) + 라벨 파생.
- ✅ `saveStoryboard` 생성 CTA에서 호출 후 이동.
- ✅ `generateDetailPage`가 저장된 콘티 기반 생성(`buildEditorBlocksFromStoryboard`).
- ✅ 크레딧 봉투(`{ data, credits }`) 5종 + `syncCredits` 단일화(에디터 로컬 account 제거).
- ✅ 제품컷 옵션 카탈로그화(`productDirections`/`productShotTypes`).
- ✅ **콘티 사용자 분류 v2** — 화면 섹션을 `핵심 장점 → 핏·코디 → 제품 확인`으로 통일하고 카드의 `contentRole`을 내부 기준값으로 사용. 전환 시점 테스트 프로젝트는 리셋하며 기존 `kind`·컷 이름 기반 저장분을 변환하지 않는다. `cutType`은 생성 레시피로 유지하되 2026-07-21부터 인스펙터에서 섹션별 허용 컷을 직접 고른다(ADR-0005).
- ✅ **콘티 사용자 분류 v3** — v2의 세 섹션을 대체해 `후킹 → 스타일링 → 스튜디오 → 의류 확인` 네 섹션을 화면·저장·서버 생성의 정본으로 통일. 기존 v2 저장본은 카드의 컷 종류에 따라 자동 승격한다(ADR-0005 후속 결정, 2026-08-05).
- ✅ **사진 양 두 가지로 통일** — 기본형(`basic`)·확장형(`extended`)만 새로 저장하고 두 방식은 같은 네 섹션을 사용. 확장형은 사진 수만 늘림.
- ✅ **제품 확인 방식 정리** — 제품 전체는 고스트샷 하나로 두고 플랫레이는 선택한 생성예시의 표현 방식으로 흡수. 디테일은 상품 전체 중 실제 `Detail` 입력이 하나 이상 있으면 제공하고 목표 색상에 없을 때는 타색 근거의 색만 전환. 행거 생성은 제거하고 아우터 착용 이미지에는 `아우터 열림 정도` 3가지를 제공(ADR-0008).
- ✅ **빈 완료 페이지 방지** — 상세페이지 AI 컷이 전부 실패하면 `all_cuts_failed`로 실패·예약 해제. 일부만 실패하면 성공 컷만 과금하고 실패 자리만 에디터에서 다시 만들 수 있게 유지.
- ✅ 콘티 '내 레퍼런스' 영속(`block.refImages`) + 에디터 `NewCutRequest.refImages` 입력.
- ✅ `lib/types.js`가 계약 미러링, `ProjectSummary`(`blockCount`·ISO `updatedAt`), id 생성기 `lib/ids.js` 이관.
- ✅ `saveEditorBlocks` — 저장 버튼 + 1.5s 디바운스 자동 저장 + 이탈 시 플러시(세션 내 재진입 유지).

### 남음

> 각 항목 끝의 **→ Phase N**은 그 갭을 처리할 단계. 백엔드 에이전트는 해당 Phase 착수 시, 그 Phase가 건드리는 함수의 갭을 **함께** 처리한다(별도 "코드 정리 Phase" 없음). `독립`은 백엔드와 무관해 아무 때나 가능.

- 🔶 **Product/Analysis 소유권 일원화** — `clothingType`·`measurements`·`measurementsUnknown`을 **Product 단일 소유**로. 현재 프론트는 이 필드들을 analysis 작업본에 두고 `saveAnalysis`가 product에 미러링하는 과도기 규칙(`src/mock/api.js` saveAnalysis). 최종은 analysis 레코드에서 제거. (구 계약 §7-4) **→ Phase 3**
- 🔶 **화면의 `Placeholder` 직접 import 정리** — 콘티 새 블록 썸네일·분위기 예시. 분위기 예시의 운영자 시드 전환과 함께. (구 계약 §7-9 잔여) **→ Phase 3~ (분위기 예시 시드)**
- 🔶 **죽은 코드 정리** — `AnalysisForm.jsx`의 미사용 `Analysis` 라우트 컴포넌트(구 시그니처 `analyzeProduct({})`·`saveAnalysis(null,…)` 호출 포함), `colorIds` 잔존 읽기, `catalogs.backgrounds`·`extendedColorPriority`(소비처 0). db 재작성 시 제거. (구 계약 §7-13) **→ Phase 7 (또는 수시)**
- 🔶 **TanStack Query 도입** — 백엔드 연동과 함께. 서버 상태를 Query 캐시로, store의 account/catalogs 캐시 제거. (구 상태모델 §8-7, `03_기술스택_결정서.md` §3) **→ Phase 1** *(부분 적용 완료: Pricing·Library·CreditsHistory에 useQuery 도입. 앱 전역 전환은 미완)*
- 🔶 **에디터 구매 정보 목록 확장** — 현재 조립기는 사이즈·세탁·AI 안내 자동 블록 3종만 만든다. PRD §10.14의 소재 혼용률·색상 옵션·배송/교환/반품·필수 고지와 도움/선택 콘텐츠를 `kind='info'` + `infoType` 일반 블록으로 추가하고, `내용 추가`에서 중요도순으로 보여주는 UI가 남았다. 필수 정보에 값이 없으면 `정보 입력 필요`로 표시한다. **→ 독립 (에디터 UI + 조립기)**
- ✅ **실제 생성예시 카탈로그 배선** — 2026-07-20 2단계 완료. 확정 manifest 검증·결정적 WebP thumb·서버 레지스트리 v2·프론트 카탈로그를 함께 만드는 `server/tools/release_genexamples.py`를 추가했고, QC 승인 부분집합을 불변 R2 릴리스 `2026-07-19-pilot-qc-01`로 발행해 서버·프론트에 함께 적용했다. 컷·샷·상품 종류·성별 필터(제품은 성별 공용), 현재 컷 종류 안의 rank 라운드로빈 최대 6장, 발행 variant별 범위 활성화, 같은 공간 pose 게이트, 빈 상태, 에디터/상세페이지 적용성 안전 닫기까지 실제 자산으로 검증했다. 2026-07-21 소비 규칙 개정으로 핏·코디의 서로 다른 컷 종류는 한 갤러리에 섞지 않는다. 2026-07-30에는 일반 블록 갤러리에 기존 개별 예시 최대 6장을 먼저 보여준 뒤 같은 조건의 공간 세트 멤버를 추가 선택지로 이어 붙이되, 자동 초안 배정에는 기존 개별 예시만 쓰도록 확정했다. 디테일은 실제 입력으로 만들 수 있는 핵심 부위만 상품당 1~3개 제공한다.
  - **대표 이미지 선별 체크포인트(2026-07-19)** — 선택판은 `anchor-v10`, 선택 묶음 75개·교정함 9개·후보 530장(빈 묶음 19개)이다. 일반 최대 6장·디테일 최대 3장으로 최종화했으며 선택은 **207장(착용 185·제품 22)**이다. 제품 ghost·flatlay는 고스트샷 목적지로 합치되 후보별 `presentationMethod` 증거를 보존한다. 제외한 11장은 워터마크·큰 글자, 같은 몰의 거의 같은 구도, 낮은 해상도 중복을 우선했고 원본과 제외 이유는 보존했다. 사용자 지정 Hatchingroom 교체와 Less Get More 승인 8/8장 활성·대기 0장 상태도 유지한다. 세부 로컬 상태는 `reference/genexamples/README.md`의 `대표 이미지 선택판 최신 규칙`을 본다.
  - **공간 세트 릴리스 체크포인트(2026-07-30)** — 오너 최종 결정은
    56세트·175멤버 승인, 미해결 0건이다. nearwear 우천 안뜰 세트는 승인했고
    wonlog 카페 세트는 원본까지 제외했다. 별도 `space-set manifest v1`과 릴리스
    도구, 프론트 카탈로그, 서버 plate+pose 바인딩을 구현했으며 175 pose·52
    대표 plate·175 all·175 thumb, 총 577개 객체의 로컬 스테이징 및 업로드
    dry-run까지 통과했다. 검토할 최종본은
    `space_set_release/2026-07-30/staging/space-sets-20260730-v1-final`이며,
    `release_stage.json`이 카탈로그와 577개 업로드 바이트를 봉인한다.
    오너 승인 후 R2 실제 업로드를 완료했고 원격 key 577개·누락 0·추가 0·
    파일 크기 불일치 0을 확인했다. 같은 sealed staging을 다시 검증한 뒤
    프론트·서버 정식 카탈로그에도 `space-sets-20260730-v1` 56세트·175멤버를
    함께 적용했다. v1은 세트당 대표 plate 1장이고,
    프로젝트별 복수 plate 배정은 후속 자산·계약 범위다. 서비스는 발행된
    `ssg1__<setId>__<instanceId>`만 촬영 세트로 지원하며, 과거 테스트용 임의
    공간 묶음은 호환·마이그레이션하지 않는다. 이 변경 배포 전 기존 테스트
    프로젝트를 초기화해야 한다. 기존 개별 생성예시는 그대로 유지한다.
    병합 전 카탈로그 점검에서 혼재했던 `placeType` 43종은 대표 배경 기준의
    통일 어휘 14종으로 교정했고, 이후 릴리스는 정본 밖 값을 거부한다.
    여러 색상에 자동으로 스타일링 세트를 붙일 때는 장소 종류가 겹치지 않는
    세트를 먼저 쓰며 호리존은 이 중복 방지에서 제외한다. 위 최초 sealed
    staging은 업로드 바이트 감사 기록으로만 보존하고, 교정 전 메타데이터를
    다시 적용하는 용도로 재사용하지 않는다.
  - **서비스용 자산 현재 상태(2026-07-20, ADR-0009)** — 해시가 묶인 최종 QC에서 승인된 예시 192개를 발행했다. 원본 variant는 `all` 192·`pose` 12·`bg` 14이고, `all`에서 파생한 WebP thumb 192개를 더해 R2 객체는 410개다. 보류·탈락한 `all` 15개와 미승인 보조 variant는 발행하지 않았다. 나머지 pose·bg 확대와 보류 이미지 재생성은 후속이다.
  - **사용자 제외 반영 후 활성 상태(2026-07-28)** — 최종 제외 16개 ID는 활성 선택 원본·서비스 자산·프론트 카탈로그·서버 레지스트리에서 모두 제거했다. 활성 선택은 **191개(착용 169·제품 22)**, 새 manifest 기대 작업 수는 **529개(`all` 191 + `pose` 169 + `bg` 169)**, 프론트·서버 활성 레지스트리는 **176개**다. 제외 tombstone은 재유입 방지용 비활성 차단 기록으로만 남긴다. 과거 선택 207개와 불변 R2 발행 192개는 삭제·덮어쓰기 없이 역사로 보존한다.
  - **아우터·원피스 개별컷 보강 릴리스(2026-07-31)** — 사용자 검토 52개 중 승인 43·수정 후 승인 2·제외 7로 확정했다. 최종 승인 45개(여성 아우터 13·남성 아우터 16·여성 원피스 16)를 기존 활성 176개와 함께 새 불변 릴리스 `2026-07-31-individual-styling-01`로 발행했다. 현재 프론트·서버 개별 생성예시는 **221개(착용 199·제품 22)**이고, R2 객체는 `all` 221·`pose` 12·`bg` 14·`thumb` 221로 총 **468개**다. 원격 key 468개·누락 0·추가 0·파일 크기 불일치 0을 확인했으며, 여성/남성 아우터와 여성 원피스의 스타일링 풀·중간 6조합도 공개했다.
  - **ZARA 호리존 시퀀스 최종 반영(2026-07-31)** — 사용자 결정대로 D5~D9 5세트·25멤버만 `space-sets-20260731-zara-v1`로 발행했고 D10 세트는 제외했다. D10의 Cut 5만 `ex_horizon_women_dress_medium_front_satin_01` 낱장 예시로 살려 `2026-07-31-zara-horizon-flat-01`에 포함했다. 앞서 발행한 여성 하의 4세트를 포함해 현재 프론트·서버 공간 세트 카탈로그는 **65세트·212멤버**, 개별 생성예시는 **222개**다. R2는 ZARA 세트 75개 객체와 개별 예시 릴리스 470개 객체를 각각 전수 대조해 key 누락·추가·파일 크기 불일치가 모두 0임을 확인했다.
  - **파일럿에서 확인한 확장 규칙** — 의자·탁자·벽에 의존하는 자세는 지지물을 없앨 때 몸의 기울기와 접점이 무너질 수 있어 중립 지지물 계약 전에는 자동 생성 대상에서 제외하거나 별도 검토한다. 주머니 손은 팔 높이·방향을 보존하되 맨 마네킹 골반에 손이 합쳐질 수 있어 검토 표시한다. `pose`·`bg` 전용 자산이 없으면 `all`로 대신하지 않는다.
  - **운영 실자산 연결 기록(2026-07-20)** — 당시 R2 410개 키·파일 크기를 업로드 후 전수 대조했고, `server/app/data/example_assets.json`과 `src/data/genExamples.json`을 같은 릴리스로 교체했다. 공개 원본 218개와 thumb 192개 모두 HTTP 200 및 이미지 형식을 확인했다. 현재 활성 레지스트리 수는 위 2026-07-28 상태를 따른다.
  - **다른 세션 작업 경계** — 이미지 생성·재생성·시각 QC와 활성 v2 착용 예시의 `pose`·`bg` 확대는 이 작업을 이어 온 Codex 세션이 주로 담당한다. 다른 Claude 세션의 `codex:review`는 ADR-0009를 읽고 설계·계약·코드 검토에 집중하며, 명시적 인계 없이 로컬 이미지·receipt·pilot 파일 수정, 유료 이미지 호출, 업로드·레지스트리 배선을 하지 않는다.
  - **다음 검증 순서** — 광범위한 상세페이지 시장 조사는 더 늘리지 않는다. 아우터·원피스 개별 `all` 보강은 완료했으며, 다음은 미발행 `pose`·`bg` 확대와 상의·하의·아우터·원피스 각 10상품 실제 생성 검사다. 이 검사에서 전체 옷·밑단 잘림, 디테일 근거 충실도, 아우터 열림 정도와 자동 이너 배정의 컷 간 고정을 확인한다.
  - **공간 세트·연출 충실도 후속 게이트(2026-07-29)** — 같은 공간 연속성만으로 생성예시를 승인하지 않는다. 촬영 등급·색감, 원본의 얼굴 노출 상태, 포즈 비대칭·카메라 원근, 배경과 인물에 함께 적용된 광원·그림자, 장소를 변주한 뒤에도 원본을 참고했다고 느껴지는 연출 유사성을 모두 하드 게이트로 검사한다(ADR-0009 §1.3). 기존 개별 예시는 `displayReady`와 `referenceReady`를 나눠, 모델·착장이 달라도 공간·카메라 흐름이 좋은 조합은 새 세트의 제작 레시피로 재사용한다(`storyboard_space_set_ui_spec.md` §6.1).
  - **공간 세트 선택 확정(2026-07-30)** — 실제 공간세트 65개는 승인 56·제외 9·미확정 0으로 사용자 판단을 마쳤다. 호리존 단독 예시 10개도 승인 5·제외 5·미확정 0이다. 제외 세트의 활성 후보와 채택 원본은 제거하며, 과거 검수 기록은 감사 이력으로만 보존한다. 로컬 최종 원장은 `reference/genexamples/space_set_production/2026-07-30/FINAL_SPACE_SET_DECISIONS.json`이다. 이는 **선택 확정**이며 아직 세트 릴리스 manifest 생성·서비스 레지스트리 교체·R2 업로드를 뜻하지 않는다.
- 🆕 **교차샷·크롭 미리보기 걷어내기(2026-07-30 오너 결정)** — 진입 배치 스펙에서 **샷은 역할 고정**(`storyboard_entry_composition_spec.md` §1: "컷 종류·샷·방향은 역할 고정, 랜덤은 어떤 예시·세트가 붙는가에만")이므로 셀러가 예시 선택 후 샷을 바꿔 예시-샷을 불일치시키는 경로가 설계상 사라진다 → `cropFromFull`·`extendFromMedium`은 상시 false인 **죽은 코드**. 걷어낼 대상: `Storyboard.jsx` 483-487·544·546-547·558-560, `features.css` 1560·1563-1572(`.sb-crop-frame`·`.sb-crop-dim`·`.crop-top`·`.crop-bottom`·`.is-extend`·`.sb-extend-zone`·`.sb-extend-note`), 그리고 feat/orb-motion-fx 쪽 `isWornCrossShotPair`·`allowCrossShot`·관련 테스트 9개·프롬프트 `${crossShotLine}`. **순서: main 병합 후**(두 구현이 서로 다른 코드라 병합 전에 지우면 main 버전이 되살아난다).
  - **함께 없애지 말 것**: `pose_crop` 하의 크롭 분기(허리~발, 오너 2026-07-30 결정). 발동 범위는 **세트가 아닌 개별 컷**이다 — 새 main이 `crop_pose_medium` 조건에 `and not spec.get("spaceGroupId")`를 추가해(`cut_generator.py` 863-867) **세트 멤버는 크롭 경로에서 제외**했고, spec §2도 "세트의 미디움샷은 풀샷의 확대·크롭 파생이 아니다"로 명문화했다. 남는 발동 조건 = 개별 미디움샷 컷에서 셀러가 `refScope='pose'`를 고를 때. 빈도는 낮지만 그때 하의는 main 구현으로 머리~허벅지가 잘려 상품이 안 보인다(main `landmark_crop_box`에 카테고리 인자 없음).
- ~~콘티 시드에 clothingType 배관~~ **철회(2026-07-30)** — `storyboard_entry_composition_spec.md` §0이 "시드 빌더(defaultStoryboard/buildStoryboard)는 수정하지 않는다"로 확정하고, §3이 **하의 버퍼·대체 낱장 = 뒷면 풀샷 우선**을 진입 배정 단계에서 처리한다(그 단계는 의류 종류를 안다). 빌더 배관은 불필요.
  - 표본 주의(유지): `cut_sequence_market_research.md`:38의 하의 시퀀스는 드로우핏(스튜디오 카탈로그형) 단일 브랜드 신호가 크다. **시퀀스 전체를 "하의 표준"으로 박제하지 않는다** — 견고한 부분은 "하반신 크롭 + 뒷면 강조"까지다.
- ✅ **contentRole 사용자 노출 제거 + 컷 종류 직접 선택** — 역할 칩과 역할명을 제거하고 카드는 `이미지 N`으로 표시한다. `contentRole`은 레시피·카피·게이트·정렬용 내부값으로 유지한다. 2026-07-21 결정으로 인스펙터에서 핵심 장점은 스타일링·호리존, 핏·코디는 스타일링·호리존·거울 중 컷 종류를 직접 고르고, 제품 확인은 product 고정 아래 고스트·디테일 샷을 고른다. 핵심 장점은 순서(첫 AI=`hero`), 핏·코디는 컷, 제품 확인은 샷으로 내부 역할을 자동 파생한다. FIT 혼합 갤러리는 폐지하고 현재 컷 종류별 최대 6장만 보여준다. 이동·복제·삭제·컷·샷 변경·되돌리기와 서버 저장에서도 다시 정규화한다(ADR-0005 후속 결정).
- ✅ **착용 샷 범위 정리** — 2026-07-18 오너 결정으로 서비스 착용컷은 `full | medium` 두 단계로 통합했다. 중간샷 저장값은 `medium`을 재사용하고 선별판 전용 `medium_knee`는 서비스에 도입하지 않는다(ADR-0007).
- 🔶 **실서버 생성 단계 체크리스트** — mock은 의미가 바뀐 화면 라벨을 보여주지만 HTTP 어댑터는 아직 `onProgress`만 전달한다. 서버 phase를 핵심 장점·핏·코디·제품 확인 중심의 표시 단계로 매핑해 `onStep`을 실배선한다. 내부 cutType key를 사용자 섹션 이름으로 재사용하지 않는다. **→ 생성 대기 UI 실배선**
- 🔶 **PL-4~6 OpenAPI·프론트 JSDoc 갱신** — `docs/openapi.json`에 콘티 저장·상세페이지 생성·에디터 이미지 생성 라우트와 taxonomy v2 요청 shape가 빠져 있고, `src/lib/types.js`의 NewCutRequest 주석도 새 필드 일부를 반영하지 못했다. 런타임 계약에서 다시 생성해 맞춘다. **→ API 문서 정리**
- 🔶 **비정상 콘티 호환 규칙 정렬** — 정상 UI 저장은 프론트·서버가 같은 v2 결과를 만들지만, `sectionRole`과 `contentRole`이 서로 충돌하는 외부/손상 데이터에서는 프론트와 서버의 우선순위가 다르다. 한쪽 규칙으로 통일하고 회귀 테스트를 둔다. **→ 호환성 정리**

### 신규 (2026-06-14 결정)

- 🆕 **`matchClothing` → `matchCandidates`(+`matchSelections`) 코드 리네임** — 설계 문서는 이미 계약 이름으로 통일됨. 코드(`src/lib/types.js`, `src/mock/*`, `AnalysisForm.jsx`, `Mannequin.jsx`, `Storyboard.jsx`)는 아직 `matchClothing`+`.selected`/`.selOrder` 사용. 계약 §3.2 shape로 이관. **→ Phase 3** (분석 어댑터가 계약 shape로 응답하면 프론트가 안 깨지려면 동시 필수)
- 🆕 **plan 토큰 `basic`/`plus`/`seller`** — 계약 §3.7/§4는 소문자 토큰 + 라벨(Basic/Plus/Seller)로 갱신됨. 코드 동기화 필요: `src/lib/types.js`(`@property {'Free'|'Pro'|'Team'} plan`), `src/mock/db.js`(`account.plan: 'Pro'`), `shell.jsx` 표시(토큰→라벨). **→ Phase 1** (`/me`가 plan 반환)
- 🆕 **세탁 안내 = 에디터 자동 블록(규칙 기반)** — `Analysis.washCare` 필드와 `draftWashCare` API 제거. 분석 화면엔 이미 미렌더(`AnalysisForm.jsx`의 `draftWash`는 죽은 코드). 세탁 안내 내용은 **의류 종류별 대표 소재 프리셋 + 애매하면 기본 세탁방침**으로 M-02(page-assembler)가 생성(AI 카피 아님). 코드 정리: `draftWashCare`(mock api), `washCare`(types/Analysis), `draftWash`(AnalysisForm) 제거. **→ Phase 3** (washCare/draftWashCare 제거) · 자동블록 생성은 **Phase 4~6**
- 🆕 **`getMatchClothing` 최종 제거(과도기 함수)** — 현재 마네킹·콘티 화면이 실제 호출(`Mannequin.jsx:234`, `Storyboard.jsx:328`)하므로 지금 제거 불가. 매칭 후보가 `analyzeProduct` 응답(`analysis.matchCandidates`)에 완전히 포함되어 두 화면이 거기서 읽게 되면 제거. **→ Phase 3**
- 🆕 **`patchProject` 서버 화이트리스트** — 실서버 구현 시 `patchProject`는 `composeMode`·`copywriting`·`selectedMannequinId`만 수용. `adjustCount`·`status`는 서버 전용(요청 페이로드에 오면 무시/거부). mock의 무검증 `Object.assign`(`src/mock/api.js:65`)을 서버는 베끼지 않는다. **→ Phase 1** (projects CRUD)
- 🆕 **필수 칩 해제 불가 + `*` 표시** — 분석 기본 정보에서 `의류 종류`·`핏`은 해제 불가(데이터상 null 불가). 라벨에 포인트 색(`--ring`/Sky) `*` 표기. 해제 가능 칩(세부 카테고리·성별)과 구분. (`AnalysisForm.jsx`) **→ 독립 (순수 UI)**
- 🆕 **마네킹 성별 베이스 + 의류 스왑 구현** — 스파이크 결과 반영. 성별(`targetGenders`)이 베이스 마네킹(남/여 고정 1장)을 결정, A/B 후보 둘 다 같은 성별 베이스 위 스왑(독립 생성 아님). 베이스 자산은 운영자 시드. (`spike/base/*` 참고, AG-04) **→ Phase 4** ✅ **AG-04 백엔드 LIVE** (`server/app/workers/mannequin_job.py` — 동일 성별 베이스·대비 핏 A/B 구현 완료, 2026-06-29 → **이후 단일컷 + fitProfile 재생성으로 전환**, `fit_profile_spec.md`). 프론트 실서버 연동은 별도.
- 🆕 **마네킹 최초 생성 크레딧 예고** — 마네킹 페이지 진입 시 자동 생성·차감되므로, 분석 CTA 버튼 `의류정보 확정 완료`에 예상 크레딧을 부착(`의류정보 확정 완료 · 2 크레딧`). (`AnalysisForm.jsx:273`) **→ Phase 4**
- ✅ **스키마: `jobs.status` `cancelled` 제거 + `profiles.plan` basic+CHECK + `jobs.kind` CHECK** — init 마이그레이션(`supabase/migrations/20260612090000_init.sql`)에 in-place 반영(계약 일치). 코드 경로 중 `status='cancelled'` 설정 없음(검증됨). ✅ **2026-06-29 기준 9개 마이그레이션 적용 완료 — 해당 전제 해소됨.**

### 신규 (2026-06-16 — 소셜 로그인 게이트 · 입력/분석 공개)

> 입력·분석을 로그인 없이 공개하고, 분석 CTA `의류정보 확정 완료`에서 소셜 로그인(구글·카카오) 모달 게이트를 띄운다. 마네킹부터 로그인 필요. (`App.jsx` RequireAuth/PostLoginRedirect · `AuthProvider`/`Login.jsx` LoginGate · `ProductInput.jsx` CTA 게이트 · `shell.jsx` TopNav 프로필)

- 🔶 **로그인 후 입력·분석 보존 — 로컬 복원 구현됨 / 백엔드 sync 보류(Option B)** — 분석 CTA 직전 입력+분석+사진을 IndexedDB 에 저장(`draftStore.saveProductDraft(product, analysis)`), 로그인 복귀·브라우저 뒤로가기(카카오→←뒤로)·취소·새로고침 시 ProductInput 이 **복원**한다(사진 blob→objectURL 재생성, `hasPendingDraft` **세션-스코프(sessionStorage)** 게이팅으로 공용 브라우저 타 사용자 누출 차단, 새 제작·로그아웃 시 정리). **단 로그인 성공 시 마네킹으로 즉시 이동하고 백엔드 sync 는 안 한다(Option B).** 이유(당시 기준): ① 원격(당시 Railway) **R2 미설정**으로 `/v1/assets/upload-url` 503 실패, ② 업로드가 사진당 3콜 **순차**라 ~10초, ③ 마네킹 등 하위가 아직 **mock** 이라 sync 해도 결과 무변(즉 지금 sync 는 비용만 큼). **→ 실사용(실서버가 사진을 실제로 쓰는) 단계에 아래 Option A 적용:**
  - ✅ **(A-1) 원격에 R2 env 설정 — 해소됨 (AWS 이전과 함께)** — 현 프로덕션(AWS Copilot)은 `copilot/api/manifest.yml` `variables`(R2_ACCOUNT_ID·R2_BUCKET·R2_ENDPOINT·R2_PUBLIC_BASE) + SSM secrets(R2_ACCESS_KEY_ID·R2_SECRET_ACCESS_KEY)로 설정 완료. (미설정 시 `app.state.r2=None`→503 — `server/app/main.py`·`config.py`·`routes.py` `_r2()`)
  - **(A-2) `src/lib/draftSync.js` 업로드 병렬화** — `for…await uploadPhoto`(line 56-58) → `Promise.all`. ~10초 → 가장 느린 1장(~2-3초).
  - **(A-3) 로그인 복귀 sync 재활성** — `src/App.jsx` RootRedirect 가 즉시 이동(현재) 대신 `syncDraftToBackend(draft)`→`setProjectId`(store에 유지됨)→마네킹. 로딩은 마네킹 생성 로딩과 **겹쳐 백그라운드** 처리해 별도 대기창 없게.
  - **(A-4) sync 에 분석 결과 포함** — 현재 `syncDraftToBackend` 는 product+photos 만 올림. `draft.analysis` 도 백엔드에 저장하도록 추가(`saveAnalysis` 경로).
  - **(A-5) 재활성 시 재검증** — 부분 실패(프로젝트 생성 후 업로드 실패) 시 빈 프로젝트 중복 생성 방지(`draftSync.js` 주석의 MVP 한계). 멱등/정리 보장.
  - **(A-6) sync/생성 전 필수 입력 검증** — 현재 '정면 사진 필수' 가드는 *입력 복원 경로*(ProductInput)에만 적용되고, **로그인 성공→마네킹 직행 경로는 draft 를 안 쓰므로 우회**된다(지금은 sync off·마네킹 mock 이라 무해). sync 재활성 시 **sync/생성 직전에 정면 등 필수값을 검증**해 frontless draft 가 실 생성에 들어가지 않게. (Codex: "guard bypassed on successful login")
- 🆕 **프로필 메뉴 목적지 페이지** — TopNav 프로필 드롭다운(`shell.jsx` ProfileMenu)은 헤더(아바타·이름·이메일) + `크레딧 관리`(현재 "준비 중" 토스트) + 로그아웃(동작)만. 결제/요금제 백엔드 확정 시 실제 페이지 + 필요 시 `요금제·결제` 항목 추가. 정책 숫자는 §2(크레딧 단가·플랜) 대기. **→ 결제 백엔드 단계**
- 🆕 **소셜 로그인 OAuth 왕복 라이브 검증 + localhost Redirect URL** — 게이트 흐름(구글/카카오 → 복귀 → `/create/mannequin` 직행; `sessionStorage 'wl_postLogin'` + `App.PostLoginRedirect`)은 빌드/코드 검증만 됨. ⚠️ **`http://localhost:5173` 이 Supabase Auth → URL Configuration → Redirect URLs(+ Site URL)에 없으면 복귀 시 세션이 안 생겨 프로필 미표시·입력 페이지로 복귀**한다(2026-06-16 로컬 테스트에서 재현). 배포 도메인 외 localhost 도 allowlist 필요. 카카오 인앱 브라우저/모바일 포함 1회 수동 QA. **→ 독립 (환경 설정 · 수동 QA)**

---

## 2. 정책 미확정 (오픈 이슈)

> 구조는 준비됐고 정책 숫자/기준만 대기. 상세는 `backend_integration_plan.md` §11.

- 크레딧 단가·플랜·무료 체험·환불 — PRD §12.2 (00_README §4 Blocking).
- 품질 불만 환불 기준 — AG-P2(image-qc)와 연결.
- 자산 보존 기한·비용 상한 — 운영 정책.
- export 해상도·마켓 프리셋 — backend plan §7 P2 트리거.
- 분위기 예시 시드 입력 — 운영자 데이터(사용자: 직접 입력 예정).
- 🚧 **회원탈퇴 ↔ 크레딧 원장 충돌 (출시 게이트)** — `credit_ledger`는 append-only + `user_id → auth.users(id)`(on delete cascade 아님)이라, **원장 행이 있는 유저는 `auth.users` 삭제가 FK에 막혀 탈퇴 불가**. 출시 전 정책 결정 필요: 탈퇴 시 원장 `user_id` **익명화**(감사 보존) vs 별도 보존 처리. project soft delete(`deleted_at`)는 이미 있음. (2026-06-20 advisor 점검 시 재확인)
- 🚧 **출시 전 하드닝 묶음** — 로그 보존·자동삭제(cron) · 회원탈퇴 플로우 · 보안 감사 · FK 커버링 인덱스(2026-06-20 advisor: 전부 INFO·데이터 0이라 규모 시 일괄)는 **PG·크레딧 단가와 함께 출시 직전 단계**에서. (개인정보 암호화는 Supabase at-rest+TLS로 커버 + 민감PII 미저장 → 별도 불필요. 보안 advisor: leaked-password WARN은 소셜 로그인 전용이라 무관, RLS 누수 0.)
- 🚧 **입력 이미지 의류 검증 (출시 전, 사용자 요청 2026-06-28)** — 의류 이미지 업로드 칸에 비-의류(예: 자동차) 사진이 올라오면 마네킹 생성이 이상하게 나옴. 업로드/생성 전에 **올린 이미지가 실제 의류인지 vision으로 사전 검증** → 아니면 거부/경고. (의류 *종류*는 pill 선택이라 텍스트 인젝션은 무관 — 이건 *이미지 내용* 검증 문제. 현 QC는 결과물만 보고 입력은 안 봄.)

---

## 3. 실행 로드맵

> 단계별 전환 순서·완료 기준은 `backend_integration_plan.md` §10(실행 로드맵)·§8(전환 단계)이 정본. 여기서 중복하지 않는다.
