"""얼굴 대조 QC(SFace) — 게이트 로직 검증.

실사진 픽스처가 없으므로 (1) 로드/비활성, (2) 무얼굴 차단, (3) pairwise 코사인 수학을
임베딩 monkeypatch 로 결정적 검증한다. 동일인/타인 임계 캘리브는 실사진 골드셋 확보 후 별도.
"""

import cv2
import numpy as np
import pytest

from app.agents import face_qc
from app.agents.face_qc import FaceQc, QcFailed, load_face_qc
from conftest import make_settings


def _blank_png() -> bytes:
    img = np.full((64, 64, 3), 255, np.uint8)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def test_qc_disabled_returns_none():
    assert load_face_qc(make_settings(fm_face_qc_enabled=False)) is None


def test_qc_enabled_loads_instance():
    qc = load_face_qc(make_settings(fm_face_qc_enabled=True))
    assert isinstance(qc, FaceQc)  # weights 번들 존재 전제(app/data/face_models)


def test_no_face_detected_raises():
    qc = FaceQc()
    with pytest.raises(QcFailed) as ei:
        qc.pairwise_min_similarity([_blank_png(), _blank_png(), _blank_png()])
    assert ei.value.reason == "no_face_detected"


def test_insufficient_images_raises():
    qc = FaceQc()
    with pytest.raises(QcFailed):
        qc.pairwise_min_similarity([_blank_png()])


def test_pairwise_min_similarity_math(monkeypatch):
    qc = FaceQc()
    vecs = {
        b"a": np.array([1.0, 0.0, 0.0]),
        b"b": np.array([1.0, 0.0, 0.0]),   # a와 동일 → cos 1.0
        b"c": np.array([0.0, 1.0, 0.0]),   # a·b와 직교 → cos 0.0 (최소)
    }
    monkeypatch.setattr(FaceQc, "_embed", lambda self, d: vecs[d])
    assert qc.pairwise_min_similarity([b"a", b"b"]) == pytest.approx(1.0)
    assert qc.pairwise_min_similarity([b"a", b"b", b"c"]) == pytest.approx(0.0, abs=1e-6)


def test_default_model_dir_points_at_bundle():
    assert face_qc.default_model_dir().endswith("data/face_models")
