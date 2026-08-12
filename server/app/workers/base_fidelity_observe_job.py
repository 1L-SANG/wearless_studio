"""거부된 마네킹컷에 대한 베이스 충실도 관측 (판정만, 생성 없음).

셀러가 결과를 거부하고 재생성을 누른 순간이 프로덕션에서 가장 강한 오류 신호다 — 자동 QC 가
통과시킨 컷도 여기 걸린다(실측: AG-P2 auto_pass 인데 베이스 이탈 6건). 그 **거부된 컷**을
베이스 마네킹과 대조해 판정만 남긴다. 새 컷이 아니라 옛 컷을 본다: 재생성이 확률적 결함을
고쳐 버리면 표본 자체가 사라지기 때문이다.

이 워커가 하지 않는 것(전부 의도적):
  · 이미지 생성 · SAM 호출 · 캐노니컬 자산 변경 · 마네킹 재시도
  · QC 판정 변경 · 크레딧 이동 · 원본 결과 상태 변경

실패는 전부 이 잡 안에서 끝난다. 셀러 화면에는 아무 영향이 없다.
"""

import logging
import time

from .. import repo
from ..agents import mannequin, mannequin_base_fidelity_qc as bf
from ..agents.gemini_image import InlineImage
from ..config import load_settings
from ._common import emit_job_event as _emit

log = logging.getLogger("wearless.base_fidelity_observe")

#: 판정을 못 한 사유. "결함 0건"과 "측정 못 함"을 코퍼스에서 구분하기 위해 남긴다.
SKIP_DISABLED = "observation_disabled"
SKIP_NO_CUT_REF = "missing_rejected_cut_reference"
SKIP_CUT_GONE = "rejected_asset_unavailable"
SKIP_NO_BASE = "missing_base_reference"


def _base_gender(cut_metadata, analysis: dict, clothing_type) -> tuple[str, str]:
    """거부된 컷이 실제로 어떤 베이스로 만들어졌는지 → (gender, 출처).

    생성 시점 성별을 컷에 따로 저장하지 않으므로 두 단계로 복원한다. 컷 자산 메타의
    `profileGender` 가 있으면 그게 생성 시점 진실이다. 없으면 워커가 쓰는 것과 **같은 함수**로
    다시 뽑는다 — 그 사이 analysis 가 바뀌었으면 달라질 수 있어 출처를 함께 기록한다.
    """
    if isinstance(cut_metadata, dict):
        g = cut_metadata.get("profileGender")
        if g in ("men", "women"):
            return g, "cut_metadata"
    return mannequin.select_base_gender(analysis, clothing_type), "rederived"


