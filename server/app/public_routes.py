"""인증 전 공개 체험 API. 프로젝트·DB·R2에 아무것도 영속하지 않는다."""

import logging
import time
from collections import deque
from ipaddress import ip_address

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from .agents.vision_llm import VisionError
from .r2 import ext_for_mime
from .routes import MAX_UPLOAD_BYTES
from .workers.analyze_job import analyze_image_bytes

logger = logging.getLogger("wearless.public_analysis")
router = APIRouter(prefix="/v1/public")

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


@router.post("/analyze", tags=["Public Analysis"], summary="비로그인 상품 사진 AI 분석")
async def public_analyze(
    request: Request,
    images: list[UploadFile] = File(...),
    slots: list[str] = Form(default=[]),
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

    source_images: list[tuple[bytes, str]] = []
    for upload in images:
        mime = (upload.content_type or "").lower()
        if ext_for_mime(mime) is None:
            raise _bad_request("unsupported_type", "지원하지 않는 이미지 형식입니다.")
        data = await upload.read(MAX_UPLOAD_BYTES + 1)
        if not data:
            raise _bad_request("empty_file", "비어 있는 사진은 분석할 수 없어요.")
        if len(data) > MAX_UPLOAD_BYTES:
            raise _bad_request("file_too_large", "사진 한 장은 25MB보다 작아야 해요.")
        source_images.append((data, mime))

    try:
        valid_slots = {"Front", "Back", "Detail", "BackDetail"}
        image_slots = slots if len(slots) == len(source_images) and all(s in valid_slots for s in slots) else None
        core = await analyze_image_bytes(
            request.app.state.settings, source_images, slots=image_slots)
    except VisionError:
        raise HTTPException(
            status_code=502,
            detail={"code": "analysis_failed", "message": "상품 분석에 실패했어요. 다시 시도해 주세요."},
        ) from None
    return JSONResponse({"data": core["result_data"]})
