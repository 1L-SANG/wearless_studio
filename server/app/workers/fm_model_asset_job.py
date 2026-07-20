"""실존 모델 자산 빌드 워커 (handoff: 가상모델 fork). payload={modelId}.

user_id 로 fm_models(verified) + personalization_face_photos(3각도)를 서버측 로드하고,
r2_face(비공개)에서 얼굴 bytes 를 읽어 얼굴 대조 QC → 통과 시 2×2 그리드 합성 + face_front 를
비공개 버킷에 저장하고 fm_model_assets 에 등록한다. 얼굴은 생성하지 않는다(실사진 합성).

순서: assets_status='building' 선점 → QC → 최종 키에 put → DB 등록 tx(lease 펜스) → done.
put 후 DB 실패면 방금 올린 오브젝트를 정리한다. 크래시로 오브젝트만 남아도 assets_status 가
'ready' 가 아니면 컷 resolve 가 쓰지 않고, 재빌드가 같은 키를 덮으므로 안전하다.

PII 하드룰(§1.4): 얼굴 키·바이트·임베딩·qc_score 는 payload·이벤트·로그·job result 미포함.
남기는 것은 상태 enum·카운트뿐. weights·경로는 예외 메시지에도 싣지 않는다.
"""

import asyncio
import hashlib
import logging

from psycopg.types.json import Json

from .. import repo
from ..agents.face_grid import compose_sedcard
from ..agents.face_qc import QcFailed, load_face_qc
from ..r2 import ext_for_mime
from ._common import emit_job_event as _emit

log = logging.getLogger("wearless.fm_model_asset_job")

_ANGLE_ORDER = {"front": 0, "side": 1, "angle45": 2}


def _asset_key(model_id: str, view: str, ext: str) -> str:
    """실존 모델 아이덴티티 자산의 비공개 버킷 키. 어떤 API 응답에도 미노출."""
    return f"facemarket/models/{model_id}/{view}.{ext}"


