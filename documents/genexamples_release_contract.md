# 생성예시 릴리스 계약 (release manifest v1) — 확정

> 상태: **확정** (2026-07-19) — 콘티보드 세션 초안에 이미지 생성 세션 답변 3건(산출 주체·R2 경로·썸네일) 반영, 오너 승인 완료.
> 정본 결정: ADR-0006(적용 의류 종류) · ADR-0008(제품 ghost|detail, all만) · ADR-0009(refScope 전용 자산·폴백 금지·단계 공개).
> 역할: 생성 세션의 로컬 산출물(`reference/genexamples/service_examples/v1/**`, gitignore)을 서비스로 넘기는 **단일 인수인계 파일**의 형식. 이 파일 하나로 서버 레지스트리와 프론트 카탈로그를 함께 생성한다(ADR-0009 §영향).

## 1. 파일: `release_manifest.json`

**생성 세션이 QC 확정 후 자동 내보내기 도구로 생성**하고, 릴리스 도구는 출시 대상을 추론하지 않고 manifest와 실파일을 **검증·소비만** 한다(합의 ①).

```jsonc
{
  "schemaVersion": 1,
  "releaseId": "2026-07-19-pilot-01",                     // 릴리스 단위 식별자 — 경로에 포함, 같은 releaseId 재업로드(덮어쓰기) 금지 (합의 ②)
  "releasedAt": "2026-07-20T00:00:00Z",
  // R2 키 규약(합의 ②): seed/genexamples/v1/releases/<releaseId>/<variant>/<exampleId>.<ext>
  // variant 디렉터리 = all | pose | bg | thumb. 공개 URL = <R2 공개 도메인> + 키.
  "source": {                                              // 재현 근거 (감사용)
    "anchors": "service_examples/v1/anchors.json",
    "qcCompletion": ["qc/all_only_completion_2026-07-19.json", "qc/pilot_10pct_completion_2026-07-19.json"]
  },
  "examples": [
    {
      "id": "ex_styling_women_top_full_resort_01",         // 안정 ID — 영구 불변 (블록이 exampleId로 저장)
      "serviceGroupKey": "styling:women:top:full:resort",   // cutType:gender:clothingType:shot[:mood]
      "rank": 1,                                            // 그룹 내 노출 순서 (1..6, 그룹 내 유일)
      "cutType": "styling", "gender": "women", "shot": "full", // 착용 women|men, 제품 null
      "mood": "resort",                                     // 스타일링만, 그 외 null
      "detailSubject": null,                                 // 제품 detail만 (원단·봉제|단추·지퍼|포켓)
      "presentationMethod": null,                            // 제품 ghost만 (ghost|flatlay 표현 증거)
      "direction": "front",                                 // 관찰 메타: front|side|back|null, front로 강제 변환 금지
      "sourceClothingType": "top",
      "applicableClothingTypes": ["top"],                   // §5 규칙: 비어있지 않음·중복 없음·source 포함
      "variants": {                                          // ★ 실제 발행(QC publishable)된 것만 — 미발행 범위는 키 없음
        "all":  { "file": "assets/all/ex_styling_women_top_full_resort_01.png",  "sha256": "…", "width": 1024, "height": 1536 },
        "pose": { "file": "assets/pose/ex_styling_women_top_full_resort_01.png", "sha256": "…", "width": 1024, "height": 1536 },
        "bg":   { "file": "assets/bg/ex_styling_women_top_full_resort_01.png",   "sha256": "…", "width": 1024, "height": 1536 }
      }
      // thumb는 manifest에 없다 — 릴리스 도구가 all에서 WebP로 결정적 파생 생성(§3, 합의 ③)
    }
  ]
}
```

## 2. 불변식 (릴리스 도구가 검증, 위반 시 릴리스 거부)

