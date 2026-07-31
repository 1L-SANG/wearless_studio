# 공간 세트 릴리스 계약 (space-set manifest v1)

> 상태: **확정** (2026-07-30)
>
> 관련 정본: ADR-0006(적용 의류 종류), ADR-0009(범위별 전용 자산과 릴리스
> 책임), `documents/storyboard_space_set_ui_spec.md`,
> `documents/genexamples_release_contract.md`.

이 계약은 사람 검토가 끝난 **공간 세트**를 서비스로 넘기는 형식이다. 기존
개별 생성예시 릴리스와 수명주기·소비 방식이 다르므로
`seed/genexamples/v1/**`, `src/data/genExamples.json`,
`server/app/data/example_assets.json`을 재사용하거나 덮어쓰지 않는다.

생성 세션은 최종 승인·제외 결정을 반영한 manifest와 자산을 내보낸다. 릴리스
도구는 승인 대상을 추론하거나 이미지를 선택하지 않고, manifest·QC·실파일을
검증하고 결정적 썸네일을 만든 뒤 두 소비 파일을 조립한다.

## 1. manifest

```jsonc
{
  "schemaVersion": 1,
  "releaseId": "2026-07-30-space-sets-01",
  "releasedAt": "2026-07-30T12:00:00Z",
  "sets": [{
    "setId": "set_style_women_top_cafe_01",
    "name": "밝은 창가 카페",
    "setType": "styling", // styling | horizon-rotation | horizon-sequence
    "gender": "women",
    "applicableClothingTypes": ["top"],
    "placeType": "cafe-shop-interior",
    "tone": "daily-snapshot",
    "compositionLabel": "풀 1 + 미디움 2",
    "spaceVariation": "subtle", // subtle | fixed
    "platePolicy": "required",  // required | not-required
    "representativePlate": {
      "localPath": "assets/plate/set_style_women_top_cafe_01.png",
      "key": "seed/genexamples/space-sets/v1/releases/2026-07-30-space-sets-01/plate/set_style_women_top_cafe_01.png",
      "sha256": "<64 hex>",
      "width": 1024,
      "height": 1536,
      "promptLineage": {
        "promptPath": "prompts/set_style_women_top_cafe_01_plate.txt",
        "sha256": "<64 hex>",
        "model": "gemini-2.5-flash-image"
      }
    },
    "qc": {
      "status": "pass",
      "reviewedAt": "2026-07-30T12:00:00Z",
      "reviewedBy": "owner",
      "gates": {
        "sameSpace": true,
        "sourceSimilarity": true,
        "naturalBodyPose": true,
        "lightingIntegration": true,
        "identityGarmentIntegrity": true
      }
    },
    "members": [{
      "exampleId": "ss_set_style_women_top_cafe_01_01",
      "order": 1,
      "cutType": "styling",
      "shot": "full",
      "direction": "front",
      "all": {
        "localPath": "assets/all/ss_set_style_women_top_cafe_01_01.png",
        "key": "seed/genexamples/space-sets/v1/releases/2026-07-30-space-sets-01/all/ss_set_style_women_top_cafe_01_01.png",
        "sha256": "<64 hex>",
        "width": 1024,
        "height": 1536,
        "promptLineage": {
          "promptPath": "prompts/ss_set_style_women_top_cafe_01_01.txt",
          "sha256": "<64 hex>",
          "model": "gemini-2.5-flash-image"
        }
      },
      "pose": {
        "localPath": "assets/pose/ss_set_style_women_top_cafe_01_01.png",
        "key": "seed/genexamples/space-sets/v1/releases/2026-07-30-space-sets-01/pose/ss_set_style_women_top_cafe_01_01.png",
        "sha256": "<64 hex>",
        "width": 1024,
        "height": 1536,
        "promptLineage": {
          "promptPath": "prompts/ss_set_style_women_top_cafe_01_01_pose.txt",
          "sha256": "<64 hex>",
          "model": "gemini-2.5-flash-image"
        }
      }
      // thumb는 선택 입력이다. 없으면 릴리스 도구가 all에서 결정적으로 만든다.
    }]
  }]
}
```

