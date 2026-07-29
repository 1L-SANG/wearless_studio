# 마네킹 체형 볼륨 조절 + 상의 tuck-in 수정 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 여성 마네킹 베이스를 `bust × hip` 3×3 체형 매트릭스로 확장해 셀러가 분석 화면에서 볼륨을 고를 수 있게 하고, 매칭 하의가 있을 때 주상품 상의가 하의 안으로 tuck-in 되는 버그를 고친다.

**Architecture:** 체형은 프롬프트로 몸을 변형시키지 않는다. 워커가 `image 1` 로 넣는 **베이스 에셋 자체를 바꾼다**. 프롬프트의 `Keep the SAME mannequin body` 룰과 `mannequinBasePreserved` QC 는 그대로 성립한다. 체형 값은 `fit_axes` 카탈로그 밖의 독립 필드(`analysis.mannequinBody`)로 두어 프롬프트 보간·`adjusted_axes`·fit QC 경로를 오염시키지 않는다. 잡 실행 시점의 analysis 재독은 저장 경합으로 무음 유실을 일으키므로, 기존 `fitProfileSnapshot` 과 동일하게 **잡 페이로드 스냅샷**을 정본으로 쓴다.

**Tech Stack:** Python 3.12 / FastAPI / psycopg (server), React + Vite (frontend), pytest (server tests), `node --test` (frontend tests)

## Global Constraints

- 셀러 자유 문자열은 프롬프트의 어떤 필드에도 보간하지 않는다.
- 체형 값(`bust`/`hip`)은 프롬프트에 **한 글자도** 들어가지 않는다. 베이스 에셋 선택에만 쓴다.
- 체형 레벨은 정확히 `"slim" | "regular" | "volume"` 3개. 기본값 `"regular"`.
- `regular/regular` 조합은 매트릭스에 넣지 않고 기존 `MANNEQUIN_BASE_WOMEN_ASSET_ID` 로 폴백한다 → 기본값 셀러는 현행과 동일 결과(회귀 0).
- 매트릭스 미설정·조합 미스·남성 타깃은 전부 현행 단일 에셋으로 폴백한다 → 코드를 에셋보다 먼저 배포해도 안전.
- 남성 베이스는 현행 단일 에셋 유지. 체형 매트릭스 없음.
- 서버 테스트는 `server/` 에서 실행한다 (`pytest.ini` 의 `testpaths = tests`, 골든 파일 경로가 상대경로).
- 프론트 `src/lib/mannequinBody.js` 는 서버 `server/app/agents/mannequin_body.py` 의 **수동 미러**다 (`fitAxes.js` ↔ `fit_axes.py` 선례). 한쪽을 고치면 다른 쪽도 고친다.

---

### Task 1: 상의 tuck-in 버그 수정 (프롬프트)

매칭 하의 지시의 untuck 강제가 `length` 축 선언 시에만 걸린다. `AnalysisForm` 은 `fit` 축만 채우므로(`src/features/analysis/AnalysisForm.jsx:417`) 대부분의 잡이 tuck 허용 분기로 빠진다. 조건을 제거한다.

**Files:**
- Modify: `server/prompts/mannequin_generate_v1.txt`
- Modify: `server/prompts/mannequin_generate_v1.ko.txt:27`
- Modify: `server/tests/golden/mannequin_generate_top_women_slim_long.txt` (재생성)
- Test: `server/tests/test_mannequin_fit_profile.py`

**Interfaces:**
- Consumes: 없음 (이 태스크가 첫 태스크)
- Produces: 없음 (프롬프트 텍스트만 변경. 후속 태스크가 의존하는 심볼 없음)

- [ ] **Step 1: 실패하는 테스트를 먼저 쓴다**

`server/tests/test_mannequin_fit_profile.py` 파일 **맨 끝**에 아래를 추가한다. 골든 파일을 새로 만들지 않는 이유: 골든은 프롬프트 전문을 고정해 무관한 개정마다 깨진다. 이 케이스의 본질은 "length 미선언이어도 untuck 문구가 살아 있는가" 하나다.

```python
def test_untuck_instruction_survives_undeclared_length_axis():
    # 회귀: untuck 강제가 length 축 선언 시에만 걸리면, fit 축만 채우는 AnalysisForm 경로의
    # 모든 잡이 tuck 허용 분기로 빠져 주상품 상의가 하의 안에 넣어진다(2026-07-29).
    from app.agents.prompts import load_prompt_template, render_mannequin_prompt
    from app.agents import mannequin as m
    from conftest import make_settings
    template = load_prompt_template(make_settings())
    profile = {"category": "top", "gender": "women", "source": "seller",
               "axes": {"fit": "regular"}, "version": 1}  # length 미선언 — 실사용 기본 경로
    ctx = m.prompt_context(
        clothing_type="top", product_count=2, base_gender="women",
        image_manifest="1. Base mannequin\n2. front view of the garment\n3. matching bottom",
        fit_profile=profile, adjusted_axes=("fit",))
    prompt = render_mannequin_prompt(
        template, ctx,
        product={"name": "테스트 반팔 티셔츠", "clothing_type": "top"},
        analysis={"clothingType": "top", "targetGenders": ["women"]})
    assert "ALWAYS keep it untucked" in prompt
    assert "never tuck it into the matching bottom" in prompt
    # 조건부 분기가 남아 있으면 실패시킨다
    assert "otherwise use appropriate layering, tuck, and proportion" not in prompt
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

```bash
cd server && python -m pytest tests/test_mannequin_fit_profile.py::test_untuck_instruction_survives_undeclared_length_axis -v
```

Expected: FAIL — `assert "ALWAYS keep it untucked" in prompt` 에서 AssertionError

- [ ] **Step 3: 영문 템플릿 수정**

`server/prompts/mannequin_generate_v1.txt` 의 MATCHING BOTTOM 줄 전체를 교체한다.

찾을 문자열 (한 줄):
```
- MATCHING BOTTOM (if attached, the last image): also dress the mannequin in this bottom garment, coordinated naturally with the top. If the main product is a top or outerwear and its length axis is declared in FIT PROFILE, keep the main product untucked with its entire hem visible and do not let the matching bottom cover it; otherwise use appropriate layering, tuck, and proportion.
```

교체할 문자열 (한 줄):
```
- MATCHING BOTTOM (if attached, the last image): also dress the mannequin in this bottom garment, coordinated naturally with the top. If the main product is a top or outerwear, ALWAYS keep it untucked with its entire hem fully visible — never tuck it into the matching bottom, and never let the bottom cover its hem. This holds whether or not a length axis is declared in FIT PROFILE. Choose layering and proportion only in ways that keep the main product's hem visible.
```

- [ ] **Step 4: 영문 템플릿 `<critical rules>` 에 1줄 추가**

`server/prompts/mannequin_generate_v1.txt` 의 아래 줄을 찾아
```
- You are ONLY changing what the mannequin wears — never its head, body, pose, camera framing, or background.
```
그 **앞에** 아래 줄을 삽입한다:
```
- The main product is NEVER tucked into the matching bottom and is never hidden by it.
```

- [ ] **Step 5: 한국어 템플릿 수정**

`server/prompts/mannequin_generate_v1.ko.txt:27` 을 교체한다. 이 템플릿은 조건 분기조차 없이 턱을 상시 허용하고 있었다.

찾을 문자열:
```
- 매칭 하의(있으면, 마지막 이미지): 이 하의도 마네킹에 함께, 상의와 자연스럽게 코디해 입힌다(적절한 레이어링·턱·비율).
```

교체할 문자열:
```
- 매칭 하의(있으면, 마지막 이미지): 이 하의도 마네킹에 함께, 상의와 자연스럽게 코디해 입힌다. 주상품이 상의·아우터면 절대 하의 안에 넣지 말고, 밑단 전체가 보이도록 밖으로 빼서 입힌다 — FIT PROFILE 에 기장 축이 선언됐는지와 무관하다. 레이어링·비율은 주상품 밑단이 보이는 범위에서만 조정한다.
```

- [ ] **Step 6: 한국어 템플릿 `<critical rules>` 에 1줄 추가**

`server/prompts/mannequin_generate_v1.ko.txt` 의 아래 줄을 찾아
```
- 마네킹이 "입은 것"만 바꾼다 — 머리·몸·포즈·프레이밍·배경은 절대 바꾸지 않는다.
```
그 **앞에** 아래 줄을 삽입한다:
```
- 주상품은 절대 매칭 하의 안에 넣지 않으며, 하의에 가려지지 않는다.
```

- [ ] **Step 7: 골든 스냅샷 재생성**

프롬프트 전문이 바뀌었으므로 기존 골든(`test_prompt_golden_top_women_slim_long`)이 깨진다. 테스트와 **동일한 렌더 경로**로 재생성한다.

```bash
cd server && python - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, "."); sys.path.insert(0, "tests")
from app.agents.prompts import load_prompt_template, render_mannequin_prompt
from app.agents import mannequin as m
from conftest import make_settings

