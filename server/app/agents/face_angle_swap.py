"""옆·뒷모습 컷의 머리 교체 — 등록자 각도 사진 1장으로 ComfyUI(BFS Head V5)에서 다시 그린다.

왜 얼굴 패스(face_identity)와 따로인가: v7 LoRA 는 정면·3/4 만 배웠다. 옆얼굴(yaw>0.65)은
건너뛰고, 뒷모습은 얼굴이 아예 안 잡혀 시작도 못 한다. 그래서 그 두 각도는 **등록자 실사진**을
참고로 머리만 바꾼다(2026-09-20 실측: 옆 4/4 머리·귀·볼 피부가 본인, 뒤 4/4 얼굴 안 그려짐,
옷 변화 0~228px).

머리 자리를 찾는 기준은 각도마다 다르다:
  · 옆모습 — 얼굴 박스(YuNet)가 잡힌다. 그 박스를 키운 창 안의 전경이 머리다.
  · 뒷모습 — 얼굴이 없다. 위쪽 절반에서 가장 큰 **어두운 덩어리(머리카락)** 가 머리다.
실루엣 기하(가장 좁은 행 = 목)는 옷 색에 흔들린다 — 흰 나시·연한 줄무늬는 배경과 차이가 10
안팎이라 실루엣에서 빠지고, 그러면 바지가 인물로 뽑힌다(2026-09-20 실측: 8컷 중 4컷 실패).

그 뒤는 확정 워크플로우와 같다: 머리 정사각 크롭 1024 + 머리 마스크 → ComfyUI(2511 + BFS Head V5
+ LanPaint) → 마스크 안만 원본 해상도로 합성, 옷은 원본 픽셀로 되돌림.

계약은 얼굴 패스와 같다 — 결과를 못 만들면 남의 머리를 내보내지 않고 예외를 올린다.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import time
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image

from . import face_identity

log = logging.getLogger("wearless.face_angle_swap")

PROMPT_VERSION = "face_angle_swap_v1"
#: 2511 은 BFS Head V5 와 같이 쓴다(2509+V4 조합은 깨진다 — 2026-09-18 실측).
UNET = "qwen_image_edit_2511_fp8mixed.safetensors"
CLIP = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
VAE = "qwen_image_vae.safetensors"
BFS_LORA = "bfs_head_v5_2511_merged_version_rank_16_fp16.safetensors"
STEPS = 20
CFG = 2.5
SEED = 42
#: BFS LoRA 강도. 2026-09-20 스윕(옆모습 2컷)에서 1.2 는 닮음 점수가 높았지만(0.333→0.41) 머리가
#: 부풀고 앞머리가 두꺼워졌다. 1.4 는 더 멀어졌다(0.25). 눈으로 고른 값은 1.0 이다 — 합격 판정을
#: 받은 옆 4컷·뒤 4컷이 전부 이 강도로 나왔다. 점수보다 실제로 본 결과를 따른다.
BFS_STRENGTH = 1.0
CROP_PX = 1024
#: 머리 크롭 = (여유 주기 전) 머리 크기 × 이만큼의 정사각형. 목·칼라가 같이 보여야 모델이 자리를
#: 맞춘다. 2026-09-20 성공 실행의 크롭이 머리 크기의 1.5~1.9 배였다.
CROP_HEAD_WIDTHS = 1.9
#: 머리를 이만큼(머리 너비 비율) 넓혀 머리카락이 새로 날 자리를 준다. 짧은 머리 기준
#: (긴 머리 기본값 0.6/0.9 는 배경까지 크게 잡혀 2026-09-20 에 줄였다).
HAIR_MARGIN_SIDE = 0.25
HAIR_MARGIN_BACK = 0.30
#: 얼굴 박스(옆) · 머리카락 덩어리(뒤) 아래로 더 잡는 높이 — 박스 높이 비율.
FACE_DOWN_SIDE = 0.35
HAIR_DOWN_BACK = 0.12
#: 배경과 사람을 가르는 문턱(채널별 차이의 최댓값, 0~255). 2026-09-20 실측(호리존 컷):
#: 벽 그림자 12(머리 바로 옆은 더 진하다) · 피부 82 · 옷 130 · 머리 206 → 50 이면 그림자만 떨어진다. 낮게 두면 그림자가
#: 사람으로 붙어 머리 크롭이 2배가 된다.
BG_THRESHOLD = 50.0
#: 머리카락 후보 밝기 상한 — 배경 밝기의 이 비율보다 어두운 픽셀(머리 0.13 · 그림자 0.95 ·
#: 진한 데님 0.47).
HAIR_MAX_LUMINANCE = 0.45
#: 머리 아래 목으로 보는 폭 비율 — 가장 넓은 행의 이 비율 밑으로 떨어지면 목이다.
NECK_WIDTH_RATIO = 0.6
#: 사람이 이보다 작으면 사람이 안 담긴 컷으로 본다(이미지 면적 비율).
MIN_PERSON_FRACTION = 0.02

_POS_HEAD = (
    "head_swap: start with Picture 1 as the base image, keeping its lighting, environment, and background. "
    "remove the head from Picture 1 completely and replace it with the head from Picture 2, strictly preserving "
    "the hair, eye color, nose structure of Picture 2. copy the direction of the eye, head rotation, micro "
    "expressions from Picture 1, high quality, sharp details, 4k"
)
_POS_SIDE = (
    " The hair must be exactly the hairstyle of Picture 2: the same length, the same fringe and the same nape. "
    "The person is not bald. Keep the exact side profile of Picture 1. Also copy from Picture 2 the nose shape, "
    "the lip shape and the chin and jaw line, so the profile outline matches Picture 2."
)
_POS_BACK = (
    " Picture 2 shows the back of the head. Draw the back of the head seen from behind with the same hair shape, "
    "crown and nape as Picture 2, and the same ears. No face is visible. The garment must stay exactly as in Picture 1."
)
NEG = ("bad quality, noise, blurry, worst quality, low resolution, blur, distortion, unnatural blending, "
       "cartoon, illustration, painting")

#: 컷 방향 → 등록 사진 칸(facemarket_photos 의 칸 이름). 옆모습은 컷이 보는 쪽에 맞춰 고른다.
SLOT_BACK = "sh_back"
SLOT_SIDE_NOSE_LEFT = "sh_side"        # 코가 화면 왼쪽 = 사람의 왼쪽 옆모습
SLOT_SIDE_NOSE_RIGHT = "sh_side_right"


@dataclass
class AngleSwapSpec:
    """이 컷에 쓸 등록자 각도 사진과 ComfyUI 백엔드. 워커가 만들어 generate() 에 넘긴다."""

    photos: AnglePhotos
    backend: object
    seed: int = SEED


def spec_from(settings, photos: AnglePhotos, *, pod_id: str | None = None) -> AngleSwapSpec | None:
    """부를 백엔드를 만든다. 부를 곳이 없으면 None(=이 경로를 안 탄다).

    **서버리스가 우선이다.** 엔드포인트가 설정돼 있으면 그쪽으로 간다 — 유휴 요금이 없고
    동시성을 워커 수가 맡는다. 파드 경로는 서버리스를 세우기 전·검증 중에만 쓰는 다리다.

    파드로 갈 때 주소의 정본은 **지금 살아 있는 파드**다(pod_id). 자동 기동이 재고를 못 잡아
    파드를 새로 만들면 id 가 바뀌는데, 설정값을 앞에 두면 죽은 주소를 계속 찌른다 — 얼굴
    패스가 같은 순서를 쓴다(face_identity.resolve_backend).
    """
    if not getattr(settings, "face_angle_swap_enabled", False):
        return None
    seed = int(getattr(settings, "face_angle_seed", SEED))
    endpoint = (getattr(settings, "face_angle_endpoint_id", None) or "").strip()
    api_key = (getattr(settings, "face_runpod_api_key", None) or "").strip()
    if endpoint and api_key:
        return AngleSwapSpec(photos=photos, backend=ServerlessBackend(endpoint, api_key), seed=seed)
    url = pod_backend_url(pod_id) or getattr(settings, "face_angle_backend_url", None)
    if not url:
        return None
    token = getattr(settings, "face_angle_backend_token", "") or ""
    return AngleSwapSpec(photos=photos, backend=ComfyBackend(url, token), seed=seed)


def pod_backend_url(pod_id: str | None) -> str | None:
    """파드 id → ComfyUI 주소. services/angle_autoscale 의 같은 이름과 한 몸이어야 한다 —
    어댑터는 이 주소로 /healthz 를 보고 워커는 이 주소로 컷을 만든다. 갈리면 둘이 다른 파드를
    본다. 순환 import 를 피하려고 여기서 다시 쓰되, 계약 테스트가 두 값을 맞춰 둔다."""
    pod_id = (pod_id or "").strip()
    return f"https://{pod_id}-8000.proxy.runpod.net" if pod_id else None


def direction_of(spec: dict) -> str | None:
    """이 컷이 각도 교체 대상인가 — 옆·뒷모습만."""
    direction = str((spec or {}).get("direction") or "")
    return direction if direction in ("side", "back") else None


class AngleSwapUnavailable(RuntimeError):
    """머리를 못 바꿨다 — 호출자는 컷을 실패로 끝낸다(남의 머리를 내보내지 않는다)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class AnglePhotos:
    """등록자 각도 사진 바이트. 없는 칸은 None."""

    side_nose_left: bytes | None = None    # sh_side
    side_nose_right: bytes | None = None   # sh_side_right
    back: bytes | None = None              # sh_back

    def for_direction(self, direction: str, nose_right: bool | None) -> tuple[bytes | None, str]:
        if direction == "back":
            return self.back, SLOT_BACK
        if nose_right is None:
            return None, ""
        if nose_right:
            return self.side_nose_right, SLOT_SIDE_NOSE_RIGHT
        return self.side_nose_left, SLOT_SIDE_NOSE_LEFT


