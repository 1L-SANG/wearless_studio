import asyncio
import contextlib
import types

import app.routes as routes
from app.workers import mannequin_adjust_job as maj


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


class _Conn:
    async def commit(self):
        return None


def _no_db(monkeypatch):
    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn()
    monkeypatch.setattr(routes, "get_conn", fake_conn)


# ---------- 라우트 ----------


def test_adjust_deprecated_410_no_job_no_reserve(client, make_token, monkeypatch):
    """@deprecated (2026-07): :adjust는 항상 410 — 잡 생성·크레딧 예약이 절대 일어나지 않는다.
    (단가 0 전환 후 잡 생성을 허용하면 무과금 AI 생성 경로가 되므로 라우트 차단.)"""
    called = {"create_job": 0, "reserve": 0}

    async def fake_create_job(conn, **kw):
        called["create_job"] += 1
        return {"id": "job-adj-1"}, True

    async def fake_reserve(conn, uid, amount):
        called["reserve"] += 1
        return 99

    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    _no_db(monkeypatch)
    res = client.post(
        "/v1/projects/p1/mannequins:adjust",
        json={"baseId": "A-0", "fitAdjust": "slimmer"},
        headers=_auth(make_token))
    assert res.status_code == 410
    assert res.json()["error"]["code"] == "deprecated_endpoint"
    # 바디 없이 호출해도 410 — Body 필수 검증(422)이 410 계약을 가리면 안 된다
    res_nobody = client.post(
        "/v1/projects/p1/mannequins:adjust", headers=_auth(make_token))
    assert res_nobody.status_code == 410
    assert called == {"create_job": 0, "reserve": 0}


def test_adjust_410_requires_auth(client):
    # 인증 없이도 라우트 계약(401)이 먼저 — 폐기 여부와 무관하게 인증 게이트 유지
    res = client.post("/v1/projects/p1/mannequins:adjust", json={"baseId": "A-0"})
    assert res.status_code == 401


# ---------- 워커 ----------

class _FakePool:
    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn()
        return _cm()


class _FakeR2:
    def get_bytes(self, key):
        return b"\x89PNG-bytes"

    def put_bytes(self, key, data, mime, cache=None):
        return None

    def delete(self, key):
        return None


class _FakeGemini:
    pass


def _app(settings):
    st = types.SimpleNamespace(settings=settings, pool=_FakePool(), r2=_FakeR2(), gemini=_FakeGemini())
    return types.SimpleNamespace(state=st)


def _job(payload=None):
    return {
        "id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "u1:tok",
        "credits_reserved": 1, "payload": payload or {"baseId": "A-0", "fitAdjust": "slimmer"},
    }


def _settings():
    from conftest import make_settings
    return make_settings(gemini_api_key="x", r2_bucket="b")


def test_run_mannequin_adjust_job_drains_without_ai_and_releases_reserved(monkeypatch):
    """@deprecated 툼스톤: legacy 잡은 AI 호출·R2 접근·성공 종결 없이 즉시 실패 종결(예약 release).
    단가 0 상태에서 legacy 잡이 실행되면 무과금 생성 경로가 되므로 생성 코드 자체가 없어야 한다."""
    captured = {}

    async def fake_finalize_failure(conn, **kw):
        captured.update(kw)
        return True

    async def fail_if_called(*a, **kw):  # 생성·성공 경로가 호출되면 즉시 실패
        raise AssertionError("deprecated drain must not call AI/finalize_success")

    monkeypatch.setattr(maj.repo, "finalize_mannequin_adjust_failure", fake_finalize_failure)
    monkeypatch.setattr(maj.repo, "finalize_mannequin_adjust_success", fail_if_called, raising=False)

    asyncio.run(maj.run_mannequin_adjust_job(_app(_settings()), _job()))

    assert captured["metadata"]["error"] == "deprecated_job_kind"
    assert captured["reserved"] == 1  # legacy 예약분 그대로 release 대상
    assert captured["job_id"] == "j1"
    # 생성 코드가 모듈에서 제거됐는지(무과금 생성 경로 원천 차단) 방어적으로 확인
    assert not hasattr(maj, "mannequin_adjuster")
