"""관리자 출처 추적 — 쇼핑몰에서 발견한 이미지 → 배포본·셀러·모델·라이선스 후보(2026-09-26).

POST /v1/facemarket/admin/trace (multipart `image`)
  1. 워터마크 판독(fm_watermark) — 전체 이미지 + 세로 1000px 창. 코드가 원장의 wm_code 와
     맞으면 그 배포본이 **확실한 후보**(confidence='high').
  2. 지각 해시(fm_fingerprint) — 발견 이미지 전체 + 700px 창(20px 간격)을 원장 지문 전수와
     해밍 비교. 워터마크가 지워진 경우의 두 번째 줄이다(pHash ≤4 'medium', ≤10 'low').
  3. 후보마다 배포본/컷 → 프로젝트·셀러(마스킹)·모델 표시명·라이선스 상태·검증 URL.

🔴 이미지 바이트는 저장·로그하지 않는다. 감사 원장(admin_audit_log)에는 sha256 앞 12자와
   결과 요약만 남긴다 — 누가 언제 어떤 추적을 돌렸는지는 남아야 한다(셀러 신원이 드러나는 화면).
관리자 판정은 다른 관리자 라우트와 같은 admin_guard 가드다 — 기기 게이트까지 같다.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time

from typing import Literal

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from . import admin_guard, fm_fingerprint_store, fm_trace_findings
from .auth import require_user
from .db import get_conn
from .models import CamelModel
from .facemarket import _err, _mask_name
from .fm_trace_findings import _mask_email
from .services import fm_fingerprint, fm_watermark

logger = logging.getLogger("facemarket.trace")

router = APIRouter(prefix="/v1/facemarket/admin", tags=["FaceMarket admin console"])

MAX_TRACE_BYTES = 60 * 1024 * 1024
MAX_CANDIDATES = 10


def _analyze(data: bytes) -> dict:
    """동기 — 디코드 1회로 워터마크 판독 + 조회 해시. to_thread 로 부른다."""
    img = fm_fingerprint.open_image(data)
    t0 = time.monotonic()
    wm = fm_watermark.read_image(img)
    wm_ms = int((time.monotonic() - t0) * 1000)
    queries = fm_fingerprint.query_fingerprints(img)
    return {"width": img.width, "height": img.height, "wm": wm, "wm_ms": wm_ms,
            "queries": queries}


def rank_candidates(matches: list[dict], wm_publication_ids: list[str]) -> list[dict]:
    """지문 매치(행 단위)를 대상(배포본·컷) 단위로 묶고 순위를 매긴다. 순수 함수."""
    groups: dict[tuple[str, str], dict] = {}
    for m in matches:
        row = m["row"]
        key = (("publication", row["publication_id"]) if row.get("publication_id")
               else ("cut", row["output_record_id"]))
        g = groups.setdefault(key, {"best": None, "count": 0, "watermark": False})
        g["count"] += 1
        best = g["best"]
        if best is None or (m["phash_distance"], m["dhash_distance"]) < (
                best["phash_distance"], best["dhash_distance"]):
            g["best"] = m
    for pid in wm_publication_ids:
        groups.setdefault(("publication", pid), {"best": None, "count": 0, "watermark": False})
        groups[("publication", pid)]["watermark"] = True
    out = []
    for (target, target_id), g in groups.items():
        best = g["best"]
        if g["watermark"]:
            confidence = "high"
        elif best is not None and best["phash_distance"] <= fm_fingerprint.STRONG_PHASH:
            confidence = "medium"
        else:
            confidence = "low"
        row = best["row"] if best else None
        out.append({
            "target": target, "target_id": target_id, "confidence": confidence,
            "watermark": g["watermark"], "matched_fingerprints": g["count"],
            "phash_distance": best["phash_distance"] if best else None,
            "dhash_distance": best["dhash_distance"] if best else None,
            "matched_kind": row["kind"] if row else None,
            "region": ({"y0": row["region_y0"], "y1": row["region_y1"]}
                       if row and row.get("region_y0") is not None else None),
            "query": best["query"] if best else None,
        })
    rank = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda c: (rank[c["confidence"]],
                            c["phash_distance"] if c["phash_distance"] is not None else 99,
                            -c["matched_fingerprints"]))
    return out[:MAX_CANDIDATES]


_PUBLICATION_DETAIL_SQL = """
select p.id::text as id, p.project_id::text as project_id, p.seller_id::text as seller_id,
       p.model_id::text as model_id, p.license_ref::text as license_ref, p.kind,
       p.created_at, p.revoked_at, p.wm_status, p.c2pa_status, p.chain_status,
       pr.title as project_title, m.display_name as model_name,
       l.status as license_status, l.license_valid_until,
       u.email as seller_email, pf.display_name as seller_name
  from fm_publication_records p
  left join projects pr on pr.id = p.project_id
  left join fm_models m on m.id = p.model_id
  left join fm_licenses l on l.id = p.license_id
  left join auth.users u on u.id = p.seller_id
  left join profiles pf on pf.user_id = p.seller_id
 where p.id = any(%s::uuid[])
