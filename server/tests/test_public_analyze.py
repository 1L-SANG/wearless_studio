import app.public_routes as public_routes
from app.public_routes import PublicAnalysisRateLimiter
from app.public_routes import MAX_PUBLIC_REQUEST_BYTES
from app.routes import MAX_UPLOAD_BYTES


JPEG = b"\xff\xd8\xffjpeg-bytes"
PNG = b"\x89PNG\r\n\x1a\nfront-bytes"
WEBP = b"RIFF\x04\x00\x00\x00WEBPwebp"


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
        seen["product"] = kwargs.get("product")
        return _analysis_result()

    monkeypatch.setattr(public_routes, "analyze_image_bytes", fake_analyze)
    response = client.post(
        "/v1/public/analyze",
        files=[
            ("images", ("front.png", PNG, "image/png")),
            ("images", ("back.png", PNG, "image/png")),
        ],
        data={
            "slots": ["Front", "Back"],
            "productContext": '{"colors":[{"id":"base"}]}',
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["styleTags"] == ["daily"]
    assert response.json()["data"]["measurements"] == []
    assert seen["images"] == [
        (PNG, "image/png"), (PNG, "image/png")]
    assert seen["slots"] == ["Front", "Back"]
    assert seen["product"] == {"colors": [{"id": "base"}]}


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
    files = [("images", ("front.jpg", JPEG, "image/jpeg"))]

    assert client.post("/v1/public/analyze", files=files).status_code == 200
    response = client.post("/v1/public/analyze", files=files)

    assert response.status_code == 429
    assert response.json()["error"]["message"] == "잠시 후 다시 시도해주세요."


def test_public_analyze_valid_bearer_skips_ip_rate_limit(
    client, make_token, monkeypatch
):
    class DenyLimiter:
        def allow(self, key):
            raise AssertionError("authenticated analysis must not call the IP limiter")

    async def fake_analyze(settings, source_images, **kwargs):
        return _analysis_result()

    client.app.state.public_analysis_limiter = DenyLimiter()
    monkeypatch.setattr(public_routes, "analyze_image_bytes", fake_analyze)
    response = client.post(
        "/v1/public/analyze",
        headers={"Authorization": f"Bearer {make_token()}"},
        files=[("images", ("front.jpg", JPEG, "image/jpeg"))],
    )

    assert response.status_code == 200


def test_public_analyze_invalid_bearer_stays_anonymous(client, monkeypatch):
    class DenyLimiter:
        def allow(self, key):
            return False

    client.app.state.public_analysis_limiter = DenyLimiter()
    response = client.post(
        "/v1/public/analyze",
        headers={"Authorization": "Bearer invalid"},
        files=[("images", ("front.jpg", JPEG, "image/jpeg"))],
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"


def test_public_analyze_accepts_25mb_and_rejects_one_byte_over(client, monkeypatch):
    async def fake_analyze(settings, source_images, **kwargs):
        return _analysis_result()

    monkeypatch.setattr(public_routes, "analyze_image_bytes", fake_analyze)
    boundary = client.post(
        "/v1/public/analyze",
        files=[("images", ("boundary.jpg", b"\xff\xd8\xff" + b"x" * (MAX_UPLOAD_BYTES - 3), "image/jpeg"))],
    )
    over = client.post(
        "/v1/public/analyze",
        files=[("images", ("over.jpg", b"\xff\xd8\xff" + b"x" * (MAX_UPLOAD_BYTES - 2), "image/jpeg"))],
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
        files=[("images", ("front.webp", WEBP, "image/webp"))],
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
        files=[("images", ("front.jpg", JPEG, "image/jpeg"))],
    )

    assert response.status_code == 200
    assert seen["key"] == "203.0.113.25"


def test_public_analyze_rejects_declared_request_over_60mb_before_multipart_parse(client):
    response = client.post(
        "/v1/public/analyze",
        headers={"Content-Length": str(MAX_PUBLIC_REQUEST_BYTES + 1)},
        content=b"",
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_public_analyze_returns_429_immediately_when_analysis_slots_are_full(
    client, monkeypatch,
):
    class BusySemaphore:
        def locked(self):
            return True

        async def acquire(self):
            raise AssertionError("busy requests must not wait")

        def release(self):
            raise AssertionError("unacquired semaphore must not be released")

    monkeypatch.setattr(public_routes, "_analysis_semaphore", BusySemaphore())
    response = client.post(
        "/v1/public/analyze",
        files=[("images", ("front.jpg", JPEG, "image/jpeg"))],
    )

    assert response.status_code == 429
    assert response.json()["error"] == {
        "code": "analysis_busy",
        "message": "잠시 후 다시 시도해주세요.",
    }


def test_public_analyze_rejects_mime_spoof_before_llm(client, monkeypatch):
    async def must_not_analyze(*args, **kwargs):
        raise AssertionError("spoofed bytes reached the LLM")

    monkeypatch.setattr(public_routes, "analyze_image_bytes", must_not_analyze)
    response = client.post(
        "/v1/public/analyze",
        files=[("images", ("fake.jpg", b"plain text", "image/jpeg"))],
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_image_content"


def test_public_analyze_rejects_slots_length_and_supported_value_mismatches(client):
    length_mismatch = client.post(
        "/v1/public/analyze",
        files=[
            ("images", ("front.jpg", JPEG, "image/jpeg")),
            ("images", ("back.jpg", JPEG, "image/jpeg")),
        ],
        data={"slots": ["Front"]},
    )
    unsupported = client.post(
        "/v1/public/analyze",
        files=[("images", ("front.jpg", JPEG, "image/jpeg"))],
        data={"slots": ["Side"]},
    )

    assert length_mismatch.status_code == 400
    assert length_mismatch.json()["error"]["code"] == "invalid_slots"
    assert unsupported.status_code == 400
    assert unsupported.json()["error"]["code"] == "invalid_slots"
