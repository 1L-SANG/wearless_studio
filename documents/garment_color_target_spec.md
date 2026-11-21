# 셀러가 정한 옷 색 반영 명세 (garment-color-v1)

- 날짜: 2026-11-21
- 상태: 1차 구현 정본. 결정 근거는 ADR-0029.
- 범위: 코디보드에서 셀러가 실물 옷 색을 맞추고, 상세페이지 생성이 끝난 뒤 그 색을 옷 부분에만 반영한다. 에디터에서 다시 맞추기는 2차다.

## 1. 한 줄 규칙

셀러는 올린 상품 사진을 보며 슬라이더로 "실물은 이 색"을 정한다(목표 색 T). 생성은 지금 그대로 하고, 모든 컷이 끝난 뒤 그 색상의 컷들에서 옷의 가운데 색 M을 잰다. M이 T가 되게 하는 보정 한 벌을 구해 그 색상의 모든 컷 옷 부분에 똑같이 건다. AI로 다시 그리지 않는다.

보정은 선형광에서 픽셀마다 곱하는 연산(따뜻함 채널 이득, 밝기 노출 이득)과 그 픽셀 자신의 휘도 둘레 채도 조절뿐이다. 사진의 픽셀은 "빛 × 옷감 색"이므로, 같은 비율을 곱하면 옷감 색만 바뀌고 컷마다 다른 빛의 세기와 색온도는 그대로 남는다.

## 2. 색 연산 `garment-color-v1`

입력은 선형 sRGB 한 픽셀 `(r, g, b)`와 값 `p = {chroma, lightness, warmth}`다. 화면 미리보기(`src/lib/garmentColorTone.js`)와 서버(파이썬)가 같은 식을 쓴다. 상수:

| 이름 | 값 | 뜻 |
|---|---|---|
| `LR, LG, LB` | 0.2126, 0.7152, 0.0722 | Rec.709 휘도 |
| `WARM_END` | 0.10 | 따뜻함 ±100일 때 빨강과 파랑 채널 이득 ±10% |
| `CHROMA_SPAN` | 0.40 | 진하기 ±100일 때 채도 ×0.6~×1.4 |
| `EXPOSURE_SPAN_EV` | 0.50 | 밝기 ±100일 때 ±0.5EV |

순서는 아래와 같다. 값은 실수여도 된다(셀러 슬라이더는 정수, 서버 보정은 실수).

1. 따뜻함: `t = warmth/100 × WARM_END`, `gr = 1+t`, `gb = 1−t`, `n = 1/(LR·gr + LG + LB·gb)`. `r ← r·gr·n`, `g ← g·n`, `b ← b·gb·n`.
2. 진하기: `f = 1 + chroma/100 × CHROMA_SPAN`, `y = LR·r + LG·g + LB·b`, 각 채널 `c ← y + f·(c − y)`.
3. 색역 지키기: 채널이 0 미만이나 1 초과면 휘도 `y`를 유지한 채 채도만 줄인다(`src/lib/toneRender.js`의 `gamutSafe`와 같은 식).
4. 밝기: `gain = 2^(lightness/100 × EXPOSURE_SPAN_EV)`, 세 채널에 곱한다.
5. sRGB로 돌릴 때만 0~1로 자른다.

셀러 슬라이더 범위는 정수 −100~100이다. 화면은 |값| ≤ 3을 0으로 붙인다. 슬라이더 끝값은 사진에 안전하게 옮길 수 있는 한도(4절 상한)에 맞춘 것이다.

## 3. 목표 색 T

