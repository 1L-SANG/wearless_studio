"""인물 LoRA 얼굴 렌더 서비스 — HttpFaceBackend 의 서버 쪽 반쪽 (GPU 파드 전용, 단일 파일).

계약은 app/agents/face_identity.py 의 HttpFaceBackend.render 하나다:

    POST /render   {control_png(b64), prompt, seed, steps, guidance_scale, negative_prompt, lora}
                →  {image_png(b64)}
    POST /upscale  {image_png(b64), scale} → {image_png(b64)}  — 얼굴 **크롭만** ESRGAN 으로 키운다.
                가중치가 없으면 503 이고 호출자는 Lanczos 로 간다(컷은 그대로 나온다).
    GET  /healthz  인증 없음. 파이프라인이 올라와 있는지·어떤 LoRA 가 물려 있는지·확대기·레시피 해시.

렌더 자체는 app/agents/face_identity_qwen.QwenLocalBackend 를 그대로 쓴다 — 파드와 로컬이
같은 코드를 돌아야 "HTTP 로 바꿨더니 그림이 달라졌다"가 생기지 않는다(C-3 바이트 비교의 근거).

★ 파드 규칙(2026-09-05 실측): /workspace 는 MooseFS FUSE 라 큰 파일 I/O 에서 파이썬이 멈춘다.
  가중치 캐시는 반드시 로컬 디스크(/root) 아래에 둔다.
★ lora 는 r2_face 의 **키**(캐시 식별자)이고, 실제 다운로드는 요청이 실어 보내는 presigned URL
  (`lora_url`)로만 한다 — 이 파드에는 R2 자격증명이 없다. 캐시에 있으면 URL 을 아예 안 쓴다.
  키를 그대로 파일 경로로 쓰지 않는다(경로 탈출) — 해시로 평탄화한다.
  받은 파일은 `lora_sha256` 과 대조하고, 다르면 지우고 400 을 돌려준다(엉뚱한 얼굴 방지).
  ★ presigned URL 은 로그에 남기지 않는다.
★ A40(44GiB)에는 파이프라인이 하나만 올라간다. LoRA 는 fuse 되므로 다른 키가 오면
  기존 백엔드를 버리고 새로 만든다(그 요청은 느리다). 프로덕션은 모델당 파드가 아니라
  **한 번에 한 사람**을 처리한다는 전제다.

★ 파드 사양(2026-09-10 A40 실측): **A40 에서는 안 뜬다.** 컨테이너 메모리 상한이 50GB
  (cgroup memory.max=49999998976)인데 Qwen-Image-Edit-2509 bf16 전체는 TE 16.6G +
  transformer 40.8G ≈ 58GB 라 QwenLocalBackend.pipeline() 의 from_pretrained 가
  OOM 으로 조용히 죽는다(트레이스백 없이 프로세스 소멸, GPU 사용량 0). cpu_offload=True 도
  같은 벽이다 — 오프로드는 모델을 CPU RAM 에 먼저 올린 뒤 작동한다.
  → 80GB 급 카드(A100 80G·H100)를 쓰거나, 학습 표본 스크립트(v6_kit/gen1_pod.py)처럼
    TE 를 먼저 올려 임베딩을 뽑고 해제한 뒤 transformer+VAE 를 올리는 **3단 분할**로
    QwenLocalBackend 를 고쳐야 한다. 서비스는 프롬프트가 요청마다 달라 TE 가 상주해야 하므로
    3단 분할은 요청마다 TE 를 다시 올리는 비용이 붙는다(그래서 80GB 카드가 유력).

★ 96GB 카드 실측(2026-09-10 · RTX PRO 6000 Blackwell Server Edition, 컨테이너 RAM 234GiB):
  HF 가중치 내려받기 390초(53.8GiB) · 파이프라인 적재 175초 · 적재 후 VRAM 54.1GiB(nvidia-smi 56GiB)
  · RAM 피크 94GiB · /healthz 17ms · 렌더 33.5초(1024², 25 step). 웜 3연속 33.5/33.5/33.4초 —
  요청 간 추가 절감 없음(모델이 이미 상주라 첫 요청부터 웜이다).
  ※ 적재 중에는 포트가 아예 안 열린다(lifespan 이 끝나야 uvicorn 이 listen) → 헬스 체크는
    "연결 거부 = 아직 적재 중"으로 읽어야 한다.
  ※ 로컬 QwenLocalBackend 직접 렌더와 이 서비스 경유 렌더가 **PNG sha256 동일**(픽셀 diff 0).

실행:
    FACE_RENDER_TOKEN=... FACE_RENDER_CACHE_DIR=/workspace/face_render/loras \
    uvicorn face_render_service:app --host 0.0.0.0 --port 8000
(R2 자격증명은 필요 없다 — 가중치는 요청이 실어 보내는 presigned URL 로만 받는다.)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import os
import pathlib
import threading
import time
import uuid
from contextlib import asynccontextmanager
from io import BytesIO
from urllib.parse import urlparse

from fastapi import FastAPI, Header, HTTPException
from PIL import Image
from pydantic import BaseModel, Field

import face_esrgan
from app.agents.face_identity_qwen import (
    RENDER_GUIDANCE,
    RENDER_NEGATIVE,
    RENDER_STEPS,
    QwenLocalBackend,
    load_base_pipeline,
)

log = logging.getLogger("face_render")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log.setLevel(logging.INFO)

#: 가중치 캐시. /workspace 금지 — 위 주석 참고.
CACHE_DIR = os.getenv("FACE_RENDER_CACHE_DIR", "/root/loras")
MODEL_ID = os.getenv("FACE_RENDER_MODEL_ID", "Qwen/Qwen-Image-Edit-2509")
DEVICE = os.getenv("FACE_RENDER_DEVICE", "cuda")
#: A40(48GiB) 처럼 bf16 전체(≈55GiB)가 한 번에 안 올라가는 카드에서 켠다 —
#: enable_model_cpu_offload() 로 transformer 만 GPU 에 두고 돌린다(느리지만 돈다).
CPU_OFFLOAD = (os.getenv("FACE_RENDER_CPU_OFFLOAD", "false").lower() == "true")
#: LoRA 를 GPU 에서 합친다 — 적재 214.7초 → 13.5초(실측은 face_identity_qwen.py 상단 주석).
#: 기본 on. false 가 탈출구다(결과 픽셀을 예전과 똑같이 만들고 싶을 때).
GPU_FUSE = (os.getenv("FACE_RENDER_GPU_FUSE", "true").lower() == "true")
#: 기동 때 **베이스 모델**을 올려 둔다(기본 on). LoRA 는 요청마다 붙는다(GPU 합치기 ≈3초).
#: 이게 없으면 캐시가 빈 새 파드는 첫 렌더가 끝나야 loaded=true 가 되고, 그동안 wait_for_backend·
#: 준비 칩·autoscale 이 "아직 안 떴다"고 본다(2026-09-11 실측). false 는 개발용 탈출구.
PRELOAD_BASE = (os.getenv("FACE_RENDER_PRELOAD_BASE", "true").lower() != "false")
#: 프로세스 기동 때 미리 붙여 둘 LoRA 키. 비우면 첫 요청이 LoRA 를 붙인다.
#: **캐시에 있는 키만** 의미가 있다(기동 시점에는 presigned URL 이 없다).
PRELOAD_LORA = os.getenv("FACE_RENDER_PRELOAD_LORA") or None
TOKEN = os.getenv("FACE_RENDER_TOKEN") or None
#: 배포된 코드가 어느 커밋인지. 볼륨에 올린 VERSION 파일이나 env 로 들어온다(관측용).
CODE_VERSION = (os.getenv("FACE_RENDER_CODE_VERSION")
                or (pathlib.Path(__file__).with_name("VERSION").read_text().strip()
                    if pathlib.Path(__file__).with_name("VERSION").exists() else "unknown"))
#: presigned 다운로드를 허용할 호스트 접미사. 다른 호스트로 유도당하지 않게 고정한다.
ALLOWED_LORA_HOST_SUFFIX = ".r2.cloudflarestorage.com"
DOWNLOAD_TIMEOUT = 300.0

#: 얼굴 크롭 확대 입력 상한. 호출자는 1024² 미만의 크롭만 보낸다(그 이상은 축소라 확대기를 안 부른다).
UPSCALE_MAX_SIDE = 1024
#: GPU 는 하나다 — 렌더를 직렬화한다. 동시 요청은 대기(타임아웃은 호출자 몫).
_RENDER_LOCK = threading.Lock()
#: LoRA 교체(파이프라인 재적재)를 직렬화한다. 렌더 자물쇠와 **중첩되지 않는다** —
#: 교체는 _backend() 안에서 끝나고 렌더는 그 뒤에 _RENDER_LOCK 을 잡는다.
_SWAP_LOCK = threading.RLock()
#: LoRA 키별 다운로드 자물쇠. 캐시가 빈 상태에서 동시 요청이 오면 **하나만 받고** 나머지는 그 파일을 쓴다.
_LORA_LOCKS: dict[str, threading.Lock] = {}
_LORA_LOCKS_GUARD = threading.Lock()
def initial_state() -> dict:
    """빈 상태. **키를 여기 한 곳에서만 정한다** — 테스트가 손으로 다시 적으면 키가 갈라져
    healthz 가 KeyError 로 죽는다(실제로 그랬다)."""
    return {"lora": None, "lora_sha256": None, "backend": None, "base": None,
            "loaded_at": None, "renders": 0, "swapping": None, "last_error": None}


#: base = LoRA 를 아직 안 붙인 베이스 파이프라인(기동 때 적재). backend 가 생기면 그 안으로 들어간다.
#: swapping = 지금 바꿔 끼우는 중인 키(관측용), last_error = 마지막 교체 실패 사유(관측용).
_state: dict = initial_state()


class RenderRequest(BaseModel):
    control_png: str
    prompt: str
    seed: int = 42
    steps: int = Field(default=RENDER_STEPS, ge=1, le=100)
    guidance_scale: float = RENDER_GUIDANCE
    negative_prompt: str = RENDER_NEGATIVE
    lora: str | None = None
    lora_url: str | None = None       # presigned GET. 캐시 히트면 없어도 된다
    lora_sha256: str | None = None    # 받은 파일 검증값(fm_model_loras.lora_sha256)


class RenderResponse(BaseModel):
    image_png: str
    seed: int
    lora: str | None
    ms: int


class UpscaleRequest(BaseModel):
    """얼굴 **크롭만** 키운다 — 사진 전체는 호출자가 보내지 않는다(옷 픽셀을 바꾸지 않으려고)."""

    image_png: str
    scale: int = Field(default=2, ge=2, le=face_esrgan.NATIVE_SCALE)


class UpscaleResponse(BaseModel):
    image_png: str
    scale: int
    ms: int


def _load_base() -> None:
    """베이스 파이프라인을 올려 _state["base"] 에 둔다. 이 뒤로 /healthz base_loaded=true."""
    t0 = time.perf_counter()
    _state["base"] = load_base_pipeline(MODEL_ID, DEVICE, cpu_offload=CPU_OFFLOAD, gpu_fuse=GPU_FUSE)
    log.info("base pipeline ready in %.1fs", time.perf_counter() - t0)


def _sweep_partials() -> None:
    """받다 만 임시 파일을 치운다. 이름이 요청마다 달라져서(uuid) 죽은 다운로드가 그냥 쌓인다."""
    try:
        for stale in pathlib.Path(CACHE_DIR).glob("*.part"):
            stale.unlink(missing_ok=True)
            log.info("removed stale partial %s", stale.name)
    except OSError:
        log.warning("could not sweep partial downloads", exc_info=True)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """기동 때 베이스를 올린다 — 첫 컷이 베이스 적재(수 분)를 물지 않고, 준비 판정이 첫 렌더를 기다리지 않게."""
    _sweep_partials()
    if PRELOAD_BASE:
        try:
            await asyncio.to_thread(_load_base)
        except Exception:  # noqa: BLE001 — 미리 올리기 실패해도 서비스는 뜬다(첫 요청이 재시도)
            log.exception("base preload failed")
    else:
        log.info("FACE_RENDER_PRELOAD_BASE=false — first request pays the base load")
    if PRELOAD_LORA:
        try:
            await asyncio.to_thread(_backend, PRELOAD_LORA)
        except Exception:  # noqa: BLE001
            log.exception("preload failed for %s", PRELOAD_LORA)
    yield


app = FastAPI(title="face-render", lifespan=_lifespan)


#: 토큰 최소 길이. 프록시 URL 이 매니페스트에 있으므로 이 엔드포인트는 사실상 공개 주소다 —
#: 짧은 토큰은 없는 것과 같다. secrets.token_urlsafe(32) 는 43자를 만든다.
MIN_TOKEN_LEN = 32


def _authorize(authorization: str | None) -> None:
    """토큰이 없거나 너무 짧으면 **닫는다**(fail-closed) — 약한 토큰으로 열지 않는다."""
    if not TOKEN:
        raise HTTPException(503, "FACE_RENDER_TOKEN not configured")
    if len(TOKEN) < MIN_TOKEN_LEN:
        raise HTTPException(503, f"FACE_RENDER_TOKEN too short (min {MIN_TOKEN_LEN})")
    expected = f"Bearer {TOKEN}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(401, "unauthorized")


def _cache_path(key: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    name = f"{hashlib.sha256(key.encode()).hexdigest()[:16]}_{os.path.basename(key)}"
    return os.path.join(CACHE_DIR, name)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _check_url(url: str) -> None:
    """https + 우리 R2 호스트만. 값은 예외 메시지에도 넣지 않는다."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise HTTPException(400, "lora_url must be https")
    host = (parsed.hostname or "").lower()
    if not host.endswith(ALLOWED_LORA_HOST_SUFFIX):
        raise HTTPException(400, f"lora_url host not allowed (expected *{ALLOWED_LORA_HOST_SUFFIX})")


