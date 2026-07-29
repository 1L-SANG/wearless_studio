# 마네킹 체형 볼륨 조절 + 상의 tuck-in 버그 수정

- 작성일: 2026-07-29
- 브랜치: `feat/mannequin-body-volume`
- 관련 코드: `server/app/workers/mannequin_job.py`, `server/app/agents/mannequin.py`, `server/prompts/mannequin_generate_v1.txt`, `src/features/analysis/AnalysisForm.jsx`

## 1. 문제

### 1-1. 여성 마네킹 체형이 고정

여성 타깃 상품이어도 항상 동일한 체형의 베이스 마네킹으로 마네킹컷이 생성된다. 가슴·힙 볼륨에 따라 같은 옷도 실루엣이 크게 달라지는데, 셀러가 이를 조절할 수단이 없다.

### 1-2. 상의가 하의에 tuck-in 됨

매칭 하의가 있는 마네킹컷에서 주상품(상의)이 하의 안으로 넣어져 생성된다. 상품인 상의의 밑단·기장이 가려져 상세페이지 컷으로 못 쓴다.

**근본원인** — `server/prompts/mannequin_generate_v1.txt` 의 매칭 하의 지시가 조건부다:

```
If the main product is a top or outerwear and its length axis is declared in FIT PROFILE,
keep the main product untucked with its entire hem visible and do not let the matching
bottom cover it; otherwise use appropriate layering, tuck, and proportion.
```

untuck 강제가 **`length` 축이 선언된 경우에만** 걸린다. 그런데 `src/features/analysis/AnalysisForm.jsx:406-429` 의 `withFitProfile` 은 `axes` 를 빈 객체로 시작해 **`fit` 축만** 채운다(`:417`). `length` 는 셀러가 별도로 건드리지 않는 한 비어 있고, 따라서 대부분의 잡이 `otherwise use appropriate layering, tuck, and proportion` 분기로 빠진다.

한국어 템플릿(`mannequin_generate_v1.ko.txt:27`)은 조건 분기조차 없이 tuck 을 상시 허용한다:

```
- 매칭 하의(있으면, 마지막 이미지): 이 하의도 마네킹에 함께, 상의와 자연스럽게 코디해 입힌다(적절한 레이어링·턱·비율).
```

기본 템플릿은 영문(`server/app/agents/prompts.py:52` — `settings.mannequin_prompt_file` 미설정 시 `mannequin_generate_v1.txt`)이고, 한국어본은 `MANNEQUIN_PROMPT_FILE` 오버라이드용이다.

## 2. 설계 원칙

현행 파이프라인의 불변식을 **한 줄도 깨지 않는다**:

- 생성 프롬프트의 `Keep the SAME mannequin body and shape ... as image 1` 크리티컬 룰 유지
- structure QC 의 `mannequinBasePreserved` 판정(`server/app/agents/mannequin_structure_qc.py:95-97`) 유지
- 셀러 자유 문자열은 프롬프트에 절대 보간하지 않는다

따라서 체형 조절은 **프롬프트로 몸을 변형시키는 게 아니라, 애초에 다른 베이스 이미지를 image 1 로 넣는 방식**으로 구현한다. 프롬프트 입장에서는 "주어진 베이스를 그대로 보존"이라는 계약이 그대로다.

### 기각한 대안

| 대안 | 기각 사유 |
|---|---|
| 프롬프트에 `bodyVolume` 축 추가 | `Keep the SAME mannequin body` 룰과 정면 충돌. `mannequinBasePreserved` 가 상시 `altered` 판정 → QC 오탐. 같은 값이어도 컷마다 볼륨이 달라져 A/B 후보 간 몸이 불일치 |
| 베이스 이미지 프로그램적 메쉬 워프 | 그림자·라이팅·마네킹 표면이 왜곡돼 티가 남. 구현 부담 대비 품질 보장 없음 |
| 사용 시점 lazy 빌드 + 캐시 | 매트릭스가 전역 공용(상품·유저 무관)이고 9칸으로 유한하므로 오버엔지니어링. 첫 사용자에게 생성 지연이 전가됨 |

## 3. 설계

### 3-1. 베이스 에셋 매트릭스 (3×3)

여성 베이스를 `bust × hip` 3단계 매트릭스로 확장한다. 남성은 현행 단일 에셋 유지.

|  | hip: slim | hip: regular | hip: volume |
|---|---|---|---|
| **bust: slim** | 신규 | 신규 | 신규 |
| **bust: regular** | 신규 | **현행 에셋 재사용** | 신규 |
| **bust: volume** | 신규 | 신규 | 신규 |