- **원본 누끼:** 색상마다 그 색상의 첫 Front 원본 사진에서 판매 의류만 오려낸 투명 PNG(`source`)를 만든다. 옷 영역은 기존 SAM 3 옷 윤곽(`tone_mask`, `garment_color_tune`과 같은 프롬프트와 코디 겹침 빼기)을 쓴다. 기준 색상의 기존 누끼가 준비돼 있으면 재사용해도 된다.
- **원본 색 S:** `source`에서 알파 200 이상 픽셀의 sRGB 8비트 채널별 중앙값 `S8`, `S = linear(S8)`.
- **목표 색:** `T = tone(S, sliders)`. 슬라이더가 모두 0이면 목표가 없다.
- **AI 자동:** 그 색상의 원본 사진(배경 포함)으로 기존 조명 추정(`qwen_flatlay._color_target`의 색 방향만, 밝기는 쓰지 않음)을 돌려 목표 a, b를 얻고, `lightness = 0`에서 `chroma`, `warmth` 정수 격자를 훑어 `tone(S, ·)`의 Oklab a, b가 목표에 가장 가까운 값을 `auto`로 저장한다. 조명을 추정하지 못하면 `auto = null`(화면에서 버튼이 꺼진다).

## 4. 끝 단계 (생성 뒤 반영)

상세페이지 생성 잡에서 모든 컷 생성이 끝난 뒤(`asyncio.gather` 뒤), 결과 저장(`finalize_detail_page_success`) 전에 한 번 돈다.

1. 잡을 시작할 때 읽은 분석의 `garmentColorTargets`에서 목표가 있는 색상만 고른다. 없으면 이 단계는 아무것도 하지 않는다(비용 0).
2. 그 색상의 판매 의류가 보이는 모든 결과 컷(착용컷, 스튜디오컷, 제품 단독컷, 디테일컷)을 모은다. 실존 모델 얼굴이 들어간 컷은 외부 서비스에 보내지 않는 규칙 때문에 `skipped: real_model`로 둔다.
3. 컷마다 옷 윤곽 `m_i`를 따고(코디 겹침 빼기 포함) 가운데 색 `M_i`(3절과 같은 채널별 중앙값)를 잰다. 윤곽 실패 컷은 건너뛴다.
4. `M = 채널별 중앙값(M_i)`. 셀러가 움직인 축만 푼다. 밝기를 움직였으면 `lightness`, 진하기나 따뜻함을 움직였으면 `chroma`와 `warmth`. 움직이지 않은 축은 0으로 고정한다. `tone(M, q)`가 T에 가장 가까운 실수 `q`를 찾는다(밝기는 Oklab L, 색은 Oklab a, b로 잰다).
5. 상한: `tone(M, q)`와 `M`의 차이가 Oklab L 0.10, a·b 거리 0.08을 넘지 않게 `q`를 같은 비율로 줄이고 `capped: true`를 남긴다.
6. 컷마다 윤곽 안 픽셀에 `tone(px, q)`를 건다. 가중치는 `윤곽 알파 × 보호`이고, 보호는 그 픽셀과 `M_i`의 Oklab 거리 0.10 이하에서 1, 0.20에서 0으로 줄어든다(프린트, 머리카락, 다른 옷 보호. 숫자는 로컬 표본 시험으로 조정 가능). 윤곽 밖 픽셀은 원래 바이트 그대로다.
7. 자기 검사: 바꾼 뒤 윤곽 밖이 달라졌거나 결과가 깨졌으면 그 컷만 원본으로 되돌린다.
8. 원본과 윤곽은 내부 자산으로 보관한다(의류 탭, 장수, 크레딧 계산에서 제외. 2차 에디터 다시 맞추기가 비용 0으로 쓴다).
9. 이 단계의 실패는 잡을 실패시키지 않는다. 취소 신호만 그대로 올린다.

추가 색상 컷은 지금처럼 색 입히기, 검사, `garment_color_tune`을 모두 지난 뒤 이 단계를 거친다. 셀러가 그 색상을 손대지 않았으면 이 단계는 그 색상을 건너뛴다.

## 5. 저장 계약

`analysis.garmentColorTargets`는 서버 소유이고 클라이언트의 분석 PATCH로 만들거나 바꿀 수 없다(`_IMMUTABLE_SERVER_OWNED_ANALYSIS_KEYS`). 전용 API만 쓴다. DB 마이그레이션은 없다.