"""

_OUTPUT_DETAIL_SQL = """
select r.id::text as id, r.asset_id::text as asset_id, r.job_id::text as job_id,
       j.project_id::text as project_id, r.seller_id::text as seller_id,
       r.model_id::text as model_id, r.license_ref::text as license_ref, r.created_at,
       pr.title as project_title, m.display_name as model_name,
       l.status as license_status, l.license_valid_until,
       u.email as seller_email, pf.display_name as seller_name
  from fm_output_records r
  left join jobs j on j.id = r.job_id
  left join projects pr on pr.id = j.project_id
  left join fm_models m on m.id = r.model_id
  left join fm_licenses l on l.id = r.license_id
  left join auth.users u on u.id = r.seller_id
  left join profiles pf on pf.user_id = r.seller_id
 where r.id = any(%s::uuid[])
"""


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _candidate_view(c: dict, detail: dict | None, origin: str) -> dict:
    d = detail or {}
    is_pub = c["target"] == "publication"
    return {
        "target": c["target"],
        "confidence": c["confidence"],
        "evidence": {
            "watermark": c["watermark"],
            "phashDistance": c["phash_distance"],
            "dhashDistance": c["dhash_distance"],
            "matchedKind": c["matched_kind"],
            "matchedFingerprints": c["matched_fingerprints"],
            "region": c["region"],
            "queryWindow": c["query"],
        },
        "publicationId": c["target_id"] if is_pub else None,
        "outputRecordId": None if is_pub else c["target_id"],
        "assetId": d.get("asset_id"),
        "jobId": d.get("job_id"),
        "publicationKind": d.get("kind"),
        "projectId": d.get("project_id"),
        "projectTitle": d.get("project_title"),
        "seller": {
            "id": d.get("seller_id"),
            "emailMasked": _mask_email(d.get("seller_email")),
            "nameMasked": _mask_name(d["seller_name"]) if d.get("seller_name") else None,
        },
        "model": {"id": d.get("model_id"), "displayName": d.get("model_name")},
        "license": {
            "id": d.get("license_ref"),
            "status": d.get("license_status") or ("deleted" if d else None),
            "validUntil": _iso(d.get("license_valid_until")),
        },
        "createdAt": _iso(d.get("created_at")),
        "revokedAt": _iso(d.get("revoked_at")),
        "verifyUrl": f"{origin}/verify/p/{c['target_id']}" if is_pub else None,
        "found": bool(detail),
    }


async def _details(conn, candidates: list[dict]) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    pub_ids = [c["target_id"] for c in candidates if c["target"] == "publication"]
    cut_ids = [c["target_id"] for c in candidates if c["target"] == "cut"]
    async with conn.cursor() as cur:
        if pub_ids:
            await cur.execute(_PUBLICATION_DETAIL_SQL, (pub_ids,))
            for r in await cur.fetchall() or []:
                out[("publication", r["id"])] = r
        if cut_ids:
            await cur.execute(_OUTPUT_DETAIL_SQL, (cut_ids,))
            for r in await cur.fetchall() or []:
                out[("cut", r["id"])] = r
    return out


async def trace_image(conn, data: bytes, *, origin: str, rows: list[dict] | None = None) -> dict:
    """발견 이미지 바이트 → 워터마크 판독 + 지문 후보(관리자 추적·모델 제보·순찰이 같이 쓴다).

    감사 기록·커밋은 하지 않는다(호출부 몫). rows = 미리 적재한 지문 — 순찰처럼 한 번에 수십 장을
    대조할 때 매번 전수 적재하지 않으려고 넘긴다. 디코드 실패는 ValueError.
    """
    sha256 = hashlib.sha256(data).hexdigest()
    try:
        analysis = await asyncio.to_thread(_analyze, data)
    except Exception as exc:
        raise ValueError("invalid image") from exc
    wm: fm_watermark.WatermarkRead = analysis["wm"]
    wm_hits: dict[int, str] = {}
    if wm.codes:
        async with conn.cursor() as cur:
            await cur.execute(
                "select id::text as id, wm_code from fm_publication_records "
                "where wm_code = any(%s::bigint[])",
                (list(wm.codes),),
            )
            for r in await cur.fetchall() or []:
                wm_hits[int(r["wm_code"])] = r["id"]
    if rows is None:
        rows = await fm_fingerprint_store.load_fingerprints(conn)
    matches = await asyncio.to_thread(fm_fingerprint.search, analysis["queries"], rows)
    ranked = rank_candidates(matches, list(dict.fromkeys(wm_hits.values())))
    details = await _details(conn, ranked)
    candidates = [
        _candidate_view(c, details.get((c["target"], c["target_id"])), origin) for c in ranked
    ]
    matched_code = next((code for code in sorted(wm.codes, key=lambda k: -wm.codes[k])
                         if code in wm_hits), None)
    if matched_code is not None:
        wm_status = "matched"
    elif wm.code is not None and wm.votes >= 2:
        wm_status = "unregistered"     # 코드는 읽혔지만 이 원장에 없다(다른 환경 배포본 등)
    else:
        wm_status = "not_found"
    shown_code = matched_code if matched_code is not None else (
        wm.code if wm_status == "unregistered" else None)
    return {
        "image": {"width": analysis["width"], "height": analysis["height"],
                  "sha256": sha256, "sha256Prefix": sha256[:12]},
        "watermark": {
            "status": wm_status,
            "code": f"{shown_code:08x}" if shown_code is not None else None,
            "votes": wm.codes.get(shown_code, 0) if shown_code is not None else 0,
            "windows": wm.windows,
            "syncScore": round(wm.best_z, 1),
            "publicationId": wm_hits.get(matched_code) if matched_code is not None else None,
        },
        "candidates": candidates,
        "stats": {"fingerprintsScanned": len(rows), "queryWindows": len(analysis["queries"]),
                  "watermarkMs": analysis["wm_ms"]},
    }


@router.post("/trace")
async def admin_trace(
    request: Request,
    image: UploadFile = File(...),
    user_id: str = Depends(require_user),
):
    """발견 이미지 → 출처 후보. 관리자 전용·감사 기록."""
    started = time.monotonic()
    # 관리자 확인이 먼저다 — 비관리자가 큰 업로드로 메모리를 쓰게 두지 않는다(테스트컷 업로드 선례).
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id, request)
    data = await image.read(MAX_TRACE_BYTES + 1)
    if not data:
        raise _err("empty_upload", "빈 파일은 사용할 수 없어요.")
    if len(data) > MAX_TRACE_BYTES:
        raise _err("file_too_large", "이미지는 60MB 이하만 올릴 수 있어요.", status=413)
    s = request.app.state.settings
    async with get_conn(request) as conn:
        try:
            result = await trace_image(conn, data, origin=s.public_web_origin)
        except ValueError:
            logger.info("trace_decode_failed sha=%s bytes=%d",
                        hashlib.sha256(data).hexdigest()[:12], len(data))
            raise _err("invalid_image",
                       "이미지를 읽을 수 없어요. PNG·JPEG·WebP 파일인지 확인해 주세요.")
        del data   # 이후 단계는 바이트가 필요 없다 — 요청이 끝날 때까지 들고 있지 않는다
        watermark, candidates = result["watermark"], result["candidates"]
        sha_prefix = result["image"]["sha256Prefix"]
        elapsed = int((time.monotonic() - started) * 1000)
        await admin_guard.write_audit(
            conn,
            actor_user_id=user_id,
            action="facemarket.trace",
            target_type="image_trace",
            target_id=watermark["publicationId"],
            after={
                "sha256Prefix": sha_prefix,
                "watermark": watermark["status"],
                "candidates": len(candidates),
                "publicationIds": [c["publicationId"] for c in candidates if c["publicationId"]],
                "outputRecordIds": [c["outputRecordId"] for c in candidates
                                    if c["outputRecordId"]],
            },
        )
        await conn.commit()
    stats = result["stats"]
    logger.info(
        "trace sha=%s wm=%s candidates=%d fingerprints=%d windows=%d ms=%d",
        sha_prefix, watermark["status"], len(candidates), stats["fingerprintsScanned"],
        stats["queryWindows"], elapsed,
    )
    image_info = {k: v for k, v in result["image"].items() if k != "sha256"}
    return JSONResponse({
        "image": image_info,
        "watermark": watermark,
        "candidates": candidates,
        "stats": {**stats, "elapsedMs": elapsed},
    })


# ---------- 자동 발견(순찰·모델 제보) 목록·판정 ----------

class FindingStatusRequest(CamelModel):
    status: Literal["new", "seller_own", "misuse", "dismissed"]
    remember_store: bool = False


def _finding_error(e: fm_trace_findings.FindingError):
    return _err(e.code, e.message, status=e.status)


@router.get("/trace/findings")
async def admin_list_trace_findings(
    request: Request,
    status: str | None = Query(None),
    source: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, max_length=256),
    user_id: str = Depends(require_user),
):
    """자동 발견 목록(최근 본 순). 셀러는 마스킹. 관리자 전용."""
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id, request)
        try:
            result = await fm_trace_findings.list_findings(
                conn, status=status, source=source, limit=limit, cursor=cursor)
        except fm_trace_findings.FindingError as e:
            raise _finding_error(e) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.patch("/trace/findings/{finding_id}")
async def admin_update_trace_finding(
    request: Request,
    finding_id: str,
    body: FindingStatusRequest,
    user_id: str = Depends(require_user),
):
    """관리자 판정. seller_own + rememberStore 면 그 판매처를 셀러 본인 것으로 기억해 다음 순찰부터
    알리지 않는다. 감사 원장에 전후 상태가 남는다."""
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id, request)
        try:
            result = await fm_trace_findings.update_finding_status(
                conn, finding_id=finding_id, actor=user_id, status=body.status,
                remember_store=body.remember_store, write_audit=admin_guard.write_audit)
        except fm_trace_findings.FindingError as e:
            await conn.rollback()
            raise _finding_error(e) from None
        await conn.commit()
    return JSONResponse(result)
