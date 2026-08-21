# sam2 온디맨드 기동/종료 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `sam2` 를 평소 0대로 두고, 어떤 사용자든 사진을 올리면 자동으로 1대를 띄우고, 30분 유휴 뒤 자동으로 0대로 내린다.

**Architecture:** 진실의 원천은 60초 주기 **reconciler** 하나다 — "지금 몇 대여야 하는가(want)"를 DB에서 계산해 ECS 실제 값과 다르면 맞춘다(양방향). 업로드 라우트와 `SamUnavailable` 지점의 **prewarm 훅**은 "60초 기다리지 말고 지금 켜라"는 지연 최적화일 뿐이라 실패해도 무해하다. ECS/SNS 호출은 전부 `app/services/sam_autoscale.py` 한 모듈 뒤에 숨기고, 그 모듈은 `SAM_AUTOSCALE=off` 면 boto3 클라이언트조차 만들지 않는다.

**Tech Stack:** Python 3.12 · FastAPI · psycopg(async) · boto3(ECS·SNS, `asyncio.to_thread`) · AWS Copilot addon(CloudFormation) · pytest

**Spec:** `docs/superpowers/specs/2026-08-21-sam2-on-demand-scaling-design.md` §2, §4~§16

## Global Constraints

- `SAM_AUTOSCALE` 미설정·오타 = `off`. **off 면 ECS/SNS 클라이언트 생성·서비스 탐색·DB 조회 전부 없음.** 기존 2,558 테스트가 AWS 호출 0 으로 그대로 통과해야 한다.
- 유휴 기준 **30분** (오너 결정). 켜는 조건은 스펙 §4 의 세 가지, 끄는 조건은 그 셋이 전부 거짓일 때만.
- 라우트는 **절대 내리지 않는다.** 내리는 건 reconciler 뿐.
- 훅·reconciler 의 AWS 실패는 삼킨다. **업로드·분석·마네킹 생성은 어떤 경우에도 막히지 않는다.**
- boto3 는 동기 SDK — 모든 호출을 `asyncio.to_thread` 로 격리(`app/r2.py:8` 관례). 타임아웃 connect 3s / read 5s.
- 서비스명을 박지 않는다. 태그 `copilot-application=wearless` + `copilot-environment=prod` + `copilot-service=sam2` **세 개 모두** 매칭. 0개·2개 이상이면 비활성 + 알림 1회.
- `ecs:UpdateService` 는 `aws:ResourceTag/copilot-service=sam2` 조건으로만. **IAM 정책 시뮬레이터 실측(2026-08-21): sam2 서비스 `allowed`, api 서비스 `implicitDeny`** — api 가 자기 자신을 내릴 수 없다.
- 강제 종료 없음. 연속 가동 3시간 초과 시 **알림만**(가동 1회당 1번).
- 로그는 상태 변경·실패에만. 매 주기 성공 로그 금지.
- 서버 검증 `cd server && .venv/bin/pytest -q`. 기준선 **2,558 passed**(personalization 96 error 는 로컬 DB 미기동, 무관).
- 커밋·푸시는 오너 요청 시에만.

## File Structure

| 파일 | 책임 |
|---|---|
| `server/app/config.py` (수정) | `sam_autoscale`, `sam_autoscale_idle_minutes`, `sam_alert_topic_arn` 설정 |
| `server/app/services/sam_autoscale.py` (신규) | ECS/SNS 어댑터 + 순수 `want` 판정. boto3 는 이 파일에만 |
| `server/app/repo.py` (수정) | `sam_demand_snapshot(conn)` — want 계산에 필요한 세 시각을 한 쿼리로 |
| `server/app/workers/sam_autoscaler.py` (신규) | 60초 reconciler 백그라운드 태스크 (advisory lock) |
| `server/app/main.py` (수정) | lifespan 기동/정지 |
| `server/app/routes.py` (수정) | `create_upload_url` 에 prewarm 훅 |
| `server/app/services/sam_client.py` (수정) | `SamUnavailable` 발생 시 prewarm 훅 |
| `copilot/api/addons/sam-autoscale.yml` (신규) | IAM 정책 + SNS 토픽·구독 |
| `copilot/api/manifest.yml` (수정) | `SAM_AUTOSCALE: "on"`, `SAM_ALERT_TOPIC_ARN` |
| `copilot/sam2/manifest.yml` (수정) | `count: 0` |
| `.github/workflows/deploy-sam2.yml`, `deploy-server.yml` (수정) | Copilot 버전 고정 |
| `supabase/migrations/20260821010000_sam_autoscale_index.sql` (신규) | `assets` upload created_at partial index |

---

### Task 1: 설정 플래그

**Files:**
- Modify: `server/app/config.py:175-176` (필드), `server/app/config.py:378-379` 근처 (로더)
- Test: `server/tests/test_sam_autoscale_config.py`

**Interfaces:**
- Produces: `Settings.sam_autoscale: str = "off"`, `Settings.sam_autoscale_idle_minutes: int = 30`, `Settings.sam_alert_topic_arn: str | None = None`

- [ ] **Step 1: 실패하는 테스트**

`server/tests/test_sam_autoscale_config.py`:

```python
"""SAM_AUTOSCALE 은 기본 off 다 — 미설정·오타·공백 전부 off 로 떨어져야 로컬·테스트가 AWS 를 모른다."""

import pytest

from app.config import load_settings
from conftest import make_settings


def test_settings_default_to_off_without_env():
    s = make_settings()
    assert s.sam_autoscale == "off"
    assert s.sam_autoscale_idle_minutes == 30
    assert s.sam_alert_topic_arn is None


@pytest.mark.parametrize("raw,expected", [
    ("on", "on"), ("ON", "on"), (" on ", "on"),
    ("off", "off"), ("", "off"), ("yes", "off"), ("true", "off"), ("1", "off"),
])
def test_loader_normalises_the_flag(monkeypatch, raw, expected):
    monkeypatch.setenv("SAM_AUTOSCALE", raw)
    assert load_settings().sam_autoscale == expected


def test_loader_reads_idle_minutes_and_topic(monkeypatch):
    monkeypatch.setenv("SAM_AUTOSCALE_IDLE_MINUTES", "45")
    monkeypatch.setenv("SAM_ALERT_TOPIC_ARN", "arn:aws:sns:ap-northeast-2:1:t")
    s = load_settings()
    assert s.sam_autoscale_idle_minutes == 45
    assert s.sam_alert_topic_arn == "arn:aws:sns:ap-northeast-2:1:t"


def test_loader_falls_back_on_garbage_idle_minutes(monkeypatch):
    monkeypatch.setenv("SAM_AUTOSCALE_IDLE_MINUTES", "soon")
    assert load_settings().sam_autoscale_idle_minutes == 30
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && .venv/bin/pytest -q tests/test_sam_autoscale_config.py`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'sam_autoscale'`

- [ ] **Step 3: 구현**

`server/app/config.py` 의 `matching_cutout: str = "off"` 줄 아래에 추가:

```python
    # sam2 온디맨드 기동/종료(2026-08-21). off 면 ECS/SNS 클라이언트를 만들지도 않는다 —
    # 로컬·테스트는 AWS 를 모른다. 정본: docs/superpowers/specs/2026-08-21-sam2-on-demand-scaling-design.md
    sam_autoscale: str = "off"
    sam_autoscale_idle_minutes: int = 30   # 마지막 수요로부터 이 시간 지나면 0대 (오너 결정)
    sam_alert_topic_arn: str | None = None # SNS 토픽. 없으면 알림만 조용히 생략(기동/종료는 그대로)
```

`load_settings()` 의 `sam_internal_token=...` 줄 아래에 추가:

```python
        sam_autoscale=_flag("SAM_AUTOSCALE", "off", {"off", "on"}),
        sam_autoscale_idle_minutes=_int_env("SAM_AUTOSCALE_IDLE_MINUTES", 30),
        sam_alert_topic_arn=os.getenv("SAM_ALERT_TOPIC_ARN") or None,
```

`_flag` 바로 아래에 헬퍼 추가(기존에 같은 역할 함수가 있으면 그것을 쓰고 이건 만들지 않는다 —
`grep -n "def _int" app/config.py` 로 먼저 확인):

```python
def _int_env(env: str, default: int) -> int:
    try:
        return int((os.getenv(env) or "").strip() or default)
    except ValueError:
        return default
