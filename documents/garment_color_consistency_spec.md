# 끝에서 옷 색 맞추기 모듈 명세

> 문서 상태: 작성일 2026-11-13, 판 이름 `garment-color-consistency-v1`(코드의 `ALGORITHM_VERSION`), 생성 파이프라인 연결 전.

2026-11-15 추가: 1절부터 9절과 맨 아래 쉬운 설명은 기존의 같은 색상 묶음 계약이다. 다른 색상 AI 결과를 운영 흐름에서 맞추는 계약은 10절이다. 같은 색상 묶음의 계산과 상수는 바꾸지 않는다.

- 코드는 `server/app/services/garment_color_consistency.py`, 테스트는 `server/tests/test_garment_color_consistency.py`다(numpy로 만든 합성 그림만 쓴다. 198건, 2026-11-13 통과).
- 이 문서는 지금 코드가 하는 계산을 적은 것이다. 문서와 코드가 다르면 코드가 정답이고 문서를 고친다. 상수는 코드의 이름과 값을 함께 적었고, 값을 바꿀 때는 코드의 상수 한 곳과 이 문서를 같이 고친다.
- 코드 주석의 절 번호는 이 문서가 아니라 옛 작업 명세의 번호다. 주석의 2절은 이 문서의 4.1, 4.2, 4.4절, 3절과 4절은 4.3, 4.5절, 5절은 4.6절, 6절은 4.7절, 7절은 4.8절, 8절은 4.9절이다. 주석의 1.1절은 3.1절이고, 기본값을 모은 10절은 이 문서에서 각 절에 나눠 적었다.

## 1. 무엇을 하는 모듈인가

### 1.1 하는 일

상세페이지에 들어갈 사진을 다 만든 뒤에 도는 안전망이다. 한 색상 옵션의 최종 사진 묶음을 받아서, 옷 색이 혼자 크게 튄 사진만 골라 옷 화소를 기준 색 쪽으로 조금 옮긴다.
- 대부분의 사진이 바이트 그대로 돌아오는 것이 정상이다. 실제 표본 237장(27묶음)을 기본값으로 돌린 기록에서 고친 사진은 4장이었고 모두 밝기만 0.006에서 0.024 옮겼다. 그대로 둔 사진이 118장, 자격이 없어 건너뛴 사진이 115장, 되돌린 사진이 0장, 윤곽 밖에서 바뀐 화소가 0개였다.
- 화소를 목표 색으로 칠하지 않는다. 옷 전체를 같은 규칙으로 옮겨서 주름, 그늘, 무늬가 남는다.
- 옷 윤곽 밖은 한 화소도 바꾸지 않는다. 옮긴 뒤 스스로 검사해서 하나라도 어긋나면 원본을 그대로 돌려준다.
- 어느 단계에서든 조건에 걸린 사진은 원본과 이유 코드를 돌려준다. 예외는 입력이 틀렸을 때만 던진다.

### 1.2 하지 않는 일

- **색을 갈아입히지 않는다.** 한 번에 옮기는 한도가 색 방향 0.02, 밝기 0.10(장면 사진은 0.08)이라 아이보리를 네이비로 바꿀 수 없다. 다른 색상의 컷은 그 색상의 원본 사진으로 새로 만든다.
- **길이와 핏은 못 고친다.** 옷 화소의 색만 옮기고 모양은 건드리지 않는다.
- 윤곽을 따지 않는다. 윤곽은 입력으로 받는다. 저장, 재시도, 미리보기 교체도 하지 않는다.
- 다시 그리지 않는다. 다시 그릴 만한 사진에 표시(`redraw_candidate`)만 단다.
- 여러 색 옷은 고치지 않는다(4.5절).

## 2. 지금 상태

- **어디에도 연결돼 있지 않다.** 이 모듈을 부르는 운영 코드가 없고 테스트만 부른다.
- **스위치가 없다.** 켜고 끄는 설정도, 환경 변수도 없다. 기본값은 모두 모듈 상수다.
- 유료 호출과 네트워크 호출이 없다. numpy와 OpenCV 계산뿐이다. 낱말 표 때문에 운영 `tone_mask` 모듈을 import 한다(4.1절).
- 기록을 저장하는 자리가 없다. 함수가 돌려주는 값이 전부다.
- 오너 확정(2026-11-13). 허용 폭, 제품 사진의 허용 폭, 세기, 밝게 고치는 방법, 밝기 한도, 셀러가 색을 조절했을 때의 허용 폭, 색 방향 한도 0.02는 지금 기본값을 유지한다. 톤만 다른 두 색 옷은 상품 분석의 무늬 정보를 입력으로 받는 방향이고, 윤곽 낱말 확인 실험(유료)은 연결 전에 한 번 하되 실행 직전에 따로 승인을 받는다.

## 3. 입력과 출력

### 3.1 낱말과 색 공간

| 낱말 | 뜻 |
|---|---|
| 색상 옵션 | 한 상품의 색 하나. 한 번의 호출은 한 색상 옵션의 사진 묶음이다 |
| 윤곽 | 사진에서 팔리는 옷이 차지한 화소를 참과 거짓으로 표시한 배열 |
| 밝기 | Oklab L (0에서 1) |
| 색 방향 차이 | 두 색의 (a, b) 사이 거리 `hypot(da, db)`. 코드의 `chroma_distance` |
| 진하기 | `hypot(a, b)` |
| 가중 거리 | `sqrt((0.35 * dL)^2 + da^2 + db^2)`. 코드의 `color_distance`, 0.35는 `LIGHTNESS_DISTANCE_WEIGHT` |
| 옷 가운데 색 g | 4.3절에서 잰 사진 한 장의 옷 색 (g_L, g_a, g_b) |
| 폭 비례 scale | `사진 폭 / 1536`. 1536은 `REFERENCE_WIDTH`이고, 길이 상수는 모두 폭 1536 기준 화소다. 바닥값인 `ERODE_MIN_PX`만 비례시키지 않는 실제 화소 수다 |
| 날아간 화소, 뭉개진 화소 | 세 채널 모두 250 이상(`BLOWN_MIN`), 세 채널 모두 5 이하(`CRUSHED_MAX`) |

색은 모두 Oklab으로 다룬다(sRGB에서 바꾸는 함수는 `rgb_to_oklab`). 변환 상수와 가중 거리 식은 운영 `server/app/agents/garment_color_evidence.py`의 `_lab`, `_color_distance`와 같고 테스트가 대조한다.
```
c   = rgb8 / 255
lin = c / 12.92                       (c <= 0.04045)
    = ((c + 0.055) / 1.055) ^ 2.4     (그 밖)
l = cbrt(0.4122214708 R + 0.5363325363 G + 0.0514459929 B)      # R, G, B 는 lin
m = cbrt(0.2119034982 R + 0.6806995451 G + 0.1073969566 B)
s = cbrt(0.0883024619 R + 0.2817188376 G + 0.6299787005 B)
L = 0.2104542553 l + 0.7936177850 m - 0.0040720468 s
a = 1.9779984951 l - 2.4285922050 m + 0.4505937099 s
b = 0.0259040371 l + 0.7827717662 m - 0.8086757660 s
```
거꾸로 갈 때는 두 행렬의 역행렬을 쓰고 세제곱한 뒤 sRGB로 바꾼다. 크기 `|x|`가 0.0031308 이하면 `12.92 |x|`, 아니면 `1.055 |x|^(1/2.4) - 0.055`이고, 부호를 살린 채 255를 곱한다. `oklab_to_srgb`는 그 값을 0에서 255로 잘라 돌려주고, 옮기는 계산은 색역 처리(4.8절)를 마친 뒤에 자른다.

### 3.2 입력

부르는 함수는 `correct_color_option(photos, *, upload_color=None, seller_color=None)` 하나다. `photos`는 사진 한 장을 담은 `PhotoInput`의 목록이다.

| 항목 | 모양 | 설명 |
|---|---|---|
| `key` | 문자열 | 기록에서 사진을 가리키는 이름. 묶음 안에서 겹치면 안 된다 |
| `image` | uint8, (H, W, 3), sRGB | 이 모듈이 고친 적 없는 원본 |
| `masks` | bool (H, W) 한 장, 또는 그런 배열의 목록 | 윤곽 후보. 없으면 빈 목록. 운영 톤 마스크의 구멍 메우기(`fill_holes`)와 정돈(`tidy`)을 거치지 않은 윤곽을 넣는다 |
| `kind` | `studio`, `product`, `styling` (`PHOTO_KINDS`) | 스튜디오 사진, 제품 사진(옷만 찍은 사진), 장면 사진 |
| `color_option_id` | 문자열 | 한 호출의 사진은 모두 같아야 한다 |
| `clothing_type`, `sub_category` | 문자열 또는 없음 | 기록에만 남는다. 묶음 계산에는 쓰지 않는다 |
| `upload_color` (묶음) | (L, a, b) 또는 없음 | 올린 옷 사진의 색 R. 코디보드가 낸 값이다(밝기는 올린 사진의 옷 밝기, 색 방향은 조명 색을 걷은 값). 후보 고르기(4.1절)와 기준 색 대조(4.6절)에 쓴다. 이 모듈은 올린 사진을 직접 재지 않는다 |
| `seller_color` (묶음) | (L, a, b) 또는 없음 | 셀러가 조절한 색. 있으면 절대 기준이다(4.6절) |

