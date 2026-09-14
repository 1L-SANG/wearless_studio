# 레퍼런스 기반 생성예시 제작·서비스 적용 파이프라인

상태: Working specification  
작성일: 2026-08-17  
적용 범위: 개별 생성예시 `all` 제작과 해당 예시를 이용한 서비스 착용컷 생성  
대표 실물 사례: `reference/genexamples/individual_production/2026-08-17/mia-cafe-snapshot-4/`

## 0. 문서의 목적

이 문서는 사용자가 제공하거나 운영자가 선별한 사진 한 장을 **연출 레퍼런스**로 삼아,
원본의 포즈·카메라·구도·광원·색감·촬영 등급은 알아볼 수 있게 유지하면서도 원본 인물,
판매 의류, 정확한 장소를 복제하지 않은 서비스용 생성예시를 만드는 방법을 정의한다.

또한 승인된 생성예시를 실제 서비스에서 사용할 때, 예시의 옷이 셀러 상품을 덮어쓰지 않고
예시가 포즈·공간·촬영 분위기만 담당하도록 입력 권한과 프롬프트 조립 순서를 정의한다.

이 문서에서 `MUST`, `MUST NOT`, `SHOULD`, `MAY`는 각각 필수, 금지, 권장, 선택을 뜻한다.

이 문서는 다음 정본을 구현 지침으로 풀어 쓴 운영 문서다.

- ADR-0004: 생성예시는 촬영 연출만 참고하고 예시 의류를 가져오지 않는다.
- ADR-0005: 사용자 섹션과 내부 생성 레시피를 분리한다.
- ADR-0006: `sourceClothingType`과 `applicableClothingTypes`를 분리한다.
- ADR-0007: 착용컷 샷은 서비스에서 `full | medium`만 사용한다.
- ADR-0008: 제품컷은 `ghost | detail`이며 이 문서의 생활 스타일링 파이프라인과 분리한다.
- ADR-0009: `all | pose | bg`는 참고 범위이며 전용 자산이 없으면 다른 범위로 폴백하지 않는다.
- ADR-0010: 생성 계열과 항목별 정본을 분리하고, 생성과 QC를 독립시킨다.
- ADR-0011: 일반 `all`은 포즈 의미와 촬영 분위기를 유지하되 자연스러운 다른 장소로 변주한다.
- `documents/genexamples_release_contract.md`: 안정 ID, 불변 릴리스, manifest와 서비스 배선 계약.

정본과 이 문서가 충돌하면 정본이 우선한다. 이 문서의 예시는 정본을 바꾸는 새 제품 결정이 아니다.

## 1. 한 문장 계약

> 레퍼런스는 편집할 원본이 아니라 연출 근거이고, 모델 레퍼런스는 정체성·체형 근거이며,
> 셀러 상품 이미지는 실제 서비스 의류의 유일한 근거다.

이 계약을 지키기 위해 결과 항목마다 정본을 하나만 둔다. 여러 이미지를 한 줄의 전역 우선순위로
경쟁시키지 않는다.

| 결과 항목 | 생성예시 제작 시 정본 | 서비스 상품컷 생성 시 정본 |
|---|---|---|
| 포즈·행동·체중·중요 접촉 | 연출 레퍼런스 | 현재 카드의 명시 포즈, 없으면 생성예시 |
| 카메라·프레이밍 | 연출 레퍼런스와 목표 shot | 현재 카드와 호환되는 생성예시 |
| 장소 유형·시간·광원·촬영 등급 | 연출 레퍼런스 | 공간 세트/`bg`/`all` 중 활성 장소 정본 |
| 얼굴 정체성 | 하우스 모델 얼굴 | 프로젝트에서 선택한 모델 |
| 체형·비율 | 하우스 모델 전신 | 프로젝트에서 선택한 모델 |
| 생성예시 속 의류 | 같은 범주의 다른 무채색·무브랜드 의류 | 사용 금지 |
| 판매 의류 | 해당 없음 | 셀러 PRODUCT 이미지 |
| 매칭 의류 | 중립 무브랜드 코디 | 사용자가 고른 MATCHING 이미지 |
| shot·direction·faceExposure | 제작 스펙 | 현재 콘티보드 카드 |

## 2. 파이프라인 전체 흐름

```text
[A. 원본 수령]
      ↓
[B. 적합성·중복·권리·샷 분류]
      ↓
[C. 연출 의미 추출 + 입력 권한 선언]
      ↓
[D. 이미지별 구조화 프롬프트 작성]
      ↓
[E. all 직접 생성 — 이미지마다 독립 호출]
      ↓
[F. 제작자 1차 검사]
      ↓
[G. 독립 QC]
      ├─ PASS → 사용자 검토
      ├─ FAIL → 실패 축 하나를 교정, 최대 2회
      └─ UNJUDGEABLE/반복 실패 → HOLD
      ↓
[H. 사용자 승인]
      ↓
[I. 불변 release manifest 생성·검증]
      ↓
[J. 사용자 승인 후 R2 업로드 + 프론트·서버 동시 적용]
      ↓
[K. 서비스 CutPlan에서 PRODUCT/MODEL/EXAMPLE 권한 분리 후 사용]
```