async def run_fm_model_asset_job(app, job: dict) -> None:
    pool = app.state.pool
    job_id, user_id, lease = job["id"], job["user_id"], job["lease_token"]
    model_id = (job.get("payload") or {}).get("modelId")
    r2_face = getattr(app.state, "r2_face", None)
    s = app.state.settings
    put_keys: list[str] = []  # 실패 시 정리할 최종 키

    async def _fail(message: str, meta: dict, code: str = "asset_build_failed") -> None:
        for k in put_keys:
            try:
                await asyncio.to_thread(r2_face.delete, k)
            except Exception:
                log.warning("orphan face asset cleanup failed job %s", job_id)
        try:
            async with pool.connection() as conn:
                await repo._finalize_job_failure(
                    conn, job_id=job_id, lease_token=lease,
                    message=message, metadata=meta, code=code)
                if model_id:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "update fm_models set assets_status='failed' where id=%s", (model_id,))
                await conn.commit()
        except Exception:
            log.exception("asset job finalize_failure error job %s", job_id)

    try:
        if not model_id:
            await _fail("모델 대상이 없어요.", {"error": "missing_model_id"}); return
        if r2_face is None:  # 얼굴 로드·저장 불가 — 공개 버킷 폴백 금지(§1.4)
            await _fail("얼굴 저장소가 설정되지 않았어요.", {"error": "face_storage_unavailable"}); return

        # ── 1) 로드: 모델(verified) + 최신 프로필의 얼굴 3장 ──
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "select m.status, p.id as profile_id from fm_models m "
                    "join personalization_profiles p on p.user_id = m.user_id "
                    "where m.id=%s and m.user_id=%s "
                    "order by p.created_at desc limit 1",
                    (model_id, user_id))
                mrow = await cur.fetchone()
                if mrow is None:
                    await _fail("모델 또는 프로필을 찾을 수 없어요.",
                                {"error": "model_or_profile_missing"}, code="model_or_profile_missing")
                    return
                if mrow["status"] != "verified":
                    await _fail("검증된 모델이 아니에요.",
                                {"error": "model_not_verified"}, code="model_not_verified")
                    return
                await cur.execute(
                    "select angle, r2_key, mime_type from personalization_face_photos "
                    "where profile_id=%s", (mrow["profile_id"],))
                faces = await cur.fetchall()
                await cur.execute(
                    "update fm_models set assets_status='building' where id=%s", (model_id,))
            await conn.commit()

        if len({f["angle"] for f in faces}) < 3:
            await _fail("얼굴 사진 3장이 필요해요.",
                        {"error": "face_photos_incomplete", "have": len(faces)},
                        code="face_photos_incomplete")
            return

        faces.sort(key=lambda f: _ANGLE_ORDER.get(f["angle"], 9))
        face_bytes = [
            await asyncio.to_thread(r2_face.get_bytes, f["r2_key"]) for f in faces
        ]
        await _emit(pool, job_id, "progress", {"progress": 30, "phase": "inputs_loaded"})

        # ── 2) 얼굴 대조 QC — 미달/검출실패 시 등록 차단 ──
        qc = load_face_qc(s)
        qc_score = None
        if qc is not None:
            try:
                qc_score = qc.pairwise_min_similarity(face_bytes)
            except QcFailed as e:
                await _fail("본인 얼굴 일치 확인에 실패했어요.",
                            {"error": "qc_failed", "reason": e.reason}, code="qc_failed")
                return
            if qc_score < s.fm_face_qc_threshold:
                await _fail("본인 얼굴 일치 확인에 실패했어요.",
                            {"error": "qc_below_threshold"}, code="qc_failed")
                return
        await _emit(pool, job_id, "progress", {"progress": 55, "phase": "qc_passed"})

        # ── 3) 그리드 합성 + face_front ──
        grid = compose_sedcard(face_bytes)
        front_i = next((i for i, f in enumerate(faces) if f["angle"] == "front"), 0)
        front, front_mime = face_bytes[front_i], faces[front_i]["mime_type"]
        src_hash = hashlib.sha256(b"".join(sorted(face_bytes, key=len))).hexdigest()
        assets = [
            ("grid_sedcard", grid, "image/png"),
            ("face_front", front, front_mime),
        ]

        # ── 4) 최종 키에 put ──
        registered = []
        for view, data, mime in assets:
            ext = ext_for_mime(mime) or "png"
            key = _asset_key(model_id, view, ext)
            await asyncio.to_thread(r2_face.put_bytes, key, data, mime)
            put_keys.append(key)
            registered.append((view, key, mime))
        await _emit(pool, job_id, "progress", {"progress": 80, "phase": "stored"})

        # ── 5) DB 등록 tx(lease 펜스) ──
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "select id from jobs where id=%s and locked_by=%s and status='running' for update",
                    (job_id, lease))
                if await cur.fetchone() is None:
                    raise RuntimeError("lease_lost")
                for view, key, mime in registered:
                    await cur.execute(
                        "insert into fm_model_assets (model_id, view, r2_key, mime, bucket) "
                        "values (%s,%s,%s,%s,'face') on conflict (model_id, view) "
                        "do update set r2_key=excluded.r2_key, mime=excluded.mime",
                        (model_id, view, key, mime))
                await cur.execute(
                    "update fm_models set assets_status='ready', qc_score=%s, assets_source_hash=%s "
                    "where id=%s", (qc_score, src_hash, model_id))
                await cur.execute(
                    "update jobs set status='done', progress=100, locked_by=null, "
                    "locked_at=null, finished_at=now(), result=%s where id=%s",
                    (Json({"data": {"modelId": model_id, "assetsStatus": "ready"}}), job_id))
                await cur.execute(
                    "insert into job_events (job_id, event_type, payload) values (%s,'done',%s)",
                    (job_id, Json({"data": {"modelId": model_id}})))
            await conn.commit()
        put_keys = []  # 커밋 성공 → 정리 대상 아님
    except Exception as e:
        await _fail("자산 생성 중 오류가 발생했어요.", {"error": str(e)[:200]})
