"""얼굴 대조 QC(SFace) — 게이트 로직 검증.

실사진 픽스처가 없으므로 (1) 로드/비활성, (2) 무얼굴 차단, (3) pairwise 코사인 수학을
임베딩 monkeypatch 로 결정적 검증한다. 동일인/타인 임계 캘리브는 실사진 골드셋 확보 후 별도.

weights(SFace/YuNet onnx)는 gitignore 이고 Docker 빌드 때 번들된다. CI 의 pytest 잡은 도커가
아니라 weights 가 없으므로, 실제 cv2 모델 로드가 필요한 테스트는 skip 하고, 로직만 검증하는
테스트는 __new__ 로 __init__(cv2 로드)을 우회한다.
"""

import os

import cv2
import numpy as np
import pytest

from app.agents import face_qc
from app.agents.face_qc import FaceQc, QcFailed, load_face_qc
from conftest import make_settings

_DIR = face_qc.default_model_dir()
_HAS_WEIGHTS = (os.path.exists(os.path.join(_DIR, face_qc._SFACE))
                and os.path.exists(os.path.join(_DIR, face_qc._YUNET)))
_needs_weights = pytest.mark.skipif(not _HAS_WEIGHTS, reason="SFace weights 미번들(도커 빌드 전용)")


def _blank_png() -> bytes:
    img = np.full((64, 64, 3), 255, np.uint8)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


class FakeDetector:
    def __init__(self, face_count: int):
        self.face_count = face_count

    def setInputSize(self, size):
        pass

    def detect(self, image):
        faces = np.ones((self.face_count, 15), dtype=np.float32)
        return 1, faces


class FakeRecognizer:
    def alignCrop(self, image, face):
        return image

    def feature(self, image):
        return np.array([[1.0, 0.0]], dtype=np.float32)


def test_qc_disabled_returns_none():
    assert load_face_qc(make_settings(fm_face_qc_enabled=False)) is None


@_needs_weights
def test_qc_enabled_loads_instance():
    qc = load_face_qc(make_settings(fm_face_qc_enabled=True))
    assert isinstance(qc, FaceQc)


def test_qc_enabled_without_weights_degrades_to_none(monkeypatch):
    # weights 부재/초기화 실패는 QC 스킵(None)으로 강등 — 생성 자체를 막지 않는다.
    monkeypatch.setattr(face_qc, "FaceQc", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    assert load_face_qc(make_settings(fm_face_qc_enabled=True)) is None


def test_required_loader_fails_closed_when_weights_are_missing(monkeypatch):
    monkeypatch.setattr(face_qc, "FaceQc", lambda *args: (_ for _ in ()).throw(FileNotFoundError()))

    with pytest.raises(QcFailed) as error:
        load_face_qc(make_settings(fm_face_qc_enabled=True), required=True)

    assert error.value.reason == "qc_unavailable"


def test_required_loader_fails_closed_when_disabled():
    with pytest.raises(QcFailed) as error:
        load_face_qc(make_settings(fm_face_qc_enabled=False), required=True)

    assert error.value.reason == "qc_unavailable"


@_needs_weights
def test_no_face_detected_raises():
    qc = FaceQc()
    with pytest.raises(QcFailed) as ei:
        qc.pairwise_min_similarity([_blank_png(), _blank_png(), _blank_png()])
    assert ei.value.reason == "no_face_detected"


def test_insufficient_images_raises():
    # 2장 미만은 임베딩(cv2) 전에 즉시 차단 — weights 불필요, __init__ 우회.
    qc = FaceQc.__new__(FaceQc)
    with pytest.raises(QcFailed):
        qc.pairwise_min_similarity([_blank_png()])


def test_pairwise_min_similarity_math(monkeypatch):
    qc = FaceQc.__new__(FaceQc)  # cv2 모델 로드 우회 — _embed 를 patch 하므로 불필요
    vecs = {
        b"a": np.array([1.0, 0.0, 0.0]),
        b"b": np.array([1.0, 0.0, 0.0]),   # a와 동일 → cos 1.0
        b"c": np.array([0.0, 1.0, 0.0]),   # a·b와 직교 → cos 0.0 (최소)
    }
    monkeypatch.setattr(FaceQc, "_embed", lambda self, d: vecs[d])
    assert qc.pairwise_min_similarity([b"a", b"b"]) == pytest.approx(1.0)
    assert qc.pairwise_min_similarity([b"a", b"b", b"c"]) == pytest.approx(0.0, abs=1e-6)


def test_one_to_one_similarity_wipes_both_embeddings(monkeypatch):
    qc = FaceQc.__new__(FaceQc)
    first = np.array([1.0, 0.0])
    second = np.array([1.0, 0.0])
    values = iter((first, second))
    monkeypatch.setattr(FaceQc, "_embed", lambda self, data: next(values))

    assert qc.one_to_one_similarity(b"id", b"live") == pytest.approx(1.0)
    assert np.count_nonzero(first) == 0
    assert np.count_nonzero(second) == 0


def test_embed_rejects_multiple_faces():
    qc = FaceQc.__new__(FaceQc)
    qc._det = FakeDetector(face_count=2)
    qc._rec = FakeRecognizer()

    with pytest.raises(QcFailed) as error:
        qc._embed(_blank_png())

    assert error.value.reason == "multiple_faces"


def test_default_model_dir_points_at_bundle():
    assert face_qc.default_model_dir().endswith("data/face_models")
