"""개인화 생성 워커 — 경로 α (api-spec §4). payload = {profileId, productImageAssetIds, options, generationId}.

profileId 로 얼굴 3장(비공개 버킷)·신체 프로필을 서버측 로드하고, 상품 이미지 asset 을 로드해
신체 프로필로 프롬프트를 구성한 뒤 gemini.generate_content_image([프롬프트, 얼굴들, 상품들]) 로
개인화 이미지를 만든다. 결과는 **비공개 r2_face 버킷**에 저장(공개 버킷·무인증 capability URL
금지 §4)하고, 산출물 R2 키를 personalization_generations.result_keys 에 저장한다(공유 assets
테이블 미적재 — CRITICAL-B). job done 이벤트엔 게이트 URI
(`/v1/personalization/generations/{id}/results/{n}/file`)만 싣는다.

진행 중 프로필 상태가 purging|purged 면 결과를 폐기하고 job error 로 종결한다(§3.5 —
running 잡은 finalize 시점 프로필 상태를 확인).

PII 하드룰(§1.4): job payload·이벤트·로그에 얼굴 바이트·임베딩·비공개 R2 키·공개/서명 URL
미포함. 남기는 것은 id·상태 enum·지연·카운트뿐. 결과는 인증 게이트 라우트로만 서빙한다.
"""

import asyncio
import logging
from io import BytesIO

from PIL import Image
from psycopg.types.json import Json

from .. import repo
from ..agents.gemini_image import GeminiError, InlineImage
from ..agents.model_routing import resolve_model
from ..r2 import ext_for_mime
from ._common import emit_job_event as _emit

log = logging.getLogger("wearless.personalization_generation_job")

_EXT_FALLBACK = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
# 얼굴 슬롯 주입 순서 고정(front→side→angle45) — 모델 입력 순서 안정화.
_ANGLE_ORDER = {"front": 0, "side": 1, "angle45": 2}


def _dims(data: bytes) -> tuple[int | None, int | None]:
    try:
        im = Image.open(BytesIO(data))
        return im.width, im.height
    except Exception:
        return None, None


def _result_key(user_id: str, generation_id: str, index: int, ext: str) -> str:
    """개인화 생성 결과의 비공개 버킷 키. 얼굴 PII 포함 → r2_face 전용, 게이트 서빙만(§4).
    user_id/generation_id 경로 = prefix 단위 소유 경계. 어떤 API 응답에도 이 키는 미노출."""
    return f"personalization/{user_id}/generations/{generation_id}/{index}.{ext}"


def _build_prompt(body: dict, options: dict) -> str:
    """신체 프로필 + 옵션으로 생성 프롬프트 구성(경로 α 골격 — 세부 파라미터는 §4 TBD).

    body 값(키·몸무게 등)은 모델 입력으로만 사용 — 로그·이벤트에 남기지 않는다(§1.4).
    """
    lines = [
        "Generate a realistic, natural full-body fashion photograph of the SAME person shown "
        "in the provided face reference photos, wearing the product garment shown in the "
        "product photos. Preserve the person's facial identity from the face references and "
        "the exact garment (color, pattern, silhouette, length) from the product photos.",
    ]
    desc: list[str] = []
    if body.get("height_cm") is not None:
        desc.append(f"height about {body['height_cm']} cm")
    if body.get("weight_kg") is not None:
        desc.append(f"weight about {body['weight_kg']} kg")
    body_type = body.get("body_type")
    if body_type == "custom" and body.get("body_type_custom"):
        desc.append(f"body type: {body['body_type_custom']}")
    elif body_type:
        desc.append(f"{body_type} body type")
    if body.get("gender"):
        desc.append(f"gender presentation: {body['gender']}")
    if body.get("age_range"):
        desc.append(f"age range: {body['age_range']}")
    if body.get("skin_tone"):
        desc.append(f"skin tone: {body['skin_tone']}")
    if body.get("hair"):
        desc.append(f"hair: {body['hair']}")
    if desc:
        lines.append("Person attributes — " + ", ".join(desc) + ".")
    # options 는 화이트리스트 문자열 힌트만 반영(§4 옵션 스키마 TBD — 배경/포즈 등).
    background = options.get("background") if isinstance(options, dict) else None
    if isinstance(background, str) and background.strip():
        lines.append(f"Background: {background.strip()[:120]}.")
    pose = options.get("pose") if isinstance(options, dict) else None
    if isinstance(pose, str) and pose.strip():
        lines.append(f"Pose: {pose.strip()[:120]}.")
    return "\n".join(lines)


