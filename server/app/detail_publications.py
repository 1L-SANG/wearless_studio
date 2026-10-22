"""Immutable JPEG publications hosted independently of project lifetime."""

from __future__ import annotations

import base64
import asyncio
import calendar
import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from . import repo
from .auth import require_user
from .db import get_conn
from .models import CamelModel


log = logging.getLogger("wearless.detail_publications")
router = APIRouter(prefix="/v1", tags=["Detail publications"])


MAX_FILES = 40
MAX_FILE_BYTES = 3 * 1024 * 1024
MAX_TOTAL_BYTES = 60 * 1024 * 1024
UPLOAD_TTL = 300
RATE_LIMIT_PER_HOUR = 20
CACHE_CONTROL = "public, max-age=31536000, immutable"


class TokenInvalid(Exception):
    """The upload capability is malformed, expired, or has been altered."""


def publication_key(user_id: str, publication_id: str, index: int) -> str:
    return f"pub/{user_id}/{publication_id}/{index:02d}.jpg"


def make_file_token(secret, *, user_id, publication_id, index, key, byte_size, expires_at) -> str:
    payload = json.dumps({"u": user_id, "p": publication_id, "i": index, "k": key,
                          "b": byte_size, "e": int(expires_at)}, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    mac = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def parse_file_token(secret, token) -> dict:
    try:
        body, mac = str(token).split(".", 1)
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, mac):
            raise ValueError("signature")
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if not isinstance(data, dict) or int(data["e"]) < time.time():
            raise ValueError("expired")
        if (type(data["i"]) is not int or not 1 <= data["i"] <= MAX_FILES
                or type(data["b"]) is not int or not 0 < data["b"] <= MAX_FILE_BYTES
                or not all(isinstance(data[k], str) and data[k] for k in ("u", "p", "k"))):
            raise ValueError("payload")
        return {"user_id": data["u"], "publication_id": data["p"], "index": data["i"],
                "key": data["k"], "byte_size": data["b"]}
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise TokenInvalid("invalid_upload_token") from None


def compute_expires_at(plan: str, *, created_at: datetime, now: datetime,
                       current: datetime | None) -> datetime | None:
    if plan != "free":
        return None
    if current is not None:
        return current
    month = now.month - 1 + 6
    year, month = now.year + month // 12, month % 12 + 1
    return now.replace(year=year, month=month,
                       day=min(now.day, calendar.monthrange(year, month)[1]))


class PublicationFile(CamelModel):
    index: int
    byte_size: int
    width: int
    height: int


class CreatePublication(CamelModel):
    preset: str
    width: int
    product_name: str = ""
    files: list[PublicationFile]


class UploadedFile(CamelModel):
    index: int
    upload_token: str


class CompletePublication(CamelModel):
    uploads: list[UploadedFile]


def _error(code, message, status=400, **extra):
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def _configured(request):
    settings = request.app.state.settings
    if not settings.detail_publish_token_secret:
        raise _error("detail_publish_unconfigured", "몰에 올리기 기능이 아직 준비되지 않았어요.", 503)
    return settings.detail_publish_token_secret


def _storage(request):
    r2 = getattr(request.app.state, "r2", None)
    if r2 is None or not request.app.state.settings.r2_public_base:
        raise _error("detail_publish_unconfigured", "이미지 보관 기능이 아직 준비되지 않았어요.", 503)
    return r2


async def _assert_account_open(conn, user_id):
    if await repo.user_account_purge_closed(conn, user_id):
        raise _error("account_closed", "계정 삭제가 완료되어 사용할 수 없습니다.", 404)


def publication_files(row):
    """Owner-writable JSON never supplies a key for another owner's object."""
    files = row.get("files") or []
    if (not isinstance(files, list) or not 0 < len(files) <= MAX_FILES
            or any(not isinstance(f, dict) or type(f.get("index")) is not int for f in files)
            or sorted(f["index"] for f in files) != list(range(1, len(files) + 1))):
        raise ValueError("이미지 조각 정보를 다시 확인해 주세요.")
    return [dict(f, r2_key=publication_key(row["user_id"], row["id"], f["index"]))
            for f in sorted(files, key=lambda f: f["index"])]