제작과 발행은 분리한다. 이미지가 좋아 보여도 사용자 승인과 릴리스 게이트를 통과하기 전에는
R2 업로드, 카탈로그 수정, 서비스 적용, 커밋·푸시를 수행하지 않는다.

## 3. 범위와 비범위

### 3.1 이 문서가 다루는 것

- 개별 `styling` 착용 생성예시의 `all` 완성 이미지
- 풀샷과 중간샷의 독립 생성
- 연출 레퍼런스 + 하우스 모델 얼굴 + 하우스 모델 전신의 역할 분리
- 휴대폰 스냅 촬영 등급 보존
- 원본과 다른 구체 장소·다른 의류 생성
- QC, 재시도, 사용자 승인, manifest, 서비스 연결

### 3.2 별도 계약으로 다루는 것

- `pose`: 옷·배경이 없는 투명 PNG 중립 마네킹 전용 자산
- `bg`: 사람과 접촉 그림자가 없는 빈 장소 플레이트를 편집 캔버스로 사용하는 방식
- 공간 세트: 하나의 대표 공간과 독립 생성된 멤버 컷을 묶는 `SeriesPlan`
- `horizon`: 중립 스튜디오 계열
- `product/ghost | product/detail`: 사람 없는 제품컷
- `mirrorSelfie`: 거울 반사·휴대폰·얼굴 가림을 검사하는 styling 하위 방식

`pose` 또는 `bg` 파일이 없을 때 `all` 이미지로 대신하면 안 된다. 반대 방향도 금지한다.

## 4. 입력 준비

### 4.1 필수 입력

개별 결과 한 장을 만들 때 다음 세 역할을 준비한다.

1. **DIRECTING REFERENCE**
   - 포즈, 행동, 좌우 팔다리 역할, 체중 분배, 고개·시선, 카메라 높이·거리·원근,
     프레이밍, 장소 종류, 시간대, 색감, 광원, 촬영 등급의 근거다.
   - 편집 대상이 아니다.
   - 원본 인물, 정확한 의류, 로고, 장소 픽셀, UI 테두리는 복제하지 않는다.
2. **MODEL FACE**
   - 얼굴이 보여야 하는 컷에서만 하우스 모델 정체성 근거로 사용한다.
   - 원본 얼굴이 가려졌거나 프레임 밖이면 얼굴을 드러내기 위해 사용하지 않는다.
3. **MODEL FULL BODY**
   - 체형과 신체 비율의 근거다.
   - 포즈·카메라·배경 근거로 사용하지 않는다.

Mia의 현재 대표 참조는 다음과 같다.

```text
MODEL FACE: public/models/women/w1.webp
MODEL FULL BODY: spike/runs/facepack-w1v2-2026-07-13T23-49-24/grid-fullbody.png
```

### 4.2 입력 사전 검사

다음 중 하나라도 충족하면 자동 생성으로 넘기지 않고 제외 또는 사람 검토한다.

- 워터마크·큰 오버레이·읽기 어려운 해상도 때문에 연출을 판단할 수 없다.
- 광고 캠페인·고급 룩북이며 목표 촬영 등급과 맞지 않는다.
- 관절·발·의류 범위를 판단할 수 없을 정도로 잘렸다.
- 풀샷을 잘라 만든 것을 중간샷 원본처럼 사용하려 한다.
- 기존 카탈로그의 동일 상품 URL·동일 이미지 또는 기존 exampleId와 중복된다.
- 의자·난간·탁자 같은 지지물을 없애면 포즈 의미가 붕괴하는데 대체 기능을 정의할 수 없다.
- 원본 사용 출처와 내부 사용 근거를 기록할 수 없다.

## 5. SOURCE_SELECTION 작성

작업을 시작할 때 프로젝트 작업 폴더에 `SOURCE_SELECTION.json`을 먼저 만든다. 최소 형식은
다음과 같다.