```

- [ ] **Step 4: 통과 확인**

Run: `cd server && .venv/bin/pytest -q tests/test_sam_autoscale_config.py`
Expected: PASS (11 passed)

- [ ] **Step 5: 전체 회귀**

Run: `cd server && .venv/bin/pytest -q 2>&1 | tail -1`
Expected: `2558 passed` 이상, 새 실패 0

---

### Task 2: want 판정 (순수 함수) + 수요 스냅샷 조회

**Files:**
- Create: `server/app/services/sam_autoscale.py` (이 Task 에서는 순수 부분만)
- Modify: `server/app/repo.py` (`list_retryable_sam_jobs` 아래)
- Create: `supabase/migrations/20260821010000_sam_autoscale_index.sql`
- Test: `server/tests/test_sam_autoscale_want.py`

**Interfaces:**
- Produces: `sam_autoscale.SAM_KINDS: tuple[str, ...]`, `sam_autoscale.DemandSnapshot` (dataclass: `active_sam_jobs: int`, `last_sam_finished_at: datetime | None`, `last_upload_at: datetime | None`), `sam_autoscale.want_running(snap: DemandSnapshot, *, idle_minutes: int, now: datetime | None = None) -> bool`, `repo.sam_demand_snapshot(conn, kinds: tuple[str, ...]) -> DemandSnapshot`

- [ ] **Step 1: 실패하는 테스트**

`server/tests/test_sam_autoscale_want.py`:

```python
"""지금 sam2 가 몇 대여야 하는가 — 세 조건 중 하나라도 참이면 1, 전부 거짓이면 0.

조건 2(마지막 SAM 잡 종료 30분 이내)가 없으면 "어제 만든 컷을 오늘 열어 색감 조정" 경로가
안 잡힌다: 그 잡은 1초 만에 unavailable 로 끝나 60초 뒤 reconciler 가 볼 땐 pending 도
running 도 아니다.
"""

from datetime import datetime, timedelta, timezone

from app.services import sam_autoscale
from app.services.sam_autoscale import DemandSnapshot, want_running

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def _ago(minutes):
    return NOW - timedelta(minutes=minutes)


def test_sam_kinds_are_the_three_sam_jobs():
    assert set(sam_autoscale.SAM_KINDS) == {
        "sam_preprocess", "matching_cutout", "editor_garment_mask"}


def test_active_job_wants_running_regardless_of_timestamps():
    snap = DemandSnapshot(active_sam_jobs=1, last_sam_finished_at=_ago(999),
                          last_upload_at=_ago(999))
    assert want_running(snap, idle_minutes=30, now=NOW) is True


def test_recent_sam_finish_wants_running():
    snap = DemandSnapshot(active_sam_jobs=0, last_sam_finished_at=_ago(29), last_upload_at=None)
    assert want_running(snap, idle_minutes=30, now=NOW) is True


def test_recent_upload_wants_running():
    snap = DemandSnapshot(active_sam_jobs=0, last_sam_finished_at=None, last_upload_at=_ago(29))
    assert want_running(snap, idle_minutes=30, now=NOW) is True


def test_all_quiet_wants_stopped():
    snap = DemandSnapshot(active_sam_jobs=0, last_sam_finished_at=_ago(31),
                          last_upload_at=_ago(31))
    assert want_running(snap, idle_minutes=30, now=NOW) is False


def test_boundary_is_strictly_less_than_idle():
    snap = DemandSnapshot(active_sam_jobs=0, last_sam_finished_at=_ago(30), last_upload_at=None)
    assert want_running(snap, idle_minutes=30, now=NOW) is False


def test_nothing_ever_happened_wants_stopped():
    snap = DemandSnapshot(active_sam_jobs=0, last_sam_finished_at=None, last_upload_at=None)
    assert want_running(snap, idle_minutes=30, now=NOW) is False