### 1.1 `placeType` 통일 어휘

`placeType`은 화면 제목이 아니라 **장소 겹침을 판단하는 기계 코드**다. 단일
정본은 `data/storyboard_space_place_types.json`이며 다음 14개만 허용한다.

| 값 | 뜻 |
|---|---|
| `home-interior` | 집·거실·원룸 |
| `cafe-shop-interior` | 카페·소형 매장 내부 |
| `atelier-interior` | 작업실·아틀리에 |
| `library-interior` | 도서관·서점 독서 공간 |
| `building-interior` | 복도·계단·건물 공용부 |
| `service-interior` | 세탁실·생활체육시설 |
| `industrial-yard` | 주차장·세차장·작업 안뜰 |
| `urban-alley` | 주택가·도심 골목 |
| `storefront-street` | 상점 앞·상업 거리 |
| `urban-building-exterior` | 도심 외벽·건물 외부 |
| `park-garden` | 공원·정원 |
| `waterfront` | 해변·항구·강변 |
| `resort-terrace` | 리조트·풀·테라스 |
| `horizon-studio` | 무채색 호리존 스튜디오 |

`indoor|outdoor`처럼 너무 넓은 값, 한글 화면 라벨, 세트 제목을 넣지 않는다.
밤·비·밝기·색감 같은 촬영 조건도 장소가 아니므로 `placeType`에 섞지 않는다.
릴리스 도구와 프론트 로더는 별칭을 추측해 바꾸지 않고, 정본 밖의 값을
fail-closed로 거부한다.

2026-07-30 최초 공간 세트 기능이 아직 병합·사용되기 전 발견된 혼재값은
같은 56개 setId와 같은 R2 이미지 바이트를 유지한 채 프론트·서버 카탈로그
메타데이터만 이 어휘로 교정했다. 기존 sealed stage는 업로드 바이트 감사
기록으로만 남기고 다시 적용하지 않는다. 서비스 활성화 이후의 메타데이터
변경은 이 사전 교정의 선례로 보지 않으며 새 릴리스 계약을 따른다.

## 2. 자산과 경로

- R2 루트:
  `seed/genexamples/space-sets/v1/releases/<releaseId>/`.
- 하위 경로:
  `plate/<setId>.<ext>`, `all/<exampleId>.<ext>`,
  `pose/<exampleId>.png`, `thumb/<exampleId>.webp`.
- 모든 입력 자산은 `localPath`, `key`, `sha256`, `width`, `height`를 가진다.
  `localPath`는 자산 루트 안의 상대 경로이고 `key`는 위 규약과 정확히
  일치해야 한다.
- `representativePlate`, `all`, `pose`는 생성 자산이므로
  `promptLineage={promptPath,sha256,model}`가 필수다. `promptPath` 역시 자산
  루트 안의 실제 파일이어야 하고 해시가 일치해야 한다.
- 정식 계약 이전에 만든 승인 자산은 실제 프롬프트가 남아 있지만 당시 모델
  이름이 기록되지 않은 경우 `model: "legacy-model-not-recorded"`로 사실대로
  표시한다. 추정 모델명을 적지 않으며, 신규 생성 자산에는 이 값을 쓸 수 없다.
- `thumb`는 선택 입력이다. 있으면 위 공통 필드와
  `derivedFrom: "all"`을 갖고, 릴리스 도구의 고정 파라미터로 만든 바이트와
  정확히 같아야 한다. 없으면 릴리스 도구가 생성한다.
- `pose`는 알파 채널이 있는 투명 PNG여야 한다.
- 썸네일 고정 파라미터: 최대 변 480px, WebP quality 82, method 6,
  메타데이터 없음.
- 검증을 마치면 릴리스 도구는 plate·all·pose를 스테이징 디렉터리 안으로
  복사하고 **복사본을 다시 sha256 검사**한다. 이후 업로드는 원래 작업
  디렉터리의 파일이 아니라 이 불변 복사본만 읽는다. 따라서 staging 이후
  원본이 수정·삭제되어도 업로드 바이트는 바뀌지 않는다.