```json
{
  "schemaVersion": 1,
  "createdAt": "YYYY-MM-DD",
  "sourceOrigin": "user-provided | mall-research | internal-approved",
  "sourceUrl": null,
  "sourceLocalPath": "sources/directing-reference.png",
  "sourceRole": "directing reference only",
  "houseModel": {
    "id": "mA",
    "name": "Mia",
    "sid": "w1",
    "faceReference": "public/models/women/w1.webp",
    "bodyReference": "spike/runs/facepack-w1v2-2026-07-13T23-49-24/grid-fullbody.png"
  },
  "classification": {
    "gender": "women",
    "sourceClothingType": "top",
    "applicableClothingTypes": ["top", "outer"],
    "cutType": "styling",
    "shot": "full",
    "direction": "front",
    "faceExposure": "partial-cropped",
    "mood": "bright-neighborhood-cafe-phone-snapshot"
  },
  "notes": "The source is directing evidence only. Do not copy its person, exact garment or exact location."
}
```

규칙:

- `sourceClothingType`은 원본 판매 상품의 실제 분류다.
- `applicableClothingTypes`는 해당 연출을 적용할 수 있는 목표 상품 종류다.
- 공용 범위는 사람이 판단한다. 같은 옷을 열어 입었다는 이유만으로 분류를 바꾸지 않는다.
- 착용컷 `shot`은 `full | medium`만 사용한다.
- `faceExposure`에는 상태뿐 아니라 가능하면 원인도 별도 기록한다.
  - `visible`
  - `partial-cropped`
  - `hidden-by-hair`
  - `hidden-by-object`
  - `back-facing`

릴리스 manifest v1과 현재 서버 레지스트리는 얼굴 가시성의 전체 세부 토큰을 아직 공통으로
운반하지 않는다. 세부 토큰을 릴리스 필드에 임의로 추가하지 말고, 계약·릴리스 도구·프론트·서버를
함께 확장하기 전까지는 제작/QC 증거에 보존한다. 서버 레지스트리가 지원하는 강한 힌트는 현재
`faceVisibility: hidden | visible`이다.

## 6. 연출 분석: 좌표가 아니라 의미를 추출한다

원본의 관절 좌표를 그대로 복사하지 않는다. 아래 의미 골격을 작성한다.

```json
{
  "action": "reach_for_cup",
  "bodyDirectionFamily": "front_three_quarter",
  "supportSide": "rear_leg",
  "torso": "lean_toward_cup",
  "pelvis": "shifted_over_support_leg",
  "shoulders": "uneven",
  "limbRoles": {
    "leftArm": "extended_down_to_cup",
    "rightArm": "hand_in_pocket",
    "legs": "staggered_nonparallel"
  },
  "importantContacts": ["hand_to_cup", "hand_in_pocket", "feet_to_ground"],
  "gaze": "down_toward_action",
  "faceMechanism": "top_edge_crop_with_lower_face_fragment"
}
```

반드시 보존할 축:

- 행동
- 몸 방향 계열
- 체중 지지측
- 중요한 접촉 또는 지지물의 기능
- 왼팔과 오른팔의 서로 다른 역할
- 다리의 앞뒤와 발 방향
- 시선 의미
- 의도된 비대칭

자연스럽게 달라질 수 있는 축:

- 작은 관절각
- 손가락 위치
- 작은 고개 각도
- 느슨한 머리카락
- 원단의 미세 주름

자유를 허용하더라도 좌우 대칭 마네킹 자세, 반대 지지측, 다른 행동으로 바뀌면 실패다.

## 7. 프롬프트 작성 계약

### 7.1 프롬프트 순서

모든 이미지 프롬프트는 다음 순서를 사용한다.

1. `TASK / ASSET TYPE`
2. `INPUT ROLES`
3. `SCENE / BACKDROP`
4. `SUBJECT`
5. `CAMERA / POSE`
6. `CLOTHING`
7. `LIGHTING / MOOD / CAPTURE CLASS`
8. `CONSTRAINTS / NON-TRANSFER`

각 결과는 별도 프롬프트 파일을 가진다. 여러 결과가 같은 원본을 쓰더라도 프롬프트와 생성 호출을
독립시킨다.

### 7.2 개별 생성예시 제작용 표준 템플릿