@dataclass
class Plan:
    base: np.ndarray                 # 원본 RGB
    head: np.ndarray                 # 머리 마스크(bool, 머리카락 여유 포함) — 이 안만 다시 그린다
    head_core: np.ndarray            # 여유 주기 전 머리 — 크롭 기준
    garment: np.ndarray              # 옷·몸 보호 마스크(bool) — 원본 픽셀로 되돌린다
    person: np.ndarray               # 원본 전경(사람) — 톤 표본을 여기 안에서만 고른다
    box: tuple[int, int, int]        # (left, top, side) 정사각 크롭
    crop1k: np.ndarray               # 1024 크롭 RGB
    mask1k: np.ndarray               # 1024 머리 마스크(bool)
    nose_right: bool | None          # 옆모습이 보는 쪽(뒷모습이면 None)
    metadata: dict = field(default_factory=dict)


def background_color(image: np.ndarray) -> np.ndarray:
    """배경색 = 위·좌·우 테두리의 중앙값. **전체 이미지**에서만 구한다 —
    머리 크롭 테두리로 구하면 그 테두리가 사람·옷이라 판정이 통째로 뒤집힌다."""
    a = image.astype(np.float32)
    border = np.concatenate([a[:6].reshape(-1, 3), a[:, :6].reshape(-1, 3), a[:, -6:].reshape(-1, 3)])
    return np.median(border, axis=0)


def foreground(image: np.ndarray) -> np.ndarray:
    """배경(위·좌·우 테두리 색)과 다른 픽셀 전부. 벽 그림자는 BG_THRESHOLD 로 떨어진다."""
    a = image.astype(np.float32)
    bg = background_color(image)
    m = (np.abs(a - bg).max(axis=2) > BG_THRESHOLD).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    return cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8)).astype(bool)


def trim_at_neck(mask: np.ndarray) -> np.ndarray:
    """덩어리가 목 아래로 이어지면(어두운 옷과 붙었으면) 목에서 자른다.

    머리에서 가장 넓은 행 아래로 내려가다 폭이 그 60% 밑으로 떨어지는 첫 행이 목이다.
    머리카락만 있는 덩어리는 그 밑으로 안 떨어지므로 아무것도 안 자른다.
    """
    widths = mask.sum(axis=1)
    ys = np.where(mask.any(axis=1))[0]
    top, bottom = int(ys.min()), int(ys.max())
    upper = max(top + 1, top + int(0.5 * (bottom - top)) + 1)
    widest = top + int(np.argmax(widths[top:upper]))
    tail = widths[widest:bottom + 1]
    narrow = np.where(tail < NECK_WIDTH_RATIO * float(widths[widest]))[0]
    for i in narrow:
        below = tail[i + 1:]
        # 목이라면 그 아래에 옷이 다시 넓게 나온다. 그냥 끝으로 갈수록 좁아지는 머리는 안 자른다.
        if below.size and below.max() > 1.2 * max(1.0, float(tail[i])):
            out = mask.copy()
            out[widest + int(i) + 1:] = False
            return out
    return mask


def hair_blob(image: np.ndarray) -> np.ndarray:
    """뒷모습용 — 맨 위에서 시작하는 가장 큰 어두운 덩어리 = 머리카락. 목 아래는 잘라낸다.

    어두운 옷(데님)은 머리카락과 같은 밝기라 목덜미에서 붙는다(2026-09-20 실측: 붙으면 크롭이
    사진 전체가 된다). 그래서 덩어리를 고른 뒤 목에서 자른다.
    """
    a = image.astype(np.float32)
    border = np.concatenate([a[:6].reshape(-1, 3), a[:, :6].reshape(-1, 3), a[:, -6:].reshape(-1, 3)])
    background_luminance = float(np.median(border, axis=0).mean())
    dark = (a.mean(axis=2) < HAIR_MAX_LUMINANCE * background_luminance).astype(np.uint8)
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(dark)
    if count <= 1:
        return np.zeros(dark.shape, bool)
    tops = stats[1:, cv2.CC_STAT_TOP]
    areas = stats[1:, cv2.CC_STAT_AREA]
    near_top = np.where(tops <= tops.min() + 0.03 * dark.shape[0])[0]
    if not near_top.size:
        return np.zeros(dark.shape, bool)
    pick = int(near_top[int(np.argmax(areas[near_top]))]) + 1
    return trim_at_neck(labels == pick)


def _connected_to(mask: np.ndarray, seed: np.ndarray) -> np.ndarray:
    """mask 에서 seed 와 이어진 덩어리만 남긴다 — 옆에 떨어져 있는 벽 그림자 조각을 떨군다."""
    count, labels = cv2.connectedComponents(mask.astype(np.uint8))
    if count <= 1:
        return mask
    keep = {int(v) for v in np.unique(labels[seed & mask]) if v}
    if not keep:
        return mask
    return np.isin(labels, list(keep))


