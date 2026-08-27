"""sam2 를 Service Connect 이름 대신 task 사설 IP 로 직접 부르는 폴백.

**왜 있는가.** 0대에서 깨어나는 sam2 는 실측(2026-08-27, prod use1) 이렇게 움직인다:

    +5.0s   DescribeTasks 가 privateIPv4Address 를 준다 (아직 PROVISIONING)
    +59.6s  uvicorn 이 8080 을 듣기 시작한다  ← 이 순간부터 IP 직결이면 답한다
    +146.7s Service Connect 등록 완료          ← `http://sam2:8080` 이 처음 통하는 순간

그 사이 **87초 동안 서비스는 멀쩡히 살아 있는데 아무도 못 부른다.** 프리웜부터 첫 SAM 성공까지
209초가 걸렸고 그중 실제 추론은 57초였다. 이 모듈은 그 87초를 회수한다.

**설계.** 따뜻한 경로는 건드리지 않는다 — 평소 호출은 그대로 Service Connect 이름으로 나가고,
그게 **전송 계층에서** 실패했을 때만(콜드스타트의 지문은 `httpx.ReadError` 다) 여기서 IP 를
받아 한 번 더 시도한다. 타임아웃과 4xx/5xx 응답은 재시도하지 않는다: 전자는 90초를 두 번 쓰게
되고, 후자는 이미 sam2 가 답한 것이라 IP 를 바꿔도 답이 같다.

**보안.** 노출은 늘어나지 않는다. 같은 VPC·같은 보안그룹(자기 SG 發 전 프로토콜 허용) 안이고,
`/segment-*` 는 여전히 베어러 토큰을 요구한다. `/health` 는 원래도 무인증이다.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

import httpx

log = logging.getLogger("wearless.sam_endpoint")

DEFAULT_PORT = 8080
#: 찾아낸 IP 를 이만큼 재사용한다. 태스크가 바뀌면 요청이 실패하면서 invalidate 된다.
CACHE_SECONDS = 30.0
#: 살아 있는지 묻는 데 쓰는 시간. 길게 잡을 이유가 없다 — 안 뜬 태스크는 즉시 거절한다.
PROBE_TIMEOUT_S = 2.0


def service_port(url: str | None) -> int:
    """`http://sam2:8080` 에서 포트를 되찾는다. 포트가 한 곳(매니페스트)에서만 오게 하려는 것."""
    try:
        parsed = urlparse(url or "")
        if parsed.port:
            return int(parsed.port)
    except (TypeError, ValueError):
        pass
    return DEFAULT_PORT


class SamEndpointResolver:
    """`http://<task-ip>:<port>` 를 찾아 캐시한다. 못 찾으면 None — 호출자는 원래 경로를 쓴다.

    ECS 탐색은 어댑터(`SamAutoscaleAdapter`)가 이미 하는 일이라 그대로 빌려 쓴다. 그래서 새
    IAM 권한이 없다: `ecs:ListTasks`·`ecs:DescribeTasks` 는 sam-autoscale addon 이 이미 준다.
    """

    def __init__(self, settings, adapter, *, cache_seconds: float = CACHE_SECONDS,
                 probe_timeout: float = PROBE_TIMEOUT_S, clock=time.monotonic):
        self._settings = settings
        self._adapter = adapter
        self._cache_seconds = float(cache_seconds)
        self._probe_timeout = float(probe_timeout)
        self._clock = clock
        self._cached: str | None = None
        self._cached_until = 0.0
        self.enabled = (
            getattr(settings, "sam_direct_endpoint", "off") == "on"
            and getattr(adapter, "enabled", False)
        )

    # ── 조회 ──────────────────────────────────────────────────────────────
    async def direct_url(self) -> str | None:
        """지금 쓸 수 있는 직결 URL. 없으면 None.

        예외를 올리지 않는다 — 이 경로는 **최적화**지 요구사항이 아니다. AWS 가 삐끗해도
        호출자는 Service Connect 로 하던 일을 계속한다.
        """
        if not self.enabled:
            return None
        now = self._clock()
        if self._cached and now < self._cached_until:
            return self._cached
        try:
            target = await self._adapter.discover()
            if target is None:
                return None
            ips = await self._adapter.task_ips(target)
        except Exception:  # noqa: BLE001 - 최적화 경로가 잡을 죽이면 안 된다
            log.warning("sam endpoint lookup failed", exc_info=True)
            return None

        port = service_port(getattr(self._settings, "sam_service_url", None))
        for ip in ips:
            base = f"http://{ip}:{port}"
            if await self._alive(base):
                self._cached, self._cached_until = base, self._clock() + self._cache_seconds
                log.info("sam endpoint resolved to task ip %s", base)
                return base
        return None

    async def ready(self) -> bool:
        """sam2 가 지금 요청을 받을 수 있는가. 재시도를 언제 밀지 결정하는 데 쓴다."""
        return (await self.direct_url()) is not None

    def invalidate(self) -> None:
        """직결 호출이 실패한 호출자가 부른다 — 태스크가 교체되면 IP 가 죽는다."""
        self._cached, self._cached_until = None, 0.0

    # ── 내부 ──────────────────────────────────────────────────────────────
    async def _alive(self, base: str) -> bool:
        """`/health` 200 이면 산 것으로 본다.

        `modelLoaded` 까지 요구하지 않는다: 모델은 기동 때 배경에서 올라가고(`sam_service`
        preload), 로드가 아직이면 요청이 같은 락에서 기다렸다가 이어간다. 여기서 더 기다리면
        회수하려던 87초를 도로 까먹는다.
        """
        try:
            async with httpx.AsyncClient(timeout=self._probe_timeout) as client:
                r = await client.get(f"{base}/health")
        except httpx.HTTPError:
            return False
        return r.status_code == 200