0. 릴리스는 불변이다: 같은 `releaseId` 경로에 재업로드(덮어쓰기)하지 않는다. 수정은 새 releaseId로 새 릴리스를 만든다. 업로드는 기본 dry-run이며 사용자 승인 후에만 실행한다(합의 ②).
1. `id`는 영구 불변·전역 유일. R2 키는 `seed/genexamples/v1/releases/<releaseId>/<variant>/<id>.<ext>`.
2. `rank`는 serviceGroupKey 내 1부터 연속·유일, 그룹당 최대 6개(현행 노출 한도 — 확대 시 이 계약만 갱신).
3. `variants`에는 **QC publishable 판정을 통과해 실제 존재하는 자산만** 기재한다. manifest에 적힌 미래 경로는 자산이 아니다(ADR-0009 §2). `all`은 모든 예시에 필수.
4. `pose`·`bg`는 착용컷(styling|horizon|mirror)에만 허용. 제품(ghost|detail)은 `all`만(ADR-0008).
5. `applicableClothingTypes`는 비어있지 않고 중복 없이 `sourceClothingType`을 포함한다. 스타일링 공용은 사람 검토를 거친 풀샷 `[top,outer]`만 허용한다. 일반 호리존 풀샷은 여성 `top|bottom|outer|dress`, 남성 `top|bottom|outer`에 공용 적용하며, 호리존 중간샷은 상단 크롭 계열(여성 `top|outer|dress`, 남성 `top|outer`)끼리만 공유한다. 하의 중간샷은 `bottom` 전용이다(ADR-0006).
6. `shot`은 서비스 정본(full|medium, 제품은 ghost|detail 체계)만. 선별판 전용 토큰(medium_knee)은 등장하지 않는다.
   착용컷 `gender`는 women|men, 성별 공용 제품컷은 null이다. `direction`은 앵커의 관찰 메타를 보존하며 front로 강제 변환하지 않는다.
7. 자산 픽셀 규칙: `pose`=옷·배경 없는 투명 PNG 중립 마네킹(빈 휴대폰 소품만 허용), `bg`=사람·의류·소품·접촉 그림자 없는 빈 장소 플레이트(ADR-0009 §1·§3).
8. 썸네일(합의 ③): 릴리스 도구가 모든 예시의 `all`에서 **WebP 축소본을 결정적으로 파생 생성**해 `thumb/<id>.webp`로 발행한다(원본 평균 ~9.3MB → 갤러리에 원본 사용 금지). 갤러리 표시는 thumb, 생성 첨부는 원본. `pose`·`bg` 썸네일은 만들지 않는다. 파생은 기계적 변환이므로 합의 ①(도구는 추론 금지)과 상충하지 않는다.
9. 필드 규칙(2026-07-20 명문화, 동일자 정정): `gender`는 착용컷(styling|horizon|mirror) → `women|men` 필수, **product → null**(모델 없음). `direction`은 **관찰 메타**다 — 컷 종류와 무관하게 `front|back|side|null` 전부 허용하며, 서비스 레시피 입력이 아니고 front로 강제 변환하지 않는다(§1 주석·불변식 6과 동일). 거울 예시의 front(거울을 향한 관찰값), 디테일 접사의 null이 정상 사례다. 서비스의 방향 규칙(거울=방향 없음 등)은 카드 레시피의 것이지 예시 메타의 것이 아니다. validator는 이 규칙 밖의 값만 거부한다.
10. 공개 범위의 단일 정본은 `data/genexamples_public_combinations.json`이다. 릴리스 도구는 이 표에 선언된 `cutType×shot×clothingType×gender` 조합에 `all` 예시가 0장이면 릴리스를 거부하고, 미선언 발행 조합은 경고만 남긴다. 방향은 커버리지 축이 아니다. 이 파일은 CI 커버리지와 프론트 컷·샷 비활성화 로직도 직접 읽으며 복제본을 두지 않는다.
11. v2 품질 계보(2026-07-22): 착용 `all`은 하우스 모델 얼굴·전신 시트·연출 앵커의 역할별 입력 해시와 실제 생성 프롬프트 해시를 보존한다. QC는 하우스 모델 얼굴 일치, 현실적인 탈브랜딩 후 물건 구조 보존, 컷별 장소 정책, 호리존 의상 단정함, 앵커 광원과 얼굴·옷·환경·그림자의 물리적 일관성을 각각 확인한다. `pose`·`bg`는 같은 `all` 해시의 all-only 승인 뒤에만 파생할 수 있으며, `all`이 바뀌면 이전 파생 자산과 QC 판정은 발행할 수 없다(ADR-0009 §1.2).
12. 연출 앵커 충실도(2026-07-29): 착용 `all`은 ADR-0009 §1.3의 ①촬영 등급·색감 ②얼굴 노출 상태 ③포즈 비대칭·카메라 원근 ④인물까지 이어지는 광원·그림자 ⑤장소를 변주한 뒤에도 알아볼 수 있는 연출 유사성을 각각 별도 하드 게이트로 통과해야 한다. 이 다섯 값이 모두 `true`인 새 QC 계약 버전의 결과만 발행할 수 있다. `source_all_not_copy=true`는 복사 방지일 뿐 이 다섯 게이트를 대신하지 않는다.

