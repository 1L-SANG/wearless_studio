import asyncio
import logging

import numpy as np
import pytest

from app import r2
from app import facemarket_id_document as iddoc
from app.agents.face_qc import FaceQc, QcFailed


def _one_pixel_jpeg() -> bytes:
    import cv2

    ok, buf = cv2.imencode(".jpg", np.zeros((8, 8, 3), dtype=np.uint8))
    assert ok
    return buf.tobytes()


def _real_jpeg(size: int = 20) -> bytes:
    """1픽셀보다 큰 실제 JPEG. _crop_jpeg 의 클램프 경계 케이스를 검증하려면
    크롭 여지가 있는 프레임이 필요하다(1x1 은 항상 퇴화한다)."""
    import cv2

    ok, buf = cv2.imencode(".jpg", np.zeros((size, size, 3), dtype=np.uint8))
    assert ok
    return buf.tobytes()


def test_id_document_key_has_own_prefix():
    key = r2.enrollment_id_document_key("enr-1", "jpg")
    assert key == "facemarket/enrollments/enr-1/iddoc/masked.jpg"


def test_id_document_key_does_not_collide_with_photo_quarantine():
    # 사진 정리 로직(_drain_photo_cleanup)이 quarantine/ prefix 를 쓸어 가므로
    # 신분증이 같은 prefix 에 있으면 심사 전에 지워진다.
    photo = r2.enrollment_quarantine_key("enr-1", "front", "jpg")
    doc = r2.enrollment_id_document_key("enr-1", "jpg")
    assert "/quarantine/" in photo
    assert "/quarantine/" not in doc


def test_id_document_key_versions_differ_across_calls():
    """동시/재시도 업로드가 같은 키를 공유하면, 늦게 실패한 요청의 rowcount==0
    정리(delete)가 먼저 커밋된 요청의 객체를 지워 버린다(리뷰 finding 1). 시도마다
    다른 version 을 쓰면 각자 자기 객체만 건드린다."""
    key1 = r2.enrollment_id_document_key("enr-1", "jpg", version="aaa")
    key2 = r2.enrollment_id_document_key("enr-1", "jpg", version="bbb")
    assert key1 != key2
    for key in (key1, key2):
        assert key.startswith("facemarket/enrollments/enr-1/iddoc/")
        assert "/quarantine/" not in key


class _FakeDetector:
    """cv2.FaceDetectorYN 대역. detect() 는 (retval, faces) 를 준다."""

    def __init__(self, faces):
        self.faces = faces

    def setInputSize(self, size):
        pass

    def detect(self, image):
        return 1, self.faces


def test_detect_largest_face_returns_none_when_empty():
    qc = FaceQc.__new__(FaceQc)          # __init__ 은 가중치 파일을 요구한다
    qc._det = _FakeDetector(None)
    assert qc.detect_largest_face(_one_pixel_jpeg()) is None


def test_detect_largest_face_picks_biggest_box():
    """배경에 사람이 끼어도 신분증 증명사진(가장 큰 얼굴)을 고른다."""
    qc = FaceQc.__new__(FaceQc)
    qc._det = _FakeDetector(np.array([
        [5.0, 5.0, 10.0, 10.0] + [0.0] * 11,     # 작은 얼굴(배경)
        [20.0, 20.0, 60.0, 60.0] + [0.0] * 11,   # 큰 얼굴(신분증 사진)
    ], dtype=np.float32))
    assert qc.detect_largest_face(_one_pixel_jpeg()) == (20, 20, 60, 60)


class _FakeQc:
    def __init__(self, box):
        self.box = box

    def detect_largest_face(self, data):
        return self.box


def test_crop_id_face_raises_when_no_face(monkeypatch):
    monkeypatch.setattr(iddoc, "load_face_qc", lambda settings, required: _FakeQc(None))
    with pytest.raises(iddoc.IdDocumentError) as exc:
        iddoc.crop_id_face(b"\xff\xd8\xffnot-a-face", settings=object())
    assert exc.value.reason == "id_face_not_detected"