def window(box, shape, *, left: float, right: float, up: float, down: float) -> np.ndarray:
    """박스(x, y, w, h)를 비율만큼 넓힌 직사각 창."""
    bx, by, bw, bh = box
    mask = np.zeros(shape, bool)
    x0 = max(0, int(bx - left * bw))
    x1 = min(shape[1], int(bx + bw + right * bw))
    y0 = max(0, int(by - up * bh))
    y1 = min(shape[0], int(by + bh + down * bh))
    mask[y0:y1, x0:x1] = True
    return mask


def head_region(image: np.ndarray, *, direction: str, model_dir=None) -> tuple[np.ndarray, bool | None]:
    """(여유 주기 전 머리 마스크, 옆모습이 보는 쪽)."""
    fg = foreground(image)
    shape = image.shape[:2]
    if direction != "back":
        detection = face_identity.detect_face(Image.fromarray(image), model_dir)
        if detection is None:
            raise AngleSwapUnavailable("no_face")
        box = tuple(float(v) for v in detection.box)
        core = fg & window(box, shape, left=1.0, right=0.45, up=1.5, down=FACE_DOWN_SIDE)
        # 얼굴 박스와 이어진 덩어리만 — 머리 옆 벽 그림자가 네모로 딸려오지 않게.
        core = _connected_to(core, window(box, shape, left=0.0, right=0.0, up=0.0, down=0.0))
        if not core.any():
            raise AngleSwapUnavailable("no_head")
        bx, _by, bw, _bh = box
        xs = np.where(core.any(axis=0))[0]
        return core, float(bx + bw / 2) > float(int(xs.min()) + int(xs.max())) / 2.0
    hair = hair_blob(image)
    if not hair.any():
        raise AngleSwapUnavailable("no_head")
    ys, xs = np.where(hair)
    box = (float(xs.min()), float(ys.min()), float(xs.max() - xs.min()), float(ys.max() - ys.min()))
    near = fg & window(box, shape, left=0.12, right=0.12, up=0.15, down=HAIR_DOWN_BACK)
    return _connected_to(near | hair, hair), None


#: 끄면 예전처럼 목 중간에서 끊는다 — 검수 대조용.
NECK_TO_COLLAR = True
#: 머리 마스크를 드러난 목 피부를 따라 칼라까지 내린다(2026-09-23). 머리 높이 대비 최대 길이.
NECK_EXTEND_MAX = 0.6


def extend_to_collar(image: np.ndarray, core: np.ndarray, person: np.ndarray) -> np.ndarray:
    """머리 마스크 아래로 이어진 **맨 목 피부**를 마스크에 넣는다 — 이음선을 칼라로 보낸다.

    왜(09-23 운영 뒷면 E2E): 마스크가 목 중간에서 끝나면 위는 새로 그린 목, 아래는 원래 목이라
    피부 결·그늘까지 달라 색만 맞춰서는 가로 줄이 남는다(색 차 57 → 6 으로 줄여도 부자연).
    목을 통째로 새로 그리게 하면 경계가 칼라 선(옷 경계)으로 가서 원래부터 있는 선이 된다.

    열마다 마스크 바닥에서 아래로 내려가며 **끊김 없이 피부인 동안만** 넣는다. 덩어리로 이으면
    물 빠진 데님(붉은기 조금)이 피부로 붙어 셔츠 어깨까지 딸려 온다(09-23 옆모습 미리보기).
    """
    a = image.astype(np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    skin = (r - b >= 25.0) & (r - g >= 10.0) & (a.mean(axis=2) > TONE_HAIR_MAX) & person
    ys = np.where(core.any(axis=1))[0]
    limit = int(NECK_EXTEND_MAX * max(1, int(ys.max()) - int(ys.min())))
    out = core.copy()
    for x in np.where(core.any(axis=0))[0]:
        y = int(np.nonzero(core[:, x])[0].max()) + 1
        stop = min(core.shape[0], y + limit)
        while y < stop and skin[y, x]:
            out[y, x] = True
            y += 1
    return out


def garment_region(image: np.ndarray, core: np.ndarray) -> np.ndarray:
    """머리 아래 전경 전부 = 옷·몸 보호 영역."""
    garment = foreground(image)
    ys = np.where(core.any(axis=1))[0]
    garment[:int(ys.max()) + 1] = False
    return garment


def grow_hair(core: np.ndarray, direction: str) -> np.ndarray:
    """머리카락이 새로 날 자리만큼 넓힌다(짧은 머리 기준)."""
    xs = np.where(core.any(axis=0))[0]
    width = max(1, int(xs.max()) - int(xs.min()))
    margin = int((HAIR_MARGIN_BACK if direction == "back" else HAIR_MARGIN_SIDE) * width)
    if margin <= 0:
        return core
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * margin + 1,) * 2)
    return cv2.dilate(core.astype(np.uint8), kernel).astype(bool)


def crop_box(core: np.ndarray, size: tuple[int, int]) -> tuple[int, int, int]:
    """머리를 담는 정사각 크롭 (left, top, side). 이미지 밖으로 나가면 안으로 민다."""
    width, height = size
    ys, xs = np.where(core)
    cx = (int(xs.min()) + int(xs.max())) // 2
    cy = (int(ys.min()) + int(ys.max())) // 2
    span = max(int(xs.max()) - int(xs.min()), int(ys.max()) - int(ys.min()))
    side = int(min(max(CROP_HEAD_WIDTHS * span, 64), width, height))
    left = max(0, min(width - side, cx - side // 2))
    top = max(0, min(height - side, cy - side // 2))
    return left, top, side


def plan(image: np.ndarray, *, direction: str, model_dir=None) -> Plan:
    """원본 → 크롭·마스크. 사람·머리를 못 찾으면 AngleSwapUnavailable."""
    height, width = image.shape[:2]
    person = foreground(image)
    if person.sum() < MIN_PERSON_FRACTION * height * width:
        raise AngleSwapUnavailable("no_person")
    core, nose_right = head_region(image, direction=direction, model_dir=model_dir)
    if NECK_TO_COLLAR:
        core = extend_to_collar(image, core, person)
    garment = garment_region(image, core)
    head = grow_hair(core, direction) & ~garment
    left, top, side = crop_box(core, (width, height))
    crop = np.asarray(Image.fromarray(image[top:top + side, left:left + side])
                      .resize((CROP_PX, CROP_PX), Image.LANCZOS))
    mask = cv2.resize(head[top:top + side, left:left + side].astype(np.uint8), (CROP_PX, CROP_PX),
                      interpolation=cv2.INTER_NEAREST) > 0
    if not mask.any():
        raise AngleSwapUnavailable("no_head")
    return Plan(base=image, head=head, head_core=core, garment=garment, person=person,
                box=(left, top, side), crop1k=crop, mask1k=mask, nose_right=nose_right,
                metadata={"headPx": int(head.sum()), "garmentPx": int(garment.sum()),
                          "crop": [left, top, side], "noseRight": nose_right,
                          "maskCropPct": round(100.0 * float(mask.mean()), 1)})


def prompt_for(direction: str) -> str:
    return _POS_HEAD + (_POS_BACK if direction == "back" else _POS_SIDE)


def graph(crop_name: str, ref_name: str, mask_name: str, prompt: str, *, seed: int = SEED) -> dict:
    """ComfyUI API 형식 그래프 — 2026-09-18 확정본과 같은 노드·값."""
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "4": {"class_type": "LoadImage", "inputs": {"image": crop_name}},
        "15": {"class_type": "LoadImage", "inputs": {"image": ref_name}},
        "5": {"class_type": "LoadImageMask", "inputs": {"image": mask_name, "channel": "red"}},
        "16": {"class_type": "LoraLoaderModelOnly",
               "inputs": {"model": ["1", 0], "lora_name": BFS_LORA, "strength_model": BFS_STRENGTH}},
        "6": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["16", 0], "shift": 1.0}},
        "7": {"class_type": "CFGNorm", "inputs": {"model": ["6", 0], "strength": 1.0}},
        "8": {"class_type": "TextEncodeQwenImageEditPlus",
              "inputs": {"clip": ["2", 0], "vae": ["3", 0], "image1": ["4", 0], "image2": ["15", 0],
                         "prompt": prompt}},
        "9": {"class_type": "TextEncodeQwenImageEditPlus",
              "inputs": {"clip": ["2", 0], "vae": ["3", 0], "image1": ["4", 0], "image2": ["15", 0],
                         "prompt": NEG}},
        "18": {"class_type": "FluxKontextMultiReferenceLatentMethod",
               "inputs": {"conditioning": ["8", 0], "reference_latents_method": "index_timestep_zero"}},
        "19": {"class_type": "FluxKontextMultiReferenceLatentMethod",
               "inputs": {"conditioning": ["9", 0], "reference_latents_method": "index_timestep_zero"}},
        "10": {"class_type": "VAEEncode", "inputs": {"pixels": ["4", 0], "vae": ["3", 0]}},
        "11": {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["10", 0], "mask": ["5", 0]}},
        "12": {"class_type": "LanPaint_KSampler", "inputs": {
            "model": ["7", 0], "seed": int(seed), "steps": STEPS, "cfg": CFG, "sampler_name": "euler",
            "scheduler": "simple", "positive": ["18", 0], "negative": ["19", 0], "latent_image": ["11", 0],
            "denoise": 1.0, "LanPaint_NumSteps": 5, "LanPaint_PromptMode": "Image First",
            "LanPaint_Info": "angle_swap", "Inpainting_mode": "🖼️ Image Inpainting"}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["3", 0]}},
        "14": {"class_type": "SaveImage", "inputs": {"images": ["13", 0], "filename_prefix": "angle"}},
    }


