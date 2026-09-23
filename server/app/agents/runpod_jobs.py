"""RunPod 서버리스 잡 뒷정리 — 결과를 더는 기다리지 않는 잡은 RunPod 에서도 끈다(2026-09-23).

얼굴 패스(face_identity.ServerlessFaceBackend)와 각도 교체(face_angle_swap.ServerlessBackend)는
잡을 /run 으로 넣고 /status 를 폴링한다. 예전에는 우리가 먼저 손을 떼도(폴링 제한 시간 초과 ·
상태 조회 연속 실패 · 잡 취소) RunPod 쪽 잡은 그대로 돌았다 — 결과를 버릴 GPU 시간을 끝까지
냈다. 그리고 폴링은 asyncio.to_thread 스레드 안에서 도는데, 코루틴이 취소돼도 스레드는
멈추지 않아 900초 동안 상태를 계속 물었다(감사 7번).

그래서 두 가지를 한다:
  · 손을 떼는 모든 자리에서 POST /cancel/{job_id} 를 보낸다(best-effort — 실패해도 예외 없음).
  · 코루틴이 취소되면 폴링 스레드에 알린다(to_thread → abandoned()/wait()). 스레드는 다음
    대기에서 바로 깨어나 잡을 끄고 빠져나온다.

폴링 스레드에 알리는 길은 contextvars 다. asyncio.to_thread 는 호출 시점의 컨텍스트를 복사해
스레드에서 돌리므로, 여기서 심은 Event 를 스레드가 그대로 본다. 백엔드 객체는 프로세스당
캐시라(face_identity._BACKENDS) 여러 컷이 같이 쓴다 — 신호를 백엔드에 두면 한 컷의 취소가
남의 컷까지 끈다. 컨텍스트는 호출(컷)마다 따로다.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
import time

log = logging.getLogger("wearless.runpod_jobs")

#: 지금 스레드의 호출자가 결과를 포기했는가. None 이면 포기 신호가 없는 호출(동기 테스트 등).
_ABANDON: contextvars.ContextVar[threading.Event | None] = contextvars.ContextVar(
    "runpod_job_abandon", default=None)


def abandoned() -> bool:
    """호출자(코루틴)가 결과를 포기했는가 — 폴링 스레드가 새 요청을 보내기 전에 본다."""
    event = _ABANDON.get()
    return event is not None and event.is_set()


def wait(seconds: float) -> bool:
    """폴링 간격만큼 쉰다. 호출자가 포기하면 바로 깨어난다. 포기했으면 True."""
    event = _ABANDON.get()
    if event is None:
        time.sleep(seconds)
        return False
    return event.wait(seconds)


def cancel(client, job_id: str, *, what: str) -> None:
    """RunPod 잡을 끈다(POST /cancel/{job_id}). **절대 예외를 올리지 않는다** — 끄기 실패가
    컷의 실패 사유를 덮어쓰면 안 되고, 버려진 폴링 스레드에서는 받을 사람도 없다.

    client 는 엔드포인트 주소(…/v2/{endpoint_id})를 base_url 로 가진 httpx.Client 다.
    요청 본문은 없다. 로그에는 잡 id 와 상태 코드만 남긴다(키·이미지 없음).
    """
    if not job_id:
        return
    try:
        res = client.post(f"/cancel/{job_id}")
        status = getattr(res, "status_code", None)
        if status != 200:
            log.warning("runpod %s cancel %s http %s", what, job_id, status)
            return
        log.info("runpod %s job %s cancelled", what, job_id)
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.warning("runpod %s cancel %s failed: %s", what, job_id, type(exc).__name__)


async def to_thread(fn, /, *args, **kwargs):
    """asyncio.to_thread 와 같다. 다만 이 코루틴이 취소되면 스레드에 포기 신호를 준다.

    스레드는 그 신호를 보고(abandoned/wait) RunPod 잡을 끄고 빠져나온다. 취소 자체는
    그대로 위로 올라간다 — 워커 종료·잡 취소를 삼키지 않는다.
    """
    event = threading.Event()
    token = _ABANDON.set(event)
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except asyncio.CancelledError:
        event.set()
        raise
    finally:
        _ABANDON.reset(token)
