from dataclasses import replace
from io import BytesIO
from unittest.mock import AsyncMock

import numpy as np
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