### 3.3 출력과 예외

- `BatchResult.photos`는 입력과 같은 순서의 `PhotoResult(image, record)` 목록이다. 고치지 않은 사진의 `image`는 입력 배열 그 자체이고, 고친 사진만 새 배열이다.
- `BatchResult.record`는 묶음 기록이다(6.2절). 기록은 숫자, 문자열, 참과 거짓, None, 목록, 사전만으로 돼 있어 JSON으로 그대로 저장할 수 있다.
- 예외(`ValueError`)는 아래처럼 입력이 틀렸을 때만 던진다. 그 밖의 "고치면 안 되는 경우"는 건너뜀 결과로 돌려준다.
  - 사진 목록이 비었다. `color_option_id`가 섞여 있다. `key`가 겹친다. `kind`가 세 값 밖이다.
  - `image`가 uint8의 (H, W, 3)이 아니거나 높이나 폭이 0이다.
  - 윤곽이 bool의 (H, W)가 아니거나, 높이나 폭이 0이거나, 사진과 크기가 다르다. 크기를 맞춰 주지 않는다.
  - `upload_color`나 `seller_color`가 유한한 숫자 셋이 아니거나, 밝기가 0 미만 또는 1 초과다. 순수 검정(밝기 0)은 정상 값이다. 밝기 범위 검사는 묶음 함수에만 있다.

### 3.4 부르는 쪽이 지킬 약속

- **한 사진에 한 번만 적용한다.** 입력은 늘 원본이어야 한다. 올렸다 내리면 가운데 밝기는 돌아오지만 화소는 돌아오지 않는다(밝기 0.06 이동에서 평균 2.1에서 5.6단계 어긋났다).
- 같은 입력에는 같은 출력이 나온다. 무작위를 쓰지 않고, 화소를 덜어 쓰지 않고 전부 쓴다.
- 연결할 때는 묶음 함수만 부른다. 나머지 공개 함수(이 문서에 코드 이름으로 적은 함수들)는 테스트와 평가가 단계별로 부르는 용도다.

## 4. 계산 순서

코드가 도는 차례는 이렇다.
1. 사진마다 후보를 줄인다. 후보가 하나인 사진부터 윤곽을 다듬고, 품질을 검사하고, 통과하면 옷 색을 잰다(4.1, 4.2, 4.3절).
2. 올린 옷 색을 믿을지 정한 뒤, 후보가 둘 이상인 사진의 윤곽을 고르고 같은 일을 한다(4.1절).
3. 옷 색을 잰 사진을 형제 사진과 견준다(4.4절).
4. 여러 색 옷인지 묶음으로 판정하고 기준 색을 정한다(4.5, 4.6절).
5. 남은 사진마다 옮길 양을 정하고(4.7절), 옮길 것이 있는 사진만 옮긴 뒤(4.8절) 자기 검사를 한다(4.9절).

### 4.1 윤곽 고르기

**낱말(`mask_prompts`).** 옷 종류에서 윤곽을 딸 낱말 둘(종류 낱말, 예비 낱말)을 돌려준다. 운영 `tone_mask.prompts_for`를 그대로 부른다. 돌려주기만 하고 보내지 않는다. 종류 낱말은 옷 종류가 `top`이면 top, `outer`면 outerwear, `dress`면 dress다. `bottom`이면 pants이고, 세부 종류에 skirt, 스커트, 치마 가운데 하나가 들어 있으면 skirt다. 옷 종류가 없거나 모르는 값이면 top이다. 예비 낱말은 언제나 clothing이다.

**후보 줄이기(`reduce_candidates`).**
1. 화소 수가 화면의 2%(`CANDIDATE_MIN_AREA_FRAC = 0.02`) 미만인 후보를 버린다.
2. 큰 것부터 보면서, 이미 남긴 후보와 겹침(IoU)이 `CANDIDATE_DEDUPE_IOU = 0.90` 이상이면 버린다.
3. 남은 후보가 0개면 `no_candidate`로 건너뛴다. 1개면 그것을 쓴다(`selection = "single"`).

**후보가 둘 이상 남았을 때.** 후보가 하나인 사진들을 먼저 끝까지 잰 뒤에 본다.
1. `upload_color`가 없으면 `ambiguous_no_upload_color`로 건너뛴다.
2. 올린 색을 믿을 수 있는지 본다(`upload_color_trust`). 후보가 1개이고 품질 검사(4.2절)를 통과한 사진들의 옷 가운데 색 g와 R의 가중 거리를 모아, 그 가운데값이 `UPLOAD_TRUST_MAX = 0.07` 이하면 믿는다. 그런 사진이 한 장도 없으면 믿지 않는다. 믿지 않으면 `upload_color_untrusted`로 건너뛴다.
3. 후보마다 속(4.2절의 core) 화소의 축별 가운데값을 한 번 내고 R과의 가중 거리를 잰다(`pick_by_upload_color`). 속이 비어 못 재는 후보는 가장 먼 것으로 친다. 가장 가까운 후보와 둘째의 거리 차가 `CANDIDATE_MARGIN = 0.02` 이상이면 가장 가까운 후보를 쓴다(`selection = "closest_to_upload"`). 아니면 `ambiguous_candidates`로 건너뛴다.

근거. 같은 사진의 팔리는 옷과 다른 옷 85쌍에서 틀림 0, 모호 12였다. 색이 비슷한 옷끼리는 색으로 못 가르고, 그런 사진은 여유 0.02에 걸려 빠진다. 의도한 동작이다. 0.07은 옷 9벌 중 2벌이 넘은 값이다. 윤곽 모델의 점수는 쓰지 않는다.

**윤곽 다듬기(`trim_specks`).**
- 8방향으로 이어진 덩어리 가운데 화소 수가 윤곽 넓이의 0.05%(`SPECK_MAX_SHARE = 0.0005`) 미만인 것만 윤곽에서 뺀다. 가장 큰 덩어리는 지우지 않는다.
- 구멍을 메우지 않는다. 가장 큰 덩어리만 남기지도 않는다. 닫기, 흐리기, 넓히기도 하지 않는다. 윤곽 안에 갇힌 빈 곳은 옷 위의 손이나 팔 사이 배경이었고(넓이가 2% 넘게 늘어난 10곳 모두), 0.05%에서 2% 사이 조각 19개는 모두 진짜 옷이었다.
- 뒤의 검사와 재기는 모두 다듬은 윤곽으로 한다.

### 4.2 윤곽 품질

윤곽이 옷을 제대로 땄는지 숫자로 본다. 걸리면 그 사진은 건너뛴다. 숫자를 재는 법은 이렇다(`measure_mask_quality`).
```
area_frac    = 윤곽 화소 수 / (H * W)
fragments    = 화소 수가 윤곽 넓이의 1% (FRAGMENT_MIN_SHARE = 0.01) 이상인 덩어리 수 (8방향)
k            = max(ERODE_MIN_PX = 5, round(ERODE_PX = 9 * scale))
core         = 윤곽을 k x k 네모로 침식한 것 (속)
core_share   = core 화소 수 / 윤곽 화소 수
core_pixels  = core 화소 수
core_px_1536 = core 화소 수 / scale^2

아래는 core 화소가 QUALITY_MIN_PIXELS = 200 개 이상일 때만 잰다. 아니면 None 이다.
med          = core 화소의 Oklab 축별 가운데값 (한 번 구한 값)
far_share    = core 화소 중 med 와의 가중 거리가 FAR_DISTANCE = 0.06 을 넘는 비율
w_spread90   = 그 가중 거리의 90번째 백분위
ab_spread90  = core 화소와 med 의 색 방향 차이의 90번째 백분위
rim          = 윤곽 안이면서, 윤곽을 3k x 3k 네모로 침식한 것의 밖 (가장자리 띠)
rim_in_far   = rim 화소 중 med 와의 가중 거리가 0.06 을 넘는 비율     (rim 이 200개 미만이면 None)
rim_excess   = rim_in_far - far_share                                 (rim 이 200개 미만이면 None)
```
침식은 OpenCV 기본 동작을 쓴다. 윤곽이 사진 가장자리에 닿은 변에서는 깎이지 않는다.

판정은 표의 순서대로 보고(`judge_mask_quality`), 걸린 코드를 모두 `quality_codes`에 적는다. 맨 앞 코드가 건너뛴 이유다.

| 순서 | 코드 | 조건 | 상수 |
|---|---|---|---|
| 1 | `mask_tiny` | area_frac < 0.02 | `MASK_AREA_MIN` |
| 2 | `mask_frame_fill` | area_frac > 0.75. 사진 종류와 상관없다 | `MASK_AREA_MAX` |
| 3 | `mask_thin` | core_share < 0.85 또는 core_px_1536 < 40,000 | `CORE_SHARE_MIN`, `CORE_PX_1536_MIN` |
| 4 | `mask_fragments` | fragments > 3 | `FRAGMENTS_MAX` |
| 5 | `mask_too_few_pixels` | 색 숫자가 None 이다(core 화소 200개 미만). 여기서 판정을 끝낸다 | `QUALITY_MIN_PIXELS` |
| 6 | `mask_edge_spill` | rim_excess 가 있고 0.33 을 넘는다 | `RIM_EXCESS_MAX` |
| 7 | `mask_mixed_color` | far_share > 0.42 또는 w_spread90 > 0.14 | `FAR_SHARE_MAX`, `W_SPREAD90_MAX` |

