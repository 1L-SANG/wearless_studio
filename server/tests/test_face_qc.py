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


class FailingFeatureRecognizer:
    def __init__(self, aligned):
        self.aligned = aligned

    def alignCrop(self, image, face):
        return self.aligned

    def feature(self, image):
        raise RuntimeError("feature failed")


class TrackingRecognizer:
    def __init__(self, aligned, feature):
        self.aligned = aligned
        self.raw_feature = feature

    def alignCrop(self, image, face):
        return self.aligned

    def feature(self, image):
        return self.raw_feature


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
    returned = []

    def embed(self, data):
        feature = vecs[data].copy()
        returned.append(feature)
        return feature

    monkeypatch.setattr(FaceQc, "_embed", embed)
    assert qc.pairwise_min_similarity([b"a", b"b"]) == pytest.approx(1.0)
    assert all(np.count_nonzero(feature) == 0 for feature in returned)
    returned.clear()
    assert qc.pairwise_min_similarity([b"a", b"b", b"c"]) == pytest.approx(0.0, abs=1e-6)
    assert all(np.count_nonzero(feature) == 0 for feature in returned)


def test_one_to_one_similarity_wipes_both_embeddings(monkeypatch):
    qc = FaceQc.__new__(FaceQc)
    first = np.array([1.0, 0.0])
    second = np.array([1.0, 0.0])
    values = iter((first, second))
    monkeypatch.setattr(FaceQc, "_embed", lambda self, data: next(values))

    assert qc.one_to_one_similarity(b"id", b"live") == pytest.approx(1.0)
    assert np.count_nonzero(first) == 0
    assert np.count_nonzero(second) == 0


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (np.array([np.nan, 0.0]), np.array([1.0, 0.0])),
        (np.array([1.0, 0.0]), np.array([np.inf, 0.0])),
        (np.array([0.0, 0.0]), np.array([1.0, 0.0])),
        (
            np.array([np.finfo(np.float64).max, np.finfo(np.float64).max]),
            np.array([1.0, 0.0]),
        ),
    ],
    ids=("nan", "inf", "zero", "nonfinite-denominator"),
)
def test_one_to_one_rejects_invalid_embeddings(first, second, monkeypatch):
    qc = FaceQc.__new__(FaceQc)
    values = iter((first, second))
    monkeypatch.setattr(FaceQc, "_embed", lambda self, data: next(values))

    with pytest.raises(QcFailed) as error:
        qc.one_to_one_similarity(b"id", b"live")

    assert error.value.reason == "embedding_invalid"
    assert np.count_nonzero(first) == 0
    assert np.count_nonzero(second) == 0


def test_one_to_one_rejects_nonfinite_dot(monkeypatch):
    qc = FaceQc.__new__(FaceQc)
    values = iter((np.array([1.0, 0.0]), np.array([1.0, 0.0])))
    monkeypatch.setattr(FaceQc, "_embed", lambda self, data: next(values))
    monkeypatch.setattr(face_qc.np, "dot", lambda left, right: np.inf)

    with pytest.raises(QcFailed) as error:
        qc.one_to_one_similarity(b"id", b"live")

    assert error.value.reason == "embedding_invalid"


def test_one_to_one_rejects_nonfinite_final_score(monkeypatch):
    qc = FaceQc.__new__(FaceQc)
    values = iter((np.array([1.0, 0.0]), np.array([1.0, 0.0])))
    norms = iter((1.0, 1e-200))
    monkeypatch.setattr(FaceQc, "_embed", lambda self, data: next(values))
    monkeypatch.setattr(face_qc.np.linalg, "norm", lambda value: next(norms))
    monkeypatch.setattr(face_qc.np, "dot", lambda left, right: np.finfo(np.float64).max)

    with pytest.raises(QcFailed) as error:
        qc.one_to_one_similarity(b"id", b"live")

    assert error.value.reason == "embedding_invalid"


def test_one_to_one_wipes_first_embedding_when_second_fails(monkeypatch):
    qc = FaceQc.__new__(FaceQc)
    first = np.array([1.0, 0.0])
    calls = iter((first, QcFailed("decode_failed")))

    def embed(self, data):
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(FaceQc, "_embed", embed)

    with pytest.raises(QcFailed) as error:
        qc.one_to_one_similarity(b"id", b"live")

    assert error.value.reason == "decode_failed"
    assert np.count_nonzero(first) == 0


def test_embed_rejects_multiple_faces_and_wipes_decoded_image(monkeypatch):
    qc = FaceQc.__new__(FaceQc)
    qc._det = FakeDetector(face_count=2)
    qc._rec = FakeRecognizer()
    decoded = np.ones((4, 4, 3), dtype=np.uint8)
    caller_data = bytearray(b"caller-owned")
    monkeypatch.setattr(face_qc.cv2, "imdecode", lambda data, mode: decoded)

    with pytest.raises(QcFailed) as error:
        qc._embed(caller_data)

    assert error.value.reason == "multiple_faces"
    assert np.count_nonzero(decoded) == 0
    assert caller_data == bytearray(b"caller-owned")


def test_embed_wipes_decoded_and_aligned_images_when_feature_fails(monkeypatch):
    qc = FaceQc.__new__(FaceQc)
    qc._det = FakeDetector(face_count=1)
    decoded = np.ones((4, 4, 3), dtype=np.uint8)
    aligned = np.ones((2, 2, 3), dtype=np.uint8)
    qc._rec = FailingFeatureRecognizer(aligned)
    monkeypatch.setattr(face_qc.cv2, "imdecode", lambda data, mode: decoded)

    with pytest.raises(RuntimeError, match="feature failed"):
        qc._embed(b"encoded")

    assert np.count_nonzero(decoded) == 0
    assert np.count_nonzero(aligned) == 0


def test_embed_wipes_raw_feature_after_copying_embedding(monkeypatch):
    qc = FaceQc.__new__(FaceQc)
    qc._det = FakeDetector(face_count=1)
    decoded = np.ones((4, 4, 3), dtype=np.uint8)
    aligned = np.ones((2, 2, 3), dtype=np.uint8)
    raw_feature = np.array([[1.0, 0.0]])
    qc._rec = TrackingRecognizer(aligned, raw_feature)
    monkeypatch.setattr(face_qc.cv2, "imdecode", lambda data, mode: decoded)

    embedding = qc._embed(b"encoded")

    assert embedding.tolist() == [1.0, 0.0]
    assert np.count_nonzero(decoded) == 0
    assert np.count_nonzero(aligned) == 0
    assert np.count_nonzero(raw_feature) == 0
    embedding.fill(0)


def test_pairwise_wipes_embeddings_after_partial_failure(monkeypatch):
    qc = FaceQc.__new__(FaceQc)
    first = np.array([1.0, 0.0])
    second = np.array([0.0, 1.0])
    calls = iter((first, second, QcFailed("decode_failed")))

    def embed(self, data):
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(FaceQc, "_embed", embed)

    with pytest.raises(QcFailed) as error:
        qc.pairwise_min_similarity([b"a", b"b", b"c"])

    assert error.value.reason == "decode_failed"
    assert np.count_nonzero(first) == 0
    assert np.count_nonzero(second) == 0


def test_default_model_dir_points_at_bundle():
    assert face_qc.default_model_dir().endswith("data/face_models")
