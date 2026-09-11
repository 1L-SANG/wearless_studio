"""파드 재고가 없을 때 자동 대체 — 파드 id 는 DB 가 정본이다.

2026-09-10 실측: 멈춘 파드 start 가 "not enough free GPUs on the host machine" 로 3회 실패했고,
같은 DC 에 새 파드도 "no instances currently available" 로 5회 실패했다. 파드 id 는 바뀔 수 있는
값이라 매니페스트(env)에 박아 두면 따라가지 못한다 — 그래서 DB 가 정본이고 env 는 폴백이다.
"""

import asyncio

import pytest

from app.services.face_autoscale import (
    GPU_PRIORITY,
    code_tarball_key,
    POD_DISK_GB,
    POD_TOKEN_REF,
    RunpodAutoscaleAdapter,
    RunpodTarget,
    pod_backend_url,
)
from conftest import make_settings

OLD_POD = "pod-old"
NEW_POD = "pod-new"


class _Res:
    def __init__(self, payload=None, status=200):
        self._payload, self.status_code = payload or {}, status
        self.content = b"{}"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _Client:
    """RunPod REST 대역. start 실패·생성 재고 없음을 흉내 낸다."""

    def __init__(self, *, start_fails=False, create_fails_for=(), pod_status="EXITED"):
        self.start_fails = start_fails
        self.create_fails_for = set(create_fails_for)
        self.pod_status = pod_status
        self.posts, self.creates, self.deletes, self.patches = [], [], [], []
        self.gets = []

    def patch(self, path, json=None):
        self.patches.append((path, json))
        return _Res({})

    def get(self, path):
        self.gets.append(path)
        return _Res({"desiredStatus": self.pod_status})

    def post(self, path, json=None):
        if path == "/pods":
            gpu = (json or {}).get("gpuTypeIds", [None])[0]
            self.creates.append(json)
            if gpu in self.create_fails_for:
                raise RuntimeError("no instances currently available")
            return _Res({"id": NEW_POD})
        self.posts.append(path)
        if path.endswith("/start") and self.start_fails:
            raise RuntimeError("not enough free GPUs on the host machine")
        return _Res({})

    def delete(self, path):
        self.deletes.append(path)
        return _Res({}, status=204)


class _Store:
    def __init__(self, active=None):
        self.active = active
        self.set_calls, self.retired = [], []

    async def get_active(self):
        return self.active

    async def set_active(self, pod_id, gpu_type):
        self.active = pod_id
        self.set_calls.append((pod_id, gpu_type))

    async def retire(self, pod_id):
        self.retired.append(pod_id)


#: 커밋 sha(=R2 키의 좌표)와 묶음 **내용** 해시는 다른 값이다.
CODE_SHA = "a" * 40          # 커밋 sha — 키가 된다
CONTENT_SHA = "b" * 64       # 묶음 내용 sha256 — 파드가 받은 파일을 대조하는 값
CODE_URL = "https://acct.r2.cloudflarestorage.com/wearless-face/face_render/x.tgz?sig=zzz"


def _adapter(client, store=None, *, code_urls=None, head_meta=..., **over):
    kw = {"gemini_api_key": "x", "r2_bucket": "b", "face_autoscale": "on",
          "face_runpod_api_key": "k", "face_runpod_pod_id": OLD_POD,
          "face_render_code_version": CODE_SHA}
    kw.update(over)
    provider = None
    if code_urls is not None:
        def provider(key):
            code_urls.append(key)
            return CODE_URL
    meta = {"sha256": CONTENT_SHA} if head_meta is ... else head_meta
    head = (lambda key: meta) if code_urls is not None else None
    return RunpodAutoscaleAdapter(make_settings(**kw), client=client, pod_store=store,
                                  code_url_provider=provider, code_head_provider=head)


# ── URL 유도 ──
def test_backend_url_is_derived_from_pod_id():
    assert pod_backend_url(NEW_POD) == f"https://{NEW_POD}-8000.proxy.runpod.net/render"
    assert pod_backend_url("") is None and pod_backend_url(None) is None


