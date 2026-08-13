# 마네킹 소매 기장 축 + 겨드랑이 핫존 재배치 — 설계

- 날짜: 2026-08-13
- 브랜치: `feat/mannequin-sleeve-hotspots` (origin/main 기준)
- 관련: PR #106(핫존 도입), `documents/mannequin_ui_direction.md`, `documents/fit_profile_spec.md`

## 1. 배경

"의류 재현성 높이기" 화면은 마네킹컷 위의 **핫존**(점)이 유일한 핏 조정 진입점이다. PR #106에서 순차 질문 카드(`.fit-ask`)를 제거해, 핫존이 없는 축은 셀러가 도달할 방법이 없다.

현재 상의 핫존은 2개다.

| id | 좌표 | 라벨 | 축 |
|---|---|---|---|
| `top-fit` | 60%, 24% | 몸통·소매 핏 | `top.fit` |
| `top-hem` | 52%, 41% | 상의 밑단 | `top.length` |

두 가지 문제가 있다.

1. **소매 기장을 조정할 수 없다.** `FIT_AXES.top`은 `fit`(몸통·소매 볼륨)과 `length`(**밑단** 기장)뿐이다. 긴팔 상품을 반팔·민소매로 보여주고 싶어도 수단이 없다.
2. **`top-fit` 점이 가슴 한가운데 얹혀 있어** 무엇을 조정하는 점인지 몸에서 읽히지 않는다.

부수적으로, 별개 조사에서 확인된 좌표 충돌이 하나 더 있다: `outer-hem`(55%, 55%)과 매칭 하의의 `pants-cut`(56%, 55%)이 사실상 같은 자리라, 아우터 상품 화면에서 **아우터 기장 축이 클릭 불가**하다. 같은 CSS 블록과 같은 pin 테스트를 건드리므로 이번 작업에 포함한다.

## 2. 범위

**한다**

- `top.sleeve` 축 신설 — 값 2개(`sleeveless`, `short`)
- 핫존 3개 재배치: `top-fit` → 화면 왼쪽 겨드랑이, `top-sleeve`(신규) → 화면 오른쪽 겨드랑이, `top-hem` 유지
- 하의 계열 핫존 3개 재배치(`outer-hem`·`pants-cut`·`skirt-shape`) — 충돌 해소에 필요한 최소 집합
- 핫존 최소거리 회귀 테스트

**안 한다**

- `outer.sleeve` · `dress.sleeve` — 이번 요청 범위 밖
- 반팔/긴팔 자동 판별 — 판별 데이터가 계약에 없다(§7 참조)
- `long`(긴팔) 값 — §7 참조
- 핫존 좌표계 자체의 개편(이미지 정규화 좌표·앵커 테이블)

## 3. 축 정의

`src/lib/fitAxes.js`의 `FIT_AXES.top`에 `sleeve` 키를 **`fit`, `length` 뒤에** 추가한다. 카탈로그 키 순서가 UI 스텝 순서와 `declared_axis_spec` 순서를 결정하므로, 뒤에 붙여 기존 순서를 보존한다.

```js
sleeve: {
  women: [
    { value: 'sleeveless', label: '민소매', promptEn: '...' },
    { value: 'short',      label: '반팔',   promptEn: '...' },
  ],
  men: [ /* 동일 */ ],
}
```

`promptEn`은 기존 축(`outer.fit.over`, `pants.length.below_ankle`)의 **조건부 재단** 패턴을 따른다.

- `sleeveless`:
  > a sleeveless version of the same top; if the photographed garment has sleeves, visibly re-tailor only its sleeves by removing them completely and finishing clean armholes at the shoulder points, leaving the neckline, body width and hem length unchanged; if it is already sleeveless, preserve those proportions

- `short`:
  > a short-sleeve version of the same top; if the photographed garment has long or three-quarter sleeves, visibly re-tailor only its sleeves by shortening them to end around the mid-upper-arm, leaving the neckline, body width and hem length unchanged; if it already satisfies this target, preserve those proportions

서버 `server/app/agents/fit_axes.py`의 `FIT_AXES`에 같은 내용을 미러한다. `tests/frontend/fit-vocabulary.test.mjs`가 양쪽 일치를 잠근다.

**기본값은 없다.** 축 미선택 = `axes.sleeve` 부재 = 사진 그대로(긴팔이면 긴팔). `normalize_fit_profile`이 FIT_AXES에서 allowlist를 파생하므로 기존 저장 프로필과의 호환은 자동이다 — 별도 마이그레이션 없음.

## 4. QC·프롬프트 배선

`mannequin_fit_qc.declared_axis_spec`은 `AXIS_OBSERVABLES`에 문구가 없는 축을 **조용히 버린다**. 아래를 전부 채우지 않으면 sleeve만 QC에서 빠진 채 생성된다.

