"""얼굴 파드 — 2026-09-14 20:33 셀러 잡 3ed6970e 얼굴컷 8장 전멸의 원인 두 개.

detail-worker 로그(11:36Z): render HTTP 400 {"detail":"lora sha256 mismatch"} ×2 · HTTP 500 ×2 ·
이어서 "파드가 600초 안에 뜨지 않았다" ×4. fm_model_loras.lora_sha256 과 R2 파일 sha 는 같았다
(65dc75fc…) — 파일은 멀쩡했고 **경합**이었다. 직전에 그 파드에는 다른 LoRA 가 올라가 있었다.

  ① 같은 `<path>.part` 에 동시 쓰기 → 내용이 섞여 sha 불일치(400), 한쪽 _unlink 가 다른 쪽이
     os.replace 할 파일을 지워 500.
  ② LoRA **교체** 자체. Qwen-Image-Edit-2509 파이프라인이 ≈58GB 라 80GB 카드에 한 벌만 들어간다 —
     교체는 이전 파이프라인 참조를 쥔 채 새로 적재해 항상 OOM 이고, 두 번째 모델이 영영 안 올라간다.
     그래서 **교체를 없앴다**: 파드 하나 = LoRA 하나, 다른 키는 409(2026-09-14 사용자 결정).
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


# ── ② 파드 하나 = LoRA 하나 ─────────────────────────────────────────────────
class _FakeBackend:
    def __init__(self, *a, **kw):
        self.base_pipe = kw.get("base_pipe")

    def pipeline(self):
        return self


def _patch_bind(monkeypatch, *, backend=_FakeBackend, lora_file=None, bound=None):
    monkeypatch.setattr(svc, "QwenLocalBackend", backend)
    monkeypatch.setattr(svc, "LORA_KEY", bound)
    monkeypatch.setattr(svc, "LORA_URL", None)
    monkeypatch.setattr(svc, "LORA_SHA256", None)
    if lora_file is not None:
        monkeypatch.setattr(svc, "_lora_file", lora_file)


def test_another_lora_is_refused_not_swapped(monkeypatch):
    """★ 이 파드는 물고 있는 LoRA 만 렌더한다. 교체 시도 자체가 없다.

    교체하면 58GB 파이프라인 두 벌이 겹치는 순간이 생겨 항상 OOM 이다. 409 를 받은 클라이언트는
    그 컷을 실패시키고 원본을 내보내지 않는다(#309 규칙).
    """
    loads = []

    class _Counting(_FakeBackend):
        def pipeline(self):
            loads.append(1)
            return self

    _patch_bind(monkeypatch, backend=_Counting, bound=LORA_KEY,
                lora_file=lambda k, u=None, s=None: "/tmp/x.safetensors")
    svc._state.update(backend=None, lora=None, base=object(), binding=None, last_error=None)
    assert svc._backend(LORA_KEY, _URL, None) is not None

    with pytest.raises(HTTPException) as err:
        svc._backend(OTHER_KEY, _URL, None)
    assert err.value.status_code == 409
    assert "bound to another lora" in str(err.value.detail)
    assert loads == [1], "다른 키 요청이 파이프라인을 다시 올렸다 — 교체가 남아 있다"
    assert svc._state["lora"] == LORA_KEY


def test_the_pod_binds_at_boot_when_the_env_says_which_lora(monkeypatch):
    """부팅에서 결합까지 끝낸다 — 첫 컷이 다운로드·적재를 물지 않는다."""
    got = {}

    def fake_file(key, url=None, sha=None):
        got.update(key=key, url=url, sha=sha)
        return "/tmp/x.safetensors"

    _patch_bind(monkeypatch, bound=LORA_KEY, lora_file=fake_file)
    monkeypatch.setattr(svc, "LORA_URL", _URL)
    monkeypatch.setattr(svc, "LORA_SHA256", "b" * 64)
    svc._state.update(backend=None, lora=None, base=object(), binding=None, last_error=None)

    svc._backend(LORA_KEY, None, None)
    assert got == {"key": LORA_KEY, "url": _URL, "sha": "b" * 64}
    body = svc.healthz()
    assert body["lora"] == LORA_KEY and body["ready"] is True


def test_healthz_names_the_bound_lora_before_it_is_loaded(monkeypatch):
    """아직 적재 전이라도 **어느 LoRA 의 파드인지**는 보여야 한다 — 라우팅을 사람이 확인한다."""
    _patch_bind(monkeypatch, bound=LORA_KEY)
    svc._state.update(backend=None, lora=None, base=None, binding=None, last_error="boom")
    body = svc.healthz()
    assert body["lora"] == LORA_KEY and body["ready"] is False
    assert body["last_error"] == "boom"


def test_a_failed_bind_leaves_no_half_fused_base(monkeypatch):
    """적재가 깨지면 베이스를 버린다 — 절반 융합된 베이스를 다음 시도가 재사용하면 안 된다."""
    class _Boom:
        def __init__(self, *a, **kw):
            raise RuntimeError("CUDA out of memory")

    _patch_bind(monkeypatch, backend=_Boom, bound=LORA_KEY,
                lora_file=lambda k, u=None, s=None: "/tmp/x.safetensors")
    monkeypatch.setattr(svc, "_free_cuda", lambda: None)
    svc._state.update(backend=None, lora=None, base=object(), binding=None, last_error=None)
    with pytest.raises(HTTPException) as err:
        svc._backend(LORA_KEY, _URL, None)
    assert err.value.status_code == 503
    assert svc._state["base"] is None and svc._state["backend"] is None
    assert "CUDA" in (svc._state["last_error"] or "") or "RuntimeError" in (svc._state["last_error"] or "")
    assert svc._state["binding"] is None


def test_binding_is_serialized(monkeypatch):
    """같은 키로 동시에 들어와도 파이프라인은 한 번만 만든다."""
    inside, peak = [], []

    class _Slow(_FakeBackend):
        def pipeline(self):
            inside.append(1)
            peak.append(len(inside))
            time.sleep(0.05)
            inside.pop()
            return self

    _patch_bind(monkeypatch, backend=_Slow, bound=LORA_KEY,
                lora_file=lambda k, u=None, s=None: "/tmp/x.safetensors")
    svc._state.update(backend=None, lora=None, base=object(), binding=None, last_error=None)
    threads = [threading.Thread(target=svc._backend, args=(LORA_KEY, _URL, None)) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert max(peak) == 1, f"동시에 {max(peak)}개가 파이프라인을 만들었다"


def test_a_pod_without_an_env_key_binds_to_the_first_request(monkeypatch):
    """env 가 없는 수동 파드는 첫 요청의 키로 굳는다 — 그 뒤로는 마찬가지로 409 다."""
    _patch_bind(monkeypatch, bound=None, lora_file=lambda k, u=None, s=None: "/tmp/x.safetensors")
    svc._state.update(backend=None, lora=None, base=object(), binding=None, last_error=None)
    svc._backend(LORA_KEY, _URL, None)
    assert svc.bound_lora() == LORA_KEY
    with pytest.raises(HTTPException) as err:
        svc._backend(OTHER_KEY, _URL, None)
    assert err.value.status_code == 409
