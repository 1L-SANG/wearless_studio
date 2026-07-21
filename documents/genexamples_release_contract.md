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
5. `applicableClothingTypes`는 비어있지 않고 중복 없이 `sourceClothingType`을 포함. 공용([top,outer])은 사람 검토를 거친 스타일링·호리존 풀샷만(ADR-0006).
6. `shot`은 서비스 정본(full|medium, 제품은 ghost|detail 체계)만. 선별판 전용 토큰(medium_knee)은 등장하지 않는다.
   착용컷 `gender`는 women|men, 성별 공용 제품컷은 null이다. `direction`은 앵커의 관찰 메타를 보존하며 front로 강제 변환하지 않는다.
7. 자산 픽셀 규칙: `pose`=옷·배경 없는 투명 PNG 중립 마네킹(빈 휴대폰 소품만 허용), `bg`=사람·의류·소품·접촉 그림자 없는 빈 장소 플레이트(ADR-0009 §1·§3).
8. 썸네일(합의 ③): 릴리스 도구가 모든 예시의 `all`에서 **WebP 축소본을 결정적으로 파생 생성**해 `thumb/<id>.webp`로 발행한다(원본 평균 ~9.3MB → 갤러리에 원본 사용 금지). 갤러리 표시는 thumb, 생성 첨부는 원본. `pose`·`bg` 썸네일은 만들지 않는다. 파생은 기계적 변환이므로 합의 ①(도구는 추론 금지)과 상충하지 않는다.
9. 필드 규칙(2026-07-20 명문화, 동일자 정정): `gender`는 착용컷(styling|horizon|mirror) → `women|men` 필수, **product → null**(모델 없음). `direction`은 **관찰 메타**다 — 컷 종류와 무관하게 `front|back|side|null` 전부 허용하며, 서비스 레시피 입력이 아니고 front로 강제 변환하지 않는다(§1 주석·불변식 6과 동일). 거울 예시의 front(거울을 향한 관찰값), 디테일 접사의 null이 정상 사례다. 서비스의 방향 규칙(거울=방향 없음 등)은 카드 레시피의 것이지 예시 메타의 것이 아니다. validator는 이 규칙 밖의 값만 거부한다.

## 3. 소비 (1단계 구현 대상 — 릴리스 도구 하나가 두 산출물 생성)

- **서버 레지스트리** `server/app/data/example_assets.json` v2: `assets[id] = { all, pose?, bg?, applicableClothingTypes, cutType, shot, gender, direction }` (URL = baseUrl+key). 기존 `load_example_asset_registry()` 스키마의 상위 호환 확장 — 서버는 §5 규칙(적용 의류 검증, 미발행 범위 첨부 생략, spaceGroup은 pose 강제)을 정본으로 검증하고, pose 범위는 예시의 관찰 `direction`과 카드 레시피 방향의 사전 호환 게이트를 통과해야 한다.
- **프론트 카탈로그** `catalogs.genExamples`: `{ id, thumb, cutType, gender, direction, clothingType(=source), applicableClothingTypes, shot, mood, rank, variants: ["all","pose","bg"] }` — 제품의 `gender=null`은 성별 공용으로 필터링하고, 갤러리는 현재 상품·조건으로 필터링, rank 순 최대 6장, 범위 버튼은 발행 variant와 pose 방향 호환 여부에 따라 활성화한다.
- 파일럿 범위: **pose·bg는 파일럿 19개만 variants에 존재** → UI에서 자동으로 그 19개만 포즈만·배경만 활성화(별도 플래그 불필요 — 계약이 곧 스위치).

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