#: 톤 보정에 쓰는 링 폭(px, 마스크 바깥). 목이 들어올 만큼은 넓고 배경까지 가지는 않을 만큼 좁다.
TONE_RING_PX = 6
#: 사람 안에서 **피부만** 고르는 밝기 하한. 머리카락(어둡다)을 뺀다. 위쪽 한계는 두지 않는다 —
#: 벽·그림자는 전경 마스크가 이미 걸러 주고, 밝은 피부를 상한으로 자르면 표본이 편향된다.
#: 2026-09-20 실측 3컷의 목 평균 밝기 117~184, 머리카락 40 안팎.
TONE_SKIN_MIN = 90.0
#: 이만큼은 표본이 있어야 보정한다. 적으면 그 평균이 목이 아니라 잡음이다.
TONE_MIN_SAMPLES = 200
#: 덧셈 보정으로 맞출 수 있는 상한. 여기까지는 예전 그대로 더해서 맞춘다(2026-09-20 실측
#: 3컷이 7~12 였고 그 범위에서 튜닝된 값이다). 얼굴 패스의 GATE_COLOR_MAX(35.0)와 같은 값.
TONE_MAX_SHIFT = 35.0

#: 이 값을 넘는 차이는 **비율(gain)로** 맞춘다(2026-09-23 오너: "베이스 컷이 나오면 거기에
#: 사진 톤을 맞춰야지"). 왜 갈라 쓰는가: 덧셈은 차이가 커지면 밝은 쪽이 상한에 눌려 목만
#: 뜨고 얼굴은 안 따라온다. 조명 세기 차이는 본래 곱셈이라 비율이 맞는 모형이다.
#: 실측 2026-09-23: 새 통일 촬영 베이스(238/236/234) vs 등록 옆모습 사진이 [22.9, 33.0, 42.6]
#: 으로 벌어져 컷이 통째로 버려졌다(tone_off).
TONE_GAIN_FROM = 18.0
#: 비율 보정의 한계. 이 밖으로 나가면 사진이 근본적으로 다른 조명이라 억지로 맞추지 않는다.
TONE_GAIN_MIN = 0.55
TONE_GAIN_MAX = 1.85
#: 보정을 **끝낸 뒤** 이음선에 남은 차이의 상한. 컷을 버릴지는 이 값으로 정한다 —
#: 보정 전 차이가 크다는 것만으로는 버리지 않는다. 남은 차이가 이만큼이면 목 이음선이
#: 눈에 띈다(2026-09-20 실측: 보정 후 1.6~10.3 이 "잘 맞은" 범위였다).
TONE_MAX_RESIDUAL = 14.0


def tone_shift(p: Plan, out: np.ndarray, box: tuple[int, int, int]) -> np.ndarray | None:
    """새로 그린 머리의 톤을 **원본 목**에 맞추는 전역 RGB 이동량. 표본이 없으면 None.

    ★ 얼굴 패스처럼 타원 안쪽 링을 쓸 수 없다. 저쪽 마스크는 얼굴이라 링이 곧 얼굴 주변
      피부지만, 여기 마스크는 **머리 전체**라 바깥 링에 배경 벽·그림자·옷이 같이 들어온다.
      벽을 표본에 넣으면 목이 벽 색으로 끌려간다 — 얼굴 패스에서 국소 보정이 실패한 것과
      같은 이유다(face_identity.paste_back 주석: 링이 밝은 벽을 표본해 목·턱이 노랗게 과보정).

    ★ 그래서 **전경 마스크로 먼저 자르고**(벽·그림자가 통째로 빠진다) 그 안에서 밝기로
      머리카락만 뺀다. 밝기 창만으로 고르면 벽 그림자가 피부로 들어온다 — 2026-09-20
      합성 컷에서 바깥 링 평균이 199(그림자)로 나왔다. 실사진에서 안 걸린 건 그 벽이
      우연히 상한 위였기 때문이다.

    ★ 전역 이동만 한다. 국소 차분 필드는 얼굴 패스에서 이미 되돌렸다(신원 붕괴·과보정).

    2026-09-20 실측(보정 전): 오른쪽 옆 9.2 · 왼쪽 옆 12.3 · 뒤 7.1. 세 컷 다 RGB 가 거의
    같은 양으로 어긋난 **밝기** 차이였다 — 참고 사진이 베이스 컷보다 밝았다.
    """
    left, top, side = box
    head = p.head[top:top + side, left:left + side]
    if not head.any():
        return None
    k = 2 * TONE_RING_PX + 1
    ring_out = cv2.dilate(head.astype(np.uint8), np.ones((k, k), np.uint8)).astype(bool) & ~head
    ring_in = head & ~cv2.erode(head.astype(np.uint8), np.ones((k, k), np.uint8)).astype(bool)
    base = p.base[top:top + side, left:left + side].astype(np.float32)
    person = p.person[top:top + side, left:left + side]
    bg = background_color(p.base)

    def skin(mask, image, is_person):
        return mask & is_person & (image.mean(axis=2) > TONE_SKIN_MIN)

    # 바깥 = 원본의 목(전경). 안쪽 = 새로 그린 얼굴·목 — 원본에서 배경이던 자리로 나올 수
    # 있으므로 **생성 결과**로 사람을 판정한다.
    outside = skin(ring_out, base, person)
    inside = skin(ring_in, out, np.abs(out - bg).max(axis=2) > BG_THRESHOLD)
    if outside.sum() < TONE_MIN_SAMPLES or inside.sum() < TONE_MIN_SAMPLES:
        return None
    return base[outside].mean(axis=0) - out[inside].mean(axis=0)