```text
TASK / ASSET TYPE
Generate one new photorealistic-natural service generation example.
Asset: ${gender} / ${sourceClothingType} / ${cutType} / ${shot}.
This is a direct all-image generation task, not compositing.

INPUT ROLES
- Image 1 — DIRECTING REFERENCE ONLY: use its capture class, scene archetype,
  broad composition, camera height and distance, perspective, action, balance,
  asymmetry, light direction, white balance, contrast and exposure character.
- Image 2 — MODEL FACE IDENTITY ONLY: use ${modelName}'s face only when the
  directing reference exposes a comparable face region.
- Image 3 — MODEL FULL-BODY PROPORTIONS ONLY: use ${modelName}'s body identity
  and proportions, not its pose, camera or clothing.
- None of these images is an edit target. Generate a new photograph.

SCENE / BACKDROP
Create a believable different location in the same visual family as the directing
reference: ${sceneDescription}. Preserve the place type, spatial density, broad
palette, time of day and everyday function. Do not copy the exact architecture,
furniture arrangement, signs, props or pixels. Do not add arbitrary objects merely
to prove that the place is different.

SUBJECT
Use ${modelName}'s identity and body proportions. ${faceExposureInstruction}
Use the house model's normal hair; let it respond naturally to head direction,
movement and wind. Do not copy the source person's identity, hair, tattoos or accessories.

CAMERA / POSE
Use ${captureOrientation} ordinary handheld camera framing at ${cameraHeight},
with ${lensCharacter} perspective and the same compatible subject scale, crop,
headroom and negative-space rhythm as the directing reference.
Preserve the semantic pose backbone: ${poseSemanticBackbone}.
Preserve left/right limb roles and support side. Allow only small natural changes
in joint angles, fingers, loose hair and fabric response. Do not straighten the
body into a centered symmetric mannequin stance.
${shotBoundaryInstruction}

CLOTHING
Create a different unbranded, solid, neutral-color garment in the same product
category and a compatible silhouette family: ${neutralGarmentDescription}.
Do not recolor or reproduce the exact source garment. Use neutral unbranded
coordination clothing and shoes. Fabric tension, compression, folds and drape
must respond to the action and contacts.

LIGHTING / MOOD / CAPTURE CLASS
Apply ${lightDescription} coherently to face, hair, skin, garment, shoes, floor
and background. Include plausible contact shadow, cast shadow, fabric self-shadow
and ambient color reflection. Match the directing reference's white balance,
contrast, saturation, exposure and weather character.
Keep the ${captureClass} capture class. Do not upgrade an ordinary phone snapshot
into a campaign, editorial, luxury lookbook or cinematic image.

CONSTRAINTS / NON-TRANSFER
- No background-first generation and no pasted-person compositing.
- Do not reproduce the source person, exact garment, shoes, bag, branded object,
  exact location, screenshot UI, rounded border or watermark.
- Do not add new captions, overlays or watermarks.
- Natural anatomy, physically responsive cloth, coherent perspective and grounded feet.
- The result must visibly retain the directing reference's art-direction logic
  without looking like the same person, product or exact place.
```

### 7.3 얼굴 크롭은 상태와 방법을 함께 적는다

```text
Weak: Hide the face.

Strong: Place the crown, forehead and eyes outside the top frame. Retain only a
small lower-face/profile fragment. Do not hide the face with a new hand, phone or
hair mechanism. Keep both shoes visible.
```

`hidden`만 적으면 모델이 원본과 다른 가림 수단을 발명할 수 있다. 크롭, 머리카락, 물체,
뒷모습 중 어떤 기제로 얼굴이 안 보이는지를 적는다.

### 7.4 촬영 등급은 한 단어가 아니라 묶음으로 고정한다

휴대폰 스냅이라면 다음을 함께 기술한다.

- ordinary handheld smartphone snapshot
- imperfect but intentional framing
- consumer-camera sharpness
- modest dynamic range
- natural or slightly clipped highlights
- source-like white balance and contrast
- no campaign polish
- no cinematic grading
- do not upgrade the capture class

### 7.5 `No text`를 쓰지 않는다

생성예시 제작에서는 새 글자·로고·워터마크를 만들지 말라고 지시한다. 서비스 상품컷에서는
셀러 상품에 실제로 인쇄된 영구 텍스트와 로고가 상품 정본이므로 `No text`로 지워서는 안 된다.
대신 다음처럼 구분한다.

```text
Do not add new captions, watermarks, overlays or invented signage.
Preserve only product text and logos that are visibly supported by PRODUCT references.
```

## 8. 생성 실행

### 8.1 직접 `all` 생성

- background-first를 사용하지 않는다.
- 빈 배경을 먼저 만들지 않는다.
- 원본 사진에 모델을 붙이는 편집으로 실행하지 않는다.
- 연출 레퍼런스, 모델 얼굴, 모델 전신을 역할별 참고 이미지로 넣고 새 사진을 직접 생성한다.
- 기본 내장 `image_gen`을 사용한 경우 모델 내부명, seed, temperature가 노출되지 않으므로
  존재하지 않는 값을 receipt에 지어내지 않는다.

### 8.2 이미지마다 독립 호출

```text
for each selected_source:
    build one SOURCE_SELECTION item
    write one prompt file
    call image generation once with:
        directing reference
        house-model face reference
        house-model full-body reference
        that image's prompt
    copy the returned PNG into generated/<exampleId>.png
    record the call and output path
```

- 풀샷을 확대·크롭해 중간샷을 만들지 않는다.
- 한 결과를 다음 결과의 포즈·카메라 근거로 사용하지 않는다.
- 네 장이면 독립 호출 네 번이다.
- 생성기 기본 출력 폴더에만 두지 않고 프로젝트 작업 폴더로 복사한다.