def test_discover_prefers_the_database_over_the_env_fallback():
    a = _adapter(_Client(), _Store(active="pod-from-db"))
    assert asyncio.run(a.discover()) == RunpodTarget("pod-from-db")
    # DB 가 비면 설정값(초기값·폴백)
    b = _adapter(_Client(), _Store(active=None))
    assert asyncio.run(b.discover()) == RunpodTarget(OLD_POD)
    # 둘 다 없으면 **빈 타깃** — 파드가 없는 건 정상 상태다(수요가 생기면 그때 만든다).
    # None 을 주면 공용 reconciler 가 ECS 의 "서비스 없음" 으로 읽고 자동 켜기를 영구 비활성한다
    # (2026-09-11 운영에서 실제로 그렇게 꺼졌다).
    c = _adapter(_Client(), _Store(active=None), face_runpod_pod_id=None)
    assert asyncio.run(c.discover()) == RunpodTarget("")


def test_health_address_comes_from_the_pod_not_the_env():
    """★ env 가 DB 를 이기면, 파드를 갈아탄 뒤에도 죽은 주소를 찔러 '안 떠 있다'가 영원히 이어진다.
    워커(face_identity.resolve_backend)도 DB 우선이라 순서를 맞춘다."""
    dead = "https://dead-pod-8000.proxy.runpod.net/render"
    a = _adapter(_Client(), _Store(active=NEW_POD), face_identity_backend_url=dead)
    asyncio.run(a.discover())
    assert a._health_url_for(NEW_POD) == f"https://{NEW_POD}-8000.proxy.runpod.net/healthz"
    # 파드가 하나도 없을 때만 env 를 쓴다
    assert a._health_url_for(None) == "https://dead-pod-8000.proxy.runpod.net/healthz"


def test_describe_uses_the_current_pod_address_each_time():
    """파드를 교체하면 **다음 describe** 가 새 주소를 찌른다(init 때 굳히지 않는다)."""
    seen = []

    class _Health:
        def get(self, url):
            seen.append(url)

            class _R:
                status_code = 200

                def json(self):
                    return {"loaded": True}
            return _R()

    client = _Client(pod_status="RUNNING")
    a = RunpodAutoscaleAdapter(
        make_settings(gemini_api_key="x", r2_bucket="b", face_autoscale="on",
                      face_runpod_api_key="k", face_runpod_pod_id=OLD_POD,
                      face_identity_backend_url="https://dead-pod-8000.proxy.runpod.net/render"),
        client=client, pod_store=_Store(active=OLD_POD), health_client=_Health())
    asyncio.run(a.describe(RunpodTarget(OLD_POD)))
    asyncio.run(a.describe(RunpodTarget(NEW_POD)))
    assert seen == [f"https://{OLD_POD}-8000.proxy.runpod.net/healthz",
                    f"https://{NEW_POD}-8000.proxy.runpod.net/healthz"]


def test_no_pod_anywhere_goes_to_create():
    """DB 행도 env 파드 id 도 없으면 빈 타깃 — describe 는 0/0 이고 set_desired(1) 이 만든다."""
    client = _Client()
    store = _Store(active=None)
    a = _adapter(client, store, face_runpod_pod_id=None, code_urls=[])
    target = asyncio.run(a.discover())
    assert target == RunpodTarget("")
    state = asyncio.run(a.describe(target))
    assert (state.desired, state.running, state.pending) == (0, 0, 0)
    assert client.gets == []          # 없는 파드를 조회하지 않는다
    a.begin_cycle()
    asyncio.run(a.set_desired(target, 1))
    assert store.set_calls == [(NEW_POD, GPU_PRIORITY[0][0])]
    assert client.posts == []


# ── 재고 없음 → 생성 ──
def test_start_failure_creates_a_new_pod_and_records_it():
    client = _Client(start_fails=True)
    store = _Store(active=OLD_POD)
    a = _adapter(client, store)
    a.begin_cycle()
    asyncio.run(a.set_desired(RunpodTarget(OLD_POD), 1))
    assert client.posts == [f"/pods/{OLD_POD}/start"]          # 먼저 start 를 시도했다
    assert len(client.creates) == 1
    assert store.set_calls == [(NEW_POD, GPU_PRIORITY[0][0])]   # 1순위 카드
    assert client.deletes == [f"/pods/{OLD_POD}"]               # 이전 파드는 지운다
    assert store.retired == [OLD_POD]
    assert a._target == RunpodTarget(NEW_POD)


