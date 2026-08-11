"""인증 전 공개 체험 API. 프로젝트·DB·R2에 아무것도 영속하지 않는다."""

import asyncio
import logging
import time
from collections import deque
from ipaddress import ip_address

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from .agents.vision_llm import VisionError
from .r2 import ext_for_mime
from .routes import MAX_UPLOAD_BYTES
from .workers.analyze_job import analyze_image_bytes

logger = logging.getLogger("wearless.public_analysis")

MAX_PUBLIC_REQUEST_BYTES = 60 * 1024 * 1024
PUBLIC_ANALYSIS_CONCURRENCY = 4
_analysis_semaphore = asyncio.Semaphore(PUBLIC_ANALYSIS_CONCURRENCY)


class PublicAnalysisSizeLimitRoute(APIRoute):
    """FastAPI가 multipart body를 파싱하기 전에 명시된 전체 크기를 거절한다."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def limited_handler(request: Request):
            raw_length = request.headers.get("content-length")
            try:
                content_length = int(raw_length) if raw_length is not None else None
            except ValueError:
                content_length = None
            if content_length is not None and content_length > MAX_PUBLIC_REQUEST_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"error": {
                        "code": "request_too_large",
                        "message": "한 번에 올릴 수 있는 사진 용량을 초과했어요.",
                    }},
                )
            return await original(request)

        return limited_handler


router = APIRouter(prefix="/v1/public", route_class=PublicAnalysisSizeLimitRoute)

_HOUR_SECONDS = 60 * 60
_DAY_SECONDS = 24 * _HOUR_SECONDS


class PublicAnalysisRateLimiter:
    """IP별 단일 프로세스 슬라이딩 윈도 리미터.

    레코드는 하루 뒤 제거한다. 다중 인스턴스에서는 각 인스턴스별 한도이므로 완전한 전역
    제한이 아니다. 공개 체험의 조용한 안전밸브이며, 강한 제한이 필요해지면 공유 저장소로 교체한다.
    """

    def __init__(self, hourly_limit: int = 10, daily_limit: int = 30):
        self.hourly_limit = hourly_limit
        self.daily_limit = daily_limit
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str, *, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        cutoff_day = current - _DAY_SECONDS
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= cutoff_day:
            hits.popleft()
        if not hits:
            self._hits.pop(key, None)
            hits = self._hits.setdefault(key, deque())
        cutoff_hour = current - _HOUR_SECONDS
        hourly = sum(timestamp > cutoff_hour for timestamp in hits)
        if hourly >= self.hourly_limit or len(hits) >= self.daily_limit:
            return False
        hits.append(current)
        return True


def _bad_request(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"code": code, "message": message})


def _client_ip(request: Request) -> str:
    """AWS ALB append-mode XFF의 마지막 주소(실제 ALB 접속자)를 사용한다.

    ALB는 들어온 X-Forwarded-For 뒤에 관측한 client IP를 append하므로 공격자가 앞 값을
    꾸며도 마지막 값은 ALB가 쓴다. 헤더가 없거나 형식이 틀리면 ASGI peer로 안전 폴백한다.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        candidate = forwarded.rsplit(",", 1)[-1].strip()
        try:
            return str(ip_address(candidate))
        except ValueError:
            pass
    return request.client.host if request.client else "unknown"


def _detected_image_mime(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@router.post("/analyze", tags=["Public Analysis"], summary="비로그인 상품 사진 AI 분석")
async def public_analyze(
    request: Request,
    images: list[UploadFile] = File(...),
    slots: list[str] | None = Form(default=None),
):
    """상품 사진 1~4장을 기존 AG-01 코어로 분석한다. 인증·프로젝트·DB 저장은 없다."""
    client_ip = _client_ip(request)
    try:
        limiter = request.app.state.public_analysis_limiter
        allowed = limiter.allow(client_ip)
    except Exception:
        # 리미터 장애가 공개 체험 자체를 막지 않게 fail-open.
        logger.exception("public analysis rate limiter unavailable")
        allowed = True
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={"code": "rate_limited", "message": "잠시 후 다시 시도해주세요."},
        )

    if not 1 <= len(images) <= 4:
        raise _bad_request("invalid_image_count", "상품 사진은 1장부터 4장까지 올려주세요.")

    valid_slots = {"Front", "Back", "Detail", "BackDetail"}
    if slots is not None and (
        len(slots) != len(images) or any(slot not in valid_slots for slot in slots)
    ):
        raise _bad_request("invalid_slots", "사진 위치 정보가 올바르지 않아요.")

    source_images: list[tuple[bytes, str]] = []
    total_bytes = 0
    for upload in images:
        mime = (upload.content_type or "").lower()
        if ext_for_mime(mime) is None:
            raise _bad_request("unsupported_type", "지원하지 않는 이미지 형식입니다.")
        data = await upload.read(MAX_UPLOAD_BYTES + 1)
        if not data:
            raise _bad_request("empty_file", "비어 있는 사진은 분석할 수 없어요.")
        if len(data) > MAX_UPLOAD_BYTES:
            raise _bad_request("file_too_large", "사진 한 장은 25MB보다 작아야 해요.")
        total_bytes += len(data)
        if total_bytes > MAX_PUBLIC_REQUEST_BYTES:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "request_too_large",
                    "message": "한 번에 올릴 수 있는 사진 용량을 초과했어요.",
                },
            )
        if _detected_image_mime(data) != mime:
            raise _bad_request("invalid_image_content", "실제 이미지 파일만 올려주세요.")
        source_images.append((data, mime))

    if _analysis_semaphore.locked():
        raise HTTPException(
            status_code=429,
            detail={"code": "analysis_busy", "message": "잠시 후 다시 시도해주세요."},
        )
    await _analysis_semaphore.acquire()
    try:
        core = await analyze_image_bytes(
            request.app.state.settings, source_images, slots=slots)
    except VisionError:
        raise HTTPException(
            status_code=502,
            detail={"code": "analysis_failed", "message": "상품 분석에 실패했어요. 다시 시도해 주세요."},
        ) from None
    finally:
        _analysis_semaphore.release()
    return JSONResponse({"data": core["result_data"]})
