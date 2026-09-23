"""RunPod 서버리스 잡 뒷정리(2026-09-23, 감사 7번).

결과를 더는 기다리지 않는 잡은 RunPod 에서도 끈다 — 제한 시간 초과 · 상태 조회 실패 · 호출자
포기(코루틴 취소). 예전에는 우리가 손을 떼도 RunPod 잡은 끝까지 돌았고(버릴 결과에 GPU 요금),
asyncio.to_thread 스레드는 코루틴이 취소된 뒤에도 900초 동안 혼자 폴링했다.

유료 호출 없음 — HTTP 는 전부 대역이다.
"""

import asyncio
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from app.agents import face_angle_swap as angle
from app.agents import face_identity as fi
from app.agents import runpod_jobs


class _Res:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload


class _RunPodHTTP:
    """/run · /status/<id> · /cancel/<id> 대역. statuses 는 차례로, 마지막 값은 계속 준다."""

    def __init__(self, statuses, *, status_code=200, cancel_raises=False):
        self.statuses = list(statuses)
        self.status_code = status_code
        self.cancel_raises = cancel_raises
        self.posts: list[str] = []
        self.status_calls = 0
        self.cancelled = threading.Event()

    def post(self, path, json=None):
        self.posts.append(path)
        if path.startswith("/cancel/"):
            if self.cancel_raises:
                raise RuntimeError("network down")
            self.cancelled.set()
            return _Res({"id": path.rsplit("/", 1)[1], "status": "CANCELLED"})
        return _Res({"id": "job-9"})

    def get(self, path):
        self.status_calls += 1
        body = self.statuses[0] if len(self.statuses) == 1 else self.statuses.pop(0)
        return _Res(body, self.status_code)


def _face_backend(http, *, timeout=30.0, poll=0.0):
    backend = fi.ServerlessFaceBackend("ep123", "key", timeout=timeout, poll_seconds=poll)
    backend._client = http
    return backend


def _angle_backend(http, *, timeout=30.0, poll=0.0):
    backend = angle.ServerlessBackend.__new__(angle.ServerlessBackend)
    backend.base = "https://api.runpod.ai/v2/ep1"
    backend._client = http
    backend._images = []
    backend._timeout = timeout
    backend._poll = poll
    return backend