- `mask_tiny`는 묶음 함수에서 드물다. 후보가 2%를 겨우 넘었다가 조각이 지워져 2% 아래로 내려갈 때만 나온다. `mask_too_few_pixels`가 맨 앞 이유가 되는 것은 폭 109화소 미만인 사진뿐이다. 그보다 큰 사진은 `mask_thin`이 먼저 걸린다.
- 근거. 문턱은 좋은 윤곽 표본의 최댓값 바로 위에 뒀다(far_share 0.354, w_spread90 0.1215, rim_excess 0.296, 속 비율 최소 0.905). 좋은 윤곽이 잘못 걸리는 비율은 새 옷에서 1%에서 3%로 본다. 폭의 1%만큼 번진 윤곽을 잡는 비율은 색이 진한 옷 99%, 무채색에 가까운 옷 77%다. 화면을 꽉 채운 근접 사진은 9개 중 3개에 문제가 있었고 검사가 못 잡아서 통째로 뺀다.

### 4.3 옷 색 재기 (`measure_garment_color`)

품질 검사를 통과한 사진만 잰다. 그늘진 곳도 같은 천으로 보려고, 옷 가운데 색을 화소의 밝기 비율만큼 줄여서 색 방향을 견준다. 범위는 `JUDGE_CHROMA_RANGE = (0.04, 0.08)`, `JUDGE_LIGHTNESS_RANGE = (0.30, 0.45)`, 백분위는 `CENTER_WIDTH_PERCENTILES = (35, 65)`다.
```
F(x; lo, hi) = 1 - s^2 * (3 - 2 * s),   s = clip((x - lo) / (hi - lo), 0, 1)      # lo 이하에서 1, hi 이상에서 0

t  = clip(L / max(g_L, 1e-6), 0, 1.5)                     # 화소의 밝기 비율
dc = hypot(a - t * g_a, b - t * g_b)                      # 그늘을 감안한 색 방향 거리
판단용 같은 천 정도 = F(dc; 0.04, 0.08) * F(g_L - L; 0.30, 0.45) * F(L - g_L; 0.30, 0.45)

px    = core 화소 전부의 Oklab
g1    = px 의 축별 가운데값                                              # 기록 first_pass_center
keep  = 판단용 같은 천 정도(px, g1) >= SAME_FABRIC_MIN = 0.5
g     = keep 이 REFINE_MIN_PIXELS = 200 개 이상이면 px[keep] 의 축별 가운데값, 아니면 g1     # 기록 center
same  = px 중 판단용 같은 천 정도(px, g) >= 0.5 인 비율
sp    = 그 화소들. 200개 이상일 때만 cw_c 와 cw_l 을 잰다. 아니면 None 이다
u, v  = sp 의 (a - t * g_a), (b - t * g_b)
cw_c  = hypot(u 의 65번째 백분위 - 35번째 백분위, v 의 65번째 백분위 - 35번째 백분위)
cw_l  = sp 의 L 의 65번째 백분위 - 35번째 백분위                           # 기록만 한다
blown_frac, crushed_frac = 윤곽 안 화소 전부에서 날아간 화소 비율, 뭉개진 화소 비율
```
- 속 화소를 전부 쓴다. 일정 간격으로 뽑지 않는다. 사진에 2화소 주기의 밝기 무늬가 있어 두 칸마다 뽑으면 가운데 밝기가 치우친다(최대 0.0143).
- 날아간 화소를 빼지 않는다(빼고 재면 최대 0.035에서 0.057 틀린다). 가운데값을 두 번 구하는 까닭은 줄무늬나 그늘에 덜 끌려가게 하려는 것이다. 평균이나 색 무리 나누기는 쓰지 않는다.

### 4.4 형제 대조 (`check_against_siblings`)

품질 검사만으로는 다른 옷을 고른 윤곽을 못 막는다. 그래서 같은 묶음의 다른 사진과 견준다. 형제는 품질 검사를 통과해 옷 색을 잰 다른 사진 전부다(자기는 뺀다). 형제 대조에서 떨어진 사진도 다른 사진의 형제로는 센다.
1. 형제가 2장(`SIBLINGS_MIN = 2`) 미만이면 `no_sibling_check`로 건너뛴다.
2. 형제와 견준 품질. 형제들의 같은 숫자의 가운데값보다 아래 값 넘게 나쁘면 코드를 `quality_codes`에 더한다. 못 잰 숫자(None)는 견주지 않는다.
   - `mask_mixed_vs_siblings`: far_share 차이 > 0.28(`SIBLING_FAR_SHARE_MAX`) 또는 w_spread90 차이 > 0.07(`SIBLING_W_SPREAD90_MAX`) 또는 ab_spread90 차이 > 0.022(`SIBLING_AB_SPREAD90_MAX`).
   - `mask_edge_vs_siblings`: rim_in_far 차이 > 0.33(`SIBLING_RIM_FAR_MAX`).
3. 형제 색 거리. S는 형제들의 g의 축별 가운데값이다. `sibling_d_l = g_L - S_L`(부호 있음), `sibling_d_ab`는 g와 S의 색 방향 차이다.
4. 이유. 2번의 코드가 있으면 그 맨 앞 코드다. 없고 `|sibling_d_l| > 0.20`(`SIBLING_LIGHTNESS_MAX`)이거나 `sibling_d_ab > 0.05`(`SIBLING_CHROMA_MAX`)면 `off_siblings`로 건너뛰고 `redraw_candidate` 표시를 단다.

근거. 눈으로 틀린 윤곽 7개 중 품질 검사만으로 걸린 것은 3개였고, 형제 색 거리를 더하면 6개였다.

### 4.5 여러 색 옷

옷이 두 색 이상이면 "옷 가운데 색"이 구도에 따라 이 색에서 저 색으로 건너뛴다. 그러면 멀쩡한 사진을 튀었다고 볼 수 있어서, 여러 색 옷은 묶음 전체를 쉰다.

사진 한 장이 "여러 색"인 조건은 셋 중 하나다(4.3절의 숫자). 같은 천 화소 sp가 200개 미만이다. `same < 0.90`(`SAME_SHARE_MIN`)이다. `cw_c > 0.025`(`CENTER_WIDTH_CHROMA_MAX`)다. 실제 한 벌 옷 345장의 same 최소가 0.9203, cw_c 최대가 0.0192였다.

묶음 판정. 아래 사진이 한 장이라도 있으면 그 색상 옵션은 기준 색을 잡지 않고, 형제 대조까지 통과한 사진을 모두 `multicolor_garment`로 건너뛴다. 이 판정은 `seller_color`가 있어도 먼저 한다. 걸린 사진의 `key`는 묶음 기록 `multicolor.keys`에 남는다.
- 형제 대조까지 통과했고 위 조건에 든 사진.
- `quality_codes`에 `mask_mixed_color`나 `mask_mixed_vs_siblings`가 있는 사진. 그 사진 자신은 먼저 걸린 이유를 그대로 갖는다.

거르는 것은 진한 둘째 색이다. 둘째 색이 바탕과 밝기 0.50 이상 또는 진하기 0.08 이상 다르고 넓이가 10%에서 11%를 넘으면 걸린다(합성 864개 중 860개). 밝기가 크게 다른 둘째 색은 `mask_mixed_color`가 먼저 잡는데, 묶음 전체를 쉬는 결과는 같다. 가는 줄무늬, 톤이 비슷한 줄, 작은 프린트, 로고 티는 빼지 않는다. 한 색으로 맞춰도 줄과 바탕이 함께 맞춰졌다.

### 4.6 기준 색 (`resolve_reference`)

기준 색 T는 색상 옵션마다 하나다. 위에서부터 먼저 맞는 것을 쓴다.

**가. 셀러가 조절한 색이 있다.** T는 `seller_color` 그대로다(`reference_source = "seller"`). 스튜디오 사진 수도, 올린 색도 보지 않는다.

