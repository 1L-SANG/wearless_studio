"""각도 교체 GPU(ComfyUI 파드) 온디맨드 기동/종료 — 얼굴 패스 파드와 같은 기계, 다른 프로필.

옆·뒷모습 컷은 등록자의 각도 실사진으로 머리를 바꾼다(agents/face_angle_swap). 그 계산은
ComfyUI + Qwen-Image-Edit-2511 + BFS Head LoRA 에서 돈다 — 얼굴 패스 파드가 쓰는 diffusers
렌더 서비스와는 **다른 프로그램**이고 둘 다 8000 포트를 쓴다. 그래서 한 파드에 못 얹고
파드를 따로 둔다(2026-09-20 결정).

기동·종료·생성·재고 폴백·알림은 얼굴 파드와 한 글자도 다르지 않다. 다른 것만 PodProfile 에
담아 같은 어댑터(face_autoscale.RunpodAutoscaleAdapter)에 끼운다.

★ 수요 = "지금 옆·뒷모습 컷이 필요한 잡". 켜진 LoRA 가 있는 등록자의 잡 중에서도
  **옆·뒤를 만드는 잡만** 센다. 이 조건이 없으면 정면 컷 하나가 시간당 $2 짜리 GPU 를 켠다.
★ 준비 판정은 /healthz 의 `comfy` **와** 설치 완료 단계 둘 다다(angle_ready 주석).
★ 셀러가 실존 모델을 고르면 얼굴 파드와 **같이** 깨어난다(워밍 핑) — 첫 옆·뒤 컷이
  콜드스타트를 물지 않게. 단, 각도 사진이 있는 등록자일 때만이다.
"""

from __future__ import annotations

import logging

from app.services.face_autoscale import PodProfile
from app.services.sam_autoscale import DemandSnapshot

log = logging.getLogger("wearless.angle_autoscale")

#: 옆·뒷모습 컷을 만들 수 있는 잡. 얼굴 패스와 같은 둘이다.
ANGLE_KINDS = ("editor_image", "detail_page")
#: editor_image 는 컷 하나짜리라 방향이 페이로드에 그대로 있다. 이 둘일 때만 각도 교체가 돈다.
ANGLE_DIRECTIONS = ("side", "back")

#: 코드 묶음 R2 키. CI 가 배포할 때마다 그 커밋 sha 로 올린다(deploy-server.yml).
CODE_TARBALL_KEY_FMT = "comfy_angle/{sha}.tgz"

#: 파드 id 를 적어 두는 표. 얼굴 파드와 **다른 표**다 — 한 표에 섞으면 각도 파드를
#: 얼굴 렌더로 부르게 된다(둘은 서로 다른 API 를 말한다).
POD_TABLE = "fm_angle_render_pod"


def angle_backend_url(pod_id: str | None) -> str | None:
    """파드 id → ComfyUI 주소. 얼굴 쪽과 달리 **경로 접미사가 없다** —
    호출자(face_angle_swap.ComfyBackend)가 /prompt·/upload/image 를 직접 붙인다."""
    pod_id = (pod_id or "").strip()
    return f"https://{pod_id}-8000.proxy.runpod.net" if pod_id else None


def angle_health_url(backend_url) -> str | None:
    """ComfyUI 주소 → /healthz. 값이 없으면 None(헬스 확인 불가)."""
    if not isinstance(backend_url, str) or not backend_url.strip():
        return None
    return backend_url.strip().rstrip("/") + "/healthz"


#: 설치가 끝났다는 마지막 단계(번들 comfy_setup.sh 가 /root/setup_status.txt 에 적는다).
SETUP_DONE_STAGE = "all_done"


def angle_ready(payload: dict) -> bool:
    """준비 = ComfyUI 가 답하고 **설치가 끝났다**. 둘 다 필요하다.

    ★ comfy 만 보면 이르다(2026-09-20 실측: 123초에 comfy=true, 실제 설치는 그 뒤에도 진행).
      번들은 ComfyUI 를 **2511 다운로드 전에** 띄운다 — 2509·BFS 를 받고 서버를 올린 다음
      qwen_image_edit_2511_fp8mixed(워크플로가 실제로 쓰는 모델)를 받는다. 그 사이에 잡이
      들어오면 /prompt 가 모델 없음으로 죽고, 컷은 backend_error 로 끝난다. 그날은 첫 컷이
      늦게 와서 우연히 피했을 뿐이다.
    """
    if not isinstance(payload, dict) or not payload.get("comfy"):
        return False
    stages = payload.get("stages")
    if not isinstance(stages, list):
        return False
    # 단계 줄은 "HH:MM:SS all_done" 꼴이다 — 시각이 붙어 있어 정확히 같지 않다.
    return any(SETUP_DONE_STAGE in str(line) for line in stages)


