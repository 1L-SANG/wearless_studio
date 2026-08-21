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
            log.error("sam2 autoscale disabled: service not found by tags")
            await self._alert("sam2 autoscale: service not found",
                              "copilot-service=sam2 태그로 ECS 서비스를 찾지 못해 자동 기동/종료를 "
                              "껐습니다. sam2 스택을 확인하세요.")
        return target

    async def reconcile_once(self, repo, conn) -> str:
        """한 주기. 반환: up | down | noop | skip."""
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
                              debounce_seconds=ALERT_DEBOUNCE_SECONDS)
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

    def prewarm_soon(self) -> None:
        """라우트용 fire-and-forget. task 참조를 set 에 들고 있어야 GC 에 안 먹힌다
        (저장소 선례: facemarket.py 의 app.state task set, image_usage.py 의 _tasks)."""
        if not self.adapter.enabled or self._disabled_reason:
            return
        t = asyncio.create_task(self.prewarm(), name="sam-prewarm")
        self._inflight.add(t)
        t.add_done_callback(self._inflight.discard)