def _result(row, r2, *, listing=False):
    result = {"publicationId": row["id"], "version": row["version"],
              "files": [{"index": f["index"], "url": r2.public_url(f["r2_key"]),
                         "width": f["width"], "height": f["height"], "bytes": f["bytes"]}
                        for f in publication_files(row)],
              "expiresAt": row["expires_at"], "createdAt": row["created_at"]}
    if listing:
        result.update(preset=row["preset"], width=row["width"], sliceCount=row["slice_count"],
                      totalBytes=row["total_bytes"])
    return result


@router.post("/projects/{project_id}/detail-publications")
async def create_publication(request: Request, project_id: str, body: CreatePublication,
                             user_id: str = Depends(require_user)):
    secret = _configured(request)
    r2 = _storage(request)
    if not 0 < len(body.files) <= MAX_FILES:
        raise _error("too_many_files", "이미지는 1개부터 40개까지 올릴 수 있어요.")
    if any(not 0 < f.byte_size <= MAX_FILE_BYTES for f in body.files):
        raise _error("file_too_large", "이미지 한 장은 3MB 이하여야 해요.")
    if sum(f.byte_size for f in body.files) > MAX_TOTAL_BYTES:
        raise _error("total_too_large", "이미지 전체 용량은 60MB 이하여야 해요.")
    if ({"smartstore": 860, "coupang": 780, "high": 1290}.get(body.preset) != body.width
            or sorted(f.index for f in body.files) != list(range(1, len(body.files) + 1))
            or any(f.width != body.width or not 0 < f.height <= 3000 for f in body.files)):
        raise _error("invalid_files", "이미지 폭과 조각 번호를 다시 확인해 주세요.")
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _error("project_not_found", "프로젝트를 찾을 수 없어요.", 404)
        await _assert_account_open(conn, user_id)
        if await repo.project_uses_real_model(conn, user_id=user_id, project_id=project_id):
            raise _error("real_model_not_allowed", "실존 모델 컷은 다운로드로 받아 몰에 직접 올려 주세요.", 403)
        if await repo.count_detail_publications_last_hour(conn, user_id) >= RATE_LIMIT_PER_HOUR:
            raise _error("rate_limited", "한 시간에 20번까지 올릴 수 있어요. 잠시 후 다시 시도해 주세요.", 429)
        revision = await repo.get_editor_revision(conn, project_id)
        row = await repo.create_detail_publication(
            conn, user_id=user_id, project_id=project_id, preset=body.preset, width=body.width,
            product_name=body.product_name, editor_revision=revision,
            files=[{"index": f.index, "bytes": f.byte_size, "width": f.width, "height": f.height}
                   for f in body.files],
        )
        uploads = []
        for f in publication_files(row):
            key = f["r2_key"]
            uploads.append({"index": f["index"],
                            "uploadUrl": r2.presigned_put(key, "image/jpeg", expires=UPLOAD_TTL,
                                                         byte_size=f["bytes"]),
                            "uploadToken": make_file_token(secret, user_id=user_id, publication_id=row["id"],
                                index=f["index"], key=key, byte_size=f["bytes"], expires_at=time.time() + UPLOAD_TTL)})
        await conn.commit()
    return {"publicationId": row["id"], "version": row["version"], "uploads": uploads}