### 8.3 재시도 예산

- 최초 생성 1회
- 교정 최대 2회
- 실패한 축 하나를 중심으로 교정
- 두 번 교정 후에도 하드 게이트를 통과하지 못하면 `HOLD`

현재 프로덕션 실시간 서비스의 ADR-0010 `repair`는 최대 한 번의 2차 후보를 채택하는 별도
런타임 정책이다. 오프라인 생성예시 제작의 최대 두 번 교정과 혼동하지 않는다.

## 9. 교정 프롬프트

전체 프롬프트를 새로 쓰면 이미 통과한 항목이 회귀한다. 실패 항목을 사실 형태의 패치로 적고
나머지 불변 조건을 재선언한다.

```text
CORRECTION — ${failedAxis} only.

Keep the approved scene family, subject identity, garment category, capture class,
lighting direction, action, support side and all other previously defined constraints unchanged.

Observed failure:
${observableFailure}

Required correction:
${singleCorrection}

Do not change:
${invariants}
```

얼굴 크롭 교정 예시:

```text
CORRECTION — framing only.

The previous result revealed the full head. Move the top frame downward so the
crown, forehead and eyes remain outside the image. Retain only a very small
lower-face/profile fragment. Both shoes must remain fully visible.
Do not change the cup-reaching action, support side, camera height, body scale,
clothing, location family, daylight direction or phone-snapshot finish.
```

## 10. QC 계약

### 10.1 생성자와 판정자를 분리한다

생성 모델의 자기평가와 제작자의 PASS만으로 릴리스하지 않는다. 별도 판정자가 원본과 결과를
블라인드에 가깝게 비교하고, 각 항목에 `PASS | FAIL | NA | UNJUDGEABLE`과 관찰 근거를 남긴다.
`UNJUDGEABLE`은 통과가 아니다.

데스크톱 생성예시 제작에서는 독립 Codex 비전 검수자를 사용할 수 있다. 실시간 서비스는
데스크톱 세션에 의존하지 않고 같은 계약을 구현한 독립 비전 판정기를 사용한다.

### 10.2 하드 게이트

1. 선택 원본과 결과가 1:1 대응한다.
2. `gender`, `cutType`, `shot`, 의류 종류가 맞다.
3. 카메라 높이·거리·원근·프레이밍이 호환된다.
4. 행동, 좌우 팔다리 역할, 고개·시선, 체중 지지측과 비대칭이 유지된다.
5. 얼굴 노출 상태와 가림/크롭 기제가 유지된다.
6. 얼굴이 비교 가능할 때 하우스 모델 정체성이 맞다.
7. 원본과 다른 무채색·무브랜드 의류다.
8. 원단 주름·당김·눌림이 행동과 접촉에 반응한다.
9. 원본의 휴대폰 스냅/일반 카메라/스튜디오 촬영 등급을 유지한다.
10. 같은 장소 가족이지만 정확한 장소·소품 배열의 복사는 아니다.
11. 광원 방향·부드러움·색온도가 인물, 옷, 바닥, 배경에 일관된다.
12. 신체 비율, 손·관절, 원근, 발 접촉과 그림자가 자연스럽다.
13. 새 로고·문구·워터마크·UI·두드러진 AI 오류가 없다.

### 10.3 `QC.json` 최소 형식

```json
{
  "schemaVersion": 1,
  "createdAt": "YYYY-MM-DD",
  "producerQc": "complete",
  "independentQc": "complete",
  "counts": {
    "items": 1,
    "pass": 1,
    "hold": 0,
    "generationCalls": 1,
    "correctionCalls": 0
  },
  "items": [
    {
      "exampleId": "ex_styling_women_top_full_snapshot_07",
      "status": "PASS",
      "attempts": 1,
      "gates": {
        "referenceCorrespondence": "PASS",
        "shotAndCategory": "PASS",
        "cameraAndFraming": "PASS",
        "poseSemantics": "PASS",
        "faceExposure": "PASS",
        "modelIdentity": "NA",
        "garmentNonCopy": "PASS",
        "fabricPhysics": "PASS",
        "captureClass": "PASS",
        "sceneVariation": "PASS",
        "lightIntegration": "PASS",
        "anatomyAndGrounding": "PASS",
        "cleanOutput": "PASS"
      },
      "evidenceKo": "상단 크롭으로 하관만 남고 컵을 향한 기울기와 뒤쪽 다리 지지가 유지됨",
      "retryReason": null
    }
  ]
}
```

## 11. 작업 폴더와 산출물

권장 경로:

