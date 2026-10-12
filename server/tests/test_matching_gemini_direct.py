"""내 옷의 원본을 Gemini 한 번으로 정리하며 SAM을 기다리지 않는 계약."""
import asyncio
import io
import base64
import logging
from types import SimpleNamespace

import pytest
from PIL import Image

from app.agents.gemini_image import GeminiError
from app.agents import gemini_image
from app import image_usage
from app.services import sam_retry
from app.services import matching_flatlay
from app.workers import matching_cutout_job as worker
from test_matching_cutout_job import _job_dict, _run
from test_matching_flatlay_job import _FakeGemini, _gen_png, _wire_flatlay


class SourceImages:
    def __init__(self):
        self.reads = []
        self.puts = []
        self.sources = {}
        for key, color in zip(_job_dict()["payload"]["sourceKeys"], [(72, 41, 130), (25, 84, 100)]):
            out = io.BytesIO()
            Image.new("RGB", (40, 60), color).save(out, "PNG")
            self.sources[key] = out.getvalue()

    def get_bytes(self, key):
        self.reads.append(key)
        # 구버전의 SAM 결과를 원본과 다른 색으로 돌려 입력 혼동을 잡는다.
        return self.sources.get(key, _gen_png(size=(40, 60), color=(250, 0, 0)))

    def put_bytes(self, key, data, mime, cache=None):
        self.puts.append((key, data, mime))


@pytest.mark.parametrize("mode", ["on", "full"])
def test_original_photo_is_generated_once_without_sam(monkeypatch, mode):
    r2 = SourceImages()
    gemini = _FakeGemini(image=_gen_png())
    app, _, calls = _wire_flatlay(monkeypatch, gemini=gemini, flag=mode, r2=r2)
    _run(app, _job_dict())

    assert calls["segment"] == [], "내 옷은 SAM으로 자르지 않는다"
    assert len(gemini.calls) == 1
    transmitted = Image.open(io.BytesIO(gemini.calls[0]["images"][0].data)).convert("RGB")
    assert transmitted.getpixel((20, 30)) == (72, 41, 130), "첫 원본 사진이 Gemini 입력이다"
    assert calls["finalize"][1]["state"] == "ready"
    assert calls["swap"] is not None
    assert r2.reads == _job_dict()["payload"]["sourceKeys"]


def test_direct_generation_works_with_sam_unconfigured(monkeypatch):
    gemini = _FakeGemini(image=_gen_png())
    app, _, calls = _wire_flatlay(monkeypatch, gemini=gemini, flag="full")
    app.state.settings.sam_service_url = ""
    app.state.settings.sam_internal_token = ""
    _run(app, _job_dict())
    assert len(gemini.calls) == 1
    assert calls["finalize"][1]["state"] == "ready"


def test_full_grid_reuses_generation_and_preserves_the_other_original_photos(monkeypatch):
    captured = {}
    compose = worker.garment_grid.compose_garment_grid

    def capture(images):
        captured["images"] = list(images)
        return compose(images)

    monkeypatch.setattr(worker.garment_grid, "compose_garment_grid", capture)
    r2 = SourceImages()
    generated = _gen_png()
    gemini = _FakeGemini(image=generated)
    app, _, calls = _wire_flatlay(monkeypatch, gemini=gemini, flag="full", r2=r2)
    _run(app, _job_dict())
    assert len(gemini.calls) == 1
    assert captured["images"] == [generated, r2.sources[_job_dict()["payload"]["sourceKeys"][1]]]
    grid = next(row for row in calls["assets"] if row["asset_id"] == calls["swap"][2])
    assert grid["metadata"]["flatlayFront"] is True
    assert grid["metadata"]["producer"] == "gemini-matching-flatlay"


