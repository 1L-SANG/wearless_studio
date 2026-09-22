"""인물 LoRA 얼굴 렌더 — RunPod Serverless 핸들러 (파드 서비스의 서버리스 판).

`face_render_service.py` 가 파드에서 하던 일을 그대로 한다. **구현을 복제하지 않는다** —
그 모듈의 함수를 그대로 부른다. 두 경로가 다른 코드를 돌면 "파드에서는 되는데 서버리스에서는
다르게 나온다"가 생기고, 그건 픽셀 문제라 로그로 못 찾는다.

파드와 다른 것은 셋뿐이다:

  ① **입구**  HTTP 라우트(/render·/upscale·/healthz) → 하나의 `handler(event)`.
     `event["input"]["op"]` 가 무엇을 할지 고른다(없으면 render — 대부분의 요청이 그거다).
  ② **가중치**  파드는 재시작마다 컨테이너 디스크가 비어 53.8GiB 를 다시 받았다. 여기서는
     네트워크 볼륨(`/runpod-volume`)에 두고 HF_HOME 을 그리로 돌린다. 워커가 몇 번을 뜨든
     받는 일은 없다.
  ③ **인증**  RunPod 이 엔드포인트 앞에서 API 키로 막는다. 파드처럼 자체 Bearer 를 두지
     않는다 — 토큰이 두 군데 있으면 하나가 틀려도 증상이 같아서 못 가른다.

★ **베이스 파이프라인은 import 시점에 올린다.** `runpod.serverless.start()` 뒤로 미루면
  워커가 "준비됨"으로 잡을 받은 뒤에 수십 초를 적재에 쓰고, 그 시간이 셀러 대기로 간다.
  import 중에 죽으면 워커가 아예 안 뜨는 게 맞다 — 가중치 없이 잡을 받아 봐야 전부 실패다.

★ LoRA 는 요청이 presigned URL 로 실어 온다(파드와 같은 계약). 워커에 R2 자격증명은 없다.
  캐시는 **컨테이너 로컬**(`/root/loras`)에 둔다 — 볼륨에 두면 워커 여럿이 같은 파일을
  동시에 쓰는데, 파드 코드의 다운로드 자물쇠는 프로세스 안에서만 유효하다. LoRA 는 ~200MB라
  워커마다 한 번 받는 편이 공유 볼륨 경합보다 싸고 안전하다.

★ 응답은 파드의 JSON 과 **필드가 같다**. 서버 쪽 ServerlessFaceBackend 가 HttpFaceBackend 와
  같은 표면을 갖도록 — 두 백엔드가 다른 모양을 돌려주면 호출부가 갈라진다.
"""

from __future__ import annotations

import logging
import os
import pathlib
import traceback

# ── 가중치 자리 ───────────────────────────────────────────────────────────────
# import 보다 먼저 정한다. face_render_service 가 임포트되는 순간 diffusers 가
# HF_HOME 을 읽어 버리므로 그 뒤에 바꾸면 늦는다.
#
# ★ 베이스 이미지가 HF_HOME 을 박아 두면 그 값이 이긴다 — 파드에서 실제로 그랬다
#   (2026-09-13: /workspace 20GB 볼륨으로 53.8GiB 가 향해 Errno 122). 여기서는 `:-` 가 아니라
#   **덮어쓴다**. 볼륨이 안 붙은 개발 실행만 FACE_RENDER_HF_HOME 으로 빠져나간다.
_VOLUME = os.getenv("FACE_RENDER_VOLUME_ROOT", "/runpod-volume")
os.environ["HF_HOME"] = os.getenv("FACE_RENDER_HF_HOME") or f"{_VOLUME}/hf"
# xet 전송은 끈다 — huggingface_hub 1.x 의 기본 경로인데 54GB 수신에서 35파일 중 29개가
# `File reconstruction error` 로 터졌다(파드 실측). 끄면 같은 용량을 114초에 받는다.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
# 볼륨에 가중치가 이미 있으면 네트워크를 아예 안 탄다. 없으면(첫 워커) 받는다 —
# 그래서 기본은 오프라인이 아니다. 볼륨 채우기는 1번 단계가 미리 끝내 둔다.
os.environ.setdefault("FACE_RENDER_CACHE_DIR", "/root/loras")

log = logging.getLogger("face_render_serverless")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

import runpod  # noqa: E402 — HF_HOME 을 정한 뒤에 올린다

import face_render_service as svc  # noqa: E402 — 같은 이유


#: 이 워커가 올라온 뒤 처리한 잡 수(관측용). 서버리스는 워커가 자주 바뀌어서
#: "이 워커가 웜인가"를 이 값으로 본다.
_JOBS = 0


def _preload() -> None:
    """베이스 파이프라인 적재. 실패하면 예외를 그대로 올려 워커를 안 띄운다."""
    pathlib.Path(svc.CACHE_DIR).mkdir(parents=True, exist_ok=True)
    svc._sweep_partials()
    svc._load_base()


if os.getenv("FACE_RENDER_PRELOAD_BASE", "true").lower() != "false":
    _preload()


def _render(payload: dict) -> dict:
    return svc.run_render(svc.RenderRequest(**payload)).model_dump()


def _upscale(payload: dict) -> dict:
    return svc.run_upscale(svc.UpscaleRequest(**payload)).model_dump()


def _healthz(_payload: dict) -> dict:
    body = dict(svc.healthz())
    body["jobs"] = _JOBS
    return body


_OPS = {"render": _render, "upscale": _upscale, "healthz": _healthz}


def handler(event: dict) -> dict:
    """RunPod 잡 하나. 실패는 **예외로 올리지 않고** {"error": ...} 로 돌려준다.

    예외를 올리면 RunPod 은 잡을 FAILED 로만 표시하고 우리 쪽에는 사유가 안 온다. 호출부가
    "왜 실패했는지"를 알아야 폴백 원장(gate_failed · backend_error)이 갈린다.
    ★ 사유 문자열에 presigned URL·토큰·얼굴 바이트를 싣지 않는다(파드와 같은 규칙).
    """
    global _JOBS

    payload = (event or {}).get("input") or {}
    op = str(payload.pop("op", "render") or "render")
    fn = _OPS.get(op)
    if fn is None:
        return {"error": f"unknown op: {op[:40]}"}
    try:
        out = fn(payload)
    except svc.HTTPException as exc:  # 파드가 4xx/5xx 로 답하던 자리
        log.warning("face render %s rejected: %s", op, str(exc.detail)[:120])
        return {"error": str(exc.detail)[:200], "status": exc.status_code}
    except Exception as exc:  # noqa: BLE001 — 워커를 죽이지 않고 이 잡만 실패시킨다
        log.error("face render %s failed: %s", op, type(exc).__name__)
        log.debug("%s", traceback.format_exc())
        return {"error": f"{type(exc).__name__}"}
    _JOBS += 1
    return out


runpod.serverless.start({"handler": handler})