def test_create_walks_the_card_priority():
    client = _Client(start_fails=True, create_fails_for=[GPU_PRIORITY[0][0]])
    store = _Store(active=OLD_POD)
    a = _adapter(client, store)
    a.begin_cycle()
    asyncio.run(a.set_desired(RunpodTarget(OLD_POD), 1))
    assert [c["gpuTypeIds"][0] for c in client.creates] == [GPU_PRIORITY[0][0], GPU_PRIORITY[1][0]]
    assert store.set_calls == [(NEW_POD, GPU_PRIORITY[1][0])]


def test_all_cards_out_of_stock_raises_for_the_reconciler_to_alert():
    client = _Client(start_fails=True, create_fails_for=[g for g, _ in GPU_PRIORITY])
    a = _adapter(client, _Store(active=OLD_POD))
    a.begin_cycle()
    with pytest.raises(RuntimeError) as exc:
        asyncio.run(a.set_desired(RunpodTarget(OLD_POD), 1))
    assert "파드 생성 실패" in str(exc.value)
    assert len(client.creates) == len(GPU_PRIORITY)


def test_only_one_create_per_cycle():
    client = _Client(start_fails=True, create_fails_for=[g for g, _ in GPU_PRIORITY])
    a = _adapter(client, _Store(active=OLD_POD))
    a.begin_cycle()
    with pytest.raises(RuntimeError):
        asyncio.run(a.set_desired(RunpodTarget(OLD_POD), 1))
    with pytest.raises(RuntimeError) as second:
        asyncio.run(a.set_desired(RunpodTarget(OLD_POD), 1))
    assert "already attempted this cycle" in str(second.value)
    assert len(client.creates) == len(GPU_PRIORITY)      # 두 번째 호출은 만들지 않았다
    a.begin_cycle()                                       # 다음 주기에는 다시 시도한다
    with pytest.raises(RuntimeError):
        asyncio.run(a.set_desired(RunpodTarget(OLD_POD), 1))
    assert len(client.creates) == 2 * len(GPU_PRIORITY)


def test_missing_pod_creates_one_without_a_start_attempt():
    client = _Client()
    store = _Store(active=None)
    a = _adapter(client, store, face_runpod_pod_id=None)
    a.begin_cycle()
    asyncio.run(a.set_desired(None, 1))
    assert client.posts == []                 # 켤 파드가 없으니 start 시도 자체가 없다
    assert store.set_calls == [(NEW_POD, GPU_PRIORITY[0][0])]
    assert client.deletes == []               # 지울 이전 파드도 없다


def test_new_pod_spec_carries_bootstrap_and_secret_token():
    client = _Client(start_fails=True)
    a = _adapter(client, _Store(active=OLD_POD))
    a.begin_cycle()
    asyncio.run(a.set_desired(RunpodTarget(OLD_POD), 1))
    body = client.creates[0]
    assert body["containerDiskInGb"] == POD_DISK_GB        # 볼륨 없음 — 가중치가 컨테이너 디스크에 온다
    assert set(body["ports"]) == {"8000/http", "22/tcp"}
    assert "pre_start.sh" in body["dockerStartCmd"]        # bootstrap → start 를 잇는 훅
    assert body["env"] == {"FACE_RENDER_TOKEN": POD_TOKEN_REF}
    assert "RUNPOD_SECRET" in POD_TOKEN_REF                # 토큰 값이 아니라 Secret 참조다
    assert "networkVolumeId" not in body


# ── 종료는 stop(terminate 아님) ──
def test_scale_to_zero_stops_but_does_not_terminate():
    client = _Client()
    a = _adapter(client, _Store(active=OLD_POD))
    asyncio.run(a.set_desired(RunpodTarget(OLD_POD), 0))
    assert client.posts == [f"/pods/{OLD_POD}/stop"]
    assert client.deletes == []      # 같은 호스트에 자리가 남아 있으면 stop→start 가 제일 빠르다


def test_gpu_priority_excludes_cards_that_cannot_hold_the_model():
    """A40(48GB)에서는 모델이 아예 안 올라간다(2026-09-10 실측) — 목록에 들어오면 안 된다."""
    names = [g for g, _ in GPU_PRIORITY]
    assert names[0].startswith("NVIDIA RTX PRO 6000")
    assert not any("A40" in n or "L4" in n or "4090" in n for n in names)