template = load_prompt_template(make_settings())
profile = {"category": "top", "gender": "women", "source": "seller",
           "axes": {"fit": "slim", "length": "long"}, "version": 1}
ctx = m.prompt_context(
    clothing_type="top", product_count=1, base_gender="women",
    image_manifest="1. Base mannequin — the canvas to dress (keep it identical)\n2. front view of the garment",
    fit_profile=profile, adjusted_axes=("fit", "length"))
prompt = render_mannequin_prompt(
    template, ctx,
    product={"name": "테스트 반팔 티셔츠", "clothing_type": "top"},
    analysis={"clothingType": "top", "targetGenders": ["women"]})
Path("tests/golden/mannequin_generate_top_women_slim_long.txt").write_text(prompt, encoding="utf-8")
print("golden rewritten")
PY
```

- [ ] **Step 8: 재생성된 골든을 눈으로 검수**

```bash
cd server && git diff tests/golden/mannequin_generate_top_women_slim_long.txt
```

Expected: MATCHING BOTTOM 줄 교체 + `<critical rules>` 1줄 추가, **그 외 변경 없음**. 다른 줄이 바뀌었으면 렌더 경로가 테스트와 어긋난 것이므로 멈추고 원인을 찾는다.

- [ ] **Step 9: 마네킹 테스트 전체 통과 확인**

```bash
cd server && python -m pytest tests/test_mannequin_fit_profile.py -v
```

Expected: PASS (신규 테스트 + 골든 테스트 포함 전부)

- [ ] **Step 10: 커밋**

```bash
git add server/prompts/mannequin_generate_v1.txt server/prompts/mannequin_generate_v1.ko.txt \
        server/tests/golden/mannequin_generate_top_women_slim_long.txt \
        server/tests/test_mannequin_fit_profile.py
git commit -m "fix(mannequin): 상의가 매칭 하의에 tuck-in 되는 버그 수정

untuck 강제가 length 축 선언 시에만 걸려, fit 축만 채우는 실사용 경로의
잡이 대부분 tuck 허용 분기로 빠졌다. 조건을 제거하고 한국어 템플릿에도
동일 규칙을 넣는다. length 미선언 회귀 테스트 추가."
```

---

### Task 2: 체형 정규화 순수 모듈 (`mannequin_body.py`)

**Files:**
- Create: `server/app/agents/mannequin_body.py`
- Test: `server/tests/test_mannequin_body.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `LEVELS: tuple[str, ...]` — `("slim", "regular", "volume")`
  - `DEFAULT: str` — `"regular"`
  - `normalize(raw, gender: str) -> dict | None` — 여성이면 `{"bust": str, "hip": str}` (두 축 항상 채워짐), 그 외 `None`
  - `matrix_key(body: dict | None) -> str | None` — `"{bust}_{hip}"`, `regular/regular` 및 무효 입력은 `None`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`server/tests/test_mannequin_body.py` 생성:

```python
"""마네킹 베이스 체형(bust/hip 볼륨) 정규화 — 순수 함수 회귀."""

from app.agents.mannequin_body import DEFAULT, LEVELS, matrix_key, normalize


def test_levels_are_exactly_three():
    assert LEVELS == ("slim", "regular", "volume")
    assert DEFAULT == "regular"


def test_normalize_keeps_valid_levels():
    assert normalize({"bust": "volume", "hip": "slim"}, "women") == {
        "bust": "volume", "hip": "slim"}


def test_normalize_returns_none_for_men():
    # 남성은 체형 매트릭스가 없다 — 현행 단일 베이스만 쓴다.
    assert normalize({"bust": "volume", "hip": "volume"}, "men") is None


def test_normalize_falls_back_to_regular_for_unknown_values():
    assert normalize({"bust": "huge", "hip": None}, "women") == {
        "bust": "regular", "hip": "regular"}


def test_normalize_handles_missing_and_non_dict_input():
    assert normalize(None, "women") == {"bust": "regular", "hip": "regular"}
    assert normalize("volume", "women") == {"bust": "regular", "hip": "regular"}
    assert normalize({}, "women") == {"bust": "regular", "hip": "regular"}


def test_normalize_is_idempotent():
    once = normalize({"bust": "slim", "hip": "volume"}, "women")
    assert normalize(once, "women") == once


def test_matrix_key_skips_the_default_combination():
    # regular/regular 은 매트릭스에 없다 — 현행 MANNEQUIN_BASE_WOMEN_ASSET_ID 가 담당한다.
    assert matrix_key({"bust": "regular", "hip": "regular"}) is None


def test_matrix_key_builds_bust_hip_key():
    assert matrix_key({"bust": "slim", "hip": "volume"}) == "slim_volume"
    assert matrix_key({"bust": "regular", "hip": "slim"}) == "regular_slim"


def test_matrix_key_rejects_invalid_input():
    assert matrix_key(None) is None
    assert matrix_key({"bust": "huge", "hip": "slim"}) is None
    assert matrix_key({"bust": "slim"}) is None
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

```bash
cd server && python -m pytest tests/test_mannequin_body.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents.mannequin_body'`

- [ ] **Step 3: 모듈 구현**

`server/app/agents/mannequin_body.py` 생성:

```python
"""마네킹 베이스 체형(가슴·힙 볼륨) — 순수 모듈. 프론트 src/lib/mannequinBody.js 와 수동 미러.

체형 값은 프롬프트에 절대 보간되지 않는다. 어떤 베이스 마네킹 이미지를 image 1 로 넣을지를
고르는 데만 쓴다 — 그래서 fit_axes 카탈로그(프롬프트 보간·adjusted_axes·fit QC 경로에
물려 있음)와 분리한다.
"""

LEVELS = ("slim", "regular", "volume")
DEFAULT = "regular"


def normalize(raw, gender: str) -> dict | None:
    """여성 베이스에만 적용. gender != 'women' 이면 None(남성은 매트릭스가 없다).

    카탈로그 밖 값·타입 불일치는 조용히 DEFAULT 로 떨어진다. 반환은 항상 두 축이 채워진
    dict 이므로 호출자가 키 존재를 방어할 필요가 없다. 이미 정규화된 값을 다시 넣어도 같다.
    """
    if gender != "women":
        return None
    src = raw if isinstance(raw, dict) else {}

    def _level(key: str) -> str:
        value = src.get(key)
        return value if value in LEVELS else DEFAULT

    return {"bust": _level("bust"), "hip": _level("hip")}


def matrix_key(body: dict | None) -> str | None:
    """베이스 에셋 매트릭스 조회 키 '{bust}_{hip}'.

    regular/regular 은 매트릭스에 넣지 않는다 — 현행 단일 에셋이 그대로 담당해야
    기본값 셀러의 결과가 바뀌지 않는다. 무효 입력도 None(→ 호출자가 현행 에셋 폴백).
    """
    if not isinstance(body, dict):
        return None
    bust, hip = body.get("bust"), body.get("hip")
    if bust not in LEVELS or hip not in LEVELS:
        return None
    if bust == DEFAULT and hip == DEFAULT:
        return None
    return f"{bust}_{hip}"
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
cd server && python -m pytest tests/test_mannequin_body.py -v
```

Expected: PASS (9 passed)

- [ ] **Step 5: 커밋**

```bash
git add server/app/agents/mannequin_body.py server/tests/test_mannequin_body.py
git commit -m "feat(mannequin): 베이스 체형(가슴·힙 볼륨) 정규화 순수 모듈

체형은 프롬프트에 보간되지 않고 베이스 에셋 선택에만 쓰이므로 fit_axes
카탈로그와 분리한다. regular/regular 은 매트릭스 키를 만들지 않아
현행 단일 에셋으로 폴백된다."
```

---

### Task 3: 매트릭스 설정 + 베이스 에셋 선택기

**Files:**
- Modify: `server/app/config.py` (Settings 필드 추가 + env 파싱)
- Modify: `server/app/agents/mannequin.py` (`select_base_asset_id` 추가)
- Test: `server/tests/test_mannequin_body.py` (Task 2 파일에 이어 씀)

**Interfaces:**
- Consumes: `app.agents.mannequin_body.matrix_key`, `app.agents.mannequin_body.LEVELS` (Task 2)
- Produces:
  - `Settings.base_mannequin_women_matrix: dict[str, str]` — 기본 `{}`
  - `mannequin.select_base_asset_id(settings, gender: str, body: dict | None) -> str | None`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`server/tests/test_mannequin_body.py` **상단 import 블록**에 아래 두 줄을 추가한다 (import 는 파일 중간에 두지 않는다):

```python
from app.agents import mannequin as m
from tests.conftest import make_settings
```

그리고 파일 **끝**에 테스트를 추가한다:

```python
MATRIX = {"slim_volume": "asset-slim-volume", "volume_volume": "asset-volume-volume"}


def test_select_base_asset_id_hits_the_matrix():
    s = make_settings(base_mannequin_women_asset_id="asset-women-default",
                      base_mannequin_men_asset_id="asset-men",
                      base_mannequin_women_matrix=MATRIX)
    assert m.select_base_asset_id(s, "women", {"bust": "slim", "hip": "volume"}) \
        == "asset-slim-volume"


def test_select_base_asset_id_falls_back_when_combination_missing():
    # 매트릭스에 없는 조합(에셋 미제작) → 현행 단일 에셋. 조용히 동작하되 결과가 바뀌지 않는다.
    s = make_settings(base_mannequin_women_asset_id="asset-women-default",
                      base_mannequin_men_asset_id="asset-men",
                      base_mannequin_women_matrix=MATRIX)
    assert m.select_base_asset_id(s, "women", {"bust": "volume", "hip": "slim"}) \
        == "asset-women-default"


def test_select_base_asset_id_falls_back_when_matrix_unset():
    # 코드를 매트릭스 env 보다 먼저 배포해도 안전해야 한다(배포 순서 무관).
    s = make_settings(base_mannequin_women_asset_id="asset-women-default",
                      base_mannequin_men_asset_id="asset-men")
    assert m.select_base_asset_id(s, "women", {"bust": "slim", "hip": "volume"}) \
        == "asset-women-default"


def test_select_base_asset_id_default_body_matches_current_behavior():
    s = make_settings(base_mannequin_women_asset_id="asset-women-default",
                      base_mannequin_men_asset_id="asset-men",
                      base_mannequin_women_matrix=MATRIX)
    assert m.select_base_asset_id(s, "women", None) == "asset-women-default"
    assert m.select_base_asset_id(
        s, "women", {"bust": "regular", "hip": "regular"}) == "asset-women-default"


def test_select_base_asset_id_men_ignores_body():
    s = make_settings(base_mannequin_women_asset_id="asset-women-default",
                      base_mannequin_men_asset_id="asset-men",
                      base_mannequin_women_matrix=MATRIX)
    assert m.select_base_asset_id(s, "men", {"bust": "slim", "hip": "volume"}) == "asset-men"
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

```bash
cd server && python -m pytest tests/test_mannequin_body.py -v -k select_base_asset_id
```

Expected: FAIL — `TypeError: Settings.__init__() got an unexpected keyword argument 'base_mannequin_women_matrix'`

- [ ] **Step 3: Settings 필드 추가**

`server/app/config.py` 상단 import 를 바꾼다:

```python
from dataclasses import dataclass, field
```

`base_mannequin_men_asset_id: str | None = None` (line 72) **바로 아래**에 추가:

```python
    # 여성 베이스 체형 매트릭스 — {"{bust}_{hip}": assetId}. bust/hip ∈ slim|regular|volume.
    # regular_regular 은 넣지 않는다(base_mannequin_women_asset_id 가 담당). 미설정·파싱 실패는
    # 빈 맵 → 전부 현행 단일 에셋 폴백이라 에셋보다 코드를 먼저 배포해도 안전하다.
    base_mannequin_women_matrix: dict[str, str] = field(default_factory=dict)
