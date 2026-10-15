"""Only supported photo decoders may inspect uploaded bytes."""
import io

import pytest
from PIL import EpsImagePlugin, Image

from app.facemarket_photo_normalize import NormalizeFailed, normalize_png, sniff_image_mime


def test_eps_declared_as_jpeg_never_enters_eps_decoder(monkeypatch):
    data = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 8 8\nshowpage\n"
    assert sniff_image_mime(data, "image/jpeg") == "image/jpeg"
    decoder_calls = []

    def forbidden_eps_open(self):
        decoder_calls.append("EPS")
        raise OSError("EPS must not be inspected")

    monkeypatch.setattr(EpsImagePlugin.EpsImageFile, "_open", forbidden_eps_open)
    with pytest.raises(NormalizeFailed):
        normalize_png(data)
    assert decoder_calls == []


@pytest.mark.parametrize("format", ["JPEG", "PNG", "WEBP"])
def test_supported_photo_formats_still_normalize(format):
    original = io.BytesIO()
    Image.new("RGB", (29, 17), (20, 100, 220)).save(original, format)
    png, size = normalize_png(original.getvalue())
    assert size == (29, 17)
    with Image.open(io.BytesIO(png)) as image:
        assert image.format == "PNG"
        assert image.size == (29, 17)
        assert image.getpixel((10, 10))[2] > 200