# ── 코드 묶음 전달(파드에 R2 자격증명 없음) ──
def test_create_carries_a_fresh_code_url_and_sha():
    urls = []
    client = _Client(start_fails=True)
    a = _adapter(client, _Store(active=OLD_POD), code_urls=urls)
    a.begin_cycle()
    asyncio.run(a.set_desired(RunpodTarget(OLD_POD), 1))
    body_env = client.creates[0]["env"]
    assert body_env["CODE_TARBALL_URL"] == CODE_URL
    # 키는 커밋 sha, 검증값은 **묶음 내용 해시**다. 하나로 같이 쓰면 bootstrap 의 sha256 대조가
    # 항상 틀려 파드가 매번 exit 78 로 죽는다(2026-09-11 운영에서 그랬다).
    assert body_env["CODE_SHA256"] == CONTENT_SHA
    assert urls == [code_tarball_key(CODE_SHA)] * 2     # start 직전 + create 직전


def test_start_refreshes_the_code_url_before_booting():
    """이전에 넣어 둔 URL 은 이미 만료됐을 수 있다 — 켜기 직전에 새로 넣는다."""
    urls = []
    client = _Client()
    a = _adapter(client, _Store(active=OLD_POD), code_urls=urls)
    a.begin_cycle()
    asyncio.run(a.set_desired(RunpodTarget(OLD_POD), 1))
    assert client.patches and client.patches[0][0] == f"/pods/{OLD_POD}"
    assert client.patches[0][1]["env"]["CODE_TARBALL_URL"] == CODE_URL
    assert client.posts == [f"/pods/{OLD_POD}/start"]
    assert urls == [code_tarball_key(CODE_SHA)]


def test_stop_never_mints_a_code_url():
    urls = []
    client = _Client()
    a = _adapter(client, _Store(active=OLD_POD), code_urls=urls)
    asyncio.run(a.set_desired(RunpodTarget(OLD_POD), 0))
    assert urls == [] and client.patches == []


def test_missing_code_provider_still_creates_but_says_so(caplog):
    client = _Client(start_fails=True)
    a = _adapter(client, _Store(active=OLD_POD))          # provider 없음
    a.begin_cycle()
    asyncio.run(a.set_desired(RunpodTarget(OLD_POD), 1))
    env = client.creates[0]["env"]
    assert "CODE_TARBALL_URL" not in env                   # 없는 값을 지어내지 않는다
    assert env["FACE_RENDER_TOKEN"] == POD_TOKEN_REF


def test_presigned_url_never_reaches_the_log(caplog):
    import logging

    urls = []
    client = _Client(start_fails=True)
    a = _adapter(client, _Store(active=OLD_POD), code_urls=urls)
    a.begin_cycle()
    with caplog.at_level(logging.DEBUG):
        asyncio.run(a.set_desired(RunpodTarget(OLD_POD), 1))
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert CODE_URL not in joined and "sig=zzz" not in joined


def test_card_priority_puts_h100_before_a100():
    """2026-09-11 재고 실측: PRO 6000·A100 둘 다 "no instances", H100 만 잡혔다."""
    names = [g for g, _ in GPU_PRIORITY]
    assert names.index("NVIDIA H100 80GB HBM3") < names.index("NVIDIA A100 80GB PCIe")


