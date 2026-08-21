# SAM 잡 일시 장애 재시도 (Phase 0) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `sam_preprocess` 와 `matching_cutout` 이 SAM 일시 장애에서 영구 포기하지 않고, 톤 마스크와 같은 유한 재시도(4회 / 285초)를 받게 한다.

**Architecture:** 톤 마스크가 2026-08-18 사고 뒤 이미 갖춘 세대 키(`base:rN`) 재시도 정책을 공용 모듈 `app/services/sam_retry.py` 로 뽑고, 두 워커가 `SamUnavailable` 을 `error` 가 아니라 `done`+`state:unavailable` 로 종결하게 바꾼다. 톤 마스크는 셀러의 폴링이 재시도를 밀지만 이 둘은 폴링하는 화면이 없으므로, **디스패처와 분리된** 백그라운드 태스크가 민다.

**Tech Stack:** Python 3.12 · FastAPI · psycopg(async) · pytest · Supabase Postgres

**Spec:** `docs/superpowers/specs/2026-08-21-sam2-on-demand-scaling-design.md` §3

## Global Constraints

- 재시도 예산은 세 잡이 **같은 값**을 공유한다: `MAX_RETRIES = 4`, `BACKOFF_SECONDS = (15, 60, 90, 120)` (합 285초). 오너 결정 상한 5분.
- 인프라 장애만 재시도한다. **판정 실패(`no_garment_candidate`, `source_rejected` 등)는 재시도하지 않는다** — 같은 답이 나온다.
- 어떤 변경도 업로드·분석·마네킹 생성 경로를 막으면 안 된다. SAM 은 그 경로에서 fail-open 이다.
- 기존 톤 마스크 동작은 **바뀌지 않는다.** `tests/test_tone_mask_retry.py` 19개가 계속 통과해야 한다.
- 서버 검증: `cd server && .venv/bin/pytest -q`. 로컬 Postgres 미기동으로 `tests/test_personalization.py` 96건이 `psycopg.OperationalError` 로 error 나는 것은 **기존 상태**이며 이 작업과 무관하다. 기준선은 **2,519 passed**.
- 커밋·푸시는 오너가 요청할 때만 한다. 각 Task 의 커밋 스텝은 오너 요청이 있을 때 실행한다.

## File Structure

| 파일 | 책임 |
|---|---|
| `server/app/services/sam_retry.py` (신규) | 세 SAM 잡이 공유하는 재시도 정책 — 상수와 순수 판정 함수만. DB·IO 없음 |
| `server/app/services/editor_garment_mask.py` (수정) | 재시도 상수를 `sam_retry` 재노출로 바꾼다 (값의 단일 출처화) |
| `server/app/routes.py` (수정) | `_tone_job_*` 세 헬퍼를 `sam_retry` 위임으로 축약 |
| `server/app/services/canonical_reference.py` (수정) | `preprocess_idempotency_key` 에 `retry` 인자 추가 |
| `server/app/services/matching_cutout.py` (수정) | `cutout_job_key(project_id, item_id, retry)` 신설 |
| `server/app/workers/sam_preprocess_job.py` (수정) | `SamUnavailable` → `done`+`unavailable` |
| `server/app/workers/matching_cutout_job.py` (수정) | `SamUnavailable` → `done`+`unavailable` |
| `server/app/repo.py` (수정) | `list_retryable_sam_jobs` 조회 추가 |
| `server/app/workers/sam_retry_pusher.py` (신규) | 백그라운드 재시도 푸셔 |
| `server/app/main.py` (수정) | lifespan 에서 푸셔 기동·정지 |
| `supabase/migrations/20260821000000_sam_retry_index.sql` (신규) | 푸셔 조회용 partial index |

---

### Task 1: 공용 재시도 정책 모듈

**Files:**
- Create: `server/app/services/sam_retry.py`
- Test: `server/tests/test_sam_retry_policy.py`

**Interfaces:**
- Consumes: 없음 (순수 모듈)
- Produces: `MAX_RETRIES: int`, `BACKOFF_SECONDS: tuple[int, ...]`, `TERMINAL_STATUSES: tuple[str, ...]`, `RETRYABLE_STATES: tuple[str, ...]`, `job_retry_count(job: dict) -> int`, `job_is_retryable(job: dict, *, states: tuple[str, ...] = RETRYABLE_STATES) -> bool`, `backoff_elapsed(job: dict, *, waits: tuple[int, ...] = BACKOFF_SECONDS, now: datetime | None = None) -> bool`, `generation_key(base: str, retry: int) -> str`, `base_key(idempotency_key: str) -> str`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`server/tests/test_sam_retry_policy.py`:

```python
"""세 SAM 잡이 공유하는 재시도 정책의 순수 판정.

톤 마스크(2026-08-18 사고 2호)에서 나온 규칙을 sam_preprocess·matching_cutout 이 같이 쓴다.
원칙: 일시 장애는 판정이 아니다. 인프라 장애만 다시 돌리고, 입력에 대한 판정은 그대로 둔다.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services import sam_retry


def _job(**over):
    base = {"status": "done", "result": {"state": "unavailable"}, "payload": {},
            "finished_at": datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc)}
    return {**base, **over}


def test_budget_is_four_generations_over_285_seconds():
    assert sam_retry.MAX_RETRIES == 4
    assert sam_retry.BACKOFF_SECONDS == (15, 60, 90, 120)
    assert sum(sam_retry.BACKOFF_SECONDS) == 285


@pytest.mark.parametrize("state", ["unavailable", "unverified"])
def test_infrastructure_states_are_retryable(state):
    assert sam_retry.job_is_retryable(_job(result={"state": state})) is True


@pytest.mark.parametrize("state", ["no_garment_candidate", "source_rejected", "failed",
                                   "skipped", "ready", "partial"])
def test_input_verdicts_are_not_retryable(state):
    assert sam_retry.job_is_retryable(_job(result={"state": state})) is False


def test_lease_recovery_error_without_result_is_retryable():
    """리스 회수가 실행을 error 로 닫으면 result 가 없다. 판정이 아니라 실행 인프라 사망이다."""
    assert sam_retry.job_is_retryable(_job(status="error", result=None)) is True


def test_done_without_result_is_not_retryable():
    """리스 회수가 아닌 정상 done 은 result 가 없어도 재시도 대상이 아니다."""
    assert sam_retry.job_is_retryable(_job(status="done", result=None)) is False


def test_retry_count_reads_payload_and_survives_garbage():
    assert sam_retry.job_retry_count(_job(payload={"retry": 3})) == 3
    assert sam_retry.job_retry_count(_job(payload={})) == 0
    assert sam_retry.job_retry_count(_job(payload={"retry": "x"})) == 0
    assert sam_retry.job_retry_count(_job(payload=None)) == 0


def test_backoff_uses_the_wait_for_the_current_generation():
    fin = datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc)
    job = _job(payload={"retry": 1}, finished_at=fin)          # 다음 대기 = waits[1] = 60초
    assert sam_retry.backoff_elapsed(job, now=fin + timedelta(seconds=59)) is False
    assert sam_retry.backoff_elapsed(job, now=fin + timedelta(seconds=60)) is True


def test_backoff_is_false_once_the_budget_is_spent():
    fin = datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc)
    job = _job(payload={"retry": sam_retry.MAX_RETRIES}, finished_at=fin)
    assert sam_retry.backoff_elapsed(job, now=fin + timedelta(days=1)) is False


def test_backoff_parses_iso_strings_and_assumes_utc_when_naive():
    job = _job(payload={"retry": 0}, finished_at="2026-08-21T00:00:00Z")
    assert sam_retry.backoff_elapsed(
        job, now=datetime(2026, 8, 21, 0, 0, 20, tzinfo=timezone.utc)) is True
    naive = _job(payload={"retry": 0}, finished_at=datetime(2026, 8, 21, 0, 0))
    assert sam_retry.backoff_elapsed(
        naive, now=datetime(2026, 8, 21, 0, 0, 20, tzinfo=timezone.utc)) is True


def test_backoff_is_false_without_a_finish_time():
    assert sam_retry.backoff_elapsed(_job(finished_at=None)) is False


def test_generation_key_leaves_the_base_untouched_at_zero():
    assert sam_retry.generation_key("p:kind:x:v1", 0) == "p:kind:x:v1"
    assert sam_retry.generation_key("p:kind:x:v1", 2) == "p:kind:x:v1:r2"


def test_base_key_strips_only_a_generation_suffix():
    assert sam_retry.base_key("p:kind:x:v1:r3") == "p:kind:x:v1"
    assert sam_retry.base_key("p:kind:x:v1") == "p:kind:x:v1"
    # 'r' 로 시작하지만 숫자가 아닌 꼬리는 신원의 일부다 — 잘라내면 다른 잡이 된다.
    assert sam_retry.base_key("p:kind:x:region") == "p:kind:x:region"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd server && .venv/bin/pytest -q tests/test_sam_retry_policy.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.sam_retry'`

- [ ] **Step 3: 모듈을 구현한다**

`server/app/services/sam_retry.py`:

```python
"""SAM 잡 3종이 공유하는 일시 장애 재시도 정책.

`editor_garment_mask` 가 2026-08-18 사고 2호 뒤에 갖춘 규칙을 `sam_preprocess`·
`matching_cutout` 이 같이 쓰기 위해 뽑아낸 모듈이다. 순수 판정만 있고 DB·네트워크는 없다.

원칙: **일시 장애는 판정이 아니다.** 인프라 장애(unavailable·unverified)는 다시 돌리면 답이
바뀔 수 있으므로 유한 예산 안에서 재시도하고, 입력에 대한 판정(옷을 못 찾음 등)은 다시 돌려도
같은 답이므로 재시도하지 않는다.
"""

from __future__ import annotations

import contextlib
import re
from datetime import datetime, timedelta, timezone

#: 재시도 상한과 세대별 대기(초). 합 285초(~5분).
#:
#: 상한이 5분인 것은 오너 결정이다(2026-08-21) — "그 이상 걸리면 사용자는 어차피 이탈한다".
#: 근거는 sam2 콜드스타트 실측 101초(RUNNING+HEALTHY) + 모델 lazy load 다. 여유 2배.
#: 실측이 5분을 넘기게 되면 이 값을 늘리지 말고 기동 트리거를 앞당긴다.
MAX_RETRIES = 4
BACKOFF_SECONDS = (15, 60, 90, 120)

#: 이 상태로 끝난 잡은 더 기다릴 게 없다 — 다음 세대를 걸지 말지 판단할 시점이다.
TERMINAL_STATUSES = ("done", "error", "cancelled")

#: 인프라 장애. 입력 판정인 no_garment_candidate·source_rejected 는 의도적으로 없다.
RETRYABLE_STATES = ("unavailable", "unverified")

_GENERATION_SUFFIX = re.compile(r":r\d+$")


def job_retry_count(job: dict) -> int:
    """이 잡이 몇 번째 세대인가. payload 가 깨져 있으면 0으로 본다."""
    payload = (job or {}).get("payload") or {}
    try:
        return int(payload.get("retry") or 0)
    except (TypeError, ValueError):
        return 0


def job_is_retryable(job: dict, *, states: tuple[str, ...] = RETRYABLE_STATES) -> bool:
    """이 종결이 판정이 아니라 일시 장애인가.

    `result.state` 로 판별한다 — 구버전 워커가 `error` 로 종결해 둔 과거 잡도 state 는 같으므로
    배포 이전에 막힌 것들도 이 판별을 지나 되살아난다.
    """
    result = (job or {}).get("result") or {}
    state = str(result.get("state") or "")
    if state in states:
        return True
    # 리스 회수가 서버 재시작 중 실행을 error 로 닫으면 result 자체가 없다. 판정이 아니라
    # 실행 인프라 사망이므로 다음 세대에서 다시 시도한다.
    return str((job or {}).get("status") or "") == "error" and not state


def backoff_elapsed(job: dict, *, waits: tuple[int, ...] = BACKOFF_SECONDS,
                    now: datetime | None = None) -> bool:
    """다음 세대를 허용할 시각이 지났는가. 예산을 다 쓴 잡은 항상 False."""
    retry = job_retry_count(job)
    if retry >= len(waits):
        return False
    finished_at = (job or {}).get("finished_at")
    if isinstance(finished_at, str):
        with contextlib.suppress(ValueError):
            finished_at = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    if not isinstance(finished_at, datetime):
        return False
    if finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) >= finished_at + timedelta(seconds=waits[retry])


def generation_key(base: str, retry: int) -> str:
    """세대마다 새 신원을 준다. 같은 키로 걸면 이미 끝난 잡에 합류만 하고 아무것도 안 돈다."""
    return base if int(retry) <= 0 else f"{base}:r{int(retry)}"


def base_key(idempotency_key: str) -> str:
    """`base:rN` 에서 base 를 되찾는다. 세대 접미사가 아니면 건드리지 않는다."""
    return _GENERATION_SUFFIX.sub("", str(idempotency_key or ""))
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd server && .venv/bin/pytest -q tests/test_sam_retry_policy.py`
Expected: PASS (14 passed)