def tone_means(p: Plan, out: np.ndarray, box: tuple[int, int, int]):
    """이음선 링의 (원본 목 평균, 새 머리 평균). 표본이 모자라면 None.

    tone_shift 가 두 평균의 **차이**만 돌려주던 것을 갈라 둔다 — 비율 보정은 두 값이
    따로 필요하고, 보정 뒤 남은 차이를 다시 재는 데도 같은 표본을 써야 한다.
    """
    left, top, side = box
    head = p.head[top:top + side, left:left + side]
    if not head.any():
        return None
    k = 2 * TONE_RING_PX + 1
    ring_out = cv2.dilate(head.astype(np.uint8), np.ones((k, k), np.uint8)).astype(bool) & ~head
    ring_in = head & ~cv2.erode(head.astype(np.uint8), np.ones((k, k), np.uint8)).astype(bool)
    base = p.base[top:top + side, left:left + side].astype(np.float32)
    person = p.person[top:top + side, left:left + side]
    bg = background_color(p.base)

    def skin(mask, image, is_person):
        return mask & is_person & (image.mean(axis=2) > TONE_SKIN_MIN)

    outside = skin(ring_out, base, person)
    inside = skin(ring_in, out, np.abs(out - bg).max(axis=2) > BG_THRESHOLD)
    if outside.sum() < TONE_MIN_SAMPLES or inside.sum() < TONE_MIN_SAMPLES:
        return None
    return base[outside].mean(axis=0), out[inside].mean(axis=0)


def tone_gain(neck: np.ndarray, head: np.ndarray) -> np.ndarray:
    """새 머리를 **베이스 컷의 목**에 맞추는 채널별 비율. 조명 세기 차이는 곱셈이다.

    덧셈(tone_shift)은 차이가 작을 때만 맞는다 — 20 이 넘어가면 밝은 채널이 255 에 눌려
    목만 뜨고 얼굴은 안 따라온다. 한계(TONE_GAIN_MIN/MAX)를 벗어나면 그 값으로 자른다:
    억지로 다 맞추는 것보다 남은 차이를 residual 로 재서 판단하는 편이 안전하다.
    """
    safe = np.maximum(head, 1.0)
    return np.clip(neck / safe, TONE_GAIN_MIN, TONE_GAIN_MAX).astype(np.float32)


#: 톤 가중치를 부드럽게 만드는 흐림(px). 실루엣에서 뚝 끊기지 않을 만큼만.
#:
#: ★ 보정량에 비례해 키운다(2026-09-21). 2.0 은 보정량이 한 자리일 때 고른 값인데,
#:   뒷모습은 등록 사진과 스튜디오 조명이 많이 달라 보정량이 30 언저리까지 간다.
#:   그 크기를 2px 로 가리면 가중치가 0→1 로 넘어가는 자리가 **눈에 보이는 단차**가 된다 —
#:   45degree_view/4.png 실측에서 목에 직각으로 잘린 밝기 계단이 생겼다. 게이트(35.0)는
#:   "톤이 너무 어긋나면 버린다"만 보므로 통과한 컷에서도 그 계단이 남는다.
#:
#:   경계를 넘는 밝기 차이는 shift 크기에 비례하니 흐림도 같이 키운다. 기울기는
#:   "보정량 1 당 몇 px" 로 두고, 위는 머리 상자(1024px)에서 과해지지 않게 막는다.
TONE_FEATHER = 2.0
#: 보정량 1 당 더할 흐림(px). shift 27 → 2.0 + 27×0.35 ≈ 11.5px.
TONE_FEATHER_PER_SHIFT = 0.35
#: 흐림 상한(px). 이보다 크면 목 보정이 얼굴·옷까지 번진다.
TONE_FEATHER_MAX = 16.0


def tone_feather(shift: np.ndarray | None) -> float:
    """보정량에 맞춘 흐림 반경(px). 작은 보정은 예전 그대로 2.0."""
    if shift is None:
        return TONE_FEATHER
    magnitude = float(max(abs(float(v)) for v in shift))
    return min(TONE_FEATHER + magnitude * TONE_FEATHER_PER_SHIFT, TONE_FEATHER_MAX)
#: 이 밝기 아래는 머리카락으로 보고 **보정하지 않는다**. 위는 피부로 보고 전부 건다.
#: 사이는 선형으로 섞어 경계를 안 만든다(TONE_HAIR_MAX ~ TONE_SKIN_MIN).
#: 실측 밝기: 머리카락 40 안팎 · 목 117~184.
TONE_HAIR_MAX = 60.0


def apply_tone(out: np.ndarray, shift: np.ndarray, bg: np.ndarray) -> np.ndarray:
    """톤 보정을 **사람의 피부에만** 건다. 배경도 머리카락도 건드리지 않는다.

    ★ 머리 마스크는 머리카락이 새로 날 자리까지 포함해 **배경 위로 넘어간다**(grow_hair).
      전역으로 걸면 그 배경도 같이 밝아져서 벽에 머리 모양 자국이 남는다 — 2026-09-20
      실측에서 세 컷 모두 머리 옆 벽에 실루엣이 찍혔다. 얼굴 패스는 타원이 전부 얼굴이라
      이 문제가 없다(face_identity.paste_back).

    ★ **머리카락은 뺀다.** 보정량은 목 피부를 기준으로 재는데 그걸 머리카락에까지 걸면
      머리색이 같이 밝아진다 — 2026-09-21 뒷모습 컷이 이음선 1.6 으로 제일 잘 맞았는데도
      머리가 갈색기를 띠었다. 뒷모습은 보이는 피부가 목·귀뿐이고 이음선도 거기 있으므로,
      머리카락을 빼도 맞춰야 할 곳은 다 맞는다.

    ★ 사람 판정은 **생성 결과**에서 한다. 원본 전경으로 가리면 새로 나온 목·턱(원본에서는
      배경이던 자리)이 보정을 못 받아 이음선이 그대로 남는다 — 2026-09-20 왼쪽 옆 컷이
      12.3 → 10.3 밖에 안 줄었던 이유다.
    ★ 배경색은 **전체 원본** 테두리에서 온다(background_color). 크롭 테두리는 사람·옷이다.
    """
    person = (np.abs(out - bg).max(axis=2) > BG_THRESHOLD).astype(np.float32)
    lum = out.mean(axis=2)
    skin = np.clip((lum - TONE_HAIR_MAX) / (TONE_SKIN_MIN - TONE_HAIR_MAX), 0.0, 1.0)
    weight = cv2.GaussianBlur(person * skin, (0, 0), tone_feather(shift))[..., None]
    return np.clip(out + shift * weight, 0, 255)