def _cached(path: str) -> bool:
    return os.path.exists(path) and os.path.getsize(path) > 0


def _lora_lock(key: str) -> threading.Lock:
    with _LORA_LOCKS_GUARD:
        lock = _LORA_LOCKS.get(key)
        if lock is None:
            lock = _LORA_LOCKS[key] = threading.Lock()
        return lock


def _lora_file(key: str, url: str | None = None, sha256: str | None = None) -> str:
    """캐시 우선. 미스면 presigned URL 로 받아 sha256 을 대조한 뒤에만 캐시에 남긴다.

    ★ 키별 자물쇠 + 요청별 고유 임시 파일. 예전에는 캐시 미스 동시 요청이 **같은** `<path>.part`
      에 동시에 썼다: 내용이 섞여 sha 가 안 맞고(HTTP 400 "lora sha256 mismatch"), 한쪽의
      _unlink 가 다른 쪽이 os.replace 할 파일을 지워 500 이 났다.
      2026-09-14 20:33 셀러 잡 3ed6970e 의 얼굴컷 8장이 그렇게 400×2 · 500×2 로 죽었다
      (fm_model_loras.lora_sha256 과 R2 파일 sha 는 같았다 — 파일이 아니라 경합이었다).
    """
    path = _cache_path(key)
    if _cached(path):
        return path
    if not url:
        raise HTTPException(400, "lora not cached and no lora_url given")
    _check_url(url)
    import httpx

    with _lora_lock(key):
        # 기다리는 동안 먼저 들어온 요청이 받아 놨을 수 있다 — 그러면 그걸 쓴다(다운로드는 키당 1회).
        if _cached(path):
            return path
        return _download_lora(path, key, url, sha256, httpx)


