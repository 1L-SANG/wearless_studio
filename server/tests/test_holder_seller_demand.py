"""holder(opendid) scale-to-zero 가 셀러 사용을 수요로 못 봐서 운영이 막힌 건의 회귀.

2026-09-14 운영 실측: 04:10:29Z "opendid autoscale down → desired=0" → 04:17:10Z
"holder_vc_verify_unreachable". 그 사이 셀러는 verified 모델·active 라이선스로도
"라이선스 자격 증명 확인 서비스를 사용할 수 없습니다"(503) 를 받았다.

원인: 수요가 **등록 쪽 신호만** 세고 있었다(license_pending·vc_pending·폐기 잡·최근 등록 활동).
셀러가 실존 모델로 컷을 만드는 것은 매번 `/holder/vc/verify` 호출인데 그게 수요가 아니었다.
그래서 마지막 등록으로부터 30분이 지나면 holder 가 0대로 내려갔고, 손으로 desired=1 을 써도
reconciler 가 60초 안에 되돌렸다.
"""

import asyncio
import contextlib
import types
from datetime import datetime, timedelta, timezone

import pytest

from app import facemarket
from app import repo as repo_mod
from app.services import sam_autoscale

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


# ── 가짜 커서: 쿼리 순서대로 행을 돌려준다 ──────────────────────────────────
class _Cur:
    def __init__(self, rows):
        self._rows = list(rows)
        self.sql = []
        self.params = []

    async def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))
        self.params.append(params)

    async def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, rows):
        self.cur = _Cur(rows)
        self.commits = 0

    def cursor(self):
        return self.cur

    async def commit(self):
        self.commits += 1


# ── (1) 수요 스냅샷 ──────────────────────────────────────────────────────────
def _demand(*, active=0, last_finished=None, last_activity=None, has_pings=True):
    conn = _Conn([
        {"t": "fm_holder_warm_pings" if has_pings else None},
        {"active": active, "last_finished": last_finished, "last_activity": last_activity},
    ])
    return asyncio.run(repo_mod.opendid_demand_snapshot(conn)), conn


def test_a_running_real_model_job_is_demand():
    """실존 모델 컷 잡 하나하나가 holder 호출이다 — 도는 동안 0대로 내려가면 그 잡이 죽는다."""
    snap, conn = _demand(active=1)
    assert snap.active_sam_jobs == 1
    assert sam_autoscale.want_running(snap, idle_minutes=30, now=NOW) is True
    sql = " ".join(conn.cur.sql)
    assert "from jobs where kind = any(%s)" in sql
    assert "payload -> '_facemarket' ->> 'modelId'" in sql
    assert conn.cur.params[-1][1] == list(repo_mod.HOLDER_JOB_KINDS)


def test_a_recently_finished_real_model_job_keeps_it_up():
    """방금 끝난 컷 = 셀러가 지금 에디터에 있다. 다음 컷도 곧 온다."""
    snap, _ = _demand(last_finished=NOW - timedelta(minutes=5))
    assert sam_autoscale.want_running(snap, idle_minutes=30, now=NOW) is True


def test_a_seller_model_pick_keeps_it_up():
    """모델을 고른 순간 = 아직 잡은 없지만 곧 라이선스 확인이 온다(콜드스타트 선흡수)."""
    snap, _ = _demand(last_activity=NOW - timedelta(minutes=3))
    assert sam_autoscale.want_running(snap, idle_minutes=30, now=NOW) is True


def test_nothing_recent_lets_it_scale_to_zero():
    """수요가 정말 없으면 0대로 내려간다 — 이 PR 은 '항상 켜 둔다'가 아니다."""
    snap, _ = _demand(last_finished=NOW - timedelta(hours=3),
                      last_activity=NOW - timedelta(hours=3))
    assert sam_autoscale.want_running(snap, idle_minutes=30, now=NOW) is False


def test_the_ping_table_is_optional():
    """마이그 미적용 환경에서도 스냅샷이 죽지 않는다(기존 신호만으로 판단)."""
    snap, conn = _demand(has_pings=False)
    assert snap.last_upload_at is None
    assert "fm_holder_warm_pings" not in " ".join(conn.cur.sql[1:])


def test_the_enrollment_signals_are_still_there():
    """셀러 신호를 더하면서 등록 쪽 신호를 잃으면 발급 앞 콜드스타트가 되돌아온다."""
    _, conn = _demand()
    sql = " ".join(conn.cur.sql)
    for token in ("license_pending", "vc_pending", "fm_vc_revocation_jobs",
                  "liveness_pending", "asset_building"):
        assert token in sql, token