```json
{
  "toneVersion": "garment-color-v1",
  "colors": {
    "<colorId>": {
      "sourceAssetId": "uuid",
      "sourceInput": "원본 Front 자산 id와 바이트 해시",
      "sourceSrgb": [226, 217, 196],
      "auto": { "chroma": -8, "lightness": 0, "warmth": -30 },
      "sliders": { "chroma": 0, "lightness": 0, "warmth": 0 },
      "targetLinear": [0.75, 0.69, 0.55],
      "updatedAt": "2026-11-21T00:00:00Z"
    }
  }
}
```

- 그 색상의 Front 원본이 바뀌면 그 색상 항목을 지운다(`garmentColorEvidence`와 같은 무효화 지점). 색상이 삭제돼도 지운다.
- 재분석(`finalize_analyze_success`)은 이 키를 보존한다.
- 상세 생성 잡은 시작할 때의 값을 쓴다. 생성 중에 바꾼 값은 다음 생성부터 반영된다.

## 6. API

모두 로그인과 프로젝트 소유 확인을 거친다. 스위치 `GARMENT_COLOR_TARGET_ENABLED`(기본 꺼짐)가 꺼져 있으면 GET은 `{"enabled": false, "colors": []}`, 나머지는 404다.

### GET `/v1/projects/{projectId}/garment-color-targets`

외부 호출 없이 저장값만 읽는다.

```json
{
  "enabled": true,
  "toneVersion": "garment-color-v1",
  "colors": [
    {
      "colorId": "c1", "name": "아이보리", "isBase": true,
      "status": "unprepared",
      "reason": null,
      "sourceSrgb": null,
      "auto": null,
      "sliders": { "chroma": 0, "lightness": 0, "warmth": 0 }
    }
  ]
}
```

- `colors` 순서는 기준 색상이 먼저, 나머지는 상품의 색상 순서다. Front 원본이 없는 색상은 `status: "unavailable"`, `reason: "no_front_photo"`.
- `status`: `unprepared`(아직 누끼 안 만듦), `ready`, `unavailable`(`reason`: `no_front_photo`, `mask_failed`, `invalid_image`).

### POST `/v1/projects/{projectId}/garment-color-targets:prepare`

`unprepared` 색상의 누끼, 원본 색, AI 자동 값을 만든다. 색상끼리 병렬, 동기 응답, 같은 입력이면 다시 만들지 않는다. 응답은 GET과 같다. 색상 하나의 실패는 그 색상만 `unavailable`이 된다. 비용은 색상마다 SAM 3 호출 1~2회.

### PUT `/v1/projects/{projectId}/garment-color-targets/{colorId}`

본문 `{"sliders": {"chroma": int, "lightness": int, "warmth": int}}`. 정수 −100~100이 아니면 422, 없는 색상 404, `ready`가 아니면 409. 서버가 T를 계산해 저장하고 그 색상 한 개를 GET의 `colors[]` 원소 모양으로 돌려준다. 모두 0이면 목표를 지운다(`targetLinear: null`).

### GET `/v1/projects/{projectId}/garment-color-targets/{colorId}/source`

`ready` 색상의 누끼 PNG 바이트. 화면이 캔버스로 픽셀을 읽으므로 인증 fetch로 받는 바이트 응답이다(톤 에디터 source와 같은 방식).

## 7. 기록

- 잡 메타데이터 `garmentColorTarget`: 색상마다 `target`, `median`, `params`(q), `capped`, 컷마다 `status`(`applied`, `skipped`, `reverted`), `reason`, 바꾸기 전후 가운데 색.
- 바꾼 결과 자산 메타데이터 `colorTarget`: `originalAssetId`, `maskAssetId`, `params`, `toneVersion`.

## 8. 비용

셀러가 색을 정한 색상에만 든다. 코디보드에서 판을 열 때 색상마다 SAM 3 1~2회(약 0.005~0.01달러), 생성 뒤 컷마다 SAM 3 약 2회(약 0.01달러). 15컷 페이지 약 0.15달러. 손대지 않으면 0이다.

## 9. 알려진 한계

- 실존 모델 얼굴이 든 컷은 반영하지 않는다.
- 무늬와 배색은 가운데 색만 옮긴다. 길이, 핏, 질감은 바꾸지 못한다.
- 4절 상한을 넘는 큰 차이는 상한까지만 옮긴다.