- 스테이징에는 카탈로그 3개와 업로드 자산 전체의 경로·크기·sha256을 봉인한
  `release_stage.json`을 함께 둔다. 승인 뒤 실제 업로드는
  `--from-stage <검토한 경로> --upload --execute --apply`로 이 봉인을 다시
  검증하고 **같은 스테이징 바이트**를 재사용한다. manifest에서 새로
  스테이징해 바꿔치기하지 않는다.

`horizon-sequence`는 알아볼 만한 단일 장소를 공유하지 않는 스튜디오 연속
예시일 수 있다. 이 유형만 `platePolicy: "not-required"`와
`representativePlate: null`을 함께 쓸 수 있다. `styling`과
`horizon-rotation`은 대표 plate가 필수다.

## 3. QC

각 세트는 하나의 정규화된 QC receipt를 가진다. `status`는 `pass`여야 하며
다음 다섯 gate는 모두 `true`여야 한다.

- `sameSpace`: 컷들이 같은 공간에서 이어 찍은 것으로 보임
- `sourceSimilarity`: 원본의 구도·분위기·색감·촬영 성격을 보존함
- `naturalBodyPose`: 시점·무게중심·비대칭·원근이 자연스러움
- `lightingIntegration`: 인물과 공간의 광원·그림자·반사가 어울림
- `identityGarmentIntegrity`: 하우스 모델 정체성과 의류 형태가 컷 사이에서
  유지됨

QC 필드가 없거나 `false`인 자산은 “나중에 확인할 후보”이지 릴리스 자산이
아니다. 릴리스 도구는 이를 자동 보정하거나 묵인하지 않는다.

## 4. 검토된 계보 예외

다음 두 세트의 기존 승인 `all` 이미지는 생성 당시 실제 프롬프트 파일이
남지 않았다.

- `set-style-women-dress-neighborhood-garage-modimood-3266-root04`
- `set-style-women-dress-night-riverwalk-maybins-40948-root07`

이 두 setId의 `all` 자산에 한해서만 `promptLineage` 대신 아래 필드를 허용한다.

```jsonc
"reviewedProvenanceException": {
  "code": "legacy-approved-missing-prompt",
  "reason": "실제 사유를 구체적으로 기록",
  "reviewedBy": "owner",
  "reviewedAt": "2026-07-30T12:00:00Z"
}
```

예외는 자동 추론하지 않는다. 다른 setId, plate, pose에 사용하면 릴리스를
거부한다. 빈 사유나 `owner` 이외의 승인자도 거부한다.

## 5. 불변식

1. `releaseId`, `setId`, `exampleId`는 각각 유일하다. `setId`와
   `exampleId`는 영문·숫자로 시작하고 최대 200자의 영문·숫자·밑줄·하이픈만 사용하며 `__`를
   포함하지 않는다. `exampleId`는 개별 생성예시 ID와 충돌하지 않도록
   반드시 `ss_`로 시작한다. 릴리스 도구는 예약 prefix만 믿지 않고 현재
   `src/data/genExamples.json`과 `server/app/data/example_assets.json`의
   실제 ID 합집합도 읽는다. 어느 한쪽의 기존 flat ID와 같으면 발행을
   거부한다.
2. 멤버 `order`는 1부터 연속·유일하고 manifest 배열 순서와 같다.
3. `styling` 세트 멤버는 모두 styling, 호리존 세트 멤버는 모두 horizon이다.
   착용 샷은 `full|medium`, 방향은 `front|side|back`만 허용한다. 공간 세트의
   멤버 레시피는 생성 시점을 명확히 고정해야 하므로 null 방향은 발행하지 않는다.
4. `applicableClothingTypes`는 비어 있지 않고 중복이 없다. 복수 적용은
   ADR-0006에 따라 `[top, outer]` 또는 `[outer, top]`인
   styling/horizon **풀샷 전용 세트**에만 허용한다.
