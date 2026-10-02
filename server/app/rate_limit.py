"""분석 라우트 공용 한도 검사. 리미터 장애는 기능을 막지 않는다(fail-open)."""

import logging

from fastapi import HTTPException

logger = logging.getLogger("wearless.rate_limit")


def enforce(limiter, key: str) -> None:
    """한도를 넘었으면 429. 리미터가 고장 나면 통과시킨다 — 안전밸브가 본 기능을 죽이지 않게."""
    if limiter is None:
        return
    try:
        allowed = limiter.allow(key)
    except Exception:
        logger.exception("rate limiter unavailable")
        return
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={"code": "rate_limited", "message": "잠시 후 다시 시도해주세요."},
        )


def user_key(user_id: str) -> str:
    return f"user:{user_id}"