def _download_lora(path: str, key: str, url: str, sha256: str | None, httpx) -> str:
    tmp = f"{path}.{uuid.uuid4().hex}.part"     # 요청마다 다른 이름 — 남의 임시 파일을 건드리지 않는다
    t0 = time.perf_counter()
    try:
        with httpx.stream("GET", url, timeout=DOWNLOAD_TIMEOUT, follow_redirects=False) as res:
            if res.status_code != 200:
                # 403/만료를 그대로 보여준다(URL 값은 빼고) — 발급 쪽을 봐야 하는 실패다.
                raise HTTPException(502, f"lora_url fetch failed: HTTP {res.status_code}")
            with open(tmp, "wb") as fh:
                for chunk in res.iter_bytes(1 << 20):
                    fh.write(chunk)
    except HTTPException:
        _unlink(tmp)
        raise
    except Exception as exc:
        _unlink(tmp)
        raise HTTPException(502, f"lora_url fetch failed: {type(exc).__name__}") from exc
    if sha256:
        got = _sha256_file(tmp)
        if got.lower() != sha256.strip().lower():
            _unlink(tmp)   # 검증 실패본을 캐시에 남기면 다음 요청이 그걸 쓴다
            raise HTTPException(400, "lora sha256 mismatch")
    os.replace(tmp, path)
    # 캐시 파일명은 키의 해시라 되돌릴 수 없다 → 옆에 키를 적어 둔다.
    # start.sh 가 이 파일을 읽어 재기동 때 PRELOAD 할 키를 정한다(캐시가 비면 PRELOAD 없이 뜬다).
    try:
        pathlib.Path(f"{path}.key").write_text(key)
    except OSError:
        log.warning("could not write cache key sidecar for %s", key)
    log.info("lora fetched key=%s (%.1fMB, %.1fs)", key,
             os.path.getsize(path) / 2**20, time.perf_counter() - t0)
    return path


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _free_cuda() -> None:
    try:
        import gc

        import torch

        gc.collect()
        torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001 — 정리 실패는 로드 실패가 아니다
        log.warning("cuda cache clear failed", exc_info=True)