```

- [ ] **Step 4: env 파서 추가**

`server/app/config.py` 의 `import os` 아래에 `import json` 을 추가하고, 모듈 레벨(첫 `@dataclass` 선언 **위**)에 헬퍼를 넣는다:

```python
def _json_str_map(raw: str | None) -> dict[str, str]:
    """JSON object(str→str) 파싱. 실패·형식 불일치는 빈 맵 — 설정 오타가 부팅을 막지 않는다."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items()
            if isinstance(k, str) and isinstance(v, str) and v}
```

`server/app/config.py:205` 의 `base_mannequin_men_asset_id=...` 줄 **바로 아래**에 추가:

```python
        base_mannequin_women_matrix=_json_str_map(os.getenv("MANNEQUIN_BASE_WOMEN_MATRIX")),
```

- [ ] **Step 5: 선택기 구현**

`server/app/agents/mannequin.py` 의 import 를 바꾼다:

```python
from . import mannequin_body
from .prompts import MannequinPromptContext
```

`select_base_gender` 함수 **바로 아래**에 추가:

```python
def select_base_asset_id(settings, gender: str, body: dict | None) -> str | None:
    """베이스 마네킹 에셋 id — 여성 체형 매트릭스 우선, 없으면 현행 단일 에셋.

    남성·매트릭스 미설정·조합 미스는 전부 현행 값으로 폴백한다. 그래서 매트릭스 env 없이
    코드만 먼저 배포해도 동작이 변하지 않는다(배포 순서 무관). 프롬프트는 이 선택을
    알지 못한다 — "image 1 을 그대로 보존하라"는 계약이 그대로 유지된다.
    """
    if gender == "men":
        return settings.base_mannequin_men_asset_id
    key = mannequin_body.matrix_key(body)
    if key:
        asset_id = (settings.base_mannequin_women_matrix or {}).get(key)
        if asset_id:
            return asset_id
    return settings.base_mannequin_women_asset_id
```

- [ ] **Step 6: 테스트 통과 확인**

```bash
cd server && python -m pytest tests/test_mannequin_body.py -v
```

Expected: PASS (14 passed)

- [ ] **Step 7: env 파서 단위 확인**

```bash
cd server && python -c "
import sys; sys.path.insert(0,'.')
from app.config import _json_str_map
assert _json_str_map(None) == {}
assert _json_str_map('not json') == {}
assert _json_str_map('[1,2]') == {}
assert _json_str_map('{\"slim_volume\":\"a1\",\"bad\":3,\"empty\":\"\"}') == {'slim_volume':'a1'}
print('ok')
"
```

Expected: `ok`

- [ ] **Step 8: 커밋**

```bash
git add server/app/config.py server/app/agents/mannequin.py server/tests/test_mannequin_body.py
git commit -m "feat(mannequin): 여성 베이스 체형 매트릭스 설정 + 에셋 선택기

MANNEQUIN_BASE_WOMEN_MATRIX(JSON 맵 1개)로 bust_hip 조합을 에셋 id 에
매핑한다. 미설정·조합 미스·남성은 현행 단일 에셋 폴백이라 에셋보다
코드를 먼저 배포해도 결과가 바뀌지 않는다."
```

---

### Task 4: 잡 스냅샷 + 워커 배선

체형을 워커가 analysis 에서 재독하면 잡 생성↔실행 사이의 저장 경합으로 다른 체형이 조용히 쓰인다 — `fitProfileSnapshot` 이 이미 해결한 문제다(`server/app/workers/mannequin_job.py:463-465`). 동일 규율로 페이로드 스냅샷을 추가한다.

**Files:**
- Modify: `server/app/routes.py` (스냅샷 헬퍼 + generate/regenerate 페이로드)
- Modify: `server/app/workers/mannequin_job.py:405-407` (베이스 에셋 선택), 결과 metadata
- Test: `server/tests/test_mannequin_body.py`

**Interfaces:**
- Consumes: `mannequin.select_base_asset_id` (Task 3), `mannequin_body.normalize` (Task 2)
- Produces:
  - 잡 페이로드 키 `mannequinBodySnapshot` — `{"version": 1, "gender": str, "body": dict | None}`
  - `mannequin_job._mannequin_body_from_job(job: dict, analysis: dict, gender: str) -> dict | None`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`server/tests/test_mannequin_body.py` **상단 import 블록**에 추가:

```python
from app.workers import mannequin_job
```

그리고 파일 **끝**에 테스트를 추가한다:

```python
def test_body_from_job_prefers_the_payload_snapshot():
    # 스냅샷이 정본 — 잡 생성 후 셀러가 analysis 를 바꿔도 이번 잡은 잡힌 값으로 돈다.
    job = {"payload": {"mannequinBodySnapshot": {
        "version": 1, "gender": "women", "body": {"bust": "volume", "hip": "slim"}}}}
    analysis = {"mannequinBody": {"bust": "slim", "hip": "slim"}}
    assert mannequin_job._mannequin_body_from_job(job, analysis, "women") == {
        "bust": "volume", "hip": "slim"}


def test_body_from_job_falls_back_to_analysis_for_legacy_jobs():
    # 키가 없는 legacy 잡만 analysis 폴백(fitProfileSnapshot 과 동일 규율).
    job = {"payload": {"mode": "generate"}}
    analysis = {"mannequinBody": {"bust": "slim", "hip": "volume"}}
    assert mannequin_job._mannequin_body_from_job(job, analysis, "women") == {
        "bust": "slim", "hip": "volume"}


def test_body_from_job_returns_none_for_men():
    job = {"payload": {"mannequinBodySnapshot": {
        "version": 1, "gender": "women", "body": {"bust": "volume", "hip": "volume"}}}}
    assert mannequin_job._mannequin_body_from_job(job, {}, "men") is None


def test_body_from_job_ignores_unknown_snapshot_version():
    # 미래 버전 스냅샷은 신뢰하지 않고 analysis 폴백 — 조용한 오해석보다 낫다.
    job = {"payload": {"mannequinBodySnapshot": {"version": 99, "body": {"bust": "volume"}}}}
    analysis = {"mannequinBody": {"bust": "slim", "hip": "slim"}}
    assert mannequin_job._mannequin_body_from_job(job, analysis, "women") == {
        "bust": "slim", "hip": "slim"}
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

```bash
cd server && python -m pytest tests/test_mannequin_body.py -v -k body_from_job
```

Expected: FAIL — `AttributeError: module 'app.workers.mannequin_job' has no attribute '_mannequin_body_from_job'`

- [ ] **Step 3: 워커 헬퍼 구현**

`server/app/workers/mannequin_job.py` 의 import 블록에 추가:

```python
from ..agents import mannequin_body
```

모듈 레벨 함수로 (`_build_manifest` 등 다른 순수 헬퍼 곁에) 추가:

```python
def _mannequin_body_from_job(job: dict, analysis: dict, gender: str) -> dict | None:
    """체형은 잡 생성 시점 스냅샷이 정본(fitProfileSnapshot 과 동일 규율).

    워커가 analysis 를 재독하면 잡 생성↔실행 사이의 저장 경합으로 다른 체형이 조용히
    쓰인다. 키가 없는 legacy 잡과 알 수 없는 버전만 analysis 로 폴백한다.
    normalize 는 멱등이라 스냅샷을 다시 정규화해도 값이 변하지 않는다.
    """
    snap = (job.get("payload") or {}).get("mannequinBodySnapshot")
    if isinstance(snap, dict) and snap.get("version") == 1:
        return mannequin_body.normalize(snap.get("body"), gender)
    return mannequin_body.normalize(analysis.get("mannequinBody"), gender)
```

- [ ] **Step 4: 워커 베이스 에셋 선택 교체 (+ 매트릭스 오설정 폴백)**

`server/app/workers/mannequin_job.py:405-409` 를 교체한다. 매트릭스에 오타난 asset id 가 들어 있으면 잡이 `base_mannequin_missing` 으로 죽는데, 그건 셀러 잘못이 아니다 — 현행 베이스로 조용히 물러난다.

찾을 코드:
```python
            gender = mannequin.select_base_gender(analysis)
            base_asset_id = (s.base_mannequin_men_asset_id if gender == "men"
                             else s.base_mannequin_women_asset_id)
            base_asset = (await repo.get_asset_for_user(conn, user_id, base_asset_id)
                          if base_asset_id else None)
```

교체할 코드:
```python
            gender = mannequin.select_base_gender(analysis)
            body = _mannequin_body_from_job(job, analysis, gender)
            base_asset_id = mannequin.select_base_asset_id(s, gender, body)
            base_asset = (await repo.get_asset_for_user(conn, user_id, base_asset_id)
                          if base_asset_id else None)
            if base_asset is None and base_asset_id != s.base_mannequin_women_asset_id \
                    and gender != "men":
                # 매트릭스 asset id 오설정 — 셀러 잡을 죽이지 않고 현행 베이스로 물러난다.
                log.warning("mannequin base matrix asset missing: %s (body=%s)", base_asset_id, body)
                body = None
                base_asset_id = s.base_mannequin_women_asset_id
                base_asset = (await repo.get_asset_for_user(conn, user_id, base_asset_id)
                              if base_asset_id else None)
```

- [ ] **Step 5: 결과 metadata 에 체형 스냅샷 기록**

`server/app/workers/mannequin_job.py` 의 `finalize_mannequin_success` 호출 metadata 를 바꾼다 (재현성 — 나중에 "이 컷이 어느 베이스로 생성됐나"를 되짚기 위해).

찾을 코드:
```python
                metadata={"creditCostVersion": s.credit_cost_version,
                          "promptVersion": s.mannequin_prompt_version, "gender": gender})
```

교체할 코드:
```python
                metadata={"creditCostVersion": s.credit_cost_version,
                          "promptVersion": s.mannequin_prompt_version, "gender": gender,
                          "mannequinBody": body})
```

- [ ] **Step 6: 테스트 통과 확인**

```bash
cd server && python -m pytest tests/test_mannequin_body.py -v
```

Expected: PASS (18 passed)

- [ ] **Step 7: 라우트 스냅샷 헬퍼 추가**

`server/app/routes.py` 의 `_fit_profile_snapshot` 함수 **바로 아래**에 추가한다. import 블록에 `mannequin_body` 가 없으면 `from .agents import mannequin_body` 를 기존 agents import 옆에 추가한다.

```python
async def _mannequin_body_snapshot(conn, project_id: str) -> dict:
    """잡 생성 시점 체형 스냅샷 — 워커의 불변 입력(fitProfileSnapshot 과 동일 규율).

    체형은 프롬프트에 들어가지 않고 베이스 마네킹 에셋 선택에만 쓰인다. gender 는 provenance
    용으로만 싣는다 — 워커는 자신의 select_base_gender 결과를 쓴다.
    """
    analysis = await repo.get_analysis(conn, project_id) or {}
    gender = mannequin.select_base_gender(analysis)
    return {"version": 1, "gender": gender,
            "body": mannequin_body.normalize(analysis.get("mannequinBody"), gender)}
```

- [ ] **Step 8: generate 라우트 페이로드에 스냅샷 추가**

`server/app/routes.py:927-930` 를 교체한다.

찾을 코드:
```python
        snapshot = await _fit_profile_snapshot(conn, project_id, None)
        job, created = await repo.create_job(
            conn, user_id=user_id, project_id=project_id, kind="mannequin",
            payload={"mode": "generate", "fitProfileSnapshot": snapshot}, idempotency_key=scoped_key,
```

교체할 코드:
```python
        snapshot = await _fit_profile_snapshot(conn, project_id, None)
        body_snapshot = await _mannequin_body_snapshot(conn, project_id)
        job, created = await repo.create_job(
            conn, user_id=user_id, project_id=project_id, kind="mannequin",
            payload={"mode": "generate", "fitProfileSnapshot": snapshot,
                     "mannequinBodySnapshot": body_snapshot}, idempotency_key=scoped_key,
```

- [ ] **Step 9: regenerate 라우트 페이로드에 스냅샷 추가**

`server/app/routes.py:1032-1041` 를 교체한다.

찾을 코드:
```python
        snapshot = await _fit_profile_snapshot(
            conn,
            project_id,
            body.get("fitProfile"),
            validate_matching_fit=True,
        )
        job, created = await repo.create_job(
            conn, user_id=user_id, project_id=project_id, kind="mannequin",
            payload={"mode": "regenerate", "fitProfile": body.get("fitProfile"),
                     "fitProfileSnapshot": snapshot},
```

교체할 코드:
```python
        snapshot = await _fit_profile_snapshot(
            conn,
            project_id,
            body.get("fitProfile"),
            validate_matching_fit=True,
        )
        body_snapshot = await _mannequin_body_snapshot(conn, project_id)
        job, created = await repo.create_job(
            conn, user_id=user_id, project_id=project_id, kind="mannequin",
            payload={"mode": "regenerate", "fitProfile": body.get("fitProfile"),
                     "fitProfileSnapshot": snapshot,
                     "mannequinBodySnapshot": body_snapshot},
```

- [ ] **Step 10: 마네킹 관련 서버 테스트 전체 회귀 확인**

```bash
cd server && python -m pytest tests/ -k mannequin -q
```

Expected: PASS. 실패가 나오면 기존 잡 페이로드 assert 가 새 키를 몰라서일 수 있다 — 그 테스트를 새 키까지 포함하도록 고친다(키 제거 금지).

- [ ] **Step 11: 커밋**

```bash
git add server/app/routes.py server/app/workers/mannequin_job.py server/tests/test_mannequin_body.py
git commit -m "feat(mannequin): 체형 잡 스냅샷 + 베이스 에셋 선택 배선

체형을 워커가 analysis 에서 재독하면 잡 생성↔실행 사이 저장 경합으로
다른 값이 조용히 쓰인다. fitProfileSnapshot 과 동일하게 페이로드
스냅샷을 정본으로 두고, 키 없는 legacy 잡만 analysis 폴백한다.
결과 metadata 에 사용된 체형을 남겨 재현 가능하게 한다."
```

---

### Task 5: 프론트 미러 모듈 + 분석 화면 체형 UI

**Files:**
- Create: `src/lib/mannequinBody.js`
- Modify: `src/lib/api/shapes.js:158` (`defaultAnalysisShape` 에 필드 추가)
- Modify: `src/features/analysis/AnalysisForm.jsx` (import, 헬퍼, 성별 스코프, JSX 행)
- Test: `tests/frontend/mannequin-body.test.mjs`

**Interfaces:**
- Consumes: 서버 `mannequin_body.py` 의 값 계약 (Task 2) — 레벨 3개·기본 `regular`
- Produces:
  - `BODY_LEVELS: ReadonlyArray<{value: string, label: string}>`
  - `DEFAULT_BODY_LEVEL: string`
  - `normalizeMannequinBody(raw, gender) -> {bust, hip} | null`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/frontend/mannequin-body.test.mjs` 생성:

```javascript
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  BODY_LEVELS,
  DEFAULT_BODY_LEVEL,
  normalizeMannequinBody,
} from '../../src/lib/mannequinBody.js';

test('levels mirror the server catalog exactly', () => {
  assert.deepEqual(BODY_LEVELS.map((o) => o.value), ['slim', 'regular', 'volume']);
  assert.equal(DEFAULT_BODY_LEVEL, 'regular');
});

test('keeps valid levels for women', () => {
  assert.deepEqual(
    normalizeMannequinBody({ bust: 'volume', hip: 'slim' }, 'women'),
    { bust: 'volume', hip: 'slim' },
  );
});

test('returns null for men — the matrix is women-only', () => {
  assert.equal(normalizeMannequinBody({ bust: 'volume', hip: 'volume' }, 'men'), null);
});

test('falls back to regular for unknown or missing values', () => {
  assert.deepEqual(
    normalizeMannequinBody({ bust: 'huge' }, 'women'),
    { bust: 'regular', hip: 'regular' },
  );
  assert.deepEqual(
    normalizeMannequinBody(null, 'women'),
    { bust: 'regular', hip: 'regular' },
  );
});
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

```bash
node --test tests/frontend/mannequin-body.test.mjs
```

Expected: FAIL — `Cannot find module '.../src/lib/mannequinBody.js'`

- [ ] **Step 3: 미러 모듈 구현**

`src/lib/mannequinBody.js` 생성:

```javascript
// 마네킹 베이스 체형(가슴·힙 볼륨) — 서버 server/app/agents/mannequin_body.py 와 수동 미러.
// 값은 프롬프트에 들어가지 않는다. 어떤 베이스 마네킹 이미지를 쓸지 고르는 데만 쓰인다.
// (fitAxes.js ↔ fit_axes.py 와 동일한 미러 규약 — 한쪽을 고치면 다른 쪽도 고친다.)
export const BODY_LEVELS = Object.freeze([
  Object.freeze({ value: 'slim', label: '슬림' }),
  Object.freeze({ value: 'regular', label: '보통' }),
  Object.freeze({ value: 'volume', label: '볼륨' }),
]);

export const DEFAULT_BODY_LEVEL = 'regular';

// 여성 베이스에만 적용 — 남성은 체형 매트릭스가 없어 null.
// 카탈로그 밖 값은 조용히 기본값으로 떨어지고, 항상 두 축이 채워진 객체를 돌려준다.
export function normalizeMannequinBody(raw, gender) {
  if (gender !== 'women') return null;
  const src = raw && typeof raw === 'object' ? raw : {};
  const level = (v) => (BODY_LEVELS.some((o) => o.value === v) ? v : DEFAULT_BODY_LEVEL);
  return { bust: level(src.bust), hip: level(src.hip) };
}
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
node --test tests/frontend/mannequin-body.test.mjs
```

Expected: PASS (4 tests)

- [ ] **Step 5: analysis shape 에 필드 추가**

`src/lib/api/shapes.js:158` 의 `fitProfile: null,` **바로 아래**에 추가한다 (AnalysisForm 이 무가드로 읽는 필드는 전부 shape 에 있어야 한다 — 계약 §6):

```javascript
    mannequinBody: null,
```

- [ ] **Step 6: AnalysisForm import 추가**

`src/features/analysis/AnalysisForm.jsx:14` 의 `import { axesFor, fitProfileCategory } from '@/lib/fitAxes.js';` **바로 아래**에 추가:

```javascript
import { BODY_LEVELS, normalizeMannequinBody } from '@/lib/mannequinBody.js';
```

- [ ] **Step 7: 성별 스코프 + 체형 편집 헬퍼 추가**

`src/features/analysis/AnalysisForm.jsx` 의 `const fitOpts = fitOptsOf(a).opts;` (line 435) **바로 아래**에 추가:

```jsx
  // 체형 = 베이스 마네킹 선택(가슴·힙 볼륨). fitProfile 밖의 독립 필드 — 프롬프트에 안 들어간다.
  // 여성 타깃일 때만 값이 존재하고, 그 외엔 null 이라 행 자체가 렌더되지 않는다.
  const body = normalizeMannequinBody(a.mannequinBody, genderOf(a.targetGenders));
  const setBody = (axis, value) => onChange({
    mannequinBody: { ...normalizeMannequinBody(a.mannequinBody, 'women'), [axis]: value },
  });
  // 남성 타깃으로 바뀌면 체형 값을 떨군다 — fitProfile 의 성별 변경 축 리셋과 같은 방어.
  const withGenderScope = (patch) => (
    genderOf('targetGenders' in patch ? patch.targetGenders : a.targetGenders) === 'women'
      ? patch
      : { ...patch, mannequinBody: null });
```

- [ ] **Step 8: 성별 Chips 가 체형을 떨구도록 배선**

`src/features/analysis/AnalysisForm.jsx:468` 을 교체한다.

찾을 코드:
```jsx
            <Chips options={catalogs.genders} value={a.targetGenders?.[0] || null} onChange={(v) => onChange(withFitProfile({ targetGenders: v ? [v] : [] }))} /></div>
```

교체할 코드:
```jsx
            <Chips options={catalogs.genders} value={a.targetGenders?.[0] || null} onChange={(v) => onChange(withGenderScope(withFitProfile({ targetGenders: v ? [v] : [] })))} /></div>
```

- [ ] **Step 9: 체형 행 JSX 추가**

`src/features/analysis/AnalysisForm.jsx:469-471` 의 핏 행 블록 **바로 아래**에 추가한다.

찾을 코드 (핏 행의 닫는 `)}` 까지):
```jsx
          {fitOpts.length > 0 && (
            <div className="field-row"><label className="lbl">핏</label>
              <Chips options={fitOpts} value={a.fit} onChange={(v) => onChange(withFitProfile({ fit: v }, 'seller'))} /></div>
          )}
```

교체할 코드:
```jsx
          {fitOpts.length > 0 && (
            <div className="field-row"><label className="lbl">핏</label>
              <Chips options={fitOpts} value={a.fit} onChange={(v) => onChange(withFitProfile({ fit: v }, 'seller'))} /></div>
          )}
          {body && (
            <>
              <div className="field-row"><label className="lbl">체형 · 가슴</label>
                <Chips options={BODY_LEVELS} value={body.bust} onChange={(v) => setBody('bust', v)} /></div>
              <div className="field-row"><label className="lbl">체형 · 힙</label>
                <Chips options={BODY_LEVELS} value={body.hip} onChange={(v) => setBody('hip', v)} /></div>
            </>
          )}
```

핏 행 블록의 실제 들여쓰기·줄바꿈이 위와 다르면 **파일의 원문을 그대로 두고** 그 아래에 체형 블록만 삽입한다.

- [ ] **Step 10: 프론트 테스트 + 빌드 확인**

```bash
npm run test:frontend 2>&1 | grep -E "^ℹ (tests|pass|fail)|✖" | head -6
pnpm build 2>&1 | tail -1
```

Expected: 프론트 테스트 전부 pass, 빌드 성공

- [ ] **Step 11: 화면에서 눈으로 확인**

개발 서버를 띄우고 분석 화면에서 확인한다:
1. 성별을 여성으로 두면 "체형 · 가슴", "체형 · 힙" 행이 보이고 기본값이 `보통` 이다
2. 성별을 남성으로 바꾸면 두 행이 사라진다
3. 다시 여성으로 바꾸면 `보통/보통` 으로 돌아온다 (남성 전환 시 값을 떨궜으므로)

- [ ] **Step 12: 커밋**

```bash
git add src/lib/mannequinBody.js src/lib/api/shapes.js \
        src/features/analysis/AnalysisForm.jsx tests/frontend/mannequin-body.test.mjs
git commit -m "feat(analysis): 마네킹 체형(가슴·힙 볼륨) 선택 UI

여성 타깃일 때만 핏 설정 아래에 3단 체형 행을 노출한다. 값은
fitProfile 밖의 analysis.mannequinBody 로 저장돼 프롬프트에 들어가지
않고 베이스 마네킹 에셋 선택에만 쓰인다. 남성 전환 시 값을 떨군다."
```

---

### Task 6: 베이스 에셋 매트릭스 시드 스크립트

8칸(=9조합 중 `regular_regular` 제외)을 현행 여성 베이스로부터 생성하고, 사람이 검수한 칸만 env 에 기록한다.

**Files:**
- Create: `server/scripts/seed_mannequin_matrix.py`
- Reference: `server/seed_phase4.py:66-88` (R2 put + assets upsert + `append_env` 멱등 패턴)

**Interfaces:**
- Consumes: `mannequin_body.LEVELS`, `mannequin_body.DEFAULT` (Task 2), `Settings.base_mannequin_women_matrix` (Task 3)
- Produces: `MANNEQUIN_BASE_WOMEN_MATRIX` env 값 (JSON 맵)

- [ ] **Step 1: 스크립트 작성**

`server/scripts/seed_mannequin_matrix.py` 생성:

```python
"""여성 베이스 마네킹 체형 매트릭스 시드 — 현행 베이스에서 8칸을 생성한다.