def test_naive_timestamps_are_treated_as_utc():
    snap = DemandSnapshot(active_sam_jobs=0,
                          last_sam_finished_at=NOW.replace(tzinfo=None) - timedelta(minutes=5),
                          last_upload_at=None)
    assert want_running(snap, idle_minutes=30, now=NOW) is True
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && .venv/bin/pytest -q tests/test_sam_autoscale_want.py`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 순수 부분 구현**

`server/app/services/sam_autoscale.py` (파일 신규 — Task 3 에서 어댑터를 이어 붙인다):

```python
"""sam2 온디맨드 기동/종료 — ECS/SNS 어댑터와 `want` 판정.

boto3 는 이 파일에만 산다. `SAM_AUTOSCALE=off` 면 클라이언트를 만들지도 않는다.
정본: docs/superpowers/specs/2026-08-21-sam2-on-demand-scaling-design.md
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

log = logging.getLogger("wearless.sam_autoscale")

#: 수요를 만드는 잡. 셋 중 하나라도 pending/running 이면 켜져 있어야 한다.
SAM_KINDS = ("sam_preprocess", "matching_cutout", "editor_garment_mask")


@dataclass(frozen=True)
class DemandSnapshot:
    active_sam_jobs: int
    last_sam_finished_at: datetime | None
    last_upload_at: datetime | None


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def want_running(snap: DemandSnapshot, *, idle_minutes: int,
                 now: datetime | None = None) -> bool:
    """세 조건 중 하나라도 참이면 1대. 전부 거짓일 때만 0대.

    조건 2(마지막 SAM 잡 종료)는 "어제 컷을 오늘 열어 색감 조정" 경로를 잡는다 — 그 잡은
    1초 만에 unavailable 로 끝나 60초 뒤엔 active 로 안 보인다. 실패한 잡도 "방금 SAM 이
    필요했다"는 증거다.
    """
    if snap.active_sam_jobs > 0:
        return True
    now = now or datetime.now(timezone.utc)
    horizon = now - timedelta(minutes=int(idle_minutes))
    for ts in (_utc(snap.last_sam_finished_at), _utc(snap.last_upload_at)):
        if ts is not None and ts > horizon:
            return True
    return False
```

- [ ] **Step 4: 순수 테스트 통과 확인**

Run: `cd server && .venv/bin/pytest -q tests/test_sam_autoscale_want.py`
Expected: PASS (8 passed)

- [ ] **Step 5: 수요 스냅샷 조회 구현**

`server/app/repo.py` 의 `list_retryable_sam_jobs` 바로 아래:

```python
async def sam_demand_snapshot(conn: AsyncConnection, kinds: tuple[str, ...]):
    """sam2 가 켜져 있어야 하는지 판단할 세 값을 한 왕복으로(2026-08-21, sam_autoscaler 전용).

    업로드는 `source='upload'` 만 센다 — 에디터 산출물·파생 asset 은 SAM 수요가 아니다.
    `jobs_sam_retry_idx`(kind, finished_at) 와 `assets_upload_created_idx` 가 받친다.
    """
    from app.services.sam_autoscale import DemandSnapshot
    async with conn.cursor() as cur:
        await cur.execute(
            "select "
            "  (select count(*) from jobs where kind = any(%s) "
            "     and status in ('pending', 'running')) as active_sam_jobs, "
            "  (select max(finished_at) from jobs where kind = any(%s) "
            "     and finished_at is not null) as last_sam_finished_at, "
            "  (select max(created_at) from assets where source = 'upload') as last_upload_at",
            (list(kinds), list(kinds)),
        )
        row = await cur.fetchone() or {}
    return DemandSnapshot(
        active_sam_jobs=int(row.get("active_sam_jobs") or 0),
        last_sam_finished_at=row.get("last_sam_finished_at"),
        last_upload_at=row.get("last_upload_at"),
    )
```

- [ ] **Step 6: 인덱스 마이그레이션**

`supabase/migrations/20260821010000_sam_autoscale_index.sql`:

```sql
-- sam2 온디맨드 reconciler(app/workers/sam_autoscaler.py)가 60초마다 읽는
-- "마지막 업로드 시각" 조회용. assets 는 user_id/project_id 인덱스뿐이라 전역 max() 가
-- 테이블 증가에 따라 비싸진다. 업로드 원본만 partial 로 건다 — 파생 asset 은 SAM 수요가 아니다.
create index if not exists assets_upload_created_idx
  on public.assets (created_at desc)
  where source = 'upload';
```

- [ ] **Step 7: 전체 회귀**

Run: `cd server && .venv/bin/pytest -q 2>&1 | tail -1`
Expected: `2566 passed` 이상, 새 실패 0

---

### Task 3: ECS/SNS 어댑터

**Files:**
- Modify: `server/app/services/sam_autoscale.py` (Task 2 파일에 이어 붙임)
- Test: `server/tests/test_sam_autoscale_adapter.py`

**Interfaces:**
- Produces: `class EcsTarget` (dataclass: `cluster_arn: str`, `service_arn: str`), `class ServiceState` (dataclass: `desired: int`, `running: int`, `pending: int`, `oldest_started_at: datetime | None`), `class SamAutoscaleAdapter` with `__init__(self, settings, *, ecs=None, sns=None)`, `async discover(self) -> EcsTarget | None`, `async describe(self, target) -> ServiceState`, `async set_desired(self, target, count: int) -> None`, `async notify(self, subject: str, body: str) -> None`, `enabled: bool` property

- [ ] **Step 1: 실패하는 테스트**

`server/tests/test_sam_autoscale_adapter.py`:

```python
"""ECS/SNS 어댑터 — boto3 대역으로 계약만 고정한다. 실제 AWS 는 부르지 않는다."""

import asyncio
from datetime import datetime, timezone

import pytest

from app.services.sam_autoscale import EcsTarget, SamAutoscaleAdapter, ServiceState
from conftest import make_settings

CLUSTER = "arn:aws:ecs:ap-northeast-2:1:cluster/wearless-prod-Cluster-x"
SAM2 = "arn:aws:ecs:ap-northeast-2:1:service/wearless-prod-Cluster-x/wearless-prod-sam2-Service-y"
API = "arn:aws:ecs:ap-northeast-2:1:service/wearless-prod-Cluster-x/wearless-prod-api-Service-z"


def _tags(service):
    return [{"key": "copilot-application", "value": "wearless"},
            {"key": "copilot-environment", "value": "prod"},
            {"key": "copilot-service", "value": service}]


class _Ecs:
    def __init__(self, services=None, tasks=None):
        self.services = services if services is not None else {
            SAM2: {"serviceArn": SAM2, "desiredCount": 0, "runningCount": 0,
                   "pendingCount": 0, "tags": _tags("sam2")},
            API: {"serviceArn": API, "desiredCount": 1, "runningCount": 1,
                  "pendingCount": 0, "tags": _tags("api")},
        }
        self.tasks = tasks or []
        self.updates = []

    def list_clusters(self):
        return {"clusterArns": [CLUSTER]}

    def list_services(self, cluster):
        return {"serviceArns": list(self.services)}

    def describe_services(self, cluster, services, include=()):
        return {"services": [self.services[a] for a in services if a in self.services]}

    def update_service(self, cluster, service, desiredCount):
        self.updates.append((service, desiredCount))
        self.services[service]["desiredCount"] = desiredCount
        return {"service": self.services[service]}

    def list_tasks(self, cluster, serviceName, desiredStatus="RUNNING"):
        return {"taskArns": [t["taskArn"] for t in self.tasks]}

    def describe_tasks(self, cluster, tasks):
        return {"tasks": [t for t in self.tasks if t["taskArn"] in tasks]}


class _Sns:
    def __init__(self):
        self.published = []

    def publish(self, TopicArn, Subject, Message):
        self.published.append((TopicArn, Subject, Message))
        return {"MessageId": "m"}


def _adapter(ecs=None, sns=None, **over):
    values = {"sam_autoscale": "on", "sam_alert_topic_arn": "arn:aws:sns:ap-northeast-2:1:t"}
    values.update(over)                       # 같은 키를 두 번 넘기면 TypeError — 병합한다
    s = make_settings(**values)
    return SamAutoscaleAdapter(s, ecs=ecs or _Ecs(), sns=sns or _Sns())


def test_disabled_adapter_never_builds_clients():
    s = make_settings(sam_autoscale="off")
    a = SamAutoscaleAdapter(s)
    assert a.enabled is False
    assert asyncio.run(a.discover()) is None


def test_discover_matches_all_three_copilot_tags():
    a = _adapter()
    t = asyncio.run(a.discover())
    assert t == EcsTarget(cluster_arn=CLUSTER, service_arn=SAM2)


def test_discover_refuses_ambiguity():
    ecs = _Ecs()
    ecs.services["dup"] = {"serviceArn": "dup", "desiredCount": 0, "runningCount": 0,
                           "pendingCount": 0, "tags": _tags("sam2")}
    a = _adapter(ecs=ecs)
    assert asyncio.run(a.discover()) is None


def test_discover_refuses_partial_tag_match():
    ecs = _Ecs()
    ecs.services[SAM2]["tags"] = [{"key": "copilot-service", "value": "sam2"}]   # env/app 없음
    a = _adapter(ecs=ecs)
    assert asyncio.run(a.discover()) is None


def test_discover_is_cached_per_process():
    ecs = _Ecs()
    a = _adapter(ecs=ecs)
    asyncio.run(a.discover())
    ecs.services.clear()                      # 이후 호출은 ECS 를 안 본다
    assert asyncio.run(a.discover()) == EcsTarget(cluster_arn=CLUSTER, service_arn=SAM2)


def test_describe_reports_counts_and_oldest_task_start():
    started = datetime(2026, 8, 21, 3, 46, 45, tzinfo=timezone.utc)
    ecs = _Ecs(tasks=[{"taskArn": "t1", "startedAt": started}])
    ecs.services[SAM2].update(desiredCount=1, runningCount=1)
    a = _adapter(ecs=ecs)
    t = asyncio.run(a.discover())
    st = asyncio.run(a.describe(t))
    assert st == ServiceState(desired=1, running=1, pending=0, oldest_started_at=started)


def test_describe_without_tasks_has_no_start_time():
    a = _adapter()
    t = asyncio.run(a.discover())
    st = asyncio.run(a.describe(t))
    assert st.oldest_started_at is None


def test_set_desired_calls_update_service_once():
    ecs = _Ecs()
    a = _adapter(ecs=ecs)
    t = asyncio.run(a.discover())
    asyncio.run(a.set_desired(t, 1))
    assert ecs.updates == [(SAM2, 1)]


def test_notify_publishes_to_the_configured_topic():
    sns = _Sns()
    a = _adapter(sns=sns)
    asyncio.run(a.notify("sam2 test", "hello"))
    assert sns.published == [("arn:aws:sns:ap-northeast-2:1:t", "sam2 test", "hello")]


def test_notify_is_a_noop_without_a_topic():
    sns = _Sns()
    a = _adapter(sns=sns, sam_alert_topic_arn=None)
    asyncio.run(a.notify("x", "y"))
    assert sns.published == []


def test_aws_errors_surface_as_exceptions_for_the_caller_to_swallow():
    class _Boom(_Ecs):
        def list_clusters(self):
            raise RuntimeError("throttled")
    a = _adapter(ecs=_Boom())
    with pytest.raises(RuntimeError):
        asyncio.run(a.discover())
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && .venv/bin/pytest -q tests/test_sam_autoscale_adapter.py`
Expected: FAIL — `ImportError: cannot import name 'EcsTarget'`

- [ ] **Step 3: 어댑터 구현**

`server/app/services/sam_autoscale.py` 끝에 이어 붙인다:

```python
import asyncio

#: 세 태그 모두 정확히 일치해야 한다. 실측(2026-08-21): Copilot 이 ECS 서비스에 이 태그를 단다.
_REQUIRED_TAGS = {"copilot-application": "wearless", "copilot-environment": "prod",
                  "copilot-service": "sam2"}


@dataclass(frozen=True)
class EcsTarget:
    cluster_arn: str
    service_arn: str


@dataclass(frozen=True)
class ServiceState:
    desired: int
    running: int
    pending: int
    oldest_started_at: datetime | None


class SamAutoscaleAdapter:
    """ECS/SNS 호출을 한 곳에. off 면 클라이언트를 만들지 않는다.

    boto3 는 동기다 — 모든 호출을 `asyncio.to_thread` 로 격리한다(`app/r2.py` 관례).
    예외는 삼키지 않고 올린다: 훅·reconciler 가 각자 맥락에 맞게 삼킨다.
    """

    def __init__(self, settings, *, ecs=None, sns=None):
        self._settings = settings
        self.enabled = getattr(settings, "sam_autoscale", "off") == "on"
        self._ecs = ecs
        self._sns = sns
        self._target: EcsTarget | None = None
        if self.enabled and (ecs is None or sns is None):
            import boto3
            from botocore.config import Config
            cfg = Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 2})
            region = "ap-northeast-2"
            self._ecs = ecs or boto3.client("ecs", region_name=region, config=cfg)
            self._sns = sns or boto3.client("sns", region_name=region, config=cfg)

    async def discover(self) -> EcsTarget | None:
        """태그로 sam2 서비스를 찾는다. 0개·2개 이상이면 None — 임의 선택하지 않는다.

        클러스터부터 나열하는 이유: Copilot 이 태스크에 주는 COPILOT_* 환경변수에 클러스터 ARN 이
        **없다**(실측 2026-08-21: SERVICE_NAME·APPLICATION_NAME·ENVIRONMENT_NAME·
        SERVICE_DISCOVERY_ENDPOINT·LB_DNS 뿐). 그래서 ListClusters 권한이 필요하다.
        """
        if not self.enabled:
            return None
        if self._target is not None:
            return self._target
        self._target = await asyncio.to_thread(self._discover_sync)
        return self._target

    def forget_target(self) -> None:
        """ServiceNotFound 를 받은 호출자가 부른다 — 다음 discover 가 한 번 더 찾는다(스택 재생성)."""
        self._target = None

    def _discover_sync(self) -> EcsTarget | None:
        matches: list[EcsTarget] = []
        for cluster in self._paged("list_clusters", "clusterArns"):
            arns = self._paged("list_services", "serviceArns", cluster=cluster)
            # DescribeServices 는 한 번에 10개까지 — 11번째 서비스의 중복을 놓치지 않게 쪼갠다.
            for i in range(0, len(arns), 10):
                desc = self._ecs.describe_services(
                    cluster=cluster, services=arns[i:i + 10], include=["TAGS"])
                for svc in desc.get("services", []):
                    tags = {t.get("key"): t.get("value") for t in (svc.get("tags") or [])}
                    if all(tags.get(k) == v for k, v in _REQUIRED_TAGS.items()):
                        matches.append(EcsTarget(cluster_arn=cluster,
                                                 service_arn=svc["serviceArn"]))
        if len(matches) != 1:
            log.warning("sam2 service discovery matched %d services — autoscale disabled",
                        len(matches))
            return None
        return matches[0]

    def _paged(self, op: str, key: str, **kw) -> list:
        """boto3 paginator 가 있으면 쓰고(실 클라이언트), 없으면(테스트 대역) 단일 호출."""
        client = self._ecs
        if hasattr(client, "get_paginator"):
            out = []
            for page in client.get_paginator(op).paginate(**kw):
                out.extend(page.get(key, []))
            return out
        return list(getattr(client, op)(**kw).get(key, []))

    async def describe(self, target: EcsTarget) -> ServiceState:
        return await asyncio.to_thread(self._describe_sync, target)

    def _describe_sync(self, target: EcsTarget) -> ServiceState:
        svc = self._ecs.describe_services(
            cluster=target.cluster_arn, services=[target.service_arn])["services"][0]
        oldest = None
        arns = self._ecs.list_tasks(cluster=target.cluster_arn, serviceName=target.service_arn,
                                    desiredStatus="RUNNING").get("taskArns", [])
        if arns:
            tasks = self._ecs.describe_tasks(cluster=target.cluster_arn, tasks=arns).get("tasks", [])
            starts = [t.get("startedAt") for t in tasks if t.get("startedAt")]
            oldest = min(starts) if starts else None
        return ServiceState(desired=int(svc.get("desiredCount") or 0),
                            running=int(svc.get("runningCount") or 0),
                            pending=int(svc.get("pendingCount") or 0),
                            oldest_started_at=oldest)

    async def set_desired(self, target: EcsTarget, count: int) -> None:
        await asyncio.to_thread(
            self._ecs.update_service, cluster=target.cluster_arn,
            service=target.service_arn, desiredCount=int(count))

    async def notify(self, subject: str, body: str) -> None:
        topic = getattr(self._settings, "sam_alert_topic_arn", None)
        if not self.enabled or not topic or self._sns is None:
            return
        await asyncio.to_thread(self._sns.publish, TopicArn=topic,
                                Subject=subject[:100], Message=body)
```

- [ ] **Step 4: 통과 확인**

Run: `cd server && .venv/bin/pytest -q tests/test_sam_autoscale_adapter.py`
Expected: PASS (11 passed)

- [ ] **Step 5: 전체 회귀**

Run: `cd server && .venv/bin/pytest -q 2>&1 | tail -1`
Expected: `2577 passed` 이상, 새 실패 0

---

### Task 4: Reconciler 백그라운드 태스크

**Files:**
- Create: `server/app/workers/sam_autoscaler.py`
- Modify: `server/app/main.py` (lifespan)
- Test: `server/tests/test_sam_autoscaler.py`

**Interfaces:**
- Consumes: Task 2 `repo.sam_demand_snapshot`, `want_running`; Task 3 `SamAutoscaleAdapter`
- Produces: `class SamAutoscaler(app, adapter)` — `async start()`, `async stop()`, `async reconcile_once(repo, conn) -> str` (반환: `"up"|"down"|"noop"|"skip"`), `async prewarm() -> None` (훅용), `RECONCILE_SECONDS = 60.0`, `LONG_RUN_ALERT_HOURS = 3`, `LOCK_KEY = "sam_autoscaler"`

- [ ] **Step 1: 실패하는 테스트**

`server/tests/test_sam_autoscaler.py`:

```python
"""Reconciler — 60초마다 want 를 계산해 ECS 와 맞춘다(양방향). 훅은 지름길일 뿐이다."""

import asyncio
from datetime import datetime, timedelta, timezone

from app.services.sam_autoscale import DemandSnapshot, EcsTarget, ServiceState
from app.workers import sam_autoscaler as mod
from app.workers.sam_autoscaler import SamAutoscaler
from conftest import make_settings

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
T = EcsTarget(cluster_arn="c", service_arn="s")


class _Adapter:
    def __init__(self, state, target=T, enabled=True):
        self.enabled = enabled
        self._target = target
        self.state = state
        self.set_calls = []
        self.notices = []

    async def discover(self):
        return self._target

    async def describe(self, target):
        return self.state

    async def set_desired(self, target, count):
        self.set_calls.append(count)
        self.state = ServiceState(desired=count, running=self.state.running,
                                  pending=self.state.pending,
                                  oldest_started_at=self.state.oldest_started_at)

    async def notify(self, subject, body):
        self.notices.append(subject)


class _Repo:
    def __init__(self, snap, lock=True):
        self.snap = snap
        self.lock = lock

    async def sam_demand_snapshot(self, conn, kinds):
        return self.snap

    async def try_advisory_lock(self, conn, key):
        return self.lock


def _busy():
    return DemandSnapshot(active_sam_jobs=1, last_sam_finished_at=None, last_upload_at=None)


def _quiet():
    return DemandSnapshot(active_sam_jobs=0, last_sam_finished_at=NOW - timedelta(hours=2),
                          last_upload_at=NOW - timedelta(hours=2))


def _scaler(adapter, **over):
    values = {"sam_autoscale": "on"}
    values.update(over)
    s = make_settings(**values)
    app = type("A", (), {"state": type("S", (), {"settings": s, "pool": None})()})()
    sc = SamAutoscaler(app, adapter)
    sc._now = lambda: NOW
    return sc


def test_it_scales_up_when_demand_exists_and_service_is_down():
    a = _Adapter(ServiceState(0, 0, 0, None))
    assert asyncio.run(_scaler(a).reconcile_once(_Repo(_busy()), None)) == "up"
    assert a.set_calls == [1]


def test_it_scales_down_when_quiet_and_service_is_up():
    a = _Adapter(ServiceState(1, 1, 0, NOW - timedelta(minutes=40)))
    assert asyncio.run(_scaler(a).reconcile_once(_Repo(_quiet()), None)) == "down"
    assert a.set_calls == [0]


def test_it_does_nothing_when_already_correct():
    a = _Adapter(ServiceState(1, 1, 0, NOW))
    assert asyncio.run(_scaler(a).reconcile_once(_Repo(_busy()), None)) == "noop"
    assert a.set_calls == []


def test_it_repairs_a_failed_hook_by_scaling_up_itself():
    """훅이 실패해 0 으로 남아 있어도 reconciler 가 올린다 — 훅은 지름길일 뿐이다."""
    a = _Adapter(ServiceState(0, 0, 0, None))
    snap = DemandSnapshot(active_sam_jobs=0, last_sam_finished_at=None,
                          last_upload_at=NOW - timedelta(minutes=1))
    assert asyncio.run(_scaler(a).reconcile_once(_Repo(snap), None)) == "up"


def test_it_never_scales_down_while_a_task_is_starting():
    """켜는 중에 내리면 콜드스타트를 버린다 — 다음 주기에 다시 본다.

    실측(2026-08-21): desired=1 요청 후 **첫 13~19초는 pending=0 running=0** 이다
    (PROVISIONING 전). pending>0 만 보면 그 창에서 내려버린다. 판정은 desired>0 and running==0.
    """
    for state in (ServiceState(1, 0, 0, None),     # 요청 직후 — pending 아직 0
                  ServiceState(1, 0, 1, None)):    # PROVISIONING~ACTIVATING
        a = _Adapter(state)
        assert asyncio.run(_scaler(a).reconcile_once(_Repo(_quiet()), None)) == "skip"
        assert a.set_calls == []


def test_it_skips_when_another_process_holds_the_lock():
    a = _Adapter(ServiceState(0, 0, 0, None))
    assert asyncio.run(_scaler(a).reconcile_once(_Repo(_busy(), lock=False), None)) == "skip"
    assert a.set_calls == []


def test_it_disables_itself_when_discovery_fails_and_alerts_once():
    a = _Adapter(ServiceState(0, 0, 0, None), target=None)
    sc = _scaler(a)
    assert asyncio.run(sc.reconcile_once(_Repo(_busy()), None)) == "skip"
    assert asyncio.run(sc.reconcile_once(_Repo(_busy()), None)) == "skip"
    assert a.notices == ["sam2 autoscale: service not found"]


def test_it_alerts_once_per_long_run():
    old = NOW - timedelta(hours=mod.LONG_RUN_ALERT_HOURS, minutes=1)
    a = _Adapter(ServiceState(1, 1, 0, old))
    sc = _scaler(a)
    asyncio.run(sc.reconcile_once(_Repo(_busy()), None))
    asyncio.run(sc.reconcile_once(_Repo(_busy()), None))
    assert a.notices == ["sam2 autoscale: running over 3h"]


def test_long_run_alert_is_keyed_by_task_start_so_a_redeploy_alerts_again():
    """래치를 '내가 내렸는가'로 풀면 외부 재배포로 태스크가 바뀐 새 가동의 알림이 묻힌다."""
    first = NOW - timedelta(hours=mod.LONG_RUN_ALERT_HOURS, minutes=1)
    a = _Adapter(ServiceState(1, 1, 0, first))
    sc = _scaler(a)
    asyncio.run(sc.reconcile_once(_Repo(_busy()), None))          # 알림 1 (first)
    a.state = ServiceState(1, 1, 0, first - timedelta(hours=5))    # 재배포 → 다른 startedAt
    asyncio.run(sc.reconcile_once(_Repo(_busy()), None))          # 알림 2 (new run)
    assert a.notices.count("sam2 autoscale: running over 3h") == 2


def test_scale_failure_alert_is_debounced_for_ten_minutes():
    class _Boom(_Adapter):
        async def set_desired(self, target, count):
            raise RuntimeError("AccessDenied")
    a = _Boom(ServiceState(0, 0, 0, None))
    sc = _scaler(a)
    for _ in range(3):                                            # 60초 간격 3회 → 알림 1회
        asyncio.run(sc.reconcile_once(_Repo(_busy()), None))
    assert a.notices == ["sam2 autoscale: scale to 1 failed"]


def test_long_run_alert_resets_after_a_scale_down():
    old = NOW - timedelta(hours=mod.LONG_RUN_ALERT_HOURS, minutes=1)
    a = _Adapter(ServiceState(1, 1, 0, old))
    sc = _scaler(a)
    asyncio.run(sc.reconcile_once(_Repo(_busy()), None))          # 알림 1
    asyncio.run(sc.reconcile_once(_Repo(_quiet()), None))         # down
    a.state = ServiceState(1, 1, 0, old)
    asyncio.run(sc.reconcile_once(_Repo(_busy()), None))          # 새 가동 → 알림 2
    assert a.notices.count("sam2 autoscale: running over 3h") == 2


def test_scale_failure_alerts_and_does_not_raise():
    class _Boom(_Adapter):
        async def set_desired(self, target, count):
            raise RuntimeError("AccessDenied")
    a = _Boom(ServiceState(0, 0, 0, None))
    assert asyncio.run(_scaler(a).reconcile_once(_Repo(_busy()), None)) == "skip"
    assert a.notices == ["sam2 autoscale: scale to 1 failed"]


def test_prewarm_scales_up_only_when_down_and_debounces():
    a = _Adapter(ServiceState(0, 0, 0, None))
    sc = _scaler(a)
    asyncio.run(sc.prewarm())
    asyncio.run(sc.prewarm())
    asyncio.run(sc.prewarm())
    assert a.set_calls == [1], "60초 안 반복 호출은 AWS 를 한 번만 부른다"


def test_prewarm_is_a_noop_when_already_up():
    a = _Adapter(ServiceState(1, 1, 0, NOW))
    asyncio.run(_scaler(a).prewarm())
    assert a.set_calls == []


def test_prewarm_swallows_aws_errors():
    class _Boom(_Adapter):
        async def describe(self, target):
            raise RuntimeError("throttled")
    asyncio.run(_scaler(_Boom(ServiceState(0, 0, 0, None))).prewarm())   # 예외 없음


def test_disabled_scaler_is_inert():
    a = _Adapter(ServiceState(0, 0, 0, None), enabled=False)
    sc = _scaler(a, sam_autoscale="off")
    assert asyncio.run(sc.reconcile_once(_Repo(_busy()), None)) == "skip"
    asyncio.run(sc.prewarm())
    assert a.set_calls == []
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && .venv/bin/pytest -q tests/test_sam_autoscaler.py`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: advisory lock 조회 추가**

`server/app/repo.py` 의 `sam_demand_snapshot` 아래:

```python
async def try_advisory_lock(conn: AsyncConnection, key: str) -> bool:
    """트랜잭션 범위 advisory lock 을 **기다리지 않고** 시도한다. 못 잡으면 False.

    api 가 2대가 되는 날 두 reconciler 가 반대 방향으로 밀지 않게 한다. 잡은 프로세스가
    커밋/롤백하면 풀린다(pg_advisory_xact_lock 계열 — repo.py 의 기존 선례와 같은 결).
    """
    async with conn.cursor() as cur:
        await cur.execute("select pg_try_advisory_xact_lock(hashtext(%s)) as locked", (key,))
        row = await cur.fetchone()
    return bool(row and row["locked"])
```

- [ ] **Step 4: reconciler 구현**

`server/app/workers/sam_autoscaler.py`:

```python
"""sam2 온디맨드 reconciler — 60초마다 want 를 계산해 ECS 실제 대수와 맞춘다(양방향).