- [ ] **Step 5: 전체 회귀를 확인한다**

Run: `cd server && .venv/bin/pytest -q 2>&1 | tail -3`
Expected: `2519 passed` 이상, 새 실패 0 (personalization 96 error 는 기존 상태)

- [ ] **Step 6: 커밋 (오너 요청 시)**

```bash
git add server/app/services/sam_retry.py server/tests/test_sam_retry_policy.py
git commit -m "feat: SAM 재시도 규칙을 한 곳에 모음"
```

---

### Task 2: 톤 마스크가 공용 모듈을 쓰게 한다 (동작 무변경)

**Files:**
- Modify: `server/app/services/editor_garment_mask.py:66-72`
- Modify: `server/app/routes.py:2070-2107`
- Test: `server/tests/test_tone_mask_retry.py` (기존 — 수정하지 않는다)

**Interfaces:**
- Consumes: Task 1 의 `sam_retry.MAX_RETRIES`, `BACKOFF_SECONDS`, `RETRYABLE_STATES`, `job_retry_count`, `job_is_retryable`, `backoff_elapsed`
- Produces: `editor_garment_mask.TONE_MASK_MAX_RETRIES`, `TONE_MASK_RETRY_BACKOFF_SECONDS`, `TONE_MASK_RETRYABLE_STATES` 는 **이름과 값이 그대로 유지**된다 (기존 참조가 깨지지 않는다)

- [ ] **Step 1: 기존 톤 테스트가 지금 통과하는지 먼저 확인한다 (기준선)**

Run: `cd server && .venv/bin/pytest -q tests/test_tone_mask_retry.py`
Expected: PASS (19 passed) — 이 숫자가 Task 2 이후에도 같아야 한다

- [ ] **Step 2: 상수를 공용 모듈 재노출로 바꾼다**

`server/app/services/editor_garment_mask.py` 에서 아래 세 정의를 교체한다. `import` 목록에
`from app.services import sam_retry` 를 추가한다.

```python
#: 재시도 정책은 sam_retry 가 단일 출처다 — 세 SAM 잡이 같은 예산을 써야 한다(2026-08-21).
#: 이 이름들은 기존 호출부·테스트 호환을 위해 남긴다.
TONE_MASK_MAX_RETRIES = sam_retry.MAX_RETRIES
TONE_MASK_RETRY_BACKOFF_SECONDS = sam_retry.BACKOFF_SECONDS
TONE_MASK_RETRYABLE_STATES = sam_retry.RETRYABLE_STATES
```

`TONE_MASK_RETRYABLE_CODES` 는 **그대로 둔다** — SAM 이 HTTP 200 안에서 돌려주는 코드 목록이라
성격이 다르고 톤 워커만 쓴다.

- [ ] **Step 3: 라우트의 세 헬퍼를 위임으로 축약한다**

`server/app/routes.py` 에서 `_tone_job_is_retryable`, `_tone_job_retry_count`,
`_tone_retry_backoff_elapsed` 의 본문을 아래로 교체한다. `import` 목록에
`from app.services import sam_retry` 를 추가한다.

```python
def _tone_job_is_retryable(job: dict) -> bool:
    """이 종결이 판정이 아니라 일시 장애인가. 판정은 sam_retry 가 단일 출처다."""
    return sam_retry.job_is_retryable(
        job, states=editor_garment_mask.TONE_MASK_RETRYABLE_STATES)


def _tone_job_retry_count(job: dict) -> int:
    return sam_retry.job_retry_count(job)


def _tone_retry_backoff_elapsed(job: dict, *, now: datetime | None = None) -> bool:
    return sam_retry.backoff_elapsed(
        job, waits=editor_garment_mask.TONE_MASK_RETRY_BACKOFF_SECONDS, now=now)
```

- [ ] **Step 4: 톤 동작이 그대로인지 확인한다**

Run: `cd server && .venv/bin/pytest -q tests/test_tone_mask_retry.py`
Expected: PASS (19 passed) — Step 1 과 같은 숫자

- [ ] **Step 5: 전체 회귀를 확인한다**

Run: `cd server && .venv/bin/pytest -q 2>&1 | tail -3`
Expected: `2519 passed` 이상, 새 실패 0

- [ ] **Step 6: 커밋 (오너 요청 시)**

```bash
git add server/app/services/editor_garment_mask.py server/app/routes.py
git commit -m "refactor: 색감 조정 재시도 규칙을 공용 모듈로 연결 (동작 그대로)"
```

---

### Task 3: 두 워커의 키 빌더에 세대를 붙인다