def _png_b64():
    import base64
    from io import BytesIO

    buf = BytesIO()
    Image.new("RGB", (4, 4), (7, 8, 9)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


# ── 얼굴 패스(ServerlessFaceBackend) ──────────────────────────────────────────
def test_face_poll_timeout_cancels_the_runpod_job():
    http = _RunPodHTTP([{"status": "IN_PROGRESS"}])
    backend = _face_backend(http, timeout=0.0)
    with pytest.raises(RuntimeError, match="serverless_"):
        backend.render(Image.new("RGB", (8, 8)), "p", 42)
    assert http.posts == ["/run", "/cancel/job-9"]


def test_face_status_errors_cancel_the_runpod_job():
    http = _RunPodHTTP([{}], status_code=503)
    backend = _face_backend(http)
    with pytest.raises(RuntimeError, match="serverless_status_http_error"):
        backend.render(Image.new("RGB", (8, 8)), "p", 42)
    assert http.status_calls == fi.SERVERLESS_STATUS_RETRIES
    assert http.posts == ["/run", "/cancel/job-9"]


def test_face_completed_job_is_not_cancelled():
    http = _RunPodHTTP([{"status": "IN_QUEUE"},
                        {"status": "COMPLETED", "output": {"image_png": _png_b64()}}])
    out = _face_backend(http).render(Image.new("RGB", (8, 8)), "p", 42)
    assert out.size == (4, 4)
    assert http.posts == ["/run"]


def test_face_failed_job_is_not_cancelled_again():
    """RunPod 이 이미 끝낸 잡(FAILED)은 끌 게 없다 — 손을 뗀 경우에만 끈다."""
    http = _RunPodHTTP([{"status": "FAILED"}])
    with pytest.raises(RuntimeError, match="serverless_failed"):
        _face_backend(http).render(Image.new("RGB", (8, 8)), "p", 42)
    assert http.posts == ["/run"]


def test_cancel_failure_never_masks_the_original_failure():
    """끄기는 best-effort 다 — 그 실패가 컷의 실패 사유를 덮으면 안 된다."""
    http = _RunPodHTTP([{"status": "IN_PROGRESS"}], cancel_raises=True)
    backend = _face_backend(http, timeout=0.0)
    with pytest.raises(RuntimeError, match="serverless_timed_out"):
        backend.render(Image.new("RGB", (8, 8)), "p", 42)
    assert http.posts == ["/run", "/cancel/job-9"]


def test_cancel_helper_swallows_http_errors():
    class _Boom:
        def post(self, path):
            return _Res({}, 500)

    runpod_jobs.cancel(_Boom(), "job-1", what="test")      # 예외 없음
    runpod_jobs.cancel(_Boom(), "", what="test")           # 잡 id 가 없으면 아무것도 안 한다


def test_abandoned_face_render_cancels_and_stops_polling():
    """코루틴이 취소되면(워커 종료) 스레드가 RunPod 잡을 끄고 폴링을 멈춘다."""
    http = _RunPodHTTP([{"status": "IN_PROGRESS"}])
    backend = _face_backend(http, timeout=60.0, poll=0.02)

    async def go():
        task = asyncio.create_task(runpod_jobs.to_thread(
            backend.render, Image.new("RGB", (8, 8)), "p", 42))
        while http.status_calls < 2:          # 폴링이 돌기 시작할 때까지
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert http.cancelled.wait(5.0), "abandoned job must be cancelled on RunPod"
    assert http.posts == ["/run", "/cancel/job-9"]
    calls = http.status_calls
    time.sleep(0.2)
    assert http.status_calls == calls, "polling thread must stop after abandonment"


def test_abandoned_face_pass_does_not_submit_the_next_seed():
    """시드 루프(run_face_pass)는 한 시드가 실패해도 다음 시드를 넣는다 — 포기한 뒤엔 넣지 않는다."""
    http = _RunPodHTTP([{"status": "IN_PROGRESS"}])
    backend = _face_backend(http, timeout=60.0, poll=0.02)
    second_attempt = {}

    def two_seeds():
        try:
            backend.render(Image.new("RGB", (8, 8)), "p", 42)
        except RuntimeError:
            pass
        try:
            backend.render(Image.new("RGB", (8, 8)), "p", 43)
        except RuntimeError as exc:
            second_attempt["error"] = str(exc)

    async def go():
        task = asyncio.create_task(runpod_jobs.to_thread(two_seeds))
        while http.status_calls < 2:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert http.cancelled.wait(5.0)
    deadline = time.monotonic() + 5.0
    while "error" not in second_attempt and time.monotonic() < deadline:
        time.sleep(0.01)
    assert second_attempt["error"] == "serverless_abandoned"
    assert http.posts.count("/run") == 1


def test_to_thread_without_cancellation_behaves_like_asyncio_to_thread():
    async def go():
        return await runpod_jobs.to_thread(lambda a, b=0: a + b, 2, b=3)

    assert asyncio.run(go()) == 5
    assert runpod_jobs.abandoned() is False


# ── 각도 교체(face_angle_swap.ServerlessBackend) ──────────────────────────────
def test_angle_poll_timeout_cancels_the_runpod_job():
    http = _RunPodHTTP([{"status": "IN_PROGRESS"}])
    backend = _angle_backend(http, timeout=0.0)
    with pytest.raises(angle.AngleSwapUnavailable) as err:
        backend.run({"14": {}})
    assert err.value.reason == "backend_error"
    assert http.posts == ["/run", "/cancel/job-9"]


def test_angle_status_error_cancels_the_runpod_job():
    http = _RunPodHTTP([{}], status_code=502)
    with pytest.raises(angle.AngleSwapUnavailable):
        _angle_backend(http).run({"14": {}})
    assert http.posts == ["/run", "/cancel/job-9"]


def test_angle_completed_job_is_not_cancelled():
    http = _RunPodHTTP([{"status": "COMPLETED",
                         "output": {"images": [{"type": "base64", "data": _png_b64()}]}}])
    out = _angle_backend(http).run({"14": {}})
    assert out.shape == (4, 4, 3)
    assert http.posts == ["/run"]


def test_abandoned_angle_swap_cancels_and_stops_polling():
    http = _RunPodHTTP([{"status": "IN_PROGRESS"}])
    backend = _angle_backend(http, timeout=60.0, poll=0.02)

    async def go():
        task = asyncio.create_task(runpod_jobs.to_thread(backend.run, {"14": {}}))
        while http.status_calls < 2:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert http.cancelled.wait(5.0)
    calls = http.status_calls
    time.sleep(0.2)
    assert http.status_calls == calls
    assert http.posts == ["/run", "/cancel/job-9"]


def test_swap_runs_the_backend_through_the_abandonable_thread(monkeypatch):
    """swap() 이 asyncio.to_thread 로 되돌아가면 취소 신호가 스레드에 안 간다."""
    seen = {}

    async def fake_to_thread(fn, *args, **kwargs):
        seen["fn"] = fn
        raise angle.AngleSwapUnavailable("backend_error")

    plan = SimpleNamespace(metadata={}, nose_right=None)
    monkeypatch.setattr(angle, "plan", lambda *a, **k: plan)
    monkeypatch.setattr(runpod_jobs, "to_thread", fake_to_thread)
    from io import BytesIO

    buf = BytesIO()
    Image.fromarray(np.full((8, 8, 3), 9, np.uint8)).save(buf, "PNG")
    with pytest.raises(angle.AngleSwapUnavailable):
        asyncio.run(angle.swap(buf.getvalue(), "image/png", direction="back",
                               photos=angle.AnglePhotos(back=buf.getvalue()), backend=object()))
    assert seen["fn"] is angle._run