@pytest.mark.parametrize("failure", [GeminiError("provider unavailable"), asyncio.TimeoutError()])
def test_failed_generation_keeps_original_and_does_not_fall_back_to_sam_or_retry(monkeypatch, failure):
    gemini = _FakeGemini(error=failure)
    app, r2, calls = _wire_flatlay(monkeypatch, gemini=gemini, flag="full", r2=SourceImages())
    _run(app, _job_dict())
    assert len(gemini.calls) == 1
    assert calls["segment"] == []
    assert calls["swap"] is None
    assert calls["assets"] == []
    assert r2.puts == []
    assert calls["finalize"][0] == "done"
    assert calls["finalize"][1]["state"] == "failed"
    assert sam_retry.job_is_retryable({"status": "done", "result": calls["finalize"][1]}) is False


def test_the_whole_generation_has_one_deadline_even_when_the_client_keeps_connecting(monkeypatch):
    class SlowClient(_FakeGemini):
        started = 0
        cancelled = False

        async def generate_content_image(self, *args, **kwargs):
            self.started += 1
            try:
                await asyncio.sleep(0.03)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            return await super().generate_content_image(*args, **kwargs)

    monkeypatch.setattr(matching_flatlay, "TIMEOUT_S", 0.01)
    client = SlowClient(image=_gen_png())
    app, r2, calls = _wire_flatlay(monkeypatch, gemini=client, flag="full")
    _run(app, _job_dict())
    assert client.started == 1
    assert client.cancelled is True
    assert calls["finalize"][1]["state"] == "failed"
    assert calls["swap"] is None
    assert r2.puts == []


def _recording_client(monkeypatch, *, gate=None, pause="decode"):
    payload = {"usageMetadata": {"promptTokenCount": 369, "candidatesTokenCount": 1483},
               "candidates": [{"content": {"parts": [{"inlineData": {
                   "mimeType": "image/png", "data": base64.b64encode(_gen_png()).decode()}}]}}]}
    response = SimpleNamespace(status_code=200, json=lambda: payload, text="")

    class Transport:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, *args, **kwargs):
            if gate is not None and pause == "request":
                gate.set()
                await asyncio.Future()
            return response

    async def cpu(func, *args, **kwargs):
        if func is base64.b64decode and pause == "decode":
            if gate is not None:
                gate.set()
                await asyncio.Future()
            else:
                await asyncio.sleep(0.03)
        return func(*args, **kwargs)

    monkeypatch.setattr(gemini_image.httpx, "AsyncClient", Transport)
    monkeypatch.setattr(gemini_image, "run_cpu_bound", cpu)
    monkeypatch.setattr(image_usage, "_persist", False)
    return gemini_image.GeminiImageClient(SimpleNamespace(
        gemini_api_key="test-key", vertex_project=None, vertex_location="global"))


def test_timeout_after_a_recorded_response_does_not_record_the_same_generation_twice(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="wearless.image_usage")
    monkeypatch.setattr(matching_flatlay, "TIMEOUT_S", 0.01)
    client = _recording_client(monkeypatch)
    app, _, calls = _wire_flatlay(monkeypatch, gemini=client, flag="full")
    _run(app, _job_dict())
    assert calls["finalize"][1]["state"] == "failed"
    records = [r for r in caplog.records if r.message.startswith("image_usage model=")]
    assert len(records) == 1
    assert "stage=matching_flatlay" in records[0].message
    assert "image=True" in records[0].message


@pytest.mark.parametrize("pause", ["request", "decode"])
def test_cancellation_propagates_and_records_the_generation_only_once(monkeypatch, caplog, pause):
    caplog.set_level(logging.INFO, logger="wearless.image_usage")
    async def scenario():
        gate = asyncio.Event()
        client = _recording_client(monkeypatch, gate=gate, pause=pause)
        app, _, _ = _wire_flatlay(monkeypatch, gemini=client, flag="full")
        task = asyncio.create_task(matching_flatlay.render_thumbnail(
            client, settings=app.state.settings, source_image=_gen_png(), clothing_type="bottom"))
        await asyncio.wait_for(gate.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())
    records = [r for r in caplog.records if r.message.startswith("image_usage model=")]
    assert len(records) == 1
    assert "stage=matching_flatlay" in records[0].message
