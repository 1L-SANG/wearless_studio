"""얼굴 파드 동시 요청 — 2026-09-14 20:33 셀러 잡 3ed6970e 얼굴컷 8장 전멸의 원인 두 개.

detail-worker 로그(11:36Z): render HTTP 400 {"detail":"lora sha256 mismatch"} ×2 · HTTP 500 ×2 ·
이어서 "파드가 600초 안에 뜨지 않았다" ×4. fm_model_loras.lora_sha256 과 R2 파일 sha 는 같았다
(65dc75fc…) — 파일은 멀쩡했고 **경합**이었다. 직전에 그 파드에는 다른 LoRA 가 올라가 있었다.

  ① 같은 `<path>.part` 에 동시 쓰기 → 내용이 섞여 sha 불일치(400), 한쪽 _unlink 가 다른 쪽이
     os.replace 할 파일을 지워 500.
  ② 교체가 **새 파일을 받기 전에** 기존 backend 를 버린다. 베이스는 backend 안에 있어서
     (_state["base"]=None) 다운로드가 깨지면 backend 도 base 도 없다 → healthz base_loaded=false
     영구 → 클라이언트는 base_loaded 만 보고 기다리다 상한에서 포기 → 교착.
"""

import hashlib
import pathlib
import threading
import time

import pytest
from fastapi import HTTPException

import face_render_service as svc

LORA_KEY = "facemarket/loras/m/v1.safetensors"
OTHER_KEY = "facemarket/loras/other/v1.safetensors"
_URL = "https://acct.r2.cloudflarestorage.com/wearless-face/x.safetensors?X-Amz-Signature=zzz"


def _weights(seed: int = 0) -> bytes:
    return bytes((i + seed) % 256 for i in range(4096))


@pytest.fixture(autouse=True)
def _reset_state():
    # getattr — 고치기 **전** 코드로 돌려도 픽스처가 아니라 **동작**에서 깨지게 한다(회귀 증명용).
    before = dict(svc._state)
    getattr(svc, "_LORA_LOCKS", {}).clear()
    yield
    svc._state.clear()
    svc._state.update(before)
    getattr(svc, "_LORA_LOCKS", {}).clear()


class _SlowStream:
    """다운로드가 **느리게** 흐르는 대역 — 그동안 다른 요청이 끼어들 틈을 만든다."""

    def __init__(self, body: bytes, delay: float):
        self.status_code, self._body, self._delay = 200, body, delay

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_bytes(self, size=None):
        half = len(self._body) // 2
        yield self._body[:half]
        time.sleep(self._delay)
        yield self._body[half:]


# ── ① 캐시 미스 동시 요청 ────────────────────────────────────────────────────
def test_four_concurrent_misses_download_once_and_all_succeed(tmp_path, monkeypatch):
    """네 요청이 같은 키로 동시에 들어와도 다운로드는 **한 번**, 넷 다 같은 파일을 받는다."""
    monkeypatch.setattr(svc, "CACHE_DIR", str(tmp_path))
    body = _weights()
    sha = hashlib.sha256(body).hexdigest()
    calls: list[str] = []

    def fake_stream(method, url, **kw):
        calls.append(url)
        return _SlowStream(body, 0.05)

    import httpx

    monkeypatch.setattr(httpx, "stream", fake_stream)

    results: list = []
    errors: list = []

    def worker():
        try:
            results.append(svc._lora_file(LORA_KEY, _URL, sha))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    assert errors == [], f"동시 요청이 실패했다: {errors}"
    assert len(results) == 4 and len(set(results)) == 1
    assert len(calls) == 1, f"다운로드가 {len(calls)}번 일어났다 — 키당 1회여야 한다"
    assert pathlib.Path(results[0]).read_bytes() == body


