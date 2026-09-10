"""얼굴 패스 GPU(RunPod 파드) 온디맨드 기동/종료 — sam2 선례의 RunPod 판.

판정(want)·reconciler 는 sam2 것을 그대로 쓴다(services/sam_autoscale.want_count,
workers/sam_autoscaler.SamAutoscaler). 다른 건 **무엇을 켜고 끄는가** 하나뿐이라
어댑터만 갈아 끼운다: ECS UpdateService → RunPod pod start/stop.

수요 = "지금 얼굴 패스가 필요한 잡". 착장 컷을 만드는 잡(editor_image·detail_page) 중
**켜진 LoRA 가 있는 등록자**의 잡만 센다. 이 조건이 없으면 가상모델 잡 하나가 GPU 를
24/7 켜 둔다(얼굴 패스는 fm_model_loras 행이 있어야만 걸린다 — cut_generator 참고).

★ RunPod API 키는 **서버 env 에만** 둔다. 파드에는 올리지 않는다 — 파드가 자기 계정을
  조작할 수 있게 되는 순간 유휴 종료가 안전장치가 아니라 사고 경로가 된다.
★ REST 호출에는 User-Agent 를 반드시 붙인다. 2026-09-08 야간 워치독의 stop 이 UA 없이
  거부돼 파드가 53분 더 돌았다(그 회차 실측).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from app.services.sam_autoscale import DemandSnapshot, ServiceState

log = logging.getLogger("wearless.face_autoscale")

#: 얼굴 패스를 돌릴 수 있는 잡. 이 둘만 착장 컷을 만든다.
FACE_KINDS = ("editor_image", "detail_page")

RUNPOD_API_BASE = "https://rest.runpod.io/v1"
#: UA 없는 요청이 거부된 실측이 있다(위 주석) — 고정 문자열로 박아 둔다.
USER_AGENT = "wearless-face-autoscale/1"
REQUEST_TIMEOUT = 15.0

#: 파드가 "켜져 있다"고 볼 상태. RunPod 는 시작 중에도 RUNNING 을 늦게 준다.
_RUNNING = ("RUNNING",)
_PENDING = ("CREATED", "RESTARTING", "STARTING", "PENDING")


@dataclass(frozen=True)
class RunpodTarget:
    pod_id: str


async def face_demand_snapshot(conn) -> DemandSnapshot:
    """얼굴 패스가 필요한 잡의 수요. fm_model_loras 가 없으면 항상 0(마이그 미적용 환경).

    payload 의 `_facemarket.modelId` 가 켜진 LoRA 를 가진 등록자인 잡만 센다.
    last_upload_at 은 이 워크로드에 해당 신호가 없어 None — want 판정은 활성 잡과
    마지막 종료 시각 두 신호로만 돈다(sam_autoscale.want_running 참고).
    """
    async with conn.cursor() as cur:
        await cur.execute("select to_regclass('public.fm_model_loras') as t")
        row = await cur.fetchone() or {}
        if not row.get("t"):
            return DemandSnapshot(0, None, None)
        await cur.execute(
            "with enabled as ("
            "  select model_id::text as id from fm_model_loras where enabled and status = 'ready'"
            ") "
            "select "
            "  (select count(*) from jobs where kind = any(%s) "
            "     and status in ('pending', 'running') "
            "     and payload -> '_facemarket' ->> 'modelId' in (select id from enabled)"
            "  ) as active_face_jobs, "
            "  (select max(finished_at) from jobs where kind = any(%s) "
            "     and finished_at is not null "
            "     and payload -> '_facemarket' ->> 'modelId' in (select id from enabled)"
            "  ) as last_face_finished_at",
            (list(FACE_KINDS), list(FACE_KINDS)),
        )
        row = await cur.fetchone() or {}
    return DemandSnapshot(
        active_sam_jobs=int(row.get("active_face_jobs") or 0),
        last_sam_finished_at=row.get("last_face_finished_at"),
        last_upload_at=None,
    )


class RunpodAutoscaleAdapter:
    """SamAutoscaleAdapter 와 같은 표면(discover/describe/set_desired/notify)의 RunPod 판.

    off 면 HTTP 클라이언트를 만들지 않는다. 예외는 삼키지 않고 올린다 — reconciler 가
    자기 맥락에서 삼킨다(sam 어댑터와 같은 계약).
    """

    def __init__(self, settings, *, enabled_attr="face_autoscale", client=None):
        self._settings = settings
        self.enabled = getattr(settings, enabled_attr, "off") == "on"
        self._pod_id = (getattr(settings, "face_runpod_pod_id", None) or "").strip() or None
        self._api_key = (getattr(settings, "face_runpod_api_key", None) or "").strip() or None
        self._client = client
        self._target: RunpodTarget | None = None
        self._now = lambda: datetime.now(timezone.utc)

    # ── HTTP ──
    def _http(self):
        if self._client is not None:
            return self._client
        import httpx

        self._client = httpx.Client(
            base_url=RUNPOD_API_BASE,
            timeout=REQUEST_TIMEOUT,
            headers={"Authorization": f"Bearer {self._api_key}",
                     "User-Agent": USER_AGENT,
                     "Content-Type": "application/json"},
        )
        return self._client

    def _get_sync(self, path: str) -> dict:
        res = self._http().get(path)
        res.raise_for_status()
        return res.json()

    def _post_sync(self, path: str) -> dict:
        res = self._http().post(path)
        res.raise_for_status()
        return res.json() if res.content else {}

    # ── 탐색 ──
    async def discover(self) -> RunpodTarget | None:
        """파드 id 는 설정으로 받는다 — 이름으로 고르면 계정의 다른 파드를 끌 수 있다."""
        if not self.enabled:
            return None
        if self._target is not None:
            return self._target
        if not self._pod_id or not self._api_key:
            log.error("face autoscale: FACE_RUNPOD_POD_ID / RUNPOD_API_KEY not configured")
            return None
        self._target = RunpodTarget(self._pod_id)
        return self._target

    def forget_target(self) -> None:
        self._target = None

    # ── 상태 ──
    async def describe(self, target: RunpodTarget) -> ServiceState:
        pod = await asyncio.to_thread(self._get_sync, f"/pods/{target.pod_id}")
        status = str(pod.get("desiredStatus") or pod.get("status") or "").upper()
        running = 1 if status in _RUNNING else 0
        pending = 1 if status in _PENDING else 0
        # RunPod 은 "desired" 개념이 없다 — 켜져 있거나(=1) 꺼져 있거나(=0) 다.
        desired = 1 if (running or pending) else 0
        started = pod.get("lastStartedAt") or pod.get("startedAt")
        return ServiceState(desired=desired, running=running, pending=pending,
                            oldest_started_at=_parse_ts(started) if running else None)

    async def set_desired(self, target: RunpodTarget, count: int) -> None:
        action = "start" if int(count) > 0 else "stop"
        await asyncio.to_thread(self._post_sync, f"/pods/{target.pod_id}/{action}")
        log.info("face autoscale: pod %s %s", target.pod_id, action)

    async def notify(self, subject: str, body: str) -> None:
        # SNS 토픽을 따로 두지 않는다 — 크리티컬 로그가 이미 Slack 으로 간다.
        log.error("face autoscale alert: %s — %s", subject, body)


def _parse_ts(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