**Files:**
- Modify: `server/app/services/canonical_reference.py:47-63`
- Modify: `server/app/services/matching_cutout.py`
- Modify: `server/app/routes.py:797` (sam_preprocess enqueue), `server/app/routes.py:1285` (matching_cutout enqueue)
- Test: `server/tests/test_sam_retry_keys.py`

**Interfaces:**
- Consumes: Task 1 의 `sam_retry.generation_key`
- Produces: `canonical_reference.preprocess_idempotency_key(project_id: str, product: dict, *, retry: int = 0) -> str | None`, `matching_cutout.cutout_job_key(project_id: str, matching_item_id: str, *, retry: int = 0) -> str`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`server/tests/test_sam_retry_keys.py`:

```python
"""세대 키 — 같은 키로 다시 걸면 끝난 잡에 합류만 하고 아무것도 안 돈다.

톤 마스크의 mask_job_key 와 같은 규칙을 sam_preprocess·matching_cutout 이 쓴다.
"""

from app.services import canonical_reference, matching_cutout


def _product():
    return {"colors": [{"isBase": True, "images": {"Front": "asset-front",
                                                   "Back": "asset-back"}}]}


def test_preprocess_key_is_stable_at_generation_zero():
    a = canonical_reference.preprocess_idempotency_key("proj", _product())
    b = canonical_reference.preprocess_idempotency_key("proj", _product(), retry=0)
    assert a is not None
    assert a == b
    assert not a.endswith(":r0")


def test_preprocess_key_changes_per_generation():
    base = canonical_reference.preprocess_idempotency_key("proj", _product())
    gen2 = canonical_reference.preprocess_idempotency_key("proj", _product(), retry=2)
    assert gen2 == f"{base}:r2"


def test_preprocess_key_is_still_none_without_photos():
    assert canonical_reference.preprocess_idempotency_key("proj", {}, retry=3) is None


def test_cutout_key_is_stable_at_generation_zero():
    key = matching_cutout.cutout_job_key("proj", "item")
    assert key == f"proj:matching_cutout:item:{matching_cutout.ALGORITHM_VERSION}"


def test_cutout_key_changes_per_generation():
    base = matching_cutout.cutout_job_key("proj", "item")
    assert matching_cutout.cutout_job_key("proj", "item", retry=1) == f"{base}:r1"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd server && .venv/bin/pytest -q tests/test_sam_retry_keys.py`
Expected: FAIL — `preprocess_idempotency_key() got an unexpected keyword argument 'retry'` 및 `module 'app.services.matching_cutout' has no attribute 'cutout_job_key'`

- [ ] **Step 3: `preprocess_idempotency_key` 에 세대를 붙인다**

`server/app/services/canonical_reference.py` 의 시그니처와 반환을 바꾼다. 파일 상단 import 에
`from app.services import sam_retry` 를 추가한다.

```python
def preprocess_idempotency_key(project_id: str, product: dict, *,
                               retry: int = 0) -> str | None:
    """The cutout job's identity: this project's *current* base-colour photographs.

    None when there is nothing to segment — an empty job must never take the key, or the real
    photographs arriving a moment later would join a `skipped` job and never be segmented.

    The photo ids are IN the key on purpose. A seller who swaps the front photograph has to get
    a new cutout; a fixed per-project key would keep serving the previous garment's silhouette
    to every consumer that asks "what does the product look like".

    `retry` 도 같은 이유로 키에 들어간다 — 일시 장애로 끝난 잡이 키를 물고 있는 한 재시도는
    그 시체에 합류만 한다(2026-08-21, 톤 마스크의 mask_job_key 와 같은 규칙).
    """
    from app.agents import mannequin      # 서비스→에이전트 단방향 (editor_garment_mask 와 같은 결)
    ids = [aid for slot, aid in mannequin.base_color_images(product)
           if slot in ELIGIBLE_VIEWS and aid]
    if not ids:
        return None
    digest = hashlib.sha256("|".join(ids).encode("utf-8")).hexdigest()[:16]
```

`digest` 다음 줄의 기존 `return` 문을 `sam_retry.generation_key(<기존 반환식>, retry)` 로 감싼다.
예: 기존이 `return f"{project_id}:sam_preprocess:{digest}"` 였다면

```python
    return sam_retry.generation_key(f"{project_id}:sam_preprocess:{digest}", retry)
```

- [ ] **Step 4: `cutout_job_key` 를 신설한다**

`server/app/services/matching_cutout.py` 에 추가한다. 파일 상단 import 에
`from app.services import sam_retry` 를 추가한다.

```python
def cutout_job_key(project_id: str, matching_item_id: str, *, retry: int = 0) -> str:
    """누끼 잡의 신원. 등록 라우트와 재시도 푸셔가 **같은 함수**를 지나야 한다.

    라우트에 문자열을 인라인으로 두면 푸셔가 만든 키와 갈라지고, 그 순간 재시도는 새 잡이
    아니라 별개의 잡이 된다.
    """
    return sam_retry.generation_key(
        f"{project_id}:matching_cutout:{matching_item_id}:{ALGORITHM_VERSION}", retry)
```

- [ ] **Step 5: 등록 라우트가 새 빌더를 쓰게 한다**

`server/app/routes.py:1285` 의 인라인 f-string 을 교체한다.

```python
            idempotency_key=matching_cutout.cutout_job_key(project_id, matching_item_id),
```

- [ ] **Step 6: 통과를 확인한다**

Run: `cd server && .venv/bin/pytest -q tests/test_sam_retry_keys.py`
Expected: PASS (5 passed)

- [ ] **Step 7: 전체 회귀를 확인한다**

Run: `cd server && .venv/bin/pytest -q 2>&1 | tail -3`
Expected: `2519 passed` 이상, 새 실패 0

- [ ] **Step 8: 커밋 (오너 요청 시)**

```bash
git add server/app/services/canonical_reference.py server/app/services/matching_cutout.py \
        server/app/routes.py server/tests/test_sam_retry_keys.py
git commit -m "feat: 누끼 작업에 재시도 번호를 붙일 수 있게"
```

