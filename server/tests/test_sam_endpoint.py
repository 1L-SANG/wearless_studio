"""콜드스타트 직결 폴백 — Service Connect 등록을 기다리지 않고 task IP 로 간다.

실측 근거(2026-08-27 prod use1, 태스크 하나 전수 추적):
    +5.0s   DescribeTasks 가 privateIPv4Address 를 준다 (lastStatus=PROVISIONING)
    +59.6s  uvicorn listening
    +146.7s Service Connect 등록 완료 = `http://sam2:8080` 이 처음 통함
그 87초가 이 모듈이 회수하려는 시간이다.
"""

import asyncio

import httpx
import pytest

from app.services import sam_client
from app.services.sam_endpoint import SamEndpointResolver, service_port


class _Settings:
    sam_service_url = "http://sam2:8080"
    sam_internal_token = "t"
    sam_request_timeout_s = 1.0
    sam_direct_endpoint = "on"


class _Adapter:
    """ECS 어댑터 대역. discover/task_ips 만 쓴다."""

    def __init__(self, ips=("10.0.0.205",), enabled=True, target="tgt", boom=False):
        self.enabled = enabled
        self._ips = list(ips)
        self._target = target
        self._boom = boom
        self.lookups = 0

    async def discover(self):
        return self._target

    async def task_ips(self, target):
        self.lookups += 1
        if self._boom:
            raise RuntimeError("ecs down")
        return list(self._ips)


def _resolver(adapter=None, *, settings=None, alive=True, **kw):
    r = SamEndpointResolver(settings or _Settings(), adapter or _Adapter(), **kw)

    async def _alive(base):
        return alive(base) if callable(alive) else alive

    r._alive = _alive
    return r


# ── 탐색 ─────────────────────────────────────────────────────────────────────

def test_it_resolves_a_live_task_ip():
    r = _resolver()
    assert asyncio.run(r.direct_url()) == "http://10.0.0.205:8080"


def test_it_skips_task_ips_that_do_not_answer_yet():
    """+5초에 IP 는 나오지만 uvicorn 은 +59.6초에야 듣는다 — 그 사이엔 직결도 없다."""
    r = _resolver(alive=False)
    assert asyncio.run(r.direct_url()) is None


def test_it_picks_the_first_ip_that_answers():
    adapter = _Adapter(ips=("10.0.0.1", "10.0.0.2"))
    r = _resolver(adapter, alive=lambda base: base.endswith("10.0.0.2:8080"))
    assert asyncio.run(r.direct_url()) == "http://10.0.0.2:8080"


def test_flag_off_means_no_aws_call_at_all():
    class _Off(_Settings):
        sam_direct_endpoint = "off"

    adapter = _Adapter()
    r = _resolver(adapter, settings=_Off())
    assert asyncio.run(r.direct_url()) is None
    assert adapter.lookups == 0, "off 면 ECS 를 부르지도 않아야 한다"


def test_autoscale_off_disables_it_too():
    """직결은 어댑터의 ECS 탐색에 얹혀 있다 — 어댑터가 죽어 있으면 방법이 없다."""
    adapter = _Adapter(enabled=False)
    r = _resolver(adapter)
    assert asyncio.run(r.direct_url()) is None
    assert adapter.lookups == 0


def test_an_aws_failure_is_not_an_error_for_the_caller():
    """최적화 경로다. AWS 가 삐끗하면 None 을 주고 호출자는 Service Connect 로 계속 간다."""
    r = _resolver(_Adapter(boom=True))
    assert asyncio.run(r.direct_url()) is None


# ── 캐시 ─────────────────────────────────────────────────────────────────────

def test_it_caches_the_resolved_ip():
    adapter = _Adapter()
    r = _resolver(adapter, cache_seconds=30.0, clock=lambda: 100.0)
    assert asyncio.run(r.direct_url()) == "http://10.0.0.205:8080"
    assert asyncio.run(r.direct_url()) == "http://10.0.0.205:8080"
    assert adapter.lookups == 1, "캐시가 살아 있는 동안은 ECS 를 다시 부르지 않는다"


def test_invalidate_forces_a_fresh_lookup():
    """태스크가 교체되면 캐시된 IP 는 죽는다 — 실패한 호출자가 이걸 부른다."""
    adapter = _Adapter()
    r = _resolver(adapter, clock=lambda: 100.0)
    asyncio.run(r.direct_url())
    r.invalidate()
    asyncio.run(r.direct_url())
    assert adapter.lookups == 2


def test_the_cache_expires():
    adapter = _Adapter()
    now = [100.0]
    r = _resolver(adapter, cache_seconds=30.0, clock=lambda: now[0])
    asyncio.run(r.direct_url())
    now[0] = 131.0
    asyncio.run(r.direct_url())
    assert adapter.lookups == 2


def test_ready_is_just_a_resolvable_endpoint():
    assert asyncio.run(_resolver().ready()) is True
    assert asyncio.run(_resolver(alive=False).ready()) is False


# ── 포트 ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,want", [
    ("http://sam2:8080", 8080),
    ("http://sam2:9999", 9999),
    ("http://sam2", 8080),          # 포트가 없으면 매니페스트 기본값
    (None, 8080),
    ("::not a url::", 8080),
])
def test_the_port_comes_from_the_configured_url(url, want):
    assert service_port(url) == want