`regular/regular` 은 매트릭스에 **넣지 않고** 기존 `MANNEQUIN_BASE_WOMEN_ASSET_ID` 로 폴백한다. 기본값을 그대로 둔 셀러는 현행과 픽셀 단위로 동일한 결과를 얻는다(회귀 0).

**조달**: `server/scripts/seed_mannequin_matrix.py` 신규. 현행 여성 베이스를 입력 이미지로 주고, 고정 프롬프트로 8칸을 생성한다. 프롬프트 계약:

- 포즈·카메라 프레이밍·배경·라이팅·발 위치·머리 형태 **동결**
- `bust` / `hip` 볼륨만 지정 단계로 변경
- 전신 포함, 세로 방향, 맨발 유지 (현행 베이스와 동일 규격)

R2 키는 결정적: `seed/mannequin/base-women-b{bust}_h{hip}-2K.png`. `seed_phase4.py:66-88` 의 멱등 패턴(r2_key 기준 upsert + `append_env`)을 그대로 따른다. 칸 단위 재생성이 가능하도록 `--cell bust_hip` 인자를 받는다.

**검수 게이트**: 스크립트는 생성 후 자동 승격하지 않는다. 8장을 나란히 출력해 사람이 포즈·프레이밍 일치를 육안 확인하고, 승인한 칸만 env 에 기록한다.

**설정**: env 를 9개로 늘리지 않고 JSON 맵 1개로 둔다.

```
MANNEQUIN_BASE_WOMEN_MATRIX={"slim_slim":"<assetId>","slim_regular":"<assetId>", ...}
```

`server/app/config.py` 에 `base_mannequin_women_matrix: dict[str, str]` 추가. 파싱 실패·미설정이면 빈 맵(→ 전부 현행 베이스로 폴백).

### 3-2. 데이터 계약

```
analysis.mannequinBody = {
  bust: 'slim' | 'regular' | 'volume',
  hip:  'slim' | 'regular' | 'volume',
}
```

**`fit_axes` 카탈로그에 넣지 않는다.** fit 축은 프롬프트에 보간되고 `adjusted_axes` 계산·fit QC·프로필 diff 에 전부 물려 있다. 체형은 에셋 선택에만 쓰이고 프롬프트에는 한 글자도 들어가지 않아야 이 경로들이 오염되지 않는다.

신규 순수 모듈 `server/app/agents/mannequin_body.py`:

```python
VOLUME_LEVELS = ("slim", "regular", "volume")

def normalize(raw: dict | None, gender: str) -> dict | None:
    """여성 베이스에만 적용. gender != 'women' 이면 None.
    카탈로그 밖 값은 드롭 후 'regular'. 항상 두 축이 채워진 dict 또는 None."""
```

- `gender != "women"` → `None` (남성은 매트릭스가 없음)
- 미지 값·타입 불일치 → 해당 축 `regular`
- DB/IO 없음 → 유닛 테스트 대상

마네킹 잡 result 에 정규화된 `mannequinBody` 를 스냅샷으로 기록한다(재현성 — 나중에 "이 컷이 어느 베이스로 생성됐나"를 되짚을 수 있어야 한다).

### 3-3. 워커 배선

`server/app/agents/mannequin.py` 에 추가:

```python
def select_base_asset_id(settings, analysis) -> str | None:
    """gender + mannequinBody → 베이스 에셋 id.
    매트릭스 미설정·조합 미스·남성 → 현행 단일 에셋으로 폴백."""
```

`server/app/workers/mannequin_job.py:405-407` 를 이 헬퍼 호출로 교체한다:

```python
# before
gender = mannequin.select_base_gender(analysis)
base_asset_id = (s.base_mannequin_men_asset_id if gender == "men"
                 else s.base_mannequin_women_asset_id)

# after
gender = mannequin.select_base_gender(analysis)
base_asset_id = mannequin.select_base_asset_id(s, analysis)
```

폴백이 항상 현행 값을 반환하므로 **매트릭스를 배포하기 전에 코드를 먼저 배포해도 안전**하다(배포 순서 무관).

`base_mannequin_missing` 실패 경로와 `mannequinBasePreserved` QC 는 그대로 둔다.

**생성 프롬프트는 0줄 변경.**

### 3-4. UI

`src/features/analysis/AnalysisForm.jsx` — 핏 설정 행 바로 아래에 새 행:

```
핏      [타이트] [슬림] [레귤러] [세미오버] [오버]
체형    가슴 [슬림] [보통] [볼륨]
        힙   [슬림] [보통] [볼륨]
```

