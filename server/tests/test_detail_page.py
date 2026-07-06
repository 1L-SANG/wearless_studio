import asyncio
import contextlib
import types

import app.routes as routes
from app.workers import detail_page_job as dpj


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

def test_detail_404(client, make_token, monkeypatch):
    async def fake_gp(conn, uid, pid):
        return None
    monkeypatch.setattr(routes.repo, "get_project", fake_gp)
    _no_db(monkeypatch)
    res = client.post("/v1/projects/nope/detail-page:generate", headers=_auth(make_token))
    assert res.status_code == 404


def test_detail_creates_job_and_reserves(client, make_token, monkeypatch):
    seen = {}

    async def fake_gp(conn, uid, pid):
        return {"id": pid}

    async def fake_eb(conn, pid):
        return []  # 미완료

    async def fake_sb(conn, pid):
        return [{"id": "b1", "source": "ai"}, {"id": "b2", "source": "ai"}, {"id": "m", "source": "mine"}]

    async def fake_create_job(conn, **kw):
        seen.update(kw)
        return {"id": "job-dp-1"}, True

    async def fake_reserve(conn, uid, amount):
        seen["reserved"] = amount
        return 100

    monkeypatch.setattr(routes.repo, "get_project", fake_gp)
    monkeypatch.setattr(routes.repo, "get_editor_blocks", fake_eb)
    monkeypatch.setattr(routes.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    _no_db(monkeypatch)
    res = client.post("/v1/projects/p1/detail-page:generate", headers=_auth(make_token))
    assert res.status_code == 202, res.text
    assert res.json()["jobId"] == "job-dp-1"
    assert seen["kind"] == "detail_page"
    assert seen["credits_reserved"] == 2  # ai 블록 2개 × storyboardPerCut(1)
    assert seen["reserved"] == 2


def test_detail_completed_recall(client, make_token, monkeypatch):
    async def fake_gp(conn, uid, pid):
        return {"id": pid}

    async def fake_eb(conn, pid):
        return [{"id": "b0", "kind": "hook"}]  # 이미 완료

    async def fake_acct(conn, uid):
        return {"credits": 42}

    monkeypatch.setattr(routes.repo, "get_project", fake_gp)
    monkeypatch.setattr(routes.repo, "get_editor_blocks", fake_eb)
    monkeypatch.setattr(routes.repo, "get_account", fake_acct)
    _no_db(monkeypatch)
    res = client.post("/v1/projects/p1/detail-page:generate", headers=_auth(make_token))
    assert res.status_code == 200
    body = res.json()
    assert body["data"][0]["id"] == "b0" and body["credits"] == 42


# ---------- 워커 (부분 성공 정산) ----------

class _FakePool:
    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn()
        return _cm()


class _FakeR2:
    def get_bytes(self, key):
        return b"\x89PNG-bytes"

    def put_bytes(self, key, data, mime):
        return None


class _FakeGemini:
    pass


def _app(settings):
    st = types.SimpleNamespace(settings=settings, pool=_FakePool(), r2=_FakeR2(), gemini=_FakeGemini())
    return types.SimpleNamespace(state=st)


def _job():
    return {"id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "u1:tok", "credits_reserved": 2}


def _settings():
    from conftest import make_settings
    return make_settings(gemini_api_key="x", r2_bucket="b")


def test_run_detail_page_job_partial_success(monkeypatch):
    captured = {}

    async def fake_gp(conn, uid, pid):
        return {"copywriting": False}

    async def fake_sb(conn, pid):
        return [{"id": "b1", "source": "ai", "cutType": "styling"},
                {"id": "b2", "source": "ai", "cutType": "product"}]

    async def fake_prod(conn, pid):
        return {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}

    async def fake_analysis(conn, pid):
        return {}

    async def fake_asset(conn, uid, aid):
        return {"mime_type": "image/png", "r2_key": "k/a1"}

    call = {"n": 0}

    async def fake_gen(settings, gemini, cut_spec, product, images):
        call["n"] += 1
        if call["n"] == 1:
            return b"IMG", "image/png"     # b1 성공
        raise RuntimeError("gen fail")      # b2 실패 → 빈 슬롯, 미차감

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting):
        captured["cut_results"] = cut_results
        return [{"id": "b0", "kind": "hook", "elements": []}]

    async def fake_finalize(conn, **kw):
        captured.update(kw)
        return {"editor_blocks": kw["editor_blocks"], "available": 99}

    async def fake_emit(pool, job_id, et, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_gp)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_sb)
    monkeypatch.setattr(dpj.repo, "get_product", fake_prod)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    asyncio.run(dpj.run_detail_page_job(_app(_settings()), _job()))

    assert captured["charge"] == 1              # 성공 컷 1개 × per_cut(1) — 실패 컷 미차감
    assert len(captured["cut_assets"]) == 1
    assert len(captured["cut_results"]) == 1     # b1만