**나. 스튜디오 사진들의 가운데 색 A.** 쓸 수 있는 사진은 `kind`가 `studio`이고 형제 대조까지 통과한 사진이다. 제품 사진과 장면 사진은 기준에 쓰지 않는다.
```
1. 쓸 수 있는 스튜디오 사진이 STUDIO_MIN = 3 장 미만이면 기준 없음 (no_reference_few_studio)
2. A0 = 그 사진들의 g 의 축별 가운데값
3. A0 와의 가중 거리가 STUDIO_OUTLIER_DISTANCE = 0.03 을 넘는 사진을 뺀다 (기록 studio.dropped_keys)
4. 남은 사진이 3장 미만이면 기준 없음 (no_reference_studio_disagree)
5. A = 남은 사진들의 g 의 축별 가운데값 (기록 studio.center)
```
**다. 올린 옷 색 R과 대조.** A는 이 대조를 통과해야 기준이 된다.
```
upload_color 가 없으면 기준 없음 (no_reference_no_upload_color)

s     = clip(A_L / max(R_L, 1e-6), 0.5, 2.0)                           # UPLOAD_CHROMA_SCALE_RANGE
k     = clip((A_ab · R_ab) / (R_ab · R_ab), min(1, s), max(1, s))      # R_ab · R_ab 가 1e-12 미만이면 k = 1
R*_ab = k * R_ab
d     = hypot(A_ab - R*_ab)                                            # 기록 upload_check.d_ab
A_L - R_L 은 기록만 한다                                                # 기록 upload_check.l_diff

d > 0.05 (UPLOAD_CHROMA_MAX)    기준 없음 (reference_off_upload). 묶음과 사진에 redraw_candidate 표시
d > 0.02 (UPLOAD_CHROMA_KEEP)   T_ab = R*_ab + (A_ab - R*_ab) * 0.02 / d.  기록 upload_check.pulled = True
그 밖                           T_ab = A_ab
T_L = A_L                       언제나. reference_source = "studio"
```
- **올린 사진의 밝기로 기준을 고치지 않는다.** 올린 사진의 밝기는 찍은 조명을 따라간다. 실제 12벌에서 스튜디오 가운데 색과의 밝기 차이가 -0.054에서 +0.28까지 흩어졌다.
- k의 뜻. 밝게 찍힐수록 진하기가 밝기 비율만큼 커지는 옷도, 거의 그대로인 옷도 있었다. 그래서 올린 색의 진하기 배율을 1배와 밝기 비율 s 사이에서 A에 가장 가까운 값으로 고른다. 색상 각도 차이는 배율로 줄지 않으므로 봐주지 않는다.
- 후보 고르기에서 믿지 않기로 한 올린 색(4.1절)도 이 대조에는 그대로 쓴다.
- 기준이 없으면 형제 대조까지 통과한 사진을 모두 그 이유 코드로 건너뛴다. 실제 12벌에 대 본 기록은 그대로 11벌, 끌어오기 1벌(기준이 움직인 크기 0.00005), 기준 없음 0벌이다.

### 4.7 옮길 양과 한도 (`plan_shift`)

밝기와 색 방향을 따로 판단한다. 허용 폭 안이면 건드리지 않고, 넘으면 허용 폭 끝까지만 되돌린다. 한 번에 옮기는 크기에는 한도가 있다. 사진 종류별 값은 `SHIFT_LIMITS`다.

| 사진 종류 | 색 방향 허용 폭 `chroma_tolerance` | 밝기 허용 폭 `lightness_tolerance` | 색 방향 한도 `chroma_cap` | 밝기 한도 `lightness_cap` |
|---|---|---|---|---|
| `studio` | 0.022 | 0.053 | 0.02 | 0.10 |
| `styling` | 0.035 | 0.13 | 0.02 | 0.08 |
| `product` | 0.035 | 0.13 | 0.02 | 0.10 |

그 밖의 상수는 `STRENGTH_LAMBDA = 0.0`, `MIN_SHIFT = 0.003`, `BLOWN_SKIP_FRAC = 0.01`, `LIGHTNESS_RANGE = (0.05, 0.97)`, `BRIGHTEN_CEILING = 0.99`, `CLIP_GROWTH_MAX = 0.01`, `CLIP_GUARD_ITERATIONS = 12`다.
```
need(e, b) = 0                                     (e <= b)      # 허용 폭 안은 그대로
           = e - b * exp(-λ * (e - b) / b)         (e >  b)      # λ = STRENGTH_LAMBDA. 0 이면 e - b

grow(d_l, d_ab) = max(옮긴 뒤 날아간 화소 비율 - 원본의 비율, 옮긴 뒤 뭉개진 화소 비율 - 원본의 비율)
```
grow의 분모는 윤곽 안 화소 전부다. 4.8절의 실제 적용(가중치, 색역 처리, 8비트 반올림)과 똑같이 옮겨서 잰다. 코드의 `shift_clip_growth`다. 밝기만 옮겼을 때의 값을 재는 `lightness_clip_growth`는 같은 계산에 색 방향 이동 (0, 0)을 넣은 것이다.

**밝기.**
```
e_l = g_L - T_L
n_l = need(|e_l|, 밝기 허용 폭)
d_l = -sign(e_l) * min(n_l, 밝기 한도)            # n_l 이 한도를 넘으면 capped_lightness 표시
```
`d_l`이 0이 아니면 아래를 차례로 본다. 1번이나 2번에 걸리면 거기서 끝난다.
1. 원본의 날아간 화소 비율이 1%(`BLOWN_SKIP_FRAC`)를 넘으면 `d_l = 0`, `lightness_skipped_blown` 표시.
2. g_L이 0.05 이하이거나 0.97 이상이면 `d_l = 0`, `lightness_skipped_extreme` 표시.
3. 밝힐 때는 `d_l = min(d_l, 0.99 - g_L)`.
4. `|d_l| < 0.003`이면 `d_l = 0`(표시 없음).
5. `grow(d_l, (0, 0)) > 0.01`이면 0과 `d_l` 사이를 이분법 12회로 좁혀 `grow <= 0.01`인 쪽 값을 쓴다. `clip_guard_reduced` 표시를 달고 줄이기 전 값을 `d_l_before_clip_guard`에 남긴다. 줄인 뒤 `|d_l| < 0.003`이면 `d_l = 0`, `lightness_skipped_clip_guard` 표시.

**색 방향.** 밝기를 옮기면 진하기가 같은 비율로 따라간다(4.8절). 그래서 밝기를 옮긴 뒤의 예상 가운데 색에서 판단한다.
```
gain = (g_L + d_l) / g_L                    # d_l = 0 이면 1
v    = gain * g_ab - T_ab
e_c  = |v|
n_c  = need(e_c, 색 방향 허용 폭)
m    = min(n_c, 색 방향 한도)               # n_c 가 한도를 넘으면 capped_chroma 표시
d_ab = -(v / e_c) * m                       # m 이 0 이거나 0.003 미만이면 (0, 0)
```
`d_ab`가 (0, 0)이 아니면 이어서 실제로 적용할 이동 전체로 날아감과 뭉개짐을 다시 잰다. `grow(d_l, d_ab) > 0.01`이면 밝기는 두고, `d_ab`에 곱할 비율 q를 0과 1 사이에서 이분법 12회로 찾아 `grow <= 0.01`인 쪽 값을 쓴다. `q * m < 0.003`이면 `d_ab = (0, 0)`이다. `clip_guard_reduced` 표시를 달고 줄이기 전 값을 `d_ab_before_clip_guard`에 남긴다.

**결과.**
- `d_l = 0`이고 `d_ab = (0, 0)`이면 사진을 건드리지 않는다(`untouched`). 이유는 `lightness_skipped_`로 시작하는 표시가 있으면 그것, 없고 색 방향을 날아감과 뭉개짐 한도 때문에 0으로 줄였으면 `clip_guard_reduced`, 그 밖에는 `within_tolerance`다. 옮길 양이 0.003보다 작아서 옮기지 않은 사진도 `within_tolerance`다.
- 하나라도 0이 아니면 옮긴다. 밝기가 막혀도 색 방향은 옮길 수 있고, 그때 막힌 까닭은 표시로만 남는다.
- 옮기는 한도(`lightness_cap`, `chroma_cap`)에 걸린 사진은 그 한도만큼만 옮기고 표시한다. 옮긴 뒤에도 허용 폭 밖에 남는다.

근거. 허용 폭은 오너가 같아 보인다고 한 쌍 차이의 최댓값이다(스튜디오끼리 11쌍에서 색 방향 0.0221과 밝기 0.0531, 스튜디오와 장면 5쌍에서 0.0350과 0.1316). 오너가 본 쌍에 제품 사진이 없어 제품 사진은 넓은 폭을 쓴다. 색 방향 한도 0.02는 0.04에서 흠 다섯 가지가 보여서 낮춘 값이고, 밝기 한도 0.10은 0.15에서 색역 밖 화소가 뛰어서 둔 값이다. 날아감과 뭉개짐 한도를 넘을 때 절반으로 줄이는 방식은 요청을 키웠는데 적용이 줄어드는 사진이 37장 중 10장이라 쓰지 않는다.

### 4.8 옮기기 (`apply_shift`)

**가중치(`move_weight`).** 화소마다 얼마나 옮길지를 0에서 1로 정한다. 같은 천은 모두 1로 똑같이 옮기고, 색이 다른 것만 그대로 둔다. 판단용(4.3절)보다 범위가 넓고 밝기를 차이가 아니라 비율로 잰다. 햇빛을 옆에서 받은 옷이 둘로 갈라지지 않게 하려는 것이다.
```
t_raw = L / max(g_L, 1e-6),   t = clip(t_raw, 0, 1.5)
dc    = hypot(a - t * g_a, b - t * g_b)
sf    = F(dc; 0.04, 0.08)                     # APPLY_CHROMA_RANGE
      * (1 - F(t_raw; 0.30, 0.45))            # APPLY_DARK_RATIO.  비율 0.45 이상 1, 0.30 이하 0
      * F(t_raw; 2.2, 3.0)                    # APPLY_LIGHT_RATIO. 비율 2.2 이하 1, 3.0 이상 0

sigma = WEIGHT_BLUR_PX = 1.0 * scale
num   = 가우시안 흐림(윤곽 안은 sf, 밖은 0; sigma)
den   = 가우시안 흐림(윤곽; sigma)
depth = cv2.distanceTransform(윤곽, DIST_L2, 3)                 # 윤곽 밖까지의 거리
inner = clip(depth / max(1, FEATHER_PX = 2.0 * scale), 0, 1)
w     = inner * num / max(den, 1e-6)          (윤곽 안)
      = 0                                     (윤곽 밖. 정확히 0)
```
폭 1536인 사진에서 경계의 가중치는 윤곽 바로 안쪽 화소가 0.4775, 둘째가 0.955, 셋째부터 1이다. 사진 가장자리는 윤곽 밖으로 치지 않는다.