def _recover_after_failed_swap(previous: QwenLocalBackend | None, prev: dict) -> None:
    """교체가 깨졌을 때 **준비 상태를 반드시 되돌린다**.

    왜 중요한가: 준비 판정(healthz_ready)은 base_loaded 를 본다. 예전에는 교체가 실패하면
    backend 도 base 도 None 으로 남아 base_loaded=false 가 영구가 됐고, 클라이언트는 렌더를
    보내지 못한 채 상한까지 기다리다 "파드가 600초 안에 뜨지 않았다" 로 끝났다(2026-09-14).
    """
    _state["base"] = None      # LoRA 가 절반쯤 융합됐을 수 있다 — 그 베이스는 재사용하지 않는다
    if previous is not None:
        _state.update(backend=previous, lora=prev["lora"], lora_sha256=prev["lora_sha256"],
                      loaded_at=prev["loaded_at"])
        log.warning("swap failed — 이전 LoRA(%s)로 계속 렌더한다", prev["lora"])
        return
    _free_cuda()
    try:
        _load_base()           # 되돌릴 backend 가 없다 — 최소한 base 는 다시 올려 둔다
    except Exception:  # noqa: BLE001
        log.exception("swap 실패 뒤 base 재적재도 실패했다 — healthz.last_error 를 보라")


