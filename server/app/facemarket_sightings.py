"""모델 제보 — "내 얼굴 찾기 신고"(2026-09-27).

모델이 쇼핑몰 등에서 자기 얼굴이 쓰인 이미지를 발견하면 마이페이지에서 올린다. 서버는 관리자 추적과
같은 대조(facemarket_trace.trace_image — 워터마크 → 지문)를 돌려 결과를 발견 원장(fm_trace_findings,
source='model_report')에 남기고, 알림 워커가 관리자 슬랙으로 알린다. 매칭이 없어도 남긴다 —
워터마크·지문이 못 잡는 무단 사용(재촬영·딥페이크)이면 관리자가 직접 봐야 한다.

🔴 모델에게는 접수 사실과 관리자 판정 상태만 돌려준다. 셀러·배포본 정보는 관리자 콘솔(마스킹·감사)
   에서만 본다. 이미지 바이트는 저장하지 않는다(해시만) — 발견한 페이지 주소를 함께 받는 이유다.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from . import admin_guard, facemarket_trace, fm_trace_findings
from .auth import require_user
from .db import get_conn
from .facemarket import _err

logger = logging.getLogger("facemarket.sightings")

router = APIRouter(prefix="/v1/facemarket/me", tags=["FaceMarket"])

MAX_REPORT_BYTES = 30 * 1024 * 1024
DAILY_REPORT_LIMIT = 10
MAX_PAGE_URL = 500
MAX_NOTE = 1000


def _clean_page_url(raw: str | None) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    parsed = urlparse(value)
    if (len(value) > MAX_PAGE_URL or parsed.scheme not in ("http", "https")
            or not parsed.netloc):
        raise _err("invalid_page_url", "발견한 페이지 주소는 http(s):// 로 시작하는 500자 이하 주소로 적어 주세요.")
    return value


def _clean_note(raw: str | None) -> str | None:
    value = (raw or "").strip()
    if len(value) > MAX_NOTE:
        raise _err("note_too_long", "메모는 1000자 이하로 적어 주세요.")
    return value or None


async def _own_model_id(conn, user_id: str) -> str:
    async with conn.cursor() as cur:
        await cur.execute(
            "select id::text as id from fm_models where user_id = %s "
            "order by created_at desc limit 1",
            (user_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise _err("model_not_found", "모델 등록 정보가 없어요.", status=404)
    return row["id"]


@router.post("/sightings", status_code=201)
async def report_sighting(
    request: Request,
    image: UploadFile = File(...),
    page_url: str | None = Form(None, alias="pageUrl"),
    note: str | None = Form(None),
    user_id: str = Depends(require_user),
):
    """모델 제보 접수. 모델 확인·하루 한도·입력 검사가 이미지 바이트를 읽기 전에 끝난다."""
    clean_url = _clean_page_url(page_url)
    clean_note = _clean_note(note)
    async with get_conn(request) as conn:
        model_id = await _own_model_id(conn, user_id)
        async with conn.cursor() as cur:
            await cur.execute(
                "select count(*) as n from fm_trace_findings "
                "where reporter_model_id = %s and created_at > now() - interval '1 day'",
                (model_id,),
            )
            today = (await cur.fetchone())["n"]
    if today >= DAILY_REPORT_LIMIT:
        raise _err("report_limit", "제보는 하루 10건까지 보낼 수 있어요. 내일 다시 보내 주세요.",
                   status=429)
    data = await image.read(MAX_REPORT_BYTES + 1)
    if not data:
        raise _err("empty_upload", "빈 파일은 사용할 수 없어요.")
    if len(data) > MAX_REPORT_BYTES:
        raise _err("file_too_large", "이미지는 30MB 이하만 올릴 수 있어요.", status=413)
    settings = request.app.state.settings
    async with get_conn(request) as conn:
        try:
            result = await facemarket_trace.trace_image(
                conn, data, origin=settings.public_web_origin)
        except ValueError:
            raise _err("invalid_image",
                       "이미지를 읽을 수 없어요. PNG·JPEG·WebP 파일인지 확인해 주세요.") from None
        del data
        candidates = result["candidates"]
        recorded = await fm_trace_findings.record_model_report(
            conn, reporter_model_id=model_id, reporter_user_id=user_id, page_url=clean_url,
            note=clean_note, image_sha256=result["image"]["sha256"], candidates=candidates,
        )
        await admin_guard.write_audit(
            conn,
            actor_user_id=user_id,
            action="facemarket.sighting_report",
            target_type="fm_model",
            target_id=model_id,
            after={"sha256Prefix": result["image"]["sha256Prefix"],
                   "candidates": len(candidates), "findingId": recorded["id"]},
        )
        await conn.commit()
    logger.info("sighting report model=%s candidates=%d finding=%s",
                model_id, len(candidates), recorded["id"])
    return JSONResponse({"id": recorded["id"], "status": "received"}, status_code=201)


@router.get("/sightings")
async def list_my_sightings(request: Request, user_id: str = Depends(require_user)):
    """내 제보 목록(최근 20건) — 접수 시각·관리자 판정 상태·적어 낸 주소만."""
    async with get_conn(request) as conn:
        model_id = await _own_model_id(conn, user_id)
        async with conn.cursor() as cur:
            await cur.execute(
                "select id::text as id, status, created_at, report_page_url "
                "from fm_trace_findings where reporter_model_id = %s "
                "order by created_at desc limit 20",
                (model_id,),
            )
            rows = await cur.fetchall() or []
    return JSONResponse({"items": [
        {"id": r["id"], "status": r["status"], "createdAt": r["created_at"].isoformat(),
         "pageUrl": r.get("report_page_url")}
        for r in rows
    ]}, headers={"Cache-Control": "no-store"})