**밝기(`lightness_ratio`).** w가 0보다 큰 화소의 Oklab 세 좌표에 같은 배율 r을 곱한다. 진하기가 밝기와 같은 비율로 따라간다.
```
어둡게 (d_l < 0):   r = (g_L + d_l) / g_L                   # 모든 화소에 같은 배율
밝게   (d_l > 0):   goal = min(g_L + d_l, 0.99),  gain = goal / g_L
                    bend = gain * (1 - g_L) / (1 - goal)
                    L <= g_L:  r = gain                     # 옷 가운데보다 어두운 화소는 이득 그대로
                    L >  g_L:  h  = clip((1 - L) / (1 - g_L), 0, 1)
                               L' = 1 - (1 - goal) * h / (bend - (bend - 1) * h)
                               r  = L' / max(L, 1e-9)       # 밝은 화소만 흰색 쪽으로 눌린다
```
밝히는 곡선은 옷 가운데에서 값과 기울기가 이어지고 1을 넘지 않는다. 검은 글자와 그늘이 바탕보다 더 밝아지지 않는다.

**색 방향.**
```
follow = clip(L / max(g_L, 1e-6), 0, 1)       # L 은 옮기기 전 밝기. 그늘은 덜, 바탕보다 밝은 곳은 바탕만큼
(a, b) 에 follow * d_ab 를 더한다             # 밝기 배율을 곱한 다음에
```
**색역(`fit_to_gamut`).**
1. 밝기를 0과 1 사이로 자른다.
2. sRGB 실수(0에서 255, 자르기 전)로 바꿔, 어느 채널이든 0 아래나 255 위로 반 단계(`GAMUT_TOLERANCE_8BIT = 0.5`) 넘게 나가면 색역 밖이다.
3. 색역 밖 화소는 같은 밝기에서 (a, b)에 곱할 배율을 0과 1 사이에서 이분법 12회(`GAMUT_ITERATIONS`)로 찾아, 색역 안에 드는 쪽 값을 쓴다. 채널을 자르지 않으므로 색상각이 유지된다.

**섞어서 쓰기.** 결과 배열은 원본을 복사해서 만든다. 윤곽 밖 화소의 자리에는 쓰지 않는다.
```
moved = 색역 처리한 색의 sRGB 실수를 0에서 255로 자른 것
out   = clip(rint(orig * (1 - w) + moved * w), 0, 255)        # 윤곽 안이고 w > 0 인 화소에만 쓴다
```
쓰지 않는 방식은 밝기에 더하기, 밝기에만 곱하고 색 방향은 두기, sRGB 채널 감마, 양끝을 고정한 곡선, 바탕색 거리로 매끄럽게 줄어드는 가중치, 계단식으로 진하기 줄이기다. 모두 검증에서 흠이 확인됐다.

### 4.9 자기 검사 (`self_check`)

화소를 옮긴 사진마다 아래를 순서대로 본다. 먼저 실패한 것 하나가 이유가 되고, 그 사진은 원본을 그대로 돌려준다(`reverted`). 되돌린 사진의 `result` 기록은 시도한 결과의 숫자다.

1. 윤곽 밖. 윤곽 밖 화소 전부가 원본과 바이트가 같아야 한다. 가장자리 띠를 빼고 보지 않는다. 실패 코드는 `reverted_outside_changed`다.
2. 날아감과 뭉개짐. 결과 사진에서 잰 grow가 0.01(`CLIP_GROWTH_MAX`) 이하여야 한다. 실패 코드는 `reverted_clip_guard`다.
3. 다시 재기. 결과 사진의 옷 가운데 색이 아래 식의 예상 색에 가까워야 한다. 실패 코드는 `reverted_shortfall`이다.

```
g2     = 결과 사진을 4.3절과 같은 방법으로 다시 잰 옷 가운데 색
gain   = (g_L + d_l) / g_L                                    # d_l = 0 이면 1
g_pred = (g_L + d_l,  gain * g_a + d_a,  gain * g_b + d_b)
밝기:     |g2_L - g_pred_L|    <= max(RECHECK_FLOOR = 0.003, RECHECK_RATIO = 0.15 * |d_l|)
색 방향:  |g2_ab - g_pred_ab|  <= max(0.003, 0.15 * |g_pred_ab - g_ab|)
```

## 5. 이유 코드 표

사진마다 `status` 하나와 `reason` 하나가 남는다. `reason`은 위에서부터 먼저 걸린 것이고, `corrected`일 때만 비어 있다.

| status | 단계 | reason | 뜻 |
|---|---|---|---|
| `skipped` | 윤곽 고르기 | `no_candidate` | 쓸 후보가 없다 |
| | | `ambiguous_no_upload_color` | 후보가 둘 이상인데 올린 옷 색이 없다 |
| | | `upload_color_untrusted` | 후보가 둘 이상인데 올린 옷 색을 믿을 수 없다 |
| | | `ambiguous_candidates` | 가장 가까운 후보와 둘째의 차가 0.02 미만이다 |
| | 윤곽 품질 | `mask_tiny`, `mask_frame_fill`, `mask_thin`, `mask_fragments`, `mask_too_few_pixels`, `mask_edge_spill`, `mask_mixed_color` | 4.2절의 표 |
| | 형제 대조 | `no_sibling_check` | 형제가 2장 미만이다 |
| | | `mask_mixed_vs_siblings`, `mask_edge_vs_siblings` | 형제보다 품질 숫자가 크게 나쁘다 |
| | | `off_siblings` | 형제 가운데 색과 멀다. `redraw_candidate` 표시가 함께 달린다 |
| | 여러 색 옷 | `multicolor_garment` | 묶음에 여러 색 사진이 있다 |
| | 기준 색 | `no_reference_few_studio` | 쓸 수 있는 스튜디오 사진이 3장 미만이다 |
| | | `no_reference_studio_disagree` | 튀는 사진을 빼면 3장 미만이다 |
| | | `no_reference_no_upload_color` | 대조할 올린 옷 색이 없다 |
| | | `reference_off_upload` | 스튜디오 가운데 색과 진하기를 맞춘 올린 옷 색의 색 방향 차이가 0.05를 넘는다. `redraw_candidate` 표시가 함께 달린다 |
| `untouched` | 옮길 양 | `within_tolerance` | 허용 폭 안이거나 옮길 양이 0.003 미만이다 |
| | | `lightness_skipped_blown`, `lightness_skipped_extreme`, `lightness_skipped_clip_guard` | 밝기를 옮겨야 했지만 막혔고 색 방향도 옮길 것이 없다 |
| | | `clip_guard_reduced` | 색 방향만 옮기려 했는데 날아감과 뭉개짐 한도 때문에 0이 됐다 |
| `reverted` | 자기 검사 | `reverted_outside_changed`, `reverted_clip_guard`, `reverted_shortfall` | 4.9절의 세 검사 |
| `corrected` | | None | 고쳤다 |

표시(`flags`)는 이유와 별개로 여러 개 달릴 수 있고 고친 사진에도 달린다. `capped_lightness`, `capped_chroma`, `lightness_skipped_blown`, `lightness_skipped_extreme`, `lightness_skipped_clip_guard`, `clip_guard_reduced`, `redraw_candidate`다.

## 6. 기록 필드

### 6.1 사진마다 (`PhotoResult.record`)

| 묶음 | 필드 | 내용 |
|---|---|---|
| 신원 | `key`, `color_option_id`, `kind`, `clothing_type`, `sub_category` | 입력 그대로 |
| 판정 | `status`, `reason`, `flags` | 5절 |
| `mask` | `candidates_received`, `selection` | 받은 후보 수, 고른 방식(`single`, `closest_to_upload`, 못 골랐으면 None) |
| | `candidate_distances` | 색으로 고를 때 남은 후보별 R과의 가중 거리. 큰 후보부터이고 못 잰 후보는 None |
| | `removed_fragments`, `removed_pixels` | 지운 조각 수와 화소 수 |
| | `quality`, `quality_codes` | 4.2절의 숫자 열 개(`area_frac`, `fragments`, `core_share`, `core_pixels`, `core_px_1536`, `far_share`, `w_spread90`, `ab_spread90`, `rim_in_far`, `rim_excess`)와 걸린 품질 코드 전부. 형제와 견준 코드도 여기에 든다 |
| `measure` | `center`, `first_pass_center`, `same`, `cw_c`, `cw_l` | g, g1, 4.3절의 점수. `cw_l`은 기록만 |
| | `blown_frac`, `crushed_frac` | 원본의 날아간 화소 비율, 뭉개진 화소 비율 |
| | `sibling_count`, `sibling_d_l`, `sibling_d_ab` | 4.4절 |
| `plan` | `e_l`, `e_c`, `n_l`, `n_c`, `d_l`, `d_ab` | 기준과의 차이, 필요했던 양, 정한 양. `e_c`는 밝기를 옮긴 뒤의 예상 색에서 잰 값 |
| | `lightness_tolerance`, `chroma_tolerance`, `lightness_cap`, `chroma_cap` | 쓴 허용 폭과 한도 |
| | `d_l_before_clip_guard`, `d_ab_before_clip_guard` | 날아감과 뭉개짐 한도로 줄였을 때만 줄이기 전 값 |
| `result` | `remeasured_center`, `error_l`, `error_ab` | 다시 잰 g2와 예상 색과의 오차 |
| | `clip_growth`, `gamut_reduced_frac`, `changed_pixels`, `outside_changed_pixels` | 날아감과 뭉개짐 증가, 색역 처리로 진하기를 줄인 화소의 비율, 바뀐 화소 수, 윤곽 밖에서 바뀐 화소 수 |
| 판 | `algorithm_version` | `ALGORITHM_VERSION` |