### 발행 후 적용 범위 개정

이미지 바이트·ID·R2 key·해시·variant는 불변이다. 반면
`applicableClothingTypes`는 현재 서비스가 어떤 상품에 해당 자산을 보여주고
허용할지 정하는 **소비 라우팅 메타데이터**다. 오너가 적용 범위 확대를
확정한 경우에는 기존 범위를 포함하는 단조 확대만 코드 메타데이터 개정으로
허용하며 R2 자산을 다시 올리지 않는다. 이때 프론트
`src/data/genExamples.json`, 서버 `server/app/data/example_assets.json`,
공개 조합표를 한 변경에서 함께 갱신하고, 두 소비자가 같은 범위를
허용하는지 테스트한다. 과거 `release_manifest.json`은 당시 발행 감사 기록으로
다시 쓰지 않는다. 범위 축소나 이미지·레시피 변경은 이 예외에 해당하지
않으며 새 ID·새 릴리스가 필요하다. 기존 릴리스 CLI는 이 코드 메타데이터
개정 경로가 아니므로 같은 releaseId 덮어쓰기 금지 규칙을 그대로 유지한다.

## 3. 소비 (1단계 구현 대상 — 릴리스 도구 하나가 두 산출물 생성)

- **서버 레지스트리** `server/app/data/example_assets.json` v2: `assets[id] = { all, pose?, bg?, applicableClothingTypes, cutType, shot, gender, direction }` (URL = baseUrl+key). 기존 `load_example_asset_registry()` 스키마의 상위 호환 확장 — 서버는 §5 규칙(적용 의류 검증, 미발행 범위 첨부 생략, spaceGroup은 pose 강제)을 정본으로 검증하고, pose 범위는 예시의 관찰 `direction`과 카드 레시피 방향의 사전 호환 게이트를 통과해야 한다.
- **프론트 카탈로그** `catalogs.genExamples`: `{ id, thumb, cutType, gender, direction, clothingType(=source), applicableClothingTypes, shot, mood, rank, variants: ["all","pose","bg"] }` — 제품의 `gender=null`은 성별 공용으로 필터링하고, 갤러리는 현재 상품·조건으로 필터링, rank 순 최대 6장, 범위 버튼은 발행 variant와 pose 방향 호환 여부에 따라 활성화한다.
- **공개 조합표** `data/genexamples_public_combinations.json`: 오너가 직접 확장하는 서비스 공개 범위. 릴리스 커버리지, CI, 프론트 옵션 게이트가 같은 파일을 소비한다.
- 파일럿 범위: **pose·bg는 발행된 항목에만 variants가 존재** → UI는 해당 항목에서만 포즈만·배경만 버튼을 활성화한다. `variants`는 항목별 사용 가능 여부이고, 전역 공개·긴급 롤백은 별도 운영 게이트가 담당한다. 2026-07-21 production 공개부터 프론트 `VITE_GENEXAMPLE_BG_ENABLED`와 서버 `GENEXAMPLE_BG_ENABLED`를 함께 사용하며, 이는 ADR-0009가 과거의 “별도 플래그 불필요” 결정을 대체한 것이다.

## 4. 확인된 현황 (0단계 조사, 2026-07-19)