def test_crop_id_face_returns_bytearray(monkeypatch):
    monkeypatch.setattr(iddoc, "load_face_qc", lambda settings, required: _FakeQc((10, 10, 40, 40)))
    monkeypatch.setattr(iddoc, "_crop_jpeg", lambda data, box: bytearray(b"\xff\xd8\xffcropped"))
    out = iddoc.crop_id_face(b"\xff\xd8\xffwhole-card", settings=object())
    assert isinstance(out, bytearray)
    assert bytes(out).startswith(b"\xff\xd8\xff")


class _FakeQcDecodeFailed:
    def detect_largest_face(self, data):
        raise QcFailed("decode_failed")


def test_crop_id_face_maps_decode_failed_to_id_document_unreadable(monkeypatch):
    """QcFailed('decode_failed') (스푸핑된 content-type 등으로 깨진 이미지)는
    qc_unavailable(503) 이 아니라 재촬영 유도 4xx 여야 한다(리뷰 finding 3)."""
    monkeypatch.setattr(iddoc, "load_face_qc", lambda settings, required: _FakeQcDecodeFailed())
    with pytest.raises(iddoc.IdDocumentError) as exc:
        iddoc.crop_id_face(b"\xff\xd8\xffnot-really-an-image", settings=object())
    assert exc.value.reason == "id_document_unreadable"


def test_crop_jpeg_raises_on_degenerate_box_after_clamping():
    """검출 박스가 프레임 밖이면 클램프 후 폭·높이가 0 이하가 된다. numpy 슬라이스는
    조용히 빈 배열을 주지만 cv2.imencode 는 빈 이미지에 cv2.error 를 던진다 — ok=False
    분기로 잡히지 않는다(리뷰 finding 2). IdDocumentError 로 막혀야 한다."""
    data = _real_jpeg(20)
    with pytest.raises(iddoc.IdDocumentError) as exc:
        iddoc._crop_jpeg(data, (50, 50, 5, 5))
    assert exc.value.reason == "id_document_unreadable"


def test_crop_jpeg_handles_edge_and_oversized_box():
    data = _real_jpeg(20)
    edge = iddoc._crop_jpeg(data, (0, 0, 5, 5))  # 프레임 모서리
    assert isinstance(edge, bytearray)
    assert len(edge) > 0
    assert bytes(edge).startswith(b"\xff\xd8\xff")

    oversized = iddoc._crop_jpeg(data, (0, 0, 30, 30))  # 이미지보다 큰 박스
    assert isinstance(oversized, bytearray)
    assert len(oversized) > 0
    assert bytes(oversized).startswith(b"\xff\xd8\xff")


# ── purge_id_document: R2 삭제 실패 가시성 (Task8 fix round 1, 리뷰 finding D) ──────


class _PurgeFakeCursor:
    def __init__(self, key):
        self._key = key

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        return None

    async def fetchone(self):
        return {"id_document_r2_key": self._key}


class _PurgeFakeConn:
    def __init__(self, key):
        self._cursor = _PurgeFakeCursor(key)

    def cursor(self):
        return self._cursor


class _FailingDeleteR2:
    def delete(self, key):
        raise RuntimeError("r2 delete boom")


def test_purge_id_document_logs_warning_on_delete_failure(caplog):
    """예전엔 `except Exception: pass` 로 조용히 삼켜서(리뷰 finding), R2 삭제가 실패해도
    호출부(admin approve/reject)가 볼 방법이 전혀 없었다. 이제 WARNING 으로 남긴다 —
    단, 키·바이트는 절대 로그에 남기지 않는다(원시 PII 미저장 규율)."""
    conn = _PurgeFakeConn("facemarket/enrollments/enr-1/iddoc/masked.jpg")
    with caplog.at_level(logging.WARNING, logger="app.facemarket_id_document"):
        asyncio.run(iddoc.purge_id_document(_FailingDeleteR2(), conn, "enr-1"))
    messages = [record.getMessage() for record in caplog.records]
    assert any("id_document_r2_delete_failed" in message and "enr-1" in message for message in messages)
    assert not any("masked.jpg" in message for message in messages)