`measure`, `plan`, `result`는 그 단계에 닿지 못한 사진에서 None이다.

### 6.2 묶음마다 (`BatchResult.record`)

| 필드 | 내용 |
|---|---|
| `color_option_id`, `photo_count` | 색상 옵션 id, 받은 사진 수 |
| `reference_source` | `seller`, `studio`, 없으면 None |
| `reference_color` | 기준 색 T (L, a, b). 없으면 None |
| `reference_reason` | 기준이 없는 이유 코드. `multicolor_garment`도 여기에 온다 |
| `redraw_candidate` | `reference_off_upload`일 때만 참 |
| `studio` | `used`(쓸 수 있는 스튜디오 사진 가운데 튀는 사진을 빼고 남은 수. 셀러 색이나 여러 색 판정으로 끝났으면 0), `dropped_keys`(0.03 밖이라 뺀 사진), `center`(A) |
| `upload_check` | `d_ab`(진하기를 맞춘 뒤의 색 방향 차이), `pulled`(끌어왔는지), `l_diff`(A_L - R_L) |
| `upload_trust` | `median_distance`, `trusted`. 올린 옷 색이 없으면 둘 다 None |
| `multicolor` | `flagged`, `keys` |
| `status_counts` | `corrected`, `untouched`, `skipped`, `reverted`별 사진 수 |
| `algorithm_version` | `ALGORITHM_VERSION` |

단계별 공개 함수가 돌려주는 사전은 열쇠 이름이 위 표와 다른 것이 있다. 묶음 함수가 위 표의 이름으로 옮겨 적는다.
- `resolve_reference`는 `source`, `color`, `reason`, `redraw_candidate`, `studio_used`, `studio_dropped_keys`, `studio_center`, `upload_d_ab`, `pulled`, `upload_l_diff`를 돌려준다.
- `check_against_siblings`는 `sibling_count`, `d_l`, `d_ab`, `codes`, `reason`을, `pick_by_upload_color`는 `index`, `distances`, `reason`을 돌려준다.
- `measure_garment_color`는 `multicolor`(그 사진이 4.5절의 여러 색 조건에 드는지)를 함께 돌려주지만 사진 기록에는 넣지 않는다. `plan_shift`가 돌려주는 `flags`와 `reason`, `self_check`가 돌려주는 `reason`은 사진 기록의 `flags`와 `reason`으로 옮겨진다.

## 7. 알고 받아들인 한계

**윤곽.**
- 옷의 일부만 딴 윤곽, 안으로 줄어든 윤곽, 색이 비슷한 다른 옷이나 색이 비슷한 두 벌을 합친 윤곽은 검사가 못 잡는다. 색이 같은 겹옷은 표본이 없어 보지 못했다. 화면의 75%를 넘는 근접 사진은 통째로 고치지 않는다.
- 품질 검사를 통과한 사진이 3장 미만인 색상 옵션은 형제가 모자라 한 장도 고치지 않는다. 셀러 색이 있어도 같다.
- 형제 문턱을 넘은 사진은 가장 크게 옮겼어야 할 사진인데 그대로 남는다(다시 그리기 대상).

**여러 색 옷.**
- 톤만 다른 두 색 옷(진청과 연청, 남색과 파랑, 흰색과 연한 색)은 화소 색만으로는 못 거른다. 실제 화소로 만든 두 색 80장 중 37장이 통과했다. 통과하면 멀쩡한 사진을 튀었다고 볼 수 있다.
- 윤곽 실수로 다른 옷까지 딴 사진 한 장(`mask_mixed_color`)도 그 색상 옵션 전체를 멈춘다. 틀리는 방향이 "고치지 않음"이라 받아들였다. 실제 표본 237장에서는 0장이었다.

**재기와 옮기기.**
- 가운데 밝기는 구도를 탄다. 옷의 반쪽만 보이면 가운데 밝기가 가운데값 0.025, 90% 지점 0.051 달라진다. 그래서 밝기 허용 폭이 넓다.
- 밝기를 옮기면 진하기가 같은 비율로 변한다. 밝기만 벗어난 사진을 고쳐도 색 방향이 조금 움직인다. 진하기를 원래대로 되돌리면 진한 색 옷에서 색역 문제가 생겨서 이쪽을 골랐다.
- 진한 옷과 흰 옷은 색역 때문에 목표까지 못 가는 방향이 있다. 그런 사진은 `reverted_shortfall`로 원본이 된다.
- 색 방향 이동은 그늘 화소가 덜 가서 다시 잰 값이 늘 조금 못 미친다. 평가에서 간 비율의 최소가 0.854였고 통과선이 0.85라 여유가 얇다.
- 흰색에 가까운 옷을 밝히면서 색 방향도 옮기는 한도 가까이 옮길 때, 요청을 조금 키우면 다시 재기 검사로 원본이 되는 자리가 있다(합성 5,472번 중 249번, 기본 허용 폭의 실제 표본 237장에서는 0장). 자기 검사는 안전망이라 조건을 느슨하게 하지 않는다.
- 밝기 요청이 커지면 색 방향 적용이 한 번에 줄 수 있다. 밝기를 먼저 정하고 색 방향은 남은 자리만 쓰기 때문이다. 날아감과 뭉개짐 한도의 이분법이 찾는 값도 "한도 안의 한 자리"다. 날아간 화소 수가 이동 크기에 단조가 아니라서 가장 큰 값이라는 보장은 없다. 찾은 자리의 증가는 늘 0.01 이하다.
- 윤곽이 옷 가장자리보다 1에서 3화소 안쪽이면 원래 색이 테로 남고, 윤곽이 놓친 옷 조각도 원래 색으로 남는다. 색 방향 0.04와 밝기 0.10에서 보이고 그 절반에서는 자세히 봐야 보인다.
- 지켜지는 것은 천과 색 방향이 0.08 넘게 다른 색, 짙은 옷 위의 흰 라벨(밝기 비율 3 이상), 색 단추다. 회색과 검정 프린트는 천과 함께 움직이고, 연한 여러 색 그림은 절반쯤 따라 움직인다. 청바지의 은색 단추는 17개 중 6개만 지켜져서 지켜진다고 말하지 않는다. 밝기 비율 0.45 아래의 깊은 그늘은 덜 옮겨진다.
- 기준 색 대조는 스튜디오 가운데 색이 올린 색과 크게 어긋나지 않는지만 본다. 실물 색이 맞는지는 보증하지 않는다.

**근거가 얇은 값.** 코드 주석에 임시값으로 적힌 것들이다. 오너가 지금 값 유지로 확정했지만 표본이 적다. `UPLOAD_TRUST_MAX`, `CORE_SHARE_MIN`, `CORE_PX_1536_MIN`, `FRAGMENT_MIN_SHARE`, `FRAGMENTS_MAX`, `SIBLINGS_MIN`, `REFINE_MIN_PIXELS`, `STUDIO_MIN`, `STUDIO_OUTLIER_DISTANCE`, `UPLOAD_CHROMA_KEEP`, `UPLOAD_CHROMA_MAX`, `UPLOAD_CHROMA_SCALE_RANGE`, `SHIFT_LIMITS`의 허용 폭과 장면 밝기 한도, `STRENGTH_LAMBDA`, `MIN_SHIFT`, `BLOWN_SKIP_FRAC`, `LIGHTNESS_RANGE`, `BRIGHTEN_CEILING`, `APPLY_LIGHT_RATIO`, `RECHECK_RATIO`, `RECHECK_FLOOR`, 그리고 밝히는 곡선과 색 방향을 화소 밝기에 비례해 옮기는 방식이다.

**표본에 없던 것.** 원피스와 치마, 실제 굵은 줄무늬와 체크와 색 블록, 파랑과 보라와 주황의 진한 색, 매끈한 검은 옷, 짙은 옷 위의 흰 글씨, 비치는 천, 센 유색 조명 장면.

## 8. 셀러가 맞춘 색을 넣는 약속

셀러가 색 조절 화면에서 옷 색을 맞췄을 때 그 값을 이 모듈에 어떻게 넣는지 적는다. 색 조절 화면의 자리와 문구는 이 문서가 정하지 않는다. 모듈 코드는 그대로다. 이 모듈은 색을 통째로 갈아입히는 용도가 아니다(1.2절).

### 8.1 만들기 전에 맞춘 색

| 셀러가 한 것 | 모듈에 넣는 자리 | 뜻 |
|---|---|---|
| 손대지 않음 | `upload_color`에 조명 색을 걷은 올린 색 R | 4.6절 그대로다 |
| 자동 맞춤, 진하기, 따뜻함만 맞춤 | `upload_color`에 맞춘 색 | 색 방향 대조의 기준이 된다. 밝기는 스튜디오 사진들의 가운데 값을 쓴다 |
| 밝기 슬라이더까지 맞춤 | `seller_color`에 맞춘 색 | 절대 기준이다. 스튜디오 사진 수를 보지 않는다 |