def _backend(lora_key: str | None, lora_url: str | None = None,
             lora_sha256: str | None = None) -> QwenLocalBackend:
    """이 키의 백엔드. 다른 키가 오면 기존 파이프라인을 버린다(A40 에 둘은 안 올라간다).

    ★ 교체는 **전역으로 직렬화**하고, 기존 backend 는 **새 LoRA 파일을 확보·검증한 뒤에만** 버린다.
      예전 순서(먼저 버리고 그 다음 다운로드)는 다운로드가 400/502 로 깨지면 backend 도 base 도
      없는 상태를 남겼다 — 그 파드는 healthz base_loaded=false 로 영구히 굳었다.
    """
    if lora_key is None:
        raise HTTPException(400, "lora key required")
    if _state["lora"] == lora_key and _state["backend"] is not None:
        return _state["backend"]
    with _SWAP_LOCK:
        # 자물쇠를 기다리는 동안 다른 요청이 이미 이 키로 바꿔 놨을 수 있다.
        if _state["lora"] == lora_key and _state["backend"] is not None:
            return _state["backend"]
        _state["swapping"] = lora_key
        try:
            # ① 파일 먼저. 여기서 실패하면 지금 돌고 있는 backend 가 **그대로 산다**.
            try:
                lora_path = _lora_file(lora_key, lora_url, lora_sha256)
            except HTTPException as exc:
                _state["last_error"] = f"lora fetch failed: {exc.status_code} {exc.detail}"
                raise

            previous = _state["backend"]
            prev = {k: _state[k] for k in ("lora", "lora_sha256", "loaded_at")}
            if previous is not None:
                log.warning("swapping lora %s → %s (pipeline reload)", prev["lora"], lora_key)
                _state.update(backend=None, lora=None, lora_sha256=None, loaded_at=None)
                _free_cuda()

            # ② 기동 때 올려 둔 베이스가 있으면 그 위에 LoRA 만 붙는다(≈3초). 없으면 베이스부터 다시.
            t0 = time.perf_counter()
            try:
                backend = QwenLocalBackend(lora_path, model_id=MODEL_ID, device=DEVICE,
                                           cpu_offload=CPU_OFFLOAD, gpu_fuse=GPU_FUSE,
                                           base_pipe=_state["base"])
                _state["base"] = None   # 베이스는 backend 안으로 들어갔다
                backend.pipeline()      # 여기서 LoRA 를 붙인다(베이스가 없으면 베이스 적재까지 — 수 분)
            except Exception as exc:  # noqa: BLE001
                _state["last_error"] = f"pipeline load failed: {type(exc).__name__}"
                log.exception("pipeline load failed for %s", lora_key)
                _recover_after_failed_swap(previous, prev)
                raise HTTPException(503, f"pipeline load failed: {type(exc).__name__}") from exc

            # sha 는 레시피 해시에 들어간다(어떤 가중치로 도는 파드인지). 요청이 안 줬으면 None 그대로.
            _state.update(backend=backend, lora=lora_key, lora_sha256=lora_sha256,
                          loaded_at=time.time(), last_error=None)
            log.info("pipeline ready lora=%s in %.1fs", lora_key, time.perf_counter() - t0)
            return backend
        finally:
            _state["swapping"] = None


