"""인물 LoRA 얼굴 패스 — Gemini 가 만든 착용컷의 얼굴만 등록 인물 LoRA 로 뒤에서 교체한다.

cut_generator.generate() 가 GeminiImageClient.generate_content_image() 결과를 받은 직후의
후처리다. provider 는 그대로(Gemini 가 옷·장면을 만들고) 얼굴 타원만 교체한다.

기하·전처리는 학습 데이터를 만든 build_v4c.py 를 **그대로 복제**한다(v4c/v5 학습 분포).
값이 1픽셀이라도 다르면 학습 분포를 벗어나므로 여기서 새로 설계하지 않는다:

    side = int(min(3·얼굴폭, W, H));  x0/y0 = clamp(중심 − side/2, 0, W − side)
    crop → 1024² Lanczos,  s = 1024/side,  fb = 얼굴 박스를 크롭 좌표로
    타원(학습) = [fb0 − 0.30·fb2, fb1 − 0.60·fb3, fb0 + 1.30·fb2, fb1 + 1.15·fb3]  ← ELLIPSE_TRAIN
    타원(제품 기본) = [fb0 − 0.55·fb2, fb1 − 1.10·fb3, fb0 + 1.55·fb2, fb1 + 1.25·fb3]  ← ELLIPSE(확장; 헤어라인 띠 제거)
    control = 타원 안을 GaussianBlur(radius = 0.5·fb2) 로 채움 (binary = feather(0.12) > 127)

추론 경로는 C 하나뿐: QwenImageEditPlusPipeline 전체 생성(control 이 조건) + 픽셀 합성.
QwenImageEditInpaintPipeline 은 학습 조건 경로와 달라 블러 덩어리를 그대로 두므로 쓰지
않는다(2026-09-07 실측, docs/personalization/identity-lora-handoff-2026-09-07.md §8).

GPU 없이 단위 테스트 가능: 기하·프롬프트·합성·게이트는 순수 CPU(cv2 YuNet + PIL).
렌더만 FaceBackend 로 분리 — QwenLocalBackend(파드/개발, face_identity_qwen.py — no-torch 가드 예외) ·
HttpFaceBackend(원격 GPU) · NullBackend(테스트). run_face_pass 는 어떤 경우에도 예외를 던지지 않고 원본으로 폴백한다.

PII: 얼굴 바이트·경로는 로그·예외 메시지에 싣지 않는다(face_qc 와 같은 규칙).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import math
import os
import threading
import time
from dataclasses import dataclass, field
from io import BytesIO
from typing import Protocol

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .face_identity_qwen import RENDER_GUIDANCE, RENDER_NEGATIVE, RENDER_STEPS, QwenLocalBackend

log = logging.getLogger("wearless.face_identity")

CROP = 1024
#: build_v4c.py SIG — control 블러 반경 = 0.5 × 크롭 안 얼굴폭
CONTROL_BLUR_FRAC = 0.5
#: build_v4c.py 페더 — 마스크 GaussianBlur 반경 = 0.12 × 크롭 안 얼굴폭 (binary 도 여기서 파생)
FEATHER_FRAC = 0.12
#: 타원 = 얼굴 박스 기준 (좌, 상, 우, 하) 배율.
#: **기본 = 확장 타원**(2026-09-08 룩북·표정 시험): 기존 타원은 이마 위에서 원본 머리와 생성 앞머리가 부딪혀
#: 어두운 띠가 남았고, 확장하면 사라졌다(lb1 0.673→0.730, lb2 0.685→0.737). 학습 마스크(ELLIPSE_TRAIN)와
#: 달라지지만 그 두 컷에선 점수도 올랐다. 학습 분포 그대로가 필요한 곳(데이터 빌드·재현)은 ELLIPSE_TRAIN 을 쓴다.
ELLIPSE = (-0.55, -1.10, 1.55, 1.25)
#: build_v4c/v5/v6 학습 데이터가 쓴 타원 — 데이터 재생성·픽셀 대조 전용
ELLIPSE_TRAIN = (-0.30, -0.60, 1.30, 1.15)
#: upscale(1024/side) 이 이보다 크면 원본 얼굴폭 < 114px — 디테일이 보간으로 채워진다. 표시만 한다.
LOW_DETAIL_UPSCALE = 3.0
DEFAULT_SEEDS = (42, 43, 44, 102, 103, 104)
DEFAULT_TOKEN = "ohwx man"
#: 렌더 설정(25 step · guidance 4 · neg "" · 1024²)은 face_identity_qwen.RENDER_* 가 정본 — 위에서 재수출.
#: yaw_proxy = |코x − 눈중점x| / 눈간격 (YuNet 랜드마크). **프롬프트 각도구 전용 경계** — 채점 창·게이트 값과는
#: 별개다. 0.16 이었을 때 weak_34(입력 0.162)가 "three-quarters" 를 받아 결과가 0.43 으로 원본보다 더 돌아갔다
#: (2026-09-08 블라인드 패인). 0.25 미만은 전부 "facing the camera".
YAW_FRONT_MAX = 0.25
YAW_THREE_QUARTER_MAX = 0.45
#: **적용 규칙**(모두 확대본 기준 yaw — 원본 저해상도에서 잰 yaw 는 랜드마크 오차로 더 크게 나온다):
#:   ≤0.25 적용 · 0.25~0.65 적용하되 pose_risk=True · >0.65 건너뜀(skipped_reason="yaw")
#: 근거: ESRGAN 전처리 회차에서 coor1 0.453→0.469 · coor2 0.624→0.549 · coor3 0.494→0.592 · coor4 0.489→0.701 로
#: 4컷 모두 각도가 유지됐고 정면화 게이트도 발동하지 않았다. 정면화의 원인은 각도가 아니라 입력 해상도였다.
#: ★ ESRGAN 전처리 전제. Lanczos 폴백 경로에서는 정면화 위험이 남는다(coor2 Lanczos 0.741→0.437 실측).
#: 표본 4컷·시드 1개의 잠정값.
YAW_APPLY_MAX = 0.65
YAW_POSE_RISK_MIN = 0.25
#: 정면화 게이트: 결과 yaw < 입력 yaw × 0.6 이면 포즈가 펴진 것(측면 입력이 3/4 로) → 실패, 시드 재시도.
#: 입력이 이미 정면(<0.20)이면 검사하지 않는다(값이 작아 비율이 불안정).
GATE_YAW_FLATTEN_RATIO = 0.6
GATE_YAW_FLATTEN_MIN_IN = 0.20
#: 자동 확대: 얼굴폭이 이보다 작으면 이미지 전체를 먼저 확대한다(웹 썸네일 대응). 목표 얼굴폭 / 배율 상한.
AUTO_UPSCALE_FACE_W_MIN = 120.0
AUTO_UPSCALE_TARGET_FACE_W = 150.0
AUTO_UPSCALE_MAX = 6
#: 게이트 — 결과 얼굴폭 입력 대비 ±30%, 박스 중심 이탈 ≤ 얼굴높이 15%,
#: 결과 yaw_proxy > max(0.20, 입력 yaw_proxy × 2.0) 이면 실패(원본보다 더 돌아간 결과 → 시드 재시도)
GATE_WIDTH_TOL = 0.30
GATE_CENTER_FRAC = 0.15
GATE_YAW_DRIFT_MIN = 0.20
GATE_YAW_DRIFT_MULT = 2.0
#: 결과 얼굴의 고주파 표준편차가 원본의 이 배율 미만일 때만 grain 재주입
GRAIN_MIN_RATIO = 0.85
#: 표정 추정(YuNet 5점 + 입술 색 마스크, 외부 모델 없음). 2026-09-08 v5 학습 원본 118장(캡션 라벨) 캘리브레이션:
#:   mc = (입술 마스크 중심 y − 입꼬리 평균 y) / 입폭 — 무표정 p10 0.035 · 중앙 0.064, smiling 중앙 −0.008 · p75 0.053
#:   ratio = 입폭 / 눈간격 — 무표정 중앙 0.853(p90 0.892), smiling p10 0.869
#: 규칙: mc ≤ 0.00 & ratio ≥ 0.86 → "smiling" / mc ≥ 0.045 & ratio ≤ 0.87 → "neutral expression" / 그 외·옆얼굴 → 생략.
#: 라벨셋 결과: 무표정→smiling 0/47, smiling→neutral 0/11, smiling 재현 7/11, 무표정 재현 28/47.
#: 치아 노출 비율은 판정에 쓰지 않고 메타로만 남긴다(스튜디오 사진 ≈0, Gemini 렌더는 잡음).
EXPR_SMILE_MC_MAX = 0.0
EXPR_SMILE_RATIO_MIN = 0.86
EXPR_NEUTRAL_MC_MIN = 0.045
EXPR_NEUTRAL_RATIO_MAX = 0.87
#: 색 보정에 쓰는 타원 테두리 링 폭(px, 타원 안쪽)
COLOR_RING_PX = 8

_YUNET = "face_detection_yunet_2023mar.onnx"
_DEFAULT_MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "face_models")
_ANGLE_FRONT = "facing the camera"
_ANGLE_THREE_QUARTER = "turned three-quarters toward camera"
_ANGLE_PROFILE = "in profile"
#: 허용 어휘 3개. 추정기는 "smiling"/"neutral" 을 내고, 치아가 안 보이는 미소는 호출자가 "slight"(= slight smile)로 낮춘다.
_EXPRESSIONS = {"smiling": "smiling", "neutral": "neutral expression", "neutral expression": "neutral expression",
                "slight": "slight smile", "slight smile": "slight smile"}
#: 추정이 smiling 일 때 치아 비율이 이 값 미만이면 "slight smile" 로 낮춘다.
EXPR_TEETH_MIN = 0.05


# ---------------------------------------------------------------- YuNet


def default_model_dir() -> str:
    return os.getenv("FACE_IDENTITY_MODEL_DIR") or _DEFAULT_MODEL_DIR


_DET_LOCK = threading.RLock()
_DETECTORS: dict[str, object] = {}


def _detector(model_dir: str | None):
    model_dir = model_dir or default_model_dir()
    with _DET_LOCK:
        det = _DETECTORS.get(model_dir)
        if det is None:
            path = os.path.join(model_dir, _YUNET)
            if not os.path.exists(path):
                # 경로는 예외에 싣지 않는다(face_qc 와 같은 규칙).
                raise FileNotFoundError("face identity weights missing")
            det = cv2.FaceDetectorYN.create(path, "", (320, 320), score_threshold=0.7)
            _DETECTORS[model_dir] = det
        return det


@dataclass(frozen=True)
class FaceDetection:
    box: tuple[float, float, float, float]  # x, y, w, h — 이미지 픽셀
    yaw_proxy: float
    eye_dist: float
    score: float
    #: YuNet 5점: 오른눈·왼눈·코끝·오른입꼬리·왼입꼬리 (x, y)
    landmarks: tuple[tuple[float, float], ...] = ()

    @property
    def center(self) -> tuple[float, float]:
        return self.box[0] + self.box[2] / 2, self.box[1] + self.box[3] / 2


def detect_face(image: Image.Image, model_dir: str | None = None) -> FaceDetection | None:
    """YuNet 최대 얼굴(면적 기준 — build_v4c.face_box 와 동일). 미검출이면 None."""
    arr = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR)
    h, w = arr.shape[:2]
    det = _detector(model_dir)
    with _DET_LOCK:  # YuNet 객체는 setInputSize 상태를 가진다 — 스레드 간 직렬화
        det.setInputSize((w, h))
        _, faces = det.detect(arr)
    if faces is None or len(faces) == 0:
        return None
    f = faces[int(np.argmax(faces[:, 2] * faces[:, 3]))]
    eye_r, eye_l, nose = f[4:6], f[6:8], f[8:10]
    mid = (eye_r + eye_l) / 2
    eye_dist = float(np.linalg.norm(eye_r - eye_l)) or 1.0
    yaw = abs(float(nose[0] - mid[0])) / eye_dist
    return FaceDetection(
        box=tuple(float(v) for v in f[:4]),
        yaw_proxy=round(yaw, 3),
        eye_dist=round(eye_dist, 1),
        score=float(f[14]) if len(f) > 14 else 0.0,
        landmarks=tuple((float(f[i]), float(f[i + 1])) for i in range(4, 14, 2)),
    )


def estimate_expression(image: Image.Image, det: FaceDetection) -> tuple[str | None, dict]:
    """입력 얼굴 표정 → "smiling" / "neutral" / None(생략). 근거 수치는 두 번째 반환값(메타용).

    입술 마스크 = 입꼬리 상자 안에서 볼 피부보다 채도가 높거나(붉음) 뚜렷이 어두운 픽셀(중앙 70% 열).
    mc·ratio 규칙은 모듈 상수 주석의 캘리브레이션 참조. 옆얼굴(yaw ≥ YAW_THREE_QUARTER_MAX)은 캡션과
    같이 표정 없음.
    """
    if len(det.landmarks) < 5:
        return None, {"reason": "no_landmarks"}
    if det.yaw_proxy >= YAW_THREE_QUARTER_MAX:
        return None, {"reason": "profile"}
    (rex, rey), (lex, ley), _nose, (rmx, rmy), (lmx, lmy) = det.landmarks
    eye_dist = math.hypot(rex - lex, rey - ley) or 1.0
    mw = math.hypot(rmx - lmx, rmy - lmy)
    if mw < 6:
        return None, {"reason": "mouth_too_small", "mouth_w": round(mw, 1)}
    cx, yc = (rmx + lmx) / 2, (rmy + lmy) / 2
    rgb = np.asarray(image.convert("RGB"))
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).astype(np.float32)
    H, W = hsv.shape[:2]

    def c(v, lo, hi):
        return int(min(max(v, lo), hi))

    bx0, bx1 = c(cx - 0.45 * mw, 0, W - 2), c(cx + 0.45 * mw, 1, W - 1)
    by0, by1 = c(yc - 0.30 * mw, 0, H - 2), c(yc + 0.35 * mw, 1, H - 1)
    box = hsv[by0:by1, bx0:bx1]
    if bx0 > 2 and bx1 < W - 3:
        skin = np.concatenate([hsv[by0:by1, c(bx0 - 0.25 * mw, 0, W - 2):bx0], hsv[by0:by1, bx1:c(bx1 + 0.25 * mw, 1, W - 1)]], axis=1)
    else:
        skin = box
    skin_s, skin_v = float(np.median(skin[..., 1])), float(np.median(skin[..., 2]))
    sub = box[:, int(0.15 * box.shape[1]):int(0.85 * box.shape[1])]
    lip = (sub[..., 1] > skin_s * 1.15) | (sub[..., 2] < skin_v * 0.80)
    ys = np.nonzero(lip)[0]
    metrics = {"ratio": round(mw / eye_dist, 3), "mouth_w": round(mw, 1), "lip_px": len(ys)}
    if len(ys) < 20:
        metrics["reason"] = "no_lip_mask"
        return None, metrics
    mc = (by0 + float(ys.mean()) - yc) / mw
    lip_v, lip_s = float(np.median(sub[..., 2][lip])), float(np.median(sub[..., 1][lip]))
    inner = sub[ys.min():ys.max() + 1]
    teeth = float(((inner[..., 2] > lip_v * 1.2) & (inner[..., 1] < lip_s * 0.5)).mean()) if inner.size else 0.0
    metrics.update({"mc": round(mc, 3), "teeth": round(teeth, 3)})
    ratio = metrics["ratio"]
    if mc <= EXPR_SMILE_MC_MAX and ratio >= EXPR_SMILE_RATIO_MIN:
        return "smiling", metrics
    if mc >= EXPR_NEUTRAL_MC_MIN and ratio <= EXPR_NEUTRAL_RATIO_MAX:
        return "neutral", metrics
    metrics["reason"] = "ambiguous"
    return None, metrics


# ---------------------------------------------------------------- 기하 (build_v4c 복제)


@dataclass(frozen=True)
class FacePlan:
    width: int
    height: int
    box: tuple[float, float, float, float]  # 원본 얼굴 박스 x, y, w, h
    crop: tuple[int, int, int]  # x0, y0, side (원본 픽셀)
    upscale: float  # 1024 / side — 반드시 기록
    face_box_crop: tuple[float, float, float, float]  # fb (크롭 좌표)
    ellipse: tuple[float, float, float, float]  # 크롭 좌표 (좌, 상, 우, 하)
    yaw_proxy: float
    eye_dist: float
    low_detail: bool
    #: 입력 표정 추정 — "smiling" / "neutral" / None(프롬프트에서 생략). 근거 수치는 expression_metrics.
    expression: str | None = None
    expression_metrics: dict = field(default_factory=dict)

    @property
    def face_width(self) -> float:
        return self.box[2]

    @property
    def face_height(self) -> float:
        return self.box[3]

    @property
    def anchor_center(self) -> tuple[float, float]:
        """게이트 기준점(원본 픽셀) = 얼굴 박스 중심.

        타원은 머리를 담느라 박스 중심에서 위로 0.225·얼굴높이 치우쳐 있어, 타원의 기하 중심을
        기준으로 잡으면 정상 결과도 15% 한도를 넘긴다. 타원이 붙어 있는 기준점(박스 중심)을 쓴다.
        """
        return self.box[0] + self.box[2] / 2, self.box[1] + self.box[3] / 2

    def to_meta(self) -> dict:
        return {
            "size": [self.width, self.height],
            "face_box": [round(v, 1) for v in self.box],
            "crop": list(self.crop),
            "upscale": round(self.upscale, 3),
            "low_detail": self.low_detail,
            "face_width_in": round(self.box[2], 1),
            "yaw_proxy": self.yaw_proxy,
            "eye_dist": self.eye_dist,
            "expression": self.expression,
            "expression_metrics": dict(self.expression_metrics),
        }


def plan_from_box(
    width: int,
    height: int,
    box: tuple[float, float, float, float],
    *,
    yaw_proxy: float = 0.0,
    eye_dist: float = 0.0,
    expression: str | None = None,
    expression_metrics: dict | None = None,
    ellipse: tuple[float, float, float, float] = ELLIPSE,
) -> FacePlan | None:
    """얼굴 박스 → FacePlan. 크롭 산식은 build_v4c.build() 그대로(정수 절단 포함).
    타원 배율은 기본이 확장(ELLIPSE) — 학습 데이터 재현은 ellipse=ELLIPSE_TRAIN 을 넘긴다."""
    bx, by, bw, bh = (float(v) for v in box)
    cx, cy = bx + bw / 2, by + bh / 2
    side = int(min(3 * bw, width, height))
    if side <= 0:
        return None
    x0 = int(min(max(0, cx - side / 2), width - side))
    y0 = int(min(max(0, cy - side / 2), height - side))
    s = CROP / side
    fb = ((bx - x0) * s, (by - y0) * s, bw * s, bh * s)
    ell = (
        fb[0] + ellipse[0] * fb[2],
        fb[1] + ellipse[1] * fb[3],
        fb[0] + ellipse[2] * fb[2],
        fb[1] + ellipse[3] * fb[3],
    )
    return FacePlan(
        width=int(width),
        height=int(height),
        box=(bx, by, bw, bh),
        crop=(x0, y0, side),
        upscale=s,
        face_box_crop=fb,
        ellipse=ell,
        yaw_proxy=float(yaw_proxy),
        eye_dist=float(eye_dist),
        low_detail=s > LOW_DETAIL_UPSCALE,
        expression=expression,
        expression_metrics=dict(expression_metrics or {}),
    )


def plan_from_image(image: Image.Image, model_dir: str | None = None) -> FacePlan | None:
    """계획만 세운다(확대 없음). 자동 확대까지 포함한 진입점은 prepare_image()."""
    det = detect_face(image, model_dir)
    if det is None:
        return None
    w, h = image.size
    expression, metrics = estimate_expression(image, det)
    return plan_from_box(w, h, det.box, yaw_proxy=det.yaw_proxy, eye_dist=det.eye_dist,
                         expression=expression, expression_metrics=metrics)


def prepare_image(image: Image.Image, model_dir: str | None = None) -> tuple[Image.Image, FacePlan | None, dict]:
    """(확대된 이미지, 계획, 메타). 작은 얼굴은 auto_upscale 로 키운 뒤 그 이미지에서 다시 계획한다.

    메타의 skipped_reason: no_face · too_small(6배로도 120px 미만) · yaw(> YAW_APPLY_MAX). None 이면 적용 대상.
    pose_risk 는 0.25 < yaw ≤ 0.45 구간 표시(적용은 한다).
    """
    meta: dict = {"skipped_reason": None, "pose_risk": False}
    det = detect_face(image, model_dir)
    if det is None:
        meta["skipped_reason"] = "no_face"
        return image, None, meta
    image, umeta = auto_upscale(image, det.box[2])
    meta.update(umeta)
    plan = plan_from_image(image, model_dir) if umeta["upscale_applied"] else plan_from_box(
        image.size[0], image.size[1], det.box, yaw_proxy=det.yaw_proxy, eye_dist=det.eye_dist,
        **dict(zip(("expression", "expression_metrics"), estimate_expression(image, det))))
    if plan is None:
        meta["skipped_reason"] = "no_face"
        return image, None, meta
    if plan.face_width < AUTO_UPSCALE_FACE_W_MIN:
        meta["skipped_reason"] = "too_small"
    elif plan.yaw_proxy > YAW_APPLY_MAX:
        meta["skipped_reason"] = "yaw"
    elif plan.yaw_proxy > YAW_POSE_RISK_MIN:
        meta["pose_risk"] = True
    meta["face_w_after"] = round(plan.face_width, 1)
    return image, plan, meta


#: 확대기 훅 — 제품에서 ESRGAN 서비스를 붙일 자리. (image, k) -> Image 를 반환하면 그것을 쓰고, None/예외면 Lanczos.
_UPSCALER = None


def set_upscaler(fn) -> None:
    """ESRGAN 등 외부 확대기 등록. fn(image: Image, scale: int) -> Image | None."""
    global _UPSCALER
    _UPSCALER = fn


def auto_upscale(image: Image.Image, face_w: float) -> tuple[Image.Image, dict]:
    """얼굴폭이 작으면 이미지 전체를 먼저 확대한다(웹 썸네일 대응).

    배율 k = ceil(150 / 얼굴폭), 상한 6. 확대 후에도 얼굴폭이 120px 미만이면 호출자가 건너뛴다.
    ESRGAN 훅이 있으면 그것을, 없으면 Lanczos. 결과는 확대된 해상도 그대로 쓴다(되돌리면 생성 얼굴이 버려진다).
    """
    meta = {"upscale_applied": False, "upscale_method": None, "face_w_before": round(float(face_w), 1),
            "face_w_after": round(float(face_w), 1), "upscale_k": 1}
    if face_w >= AUTO_UPSCALE_FACE_W_MIN or face_w <= 0:
        return image, meta
    k = min(AUTO_UPSCALE_MAX, math.ceil(AUTO_UPSCALE_TARGET_FACE_W / face_w))
    if k <= 1:
        return image, meta
    out, method = None, "lanczos"
    if _UPSCALER is not None:
        try:
            out = _UPSCALER(image, k)
            method = "esrgan" if out is not None else "lanczos"
        except Exception as exc:  # noqa: BLE001 — 확대 실패는 폴백으로 흡수
            log.warning("face_identity upscaler failed (%s); falling back to Lanczos", type(exc).__name__)
            out = None
    if out is None:
        out = image.resize((image.width * k, image.height * k), Image.LANCZOS)
    meta.update({"upscale_applied": True, "upscale_method": method, "upscale_k": k,
                 "face_w_after": round(float(face_w) * out.width / image.width, 1)})
    return out, meta


def _decode(image_bytes: bytes) -> Image.Image:
    with Image.open(BytesIO(image_bytes)) as im:
        im.load()
        return im.convert("RGB")


def plan_face_pass(image_bytes: bytes, model_dir: str | None = None) -> FacePlan | None:
    """YuNet 최대 얼굴로 크롭·타원 계획. 미검출이면 None(얼굴 패스 생략)."""
    return plan_from_image(_decode(image_bytes), model_dir)


def crop_1024(image: Image.Image, plan: FacePlan) -> Image.Image:
    x0, y0, side = plan.crop
    return image.convert("RGB").crop((x0, y0, x0 + side, y0 + side)).resize((CROP, CROP), Image.LANCZOS)


def ellipse_box(plan: FacePlan, ellipse: tuple[float, float, float, float] | None = None) -> list[float]:
    """크롭 좌표 타원 상자. ellipse=None 이면 plan 이 들고 있는 것(= 계획 시점의 기본 ELLIPSE)."""
    if ellipse is None:
        return list(plan.ellipse)
    fb = plan.face_box_crop
    return [fb[0] + ellipse[0] * fb[2], fb[1] + ellipse[1] * fb[3], fb[0] + ellipse[2] * fb[2], fb[1] + ellipse[3] * fb[3]]


def ellipse_mask(plan: FacePlan, ellipse: tuple[float, float, float, float] | None = None) -> Image.Image:
    m = Image.new("L", (CROP, CROP), 0)
    ImageDraw.Draw(m).ellipse(ellipse_box(plan, ellipse), fill=255)
    return m


def feather_mask(plan: FacePlan, feather: float = FEATHER_FRAC,
                 ellipse: tuple[float, float, float, float] | None = None) -> Image.Image:
    return ellipse_mask(plan, ellipse).filter(ImageFilter.GaussianBlur(max(3, int(feather * plan.face_box_crop[2]))))


def binary_mask(plan: FacePlan) -> Image.Image:
    """학습 control 의 블러 영역. 합성 페더와 무관하게 항상 0.12 페더에서 파생(build_v4c)."""
    return feather_mask(plan, FEATHER_FRAC).point(lambda v: 255 if v > 127 else 0)


def build_control(image: Image.Image, plan: FacePlan) -> Image.Image:
    """학습 control 과 동일: 크롭 1024² 의 타원 안을 radius 0.5·얼굴폭 가우시안 블러로 채움."""
    up = crop_1024(image, plan)
    blurred = up.filter(ImageFilter.GaussianBlur(max(2, int(plan.face_box_crop[2] * CONTROL_BLUR_FRAC))))
    return Image.composite(blurred, up, binary_mask(plan))


# ---------------------------------------------------------------- 프롬프트


def angle_phrase(yaw_proxy: float) -> str:
    if yaw_proxy < YAW_FRONT_MAX:
        return _ANGLE_FRONT
    if yaw_proxy < YAW_THREE_QUARTER_MAX:
        return _ANGLE_THREE_QUARTER
    return _ANGLE_PROFILE


AUTO = "auto"
#: 표정어 기본값 — **절대 생략하지 않는다**. 표정어가 없으면 학습 평균으로 수렴해 입꼬리가 처진 뚱한 얼굴이 된다
#: (2026-09-08 표정 시험 lb1·lb2: 없음 → 시무룩, neutral expression → 편안한 무표정).
EXPR_FALLBACK = "neutral expression"


def build_prompt(plan: FacePlan, expression: str | None = AUTO, *, token: str = DEFAULT_TOKEN) -> str:
    """학습 표본이 쓴 짧은 형식: "<token>, <표정>, <각도구>, photograph".

    expression="auto"(기본) 는 plan.expression(입력에서 추정) 을 쓰고, 추정이 없거나(측면·애매) None 이 오면
    "neutral expression" 으로 채운다 — 표정어는 어떤 경로로도 빠지지 않는다. 어휘는 세 개뿐:
    "neutral expression" / "slight smile" / "smiling". 학습 캡션의 6슬롯 형식(구도·배경·조명)은 쓰지 않는다.
    """
    if expression == AUTO:
        expression = plan.expression
    phrase = EXPR_FALLBACK if expression is None else _EXPRESSIONS.get(str(expression).strip().lower())
    if phrase is None:
        raise ValueError("unknown expression")
    return ", ".join([token, phrase, angle_phrase(plan.yaw_proxy), "photograph"])


# ---------------------------------------------------------------- 합성


def _luma(arr: np.ndarray) -> np.ndarray:
    return arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114


def _hf_std(arr: np.ndarray, region: np.ndarray) -> float:
    """영역 안 고주파(σ=1 가우시안 잔차) 표준편차 — grain 판정용."""
    luma = _luma(arr).astype(np.float32)
    hp = luma - cv2.GaussianBlur(luma, (0, 0), 1.0)
    vals = hp[region]
    return float(vals.std()) if vals.size else 0.0


def paste_alpha(plan: FacePlan, feather: float = FEATHER_FRAC) -> np.ndarray:
    """원본 해상도 (H, W) float32 알파. 0 인 픽셀은 composite 가 원본을 그대로 둔다."""
    x0, y0, side = plan.crop
    small = feather_mask(plan, feather).resize((side, side), Image.BILINEAR)
    alpha = np.zeros((plan.height, plan.width), np.float32)
    alpha[y0 : y0 + side, x0 : x0 + side] = np.asarray(small, np.float32) / 255.0
    return alpha


def composite_with_meta(
    original: Image.Image,
    generated_1024: Image.Image,
    plan: FacePlan,
    *,
    feather: float = FEATHER_FRAC,
    grain: bool | None = None,
) -> tuple[Image.Image, dict]:
    """생성 1024² 크롭의 타원 영역을 원본에 되붙인다. 마스크 밖 픽셀은 원본과 100% 동일.

    순서: 색 보정(타원 테두리 안쪽 8px 링 평균 RGB 를 원본에 맞춤) → 페더 합성(1024²) →
    조건부 grain(결과 얼굴 고주파 σ < 0.85×원본일 때만) → 크롭 크기로 축소해 알파로 되붙임.
    grain=None 이면 조건부, True/False 는 강제.
    """
    orig = original.convert("RGB")
    up = np.asarray(crop_1024(orig, plan), np.float32)
    gen = generated_1024.convert("RGB")
    if gen.size != (CROP, CROP):
        gen = gen.resize((CROP, CROP), Image.LANCZOS)
    gen_arr = np.asarray(gen, np.float32)

    ell = np.asarray(ellipse_mask(plan)) > 127
    k = 2 * COLOR_RING_PX + 1
    inner = cv2.erode(ell.astype(np.uint8), np.ones((k, k), np.uint8)).astype(bool)
    ring = ell & ~inner
    meta: dict = {"feather": feather, "color_shift": [0.0, 0.0, 0.0]}
    if ring.any():
        shift = up[ring].mean(axis=0) - gen_arr[ring].mean(axis=0)
        gen_arr = np.clip(gen_arr + shift, 0, 255)
        meta["color_shift"] = [round(float(v), 2) for v in shift]

    alpha = np.asarray(feather_mask(plan, feather), np.float32)[..., None] / 255.0
    comp = gen_arr * alpha + up * (1.0 - alpha)

    hf_orig = _hf_std(up, ell)
    hf_result = _hf_std(comp, ell)
    apply_grain = (hf_orig > 0 and hf_result < GRAIN_MIN_RATIO * hf_orig) if grain is None else bool(grain)
    meta.update({"hf_std_orig": round(hf_orig, 3), "hf_std_result": round(hf_result, 3), "grain_applied": apply_grain})
    if apply_grain:
        target = math.sqrt(max(hf_orig * hf_orig - hf_result * hf_result, 0.0))
        rng = np.random.default_rng(int(plan.crop[0]) * 7919 + int(plan.crop[1]))
        noise = rng.normal(0.0, target, size=(CROP, CROP, 1)).astype(np.float32)
        comp = comp + noise * alpha
        meta["hf_std_after_grain"] = round(_hf_std(comp, ell), 3)
    comp_img = Image.fromarray(np.clip(comp + 0.5, 0, 255).astype(np.uint8))

    x0, y0, side = plan.crop
    small = np.asarray(comp_img.resize((side, side), Image.LANCZOS), np.float32)
    a_small = paste_alpha(plan, feather)[y0 : y0 + side, x0 : x0 + side][..., None]
    out = np.asarray(orig, np.float32).copy()
    region = out[y0 : y0 + side, x0 : x0 + side]
    out[y0 : y0 + side, x0 : x0 + side] = small * a_small + region * (1.0 - a_small)
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8)), meta


def composite(
    original: Image.Image,
    generated_1024: Image.Image,
    plan: FacePlan,
    *,
    feather: float = FEATHER_FRAC,
    grain: bool | None = None,
) -> Image.Image:
    return composite_with_meta(original, generated_1024, plan, feather=feather, grain=grain)[0]


# ---------------------------------------------------------------- 게이트


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reason: str  # ok · no_face · face_width · center_off · yaw_drift · yaw_flatten
    face_width: float | None = None
    center_offset: float | None = None  # 얼굴높이 대비 비율
    yaw_proxy: float | None = None


def evaluate_gate(plan: FacePlan, result_image: Image.Image, model_dir: str | None = None) -> GateResult:
    det = detect_face(result_image, model_dir)
    if det is None:
        return GateResult(False, "no_face")
    w_in = plan.face_width
    ax, ay = plan.anchor_center
    cx, cy = det.center
    offset = math.hypot(cx - ax, cy - ay) / max(plan.face_height, 1.0)
    width_ok = abs(det.box[2] - w_in) <= GATE_WIDTH_TOL * w_in
    center_ok = offset <= GATE_CENTER_FRAC
    yaw_ok = det.yaw_proxy <= yaw_drift_limit(plan.yaw_proxy)
    # 정면화: 측면 입력이 3/4 로 펴진 것도 포즈 변형이다(yaw_drift 는 "더 돌아간 것"만 잡는다).
    flat = (plan.yaw_proxy >= GATE_YAW_FLATTEN_MIN_IN and det.yaw_proxy < GATE_YAW_FLATTEN_RATIO * plan.yaw_proxy)
    if not width_ok:
        reason = "face_width"
    elif not center_ok:
        reason = "center_off"
    elif not yaw_ok:
        reason = "yaw_drift"
    elif flat:
        reason = "yaw_flatten"
    else:
        reason = "ok"
    return GateResult(reason == "ok", reason, round(det.box[2], 1), round(offset, 3), det.yaw_proxy)


def yaw_drift_limit(input_yaw: float) -> float:
    """결과 yaw_proxy 허용 상한 = max(0.20, 입력 × 2.0). 그 위면 원본보다 더 돌아간 결과."""
    return max(GATE_YAW_DRIFT_MIN, float(input_yaw) * GATE_YAW_DRIFT_MULT)


def check_gate(plan: FacePlan, result_image: Image.Image, model_dir: str | None = None) -> bool:
    """결과 YuNet 얼굴폭이 입력 대비 ±30% 밖, 박스 중심이 기준점에서 얼굴높이 15% 초과 이탈,
    결과 yaw_proxy 가 max(0.20, 입력×2) 초과(yaw_drift), 또는 입력 대비 0.6배 미만(yaw_flatten)이면 실패."""
    return evaluate_gate(plan, result_image, model_dir).passed


# ---------------------------------------------------------------- 백엔드


class FaceBackend(Protocol):
    def render(self, control: Image.Image, prompt: str, seed: int) -> Image.Image: ...


class NullBackend:
    """control 을 그대로 돌려준다(테스트·배선 검증용)."""

    def render(self, control: Image.Image, prompt: str, seed: int) -> Image.Image:
        return control.copy()


class HttpFaceBackend:
    """원격 GPU 렌더 서비스. POST {control_png, prompt, seed, steps, guidance_scale, lora} → {image_png}."""

    def __init__(self, url: str, *, lora: str | None = None, timeout: float = 180.0, token: str | None = None):
        self.url = url
        self.lora = lora
        self.timeout = timeout
        self.token = token

    def render(self, control: Image.Image, prompt: str, seed: int) -> Image.Image:
        import httpx

        buf = BytesIO()
        control.convert("RGB").save(buf, "PNG")
        payload = {
            "control_png": base64.b64encode(buf.getvalue()).decode(),
            "prompt": prompt,
            "seed": int(seed),
            "steps": RENDER_STEPS,
            "guidance_scale": RENDER_GUIDANCE,
            "negative_prompt": RENDER_NEGATIVE,
            "lora": self.lora,
        }
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        res = httpx.post(self.url, json=payload, headers=headers, timeout=self.timeout)
        res.raise_for_status()
        data = base64.b64decode(res.json()["image_png"])
        with Image.open(BytesIO(data)) as im:
            im.load()
            return im.convert("RGB")


# ---------------------------------------------------------------- 실행기


@dataclass
class FacePassResult:
    image: bytes
    mime: str
    applied: bool
    meta: dict = field(default_factory=dict)


def run_face_pass(
    image_bytes: bytes,
    backend: FaceBackend,
    expression: str | None = AUTO,
    *,
    token: str = DEFAULT_TOKEN,
    seeds: tuple[int, ...] = DEFAULT_SEEDS,
    feather: float = FEATHER_FRAC,
    model_dir: str | None = None,
    mime: str = "image/png",
) -> FacePassResult:
    """시드를 순차로 시도해 check_gate 통과분을 채택. 전부 실패·예외면 원본 그대로(폴백) + 메타.

    예외를 던지지 않는다 — 얼굴 패스가 컷 생성을 막아서는 안 된다.
    """
    t0 = time.perf_counter()
    meta: dict = {"applied": False, "fallback": True, "attempts": 0, "seed": None, "tries": []}
    try:
        decoded = _decode(image_bytes)
        original, plan, pmeta = prepare_image(decoded, model_dir)
        meta.update(pmeta)
        if plan is None or pmeta["skipped_reason"]:
            meta["reason"] = pmeta["skipped_reason"] or "no_face"
            if plan is not None:
                meta.update(plan.to_meta())
            return FacePassResult(image_bytes, mime, False, meta)
        meta.update(plan.to_meta())
        prompt = build_prompt(plan, expression, token=token)
        meta["prompt"] = prompt
        control = build_control(original, plan)
        for seed in seeds:
            meta["attempts"] += 1
            t1 = time.perf_counter()
            generated = backend.render(control, prompt, int(seed))
            result, cmeta = composite_with_meta(original, generated, plan, feather=feather)
            gate = evaluate_gate(plan, result, model_dir)
            meta["tries"].append({
                "seed": int(seed),
                "gate": gate.reason,
                "face_width_out": gate.face_width,
                "center_offset": gate.center_offset,
                "yaw_out": gate.yaw_proxy,
                "ms": round((time.perf_counter() - t1) * 1000),
            })
            if gate.passed:
                meta.update(cmeta)
                meta.update({
                    "applied": True,
                    "fallback": False,
                    "seed": int(seed),
                    "face_width_out": gate.face_width,
                    "yaw_out": gate.yaw_proxy,
                    "reason": "ok",
                })
                buf = BytesIO()
                result.save(buf, "PNG")
                return FacePassResult(buf.getvalue(), "image/png", True, meta)
        meta["reason"] = "gate_failed"
        return FacePassResult(image_bytes, mime, False, meta)
    except Exception as exc:  # noqa: BLE001 — 폴백이 계약이다
        meta["reason"] = f"error:{type(exc).__name__}"
        log.warning("face_identity pass failed, keeping original: %s", type(exc).__name__)
        return FacePassResult(image_bytes, mime, False, meta)
    finally:
        meta["elapsed_ms"] = round((time.perf_counter() - t0) * 1000)


# ---------------------------------------------------------------- 레지스트리·설정 배선


@dataclass(frozen=True)
class FaceIdentitySpec:
    """virtual_models.json 항목의 `faceIdentity: {loraPath, token}`. 없으면 얼굴 패스 없음."""

    lora_path: str
    token: str = DEFAULT_TOKEN


def face_identity_from_registry_entry(entry: dict | None) -> FaceIdentitySpec | None:
    if not isinstance(entry, dict):
        return None
    info = entry.get("faceIdentity")
    if not isinstance(info, dict):
        return None
    lora = info.get("loraPath")
    if not isinstance(lora, str) or not lora.strip():
        return None
    token = info.get("token")
    return FaceIdentitySpec(lora.strip(), str(token).strip() if isinstance(token, str) and token.strip() else DEFAULT_TOKEN)


def resolve_lora_file(spec: FaceIdentitySpec, base: str | None) -> str:
    """레지스트리 loraPath 가 절대경로면 그대로, 아니면 face_identity_lora_path(디렉터리) 기준.
    base 가 .safetensors 파일이면 단일 인물 배포로 보고 그 파일을 쓴다."""
    if os.path.isabs(spec.lora_path):
        return spec.lora_path
    if base and base.endswith(".safetensors"):
        return base
    return os.path.join(base or "", spec.lora_path)


_BACKENDS: dict[tuple, FaceBackend] = {}
_BACKEND_LOCK = threading.Lock()


def resolve_backend(settings, spec: FaceIdentitySpec) -> FaceBackend | None:
    """설정에 따라 백엔드 1개(프로세스당 캐시). url → Http, 아니면 lora 파일 → QwenLocal, 둘 다 없으면 None."""
    url = getattr(settings, "face_identity_backend_url", None)
    base = getattr(settings, "face_identity_lora_path", None)
    if url:
        key = ("http", url, spec.lora_path)
    else:
        lora_file = resolve_lora_file(spec, base)
        if not os.path.exists(lora_file):
            return None
        key = ("qwen", lora_file)
    with _BACKEND_LOCK:
        backend = _BACKENDS.get(key)
        if backend is None:
            backend = HttpFaceBackend(url, lora=spec.lora_path) if url else QwenLocalBackend(key[1])
            _BACKENDS[key] = backend
        return backend


def _meta_for_log(meta: dict) -> dict:
    return {k: v for k, v in meta.items() if k not in ("prompt",)}


async def apply_face_pass(
    settings,
    image: bytes,
    mime: str,
    spec: FaceIdentitySpec,
    *,
    expression: str | None = AUTO,
) -> tuple[bytes, str]:
    """generate() 후처리 진입점. 백엔드가 없거나 실패하면 (image, mime) 그대로."""
    backend = resolve_backend(settings, spec)
    if backend is None:
        log.warning("face_identity enabled but no backend (url/lora) — skipping face pass")
        return image, mime
    # 렌더는 대부분 GPU/네트워크 대기라 이미지 CPU 풀(1 worker)을 붙들지 않게 별도 스레드로.
    result = await asyncio.to_thread(
        run_face_pass,
        image,
        backend,
        expression,
        token=spec.token,
        model_dir=getattr(settings, "fm_face_qc_dir", None),
        mime=mime,
    )
    log.info("face_identity applied=%s meta=%s", result.applied, _meta_for_log(result.meta))
    return result.image, result.mime


__all__ = [
    "AUTO",
    "CROP",
    "DEFAULT_SEEDS",
    "DEFAULT_TOKEN",
    "ELLIPSE",
    "ELLIPSE_TRAIN",
    "EXPR_FALLBACK",
    "EXPR_TEETH_MIN",
    "FEATHER_FRAC",
    "YAW_APPLY_MAX",
    "YAW_POSE_RISK_MIN",
    "FaceBackend",
    "FaceDetection",
    "FaceIdentitySpec",
    "FacePassResult",
    "FacePlan",
    "GateResult",
    "HttpFaceBackend",
    "NullBackend",
    "QwenLocalBackend",
    "apply_face_pass",
    "auto_upscale",
    "binary_mask",
    "build_control",
    "build_prompt",
    "check_gate",
    "composite",
    "composite_with_meta",
    "crop_1024",
    "detect_face",
    "ellipse_box",
    "ellipse_mask",
    "estimate_expression",
    "evaluate_gate",
    "face_identity_from_registry_entry",
    "feather_mask",
    "paste_alpha",
    "plan_face_pass",
    "plan_from_box",
    "plan_from_image",
    "prepare_image",
    "resolve_backend",
    "resolve_lora_file",
    "run_face_pass",
    "set_upscaler",
    "yaw_drift_limit",
]