- 기본값 `보통/보통` — 아무것도 만지지 않으면 현행과 동일
- `genderOf(a.targetGenders) !== 'women'` → 행 숨김 + 값 드롭. 성별이 바뀌면 리셋(`withFitProfile` 의 카테고리·성별 변경 리셋 패턴과 동일한 방어)
- 저장 위치는 `fitProfile` **밖**. `withFitProfile` 에 얹지 않고 별도 `withMannequinBody(patch)` 헬퍼로 `analysis.mannequinBody` 를 갱신한다
- `src/lib/mannequinBody.js` = 백엔드 `mannequin_body.py` 와 수동 미러 (`fitAxes.js` ↔ `fit_axes.py` 선례를 따름). 상단에 미러 관계를 주석으로 명시

### 3-5. tuck-in 수정

**영문** `server/prompts/mannequin_generate_v1.txt` — 조건 분기 제거:

```
- MATCHING BOTTOM (if attached, the last image): also dress the mannequin in this bottom
  garment, coordinated naturally with the top. If the main product is a top or outerwear,
  ALWAYS keep it untucked with its entire hem fully visible — never tuck it into the
  matching bottom, and never let the bottom cover its hem. This holds whether or not a
  length axis is declared in FIT PROFILE. Coordinate proportion and layering only in ways
  that keep the main product's hem visible.
```

`<critical rules>` 에 1줄 추가해 강제력을 올린다:

```
- The main product is never tucked in and never hidden by the matching bottom.
```

**한국어** `server/prompts/mannequin_generate_v1.ko.txt:27` — "적절한 레이어링·턱·비율" 에서 턱을 제거하고 위와 동등한 문구로 교체.

**적용 범위**: 주상품이 상의·아우터일 때만. 주상품이 하의·스커트·원피스면 매칭 상의는 현행대로 자유롭게 코디한다(그건 셀러의 상품이 아니다).

## 4. 테스트

**신규 `server/tests/test_mannequin_body.py`** (순수 함수):

- `normalize` — 유효 값 통과 / 남성 → `None` / 미지 값 → `regular` / `None` 입력 → `None`
- `select_base_asset_id` — 매트릭스 히트 / 조합 미스 → 현행 폴백 / 매트릭스 미설정 → 현행 폴백 / 남성 → 남성 에셋

**골든 프롬프트**:

- `server/tests/golden/mannequin_generate_top_women_slim_long.txt` 재생성 (프롬프트 문구가 바뀌므로 `server/tests/test_mannequin_fit_profile.py:636` 이 깨진다)
- **골든 1개 추가** — `length` **미선언** 여성 상의 케이스. untuck 문구가 프롬프트에 포함되는지 검증한다. 이번 버그의 회귀 테스트

**회귀**:

- `mannequinBody` 없음 / `regular,regular` → `select_base_asset_id` 가 기존과 동일한 asset id 를 반환
- 기존 마네킹 테스트 스위트 전체 통과

## 5. 리스크

| 리스크 | 대응 |
|---|---|
| 신규 8장의 포즈·프레이밍이 원본과 미세하게 어긋나 컷 간 구도가 흔들림 | 육안 검수 게이트 필수. 스크립트가 칸 단위 재생성(`--cell`)을 지원 |
| 옷을 입히면 볼륨 차이가 안 보임 (특히 오버핏 상품) | 검수 시 각 칸을 옷 입힌 상태로도 1장 뽑아 체감 확인. 차이가 안 보이면 3단 간격을 더 벌려 재생성 |
| tuck 강제로 특정 상품이 부자연스러워짐 | 크롭·기본 기장 상의는 원래 untuck 이 정상. 골든셋으로 확인 |
| prod 가 한국어 템플릿을 쓰고 있으면 문구 차이로 결과가 갈림 | 착수 전 prod `MANNEQUIN_PROMPT_FILE` env 확인. 두 템플릿을 동시에 수정해 어느 쪽이든 동일 동작 보장 |
| 매트릭스 JSON 오타 → 잘못된 에셋 참조 | 파싱 실패 시 빈 맵으로 폴백. 스타트업 검증에서 매트릭스 asset id 존재 여부 확인 |

## 6. 범위 밖

- 남성 베이스 체형 매트릭스 (여성만)
- 키·어깨너비 등 볼륨 외 체형 축
- 마네킹컷 생성 후 화면에서의 체형 재조정 (분석 화면 생성 전 설정만)
- 연속 슬라이더 UI (3단 이산 선택만 — 기존 핏 축과 동일한 UX 형태)
