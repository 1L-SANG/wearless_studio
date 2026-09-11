"""sam2 온디맨드 reconciler — 60초마다 want 를 계산해 ECS 실제 대수와 맞춘다(양방향).

진실의 원천은 이 루프 하나다. 업로드 라우트·SamUnavailable 의 `prewarm()` 은 "60초 기다리지
말고 지금 켜라"는 지름길일 뿐이라, 실패하거나 중복돼도 여기서 60초 안에 수렴한다.

디스패처 스윕에 얹지 않는다 — 디스패처는 워커를 await 하므로 긴 잡이 도는 동안 타이머가 멈춘다.
디스패처 기동 조건(R2·AI provider)과도 독립이다 — DB 만 있으면 돈다.
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
#: 프로세스 로컬이라 api 2대면 2번 부를 수 있지만 UpdateService 는 같은 값에 no-op 이라 무해.
PREWARM_DEBOUNCE_SECONDS = 60.0
#: scale 실패 알림 디바운스 — 60초마다 메일 폭탄을 막는다(스펙 §8.1).
ALERT_DEBOUNCE_SECONDS = 600.0


async def _sam_demand(repo, conn):
    return await repo.sam_demand_snapshot(conn, sam_autoscale.SAM_KINDS)


class SamAutoscaler:
    def __init__(self, app, adapter: SamAutoscaleAdapter, *, demand_fn=None,
                 idle_attr="sam_autoscale_idle_minutes", name="sam2", lock_key=None,
                 capacity_attr=None, max_tasks_attr=None, start_grace_attr=None):
        self.app = app
        self.adapter = adapter
        self._demand_fn = demand_fn or _sam_demand
        self._idle_attr = idle_attr
        self._name = name
        self._lock_key = lock_key or LOCK_KEY
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._disabled_reason: str | None = None
        self._long_run_alerted_for: datetime | None = None   # 그 가동(startedAt)에 알렸는가
        self._last_alert_at: dict[str, float] = {}           # subject → monotonic (디바운스)
        self._last_prewarm = 0.0
        # "켜지는 중" 이 영원히 끝나지 않는 경우를 끊는 근거. ECS 는 태스크가 못 뜨면 스스로
        # 재시도하지만 RunPod 파드는 켜 둔 채 아무것도 안 할 수 있다(컨테이너가 죽어도
        # desiredStatus 는 RUNNING — 2026-09-10 실측). 그러면 수요가 사라져도
        # `running==0 이면 건드리지 않는다` 규칙에 걸려 **영원히 안 꺼진다**.
        self._start_grace_attr = start_grace_attr
        self._starting_since: float | None = None
        # 대수 산출용. 미지정이면 1/1 — sam2·opendid 는 want_running 과 동일하게 굴러간다.
        # detail-worker 만 태스크당 잡 N개를 처리하므로 이 둘을 설정 키로 받는다.
        self._capacity_attr = capacity_attr
        self._max_tasks_attr = max_tasks_attr
        self._inflight: set[asyncio.Task] = set()            # 라우트 fire-and-forget 참조 보관
        self._now = lambda: datetime.now(timezone.utc)

    # ── lifecycle ─────────────────────────────────────────────────────────
    async def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name=f"{self._name}-autoscaler")

    async def stop(self):
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        for t in list(self._inflight):
            t.cancel()

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
            log.error("%s autoscale disabled: service not found by tags", self._name)
            await self._alert(f"{self._name} autoscale: service not found",
                              f"copilot-service={self._name} 태그로 ECS 서비스를 찾지 못해 자동 "
                              f"기동/종료를 껐습니다. {self._name} 스택을 확인하세요.")
        return target

    async def reconcile_once(self, repo, conn) -> str:
        """한 주기. 반환: up | down | noop | skip."""
        target = await self._target()
        if target is None:
            return "skip"
        if not await repo.try_advisory_lock(conn, self._lock_key):
            return "skip"

        if hasattr(self.adapter, "begin_cycle"):
            self.adapter.begin_cycle()      # 한 주기에 파드 생성 1회 제한 리셋
        idle = int(getattr(self.app.state.settings, self._idle_attr, 30))
        snap = await self._demand_fn(repo, conn)
        settings = self.app.state.settings
        capacity = max(1, int(getattr(settings, self._capacity_attr, 1) or 1)
                       ) if self._capacity_attr else 1
        max_tasks = max(1, int(getattr(settings, self._max_tasks_attr, 1) or 1)
                        ) if self._max_tasks_attr else 1
        want_n = sam_autoscale.want_count(
            snap, idle_minutes=idle, per_task_capacity=capacity,
            max_tasks=max_tasks, now=self._now())
        want = want_n > 0
        try:
            state = await self.adapter.describe(target)
        except Exception as exc:
            if "ServiceNotFound" in type(exc).__name__ or "ServiceNotFound" in str(exc):
                self.adapter.forget_target()      # 스택 재생성 — 다음 주기에 한 번 더 찾는다
            log.exception("%s describe failed", self._name)
            return "skip"

        await self._check_long_run(state, want)
        stalled = self._track_start(state)
        if stalled and want:
            # 수요가 있으니 끄지는 않는다(끄면 곧바로 다시 켜야 한다) — 대신 알린다.
            # 이 알림이 없으면 "켜져 있는데 영원히 안 뜨는" 상태를 아무도 모른다.
            await self._alert(f"{self._name} autoscale: not healthy while work is waiting",
                              f"{self._start_grace_minutes()}분이 지나도 헬스가 통과하지 못했는데 "
                              "대기 중인 작업이 있습니다. 파드는 그대로 둡니다 — 시작 스크립트·토큰·"
                              "볼륨을 확인하세요.",
                              debounce_seconds=ALERT_DEBOUNCE_SECONDS)

        if want and state.desired < want_n:
            return await self._scale(target, want_n, "up")
        if want and state.desired > want_n and state.running > 0:
            # 밀린 잡이 줄면 대수도 줄인다. running==0 이면 켜는 중이라 건드리지 않는다.
            return await self._scale(target, want_n, "down")
        if not want and state.desired > 0:
            if state.running == 0 and not stalled:
                # 켜는 중에 내리면 콜드스타트를 버린다. pending>0 만 보면 안 된다 — 실측
                # (2026-08-21) desired=1 요청 후 첫 13~19초는 pending=0 running=0 이다.
                return "skip"
            if state.running == 0:
                # 유예를 넘겨도 안 떴다 = 콜드스타트를 지킬 이유가 없다. 수요도 없으니 끈다.
                await self._alert(f"{self._name} autoscale: never became healthy",
                                  f"켜 뒀는데 {self._start_grace_minutes()}분 동안 헬스가 통과하지 못했습니다. "
                                  "수요가 없어 내립니다(요금 방지). 시작 스크립트·토큰·볼륨을 확인하세요.",
                                  debounce_seconds=ALERT_DEBOUNCE_SECONDS)
                self._starting_since = None
            return await self._scale(target, 0, "down")
        return "noop"

    # ── 기동 감시 ────────────────────────────────────────────────────────
    def _start_grace_minutes(self) -> int | None:
        if not self._start_grace_attr:
            return None
        value = getattr(self.app.state.settings, self._start_grace_attr, None)
        try:
            minutes = int(value)
        except (TypeError, ValueError):
            return None
        return minutes if minutes > 0 else None

    def _track_start(self, state) -> bool:
        """지금 '켜지는 중'인가를 추적하고, 유예를 넘겼는지 돌려준다.

        유예가 설정되지 않은 서비스(sam2·opendid·detail-worker)는 항상 False —
        기존 동작이 한 줄도 바뀌지 않는다.
        """
        starting = state.desired > 0 and state.running == 0
        if not starting:
            self._starting_since = None
            return False
        if self._starting_since is None:
            self._starting_since = time.monotonic()
        grace = self._start_grace_minutes()
        if grace is None:
            return False
        stalled = (time.monotonic() - self._starting_since) > grace * 60
        if stalled:
            log.warning("%s autoscale: still not healthy after %d min", self._name, grace)
        return stalled

    async def _scale(self, target, count: int, label: str) -> str:
        try:
            await self.adapter.set_desired(target, count)
        except Exception as exc:
            log.exception("%s scale to %s failed", self._name, count)
            await self._alert(f"{self._name} autoscale: scale to {count} failed",
                              f"ECS UpdateService 실패: {type(exc).__name__}: {exc}",
                              debounce_seconds=ALERT_DEBOUNCE_SECONDS)
            return "skip"
        log.info("%s autoscale %s → desired=%s", self._name, label, count)
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
            await self._alert(f"{self._name} autoscale: running over {LONG_RUN_ALERT_HOURS}h",
                              f"{self._name} 가 {hours:.1f}시간째 켜져 있고 아직 수요가 있습니다. "
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
                log.info("%s prewarm → desired=1", self._name)
        except Exception:
            log.warning("%s prewarm failed (reconciler will retry)", self._name, exc_info=True)

    def prewarm_soon(self) -> None:
        """라우트용 fire-and-forget. task 참조를 set 에 들고 있어야 GC 에 안 먹힌다
        (저장소 선례: facemarket.py 의 app.state task set, image_usage.py 의 _tasks)."""
        if not self.adapter.enabled or self._disabled_reason:
            return
        t = asyncio.create_task(self.prewarm(), name=f"{self._name}-prewarm")
        self._inflight.add(t)
        t.add_done_callback(self._inflight.discard)