@app.get("/healthz")
def healthz() -> dict:
    return {
        "ok": True,
        # loaded = 이 LoRA 로 지금 렌더 가능. base_loaded = 베이스가 올라와 있어 어떤 LoRA 든 곧 붙일 수 있다.
        # 준비 판정(wait_for_backend·준비 칩·autoscale)은 base_loaded 를 본다 — face_identity.healthz_ready.
        "loaded": _state["backend"] is not None,
        "base_loaded": _state["base"] is not None or _state["backend"] is not None,
        "lora": _state["lora"],
        # 교체 중이면 그 키. 교체는 직렬화돼 있어 이 동안 온 렌더는 대기한다(실패가 아니다).
        "swapping": _state["swapping"],
        # 마지막 교체 실패 사유. 성공하면 지워진다 — 파드가 왜 옛 LoRA 로 도는지 여기서 보인다.
        "last_error": _state["last_error"],
        "renders": _state["renders"],
        "token_configured": bool(TOKEN) and len(TOKEN) >= MIN_TOKEN_LEN,
        "model_id": MODEL_ID,
        "cpu_offload": CPU_OFFLOAD,
        "gpu_fuse": GPU_FUSE,
        "code_version": CODE_VERSION,
        "cache_dir": CACHE_DIR,
        # 얼굴 크롭 확대기. available=false 면 호출자가 Lanczos 로 간다(컷은 그대로 나온다).
        "esrgan": face_esrgan.status(),
        # 이 파드가 어떤 상수로 도는가 — 컷 메타의 face_recipe 와 맞춰 본다(agents/face_recipe.py).
        "recipe": _recipe_id(),
    }