- `anchors.json` 207개(착용 185+제품 22)가 위 필드를 사실상 전부 보유(id·rank·계보·적용성) — **스키마는 발명이 아니라 정리**다. 59개 그룹 전부 6장 이하·rank 1..6 부여 완료 → 6장 초과 선별 문제는 이미 해소됨.
- 서버에 레지스트리 로더·범위 해석(`load_example_asset_registry`/`resolve_example_asset`, spaceGroup→pose 강제)이 이미 있고 variant dict(`{all,pose,bg}`)도 이해한다 → v2는 메타 필드 추가 수준.
- 프론트는 placeholder 6칸 + `catalogs.genExamples` 자리만 있는 상태(실소비 없음).
- QC 산출물(`qc/*completion*.json`)에 releaseState 게이트(uploaded/productionRegistryUpdated/frontendCatalogUpdated 전부 false)가 이미 있어 릴리스 전 상태가 명시돼 있다.

## 5. 확정 이력

초안의 미확정 3건은 2026-07-19 생성 세션 답변으로 확정됐다(오너 승인 동일자).

1. manifest는 **생성 세션의 자동 내보내기 도구**가 QC 확정 후 생성. 릴리스 도구는 검증·소비만(추론 금지) → §1.
2. R2 키 `seed/genexamples/v1/releases/<releaseId>/<variant>/<id>.<ext>`, 업로드는 릴리스 도구 담당·기본 dry-run·사용자 승인 후 실행·같은 릴리스 경로 덮어쓰기 금지 → §2 불변식 0.
3. `all` 기반 WebP 썸네일을 별도 발행(원본 평균 ~9.3MB — 갤러리에 무거움), pose·bg 썸네일 없음 → §2 불변식 8. 썸네일 파생 주체는 릴리스 도구로 정리(기계적 변환) — 생성 세션이 직접 발행을 원하면 manifest에 thumb variant를 포함하는 것으로 대체 가능(도구는 있으면 검증, 없으면 파생).

## 6. 운영 릴리스 이력

### 2026-07-31 아우터·원피스 개별 스타일링 보강

- 릴리스 ID: `2026-07-31-individual-styling-01`
- 사용자 검토 52개: 즉시 승인 43·수정 후 승인 2·제외 7. 최종 승인 45개만 신규 발행했다.
- 신규 라벨 범위: 여성 아우터 13(풀 8·중간 5), 남성 아우터 16(풀 8·중간 8), 여성 원피스 16(풀 9·중간 7). 모든 항목은 안정 ID와 `cutType`·`gender`·`sourceClothingType`·`applicableClothingTypes`·`shot`·`direction`·`mood`·`rank`·실발행 `variants`를 함께 가진다.
- 기존 활성 176개와 신규 45개를 하나의 manifest로 봉인해 프론트·서버 카탈로그를 동시에 221개로 교체했다. 과거 릴리스는 삭제·덮어쓰기하지 않는다.
- R2 객체는 `all` 221·`pose` 12·`bg` 14·파생 `thumb` 221, 총 468개다. 업로드 후 원격 key·파일 크기 전수 대조에서 누락 0·추가 0·불일치 0을 확인했다.
- 여성/남성 아우터와 여성 원피스의 스타일링 풀·중간 6조합을 `data/genexamples_public_combinations.json`에 함께 공개했다.

### 2026-07-31 ZARA 호리존 낱장 추가

- 릴리스 ID: `2026-07-31-zara-horizon-flat-01`
- 제외한 D10 공간 세트의 Cut 5만 `ex_horizon_women_dress_medium_front_satin_01`로 독립 발행했다. 분류는 여성·원피스 원본·호리존·미디움·정면이며 적용 범위는 `top|outer|dress`다.
- 기존 활성 221개와 신규 1개를 하나의 manifest로 봉인해 프론트·서버 카탈로그를 **222개**로 함께 교체했다.
- R2 객체는 `all` 222·`pose` 12·`bg` 14·파생 `thumb` 222, 총 **470개**다. 업로드 후 원격 key와 파일 크기를 전수 대조해 누락·추가·불일치가 모두 0임을 확인했다.

