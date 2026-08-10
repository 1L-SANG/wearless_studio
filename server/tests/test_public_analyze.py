import app.public_routes as public_routes
from app.public_routes import PublicAnalysisRateLimiter
from app.routes import MAX_UPLOAD_BYTES


def _analysis_result():
    return {
        "result_data": {
            "clothingType": "top",
            "subCategory": "knit",
            "fit": "regular",
            "targetGenders": ["women"],
            "materials": [],
            "aiSuggestedPoints": ["골지 짜임"],
            "suggestedName": "데일리 골지 니트",
            "styleTags": ["daily"],
            "swatchSuggestions": [],
            "measurements": [],
        }
    }


def test_public_analyze_succeeds_without_bearer_and_returns_login_shape(client, monkeypatch):
    seen = {}

    async def fake_analyze(settings, source_images, **kwargs):
        seen["images"] = source_images
        seen["slots"] = kwargs.get("slots")
        return _analysis_result()

    monkeypatch.setattr(public_routes, "analyze_image_bytes", fake_analyze)
    response = client.post(
        "/v1/public/analyze",
        files=[
            ("images", ("front.png", b"front-bytes", "image/png")),
            ("images", ("back.png", b"back-bytes", "image/png")),
        ],
        data={"slots": ["Front", "Back"]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["styleTags"] == ["daily"]
    assert response.json()["data"]["measurements"] == []
    assert seen["images"] == [
        (b"front-bytes", "image/png"), (b"back-bytes", "image/png")]
    assert seen["slots"] == ["Front", "Back"]


def test_public_analyze_rejects_unsupported_mime_with_easy_korean_message(client):
    response = client.post(
        "/v1/public/analyze",
        files=[("images", ("notes.txt", b"not-an-image", "text/plain"))],
    )

    assert response.status_code == 400
    assert response.json()["error"] == {
        "code": "unsupported_type",
        "message": "지원하지 않는 이미지 형식입니다.",
    }


def test_public_analyze_rate_limit_returns_429(client, monkeypatch):
    async def fake_analyze(settings, source_images, **kwargs):
        return _analysis_result()

    monkeypatch.setattr(public_routes, "analyze_image_bytes", fake_analyze)
    client.app.state.public_analysis_limiter = PublicAnalysisRateLimiter(
        hourly_limit=1, daily_limit=30)
    files = [("images", ("front.jpg", b"jpeg-bytes", "image/jpeg"))]

    assert client.post("/v1/public/analyze", files=files).status_code == 200
    response = client.post("/v1/public/analyze", files=files)

    assert response.status_code == 429
    assert response.json()["error"]["message"] == "잠시 후 다시 시도해주세요."


def test_public_analyze_accepts_25mb_and_rejects_one_byte_over(client, monkeypatch):
    async def fake_analyze(settings, source_images, **kwargs):
        return _analysis_result()

    monkeypatch.setattr(public_routes, "analyze_image_bytes", fake_analyze)
    boundary = client.post(
        "/v1/public/analyze",
        files=[("images", ("boundary.jpg", b"x" * MAX_UPLOAD_BYTES, "image/jpeg"))],
    )
    over = client.post(
        "/v1/public/analyze",
        files=[("images", ("over.jpg", b"x" * (MAX_UPLOAD_BYTES + 1), "image/jpeg"))],
    )

    assert boundary.status_code == 200
    assert over.status_code == 400
    assert over.json()["error"]["code"] == "file_too_large"


def test_public_analyze_rate_limiter_failure_is_fail_open(client, monkeypatch):
    class BrokenLimiter:
        def allow(self, key):
            raise RuntimeError("limiter unavailable")

    async def fake_analyze(settings, source_images, **kwargs):
        return _analysis_result()

    client.app.state.public_analysis_limiter = BrokenLimiter()
    monkeypatch.setattr(public_routes, "analyze_image_bytes", fake_analyze)
    response = client.post(
        "/v1/public/analyze",
        files=[("images", ("front.webp", b"webp", "image/webp"))],
    )

    assert response.status_code == 200


def test_public_analyze_uses_last_alb_forwarded_ip(client, monkeypatch):
    seen = {}

    class RecordingLimiter:
        def allow(self, key):
            seen["key"] = key
            return True

    async def fake_analyze(settings, source_images, **kwargs):
        return _analysis_result()

    client.app.state.public_analysis_limiter = RecordingLimiter()
    monkeypatch.setattr(public_routes, "analyze_image_bytes", fake_analyze)
    response = client.post(
        "/v1/public/analyze",
        headers={"X-Forwarded-For": "198.51.100.10, 203.0.113.25"},
        files=[("images", ("front.jpg", b"jpg", "image/jpeg"))],
    )

    assert response.status_code == 200
    assert seen["key"] == "203.0.113.25"