@router.post("/detail-publications/{publication_id}/complete")
async def complete_publication(request: Request, publication_id: str, body: CompletePublication,
                               user_id: str = Depends(require_user)):
    secret = _configured(request)
    r2 = _storage(request)
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        row = await repo.get_detail_publication(conn, user_id=user_id, publication_id=publication_id)
        if row is None or row["status"] == "deleted":
            raise _error("publication_not_found", "게시한 상세페이지를 찾을 수 없어요.", 404)
        try:
            if row["status"] == "ready":
                return _result(row, r2)
            files = publication_files(row)
        except ValueError as exc:
            raise _error("invalid_files", str(exc)) from None
        tokens = {}
        try:
            for upload in body.uploads:
                token = parse_file_token(secret, upload.upload_token)
                if (upload.index in tokens or token["index"] != upload.index
                        or token["user_id"] != user_id or token["publication_id"] != publication_id
                        or token["key"] != publication_key(user_id, publication_id, upload.index)):
                    raise TokenInvalid()
                tokens[upload.index] = token
            if set(tokens) != {f["index"] for f in files}:
                raise TokenInvalid()
            if any(tokens[f["index"]]["byte_size"] != f.get("bytes") for f in files):
                raise TokenInvalid()
        except TokenInvalid:
            raise _error("invalid_upload_token", "업로드 확인 시간이 지났어요. 다시 올려 주세요.", 403) from None
        semaphore = asyncio.Semaphore(6)

        async def storage_call(fn, *args):
            async with semaphore:
                return await asyncio.to_thread(fn, *args)

        heads = await asyncio.gather(*(storage_call(r2.head, f["r2_key"]) for f in files))
        missing = [f["index"] for f, head in zip(files, heads)
                   if not head or head.get("size") != f["bytes"] or head.get("mime") != "image/jpeg"]
        if missing:
            raise _error("files_missing", "일부 이미지를 다시 올려 주세요.", 409, missing=missing)
        await asyncio.gather(*(storage_call(r2.copy_with_cache, f["r2_key"], "image/jpeg", CACHE_CONTROL)
                               for f in files))
        for f in files:
            asset_id = str(uuid.uuid5(uuid.UUID(publication_id), str(f["index"])))
            await repo.create_asset(conn, asset_id=asset_id, user_id=user_id, project_id=row["project_id"],
                source="export", visibility="public", bucket=request.app.state.settings.r2_bucket,
                key=f["r2_key"], mime="image/jpeg", size=f["bytes"], original_filename=f"{f['index']:02d}.jpg",
                metadata={"publication_id": publication_id})
            f["asset_id"] = asset_id
        account = await repo.get_account(conn, user_id)
        expiry = compute_expires_at((account or {}).get("plan") or "free", created_at=row["created_at"],
                                    now=row["created_at"], current=None)
        row = await repo.mark_detail_publication_ready(conn, publication_id=publication_id,
                  files=files, total_bytes=sum(f["bytes"] for f in files), expires_at=expiry)
        await conn.commit()
    return _result(row, r2)


@router.get("/projects/{project_id}/detail-publications")
async def list_publications(request: Request, project_id: str, user_id: str = Depends(require_user)):
    _configured(request)
    r2 = _storage(request)
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _error("project_not_found", "프로젝트를 찾을 수 없어요.", 404)
        rows = await repo.list_detail_publications(conn, user_id=user_id, project_id=project_id)
    try:
        return {"items": [_result(row, r2, listing=True) for row in rows]}
    except ValueError as exc:
        raise _error("invalid_files", str(exc)) from None


async def delete_publication_objects(r2, row):
    keys = [f["r2_key"] for f in publication_files(row)]
    for key in keys:
        await asyncio.to_thread(r2.delete, key)
    await asyncio.to_thread(r2.purge_public_cache, keys)


async def purge_user_detail_publications(app, conn, *, user_id) -> int:
    """Persist deletion intent before storage so paid-plan expiry cannot undo a retry."""
    async with conn.cursor() as cur:
        await cur.execute(
            """select id::text as id, user_id::text as user_id, files, status
                 from detail_publications where user_id = %s
                  and (status in ('ready', 'pending') or (status = 'deleted' and deleted_at is null))
                 for update""",
            (user_id,),
        )
        rows = await cur.fetchall()
    for row in rows:
        await repo.soft_delete_detail_publication(conn, user_id=user_id, publication_id=row["id"])
    await conn.commit()
    deleted = 0
    for row in rows:
        try:
            await delete_publication_objects(app.state.r2, row)
            await repo.finalize_detail_publication_deletion(conn, publication_id=row["id"])
            await conn.commit()
            deleted += 1
        except Exception:
            await conn.rollback()
            log.exception("account publication cleanup deferred: %s", row["id"])
    return deleted


@router.delete("/detail-publications/{publication_id}", status_code=204)
async def delete_publication(request: Request, publication_id: str, user_id: str = Depends(require_user)):
    _configured(request)
    r2 = _storage(request)
    async with get_conn(request) as conn:
        row = await repo.get_detail_publication(conn, user_id=user_id, publication_id=publication_id)
        if row is None:
            raise _error("publication_not_found", "게시한 상세페이지를 찾을 수 없어요.", 404)
        if row["deleted_at"] is not None:
            return Response(status_code=204)
        row = await repo.soft_delete_detail_publication(conn, user_id=user_id, publication_id=publication_id)
        await conn.commit()
    try:
        await delete_publication_objects(r2, row)
        async with get_conn(request) as conn:
            await repo.finalize_detail_publication_deletion(conn, publication_id=publication_id)
            await conn.commit()
    except ValueError as exc:
        raise _error("invalid_files", str(exc)) from None
    except Exception:
        log.exception("detail publication cleanup deferred: %s", publication_id)
    return Response(status_code=204)