진실의 원천은 이 루프 하나다. 업로드 라우트·SamUnavailable 의 `prewarm()` 은 "60초 기다리지
말고 지금 켜라"는 지름길일 뿐이라, 실패하거나 중복돼도 여기서 60초 안에 수렴한다.

디스패처 스윕에 얹지 않는다 — 디스패처는 워커를 await 하므로 긴 잡이 도는 동안 타이머가 멈춘다.
정본: docs/superpowers/specs/2026-08-21-sam2-on-demand-scaling-design.md §5~§8
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import datetime, timezone

from app import repo as _repo
from app.services import sam_autoscale
from app.services.sam_autoscale import SamAutoscaleAdapter

log = logging.getLogger("wearless.sam_autoscale")

RECONCILE_SECONDS = 60.0
LONG_RUN_ALERT_HOURS = 3
LOCK_KEY = "sam_autoscaler"
#: prewarm 훅의 프로세스 내 디바운스. 사진 6장 연속 업로드가 AWS 를 6번 부르지 않게.
PREWARM_DEBOUNCE_SECONDS = 60.0


class SamAutoscaler:
    def __init__(self, app, adapter: SamAutoscaleAdapter):
        self.app = app
        self.adapter = adapter
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._disabled_reason: str | None = None
        self._long_run_alerted_for: datetime | None = None   # 그 가동(startedAt)에 알렸는가
        self._last_alert_at: dict[str, float] = {}           # subject → monotonic (디바운스)
        self._last_prewarm = 0.0
        self._inflight: set[asyncio.Task] = set()            # 라우트 fire-and-forget 참조 보관
        self._now = lambda: datetime.now(timezone.utc)

    # ── lifecycle ─────────────────────────────────────────────────────────
    async def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="sam-autoscaler")

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
                    await self.reconcile_once(_repo, conn)
                    await conn.commit()            # advisory xact lock 해제
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("sam autoscaler reconcile error")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=RECONCILE_SECONDS)

    # ── core ──────────────────────────────────────────────────────────────
    async def _target(self):
        """sam2 서비스. 못 찾으면 비활성 + 알림 1회 — 요청 경로는 절대 막지 않는다."""
        if not self.adapter.enabled or self._disabled_reason:
            return None
        target = await self.adapter.discover()
        if target is None:
            self._disabled_reason = "service not found"
            log.error("sam2 autoscale disabled: service not found by tags")
            await self._alert("sam2 autoscale: service not found",
                              "copilot-service=sam2 태그로 ECS 서비스를 찾지 못해 자동 기동/종료를 "
                              "껐습니다. sam2 스택을 확인하세요.")
        return target

    async def reconcile_once(self, repo, conn) -> str:
        target = await self._target()
        if target is None:
            return "skip"
        if not await repo.try_advisory_lock(conn, LOCK_KEY):
            return "skip"

        idle = int(getattr(self.app.state.settings, "sam_autoscale_idle_minutes", 30))
        snap = await repo.sam_demand_snapshot(conn, sam_autoscale.SAM_KINDS)
        want = sam_autoscale.want_running(snap, idle_minutes=idle, now=self._now())
        try:
            state = await self.adapter.describe(target)
        except Exception as exc:
            if "ServiceNotFound" in type(exc).__name__ or "ServiceNotFound" in str(exc):
                self.adapter.forget_target()      # 스택 재생성 — 다음 주기에 한 번 더 찾는다
            log.exception("sam2 describe failed")
            return "skip"

        await self._check_long_run(state, want)

        if want and state.desired == 0:
            return await self._scale(target, 1, "up")
        if not want and state.desired > 0:
            if state.running == 0:
                # 켜는 중에 내리면 콜드스타트를 버린다. pending>0 만 보면 안 된다 — 실측
                # (2026-08-21) desired=1 요청 후 첫 13~19초는 pending=0 running=0 이다.
                return "skip"
            return await self._scale(target, 0, "down")
        return "noop"

    async def _scale(self, target, count: int, label: str) -> str:
        try:
            await self.adapter.set_desired(target, count)
        except Exception as exc:
            log.exception("sam2 scale to %s failed", count)
            await self._alert(f"sam2 autoscale: scale to {count} failed",
                              f"ECS UpdateService 실패: {type(exc).__name__}: {exc}",
                              debounce_seconds=600)           # 60초마다 메일 폭탄 방지(스펙 §8.1)
            return "skip"
        log.info("sam2 autoscale %s → desired=%s", label, count)
        return label

    async def _check_long_run(self, state, want: bool) -> None:
        if state.oldest_started_at is None or not want:
            return
        started = state.oldest_started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        hours = (self._now() - started).total_seconds() / 3600
        # 래치 키는 '내가 내렸는가'가 아니라 **그 가동의 startedAt** 이다 — 외부 재배포로 태스크가
        # 바뀌면 새 가동이고, 다시 3시간이 지나면 다시 알린다.
        if hours > LONG_RUN_ALERT_HOURS and self._long_run_alerted_for != started:
            self._long_run_alerted_for = started
            await self._alert(f"sam2 autoscale: running over {LONG_RUN_ALERT_HOURS}h",
                              f"sam2 가 {hours:.1f}시간째 켜져 있고 아직 수요가 있습니다. "
                              "버그인지 실제 사용인지 확인하세요. 강제 종료는 하지 않습니다.")

    async def _alert(self, subject: str, body: str, *, debounce_seconds: float = 0.0) -> None:
        now = time.monotonic()
        if debounce_seconds and now - self._last_alert_at.get(subject, -1e9) < debounce_seconds:
            return
        self._last_alert_at[subject] = now
        try:
            await self.adapter.notify(subject, body)
        except Exception:
            log.exception("sam2 alert failed: %s", subject)

    def prewarm_soon(self) -> None:
        """라우트용 fire-and-forget. task 참조를 set 에 들고 있어야 GC 에 안 먹힌다
        (저장소 선례: facemarket.py 의 app.state task set, image_usage.py 의 _tasks)."""
        if not self.adapter.enabled or self._disabled_reason:
            return
        t = asyncio.create_task(self.prewarm(), name="sam-prewarm")
        self._inflight.add(t)
        t.add_done_callback(self._inflight.discard)

    # ── prewarm hook ──────────────────────────────────────────────────────
    async def prewarm(self) -> None:
        """지름길 — 0대면 지금 올린다. 실패·중복 전부 무해(reconciler 가 60초 안에 덮는다)."""
        if not self.adapter.enabled or self._disabled_reason:
            return
        now = time.monotonic()
        if now - self._last_prewarm < PREWARM_DEBOUNCE_SECONDS:
            return
        self._last_prewarm = now
        try:
            target = await self._target()
            if target is None:
                return
            state = await self.adapter.describe(target)
            if state.desired == 0:
                await self.adapter.set_desired(target, 1)
                log.info("sam2 prewarm → desired=1")
        except Exception:
            log.warning("sam2 prewarm failed (reconciler will retry)", exc_info=True)