# ── "파드 아직 없음" 은 장애가 아니다 ──
def test_missing_pod_does_not_disable_the_reconciler():
    """★ 2026-09-11 운영: discover 가 None 을 주자 공용 reconciler 가 ECS 의 "서비스 없음" 으로
    읽고 자동 켜기를 영구 비활성했다(파드 행 0개 상태) → 얼굴 패스가 통째로 죽었다."""
    import types

    from app.services.sam_autoscale import DemandSnapshot
    from app.workers.sam_autoscaler import SamAutoscaler
    from conftest import make_settings as _ms

    class _Repo:
        async def try_advisory_lock(self, conn, key):
            return True

    async def _snap(repo, conn):
        return DemandSnapshot(0, None, None)          # 수요 없음

    client = _Client()
    adapter = _adapter(client, _Store(active=None), face_runpod_pod_id=None, code_urls=[])
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=_ms(gemini_api_key="x", r2_bucket="b", face_autoscale="on",
                     face_autoscale_start_grace_minutes=8)))
    scaler = SamAutoscaler(app, adapter, demand_fn=_snap,
                           idle_attr="face_autoscale_idle_minutes", name="face-render",
                           lock_key="face_autoscaler",
                           start_grace_attr="face_autoscale_start_grace_minutes")
    alerts = []
    adapter.notify = lambda subject, body: alerts.append(subject) or _done()

    async def _done():
        return None

    assert asyncio.run(scaler.reconcile_once(_Repo(), None)) == "noop"
    assert scaler._disabled_reason is None        # 비활성되지 않는다
    assert alerts == []                            # 알림도 없다
    assert client.creates == []                    # 수요가 없으니 만들지도 않는다


def test_missing_pod_with_demand_creates_one():
    import types

    from app.services.sam_autoscale import DemandSnapshot
    from app.workers.sam_autoscaler import SamAutoscaler
    from conftest import make_settings as _ms

    class _Repo:
        async def try_advisory_lock(self, conn, key):
            return True

    async def _snap(repo, conn):
        return DemandSnapshot(2, None, None)          # 대기 중인 얼굴 컷 2개

    client = _Client()
    store = _Store(active=None)
    adapter = _adapter(client, store, face_runpod_pod_id=None, code_urls=[])
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=_ms(gemini_api_key="x", r2_bucket="b", face_autoscale="on")))
    scaler = SamAutoscaler(app, adapter, demand_fn=_snap,
                           idle_attr="face_autoscale_idle_minutes", name="face-render",
                           lock_key="face_autoscaler")
    assert asyncio.run(scaler.reconcile_once(_Repo(), None)) == "up"
    assert store.set_calls == [(NEW_POD, GPU_PRIORITY[0][0])]


def test_a_real_config_error_still_disables():
    """RUNPOD_API_KEY 가 없는 건 진짜 설정 오류다 — 그건 여전히 None 이고 비활성 대상."""
    a = _adapter(_Client(), _Store(active=None), face_runpod_api_key=None,
                 face_runpod_pod_id=None)
    assert asyncio.run(a.discover()) is None


def test_code_key_is_the_commit_sha_and_verification_is_the_content_hash():
    urls = []
    client = _Client(start_fails=True)
    a = _adapter(client, _Store(active=OLD_POD), code_urls=urls)
    a.begin_cycle()
    asyncio.run(a.set_desired(RunpodTarget(OLD_POD), 1))
    assert urls[0] == f"face_render/{CODE_SHA}.tgz"        # 좌표 = 커밋 sha
    assert client.creates[0]["env"]["CODE_SHA256"] == CONTENT_SHA   # 검증값 = 내용 해시
    assert CODE_SHA != CONTENT_SHA


def test_missing_bundle_metadata_sends_no_code_env_and_alerts(caplog):
    import logging

    urls = []
    client = _Client(start_fails=True)
    a = _adapter(client, _Store(active=OLD_POD), code_urls=urls, head_meta=None)
    a.begin_cycle()
    with caplog.at_level(logging.CRITICAL):
        asyncio.run(a.set_desired(RunpodTarget(OLD_POD), 1))
    env = client.creates[0]["env"]
    assert "CODE_TARBALL_URL" not in env and "CODE_SHA256" not in env   # 지어내지 않는다
    assert urls == []                                                   # URL 도 안 만든다
    criticals = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert criticals and "코드 묶음" in criticals[0].getMessage()


def test_code_alert_is_debounced(caplog):
    import logging

    client = _Client(start_fails=True)
    a = _adapter(client, _Store(active=OLD_POD), code_urls=[], head_meta={})
    with caplog.at_level(logging.CRITICAL):
        a.begin_cycle()
        asyncio.run(a.set_desired(RunpodTarget(OLD_POD), 1))
        a.begin_cycle()
        asyncio.run(a.set_desired(RunpodTarget(OLD_POD), 1))
    assert len([r for r in caplog.records if r.levelno == logging.CRITICAL]) == 1