def test_the_temp_file_is_unique_per_request(tmp_path, monkeypatch):
    """임시 파일 이름이 요청마다 달라야 남의 것을 덮어쓰거나 지우지 않는다."""
    monkeypatch.setattr(svc, "CACHE_DIR", str(tmp_path))
    seen: set[str] = set()
    body = _weights()
    sha = hashlib.sha256(body).hexdigest()

    class _Peek(_SlowStream):
        def iter_bytes(self, size=None):
            seen.update(p.name for p in pathlib.Path(str(tmp_path)).glob("*.part"))
            yield self._body

    import httpx

    monkeypatch.setattr(httpx, "stream", lambda m, u, **kw: _Peek(body, 0))
    svc._lora_file(LORA_KEY, _URL, sha)
    pathlib.Path(svc._cache_path(LORA_KEY)).unlink()
    getattr(svc, "_LORA_LOCKS", {}).clear()
    svc._lora_file(LORA_KEY, _URL, sha)
    assert len(seen) == 2, f"두 번의 다운로드가 같은 임시 이름을 썼다: {seen}"
    assert not list(pathlib.Path(str(tmp_path)).glob("*.part")), "성공한 뒤 임시 파일이 남았다"


def test_a_failed_download_never_removes_someone_elses_file(tmp_path, monkeypatch):
    """sha 불일치로 지우는 것은 **자기 임시 파일**뿐 — 캐시에 있는 정상 파일은 그대로다."""
    monkeypatch.setattr(svc, "CACHE_DIR", str(tmp_path))
    good = _weights()
    other = svc._cache_path(OTHER_KEY)
    pathlib.Path(other).write_bytes(good)

    import httpx

    monkeypatch.setattr(httpx, "stream", lambda m, u, **kw: _SlowStream(_weights(7), 0))
    with pytest.raises(HTTPException) as err:
        svc._lora_file(LORA_KEY, _URL, hashlib.sha256(good).hexdigest())
    assert err.value.status_code == 400 and "sha256" in str(err.value.detail)
    assert pathlib.Path(other).read_bytes() == good
    assert not list(pathlib.Path(str(tmp_path)).glob("*.part"))


def test_startup_sweeps_partials_left_by_a_crash(tmp_path, monkeypatch):
    assert hasattr(svc, "_sweep_partials"), "기동 때 죽은 임시 파일을 치우는 절차가 없다"
    monkeypatch.setattr(svc, "CACHE_DIR", str(tmp_path))
    (tmp_path / "abc.part").write_bytes(b"x")
    (tmp_path / "keep.safetensors").write_bytes(b"y")
    svc._sweep_partials()
    assert not (tmp_path / "abc.part").exists()
    assert (tmp_path / "keep.safetensors").exists()


# ── ② 교체 ──────────────────────────────────────────────────────────────────
class _FakeBackend:
    def __init__(self, *a, **kw):
        self.base_pipe = kw.get("base_pipe")

    def pipeline(self):
        return self


def _patch_swap(monkeypatch, *, backend=_FakeBackend, lora_file=None):
    monkeypatch.setattr(svc, "QwenLocalBackend", backend)
    if hasattr(svc, "_free_cuda"):
        monkeypatch.setattr(svc, "_free_cuda", lambda: None)
    if lora_file is not None:
        monkeypatch.setattr(svc, "_lora_file", lora_file)


def test_a_failed_lora_fetch_keeps_the_current_backend(monkeypatch):
    """★ 다운로드가 깨져도 **지금 돌던 LoRA 로 계속 렌더한다**. base_loaded 는 true 로 남는다."""
    current = _FakeBackend()
    svc._state.update(backend=current, lora=LORA_KEY, lora_sha256="a" * 64, loaded_at=1.0,
                      base=None, swapping=None, last_error=None)

    def boom(key, url=None, sha=None):
        raise HTTPException(400, "lora sha256 mismatch")

    _patch_swap(monkeypatch, lora_file=boom)
    with pytest.raises(HTTPException) as err:
        svc._backend(OTHER_KEY, _URL, "b" * 64)
    assert err.value.status_code == 400

    assert svc._state["backend"] is current, "받지도 못한 LoRA 때문에 멀쩡한 backend 를 버렸다"
    assert svc._state["lora"] == LORA_KEY
    assert svc.healthz()["base_loaded"] is True
    assert svc._state["swapping"] is None
    assert "mismatch" in (svc._state["last_error"] or "")