# ── (2) /face-render/warm — 신호 둘, 대상 다름 ───────────────────────────────
def _warm_request(monkeypatch, conn, *, body):
    woken = []
    monkeypatch.setattr(facemarket, "_wake_opendid", lambda app: woken.append(app))

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield conn

    monkeypatch.setattr(facemarket, "get_conn", fake_conn)
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=types.SimpleNamespace(facemarket_enabled=True)))

    async def json_body():
        return body

    request = types.SimpleNamespace(app=app, json=json_body)
    response = types.SimpleNamespace(headers={})
    status = asyncio.run(facemarket.warm_face_render(request, response, user_id=USER)).status_code
    return status, woken


USER = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
MODEL = "ca5d2abd-5a70-47c1-973b-10573cf60680"


def test_a_real_model_without_a_lora_still_wakes_the_holder(monkeypatch):
    """holder 는 LoRA 와 무관하다 — 이 경우가 빠져 있어서 운영이 막혔다.

    얼굴 파드 쪽(fm_face_warm_pings)은 **기록하지 않는다**: LoRA 가 없으면 얼굴 패스가 안 걸려
    GPU 를 켤 이유가 없다(그 동작은 이 PR 에서 바꾸지 않는다).
    """
    conn = _Conn([
        {"x": 1},                      # fm_models verified
        {"t": "fm_holder_warm_pings"},
        None,                          # 창 안 중복 없음 → insert
        {"t": "fm_face_warm_pings"},
        None,                          # 켜진 LoRA 없음 → 얼굴 핑 없이 종료
    ])
    status, woken = _warm_request(monkeypatch, conn, body={"modelId": MODEL})
    assert status == 204 and len(woken) == 1
    sql = " ".join(conn.cur.sql)
    assert "insert into fm_holder_warm_pings" in sql
    assert "insert into fm_face_warm_pings" not in sql
    assert conn.commits == 1


def test_a_real_model_with_a_lora_records_both(monkeypatch):
    conn = _Conn([
        {"x": 1},                      # verified
        {"t": "fm_holder_warm_pings"},
        None,                          # holder 핑 창 안 중복 없음 → insert
        {"t": "fm_face_warm_pings"},
        {"x": 1},                      # 켜진 LoRA 있음
        None,                          # 얼굴 핑 창 안 중복 없음 → insert
    ])
    status, woken = _warm_request(monkeypatch, conn, body={"modelId": MODEL})
    assert status == 204 and len(woken) == 1
    sql = " ".join(conn.cur.sql)
    assert "insert into fm_holder_warm_pings" in sql
    assert "insert into fm_face_warm_pings" in sql


def test_a_virtual_model_records_neither(monkeypatch):
    """가상 모델은 라이선스도 얼굴 패스도 없다 — 아무것도 켜지 않는다."""
    conn = _Conn([])
    status, woken = _warm_request(monkeypatch, conn, body={"modelId": "virtual-a"})
    assert status == 204 and woken == [] and conn.cur.sql == []


def test_an_unverified_real_model_does_not_wake_the_holder(monkeypatch):
    """등록이 안 끝난 모델은 라이선스가 없다 — 켤 이유가 없다."""
    conn = _Conn([
        None,                          # verified 아님
        {"t": "fm_face_warm_pings"},
        None,                          # LoRA 없음
    ])
    status, woken = _warm_request(monkeypatch, conn, body={"modelId": MODEL})
    assert status == 204 and woken == []
    assert "insert into fm_holder_warm_pings" not in " ".join(conn.cur.sql)


# ── (3) verify_license — 못 닿으면 깨우고 "켜는 중" ──────────────────────────
def _app(*, required=True, autoscale="on"):
    return types.SimpleNamespace(state=types.SimpleNamespace(settings=types.SimpleNamespace(
        fm_vc_required=required,
        opendid_autoscale=autoscale,
        opendid_holder_url="http://holder.internal",
        opendid_holder_hmac_secret="s",
    )))


LICENSE = {"status": "active", "license_valid_until": None, "vc_id": "vc-1",
           "allowed_use": ["일반 의류"], "forbidden_use": []}


def _verify(monkeypatch, *, post):
    app = _app()
    woken = []
    monkeypatch.setattr(facemarket, "_wake_opendid", lambda a: woken.append(a))
    monkeypatch.setattr(facemarket, "verify_license_local", lambda *a, **kw: None)
    monkeypatch.setattr(facemarket.holder_client, "post", post)
    with pytest.raises(facemarket.HTTPException) as caught:
        asyncio.run(facemarket.verify_license(
            app, dict(LICENSE), model_id=MODEL, brand_use_category="일반 의류"))
    return caught.value, woken


def test_an_unreachable_holder_is_woken_and_reported_as_starting(monkeypatch):
    async def boom(*a, **kw):
        raise OSError("connection refused")

    error, woken = _verify(monkeypatch, post=boom)
    assert error.status_code == 503
    assert error.detail["code"] == "holder_starting"
    assert "1~2분" in error.detail["message"]
    assert len(woken) == 1, "못 닿았으면 깨워야 한다 — 안 깨우면 영원히 0대다"