```

- [ ] **Step 5: 통과 확인**

Run: `cd server && .venv/bin/pytest -q tests/test_sam_autoscaler.py`
Expected: PASS (14 passed)

- [ ] **Step 6: lifespan 배선**

`server/app/main.py`: import 에 `from .services.sam_autoscale import SamAutoscaleAdapter` 와
`from .workers.sam_autoscaler import SamAutoscaler` 추가. lifespan 의 `sam_retry_pusher = None`
아래에 `sam_autoscaler = None`.

**디스패처 `if` 블록 안이 아니라 `if pool is not None:` 바로 아래, 디스패처와 같은 수준에** 둔다 —
디스패처는 R2·AI provider 가 있어야 뜨는데, autoscaler 는 DB 와 AWS 만 있으면 된다. 디스패처
조건문 안에 넣으면 provider 키가 빠진 환경에서 sam2 가 영영 안 켜진다(Codex 지적).

```python
            # sam2 온디맨드 기동/종료(2026-08-21). 디스패처와 독립 — DB 만 있으면 돈다.
            # off 면 어댑터가 클라이언트를 안 만들고 prewarm 은 즉시 return — 로컬·테스트는 AWS 를 모른다.
            autoscale_adapter = SamAutoscaleAdapter(settings)
            app.state.sam_autoscaler = SamAutoscaler(app, autoscale_adapter)
            if autoscale_adapter.enabled:
                sam_autoscaler = app.state.sam_autoscaler
                await sam_autoscaler.start()
