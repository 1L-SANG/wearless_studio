"""각도 교체(ComfyUI) 파드의 온디맨드 기동 — 얼굴 파드와 같은 어댑터, 다른 프로필.

여기서 지키는 계약 셋:
  ① 파드 주소·준비 판정·코드 묶음 키가 얼굴 파드와 **다르다**(섞이면 각도 컷이 얼굴 파드로 간다).
  ② 수요는 옆·뒤를 만드는 잡만 센다(정면 컷이 시간당 $2 짜리 GPU 를 켜면 안 된다).
  ③ 워커가 보는 주소와 어댑터가 찌르는 주소가 **같은 함수**에서 나온다.
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.agents import face_angle_swap
from app.services import angle_autoscale as angle
from app.services import face_autoscale as face


def settings(**overrides):
    base = dict(
        angle_autoscale="on",
        angle_runpod_pod_id=None,
        face_runpod_api_key="k",
        face_render_code_version="abc123",
        face_angle_backend_url=None,
        face_angle_backend_token="t",
        face_angle_swap_enabled=True,
        face_angle_seed=42,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ── ① 프로필이 얼굴 파드와 갈린다 ──────────────────────────────────────────────

def test_angle_pod_url_has_no_render_suffix():
    """ComfyUI 는 /prompt·/upload/image 를 직접 받는다 — 얼굴 쪽 /render 를 붙이면 404 다."""
    assert angle.angle_backend_url("pod9") == "https://pod9-8000.proxy.runpod.net"
    assert face.pod_backend_url("pod9").endswith("/render")
    assert angle.angle_backend_url(None) is None and angle.angle_backend_url("  ") is None


def test_worker_and_adapter_derive_the_same_address():
    """계약 ③ — 갈리면 어댑터는 '떴다'고 보고 워커는 죽은 주소를 찌른다."""
    assert face_angle_swap.pod_backend_url("pod9") == angle.angle_backend_url("pod9")


def test_ready_means_comfyui_answered_not_just_the_port_being_open():
    # 설치가 10분 도는 동안 프록시는 200 을 주지만 ComfyUI 는 아직 없다.
    assert angle.angle_ready({"comfy": True, "stages": ["all_done"]}) is True
    assert angle.angle_ready({"comfy": False, "stages": ["venv_ok", "comfy_cloned"]}) is False
    assert angle.angle_ready({}) is False and angle.angle_ready(None) is False


def test_health_url_hangs_healthz_off_the_pod_address():
    assert angle.angle_health_url("https://p-8000.proxy.runpod.net/") == \
        "https://p-8000.proxy.runpod.net/healthz"
    assert angle.angle_health_url(None) is None and angle.angle_health_url("") is None


def test_profile_points_at_its_own_bundle_and_table():
    assert angle.ANGLE_PROFILE.code_key_fmt.format(sha="s") == "comfy_angle/s.tgz"
    assert face.FACE_PROFILE.code_key_fmt.format(sha="s") == "face_render/s.tgz"
    assert angle.ANGLE_PROFILE.pod_table == "fm_angle_render_pod"
    assert angle.ANGLE_PROFILE.pod_table != face.FACE_PROFILE.pod_table
    # 파드 이미지·부팅 스크립트·토큰 주입은 같다 — 다른 건 번들뿐이다.
    assert angle.ANGLE_PROFILE.token_env == face.FACE_PROFILE.token_env


# ── ② 어댑터가 프로필대로 움직인다 ────────────────────────────────────────────

class FakeHTTP:
    """RunPod REST 대역. 만든 파드 본문을 그대로 보관한다."""

    def __init__(self):
        self.created: list[dict] = []
        self.posts: list[str] = []

    def get(self, path):
        return SimpleNamespace(status_code=200, content=b"{}",
                               json=lambda: {"desiredStatus": "RUNNING"},
                               raise_for_status=lambda: None)

    def post(self, path, json=None):
        self.posts.append(path)
        if path == "/pods":
            self.created.append(json)
            return SimpleNamespace(status_code=200, content=b"{}",
                                   json=lambda: {"id": "newpod"},
                                   raise_for_status=lambda: None)
        return SimpleNamespace(status_code=200, content=b"", raise_for_status=lambda: None)

    def patch(self, path, json=None):
        return SimpleNamespace(status_code=200, content=b"", raise_for_status=lambda: None)


def adapter(http, **overrides):
    return face.RunpodAutoscaleAdapter(
        settings(**overrides), profile=angle.ANGLE_PROFILE, client=http,
        code_url_provider=lambda key: f"https://x.r2.cloudflarestorage.com/{key}",
        code_head_provider=lambda key: {"sha256": "deadbeef"})


def test_created_pod_carries_the_comfy_bundle_and_its_own_name():
    http = FakeHTTP()
    a = adapter(http)
    asyncio.run(a.set_desired(face.RunpodTarget(""), 1))
    body = http.created[0]
    assert body["name"] == "comfy-angle"
    assert body["env"]["CODE_TARBALL_URL"].endswith("comfy_angle/abc123.tgz")
    assert body["env"]["CODE_SHA256"] == "deadbeef"
    # 토큰은 값이 아니라 RunPod Secret 참조여야 한다 — 평문이 API 요청에 실리면 안 된다.
    assert body["env"]["FACE_RENDER_TOKEN"] == face.POD_TOKEN_REF
    # 파드 이미지·부팅 스크립트는 얼굴 파드와 같은 것을 쓴다(번들만 다르다).
    assert body["imageName"] == face.POD_IMAGE and body["dockerStartCmd"] == list(face.POD_ARGS)


def test_switch_is_its_own_setting():
    assert adapter(FakeHTTP()).enabled is True
    assert adapter(FakeHTTP(), angle_autoscale="off").enabled is False


def test_health_is_probed_at_the_pod_not_the_configured_fallback():
    a = adapter(FakeHTTP(), face_angle_backend_url="https://stale.example")
    assert a._health_url_for("live") == "https://live-8000.proxy.runpod.net/healthz"
    # 파드가 아직 없을 때만 설정값으로 떨어진다.
    assert a._health_url_for(None) == "https://stale.example/healthz"


# ── ③ 수요 판정 ───────────────────────────────────────────────────────────────

class FakeCursor:
    def __init__(self, rows):
        self._rows, self.sql, self.params = rows, [], []

    async def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))
        self.params.append(params)

    async def fetchone(self):
        return self._rows.pop(0) if self._rows else {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


class FakeConn:
    def __init__(self, rows):
        self.cur = FakeCursor(rows)

    def cursor(self):
        return self.cur


def test_demand_counts_only_side_and_back_work():
    conn = FakeConn([{"t": "jobs"},
                     {"active_angle_jobs": 3, "last_angle_finished_at": None}])
    snap = asyncio.run(angle.angle_demand_snapshot(conn))
    assert snap.active_sam_jobs == 3
    sql = conn.cur.sql[-1]
    # 정면 컷 하나가 GPU 를 켜지 않게 — 방향 조건이 SQL 에 있어야 한다.
    assert "payload ->> 'direction' = any(%s)" in sql
    assert conn.cur.params[-1][1] == ["side", "back"]
    # 등록자(켜진 LoRA)의 잡만 — 가상모델 잡은 각도 교체를 안 쓴다.
    assert "fm_model_loras" in sql and "enabled" in sql


def test_demand_is_zero_before_the_lora_migration_lands():
    conn = FakeConn([{"t": None}])
    snap = asyncio.run(angle.angle_demand_snapshot(conn))
    assert (snap.active_sam_jobs, snap.last_sam_finished_at) == (0, None)


def test_no_warm_ping_for_the_angle_pod():
    """모델을 고른 순간에는 옆·뒤를 만들지 알 수 없다 — 미리 켜면 그냥 요금이다."""
    conn = FakeConn([{"t": "jobs"}, {"active_angle_jobs": 0, "last_angle_finished_at": None}])
    assert asyncio.run(angle.angle_demand_snapshot(conn)).last_upload_at is None


# ── 저장소 ────────────────────────────────────────────────────────────────────

def test_pod_store_refuses_a_table_it_does_not_know():
    face.FaceRenderPodStore(None, "fm_angle_render_pod")      # 통과해야 한다
    with pytest.raises(ValueError):
        face.FaceRenderPodStore(None, "jobs; drop table users")


# ── 워커가 파드 주소를 어떻게 고르는가 ────────────────────────────────────────

def test_spec_prefers_the_live_pod_over_the_configured_address():
    photos = face_angle_swap.AnglePhotos(back=b"b")
    spec = face_angle_swap.spec_from(
        settings(face_angle_backend_url="https://stale.example"), photos, pod_id="live")
    assert spec.backend.base == "https://live-8000.proxy.runpod.net"


def test_spec_falls_back_to_the_configured_address_when_no_pod_exists():
    photos = face_angle_swap.AnglePhotos(back=b"b")
    spec = face_angle_swap.spec_from(
        settings(face_angle_backend_url="https://fallback.example"), photos, pod_id=None)
    assert spec.backend.base == "https://fallback.example"
    # 파드도 설정값도 없으면 이 경로를 아예 안 탄다.
    assert face_angle_swap.spec_from(settings(), photos, pod_id=None) is None


# ── QA 창구: 상태 라우트의 각도 블록 ──────────────────────────────────────────
#
# 배포 뒤 "옆·뒤 컷이 지금 되는가"를 한 번의 호출로 읽는 자리다. 셋이 모두 맞아야 된다:
# 플래그 · 등록자 각도 사진 · ComfyUI 파드. 하나라도 빠지면 어디가 빠졌는지 보여야 한다.

def angle_status(monkeypatch, *, flag=True, photos=None, pod=None, healthy=True,
                 autoscale=True, enrollment="e1", model_id="11111111-1111-1111-1111-111111111111"):
    import contextlib
    import types

    from app import facemarket
    from app.agents import identity_source

    class Cur:
        def __init__(self):
            self.params = []

        async def execute(self, sql, params=None):
            self.params.append(params)

        async def fetchone(self):
            return {"e": enrollment}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    class Conn:
        def cursor(self):
            return Cur()

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield Conn()

    async def fake_photos(_conn, _enrollment_id):
        return photos if photos is not None else {}

    async def fake_pod(_pool):
        return pod

    monkeypatch.setattr(facemarket, "get_conn", fake_conn)
    monkeypatch.setattr(identity_source, "resolve_angle_photos", fake_photos)
    monkeypatch.setattr(identity_source, "active_angle_pod_id", fake_pod)
    monkeypatch.setattr(facemarket, "_probe_angle_pod", lambda url, ready: bool(url) and healthy)
    request = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(
        settings=SimpleNamespace(face_angle_swap_enabled=flag, face_angle_backend_url=None),
        pool=object(),
        angle_autoscaler=types.SimpleNamespace(
            adapter=types.SimpleNamespace(enabled=autoscale)))))
    return asyncio.run(facemarket._angle_status(request, model_id))


def test_qa_sees_ready_only_when_photos_and_pod_are_both_there(monkeypatch):
    body = angle_status(monkeypatch, photos={"sh_side": "k1", "sh_back": "k2"}, pod="p1")
    assert body == {"enabled": True, "ready": True, "state": "ready",
                    "slots": ["sh_back", "sh_side"]}


def test_qa_sees_which_slots_the_registrant_actually_has(monkeypatch):
    """'오른쪽 옆모습만 안 된다'를 바로 읽는 자리 — 옛 등록(v1·v2)은 칸이 비어 있다."""
    body = angle_status(monkeypatch, photos={"sh_side": "k1"}, pod="p1")
    assert body["slots"] == ["sh_side"]


def test_no_angle_photos_reads_as_offline_not_starting(monkeypatch):
    """사진이 없으면 파드를 아무리 띄워도 안 된다 — '준비 중'이 영원히 뜨면 안 된다."""
    assert angle_status(monkeypatch, photos={}, pod="p1") == {
        "enabled": False, "ready": False, "state": "offline", "slots": []}


def test_flag_off_reads_as_offline(monkeypatch):
    assert angle_status(monkeypatch, flag=False, photos={"sh_back": "k"}, pod="p1")["state"] \
        == "offline"


def test_pod_not_up_yet_reads_as_starting_when_autoscale_will_make_one(monkeypatch):
    body = angle_status(monkeypatch, photos={"sh_back": "k"}, pod=None, autoscale=True)
    assert body == {"enabled": True, "ready": False, "state": "starting", "slots": ["sh_back"]}


def test_nobody_will_make_a_pod_reads_as_offline(monkeypatch):
    body = angle_status(monkeypatch, photos={"sh_back": "k"}, pod=None, autoscale=False)
    assert body["state"] == "offline" and body["ready"] is False


def test_pod_exists_but_comfyui_has_not_finished_installing(monkeypatch):
    """설치 10분 동안 프록시는 200 을 주지만 comfy 는 false 다 — 그동안은 starting."""
    body = angle_status(monkeypatch, photos={"sh_back": "k"}, pod="p1", healthy=False)
    assert body["state"] == "starting" and body["ready"] is False
