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
★ 재시작 실측(2026-09-10): stop 반영 2초 · start 후 SSH 40초. **컨테이너 디스크는 초기화된다** —
  코드·가중치·HF 캐시(54GiB)가 전부 사라지고 서비스도 자동으로 뜨지 않는다.
  TCP 포트 매핑도 재할당된다(22 번이 17500 → 17667). 반면 HTTP 프록시 주소
  `https://<podId>-8000.proxy.runpod.net` 은 파드 id 기반이라 재시작 후에도 그대로다
  → FACE_IDENTITY_BACKEND_URL 은 반드시 프록시 형태로 둔다.
"""

from __future__ import annotations

import asyncio
import logging
import time
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

#: 파드가 "켜져 있다"고 볼 상태.
_RUNNING = ("RUNNING",)
_PENDING = ("CREATED", "RESTARTING", "STARTING", "PENDING")

#: ★ 2026-09-10 실측(파드 nmtqyoxfpupvaa · RTX PRO 6000): REST GET /v1/pods/{id} 의 `runtime` 은
#: 컨테이너가 **살아서 서비스까지 하고 있는 동안에도 계속 null** 이었다. desiredStatus 는 요청한
#: 상태라 컨테이너가 죽어 있어도 RUNNING 이다(파드 2대가 그 상태로 붙잡혀 있었다). 즉 이 API 만으로는
#: "지금 진짜 떠 있는가"를 알 수 없다 → 렌더 서비스의 /healthz 를 직접 찔러야 한다.
#: health_url 이 없으면 desiredStatus 로 폴백하고, 그 사실을 running 판정에 그대로 반영한다.
HEALTH_TIMEOUT = 5.0

#: 셀러에게 보여 줄 콜드스타트 예상(분). 실측에서 온다 — 부팅 ~1분 + 가중치 + 적재.
#: 2026-09-10 실측 9.6분(pip 13s + HF 390s + 적재 175s)이 기준선이고, hf_transfer·GPU fuse 로
#: 줄어들면 이 값을 실측으로 다시 내린다.
COLD_START_ETA_MINUTES = 4


#: 카드 우선순위. 60GB 미만은 쓰지 않는다 — A40(48GB)에서는 모델이 아예 안 올라간다(2026-09-10 실측).
#: 2026-09-11 재고 실측(SECURE, 생성 시도): PRO 6000 "no instances" · A100 "no instances" ·
#: H100 만 실제로 잡혔다. A100 은 값이 싸지만 그날 한 번도 못 띄웠다 — 못 뜨는 카드를 2순위에
#: 두면 재고 없는 순간마다 한 번씩 더 헛돈다. 그래서 H100 을 A100 보다 앞에 둔다.
GPU_PRIORITY: tuple[tuple[str, float], ...] = (
    ("NVIDIA RTX PRO 6000 Blackwell Server Edition", 2.09),
    ("NVIDIA H100 80GB HBM3", 3.49),
    ("NVIDIA A100 80GB PCIe", 1.59),
)
#: 새 파드 사양. 볼륨은 쓰지 않는다(2026-09-10 실측: 볼륨 적재 472초로 이득 없음).
POD_IMAGE = "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404"
POD_DISK_GB = 100
POD_PORTS = ("8000/http", "22/tcp")
#: 파드 부팅 훅 — 이미지의 /start.sh 가 /pre_start.sh 를 부르고, 그게 bootstrap → start 를 잇는다.
#:
#: ★ **새로 만든 파드에는 그 pre_start.sh 조차 없다.** 컨테이너 디스크는 매번 비어 있고 코드는
#:   R2 묶음에만 있다. 예전 args 는 없는 파일을 복사하려다 조용히 실패해서 bootstrap 이 한 번도
#:   돌지 않았다 — 자동 생성 파드는 켜지기만 하고 서비스는 뜨지 않았다(2026-09-11 실측:
#:   R2 face_render/ 객체 0개인 것과 별개로, 있어도 못 받는 상태였다).
#:   그래서 여기서 **묶음을 직접 받아** 부팅 스크립트를 깔고 그다음 이미지의 start.sh 로 넘긴다.
#:   - 호스트는 우리 R2 만 허용(bootstrap.sh 와 같은 규칙), 내용은 sha256 으로 검증한다.
#:   - CODE_SHA/VERSION 을 같이 써 두면 뒤이어 도는 bootstrap.sh 가 "최신"으로 보고 건너뛴다.
#:   - 실패해도 exec /start.sh 는 그대로 — ssh 는 뜨고 사람이 들어가 볼 수 있다.
POD_ARGS = (
    "bash -c '"
    "R=/root/face_render; mkdir -p \"$R\"; "
    "if [ ! -x \"$R/pre_start.sh\" ] && [ -n \"${CODE_TARBALL_URL:-}\" ] "
    "&& [ -n \"${CODE_SHA256:-}\" ]; then "
    "case \"$CODE_TARBALL_URL\" in https://*.r2.cloudflarestorage.com/*) "
    "curl -fsSL --max-time 120 -o /tmp/face_render.tgz \"$CODE_TARBALL_URL\" "
    "&& [ \"$(sha256sum /tmp/face_render.tgz | cut -d\" \" -f1)\" = \"$CODE_SHA256\" ] "
    "&& tar --no-same-owner -xzf /tmp/face_render.tgz -C \"$R\" "
    "&& cp -f \"$R\"/deploy/*.sh \"$R\"/ && chmod +x \"$R\"/*.sh "
    "&& echo \"$CODE_SHA256\" > \"$R/CODE_SHA\" && echo \"$CODE_SHA256\" > \"$R/VERSION\"; "
    "esac; fi; "
    "rm -f /tmp/face_render.tgz; "
    "cp -f \"$R/pre_start.sh\" /pre_start.sh 2>/dev/null || true; "
    "exec /start.sh'"
)
#: 토큰은 RunPod Secret 참조로만 넣는다 — 값이 API 요청·응답·로그 어디에도 실리지 않는다.
POD_TOKEN_REF = "{{ RUNPOD_SECRET_face_render_token }}"


#: 코드 묶음 R2 키. CI 가 배포할 때마다 그 커밋 sha 로 올린다(deploy-server.yml).
CODE_TARBALL_KEY_FMT = "face_render/{sha}.tgz"
#: 코드 URL 만료. 파드가 켜지면서 한 번 받으면 끝이라 짧게 둔다.
CODE_URL_EXPIRES_S = 900
#: 코드 묶음이 없다는 알림의 디바운스 — 60초 주기마다 같은 말을 반복하지 않는다.
CODE_ALERT_DEBOUNCE_SECONDS = 1800


def code_tarball_key(sha: str) -> str:
    return CODE_TARBALL_KEY_FMT.format(sha=sha)


def pod_backend_url(pod_id: str | None) -> str | None:
    """파드 id → 렌더 URL. 프록시 주소는 id 기반이라 재시작해도 그대로다."""
    pod_id = (pod_id or "").strip()
    return f"https://{pod_id}-8000.proxy.runpod.net/render" if pod_id else None


@dataclass(frozen=True)
class RunpodTarget:
    pod_id: str


class FaceRenderPodStore:
    """현재 파드 id 의 DB 정본(fm_face_render_pod). 실패는 삼키지 않고 올린다 —
    어댑터가 그걸 보고 설정값으로 폴백할지 정한다."""

    def __init__(self, pool):
        self._pool = pool

    async def get_active(self) -> str | None:
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("select to_regclass('public.fm_face_render_pod') as t")
            if not (await cur.fetchone() or {}).get("t"):
                return None            # 마이그 미적용 환경 — 설정값으로 간다
            await cur.execute(
                "select pod_id from fm_face_render_pod where retired_at is null "
                "order by created_at desc limit 1")
            row = await cur.fetchone()
        return (row or {}).get("pod_id")

    async def set_active(self, pod_id: str, gpu_type: str) -> None:
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "update fm_face_render_pod set retired_at = now() "
                "where retired_at is null and pod_id <> %s", (pod_id,))
            await cur.execute(
                "insert into fm_face_render_pod (pod_id, gpu_type) values (%s, %s) "
                "on conflict (pod_id) do update set retired_at = null, gpu_type = excluded.gpu_type",
                (pod_id, gpu_type))
            await conn.commit()

    async def retire(self, pod_id: str) -> None:
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "update fm_face_render_pod set retired_at = now() "
                "where pod_id = %s and retired_at is null", (pod_id,))
            await conn.commit()


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
    # 세 번째 신호 = **워밍 핑**(셀러가 FaceMarket 모델을 고른 순간). SAM 의 last_upload_at 자리를
    # 그대로 쓴다 — "곧 필요해진다"를 미리 알리는 같은 성격의 신호다. 이게 없으면 셀러는 첫 컷에서
    # 콜드스타트를 그대로 기다린다.
    ping = None
    async with conn.cursor() as cur:
        await cur.execute("select to_regclass('public.fm_face_warm_pings') as t")
        if (await cur.fetchone() or {}).get("t"):
            await cur.execute("select max(pinged_at) as at from fm_face_warm_pings")
            ping = (await cur.fetchone() or {}).get("at")
    return DemandSnapshot(
        active_sam_jobs=int(row.get("active_face_jobs") or 0),
        last_sam_finished_at=row.get("last_face_finished_at"),
        last_upload_at=ping,
    )


class RunpodAutoscaleAdapter:
    """SamAutoscaleAdapter 와 같은 표면(discover/describe/set_desired/notify)의 RunPod 판.

    off 면 HTTP 클라이언트를 만들지 않는다. 예외는 삼키지 않고 올린다 — reconciler 가
    자기 맥락에서 삼킨다(sam 어댑터와 같은 계약).
    """

    def __init__(self, settings, *, enabled_attr="face_autoscale", client=None, health_client=None,
                 pod_store=None, code_url_provider=None, code_head_provider=None):
        self._settings = settings
        self.enabled = getattr(settings, enabled_attr, "off") == "on"
        self._pod_id = (getattr(settings, "face_runpod_pod_id", None) or "").strip() or None
        self._api_key = (getattr(settings, "face_runpod_api_key", None) or "").strip() or None
        self._client = client
        self._health_client = health_client
        #: 현재 파드 id 의 정본. 없으면 설정값(FACE_RUNPOD_POD_ID)으로 폴백한다.
        self._pod_store = pod_store
        self._created_this_cycle = False
        #: 파드에 넣어 줄 코드 묶음(키, sha). 없으면 파드가 코드를 못 받는다 → 알림 대상.
        self._code_sha = (getattr(settings, "face_render_code_version", None) or "").strip() or None
        self._code_url_provider = code_url_provider
        self._code_head_provider = code_head_provider
        self._last_code_alert: float | None = None
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

    def _post_json_sync(self, path: str, body: dict) -> dict:
        res = self._http().post(path, json=body)
        res.raise_for_status()
        return res.json() if res.content else {}

    def _patch_sync(self, path: str, body: dict) -> dict:
        res = self._http().patch(path, json=body)
        res.raise_for_status()
        return res.json() if res.content else {}

    def _delete_sync(self, path: str) -> None:
        res = self._http().delete(path)
        if res.status_code not in (200, 204, 404):
            res.raise_for_status()

    # ── 탐색 ──
    async def discover(self) -> RunpodTarget | None:
        """지금 쓰는 파드. **DB 가 정본**이고 설정값은 초기값·폴백이다.

        이름으로 고르지 않는다 — 계정의 다른 파드를 끌 수 있다.
        파드가 아예 없으면(첫 기동·전부 폐기) None 을 돌려주되, set_desired(1) 이
        그때 새로 만든다(아래 _create_pod).
        """
        if not self.enabled:
            return None
        if self._target is not None:
            return self._target
        if not self._api_key:
            log.error("face autoscale: RUNPOD_API_KEY not configured")
            return None
        pod_id = None
        if self._pod_store is not None:
            pod_id = await self._pod_store.get_active()
        pod_id = pod_id or self._pod_id
        if not pod_id:
            # ★ 파드가 없는 건 **정상 상태**다(설계상 수요가 생기면 그때 만든다).
            # 여기서 None 을 주면 공용 reconciler 가 ECS 의 "서비스 없음" 으로 읽고 자동 켜기를
            # 영구 비활성한다 — 2026-09-11 운영에서 실제로 그렇게 꺼졌다(파드 행 0개).
            # 그래서 **빈 타깃**을 준다: describe 는 0/0, set_desired(1) 이 그때 만든다.
            log.info("face autoscale: 등록된 파드가 없다 — 수요가 생기면 새로 만든다")
            self._target = RunpodTarget("")
            return self._target
        self._target = RunpodTarget(pod_id)
        return self._target

    def forget_target(self) -> None:
        self._target = None

    # ── 상태 ──
    async def describe(self, target: RunpodTarget) -> ServiceState:
        """desired 는 파드 API, running 은 렌더 서비스 /healthz 가 정본(위 상수 주석의 실측)."""
        if not target.pod_id:
            # 아직 파드가 없다 — 꺼져 있는 것과 같다. 수요가 있으면 set_desired(1) 이 만든다.
            return ServiceState(desired=0, running=0, pending=0, oldest_started_at=None)
        pod = await asyncio.to_thread(self._get_sync, f"/pods/{target.pod_id}")
        # 헬스 주소는 **지금 보는 그 파드**에서 계산한다. init 때 env 로 굳혀 두면 파드를
        # 갈아탄 뒤에도 죽은 주소를 찔러 "안 떠 있다"가 영원히 이어진다.
        health_url = self._health_url_for(target.pod_id)
        status = str(pod.get("desiredStatus") or pod.get("status") or "").upper()
        # RunPod 은 대수 개념이 없다 — 켜라고 해 뒀거나(=1) 꺼 뒀거나(=0) 다.
        desired = 1 if status in _RUNNING or status in _PENDING else 0
        served = await self.health_ok(health_url) if desired else False
        running = 1 if served else 0
        # 확인할 주소가 없으면 desiredStatus 를 그대로 믿는다(폴백).
        if health_url is None:
            running = desired
        pending = 1 if desired and not running else 0
        started = pod.get("lastStartedAt") or pod.get("startedAt")
        return ServiceState(desired=desired, running=running, pending=pending,
                            oldest_started_at=_parse_ts(started) if running else None)

    def _health_url_for(self, pod_id: str | None) -> str | None:
        """파드 id 가 있으면 **그 파드 주소**가 정본이다. env 는 파드가 없을 때만 쓴다.

        워커도 같은 순서다(face_identity.resolve_backend: spec.backend_url → 설정값).
        어댑터만 env 를 앞에 두면 파드를 갈아탄 뒤 헬스와 렌더가 서로 다른 파드를 보게 된다.
        """
        derived = pod_backend_url(pod_id)
        return _health_url(derived or getattr(self._settings, "face_identity_backend_url", None))

    async def health_ok(self, health_url: str | None = None) -> bool:
        """렌더 서비스가 실제로 응답하는가. URL 이 없거나 실패면 False(예외 없음).

        주소를 안 주면 **지금 등록된 파드**에서 계산한다(라우트가 이렇게 부른다).
        """
        url = health_url or self._health_url_for(self._target.pod_id if self._target else None)
        if url is None:
            return False
        try:
            return await asyncio.to_thread(self._health_sync, url)
        except Exception as exc:  # noqa: BLE001 — 헬스 실패는 "안 떠 있다" 이지 에러가 아니다
            log.info("face render health probe failed: %r", exc)
            return False

    def _health_sync(self, url: str) -> bool:
        client = self._health_client
        if client is None:
            import httpx

            client = httpx.Client(timeout=HEALTH_TIMEOUT, headers={"User-Agent": USER_AGENT})
            self._health_client = client
        res = client.get(url)
        return res.status_code == 200 and bool(res.json().get("loaded"))

    async def set_desired(self, target: RunpodTarget | None, count: int) -> None:
        """0 이면 stop(terminate 아님 — 같은 호스트에 자리가 남아 있으면 stop→start 가 제일 빠르다).

        1 인데 start 가 실패하면(호스트에 GPU 없음·파드 없음) **새 파드를 만든다**.
        2026-09-10 실측: 멈춘 파드 start 가 "not enough free GPUs on the host machine" 로 3회
        실패했고, 그 상태로는 얼굴 패스가 영영 안 돈다. 생성은 한 주기에 1번만 한다.
        """
        if int(count) <= 0:
            if target is None or not target.pod_id:
                return          # 없는 파드는 끌 것도 없다
            await asyncio.to_thread(self._post_sync, f"/pods/{target.pod_id}/stop")
            log.info("face autoscale: pod %s stop", target.pod_id)
            return
        if target is not None and target.pod_id:
            try:
                code_env = self._code_env()
                if code_env:
                    # 켜기 직전에 코드 URL 을 새로 넣는다 — 이전 URL 은 이미 만료됐을 수 있다.
                    await asyncio.to_thread(
                        self._patch_sync, f"/pods/{target.pod_id}",
                        {"env": {"FACE_RENDER_TOKEN": POD_TOKEN_REF, **code_env}})
                await asyncio.to_thread(self._post_sync, f"/pods/{target.pod_id}/start")
                log.info("face autoscale: pod %s start", target.pod_id)
                return
            except Exception as exc:  # noqa: BLE001 — 재고 문제면 아래에서 새로 만든다
                log.warning("face autoscale: start failed for %s (%r) — 새 파드로 간다",
                            target.pod_id, exc)
        if self._created_this_cycle:
            raise RuntimeError("pod create already attempted this cycle")
        self._created_this_cycle = True
        await self._create_pod(replacing=target if (target and target.pod_id) else None)

    def _code_env(self) -> dict[str, str]:
        """파드에 넣을 코드 전달 env. **호출 직전에** presigned 를 만든다(만료가 짧다).

        ★ 키와 검증값은 서로 다른 것이다. 키는 **커밋 sha**(face_render/<sha>.tgz — 서버와 파드가
        같은 코드를 쓰게 하는 좌표)이고, CODE_SHA256 은 **묶음 내용의 해시**(파드가 받은 파일이
        그 파일인지 확인하는 값)다. 하나로 같이 쓰면 bootstrap 의 sha256 대조가 항상 틀려서
        파드가 매번 exit 78 로 죽는다 — 실제로 그랬다.
        내용 해시는 업로드 때 넣어 둔 R2 메타(sha256)에서 읽는다.

        URL 값은 로그·DB·이벤트 어디에도 남기지 않는다 — 여기서 만들어 곧장 RunPod 에만 준다.
        """
        if not self._code_sha or self._code_url_provider is None:
            self._alert_code_missing("코드 묶음 sha/공급자가 없다")
            return {}
        key = code_tarball_key(self._code_sha)
        content_sha = self._code_content_sha(key)
        if not content_sha:
            self._alert_code_missing(f"R2 에 코드 묶음이 없거나 메타가 없다 ({key})")
            return {}
        try:
            url = self._code_url_provider(key)
        except Exception as exc:  # noqa: BLE001
            self._alert_code_missing(f"코드 묶음 URL 발급 실패 ({type(exc).__name__})")
            return {}
        if not url:
            self._alert_code_missing("코드 묶음 URL 이 비었다")
            return {}
        return {"CODE_TARBALL_URL": url, "CODE_SHA256": content_sha}

    def _code_content_sha(self, key: str) -> str | None:
        """업로드 때 붙여 둔 메타(sha256)를 읽는다. 객체·메타가 없으면 None."""
        head = self._code_head_provider
        if head is None:
            return None
        try:
            meta = head(key) or {}
        except Exception as exc:  # noqa: BLE001 — 없으면 코드 env 를 안 넣는다(지어내지 않는다)
            log.info("face autoscale: 코드 묶음 head 실패 (%s)", type(exc).__name__)
            return None
        value = str(meta.get("sha256") or "").strip().lower()
        return value or None

    def _alert_code_missing(self, detail: str) -> None:
        """코드를 못 주는 상황은 파드가 떠도 서비스가 안 뜬다는 뜻이라 CRITICAL(디바운스)."""
        now = time.monotonic()
        if (self._last_code_alert is not None
                and now - self._last_code_alert < CODE_ALERT_DEBOUNCE_SECONDS):
            return
        self._last_code_alert = now
        log.critical("face autoscale: %s — 새로 만드는 파드는 코드를 받지 못한다. "
                     "배포의 '얼굴 렌더 코드 묶음 업로드' 단계를 확인하세요.", detail)

    def begin_cycle(self) -> None:
        """reconciler 한 주기의 시작 — 생성 1회 제한을 리셋한다."""
        self._created_this_cycle = False

    async def _create_pod(self, *, replacing: RunpodTarget | None) -> None:
        """카드 우선순위대로 새 파드를 만든다. 전부 실패하면 예외(호출자가 알림을 낸다)."""
        errors: list[str] = []
        for gpu_type, price in GPU_PRIORITY:
            body = {
                "name": "face-render",
                "imageName": POD_IMAGE,
                "cloudType": "SECURE",
                "containerDiskInGb": POD_DISK_GB,
                "ports": list(POD_PORTS),
                "gpuTypeIds": [gpu_type],
                "gpuCount": 1,
                "dockerStartCmd": POD_ARGS,
                "env": {"FACE_RENDER_TOKEN": POD_TOKEN_REF, **self._code_env()},
            }
            try:
                created = await asyncio.to_thread(self._post_json_sync, "/pods", body)
            except Exception as exc:  # noqa: BLE001 — 재고 없음도 여기로 온다
                errors.append(f"{gpu_type}: {type(exc).__name__}")
                continue
            pod_id = str((created or {}).get("id") or "").strip()
            if not pod_id:
                errors.append(f"{gpu_type}: id 없음")
                continue
            log.info("face autoscale: 새 파드 %s (%s, $%.2f/h)", pod_id, gpu_type, price)
            if self._pod_store is not None:
                await self._pod_store.set_active(pod_id, gpu_type)
            self._target = RunpodTarget(pod_id)
            if replacing is not None and replacing.pod_id != pod_id:
                await self._retire(replacing.pod_id)
            return
        raise RuntimeError("모든 카드에서 파드 생성 실패: " + ", ".join(errors))

    async def _retire(self, pod_id: str) -> None:
        """이전 파드는 지운다 — 멈춰만 두면 컨테이너 디스크 요금이 계속 나간다."""
        try:
            await asyncio.to_thread(self._delete_sync, f"/pods/{pod_id}")
        except Exception:  # noqa: BLE001
            log.warning("face autoscale: 이전 파드 %s terminate 실패", pod_id, exc_info=False)
        if self._pod_store is not None:
            await self._pod_store.retire(pod_id)

    async def notify(self, subject: str, body: str) -> None:
        """SNS 토픽 대신 **CRITICAL 로그**로 알린다.

        api 의 Slack 필터(main 18b24cf2)는 "http error status=5" · " CRITICAL " · "CRITICAL:" ·
        "Application startup failed" 만 잡는다. log.error 로 남기면 CloudWatch 에는 있고
        Slack 에는 안 가서 "never became healthy" 같은 알림이 조용히 사라진다.
        """
        log.critical("face autoscale alert: %s — %s", subject, body)


def _health_url(backend_url) -> str | None:
    """렌더 서비스 URL(…/render) → …/healthz. 값이 없으면 None(헬스 확인 불가)."""
    if not isinstance(backend_url, str) or not backend_url.strip():
        return None
    base = backend_url.strip().rstrip("/")
    for suffix in ("/render", ""):
        if suffix and base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return f"{base}/healthz"


def _parse_ts(value) -> datetime | None:
    """RunPod 시각 파싱. REST 는 ISO 가 아니라 `2026-09-10 10:58:38.444 +0000 UTC` 를 준다
    (2026-09-10 실측) — fromisoformat 만 쓰면 항상 None 이라 장시간 가동 알림이 죽는다."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(" UTC"):
        text = text[: -len(" UTC")]
    parsed = None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S.%f %z", "%Y-%m-%d %H:%M:%S %z"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