```

정리 구간, `sam_retry_pusher.stop()` 앞에:

```python
        if sam_autoscaler is not None:
            await sam_autoscaler.stop()
```

`app.state.sam_autoscaler` 는 **off 여도 존재**한다(훅이 `getattr` 없이 부를 수 있게) — 다만
`prewarm()` 이 즉시 return 한다.

- [ ] **Step 7: 전체 회귀**

Run: `cd server && .venv/bin/pytest -q 2>&1 | tail -1`
Expected: `2591 passed` 이상, 새 실패 0

---

### Task 5: Prewarm 훅 2곳

**Files:**
- Modify: `server/app/routes.py:1736` (`create_upload_url`)
- Modify: `server/app/services/sam_client.py` (`SamUnavailable` 발생 지점)
- Test: `server/tests/test_sam_autoscale_hooks.py`

**Interfaces:**
- Consumes: Task 4 `app.state.sam_autoscaler.prewarm()`
- Produces: `sam_client.install_prewarm_hook(fn: Callable[[], Awaitable[None]] | None)`; `sam_client.PREWARM_HOOK`

- [ ] **Step 1: 실패하는 테스트**

`server/tests/test_sam_autoscale_hooks.py`:

```python
"""prewarm 훅 — 업로드 서명 발급과 SamUnavailable 에서 '지금 켜라'를 쏜다. 실패해도 응답은 그대로."""

import asyncio

import pytest

import app.routes as routes
from app.services import sam_client
from conftest import patch_route_db