5. 모든 해시·픽셀 크기·실제 이미지 형식·확장자가 manifest와 일치한다.
6. 자산 key는 이 releaseId의 불변 R2 루트 밖을 가리킬 수 없다.
7. 같은 releaseId의 스테이징 경로와 R2 prefix는 덮어쓰지 않는다. 수정은 새
   releaseId로 발행한다.
8. 업로드는 기본 dry-run이다. 첫 실행은 새 스테이징에 대해 `--upload`로
   목록을 검토한다. 승인 뒤에는 `--from-stage`로 그 **동일한 봉인
   스테이징**을 불러오고 `--upload --execute`를 함께 지정해야 실제로 쓴다.
   기존 prefix에 객체가 하나라도 있으면 전체 업로드를 거부한다.
   `--apply`는 반드시 실제 업로드와 **같은 CLI 실행**에
   `--upload --execute`가 모두 있어야 하며, 업로드가 성공한 뒤에만
   실행한다.
9. 적용 시 프론트 카탈로그는 새 릴리스만 보여준다. 서버 레지스트리는 과거
   릴리스에서만 존재하는 setId를 보존해 저장된 프로젝트가 계속 생성되게
   한다. 기존과 새 릴리스에 같은 setId가 있으면 정의가 바이트 수준으로
   동일한 경우만 허용하며, 하나라도 바뀌면 새 setId를 요구한다.
10. 프론트·서버 두 정식 파일은 한 적용 단위다. 첫 파일 교체 뒤 두 번째
    파일 교체가 실패하면 첫 파일을 이전 바이트로 되돌린다. 적용 전 기존
    서버 레지스트리 검증·병합도 모두 끝내므로 병합 오류는 정식 파일을
    변경하지 않는다.
11. `placeType`은 §1.1의 통일 어휘 중 하나여야 한다. 프론트 카탈로그의
    중복 필드 `place`가 존재하면 `placeType`과 정확히 같아야 하며, 서버
    레지스트리도 같은 `setId`에 같은 값을 보존해야 한다.

## 6. 릴리스 도구 산출물

`server/tools/release_space_sets.py`는 검증 후 스테이징 디렉터리에 다음 파일을
만든다.

- `storyboardSpaceSets.json`: 프론트 공간 세트 카탈로그. `_meta`와 `sets`를
  가지며, 대표 plate는 `{url}`, 각 멤버에는 정확한 `exampleId`와
  `allUrl|thumbUrl`이 들어간다.
- `space_set_assets.json`: 서버 공간 세트 레지스트리. 최상위 형식은
  `{schemaVersion, releaseId, releasedAt, baseUrl, placeTypes, sets: []}`다.
  `placeTypes`는 §1.1 정본에서 생성한 런타임 허용값이며, 대표 plate와 멤버별
  `all|pose`는 URL 문자열이 아니라
  `{key, sha256, width, height, mime}`로 기록한다. 서버는 `baseUrl+key`로
  실제 URL을 해석한다. `sets`는 manifest 순서를 보존하는 배열이다.
- `space_set_release_audit.json`: 릴리스 검증에 사용한 세트별 QC receipt와
  자산별 `promptLineage` 또는 검토된 예외를 보존한다. 런타임 레지스트리에
  감사 정보를 섞지 않는다.
- `release_stage.json`: 카탈로그 3개와 업로드 자산 전체를 경로·크기·sha256로
  봉인한 재사용 receipt. 승인 뒤 `--from-stage`가 이를 재검증한다.
- `assets/plate|all|pose/*`: 업로드에 사용하는 검증 완료 불변 복사본.
- `assets/thumb/*.webp`: 결정적으로 파생된 썸네일.

`--apply`는 실제 업로드와 같은 실행에서 성공한 뒤 위 두 JSON만 각각
`src/data/storyboardSpaceSets.json`,
`server/app/data/space_set_assets.json`에 원자적으로 복사한다. 기존
`genExamples.json`과 `example_assets.json`은 읽거나 쓰지 않는다.