# ── 클라이언트 결합 ───────────────────────────────────────────────────────────

class _Recorder:
    """httpx.AsyncClient 대역. 첫 base 는 전송 오류, 두 번째부터 200."""

    calls: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **k):
        _Recorder.calls.append(url)
        if url.startswith("http://sam2:8080"):
            raise httpx.ReadError("connection reset")     # 콜드스타트의 실제 지문
        return httpx.Response(200, json={"status": "ready", "views": {
            "Front": {"status": "ready", "cutoutKey": "k"}}},
            request=httpx.Request("POST", url))


@pytest.fixture(autouse=True)
def _clean_hooks():
    yield
    sam_client.install_endpoint_resolver(None)
    sam_client.install_prewarm_hook(None)
    _Recorder.calls = []


def test_a_transport_failure_retries_on_the_task_ip(monkeypatch):
    _Recorder.calls = []
    monkeypatch.setattr(sam_client.httpx, "AsyncClient", _Recorder)
    sam_client.install_endpoint_resolver(_resolver())

    out = asyncio.run(sam_client.segment_garment(_Settings(), {"Front": "k"}))

    assert out["Front"].ready is True
    assert _Recorder.calls == ["http://sam2:8080/segment-garment",
                               "http://10.0.0.205:8080/segment-garment"]


def test_without_a_resolver_nothing_changes(monkeypatch):
    """리졸버가 안 걸려 있으면 예전과 똑같이 한 번 시도하고 SamUnavailable."""
    _Recorder.calls = []
    monkeypatch.setattr(sam_client.httpx, "AsyncClient", _Recorder)
    sam_client.install_endpoint_resolver(None)

    with pytest.raises(sam_client.SamUnavailable) as ei:
        asyncio.run(sam_client.segment_garment(_Settings(), {"Front": "k"}))
    assert "SAM request failed: ReadError" in str(ei.value)
    assert _Recorder.calls == ["http://sam2:8080/segment-garment"]


def test_a_timeout_is_not_retried_on_the_task_ip(monkeypatch):
    """타임아웃은 sam2 가 받아서 도는 중이라는 뜻이다. 다시 걸면 90초를 두 번 쓴다."""
    calls = []

    class _Timeout(_Recorder):
        async def post(self, url, **k):
            calls.append(url)
            raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(sam_client.httpx, "AsyncClient", _Timeout)
    sam_client.install_endpoint_resolver(_resolver())

    with pytest.raises(sam_client.SamUnavailable) as ei:
        asyncio.run(sam_client.segment_garment(_Settings(), {"Front": "k"}))
    assert "timed out" in str(ei.value)
    assert calls == ["http://sam2:8080/segment-garment"]


def test_an_http_status_is_not_retried_on_the_task_ip(monkeypatch):
    """sam2 가 이미 답했다 — 주소를 바꿔도 같은 답이 온다."""
    calls = []

    class _Boom(_Recorder):
        async def post(self, url, **k):
            calls.append(url)
            return httpx.Response(500, request=httpx.Request("POST", url))

    monkeypatch.setattr(sam_client.httpx, "AsyncClient", _Boom)
    sam_client.install_endpoint_resolver(_resolver())

    with pytest.raises(sam_client.SamUnavailable) as ei:
        asyncio.run(sam_client.segment_garment(_Settings(), {"Front": "k"}))
    assert "SAM responded 500" in str(ei.value)
    assert calls == ["http://sam2:8080/segment-garment"]


def test_a_dead_cached_ip_is_forgotten(monkeypatch):
    """직결까지 실패하면 캐시를 버린다 — 태스크가 바뀐 뒤에도 죽은 IP 를 붙들면 안 된다."""
    class _AllDead(_Recorder):
        async def post(self, url, **k):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(sam_client.httpx, "AsyncClient", _AllDead)
    resolver = _resolver(clock=lambda: 100.0)
    sam_client.install_endpoint_resolver(resolver)

    with pytest.raises(sam_client.SamUnavailable):
        asyncio.run(sam_client.segment_garment(_Settings(), {"Front": "k"}))
    assert resolver._cached is None


def test_the_worn_garment_route_gets_the_same_fallback(monkeypatch):
    _Recorder.calls = []

    class _Worn(_Recorder):
        async def post(self, url, **k):
            _Recorder.calls.append(url)
            if url.startswith("http://sam2:8080"):
                raise httpx.ReadError("connection reset")
            return httpx.Response(200, json={"status": "ready", "maskKey": "m"},
                                  request=httpx.Request("POST", url))

    monkeypatch.setattr(sam_client.httpx, "AsyncClient", _Worn)
    sam_client.install_endpoint_resolver(_resolver())

    out = asyncio.run(sam_client.segment_worn_garment(
        _Settings(), source_key="s", base_key="b", clothing_type="top"))

    assert out.ready is True
    assert _Recorder.calls == ["http://sam2:8080/segment-worn-garment",
                               "http://10.0.0.205:8080/segment-worn-garment"]