def apply_tone_gain(out: np.ndarray, gain: np.ndarray, bg: np.ndarray,
                    *, feather_shift: np.ndarray | None = None) -> np.ndarray:
    """비율 보정판 apply_tone. 거는 자리(사람 피부만·머리카락 제외·배경 제외)는 같다.

    흐림 반경은 **밝기가 실제로 얼마나 움직이는지**로 잡는다 — 비율 1.3 은 목(≈150)에서
    45 만큼 움직이고 어두운 곳에서는 덜 움직인다. 덧셈 경로와 같은 기준(tone_feather)을
    쓰려고 등가 이동량을 넘겨받는다.
    """
    person = (np.abs(out - bg).max(axis=2) > BG_THRESHOLD).astype(np.float32)
    lum = out.mean(axis=2)
    skin = np.clip((lum - TONE_HAIR_MAX) / (TONE_SKIN_MIN - TONE_HAIR_MAX), 0.0, 1.0)
    weight = cv2.GaussianBlur(person * skin, (0, 0), tone_feather(feather_shift))[..., None]
    scaled = out * gain
    return np.clip(out * (1.0 - weight) + scaled * weight, 0, 255)


#: 목 이음선 색 맞춤 — **끔**(2026-09-23). NECK_TO_COLLAR 로 목을 칼라까지 새로 그리면 이을 목이
#: 없어서, 남은 표본(목 옆·칼라 가)으로 비율을 구하면 오히려 목을 회녹색으로 뺀다(실측 R×0.81).
#: 마스크를 칼라까지 못 내린 컷을 다시 볼 때 켜는 스위치로 남긴다.
NECK_SEAM_MATCH = False
#: 이음선 위·아래로 표본을 뜨는 띠 두께(원본 px, 크롭 한 변 기준 비율).
NECK_BAND_FRAC = 0.035
#: 보정이 위로 번지는 높이(크롭 한 변 비율). 목 길이 정도 — 여기서 0 으로 사라져 턱·얼굴은 안 건드린다.
NECK_RAMP_FRAC = 0.16
#: 목 맞춤 비율의 한계. 밖이면 표본이 이상한 것이라 자른다.
NECK_GAIN_MIN = 0.70
NECK_GAIN_MAX = 1.40
NECK_MIN_SAMPLES = 120


def _true_skin(image: np.ndarray) -> np.ndarray:
    """피부 판정 — 밝기만 보면 회색 옷도 피부로 잡힌다. R>G>B 이고 붉은기가 있는 픽셀만."""
    r, g, b = image[..., 0], image[..., 1], image[..., 2]
    lum = image.mean(axis=2)
    return (r > g) & (g > b) & ((r - b) >= 12.0) & (lum > TONE_SKIN_MIN) & (lum < 245.0)


def _neck_pixels(image: np.ndarray, bg: np.ndarray) -> np.ndarray:
    """붙인 머리 쪽 목 판정 — **색조는 안 본다**. 문제가 되는 목은 회색으로 빠져 있어서
    (2026-09-23 운영 뒷면: 칼라 위 목이 회색 띠) _true_skin 으로 거르면 고칠 대상이 표본에서
    빠진다. 머리카락(어둡다)·배경(bg 와 가깝다)·하이라이트만 뺀다."""
    lum = image.mean(axis=2)
    not_bg = np.abs(image - bg).max(axis=2) > 18.0  # 회색으로 빠진 목은 배경과 30~45 차이뿐이다
    # 따뜻한 쪽만(R>B) — 목 옆 배경의 푸른 번짐·칼라 데님은 R<=B 라 빠진다. 안 빼면 그 번짐까지
    # 살구색으로 칠해져 목 옆에 빛이 뜬다(09-23 재현).
    warm = (image[..., 0] - image[..., 2]) >= 8.0
    return (lum > TONE_HAIR_MAX + 30.0) & (lum < 245.0) & not_bg & warm


def match_neck_seam(p: Plan, out: np.ndarray, *, meta: dict | None = None) -> np.ndarray:
    """붙인 머리의 **목 아래쪽**을 원래 몸 목 색에 맞춘다 — 이음선 가로 줄을 없앤다.

    왜 따로 하는가(2026-09-23 운영 E2E 실측): 머리 영역이 목 **중간에서** 끝나서, 경계 위는
    등록 사진 피부·아래는 베이스(생성) 피부다. 둘은 밝기만이 아니라 **색조**도 다르다
    (06 뒷면: 위 [164,121,107] 분홍기 → 아래 [187,154,130] 살구색, 경계 Δ21~28).
    tone_means 는 머리 둘레 전체(볼·귀·턱·목) 평균이라 **그 한 줄**에서는 안 맞는다.

    그래서 경계 바로 안쪽·바깥쪽 띠만 표본으로 떠서 채널별 비율을 구하고, 경계에서 1 →
    위로 NECK_RAMP_FRAC 만큼 올라가며 0 이 되게 건다. 얼굴·머리카락은 안 건드린다.
    """
    left, top, side = p.box
    region = p.head[top:top + side, left:left + side]
    if not region.any():
        return out
    base = p.base[top:top + side, left:left + side].astype(np.float32)
    person = p.person[top:top + side, left:left + side]
    band = max(3, int(round(side * NECK_BAND_FRAC)))
    k = 2 * band + 1
    kernel = np.ones((k, k), np.uint8)
    ring_out = cv2.dilate(region.astype(np.uint8), kernel).astype(bool) & ~region
    ring_in = region & ~cv2.erode(region.astype(np.uint8), kernel).astype(bool)
    ys = np.nonzero(region)[0]
    y_top, y_bot = int(ys.min()), int(ys.max())
    # 목은 머리 영역의 아래쪽이다 — 위 45% 는 머리·얼굴이라 표본에서 뺀다.
    lower = np.zeros_like(region)
    lower[y_top + int(0.45 * (y_bot - y_top)):, :] = True
    outside = ring_out & lower & person & _true_skin(base)
    bg = background_color(p.base)
    inside = ring_in & lower & _neck_pixels(out, bg)
    # 표본은 **이음선 바로 위아래 줄**만 — 링 전체면 귀 밑·머리선의 어두운 피부가 섞여
    # 비율이 모자란다(09-23 뒷면 재현: 링 전체 잔차 21 → 줄만 쓰면 한 자릿수).
    near = np.zeros_like(region)
    near[max(0, y_bot - 3 * band):min(side, y_bot + 3 * band), :] = True
    if (inside & near).sum() >= NECK_MIN_SAMPLES and (outside & near).sum() >= NECK_MIN_SAMPLES:
        inside, outside = inside & near, outside & near
    if outside.sum() < NECK_MIN_SAMPLES or inside.sum() < NECK_MIN_SAMPLES:
        if meta is not None:
            meta["neckSeam"] = None
        return out
    neck = base[outside].mean(axis=0)
    head = np.maximum(out[inside].mean(axis=0), 1.0)
    gain = np.clip(neck / head, NECK_GAIN_MIN, NECK_GAIN_MAX).astype(np.float32)
    # 경계 높이 = 안쪽 띠의 평균 y. 거기서 1, 위로 ramp 만큼 올라가며 0.
    seam_y = float(np.nonzero(inside)[0].mean())
    ramp = max(8.0, side * NECK_RAMP_FRAC)
    yy = np.arange(side, dtype=np.float32)[:, None]
    vertical = np.clip(1.0 - (seam_y - yy) / ramp, 0.0, 1.0)
    # 영역(region)으로 먼저 자르고 흐리면 경계에서 가중치가 절반으로 떨어져 **바로 그 줄**이
    # 덜 맞는다(09-23 재현 잔차 21). 흐린 뒤에 쓰는 건 어차피 composite 의 영역 안뿐이다.
    weight = vertical * _neck_pixels(out, bg).astype(np.float32)
    weight = cv2.GaussianBlur(weight, (0, 0), max(2.0, band / 4.0))[..., None]
    fixed = np.clip(out * (1.0 - weight) + out * gain * weight, 0, 255)
    if meta is not None:
        after = fixed[inside].mean(axis=0)
        meta["neckSeam"] = {
            "gain": [round(float(v), 3) for v in gain],
            "before": round(float(np.abs(neck - head).max()), 2),
            "after": round(float(np.abs(neck - after).max()), 2),
        }
    return fixed