async def _finalize_success(
    app, *, job: dict, profile_id: str, generation_id: str | None,
    result_assets: list[dict], reserved: int, charge: int,
) -> str:
    """성공 종결(원자·lease 펜스) → "done" | "lease_lost" | "purged".

    한 tx·한 락으로 generation.result_keys 저장 + 크레딧 확정 + job done/이벤트를 처리한다.
    finalize 시점에 프로필이 purging|purged 면 아무것도 쓰지 않고 "purged" 반환(§3.5) —
    호출자가 결과 R2 를 폐기하고 error 로 종결한다.
    """
    s = app.state.settings
    pool = app.state.pool
    job_id, user_id = job["id"], job["user_id"]
    project_id = job.get("project_id")
    lease_token = job["lease_token"]

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # lease 펜스 — 잃었으면 부수효과 0(복구·재클레임이 소유)
            await cur.execute(
                "select id from jobs where id = %s and locked_by = %s and status = 'running' "
                "for update",
                (job_id, lease_token))
            if await cur.fetchone() is None:
                return "lease_lost"
            # §3.5 — running 잡은 finalize 시점 프로필 상태 확인. purging|purged 면 결과 폐기.
            await cur.execute(
                "select status from personalization_profiles where id = %s for update",
                (profile_id,))
            prow = await cur.fetchone()
            if prow is None or prow["status"] in ("purging", "purged"):
                await conn.rollback()
                return "purged"

            # 산출물 R2 키를 generation.result_keys 에 저장 — 공유 assets 테이블 미적재(CRITICAL-B:
            # 무인증 /v1/assets/{id}/file 누출원 제거). 서빙은 게이트 라우트만(§4). 키는 API 응답 미노출.
            result_keys = [ra["key"] for ra in result_assets]
            if generation_id:
                await cur.execute(
                    "update personalization_generations set status = 'done', "
                    "result_keys = %s, engine = 'alpha' where id = %s returning id::text as id",
                    (result_keys, generation_id))
            else:
                await cur.execute(
                    "update personalization_generations set status = 'done', "
                    "result_keys = %s, engine = 'alpha' where job_id = %s returning id::text as id",
                    (result_keys, job_id))
            grow = await cur.fetchone()
            # 게이트 URI 엔 generation id 필요 — generation_id 미전달 시 job_id 분기의 RETURNING 으로 확보
            gen_id = grow["id"] if grow else (generation_id or job_id)

            # 감사로그 generation_done — 카운트만(PII 없음, §5)
            await cur.execute(
                "insert into personalization_audit_log (user_id, profile_id, event_type, detail) "
                "values (%s, %s, 'generation_done', %s)",
                (user_id, profile_id, Json({"resultCount": len(result_keys)})))

        # 크레딧 확정 — 예약 있을 때만. 버킷 FIFO 차감, 같은 tx·job 락 유지(멱등 경계=job.status).
        credits_after = None
        if reserved > 0 or charge > 0:
            credits_after = await repo._consume_buckets(
                conn, user_id=user_id, project_id=project_id, job_id=job_id,
                reserved=reserved, charge=charge, action_key="personalizationGenerate",
                metadata={"creditCostVersion": s.credit_cost_version})

        # done — index 기반 게이트 URI만(n=0..len-1). 무인증 capability URL·공개 URL·result_keys·
        # 바이트·digest 미포함(§4·§1.4). URI 의 generation id 는 위 RETURNING 으로 확보한 gen_id.
        gate_uris = [
            f"/v1/personalization/generations/{gen_id}/results/{i}/file"
            for i in range(len(result_keys))
        ]
        envelope = {
            "data": {
                "generationId": gen_id,
                "results": [{"index": i, "uri": u} for i, u in enumerate(gate_uris)],
                "resultCount": len(gate_uris),
            },
            "credits": credits_after,
            "creditsCharged": charge,
        }
        async with conn.cursor() as cur:
            await cur.execute(
                "update jobs set status = 'done', result = %s, credits_charged = %s, "
                "progress = 100, locked_by = null, locked_at = null, finished_at = now() "
                "where id = %s",
                (Json(envelope), charge, job_id))
            await cur.execute(
                "insert into job_events (job_id, event_type, payload) values (%s, 'done', %s)",
                (job_id, Json(envelope)))
        await conn.commit()
    return "done"