```text
reference/genexamples/individual_production/YYYY-MM-DD/<owner>-styling-<count>/
├── sources/
│   └── <source-id>.png
├── generated/
│   └── <exampleId>.png
├── prompts/
│   └── <exampleId>.txt
├── SOURCE_SELECTION.json
├── QC.json
├── HANDOFF.json
└── review.html
```

필수 조건:

- `generated/`에는 최종 사용자 검토 후보만 둔다.
- 실패 시도는 삭제하지 말고 별도 attempts 또는 audit 경로에 보존하거나 QC에 해시·경로를 남긴다.
- 과거에 삭제된 ID를 재사용하지 않는다.
- 저장소 전체와 과거 산출물에서 ID 중복을 검사한다.
- `review.html`은 원본과 생성 결과를 좌우로 보여주고 PASS/HOLD와 QC 사유를 표시한다.
- 사용자는 승인/수정/제외를 선택하고 JSON으로 내보낼 수 있어야 한다.

## 12. 사용자 승인 상태

권장 상태 전이는 다음과 같다.

```text
DRAFT
→ GENERATED
→ PRODUCER_REVIEWED
→ INDEPENDENT_QC_PASS | HOLD
→ READY_FOR_USER_REVIEW
→ USER_APPROVED | USER_REWORK | USER_EXCLUDED
→ RELEASE_STAGED
→ RELEASED
```

제작자 검사만 끝났다면 `PASS` 또는 `RELEASED`로 쓰지 않는다. `READY_FOR_USER_REVIEW`로 기록한다.
사용자가 품질 예외를 알고 HOLD 결과를 승인하더라도 원래 QC 상태와 실패 사유를 감사 이력에서
PASS로 바꾸지 않는다.

## 13. 릴리스 manifest와 배포

### 13.1 발행 원칙

- exampleId는 전역 유일하고 영구 불변이다.
- 동일 `releaseId` 경로를 덮어쓰지 않는다.
- `all`은 필수다.
- `pose`와 `bg`는 실제 QC 승인 파일이 있을 때만 variants에 넣는다.
- 미발행 variant의 미래 경로를 manifest에 적지 않는다.
- `thumb`은 릴리스 도구가 `all`에서 결정적으로 만든다.
- 프론트 카탈로그와 서버 레지스트리는 한 릴리스에서 함께 생성·검증·적용한다.

### 13.2 manifest 항목

```json
{
  "id": "ex_styling_women_top_full_snapshot_07",
  "serviceGroupKey": "styling:women:top:full:neighborhood-cafe-phone-snapshot",
  "rank": 1,
  "cutType": "styling",
  "gender": "women",
  "shot": "full",
  "mood": "neighborhood-cafe-phone-snapshot",
  "detailSubject": null,
  "presentationMethod": null,
  "direction": "front",
  "sourceClothingType": "top",
  "applicableClothingTypes": ["top", "outer"],
  "variants": {
    "all": {
      "file": "assets/all/ex_styling_women_top_full_snapshot_07.png",
      "sha256": "<actual sha256>",
      "width": 1111,
      "height": 1415
    }
  }
}
```

### 13.3 릴리스 도구

실제 명령 형태는 다음과 같다.

```bash
python3 server/tools/release_genexamples.py \
  <release_manifest.json> \
  <asset_root> \
  --out <staging_dir>
```

업로드 목록만 보는 dry-run:

```bash
python3 server/tools/release_genexamples.py \
  <release_manifest.json> \
  <asset_root> \
  --out <staging_dir> \
  --upload
```

실제 업로드와 적용은 파괴적이지 않더라도 외부 운영 상태를 바꾸므로 사용자 명시 승인 후에만
같은 실행에서 수행한다.

```bash
python3 server/tools/release_genexamples.py \
  <release_manifest.json> \
  <asset_root> \
  --out <staging_dir> \
  --upload --execute --apply
```

`--apply`는 같은 실행의 성공한 `--upload --execute` 영수증 없이는 허용되지 않는다. 적용 전
프론트·서버 문서 전체를 fail-closed로 검증하고, 두 소비자의 ID 집합과 thumb URL이 같아야 한다.

## 14. 서비스 런타임 적용

### 14.1 입력 권한

서비스에서 생성예시를 선택하면 예시의 옷을 재사용하지 않는다. 축별 정본은 다음과 같다.

```text
PRODUCT            → 실제 판매 상품의 구조·색·소재·무늬·봉제·부자재·영구 텍스트
MATCHING           → 사용자가 고른 코디 의류
MODEL FACE         → 모델 얼굴 정체성
MODEL FULL BODY    → 모델 체형·비율
CUT SPEC           → cutType, shot, direction, faceExposure, color, outerClosure, named pose
EXAMPLE all        → 호환되는 포즈 의미, 카메라, 장소 가족, 광원, 촬영 등급
MOOD               → 위에 장소 정본이 없을 때만 보조
```