- 셀러가 밝기를 직접 맞추지 않았는데 올린 사진의 밝기를 절대 기준으로 쓰면, 제대로 그린 사진이 한꺼번에 어두워지거나 밝아진다. 그래서 "밝기까지 맞췄을 때만" 절대 기준으로 넣는다.

### 8.2 만든 뒤에 맞춘 색

- 언제나 `seller_color`, 절대 기준이다. 셀러가 결과 사진을 보면서 맞추기 때문이다.
- 저장해 둔 윤곽과 고치기 전 원본으로 다시 계산한다. 다시 그리지 않고 유료 호출도 없다. 그 색상 옵션의 사진 전부에 함께 적용한다.
- `seller_color`를 넣을 때도 `upload_color`를 함께 넣는다. 후보가 둘 이상인 사진의 윤곽 고르기에 쓰이기 때문이다(4.1절).
- 그 색상의 사진이 3장 미만이면 셀러 색을 넣어도 한 장도 고치지 못한다(형제 대조에 형제 2장이 필요하다, 4.4절).

### 8.3 색상을 바꿔 다시 만든 컷, 나중에 넣은 컷

- 그 색상 옵션의 고치기 전 원본 전부와 새 사진을 함께 넣고, 저장해 둔 기준 색을 `seller_color` 자리에 넣어 부른다. `upload_color`도 처음 부를 때와 같은 값을 함께 넣는다. 새 사진의 결과만 쓰고 이미 보여 준 사진은 바꾸지 않는다.
- 원본을 함께 넣는 까닭은 형제 대조와 여러 색 판정에 형제 사진이 필요해서다. 기준이 정해진 뒤의 계산은 기준 색만 받고 출처를 보지 않으며, 허용 폭도 셀러 색일 때와 같다.
- 이때 모듈은 묶음 기록의 `reference_source`를 `seller`로 적는다. 연결 쪽이 저장할 때 `stored`로 바꿔 적는다.
- 기준이 없던 색상 옵션은 사진이 늘어 기준이 처음 잡히는 때 그 색상 사진 전부에 한 번 적용한다.

### 8.4 부르는 쪽이 알아야 할 모듈 동작

- 허용 폭과 한도는 셀러 색에도 똑같다. 셀러가 맞춘 색이 사진의 옷 색과 허용 폭 안이면 사진은 움직이지 않고, 멀어도 한 번에 한도까지만 간다.
- 여러 색 판정은 셀러 색보다 먼저다. 여러 색 옷이면 셀러가 색을 맞춰도 사진은 그대로다.
- 셀러 색이 있어도 윤곽 검사와 형제 대조는 그대로 한다. 건너뛴 사진은 움직이지 않는다.
- `photo_count`에는 건너뛴 사진도 들어간다. 실제로 맞춰 본 사진 수는 `status_counts.corrected + status_counts.untouched`다. 셀러 색이 있으면 사진이 모두 건너뛰어져도 `reference_source`는 `seller`이고 `reference_color`는 그 색이다. 여러 색 판정에 걸렸을 때만 둘 다 None이다.
- `upload_color` 자리에 넣은 색은 후보가 둘 이상인 사진의 윤곽 고르기에도 쓰인다(4.1절). `upload_color`가 없으면 그런 사진은 `seller_color`가 있어도 `ambiguous_no_upload_color`로 건너뛴다.

## 9. 연결할 때 정할 것

지금은 손대지 않는다. 생성 파이프라인에 연결할 때 정한다.
- **사진 종류를 누가 정하나.** 콘티보드 컷 종류로 정하는 것을 추천한다. 호리존컷은 `studio`, 스타일링컷은 `styling`, 제품컷은 `product`다.
- **저장 자리.** 색상 옵션마다 만들기 전 슬라이더 값과 색, 만든 뒤 셀러 색, 기준 색과 출처, 판 이름. 사진마다 원본 자산, 윤곽 자산, 결과 자산, 기록.
- **사진 한 장의 시간 상한.** 날아감과 뭉개짐 한도의 이분법이 걸리면 grow를 많게는 26번 재서 느리다. 이분법이 걸리도록 크게 옮겨 본 시험에서 그런 사진 한 장이 1.1초에서 34초 걸렸다(옷이 넓고 밝을수록 느리다). 기본값으로 실제 표본 237장 전체를 돌린 시간은 잴 때마다 달라 약 40초에서 65초였다.
- **다시 그리기 표시 규칙.** 형제가 정확히 2장일 때와 품질 코드가 함께 걸렸을 때 어떻게 할지.
- **믿지 않기로 한 올린 색을 기준 색 대조에 쓸지.** 지금은 쓴다. 바꾸면 테스트도 함께 고친다.
- **이유 코드의 화면 문구**(`reference_off_upload` 등)와 다시 그리기 표시를 화면에 어떻게 보일지.
- **윤곽 낱말.** 낱말 확인 실험(유료)은 연결 전에 한 번 하고, 실행 직전에 모델과 건수와 예상 비용을 적어 따로 승인을 받는다. 지금 표에서 outerwear와 dress는 써 본 기록이 없다. 보내는 순서는 종류 낱말을 먼저 보내고 후보가 0개일 때만 예비 낱말을 보내는 것을 추천한다.
- **여러 색 옷을 만들기 전에 알리기.** 상품 분석의 무늬 정보가 필요하다. 톤만 다른 두 색 옷을 거르는 데에도 같은 정보를 쓴다.
- **올린 색 대조의 숫자**(0.02, 0.05, 0.5, 2.0). 운영 기록이 쌓이면 `upload_check.d_ab` 분포로 다시 본다.
- **판 이름과 import.** 판 이름은 기록을 저장하기 시작한 뒤에 계산이 바뀌면 그때부터 올린다. `tone_mask`를 import 하면 윤곽 호출 모듈이 함께 올라오지만 네트워크 호출은 없다.
- **메모리.** 묶음 안에서 화소 단위 배열을 사진 수만큼 쌓지 않는다. 잰 값만 남기고, 한도 재기나 실제 적용이 필요한 사진에서만 그 사진의 배열을 다시 만든다. 2304x1536 사진 22장, 윤곽이 화면의 절반일 때 계산 중 최대 메모리는 약 240MiB였다(입력 사진 배열은 빼고 잰 값이다. 사진 수를 늘려도 거의 같다). 작업기에서 여러 묶음을 동시에 돌릴지는 이 숫자로 정한다.
- **주석의 절 번호.** 코드 주석의 절 번호는 옛 작업 명세의 번호다(맨 위 대응표). 이 문서의 번호로 맞출지는 연결할 때 정한다.

## 10. 다른 색상 결과 맞추기

### 10.1 하는 일과 입력, 출력

대표색으로 만든 착용컷에 AI가 목표 색을 입힌 뒤, 남은 작은 색 방향 오차만 맞춘다. 목표색 Front 사진의 조명 밝기를 착용컷에 옮기지 않는다. 오너가 2026-11-15 운영 흐름에 넣기로 정했다.

계산 함수는 `match_to_target_color(image, mask, target_ab)`다. 사진은 sRGB uint8 `(H, W, 3)`, 윤곽은 같은 크기의 bool `(H, W)`, 목표는 유한한 Oklab `(a, b)` 두 수다. 결과 사진 배열과 기록 dict를 돌려준다. 옮기지 않거나 되돌리면 입력 배열 객체를 그대로 돌려준다. 같은 입력에는 같은 결과와 기록이 나온다. 입력 형식이 틀렸을 때만 예외를 던지고, 계산 실패는 `skipped`, `calculation_failed`로 남긴다.

옷 윤곽의 작은 조각 정리, 윤곽 품질 검사, 옷 가운데 색 재기, 가중치, 날아감과 뭉개짐 검사, 화소 이동과 자기 검사는 기존 부품을 그대로 쓴다. 다른 사진과의 형제 대조는 하지 않는다. `plan_shift`는 선택 인자 `limits`를 받으며, 생략하면 기존 `SHIFT_LIMITS`를 그대로 쓴다.

### 10.2 규칙과 근거

| 이름 | 값 | 동작과 근거 |
|---|---|---|
| `TARGET_SHIFT_LIMITS.lightness_cap` | 0 | 밝기 이동 `d_l`은 항상 0이다. Front 사진의 조명을 복사하지 않는다 |
| `TARGET_CHROMA_CAP` | 0.04 | 필요한 색 방향 이동이 이 값을 넘으면 조금도 옮기지 않고 `over_cap`으로 건너뛴다 |
| `TARGET_CHROMA_TOLERANCE` | 0.01 | 필요한 이동이 이 값 미만이면 `untouched`, `within_tolerance`다. 시험하지 않은 임시값이다 |
| `TARGET_ACHROMATIC_CHROMA` | 0.02 | 목표의 `hypot(a, b)`가 이 값 미만이면 `untouched`, `target_achromatic`이다 |
| `TARGET_ALGORITHM_VERSION` | `garment-target-color-v1` | 다른 색상 결과용 계산의 판 이름이다 |

