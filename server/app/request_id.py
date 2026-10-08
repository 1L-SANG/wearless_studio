"""자체 API 요청과 비동기 작업을 잇는 추적 번호."""

from contextvars import ContextVar
from uuid import UUID, uuid4

from starlette.datastructures import Headers, MutableHeaders

current_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def valid_request_id(value) -> str | None:
    if not isinstance(value, str) or len(value) != 36:
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def job_metadata(metadata: dict) -> dict:
    request_id = current_request_id.get()
    return {**metadata, **({"requestId": request_id} if request_id else {})}


class RequestIdMiddleware:
    """CORS 사전 응답에도 번호를 붙이고 요청 context를 작업 생성까지 전달한다."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = valid_request_id(Headers(scope=scope).get("X-Client-Request-Id")) or str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        token = current_request_id.set(request_id)

        async def with_id(message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-Id"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, with_id)
        finally:
            current_request_id.reset(token)