---

### Task 4: 두 워커가 일시 장애를 영구 실패로 적지 않게 한다

**Files:**
- Modify: `server/app/workers/sam_preprocess_job.py:66-71`
- Modify: `server/app/workers/matching_cutout_job.py:232-235`
- Test: `server/tests/test_sam_job_transient_failure.py`

**Interfaces:**
- Consumes: Task 1 의 `sam_retry.RETRYABLE_STATES`
- Produces: 두 워커가 `SamUnavailable` 에서 `status="done"`, `result={"state": "unavailable", ...}` 로 종결한다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`server/tests/test_sam_job_transient_failure.py`:

```python
"""일시 장애를 error 로 적으면 그 잡이 멱등키를 문 채 종착한다 — 영구 누락의 원인.

톤 마스크가 2026-08-18 사고로 이미 배운 것을 sam_preprocess·matching_cutout 에 적용한다.
`error` 종결은 디스패처가 재시도하지 않고(재시도 코드가 존재하지 않는다), 같은 키로 다시 걸면
그 실패 잡에 합류만 한다. `done` + state:unavailable 로 적어야 푸셔가 다음 세대를 걸 수 있다.
"""

import pytest

import app.workers.matching_cutout_job as mcj
import app.workers.sam_preprocess_job as spj
from app.services import sam_client, sam_retry


class _Settings:
    matching_cutout = "on"
    sam_service_url = "http://sam2:8080"
    sam_internal_token = "t"
    r2_bucket = "b"


class _Pool:
    def connection(self):
        raise AssertionError("이 테스트는 SAM 실패 지점 앞에서 끝나야 한다")


def _capture():
    seen = {}

    async def finalize_uncharged_job(conn, *, job_id, lease_token, status, result):
        seen["status"] = status
        seen["result"] = result
    return seen, finalize_uncharged_job


@pytest.mark.asyncio
async def test_sam_preprocess_records_an_outage_as_retryable(monkeypatch):
    seen, finalize = _capture()
    monkeypatch.setattr(spj.repo, "finalize_uncharged_job", finalize)
    monkeypatch.setattr(spj, "load_settings", lambda: _Settings())
    monkeypatch.setattr(spj.sam_client, "configured", lambda s: True)

    async def _boom(*a, **k):
        raise sam_client.SamUnavailable("connection refused")
    monkeypatch.setattr(spj.sam_client, "segment_garment", _boom)

    async def _product(conn, project_id):
        return {"colors": [{"isBase": True, "images": {"Front": "a1"}}]}
    monkeypatch.setattr(spj.repo, "get_product", _product)

    async def _asset(conn, user_id, asset_id):
        return {"id": asset_id, "r2_key": "users/u/p/front.jpg"}
    monkeypatch.setattr(spj.repo, "get_asset_for_user", _asset)

    app = type("A", (), {"state": type("S", (), {"pool": _FakePool()})()})()
    await spj.run_sam_preprocess_job(app, {
        "id": "j1", "project_id": "p", "user_id": "u", "lease_token": "t"})

    assert seen["status"] == "done", "error 로 적으면 멱등키가 시체에 묶인다"
    assert seen["result"]["state"] in sam_retry.RETRYABLE_STATES


@pytest.mark.asyncio
async def test_matching_cutout_records_an_outage_as_retryable(monkeypatch):
    seen, finalize = _capture()
    monkeypatch.setattr(mcj.repo, "finalize_uncharged_job", finalize)
    monkeypatch.setattr(mcj.sam_client, "configured", lambda s: True)

    async def _boom(*a, **k):
        raise sam_client.SamUnavailable("connection refused")
    monkeypatch.setattr(mcj.sam_client, "segment_garment", _boom)

    app = type("A", (), {"state": type("S", (), {
        "pool": _FakePool(), "settings": _Settings(), "r2": None})()})()
    await mcj.run_matching_cutout_job(app, {
        "id": "j2", "project_id": "p", "user_id": "u", "lease_token": "t",
        "payload": {"matchingItemId": "i1", "sourceKeys": ["k1"], "sourceAssetIds": ["a1"]}})

    assert seen["status"] == "done", "error 로 적으면 그 옷은 영영 누끼가 없다"
    assert seen["result"]["state"] in sam_retry.RETRYABLE_STATES


class _FakePool:
    """finalize 만 지나가면 되는 최소 커넥션 풀."""
    def connection(self):
        class _Ctx:
            async def __aenter__(self_inner):
                class _Conn:
                    async def commit(self_c):
                        return None
                return _Conn()

            async def __aexit__(self_inner, *exc):
                return False
        return _Ctx()
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd server && .venv/bin/pytest -q tests/test_sam_job_transient_failure.py`
Expected: FAIL — `assert 'error' == 'done'` 두 건

- [ ] **Step 3: `sam_preprocess` 를 고친다**

`server/app/workers/sam_preprocess_job.py` 의 `except sam_client.SamUnavailable` 블록을 교체한다.

```python
    except sam_client.SamUnavailable as exc:
        # error 가 아니라 done + unavailable 이다. error 로 적으면 이 잡이 멱등키를 문 채
        # 종착하고, 같은 상품을 다시 저장해도 그 시체에 합류만 한다 — 캐노니컬 컷아웃이
        # 영영 안 생긴다(2026-08-21). 다음 세대는 sam_retry_pusher 가 건다.
        # 재시도가 싼 이유: 컷아웃 키가 결정론적이라 이미 성공한 뷰는 R2 에서 그대로 온다.
        await finish("done", {"state": "unavailable", "reason": str(exc)})
        return
```

- [ ] **Step 4: `matching_cutout` 을 고친다**

`server/app/workers/matching_cutout_job.py` 의 `except sam_client.SamUnavailable` 블록을 교체한다.