class _Scaler:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def prewarm_soon(self):
        self.calls += 1                        # 라우트는 이것만 부른다(동기, 참조 보관은 실물이 한다)

    async def prewarm(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("aws down")


def _ok_project(monkeypatch):
    async def _get_project(conn, user_id, project_id):
        return {"id": project_id}
    monkeypatch.setattr(routes.repo, "get_project", _get_project)


def test_upload_url_calls_prewarm(client, make_token, monkeypatch):
    sc = _Scaler()
    client.app.state.sam_autoscaler = sc
    patch_route_db(monkeypatch, routes)
    _ok_project(monkeypatch)
    r = client.post("/v1/assets/upload-url",
                        headers={"Authorization": f"Bearer {make_token()}"},
                        json={"filename": "a.jpg", "mime": "image/jpeg", "size": 10,
                              "projectId": "p1", "purpose": "upload"})
    assert r.status_code == 200
    assert sc.calls == 1


def test_upload_url_succeeds_even_when_prewarm_raises(client, make_token, monkeypatch):
    class _Boom(_Scaler):
        def prewarm_soon(self):
            raise RuntimeError("aws down")
    client.app.state.sam_autoscaler = _Boom()
    patch_route_db(monkeypatch, routes)
    _ok_project(monkeypatch)
    r = client.post("/v1/assets/upload-url",
                        headers={"Authorization": f"Bearer {make_token()}"},
                        json={"filename": "a.jpg", "mime": "image/jpeg", "size": 10,
                              "projectId": "p1", "purpose": "upload"})
    assert r.status_code == 200


def test_upload_url_works_without_a_scaler_on_state(client, make_token, monkeypatch):
    if hasattr(client.app.state, "sam_autoscaler"):
        del client.app.state.sam_autoscaler
    patch_route_db(monkeypatch, routes)
    _ok_project(monkeypatch)
    r = client.post("/v1/assets/upload-url",
                        headers={"Authorization": f"Bearer {make_token()}"},
                        json={"filename": "a.jpg", "mime": "image/jpeg", "size": 10,
                              "projectId": "p1", "purpose": "upload"})
    assert r.status_code == 200


def test_sam_unavailable_fires_the_hook_once_per_raise(monkeypatch):
    sc = _Scaler()
    sam_client.install_prewarm_hook(sc.prewarm)
    try:
        class _S:
            sam_service_url = None
            sam_internal_token = None
        with pytest.raises(sam_client.SamUnavailable):
            asyncio.run(sam_client.segment_garment(_S(), {"Front": "k"}))
        assert sc.calls == 1
    finally:
        sam_client.install_prewarm_hook(None)


def test_hook_failure_does_not_mask_sam_unavailable(monkeypatch):
    sam_client.install_prewarm_hook(_Scaler(fail=True).prewarm)
    try:
        class _S:
            sam_service_url = None
            sam_internal_token = None
        with pytest.raises(sam_client.SamUnavailable):
            asyncio.run(sam_client.segment_garment(_S(), {"Front": "k"}))
    finally:
        sam_client.install_prewarm_hook(None)
```

> `patch_route_db(monkeypatch, routes_module)` 는 `conftest.py:30` 의 실제 시그니처다(컨텍스트
> 매니저가 아니다). `import app.routes as routes` 를 테스트 상단에 둔다. FakeConn 이 `get_project`
> 에 뭘 돌려주는지는 기존 upload-url 테스트를 따라 monkeypatch 한다.

- [ ] **Step 2: 실패 확인**

Run: `cd server && .venv/bin/pytest -q tests/test_sam_autoscale_hooks.py`
Expected: FAIL — `AttributeError: module 'app.services.sam_client' has no attribute 'install_prewarm_hook'` 및 `sc.calls == 0`

- [ ] **Step 3: sam_client 훅**

`server/app/services/sam_client.py` 의 `class SamUnavailable` 아래에:

```python
#: sam2 온디맨드(2026-08-21): SamUnavailable 이 나는 순간 "지금 켜라"를 쏜다. 업로드 없이
#: SAM 이 필요해지는 모든 경로(보관함 재진입 등)를 한 곳에서 덮는다. 훅은 실패해도 예외를
#: 바꾸지 않는다 — 원래의 SamUnavailable 이 그대로 올라간다.
PREWARM_HOOK = None


def install_prewarm_hook(fn) -> None:
    global PREWARM_HOOK
    PREWARM_HOOK = fn


async def _fire_prewarm() -> None:
    hook = PREWARM_HOOK
    if hook is None:
        return
    try:
        await hook()
    except Exception:  # noqa: BLE001 - 훅 실패가 SamUnavailable 을 가리면 안 된다
        log.warning("sam prewarm hook failed", exc_info=True)


async def _raise_unavailable(msg: str, cause: BaseException | None = None):
    """prewarm 을 먼저 쏘고 SamUnavailable 을 올린다. `from cause` 로 원인 체인을 그대로 보존한다."""
    await _fire_prewarm()
    if cause is not None:
        raise SamUnavailable(msg) from cause
    raise SamUnavailable(msg)
```

그리고 파일 안의 **모든** `raise SamUnavailable(...)` 10곳(155·168·170·173·177·193·207·209·212·216행)을
`await _raise_unavailable(...)` 로 바꾼다. `from e` 가 붙은 것은 `cause=e` 로 옮긴다. 예:

```python
        raise SamUnavailable(f"SAM request timed out after {timeout}s") from e
→       await _raise_unavailable(f"SAM request timed out after {timeout}s", e)
```

`log` 가 파일에 없으면 상단에 `import logging` / `log = logging.getLogger(__name__)` 추가.

- [ ] **Step 4: 업로드 라우트 훅**

`server/app/routes.py` 의 `create_upload_url` 에서 `asset_id = str(uuid.uuid4())` 바로 앞에:

```python
    # sam2 온디맨드(2026-08-21): 사진이 올라오는 가장 이른 순간에 "지금 켜라". 실패·중복 전부
    # 무해 — reconciler 가 60초 안에 덮고, 어댑터가 off 면 즉시 return 한다. 발급 응답을
    # 기다리게 하지 않도록 백그라운드 태스크로 던진다.
    scaler = getattr(request.app.state, "sam_autoscaler", None)
    if scaler is not None:
        with contextlib.suppress(Exception):   # 훅은 발급 응답을 절대 막지 않는다
            scaler.prewarm_soon()
```

라우트에서 `asyncio.create_task` 를 직접 만들지 않는다 — 참조를 안 들고 있으면 GC 될 수 있다
(저장소 선례 `facemarket.py:1102`, `image_usage.py:78` 은 전부 set 에 보관). Task 4 의
`SamAutoscaler.prewarm_soon()` 이 task 를 만들고 자기 `_inflight` set 에 보관한다. 테스트 `_Scaler`
대역에도 `prewarm_soon` 을 두고 `calls += 1`.

- [ ] **Step 5: lifespan 에서 훅 설치**

`server/app/main.py` 의 `app.state.sam_autoscaler = SamAutoscaler(...)` 직후:

```python
                sam_client.install_prewarm_hook(app.state.sam_autoscaler.prewarm)
```

(`from .services import sam_client` import 확인.) 정리 구간에서 `sam_client.install_prewarm_hook(None)`.

- [ ] **Step 6: 통과 확인**

Run: `cd server && .venv/bin/pytest -q tests/test_sam_autoscale_hooks.py tests/test_canonical_pipeline.py tests/test_tone_mask_retry.py tests/test_sam_job_transient_failure.py`
Expected: 전부 PASS — `SamUnavailable` 이 여전히 같은 메시지·같은 `__cause__` 로 올라온다

- [ ] **Step 7: 전체 회귀**

Run: `cd server && .venv/bin/pytest -q 2>&1 | tail -1`
Expected: `2596 passed` 이상, 새 실패 0

---

### Task 6: 인프라 — addon · 매니페스트 · 워크플로

**Files:**
- Create: `copilot/api/addons/sam-autoscale.yml`
- Modify: `copilot/api/manifest.yml` (`variables`)
- Modify: `copilot/sam2/manifest.yml:53`
- Modify: `.github/workflows/deploy-sam2.yml:107`, `.github/workflows/deploy-server.yml` (Copilot 설치 줄)
- Test: `server/tests/test_deploy_manifest_qc_flags.py` (기존 파일에 케이스 추가)

- [ ] **Step 1: 실패하는 테스트**

`server/tests/test_deploy_manifest_qc_flags.py` — 이 파일은 `MANIFEST`(api 매니페스트 경로)와
`manifest_vars` fixture, 그리고 **"미선언 플래그가 조용히 off 로 떨어지는 사고"를 막는 `QC_FLAGS`
목록**을 이미 갖고 있다. 그 목록에 한 줄 추가하면 "값이 로더를 통과해 살아남는가" 검증을 공짜로
얻는다:

```python
    # sam2 온디맨드(2026-08-21). 미선언이면 reconciler 가 매 주기 skip — sam2 가 영영 안 켜진다.
    ("SAM_AUTOSCALE", "sam_autoscale"),
```

파일 끝에 추가:

```python
SAM2_MANIFEST = MANIFEST.parent.parent / "sam2/manifest.yml"
ADDON = MANIFEST.parent / "addons/sam-autoscale.yml"


def test_sam2_manifest_defaults_to_zero_tasks():
    """온디맨드(2026-08-21): 배포가 desiredCount 를 1로 되돌리면 상시 가동으로 복귀한다."""
    doc = yaml.safe_load(SAM2_MANIFEST.read_text(encoding="utf-8"))
    assert doc["count"] == 0


def test_api_manifest_declares_idle_minutes_but_not_the_topic(manifest_vars):
    assert manifest_vars["SAM_AUTOSCALE_IDLE_MINUTES"] == "30"
    # 토픽 ARN 은 addon Output 이 자동 주입한다 — 매니페스트에 박으면 배포가 깨지거나 값이 갈린다.
    assert "SAM_ALERT_TOPIC_ARN" not in manifest_vars


def test_autoscale_addon_exists_with_scoped_permissions():
    addon = yaml.safe_load(ADDON.read_text(encoding="utf-8"))
    res = addon["Resources"]
    assert res["SamAlertTopic"]["Type"] == "AWS::SNS::Topic"
    assert res["SamAlertEmail"]["Type"] == "AWS::SNS::Subscription"
    assert res["SamAlertEmail"]["Properties"]["Endpoint"] == "dlftkd3269@gmail.com"
    statements = res["SamAutoscalePolicy"]["Properties"]["PolicyDocument"]["Statement"]
    actions = set()
    for st in statements:
        acts = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
        actions.update(acts)
    assert {"ecs:ListClusters", "ecs:ListServices", "ecs:DescribeServices", "ecs:ListTasks",
            "ecs:DescribeTasks", "ecs:UpdateService", "sns:Publish"} <= actions
    assert not any(a.startswith("iam:") for a in actions)
    # UpdateService 는 sam2 태그 조건이 있어야 한다 — api 가 자기 자신을 내리면 안 된다.
    upd = next(st for st in statements if st["Action"] == "ecs:UpdateService")
    assert upd["Condition"]["StringEquals"]["aws:ResourceTag/copilot-service"] == "sam2"
    assert "SamAutoscalePolicyArn" in addon["Outputs"]
```

> CloudFormation 단축 태그(`!Sub`, `!Ref`)는 `yaml.safe_load` 가 모른다. 파일 상단에 한 번:
> ```python
> yaml.SafeLoader.add_multi_constructor("!", lambda loader, suffix, node: None)
> ```
> 이미 같은 처리가 있는지 `grep -n "add_multi_constructor\|add_constructor" server/tests/*.py` 로 확인.

- [ ] **Step 2: 실패 확인**

Run: `cd server && .venv/bin/pytest -q tests/test_deploy_manifest_qc_flags.py`
Expected: 새 3건 FAIL

- [ ] **Step 3: addon 작성**

`copilot/api/addons/sam-autoscale.yml`:

```yaml
# sam2 온디맨드 기동/종료(2026-08-21)가 api 태스크에 요구하는 권한과 알림 토픽.
#
# 수동으로 만들지 않는다 — 이 저장소는 raw `aws ssm put-parameter` 로 만든 시크릿에 Copilot 태그가
# 없어 태스크가 못 읽던 사고를 겪었다(copilot/api/manifest.yml, 2026-07-17). addon 은 Copilot 이
# 같은 스택 안에서 만들고 지운다.
#
# 정본: docs/superpowers/specs/2026-08-21-sam2-on-demand-scaling-design.md §8~§9
Parameters:
  App:
    Type: String
  Env:
    Type: String
  Name:
    Type: String

Resources:
  SamAlertTopic:
    Type: AWS::SNS::Topic
    Properties:
      TopicName: !Sub '${App}-${Env}-sam-autoscale-alerts'

  # Topic 에 임베드하지 않고 별도 리소스로 — 임베드하면 Topic 삭제 시 구독이 같이 안 지워질 수 있다.
  # 배포 시점에 확인 메일이 간다. 오너가 링크를 1회 클릭해야 알림이 살아난다.
  SamAlertEmail:
    Type: AWS::SNS::Subscription
    Properties:
      TopicArn: !Ref SamAlertTopic
      Protocol: email
      Endpoint: dlftkd3269@gmail.com

  SamAutoscalePolicy:
    Type: AWS::IAM::ManagedPolicy
    Properties:
      PolicyDocument:
        Version: '2012-10-17'
        Statement:
          # ListClusters/ListServices 는 ARN 리소스가 없는 액션 — 조건으로 이 계정·리전만.
          - Sid: Discover
            Effect: Allow
            Action:
              - ecs:ListClusters
              - ecs:ListServices
            Resource: '*'
          # ListTasks 도 ARN 리소스가 없는 액션 — ecs:cluster 조건으로 이 환경 클러스터만.
          - Sid: ListTasksInEnvCluster
            Effect: Allow
            Action: ecs:ListTasks
            Resource: '*'
            Condition:
              ArnLike:
                ecs:cluster: !Sub 'arn:${AWS::Partition}:ecs:${AWS::Region}:${AWS::AccountId}:cluster/${App}-${Env}-Cluster-*'
          # 읽기는 이 클러스터의 서비스·태스크로. Copilot 클러스터명은 스택마다 접미사가 붙으므로
          # 패턴으로 건다.
          - Sid: Inspect
            Effect: Allow
            Action:
              - ecs:DescribeServices
              - ecs:DescribeTasks
            Resource:
              - !Sub 'arn:aws:ecs:${AWS::Region}:${AWS::AccountId}:cluster/${App}-${Env}-Cluster-*'
              - !Sub 'arn:aws:ecs:${AWS::Region}:${AWS::AccountId}:service/${App}-${Env}-Cluster-*/*'
              - !Sub 'arn:aws:ecs:${AWS::Region}:${AWS::AccountId}:task/${App}-${Env}-Cluster-*/*'
          # 대수 변경은 **sam2 서비스 하나**만. 태그 조건이 api 자신을 포함한 다른 서비스를 막는다.
          - Sid: ScaleSam2Only
            Effect: Allow
            Action: ecs:UpdateService
            Resource: !Sub 'arn:aws:ecs:${AWS::Region}:${AWS::AccountId}:service/${App}-${Env}-Cluster-*/*'
            Condition:
              StringEquals:
                aws:ResourceTag/copilot-application: !Ref App
                aws:ResourceTag/copilot-environment: !Ref Env
                aws:ResourceTag/copilot-service: sam2
          - Sid: Alert
            Effect: Allow
            Action: sns:Publish
            Resource: !Ref SamAlertTopic

Outputs:
  # `PolicyArn` 으로 끝나는 ManagedPolicy Output 은 Copilot 이 task role 에 자동 부착한다.
  SamAutoscalePolicyArn:
    Value: !Ref SamAutoscalePolicy
  # 일반 Output 은 Copilot 이 **SAM_ALERT_TOPIC_ARN 환경변수로 자동 주입**한다 — 매니페스트에
  # 따로 쓰지 않는다. config.py 가 그 이름을 읽는다.
  SamAlertTopicArn:
    Value: !Ref SamAlertTopic
```

- [ ] **Step 4: api 매니페스트 변수**

`copilot/api/manifest.yml` 의 `variables:` 블록, `MANNEQUIN_TONE_EDITOR: "on"` 아래에:

```yaml
  # ── sam2 온디맨드 기동/종료 (2026-08-21, 오너 승인) ──
  # sam2 는 평소 0대(copilot/sam2/manifest.yml count: 0). 사진 업로드·SamUnavailable 이 켜고,
  # 30분 유휴면 reconciler 가 끈다. off 로 바꾸면 자동 기동이 멈추니 sam2 count 도 같이 되돌릴 것.
  # 정본: docs/superpowers/specs/2026-08-21-sam2-on-demand-scaling-design.md
  SAM_AUTOSCALE: "on"
  SAM_AUTOSCALE_IDLE_MINUTES: "30"
```

`SAM_ALERT_TOPIC_ARN` 은 **매니페스트에 쓰지 않는다.** Copilot workload addon 의 일반 `Outputs`
(`SamAlertTopicArn`) 는 Copilot 이 **자동으로 `SAM_ALERT_TOPIC_ARN` 환경변수로 주입**한다
(공식: workload addon Outputs → 컨테이너 env). `from_cfn` 은 환경 addon 의 명시적 `Export` 를
소비하는 기능이라 여기엔 맞지 않는다(Codex 검토, 공식 문서 근거).

- [ ] **Step 5: sam2 매니페스트 `count: 0`**

`copilot/sam2/manifest.yml:53`:

```yaml
# 평소 0대(2026-08-21, 오너 승인). api 의 reconciler 가 수요에 따라 1대로 올리고 30분 유휴면
# 내린다 — copilot/api/manifest.yml SAM_AUTOSCALE 참고. 여기를 1로 되돌리면 배포마다 상시
# 가동으로 복귀한다(그게 의도라면 api 의 SAM_AUTOSCALE 도 off 로).
# Copilot 의 Count 타입은 "0 is a valid value" 로 명시돼 있다(v1.34.1 소스).
count: 0
```

- [ ] **Step 6: Copilot 버전 고정**

`.github/workflows/deploy-sam2.yml:107` 과 `deploy-server.yml` 의 Copilot 설치 줄에서 `latest` 를
고정 버전으로 바꾼다. 먼저 현재 줄을 확인한다:

Run: `grep -n "copilot" .github/workflows/deploy-sam2.yml | grep -i "curl\|download\|latest"`

두 워크플로(`deploy-sam2.yml:107`, `deploy-server.yml:111`) 모두 같은 줄이다:

```diff
- curl -Lo copilot https://github.com/aws/copilot-cli/releases/latest/download/copilot-linux
+ curl -Lo copilot https://github.com/aws/copilot-cli/releases/download/v1.34.1/copilot-linux
```

(`count: 0` 유효성을 확인한 버전.) 그리고 `deploy-server.yml` 의 **path filter 두 곳**
(`on.push.paths`, `on.pull_request.paths`, 그리고 `:84` 근처의 `filters.server`)에
`"copilot/api/addons/**"` 를 추가한다 — 안 넣으면 addon 만 바꾼 커밋이 배포를 안 탄다.

- [ ] **Step 7: 통과 확인**

Run: `cd server && .venv/bin/pytest -q tests/test_deploy_manifest_qc_flags.py`
Expected: PASS

- [ ] **Step 8: 전체 회귀**

Run: `cd server && .venv/bin/pytest -q 2>&1 | tail -1`
Expected: `2599 passed` 이상, 새 실패 0

---

## 배포 순서 (오너 액션 포함)

1. 마이그레이션 2건 적용(오너): `20260821000000_sam_retry_index.sql`, `20260821010000_sam_autoscale_index.sql`
2. `copilot svc package --name api --env prod` 로 addon·`from_cfn` 이 렌더되는지 확인
3. api 배포 → **구독 확인 메일 클릭**(오너) → 테스트 알림 1건 발송해 도달 확인
4. sam2 배포(`count: 0`) → 스택 완료 후 desired 0 확인
5. 사진 1장 업로드 → 1~2분 안에 sam2 RUNNING 확인 → 30분 방치 → 0대 확인
6. 2주 계측(스펙 §11) 후 재판단

## Self-Review

**Spec coverage:** §4 세 조건 → Task 2 / §5 끄기·PENDING 보호·advisory lock → Task 4 / §6.1 훅 2곳 → Task 5 / §6.2 별도 태스크 → Task 4 / §7 태그 탐색·0·2개 거부·캐시 → Task 3 / §8 3h 알림만·가동당 1회·SNS addon·별도 Subscription → Task 4, 6 / §9 권한 → Task 6 / §10 스위치·`count: 0`·버전 고정 → Task 1, 6 / §12 인덱스 → Task 2 / §14 `desiredCount=1 ≠ ready` 는 Phase 0 재시도가 흡수(이미 구현).

**Placeholder scan:** `from_cfn` export 이름과 `patch_route_db` 시그니처 두 곳은 "확인 후 맞춘다"로 남겼다 — 둘 다 저장소·버전 의존이라 계획에서 단정하면 오히려 틀린다. 확인 방법을 명시했다.

**Type consistency:** `DemandSnapshot` 3필드, `ServiceState` 4필드, `EcsTarget` 2필드가 Task 2·3·4 테스트·구현에서 동일. `reconcile_once(repo, conn)` 반환 4종 문자열 일치. `try_advisory_lock(conn, key)` 가 Task 4 대역과 구현에서 같다.
