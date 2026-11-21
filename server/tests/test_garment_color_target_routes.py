from dataclasses import replace
from io import BytesIO
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest
from PIL import Image

from app import public_routes, repo, routes
from app.services import garment_color_target_sources as sources
from conftest import auth_headers, patch_route_db

STATUS = "/v1/public/garment-color-targets/status"
PUBLIC_SOURCE = "/v1/public/garment-color-source"
PROJECT_SOURCE = "/v1/projects/p1/garment-color-source"
FRONT_ID = "00000000-0000-0000-0000-000000000001"


def enable(client):
    client.app.state.settings = replace(client.app.state.settings, garment_color_target_enabled=True)


def test_status_and_disabled_source_routes(client, make_token):
    assert client.get(STATUS).json() == {"enabled": False}
    assert client.post(PUBLIC_SOURCE).status_code == 404
    assert client.post(PROJECT_SOURCE, headers=auth_headers(make_token)).status_code == 404
    enable(client)
    assert client.get(STATUS).json() == {"enabled": True, "toneVersion": "garment-color-v1"}


@pytest.mark.parametrize(("scope", "hourly", "daily"), [("public", 40, 120), ("user", 60, 200)])
def test_color_source_limits_use_configured_hourly_and_daily_budgets(client, scope, hourly, daily):
    limiter = getattr(client.app.state, f"{scope}_garment_color_source_limiter")
    for request_index in range(daily):
        now = (request_index // hourly) * 3601
        assert limiter.allow("seller", now=now)
        if (request_index + 1) % hourly == 0:
            assert not limiter.allow("seller", now=now)
    assert not limiter.allow("seller", now=now + 3601)
    assert limiter.allow("another_seller", now=now)
    assert limiter.allow("seller", now=24 * 3600 + 1)


def test_public_source_rate_limits_before_segmentation(client, monkeypatch):
    enable(client)
    client.app.state.public_garment_color_source_limiter = public_routes.PublicAnalysisRateLimiter(hourly_limit=1)
    monkeypatch.setattr(sources.garment_color_tune, "segment_seller_garment", AsyncMock(
        side_effect=ValueError("SAM failed")))
    image = BytesIO()
    Image.new("RGB", (10, 10), "blue").save(image, "PNG")
    request = {"files": {"image": ("front.png", image.getvalue(), "image/png")},
               "data": {"clothingType": "top", "subCategory": "knit"}}
    first = client.post(PUBLIC_SOURCE, **request)
    assert first.json() == {"status": "unavailable", "reason": "mask_failed",
                            "cutoutPng": None, "sourceSrgb": None, "auto": None}
    assert client.post(PUBLIC_SOURCE, **request).status_code == 429
    sources.garment_color_tune.segment_seller_garment.assert_awaited_once()


def test_project_source_requires_owner_and_uses_first_front(client, make_token, monkeypatch):
    enable(client)
    patch_route_db(monkeypatch, routes)
    owner = AsyncMock(return_value=None)
    monkeypatch.setattr(repo, "get_project", owner)
    read = AsyncMock(return_value=b"original")
    monkeypatch.setattr(sources, "read_front", read)
    render = AsyncMock(return_value={"status": "ready", "auto": None})
    monkeypatch.setattr(sources, "preview", render)
    product = {"clothing_type": "top", "colors": [
        {"id": "c1", "images": [{"slot": "Back", "id": "back"}, {"slot": "Front", "id": FRONT_ID},
                                {"slot": "Front", "id": "other"}]},
    ]}
    monkeypatch.setattr(repo, "get_product", AsyncMock(return_value=product))
    headers = auth_headers(make_token)
    assert client.post(PROJECT_SOURCE, json={"colorId": "c1"}).status_code == 401
    assert client.post(PROJECT_SOURCE, headers=headers, json={"colorId": "c1"}).status_code == 404
    owner.return_value = {"id": "p1"}
    assert client.post(PROJECT_SOURCE, headers=headers, json={"colorId": "missing"}).status_code == 404
    read.assert_not_awaited()
    assert client.post(PROJECT_SOURCE, headers=headers, json={"colorId": "c1"}).json() == {
        "status": "ready", "auto": None,
    }
    assert read.await_args.kwargs["front_id"] == FRONT_ID
    assert render.await_args.args[1] == b"original"


@pytest.mark.parametrize("first_path", [PUBLIC_SOURCE, PROJECT_SOURCE])
def test_project_and_public_sources_share_user_budget(client, make_token, monkeypatch, first_path):
    enable(client)
    patch_route_db(monkeypatch, routes)
    client.app.state.user_garment_color_source_limiter = public_routes.PublicAnalysisRateLimiter(hourly_limit=1)
    owner = AsyncMock(return_value=None)
    monkeypatch.setattr(repo, "get_project", owner)
    monkeypatch.setattr(repo, "get_product", AsyncMock(return_value={"clothing_type": "top", "colors": [
        {"id": "c1", "images": [{"slot": "Front", "id": FRONT_ID}]},
    ]}))
    read = AsyncMock(return_value=b"original")
    preview = AsyncMock(return_value={"status": "ready"})
    monkeypatch.setattr(sources, "read_front", read)
    monkeypatch.setattr(sources, "preview", preview)
    headers = auth_headers(make_token)
    photo = BytesIO()
    Image.new("RGB", (8, 8)).save(photo, "PNG")

    def request(path):
        if path == PROJECT_SOURCE:
            return client.post(path, headers=headers, json={"colorId": "c1"})
        return client.post(path, headers=headers, data={"clothingType": "top"},
                           files={"image": ("front.png", photo.getvalue(), "image/png")})

    assert request(PROJECT_SOURCE).status_code == 404
    owner.return_value = {"id": "p1"}
    assert client.post(PROJECT_SOURCE, headers=headers, json={"colorId": "unknown"}).status_code == 404
    assert request(first_path).status_code == 200
    public_denied, project_denied = request(PUBLIC_SOURCE), request(PROJECT_SOURCE)
    assert public_denied.status_code == project_denied.status_code == 429
    assert public_denied.json() == project_denied.json()
    assert project_denied.json()["error"]["code"] == "rate_limited"
    preview.assert_awaited_once()
    assert read.await_count == int(first_path == PROJECT_SOURCE)


@pytest.mark.parametrize("content_type", [None, "application/json"])
def test_public_source_rejects_non_multipart_before_parsing(client, monkeypatch, content_type):
    enable(client)
    parser = Mock(wraps=public_routes.MultiPartParser)
    monkeypatch.setattr(public_routes, "MultiPartParser", parser)
    headers = {"Content-Type": content_type} if content_type else {}
    response = client.post(PUBLIC_SOURCE, headers=headers, content=b"{}")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_input"
    parser.assert_not_called()


@pytest.mark.parametrize(("content_type", "body"), [
    ("multipart/form-data", b"--x\r\n"),
    ("multipart/form-data; boundary=x", b"--x\r\nContent-Disposition: form-data\r\n\r\nbroken"),
    ("multipart/form-data; boundary=x", b"\x00\x01 not multipart at all"),
])
def test_public_source_rejects_malformed_multipart_as_input_error(client, content_type, body):
    enable(client)
    response = client.post(PUBLIC_SOURCE, headers={"Content-Type": content_type}, content=body)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "invalid_input"


@pytest.mark.parametrize(("image_format", "available"), [
    ("JPEG", True), ("PNG", True), ("WEBP", True), ("BMP", False), ("TIFF", False),
])
def test_public_source_checks_image_bytes_before_decoding(client, monkeypatch, image_format, available):
    enable(client)
    preview = AsyncMock(return_value={"status": "ready"})
    monkeypatch.setattr(sources, "preview", preview)
    photo = BytesIO()
    Image.new("RGB", (8, 8)).save(photo, image_format)
    data = photo.getvalue()
    declared_type = f"image/{image_format.lower()}" if available else "image/png"
    response = client.post(PUBLIC_SOURCE, files={"image": ("front", data, declared_type)},
                           data={"clothingType": "top"})
    if available:
        assert response.status_code == 200
        preview.assert_awaited_once()
        assert preview.await_args.args[1] == data
    else:
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_image_content"
        preview.assert_not_awaited()


def test_source_png_is_cropped_and_downscaled(client, monkeypatch):
    import base64

    enable(client)
    image = BytesIO()
    Image.new("RGB", (900, 750), (30, 70, 160)).save(image, "PNG")

    async def segment(_settings, _data, rgba, **_kwargs):
        alpha = np.zeros(rgba.shape[:2], dtype=np.uint8)
        alpha[20:720, 100:800] = 255
        return alpha

    monkeypatch.setattr(sources.garment_color_tune, "segment_seller_garment", segment)
    monkeypatch.setattr(sources.qwen_flatlay, "_color_target", lambda *args, **kw: None)
    response = client.post(PUBLIC_SOURCE, files={"image": ("front.png", image.getvalue(), "image/png")},
                           data={"clothingType": "top"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "ready"
    assert result["sourceSrgb"] == [30, 70, 160]
    assert result["auto"] is None
    prefix, encoded = result["cutoutPng"].split(",", 1)
    assert prefix == "data:image/png;base64"
    cutout = Image.open(BytesIO(base64.b64decode(encoded)))
    assert cutout.size == (640, 640)
    assert cutout.getpixel((0, 0)) == (30, 70, 160, 255)



def test_small_cutout_clears_hidden_background_pixels():
    rgba = np.full((8, 8, 4), 255, dtype=np.uint8)
    alpha = np.zeros((8, 8), dtype=np.uint8)
    alpha[2:6, 2:6] = 255
    alpha[2, 2] = 0
    cutout, _ = sources._cutout(rgba, alpha)
    assert cutout.size == (4, 4)
    assert cutout.getpixel((0, 0)) == (0, 0, 0, 0)


def test_public_upload_rejects_oversize_stream_without_content_length(client, monkeypatch):
    enable(client)
    monkeypatch.setattr(public_routes, "MAX_UPLOAD_BYTES", 1024)
    preview = AsyncMock()
    monkeypatch.setattr(sources, "preview", preview)

    def chunks():
        yield b'--test\r\nContent-Disposition: form-data; name="image"; filename="front.png"\r\n\r\n'
        for _ in range(10):
            yield b"x" * 8192
        yield b"\r\n--test--\r\n"

    response = client.post(PUBLIC_SOURCE, content=chunks(),
                           headers={"Content-Type": "multipart/form-data; boundary=test"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "file_too_large"
    preview.assert_not_awaited()
