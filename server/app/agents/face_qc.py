"""실존 모델 얼굴 대조 QC — OpenCV SFace(임베딩)+YuNet(검출), Apache-2.0.

업로드 3장(front/side/angle45)이 동일인인지 pairwise 코사인으로 게이트한다. 실존 모델은
그리드 자산을 실사진 합성으로 만들므로(생성 아님) 핵심 위협은 스푸핑 — 남의 얼굴 3장을 섞어
등록하는 것 — 이며, 임계 미달이면 자산 등록을 차단한다(handoff §03 필수 게이트).

PII 하드룰(§1.4): 얼굴 바이트·임베딩·비공개 키·모델 경로는 로그·응답·예외 메시지에 남기지
않는다. weights 는 빌드타임 번들(런타임 다운로드 금지). enabled=false 면 QC 스킵(dev·shadow).
"""

import logging
import os

import cv2
import numpy as np

log = logging.getLogger("wearless.face_qc")

_SFACE = "face_recognition_sface_2021dec.onnx"
_YUNET = "face_detection_yunet_2023mar.onnx"
_DEFAULT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "face_models")


def default_model_dir() -> str:
    return _DEFAULT_DIR


class QcFailed(Exception):
    """얼굴 검출 실패 또는 임베딩 오류 — 자산 등록 차단 사유. score 는 부재 시 0.0."""

    def __init__(self, reason: str, score: float = 0.0):
        super().__init__(reason)
        self.reason = reason
        self.score = score


class FaceQc:
    """SFace 임베딩 + YuNet 검출. 프로세스당 1회 로드해 재사용한다."""

    def __init__(self, model_dir: str | None = None):
        model_dir = model_dir or _DEFAULT_DIR
        det_path = os.path.join(model_dir, _YUNET)
        rec_path = os.path.join(model_dir, _SFACE)
        if not (os.path.exists(det_path) and os.path.exists(rec_path)):
            # 경로는 예외에 싣지 않는다(로그에 로컬 경로 누출 방지).
            raise FileNotFoundError("face QC weights missing")
        self._det = cv2.FaceDetectorYN.create(det_path, "", (320, 320), score_threshold=0.7)
        self._rec = cv2.FaceRecognizerSF.create(rec_path, "")

    def _embed(self, data: bytes) -> np.ndarray:
        arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            raise QcFailed("decode_failed")
        h, w = arr.shape[:2]
        self._det.setInputSize((w, h))
        _, faces = self._det.detect(arr)
        if faces is None or len(faces) == 0:
            raise QcFailed("no_face_detected")
        face = max(faces, key=lambda f: float(f[2]) * float(f[3]))  # 가장 큰 얼굴 선택
        aligned = self._rec.alignCrop(arr, face)
        return self._rec.feature(aligned).flatten()

    def pairwise_min_similarity(self, images: list[bytes]) -> float:
        """모든 쌍의 코사인 유사도 중 최소값. 얼굴 미검출 시 QcFailed."""
        if len(images) < 2:
            raise QcFailed("insufficient_images")
        feats = [self._embed(d) for d in images]
        mn = 1.0
        for i in range(len(feats)):
            for j in range(i + 1, len(feats)):
                a, b = feats[i], feats[j]
                denom = (float(np.linalg.norm(a)) * float(np.linalg.norm(b))) + 1e-9
                cos = float(np.dot(a, b)) / denom
                mn = min(mn, cos)
        return mn


def load_face_qc(settings) -> "FaceQc | None":
    """설정에 따라 FaceQc 를 생성. disabled 이거나 weights 부재면 None(QC 스킵)."""
    if not getattr(settings, "fm_face_qc_enabled", False):
        return None
    model_dir = getattr(settings, "fm_face_qc_dir", None) or _DEFAULT_DIR
    try:
        return FaceQc(model_dir)
    except Exception as e:  # weights 부재 등 — 초기화 실패는 QC 스킵으로 강등(생성 자체는 막지 않음)
        log.warning("face QC init failed: %s", type(e).__name__)
        return None