regular_regular 은 만들지 않는다(현행 MANNEQUIN_BASE_WOMEN_ASSET_ID 가 담당).
생성물은 자동 승격하지 않는다 — 로컬에 내려받아 사람이 포즈·프레이밍을 검수하고,
살아남은 파일만 --promote 로 R2·assets·env 에 올린다(탈락한 칸은 파일을 지우면 된다).

실행:
  cd server
  .venv/bin/python -m scripts.seed_mannequin_matrix --out ./matrix_review
  .venv/bin/python -m scripts.seed_mannequin_matrix --out ./matrix_review --cell slim_volume
  .venv/bin/python -m scripts.seed_mannequin_matrix --promote ./matrix_review
"""
import argparse
import asyncio
import itertools
import json
import pathlib
import sys
import uuid

from scripts._env import load_env

load_env()

import psycopg  # noqa: E402

from app.agents.gemini_image import GeminiImageClient, InlineImage  # noqa: E402
from app.agents.mannequin_body import DEFAULT, LEVELS  # noqa: E402
from app.agents.model_routing import resolve_model  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.r2 import IMMUTABLE_CACHE, R2Client  # noqa: E402

SERVER_ENV = pathlib.Path(__file__).resolve().parents[1] / ".env"

# regular_regular 은 제외 — 현행 단일 에셋이 담당한다(그래야 기본값 셀러의 결과가 안 바뀐다).
CELLS = [(b, h) for b, h in itertools.product(LEVELS, LEVELS) if not (b == DEFAULT and h == DEFAULT)]

# 볼륨 레벨 → 고정 영문 문구. 상수만 보간한다 — 자유 문자열이 끼어들 자리가 없다.
_VOLUME_EN = {
    "slim": "noticeably slimmer and flatter than the reference",
    "regular": "unchanged from the reference",
    "volume": "noticeably fuller and rounder than the reference",
}

PROMPT = """<role>
You are a studio product photographer preparing a fixed base mannequin for e-commerce photography.
</role>