### 2026-08-01 구세대(파일럿 계열) 소비 제외 — 재적용 v2

- 오너 전수 검토 판정(7/31)의 재적용: 최초 적용(#74)이 ZARA 릴리스 등 main 이동으로 낡아, 최신 카탈로그 위에 같은 판정을 다시 실행했다. 유지 2(`ex_horizon_women_bottom_medium_01`·`03`) + 제품컷 22 롤백 유지, 재생성 앵커 9는 제외(재생성 후 새 ID 복귀), 나머지 구세대 제외.
- 반영: 프론트·서버 카탈로그 222→**70**, 공개 조합표 36→**14**(신작 스타일링 6 + 호리존 미디움 여 4 + 제품 4). R2 실삭제는 7/31에 이미 완료(968객체·잔존0 — 명단 `~/Documents/wearless_studio_r2_deleted_keys_2026-07-31.txt`).
- 소비 영향: 닫힌 착용 조합 갤러리는 세트 멤버(setOnly 우회)로 유지, 해당 조합 낱장 자동 배정은 미배정(fail-closed). 기존 프로젝트의 구세대 썸네일 참조는 프론트 폴백이 처리.

### 2026-08-02 제품 고스트컷 실체 정합 — 명단 70→58

- 7/31 판정의 "제품컷 22 유지"는 판정 시점 기준이었고, 같은 날 R2 실삭제에서 고스트 13장 중 12장이 함께 삭제됐다. 재적용본(70)을 그대로 반영하면 삭제된 12장이 명단에 남아 깨진 썸네일이 유지된다 — 이 릴리스가 없애려던 증상과 같다.
- 근거(실측 2026-08-02): 명단 70개의 `thumb` 전수 HTTP 프로브 결과 **200 58 · 404 12**. 404 12개는 `ex_product_bottom_ghost_01~06`·`ex_product_outer_ghost_01`·`ex_product_top_ghost_02~06`이며, 생존 고스트는 `ex_product_top_ghost_01` 1장이다.
- 반영: 카탈로그 70→**58**, 공개 조합표 14→**13**(`product×ghost×bottom` 제거 — 잔존 0장이라 릴리스 도구의 커버리지 게이트가 거부). `product×ghost×top`은 생존 1장으로 유지.
- 원칙: 명단의 단일 정본은 **R2 실체**다. 판정 문서와 창고가 어긋나면 창고를 따르고, 판정 문서에는 사유를 남긴다.

### 2026-08-03 상의·하의 개별 스타일링 보강

- 서비스 적용 릴리스 ID: `2026-08-03-individual-styling-02`
- 최신 `origin/main` 활성 58개를 기준선으로 보존하고, 사용자가 최종 확정한 신규 45개를 더해 프론트·서버 카탈로그를 **103개**로 함께 교체했다. 신규는 여성 하의 11(풀 5·중간 6), 여성 상의 12(풀 6·중간 6), 남성 하의 11(풀 5·중간 6), 남성 상의 11(풀 5·중간 6)이다.
- 제작자 독립 QC 기록은 PASS 37·HOLD 8이다. HOLD 8개는 사용자의 묶음별 최종 확정 지시를 오너 승인으로 적용했으며, 원래 판정과 실패 사유는 PASS로 바꾸지 않고 로컬 감사 기록에 보존했다.
- `all` 103·파생 `thumb` 103, 총 **206개**를 새 불변 R2 prefix에 업로드했다. 원격 key·파일 크기 전수 대조는 누락 0·추가 0·불일치 0이고, 신규 원본·썸네일 공개 URL 90개도 모두 HTTP 200이다.
- 공개 조합표는 기존 13개를 유지하면서 상의·하의 × 여성·남성 × 풀·중간 스타일링 8개만 추가해 **21개**로 확장했다.
- 선행 `2026-08-03-individual-styling-01`은 최신 정리 전 로컬 기준선을 사용한 사실을 뒤늦게 발견해 서비스 카탈로그에 적용하지 않았다. 이미 업로드된 불변 경로는 역사 기록으로만 남기며 어떤 활성 URL도 이 릴리스를 참조하지 않는다.