@pytest.mark.parametrize("status", [502, 503, 504])
def test_a_booting_holder_behind_the_load_balancer_is_starting_too(monkeypatch, status):
    async def gateway(*a, **kw):
        return types.SimpleNamespace(status_code=status)

    error, woken = _verify(monkeypatch, post=gateway)
    assert error.detail["code"] == "holder_starting" and len(woken) == 1


def test_a_live_holder_that_refuses_the_vc_still_blocks(monkeypatch):
    """**VC 검증을 건너뛰는 폴백은 없다.** 홀더가 '무효'라고 하면 지금처럼 409 다."""
    async def refuses(*a, **kw):
        return types.SimpleNamespace(
            status_code=200, json=lambda: {"verified": False, "status": "revoked"})

    error, woken = _verify(monkeypatch, post=refuses)
    assert error.status_code == 409 and error.detail["code"] == "license_unverified"
    assert woken == [], "정상 응답인데 깨울 이유가 없다"


# ── (4) 워커 대기 ────────────────────────────────────────────────────────────
def _wait(monkeypatch, *, ready_after, app=None, max_wait=30.0):
    app = app or _app()
    calls = {"health": 0, "wake": 0, "slept": []}
    clock = {"t": 0.0}

    async def ready(_app, **kw):
        calls["health"] += 1
        return calls["health"] > ready_after

    async def sleep(seconds):
        calls["slept"].append(seconds)
        clock["t"] += seconds

    monkeypatch.setattr(facemarket, "holder_ready", ready)
    monkeypatch.setattr(facemarket, "_wake_opendid", lambda a: calls.__setitem__("wake", calls["wake"] + 1))
    got = asyncio.run(facemarket.wait_for_holder(
        app, max_wait_seconds=max_wait, poll_seconds=5.0,
        sleep=sleep, monotonic=lambda: clock["t"]))
    return got, calls


def test_the_worker_waits_through_the_cold_start(monkeypatch):
    """콜드스타트 ~2분 — 여기서 기다려 주지 않으면 셀러의 잡이 그냥 실패한다."""
    got, calls = _wait(monkeypatch, ready_after=3)
    assert got is True
    assert calls["slept"] == [5.0, 5.0, 5.0]
    assert calls["wake"] >= 1, "기다리기 전에 깨워야 한다"


def test_the_worker_gives_up_at_the_deadline(monkeypatch):
    """무한정 기다리지 않는다 — 끝내 못 켜지면 잡은 지금처럼 실패한다."""
    got, calls = _wait(monkeypatch, ready_after=999, max_wait=12.0)
    assert got is False
    assert sum(calls["slept"]) <= 12.0


def test_the_worker_keeps_pushing_while_it_waits(monkeypatch):
    """reconciler 가 60초마다 0 으로 되돌릴 수 있다 — 기다리는 동안 계속 민다."""
    _, calls = _wait(monkeypatch, ready_after=3)
    assert calls["wake"] >= 3


def test_an_always_on_holder_is_not_waited_for(monkeypatch):
    """상시 가동인데 못 닿으면 진짜 장애다 — 3분을 기다려도 달라지지 않고 잡만 늦게 죽는다."""
    monkeypatch.setattr(facemarket, "holder_ready",
                        lambda *a, **kw: pytest.fail("건드리면 안 된다"))
    assert asyncio.run(facemarket.wait_for_holder(_app(autoscale="off"))) is True


def test_the_flag_is_a_string_not_a_boolean():
    """"off" 는 참인 문자열이다 — bool() 로 보면 상시 가동 환경이 3분을 헛되이 기다린다."""
    assert bool("off") is True


def test_nothing_to_wait_for_when_vc_is_not_required(monkeypatch):
    """VC 가 필수가 아니면 holder 가 없어도 컷은 나간다 — 3분을 잡아먹으면 안 된다."""
    monkeypatch.setattr(facemarket, "holder_ready",
                        lambda *a, **kw: pytest.fail("건드리면 안 된다"))
    got = asyncio.run(facemarket.wait_for_holder(_app(required=False)))
    assert got is True


def test_the_workers_wait_before_they_take_a_connection():
    """**커넥션을 쥔 채로 자면 풀이 마른다**(2026-09-08 사고). 대기는 페이로드만 보고 먼저 한다."""
    import pathlib

    from app.workers import detail_page_job, editor_image_job

    for module, entry in ((editor_image_job, "async def run_editor_image_job"),
                          (detail_page_job, "async def run_detail_page_job")):
        text = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        body = text[text.index(entry):]
        wait_at = body.index("await facemarket.wait_for_holder(app)")
        first_conn = body.index("pool.connection()")
        assert wait_at < first_conn, module.__name__