<instruction>
- Output the SAME studio mannequin as the attached image, with ONE change: its body volume.
- Bust volume: {bust}. Hip volume: {hip}.
- Keep EVERYTHING else identical in character: the same pose, the same camera angle and framing,
  the same distance and crop, the same plain studio background, the same lighting and shadows,
  the same head shape, and the same bare feet in the same position on the floor.
- The mannequin remains undressed, matte, and featureless — no garments, no skin texture,
  no face, no hair.
</instruction>

<output format>
- Output EXACTLY ONE photorealistic image, fully opaque, portrait orientation.
- FULL BODY in frame, from the top of the head down to the feet — nothing cropped.
- No grid, no collage, no panels, no text.
</output format>"""


def cell_key(bust: str, hip: str) -> str:
    return f"{bust}_{hip}"


def r2_key(bust: str, hip: str) -> str:
    return f"seed/mannequin/base-women-{cell_key(bust, hip)}-2K.png"


def upsert_env(path: pathlib.Path, key: str, value: str) -> None:
    """append_env(seed_phase4)와 달리 기존 값을 덮어쓴다 — 매트릭스는 재승격이 정상 운영이다."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, replaced = [], False
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"  .env: {key} {'갱신' if replaced else '추가'}")


def load_base_bytes(settings, conn) -> tuple[bytes, str]:
    """현행 여성 베이스 에셋의 (bytes, mime). 미설정이면 즉시 중단한다."""
    asset_id = settings.base_mannequin_women_asset_id
    if not asset_id:
        raise SystemExit("MANNEQUIN_BASE_WOMEN_ASSET_ID 가 없습니다 — 먼저 베이스를 시드하세요.")
    with conn.cursor() as cur:
        cur.execute("select r2_key, mime_type from assets where id = %s", (asset_id,))
        row = cur.fetchone()
    if not row:
        raise SystemExit(f"베이스 에셋 {asset_id} 을 assets 에서 찾을 수 없습니다.")
    return R2Client(settings).get_bytes(row[0]), row[1]