| 파일 | 키 | 내용 |
|---|---|---|
| `fit_axes.AXIS_OBSERVABLES` | `("top","sleeve","sleeveless")` | both shoulders bare with clean finished armholes and no sleeve fabric on the upper arms |
| `fit_axes.AXIS_OBSERVABLES` | `("top","sleeve","short")` | both sleeve hems end on the upper arm above the elbow, with the forearms fully bare |
| `mannequin_fit_qc._EDIT_TEMPLATES` | `("top","sleeve")` | Re-tailor only the top's sleeves in this photo until {observable}; keep the neckline, body width and hem length unchanged. |
| `mannequin_pairwise_qc._COMPARATIVE` | `"sleeve"` | the sleeves cover MORE of the arm (i.e. they are longer) |
| `mannequin_pairwise_qc._ORDINAL` | `("top","sleeve")` | `{"sleeveless": 0, "short": 1}` |
| `fit_axis_matrix.AXIS_PAIRS` | — | `("top","sleeve")` 추가 (10쌍 → 11쌍) |

`mannequin_adjust.py`와 `image_qc.build_declared_fit_block`은 `AXIS_OBSERVABLES` 조회만 하므로 추가 작업이 없다.

## 5. 핫존

### 5.1 카탈로그

`src/features/mannequin/fitHotspots.js`:

```js
top: {
  fit:    [{ id: 'top-fit',    label: '몸통 핏' }],
  length: [{ id: 'top-hem',    label: '상의 밑단' }],
  sleeve: [{ id: 'top-sleeve', label: '소매 기장' }],
}
```

`top-fit` 라벨을 "몸통·소매 핏" → **"몸통 핏"**으로 바꾼다. 소매 축이 독립하면 이름이 충돌한다. `outer-fit`은 소매 축이 없으므로 "몸통·소매 핏"을 유지한다.

`Mannequin.jsx:44`의 `AXIS_LABELS`에 `sleeve: '소매 기장'`을 추가한다. 예시 패널 안내 문구(`stepExNote`)는 이 라벨을 그대로 써서 "예시에 보여지는 의류의 소매 기장만 참고해주세요."가 자동 생성된다.

### 5.2 좌표 (`Mannequin.css`)

```
        ___
       / o \
   fit X   X sleeve
      /|   |\
       |   |
       |_o_|  hem
```

| id | 현재 | 변경 | 근거 |
|---|---|---|---|
| `top-fit` | 60%, 24% | **46%, 27%** | 화면 왼쪽 겨드랑이 |
| `top-sleeve` | — | **64%, 27%** | 화면 오른쪽 겨드랑이 |
| `top-hem` | 52%, 41% | 유지 | |
| `outer-fit` | 58%, 32% | 유지 | |
| `outer-hem` | 55%, 55% | **46%, 50%** | 왼쪽 힙 — `pants-cut`에서 분리 |
| `pants-cut` | 56%, 55% | **60%, 62%** | 오른쪽 허벅지 — '바지 통·실루엣'은 힙보다 다리에서 읽힌다 |
| `pants-hem` | 57%, 84% | 유지 | |
| `skirt-shape` | 58%, 58% | **62%, 60%** | 스커트 오른쪽 옆선 — `outer-hem`에서 분리 |
| `skirt-hem` | 56%, 73% | 유지 | |
| `dress-shape` | 58%, 49% | 유지 | |
| `dress-hem` | 56%, 76% | 유지 | |

`outer-hem` 하나만 옮겨서는 최소거리 불변식(§8)을 만족시킬 수 없다. 왼쪽으로 더 밀면 마네킹 몸 밖 배경에 점이 뜬다. 아우터 밑단·바지 통·스커트 실루엣이 모두 힙 주변에 몰려 있는 것이 원인이라, 하의 두 축을 해부학적으로 더 맞는 자리(허벅지·옆선)로 내려 세 점을 분산한다.

이 세트는 좁은 쪽 프레임(`comparing` 상태 300×400px)에서 실제로 동시에 뜨는 모든 조합에 대해 최소 중심거리 54px을 확보한다(기준 48px).