```python
    except sam_client.SamUnavailable as exc:
        # done + unavailable. error 로 적으면 멱등키가 이 실패 잡에 묶여 그 옷은 영영 누끼가
        # 없다 — 마네킹 생성이 셀러 원본(접힌 사진)을 그대로 입력으로 쓰게 된다(2026-08-21).
        # 다음 세대는 sam_retry_pusher 가 건다. 원본 자산은 여기서도 그대로 남는다.
        await finish("done", {"state": "unavailable", "reason": str(exc),
                              "matchingItemId": matching_item_id})
        return
```

- [ ] **Step 5: 통과를 확인한다**

Run: `cd server && .venv/bin/pytest -q tests/test_sam_job_transient_failure.py`
Expected: PASS (2 passed)

- [ ] **Step 6: 이 변경이 기존 소비자를 깨지 않는지 확인한다**

Run: `cd server && .venv/bin/pytest -q tests/test_matching_cutout.py tests/test_canonical_pipeline.py -q`
Expected: PASS — `state` 값으로 상태를 판정하는 곳이 `unavailable` 을 이미 알고 있어야 한다.
실패하면 그 판정 지점을 `sam_retry.RETRYABLE_STATES` 기준으로 고친다(스켈레톤에 가두지 않는다).

- [ ] **Step 7: 전체 회귀를 확인한다**

Run: `cd server && .venv/bin/pytest -q 2>&1 | tail -3`
Expected: `2519 passed` 이상, 새 실패 0

- [ ] **Step 8: 커밋 (오너 요청 시)**

```bash
git add server/app/workers/sam_preprocess_job.py server/app/workers/matching_cutout_job.py \
        server/tests/test_sam_job_transient_failure.py
git commit -m "fix: SAM 서버가 잠깐 없을 때 누끼를 영영 포기하던 문제"
```

---

### Task 5: 재시도 후보 조회

**Files:**
- Modify: `server/app/repo.py` (`get_latest_job_generation` 아래, 1123행 근처)
- Create: `supabase/migrations/20260821000000_sam_retry_index.sql`
- Test: `server/tests/test_sam_retry_pusher.py` (Task 6 과 공유, 여기서 첫 테스트만 추가)

**Interfaces:**
- Consumes: 없음
- Produces: `repo.list_retryable_sam_jobs(conn, kinds: tuple[str, ...], *, max_retries: int, min_age_seconds: float, limit: int = 50) -> list[dict]` — 반환 dict 는 `_JOB_COLS` + `idempotency_key`

- [ ] **Step 1: 조회를 구현한다**

`server/app/repo.py` 의 `get_latest_job_generation` 바로 아래에 추가한다.

```python
async def list_retryable_sam_jobs(
    conn: AsyncConnection, kinds: tuple[str, ...], *, max_retries: int,
    min_age_seconds: float, limit: int = 50,
) -> list[dict]:
    """일시 장애로 끝났고 예산이 남은 SAM 잡 후보.

    정확한 백오프 판정은 세대마다 대기가 달라서 SQL 로 하지 않는다 — 여기서는 **가장 짧은
    대기**보다 오래된 것만 넓게 긁고, 호출자가 `sam_retry.backoff_elapsed` 로 거른다.
    `idempotency_key` 를 함께 돌려주는 이유는 푸셔가 base 키를 되찾아야 하기 때문이다.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            f"select {_JOB_COLS}, idempotency_key from jobs "
            "where kind = any(%s) and status = any(%s) "
            "and finished_at is not null "
            "and finished_at < now() - make_interval(secs => %s) "
            "and coalesce((payload->>'retry')::int, 0) < %s "
            "order by finished_at desc limit %s",
            (list(kinds), ["done", "error"], float(min_age_seconds),
             int(max_retries), int(limit)),
        )
        return await cur.fetchall()
```

- [ ] **Step 2: 인덱스 마이그레이션을 쓴다**

`supabase/migrations/20260821000000_sam_retry_index.sql`:

```sql
-- SAM 잡 재시도 푸셔가 15초마다 도는 조회를 위한 partial index.
--
-- 푸셔는 "일시 장애로 끝났고 예산이 남은 SAM 잡"만 본다(app/repo.py list_retryable_sam_jobs).
-- 기존 인덱스는 (project_id, kind, status) 와 pending 전용뿐이라 이 조회에 맞지 않는다 —
-- 전역 스캔이 되면 jobs 가 커질수록 매 15초가 비싸진다.
--
-- partial 조건은 조회의 where 절 중 **변하지 않는 부분**만 담는다. retry 예산과 백오프는
-- 코드 상수라 인덱스에 넣지 않는다(상수가 바뀌면 인덱스를 다시 만들어야 한다).
create index if not exists jobs_sam_retry_idx
  on public.jobs (kind, finished_at desc)
  where status in ('done', 'error')
    and kind in ('sam_preprocess', 'matching_cutout', 'editor_garment_mask');
```

- [ ] **Step 3: 마이그레이션이 SQL 로 유효한지 확인한다**

Run: `cd server && .venv/bin/python -c "import pathlib; print(pathlib.Path('../supabase/migrations/20260821000000_sam_retry_index.sql').read_text()[:80])"`
Expected: 파일 내용의 첫 줄이 출력된다 (파일 존재·읽기 확인)

> 적용은 오너가 직접 실행한다 (이 저장소 관례 — MCP 는 read-only).

- [ ] **Step 4: 전체 회귀를 확인한다**

Run: `cd server && .venv/bin/pytest -q 2>&1 | tail -3`
Expected: `2519 passed` 이상, 새 실패 0

- [ ] **Step 5: 커밋 (오너 요청 시)**

```bash
git add server/app/repo.py supabase/migrations/20260821000000_sam_retry_index.sql
git commit -m "feat: 다시 시도할 누끼 작업을 찾는 조회 추가"
```

---

### Task 6: 재시도 푸셔

**Files:**
- Create: `server/app/workers/sam_retry_pusher.py`
- Modify: `server/app/main.py:85-111`
- Test: `server/tests/test_sam_retry_pusher.py`

