"""인물 LoRA 얼굴 렌더 서비스 — HttpFaceBackend 의 서버 쪽 반쪽 (GPU 파드 전용, 단일 파일).

계약은 app/agents/face_identity.py 의 HttpFaceBackend.render 하나다:

    POST /render  {control_png(b64), prompt, seed, steps, guidance_scale, negative_prompt, lora}
               →  {image_png(b64)}
    GET  /healthz  인증 없음. 파이프라인이 올라와 있는지·어떤 LoRA 가 물려 있는지.

렌더 자체는 app/agents/face_identity_qwen.QwenLocalBackend 를 그대로 쓴다 — 파드와 로컬이
같은 코드를 돌아야 "HTTP 로 바꿨더니 그림이 달라졌다"가 생기지 않는다(C-3 바이트 비교의 근거).

★ 파드 규칙(2026-09-05 실측): /workspace 는 MooseFS FUSE 라 큰 파일 I/O 에서 파이썬이 멈춘다.
  가중치 캐시는 반드시 로컬 디스크(/root) 아래에 둔다.
★ lora 는 r2_face 의 **키**다. 첫 요청 때 받아서 캐시하고, 그 다음부터는 디스크에서 쓴다.
  키를 그대로 파일 경로로 쓰지 않는다(경로 탈출) — 해시로 평탄화한다.
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
    FACE_RENDER_TOKEN=... R2_ENDPOINT=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... \
    R2_FACE_BUCKET=wearless-face uvicorn face_render_service:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from io import BytesIO

from fastapi import FastAPI, Header, HTTPException
from PIL import Image
from pydantic import BaseModel, Field

from app.agents.face_identity_qwen import (
    RENDER_GUIDANCE,
    RENDER_NEGATIVE,
    RENDER_STEPS,
    QwenLocalBackend,
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
#: 프로세스 기동 때 미리 올려 둘 LoRA 키. 비우면 첫 요청이 로드 비용(수 분)을 문다.
PRELOAD_LORA = os.getenv("FACE_RENDER_PRELOAD_LORA") or None
TOKEN = os.getenv("FACE_RENDER_TOKEN") or None
BUCKET = os.getenv("R2_FACE_BUCKET") or None

#: GPU 는 하나다 — 렌더를 직렬화한다. 동시 요청은 대기(타임아웃은 호출자 몫).
_RENDER_LOCK = threading.Lock()
_state: dict = {"lora": None, "backend": None, "loaded_at": None, "renders": 0}


class RenderRequest(BaseModel):
    control_png: str
    prompt: str
    seed: int = 42
    steps: int = Field(default=RENDER_STEPS, ge=1, le=100)
    guidance_scale: float = RENDER_GUIDANCE
    negative_prompt: str = RENDER_NEGATIVE
    lora: str | None = None


class RenderResponse(BaseModel):
    image_png: str
    seed: int
    lora: str | None
    ms: int


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """기동 때 파이프라인을 미리 올린다 — 첫 컷이 로드 비용(수 분)을 물지 않게."""
    if PRELOAD_LORA:
        try:
            await asyncio.to_thread(_backend, PRELOAD_LORA)
        except Exception:  # noqa: BLE001 — 미리 올리기 실패해도 서비스는 뜬다(첫 요청이 재시도)
            log.exception("preload failed for %s", PRELOAD_LORA)
    else:
        log.info("no FACE_RENDER_PRELOAD_LORA — first request pays the load")
    yield


app = FastAPI(title="face-render", lifespan=_lifespan)


def _authorize(authorization: str | None) -> None:
    """토큰이 설정돼 있지 않으면 **닫는다**(fail-closed) — 열린 GPU 엔드포인트를 만들지 않는다."""
    if not TOKEN:
        raise HTTPException(503, "FACE_RENDER_TOKEN not configured")
    expected = f"Bearer {TOKEN}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(401, "unauthorized")


def _lora_file(key: str) -> str:
    """r2_face 키 → 로컬 캐시 파일. 없으면 받아서 저장하고, 있으면 그대로 쓴다."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    name = f"{hashlib.sha256(key.encode()).hexdigest()[:16]}_{os.path.basename(key)}"
    path = os.path.join(CACHE_DIR, name)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    if not BUCKET:
        raise HTTPException(503, "R2_FACE_BUCKET not configured")
    import boto3

    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    t0 = time.perf_counter()
    tmp = f"{path}.part"
    s3.download_file(BUCKET, key, tmp)
    os.replace(tmp, path)   # 부분 파일이 캐시로 둔갑하지 않게
    log.info("lora downloaded %s (%.1fMB, %.1fs)", key,
             os.path.getsize(path) / 2**20, time.perf_counter() - t0)
    return path


def _backend(lora_key: str | None) -> QwenLocalBackend:
    """이 키의 백엔드. 다른 키가 오면 기존 파이프라인을 버린다(A40 에 둘은 안 올라간다)."""
    if lora_key is None:
        raise HTTPException(400, "lora key required")
    if _state["lora"] == lora_key and _state["backend"] is not None:
        return _state["backend"]
    if _state["backend"] is not None:
        log.warning("swapping lora %s → %s (pipeline reload)", _state["lora"], lora_key)
        _state.update(backend=None, lora=None, loaded_at=None)
        try:
            import gc

            import torch

            gc.collect()
            torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001 — 정리 실패는 로드 실패가 아니다
            log.warning("cuda cache clear failed", exc_info=True)
    backend = QwenLocalBackend(_lora_file(lora_key), model_id=MODEL_ID, device=DEVICE,
                               cpu_offload=CPU_OFFLOAD)
    t0 = time.perf_counter()
    backend.pipeline()          # 여기서 실제 로드가 일어난다(수 분)
    _state.update(backend=backend, lora=lora_key, loaded_at=time.time())
    log.info("pipeline ready lora=%s in %.1fs", lora_key, time.perf_counter() - t0)
    return backend


@app.get("/healthz")
def healthz() -> dict:
    return {
        "ok": True,
        "loaded": _state["backend"] is not None,
        "lora": _state["lora"],
        "renders": _state["renders"],
        "token_configured": bool(TOKEN),
        "bucket": BUCKET,
        "model_id": MODEL_ID,
        "cpu_offload": CPU_OFFLOAD,
    }


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
    backend = _backend(req.lora)
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