현재 카드가 방향·shot·얼굴 노출·포즈를 바꾸면 현재 카드가 우선한다. 충돌하는 지시를 둘 다
프롬프트에 남기지 말고 컴파일 단계에서 예시 권한을 낮추거나 제거한다.

### 14.2 방향 불일치

- 예시와 현재 direction이 호환되면 `all`은 포즈·카메라까지 연출 근거가 된다.
- direction이 다르면 `all_scene_only`로 낮춘다.
- 이때 예시는 장소·광원·촬영 톤·넓은 공간 분위기만 맡는다.
- 원래 포즈, 시선, 좌우 팔다리, 원근, 크롭은 전달하지 않는다.

### 14.3 서비스용 프롬프트 골격

```text
TASK
Generate one new photorealistic fashion image from the compiled CUT SPEC.

REFERENCE AUTHORITY
- PRODUCT is the sole garment truth.
- MATCHING is the truth for selected coordination items.
- MODEL FACE and MODEL FULL BODY define model identity and proportions.
- EXAMPLE is art direction only within the active refScope.
- None of these references supplies authority outside its declared role.

SCENE / SUBJECT / CAMERA / POSE
Apply only the compatible example direction defined by the compiled authority plan.
The current CUT SPEC wins for shot, direction, face exposure and named pose.

GARMENT
Reproduce the seller PRODUCT faithfully. Never transfer the EXAMPLE garment,
shoes, logo, accessories or temporary tags.

LIGHTING / CAPTURE
Integrate person, product, floor and scene into one coherent light and shadow system.
Preserve the EXAMPLE capture class when it owns art direction.

OUTPUT
Do not add new captions, watermarks, overlays or unsupported text.
Preserve only PRODUCT-supported permanent text and logos.
```

실제 정본 템플릿은 `server/prompts/cut_generate_v1.txt`, 조립기는
`server/app/agents/cut_generator.py`다.

## 15. 서버 소유 directing profile

이미지를 매번 자유 문장으로 다시 해석하게 두면 편차가 커진다. 현재 서버는 클라이언트의 자유
문장이 아니라, 승인된 생성예시에 연결된 enum-only `directing_profile`을 보조 지시로 받을 수 있다.

허용 필드와 값은 `server/app/agents/directing_profile.py`가 정본이다.

```json
{
  "directionMode": "exact | retarget",
  "poseDynamics": "reference_kinematics | natural_asymmetry | controlled_stillness | natural_motion",
  "camera": "reference_geometry | handheld_oblique | handheld_eye_level | tripod_centered | mirror_phone | product_camera",
  "framing": "reference_crop | casual_off_center | centered_catalog | product_close",
  "capture": "phone_snapshot | casual_digital | editorial | studio_catalog | mirror_selfie | product_catalog",
  "scene": "reference_location | lifestyle_location | horizon_studio | mirror_room | product_studio",
  "light": "reference_integrated | natural_soft | natural_hard | mixed_available | studio_soft | product_diffused"
}
```

예시:

```json
{
  "directionMode": "exact",
  "poseDynamics": "reference_kinematics",
  "camera": "reference_geometry",
  "framing": "reference_crop",
  "capture": "phone_snapshot",
  "scene": "reference_location",
  "light": "reference_integrated"
}
```

규칙:

- 알 수 없는 필드와 자유 문장은 fail-closed로 거부한다.
- 명시 포즈가 있으면 profile의 poseDynamics를 렌더하지 않는다.
- direction 불일치는 서버가 `retarget`으로 강제한다.
- profile은 CUT SPEC과 상품 정본보다 낮은 권한이다.
- product 컷에는 사람 포즈와 directionMode를 넣지 않는다.

현재 release manifest v1은 이 profile을 예시 항목의 표준 필드로 운반하지 않는다. 서비스에
영구 연결하려면 release contract, release tool, 서버 registry, 프론트 catalog 중 필요한 소비자를
함께 갱신해야 하며, 과거 manifest를 덮어쓰면 안 된다.

## 16. 서비스 전 검사와 실패 처리

생성 호출 전 다음을 검사한다.

- exampleId가 서버 레지스트리에 존재한다.
- 목표 `clothingType`이 `applicableClothingTypes`에 포함된다.
- 선택한 `refScope` variant가 실제 발행되어 있다.
- pose variant는 현재 direction과 호환된다.
- 공간 세트는 발행된 정식 `spaceGroupId`만 사용한다.
- PRODUCT, MODEL, MATCHING, EXAMPLE 역할 목록과 실제 첨부 순서가 일치한다.
- `all` 또는 `bg`가 장소 정본이면 경쟁하는 mood 이미지를 제거한다.
- `faceVisibility=hidden` 힌트와 사용자의 명시 `show`가 충돌하면 사용자 카드가 우선한다.

