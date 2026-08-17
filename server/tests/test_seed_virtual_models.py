import hashlib
from io import BytesIO
import struct
import zlib

import pytest
from PIL import Image

from scripts import seed_virtual_models as seed


def _record(model_id: str, data: bytes, mime: str = "image/jpeg") -> dict:
    return {
        "modelId": model_id,
        "source": f"/approved/{model_id}/grid-fullbody.png",
        "key": f"seed/models/{model_id}/grid_fullbody.png",
        "mime": mime,
        "data": data,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


class FakeR2:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.puts = []

    def head(self, key):
        row = self.objects.get(key)
        return None if row is None else {"size": len(row[0]), "mime": row[1]}

    def get_bytes(self, key):
        return self.objects[key][0]

    def put_bytes(self, key, data, mime, cache):
        self.puts.append((key, data, mime, cache))
        self.objects[key] = (data, mime)


def test_grid_fullbody_only_uploads_missing_and_preserves_verified_existing(monkeypatch):
    records = [_record("mA", b"A"), _record("mB", b"B", "image/png")]
    r2 = FakeR2({records[0]["key"]: (b"A", "image/jpeg")})
    monkeypatch.setattr(seed, "_grid_fullbody_records", lambda: records)

    result = seed._seed_grid_fullbody_only(r2)

    assert [(row["modelId"], row["status"]) for row in result] == [
        ("mA", "verified-existing"),
        ("mB", "uploaded"),
    ]
    assert r2.puts == [
        (records[1]["key"], b"B", "image/png", seed._IMMUTABLE),
    ]


def test_grid_fullbody_only_refuses_any_overwrite_before_first_put(monkeypatch):
    records = [_record("mA", b"A"), _record("mB", b"B")]
    r2 = FakeR2({records[1]["key"]: (b"DIFFERENT", "image/jpeg")})
    monkeypatch.setattr(seed, "_grid_fullbody_records", lambda: records)

    with pytest.raises(RuntimeError, match="refusing overwrite: mB"):
        seed._seed_grid_fullbody_only(r2)

    assert r2.puts == []


def test_face_direction_only_uses_the_exact_face_record_set(monkeypatch):
    records = [_record("mA", b"FACE")]
    records[0]["key"] = "seed/models/mA/grid_face_direction.png"
    monkeypatch.setattr(seed, "_grid_face_direction_records", lambda: records)
    r2 = FakeR2()

    result = seed._seed_grid_face_direction_only(r2)

    assert result[0]["key"].endswith("/grid_face_direction.png")
    assert r2.puts[0][0] == "seed/models/mA/grid_face_direction.png"


def _png_with_cabx() -> tuple[bytes, bytes]:
    output = BytesIO()
    Image.new("RGB", (2, 3), "red").save(output, format="PNG")
    clean = output.getvalue()
    payload = b"openai c2pa jumb contentauth"
    chunk_type = b"caBX"
    chunk = (
        struct.pack(">I", len(payload))
        + chunk_type
        + payload
        + struct.pack(">I", zlib.crc32(chunk_type + payload) & 0xFFFFFFFF)
    )
    return clean[:-12] + chunk + clean[-12:], clean


def test_face_direction_png_sanitization_is_lossless_and_historical_style():
    original, expected = _png_with_cabx()

    sanitized, mime, proof = seed._sanitize_face_direction(original)

    assert sanitized == expected
    assert mime == "image/png"
    assert proof["removedContainerCount"] == 1
    assert proof["width"] == 2 and proof["height"] == 3


def test_face_direction_jpeg_sanitization_preserves_entropy_bytes():
    output = BytesIO()
    Image.new("RGB", (3, 2), "blue").save(output, format="JPEG")
    clean = output.getvalue()
    payload = b"JP\x00c2pa jumb openai"
    app11 = b"\xff\xeb" + struct.pack(">H", len(payload) + 2) + payload
    original = clean[:2] + app11 + clean[2:]

    sanitized, mime, proof = seed._sanitize_face_direction(original)

    assert sanitized == clean
    assert mime == "image/jpeg"
    assert proof["removedContainerCount"] == 1
    assert proof["width"] == 3 and proof["height"] == 2