async def run_personalization_generation_job(app, job: dict) -> None:
    s = app.state.settings
    pool = app.state.pool
    job_id, user_id = job["id"], job["user_id"]
    project_id = job.get("project_id")  # 개인화 잡은 대개 null(프로젝트 비귀속)
    lease_token = job["lease_token"]
    reserved = job.get("credits_reserved") or 0
    settle_key = f"credit:job:{job_id}:settle"
    payload = job.get("payload") or {}
    profile_id = payload.get("profileId")
    generation_id = payload.get("generationId")
    product_asset_ids = payload.get("productImageAssetIds") or []
    options = payload.get("options") or {}
    r2_face = getattr(app.state, "r2_face", None)

    async def _mark_generation(status: str) -> None:
        """generation 행 상태 갱신(best-effort, 별도 tx). 성공 종결은 _finalize_success 가 처리."""
        try:
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    if generation_id:
                        await cur.execute(
                            "update personalization_generations set status = %s where id = %s",
                            (status, generation_id))
                    else:
                        await cur.execute(
                            "update personalization_generations set status = %s where job_id = %s",
                            (status, job_id))
                await conn.commit()
        except Exception:
            log.warning("personalization_generation: generation status update failed job %s", job_id)

    async def _fail(message: str, meta: dict, code: str = "generation_failed",
                    discard_keys: list[str] | None = None) -> None:
        # 이미 저장된 결과 R2 객체 best-effort 정리(고아 얼굴 금지)
        for k in (discard_keys or []):
            if r2_face is None:
                break
            try:
                await asyncio.to_thread(r2_face.delete, k)
            except Exception:
                log.warning("personalization_generation: orphan face R2 cleanup failed job %s", job_id)
        try:
            async with pool.connection() as conn:
                release = ({"user_id": user_id, "project_id": project_id, "reserved": reserved,
                            "action_key": "personalizationGenerate.release", "settle_key": settle_key}
                           if reserved > 0 else None)
                await repo._finalize_job_failure(
                    conn, job_id=job_id, lease_token=lease_token,
                    message=message, metadata=meta, code=code, release=release)
                await conn.commit()
        except Exception:
            log.exception("personalization_generation finalize_failure error for job %s", job_id)
        await _mark_generation("error")

    try:
        if not profile_id:
            await _fail("생성 대상 프로필이 없어요.", {"error": "missing_profile_id"})
            return
        if r2_face is None:  # 얼굴 로드·결과 저장 불가 — 공개 버킷 폴백 금지(§1.4)
            await _fail("얼굴 저장소가 설정되지 않았어요.", {"error": "face_storage_unavailable"})
            return

        # ── 1) 로드: 프로필(상태·신체) + 얼굴 3장 키 + 상품 asset(소유 검증) ──
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "select status, height_cm, weight_kg, body_type, body_type_custom, gender, "
                    "age_range, skin_tone, hair, clothing_size "
                    "from personalization_profiles where id = %s",
                    (profile_id,))
                profile = await cur.fetchone()
                await cur.execute(
                    "select angle, r2_key, mime_type from personalization_face_photos "
                    "where profile_id = %s",
                    (profile_id,))
                face_rows = await cur.fetchall()
            product_assets = []
            for aid in product_asset_ids:
                a = await repo.get_asset_for_user(conn, user_id, str(aid))
                if a:
                    product_assets.append(a)

        if profile is None:
            await _fail("프로필을 찾을 수 없어요.", {"error": "profile_not_found"},
                        code="profile_not_found")
            return
        if profile["status"] in ("purging", "purged"):  # §3.5 — 진행 중 파기면 즉시 중단
            await _fail("개인화가 파기되어 생성을 중단했어요.", {"error": "purge_cancelled"},
                        code="purge_cancelled")
            return
        if len(face_rows) < 3:
            await _fail("얼굴 사진 3장이 필요해요.",
                        {"error": "face_photos_incomplete", "have": len(face_rows)},
                        code="face_photos_incomplete")
            return
        if not product_assets:
            await _fail("상품 이미지를 찾을 수 없어요.", {"error": "no_product_images"},
                        code="no_product_images")
            return

        await _mark_generation("running")
        await _emit(pool, job_id, "progress", {"progress": 15, "phase": "inputs_loaded"})

        # ── 2) 바이트 로드 — 얼굴(비공개 r2_face) + 상품(메인 r2). 바이트는 로그·이벤트 미포함 ──
        face_rows.sort(key=lambda r: _ANGLE_ORDER.get(r["angle"], 9))
        face_imgs = [
            InlineImage(fr["mime_type"], await asyncio.to_thread(r2_face.get_bytes, fr["r2_key"]))
            for fr in face_rows
        ]
        product_imgs = [
            InlineImage(a["mime_type"], await asyncio.to_thread(app.state.r2.get_bytes, a["r2_key"]))
            for a in product_assets
        ]
        await _emit(pool, job_id, "progress", {"progress": 35, "phase": "generating"})

        # ── 3) 경로 α 생성: [프롬프트, 얼굴들, 상품들] 다중 InlineImage 입력 ──
        prompt = _build_prompt(profile, options)
        model = resolve_model(s, "image_high")
        try:
            res = await app.state.gemini.generate_content_image(
                model, prompt, [*face_imgs, *product_imgs], s.mannequin_image_size,
                aspect_ratio=s.mannequin_aspect_ratio)
        except GeminiError as e:
            await _fail("개인화 생성에 실패했어요. 다시 시도해 주세요.", {"error": str(e)[:300]})
            return
        await _emit(pool, job_id, "progress", {"progress": 75, "phase": "generated"})

        # ── 4) 결과 → 비공개 r2_face 저장(§4 — 공개 버킷·capability URL 금지) ──
        ext = ext_for_mime(res.mime) or _EXT_FALLBACK.get(res.mime, "png")
        gid = generation_id or job_id
        key = _result_key(user_id, gid, 0, ext)
        await asyncio.to_thread(r2_face.put_bytes, key, res.image, res.mime)
        w, h = _dims(res.image)
        result_assets = [{
            "key": key, "mime": res.mime,
            "size": len(res.image), "width": w, "height": h,
        }]

        # ── 5) 종결(원자·lease 펜스, 파기 재확인). charge = reserved(예약 시점 견적 확정) ──
        charge = reserved
        outcome = await _finalize_success(
            app, job=job, profile_id=profile_id, generation_id=generation_id,
            result_assets=result_assets, reserved=reserved, charge=charge)
        if outcome == "purged":  # finalize 시점 파기 감지 → 결과 폐기 + error 종결(§3.5)
            await _fail("개인화가 파기되어 생성을 중단했어요.", {"error": "purge_cancelled"},
                        code="purge_cancelled", discard_keys=[ra["key"] for ra in result_assets])
        elif outcome == "lease_lost":  # 복구·재클레임이 소유 → 결과 R2 폐기만(잡 건드리지 않음)
            for ra in result_assets:
                try:
                    await asyncio.to_thread(r2_face.delete, ra["key"])
                except Exception:
                    log.warning("personalization_generation: orphan face R2 cleanup failed job %s", job_id)
    except Exception as e:
        await _fail("개인화 생성 중 오류가 발생했어요. 다시 시도해 주세요.", {"error": str(e)[:300]})
