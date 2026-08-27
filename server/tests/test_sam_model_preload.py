"""기동 직후 배경에서 가중치를 올린다 — 콜드스타트의 죽은 시간을 쓰려는 것.

실측(2026-08-27 prod use1): 0대에서 깨어난 sam2 는 +59.6초에 이미 uvicorn 이 듣고 있는데
첫 요청은 +157초에야 온다. 그 사이 100초가 통째로 놀고 있고, 그동안 안 올려 둔 탓에 첫 셀러
요청이 torch/transformers 임포트 값을 대신 냈다.
"""

import asyncio

import pytest
from fastapi import FastAPI

from sam_service import api as sam_api
from sam_service.segmentation import SegmentationUnavailable


class _Recorder:
    def __init__(self, boom=None):
        self.calls = []
        self._boom = boom

    async def __call__(self, model_id=None):
        self.calls.append(model_id)
        if self._boom:
            raise self._boom
        return object()


async def _run_lifespan(app):
    async with sam_api._lifespan(app):
        # 기동은 preload 를 **기다리지 않는다** — 여기서 태스크에 양보만 해 주면
        # 프로덕션에서 uvicorn 이 이미 8080 을 듣고 있는 상태와 같다.
        await asyncio.sleep(0)
        await asyncio.sleep(0)


def _lifespan_with(monkeypatch, recorder, preload=None):
    monkeypatch.setattr(sam_api.model_registry, "get_segmenter", recorder)
    if preload is None:
        monkeypatch.delenv("SAM_MODEL_PRELOAD", raising=False)
    else:
        monkeypatch.setenv("SAM_MODEL_PRELOAD", preload)
    app = FastAPI()
    asyncio.run(_run_lifespan(app))
    return app


def test_startup_loads_the_model_in_the_background(monkeypatch):
    rec = _Recorder()
    _lifespan_with(monkeypatch, rec)
    assert len(rec.calls) == 1, "기동 한 번에 로드도 한 번이다"


def test_it_can_be_turned_off(monkeypatch):
    """로컬·테스트가 실수로 700MB 짜리 임포트를 시작하지 않게 하는 탈출구."""
    rec = _Recorder()
    _lifespan_with(monkeypatch, rec, preload="off")
    assert rec.calls == []


def test_a_missing_model_does_not_kill_startup(monkeypatch):
    """로드 실패는 이미 model_registry 가 기억한다 — 기동까지 죽이면 헬스체크가 영영 안 뜬다."""
    rec = _Recorder(boom=SegmentationUnavailable("no weights"))
    app = _lifespan_with(monkeypatch, rec)
    assert len(rec.calls) == 1
    assert app.state.preload_task.done()
    assert app.state.preload_task.exception() is None


def test_any_other_crash_is_swallowed_too(monkeypatch):
    rec = _Recorder(boom=RuntimeError("segfault-ish"))
    app = _lifespan_with(monkeypatch, rec)
    assert app.state.preload_task.exception() is None


def test_the_task_is_not_awaited_during_startup(monkeypatch):
    """preload 를 await 하면 uvicorn 이 안 듣고, ECS 헬스체크 시계가 시작조차 안 한다 —
    회수하려던 시간을 정확히 되돌려주는 실수다."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow(model_id=None):
        started.set()
        await release.wait()
        return object()

    monkeypatch.setattr(sam_api.model_registry, "get_segmenter", _slow)
    monkeypatch.delenv("SAM_MODEL_PRELOAD", raising=False)

    async def _go():
        app = FastAPI()
        async with sam_api._lifespan(app):
            await asyncio.wait_for(started.wait(), timeout=1)
            assert not app.state.preload_task.done(), "기동이 로드를 기다리면 안 된다"
            release.set()

    asyncio.run(_go())


def test_health_still_answers_while_the_model_is_loading():
    """준비 신호(sam_endpoint)가 /health 200 만 보는 근거 — 로드 중에도 200 이다."""
    from fastapi.testclient import TestClient

    app = sam_api.create_app(source_factory=lambda _s: None)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SAM_MODEL_PRELOAD", "off")
        with TestClient(app) as client:
            r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
