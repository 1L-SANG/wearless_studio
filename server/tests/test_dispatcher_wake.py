"""job 생성 라우트의 디스패처 즉시 기상(wake) 계약 — 2026-07-12 사고 회귀 방지.

wake 가 없으면 202 후 최대 poll_interval(3s) 동안 잡이 pending 으로 떠 있고, 그 창에서
같은 DB 를 폴링하는 외부(구버전/타 env) dispatcher 가 클레임해 갈 수 있다.
계약: 마네킹 생성·재생성이 202 를 반환하기 전에 dispatcher.wake() 가 호출된다.
(dispatcher 미기동 환경에선 no-op — 라우트가 죽지 않는다.)
"""
import contextlib

import app.routes as routes


class _Conn:
    async def commit(self):
        return None


def _no_db(monkeypatch):
    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn()
    monkeypatch.setattr(routes, "get_conn", fake_conn)


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def _wire_mannequin_fakes(monkeypatch, created=True):
    async def fake_get_project(conn, uid, pid):
        return {"id": pid}

    async def fake_create_job(conn, **kw):
        return {"id": "job-1"}, created

    async def fake_get_product(conn, pid):
        return {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}

    async def fake_reserve(conn, uid, cost):
        return {"balance": 10, "reserved": cost}

    async def fake_get_analysis(conn, pid):
        return {}

    async def fake_save_analysis(conn, pid, payload):
        return None

    async def fake_list_cuts(conn, uid, pid):
        return []  # 기존 컷 없음 → 200 캐시 경로 대신 job 생성 경로

    monkeypatch.setattr(routes.repo, "list_mannequin_cuts", fake_list_cuts)
    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)
    monkeypatch.setattr(routes.repo, "get_product", fake_get_product)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_get_analysis)
    monkeypatch.setattr(routes.repo, "save_analysis", fake_save_analysis)
    _no_db(monkeypatch)


class _SpyDispatcher:
    def __init__(self):
        self.woken = 0

    def wake(self):
        self.woken += 1


def test_generate_wakes_dispatcher(client, make_token, monkeypatch):
    _wire_mannequin_fakes(monkeypatch)
    spy = _SpyDispatcher()
    client.app.state.dispatcher = spy
    res = client.post("/v1/projects/p1/mannequins:generate", headers=_auth(make_token))
    assert res.status_code == 202, res.text
    assert spy.woken == 1


def test_regenerate_wakes_dispatcher(client, make_token, monkeypatch):
    _wire_mannequin_fakes(monkeypatch)
    spy = _SpyDispatcher()
    client.app.state.dispatcher = spy
    res = client.post("/v1/projects/p1/mannequins:regenerate", json={}, headers=_auth(make_token))
    assert res.status_code == 202, res.text
    assert spy.woken == 1


def test_generate_survives_without_dispatcher(client, make_token, monkeypatch):
    # dispatcher 미기동(로컬 최소 구동 등) — wake 는 no-op, 라우트는 정상 202
    _wire_mannequin_fakes(monkeypatch)
    client.app.state.dispatcher = None
    res = client.post("/v1/projects/p1/mannequins:generate", headers=_auth(make_token))
    assert res.status_code == 202, res.text