표의 숫자는 현재 좌표에서 역산한 **출발값**이다. 구현 중 실제 마네킹컷 위에 띄워 스크린샷으로 보정한다(PR #106과 같은 절차). 보정 후 값이 최종이며, pin 테스트와 이 문서를 함께 갱신한다. 보정 시에도 최소거리 테스트가 불변식을 지킨다.

좌표계 주의: `.fit-mine-img`는 `aspect-ratio: 3/4` + `object-fit: cover`이고 실제 마네킹컷은 2:3이라, 세로 10.6%가 잘린다. 핫존 %는 **프레임 좌표**지 이미지 좌표가 아니다. 이번 작업은 이 구조를 바꾸지 않고 프레임 좌표로만 다룬다.

## 6. 예시 이미지

`server/scripts/gen_fit_examples.py`로 2장 생성한다.

- `public/assets/fit-examples/top-any-sleeve-sleeveless.jpg`
- `public/assets/fit-examples/top-any-sleeve-short.jpg`

성별은 `any`(여성 베이스) — `outer.fit`·`pants.length`와 같은 관례다. 생성 후 `src/lib/fitExampleImages.js`의 `FILES`에 등록한다.

이미지가 없으면 `fitExampleImage`가 `null`을 반환해 UI가 텍스트 타일로 폴백하므로 기능은 깨지지 않는다. 따라서 **이미지 생성을 마지막 단계**에 두고, 그 전 단계까지만으로도 동작하는 순서로 구현한다.

## 7. 명시적으로 남기는 한계

**반팔 자동 판별 불가.** 소매 길이를 나타내는 필드가 계약에 없다 — `subCategory`는 `tshirt|sweatshirt|shirt|knit`로 소매를 구분하지 않고, `measurements.sleeveLength`(cm)는 셀러 **선택** 입력이라 비어 있을 수 있다. 따라서 `top-sleeve` 핫존은 상의 상품에 **상시 노출**한다. 긴팔 상품 셀러도 반팔·민소매로 바꿀 수 있게 되는 부수 효과가 있으며, 이는 의도된 동작이다.

**긴팔로의 명시적 복귀 없음.** 값이 `sleeveless`·`short` 둘뿐이라, 한 번 저장된 `axes.sleeve`를 "원본 긴팔"로 되돌리는 선택지가 UI에 없다. `선택 취소`는 이번 회차 미반영분만 철회하고 저장된 값은 남는다. 복귀는 이전 버전 썸네일 선택으로만 가능하다. 필요해지면 `long` 값을 추가하는 것으로 해결한다(카탈로그 한 줄 + 예시 1장 + observable 1개).

## 8. 테스트

FE 카탈로그와 서버 카탈로그를 대조하는 미러 테스트는 **존재하지 않는다** — 두 파일은 주석으로만 "수동 미러"라고 선언돼 있다. 따라서 sleeve 값 어휘는 양쪽에서 각각 명시적으로 잠근다.

| 파일 | 변경 |
|---|---|
| `tests/frontend/fit-vocabulary.test.mjs` | `axesFor('top', *).sleeve` 값·순서 잠금 (신규 테스트) |
| `tests/frontend/fit-example-files.test.mjs` | `FILES`↔디스크 정합 — 신규 2장(에셋 단계에서) |
| `tests/frontend/mannequin-fit-hotspots.test.mjs` | 좌표 pin 갱신, `top.sleeve` 커버리지, **신규 최소거리 테스트** |
| `server/tests/test_mannequin_fit_profile.py` | 서버 sleeve 어휘 잠금 + `normalize_fit_profile`의 sleeve 통과·이상값 제거 |
| `server/tests/test_fit_axis_matrix.py` | 기존 개수 단언 갱신(women 10 → **11**, men 6 → **7**) + `extreme_pair("top","sleeve") == ("sleeveless","short")` |

`server/tests/test_mannequin_fit_qc.py:137`의 `_EDIT_TEMPLATES` 커버리지 테스트는 카탈로그를 순회하므로, 축만 추가하고 템플릿을 빠뜨리면 **자동으로 실패한다** — 추가 작업 없이 안전망이 된다.

**최소거리 테스트**가 이번 작업의 회귀 방지 핵심이다. 한 화면에 동시에 뜨는 핫존 조합(주상품 카테고리 × 성별 × 매칭 의류 fitCategory 전수)을 만들어, 어떤 두 점도 프레임 기준 48px 미만으로 붙지 않음을 검증한다. 기준 프레임은 좁은 쪽인 `comparing` 상태(300×400px)로 잡는다.

## 9. 문서

- `documents/fit_profile_spec.md` §2 축 카탈로그 표에 `top.sleeve` 행 추가
- `documents/mannequin_ui_direction.md` §4 에셋 현황에 신규 2장 반영, §2 핫존 설명에 소매 축 추가

## 10. 리스크

- **생성 품질**: 소매 제거·단축은 기존 축과 달리 옷의 실루엣을 크게 바꾼다. 원본과 다른 옷으로 읽힐 위험이 `length`·`fit`보다 크다. `promptEn`과 observable이 "소매만, 넥라인·몸통·밑단 불변"을 반복 명시해 완화하고, pairwise QC 극단쌍(`sleeveless`↔`short`)으로 반영 여부를 잰다.
- **좌표 출발값**: §5.2의 숫자는 추정이라 스크린샷 보정 전에는 겨드랑이에 정확히 얹히지 않을 수 있다. 보정을 구현 단계의 명시적 체크포인트로 둔다.
- **마네킹 베이스 교체**: 베이스 포즈·프레이밍이 바뀌면 모든 핫존 좌표가 조용히 어긋난다. 이번 작업이 만드는 위험은 아니지만 표면적을 1개 늘린다.