#: 각도 교체 파드(ComfyUI + LanPaint + BFS). 이미지·부팅 스크립트·토큰 주입은 얼굴 파드와 같다.
ANGLE_PROFILE = PodProfile(
    name="comfy-angle",
    log_prefix="angle autoscale",
    enabled_attr="angle_autoscale",
    pod_id_attr="angle_runpod_pod_id",
    backend_url_attr="face_angle_backend_url",
    code_version_attr="face_render_code_version",
    code_key_fmt=CODE_TARBALL_KEY_FMT,
    token_env="FACE_RENDER_TOKEN",
    url_for=angle_backend_url,
    health_url=angle_health_url,
    ready=angle_ready,
    pod_table=POD_TABLE,
)


async def angle_demand_snapshot(conn) -> DemandSnapshot:
    """각도 교체가 필요한 잡의 수요. fm_model_loras 가 없으면 항상 0(마이그 미적용 환경).

    **정면 컷은 세지 않는다.** detail_page 는 컷 묶음이라 옆·뒤가 섞여 있다고 보고 세지만,
    editor_image 는 방향이 페이로드에 있으니 옆·뒤일 때만 센다. 이 구분이 없으면 정면만
    만드는 잡이 ComfyUI 파드를 켜 놓는다 — 그 파드는 정면 컷에 아무것도 하지 않는다.

    각도 사진이 있는지는 **보지 않는다**. 그건 등록 행 조인이 필요해 이 판정을 무겁게 만들고,
    사진이 없으면 그 컷만 실패한 뒤 파드가 유휴로 꺼진다(비용은 한 주기치).
    """
    async with conn.cursor() as cur:
        await cur.execute("select to_regclass('public.fm_model_loras') as t")
        if not (await cur.fetchone() or {}).get("t"):
            return DemandSnapshot(0, None, None)
        await cur.execute(
            "with enabled as ("
            "  select model_id::text as id from fm_model_loras where enabled and status = 'ready'"
            "), angle_jobs as ("
            "  select status, finished_at from jobs where kind = any(%s) "
            "    and payload -> '_facemarket' ->> 'modelId' in (select id from enabled) "
            "    and (kind = 'detail_page' or payload ->> 'direction' = any(%s))"
            ") "
            "select "
            "  (select count(*) from angle_jobs where status in ('pending', 'running')"
            "  ) as active_angle_jobs, "
            "  (select max(finished_at) from angle_jobs where finished_at is not null"
            "  ) as last_angle_finished_at",
            (list(ANGLE_KINDS), list(ANGLE_DIRECTIONS)),
        )
        row = await cur.fetchone() or {}
    # 세 번째 신호 = **워밍 핑**(셀러가 실존 모델을 고른 순간). 얼굴 파드와 **같은 순간**에
    # 깨어나야 첫 옆·뒤 컷이 콜드스타트를 물지 않는다 — 안 그러면 셀러는 정면 컷만 먼저
    # 나오고 옆·뒤만 늦게 채워지는 화면을 본다. 핑은 **각도 사진이 있는 모델만** 남는다
    # (조건 판정은 라우트에서 한 번, facemarket._record_angle_warm_ping).
    ping = None
    async with conn.cursor() as cur:
        await cur.execute("select to_regclass('public.fm_angle_warm_pings') as t")
        if (await cur.fetchone() or {}).get("t"):
            await cur.execute("select max(pinged_at) as at from fm_angle_warm_pings")
            ping = (await cur.fetchone() or {}).get("at")
    return DemandSnapshot(
        active_sam_jobs=int(row.get("active_angle_jobs") or 0),
        last_sam_finished_at=row.get("last_angle_finished_at"),
        last_upload_at=ping,
    )