async def run_base_fidelity_observe_job(app, job: dict) -> None:
    """Worker entrypoint. 시그니처는 `_WORKERS` 의 다른 kind 와 같다."""
    pool = app.state.pool
    job_id, project_id = job["id"], job["project_id"]
    user_id, lease_token = job["user_id"], job["lease_token"]
    s = load_settings()
    payload = job.get("payload") or {}

    async def finish(status: str, detail: dict) -> None:
        async with pool.connection() as conn:
            await repo.finalize_uncharged_job(
                conn, job_id=job_id, lease_token=lease_token, status=status, result=detail)
            await conn.commit()
        log.info("base_fidelity_observe job=%s project=%s %s", job_id, project_id,
                 detail.get("observationStatus"))

    async def skip(reason: str, extra: dict | None = None) -> None:
        # skip 도 done 으로 종결한다 — 관측을 못 한 것은 잡의 실패가 아니다.
        await finish("done", {"observationStatus": "skipped", "reason": reason,
                              "userRejected": True, **(extra or {})})

    if getattr(s, "mannequin_base_fidelity_observe_regenerations", "off") != "on":
        await skip(SKIP_DISABLED)
        return

    cut_ref = payload.get("rejectedCutId")
    if not isinstance(cut_ref, str) or not cut_ref:
        await skip(SKIP_NO_CUT_REF)
        return

    async with pool.connection() as conn:
        cut = await repo.get_mannequin_cut_asset(conn, user_id, project_id, cut_ref)
        product = await repo.get_product(conn, project_id) or {}
        analysis = await repo.get_analysis(conn, project_id) or {}
        clothing_type = (product.get("clothing_type") or product.get("clothingType")
                         or analysis.get("clothingType") or "top")
        gender, gender_source = _base_gender(
            payload.get("cutMetadata"), analysis, clothing_type)
        base_asset_id = (s.base_mannequin_men_asset_id if gender == "men"
                         else s.base_mannequin_women_asset_id)
        base_asset = (await repo.get_asset_for_user(conn, user_id, base_asset_id)
                      if base_asset_id else None)

    if cut is None or not cut.get("r2_key"):
        await skip(SKIP_CUT_GONE, {"rejectedCutId": cut_ref})
        return
    if base_asset is None or not base_asset.get("r2_key"):
        await skip(SKIP_NO_BASE, {"rejectedCutId": cut_ref, "rejectedAssetId": cut.get("id"),
                                  "baseGender": gender})
        return

    base_ref = {"gender": gender, "assetId": base_asset_id, "source": gender_source}
    record = {
        "userRejected": True,          # 사용자가 거부했다는 사실만. 결함 종류는 사람이 라벨한다.
        "manualDefect": None,          # 절대 자동으로 채우지 않는다 (거부 사유는 포즈·색·핏 등 다양)
        "projectId": project_id,
        "rejectedCutId": cut_ref,
        "rejectedAssetId": cut.get("id"),
        "baseReference": base_ref,
        "attempt": payload.get("attempt"),
        "candidate": cut_ref.rsplit("-", 1)[0],
        "product": {"category": clothing_type,
                    "subCategory": analysis.get("subCategory"),
                    "fit": analysis.get("fit")},
        "qcVersion": bf.QC_VERSION,
    }

    try:
        base_bytes = await _get_bytes(app, base_asset["r2_key"])
        gen_bytes = await _get_bytes(app, cut["r2_key"])
    except Exception as e:  # noqa: BLE001 - 객체가 사라졌거나 R2 장애
        log.warning("base_fidelity_observe asset read failed job=%s: %r", job_id, e)
        await skip(SKIP_CUT_GONE, {**record, "error": type(e).__name__})
        return

    t0 = time.monotonic()
    try:
        out = await bf.verdict(
            s, InlineImage(base_asset.get("mime_type") or "image/png", base_bytes),
            InlineImage(cut.get("mime_type") or "image/png", gen_bytes),
            product=product, analysis=analysis)
    except Exception as e:  # noqa: BLE001 - 판정 실패가 재생성에 영향을 주지 않는다
        latency = round(time.monotonic() - t0, 2)
        log.warning("base_fidelity_observe verdict failed job=%s: %r", job_id, e)
        await _emit(pool, job_id, "step", {
            "status": "base_fidelity_observe_failed", "error": type(e).__name__,
            "message": str(e)[:200], "latencySeconds": latency})
        # error 로 종결한다 — 판정기 장애율이 코퍼스 통계에서 보여야 한다. 재시도는 디스패처의
        # lease 복구가 아니라 사용자의 다음 거부가 만든다(공격적 재시도 금지).
        await finish("error", {**record, "observationStatus": "failed",
                               "error": type(e).__name__, "latencySeconds": latency})
        return

    latency = round(time.monotonic() - t0, 2)
    record.update(
        observationStatus="completed", latencySeconds=latency,
        baseFidelity={a: {"decision": out[a]["decision"], "reason": out[a]["reason"]}
                      for a in (*bf.AXES, "overall")})
    await _emit(pool, job_id, "step", {
        "status": "base_fidelity_observe",
        "poseFrameMatch": out["poseFrameMatch"]["decision"],
        "wearGeometry": out["wearGeometry"]["decision"],
        "overall": out["overall"]["decision"],
        "latencySeconds": latency, "baseGender": gender})
    # pass 도 반드시 남긴다. "사용자는 거부했는데 wearGeometry=pass" 인 표본이 바로 그 축의
    # 거짓 음성 후보이고, 캘리브레이션에 필요한 것은 retry 가 아니라 이쪽이다.
    await finish("done", record)


async def _get_bytes(app, key: str) -> bytes:
    import asyncio
    return await asyncio.to_thread(app.state.r2.get_bytes, key)
