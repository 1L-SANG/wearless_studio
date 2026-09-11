import numpy as np
import pytest

from app import r2
from app import facemarket_id_document as iddoc
from app.agents.face_qc import FaceQc


def _one_pixel_jpeg() -> bytes:
    import cv2

    ok, buf = cv2.imencode(".jpg", np.zeros((8, 8, 3), dtype=np.uint8))
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