def _recipe_id() -> str | None:
    """레시피 해시. **관측용**이라 실패해도 파드는 계속 렌더한다(None 으로 비워 둔다).

    face_recipe 는 합성 상수를 face_identity 에서 가져오고 그쪽은 cv2 를 임포트한다 —
    파드 venv 에 없다고 렌더까지 못 하게 만들 이유가 없다.
    """
    try:
        from app.agents import face_recipe

        return face_recipe.recipe_id(face_recipe.recipe_fields(
            model_id=MODEL_ID, lora_sha256=_state.get("lora_sha256"),
            upscale_scope=(face_recipe.UPSCALE_SCOPE_FACE_CROP if face_esrgan.status()["available"]
                           else face_recipe.UPSCALE_SCOPE_OFF)))
    except Exception:  # noqa: BLE001
        log.warning("recipe id unavailable", exc_info=True)
        return None


#: 확대도 GPU 를 쓴다 — 렌더와 같은 자물쇠로 직렬화한다(동시에 돌면 둘 다 느려지고 VRAM 이 튄다).
@app.post("/upscale", response_model=UpscaleResponse)
def upscale(req: UpscaleRequest, authorization: str | None = Header(default=None)) -> UpscaleResponse:
    _authorize(authorization)
    esr = face_esrgan.get(DEVICE)
    if esr is None:
        # 503 = "이 파드는 못 한다". 호출자는 다시 묻지 않고 Lanczos 로 간다.
        raise HTTPException(503, f"esrgan unavailable ({face_esrgan.status()['reason']})")
    try:
        raw = base64.b64decode(req.image_png, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, "image_png must be base64") from exc
    with Image.open(BytesIO(raw)) as im:
        im.load()
        src = im.convert("RGB")
    if max(src.size) > UPSCALE_MAX_SIDE:
        raise HTTPException(400, f"image too large (max {UPSCALE_MAX_SIDE}px)")
    t0 = time.perf_counter()
    with _RENDER_LOCK:
        out = esr(src, int(req.scale))
    buf = BytesIO()
    out.save(buf, "PNG")
    ms = round((time.perf_counter() - t0) * 1000)
    log.info("upscale %sx%s ×%s %sms", src.width, src.height, req.scale, ms)
    return UpscaleResponse(image_png=base64.b64encode(buf.getvalue()).decode(),
                           scale=int(req.scale), ms=ms)


@app.post("/render", response_model=RenderResponse)
@app.post("/", response_model=RenderResponse)
def render(req: RenderRequest, authorization: str | None = Header(default=None)) -> RenderResponse:
    _authorize(authorization)
    try:
        raw = base64.b64decode(req.control_png, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, "control_png must be base64") from exc
    with Image.open(BytesIO(raw)) as im:
        im.load()
        control = im.convert("RGB")
    backend = _backend(req.lora, req.lora_url, req.lora_sha256)
    # 요청이 준 값이 정본 — 호출자(face_identity)가 RENDER_* 를 보낸다. 파드가 임의로 바꾸면
    # 같은 시드로도 그림이 달라져 로컬/원격 비교가 성립하지 않는다.
    backend.steps = int(req.steps)
    backend.guidance_scale = float(req.guidance_scale)
    backend.negative_prompt = req.negative_prompt
    t0 = time.perf_counter()
    with _RENDER_LOCK:
        out = backend.render(control, req.prompt, int(req.seed))
    buf = BytesIO()
    out.save(buf, "PNG")
    _state["renders"] += 1
    ms = round((time.perf_counter() - t0) * 1000)
    log.info("render seed=%s steps=%s %sms", req.seed, req.steps, ms)
    return RenderResponse(image_png=base64.b64encode(buf.getvalue()).decode(),
                          seed=int(req.seed), lora=req.lora, ms=ms)