async def generate(settings, base: InlineImage, bust: str, hip: str) -> bytes:
    gemini = GeminiImageClient(settings)
    prompt = PROMPT.format(bust=_VOLUME_EN[bust], hip=_VOLUME_EN[hip])
    res = await gemini.generate_content_image(
        resolve_model(settings, "image_high"), prompt, [base],
        settings.mannequin_image_size, aspect_ratio=settings.mannequin_aspect_ratio)
    return res.image


def cmd_out(settings, out_dir: pathlib.Path, only: str | None) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(settings.database_url) as conn:
        data, mime = load_base_bytes(settings, conn)
    (out_dir / "_reference.png").write_bytes(data)
    base = InlineImage(mime, data)
    targets = [(b, h) for b, h in CELLS if only is None or cell_key(b, h) == only]
    if not targets:
        print(f"알 수 없는 칸: {only} (가능: {[cell_key(b, h) for b, h in CELLS]})")
        return 1
    for bust, hip in targets:
        image = asyncio.run(generate(settings, base, bust, hip))
        path = out_dir / f"{cell_key(bust, hip)}.png"
        path.write_bytes(image)
        print(f"  생성 {path} ({len(image)} bytes)")
    print(f"\n검수: {out_dir}/_reference.png 와 나란히 비교하세요. "
          f"포즈·프레이밍·배경이 어긋난 칸은 파일을 지우면 승격에서 빠집니다.")
    return 0