def test_a_failed_pipeline_load_restores_the_previous_backend(monkeypatch):
    """파이프라인 적재가 깨지면 이전 backend 를 되돌린다 — base_loaded=false 로 굳지 않는다."""
    current = _FakeBackend()
    svc._state.update(backend=current, lora=LORA_KEY, lora_sha256="a" * 64, loaded_at=1.0,
                      base=None, swapping=None, last_error=None)

    class _Boom:
        def __init__(self, *a, **kw):
            raise RuntimeError("CUDA out of memory")

    _patch_swap(monkeypatch, backend=_Boom, lora_file=lambda k, u=None, s=None: "/tmp/x.safetensors")
    with pytest.raises(HTTPException) as err:
        svc._backend(OTHER_KEY, _URL, "b" * 64)
    assert err.value.status_code == 503

    assert svc._state["backend"] is current and svc._state["lora"] == LORA_KEY
    assert svc.healthz()["base_loaded"] is True
    assert "CUDA" in (svc._state["last_error"] or "") or "RuntimeError" in (svc._state["last_error"] or "")


def test_with_no_previous_backend_a_failed_swap_reloads_the_base(monkeypatch):
    """되돌릴 backend 가 없으면 최소한 base 를 다시 올린다 — 준비 판정이 살아 있어야 한다."""
    svc._state.update(backend=None, lora=None, lora_sha256=None, loaded_at=None,
                      base=None, swapping=None, last_error=None)
    reloaded = []

    class _Boom:
        def __init__(self, *a, **kw):
            raise RuntimeError("load failed")

    monkeypatch.setattr(svc, "_load_base", lambda: (reloaded.append(1), svc._state.update(base=object()))[0])
    _patch_swap(monkeypatch, backend=_Boom, lora_file=lambda k, u=None, s=None: "/tmp/x.safetensors")
    with pytest.raises(HTTPException):
        svc._backend(LORA_KEY, _URL, "b" * 64)
    assert reloaded == [1]
    assert svc.healthz()["base_loaded"] is True


def test_swaps_are_serialized(monkeypatch):
    """교체가 겹치면 직렬화된다 — 두 스레드가 동시에 파이프라인을 만들지 않는다."""
    svc._state.update(backend=None, lora=None, lora_sha256=None, loaded_at=None,
                      base=object(), swapping=None, last_error=None)
    inside = []
    peak = []

    class _Slow(_FakeBackend):
        def pipeline(self):
            inside.append(1)
            peak.append(len(inside))
            time.sleep(0.05)
            inside.pop()
            return self

    _patch_swap(monkeypatch, backend=_Slow, lora_file=lambda k, u=None, s=None: "/tmp/x.safetensors")
    threads = [threading.Thread(target=svc._backend, args=(f"{LORA_KEY}#{i}", _URL, None))
               for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert max(peak) == 1, f"동시에 {max(peak)}개가 파이프라인을 만들었다"


def test_the_same_key_twice_does_not_reload(monkeypatch):
    """같은 키가 다시 오면 그대로 쓴다(교체 아님) — 자물쇠를 잡고도 다시 확인한다."""
    loads = []

    class _Counting(_FakeBackend):
        def pipeline(self):
            loads.append(1)
            return self

    svc._state.update(backend=None, lora=None, lora_sha256=None, loaded_at=None,
                      base=object(), swapping=None, last_error=None)
    _patch_swap(monkeypatch, backend=_Counting, lora_file=lambda k, u=None, s=None: "/tmp/x.safetensors")
    first = svc._backend(LORA_KEY, _URL, None)
    second = svc._backend(LORA_KEY, _URL, None)
    assert first is second and len(loads) == 1


def test_healthz_shows_swapping_and_last_error():
    svc._state.update(backend=None, base=object(), swapping="k", last_error="boom")
    body = svc.healthz()
    assert body.get("swapping") == "k" and body.get("last_error") == "boom"