**Interfaces:**
- Consumes: Task 1 의 `sam_retry.*`, Task 5 의 `repo.list_retryable_sam_jobs`, 기존 `repo.get_latest_job_generation`, `repo.create_job`
- Produces: `SamRetryPusher(app)` — `async start()`, `async stop()`; `PUSH_KINDS: tuple[str, ...]`; `POLL_SECONDS: float`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`server/tests/test_sam_retry_pusher.py`:

```python
"""재시도를 밀어줄 사람 — 톤 마스크는 셀러 폴링이 밀지만 이 둘은 보는 화면이 없다.

디스패처 스윕에 얹지 않는다: 디스패처는 워커를 await 한 뒤 다음 반복으로 가므로
(dispatcher.py), detail_page(평균 563초)가 도는 동안 스윕이 멈춘다 — 285초 예산의 타이머로
쓸 수 없다.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services import sam_retry
from app.workers.sam_retry_pusher import SamRetryPusher


def _job(**over):
    fin = datetime.now(timezone.utc) - timedelta(seconds=600)
    base = {"id": "j1", "user_id": "u", "project_id": "p", "kind": "matching_cutout",
            "status": "done", "result": {"state": "unavailable"},
            "payload": {"matchingItemId": "i1"}, "finished_at": fin,
            "idempotency_key": "p:matching_cutout:i1:v1"}
    return {**base, **over}


class _Recorder:
    """repo 대역 — 푸셔가 무엇을 걸었는지만 본다."""

    def __init__(self, candidates, latest=None):
        self.candidates = candidates
        self._latest = latest
        self.created = []

    async def list_retryable_sam_jobs(self, conn, kinds, *, max_retries,
                                      min_age_seconds, limit=50):
        return self.candidates

    async def get_latest_job_generation(self, conn, user_id, base_key):
        return self._latest if self._latest is not None else self.candidates[0]

    async def create_job(self, conn, *, user_id, project_id, kind, payload,
                         idempotency_key, credits_reserved, metadata):
        self.created.append({"kind": kind, "key": idempotency_key, "payload": payload})
        return {"id": "new"}, True


@pytest.mark.asyncio
async def test_it_queues_the_next_generation_for_a_transient_outage():
    rec = _Recorder([_job()])
    pusher = SamRetryPusher(_app(rec))
    await pusher._push_once(rec, None)

    assert len(rec.created) == 1
    assert rec.created[0]["key"] == "p:matching_cutout:i1:v1:r1"
    assert rec.created[0]["payload"]["retry"] == 1
    assert rec.created[0]["payload"]["matchingItemId"] == "i1", "원래 payload 를 이월해야 한다"


@pytest.mark.asyncio
async def test_it_does_not_retry_an_input_verdict():
    rec = _Recorder([_job(result={"state": "failed", "reason": "no_cutout"})])
    pusher = SamRetryPusher(_app(rec))
    await pusher._push_once(rec, None)
    assert rec.created == []


@pytest.mark.asyncio
async def test_it_waits_for_the_backoff():
    fresh = datetime.now(timezone.utc) - timedelta(seconds=5)   # waits[0] = 15초
    rec = _Recorder([_job(finished_at=fresh)])
    pusher = SamRetryPusher(_app(rec))
    await pusher._push_once(rec, None)
    assert rec.created == []


@pytest.mark.asyncio
async def test_it_stops_at_the_budget():
    spent = _job(payload={"matchingItemId": "i1", "retry": sam_retry.MAX_RETRIES},
                 idempotency_key=f"p:matching_cutout:i1:v1:r{sam_retry.MAX_RETRIES}")
    rec = _Recorder([spent])
    pusher = SamRetryPusher(_app(rec))
    await pusher._push_once(rec, None)
    assert rec.created == []


@pytest.mark.asyncio
async def test_it_ignores_a_stale_generation():
    """이미 다음 세대가 걸린 잡을 또 밀면 세대가 두 갈래로 갈라진다."""
    old = _job(id="old", payload={"matchingItemId": "i1"})
    newer = _job(id="new", payload={"matchingItemId": "i1", "retry": 1},
                 idempotency_key="p:matching_cutout:i1:v1:r1")
    rec = _Recorder([old], latest=newer)
    pusher = SamRetryPusher(_app(rec))
    await pusher._push_once(rec, None)
    assert rec.created == []


@pytest.mark.asyncio
async def test_it_skips_while_the_latest_generation_is_still_running():
    running = _job(id="run", status="running", result=None)
    rec = _Recorder([_job()], latest=running)
    pusher = SamRetryPusher(_app(rec))
    await pusher._push_once(rec, None)
    assert rec.created == []


def _app(rec):
    return type("A", (), {"state": type("S", (), {"pool": None, "repo": rec})()})()
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd server && .venv/bin/pytest -q tests/test_sam_retry_pusher.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.workers.sam_retry_pusher'`

- [ ] **Step 3: 푸셔를 구현한다**

`server/app/workers/sam_retry_pusher.py`:

```python
"""일시 장애로 끝난 SAM 잡의 다음 세대를 민다.

톤 마스크는 셀러가 톤 에디터를 열고 있는 동안 상태 라우트가 재시도를 민다
(`routes._enqueue_tone_mask_generations`). 그런데 `sam_preprocess` 와 `matching_cutout` 은
백그라운드 잡이라 **폴링하는 화면이 없다.** 아무도 밀지 않으면 재시도는 일어나지 않는다.

디스패처 스윕에 얹지 않는 이유: 디스패처는 워커를 `await` 한 뒤 다음 반복으로 간다. 평균
563초짜리 `detail_page` 가 도는 동안 스윕도 멈추므로, 285초 예산을 지키는 타이머로 쓸 수 없다.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from app import repo as _repo
from app.services import sam_retry

log = logging.getLogger("wearless.sam_retry")

#: 폴링하는 화면이 없는 잡만. `editor_garment_mask` 는 톤 에디터가 이미 민다 — 여기서 또 밀면
#: 셀러가 보고 있지 않은 컷까지 예산을 태운다.
PUSH_KINDS = ("sam_preprocess", "matching_cutout")

#: 가장 짧은 백오프(15초)를 따라간다. 인덱스가 받쳐 주는 단일 조회라 비용이 미미하다.
POLL_SECONDS = 15.0


class SamRetryPusher:
    def __init__(self, app):
        self.app = app
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="sam-retry-pusher")

    async def stop(self):
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()

    async def _run(self):
        pool = self.app.state.pool
        while not self._stop.is_set():
            try:
                async with pool.connection() as conn:
                    await self._push_once(_repo, conn)
                    await conn.commit()
            except asyncio.CancelledError:
                raise
            except Exception:
                # 푸셔가 죽어도 디스패처는 계속 돈다 — 분리해 둔 이유가 이것이다.
                log.exception("sam retry pusher error")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=POLL_SECONDS)

    async def _push_once(self, repo, conn) -> int:
        """다음 세대를 걸 수 있는 잡을 전부 걸고, 건 개수를 돌려준다."""
        candidates = await repo.list_retryable_sam_jobs(
            conn, PUSH_KINDS, max_retries=sam_retry.MAX_RETRIES,
            min_age_seconds=min(sam_retry.BACKOFF_SECONDS))
        pushed = 0
        for job in candidates:
            if not sam_retry.job_is_retryable(job):
                continue                       # 판정 실패 — 다시 돌려도 같은 답이다
            if not sam_retry.backoff_elapsed(job):
                continue
            base = sam_retry.base_key(job.get("idempotency_key"))
            latest = await repo.get_latest_job_generation(conn, job["user_id"], base)
            if latest is None or str(latest.get("id")) != str(job.get("id")):
                continue                       # 이미 다음 세대가 있다 — 갈래를 만들지 않는다
            if str(latest.get("status") or "") not in sam_retry.TERMINAL_STATUSES:
                continue                       # 아직 도는 중이다
            retry = sam_retry.job_retry_count(job) + 1
            if retry > sam_retry.MAX_RETRIES:
                continue
            payload = {k: v for k, v in (job.get("payload") or {}).items() if k != "retry"}
            payload["retry"] = retry
            await repo.create_job(
                conn, user_id=job["user_id"], project_id=job["project_id"],
                kind=job["kind"], payload=payload,
                idempotency_key=sam_retry.generation_key(base, retry),
                credits_reserved=0, metadata={})
            pushed += 1
            log.info("sam retry queued kind=%s base=%s generation=%s", job["kind"], base, retry)
        return pushed
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd server && .venv/bin/pytest -q tests/test_sam_retry_pusher.py`
Expected: PASS (6 passed)

- [ ] **Step 5: lifespan 에 배선한다**

`server/app/main.py` 의 lifespan 을 고친다. 상단 import 에
`from app.workers.sam_retry_pusher import SamRetryPusher` 를 추가한다.

`dispatcher = None` 아래에 `sam_retry_pusher = None` 을 추가하고, 디스패처를 기동하는 `if` 블록
안(디스패처 `start()` 직후)에 다음을 넣는다.

```python
                # 폴링하는 화면이 없는 SAM 잡의 재시도를 민다. 디스패처와 **분리**한다 —
                # 디스패처는 워커를 await 하므로 긴 잡이 도는 동안 타이머가 멈춘다.
                sam_retry_pusher = SamRetryPusher(app)
                await sam_retry_pusher.start()
```

`yield` 뒤 정리 구간에서 디스패처보다 **먼저** 멈춘다.

```python
        if sam_retry_pusher is not None:
            await sam_retry_pusher.stop()
```

- [ ] **Step 6: 전체 회귀를 확인한다**

Run: `cd server && .venv/bin/pytest -q 2>&1 | tail -3`
Expected: `2519 passed` 이상 + 새 테스트 27건, 새 실패 0

- [ ] **Step 7: 커밋 (오너 요청 시)**

```bash
git add server/app/workers/sam_retry_pusher.py server/app/main.py \
        server/tests/test_sam_retry_pusher.py
git commit -m "feat: 실패한 누끼 작업을 알아서 다시 시도하게"
```

---

## Self-Review

**1. Spec coverage (§3):**

| 스펙 요구 | 담당 |
|---|---|
| 인프라 장애만 재시도, 판정 실패는 제외 | Task 1 (`RETRYABLE_STATES`), Task 6 (`job_is_retryable` 필터) |
| 예산 4회 / 285초, 톤 마스크와 같은 값 | Task 1 상수 + Task 2 재노출 |
| `error` 가 아니라 `done`+`unavailable` 로 종결 | Task 4 |
| 세대 키 `:rN` | Task 3 |
| 재시도를 미는 백그라운드 태스크 (디스패처와 분리) | Task 6 |
| 톤 마스크 동작 무변경 | Task 2 Step 1/4 의 19건 기준선 |

**2. Placeholder scan:** "TBD"·"적절히 처리"·코드 없는 구현 스텝 없음. Task 3 Step 3 의 기존
`return` 문은 파일에서 확인 후 감싸라고 **정확한 대체식**과 함께 지시했다.

**3. Type consistency:** `sam_retry` 의 6개 함수 이름이 Task 2·4·6 에서 동일하게 쓰였다.
`list_retryable_sam_jobs` 의 키워드 인자(`max_retries`, `min_age_seconds`, `limit`)가 Task 5 정의와
Task 6 호출·테스트 대역에서 일치한다. `_push_once(repo, conn)` 시그니처가 테스트와 구현에서 같다.

**남은 위험 하나:** Task 4 Step 6 — `state` 로 상태를 판정하는 기존 소비자가 `unavailable` 을
모를 수 있다. 그래서 별도 스텝으로 분리해 두었고, 깨지면 그 판정 지점을
`sam_retry.RETRYABLE_STATES` 기준으로 고친다.