def cmd_promote(settings, out_dir: pathlib.Path) -> int:
    r2 = R2Client(settings)
    mapping: dict[str, str] = {}
    with psycopg.connect(settings.database_url) as conn:
        for bust, hip in CELLS:
            path = out_dir / f"{cell_key(bust, hip)}.png"
            if not path.exists():
                print(f"  건너뜀 {cell_key(bust, hip)} (검수 탈락 또는 미생성)")
                continue
            data = path.read_bytes()
            key = r2_key(bust, hip)
            r2.put_bytes(key, data, "image/png", cache=IMMUTABLE_CACHE)
            with conn.cursor() as cur:  # 멱등: r2_key 기준 재사용 (seed_phase4 패턴)
                cur.execute("select id::text from assets where r2_key = %s", (key,))
                row = cur.fetchone()
                if row:
                    asset_id = row[0]
                else:
                    asset_id = str(uuid.uuid4())
                    cur.execute(
                        "insert into assets (id, user_id, project_id, source, visibility, "
                        "r2_bucket, r2_key, mime_type, byte_size) "
                        "values (%s, null, null, 'seed', 'private', %s, %s, 'image/png', %s)",
                        (asset_id, settings.r2_bucket, key, len(data)))
            mapping[cell_key(bust, hip)] = asset_id
            print(f"  승격 {cell_key(bust, hip)}: asset {asset_id} key={key}")
        conn.commit()
    if not mapping:
        print("승격할 칸이 없습니다.")
        return 1
    upsert_env(SERVER_ENV, "MANNEQUIN_BASE_WOMEN_MATRIX",
               json.dumps(mapping, ensure_ascii=False, separators=(",", ":")))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path, help="8칸 생성 후 저장할 검수 디렉터리")
    ap.add_argument("--cell", help="그 칸만 재생성 (예: slim_volume)")
    ap.add_argument("--promote", type=pathlib.Path, help="검수 통과 파일만 R2·assets·env 로 승격")
    args = ap.parse_args()
    settings = load_settings()
    if args.promote:
        return cmd_promote(settings, args.promote)
    if args.out:
        return cmd_out(settings, args.out, args.cell)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 1b: 스크립트가 임포트되는지 확인**

실제 생성 전에 배선 오류(잘못된 심볼명 등)를 먼저 잡는다.

```bash
cd server && python -c "
import sys; sys.path.insert(0,'.')
import scripts.seed_mannequin_matrix as m
assert len(m.CELLS) == 8, m.CELLS
print(sorted(m.cell_key(b,h) for b,h in m.CELLS))
"
```

Expected: 8개 키가 출력되고 `regular_regular` 은 없다.

`ImportError` 가 나면 `R2Client.put_bytes`/`IMMUTABLE_CACHE`/`resolve_model` 의 실제 이름을 `server/app/r2.py`, `server/app/agents/model_routing.py` 에서 확인해 맞춘다.

- [ ] **Step 2: 8칸 생성**

```bash
cd server && python scripts/seed_mannequin_matrix.py --out ./matrix_review
```

Expected: `matrix_review/` 에 png 9개 (`_reference.png` + 8칸)

- [ ] **Step 3: 육안 검수**

`matrix_review/_reference.png` 를 기준으로 8칸을 하나씩 비교한다. 각 칸에 대해:

1. 포즈가 같은가 (팔 각도·다리 간격·어깨 기울기)
2. 카메라 프레이밍이 같은가 (머리 위 여백·발 아래 여백·좌우 여백)
3. 배경·바닥 그림자가 같은가
4. 지시한 방향으로 볼륨이 실제로 바뀌었는가 (slim 은 더 납작, volume 은 더 풍만)

**하나라도 어긋나면 그 칸의 png 를 지우고** Step 2 를 `--cell {bust}_{hip}` 로 다시 돌린다.

- [ ] **Step 4: 옷 입힌 상태 체감 확인**

극단 두 칸(`slim_slim`, `volume_volume`)에 대해 실제 상품 사진으로 마네킹컷을 1장씩 뽑아, **옷을 입힌 뒤에도 볼륨 차이가 보이는지** 확인한다. 차이가 안 보이면 `_VOLUME_EN` 문구의 강도를 올려 8칸을 재생성한다 — 셀러가 고른 값이 결과에 안 보이면 기능이 없는 것과 같다.

- [ ] **Step 5: 승격**

```bash
cd server && python scripts/seed_mannequin_matrix.py --promote ./matrix_review
```

Expected: 검수 통과한 칸 수만큼 asset id 가 출력되고, `server/.env` 에 `MANNEQUIN_BASE_WOMEN_MATRIX` 가 기록된다.

- [ ] **Step 6: end-to-end 확인**

서버를 재시작하고, 분석 화면에서 체형을 `가슴 볼륨 / 힙 보통` 으로 바꾼 뒤 마네킹컷을 생성한다. 잡 결과 metadata 의 `mannequinBody` 가 `{"bust": "volume", "hip": "regular"}` 인지, 생성된 컷의 체형이 기본값과 다른지 확인한다.

- [ ] **Step 7: 커밋**

검수용 png 는 커밋하지 않는다 (`matrix_review/` 는 로컬 작업물).

```bash
git add server/scripts/seed_mannequin_matrix.py
git commit -m "chore(mannequin): 여성 베이스 체형 매트릭스 시드 스크립트

현행 여성 베이스에서 8칸을 생성하고, 사람이 포즈·프레이밍을 검수한
칸만 --promote 로 R2·assets·env 에 승격한다. 칸 단위 재생성 지원."
```

---

## 배포 순서

폴백이 전 구간에 깔려 있어 순서 제약이 없다. 그래도 안전한 순서는:

1. Task 1~5 코드 배포 (매트릭스 env 없음 → 모든 셀러가 현행 베이스. 단, tuck 수정은 즉시 적용)
2. Task 6 으로 에셋 승격 + `MANNEQUIN_BASE_WOMEN_MATRIX` 배포 → 체형 선택이 실제로 반영되기 시작

## 롤백

`MANNEQUIN_BASE_WOMEN_MATRIX` 를 지우면 체형 기능이 즉시 무력화되고 전부 현행 베이스로 돌아간다 (UI 는 남지만 결과에 영향 없음). tuck 수정만 되돌리려면 Task 1 커밋을 revert 한다.