자산이 없으면 다른 variant로 폴백하지 않는다. 적용할 수 없는 예시는 조용히 사용하지 않고
상태를 `unknown | not_applicable | variant_unpublished`로 구분해 기록한다.

## 17. 대표 Mia 사례

실제 제작 사례:

```text
reference/genexamples/individual_production/2026-08-17/mia-cafe-snapshot-4/
```

특징:

- 사용자 제공 스크린샷을 `directing reference only`로 사용했다.
- Mia 얼굴과 전신을 별도 정체성·체형 근거로 사용했다.
- built-in `image_gen`으로 네 장을 각각 독립 호출했다.
- background-first를 사용하지 않았다.
- 결과를 `generated/`로 복사하고 이미지별 프롬프트를 보존했다.
- 첫 컷은 정수리·눈을 상단 밖으로 두고 하관만 남기는 크롭을 명시했다.
- 네 장 중 사용자가 승인한 첫 컷만 릴리스했다.
- 이 배치의 `QC.json`은 제작자 검사만 기록하고 독립 QC는 실행하지 않았으므로, 이 문서의
  목표 파이프라인을 재현할 때는 독립 QC 단계를 추가해야 한다.

대표 파일:

- `SOURCE_SELECTION.json`
- `prompts/ex_styling_women_top_full_mia_cafe_snapshot_01.txt`
- `generated/ex_styling_women_top_full_mia_cafe_snapshot_01.png`
- `QC.json`
- `HANDOFF.json`
- `review.html`

## 18. 구현 완료 기준

다음 조건을 모두 만족해야 “동일한 파이프라인을 구현했다”고 판단한다.

### 제작 단계

- [ ] 연출, 얼굴, 전신 입력이 역할별로 분리되어 있다.
- [ ] 어느 입력도 암묵적인 편집 대상이 아니다.
- [ ] 이미지마다 독립 프롬프트와 독립 생성 호출을 사용한다.
- [ ] full과 medium을 서로 크롭 파생하지 않는다.
- [ ] capture class, 포즈 의미, 얼굴 노출 기제, 광원을 명시한다.
- [ ] 원본 인물·의류·정확한 장소의 복제를 금지한다.
- [ ] 최초 1회 + 교정 최대 2회 예산과 HOLD 상태가 있다.
- [ ] 생성과 독립된 QC가 구조화된 근거를 남긴다.
- [ ] 원본/결과 비교 HTML과 사용자 결정 JSON을 제공한다.

### 릴리스 단계

- [ ] 전역 ID 중복을 검사한다.
- [ ] 사용자 승인된 항목만 manifest에 넣는다.
- [ ] 실제 파일·해시·크기·dimensions를 검증한다.
- [ ] 같은 releaseId를 덮어쓰지 않는다.
- [ ] dry-run과 실제 업로드를 분리한다.
- [ ] 실제 업로드 영수증 뒤에만 프론트·서버 카탈로그를 함께 적용한다.

### 서비스 단계

- [ ] PRODUCT가 의류의 유일한 정본이다.
- [ ] MODEL FACE와 FULL BODY의 역할이 분리되어 있다.
- [ ] CUT SPEC이 shot·direction·faceExposure·명시 포즈에서 우선한다.
- [ ] EXAMPLE은 활성 `refScope` 밖의 정보를 공급하지 않는다.
- [ ] 방향 불일치 시 `all_scene_only`로 낮춘다.
- [ ] 발행되지 않은 pose/bg로 폴백하지 않는다.
- [ ] 촬영 등급과 광원 통합을 QC한다.
- [ ] 서비스 QC는 생성 모델과 분리되어 있다.

## 19. 새 작업자가 시작할 때의 짧은 실행 지시

```text
ADR-0004~0011, documents/genexamples_release_contract.md와 이 문서를 먼저 읽는다.
선택 원본은 연출 근거, 하우스 얼굴은 정체성 근거, 하우스 전신은 비율 근거로만 사용한다.
background-first 없이 all 이미지를 직접 생성한다.
결과마다 독립 프롬프트·독립 image generation 호출을 사용한다.
포즈는 좌표가 아니라 행동·지지측·접촉·좌우 팔다리 역할·시선·비대칭 의미를 보존한다.
원본의 촬영 등급·카메라·광원·얼굴 노출 기제를 유지하되 원본 인물·의류·정확한 장소는 복제하지 않는다.
최초 1회와 교정 최대 2회만 허용하고, 독립 QC 실패는 품질 기준을 낮추지 말고 HOLD로 남긴다.
사용자 승인 전에는 R2, 서비스 카탈로그, 커밋, 푸시를 변경하지 않는다.
```