한도 근거는 `.scratch/recolor-probe-20261113/tune/REPORT.md`의 실제 사진 13장 실험이다. AI 결과의 최대 색 방향 오차는 0.032였다. 색 방향 이동 0.04까지 흠이 없었고, 0.06부터 밑단 테두리와 목 피부 번짐, 단추 물듦이 생겼다. 같은 색상 묶음의 `SHIFT_LIMITS` 값은 이 실험으로 바꾸지 않는다.

목표가 무채색이 아니고 필요한 이동이 0.01 이상 0.04 이하이면 목표까지 옮길 양을 계획한다. 허용 폭 0.01을 이동량에서 빼지 않는다. 기존 날아감과 뭉개짐 보호는 이동량을 줄일 수 있다. 윤곽 품질이 나쁘면 `skipped`와 기존 품질 코드를 남긴다. 자기 검사가 실패하면 `reverted`와 기존 자기 검사 코드를 남기고 원본을 돌려준다. 밝기를 계획해서 옮기지는 않지만 8비트 반올림과 색역 처리에 따른 작은 밝기 차이는 있을 수 있다.

### 10.3 서비스의 순서와 실패 처리

`garment_color_tune.tune(settings, *, master, target, output, clothing_type, sub_category=None, matching_side=None)`가 바이트 연결을 맡는다. 세 사진은 `InlineImage`다. master는 색 적용 전 대표색 컷, target은 목표색 Front 사진, output은 AI 색 적용 결과다. 반환값은 사진 바이트, MIME, 기록 dict다.

1. 스위치가 꺼져 있으면 사진을 읽거나 fal을 부르지 않고 `{"status": "disabled"}`를 돌려준다. fal 설정이 없으면 `skipped`, `fal_unconfigured`다.
2. 실제 컷의 레시피에서 사용한 매칭 의류의 종류를 확인한다. 종류가 맞지 않거나 안에 입은 별도 옷이 함께 지정돼 윤곽을 나누기 어려우면 보정을 건너뛴다.
3. 목표색 사진에서 상품 종류에 맞는 윤곽을 찾는다. 품질 검사를 통과한 후보가 하나여야 색을 잰다. 거의 무채색이면 여기서 끝낸다. 상의나 하의는 반대편 의류 후보도 확인한다. 목표색을 잰 윤곽과 겹치면 보정을 건너뛴다.
4. 색을 바꾼 결과에서도 상품 종류와 반대편 의류의 윤곽을 찾는다. 상품 후보가 여러 개로 나뉘거나, 선택한 코디의 윤곽을 확인할 수 없으면 보정을 건너뛴다. 코디와 겹치는 상품 후보를 빼고 기존 `tone_mask.produce`로 윤곽을 정한다. 작은 구멍을 메우는 정리를 거친 뒤에도 코디와 겹치지 않아야 한다.
5. 계산 함수를 부른다. 옮겼을 때만 PNG로 인코딩한다. 그 밖에는 output의 바이트 객체와 MIME을 그대로 돌려준다.

해독, 재기, 선택, 이동과 인코딩은 `run_cpu_bound`로 이벤트 루프 밖에서 돈다. 서비스 전체 시간 상한 `TIMEOUT_S`는 기존 `fal_api.TIMEOUT_S`와 같은 60초다. 코디 정보 조회, CPU 풀 대기, fal 호출과 다운로드도 이 상한에 들어간다. 이미 시작한 CPU 스레드가 상한 뒤 끝날 수 있지만 그 결과를 채택하거나 저장하지 않는다.

서비스는 잡 취소 신호 말고는 예외를 밖으로 내보내지 않는다. 취소 신호는 그대로 전달해 잡이 멈추게 한다. fal 실패는 `fal_failed`, 후보 없음은 `no_candidate`, 목표 누끼 없음은 `target_no_garment_mask`, 깨진 입력은 `invalid_image`, 시간 초과는 `timeout`, 나머지 계산 실패는 `calculation_failed`로 기록하고 output을 그대로 돌려준다. 목표 윤곽 품질 실패에는 기존 품질 코드 앞에 `target_`을 붙인다. 후보가 모호하면 `target_ambiguous` 또는 `no_candidate`, 코디 구분을 확인하지 못하면 `matching_unverified`를 남긴다. fal 호출 비용은 기존 `fal_api.run_model`로 잰다. 정상 상의·하의 컷은 SAM 3 호출 4회로 $0.020, 원피스는 2회로 $0.010이다. 거의 무채색인 목표는 1회, $0.005에서 끝난다. 기존 HTTP 재시도가 발동하면 요청 횟수는 더 늘 수 있다.

### 10.4 기록과 생성 경로

상세페이지의 새 컷과 비슷한 컷, 에디터의 색 변경과 비슷한 컷과 새 컷 다섯 자리에서 색 적용 QC 직후에 실행한다. QC가 `failed`면 실행하지 않는다. `unavailable`, `indeterminate` 등 기존 규칙상 계속 진행하는 결과에는 실행한다. 공용 `after_color_check`가 아래 기록과 경고를 남기고, 호출자는 반환된 바이트와 MIME을 채택한다.

| 필드 | 내용 |
|---|---|
| `status`, `reason` | 고침, 그대로 둠, 건너뜀, 되돌림과 이유 코드. 스위치 꺼짐은 `disabled`다 |
| `algorithm_version` | 계산의 판 이름. 공용 도우미는 계산 전 끝난 기록에도 이 값을 붙인다 |
| `measure` | 이동 전에 잰 옷 가운데 색과 기존 재기 숫자 |
| `target_measure`, `target_ab` | 누끼에서 잰 목표색과 목표 색 방향 |
| `needed_shift` | 밝기 이동 0, 필요한 `(da, db)`, 그 거리 `distance_ab` |
| `actual_shift` | 채택 결과를 다시 재서 얻은 실제 밝기 차이와 색 방향 차이. 안 옮기거나 되돌리면 모두 0 |
| `mask`, `plan`, `result` | 윤곽 품질, 계획한 이동량, 자기 검사와 바뀐 화소 숫자. 못 간 단계는 None |
| `stage` | 서비스 실패가 난 단계 |
| `inputOutputSha256` | 보정에 넣은 AI 결과의 해시 |
| `outputSha256` | 보정을 마친 채택 사진의 해시. 뒤의 목선 복원이 별도로 사진을 바꿀 수 있다 |

기록은 `color_execution["colorTune"]`에 넣는다. `colorCheck.checkedOutputSha256`은 QC가 본 보정 전 사진의 해시를 유지한다. 계산 전 끝난 서비스에는 아직 재지 않은 필드가 없다. `over_cap`이면 컷 경고에 `color_tune_over_cap`을 넣는다. 자동 다시 그리기는 하지 않는다.

자산 메타와 체크포인트는 기록 dict와 경고 dict를 허용한다. 경고 코드 허용 목록은 없다. 체크포인트의 요청 비교는 실행 결과인 `colorTune`을 제외한다. 완성 체크포인트를 재사용할 때 생성과 QC, 보정을 모두 건너뛰므로 한 컷을 두 번 옮기지 않는다. 새 색 변경이나 비슷한 컷은 기존 대표색 master에서 시작한다. 얼굴 패스 결과를 보관하는 해시와 이후 목선 복원, 저장 사진 해시는 채택한 보정 사진을 따라간다.

### 10.5 스위치

`Settings.garment_color_tune_enabled`와 환경 변수 `GARMENT_COLOR_TUNE_ENABLED`를 쓴다. 코드 기본값은 false다. API 매니페스트와 상세페이지 전용 detail-worker 매니페스트에는 true를 둔다. 에디터 잡은 API에서 돈다. detail-worker에는 API와 같은 기존 `FAL_KEY` 시크릿 참조도 둔다. 스위치를 켰어도 fal 키가 없으면 컷은 실패하지 않고 보정을 건너뛴다.

## 쉬운 설명

- **무엇을 하는 기능인가.** 상세페이지 사진을 다 만든 뒤, 같은 옷인데 한 장만 유독 밝거나 색이 달라 보이는 사진을 찾아 옷 부분만 살짝 되돌린다. 옷 밖은 한 점도 건드리지 않고, 고친 뒤 스스로 검사해서 이상하면 원본으로 되돌린다.
- **얼마나 건드리나.** 거의 안 건드린다. 실제 사진 237장에 돌렸을 때 4장만 아주 조금 밝아졌다. 조금이라도 애매하면(옷 윤곽이 불확실함, 무늬가 여러 색임, 비교할 사진이 모자람) 고치지 않고 이유를 적어 둔다.
- **못 하는 것.** 색을 다른 색으로 갈아입히지 못하고, 옷 길이와 핏은 못 고친다. 진청과 연청처럼 톤만 다른 두 색 옷은 아직 가려내지 못한다.
- **지금 상태와 다음 할 일.** 같은 색상 사진끼리 맞추는 계산과 시험은 끝났고 아직 연결하지 않았다. 다른 색상으로 바꾼 컷의 색을 살짝 맞추는 부분(10절)만 생성 흐름에 연결했다. 기본값은 오너가 지금 값 그대로 쓰기로 정했다. 최종컷 만드는 방식이 정해지면 9절의 항목을 정하고 연결한다. 셀러가 색 조절 화면에서 옷 색을 직접 맞추면 8절의 약속대로 이 계산에 넣는다.