def composite(p: Plan, out1k: np.ndarray, *, meta: dict | None = None) -> np.ndarray:
    """마스크 안만 원본 해상도에 섞고, 옷은 원본 픽셀로 되돌린다(경계 2px 만 섞음).

    섞기 전에 톤을 원본 목에 맞춘다(tone_shift) — 등록자 사진과 베이스 컷의 조명이 다르면
    목 이음선에서 밝기가 튄다. meta 를 주면 보정량을 적어 둔다(검수·게이트용).
    """
    left, top, side = p.box
    out = np.asarray(Image.fromarray(out1k).resize((side, side), Image.LANCZOS), np.float32)
    # 베이스 컷이 기준이다 — 새 머리를 거기에 맞춘다. 작은 차이는 예전처럼 더해서,
    # 큰 차이는 비율로(조명 세기 차이는 곱셈이다). 맞추고 나서 남은 차이를 다시 재고,
    # 버릴지는 **그 잔차**로 정한다(2026-09-23 오너).
    means = tone_means(p, out, p.box)
    shift = None if means is None else (means[0] - means[1])
    mode = None
    if shift is not None:
        if float(max(abs(float(v)) for v in shift)) <= TONE_GAIN_FROM:
            out = apply_tone(out, shift, background_color(p.base))
            mode = "shift"
        else:
            gain = tone_gain(means[0], means[1])
            out = apply_tone_gain(out, gain, background_color(p.base), feather_shift=shift)
            mode = "gain"
            if meta is not None:
                meta["toneGain"] = [round(float(v), 3) for v in gain]
    if meta is not None:
        meta["toneShift"] = ([round(float(v), 2) for v in shift] if shift is not None else None)
        meta["toneMode"] = mode
        after = tone_means(p, out, p.box)
        meta["toneResidual"] = (
            None if after is None
            else round(float(max(abs(float(v)) for v in (after[0] - after[1]))), 2)
        )
    if NECK_SEAM_MATCH:
        out = match_neck_seam(p, out, meta=meta)
    region = p.head[top:top + side, left:left + side].astype(np.float32)
    blur = cv2.GaussianBlur(region, (0, 0), 1.5 * max(1.0, side / CROP_PX))
    alpha = np.maximum(blur * (cv2.dilate(region, np.ones((3, 3), np.uint8)) > 0), region)[..., None]
    final = p.base.astype(np.float32).copy()
    patch = final[top:top + side, left:left + side]
    final[top:top + side, left:left + side] = out * alpha + patch * (1.0 - alpha)
    garment = p.garment.astype(np.float32)
    core = cv2.erode(p.garment.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(np.float32)
    soft = np.clip(cv2.GaussianBlur(core, (0, 0), 1.6), 0, 1) * garment
    weight = np.maximum(soft, core)[..., None]
    blended = p.base.astype(np.float32) * weight + final * (1.0 - weight)
    return np.clip(blended + 0.5, 0, 255).astype(np.uint8)


def changed_garment_px(p: Plan, final: np.ndarray) -> int:
    """옷 영역에서 원본과 달라진 픽셀 수 — 검수 지표(2026-09-20 실측 0~228)."""
    if not p.garment.any():
        return 0
    diff = np.abs(final[p.garment].astype(np.int16) - p.base[p.garment].astype(np.int16)).max(axis=1)
    return int((diff > 0).sum())


#: RunPod Serverless API. 엔드포인트 id 로 주소가 정해진다.
SERVERLESS_BASE = "https://api.runpod.ai/v2"
#: 출력 노드 번호(graph 의 SaveImage). 서버리스 응답에는 노드 번호가 없어 첫 이미지를 쓴다.
SAVE_NODE = "14"


class ServerlessBackend:
    """RunPod Serverless(runpod/worker-comfyui) 호출 — /runsync 한 번으로 끝난다.

    파드 경로와 표면이 같다(upload → run) — `graph()` 가 만드는 워크플로 JSON 은 그대로
    들어간다. 다른 건 **어디에 올리는가**뿐이다: 파드는 ComfyUI 의 /upload/image 에 실제로
    올리고 이름으로 참조하지만, 여기서는 요청 본문의 `images` 배열에 담아 보낸다. 워커가
    그 이름으로 ComfyUI 입력 폴더에 풀어 주므로 워크플로에서 보는 이름은 같다.

    ★ 유휴 요금이 없다. 파드는 일이 없어도 유휴 시간만큼 GPU 를 물고 있었다.
    ★ 동시성은 워커 수가 알아서 맡는다 — 파드 하나가 한 컷씩 처리하던 제약이 없다.
    """

    def __init__(self, endpoint_id: str, api_key: str, *, timeout: float = 900.0,
                 poll_seconds: float = 5.0):
        import httpx

        self.base = f"{SERVERLESS_BASE}/{endpoint_id.strip()}"
        #: HTTP 한 번의 제한과 **잡 전체의 제한**은 다르다. 앞은 짧게(요청 하나), 뒤(_timeout)는
        #: 컷 길이만큼 길게. 한 값으로 묶으면 폴링 한 번이 컷 전체 시간을 기다리게 된다.
        self._client = httpx.Client(base_url=self.base, timeout=60.0,
                                    headers={"Authorization": f"Bearer {api_key}"})
        self._timeout = timeout
        self._poll = poll_seconds
        self._images: list[dict] = []

    def upload(self, array: np.ndarray, name: str, mode: str = "RGB") -> str:
        """실제로 올리지 않고 요청 본문에 담아 둔다. 이름은 워크플로가 참조하는 그 이름이다."""
        import base64

        stream = io.BytesIO()
        Image.fromarray(array).convert(mode).save(stream, "PNG")
        self._images.append({"name": name,
                             "image": base64.b64encode(stream.getvalue()).decode()})
        return name

    def run(self, workflow: dict) -> np.ndarray:
        import base64

        # ★ **/runsync 를 쓰면 안 된다.** 그 호출은 90초쯤에서 잘리고 IN_PROGRESS 를 돌려준다
        #   (2026-09-21 실측: 세 컷 모두 93초에 끊겼다). 한 컷은 190초 안팎이라 동기 호출로는
        #   받을 수 없다. 비동기로 넣고(/run) 상태를 폴링한다(/status/<id>).
        res = self._client.post("/run",
                                json={"input": {"workflow": workflow, "images": self._images}})
        self._images = []
        if res.status_code != 200:
            log.warning("angle swap serverless http %s", res.status_code)
            raise AngleSwapUnavailable("backend_error")
        job_id = str((res.json() or {}).get("id") or "")
        if not job_id:
            log.warning("angle swap serverless returned no job id")
            raise AngleSwapUnavailable("backend_error")
        body = self._await(job_id)
        status = str(body.get("status") or "")
        if status != "COMPLETED":
            # FAILED·CANCELLED·TIMED_OUT — 워커가 컷을 만들지 못했다.
            log.warning("angle swap serverless status=%s error=%s",
                        status, str(body.get("error"))[:200])
            raise AngleSwapUnavailable("backend_error")
        images = ((body.get("output") or {}).get("images") or [])
        if not images or not images[0].get("data"):
            log.warning("angle swap serverless returned no image: %s",
                        str((body.get("output") or {}).get("errors"))[:200])
            raise AngleSwapUnavailable("graph_error")
        first = images[0]
        if str(first.get("type")) != "base64":
            # S3 업로드를 켜면 여기로 온다. 우리는 안 켠다 — 컷 바이트가 우리 버킷 밖으로 나간다.
            log.warning("angle swap serverless returned %s, expected base64", first.get("type"))
            raise AngleSwapUnavailable("backend_error")
        with Image.open(io.BytesIO(base64.b64decode(first["data"]))) as opened:
            return np.asarray(opened.convert("RGB"))

    def _await(self, job_id: str) -> dict:
        """끝날 때까지 상태를 묻는다. 제한 시간을 넘기면 마지막 상태를 그대로 돌려준다 —
        호출자가 COMPLETED 아님으로 보고 컷을 버린다(남의 머리를 내보내지 않는다)."""
        deadline = time.monotonic() + self._timeout
        body: dict = {}
        while time.monotonic() < deadline:
            res = self._client.get(f"/status/{job_id}")
            if res.status_code != 200:
                log.warning("angle swap serverless status http %s", res.status_code)
                return {"status": "STATUS_HTTP_ERROR"}
            body = res.json() or {}
            if str(body.get("status") or "") not in ("IN_QUEUE", "IN_PROGRESS"):
                return body
            time.sleep(self._poll)
        return body or {"status": "TIMED_OUT"}


class ComfyBackend:
    """ComfyUI 파드 호출. upload → /prompt → /history → /view."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 900.0, poll_seconds: float = 5.0):
        import httpx

        #: 어느 파드를 보고 있는가. 파드가 재고 때문에 갈릴 수 있어 로그·검증에서 읽는다.
        self.base = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base, timeout=timeout,
                                    headers={"Authorization": f"Bearer {token}"})
        self._poll = poll_seconds

    def upload(self, array: np.ndarray, name: str, mode: str = "RGB") -> str:
        stream = io.BytesIO()
        Image.fromarray(array).convert(mode).save(stream, "PNG")
        r = self._client.post("/upload/image", files={"image": (name, stream.getvalue(), "image/png")},
                              data={"overwrite": "true"})
        r.raise_for_status()
        return r.json()["name"]

    def run(self, workflow: dict) -> np.ndarray:
        r = self._client.post("/prompt", json={"prompt": workflow, "client_id": "angle_swap"})
        if r.status_code != 200:
            raise AngleSwapUnavailable("backend_error")
        prompt_id = r.json()["prompt_id"]
        while True:
            entry = (self._client.get(f"/history/{prompt_id}").json() or {}).get(prompt_id)
            if entry:
                if (entry.get("status") or {}).get("status_str") == "error":
                    log.warning("angle swap graph error: %s", json.dumps(entry.get("status"))[:200])
                    raise AngleSwapUnavailable("graph_error")
                images = (entry.get("outputs") or {}).get("14", {}).get("images")
                if images:
                    view = self._client.get("/view", params={
                        "filename": images[0]["filename"], "subfolder": images[0].get("subfolder", ""),
                        "type": images[0].get("type", "output")})
                    view.raise_for_status()
                    with Image.open(io.BytesIO(view.content)) as opened:
                        return np.asarray(opened.convert("RGB"))
            time.sleep(self._poll)


#: 참고 사진을 보낼 때의 긴 변 상한(px). 모델은 1024 에서 도는데 등록 사진은 아이폰 원본
#: (8064×6048, 수 MB)일 수 있다. 서버리스 요청에는 크기 상한이 있어(/runsync 20MB) 원본을
#: 그대로 실으면 큰 사진 하나로 요청이 막힌다. 줄여도 참고로 쓰는 머리는 그대로 담긴다.
REFERENCE_MAX_PX = 2048


def shrink(array: np.ndarray, limit: int = REFERENCE_MAX_PX) -> np.ndarray:
    """긴 변이 limit 을 넘으면 비율을 지켜 줄인다. 작으면 그대로."""
    height, width = array.shape[:2]
    longest = max(height, width)
    if longest <= limit:
        return array
    scale = limit / float(longest)
    size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    return np.asarray(Image.fromarray(array).resize(size, Image.LANCZOS))


def _run(backend, p: Plan, reference: np.ndarray, direction: str, seed: int) -> np.ndarray:
    crop_name = backend.upload(p.crop1k, "angle_crop.png")
    ref_name = backend.upload(shrink(reference), "angle_ref.png")
    mask_name = backend.upload((p.mask1k * 255).astype(np.uint8), "angle_mask.png", "L")
    return backend.run(graph(crop_name, ref_name, mask_name, prompt_for(direction), seed=seed))


async def swap(image: bytes, mime: str, *, direction: str, photos: AnglePhotos, backend,
               model_dir=None, seed: int = SEED, outcome: dict | None = None) -> tuple[bytes, str]:
    """옆·뒷모습 컷의 머리를 등록자 사진으로 교체한다. 실패는 AngleSwapUnavailable."""
    if direction not in ("side", "back"):
        raise ValueError("angle_swap_direction")
    meta: dict = {"promptVersion": PROMPT_VERSION, "direction": direction, "seed": seed}
    with Image.open(io.BytesIO(image)) as opened:
        base = np.asarray(opened.convert("RGB"))
    p = await asyncio.to_thread(plan, base, direction=direction, model_dir=model_dir)
    meta.update(p.metadata)
    reference_bytes, slot = photos.for_direction(direction, p.nose_right)
    meta["slot"] = slot
    if not reference_bytes:
        raise AngleSwapUnavailable("no_angle_photo")
    with Image.open(io.BytesIO(reference_bytes)) as opened:
        reference = np.asarray(opened.convert("RGB"))
    try:
        out1k = await asyncio.to_thread(_run, backend, p, reference, direction, seed)
    except AngleSwapUnavailable:
        raise
    except Exception as exc:
        log.warning("angle swap backend failed: %s", type(exc).__name__)
        raise AngleSwapUnavailable("backend_error") from exc
    final = await asyncio.to_thread(composite, p, out1k, meta=meta)
    # 톤을 못 맞출 만큼 조명이 어긋난 참고 사진이면 **컷을 버린다**. 억지로 붙이면 목만
    # 물든 사람이 상품 페이지에 실린다 — 얼굴 패스의 lighting_off 와 같은 판단이다.
    # 보정 **뒤** 이음선에 남은 차이로 판단한다. 보정 전 차이가 크다는 것만으로는 버리지
    # 않는다 — 베이스 컷이 기준이고, 맞출 수 있으면 맞춰서 내보낸다(2026-09-23 오너).
    residual = meta.get("toneResidual")
    if residual is not None and residual > TONE_MAX_RESIDUAL:
        log.warning("angle swap tone off: shift=%s mode=%s residual=%s",
                    meta.get("toneShift"), meta.get("toneMode"), residual)
        raise AngleSwapUnavailable("tone_off")
    meta["garmentChangedPx"] = changed_garment_px(p, final)
    stream = io.BytesIO()
    Image.fromarray(final).save(stream, "PNG")
    if outcome is not None:
        outcome["angle_swap"] = "applied"
        outcome["angle_swap_meta"] = meta
    return stream.getvalue(), "image/png"
